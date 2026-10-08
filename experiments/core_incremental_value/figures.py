# figures.py — 9 张发表级图(Figure 1-9,PDF/SVG/PNG)
# ============================================================================
# 风格: 统一字体/尺寸/坐标;灰阶+线型/标记区分(不用颜色暗示成败);
# 数据全部来自 raw_results.jsonl / analysis.json;唯一重算是 combo 面板
# (comb_task 确定性,与 campaign 同 generator → 逐位一致)。
# ============================================================================

import json
import os
import statistics
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch

ROOT = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(ROOT, "figures")
RAW = os.path.join(ROOT, "raw_results.jsonl")
AN = os.path.join(ROOT, "analysis.json")
sys.path.insert(0, ROOT)  # 供 fig8 导入 generator

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.linewidth": 0.8,
    "axes.labelsize": 9,
    "axes.titlesize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "figure.dpi": 300,
})
GRAY = "#000000"


def save(fig, name, caption):
    fig.text(0.005, 0.005, caption, fontsize=6.5, color="#333333", wrap=True)
    for ext in ("pdf", "svg", "png"):
        fig.savefig(os.path.join(FIG, f"{name}.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print(f"  ok {name}")


def load():
    rows = [json.loads(l) for l in open(RAW, encoding="utf-8") if l.strip()]
    by = {}
    for r in rows:
        key = (r.get("_phase"), r.get("condition"), r.get("seed"), r.get("method"))
        by.setdefault(key, {})[r.get("variant") or "multi"] = r
    return rows, by


def cfg_key(cond):
    d = {}
    for part in (cond or "").split("|"):
        if ":" in part:
            k, v = part.split(":", 1)
            d[k] = v
    return d


# confirmatory 4 条件 (label, (inputs_n, N, n_distractors, noise))
CF_ORDER = [("C-1", (3, 100, 100, 0.25)), ("C-2", (3, 300, 300, 0.5)),
            ("C-3", (5, 300, 300, 0.5)), ("C-4", (5, 1000, 1000, 0.75))]
CF_MAP = {t: n for n, t in CF_ORDER}


def cf_label(cond):
    c = cfg_key(cond)
    t = (int(c.get("inputs_n", 0)), int(c.get("N", 0)),
         int(c.get("n_distractors", 0)), float(c.get("noise", -1)))
    return CF_MAP.get(t, c.get("noise", cond))


def confirm_cells(by, label):
    return [k for k in by
            if k[0] == "confirmatory" and cf_label(k[1]) == label]


def method_cell(by, cells, method):
    return [by[k]["multi"] for k in cells if k[3] == method]


# ---------------------------------------------------------------- Figure 1

def fig1(by):
    """FAS vs B2: median MRR(bar) + mean Recall@1/3(标记),4 条件。"""
    labels = [n for n, _ in CF_ORDER]
    bars, r1, r3 = {}, {}, {}
    for lbl in labels:
        cells = confirm_cells(by, lbl)
        for m in ("fas", "b2"):
            rws = method_cell(by, cells, m)
            bars[(lbl, m)] = statistics.median(r["mrr"] or 0 for r in rws)
            r1[(lbl, m)] = statistics.mean(r["recall@1"] for r in rws)
            r3[(lbl, m)] = statistics.mean(r["recall@3"] for r in rws)
    x = range(len(labels))
    w = 0.36
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ax.bar([i - w / 2 for i in x], [bars[(l, "fas")] for l in labels], w,
           color="white", edgecolor="black", hatch="//",
           label="FAS (spreading activation)")
    ax.bar([i + w / 2 for i in x], [bars[(l, "b2")] for l in labels], w,
           color="#d9d9d9", edgecolor="black", label="B2 (flat relevance)")
    for i in x:
        for s, mk in ((r1, "o"), (r3, "^")):
            ax.plot([i - w / 2], [s[(labels[i], "fas")]], marker=mk, ms=4.5,
                    color="black", fillstyle="none")
            ax.plot([i + w / 2], [s[(labels[i], "b2")]], marker=mk, ms=4.5,
                    color="black", fillstyle="full")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("performance (50 paired seeds)")
    ax.set_ylim(0, 1.08)
    ax.legend(loc="upper left", frameon=False)
    ax.axhline(1 / 143, ls=":", color="#999999")
    ax.text(len(labels) - 0.02, 1 / 143 + 0.02, "chance ≈ 1/143", fontsize=7,
            ha="right")
    ax.set_title("Figure 1 — FAS vs flat retrieval: discrimination performance",
                 fontweight="bold")
    save(fig, "figure1", "Figure 1. Confirmatory subset C-1…C-4 (50 paired seeds, "
         "no LLM). Bars: median MRR; ○ mean Recall@1 (outline=FAS); "
         "△ mean Recall@3 (filled=FAS). Floor at k=3 inputs and ceiling at k=5 for "
         "both mechanisms; FAS is MRR-identical at ceiling (ns) and marginally "
         "higher at floor (S_C-1 raw p=.006, med diff = +.0035; below chance "
         "sensitivity for practical discrimination). B3 random baseline omitted "
         "(≈chance).")


# ---------------------------------------------------------------- Figure 2

def fig2(by, an):
    """JCG 箱线 + ΔJCG 主族(每 seed 配对)。"""
    labels = [n for n, _ in CF_ORDER]
    data = {}
    for lbl in labels:
        cells = confirm_cells(by, lbl)
        n_in = [t for n2, t in CF_ORDER if n2 == lbl][0][0]
        for m in ("fas", "b2"):
            jcg = []
            for k in cells:
                if k[3] != m:
                    continue
                vd = by[k]
                multi = vd.get("multi")
                if not multi or multi.get("target_activation") is None:
                    continue
                ss = [vd.get(f"single:{i}", {}).get("target_activation")
                      for i in range(n_in)]
                ss = [s for s in ss if s is not None]
                if len(ss) < n_in:
                    continue
                jcg.append(multi["target_activation"] - statistics.mean(ss))
            data[(lbl, m)] = jcg
    med_recon = {l: statistics.median(data[(l, m)]) for l in labels for m in ("fas", "b2")}
    print("  JCG med recons:", json.dumps({k: round(v, 4) for k, v in med_recon.items()}))

    fig, axes = plt.subplots(1, 2, figsize=(6.9, 3.5),
                             gridspec_kw={"width_ratios": [1.25, 1]})
    ax = axes[0]
    pos, ticks = [], []
    for i, lbl in enumerate(labels):
        for j, m in enumerate(("fas", "b2")):
            p = i * 2 + j
            pos.append(p)
            bp = ax.boxplot(data[(lbl, m)], positions=[p], widths=0.72,
                            patch_artist=True, showfliers=False, whis=(5, 95))
            bp["boxes"][0].set_facecolor("white" if m == "fas" else "#d9d9d9")
            for el in bp["boxes"] + bp["whiskers"] + bp["caps"] + bp["medians"]:
                el.set_color("black")
        ticks.append(i * 2 + 0.5)
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels)
    ax.set_ylabel("JCG = joint score − mean single score")
    ax.set_title("a) Joint Convergence Gain (mechanism)")
    ax.legend([plt.Rectangle((0, 0), 1, 1, fc="white", ec="black"),
               plt.Rectangle((0, 0), 1, 1, fc="#d9d9d9", ec="black")],
              ["FAS", "B2"], loc="upper left", frameon=False)
    ax2 = axes[1]
    dj = {}
    for lbl in labels:
        dj[lbl] = [f - b for f, b in zip(data[(lbl, "fas")], data[(lbl, "b2")])]
    for i, lbl in enumerate(labels):
        ax2.scatter([i] * len(dj[lbl]), dj[lbl], s=10, color=GRAY, alpha=0.3)
        ax2.plot([i], [statistics.median(dj[lbl])], marker="_", ms=20, color="black")
    ax2.axhline(0, ls=(0, (4, 2)), color="#555555")
    ax2.set_xticks(range(len(labels)))
    ax2.set_xticklabels(labels)
    ax2.set_ylabel("ΔJCG (FAS − B2), paired per seed")
    ax2.set_title("b) Incremental value (primary)")
    ax2.text(1.5, -0.52, "all 4: ΔJCG<0, Holm p=1.5e-9,\nr=0.62, >0 rate=0/50",
             fontsize=7, ha="center")
    fig.suptitle("Figure 2 — Activation adds NO joint-gain beyond flat aggregation",
                 fontweight="bold", y=1.03)
    save(fig, "figure2", "Figure 2. Paired JCG (Whiskers 5–95 %, medians drawn). "
         "FAS JCG median 0.098/0.098/0.216/0.218 with >0 rate 100 % — the "
         "convergence mechanism runs as specified. But flat aggregation B2 shows "
         "systematically larger JCG on every condition: the pre-registered primary "
         "ΔJCG tests all rejected in the reversed direction (Holm-adjusted "
         "p=1.5e-9, r=0.62; ΔJCG>0 rate 0/50).")


# ---------------------------------------------------------------- Figure 3

def fig3(an):
    rob = an["robustness"]
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    for m, style in (("fas", (0, (3, 1))), ("b2", "-")):
        pts = rob[m]["points"]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.plot(xs, ys, marker="o", ms=4, ls=style, color=GRAY,
                label=f"{'FAS' if m=='fas' else 'B2 flat'} (slope {rob[m]['slope']:+.4f})")
    ax.plot([0.5], [1.0], marker="D", ms=5, color="black", label="5-input (C-3, noise 50%)")
    ax.plot([0.75], [1.0], marker="s", ms=5, color="black", fillstyle="none",
            label="5-input (C-4, noise 75%)")
    ax.set_xlabel("noise ratio (random background edges)")
    ax.set_ylabel("median MRR (30 seeds / cell)")
    ax.set_ylim(0, 1.12)
    ax.legend(frameon=False)
    ax.set_title("Figure 3 — Noise robustness: no differential",
                 fontweight="bold")
    save(fig, "figure3", "Figure 3. Noise grid 0–90 % at the 3-input T4 "
         "interference profile: both mechanisms sit at the chance floor over the "
         "entire range (slopes ≈ 0). Diamonds/squares: the 5-input confirmatory "
         "points at noise 50 % / 75 % where both reach median MRR = 1.0 — noise "
         "tolerance is governed by input support, identically for both mechanisms. "
         "Robustness is not a differential property.")


# ---------------------------------------------------------------- Figure 4/5

def sweep_figure(an, key, xlabel, fname, title, ymax):
    sw = an["sweeps"]
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    for m, style in (("fas", (0, (3, 1))), ("b2", "-")):
        cell = sw.get(f"{key}|{m}", {})
        xs = sorted(int(k) for k in cell)
        ys = [cell[str(x)] for x in xs]
        ax.plot(xs, ys, marker="o", ms=4, ls=style, color=GRAY,
                label="FAS" if m == "fas" else "B2 flat")
    ax.set_xscale("log")
    ax.set_xticks([20, 50, 100, 300, 1000])
    ax.set_xticklabels([20, 50, 100, 300, 1000])
    ax.set_xlabel(xlabel)
    ax.set_ylabel("median MRR (30 seeds / cell)")
    ax.set_ylim(0, ymax)
    ax.legend(frameon=False)
    ax.set_title(title, fontweight="bold")
    save(fig, fname, f"{title}. Sweep at the 3-input T4 profile; both mechanisms "
         "move together (identical flattened response), supporting the operating-"
         "regime account over any mechanism differential.")


def fig4(an):
    sweep_figure(an, "N", "graph nodes (target, log scale)",
                 "figure4", "Figure 4 — Complexity sweep: flat floor for both",
                 0.3)


def fig5(an):
    sweep_figure(an, "inputs_n", "input count k (log scale)",
                 "figure5", "Figure 5 — Support threshold: identical step", 1.05)


# ---------------------------------------------------------------- Figure 6

def fig6(an):
    dil = an["dilution"]
    fig, axes = plt.subplots(1, 2, figsize=(6.9, 3.4))
    for ax, (pk, rk, ttl) in zip(axes, [
            ("noise_points", "noise_sweep_rho_reach_tact", "noise sweep"),
            ("distractor_points", "distractor_sweep_rho_reach_tact",
             "distractor sweep")]):
        pt = dil[pk]
        ax.scatter(pt["reach"], pt["tact"], s=9, color=GRAY, alpha=0.45)
        ax.set_xlabel("reachable nodes (activation > ε)")
        ax.set_ylabel("joint target activation")
        ax.set_title(f"{ttl}: ρ = {dil[rk]:+.2f}", fontsize=8)
    fig.suptitle("Figure 6 — Activation dilution tradeoff (FAS)", fontweight="bold")
    save(fig, "figure6", "Figure 6. Across noise and distractor sweeps, higher "
         "diffusion reachability correlates negatively with the joint target's "
         "activation (ρ = −0.44 / −0.66). Mechanism boundary confirmed: 'spread "
         "further ⇒ target stands out less' — the dilution tradeoff hypothesized "
         "in the A9 relation-direction work.")


# ---------------------------------------------------------------- Figure 7

def fig7(an):
    tc = an["topology_compare"]
    fig, ax = plt.subplots(figsize=(5.8, 3.4))
    tops = ["T1", "T2", "T3", "T4"]
    x = range(4)
    w = 0.26
    for j, m in enumerate(("fas", "b2", "b3")):
        vals = [tc.get(f"{t}|{m}", {}).get("mrr_median", 0) for t in tops]
        ax.bar([i + (j - 1) * w for i in x], vals, w,
               color="white" if m == "fas" else ("#d9d9d9" if m == "b2" else "#f2f2f2"),
               edgecolor="black", hatch="//" if m == "fas" else ("" if m == "b2" else "."),
               label=m.upper())
    ax.set_xticks(list(x))
    ax.set_xticklabels(tops)
    ax.set_ylabel("median MRR (30 seeds, 3-input profile)")
    ax.set_ylim(0, 0.2)
    ax.legend(frameon=False)
    ax.set_title("Figure 7 — Topology comparison: no differential",
                 fontweight="bold")
    save(fig, "figure7", "Figure 7. Four topologies (300 nodes, 3 inputs, 300 "
         "distractors, 50 % noise): topology type moves the absolute difficulty "
         "but never separates the mechanisms. B1 (direct 2-hop accumulation) is "
         "definitionally 0 here (no input→joint edge); B3 random ≈ chance.")


# ---------------------------------------------------------------- Figure 8

def fig8(by):
    import generator
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.5))
    # ---- a) combo O3: 真值激活(FAS,与 campaign 同一 generator,seed 0) ----
    g, task = generator.combo_task(seed=0)
    acts = g.diffuse_round(steps=3, seeds=["I0", "I1"], seed_strength=5.0)
    o31 = acts.get("O3") or 0.0
    o11 = acts.get("O1") or 0.0
    o21 = acts.get("O2") or 0.0
    ax = axes[0]
    order = sorted([("O1", o11), ("O2", o21), ("O3", o31)],
                   key=lambda kv: -kv[1])
    print(f"  combo seed0 FAS activations: {json.dumps(dict((k, round(v, 4)) for k, v in order))}")
    cands = ["O1", "O2", "O3"]
    ax.bar([i - 0.17 for i in range(3)],
           [dict(order)[c] for c in cands], 0.32, color="white",
           edgecolor="black", hatch="//", label="FAS activation")
    b2s = {"O1": 0.8 * 0.8, "O2": 0.8 * 0.8, "O3": 0.5 * 0.5 + 0.5 * 0.5}
    ax.bar([i + 0.17 for i in range(3)], [b2s[c] for c in cands], 0.32,
           color="#d9d9d9", edgecolor="black", label="B2 score (Σ best path)")
    ax.set_xticks(range(3))
    ax.set_xticklabels(cands)
    ax.set_ylabel("activation / score")
    ax.set_title("a) shared-hub combination (§16):\nO3 must win", fontsize=8)
    ax.legend(loc="upper right", frameon=False, fontsize=7)
    ax.annotate("FAS: O3 first 30/30\nB2: O3 third 30/30", xy=(1.12, 2.1),
                fontsize=7)
    # ---- b) C-3 seed 0: joint 激活 5×single vs multi ----
    cells = confirm_cells(by, "C-3")
    pair = None
    for k in cells:
        if k[3] == "fas" and k[2] == 0:
            pair = by[k]
            break
    vals = [pair.get(f"single:{i}", {}).get("target_activation") for i in range(5)]
    mv = pair.get("multi", {}).get("target_activation")
    ax2 = axes[1]
    ax2.bar(range(5), [v or 0.0 for v in vals], 0.55, color="white",
            edgecolor="black")
    ax2.bar([5], [mv], 0.7, color="#d9d9d9", edgecolor="black", hatch="//")
    ax2.set_xticks(list(range(5)) + [5])
    ax2.set_xticklabels([f"I{i}" for i in range(5)] + ["I0-4"])
    ax2.set_xlabel("seeded inputs (5-input T4, seed 0)")
    ax2.set_ylabel("joint candidate activation")
    ax2.set_title("b) joint convergence:\nsingle vs joint seeding (C-3)", fontsize=8)
    fig.suptitle("Figure 8 — Representative activation landscapes",
                 fontweight="bold")
    save(fig, "figure8", "Figure 8. (a) §16 combination task, seed 0, recomputed "
         "with the campaign generator (same code, deterministic per seed): under joint "
         "seeding the shared hub doubles its feed, giving O3 the top activation "
         "(FAS ranks O3 first in 30/30 seeds), while B2's path-product sum ranks "
         "O3 third in 30/30 (O1=O2=0.64 before O3=0.50; the single pre-registered "
         "topology where dynamics beat flat aggregation). (b) C-3 seed 0: any "
         "single input gives the joint candidate negligible activation; joint "
         "seeding lifts it above the local 0.8-weight competitors — the "
         "convergence that JCG measures.")


# ---------------------------------------------------------------- Figure 9

def fig9():
    fig, ax = plt.subplots(figsize=(6.2, 4.4))
    ax.axis("off")
    boxes = [
        ("Behavioral: persistent memory value under asymmetry\n"
         "A10 beacon — fas_full 20/20 vs direct 0/20 (vs flat, ns)"),
        ("Mechanism: multi-input convergence exists\n"
         "JCG experiments — >0 rate .970 (here 1.000 / 4 conditions)"),
        ("Topology steers propagation\n"
         "A9 relation-direction — ρ .33→.65, p=.002, r=.886"),
        ("Incremental value over flat retrieval — THIS experiment\n"
         "ΔJCG < 0 on all 4 confirmatory conditions (Holm p=1.5e-9, r=0.62);\n"
         "MRR parity; single differential = §16 shared-hub combo (30/30)"),
    ]
    ys = [0.90, 0.67, 0.44, 0.21]
    for (text, y) in zip(boxes, ys):
        ax.text(0.5, y, text, ha="center", va="center", fontsize=8,
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#e8e8e8",
                          edgecolor="black", lw=1.0))
    for i in range(3):
        ax.add_patch(FancyArrowPatch((0.5, ys[i] - 0.115), (0.5, ys[i + 1] + 0.115),
                                     arrowstyle="-|>", mutation_scale=10,
                                     color="#555555", lw=1.0))
    ax.text(0.5, 0.03, "Layers: behavior above, mechanism below;\n"
            "incremental-value claim NOT supported at scale", ha="center",
            fontsize=8)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_title("Figure 9 — Evidence hierarchy", fontweight="bold")
    save(fig, "figure9", "Figure 9. Structured evidence map. A10: behavioral value "
         "of persistent memory. JCG experiments: the convergence mechanism runs. "
         "A9: topology sensitivity. This experiment: separates the layers — the "
         "incremental-value claim over flat aggregation fails on the pre-"
         "registered battery, with one counterfactual exception (§16 O3).")


def main():
    os.makedirs(FIG, exist_ok=True)
    _, by = load()
    an = json.load(open(AN, encoding="utf-8"))
    print("[figures]")
    fig1(by)
    fig2(by, an)
    fig3(an)
    fig4(an)
    fig5(an)
    fig6(an)
    fig7(an)
    fig8(by)
    fig9()
    print("[done]")


if __name__ == "__main__":
    main()