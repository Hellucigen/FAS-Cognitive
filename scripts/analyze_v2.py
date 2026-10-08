# analyze_v2.py — 正式 campaign 配对统计（复用 v1 方法：Wilcoxon + 效应量 r）
import csv
import math
import collections
import os

import numpy as np
from scipy import stats as sps

ROOT = r"E:\Project\Fascinator"
SRC = os.path.join(ROOT, r"experiments\core_routing_v2\campaign\summary.csv")
OUT = os.path.join(ROOT, r"experiments\core_routing_v2\campaign\statistics.csv")

rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
data = collections.defaultdict(lambda: collections.defaultdict(dict))
for r in rows:
    data[r["condition"]][r["task"]][int(r["seed"])] = r

CONDS = ["fas_full", "llm_direct", "llm_history", "llm_rag", "random_ctx",
         "D-noact", "D-nodemand", "D-flat"]
TASKS = ["T1", "T2", "T3", "T4"]
ABL_TASKS = {"D-noact": ["T2", "T3"], "D-nodemand": ["T2", "T3"],
             "D-flat": ["T2", "T3"]}
METRICS = ["success", "decisions", "target_utilization", "distractor_rate",
           "t_reroute", "obsolete_actions", "wasted_eats",
           "retrieval_precision", "prompt_tokens", "efficiency"]
BETTER = {"success": "higher", "decisions": "lower", "target_utilization": "higher",
          "distractor_rate": "lower", "t_reroute": "lower",
          "obsolete_actions": "lower", "wasted_eats": "lower",
          "retrieval_precision": "higher", "prompt_tokens": "lower",
          "efficiency": "higher"}


def paired_wilcoxon(x, y):
    d = [a - b for a, b in zip(x, y) if a != b]
    n = len(d)
    if n == 0:
        return None, None, None
    ranks = sorted(range(n), key=lambda i: abs(d[i]))
    w_stat = sum(i + 1 for i, rr in enumerate(ranks) if d[rr] > 0)
    mu = n * (n + 1) / 4
    sigma = math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
    z = (w_stat - mu) / (sigma + 1e-12)
    try:
        stat, p = sps.wilcoxon(x, y, zero_method="wilcox")
    except Exception:
        p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return w_stat, p, abs(z) / math.sqrt(n)


comparisons = [("fas_full", c) for c in
               ("llm_direct", "llm_history", "llm_rag", "random_ctx")]
comparisons += [("fas_full", a) for a in ("D-noact", "D-nodemand", "D-flat")]

out = []
n_tests = sum(len(TASKS if a in CONDS[:5] else ABL_TASKS[b]) * len(METRICS)
              for a, b in comparisons)
print(f"total paired tests: {n_tests}")
for a, b in comparisons:
    tasks = TASKS if b in CONDS[:5] else ABL_TASKS[b]
    for task in tasks:
        for met in METRICS:
            xa, xb = [], []
            for s in range(1, 21):
                ra = data.get(a, {}).get(task, {}).get(s)
                rb = data.get(b, {}).get(task, {}).get(s)
                if not ra or not rb:
                    continue
                va, vb = ra.get(met, ""), rb.get(met, "")
                if va in ("", None) or vb in ("", None):
                    continue
                xa.append(float(va)); xb.append(float(vb))
            if len(xa) < 10:
                continue
            W, p, r = paired_wilcoxon(xa, xb)
            bonf = min(1.0, p * n_tests) if p is not None else None
            row = {"condition_a": a, "condition_b": b, "task": task,
                   "metric": met, "n": len(xa),
                   "mean_a": round(sum(xa) / len(xa), 4),
                   "mean_b": round(sum(xb) / len(xb), 4),
                   "sd_a": round(float(np.std(xa, ddof=1)), 4),
                   "sd_b": round(float(np.std(xb, ddof=1)), 4),
                   "p": round(p, 6) if p is not None else None,
                   "effect_r": round(r, 3) if r is not None else None,
                   "p_bonferroni": round(bonf, 6) if bonf is not None else None,
                   "better_direction": BETTER[met]}
            out.append(row)
            star = "*" if (p is not None and p < 0.05) else ""
            ps = f"{row['p']:.4f}" if row["p"] is not None else "  n/a"
            rs = f"{row['effect_r']:.3f}" if row["effect_r"] is not None else "n/a"
            print(f"{a+' vs '+b:<30}{task:<6}{met:<21}"
                  f"{row['mean_a']:>9}{row['mean_b']:>9}"
                  f"{ps:>9}{rs:>7}{len(xa):>4}  {star}")

with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
    w.writeheader(); w.writerows(out)
print("\nstatistics.csv:", len(out), "rows; tests:", n_tests)
