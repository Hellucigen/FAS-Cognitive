# drive_field.py — Drive Field: 张力的持续性聚合场
# ============================================================================
# Drive 重构 Phase 1（2026-09-20）。
#
# 新定义：Drive = 对多个 Tension 的持续性聚合、调制和保持。
#   target = clamp( Σ affinity[tension] × tension_level × gain[tension] )
#            × (1 − Σ lateral[d] × other_level)      ← 软竞争，非 winner-take-all
#   level 以非对称速率趋近 target（上升快、消退慢 → 驱动力有惯性），
#   rise/fall 速率本身被激素调制（多巴胺 → 增长更快/消退更慢）。
#
#   * 多个 Drive 同时存在、并存、可叠加可互抑——不存在"取唯一 dominant 作
#     控制中枢"的语义（dominant 只是兼容派生字段，在 facade 层）。
#   * Drive 不执行行为：它的唯一输出是把 level 换算成图谱节点激活
#     （写回由 CognitiveField/DriveEvaluator 完成，参与扩散与行为竞争）。
#   * 亲和度矩阵是数据（config["drive_field"]），新增 Drive/新增张力
#     只改数据与注册，不改本模块。
# ============================================================================

import logging
import threading

logger = logging.getLogger(__name__)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))


class DriveField:
    def __init__(self, specs: dict):
        """specs 形如（config["drive_field"]["drives"]）::

            "curiosity": {
                "node": "CuriosityDrive",
                "description": "...",
                "affinities": {"discrepancy": 0.34, "novelty": 0.18,
                                "satiation": -0.35, ...},
                "rise": 0.5, "fall": 0.08,
            }
        affinities 可为负（抑制项，如满足感压低探索）。
        """
        self._specs = {k: dict(v or {}) for k, v in (specs or {}).items()}
        self._lateral = (self._specs.pop("_lateral", None)
                         or {})   # {"curiosity": {"social": 0.1, ...}}
        self._lock = threading.RLock()
        self._levels = {n: 0.0 for n in self._specs}
        self._targets = {n: 0.0 for n in self._specs}
        self._contribs = {n: {} for n in self._specs}

    # ── 动力学 ────────────────────────────────────────────

    def target_of(self, name: str, tensions: dict,
                  affinity_gain: dict = None, bias: float = 0.0) -> float:
        spec = self._specs.get(name) or {}
        aff = spec.get("affinities") or {}
        gains = affinity_gain or {}
        total = 0.0
        contribs = {}
        for tname, coef in aff.items():
            tl = float(tensions.get(tname, 0.0) or 0.0)
            if tl <= 0.001:
                continue
            # 键 "<drive>.<tension>"（与调用方一致）；只给 "<drive>" 时整驱共享
            g = float(gains.get(f"{name}.{tname}",
                                gains.get(name, 0.0)) or 0.0)
            v = coef * tl * (1.0 + g)
            total += v
            contribs[tname] = round(v, 4)
        # R2 P9：调制器→驱动的**连续偏置**（图上的 `调制器 -[w]-> <DriveNode>` 边，
        # Σ w×dev(调制器)）。加在亲和度求和之后、钳位之前 ⇒ 移动的是稳态，
        # 且出不了 [0,1]。与 affinity_gain 的区别：偏置不需要张力在场
        # （"奖赏水位高→坐不住"），增益必须有张力可乘。
        raw = _clamp01(total + float(bias or 0.0))
        # 侧向软抑制：其他驱动的存续只压缩目标的上限，不清零——
        # 允许"想探索"和"想完成目标"同时高。
        for other, k in (self._lateral.get(name) or {}).items():
            raw *= max(0.0, 1.0 - float(k) * self._levels.get(other, 0.0))
        self._contribs[name] = contribs
        return raw

    def step(self, tensions: dict, settle: bool = False,
             rate_gains: dict = None, affinity_gain: dict = None,
             bias: dict = None) -> dict:
        """推进一次驱动场。

        rate_gains: {"<drive>.rise": ×, "<drive>.fall": ×} 全局或按驱速率
                    调制（激素→Drive 持久性的落点）。
        affinity_gain: {"<drive>.<tension>": +g} 增益（如催产素抬社交显著性）。
        bias: {"<drive>": ±x} 稳态偏置（R2 P9，来自图上的调制器→驱动边）。
        """
        rg = rate_gains or {}
        bs = bias or {}
        with self._lock:
            for name in self._specs:
                spec = self._specs[name]
                if spec.get("enabled") is False:
                    self._levels[name] = 0.0
                    continue
                target = self.target_of(name, tensions, affinity_gain,
                                        bias=float(bs.get(name, 0.0) or 0.0))
                self._targets[name] = target
                level = self._levels[name]
                if settle:
                    level = target
                else:
                    rise = float(spec.get("rise", 0.5)) * max(0.0, float(
                        rg.get(f"{name}.rise", rg.get("*", 1.0)) or 1.0))
                    fall = float(spec.get("fall", 0.08)) * max(0.0, float(
                        rg.get(f"{name}.fall", rg.get("*", 1.0)) or 1.0))
                    if target > level:
                        level += min(1.0, rise) * (target - level)
                    else:
                        level += min(1.0, fall) * (target - level)
                self._levels[name] = _clamp01(level)
            return dict(self._levels)

    # ── 读取 ──────────────────────────────────────────────

    def levels(self) -> dict:
        with self._lock:
            return dict(self._levels)

    def level(self, name: str) -> float:
        with self._lock:
            return float(self._levels.get(name, 0.0))

    def activation(self, name: str) -> float:
        """图谱尺度激活 [0,5]（与旧 DriveEvaluator 写回值域一致）。"""
        return round(self.level(name) * 5.0, 4)

    def node_id(self, name: str):
        return (self._specs.get(name) or {}).get("node")

    def spec_names(self):
        return list(self._specs.keys())

    def snapshot(self) -> dict:
        with self._lock:
            return {
                name: {
                    "level": round(self._levels[name], 4),
                    "activation": round(self._levels[name] * 5.0, 4),
                    "target": round(self._targets.get(name, 0.0), 4),
                    "contributions": dict(self._contribs.get(name, {})),
                    "description": (self._specs[name] or {}).get(
                        "description", ""),
                    "node": self._specs[name].get("node"),
                }
                for name in self._specs
            }

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
