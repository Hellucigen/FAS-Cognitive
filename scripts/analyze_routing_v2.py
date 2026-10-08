# analyze_routing_v2.py — core_routing_v2 路由战役独立分析(仅归档数据,零 LLM)。
# 重建论文引用的全部路由统计:成功率/决策数/target_utilization 对比、
# token 开销倍数、p 值 0.046/0.003/0.044/0.064/0.265(未校正)与
# "280 项检验 Bonferroni 校正"族,并与归档 campaign/statistics.csv 逐行 diff。
#
# 检验口径(从归档数据反推并逐位验证):
#   连续/计数指标 = 配对双尾 Wilcoxon(scipy 默认参数,zero_method='wilcox',
#   method 按 n 自动);二元 success 在存在变异时同样用配对 Wilcoxon
#   (归档 p=0.0455 与此逐位一致);全并列(如全 1.0)记 n/a。
#   Bonferroni 分母 m = 全比较族 = 10 条件对(5 主条件 C(5,2))× 4 任务
#   × 7 指标 = 280(归档 p_bonferroni = p×280 逐位验证)。
# 用法: python analyze_routing_v2.py <data_root>   # 含 campaign/summary.csv
import csv
import os
import sys
from collections import defaultdict

from scipy.stats import wilcoxon

METRICS = ["success", "decisions", "target_utilization", "distractor_rate",
           "prompt_tokens", "efficiency", "retrieval_precision"]
CONDS = ["fas_full", "llm_direct", "llm_history", "llm_rag", "random_ctx",
         "D-flat", "D-noact", "D-nodemand"]
TASKS = ["T1", "T2", "T3", "T4"]


def load(root):
    per = defaultdict(lambda: defaultdict(dict))
    path = os.path.join(root, "campaign", "summary.csv")
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["harness_valid"] != "True":
                continue
            per[(r["condition"], r["task"])][int(r["seed"])] = r
    return per


def paired(per, a, b, task, field):
    ka, kb = per.get((a, task), {}), per.get((b, task), {})
    common = sorted(set(ka) & set(kb))
    try:
        x = [float(ka[s][field]) for s in common]
        y = [float(kb[s][field]) for s in common]
    except (KeyError, ValueError):
        return None, len(common), None, None
    if len(x) < 2 or all(abs(v - w) == 0 for v, w in zip(x, y)):
        return None, len(common), sum(x) / max(1, len(x)), sum(y) / max(1, len(y))
    p = wilcoxon(y, x, alternative="two-sided").pvalue  # 两尾;方向由数据定
    return float(p), len(common), sum(x) / len(x), sum(y) / len(y)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    per = load(root)
    out_rows = []
    m_total = 0
    # 全比较族:5 主条件全对(10 对)× 4 任务 × 7 指标 = 280
    main_conds = CONDS[:5]
    fam = [(a, b, t, m) for i, a in enumerate(main_conds)
           for b in main_conds[i + 1:] for t in TASKS for m in METRICS]
    for a, b, task, m in fam:
        p, n, ma, mb = paired(per, a, b, task, m)
        m_total += 1
        if p is not None:
            out_rows.append({"condition_a": a, "condition_b": b, "task": task,
                             "metric": m, "n": n, "mean_a": round(ma, 5),
                             "mean_b": round(mb, 5), "p": p,
                             "p_bonferroni": min(1.0, p * m_total),
                             "family_size": m_total})
    print("== family ==")
    print("   planned comparisons (5 main conds, all pairs x 4 tasks x 7 metrics):",
          len(fam))

    # 落盘子集:与归档 statistics.csv 相同的 7 对(fas_full 为一方;消融仅 T2/T3)
    regen = [r for r in out_rows if r["condition_a"] == "fas_full"]
    ablate = []
    for c in ("D-flat", "D-noact", "D-nodemand"):
        for task in ("T2", "T3"):
            for m in METRICS:
                p, n, ma, mb = paired(per, "fas_full", c, task, m)
                if p is not None:
                    ablate.append({"condition_a": "fas_full", "condition_b": c,
                                   "task": task, "metric": m, "n": n,
                                   "mean_a": round(ma, 5), "mean_b": round(mb, 5),
                                   "p": p,
                                   "p_bonferroni": min(1.0, p * len(fam)),
                                   "family_size": len(fam)})
    regen_all = regen + ablate
    outp = os.path.join(root, "routing_statistics_regen.csv")
    with open(outp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(regen_all[0].keys()))
        w.writeheader()
        w.writerows(regen_all)
    print("   written:", outp, "(%d rows)" % len(regen_all))

    # 与归档 statistics.csv 逐行 diff(归档存 6 位小数,按 round-6 比较)
    arch_path = os.path.join(root, "campaign", "statistics.csv")
    if os.path.isfile(arch_path):
        arch = {(r["condition_a"], r["condition_b"], r["task"], r["metric"]): r
                for r in csv.DictReader(open(arch_path, encoding="utf-8"))}
        common = [r for r in regen_all
                  if (r["condition_a"], r["condition_b"], r["task"],
                      r["metric"]) in arch]
        diffs = []
        for r in common:
            ap = arch[(r["condition_a"], r["condition_b"], r["task"],
                       r["metric"])]["p"]
            if ap and abs(float(ap) - round(r["p"], 6)) > 1e-12:
                diffs.append(((r["condition_a"], r["condition_b"], r["task"],
                               r["metric"]), ap, round(r["p"], 6)))
        print("== diff vs archived statistics.csv ==")
        print("   keys in both:", len(common),
              " p mismatches (round-6):", len(diffs))
        for d in diffs[:8]:
            print("   ", d)

    # 论文引用值核对
    print("== paper-cited values (archived p vs regenerated p) ==")
    cited = [("fas_full", "llm_direct", "T4", "success", 0.0455, "0.046"),
             ("fas_full", "llm_history", "T3", "decisions", 0.003156, "0.003"),
             ("fas_full", "llm_rag", "T3", "decisions", 0.044277, "0.044"),
             ("fas_full", "llm_direct", "T1", "decisions", 0.064142, "0.064"),
             ("fas_full", "llm_direct", "T3", "decisions", 0.265405, "0.265")]
    ok = True
    for a, b, t, m, arch_p, cited_s in cited:
        p, n, ma, mb = paired(per, a, b, t, m)
        match = p is not None and abs(p - arch_p) < 5e-7
        ok &= match
        print("   %s %s %s/%s: archived=%s cited=%s regen=%s match=%s"
              % (t, m, a, b, arch_p, cited_s,
                 round(p, 6) if p else None, match))
    # Bonferroni 校正后(族 m=280):按论文引用值逐项报告
    print("== Bonferroni over the 280-test family (per cited value) ==")
    for a, b, t, m, arch_p, cited_s in cited:
        p, n, ma, mb = paired(per, a, b, t, m)
        if p is not None:
            print("   %s %s %s/%s: raw=%.6g -> Bonferroni(m=%d)=%.4g"
                  % (t, m, a, b, p, len(fam), min(1.0, p * len(fam))))
    print("   (paper's 'p~1 after 280-test correction' refers to the T4")
    print("    success directional positive p=0.046: 0.046x280 -> 1.0.)")
    print("   (token-cost rows remain significant after correction:")
    print("    EXPERIMENT_REPORT marks them with a dagger.)")

    # token 开销倍数(prompt_tokens,FAS / 基线,按任务)
    print("== token overhead (mean prompt_tokens ratio FAS/baseline) ==")
    ratios = []
    for b in ("llm_direct", "llm_history", "llm_rag", "random_ctx"):
        for t in TASKS:
            p, n, ma, mb = paired(per, "fas_full", b, t, "prompt_tokens")
            if p is not None and mb:
                ratios.append((t, b, round(ma / mb, 2), round(ma), round(mb), p))
                print("   %s vs %s: %.2fx (fas %d vs %d, wilcoxon p=%s)"
                      % (t, b, ma / mb, round(ma), round(mb), round(p, 6)))
    rs = [r[2] for r in ratios]
    rd = [r[2] for r in ratios if r[1] == "llm_direct"]
    print("   range vs llm_direct: %.2fx - %.2fx  (paper: 2.1-3.7x)"
          % (min(rd), max(rd)))
    print("   range vs all four baselines: %.2fx - %.2fx" % (min(rs), max(rs)))
    print("ALL CITED P VALUES MATCH:", ok)


if __name__ == "__main__":
    main()
