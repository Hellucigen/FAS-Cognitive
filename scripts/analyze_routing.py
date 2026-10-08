# analyze_routing.py — PHASE 5: 配对统计（Wilcoxon signed-rank + 效应量）
# 输入 experiments/core_routing/summary.csv；输出 statistics.csv + 摘要
import csv, json, math, os, sys
from collections import defaultdict

import numpy as np

try:
    from scipy import stats as sps
    HAVE_SCIPY = True
except Exception:
    HAVE_SCIPY = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "experiments", "core_routing", "summary.csv")
OUT = os.path.join(ROOT, "experiments", "core_routing", "statistics.csv")

MAIN = ["llm_direct", "llm_history", "llm_rag", "fas_full", "random_ctx"]
ABL = ["D-noact", "D-nodemand", "D-flat"]
METRICS = ["success", "decisions", "target_utilization", "distractor_rate",
           "t_reroute", "obsolete_actions", "wasted_eats",
           "retrieval_precision", "prompt_tokens", "efficiency"]
# 每指标的方向（routing 假设下"更好"的方向）
BETTER = {"success": "higher", "decisions": "lower", "target_utilization": "higher",
          "distractor_rate": "lower", "t_reroute": "lower",
          "obsolete_actions": "lower", "wasted_eats": "lower",
          "retrieval_precision": "higher", "prompt_tokens": "lower",
          "efficiency": "higher"}


def paired_wilcoxon(x, y):
    """x,y 配对样本。返回 (W, p, r 效应量)。x>y 记正向。"""
    d = [a - b for a, b in zip(x, y) if a != b]
    n = len(d)
    if n == 0:
        return None, None, None
    ranks = sorted(range(n), key=lambda i: abs(d[i]))
    w = sum(i + 1 for i, r in enumerate(ranks) if d[r] > 0)
    mu = n * (n + 1) / 4
    sigma = math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
    z = (w - mu) / (sigma + 1e-12)
    if HAVE_SCIPY:
        try:
            stat, p = sps.wilcoxon(x, y, zero_method="wilcox")
            return stat, p, abs(z) / math.sqrt(n)
        except Exception:
            pass
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))  # 正态近似
    return w, p, abs(z) / math.sqrt(n)


def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
    data = defaultdict(lambda: defaultdict(dict))   # cond->task->seed->row
    for r in rows:
        data[r["condition"]][r["task"]][int(r["seed"])] = r
    tasks = sorted({r["task"] for r in rows})
    seeds = sorted({int(r["seed"]) for r in rows})

    out = []
    print(f"conditions={list(data.keys())} tasks={tasks} seeds={len(seeds)} scipy={HAVE_SCIPY}")
    print(f"{'comparison':<28}{'task':<26}{'metric':<20}{'A(mean)':>9}{'B(mean)':>9}{'p':>8}{'r':>7}{'n_pairs':>8}")
    comparisons = [(d, c) for d in ("fas_full",) for c in
                   ("llm_direct", "llm_history", "llm_rag", "random_ctx")]
    comparisons += [("fas_full", a) for a in ABL]
    for a, b in comparisons:
        for task in tasks:
            for met in METRICS:
                xa, xb = [], []
                for s in seeds:
                    ra = data.get(a, {}).get(task, {}).get(s)
                    rb = data.get(b, {}).get(task, {}).get(s)
                    if not ra or not rb:
                        continue
                    va, vb = ra.get(met, ""), rb.get(met, "")
                    if va in ("", None) or vb in ("", None):
                        continue
                    xa.append(float(va)); xb.append(float(vb))
                if len(xa) < 5:
                    continue
                W, p, r = paired_wilcoxon(xa, xb)
                row = {"condition_a": a, "condition_b": b, "task": task,
                       "metric": met, "n": len(xa),
                       "mean_a": round(sum(xa) / len(xa), 4),
                       "mean_b": round(sum(xb) / len(xb), 4),
                       "sd_a": round(np.std(xa, ddof=1), 4) if len(xa) > 1 else 0,
                       "sd_b": round(np.std(xb, ddof=1), 4) if len(xb) > 1 else 0,
                       "p": round(p, 5) if p is not None else "",
                       "effect_r": round(r, 3) if r is not None else "",
                       "better": BETTER.get(met, "")}
                out.append(row)
                print(f"{a+' vs '+b:<28}{task:<26}{met:<20}"
                      f"{row['mean_a']:>9}{row['mean_b']:>9}"
                      f"{str(row['p']):>8}{str(row['effect_r']):>7}{len(xa):>8}")
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        wtr = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        wtr.writeheader()
        wtr.writerows(out)
    print("written", OUT)


if __name__ == "__main__":
    main()
