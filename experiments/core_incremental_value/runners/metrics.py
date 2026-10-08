# metrics.py — run 指标 + JCG/ΔJCG + 统计推断(预声明,见 manifest §metrics/§statistics)
# ============================================================================
# 指标在拿数据前锁定;统计: 配对 Wilcoxon + r 效应量 + bootstrap 95% CI(median),
# Holm 校正(主族 = 4 条 confirmatory ΔJCG)。修改任何公式即为违反预注册。
# ============================================================================

import math
import random
import statistics

EPS = 1e-9


def _entropy_gini(vals):
    """vals: 非零候选激活序列。返回 (entropy, gini)。"""
    n = len(vals)
    if n == 0:
        return 0.0, 0.0
    total = sum(vals)
    if total <= EPS:
        return 0.0, 0.0
    dist = [v / total for v in vals]
    entropy = -sum(p * math.log(p) for p in dist if p > EPS)
    sv = sorted(vals)
    cum = 0.0
    for i, v in enumerate(sv):
        cum += (i + 1) * v
    gini = (2 * cum / (n * total)) - (n + 1) / n
    return entropy, max(0.0, min(1.0, gini))


def run_metrics(scores, ranks, task, seed, acts_all=None, seeds_used=None):
    """单个 run(一种方法 × 一种输入组合)的指标包。

    ranks: {cand: rank} 由 rank_candidates 产出(候选域内排序)。
    returns dict(全部字段直接进 raw row)。
    """
    cands = task["candidates"]
    joint = task.get("joint")
    singles = task.get("singles") or []
    jrank = ranks.get(joint) if joint else None
    jact = scores.get(joint, 0.0) if joint else None
    mrr = (1.0 / (jrank + 1)) if jrank is not None else None
    n_cand = len(cands)
    recall1 = 1.0 if jrank == 0 else 0.0
    recall3 = 1.0 if (jrank is not None and jrank < 3) else 0.0
    recall5 = 1.0 if (jrank is not None and jrank < 5) else 0.0
    ndcg = (1.0 / math.log2(jrank + 2)) if jrank is not None else None
    # margins(相对 best single)
    act_margin = rank_margin = None
    if joint and singles:
        bsingle = max((scores.get(s, 0.0) for s in singles), default=0.0)
        if jact is not None:
            act_margin = jact - bsingle
        bridx = min((ranks.get(s) for s in singles if s in ranks), default=None)
        if jrank is not None and bridx is not None:
            rank_margin = bridx - jrank  # >0 = joint 排得更好
    # top-3 集合(供 topk_stability 在分析期配对计算)
    top3 = [c for c, r in sorted(ranks.items(), key=lambda kv: kv[1])[:3]]
    # 候选激活分布(entropy/gini/top3 内在候选域)
    cand_acts = [scores[c] for c in cands if scores.get(c, 0.0) > EPS]
    entropy, gini = _entropy_gini(cand_acts)
    # 全图扩散观测(仅 FAS 填充;其余方法 None)
    reachable = total_act = None
    if acts_all is not None:
        seed_set = set(seeds_used or ())
        reachable = len([k for k in acts_all if k not in seed_set])
        total_act = round(sum(acts_all.values()), 6)
    return {
        "target_rank": jrank,
        "target_activation": round(jact, 6) if jact is not None else None,
        "mrr": round(mrr, 5) if mrr is not None else None,
        "recall@1": recall1, "recall@3": recall3, "recall@5": recall5,
        "ndcg": round(ndcg, 5) if ndcg is not None else None,
        "activation_margin": round(act_margin, 6) if act_margin is not None else None,
        "rank_margin": rank_margin,
        "entropy": round(entropy, 5), "gini": round(gini, 5),
        "reachable_nodes": reachable, "total_activation": total_act,
        "top3_ids": top3,
        "n_candidates": n_cand,
        "status": "OK",
    }


def jcg_value(multi_target_score, single_target_scores):
    """JCG = score(joint | I1..In) − mean_i score(joint | Ii)。"""
    if multi_target_score is None:
        return None
    return round(multi_target_score - statistics.mean(single_target_scores), 6)


# ---------------------------------------------------------------- 统计推断

def wilcoxon_signed_rank(d1, d2, one_tailed=False):
    """配对 Wilcoxon(符号秩)。返回 (p, r)。默认两尾;one_tailed=True 单尾。"""
    diffs = [b - a for a, b in zip(d1, d2) if abs(b - a) > 1e-12]
    n = len(diffs)
    if n == 0:
        return 1.0, 0.0, 0.0
    ranked = sorted(enumerate(diffs), key=lambda x: abs(x[1]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(ranked[j + 1][1]) == abs(ranked[i][1]):
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[ranked[k][0]] = avg
        i = j + 1
    w = sum(r for idx, r in enumerate(ranks) if diffs[idx] > 0)
    mu = n * (n + 1) / 4
    denom = math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
    z = (w - mu) / denom if denom > 0 else 0.0
    p_two = math.erfc(abs(z) / math.sqrt(2))
    p = (p_two / 2) if one_tailed else p_two
    r_eff = abs(z) / math.sqrt(2 * n)
    return max(1e-12, p), r_eff, (w - mu)


def holm_correct(pvals):
    """Holm 校正(升序 p;adjusted[i] = max_{j≤i} min(1, p_(j)·(n−j+1)))。"""
    n = len(pvals)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvals[i])
    adj = [0.0] * n
    running = 0.0
    for rank, idx in enumerate(order):
        val = min(1.0, pvals[idx] * (n - rank))
        running = max(running, val)
        adj[idx] = running
    return adj


def bootstrap_ci_median(diffs, n_boot=1000, ci=0.95, seed=42):
    """配对差异序列的 95% bootstrap CI(median)。"""
    rng = random.Random(seed)
    n = len(diffs)
    if n == 0:
        return None, None
    meds = []
    for _ in range(n_boot):
        s = [rng.choice(diffs) for _ in range(n)]
        meds.append(statistics.median(s))
    meds.sort()
    lo = meds[int((1 - ci) / 2 * len(meds))]
    hi = meds[int((1 + ci) / 2 * len(meds)) - 1]
    return round(lo, 6), round(hi, 6)


def exact_binomial_gt0(x, n):
    """p(≥x 成功 | 成功概率 0.5, n 次),单尾。用于 JCG>0 率。"""
    import math as _m
    p = 0.0
    for k in range(x, n + 1):
        p += _m.comb(n, k) * 0.5 ** n
    return min(1.0, p)