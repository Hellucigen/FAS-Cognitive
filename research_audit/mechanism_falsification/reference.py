# reference.py — 核心机制最小参考模型(独立实现，不 import 任何 production 代码)
# ============================================================================
# 用途:mechanism falsification(任务 §19-27)。在受控、确定性的环境里检验:
#   "多输入同时激活 → 与多输入共同相关的候选更强激活" 这一核心假设。
#
# 对齐声明:本模型从 production diffusion_engine.py 的工程语义提炼
# (spreading activation:归一化脉冲 + 发射预算 + 发射即转移 + fire-once +
# 步间衰减 + cap 钳制 + 方向语义),但**不调用 production 任何模块**,
# 以便在绝对受控的图/参数/条件下做 falsification。
# 提炼的语义逐条记录在文档 EXPERIMENT_ALIGNMENT(见 README.md)。
#
# 本文件只依赖标准库。禁止依赖 numpy/pandas(可运行环境未必有)。
# ============================================================================

import math
import random

# ---------------------------------------------------------------- 图结构

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
    """邻接表图。id 必须是 str。"""
    def __init__(self):
        self.nodes = {}          # id -> GNode
        self.out = {}            # id -> [GEdge]  (src 视角出边)
        self.inn = {}            # id -> [GEdge]  (dst 视角入边)
        self._fired = set()

    def add_node(self, ident):
        ident = str(ident)
        if ident not in self.nodes:
            self.nodes[ident] = GNode(ident)

    def add_edge(self, src, dst, weight=0.5, bidirectional=False):
        src, dst = str(src), str(dst)
        self.add_node(src); self.add_node(dst)
        e = GEdge(src, dst, weight, bidirectional)
        self.out.setdefault(src, []).append(e)
        self.inn.setdefault(dst, []).append(e)

    def edges_of(self, nid):
        """nid 的全部可传播边:(edge, direction_forward)"""
        out = []
        for e in self.out.get(nid, ()):
            out.append((e, True))
        for e in self.inn.get(nid, ()):
            if e.bidirectional:
                out.append((e, False))
        return out

    # ------------------------------------------------------------ 扩散核心

    def _emit(self, nid, delta):
        """单节点的一次发射(pulse)。delta: dict 累积目标。返回 emitted_sum。"""
        node = self.nodes[nid]
        a = node.activation
        if a <= 0.0:
            return 0.0
        pairs = self.edges_of(nid)
        if not pairs:
            return 0.0
        emits = []          # (dst, contribution, reverse)
        total_w = 0.0
        total_abs_neg = 0.0
        for e, fwd in pairs:
            w = e.weight
            if fwd:
                if w > 0: total_w += w
                elif w < 0: total_abs_neg += -w
            else:
                if w > 0: total_w += w
                elif w < 0: total_abs_neg += -w
        denom = total_w if total_w > 0 else total_abs_neg
        if denom <= 0.0:
            return 0.0
        for e, fwd in pairs:
            w = e.weight
            contrib = a * w * self.beta * self.gain / denom
            if not fwd:
                contrib *= 0.5          # 回溯入边强度为正向一半(生产语义)
            emits.append((e.dst if fwd else e.src, contrib, fwd))
        # 发射预算钳制(只钳正贡献总量,与生产一致)
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
                      decay=0.05, cap=5.0, clear_after=True):
        """一轮扩散:注入 seeds → steps×(发射+衰减) → 尾衰减。

        与生产语义对齐:
          - seeds 作为本轮的 activation source,发射免扣(transfer=0)
          - fire-once:每节点每轮至多向外发射一次
          - 步间衰减(EPS 吸附取 1e-4,与生产 activation_epsilon 同阶)
          - 负权重边:归一化脉冲内的抑制贡献
        返回 (final_activations_dict, round_ledger)
        """
        self.beta = beta; self.gain = gain; self.ratio = ratio
        self.transfer = transfer; self.decay = decay; self.cap = cap
        # 注入 seeds
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
        return {nid: round(n.activation, 9)
                for nid, n in self.nodes.items() if n.activation > 1e-9}, None

    def _step(self, sources, max_delta_watch=False):
        epsilon = 1e-4
        delta = {}
        for nid in list(self.nodes.keys()):
            n = self.nodes[nid]
            if n.activation < epsilon:
                continue
            if nid in self._fired:
                continue
            emitted = self._emit(nid, delta)
            if emitted > 0.0:
                # 发射即转移(来源免扣)
                if nid not in sources:
                    delta[nid] = delta.get(nid, 0.0) - emitted * self.transfer
            self._fired.add(nid)
        for dst, d in delta.items():
            n = self.nodes.get(dst)
            if n is None:
                continue
            newv = max(0.0, min(self.cap, n.activation + d))
            n.activation = newv

    def _decay(self):
        eps = 1e-4
        for n in self.nodes.values():
            n.activation *= (1.0 - self.decay)
            if n.activation < eps:
                n.activation = 0.0

    # ------------------------------------------------------------ 指标工具

    def activations(self):
        return {nid: n.activation for nid, n in self.nodes.items()}

    def rank_of(self, nid):
        acts = self.activations()
        if nid not in acts:
            return None
        order = sorted(acts.items(), key=lambda kv: -kv[1])
        for i, (k, _) in enumerate(order):
            if k == nid:
                return i
        return None

# ---------------------------------------------------------------- 候选/输入构造

def build_toy_task(kind="std", seed=1):
    """确定性 toy 图构造(任务 §20 要求,自动生成 expected outcome)。

    kind="std"  (§20 原例):
        I1 ─ A ─ O1     I2 ─ A ─ O1
        I1 ─ B ─ O2     I2 ─ C ─ O3
    输入 I1+I2 → 期望 O1 > O2 且 O1 > O3
    """
    rng = random.Random(seed)
    g = Graph()
    nodes = ["I1", "I2", "A", "B", "C", "O1", "O2", "O3"]
    for n in nodes:
        g.add_node(n)
    edges = [("I1", "A"), ("I2", "A"), ("A", "O1"),
             ("I1", "B"), ("B", "O2"), ("I2", "C"), ("C", "O3")]
    for s, d in edges:
        g.add_edge(s, d, weight=round(rng.uniform(0.4, 0.8), 3))
    return g, ["I1", "I2"], {"O1": 1, "O2": 2, "O3": 3}   # 期望排序:O1 第 1