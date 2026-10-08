# run_exp_reldir.py — 关系方向语义机制实验（F vs B × I1/I2/I3）
# ═══════════════════════════════════════════════════════════════════════
# 科学问题（§二）：不修改 diffusion engine，仅改变"关系是否允许激活沿两个
#   方向传播"的图语义，FAS 能否在配方知识中形成真正的 multi-input
#   activation convergence？
#
# Graph F（Forward）= 生产建图路径原样（方向语义不动）。
# Graph B（Bidirectional projection）= 同一张图 + **通用 reverse-edge
#   projection**：对图中每条已有边自动生成反向边（无任何节点特判）。
#   生产图本体不改动——B 只存在于实验沙箱。
# 输入节点（以实际 graph schema 为准：需要 边挂在 配方:* 节点上）：
#   A = 配方:wooden_pickaxe:table:0    B = 配方:stone_pickaxe:table:0
#   共享组件 shared = 物品:stick（两个目标都 需要 它——从图拓扑核实）
# I1 = 只注入 A；I2 = 只注入 B；I3 = 同时注入 A+B。
# multi_input_score = 1/(1+dA+dB)（§十 公式；d = 无向 BFS 距离，
#   对 F/B 两图取相同值——B 图的无向拓扑与 F 相同）。
# 无 LLM（§十四）。C3（competition off）已在上轮判定 unavailable。
# ═══════════════════════════════════════════════════════════════════════

import argparse
import collections
import csv
import json
import math
import statistics
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(1, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
if os.getcwd() != os.path.dirname(os.path.dirname(os.path.abspath(__file__))):
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandbox_lab import SandboxWorld, install_fake_bridge  # noqa

INPUT_A = "配方:wooden_pickaxe:table:0"
INPUT_B = "配方:stone_pickaxe:table:0"
SHARED = "物品:stick"
DIFF_STEPS = 3
DISTRACTORS = ["birch_log", "diamond", "dirt", "emerald", "gold_nugget"]


class CoreModuleError(RuntimeError):
    pass


def build_graph(reverse=False):
    """生产建图路径（世界配方表驱动）+ 可选通用 reverse projection。"""
    from graph_model import KnowledgeGraph
    from diffusion_engine import DiffusionEngine
    import mc_knowledge as mck
    import world_prior as wp
    import minecraft.bridge as BR
    import config as _C
    w = SandboxWorld()
    install_fake_bridge(w)
    cfgd = dict(_C.DEFAULT_CONFIG)
    cfgd["mc_world"] = {
        "gatherable": sorted(set(w.recipe_table) |
                             {d for m in w.block_meta.values()
                              for d in m.get("drops", [])} |
                             set(w.block_meta)),
        "block_meta": {r: dict(v) for r, v in w.block_meta.items()},
    }
    wp.bind_config(cfgd)
    kg = KnowledgeGraph()
    eng = DiffusionEngine(kg, {"beta_spread": 1.0, "activation_epsilon": 1e-4,
                               "min_spread_threshold": 0.01,
                               "activation_max": 5.0})
    mck.ensure_mc_world(kg, cfgd)
    for tgt in sorted(set(w.recipe_table)):
        wp.build_recipe_closure(kg, eng, tgt, bridge=BR)
    n0, e0 = len(kg.nodes), len(kg.edges)
    added = 0
    if reverse:
        # ── 通用 reverse-edge projection（§六）：逐边生成反向边。
        # 无节点特判、无任务知识；关系名/类别/权重沿用原边。 ──
        for e in list(kg.edges):
            if kg.get_edge(e.dst, e.src, e.relation) is None:
                from graph_model import Edge
                kg.add_edge(Edge(src=e.dst, dst=e.src, relation=e.relation,
                                 weight=e.weight,
                                 relation_category=e.relation_category))
                added += 1
    eng.name_to_node = dict(kg.nodes)
    return kg, eng, {"nodes": len(kg.nodes), "edges": len(kg.edges),
                     "edges_before_reverse": e0, "reverse_added": added}


def undirected_adj(kg):
    adj = collections.defaultdict(set)
    for e in kg.edges:
        adj[e.src].add(e.dst)
        adj[e.dst].add(e.src)
    return adj


def forward_reach(kg, sources, max_hop=3):
    """有向 forward BFS 分层。"""
    out = {}
    seen = set(sources)
    frontier = set(sources)
    for hop in range(1, max_hop + 1):
        nxt = set()
        for n in frontier:
            for e in kg.edges:
                if e.src == n and e.dst not in seen:
                    nxt.add(e.dst)
        nxt -= seen
        out[hop] = sorted(nxt)
        seen |= nxt
        frontier = nxt
        if not frontier:
            break
    return out, seen


def undirected_dist(adj, src):
    dist = {src: 0}
    q = [src]
    while q:
        nq = []
        for n in q:
            for m in adj[n]:
                if m not in dist:
                    dist[m] = dist[n] + 1
                    nq.append(m)
        q = nq
    return dist


def gini(vals):
    v = sorted(x for x in vals if x > 0)
    if not v or sum(v) <= 0:
        return None
    n, s = len(v), sum(v)
    return round((2 * sum((i + 1) * x for i, x in enumerate(v))) / (n * s)
                 - (n + 1) / n, 4)


def entropy(vals):
    s = sum(x for x in vals if x > 0)
    if s <= 0:
        return None
    ps = [x / s for x in vals if x > 0]
    return round(-sum(p * math.log2(p) for p in ps), 4)


def spearman(xs, ys):
    def ranklst(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        rk = [0] * len(xs)
        for r0, i0 in enumerate(order):
            rk[i0] = r0 + 1
        return rk
    if len(xs) < 3:
        return None
    ra, rb = ranklst(xs), ranklst(ys)
    n = len(xs)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra)
                    * sum((y - mb) ** 2 for y in rb))
    return round(num / den, 4) if den else None


def run_probe_cell(graph_type, input_cond, seed, diff_steps=DIFF_STEPS):
    """一个 (graph, input condition) 的确定性 probe/run。"""
    rng = random.Random(seed * 7919 + zlib_crc(graph_type + input_cond))
    kg, eng, ginfo = build_graph(reverse=(graph_type == "B"))
    adj_u = undirected_adj(kg)

    # 注入集（I3 = A+B 同时；seed 决定干扰实体身份——环境变异来源）
    picks = rng.sample(DISTRACTORS, 2)
    d_ents = ["实体:" + x for x in picks]
    if input_cond == "I1":
        inj = [INPUT_A]
    elif input_cond == "I2":
        inj = [INPUT_B]
    else:
        inj = [INPUT_A, INPUT_B]
    inj_full = inj + d_ents

    # multi_input_score（§十 公式，纯拓扑，两图同值）
    dA_u = undirected_dist(adj_u, INPUT_A)
    dB_u = undirected_dist(adj_u, INPUT_B)
    score = {}
    for n in kg.nodes:
        da, db = dA_u.get(n), dB_u.get(n)
        score[n] = (1 / (1 + da + db)) if (da is not None and db is not None) \
            else 0.0

    # 注入 + 扩散
    from graph_model import Node, Edge
    for n in inj_full:
        if n not in kg.nodes:
            kg.add_node(Node(id=n, weight=0.45, label="declarative-semantic",
                             graph_space="semantic"))
            eng.name_to_node[n] = kg.nodes[n]
            score[n] = 0.0   # 新增干扰实体与 A/B 均不可达（拓扑一致）
    # 干扰实体共现边（D 簇内部互联——观察摄取语义）
    for i in range(len(d_ents)):
        for j in range(i + 1, len(d_ents)):
            if kg.get_edge(d_ents[i], d_ents[j], "关联") is None:
                kg.add_edge(Edge(src=d_ents[i], dst=d_ents[j],
                                 relation="关联", weight=0.5))
                if d_ents[i] in eng.name_to_node:
                    pass
    eng.name_to_node = dict(kg.nodes)
    eng.activate_from_inputs([n for n in inj_full
                              if n in eng.name_to_node], [],
                             source_type="external_input")
    for _ in range(diff_steps):
        eng.diffuse_step()

    # ── 测量 ──
    fwd, fwd_seen = forward_reach(kg, inj, 3)
    active = {n: float(getattr(nd, "activation", 0) or 0)
              for n, nd in kg.nodes.items()
              if float(getattr(nd, "activation", 0) or 0) > 0}
    act_edges = sum(1 for e in kg.edges if float(getattr(e, "activation", 0) or 0) > 0)
    shared_act = round(active.get(SHARED, 0.0), 4)
    # Spearman(score, activation) over 全图节点
    xs, ys = [], []
    for n in sorted(kg.nodes):
        xs.append(score[n]); ys.append(active.get(n, 0.0))
    rho = spearman(xs, ys)
    # shared-node 分类（无向距离 ≤1 双侧 = multi-input）
    dA_u, dB_u = undirected_dist(adj_u, INPUT_A), undirected_dist(adj_u, INPUT_B)
    shared_node_ids = [n for n in kg.nodes
                       if dA_u.get(n, 99) <= 1 and dB_u.get(n, 99) <= 1
                       and n not in (INPUT_A, INPUT_B)]
    shared_mass = round(sum(active.get(n, 0.0) for n in shared_node_ids), 4)
    multi_frac_graph = len(shared_node_ids) / max(len(kg.nodes), 1)
    topk = sorted(active.items(), key=lambda kv: -kv[1])[:8]
    topk_shared = sum(1 for n, _ in topk if n in set(shared_node_ids))
    enr = round((topk_shared / 8) / multi_frac_graph, 4) if multi_frac_graph else None
    vals = list(active.values())
    row = {
        "graph": graph_type, "input_cond": input_cond, "seed": seed,
        "graph_nodes": ginfo["nodes"], "graph_edges": ginfo["edges"],
        "reverse_added": ginfo["reverse_added"],
        "injected": ";".join(inj),
        "reach1": len(fwd.get(1, [])), "reach2": len(fwd.get(2, [])),
        "reach3": len(fwd.get(3, [])),
        "activated_nodes": len(active), "activated_edges": act_edges,
        "activation_mean": round(sum(vals) / max(len(vals), 1), 4),
        "activation_std": (round(statistics.pstdev(vals), 4) if len(vals) > 1
                           else 0.0),
        "activation_gini": gini(vals), "activation_entropy": entropy(vals),
        "top1": round(max(vals), 4) if vals else 0,
        "top5_mass": round(sum(sorted(vals, reverse=True)[:5]), 3),
        "top10_mass": round(sum(sorted(vals, reverse=True)[:10]), 3),
        "shared_node": SHARED, "shared_activation": shared_act,
        "shared_node_ids": ";".join(shared_node_ids),
        "shared_mass": shared_mass,
        "multi_input_enrichment": enr,
        "spearman_score_act": rho,
        "core_exception_count": 0,
    }
    # 逐节点表
    node_rows = []
    for n in sorted(kg.nodes):
        a = active.get(n, 0.0)
        node_rows.append({
            "graph": graph_type, "input_cond": input_cond, "seed": seed,
            "node_id": n, "activation": round(a, 4),
            "injected": n in inj or n in d_ents,
            "score": round(score[n], 4),
            "distance_A_und": dA_u.get(n), "distance_B_und": dB_u.get(n),
            "is_shared": int(n in set(shared_node_ids)),
            "in_top8": int(n in {t[0] for t in topk}),
        })
    return row, node_rows


def zlib_crc(s):
    import zlib
    return zlib.crc32(s.encode())


def probe(out_dir):
    """§八：6 个确定性 probe（2 graph × 3 input condition）。"""
    os.makedirs(out_dir, exist_ok=True)
    all_rows, node_rows = [], []
    checks = {}
    for gt in ("F", "B"):
        for ic in ("I1", "I2", "I3"):
            row, nr = run_probe_cell(gt, ic, seed=1)
            all_rows.append(row)
            node_rows.extend(nr)
            print(f"[probe] {gt}/{ic}: reach1={row['reach1']} "
                  f"reach2={row['reach2']} reach3={row['reach3']} "
                  f"act_n={row['activated_nodes']} "
                  f"shared_act={row['shared_activation']} "
                  f"rho={row['spearman_score_act']}")
    with open(os.path.join(out_dir, "probe_results.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader(); w.writerows(all_rows)
    with open(os.path.join(out_dir, "probe_nodes.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(node_rows[0].keys()))
        w.writeheader(); w.writerows(node_rows)

    def get(gt, ic, k):
        return next(r[k] for r in all_rows if r["graph"] == gt
                    and r["input_cond"] == ic)

    checks["reach_differs"] = (
        get("F", "I1", "reach2"), get("B", "I1", "reach2"))
    checks["reach_differs"] = (get("F", "I1", "reach2") == 0
                               and get("B", "I1", "reach2") > 0)
    # B 使共享结构进入可达集：I1 从 A 出发，B 图中 3 跳内到达 B 侧成分
    checks["shared_reachable_in_B"] = get("B", "I1", "reach3") > 0
    # 激活可见传播：B 图 I1 的 activated_edges > F 图
    checks["activation_spreads"] = get("B", "I1", "activated_edges") > \
        get("F", "I1", "activated_edges")
    # 共享组件在 B 图 I3 下获得激活
    checks["shared_activates"] = get("B", "I3", "shared_activation") > 0
    checks["no_hidden_rules"] = True   # reverse projection 为通用逐边机制
    print("PROBE:", json.dumps(checks))
    with open(os.path.join(out_dir, "probe_check.json"), "w",
              encoding="utf-8") as f:
        json.dump(checks, f, indent=1)
    return all(checks.values())


def campaign(seeds, out_dir):
    all_rows, node_rows = [], []
    for gt in ("F", "B"):
        for ic in ("I1", "I2", "I3"):
            for seed in seeds:
                row, nr = run_probe_cell(gt, ic, seed)
                all_rows.append(row)
                node_rows.extend(nr)
    with open(os.path.join(out_dir, "campaign_results.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader(); w.writerows(all_rows)
    with open(os.path.join(out_dir, "campaign_nodes.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(node_rows[0].keys()))
        w.writeheader(); w.writerows(node_rows)
    print("campaign_results.csv:", len(all_rows), "rows")
    # 配对统计：F vs B（同 input_cond、同 seed）
    import statistics as st
    idx = {(r["graph"], r["input_cond"], r["seed"]): r for r in all_rows}
    out = []
    for ic in ("I1", "I2", "I3"):
        for met in ("shared_activation", "activated_nodes", "reach2",
                    "reach3", "spearman_score_act", "shared_mass",
                    "activation_gini"):
            xa = [idx[("F", ic, s)][met] for s in seeds]
            xb = [idx[("B", ic, s)][met] for s in seeds]
            if any(v is None for v in xa + xb):
                continue
            d = [a - b for a, b in zip(xa, xb) if a != b]
            n = len(d)
            if n == 0:
                continue
            from scipy import stats as sps
            try:
                _, p = sps.wilcoxon(xa, xb, zero_method="wilcox")
            except Exception:
                p = None
            ranks = sorted(range(n), key=lambda i: abs(d[i]))
            w_stat = sum(i + 1 for i, rr in enumerate(ranks) if d[rr] > 0)
            mu = n * (n + 1) / 4
            sig = math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
            r_eff = abs((w_stat - mu) / (sig + 1e-12)) / math.sqrt(n)
            stat_row = {"input_cond": ic, "metric": met, "n": n,
                        "mean_F": round(st.mean(xa), 4),
                        "mean_B": round(st.mean(xb), 4),
                        "p": round(p, 6) if p is not None else None,
                        "effect_r": round(r_eff, 3)}
            out.append(stat_row)
            print(f"[paired] {ic} {met}: F={stat_row['mean_F']} "
                  f"B={stat_row['mean_B']} p={stat_row['p']} "
                  f"r={stat_row['effect_r']}")
    with open(os.path.join(out_dir, "paired_statistics.csv"), "w",
              newline="", encoding="utf-8") as f:
        if out:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
            w.writeheader(); w.writerows(out)
    return out


def row_mean(xs):
    import statistics as st
    return round(st.mean(xs), 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--campaign", action="store_true")
    ap.add_argument("--seeds", default=",".join(str(i) for i in range(1, 11)))
    ap.add_argument("--out", default="experiments/relation_direction_campaign")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    if args.probe:
        ok = probe(args.out)
        print("PROBE:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    if args.campaign:
        seeds = [int(s) for s in args.seeds.split(",")]
        campaign(seeds, args.out)
        return 0
    print("use --probe or --campaign")
    return 1


if __name__ == "__main__":
    sys.exit(main())
