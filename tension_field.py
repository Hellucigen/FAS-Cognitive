# tension_field.py — Tension Field: 认知张力的持续场
# ============================================================================
# Drive 重构 Phase 1（2026-09-20）：Tension → Drive → Network → Modulation。
#
# Tension = 系统里"某件事没有完成/没有解释/没有满足/未知/意外"而持续
# 占据认知资源的内部状态。设计原则：
#   1. 张力读数全部来自既有系统（provider 闭包 / 图谱采样）——不建平行状态库
#   2. 每种张力是连续量 level ∈ [0,1]，有非对称动力学（上升快、消退慢 =
#      张力天然持续；消退后才让位于新张力）
#   3. 张力的种类是注册表数据（config 可增删），不是枚举分支
#   4. 本模块只做动力学，不知道任何 Drive——聚合是 DriveField 的事
#
# 一个张力由若干 component 组成（多读数合成一个张力）：
#   intensity = clamp( Σ component.coef × min(1, raw × component.scale) )
# component.source 是一个已注册的采样器名字（provider 闭包或图采样闭包），
# 返回原始读数（次数/分钟/水位…），scale 负责饱和归一。
# ============================================================================

import json
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# 采样器缺位/抛异常时的安全读数（缺信号 = 该分量按 0 参与，不猜不造）
_MISSING = 0.0


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))


class TensionField:
    """张力场：注册表驱动的持续张力状态。

    specs 形如（config["tension_sources"]，缺失键用内置默认）::

        {
          "prediction_error": {
              "components": [{"source": "prediction_surprise",
                               "coef": 1.0, "scale": 1.0}],
              "rise": 0.55, "fall": 0.10, "enabled": true
          }, ...
        }

    rise/fall 是每次 step 的趋近系数（非对称：rise > fall → 张力持续）。
    settle=True 时直接跳到 target（回合内强制评估：读到的就是当下的），
    常规 step（CC 周期拍）走动力学 → 提供时间连续性/迟滞。
    """

    def __init__(self, specs: dict, samplers: dict = None, defaults: dict = None):
        self._specs = {}
        base = dict(defaults or {})
        for name, spec in (specs or {}).items():
            merged = dict(base.get(name, {}))
            merged.update(spec or {})
            self._specs[name] = merged
        for name, spec in base.items():
            self._specs.setdefault(name, dict(spec))
        self._samplers = dict(samplers or {})
        self._lock = threading.RLock()
        self._levels = {n: 0.0 for n in self._specs}
        self._targets = {n: 0.0 for n in self._specs}
        self._raw = {n: {} for n in self._specs}   # 最近一次原始读数（可解释性）
        self._last_step = time.time()

    # ── 采样器注册 ────────────────────────────────────────

    def register_sampler(self, name: str, fn):
        """注册一个读数采样器（provider 闭包或图采样闭包），可返回 float
        或 (value, note)。异常/缺位按 0 参与。"""
        self._samplers[name] = fn

    def _read(self, source):
        if not source:
            return _MISSING, None
        fn = self._samplers.get(source)
        if fn is None:
            return _MISSING, None
        try:
            v = fn()
        except Exception:
            return _MISSING, None
        if isinstance(v, tuple):
            try:
                return float(v[0]), v[1]
            except (TypeError, ValueError):
                return _MISSING, None
        try:
            return float(v), None
        except (TypeError, ValueError):
            return _MISSING, None

    # ── 动力学 ────────────────────────────────────────────

    def target_of(self, name: str) -> float:
        """当前采样合成的张力目标强度（不含持续性）。"""
        spec = self._specs.get(name) or {}
        total = 0.0
        raws = {}
        for comp in spec.get("components") or []:
            raw, note = self._read(comp.get("source"))
            coef = float(comp.get("coef", 1.0))
            scale = float(comp.get("scale", 1.0))
            sat = float(comp.get("saturation", 1.0))
            contrib = coef * min(sat, max(0.0, raw * scale))
            total += contrib
            if raw or note:
                raws[comp.get("source")] = {"raw": raw, "note": note,
                                            "contrib": round(contrib, 4)}
        self._raw[name] = raws
        return _clamp01(total)

    def step(self, settle: bool = False, rate_gain_rise: float = 1.0,
             rate_gain_fall: float = 1.0) -> dict:
        """推进一次全场动力学。返回 {name: level}。

        rate_gain_*：激素/网络对上升与消退速度的调制（>1 更快，<1 更黏），
        由上层 CognitiveField 每拍注入——激素是调制器，不是第二套张力。
        """
        with self._lock:
            now = time.time()
            for name in self._specs:
                spec = self._specs[name]
                if spec.get("enabled") is False:
                    self._levels[name] = 0.0
                    continue
                target = self.target_of(name)
                self._targets[name] = target
                level = self._levels[name]
                if settle:
                    level = target
                else:
                    rise = float(spec.get("rise", 0.5)) * max(0.0, rate_gain_rise)
                    fall = float(spec.get("fall", 0.08)) * max(0.0, rate_gain_fall)
                    if target > level:
                        level += min(1.0, rise) * (target - level)
                    else:
                        level += min(1.0, fall) * (target - level)
                self._levels[name] = _clamp01(level)
            self._last_step = now
            return dict(self._levels)

    def decay_offline(self, seconds: float, max_steps: int = 20):
        """重启补偿：按消退动力学虚拟推进离线时长（封顶步数防长离线清零一切）。"""
        if seconds <= 0:
            return
        steps = int(min(max_steps, seconds / 60.0))   # 每虚拟步 ≈ 一分钟
        for _ in range(steps):
            self.step()

    # ── 读取与持久化 ──────────────────────────────────────

    def levels(self) -> dict:
        with self._lock:
            return dict(self._levels)

    def intensity(self, name: str) -> float:
        with self._lock:
            return float(self._levels.get(name, 0.0))

    def raws(self) -> dict:
        with self._lock:
            return {k: dict(v) for k, v in self._raw.items()}

    def snapshot(self) -> dict:
        with self._lock:
            return {
                name: {"level": round(self._levels[name], 4),
                       "target": round(self._targets.get(name, 0.0), 4),
                       "raw": self._raw.get(name, {})}
                for name in self._specs
            }

    def get_state(self) -> dict:
        with self._lock:
            return {"levels": dict(self._levels),
                    "saved_at": time.time()}

    def load_state(self, state: dict):
        if not state:
            return
        saved = state.get("levels") or {}
        with self._lock:
            for name in self._specs:
                v = saved.get(name)
                if isinstance(v, (int, float)):
                    self._levels[name] = _clamp01(float(v))
        offline = time.time() - float(state.get("saved_at") or time.time())
        if offline > 60:
            self.decay_offline(offline)
