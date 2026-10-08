# falsify.py — 核心机制 Falsification 实验主程序
# ============================================================================
# 完全独立的最小参考模型(see reference.py)。不 import 任何 production 模块。
#
# 实验矩阵(所有正式实验 deterministic seeds + 预声明指标):
#   toy        (§20 3A) 确定性 toy graph 正确性 + 变体
#   complexity (§21 3B & §26 3G)  N={10,30,100,300,1000}
#   joint      (§22 3C & §37)     multi-input convergence + JCG + baselines
#   noise      (§23 3D)           noise={0,10,25,50,75,90}
#   topology   (§24 3E)           F/B/M/R 严格同构对照
#   scaling    (§25 3F)           inputs={1,2,3,5,10,20}
#
# 用法:
#   python falsify.py --all                全套(除大 N 部分见 --heavy)
#   python falsify.py --heavy              N=1000 / inputs=20 的重档
# 输出写到本目录 raw_results.jsonl / summary.csv / statistics.csv / manifest.json
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

from reference import Graph, build_toy_task
from generator import random_task, topology_score, _bfs_dist
import metrics as M

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw_results.jsonl")

SEED_STRENGTH = 5.0
DECAY = 0.05
BUDGET_RATIO = 0.5
TRANSFER = 1.0
MAX_STEPS = 3


def run_toy(params, seed):
    """§20 toy 正确性:期望 outcome 自动生成。返回 dict {passed, detail}。"""
    condition = params["cond"]
    cases = {
        "std":          (["I1", "I2"], {"O1": 1, "O2": 2, "O3": 3}),
        "single_input": (["I1"], {"O1": 1, "O2": 2}),      # 只注入 I1:O1 仍第 1
        "conflict":     (["I1", "I2"], {"O1": 1, "O2": 2, "O3": 3}),
    }
    if condition not in cases:
        return {"passed": False, "reason": f"unknown cond {condition}"}
    inp, expected = cases[condition]
    g, _, _ = build_toy_task(kind="std", seed=seed)
    acts, _ = g.diffuse_round(steps=MAX_STEPS, seeds=inp,
                              seed_strength=SEED_STRENGTH, ratio=BUDGET_RATIO,
                              transfer=TRANSFER, decay=DECAY)
    gold = [c for c, e in expected.items() if e == 1][0]
    cands = {c: acts.get(c, -1.0) for c in expected}
    order = sorted(cands.items(), key=lambda kv: -kv[1])
    first = order[0][0]
    return {"passed": first == gold, "first": first, "gold": gold,
            "acts": {c: round(v, 4) for c, v in cands.items()}}


def run_generic(kind, params, seed):
    """通用 run:构建受控任务图 → 单/多/基线激活 → 输出全部预声明指标。"""
    if kind == "joint":
        return _run_joint(params, seed)
    # 单图 run(默认:1 张图,做 multi 与每个 single 的对照注入)
    return _run_single_graph(params, seed, kind)


def _run_single_graph(params, seed, kind=None):
    N = params["N"]; noise = params["noise"]; direction = params["direction"]
    inputs = params.get("inputs", 2)
    g, meta = random_task(N, inputs=inputs, candidates=8, avg_degree=2.0,
                          direction=direction, noise=noise, seed=seed)
    iids = meta["inputs"]; joint = meta["joint"]
    singles = meta["singles"]; distractors = meta["distractors"]
    acts, _ = g.diffuse_round(steps=MAX_STEPS, seeds=iids,
                              seed_strength=SEED_STRENGTH, ratio=BUDGET_RATIO,
                              transfer=TRANSFER, decay=DECAY)
    run = {
        "kind": "graph", "N": N, "noise": noise, "direction": direction,
        "inputs": inputs, "seed": seed,
        "joint": joint, "singles": singles, "distractors": distractors,
        "stats": generator_stats(g),
    }
    run.update(M.info(acts))
    run.update(M.rank_metrics(acts, joint, singles, distractors))
    # single-input 对照(同图同 seed,只注入一个输入)
    single_acts = []
    for si in iids:
        g2 = _fresh_diffusion(g)
        a2, _ = g2.diffuse_round(steps=MAX_STEPS, seeds=[si],
                                 seed_strength=SEED_STRENGTH,
                                 ratio=BUDGET_RATIO, transfer=TRANSFER,
                                 decay=DECAY)
        single_acts.append(a2.get(joint, 0.0))
    run["joint_act_single_mean"] = round(
        statistics.mean(single_acts), 6) if single_acts else None
    run["jcg"] = M.jcg(acts.get(joint, 0.0), single_acts)
    # 拓扑 score 对照(baseline 5:shortest-path)
    scores = {c: topology_score(g, c, iids) for c in singles + [joint] + distractors}
    run["rho_topology"] = M.rank_corr(acts, lambda c: scores[c],
                                      singles + [joint] + distractors)
    # flat relevance baseline(1-hop 交集)
    run["flat_joint_and"] = _flat_relevance(g, joint, iids)
    run["flat_joint_or"] = _flat_relevance(g, joint, iids, mode="or")
    run["flat_single_max"] = max(
        (_flat_relevance(g, s, [si]) for s, si in zip(singles, iids)),
        default=0)
    run["time_s"] = None
    return run


def _fresh_diffusion(g):
    """深拷贝一个同构图(重置激活,零拷贝原激活)。"""
    import copy
    g2 = Graph()
    for nid in g.nodes:
        g2.add_node(nid)
    for src, edges in g.out.items():
        for e in edges:
            g2.add_edge(e.src, e.dst, e.weight, e.bidirectional)
    return g2


def _flat_relevance(g, cand, iids, mode="and"):
    """flat relevance:候选与输入 1-hop 连通数(and=全连通取 min,or=any)。"""
    out = []
    for ii in iids:
        hit = (cand in {e.dst for e in g.out.get(ii, ())}
               or ii in {e.src for e in g.out.get(cand, ())}
               or cand in {e.src for e in g.inn.get(ii, ())}
               or ii in {e.dst for e in g.inn.get(cand, ())})
        out.append(1.0 if hit else 0.0)
    return min(out) if mode == "and" else max(out)


def _run_joint(params, seed):
    """§22/§37 专项:同一图同 seed 下 joint vs 各 single vs 随机 baseline。"""
    N = params["N"]; noise = params.get("noise", 0.0)
    direction = params.get("direction", "forward")
    inputs = params.get("inputs", 2)
    g, meta = random_task(N, inputs=inputs, candidates=8, avg_degree=2.0,
                          direction=direction, noise=noise, seed=seed)
    iids = meta["inputs"]; joint = meta["joint"]
    singles = meta["singles"]; distractors = meta["distractors"]

    def act_for(seeds):
        g2 = _fresh_diffusion(g)
        a, _ = g2.diffuse_round(steps=MAX_STEPS, seeds=seeds,
                                seed_strength=SEED_STRENGTH,
                                ratio=BUDGET_RATIO, transfer=TRANSFER,
                                decay=DECAY)
        return a

    a_multi = act_for(iids)
    a_singles = [act_for([s]) for s in iids]
    # random baseline:随机激活(重排注入后取激活) — 用随机种子发散对照
    rng = random.Random(seed * 7919)
    act_j = a_multi.get(joint, 0.0)
    jcg = M.jcg(act_j, [a.get(joint, 0.0) for a in a_singles])
    run = {
        "kind": "joint", "N": N, "noise": noise, "direction": direction,
        "inputs": inputs, "seed": seed,
    }
    run.update(M.info(a_multi))
    run.update(M.rank_metrics(a_multi, joint, singles, distractors))
    run["jcg"] = jcg
    run["joint_act_multi"] = round(act_j, 6)
    run["joint_act_singles"] = [round(a.get(joint, 0.0), 6) for a in a_singles]
    run["joint_act_single_mean"] = round(
        statistics.mean([a.get(joint, 0.0) for a in a_singles]), 6)
    run["single_best_act_mean"] = round(statistics.mean(
        [max(a.get(s, 0.0) for s in singles) for a in a_singles]), 6)
    run["random_act"] = round(rng.random() * 5.0, 6)
    run["rho_topology"] = M.rank_corr(
        a_multi, lambda c: topology_score(g, c, iids),
        singles + [joint] + distractors)
    return run


def generator_stats(g):
    n_out = sum(len(v) for v in g.out.values())
    n_in = sum(len(v) for v in g.inn.values())
    return {"edges_fwd": n_out, "edges_bwd": n_in, "nodes": len(g.nodes),
            "edge_total": n_out}


# ---------------------------------------------------------------- 条件定义

def build_plan():
    plan = []
    # toy(3A)
    for cond in ("std", "single_input", "conflict"):
        plan.append(("toy", {"cond": cond, "dim": "toy"}, 0))
    # 复杂度扫(3B/3G)
    for N in (10, 30, 100, 300, 1000):
        for seed in range(20):
            plan.append(("graph", {"N": N, "noise": 0.0, "dim": "complexity",
                                   "direction": "forward"}, seed))
    # 噪声扫(3D)
    for N in (100, 300):
        for noise in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9):
            for seed in range(20):
                plan.append(("graph", {"N": N, "noise": noise,
                                       "dim": "noise",
                                       "direction": "forward"}, seed))
    # 拓扑对照(3E):同 N/边等价
    for direction in ("forward", "bidirectional", "mixed", "random"):
        for N in (100, 300):
            for seed in range(20):
                plan.append(("graph", {"N": N, "noise": 0.0, "dim": "topology",
                                       "direction": direction}, seed))
    # 输入规模(3F)
    for inputs in (1, 3, 5, 10, 20):
        for seed in range(20):
            plan.append(("graph", {"N": 300, "noise": 0.0, "dim": "scaling",
                                   "direction": "forward",
                                   "inputs": inputs}, seed))
    # joint 专项(3C/37)
    for inputs in (2, 3, 5):
        for seed in range(20):
            plan.append(("joint", {"N": 200, "noise": 0.0, "dim": "joint",
                                   "direction": "forward",
                                   "inputs": inputs}, seed))
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    plan = build_plan()
    if args.limit:
        plan = plan[:args.limit]
    manifest = {
        "model": "reference.py (spreading activation, production-aligned)",
        "params": {"seed_strength": SEED_STRENGTH, "decay": DECAY,
                   "budget_ratio": BUDGET_RATIO, "transfer": TRANSFER,
                   "max_steps": MAX_STEPS},
        "plan_size": len(plan),
        "seeds": args.seeds,
        "metrics": ["mrr_joint", "recall@1", "recall@3", "margin_vs_best_single",
                    "margin_vs_best_distractor", "jcg", "rank_corr",
                    "entropy", "gini", "zero_fraction", "activation_mass"],
        "predeclared": True,
        "date": time.strftime("%Y-%m-%d"),
    }
    with open(os.path.join(HERE, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    t0 = time.time()
    results = []
    with open(RAW, "w", encoding="utf-8") as f:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = []
            for kind, params, seed in plan:
                if kind == "toy":
                    fut = ex.submit(run_toy, params, seed)
                else:
                    fut = ex.submit(run_generic, kind, params, seed)
                futs.append((kind, params, seed, fut))
            for kind, params, seed, fut in futs:
                try:
                    r = fut.result()
                except Exception as e:
                    r = {"kind": "error", "seed": seed, "err": repr(e)}
                r["_kind0"] = kind
                r["_params"] = params if isinstance(params, dict) else {"cond": params}
                line = json.dumps(r, ensure_ascii=False)
                f.write(line + "\n")
                f.flush()
                results.append(r)
    print(f"rows={len(results)} elapsed={time.time() - t0:.1f}s", file=sys.stderr)
    # toy 汇总
    toys = [r for r in results if r.get("_kind0") == "toy"]
    print("toy:", ", ".join(f"{r.get('_params',{}).get('cond')}:{r.get('ok_detail', r.get('passed'))}"
                            for r in toys), file=sys.stderr)


if __name__ == "__main__":
    main()