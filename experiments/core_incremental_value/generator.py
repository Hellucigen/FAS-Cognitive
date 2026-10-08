# generator.py — 收官实验受控图/任务生成(独立于 production,确定性 seed)
# ============================================================================
# 拓扑: T1 纯净汇聚 / T2 局部干扰 / T3 共享噪声 / T4 混合(核心)
# 反事实: C1 星形汇聚 / C2 无联合目标 / C3 组合支持
# 组合测试: O3(§16) — combo_task()
# 负边: neg_mode ∈ {none, neg, mixed}
#
# joint 路径定义(预注册,2026-09-30): 'joint_hops' = 输入节点到 O_joint 的边数。
#   hops=1: I → C0 直接边
#   hops=2: I → pivot(共享) → C0
#   hops=3: I → m_j(独享) → pivot(共享) → C0
#   hops=4: I → m_j → pivot → j0 → C0
# singles 固定 2 跳: I → s_j(独享) → C_{1+j}(深度劣势由汇聚机制克服,是设计一部分)。
#
# 结构复用 mechanism_falsification(663-run 已验证);节点/candidate id 全部为
# 不透明字符串,排序必须只依赖 activation/分数。本文件只依赖标准库。
# ============================================================================

import random

DEFAULT_WEIGHT = 0.5
EPS = 1e-9


class GNode:
    __slots__ = ("id", "activation")

    def __init__(self, ident, activation=0.0):
        self.id = ident
        self.activation = activation


class GEdge:
    __slots__ = ("src", "dst", "weight", "bidirectional")

    def __init__(self, src, dst, weight=0.5, bidirectional=False):
        self.src = src
        self.dst = dst
        self.weight = float(weight)
        self.bidirectional = bool(bidirectional)


class Graph:
    """邻接表图。id 必须是 str。activation 语义 = production 提取模型(已验证)。"""

    def __init__(self):
        self.nodes = {}
        self.out = {}
        self.inn = {}
        self._fired = set()

    def add_node(self, ident):
        ident = str(ident)
        if ident not in self.nodes:
            self.nodes[ident] = GNode(ident)
        return ident

    def add_edge(self, src, dst, weight=0.5, bidirectional=False):
        src, dst = str(src), str(dst)
        if src == dst:
            raise ValueError(f"self-loop edge {src}->{src} not allowed")
        self.add_node(src)
        self.add_node(dst)
        for e in self.out.get(src, ()):
            if e.dst == dst and e.bidirectional == bidirectional:
                raise ValueError(f"duplicate edge {src}->{dst}")
        e = GEdge(src, dst, weight, bidirectional)
        self.out.setdefault(src, []).append(e)
        self.inn.setdefault(dst, []).append(e)
        return e

    def edges_of(self, nid):
        """nid 的全部可传播边:(edge, direction_forward)。"""
        pairs = []
        for e in self.out.get(nid, ()):
            pairs.append((e, True))
        for e in self.inn.get(nid, ()):
            if e.bidirectional:
                pairs.append((e, False))
        return pairs

    # ---------------- 扩散核心(提炼 production 语义,663-run 已验证) -----------

    def _emit(self, nid, delta):
        node = self.nodes[nid]
        a = node.activation
        if a <= 0.0:
            return 0.0
        pairs = self.edges_of(nid)
        if not pairs:
            return 0.0
        emits = []
        total_w = 0.0
        total_abs_neg = 0.0
        for e, _fwd in pairs:
            w = e.weight
            if w > 0:
                total_w += w
            else:
                total_abs_neg += -w
        denom = total_w if total_w > 0 else total_abs_neg
        if denom <= 0.0:
            return 0.0
        for e, fwd in pairs:
            w = e.weight
            contrib = a * w * self.beta * self.gain / denom
            if not fwd:
                contrib *= 0.5
            emits.append((e.dst if fwd else e.src, contrib, fwd))
        emitted = sum(c for _, c, _ in emits if c > 0)
        budget = a * self.ratio
        if emitted > budget > 0.0:
            scale = budget / emitted
            emits = [(d, c * scale, f) for d, c, f in emits]
            emitted = budget
        for d, c, f in emits:
            delta[d] = delta.get(d, 0.0) + c
        return emitted

    def diffuse_round(self, steps=3, seeds=None, seed_strength=5.0,
                      ratio=0.5, transfer=1.0, beta=1.0, gain=1.0,
                      decay=0.05, cap=5.0):
        """一轮扩散:注入 seeds → steps×(发射+衰减) + 尾衰减。返回激活字典。"""
        self.beta = beta
        self.gain = gain
        self.ratio = ratio
        self.transfer = transfer
        self.decay = decay
        self.cap = cap
        sources = set()
        for sid in (seeds or []):
            sid = str(sid)
            n = self.nodes.get(sid)
            if n is not None:
                n.activation = min(cap, n.activation + seed_strength)
                sources.add(sid)
        self._fired = set()
        for _ in range(steps):
            self._step(sources)
            self._decay()
        self._decay()
        acts = {nid: round(n.activation, 9)
                for nid, n in self.nodes.items() if n.activation > EPS}
        self.reset()
        return acts

    def _step(self, sources):
        eps = 1e-4
        delta = {}
        for nid in list(self.nodes.keys()):
            n = self.nodes[nid]
            if n.activation < eps:
                continue
            if nid in self._fired:
                continue
            emitted = self._emit(nid, delta)
            if emitted > 0.0:
                if nid not in sources:
                    delta[nid] = delta.get(nid, 0.0) - emitted * self.transfer
            self._fired.add(nid)
        for dst, d in delta.items():
            n = self.nodes.get(dst)
            if n is None:
                continue
            n.activation = max(0.0, min(self.cap, n.activation + d))

    def _decay(self):
        eps = 1e-4
        for n in self.nodes.values():
            n.activation *= (1.0 - self.decay)
            if n.activation < eps:
                n.activation = 0.0

    def reset(self):
        for n in self.nodes.values():
            n.activation = 0.0


# ---------------------------------------------------------------- 候选组织

def _flatten_distinct(seq):
    out = []
    seen = set()
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def check_graph_integrity(g, task):
    """sanity: 无自环/无重复边/权重在 [−1, 1]/候选存在/节点数一致。"""
    problems = []
    for src, edges in g.out.items():
        seen = set()
        for e in edges:
            if e.src == e.dst:
                problems.append(f"self-loop {e.src}")
            if not (-1.0 <= e.weight <= 1.0):
                problems.append(f"weight out of range {e.weight} @ {e.src}->{e.dst}")
            key = (e.dst, e.bidirectional)
            if key in seen:
                problems.append(f"dup edge {e.src}->{e.dst} bidir={e.bidirectional}")
            seen.add(key)
    for c in task.get("candidates", []):
        if c not in g.nodes:
            problems.append(f"candidate missing in graph: {c}")
    return problems


# ---------------------------------------------------------------- 主构造器

def make_task(topology="T1", N=300, inputs_n=3, n_distractors=300,
              noise=0.5, hops=2, weight=0.5, seed=1, neg_mode="none",
              shared_count=None, local_per_input=None, add_leak=True):
    """生成一个任务图。

    N = 背景填充节点目标数(非严格总量);实际总数记入 task['total_nodes']。
    返回 (Graph, task_dict)。
    """
    rng = random.Random(seed)
    g = Graph()
    inputs = [f"I{i}" for i in range(inputs_n)]
    for i in inputs:
        g.add_node(i)

    # ---- joint 汇聚链(hops = 输入→C0 边数,定义见文件头) ----
    pivot = "p"
    if hops >= 2:
        g.add_node(pivot)
    mids = []
    if hops >= 3:
        mids = [f"m{j}" for j in range(inputs_n)]
        for m in mids:
            g.add_node(m)
            g.add_edge(i, m, weight)
            g.add_edge(m, pivot, weight)
    elif hops == 2:
        for i in inputs:
            g.add_edge(i, pivot, weight)
    else:  # hops == 1
        pass
    g.add_node("C0")
    if hops == 1:
        for i in inputs:
            g.add_edge(i, "C0", weight)
    elif hops == 2:
        g.add_edge(pivot, "C0", weight)
    else:
        relay = pivot
        for h in range(hops - 3):
            r = f"j{h}"
            g.add_node(r)
            g.add_edge(relay, r, weight)
            relay = r
        g.add_edge(relay, "C0", weight)

    # ---- singles(固定 2 跳) ----
    singles = [f"C{i}" for i in range(1, 1 + inputs_n)]
    for j, i in enumerate(inputs):
        sm = f"s{j}"
        g.add_node(sm)
        g.add_edge(i, sm, weight)
        g.add_edge(sm, singles[j], weight)

    # ---- local distractors(T2/T4) ----
    locals_map = {}
    n_locals = 0 if local_per_input is None else local_per_input
    for j, i in enumerate(inputs):
        locals_map[i] = [f"L{j}_{k}" for k in range(n_locals)]
        for l in locals_map[i]:
            g.add_node(l)
            g.add_edge(i, l, 0.8)

    # ---- shared distractors(T3/T4) ----
    shared_of = {}
    n_shared = 0 if shared_count is None else shared_count
    for k in range(n_shared):
        s = f"S{k}"
        g.add_node(s)
        chosen = rng.sample(inputs, k=min(len(inputs), 2))
        for i in chosen:
            g.add_edge(i, s, 0.25)
            shared_of.setdefault(i, []).append(s)

    # ---- 候选集合与 distractor 槽位 ----
    shared_ids = _flatten_distinct(x for i in inputs for x in shared_of.get(i, []))
    locals_ids = [l for v in locals_map.values() for l in v]
    dist_ids = [f"D{i}" for i in range(n_distractors)]
    for d in dist_ids:
        g.add_node(d)

    # ---- distractor 连背景(无输入路径) ----
    special = (len(inputs) + (1 if hops >= 2 else 0) + len(mids)
               + (hops - 3 if hops >= 3 else 0) + 1  # C0
               + inputs_n * 2 + len(locals_ids) + len(shared_ids)
               + len(dist_ids) + 1)  # +1 C0 已计
    filler = [f"n{i}" for i in range(max(0, N - special))]
    for f in filler:
        g.add_node(f)
    if filler:
        for d in dist_ids:
            g.add_edge(rng.choice(filler), d, rng.uniform(0.2, 0.9))
    else:
        for d in dist_ids:
            pass  # 孤立 distractor(仍无输入路径)

    # ---- 背景链 + 泄漏边 ----
    if len(filler) > 1:
        for i in range(1, len(filler)):
            g.add_edge(filler[i - 1], filler[i], rng.uniform(0.3, 0.8))
            if rng.random() < 0.25:
                nxt = filler[i + 1] if i + 1 < len(filler) else filler[0]
                pool = [f for f in filler if f != nxt and f != filler[i]]
                if pool:
                    g.add_edge(filler[i], rng.choice(pool), rng.uniform(0.2, 0.9))
    if add_leak:
        for j in range(len(inputs)):
            if filler and rng.random() < 0.7:
                g.add_edge(inputs[j], rng.choice(filler), rng.uniform(0.3, 0.9))

    # ---- 噪声边 ----
    if noise > 0.0:
        all_ids = list(g.nodes)
        edge_total = sum(len(v) for v in g.out.values())
        n_noise = max(1, int(noise * edge_total))
        guard = 0
        tries = 0
        while guard < n_noise and tries < n_noise * 5:
            tries += 1
            a = rng.choice(all_ids)
            b = rng.choice(all_ids)
            if a == b:
                continue
            try:
                g.add_edge(a, b, rng.uniform(0.2, 0.9))
                guard += 1
            except ValueError:
                continue

    # ---- 负边模式(输入 → 部分 distractor 候选;与既有边冲突则跳过) ----
    if neg_mode == "neg":
        for i in inputs:
            for d in dist_ids[:max(1, len(dist_ids) // 4)]:
                try:
                    g.add_edge(i, d, -0.5)
                except ValueError:
                    pass
    elif neg_mode == "mixed":
        half = max(1, len(dist_ids) // 4)
        for i in inputs:
            for k, d in enumerate(dist_ids[:half]):
                try:
                    g.add_edge(i, d, -0.5 if k % 2 == 0 else 0.5)
                except ValueError:
                    pass

    task = {
        "inputs": inputs,
        "joint": "C0",
        "singles": singles,
        "locals": locals_ids,
        "shared": shared_ids,
        "distractors": dist_ids,
        "candidates": ["C0"] + singles + locals_ids + shared_ids + dist_ids,
        "roles": {"C0": "joint"},
        "joint_hops": hops,
        "weight": weight,
        "neg_mode": neg_mode,
        "total_nodes": len(g.nodes),
        "total_edges_fwd": sum(len(v) for v in g.out.values()),
        "spec_nodes_N": N,
        "filler": len(filler),
    }
    return g, task


# ---------------------------------------------------------------- 反事实

def counterfactual(which="C1", seed=1, weight=0.7):
    """C1 星形汇聚 / C2 无联合目标 / C3 组合支持。返回 (g, task)。"""
    g = Graph()
    inputs = [f"I{i}" for i in range(3)]
    for i in inputs:
        g.add_node(i)

    if which == "C1":
        g.add_node("X")
        for i in inputs:
            g.add_edge(i, "X", weight)
        return g, {"inputs": inputs, "joint": "X", "singles": [], "target": "X",
                   "candidates": ["X"],
                   "support": {"X": 3},
                   "total_nodes": 4,
                   "total_edges_fwd": 3}
    if which == "C2":
        for i, nm in zip(inputs, ["X", "Y", "Z"]):
            g.add_node(nm)
            g.add_edge(i, nm, 0.9)
        return g, {"inputs": inputs, "joint": None, "singles": ["X", "Y", "Z"],
                   "target": None, "candidates": ["X", "Y", "Z"],
                   "total_nodes": 6,
                   "total_edges_fwd": 3}
    if which == "C3":
        cands = {"X": ["I0", "I1"], "Y": ["I1", "I2"], "Z": ["I0", "I2"],
                 "W": ["I0", "I1", "I2"]}
        for nm, srcs in cands.items():
            g.add_node(nm)
            for src in srcs:
                g.add_edge(src, nm, weight)
        return g, {"inputs": inputs, "joint": "W", "singles": ["X", "Y", "Z"],
                   "target": "W", "candidates": ["X", "Y", "Z", "W"],
                   "support": {"X": 2, "Y": 2, "Z": 2, "W": 3},
                   "total_nodes": 7,
                   "total_edges_fwd": 9}
    raise ValueError(f"unknown counterfactual {which}")


def combo_task(seed=1):
    """§16 组合关系测试(结构锁定于 manifest combination_test_O3)。"""
    g = Graph()
    for nm in ["I0", "I1", "a", "b", "c", "O1", "O2", "O3"]:
        g.add_node(nm)
    g.add_edge("I0", "a", 0.8)
    g.add_edge("a", "O1", 0.8)
    g.add_edge("I1", "b", 0.8)
    g.add_edge("b", "O2", 0.8)
    g.add_edge("I0", "c", 0.5)
    g.add_edge("I1", "c", 0.5)
    g.add_edge("c", "O3", 0.5)
    return g, {"inputs": ["I0", "I1"], "joint": None,
               "candidates": ["O1", "O2", "O3"],
               "expect_O3_first": True,
               "total_nodes": 8, "total_edges_fwd": 7}


def minimal_toy(seed=1):
    """§28 最小复现图(≤20 nodes): std toy(falsification §20 同款)。"""
    rng = random.Random(seed)
    g = Graph()
    for n in ["I1", "I2", "A", "B", "C", "O1", "O2", "O3"]:
        g.add_node(n)
    for s, d in [("I1", "A"), ("I2", "A"), ("A", "O1"),
                 ("I1", "B"), ("B", "O2"), ("I2", "C"), ("C", "O3")]:
        g.add_edge(s, d, round(rng.uniform(0.4, 0.8), 3))
    return g, {"inputs": ["I1", "I2"], "joint": "O1",
               "singles": ["O2", "O3"], "candidates": ["O1", "O2", "O3"],
               "expect": [["O1"], ["O2", "O3"]],
               "total_nodes": 8, "total_edges_fwd": 7}