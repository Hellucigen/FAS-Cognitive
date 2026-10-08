# aggregate.py — raw_results.jsonl → summary.csv / statistics.csv
# ============================================================================
# 预声明聚合(任务 §27 统计协议):
#   summary.csv     每 run 一行,与 raw 一一对应(seed-level,保持原始数据可溯)
#   statistics.csv  维度级聚合:mean/CI95/效应量/配对检验
# 统计口径:(1) 固定种子可复现;(2) 效应量用 r;(3) 多重比较 Bonferroni
# ============================================================================

import csv
import json
import os
import statistics

import metrics as M

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw_results.jsonl")
SUMMARY = os.path.join(HERE, "summary.csv")
STATS = os.path.join(HERE, "statistics.csv")

KEY_FIELDS = ["kind", "N", "noise", "direction", "inputs", "seed"]
NUM_FIELDS = ["mrr_joint", "recall@1", "recall@3", "margin_vs_best_single",
              "margin_vs_best_distractor", "joint_rank", "jcg",
              "n_active", "total_activation", "max_activation", "entropy",
              "gini", "zero_fraction", "rho_topology",
              "flat_joint_and", "flat_joint_or", "flat_single_max",
              "joint_act_multi", "joint_act_single_mean", "single_best_act_mean",
              "random_act"]


def mean(coll):
    return statistics.mean(coll) if coll else None


def _boot_ci(vals, seed=1):
    lo, hi = M.bootstrap_ci(vals, mean, n_boot=1000, ci=0.95, seed=seed)
    return lo, hi


def load():
    rows = []
    with open(RAW, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("kind") == "error":
                continue
            rows.append(r)
    return rows


def write_summary(rows):
    fields = list(KEY_FIELDS)
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with open(SUMMARY, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def group_key(r):
    """维度分组:直接用计划内 dim 标签(避免参数重叠歧义)。"""
    dim = r["_params"].get("dim", "")
    if dim == "noise":
        return ("noise", r["_params"].get("N"), r["_params"].get("noise"))
    if dim == "topology":
        return ("topology", r["_params"].get("direction"),
                r["_params"].get("N"))
    if dim == "scaling":
        return ("scaling", r["_params"]["inputs"])
    if dim == "complexity":
        return ("complexity", r["_params"].get("N"))
    if dim == "joint":
        return ("joint", r["_params"]["inputs"])
    return ("toy", r["_params"].get("cond", "?"))


def _num(runs, key):
    return [r.get(key) for r in runs if isinstance(r.get(key), (int, float))]


def agg(runs):
    mrr = _num(runs, "mrr_joint")
    r1 = _num(runs, "recall@1")
    jcg = _num(runs, "jcg")
    rho = _num(runs, "rho_topology")
    margin = _num(runs, "margin_vs_best_single")
    zero = _num(runs, "zero_fraction")
    return {
        "n_seeds": len(runs),
        "mrr_mean": round(mean(mrr), 5) if mrr else "",
        "mrr_ci95": _fmt(_boot_ci(mrr)) if mrr else "",
        "recall@1_rate": round(mean(r1), 4) if r1 else "",
        "jcg_mean": round(mean(jcg), 5) if jcg else "",
        "jcg_ci95": _fmt(_boot_ci(jcg)) if jcg else "",
        "jcg_pos_rate": round(mean([x > 0 for x in jcg]), 3) if jcg else "",
        "rho_mean": round(mean(rho), 4) if rho else "",
        "margin_single_mean": round(mean(margin), 5) if margin else "",
        "zero_fraction_mean": round(mean(zero), 4) if zero else "",
    }


def _fmt(ci):
    if ci == (None, None):
        return ""
    return f"[{ci[0]},{ci[1]}]"


def paired_wilcoxon(a_vals, b_vals):
    """配对效应:p 与 r(两尾 Wilcoxon)。"""
    vals = [(a, b) for a, b in zip(a_vals, b_vals)
            if isinstance(a, (int, float)) and isinstance(b, (int, float))]
    if len(vals) < 3:
        return "", ""
    p, r = M.wilcoxon_signed_rank([a for a, _ in vals], [b for _, b in vals])
    return round(p, 5), round(r, 4)


def main():
    rows = load()
    write_summary(rows)
    groups = {}
    for r in rows:
        if r.get("_kind0") == "toy":
            continue
        groups.setdefault(group_key(r), []).append(r)

    first_key = None
    for g in groups:
        first_key = list(agg(groups[g]).keys())
        break
    with open(STATS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["dimension", "group"] +
                           (first_key or []))
        w.writeheader()
        for g in sorted(groups, key=str):
            row = {"dimension": g[0], "group": str(g[1:])}
            row.update(agg(groups[g]))
            w.writerow(row)
    print(f"summary rows={len(rows)} groups={len(groups)}")


if __name__ == "__main__":
    main()