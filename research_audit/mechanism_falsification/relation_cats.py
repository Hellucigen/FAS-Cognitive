# relation_cats.py — A9 关系方向语义扩展实验(机制级增量,2026-09-30)
# ============================================================================
# 规格: research_audit/_audits/experiment_A9_direction_extension.md(含 2.1
# 前置预声明修订)。同族于 falsify.py 的 3E 拓扑实验,但类目→结构画像非空化:
#
#   类目集(5):  recipe(深链 hops=3, S7 已知对照)
#                temporal(深链 hops=3, 生产时间顺序 1614 边在案)
#                cognitive / emotion / social(浅 hops=2)
#   处理:        treatment = 该类目移入双向白名单(全图 bidirectional)
#                control   = 生产现状(全部 forward)
#   同构:        同 seed 同 N 同 inputs 同 hops → 唯一差异=方向旗标
#   规模:        N ∈ {50,200} × inputs ∈ {2,5} × seeds 0..19 → 400 对
#   指标(预声明): jcg / mrr_joint / recall@1 / sink_fraction(新)
#                (+ margin_vs_best_single / rho_topology 记但不作主检验)
#   主检验:      5 类目 × 4 指标 = 20 配对 Wilcoxon(Bonferroni α=0.0025);
#                N/inputs 分层只作描述量。
# 输出: raw_results_a9.jsonl / a9_summary.csv / a9_stats.json(不动既有通道)
# 运行: python relation_cats.py [--workers 10] [--seeds 20] [--limit 0]
# ============================================================================

import argparse
import json
import math
import os
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reference import Graph, GEdge  # noqa: E402
import metrics as M  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw_results_a9.jsonl")
CSV = os.path.join(HERE, "a9_summary.csv")
STATS = os.path.join(HERE, "a9_stats.json")

SEED_STRENGTH = 5.0
DECAY = 0.05
BUDGET_RATIO = 0.5
TRANSFER = 1.0
MAX_STEPS = 3

# 类目 → (hops 画像, 中文名)
CATEGORIES = [
    ("recipe", 3, "配方(深链)"),
    ("temporal", 3, "时间顺序(深链)"),
    ("cognitive", 2, "认知(浅)"),
    ("emotion", 2, "情绪(浅)"),
    ("social", 2, "社交(浅)"),
]


def build_cat_graph(N, inputs, hops, direction, seed, candidates=8):
    """构造受控任务图:与 3E 同构的做法,唯一变量 = 方向旗标。

    JOINT 路径 hops 跳(深画像:输入→m0→m1→C0),SINGLE 每输入 1 条独立链,
    DISTRACTOR 与输入无结构化路径,填充链 + 泄漏边 + 候选直达背景噪声,
    与 generator.random_task 的构造精神一致(fwd/bidir 不消费 rng,保证
    同 seed 两处理严格同构)。
    """
    rng = random.Random(seed)
    g = Graph()
    filler = [f"n{i}" for i in range(N)]
    iids = [f"I{i}" for i in range(inputs)]

    def wire(a, b, w=None, bidir=None):
        w = 0.5 if w is None else w
        if bidir is None:
            bidir = {"forward": False, "bidirectional": True}[direction]
        g.add_edge(str(a), str(b), float(w), bool(bidir))

    mids = []
    for j, iid in enumerate(iids):
        prev = iid
        for h in range(hops - 1):
            mid = f"m{j}_{h}"
            mids.append(mid)
            wire(prev, mid)
            prev = mid
        wire(prev, "C0")
    cids = [f"C{i}" for i in range(candidates)]
    for j, iid in enumerate(iids):
        mid = f"s{j}"
        wire(iid, mid)
        wire(mid, cids[1 + j])
    for cid in cids[len(iids) + 1:]:
        pick = rng.choice(filler)
        wire(pick, cid, w=rng.uniform(0.2, 0.9))
    all_task = iids + cids + mids + [f"s{j}" for j in range(inputs)]
    for i in range(1, len(filler)):
        wire(filler[i - 1], filler[i], w=rng.uniform(0.3, 0.8))
        if rng.random() < 0.25:
            wire(filler[i], rng.choice(all_task or [filler[i]]),
                 w=rng.uniform(0.2, 0.9))
    for j in range(inputs):
        if rng.random() < 0.7:
            wire(iids[j], rng.choice(filler), w=rng.uniform(0.3, 0.9))
    meta = {"inputs": iids, "joint": "C0",
            "singles": cids[1:1 + inputs], "distractors": cids[1 + inputs:]}
    return g, meta


def sink_fraction(g, cand_ids):
    """预声明新指标:候选集中'0 条可发射边'(无 fwd 出边且无 bidir 入镜像)占比。

    直量生产 L2-RD-01 'target 永远当不了源' 现象在机制模型的投影:
    bidir 迁移后,原汇点获得反向发射能力 → sink_fraction 下降。
    """
    n_sink = 0
    for c in cand_ids:
        out_cnt = len(g.out.get(c, ()))
        inn_bidir = sum(1 for e in g.inn.get(c, ()) if e.bidirectional)
        if out_cnt == 0 and inn_bidir == 0:
            n_sink += 1
    return round(n_sink / len(cand_ids), 4) if cand_ids else None


def run_pair(params, seed):
    """一对同构 run:(control=forward, treatment=bidirectional)。"""
    cat, hops, N, inputs, direction = params["category"], params["hops"], \
        params["N"], params["inputs"], params["direction"]
    g, meta = build_cat_graph(N, inputs, hops, direction, seed)
    iids = meta["inputs"]; joint = meta["joint"]
    singles = meta["singles"]; distractors = meta["distractors"]

    def act_for(seeds):
        import copy
        g2 = Graph()
        for nid in g.nodes:
            g2.add_node(nid)
        for src, edges in g.out.items():
            for e in edges:
                g2.add_edge(e.src, e.dst, e.weight, e.bidirectional)
        a, _ = g2.diffuse_round(steps=MAX_STEPS, seeds=seeds,
                                seed_strength=SEED_STRENGTH,
                                ratio=BUDGET_RATIO, transfer=TRANSFER,
                                decay=DECAY)
        return a

    a_multi = act_for(iids)
    a_singles = [act_for([s]) for s in iids]
    act_j = a_multi.get(joint, 0.0)
    jcg = M.jcg(act_j, [a.get(joint, 0.0) for a in a_singles])
    rm = M.rank_metrics(a_multi, joint, singles, distractors)
    run = {
        "category": cat, "hops": hops, "direction": direction,
        "N": N, "inputs": inputs, "seed": seed,
        "jcg": jcg,
        "mrr_joint": rm["mrr_joint"],
        "recall@1": rm["recall@1"],
        "margin_vs_best_single": rm["margin_vs_best_single"],
        "sink_fraction": sink_fraction(g, [joint] + list(singles) + list(distractors)),
        "rho_topology": M.rank_corr(
            a_multi, lambda c: _topo_score(g, c, iids),
            singles + [joint] + distractors),
    }
    return run


def _topo_score(g, cand, iids):
    from generator import topology_score
    return topology_score(g, cand, iids)


def build_plan(seeds):
    plan = []
    for cat, hops, _zh in CATEGORIES:
        for N in (50, 200):
            for inputs in (2, 5):
                for seed in range(seeds):
                    for direction in ("forward", "bidirectional"):
                        plan.append(("cat", {
                            "category": cat, "hops": hops, "N": N,
                            "inputs": inputs, "direction": direction,
                        }, seed))
    return plan


def main():
    ap = argparse.ArgumentParser(description="A9 关系方向扩展(机制级)")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--keep", action="store_true", help="不清空旧 raw 输出")
    args = ap.parse_args()

    plan = build_plan(args.seeds)
    if args.limit:
        plan = plan[:args.limit]
    manifest = {
        "experiment": "A9 relation-direction extension",
        "predeclared_metrics": ["jcg", "mrr_joint", "recall@1",
                                "sink_fraction(新)", "margin_vs_best_single",
                                "rho_topology"],
        "primary_tests": "5 类目 × 4 指标 = 20 配对 Wilcoxon, Bonferroni α=0.0025",
        "structure": "同构:同 seed/N/inputs/hops 唯一差异=方向旗标",
        "categories": [{"cat": c, "hops": h, "zh": z} for c, h, z in CATEGORIES],
        "params": {"seed_strength": SEED_STRENGTH, "decay": DECAY,
                   "budget_ratio": BUDGET_RATIO, "transfer": TRANSFER,
                   "max_steps": MAX_STEPS},
        "plan_size": len(plan),
        "seeds": args.seeds,
        "predeclared": True,
        "date": time.strftime("%Y-%m-%d"),
        "spec": "research_audit/_audits/experiment_A9_direction_extension.md",
    }

    # 前检:同构(同 seed 两处理唯一差异=方向旗标)+ 确定性,破坏即拒绝运行
    _g1, _ = build_cat_graph(50, 2, 3, "forward", 0)
    _g2, _ = build_cat_graph(50, 2, 3, "bidirectional", 0)
    _t1 = sum(len(v) for v in _g1.out.values())
    _t2 = sum(len(v) for v in _g2.out.values())
    if _t1 != _t2 or sorted(_g1.nodes) != sorted(_g2.nodes):
        sys.exit("前检失败:同 seed 两处理结构不一致,拒绝运行")
    _g3, _ = build_cat_graph(50, 2, 3, "forward", 0)
    if sum(len(v) for v in _g3.out.values()) != _t1:
        sys.exit("前检失败:同参重建结构漂移(确定性破坏),拒绝运行")

    # 预注册 manifest 可做,但原始结果文件绝不能覆盖既有 663 runs 通道
    if not args.keep and os.path.exists(RAW):
        os.remove(RAW)

    t0 = time.time()
    results = []
    with open(RAW, "w", encoding="utf-8") as f:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = []
            for kind, params, seed in plan:
                fut = ex.submit(run_pair, params, seed)
                futs.append((params, seed, fut))
            done = 0
            for params, seed, fut in futs:
                try:
                    r = fut.result()
                except Exception as e:
                    r = {"category": params["category"], "direction": params["direction"],
                         "N": params["N"], "inputs": params["inputs"],
                         "seed": seed, "error": repr(e)}
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                results.append(r)
                done += 1
    print(f"rows={len(results)} elapsed={time.time() - t0:.1f}s", file=sys.stderr)

    # ── 分析:20 主检验(两处理配对,按类目聚合 N/inputs) ──
    ok = [r for r in results if "error" not in r]
    stats_out = {"primary_tests": [], "descriptive": {}}
    for cat, hops, zh in CATEGORIES:
        keep = [r for r in ok if r["category"] == cat]
        fwd = {m: {} for m in ("jcg", "mrr_joint", "recall@1", "sink_fraction")}
        bid = {m: {} for m in ("jcg", "mrr_joint", "recall@1", "sink_fraction")}
        for r in keep:
            key = (r["N"], r["inputs"], r["seed"])
            if r["direction"] == "forward":
                for m in fwd:
                    fwd[m][key] = r.get(m)
            else:
                for m in bid:
                    bid[m][key] = r.get(m)
        for m in ("jcg", "mrr_joint", "recall@1", "sink_fraction"):
            d1, d2 = [], []
            for k in sorted(fwd[m]):
                if k in bid[m] and fwd[m][k] is not None and bid[m][k] is not None:
                    d1.append(fwd[m][k]); d2.append(bid[m][k])
            p, r_eff = M.wilcoxon_signed_rank(d1, d2)  # d2−d1 = bidir−fwd
            mean_diff = round(
                statistics.mean([b - a for a, b in zip(d1, d2)], ) if d2 else None, 6)
            sig = p < 0.0025
            stats_out["primary_tests"].append({
                "category": cat, "metric": m, "n_pairs": len(d1),
                "mean_fwd": round(statistics.mean(d1), 6) if d1 else None,
                "mean_bidir": round(statistics.mean(d2), 6) if d2 else None,
                "mean_diff_bidir_minus_fwd": mean_diff,
                "wilcoxon_p": round(p, 6), "effect_r": round(r_eff, 6),
                "significant_at_0.0025": sig,
            })
    # N/inputs 分层描述量(不作主检验)
    for r in ok:
        r["metric_group"] = None
    for N in (50, 200):
        for inputs in (2, 5):
            for cat, _, _zh in CATEGORIES:
                cell = [r for r in ok
                        if r["N"] == N and r["inputs"] == inputs
                        and r["category"] == cat and "error" not in r]
                fwd_j = [r["jcg"] for r in cell if r["direction"] == "forward"]
                bid_j = [r["jcg"] for r in cell if r["direction"] == "bidirectional"]
                stats_out["descriptive"][f"{cat}_N{N}_in{inputs}"] = {
                    "mean_jcg_fwd": round(statistics.mean(fwd_j), 5) if fwd_j else None,
                    "mean_jcg_bidir": round(statistics.mean(bid_j), 5) if bid_j else None,
                    "n": len(fwd_j),
                }
    with open(STATS, "w", encoding="utf-8") as f:
        json.dump({"manifest": manifest, **stats_out}, f,
                  ensure_ascii=False, indent=2)

    # CSV 汇总(人读层面)
    with open(CSV, "w", encoding="utf-8", newline="") as f:
        import csv
        w = csv.writer(f)
        w.writerow(["category", "metric", "n_pairs", "mean_fwd", "mean_bidir",
                    "mean_diff", "wilcoxon_p", "effect_r", "sig@0.0025"])
        for t in stats_out["primary_tests"]:
            w.writerow([t["category"], t["metric"], t["n_pairs"],
                        t["mean_fwd"], t["mean_bidir"], t["mean_diff_bidir_minus_fwd"],
                        t["wilcoxon_p"], t["effect_r"], t["significant_at_0.0025"]])

    print(json.dumps(stats_out["primary_tests"], ensure_ascii=False, indent=1),
          file=sys.stderr)


if __name__ == "__main__":
    main()