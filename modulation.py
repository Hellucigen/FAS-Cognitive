# modulation.py — Modulation Layer: Network/Tension/State → 系统参数
# ============================================================================
# Drive 重构 Phase 3（2026-09-20）。
#
# 统一机制：effective_parameter
#     = clamp( baseline × Π factor_contributors, min, max )
#   再做时间平滑（EMA display 值）→ 消费者每拍读到的参数是连续演化的，
#   不会出现 0.51/0.49 抖跳。
#
# 参数消费者（diffusion.* / retrieval.* / llm.* / behavior.*）只调
# `mod.get(name)`；本层不知道任何消费者是谁——新增参数 = config 加一行。
#
# contributor 两族，全是数据：
#   1) effects 表（config 内联）：param.effects = {signal_key: coef}，
#      factor_net = 1 + Σ coef × signal_value（信号来自 CognitiveField
#      每拍组装的 signals 字典：network.CEN / tension.novelty / drive.curiosity /
#      hormone.dopamine(基线偏移) / context.* / constant）
#   2) 代码注册 contributor（fn(signals)->乘子），给需要非线性的少数场合
#      （如情绪唤醒→param_gain 这类既有通路收编）。
#      ⚠️ R2 P8 复核过：**当前生产零注册者是结论，不是缺口**。倒 U/过载的曲线
#      已经在调制器侧施加一次（`internal_state.modulator_dev` → `receptor_response`），
#      所以下游 coef 乘的本来就是响应量；在这里再挂一条曲线会把同一段过载算两遍。
#      实测（审计附八）：12 个调制器全部同时钉在自己的响应峰值时，任何一个参数上
#      的激素合力最大只有 0.50（action.score_threshold），远不足以把它推到量程端点
#      ⇒ 也不需要在这一层加"合力护栏"。hook 保留：将来出现"表表达不出来的形状"时用它。
# 没有任何 if 模式分支——参数效应是表，查表即乘子。
# ============================================================================

import logging
import threading
import time

logger = logging.getLogger(__name__)

_EPS = 1e-9


class ModulationLayer:
    def __init__(self, params_spec: dict, smoothing_alpha: float = 0.25):
        """params_spec 形如（config["modulation"]["params"]）::

            "diffusion.max_depth": {
                "baseline": 3.0, "min": 1.0, "max": 6.0,
                "effects": {"network.CEN": -0.5, "network.DMN": 0.8,
                             "constant": 0.0}
            }
        effects 语义：乘子 = 1 + Σ coef×signal，逐参数累乘后再限幅。

        注意 specs 的**深一层隔离**：`dict(v)` 只抄顶层，effects 子表若共享，
        投影器（R2 P6 把图上系数写进 effects）就会就地污染
        `config.DEFAULT_CONFIG`——后建的层会读到前一个实例写进去的系数，
        表现为"配置里的出厂值变了"。所以 effects 单独 copy 一份。
        """
        self._specs = {}
        for k, v in (params_spec or {}).items():
            spec = dict(v or {})
            if "effects" in spec:
                spec["effects"] = dict(spec["effects"] or {})
            self._specs[k] = spec
        self._contributors = {}   # param -> [fn(signals)->mult]
        self._alpha = float(smoothing_alpha)
        self._lock = threading.RLock()
        self._display = {k: float((v or {}).get("baseline", 0.0))
                         for k, v in self._specs.items()}
        self._raw = dict(self._display)
        self._factors = {k: {} for k in self._specs}
        self._last = time.time()

    # ── 注册 ──────────────────────────────────────────────

    def define(self, name: str, spec: dict):
        """运行期补定义一个参数（装配层用）。"""
        with self._lock:
            self._specs[name] = dict(spec or {})
            self._display.setdefault(name, float(spec.get("baseline", 0.0)))
            self._raw.setdefault(name, self._display[name])
            self._factors.setdefault(name, {})

    def register(self, param: str, fn):
        with self._lock:
            self._contributors.setdefault(param, []).append(fn)

    def has(self, name: str) -> bool:
        return name in self._specs

    # ── 投影写入面（R2 P6：图上系数 → effects 行）────────────
    #
    # 为什么开这三个口子：调制系数的真源改成了图谱边（禁令 2），但本层不知道
    # 图的存在——投影器把边编译成 `(signal_key → coef)` 再交进来。effects 表
    # 本来就是数据，写它不是加机制。仍不在此做任何分支判断。

    def effects(self, param: str) -> dict:
        """该参数当前的 effects 行（拷贝，改它不影响层内）。"""
        with self._lock:
            spec = self._specs.get(param) or {}
            return dict(spec.get("effects") or {})

    def set_effect(self, param: str, signal_key: str, coef: float):
        """写/覆盖一行 effect（coef 为乘性斜率，符号表示方向）。"""
        with self._lock:
            spec = self._specs.get(param)
            if spec is None:
                return False
            eff = spec.setdefault("effects", {})
            eff[signal_key] = round(float(coef), 6)
            return True

    def drop_effect(self, param: str, signal_key: str) -> bool:
        """删一行 effect（图上边被删时，投影器用它回收系数）。"""
        with self._lock:
            spec = self._specs.get(param) or {}
            eff = spec.get("effects") or {}
            if signal_key in eff:
                del eff[signal_key]
                return True
            return False

    def param_defined(self, param: str) -> bool:
        return param in self._specs

    # ── 每拍计算 ──────────────────────────────────────────

    def compute(self, signals: dict):
        """signals: {"network.CEN": .68, "tension.novelty": .71,
                     "hormone.dopamine": +0.05(基线偏移), ...}
        缺位信号按 0 参与。"""
        sig = signals or {}
        with self._lock:
            for name, spec in self._specs.items():
                factors = {}
                mult = 1.0
                eff = spec.get("effects") or {}
                if eff:
                    s = 0.0
                    for key, coef in eff.items():
                        if key == "constant":
                            s += float(coef)
                        else:
                            s += float(coef) * float(sig.get(key, 0.0) or 0.0)
                    f = max(0.0, 1.0 + s)
                    factors["effects"] = round(f, 4)
                    mult *= f
                for fn in self._contributors.get(name, []):
                    try:
                        f = float(fn(sig) or 1.0)
                    except Exception:
                        f = 1.0
                    factors[getattr(fn, "__name__", "fn")] = round(f, 4)
                    mult *= max(0.0, f)
                raw = float(spec.get("baseline", 0.0)) * mult
                raw = min(float(spec.get("max", raw)), max(
                    float(spec.get("min", raw)), raw))
                self._raw[name] = raw
                self._factors[name] = factors
                alpha = float(spec.get("alpha", self._alpha))
                # alpha≥1 直通（消费者要求即时响应的参数）
                if alpha >= 1.0 - _EPS:
                    self._display[name] = raw
                else:
                    self._display[name] += alpha * (raw - self._display[name])
            self._last = time.time()

    # ── 消费端读点 ────────────────────────────────────────

    def get(self, name: str, default=None):
        with self._lock:
            v = self._display.get(name)
        if v is None:
            if default is not None:
                return default
            raise KeyError(f"modulation param not defined: {name}")
        return v

    def get_int(self, name: str, default=None) -> int:
        return int(round(self.get(name, default)))

    def snapshot(self) -> dict:
        with self._lock:
            return {
                name: {"value": round(self._display[name], 4),
                       "target": round(self._raw[name], 4),
                       "baseline": self._specs[name].get("baseline"),
                       "factors": dict(self._factors.get(name, {}))}
                for name in self._specs
            }

    def get_state(self) -> dict:
        with self._lock:
            return {"display": dict(self._display)}

    def load_state(self, state: dict):
        if not state:
            return
        with self._lock:
            for name, v in (state.get("display") or {}).items():
                if name in self._display and isinstance(v, (int, float)):
                    self._display[name] = float(v)
