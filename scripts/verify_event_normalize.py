# -*- coding: utf-8 -*-
"""事件头部规范化验证（2026-09-02 命名约定）。

前置：migrate_p0_2_graph.py、migrate_p0_6_activity.py、normalize_event_heads.py 均已运行。
断言：
  N1  新规范名节点在位，旧 id 不再是节点（只以 aliases 存在）
  N2  event_head_pattern 结构化标记齐全且与 id 一致
  N3  关键边已重定向（参与/涉及/时间顺序/指向）
  N4  图完整性：边端点无悬空、无重复 (src,dst,relation)
  N5  实体规范化：远方市 在位且 aliases 含 远方
  N6  前瞻事件 prospective 标记保留
  N7  embedding 映射无陈旧条目
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


RENAMES = {
    "远方之行": "旅游—地点—远方市",
    "收获日2游玩经历": "游玩—对象—收获日2游玩",
    "喝奶茶": "饮用—对象—奶茶",
    "玩Minecraft": "游玩—对象—Minecraft",
    "听歌《吸吐》": "听歌—对象—吸吐",
    "荒野行动游玩计划": "计划—对象—荒野行动",
}
PATTERNS = {
    "旅游—地点—远方市": ("旅游", "地点", "远方市"),
    "游玩—对象—收获日2游玩": ("游玩", "对象", "收获日2游玩"),
    "饮用—对象—奶茶": ("饮用", "对象", "奶茶"),
    "游玩—对象—Minecraft": ("游玩", "对象", "Minecraft"),
    "听歌—对象—吸吐": ("听歌", "对象", "吸吐"),
    "计划—对象—荒野行动": ("计划", "对象", "荒野行动"),
}


def main():
    print("=" * 60)
    print("事件头部规范化验证")
    print("=" * 60)

    from graph_model import KnowledgeGraph
    kg = KnowledgeGraph.load(os.path.join("data", "runtime_graph.json"))

    # ── N1 重命名在位 ──
    print("\nN1. 规范名节点在位 / 旧 id 退位")
    for old, new in RENAMES.items():
        node = kg.get_node(new)
        check(f"节点 {new} 存在", node is not None)
        if node is None:
            continue
        check(f"旧名 {old} 不是独立节点", kg.get_node(old) is None)
        aliases = node.extra_attrs.get("aliases", [])
        check(f"{new}.aliases 含旧名 {old}", old in aliases)

    # ── N2 结构化标记 ──
    print("\nN2. event_head_pattern 标记")
    for nid, (act, slot, ent) in PATTERNS.items():
        node = kg.get_node(nid)
        p = node.extra_attrs.get("event_head_pattern") if node is not None else None
        ok = isinstance(p, dict) and p.get("activity_type") == act \
            and p.get("aspect_slot") == slot and p.get("entity") == ent
        check(f"{nid} 命名式标记一致", ok)
        # id 与标记自洽
        if ok:
            check(f"{nid} id 与标记一致", nid == f"{act}—{slot}—{ent}")

    # ── N3 关键边重定向 ──
    print("\nN3. 关键边重定向")

    def has_edge(s, d, r):
        return any(e.src == s and e.dst == d and e.relation == r for e in kg.edges)

    check("用户 -参与→ 旅游—地点—远方市", has_edge("用户", "旅游—地点—远方市", "参与"))
    check("旅游—地点—远方市 -涉及→ 远方市", has_edge("旅游—地点—远方市", "远方市", "涉及"))
    check("用户 -参与→ 饮用—对象—奶茶", has_edge("用户", "饮用—对象—奶茶", "参与"))
    check("饮用—对象—奶茶 -涉及→ 奶茶", has_edge("饮用—对象—奶茶", "奶茶", "涉及"))
    check("游玩—对象—Minecraft -涉及→ Minecraft", has_edge("游玩—对象—Minecraft", "Minecraft", "涉及"))
    check("听歌—对象—吸吐 -涉及→ 歌曲相关实体",
          has_edge("听歌—对象—吸吐", "歌曲《吸吐》", "涉及")
          or has_edge("听歌—对象—吸吐", "吸吐", "涉及"))
    check("计划—对象—荒野行动 -涉及→ 荒野行动", has_edge("计划—对象—荒野行动", "荒野行动", "涉及"))
    check("收获日2游玩结束 -时间顺序→ 游玩—对象—收获日2游玩",
          has_edge("收获日2游玩结束", "游玩—对象—收获日2游玩", "时间顺序"))
    check("出发日群聊无动静 -时间顺序→ 旅游—地点—远方市",
          has_edge("出发日群聊无动静", "旅游—地点—远方市", "时间顺序"))

    # 焦点指针（若存在 指向 边，目标必须是现存事件）
    focus = kg.get_node("当前焦点事件")
    if focus is not None:
        pointers = [e for e in kg.edges if e.src == "当前焦点事件" and e.relation == "指向"]
        if pointers:
            check("焦点指针指向现存节点", all(e.dst in kg.nodes for e in pointers),
                  " → " + ", ".join(e.dst for e in pointers))

    # ── N4 图完整性 ──
    print("\nN4. 图完整性")
    dangling = [e for e in kg.edges if e.src not in kg.nodes or e.dst not in kg.nodes]
    check("边端点无悬空", len(dangling) == 0,
          f"悬空={len(dangling)}" + (f" 例: {dangling[0].src}-[{dangling[0].relation}]→{dangling[0].dst}" if dangling else ""))
    keys = [(e.src, e.dst, e.relation) for e in kg.edges]
    check("无重复边", len(keys) == len(set(keys)), f"重复={len(keys)-len(set(keys))}")
    check("打瓦 保留原名不改", kg.get_node("打瓦") is not None
          and "event_head_pattern" not in kg.get_node("打瓦").extra_attrs)

    # ── N5 实体规范化 ──
    print("\nN5. 实体规范化")
    check("实体 远方市 存在", kg.get_node("远方市") is not None)
    check("旧实体名 远方 不是独立节点", kg.get_node("远方") is None)
    city = kg.get_node("远方市")
    if city:
        check("远方市.aliases 含 远方", "远方" in city.extra_attrs.get("aliases", []))
    check("无节点引用旧实体名（边端点检查已覆盖）", all(
        e.src != "远方" and e.dst != "远方" for e in kg.edges))

    # ── N6 前瞻标记 ──
    print("\nN6. 前瞻事件标记")
    plan = kg.get_node("计划—对象—荒野行动")
    check("计划—对象—荒野行动 保留 prospective",
          plan is not None and plan.extra_attrs.get("prospective") is True)

    # ── N7 embedding 映射 ──
    print("\nN7. embedding 映射")
    map_path = os.path.join(ROOT, "data", "faiss", "node_mapping.json")
    if os.path.exists(map_path):
        with open(map_path, "r", encoding="utf-8") as f:
            mapping = json.load(f)
        stale = [m for m in mapping if m in RENAMES or m == "远方"]
        check("node_mapping 无陈旧条目", len(stale) == 0, f"陈旧={stale}")
    else:
        print("  [SKIP] 无缓存文件")

    print("\n" + "=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 → {failures}")
        sys.exit(1)
    print("结果: 全部通过 ✔")
    sys.exit(0)


if __name__ == "__main__":
    main()
