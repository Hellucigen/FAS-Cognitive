# graph_integrity_audit.py — 图谱结构完整性审计器
# ============================================================================
# 架构对齐（2026-09-19）§十八：结构治理不能只靠一次性迁移——每次启动/定期
# 跑一遍审计，hub 污染、关系词失控、类型错位会立刻现形。
#
# 检查项：
#   1. Hub pollution   — 主体节点（Haru/Self/用户）与状态/活动节点的度数，
#                        以及全图度数 Top-N
#   2. Haru 直连边     — 白名单分类：allowed / suspicious / invalid
#   3. Relation 本体   — 非规范关系、单次关系、类别与词表不一致的边
#   4. 节点原子性      — X—Y—Z 复合命名、纯日期节点、超长 id
#   5. 类型一致性      — label 与 graph_space 的矛盾组合
#   6. 扩散可达性      — 从关键节点 BFS 的逐跳覆盖率（结构性发散指标）
#
# 运行： python graph_integrity_audit.py [--graph data/runtime_graph.json] [--json 出路径]
# 也可 import: audit_graph(kg) -> dict
# ============================================================================

import argparse
import json
import os
import sys
from collections import Counter, defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import graph_schema as gs                     # noqa: E402
from graph_model import KnowledgeGraph        # noqa: E402

# 期望空间：label → 允许的 graph_space 集合（None = 不限）
LABEL_SPACE_RULES = {
    "self": {"self"},
    "disposition": {"self"},
    "intention": {"self"},
    "declarative-episodic": {"episodic", "self", "cognitive"},
    "procedural": {"cognitive", "semantic"},
    "infrastructure": None,
    "declarative-semantic": None,
}

HUBS = ["Haru", "Self", "用户", "当前Minecraft状态"]
ACTIVITY_POINTER_REL = "当前活动"


def audit_graph(kg) -> dict:
    out_edges = defaultdict(list)
    in_edges = defaultdict(list)
    degree = Counter()
    for e in kg.edges:
        out_edges[e.src].append(e)
        in_edges[e.dst].append(e)
        degree[e.src] += 1
        degree[e.dst] += 1

    report = {}

    # ── 1. Hub pollution ──
    hub_report = {}
    for h in HUBS:
        if h in kg.nodes:
            hub_report[h] = {
                "degree": degree.get(h, 0),
                "out": len(out_edges.get(h, [])),
                "in": len(in_edges.get(h, [])),
            }
    # 当前活动指针：Haru-[当前活动]-> 的目标节点
    for e in out_edges.get("Haru", []):
        if e.relation == ACTIVITY_POINTER_REL:
            hub_report["当前活动节点"] = {
                "id": e.dst, "degree": degree.get(e.dst, 0),
                "status": (kg.nodes.get(e.dst, None).extra_attrs or {}).get("status")
                if e.dst in kg.nodes else None,
            }
    hub_report["_top10"] = [
        {"id": nid, "degree": d, "label": kg.nodes[nid].label if nid in kg.nodes else "?"}
        for nid, d in degree.most_common(10)]
    report["hubs"] = hub_report

    # ── 2. Haru 直连边分类 ──
    allowed = gs.HUB_ALLOWED["Haru"]
    haru_edges = {"allowed": [], "suspicious": [], "invalid": []}
    for e in list(out_edges.get("Haru", [])) + list(in_edges.get("Haru", [])):
        rel = e.relation
        item = {"rel": rel, "edge": f"{e.src} -[{rel}]-> {e.dst}", "w": round(e.weight, 2)}
        if rel in allowed:
            haru_edges["allowed"].append(item)
        elif rel in {"关联", "涉及", "靠近"}:
            haru_edges["invalid"].append(item)
        else:
            haru_edges["suspicious"].append(item)
    report["haru_edges"] = haru_edges

    # ── 3. Relation 本体 ──
    rel_counter = Counter(e.relation for e in kg.edges)
    unknown_rels = {r: c for r, c in rel_counter.items()
                    if not gs.is_canonical_relation(r)}
    single_use = {r: c for r, c in rel_counter.items() if c == 1}
    cat_mismatch = []
    for e in kg.edges:
        expect = gs.category_for(e.relation)
        if e.relation_category != expect:
            cat_mismatch.append(f"{e.src}-[{e.relation}]->{e.dst}: "
                                f"{e.relation_category} ≠ {expect}")
    report["relations"] = {
        "distinct": len(rel_counter),
        "single_use_count": len(single_use),
        "single_use_relations": dict(list(sorted(single_use.items()))[:40]),
        "unknown_relations": unknown_rels,
        "category_mismatch_count": len(cat_mismatch),
        "category_mismatch_sample": cat_mismatch[:20],
    }

    # ── 4. 原子性 ──
    compound = [nid for nid in kg.nodes if gs.COMPOUND_NODE_RE.match(nid)]
    dates = [nid for nid in kg.nodes if gs.DATE_NODE_RE.match(nid)]
    long_ids = [nid for nid in kg.nodes
                if len(nid) > gs.SUSPICIOUS_ID_LEN
                and not gs.COMPOUND_NODE_RE.match(nid)
                and not nid.startswith(("Unknown", "UnknownConcept", "思考_", "反思_",
                                        "CI_", "行动_", "自主行动_", "活动_", "经历_",
                                        "ActionRequest_", "ActionIntent_", "Capability_",
                                        "回答记录_", "搜索记录_", "文件操作记录_"))
                and (" " in nid or "：" in nid or len(nid) > 26)]
    report["atomicity"] = {
        "compound_nodes": compound,
        "date_nodes": dates,
        "suspicious_long_ids": sorted(long_ids)[:30],
    }

    # ── 5. 类型一致性 ──
    from graph_model import VALID_LABELS
    bad_label, bad_combo = [], []
    for nid, n in kg.nodes.items():
        if n.label not in VALID_LABELS:
            bad_label.append(f"{nid}: {n.label}")
        allowed_spaces = LABEL_SPACE_RULES.get(n.label)
        if allowed_spaces is not None and n.graph_space not in allowed_spaces:
            bad_combo.append(f"{nid}: label={n.label} space={n.graph_space}")
    report["types"] = {
        "invalid_labels": bad_label[:20],
        "label_space_conflicts": bad_combo[:30],
        "label_space_conflict_count": len(bad_combo),
    }

    # ── 6. 扩散可达性（BFS，双向）──
    adj = defaultdict(set)
    for e in kg.edges:
        adj[e.src].add(e.dst)
        adj[e.dst].add(e.src)

    def bfs(seed, hops):
        seen = {seed}
        frontier = {seed}
        for _ in range(hops):
            nxt = set()
            for u in frontier:
                nxt |= adj.get(u, set()) - seen
            seen |= nxt
            frontier = nxt
        return seen

    reach = {}
    n_total = len(kg.nodes)
    for seed in HUBS + ["僵尸", "Minecraft"]:
        if seed not in kg.nodes:
            continue
        reach[seed] = {str(h): f"{len(bfs(seed, h))}/{n_total}"
                       f"({len(bfs(seed, h)) * 100 // max(n_total, 1)}%)"
                       for h in (1, 2, 3)}
    report["reachability"] = reach

    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default=os.path.join("data", "runtime_graph.json"))
    ap.add_argument("--json", default=None, help="同时写出 JSON 报告")
    args = ap.parse_args()

    kg = KnowledgeGraph.load(args.graph)
    report = audit_graph(kg)

    print(f"===== 图谱完整性审计: {args.graph} =====")
    print(f"规模: {len(kg.nodes)} 节点 / {len(kg.edges)} 边\n")

    print("── Hub 度数 ──")
    for h, v in report["hubs"].items():
        if h == "_top10":
            continue
        print(f"  {h}: {v}")
    print("  全图度数 Top10:")
    for item in report["hubs"]["_top10"]:
        print(f"    {item['degree']:4d}  [{item['label'][:22]}] {item['id'][:40]}")

    he = report["haru_edges"]
    print(f"\n── Haru 直连边: allowed={len(he['allowed'])} "
          f"suspicious={len(he['suspicious'])} invalid={len(he['invalid'])} ──")
    for k in ("invalid", "suspicious"):
        for item in he[k][:8]:
            print(f"    [{k}] {item['edge']}")

    r = report["relations"]
    print(f"\n── 关系本体: {r['distinct']} 种，单次 {r['single_use_count']}，"
          f"非规范 {len(r['unknown_relations'])}，类别错配 {r['category_mismatch_count']} ──")
    if r["unknown_relations"]:
        print(f"    非规范: {list(r['unknown_relations'].items())[:15]}")
    for m in r["category_mismatch_sample"][:8]:
        print(f"    错配: {m}")

    a = report["atomicity"]
    print(f"\n── 原子性: 复合命名 {len(a['compound_nodes'])}，"
          f"日期节点 {len(a['date_nodes'])}，可疑长 id {len(a['suspicious_long_ids'])} ──")
    for nid in a["compound_nodes"][:8]:
        print(f"    复合: {nid}")

    t = report["types"]
    print(f"\n── 类型: 非法 label {len(t['invalid_labels'])}，"
          f"label×space 矛盾 {t['label_space_conflict_count']} ──")
    for c in t["label_space_conflicts"][:10]:
        print(f"    {c}")

    print("\n── 扩散可达性（BFS 双向，1/2/3 跳）──")
    for seed, cov in report["reachability"].items():
        print(f"  {seed}: " + "  ".join(f"{h}跳 {v}" for h, v in cov.items()))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n[JSON] {args.json}")

    # 退出码：invalid 直连边 / 非规范关系 / 矛盾类型 任一存在 → 1（CI 可用）
    bad = (len(he["invalid"]) + len(r["unknown_relations"])
           + t["label_space_conflict_count"] + len(a["date_nodes"]))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
