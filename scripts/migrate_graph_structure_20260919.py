# scripts/migrate_graph_structure_20260919.py — 图谱架构对齐一次性迁移
# ============================================================================
# 配合 2026-09-19 架构对齐修复（Haru 去枢纽化 / 当前状态中间节点 /
# CurrentActivity 词表 / 关系本体收敛 / 类型系统修复）。
#
# 步骤：
#   0. 备份 runtime_graph.json（同时保留一份 A/B baseline 副本）
#   1. Haru 节点改型 label=self / graph_space=self（具身主体）
#   2. 删除 Haru 全部白名单外的直连边（思考关联/反思涉及/Unknown靠近/CI基于…）
#   3. 建 当前Minecraft状态 中间节点，重锚 7 个状态槽位
#   4. 类型修复：Unknown*→semantic、CI_*→intention、行为:/情境:/倾向:→disposition、
#      反思_*(旧版无 space)→episodic、删除死节点 Haru游戏状态
#   5. 日期节点 → 事件 extra_attrs["event_time"]，删除日期节点
#   6. 全量关系归一（291 种自由文本 → 规范词表）+ 类别重导 + 三元组去重
#
# 幂等：所有步骤重复执行安全（删除类天然幂等，新增类走 get_edge 守卫）。
# 运行： python scripts/migrate_graph_structure_20260919.py [--dry-run]
# ============================================================================

import json
import os
import shutil
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_model import KnowledgeGraph, Node, Edge  # noqa: E402
import graph_schema as gs  # noqa: E402

GRAPH_PATH = os.path.join("data", "runtime_graph.json")
AB_BASELINE = os.path.join("data", "runtime_graph.json.bak.ab_baseline_20260919")

STATE_HUB = "当前Minecraft状态"
SLOTS = ["Haru的位置", "Haru的血量", "Haru的饥饿", "Haru的手持物",
         "附近的玩家", "附近的方块", "附近的生物"]

# 迁移后允许以 Haru 为端点的规范关系（与 graph_schema.HUB_ALLOWED["Haru"] 一致）
HARU_ALLOWED = gs.HUB_ALLOWED["Haru"]


def stats(kg):
    deg = Counter()
    for e in kg.edges:
        deg[e.src] += 1
        deg[e.dst] += 1
    rels = Counter(e.relation for e in kg.edges)
    labels = Counter(n.label for n in kg.nodes.values())
    spaces = Counter(n.graph_space for n in kg.nodes.values())
    return {
        "nodes": len(kg.nodes), "edges": len(kg.edges),
        "distinct_relations": len(rels),
        "single_use_relations": sum(1 for v in rels.values() if v == 1),
        "haru_degree": deg.get("Haru", 0),
        "self_degree": deg.get("Self", 0),
        "labels": dict(labels), "spaces": dict(spaces),
        "top_relations": rels.most_common(15),
    }


def main(dry_run=False):
    # ── 0. 备份 ──
    if os.path.exists(GRAPH_PATH):
        shutil.copy2(GRAPH_PATH, GRAPH_PATH + ".bak.pre_structure_20260919")
        if not os.path.exists(AB_BASELINE):
            shutil.copy2(GRAPH_PATH, AB_BASELINE)
            print(f"[备份] A/B baseline → {AB_BASELINE}")
        print("[备份] 迁移前快照 → .bak.pre_structure_20260919")

    kg = KnowledgeGraph.load(GRAPH_PATH)
    before = stats(kg)
    print(f"[迁移前] {before['nodes']} 节点 / {before['edges']} 边 / "
          f"{before['distinct_relations']} 种关系 / Haru 度 {before['haru_degree']}")

    removed_edges = Counter()
    label_fixes = Counter()

    # ── 1. Haru 改型 ──
    haru = kg.nodes.get("Haru")
    if haru is not None:
        haru.label = "self"
        haru.graph_space = "self"
        haru.extra_attrs.setdefault("type", "embodied_self")
        label_fixes["Haru→self"] += 1

    # ── 2. Haru 直连边治理（白名单外全删）──
    # 入边只留 名字叫；出边只留 当前活动/认知焦点/当前状态→状态hub。
    # 旧 Haru-[当前状态]->槽位 出边一律删（第 3 步重锚到 当前Minecraft状态）。
    keep = []
    for e in kg.edges:
        if e.src == "Haru" or e.dst == "Haru":
            rel = gs.normalize_relation(e.relation)
            allowed = (e.dst == "Haru" and rel in {"名字叫"}) \
                or (e.src == "Haru" and rel in {"当前活动", "认知焦点"}) \
                or (e.src == "Haru" and rel == "当前状态" and e.dst == STATE_HUB)
            if not allowed:
                removed_edges[f"{e.relation}({e.src[:12]}→{e.dst[:12]})"] += 1
                continue
        keep.append(e)
    kg.edges = keep

    # ── 3. 当前Minecraft状态 中间节点 + 槽位重锚 ──
    if STATE_HUB not in kg.nodes:
        kg.add_node(Node(
            id=STATE_HUB, weight=0.6, label="declarative-semantic",
            graph_space="self",
            extra_attrs={"type": "embodiment_state", "canon": "state",
                         "desc": "Haru 在 Minecraft 中的当前具身状态"}))
    if "Haru" in kg.nodes and not kg.get_edge("Haru", STATE_HUB, "当前状态"):
        kg.add_edge(Edge(src="Haru", dst=STATE_HUB, relation="当前状态",
                         weight=0.8, relation_category="cognitive_relation"))
    for slot in SLOTS:
        if slot in kg.nodes:
            # 旧直连边已在第 2 步删除（当前状态 不在 Haru 入边白名单内时…注意：
            # Haru-[当前状态]->槽位 是 Haru 出边，白名单只放行 →STATE_HUB 的，
            # 上面循环已把指向旧槽位的 当前状态 出边删掉）
            if not kg.get_edge(STATE_HUB, slot, "状态项"):
                kg.add_edge(Edge(src=STATE_HUB, dst=slot, relation="状态项",
                                 weight=0.6, relation_category="cognitive_relation"))
    print(f"[状态] 当前Minecraft状态 已建，槽位重锚 {sum(1 for s in SLOTS if s in kg.nodes)}/7")

    # ── 4. 类型修复 ──
    def relabel(nid_prefix, new_label, new_space=None):
        """按 id 前缀修 label/space。new_label/new_space 传 None 表示跳过该字段。"""
        for nid, n in kg.nodes.items():
            if nid.startswith(nid_prefix):
                if new_label and n.label != new_label:
                    n.label = new_label
                    label_fixes[f"{nid_prefix}*→{new_label}"] += 1
                if new_space and n.graph_space != new_space:
                    n.graph_space = new_space
                    label_fixes[f"{nid_prefix}*→space:{new_space}"] += 1

    relabel("Unknown", "declarative-semantic", "semantic")
    relabel("CI_", "intention", "self")
    relabel("行为:", "disposition", "self")
    relabel("情境:", "disposition", "self")
    relabel("倾向:", "disposition", "self")
    relabel("反思_", None, "episodic")          # 旧版反思节点缺 space
    relabel("eye_text_", None, "episodic")
    relabel("ear_text_", None, "episodic")
    relabel("思考_", None, "cognitive")          # 兜底（正常已是 cognitive）
    # 历史遗留：情绪节点无 space → cognitive
    for nid, n in kg.nodes.items():
        if (n.extra_attrs or {}).get("type") == "emotion" and n.graph_space == "semantic":
            n.graph_space = "cognitive"
            label_fixes["emotion→space:cognitive"] += 1
    # 历史遗留：episodic label + semantic space 的错位 → episodic
    for nid, n in kg.nodes.items():
        if n.label == "declarative-episodic" and n.graph_space == "semantic":
            n.graph_space = "episodic"
            label_fixes[f"episodic错位→space:episodic"] += 1
    # 死节点：Phase A 单 blob 状态
    if "Haru游戏状态" in kg.nodes:
        kg.remove_node("Haru游戏状态")
        label_fixes["删除 Haru游戏状态"] += 1

    # ── 5. 日期节点 → 事件属性 ──
    date_nodes = [nid for nid in kg.nodes if gs.DATE_NODE_RE.match(nid)]
    for d in date_nodes:
        for e in list(kg.get_in_edges(d)):
            src_node = kg.nodes.get(e.src)
            if src_node is not None:
                ea = src_node.extra_attrs = src_node.extra_attrs or {}
                if not str(ea.get("event_time", "")).strip():
                    ea["event_time"] = d
        kg.remove_node(d)
    if date_nodes:
        print(f"[时间] 日期节点转属性并删除: {len(date_nodes)} 个（{date_nodes[:5]}…）")

    # ── 6. 关系归一 + 类别重导 + 三元组去重 ──
    rel_before = Counter(e.relation for e in kg.edges)
    for e in kg.edges:
        e.relation = gs.normalize_relation(e.relation)
        e.relation_category = gs.category_for(e.relation)
    # 去重：同 (src,dst,rel) 保留列表序第一条（最早入图），合并权重/激活取 max
    seen = {}
    dedup_keep, dup_removed = [], 0
    for e in kg.edges:
        key = (e.src, e.dst, e.relation)
        first = seen.get(key)
        if first is None:
            seen[key] = e
            dedup_keep.append(e)
        else:
            first.weight = max(first.weight, e.weight)
            first.activation = max(first.activation, e.activation)
            dup_removed += 1
    kg.edges = dedup_keep
    rel_after = Counter(e.relation for e in kg.edges)
    print(f"[关系] 归一: {len(rel_before)} 种 → {len(rel_after)} 种；"
          f"去重删除重复三元组 {dup_removed} 条")

    # ── 收尾 ──
    kg.rebuild_indexes()
    after = stats(kg)

    report = {
        "before": before, "after": after,
        "removed_haru_edges": dict(removed_edges),
        "removed_haru_edge_total": sum(removed_edges.values()),
        "label_fixes": dict(label_fixes),
        "date_nodes_removed": len(date_nodes),
        "duplicate_edges_removed": dup_removed,
        "relation_reduction": f"{before['distinct_relations']} → {after['distinct_relations']}",
        "schema_stats": gs.relation_surface_stats(),
    }
    print("\n===== 迁移报告 =====")
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("before", "after")},
                     ensure_ascii=False, indent=2))
    print(f"\n[迁移后] {after['nodes']} 节点 / {after['edges']} 边 / "
          f"{after['distinct_relations']} 种关系 / Haru 度 {after['haru_degree']}")
    print(f"[Haru 出边] {[(e.relation, e.dst) for e in kg.get_out_edges('Haru')]}")
    print(f"[Haru 入边] {[(e.src, e.relation) for e in kg.get_in_edges('Haru')]}")

    if not dry_run:
        with open(os.path.join("data", "structure_migration_report_20260919.json"),
                  "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        kg.save(GRAPH_PATH)
        print(f"\n[保存] {GRAPH_PATH}（缩小保险会自动留副本）")
    else:
        print("\n[dry-run] 未写盘")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
