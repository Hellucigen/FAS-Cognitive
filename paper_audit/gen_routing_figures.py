# gen_routing_figures.py — routing campaign 图表（从 summary.csv/statistics.csv）
import csv, os
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "experiments", "core_routing", "summary.csv")
OUT = os.path.join(ROOT, "experiments", "core_routing", "figures")
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
COL = {"llm_direct": "#999999", "llm_history": "#7f8fa6", "llm_rag": "#2b6cb0",
       "fas_full": "#dd6b20", "random_ctx": "#c53030",
       "D-noact": "#b7791f", "D-nodemand": "#805ad5", "D-flat": "#319795"}
LAB = {"llm_direct": "A Direct", "llm_history": "B History", "llm_rag": "C RAG",
       "fas_full": "D FAS", "random_ctx": "E Random",
       "D-noact": "D −diffusion", "D-nodemand": "D −demand/route", "D-flat": "D flat-graph"}

rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
data = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
for r in rows:
    data[r["condition"]][r["task"]][int(r["seed"])] = r
conds = [c for c in ("llm_direct", "llm_history", "llm_rag", "fas_full", "random_ctx")
         if c in data]
tasks = sorted({r["task"] for r in rows})

def seedvals(c, t, met):
    return [float(v[met]) for v in data[c][t].values() if v.get(met) not in ("", None)]

def save(fig, name):
    fig.savefig(os.path.join(OUT, name), bbox_inches="tight"); plt.close(fig)
    print("fig:", name)

# Fig: success + distractor by task x condition（逐 seed 点）
fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.0))
for ax, met, ylab in ((axes[0], "success", "task success"), (axes[1], "distractor_rate", "distractor rate")):
    w = 0.16
    for i, c in enumerate(conds):
        means, xs, pts = [], [], []
        for j, t in enumerate(tasks):
            v = seedvals(c, t, met)
            if not v:
                continue
            means.append(sum(v) / len(v))
            xs.append(j + (i - len(conds) / 2) * w)
            pts.append((xs[-1], v))
        for x0, v in pts:
            ax.scatter([x0] * len(v), v, s=6, color=COL[c], alpha=0.5, zorder=3)
        if xs:
            ax.bar(xs, means, width=w * 0.9, color=COL[c], alpha=0.55, label=LAB.get(c, c), zorder=2)
    ax.set_xticks(range(len(tasks))); ax.set_xticklabels(tasks)
    ax.set_ylabel(ylab); ax.legend(fontsize=6, ncol=2, frameon=False)
save(fig, "fig_main_comparison.pdf")

# Fig: reroute latency (T3)
fig, ax = plt.subplots(figsize=(4.2, 2.8))
for i, c in enumerate(conds):
    v = seedvals(c, "T3", "t_reroute")
    if not v:
        continue
    ax.scatter([i] * len(v), v, s=14, color=COL[c], alpha=0.7, zorder=3)
    ax.bar(i, sum(v) / len(v), width=0.5, color=COL[c], alpha=0.35, zorder=2)
ax.set_xticks(range(len(conds))); ax.set_xticklabels([LAB.get(c, c) for c in conds], fontsize=7, rotation=20)
ax.set_ylabel("T_reroute (decisions after env change)"); ax.set_title("Re-routing latency, T3 (dots = seeds)")
save(fig, "fig_reroute.pdf")

# Fig: context efficiency（success per 1k prompt tokens）
fig, ax = plt.subplots(figsize=(4.2, 2.8))
for i, c in enumerate(conds):
    v = []
    for t in tasks:
        v += seedvals(c, t, "efficiency")
    if not v:
        continue
    ax.scatter([i] * len(v), v, s=12, color=COL[c], alpha=0.7, zorder=3)
    ax.bar(i, sum(v) / len(v), width=0.5, color=COL[c], alpha=0.35, zorder=2)
ax.set_xticks(range(len(conds))); ax.set_xticklabels([LAB.get(c, c) for c in conds], fontsize=7, rotation=20)
ax.set_ylabel("success / 1k input tokens"); ax.set_title("Context efficiency (all tasks)")
save(fig, "fig_efficiency.pdf")

# Fig: ablation（T2/T3）
abls = [c for c in ("fas_full", "D-noact", "D-nodemand", "D-flat") if c in data]
if abls:
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 2.8))
    for ax, met, ylab in ((axes[0], "distractor_rate", "distractor rate"),
                          (axes[1], "target_utilization", "target utilization")):
        w = 0.2
        for i, c in enumerate(abls):
            means, xs, pts = [], [], []
            for j, t in enumerate(("T2", "T3")):
                v = seedvals(c, t, met)
                if not v:
                    continue
                means.append(sum(v) / len(v)); xs.append(j + (i - len(abls) / 2) * w)
                pts.append((xs[-1], v))
            for x0, v in pts:
                ax.scatter([x0] * len(v), v, s=10, color=COL[c], alpha=0.6, zorder=3)
            if xs:
                ax.bar(xs, means, width=w * 0.9, color=COL[c], alpha=0.55, label=LAB.get(c, c), zorder=2)
        ax.set_xticks(range(2)); ax.set_xticklabels(["T2 distractor", "T3 reroute"])
        ax.set_ylabel(ylab); ax.legend(fontsize=6, frameon=False)
    save(fig, "fig_ablation.pdf")
print("done ->", OUT)
