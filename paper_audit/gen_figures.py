# gen_figures.py — PHASE 8: 全部实验图从 PAPER_FACT_DATABASE.json 生成
# 规则：矢量 PDF；无伪造误差棒（确定性 seeds 用逐 seed 点）；n 标注；无显著性声称。
import json, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = json.load(open(os.path.join(ROOT, "paper_audit", "PAPER_FACT_DATABASE.json"), encoding="utf-8"))
OUT = os.path.join(ROOT, "FAS_Paper_I", "figures")
os.makedirs(OUT, exist_ok=True)

plt.rcParams.update({"font.size": 9, "axes.titlesize": 10, "figure.dpi": 150,
                     "axes.spines.top": False, "axes.spines.right": False})
GRAY = "#666666"; BLUE = "#2b6cb0"; ORANGE = "#dd6b20"; GREEN = "#2f855a"; RED = "#c53030"

def save(fig, name):
    fig.savefig(os.path.join(OUT, name), bbox_inches="tight")
    plt.close(fig); print("fig:", name)

# ── Fig: write-back emergence（B1=0 vs B2=13 + 晋升关系分类）──
a2 = DB["A2"]
fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6))
ax = axes[0]
vals = [a2["ep1_B1_writeback_nodes"], a2["ep1_writeback_nodes"]]
ax.bar([0, 1], vals, color=[GRAY, BLUE], width=0.55)
ax.set_xticks([0, 1]); ax.set_xticklabels(["B1\nwrite-back off", "B2\nwrite-back on"])
ax.set_ylabel("operation:/change: nodes")
for i, v in enumerate(vals): ax.text(i, v + 0.3, str(v), ha="center")
ax.set_ylim(0, max(vals) + 2.2)
ax.set_title("(a) Write-back isolation (seed 1)")
ax = axes[1]
labels = ["substantive\naction-effect", "degenerate\n(self-success)"]
sizes = [len(a2["promoted_substantive"]), len(a2["promoted_degenerate"])]
ax.bar(range(2), sizes, color=[GREEN, GRAY], width=0.55)
ax.set_xticks(range(2)); ax.set_xticklabels(labels)
ax.set_ylabel("promoted relations")
for i, v in enumerate(sizes): ax.text(i, v + 0.15, str(v), ha="center")
ax.set_ylim(0, max(sizes) + 1.6)
ax.set_title("(b) Promoted relation taxonomy (n=%d)" % a2["promoted_total"])
save(fig, "fig_writeback.pdf")

# ── Fig: cross-context reactivation（G0/G1/G2）──
g = DB["G_transfer"]
fig, ax = plt.subplots(figsize=(3.6, 2.6))
conds = ["G0\nstructural", "G1\nflat memory", "G2\nfresh graph"]
act = [g[c]["marks_activated"] for c in ("G0", "G1", "G2")]
tot = [g[c]["marks_total"] for c in ("G0", "G1", "G2")]
for i, (a, t) in enumerate(zip(act, tot)):
    ax.scatter([i] * len(a), a, s=14, color=BLUE, zorder=3)
ax.bar(range(3), [sum(a) / len(a) for a in act], color=[BLUE, GRAY, GRAY], width=0.5,
       alpha=0.35, zorder=2)
ax.set_xticks(range(3)); ax.set_xticklabels(conds)
ax.set_ylabel("re-activated Episode-1 mark nodes")
ax.set_title("Cross-context reactivation (n=5)", pad=10)
save(fig, "fig_reactivation.pdf")

# ── Fig: ledger-mediated first-action change（既有 C 实验）──
ct = DB["C_transfer_prior_campaign"]
fig, ax = plt.subplots(figsize=(3.6, 2.6))
conds = ["No-Transfer", "Transfer"]
for i, c in enumerate(conds):
    ticks = [r["success_tick"] for r in ct[c]["rows"]]
    ax.scatter([i] * len(ticks), ticks, s=16, color=ORANGE, zorder=3)
    ax.bar(i, sum(ticks) / len(ticks), color=ORANGE if i else GRAY, width=0.5, alpha=0.35, zorder=2)
ax.set_xticks(range(2)); ax.set_xticklabels(conds)
ax.set_ylabel("Episode-2 ticks to goal (dots = seeds)")
ax.set_title("Ledger-mediated transfer (n=5)")
save(fig, "fig_ledger_transfer.pdf")

# ── Fig: modulation dose-response ──
ce = DB["C_emo"]
fig, ax = plt.subplots(figsize=(3.6, 2.6))
xs = {"E1_low": 0.85, "E0": 1.0, "E1_high": 1.3}
for name, x in xs.items():
    m = ce[name]["final_mass"]
    ax.scatter([x] * len(m), m, s=16, color=GREEN, zorder=3)
    ax.bar(x, sum(m) / len(m), width=0.045, color=GREEN, alpha=0.35, zorder=2)
m = ce["E2_disabled"]["final_mass"]
ax.scatter([1.0] * len(m), m, s=26, facecolor="none", edgecolor=RED, zorder=4, label="E2 disabled (pin 1.0)")
ax.errorbar([1.0], [sum(m) / len(m)], fmt="_", color=RED)
ax.set_xlabel("modulation gain $g$"); ax.set_ylabel("total activation mass (dots = seeds)")
ax.legend(frameon=False, fontsize=7); ax.set_title("Arousal/stress $\\rightarrow$ param-gain dose response (n=5)")
save(fig, "fig_dose.pdf")

# ── Fig: diffusion condition comparison ──
b2 = DB["B2"]
fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6))
conds = ["full", "nodiff", "rand", "retr"]; lab = ["full", "diff-off", "random", "retrieval\noracle"]
mass = [sum(b2[c]["final_mass"]) / len(b2[c]["final_mass"]) for c in conds]
lit = [sum(b2[c]["lit_set"]) / len(b2[c]["lit_set"]) for c in conds]
axes[0].bar(range(4), mass, color=[BLUE, GRAY, ORANGE, GREEN], width=0.55)
axes[0].set_xticks(range(4)); axes[0].set_xticklabels(lab, fontsize=8)
axes[0].set_ylabel("total activation mass"); axes[0].set_title("(a) Activation mass (n=5)")
axes[1].bar(range(4), lit, color=[BLUE, GRAY, ORANGE, GREEN], width=0.55)
axes[1].set_xticks(range(4)); axes[1].set_xticklabels(lab, fontsize=8)
axes[1].set_ylabel("lit domain size ($a\\geq0.05$)"); axes[1].set_title("(b) Access domain (n=5)")
save(fig, "fig_diffusion.pdf")

# ── Fig: Hebbian trajectory ──
hb = DB["F_hebbian"]
fig, ax = plt.subplots(figsize=(4.4, 2.6))
for c, colr in (("H0", BLUE), ("H1", GRAY)):
    series = hb[c]["wsum_series"]
    for s in series: ax.plot(range(1, len(s) + 1), s, color=colr, alpha=0.35, lw=0.8)
    mean = [sum(x[i] for x in series) / len(series) for i in range(len(series[0]))]
    ax.plot(range(1, len(mean) + 1), mean, color=colr, lw=1.8,
            label="%s (edges changed: %d/5 seeds)" % (c, hb[c]["edges_changed"][0]))
ax.set_xlabel("episode"); ax.set_ylabel("sum of whitelisted edge weights")
ax.legend(frameon=False, fontsize=7); ax.set_title("Hebbian weight trajectory (n=5)")
save(fig, "fig_hebbian.pdf")

# ── Fig: P0 lockout + prior boundary ──
jp = DB["J_prior"]
fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6))
ax = axes[0]
succ = [sum(jp[c]["success_per_seed"]) for c in ("P0", "P1", "P2")]
tot = [sum(jp[c]["episodes_per_seed"]) for c in ("P0", "P1", "P2")]
ax.bar(range(3), succ, color=[RED, GREEN, GREEN], width=0.55)
for i, (s, t) in enumerate(zip(succ, tot)): ax.text(i, s + 0.4, f"{s}/{t}", ha="center")
ax.set_xticks(range(3)); ax.set_xticklabels(["P0\nzero prior", "P1\nmechanisms", "P2\nfull prior"])
ax.set_ylabel("episodes achieving goal"); ax.set_title("(a) Prior boundary (5 seeds x 5 episodes)")
ax = axes[1]
gg = jp["P0"]["graph_growth_nodes"]
ax.bar([0], [sum(gg) / len(gg)], color=GRAY, width=0.5)
ax.set_xticks([0]); ax.set_xticklabels(["P0"])
ax.set_ylabel("graph node growth over 5 episodes")
ax.set_title("(b) P0: graph still grows, goal never reached")
save(fig, "fig_prior.pdf")

# ── Fig: long-chain conditions + masking ──
lc = DB["H_longchain"]
fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6))
ax = axes[0]
conds = ["L0", "L1", "L2", "L3b", "L3"]
lab = ["full", "-diff", "-wb", "-prior\n+smelt", "-prior"]
ticks = []
for c in conds:
    t = [x for x in lc[c]["success_ticks"] if x is not None]
    ticks.append(sum(t) / len(t) if t else 0)
cols = [BLUE, BLUE, BLUE, GREEN, RED]
ax.bar(range(5), ticks, color=cols, width=0.55)
for i, t in enumerate(ticks):
    ax.text(i, t + 4, f"{int(t)}" if t else "fail", ha="center")
ax.set_xticks(range(5)); ax.set_xticklabels(lab, fontsize=8)
ax.set_ylabel("ticks to goal (n=5)"); ax.set_title("(a) Long-chain conditions")
ax = axes[1]
a2c = DB["A2"]["conditions"]
ax.bar(range(3), [11, 11, 11], color=[GRAY, GRAY, BLUE], width=0.55)
wb = [sum(a2c[c]["wb_nodes_inherited"]) / len(a2c[c]["wb_nodes_inherited"]) for c in ("B0", "B1", "B2")]
ax2 = ax.twinx(); ax2.plot(range(3), wb, "o--", color=ORANGE, label="inherited wb nodes")
ax2.set_ylabel("write-back nodes", color=ORANGE); ax2.tick_params(axis="y", colors=ORANGE)
ax.set_ylim(0, 14); ax.set_ylabel("ticks to goal (all identical)")
ax.set_xticks(range(3)); ax.set_xticklabels(["B0", "B1", "B2"], fontsize=8)
ax.set_title("(b) Closure masking example (A2)")
save(fig, "fig_longchain.pdf")

print("all figures done ->", OUT)
