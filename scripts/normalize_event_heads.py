# -*- coding: utf-8 -*-
"""事件头部节点规范化迁移（2026-09-02 命名约定）。

头部事件 id 约定：{活动类型}—{方面槽}—{规范实体名}
  - 活动类型：旅游 / 游玩 / 饮用 / 听歌 / 计划 等动名词
  - 方面槽：  地点（去向）/ 对象（作用于物）/ 人（与人有关）
  - 实体名：  图谱中已有的规范实体 id
无法干净分解的事件（如 打瓦）保留自然语言摘要，不强行套用模板；
子事件保持叙事式摘要（它们是事件的内部展开，不是检索锚点）。

动作：
  1. 备份 data/runtime_graph.json → data/runtime_graph.json.bak_normalize_20260902
  2. 实体规范化：远方 → 远方市（aliases += ["远方"]）
  3. 头部事件重命名：id + 全部关联边 + aliases += [旧 id] +
     event_head_pattern 结构化标记（机器可读的分解式）
  4. 防御性同步 data/faiss/node_mapping.json（当前缓存不含这些 id，应为 no-op）
  5. 校验（边端点完整、计数守恒）并保存
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_model import KnowledgeGraph

GRAPH_PATH = os.path.join("data", "runtime_graph.json")
BACKUP_PATH = os.path.join("data", "runtime_graph.json.bak_normalize_20260902")
FAISS_MAP_PATH = os.path.join("data", "faiss", "node_mapping.json")

# ── 实体规范化：城市名用行政区划规范名，旧名进 aliases ──
ENTITY_RENAMES = [
    # (old_id, new_id)
    ("远方", "远方市"),
]

# ── 头部事件重命名：{活动类型}—{方面槽}—{规范实体名} ──
# 打瓦 不改名：无干净的实体分解，强行套模板是编造结构。
# curiosity_auto / emotion_injection 事件是机制产物，不在本约定范围内。
EVENT_RENAMES = [
    # (old_id, new_id, 活动类型, 方面槽, 实体名)
    ("远方之行",       "旅游—地点—远方市",   "旅游", "地点", "远方市"),
    ("收获日2游玩经历",  "游玩—对象—收获日2游玩", "游玩", "对象", "收获日2游玩"),
    ("喝奶茶",           "饮用—对象—奶茶",       "饮用", "对象", "奶茶"),
    ("玩Minecraft",      "游玩—对象—Minecraft",  "游玩", "对象", "Minecraft"),
    ("听歌《吸吐》",     "听歌—对象—吸吐",       "听歌", "对象", "吸吐"),
    ("荒野行动游玩计划", "计划—对象—荒野行动",   "计划", "对象", "荒野行动"),
]


def rename_node(kg: KnowledgeGraph, old_id: str, new_id: str,
                pattern: dict = None) -> bool:
    """重命名节点：改 id、重定向全部关联边、旧 id 降为别名、去重折叠。"""
    node = kg.get_node(old_id)
    if node is None:
        print(f"  [跳过] 节点不存在: {old_id}")
        return False
    if new_id in kg.nodes:
        print(f"  [中止] 目标 id 已存在，拒绝覆盖: {new_id}")
        sys.exit(1)

    incident = [e for e in kg.edges if e.src == old_id or e.dst == old_id]

    node.id = new_id
    del kg.nodes[old_id]
    kg.nodes[new_id] = node

    for e in kg.edges:
        if e.src == old_id:
            e.src = new_id
        if e.dst == old_id:
            e.dst = new_id

    # 重命名可能使两条边折叠为同一 (src, dst, relation) —— 保留权重更高者
    seen = {}
    collapsed = 0
    for e in kg.edges:
        key = (e.src, e.dst, e.relation)
        if key in seen:
            prev = seen[key]
            if abs(e.weight) > abs(prev.weight):
                prev.weight = e.weight
                prev.relation_category = e.relation_category
            kg.edges.remove(e)
            collapsed += 1
        else:
            seen[key] = e
    if collapsed:
        print(f"    折叠重复边 {collapsed} 条")

    # 旧 id 降为别名（老对话/日志里的引用仍可解析）
    aliases = node.extra_attrs.setdefault("aliases", [])
    if old_id not in aliases:
        aliases.append(old_id)

    # 结构化分解式：让命名约定机器可读，而不只是字符串习惯
    if pattern is not None:
        node.extra_attrs["event_head_pattern"] = pattern

    node.touch()
    print(f"  [改名] {old_id} → {new_id} (关联边 {len(incident)} 条)")
    return True


def main():
    if not os.path.exists(GRAPH_PATH):
        print(f"未找到 {GRAPH_PATH}，无迁移对象")
        sys.exit(1)

    shutil.copy2(GRAPH_PATH, BACKUP_PATH)
    print(f"已备份 → {BACKUP_PATH}")

    kg = KnowledgeGraph.load(GRAPH_PATH)
    n0, e0 = len(kg.nodes), len(kg.edges)
    print(f"迁移前: {n0} 节点, {e0} 边")

    print("\n① 实体规范化")
    for old_id, new_id in ENTITY_RENAMES:
        rename_node(kg, old_id, new_id)

    print("\n② 头部事件规范化（{活动类型}—{方面槽}—{规范实体名}）")
    for old_id, new_id, act, slot, ent in EVENT_RENAMES:
        rename_node(kg, old_id, new_id,
                    pattern={"activity_type": act, "aspect_slot": slot, "entity": ent})

    # ── 校验：所有边端点必须存在 ──
    dangling = [e for e in kg.edges if e.src not in kg.nodes or e.dst not in kg.nodes]
    if dangling:
        print(f"\n[错误] {len(dangling)} 条边端点悬空，回滚不保存:")
        for e in dangling[:5]:
            print(f"  {e.src} -[{e.relation}]→ {e.dst}")
        sys.exit(1)

    n1, e1 = len(kg.nodes), len(kg.edges)
    print(f"\n迁移后: {n1} 节点, {e1} 边 (Δ{n1-n0:+d}/{e1-e0:+d})")

    # ── 防御性同步 embedding 映射（当前缓存不含这些 id，应为 0 处替换）──
    if os.path.exists(FAISS_MAP_PATH):
        with open(FAISS_MAP_PATH, "r", encoding="utf-8") as f:
            mapping = json.load(f)
        renames = dict(ENTITY_RENAMES) | {o: n for o, n, *_ in EVENT_RENAMES}
        hits = sum(1 for m in mapping if m in renames)
        if hits:
            mapping = [renames.get(m, m) for m in mapping]
            with open(FAISS_MAP_PATH, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False)
        print(f"embedding 映射: {hits} 处替换 (共 {len(mapping)} 条)")

    kg.save(GRAPH_PATH)
    print("完成")


if __name__ == "__main__":
    main()
