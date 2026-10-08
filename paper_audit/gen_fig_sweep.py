# gen_fig_sweep.py — 共享枢纽参数扫描曲面图(论文 Fig. fig:sweep)。
# 数字来源:deliverables/experiments_new/shared_hub_sweep/sweep_summary.csv(程序化提取)。
# 设计约束:矢量 PDF;色盲友好三色(绿=FAS 优 / 橙=flat 优 / 灰=平);格内标注差值;
#           不用颜色暗示成败以外含义;marker 单路径(避免填充+描边重复矩形)。
import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "deliverables", "experiments_new", "shared_hub_sweep",
                   "sweep_summary.csv")
OUT = os.path.join(ROOT, "FAS_Paper_I", "figures")
os.makedirs(OUT, exist_ok=True)

plt.rcParams.update({"font.size": 9, "axes.titlesize": 9.5, "figure.dpi": 150,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "font.family": "DejaVu Sans"})
GREEN = "#2f855a"; ORANGE = "#dd6b20"; GRAY = "#cbd5e1"

rows = [r for r in csv.DictReader(open(SRC, encoding="utf-8"))
        if r["o3_replication"] in ("0", "0.0", "False")]
WS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
KS = [2, 3, 4, 6]
DS = [0, 1, 2]

def cell(r, k, d, w):
    for x in r:
        if int(float(x["k"])) == k and int(float(x["d"])) == d and abs(float(x["w"]) - w) < 1e-9:
            return x
    raise KeyError((k, d, w))

fig, axes = plt.subplots(1, 4, figsize=(11.2, 2.9), sharey=True)
cmap = {"fas": GREEN, "flat": ORANGE, "tie": GRAY}
for ax, k in zip(axes, KS):
    for d in DS:
        for wi, w in enumerate(WS):
            r = cell(rows, k, d, w)
            fas = float(r["fas_success"]); b2 = float(r["b2_success"])
            diff = fas - b2
            if diff > 0:
                key, alpha = "fas", 0.55 + 0.45 * min(1.0, diff / 30.0)
                txt, tc = "+%d" % diff, "#1a4d33"
            elif diff < 0:
                key, alpha = "flat", 0.55 + 0.45 * min(1.0, -diff / 30.0)
                txt, tc = "%d" % diff, "#7c3a06"
            else:
                key, alpha = "tie", 0.9
                txt, tc = "0", "#64748b"
            ax.add_patch(Rectangle((wi - 0.5, d - 0.38), 1.0, 0.76,
                                   facecolor=cmap[key], alpha=alpha, edgecolor="white", lw=1.2))
            ax.text(wi, d, txt, ha="center", va="center", fontsize=7.6, color=tc,
                    fontweight="bold" if key != "tie" else "normal")
    ax.set_xticks(range(len(WS)))
    ax.set_xticklabels(["%.1f" % w for w in WS], fontsize=7.4)
    ax.set_yticks(DS)
    ax.set_yticklabels(["%d" % d for d in DS], fontsize=7.6)
    ax.set_xlabel("hub-path edge weight $w$", fontsize=8.2)
    ax.set_title("$k=%d$ converging inputs" % k, fontsize=8.8)
    ax.set_xlim(-0.5, len(WS) - 0.5)
    ax.set_ylim(-0.55, 2.55)
    for s in ("left", "bottom"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)
axes[0].set_ylabel("private-path depth $d$", fontsize=8.6)
fig.text(0.5, -0.04,
         "Cell value: paired success difference on the joint target over 30 seeds (FAS minus closed-form flat). "
         "Green cells: all $w{\\leq}0.5$, $d{\\leq}1$; orange cells: all $w{\\geq}0.7$, $d{\\geq}1$. "
         "The original shared-hub configuration ($k{=}2$, $d{=}0$, $w{=}0.5$) replicates at FAS 30/30 vs flat 0/30.",
         ha="center", fontsize=7.4, color="#475569")
fig.tight_layout(w_pad=1.1)
fig.savefig(os.path.join(OUT, "fig_sweep.pdf"), bbox_inches="tight")
fig.savefig(os.path.join(OUT, "fig_sweep.png"), dpi=200)
fig.savefig(os.path.join(OUT, "fig_sweep.svg"), bbox_inches="tight")
print("fig: fig_sweep")
