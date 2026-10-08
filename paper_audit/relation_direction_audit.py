# relation_direction_audit.py — 静态图诊断：逐关系统计 forward/reverse 可达性
# 只读；使用与机制实验相同的生产建图路径（recipe closure controlled graph）。
import collections
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(1, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
if os.getcwd() != os.path.dirname(os.path.dirname(os.path.abspath(__file__))):
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_exp_mechanism as R   # 复用 build_graph（生产建图路径，零重复实现）


def forward_bfs_levels(kg, sources, max_hop=3):
    """有向 forward BFS：返回 {hop: set(new nodes at that hop)}。"""
    out = collections.defaultdict(set)
    seen = set(sources)
    frontier = set(sources)
    for hop in range(1, max_hop + 1):
        nxt = set()
        for n in frontier:
            for e in kg.edges:
                if e.src == n and e.dst not in seen:
                    nxt.add(e.dst)
        nxt -= seen
        out[hop] = nxt
        seen |= nxt
        frontier = nxt
        if not frontier:
            break
    return out


def main():
    kg, eng, world = R.build_graph()
    print(f"graph: nodes={len(kg.nodes)} edges={len(kg.edges)}")

    by_rel = collections.defaultdict(lambda: {
        "edges": [], "sources": set(), "targets": set()})
    for e in kg.edges:
        rec = by_rel[e.relation]
        rec["edges"].append(e)
        rec["sources"].add(e.src)
        rec["targets"].add(e.dst)

    out_deg = collections.Counter()
    in_deg = collections.Counter()
    for e in kg.edges:
        out_deg[e.src] += 1
        in_deg[e.dst] += 1

    rows = []
    for rel, rec in sorted(by_rel.items()):
        sources = sorted(rec["sources"])
        targets = sorted(rec["targets"])
        # source 节点的平均出度（它们还能往外走多少）
        src_out = [out_deg[s] for s in sources]
        # target 节点的平均出度（汇点性：0 = 无法继续传播）
        tgt_out = [out_deg[t] for t in targets]
        # forward 2-hop：从该关系的 source 集合出发，2 步 forward 新可达
        f2 = forward_bfs_levels(kg, sources, 3)
        f2new = len(f2.get(2, set())) + len(f2.get(3, set()))
        # reverse 2-hop：同结构、方向反转（只读计算，不建图）
        rev_adj = collections.defaultdict(set)
        for e in kg.edges:
            rev_adj[e.dst].add(e.src)
        seen = set(sources)
        frontier = set(sources)
        r2new = 0
        seen_r = set(sources)
        frontier_r = set(sources)
        for hop in range(1, 3):
            nxt = set()
            for n in frontier_r:
                for m in rev_adj[n]:
                    if m not in seen_r:
                        nxt.add(m)
            nxt -= seen_r
            seen_r |= nxt
            frontier_r = nxt
            if hop == 2:
                r2new = len(nxt) + (len(frontier_r) if False else 0)
        # reverse 3-hop 内全部新可达（ hop2 + hop3 ）
        r_all = len(seen_r) - len(sources)
        rows.append({
            "relation": rel,
            "edge_count": len(rec["edges"]),
            "source_count": len(sources),
            "target_count": len(targets),
            "mean_source_out_degree": round(st := (sum(src_out) / len(src_out))
                                            if src_out else 0, 3),
            "mean_target_out_degree": round(sum(tgt_out) / len(tgt_out)
                                            if tgt_out else 0, 3),
            "target_sink_fraction": round(sum(1 for x in tgt_out if x == 0)
                                          / max(len(tgt_out), 1), 3),
            "forward_2hop_reach": f2new,
            "reverse_2hop_reach": r_all,
        })
        print(f"{rel:<8} edges={len(rec['edges']):<4} src={len(sources):<3} "
              f"tgt={len(targets):<3} mean_tgt_out={rows[-1]['mean_target_out_degree']:<6} "
              f"sink_frac={rows[-1]['target_sink_fraction']:<6} "
              f"fwd2hop={f2new:<4} rev_reach={r_all}")

    out_dir = r"experiments\relation_direction_campaign"
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "relation_direction_report.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print("\nrelation_direction_report.csv written")

    # 汇总判定输入
    summary = {"graph_nodes": len(kg.nodes), "graph_edges": len(kg.edges),
               "per_relation": rows,
               "global_forward_2hop_max": max((r["forward_2hop_reach"] for r in rows),
                                              default=0)}
    with open(os.path.join(out_dir, "relation_direction_audit.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("relation_direction_audit.json written")


if __name__ == "__main__":
    main()
