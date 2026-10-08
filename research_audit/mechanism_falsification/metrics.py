# metrics.py — falsification 指标集(预声明,见 manifest)
# ============================================================================
# 所有正式实验统一从这里取指标,禁止在拿到结果后新增/删改。
# ============================================================================

import math
import random
import statistics

EPS = 1e-12

def _rank_of(acts, target):
    """按激活降序的 rank(0-based)。target 不在激活表返回 None。"""
    if target not in acts:
        return None
    order = sorted(acts.items(), key=lambda kv: -kv[1])
    for i, (k, _) in enumerate(order):
        if k == target:
            return i
    return None

def info(scores):
    """run-level 指标包。scores: {candidate_id: activation} 全图激活。"""
    vals = list(scores.values())
    n = len(vals)
    if n == 0:
        return {}
    total = sum(vals)
    dist = [v / (total + EPS) for v in vals]
    entropy = -sum(p * math.log(p + EPS) for p in dist if p > EPS)
    mx = max(vals)
    gini = 0.0
    sv = sorted(vals)
    cum = 0.0
    for i, v in enumerate(sv):
        cum += (i + 1) * v
    if total > EPS and n > 1:
        gini = (2 * cum / (n * total)) - (n + 1) / n
    return {
        "n_active": n,
        "total_activation": round(total, 6),
        "max_activation": round(mx, 6),
        "entropy": round(entropy, 6),
        "gini": round(max(0.0, min(1.0, gini)), 6),
        "zero_fraction": round(sum(1 for v in vals if v < EPS) / n, 4),
    }

def rank_metrics(acts, joint, singles, distractors, topk=(1, 3)):
    """排序指标:MRR/Recall@K/Top1/margin,全部限定在候选集内排序。

    测量域说明(2026-09-30 修正,实验记录在案):全图排序下输入/中间节点
    恒压榜首(seed_strength 注入伪影),候选永远进不了真实 top-k——那把
    metric 变成了"输入节点第几名",不是任务语义。任务语义是"哪个候选
    激活最强",故候选集 = singles+joint+distractors。
    """
    cands = [joint] + list(singles) + list(distractors)
    cand_acts = {c: acts[c] for c in cands if c in acts}
    if not cand_acts:
        jr = None; mrr = 0.0; rec = {f"recall@{k}": None for k in topk}
    else:
        order = sorted(cand_acts.items(), key=lambda kv: -kv[1])
        jr = next((i for i, (k, _) in enumerate(order) if k == joint), None)
        mrr = (1.0 / (jr + 1)) if jr is not None else 0.0
        rec = {}
        for k in topk:
            topk_ids = set(kk for kk, _ in order[:k])
            rec[f"recall@{k}"] = (1.0 if joint in topk_ids else 0.0)
    singles_act = [acts[s] for s in singles if s in acts]
    margin = acts.get(joint) - max(singles_act) if singles_act else None
    dist_act = [acts[d] for d in distractors if d in acts]
    margin_global = (acts.get(joint) - max(dist_act)) if dist_act else None
    # 辅助:全图 rank(测量域备注用)
    return {
        "mrr_joint": round(mrr, 5),
        **rec,
        "margin_vs_best_single": round(margin, 6) if margin is not None else None,
        "margin_vs_best_distractor": round(margin_global, 6) if margin_global is not None else None,
        "joint_rank": jr,
    }

def jcg(act_joint_multi, act_joint_singles):
    """joint convergence gain(§37):
    JCG = act(O_joint | I1..Ik) − mean_i act(O_joint | I_i)"""
    return round(act_joint_multi - statistics.mean(act_joint_singles), 6)

def rank_corr(acts, score_fn, cand_ids):
    """候选集合上 activation 排序 vs 拓扑 score 排序的 Spearman ρ(平均秩)。"""
    pairs = [(acts[c], score_fn(c)) for c in cand_ids if c in acts]
    if len(pairs) < 3:
        return None
    ax = [p[0] for p in pairs]; by = [p[1] for p in pairs]
    def _avg_ranks(vals):
        """并列值取平均秩(修复:dict 覆盖式映射在并列时产生伪相关)。"""
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks
    def _spearman(x, y):
        n = len(x)
        rx = _avg_ranks(x); ry = _avg_ranks(y)
        mx = sum(rx) / n; my = sum(ry) / n
        cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / n
        sx = math.sqrt(sum((a - mx) ** 2 for a in rx) / n)
        sy = math.sqrt(sum((b - my) ** 2 for b in ry) / n)
        if sx == 0 or sy == 0:
            return None
        return cov / (sx * sy)
    return _spearman(ax, by)

# ---------------------------------------------------------------- 统计

def wilcoxon_signed_rank(d1, d2):
    """配对 Wilcoxon 符号秩检验(两尾),返回 (p, r-effect)。"""
    diffs = [b - a for a, b in zip(d1, d2) if abs(a - b) > 1e-12]
    n = len(diffs)
    if n == 0:
        return 1.0, 0.0
    ranked = sorted(enumerate(diffs), key=lambda x: abs(x[1]))
    # 平均秩处理平局
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
    z = (w - n * (n + 1) / 4) / math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
    # 两尾正态近似 p
    from math import erfc
    p = erfc(abs(z) / math.sqrt(2))
    # 效应量 r = |z|/sqrt(2n)
    r = abs(z) / math.sqrt(2 * n)
    return max(1e-12, p), r

def bootstrap_ci(values, stat_fn, n_boot=1000, ci=0.95, seed=1):
    rng = random.Random(seed)
    n = len(values)
    stats = []
    for _ in range(n_boot):
        sample = [rng.choice(values) for _ in range(n)]
        try:
            stats.append(stat_fn(sample))
        except Exception:
            continue
    if not stats:
        return (None, None)
    stats.sort()
    lo = stats[int((1 - ci) / 2 * len(stats))]
    hi = stats[int((1 + ci) / 2 * len(stats)) - 1]
    return (round(lo, 5), round(hi, 5))