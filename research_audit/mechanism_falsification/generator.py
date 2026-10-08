# generator.py — 受控随机图/任务生成(确定性 seed 可复现)
# ============================================================================
# 为 falsification 提供四种拓扑 mode 与可控噪声/规模/输入数的构造器。
# 不依赖 production 代码。
# ============================================================================

import math
import random

from reference import Graph, GNode, GEdge


def random_task(N, inputs=2, candidates=8, avg_degree=2.0,
                direction="forward", noise=0.0, seed=1,
                max_hops=3, joint=True):
    """生成一个"多输入检索任务"图。

    结构(每种方向 mode 下保持同一节点集/边集/候选集):
      - 输入节点 I0..Ik:任务的查询点
      - 候选节点 C0..Cm:可被排序的答案。其中:
          * 若 joint=True:C0 = JOINT 候选,与全部输入都有路径
          * C1..Cs = SINGLE 候选,只与恰一个输入有路径
          * 其余 = DISTRACTOR,与输入无结构化路径(纯噪声环境)
      - 中间节点:填充路径 hop 与干扰
    边权默认 0.5(受控);方向按 mode:
      forward / bidirectional / mixed(50%)/ random(逐边随机)
    噪声:额外随机边(与输入无关的连接)占比 noise。
    返回 (Graph, {"inputs": [...], "joint": "C0", "singles": [...],
                  "distractors": [...]})
    """
    rng = random.Random(seed)
    g = Graph()
    special = ["I", "C", "m", "s"]  # 特殊节点前缀(输入/候选/汇聚/单链中继)
    filler = [f"n{i}" for i in range(N)]
    for i in filler:
        g.add_node(i)
    iids = [f"I{i}" for i in range(inputs)]
    cids = [f"C{i}" for i in range(candidates)]

    def wire(a, b, w=None, bidir=None):
        w = 0.5 if w is None else w
        if bidir is None:
            bidir = {"forward": False, "bidirectional": True,
                     "mixed": rng.random() < 0.5,
                     "random": rng.random() < 0.5}[direction]
        e = GEdge(str(a), str(b), w, bidir)
        g.out.setdefault(str(a), []).append(e)
        g.inn.setdefault(str(b), []).append(e)
        g.nodes.setdefault(str(a), GNode(str(a)))
        g.nodes.setdefault(str(b), GNode(str(b)))

    # JOINT 候选:从每个输入经不同中间节点到 C0(共享汇聚)
    mids = [f"m{i}" for i in range(inputs)]
    for j, iid in enumerate(iids):
        g.add_node(mids[j])
        wire(iid, mids[j])
        wire(mids[j], "C0")
    # SINGLE 候选:每个输入独享一个候选
    for j, iid in enumerate(iids):
        if len(cids) > 1 + j:
            mid = f"s{j}"
            g.add_node(mid)
            wire(iid, mid)
            wire(mid, cids[1 + j])
    # DISTRACTOR:占位候选,仅与填充节点相连(与输入无结构化路径)
    for cid in cids[len(iids) + 1:]:
        g.add_node(cid)
        pick = rng.choice([f for f in filler])
        wire(pick, cid, w=rng.uniform(0.2, 0.9))
    # 填充节点连成链(背景路径),让 N 真正参与扩散复杂度;
    # 每条链边 25% 概率附加一条随机出边(世界级联),输入层各带
    # 一条泄漏边进链——激活流入背景,复杂度才有判别力。
    # 注意:泄漏边加在输入层(均匀稀释全部候选),不加在 mid 汇聚层
    # (否则只分流 JOINT 候选,污染 JCG 的对比前提)。
    all_task_nodes = iids + cids + mids + [f"s{j}" for j in range(inputs)]
    for i in range(1, len(filler)):
        wire(filler[i - 1], filler[i], w=rng.uniform(0.3, 0.8))
        if rng.random() < 0.25:
            wire(filler[i], rng.choice(all_task_nodes or [filler[i]]),
                 w=rng.uniform(0.2, 0.9))
    for j in range(inputs):
        if rng.random() < 0.7:
            wire(iids[j], rng.choice(filler), w=rng.uniform(0.3, 0.9))
    # 噪声边:在填充链路与特殊路径之间随机连边(0.05 概率影响特殊路径)
    all_ids = list(g.nodes)
    if all_ids and noise > 0.0:
        edge_total = sum(len(v) for v in g.out.values())
        n_noise = int(noise * edge_total)
        for _ in range(n_noise):
            a = rng.choice(all_ids)
            b = rng.choice(all_ids)
            if a != b:
                wire(a, b, rng.uniform(0.2, 0.9))
    return g, {"inputs": iids, "joint": "C0",
               "singles": cids[1:1 + inputs],
               "distractors": cids[1 + inputs:]}


def _edge_total(g):
    return sum(len(v) for v in g.out.values())


def task_stats(g):
    out = sum(len(v) for v in g.out.values())
    inn = sum(len(v) for v in g.inn.values())
    return {"edges_fwd": out, "edges_bwd": inn, "nodes": len(g.nodes)}


def topology_score(g, cand, iids):
    """纯拓扑 oracle:score = 1/(1+max_i d(Ii→cand))。

    取 max 而非 Σ:Σ 系统性惩罚共享汇聚候选(JOINT 要对每个输入各算
    一路),与"多输入汇聚应更强"的任务语义恒向相反;max 仅度量
    "离最远的输入有多远",对汇聚焦点中立。
    """
    worst = 0
    reachable = 0
    for ii in iids:
        d = _bfs_dist(g, ii, cand)
        if d is None:
            return 1.0 / (1.0 + 99.0 * len(iids))   # 不可达=惩罚分
        reachable += 1
        worst = max(worst, d)
    return 1.0 / (1.0 + worst) * (1.0 + 0.25 * (reachable - 1))


def _bfs_dist(g, src, dst):
    from collections import deque
    seen = {src}
    q = deque([(src, 0)])
    while q:
        n, d = q.popleft()
        if n == dst:
            return d
        for e in g.out.get(n, ()):
            if e.dst not in seen:
                seen.add(e.dst); q.append((e.dst, d + 1))
        for e in g.inn.get(n, ()):
            if e.src not in seen:
                seen.add(e.src); q.append((e.src, d + 1))
    return None  # 不可达=无穷远