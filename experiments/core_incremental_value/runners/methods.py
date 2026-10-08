# methods.py — 五种打分方法(独立实现,预算相等性约束见 manifest §information_budget_equality)
# ============================================================================
# FAS: spreading activation(扩散核心 = generator.Graph,生产语义提取,663-run 已验证)
# B1 : direct activation — 仅直接出边权重求和,无传播
# B2 : flat graph relevance — 同图同信息,无传播:每输入→候选的最优路径边权乘积之和
#      (路径长度 ≤ max_len = joint_hops + 2;负边不扩展路径,见 manifest negative_edges)
# B3 : random — sanity baseline
# B4 : oracle — 仅校验 generator(不参与比较)
# 所有方法共享同一 Graph/task/输入子集/tie-break(同 seed 固定)——预算相等。
# 唯一差异: 传播动力学 vs 直接/平坦聚合。
# ============================================================================

import random

EPS = 1e-9


def tie_break_for(seed, candidates, rng=None):
    """同 seed 固定的一次性候选洗牌(避免字典序/插入序成为 tie-break 伪影)。"""
    r = random.Random(seed * 7919 + 17)
    order = list(candidates)
    r.shuffle(order)
    return {c: i for i, c in enumerate(order)}


def rank_candidates(scores, cand_order, tie_order):
    """按 (score desc, tie-order asc) 排序,返回 {cand: rank}。"""
    scored = [c for c in cand_order if c in scores and scores.get(c, 0.0) > EPS]
    scored.sort(key=lambda c: (-scores[c], tie_order.get(c, 1e9)))
    return {c: i for i, c in enumerate(scored)}


# ------------------------------------------------ FAS: spreading activation

def score_fas(g, task, input_subset, steps):
    """多播扩散后各候选激活。steps 预注册 = joint_hops + 2。"""
    g.reset()
    acts = g.diffuse_round(steps=steps, seeds=input_subset,
                           seed_strength=5.0, ratio=0.5, transfer=1.0,
                           beta=1.0, gain=1.0, decay=0.05, cap=5.0)
    return {c: acts.get(c, 0.0) for c in task["candidates"]}


def diffusion_stats(g, task, input_subset, steps):
    """扩散后的全图观测(entropy/gini/reachable 等用)。与 score_fas 同一次扩散。"""
    g.reset()
    acts = g.diffuse_round(steps=steps, seeds=input_subset,
                           seed_strength=5.0, ratio=0.5, transfer=1.0,
                           beta=1.0, gain=1.0, decay=0.05, cap=5.0)
    cands = {c: acts.get(c, 0.0) for c in task["candidates"]}
    return cands, acts


# ------------------------------------------------ B1: direct activation

def score_b1(g, task, input_subset):
    """候选 = Σ_{i∈subset} 直接出边权重(i→cand)。无传播,负边计入负值。"""
    out = {}
    for c in task["candidates"]:
        s = 0.0
        for i in input_subset:
            for e in g.out.get(i, ()):
                if e.dst == c:
                    s += e.weight
        out[c] = s
    return out


# ------------------------------------------------ B2: flat graph relevance

def _best_path_products(g, srcs, cands, max_len):
    """对每个 src×cand 求最佳简单路径边权乘积(max)。深度优先,负边不扩展。"""
    best = {}
    cand_set = set(cands)

    def dfs(node, depth, prod, seen):
        if depth > max_len:
            return
        if node in cand_set:
            key = (node,)
            old = best.get((src_anchor, node), -1.0)
            if prod > old:
                best[(src_anchor, node)] = prod
        for e in g.out.get(node, ()):
            if e.weight <= 0.0:
                continue
            if e.dst in seen:
                continue
            if depth + 1 > max_len:
                continue
            seen.add(e.dst)
            dfs(e.dst, depth + 1, prod * e.weight, seen)
            seen.discard(e.dst)

    for src_anchor in srcs:
        for c in cands:
            best.setdefault((src_anchor, c), 0.0)
        seen = {src_anchor}
        dfs(src_anchor, 0, 1.0, seen)
    return best


def score_b2(g, task, input_subset, max_len):
    """候选 = Σ_{i∈subset} best_path_product(i→cand)。同图同信息,无激活传播。"""
    best = _best_path_products(g, input_subset, task["candidates"], max_len)
    out = {}
    for c in task["candidates"]:
        out[c] = sum(best.get((i, c), 0.0) for i in input_subset)
    return out


# ------------------------------------------------ B3: random

def score_b3(task, seed):
    r = random.Random(seed * 104729 + 3)
    return {c: r.random() for c in task["candidates"]}


# ------------------------------------------------ B4: oracle(generator 校验)

def score_b4(task):
    """joint 排第 1,其余 0。仅 preflight 校验 generator 逻辑。"""
    out = {}
    for c in task["candidates"]:
        out[c] = 1.0 if c == task.get("joint") else 0.0
    return out


METHODS = {"fas": score_fas, "b1": score_b1, "b2": score_b2,
           "b3": score_b3, "b4": score_b4}