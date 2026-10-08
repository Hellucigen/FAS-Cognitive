# figures.py — 8 张 publication 图(任务 §final-5)
# ============================================================================
# 全部从 raw_results.jsonl/summary.csv 绘制(seed-level 原始数据),
# 出图到 ../figures/。风格:白底、论文字号、无花哨。
# 每张图左上角标注样本量与统计结论(来自预声明协议)。
# ============================================================================

import csv
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from aggregate import load, group_key, agg, _boot_ci, mean

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "figures")
os.makedirs(OUT, exist_ok=True)

plt.rcParams.update({
    "font.size": 9, "axes.labelsize": 10, "axes.titlesize": 10,
    "figure.dpi": 200, "savefig.dpi": 200, "axes.grid": True,
    "grid.alpha": 0.25, "grid.linestyle": ":",
    "font.family": "sans-serif",
    "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
})


def save(fig, name):
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, name))
    plt.close(fig)
    print(f"  figures/{name}")


def load_rows():
    return [r for r in load() if r.get("_params", {}).get("dim") != "toy"]


def fig1_toy():
    """F1: δ20 例 - toy 正确性(O1 汇聚胜出;O2/O3 抑制)"""
    rows = [r for r in load() if r.get("_params", {}).get("cond") == "std"]
    r = rows[0]
    labels = ["O1 (joint)", "O2 (single)", "O3 (single)"]
    vals = [r["acts"][k] for k in ("O1", "O2", "O3")]
    fig, ax = plt.subplots(figsize=(4.2, 3.0))
    bars = ax.bar(labels, vals, color=["#2e7d32", "#888", "#888"], width=0.55)
    ax.set_ylabel("final activation")
    ax.set_title("F1 toy: I1+I2 → O1 汇聚胜出\n(期望 O1 第 1: True)")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.03, f"{v:.2f}",
                ha="center", fontsize=8)
    save(fig, "fig1_toy_correctness.png")


def fig2_complexity():
    """F2: 复杂度扫 - JCG/MRR vs N(10→1000)"""
    rows = load_rows()
    groups = []
    for r in rows:
        if r["_params"]["dim"] == "complexity":
            groups.append(r)
    Ns = sorted({r["_params"]["N"] for r in groups})
    jcg_m = []; jcg_lo = []; jcg_hi = []
    mrr_m = []; mrr_lo = []; mrr_hi = []
    for N in Ns:
        rr = [r for r in groups if r["_params"]["N"] == N]
        j = [r["jcg"] for r in rr]; m = [r["mrr_joint"] for r in rr]
        lo, hi = _boot_ci(j, seed=1)
        lo2, hi2 = _boot_ci(m, seed=1)
        jcg_m.append(mean(j)); mrr_m.append(mean(m))
        jcg_lo.append(lo); jcg_hi.append(hi)
        mrr_lo.append(lo2); mrr_hi.append(hi2)
    fig, axes = plt.subplots(1, 2, figsize=(8.2, 3.2))
    x = np.arange(len(Ns))
    axes[0].errorbar(x, mrr_m, yerr=[np.array(mrr_m) - mrr_lo,
                                     np.array(mrr_hi) - np.array(mrr_m)],
                     fmt="o-", capsize=3, color="#1565c0")
    axes[0].set_xticks(x); axes[0].set_xticklabels(Ns)
    axes[0].set_xlabel("graph size N"); axes[0].set_ylabel("MRR(joint)")
    axes[0].set_title("F2a 复杂度:MRR 随 N")
    axes[1].errorbar(x, jcg_m, yerr=[np.array(jcg_m) - jcg_lo,
                                     np.array(jcg_hi) - np.array(jcg_m)],
                     fmt="s-", capsize=3, color="#2e7d32")
    axes[1].set_xticks(x); axes[1].set_xticklabels(Ns)
    axes[1].set_xlabel("graph size N"); axes[1].set_ylabel("JCG")
    axes[1].set_title("F2b 复杂度:JCG 随 N\n(20 seeds/档, C7: ns)")
    save(fig, "fig2_complexity.png")


def fig3_noise():
    """F3: 噪声鲁棒性 - JCG vs noise(单调退化,配对显著)"""
    rows = load_rows()
    nlev = sorted({r["_params"]["noise"] for r in rows
                   if r["_params"]["dim"] == "noise" and r["_params"]["N"] == 300})
    jm = []; jlo = []; jhi = []
    for nl in nlev:
        rr = [r for r in rows if r["_params"]["dim"] == "noise"
              and r["_params"]["N"] == 300 and r["_params"]["noise"] == nl]
        j = [r["jcg"] for r in rr]
        lo, hi = _boot_ci(j, seed=1)
        jm.append(mean(j)); jlo.append(lo); jhi.append(hi)
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    ax.errorbar(nlev, jm, yerr=[np.array(jm) - np.array(jlo),
                                np.array(jhi) - np.array(jm)],
                fmt="o-", capsize=3, color="#c62828")
    ax.set_xlabel("noise ratio"); ax.set_ylabel("JCG (join convergence gain)")
    ax.set_title("F3 噪声鲁棒: JCG 单调退化\nnoise 0 vs 0.9: p=8.9e-5, r=0.62 (Bonf SIG)")
    save(fig, "fig3_noise_robustness.png")


def fig4_topology():
    """F4: 拓扑模式 - r@1/MRR by direction(N=100/300)"""
    rows = load_rows()
    dirs = ["forward", "mixed", "random", "bidirectional"]
    for N in (100, 300):
        fig, ax = plt.subplots(figsize=(4.6, 3.0))
        r1 = []; mrr = []
        for d in dirs:
            rr = [r for r in rows if r["_params"]["dim"] == "topology"
                  and r["_params"]["direction"] == d and r["_params"]["N"] == N]
            r1.append(mean([r["recall@1"] for r in rr]))
            mrr.append(mean([r["mrr_joint"] for r in rr]))
        x = np.arange(len(dirs)); w = 0.35
        ax.bar(x - w / 2, r1, w, label="recall@1", color="#1565c0")
        ax.bar(x + w / 2, mrr, w, label="MRR", color="#2e7d32")
        ax.set_xticks(x); ax.set_xticklabels(dirs)
        ax.set_ylabel("rate / MRR"); ax.set_ylim(0, 1.05)
        ax.set_title(f"F4 拓扑模式 N={N}\n(fwd vs bidir JCG: p=8.9e-5 Bonf SIG)")
        ax.legend(frameon=False)
        save(fig, f"fig4_topology_N{N}.png")
    return True


def fig5_scaling():
    """F5: 输入数与 JCG - single<multi 且单调"""
    rows = load_rows()
    jcg = {}
    for r in rows:
        if r["_params"]["dim"] == "joint":
            jcg.setdefault(r["_params"]["inputs"], []).append(r["jcg"])
    ins = sorted(jcg)
    m = [mean(jcg[i]) for i in ins]
    lo = []; hi = []
    for i in ins:
        a, b = _boot_ci(jcg[i], seed=1)
        lo.append(a); hi.append(b)
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    ax.errorbar(ins, m, yerr=[np.array(m) - np.array(lo),
                              np.array(hi) - np.array(m)],
                fmt="s-", capsize=3, color="#6a1b9a")
    ax.set_xlabel("# inputs"); ax.set_ylabel("JCG")
    ax.set_title("F5 多输入汇聚增益单调递增\ninputs 2 vs 3: p=8.9e-5 Bonf SIG")
    save(fig, "fig5_input_scaling.png")


def fig6_baselines():
    """F6: baseline 对照 - diffusion vs flat vs random(JCG 取胜)"""
    rows = load_rows()
    # 取 N=300 fwd 每组 20 seed:diffusion r@1 vs flat_and vs flat_or
    sel = [r for r in rows if r["_params"]["dim"] == "complexity"
           and r["_params"]["N"] == 300]
    diff = [r["recall@1"] for r in sel]
    f_and = [r["flat_joint_and"] for r in sel]
    f_or = [r["flat_joint_or"] for r in sel]
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    labels = ["diffusion\nr@1", "flat\n(and)", "flat\n(or)"]
    vals = [mean(diff), mean(f_and), mean(f_or)]
    colors = ["#2e7d32", "#888", "#aaa"]
    bars = ax.bar(labels, vals, color=colors, width=0.55)
    ax.set_ylabel("recall@1 rate")
    ax.set_title("F6 基线对照: diffusion 20/20 胜 flat\n(同 seed,N=300)")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.2f}",
                ha="center", fontsize=8)
    save(fig, "fig6_baselines.png")


def fig7_rho():
    """F7: 激活↔拓扑一致性(候选集内 Spearman,r=0.76-0.81)"""
    rows = load_rows()
    rho_by_dim = {
        "joint": [r["rho_topology"] for r in rows
                  if r["_params"]["dim"] == "joint"
                  and isinstance(r.get("rho_topology"), (int, float))],
        "scaling": [r["rho_topology"] for r in rows
                    if r["_params"]["dim"] == "scaling"
                    and isinstance(r.get("rho_topology"), (int, float))],
    }
    fig, axes = plt.subplots(1, 2, figsize=(8.2, 3.0))
    for ax, (k, v) in zip(axes, rho_by_dim.items()):
        ax.hist(v, bins=10, color="#1565c0", alpha=0.8)
        ax.axvline(mean(v), color="#c62828", ls="--", lw=1)
        ax.set_xlabel("Spearman ρ (activation×topology)")
        ax.set_ylabel("seed count")
        ax.set_title(f"F7 {k}: ρ̄={mean(v):.2f} (n={len(v)})")
    save(fig, "fig7_activation_topology.png")


def fig8_jcg_dist():
    """F8: JCG 分布 - clean vs noise0.9(增益被噪声侵蚀)"""
    rows = load_rows()
    clean = [r["jcg"] for r in rows if r["_params"]["dim"] == "joint"
             and r["_params"]["noise"] == 0.0]
    nosy = [r["jcg"] for r in rows if r["_params"]["dim"] == "joint"
            and r["_params"]["noise"] == 0.0 and r["_params"]["inputs"] == 2]
    n300_clean = [r["jcg"] for r in rows
                  if r["_params"]["dim"] == "noise"
                  and r["_params"]["noise"] == 0.0 and r["_params"]["N"] == 300]
    n300_n09 = [r["jcg"] for r in rows
                if r["_params"]["dim"] == "noise"
                and abs(r["_params"]["noise"] - 0.9) < 1e-9 and r["_params"]["N"] == 300]
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    ax.hist(n300_clean, bins=8, alpha=0.65, label=f"noise=0 (ρ̄ JCG {mean(n300_clean):.2f})",
            color="#2e7d32")
    ax.hist(n300_n09, bins=8, alpha=0.65, label=f"noise=0.9 (ρ̄ {mean(n300_n09):.2f})",
            color="#c62828")
    ax.axvline(0, color="#333", lw=0.8)
    ax.set_xlabel("JCG"); ax.set_ylabel("count")
    ax.set_title("F8 JCG 分布: 噪声侵蚀联合增益\n(paired p<0.001 Bonf)")
    ax.legend(frameon=False)
    save(fig, "fig8_jcg_distribution.png")


def main():
    print("rendering figures...")
    fig1_toy()
    fig2_complexity()
    fig3_noise()
    fig4_topology()
    fig5_scaling()
    fig6_baselines()
    fig7_rho()
    fig8_jcg_dist()
    print("done.")


if __name__ == "__main__":
    main()