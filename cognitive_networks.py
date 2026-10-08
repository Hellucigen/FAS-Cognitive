# cognitive_networks.py — Cognitive Network State: 认知网络调制层的"场"
# ============================================================================
# Drive 重构 Phase 2（2026-09-20）。
#
# 回答的问题：Demand 回答"当前需要多少认知资源"，本层回答
# "这些认知资源应以什么方式组织"。三者解耦：Demand ≠ Mode ≠ Network。
#
# Network = 注册表数据（NetworkSpec），第一版两条：
#   CEN — 专注处理一个外部/内部任务的组织态（注意力窄、连续性好、深度优先）
#   DMN — 无强制外部任务时转向内部世界的组织态（联想广、检索强、发散优先）
# 未来 Salience / Social Cognition / Memory / Threat 网络只是新增 spec，
# 不改本模块——不存在 `if mode == "cen"` 的分支结构。
#
# 关键性质（都是连续量动力学，不是模式开关）：
#   1. 目标由张力/驱动/任务语境的加权和决定（weights 在 config，无分类器）
#   2. 惯性：rise/fall 非对称 + slew-rate 限幅 → 不会 0.51/0.49 每秒翻转
#   3. 软竞争：level_i 的目标被其他网络按 κ 乘性压制，但允许同时高
#   4. 输出 CognitiveNetworkState：各网络连续 level，交给 ModulationLayer
# ============================================================================

import logging
import threading

logger = logging.getLogger(__name__)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))


class NetworkField:
    """注册表式网络激活场。

    specs 形如（config["networks"]["field"]）::

        "CEN": {
            "inputs": {"tension.unfinished_goal": 0.22,
                        "tension.blocked_action": 0.14,
                        "drive.curiosity": -0.05,
                        "context.task_engaged": 0.40,
                        "context.time_since_task": -0.25,
                        "base": 0.08},
            "rise": 0.30, "fall": 0.10, "slew": 0.12,
        }
        "_lateral": {"CEN": {"DMN": 0.25}, "DMN": {"CEN": 0.20}}

    inputs 前缀约定（`_resolve` 真正认识的四种）：
        tension.<name>   来自 TensionField levels
        drive.<name>     来自 DriveField levels
        context.<name>   step(context=…) 传入的即时读数（0~1）
        base             常数底（恒为 1）
    权重可为负（该项抬升另一网络 / 压低本网络）。缺位的读数按 0 参与。

    调制器**不走 inputs**：它经 `step(bias=…)` 进（图上的 `调制器→网络节点` 边，
    R2 P9），需求/激素则经调制层影响参数。spec 里的 `node` 键不是加数，
    是该网络在图上的落点名（偏置边按它认领）。
    """

    def __init__(self, specs: dict, defaults: dict = None):
        merged = {k: dict(v) for k, v in (defaults or {}).items()}
        for k, v in (specs or {}).items():
            m = dict(merged.get(k, {}))
            m.update(v or {})
            merged[k] = m
        self._lateral = merged.pop("_lateral", {}) or {}
        self._specs = merged
        self._lock = threading.RLock()
        self._levels = {n: 0.0 for n in self._specs}
        self._targets = {n: 0.0 for n in self._specs}

    # ── 目标合成（多因素加权和，无 if/else 分类）──────────

    def _resolve(self, key: str, tensions: dict, drives: dict,
                 context: dict) -> float:
        kind, _, name = key.partition(".")
        if kind == "tension":
            return float(tensions.get(name, 0.0) or 0.0)
        if kind == "drive":
            return float(drives.get(name, 0.0) or 0.0)
        if kind == "base":
            return 1.0
        return float((context or {}).get(name, 0.0) or 0.0)

    def target_of(self, name: str, tensions: dict, drives: dict,
                  context: dict, bias: float = 0.0) -> float:
        spec = self._specs.get(name) or {}
        total = 0.0
        for key, w in (spec.get("inputs") or {}).items():
            total += float(w) * self._resolve(key, tensions, drives, context)
        # R2 P9：调制器→网络的**连续偏置**（来自图上的调制边，见
        # modulator_subgraph.graph_biases）。加在求和之后、钳位之前 ⇒
        # 它移动的是稳态水位，不是速度；且永远出不了 [0,1]（不是模式开关）。
        total += float(bias or 0.0)
        # 软互抑：压制的是目标，不是既有 level → 双高态可以达成
        for other, k in (self._lateral.get(name) or {}).items():
            total *= max(0.0, 1.0 - float(k) * self._levels.get(other, 0.0))
        return _clamp01(total)

    # ── 动力学：惯性 + slew-rate 限幅 ─────────────────────

    def step(self, tensions: dict, drives: dict, context: dict = None,
             settle: bool = False, rate_gains: dict = None,
             bias: dict = None) -> dict:
        """推进一次网络场。settle 仅供回合内强制读数；周期拍走动力学。

        rate_gains: {"CEN.rise": ×, "CEN.fall": ×, "*": ×} —— 激素对网络
        **惯性**的调制落点。注意它只改过渡速度：`target` 不变 ⇒ 稳态不变，
        所以"想让某个网络长期更高"必须走 `bias`，不能走这里。

        bias: {"CEN": +0.06, "DMN": -0.03} —— R2 P9 的调制器→网络连续偏置
        （来自图上的 `调制器 -[w]-> 网络节点` 边，Σ w×dev(调制器)）。
        加进 target 合成，因此是**稳态位移**；出不了 [0,1]，也因此
        不存在"低/高两档"——没有二值模式切换的落点。
        """
        rg = rate_gains or {}
        bs = bias or {}
        ctx = context or {}
        with self._lock:
            for name, spec in self._specs.items():
                target = self.target_of(name, tensions, drives, ctx,
                                        bias=float(bs.get(name, 0.0) or 0.0))
                self._targets[name] = target
                level = self._levels[name]
                if settle:
                    level = target
                else:
                    rise = float(spec.get("rise", 0.25)) * max(0.0, float(
                        rg.get(f"{name}.rise", rg.get("*", 1.0)) or 1.0))
                    fall = float(spec.get("fall", 0.12)) * max(0.0, float(
                        rg.get(f"{name}.fall", rg.get("*", 1.0)) or 1.0))
                    delta = target - level
                    rate = rise if delta > 0 else fall
                    delta *= min(1.0, rate)
                    slew = float(spec.get("slew", 0.15))
                    delta = max(-slew, min(slew, delta))
                    level = _clamp01(level + delta)
                self._levels[name] = level
            return dict(self._levels)

    # ── 读取 ──────────────────────────────────────────────

    def levels(self) -> dict:
        with self._lock:
            return dict(self._levels)

    def level(self, name: str) -> float:
        with self._lock:
            return float(self._levels.get(name, 0.0))

    def targets(self) -> dict:
        with self._lock:
            return dict(self._targets)

    def node_ids(self) -> dict:
        """图节点 id → 网络名（R2 P9：偏置边在图上写的是 `CENetwork`，
        落到这里要换算成字段名 `CEN`）。没有 `node` 键的网络不参与。"""
        return {str(s.get("node")): n for n, s in self._specs.items()
                if s.get("node")}

    def get_state(self) -> dict:
        with self._lock:
            return {"levels": dict(self._levels)}

    def load_state(self, state: dict):
        if not state:
            return
        with self._lock:
            for name, v in (state.get("levels") or {}).items():
                if name in self._levels and isinstance(v, (int, float)):
                    self._levels[name] = _clamp01(float(v))
