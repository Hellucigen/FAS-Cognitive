# -*- coding: utf-8 -*-
# gen_figures.py — capability_gap_v2 图表(FIGURES/)
# 全部数字程序化读取 raw_*.jsonl / RESULTS.csv,不硬编码。
import json
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(HERE, "FIGURES")
os.makedirs(FIG, exist_ok=True)
plt.rcParams.update({"font.size": 9, "axes.titlesize": 10, "figure.dpi": 150,
                     "axes.spines.top": False, "axes.spines.right": False})
BLUE = "#2b6cb0"; ORANGE = "#dd6b20"; GREEN = "#2f855a"; RED = "#c53030"; GRAY = "#666"


def load(fn):
    p = os.path.join(HERE, fn)
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def save(fig, name):
    fig.savefig(os.path.join(FIG, name), bbox_inches="tight")
    plt.close(fig)
    print("fig:", name)


# ── 1. B:延迟信用曲线 ───────────────────────────────────────────────
rows = [r for r in load("raw_B_mech.jsonl") if "delay_ticks" in r]
if rows:
    agg = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        d = r["delay_ticks"]
        agg[d][0] += int(r.get("inventory_outcome_attributed") or 0)
        agg[d][1] += int(r.get("self_success_attributed") or 0)
        agg[d][2] += 1
    xs = sorted(agg)
    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    ax.plot([x * 16 for x in xs], [100 * agg[x][0] / agg[x][2] for x in xs],
            marker="o", color=BLUE, label="inventory outcome attributed")
    ax.plot([x * 16 for x in xs], [100 * agg[x][1] / agg[x][2] for x in xs],
            marker="s", color=ORANGE, label="action self-success attributed")
    ax.axvline(90, color=GRAY, ls="--", lw=1)
    ax.text(92, 50, "attribution window\n90 s", fontsize=7.5, color=GRAY)
    ax.set_xlabel("consequence delay (s)")
    ax.set_ylabel("attribution rate (%)")
    ax.set_title("B: delayed credit follows the attribution window\n"
                 "(n=10 per delay, deterministic, zero-LLM)")
    ax.set_ylim(-5, 105)
    ax.legend(fontsize=7.5, frameon=False)
    save(fig, "fig_B_delay.png")

# ── 2. K:历史长度 → 性能/token ──────────────────────────────────────
rows = [r for r in load("raw_K.jsonl") if r.get("exp") == "K"]
if rows:
    d = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        c = r["condition"]
        t = r["test"]
        d[c][0] += t["success"]; d[c][1] += 1; d[c][2] += t["prompt_tokens"]
    order = ["direct", "retrieval", "fas", "history10", "history25",
             "history50", "history100", "history200"]
    order = [c for c in order if c in d]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.6, 3.4))
    xs = range(len(order))
    a1.bar(xs, [100 * d[c][0] / d[c][1] for c in order],
           color=[GREEN if d[c][0] == d[c][1] else (BLUE if d[c][0] > 0 else RED)
                  for c in order], alpha=0.8)
    a1.set_xticks(list(xs))
    a1.set_xticklabels(order, rotation=35, ha="right", fontsize=7.5)
    a1.set_ylabel("success rate (%)")
    a1.set_title("K: success is flat across history length")
    a1.set_ylim(0, 110)
    a2.bar(xs, [d[c][2] / d[c][1] for c in order], color=BLUE, alpha=0.8)
    a2.axhline(d["fas"][2] / d["fas"][1], color=GREEN, ls="--", lw=1.2)
    a2.text(0.1, d["fas"][2] / d["fas"][1] * 1.03, "FAS state cost",
            fontsize=7.5, color=GREEN)
    a2.set_xticks(list(xs))
    a2.set_xticklabels(order, rotation=35, ha="right", fontsize=7.5)
    a2.set_ylabel("prompt tokens per run")
    a2.set_title("K: cost grows with history; FAS state is constant")
    fig.tight_layout()
    save(fig, "fig_K_history_compression.png")

# ── 3. A:条件成功率对比 ─────────────────────────────────────────────
rows = [r for r in load("raw_A.jsonl") if r.get("exp") == "A"]
if rows:
    d = defaultdict(lambda: [0, 0])
    for r in rows:
        d[r["condition"]][0] += r["P4"]["success"]; d[r["condition"]][1] += 1
    fig, ax = plt.subplots(figsize=(5.6, 3.2))
    order = ["fas", "fas-nowb", "history", "retrieval", "fresh-fas"]
    ys = [100 * d[c][0] / max(1, d[c][1]) for c in order]
    ax.bar(range(len(order)), ys,
           color=[RED if y == 0 else GREEN for y in ys], alpha=0.8)
    for i, c in enumerate(order):
        ax.text(i, ys[i] + 2, "%d/%d" % (d[c][0], d[c][1]), ha="center",
                fontsize=8)
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels(order, rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("P4 success rate (%)")
    ax.set_ylim(0, 115)
    ax.set_title("A: continual learning — FAS ties LLM baselines;\n"
                 "only experience-less fresh-FAS fails")
    save(fig, "fig_A_continual.png")

# ── 4. I:全条件零完成(行为层界面容量)──────────────────────────────
rows = [r for r in load("raw_I.jsonl") if r.get("exp") == "I"]
if rows:
    d = defaultdict(lambda: [0, 0])
    for r in rows:
        d[r["condition"]][0] += r["test"]["success"]; d[r["condition"]][1] += 1
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    order = sorted(d)
    ys = [100 * d[c][0] / max(1, d[c][1]) for c in order]
    ax.bar(range(len(order)), ys, color=GRAY, alpha=0.7)
    for i, c in enumerate(order):
        ax.text(i, 2, "%d/%d" % (d[c][0], d[c][1]), ha="center", fontsize=8)
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels(order, rotation=30, ha="right", fontsize=7.5)
    ax.set_ylabel("success rate (%)")
    ax.set_title("I: 7-step convergence chain — no condition completes\n"
                 "at the decision-head interface (F5 task/interface limit)")
    save(fig, "fig_I_convergence.png")

print("done")
