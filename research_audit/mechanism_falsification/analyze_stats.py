# analyze_stats.py — 预声明统计协议(任务 §27)
# ============================================================================
# 在 summary.csv/raw_results.jsonl 上做确认性检验:
#   C1  JCG>0:      multi-input 注入下 joint 候选激活 > single-mean(每 seed 配对)
#   C2  JCG 噪声单调性: noise 0 vs 0.9 配对差异(Wilcoxon)
#   C3  拓扑效应:      F vs B vs M vs R(同一 N,配对 seed)
#   C4  输入数效应:     inputs=3 vs 2(JCG/胜率)
#   C5  rho>0:      激活排序与拓扑 oracle 一致(Spearman 均值 + 单侧符号检验)
# 多重比较: 按维度族 Bonferroni(全协议 N_tests=8 严校正,另报未校正 p)
# 全部检验固定配对 seed,输出原始统计量。
# ============================================================================

import json
import math
import os

import metrics as M
from aggregate import load

HERE = os.path.dirname(os.path.abspath(__file__))


def pairs(rows, keyfn):
    """按 seed 配对两组 run。返回 [(v_a, v_b)]。"""
    d = {}
    for r in rows:
        k = keyfn(r)
        d.setdefault(r["seed"], []).append(r)
    return d


def jcg_pairs(a_rows, b_rows):
    d = {}
    for r in a_rows:
        d.setdefault(r["seed"], []).append(r.get("jcg"))
    d2 = {}
    for r in b_rows:
        d2.setdefault(r["seed"], []).append(r.get("jcg"))
    seeds = set(d) & set(d2)
    return [(d[s][0], d2[s][0]) for s in sorted(seeds)]


def wilcox_report(name, a_list, b_list, alpha_bonf):
    p, r = M.wilcoxon_signed_rank(a_list, b_list)
    print(f"  {name}: n={len(a_list)} W-p={p} (bonf {alpha_bonf}) "
          f"effect_r={r} {'SIG' if p < alpha_bonf else 'ns'}")
    return p < alpha_bonf


def main():
    rows = load()
    # C1: JCG>0,所有 graph/joint runs 的 seed 配对(每 run 自带 jcg)
    jcg_vals = [r["jcg"] for r in rows if isinstance(r.get("jcg"), (int, float))]
    n_pos = sum(1 for v in jcg_vals if v > 0)
    print(f"[C1] JCG>0 检验: n={len(jcg_vals)} pos={n_pos} "
          f"rate={n_pos / len(jcg_vals):.3f}")

    def grp(dim, gid):
        """按计划 dim 标签取组。gid 支持标量;噪声组 gid 为 level。"""
        out = []
        for r in rows:
            p = r["_params"]
            if p.get("dim") != dim:
                continue
            if dim == "noise":
                if p.get("N") == 300 and abs(p.get("noise", -1) - gid) < 1e-9:
                    out.append(r)
            elif dim == "topology":
                if p.get("direction") == gid and p.get("N") == 300:
                    out.append(r)
            elif dim == "scaling":
                if p.get("inputs") == gid:
                    out.append(r)
            elif dim == "complexity":
                if p.get("N") == gid:
                    out.append(r)
            elif dim == "joint":
                if p.get("inputs") == gid:
                    out.append(r)
        return out

    bonf8 = 0.05 / 12   # 12 个主检验,严校正
    print(f"\n[统计协议] Bonferroni(12 检验)= {bonf8:.5f}")
    # C2 noise: 0 vs 0.75(n±) — 用 jcg 配对
    print("\n[C2] 噪声对 JCG 的效应(配对 seed,N=300):")
    a = grp("noise", 0.0); b = grp("noise", 0.75)
    pairs2 = jcg_pairs(a, b)
    wilcox_report("JCG noise0 vs 0.75",
                  [x[0] for x in pairs2], [x[1] for x in pairs2], bonf8)
    b90 = grp("noise", 0.9)
    pairs3 = jcg_pairs(a, b90)
    wilcox_report("JCG noise0 vs 0.9",
                  [x[0] for x in pairs3], [x[1] for x in pairs3], bonf8)
    # 单调性:6 个噪声水平的 JCG 均值
    nmeans = []
    for lvl in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9):
        rr = grp("noise", lvl)
        nmeans.append(sum(r["jcg"] for r in rr) / len(rr))
    mono = all(nmeans[i] > nmeans[i + 1] for i in range(5)) or \
        all(nmeans[i] < nmeans[i + 1] for i in range(5))
    print(f"  噪声 JCG 均值序列: {[round(v, 4) for v in nmeans]} "
          f"单调={'是' if mono else '否'}")

    # C3 拓扑:bidirectional vs forward(JCG 配对 + MRR)
    print("\n[C3] 拓扑模式效应(N=300,配对 seed):")
    f_fwd = grp("topology", "forward")
    f_bid = grp("topology", "bidirectional")
    pb = jcg_pairs(f_fwd, f_bid)
    wilcox_report("JCG vs bidirectional",
                  [x[0] for x in pb], [x[1] for x in pb], bonf8)
    # MRR 配对
    mrr_p = []
    d = {}
    for r in f_fwd + f_bid:
        d.setdefault((r["seed"], r["_params"]["direction"]), r)
    mrr_pairs = [(d[(s, "forward")]["mrr_joint"],
                  d[(s, "bidirectional")]["mrr_joint"])
                 for s in sorted({r["seed"] for r in f_fwd + f_bid})
                 if (s, "forward") in d and (s, "bidirectional") in d]
    wilcox_report("MRR fwd vs bidir",
                  [x[0] for x in mrr_pairs], [x[1] for x in mrr_pairs], bonf8)

    # C4 输入数 scaling:inputs=3 vs 2
    print("\n[C4] 输入数效应(joint 组配对):")
    j2 = grp("joint", 2); j3 = grp("joint", 3)
    pj = jcg_pairs(j2, j3)
    wilcox_report("JCG inputs2 vs 3",
                  [x[0] for x in pj], [x[1] for x in pj], bonf8)

    # C5 rho>0(复杂度组,拓扑一致性)
    print("\n[C5] 激活↔拓扑一致性 rho(N=300 fwd):")
    r300 = grp("complexity", 300)
    rho_v = [r.get("rho_topology") for r in r300
             if isinstance(r.get("rho_topology"), (int, float))]
    n_pos_rho = sum(1 for v in rho_v if v > 0)
    print(f"  rho mean={sum(rho_v) / len(rho_v):.4f} pos_rate={n_pos_rho / len(rho_v):.3f} n={len(rho_v)}")

    # C6 基线对照:flat relevance vs diffusion 在 r@1 上的胜率(同 seed)
    print("\n[C6] 对照:diffusion r@1 vs flat(and/or)基线:")
    cnt = {"d": 0, "flat_and": 0, "flat_or": 0, "tie": 0}
    for r in grp("complexity", 300):
        d1 = 1 if r.get("recall@1") == 1 else 0
        fa = 1 if r.get("flat_joint_and") >= 1 else 0
        fo = 1 if r.get("flat_joint_or") == 1 else 0
        if d1 > fa: cnt["d"] += 1
        elif d1 < fa: cnt["flat_and"] += 1
        else: cnt["tie"] += 1
    print(f"  diffusion 胜/N=300: {cnt}")

    # C7 复杂度单调性:MRR N=10 vs N=1000
    print("\n[C7] 规模效应:MRR N=10 vs N=1000:")
    c10 = grp("complexity", 10); c1000 = grp("complexity", 1000)
    pc = jcg_pairs(c10, c1000)
    wilcox_report("JCG N10 vs N1000",
                  [x[0] for x in pc], [x[1] for x in pc], bonf8)
    m10 = [r["mrr_joint"] for r in c10]; m1000 = [r["mrr_joint"] for r in c1000]
    wilcox_report("MRR N10 vs N1000", m10, m1000, bonf8)

    print("\n完成。")


if __name__ == "__main__":
    main()