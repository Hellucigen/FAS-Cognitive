# internal_state.py — 内部状态层（Phase 1）
# ============================================================================
# 需求 / 神经调制变量 / 性格 的**唯一可写状态源**。
#
# 三层分工（设计文档 INTERNAL_MOTIVATION_DESIGN.md §B）：
#   ① 图谱层：只承载"可解释状态节点"（每个调制变量/需求/性格各一个镜像节点，
#      in-place 更新 —— 绝不按轮次建节点）
#   ② 运行时状态层（本模块）：数值真源，落盘 data/internal_state.json（原子写）
#   ③ 控制器层：每轮/每 tick 确定性更新（本阶段先只做状态容器与周期生命周期，
#      需求/奖励/激素/参数的更新逻辑在 Phase 2–5 接入同一入口）
#
# 不变量（实现与测试共同保证）：
#   I1 运行时状态只有本模块一个写入口；其它模块只读（state()/snapshot()）
#   I2 每轮/每 tick 生成 cycle_id，所有状态写入都带它
#   I3 数值写入一律经 apply_delta/set_value：先裁剪到范围，再记历史与来源
#
# 命名与抽象声明：多巴胺样 / 皮质醇样 / 血清素样 / 催产素样是**计算模型中的功能
# 抽象**，用于奖励预测、探索-利用权衡、风险与社交价值等过程，**不声称精确复现
# 人体内分泌系统**。多巴胺额外拆 tonic（慢变量）/phasic（RPE 脉冲）两个量，
# 正是为了避免"开心=多巴胺高"这种等号式建模。
# ============================================================================

import logging
import threading
import time
from collections import deque

from graph_model import Node, now_str
from json_store import load_json, atomic_write_json

logger = logging.getLogger(__name__)

STATE_PATH = "data/internal_state.json"
HISTORY_LIMIT = 200       # 每个变量的变化历史（环）
LEDGER_LIMIT = 200        # 奖励台账（环）
CYCLE_LIMIT = 200         # 轮次记录（环）
REWARD_LIMIT = 200

# ── 调制变量（功能抽象）基准值与范围 ─────────────────────────
# **生产环境的唯一真源是 config["modulator_system"]["specs"]**（R2，2026-09-21）。
# 这里保留的 4 条只是兜底：config 缺该段（离线测试传 config={}、旧启动路径）时
# 行为与 R2 之前逐字一致。新增字段一律有默认值，旧 spec 不写也能跑。
# decay_per_min：向 baseline 回归的速率（每分钟比例）
MODULATOR_SPEC = {
    "dopamine": {"level": 0.50, "baseline": 0.50, "min": 0.0, "max": 1.0,
                 "decay_per_min": 0.02, "split_tonic": True,
                 "desc": "奖励与学习调制（tonic=总体回报预期；phasic=奖励预测误差脉冲）"},
    "cortisol": {"level": 0.30, "baseline": 0.30, "min": 0.0, "max": 1.0,
                 "decay_per_min": 0.05,
                 "desc": "压力与威胁调制（提高威胁优先级、下调风险容忍）"},
    "serotonin": {"level": 0.55, "baseline": 0.55, "min": 0.0, "max": 1.0,
                  "decay_per_min": 0.01,
                  "desc": "稳定性与抑制调制（冲动抑制、情绪恢复、长期坚持）"},
    "oxytocin": {"level": 0.40, "baseline": 0.40, "min": 0.0, "max": 1.0,
                 "decay_per_min": 0.03,
                 "desc": "社交联结调制（社交价值、同伴聚焦）"},
}

# 每个字段都必须有真实消费者 —— 消费者清单见
# docs/neuromodulation_R2_audit.md §5。默认值刻意取"不改变现有行为"的那一组：
# rise=None → 不夹上升速率；saturation=1.0 → 不做软饱和；refractory=0 → 不应期关闭。
#
# 存储语义（R2 P3 起，务必按这个读代码）：
#   item["tonic"]  = **张力性浓度**：唯一真值。事件写入、衰减回归、上升夹速都作用在它
#                    身上（拆不拆 phasic 的调制器都是它，不再分两套数）。
#   item["phasic"] = **位相脉冲**：只有 split_tonic 的调制器有（其余为 None）。
#   item["level"]  = **派生响应视图** = clamp(baseline + receptor(
#                        (tonic−baseline) + phasic_to_level·phasic))
#                    —— 只由 _compose_view() 写，是"此刻体验到的水位"，给图谱镜像、
#                    前端、序列化用；读取一律走 modulator_level()（现算，不怕旁路改）。
# 通道分工（用户已批准的设计决定 D2）：tonic → 参数通道（hormone.* → ModulationLayer），
# phasic → 图谱通道（激活注入），两条都不许变成"哪个激素高就干什么"的 if-else。
MODULATOR_FIELD_DEFAULTS = {
    "level": 0.5, "baseline": 0.5, "min": 0.0, "max": 1.0,
    "decay_per_min": 0.02,
    "rise_per_min": None,             # 上升 slew 上限（None=不夹）
    "phasic_decay_per_min": None,     # None → PHASIC_DECAY_PER_MIN
    "split_tonic": False,             # 是否拆 tonic/phasic
    "momentum": 0.0,                  # 一阶低通系数（0=关闭）→ 脉冲习惯化
    "phasic_to_level": 1.0,           # phasic 折算进 level 的比例（合成规则）
    "overload_pull": 0.6,             # 过载点后每单位多余量的回落强度
    "sensitivity": 1.0,               # 事件增益乘子
    "saturation": 1.0,                # 软饱和拐点（1.0=关闭）
    "overload": 1.0,                  # 过载拐点（效应反号的起点）
    "refractory_min": 0.0,            # 不应期（分钟；0=关闭）
    "category": "uncategorized",      # monoamine/neuropeptide/excitatory/...
    "mirror": None,                   # 图谱镜像节点 id（缺省 f"{name}样"）
    "circadian_driven": False,        # 只接受 circadian 语境驱动（melatonin）。
                                    # 消费者（R2 P10）：`modulation_events.apply_event`
                                    # 的门卫——非 circadian 来源的事件动不了它。
                                    # 它的**驱动**来自图上 时段/昼夜→调制器 的边
                                    # （`modulator_subgraph.apply_circadian_drift`）。
    "desc": "",
}

# ── 需求（4 个，Phase 2 接管 Drive 评估） ────────────────────
# R2 P9：原本这里还有 `"modulators": [...]` 一栏，零消费者（审计附七）。
# 需求↔调制器的耦合现在是**图上的边**（config.modulator_system.edges，
# 由 `modulator_subgraph.apply_need_drift()` / `update_needs_from_signals(bias=…)`
# 双向消费），镜像节点名走下面的 `MIRROR_NEED`——不在这里留第二份表。
NEED_SPEC = {
    "safety": {"level": 0.20, "setpoint": 0.0, "ideal_range": [0.0, 0.30],
               "min": 0.0, "max": 1.0, "base_weight": 1.00,
               "traits": ["caution", "risk_tolerance"],
               "goal_types": ["rest", "withdraw"],
               "desc": "安全/生存：血量、饥饿、威胁距离、夜晚风险"},
    "exploration": {"level": 0.55, "setpoint": 0.30, "ideal_range": [0.20, 0.50],
                    "min": 0.0, "max": 1.0, "base_weight": 0.85,
                    "traits": ["exploration_bias", "novelty_preference"],
                    "goal_types": ["observe", "approach", "explore"],
                    "desc": "探索：新奇刺激间隔、未知对象、信息缺口"},
    "competence": {"level": 0.40, "setpoint": 0.25, "ideal_range": [0.15, 0.45],
                   "min": 0.0, "max": 1.0, "base_weight": 0.75,
                   "traits": ["persistence"],
                   "goal_types": ["collect", "goal_progress"],
                   "desc": "掌控/能力：未完成目标、失败重试、资源与工具缺口"},
    "social": {"level": 0.35, "setpoint": 0.30, "ideal_range": [0.20, 0.50],
               "min": 0.0, "max": 1.0, "base_weight": 0.80,
               "traits": ["social_openness", "feedback_sensitivity"],
               "goal_types": ["follow", "communicate"],
               "desc": "社交：互动间隔、用户情绪、共同活动与共享记忆"},
}

# ── 性格（8 维，跨情境稳定偏置） ─────────────────────────────
# 出厂值：中性偏好奇/友好（用户可在前端设定；baseline 不可变，用于恢复）
TRAIT_SPEC = {
    "exploration_bias":   {"baseline": 0.55, "desc": "探索倾向：更愿意走出去看"},
    "risk_tolerance":     {"baseline": 0.45, "desc": "风险容忍度"},
    "social_openness":    {"baseline": 0.60, "desc": "社交开放性"},
    "persistence":        {"baseline": 0.50, "desc": "坚持程度：失败后继续尝试"},
    "caution":            {"baseline": 0.50, "desc": "谨慎程度"},
    "novelty_preference": {"baseline": 0.55, "desc": "新奇偏好"},
    "resource_thrift":    {"baseline": 0.50, "desc": "资源节约倾向"},
    "feedback_sensitivity": {"baseline": 0.60, "desc": "对用户反馈的敏感度"},
}
TRAIT_MIN, TRAIT_MAX = 0.0, 1.0

KINDS = ("modulator", "need", "trait")


def _normalize_mod_specs(mods_cfg: dict) -> dict:
    """config["modulator_system"]["specs"] → 完整字段表（缺段回退 MODULATOR_SPEC）。

    回退是有意的：离线测试常传 config={}，此时系统行为必须与 R2 之前逐字一致。
    """
    raw = (mods_cfg or {}).get("specs") or MODULATOR_SPEC
    out = {}
    for name, spec in (raw or {}).items():
        item = dict(MODULATOR_FIELD_DEFAULTS)
        item.update(spec or {})       # 显式 null 是有意义的（rise_per_min: null = 不夹）
        # mirror 留空时由 InternalState.mirror_modulator 解析（旧 4 个走中文类映射）
        item["mirror"] = (spec or {}).get("mirror")
        for k in ("level", "baseline", "min", "max", "decay_per_min",
                  "sensitivity", "saturation", "overload", "refractory_min",
                  "momentum", "phasic_to_level", "overload_pull"):
            item[k] = float(item[k])
        if item["rise_per_min"] is not None:
            item["rise_per_min"] = float(item["rise_per_min"])
        if item["phasic_decay_per_min"] is not None:
            item["phasic_decay_per_min"] = float(item["phasic_decay_per_min"])
        item["min"] = min(item["min"], item["baseline"])
        item["max"] = max(item["max"], item["baseline"])
        out[str(name)] = item
    return out


# 衰减/吸收/恢复类写入不受上升速率夹速（它们是"回落"或"行政设定"，不是事件压力）
SLEW_EXEMPT_SOURCES = ("decay", "rise", "manual", "restore")
MOMENTUM_DECAY_PER_MIN = 0.15     # 习惯化的恢复速率：事件停了呢就退掉


def receptor_response(dev: float, baseline: float, saturation: float,
                      overload: float, pull: float) -> float:
    """浓度偏差 → **受体响应**（任务规范 §14：连续非线性，不是四段 if-else）。

    以 u=|dev| 为自变量，全程 C¹ 连续（分段处函数值与斜率都相同，没有模式切换）：

      S = saturation − baseline（饱和拐点），O = overload − baseline（过载拐点）
      x = max(0, (u−S)/(O−S))，φ(u) = 1/(1+x²)（u≤S 时 φ=1）
      r(u) = u                                    u ≤ S      线性区，灵敏度满格
             = S + (u−S)·φ(u) − pull·max(0,u−O)²  u > S      边际递减 → 峰值 → 回落

    斜率 = (1−x²)/(1+x²)² − 2·pull·max(0,u−O)：在 u=S 处为 1（与线性区无缝衔接），
    在 **u=O 处正好为 0**（响应峰值就落在配置写的过载点上），u>O 后为负——
    再多分泌反而更弱（倒 U 的下行支，Yerkes-Dodson 式功能类比，不是医学数据）。
    pull 只控制峰值之后落得多快（pull=0 时仍会因 φ 缓降，只是平缓）。
    响应最大值 = S + (O−S)/2，所以"过载"永远不可能被当成"越高越好"的加数。

    saturation ≥ 1 且 overload ≥ 1 → 曲线关闭，恒等返回：config 缺该段时
    （离线测试传 config={}）旧 4 个调制器行为与 R2 之前逐字一致。

    生物学名只是功能类比（§10）；这条曲线存在的目的是"别让任何一个读数变成万能开关"。
    """
    if saturation >= 1.0 - 1e-9 and overload >= 1.0 - 1e-9:
        return float(dev)
    sgn = 1.0 if dev >= 0 else -1.0
    u = abs(float(dev))
    b = float(baseline)
    s = max(1e-6, float(saturation) - b)
    o = max(s + 1e-6, float(overload) - b)
    if u <= s:
        r = u
    else:
        x = (u - s) / (o - s)
        r = s + (u - s) / (1.0 + x * x)
        if u > o:
            r -= max(0.0, float(pull)) * (u - o) ** 2
    return sgn * r


class InternalState:
    """内部状态层：需求 / 调制变量 / 性格 的唯一写入口 + 周期生命周期。"""

    def __init__(self, kg=None, config=None, persona=None, engine=None,
                 data_dir: str = "data", save_graph_fn=None):
        self.kg = kg
        self.config = config or {}
        self.persona = persona
        self.engine = engine
        self._save_graph_fn = save_graph_fn
        self.path = f"{data_dir}/internal_state.json"

        self._lock = threading.RLock()
        self._seq = 0
        # 调制系统配置段（R2）：规格 + 图谱参与开关。规格在此归一化一次，
        # 之后所有读取都走 self._specs_mod（不再有第二份基线表）。
        self._mods_cfg = ((self.config.get("modulator_system")
                           if isinstance(self.config, dict) else None) or {})
        self._specs_mod = _normalize_mod_specs(self._mods_cfg)
        self._cycle = None            # 当前打开的 cycle {id, kind, meta, start}
        self._modulators = {}
        self._needs = {}
        self._traits = {}
        self._expectations = {}
        self._ledger = deque(maxlen=LEDGER_LIMIT)
        self._cycles = deque(maxlen=CYCLE_LIMIT)
        self._dirty = False
        self._need_providers = {}
        # 统一时基（R2 P10）：None = 墙钟（生产）。见 set_clock 的注释。
        self._clock_fn = None

        self._init_defaults()
        self._load()

    # ── 时基（R2 P10）───────────────────────────────────────

    def set_clock(self, fn) -> None:
        """注入时间源（实验/回放模式，§26）：`fn() → epoch 秒`；传 None 复原。

        为什么必须是**一个**时基而不是给 `tick_decay` 单独喂 `now=`：
        衰减的 Δt 来自 `last_update`，而任何一次普通写入都会把 `last_update`
        盖成写入时刻的墙钟。两者不同源时（附九 6② 实测）：合成 now 落在墙钟之后
        ⇒ Δt 虚大到"一拍回到基线"，落在墙钟之前 ⇒ `dt<=0` 被跳过 ⇒ **衰减静默失效**。
        所以凡参与 Δt 计算的地方（写入时间戳、不应期、周期起终、证据缓冲）都走
        `_now()`；不注入时 `_now()==time.time()`，生产行为逐字不变。
        """
        self._clock_fn = fn

    def now(self) -> float:
        """统一时基的**公开**读法（R2 P11）：别的模块要"和调制器同一个此刻"时
        读这个，不要各自 `time.time()`——注入时基做回放/实验时（§26），两者会分家。
        """
        return self._now()

    def get_clock(self):
        """当前的时基闭包（None = 墙钟）。R2 P12 实验台配平 `open()/close()` 用：
        接管时基前先把原来那个存下来，结束时原样交还——不假设"原来一定是 None"，
        否则实验台会把**别的**实验（回放测试）注入的时基踩掉。
        """
        return self._clock_fn

    def _now(self) -> float:
        fn = self._clock_fn
        if fn is None:
            return time.time()
        try:
            return float(fn())
        except Exception:                           # noqa: BLE001
            return time.time()

    # ── 初始化 / 持久化 ───────────────────────────────────

    def _init_defaults(self):
        for name, spec in self._specs_mod.items():
            split = bool(spec["split_tonic"])
            self._modulators[name] = {
                "level": float(spec["level"]),        # 派生响应视图（循环末重算）
                "baseline": float(spec["baseline"]),
                "min": float(spec["min"]), "max": float(spec["max"]),
                "decay_per_min": float(spec["decay_per_min"]),
                "rise_per_min": spec["rise_per_min"],
                "phasic_decay_per_min": spec["phasic_decay_per_min"],
                "momentum": float(spec["momentum"]),
                "momentum_state": 0.0,        # 近期脉冲的指数平均（习惯化用）
                "sensitivity": float(spec["sensitivity"]),
                "saturation": float(spec["saturation"]),
                "overload": float(spec["overload"]),
                "overload_pull": float(spec["overload_pull"]),
                "phasic_to_level": float(spec["phasic_to_level"]),
                "refractory_min": float(spec["refractory_min"]),
                "refractory_until": 0.0,      # epoch 秒；脉冲后不应期
                "last_pulse_ts": None,
                "category": str(spec["category"]),
                "circadian_driven": bool(spec.get("circadian_driven")),
                "split_tonic": split,
                "tonic": float(spec["level"]),        # 张力性浓度 = 唯一真值
                "phasic": 0.0 if split else None,     # None = 本调制器不拆快通道
                "pending_rise": 0.0,                  # 夹速后待吸收的上升余量
                "last_slow_ts": None,                 # 上次慢写入时刻（算 dt）
                "last_update": None,
                "last_sources": [],
                "source_detail": [],          # [{ts,reason,delta}] 给 trace/dump
                "history": [],
                "desc": spec["desc"],
            }
            self._compose_view(name)      # level = 派生响应视图（出厂时=规格水位）
        for name, spec in NEED_SPEC.items():
            self._needs[name] = {
                "level": float(spec["level"]),
                "setpoint": float(spec["setpoint"]),
                "ideal_range": list(spec["ideal_range"]),
                "min": float(spec["min"]), "max": float(spec["max"]),
                "urgency": 0.0,
                "drivers": {},
                "last_update": None,
                "peak": float(spec["level"]),
                "satisfied_count": 0,
                "history": [],
                "desc": spec["desc"],
            }
        for name, spec in TRAIT_SPEC.items():
            self._traits[name] = {
                "value": float(spec["baseline"]),
                "baseline": float(spec["baseline"]),
                "min": TRAIT_MIN, "max": TRAIT_MAX,
                "evidence_buffer": [],
                "last_update": None,
                "history": [],
                "desc": spec["desc"],
            }

    def _load(self):
        data = load_json(self.path, default=None)
        if not data:
            logger.info("[InternalState] 无状态文件，使用出厂基线")
            return
        with self._lock:
            self._seq = int(data.get("cycle_seq", 0) or 0)
            for name, saved in (data.get("modulators") or {}).items():
                if name in self._modulators and isinstance(saved, dict):
                    # 只恢复数值与历史，spec 字段（范围/基线/描述）始终以代码为准
                    for k in ("level", "tonic", "phasic", "last_update",
                              "last_sources", "history",
                              "refractory_until", "last_pulse_ts", "source_detail",
                              "momentum_state", "pending_rise", "last_slow_ts"):
                        if k in saved:
                            self._modulators[name][k] = saved[k]
                    # 迁移：R2 P3 之前的快照里 tonic 可能为 null（只有 split 才有）。
                    # 张力性浓度是唯一真值，缺失时回落到当时记录的 level。
                    item = self._modulators[name]
                    if item.get("tonic") is None:
                        item["tonic"] = float(item.get("level") or
                                              item.get("baseline") or 0.0)
                    if item.get("pending_rise") is None:
                        item["pending_rise"] = 0.0
            for name, saved in (data.get("needs") or {}).items():
                if name in self._needs and isinstance(saved, dict):
                    for k in ("level", "urgency", "drivers", "last_update", "peak",
                              "satisfied_count", "history"):
                        if k in saved:
                            self._needs[name][k] = saved[k]
            for name, saved in (data.get("traits") or {}).items():
                if name in self._traits and isinstance(saved, dict):
                    for k in ("value", "evidence_buffer", "last_update", "history"):
                        if k in saved:
                            self._traits[name][k] = saved[k]
            self._expectations = dict(data.get("expectations") or {})
            self._ledger = deque(data.get("reward_ledger") or [], maxlen=LEDGER_LIMIT)
            self._cycles = deque(data.get("cycles") or [], maxlen=CYCLE_LIMIT)
            for name in self._modulators:
                self._compose_view(name)   # 派生视图按恢复出来的真值重算
        if self._traits.get("exploration_bias", {}).get("value") is not None:
            logger.info("[InternalState] 已加载（cycle_seq=%d）", self._seq)

    def save(self) -> bool:
        """原子落盘（批量、每轮一次即可；高频路径不要调）。"""
        with self._lock:
            payload = {
                "version": 1,
                "updated_at": now_str(),
                "cycle_seq": self._seq,
                "modulators": {k: {kk: vv for kk, vv in v.items() if kk != "desc"}
                               for k, v in self._modulators.items()},
                "needs": {k: {kk: vv for kk, vv in v.items() if kk != "desc"}
                          for k, v in self._needs.items()},
                "traits": {k: {kk: vv for kk, vv in v.items() if kk != "desc"}
                           for k, v in self._traits.items()},
                "expectations": dict(self._expectations),
                "reward_ledger": list(self._ledger),
                "cycles": list(self._cycles),
            }
            self._dirty = False
        ok = atomic_write_json(self.path, payload)
        if not ok:
            logger.warning("[InternalState] 状态落盘失败（内存状态仍有效）")
        return ok

    # ── 周期生命周期（I2） ────────────────────────────────

    def begin_cycle(self, kind: str = "turn", meta: dict = None) -> str:
        with self._lock:
            self._seq += 1
            cid = f"c_{self._seq:06d}"
            self._cycle = {"id": cid, "kind": str(kind), "meta": dict(meta or {}),
                           "start": self._now(), "start_str": now_str(),
                           "end": None, "duration_s": None, "outcome": None}
        try:  # 观测接线：只把 cycle 关联进 fas_log（单一真源，无行为分支）
            import fas_log
            fas_log.set_cycle(cid)
        except Exception:
            pass
        return cid

    def end_cycle(self, cycle_id: str = None, outcome: dict = None,
                  persist: bool = True) -> dict:
        """结束一轮：写入轮次记录（环形），可选落盘。返回该轮记录。"""
        with self._lock:
            entry = dict(self._cycle) if self._cycle else {}
            if cycle_id and entry.get("id") != cycle_id:
                entry = {"id": cycle_id, "kind": "unknown", "meta": {},
                         "start": None, "start_str": None}
            entry["end"] = self._now()
            if entry.get("start"):
                entry["duration_s"] = round(entry["end"] - entry["start"], 3)
            entry["outcome"] = dict(outcome or {})
            self._cycles.append(entry)
            if self._cycle and self._cycle.get("id") == entry.get("id"):
                self._cycle = None
            self._dirty = True
        if persist:
            self.save()
            self.sync_graph()
        try:  # 观测接线：周期结算由调用方先发事件，这里只清关联
            import fas_log
            if fas_log.get_cycle() == (entry.get("id") or cycle_id):
                fas_log.set_cycle(None)
        except Exception:
            pass
        return entry

    # ── 唯一写入口（I1 + I3） ─────────────────────────────

    def _bounds(self, kind: str, name: str) -> tuple:
        if kind == "trait":
            return TRAIT_MIN, TRAIT_MAX
        store = self._modulators if kind == "modulator" else self._needs
        item = store.get(name)
        if item is None:
            return 0.0, 1.0
        return float(item.get("min", 0.0)), float(item.get("max", 1.0))

    def _resolve(self, kind: str, name: str) -> dict:
        if kind == "modulator":
            return self._modulators.get(name)
        if kind == "need":
            return self._needs.get(name)
        if kind == "trait":
            return self._traits.get(name)
        return None

    @staticmethod
    def _value_key(kind: str) -> str:
        """每种状态的**权威数值字段**：调制器=tonic（张力性浓度），不是派生视图 level。"""
        return "tonic" if kind == "modulator" else (
            "value" if kind == "trait" else "level")

    def _pull_coefficient(self, name: str, item: dict) -> float:
        """把 config 的 `overload_pull`（"顶格时扣掉多少响应"）换算成
        `receptor_response` 要的**二次项系数**。

        为什么必须换算（R2 P8/D5）：12 个调制器的 `overload` 都写在 .80–.95，
        而量程上限是 1.0 ⇒ 可达的超出量只有 .05–.20，原始二次项在这个尺度上
        约等于零（换算前实测：dopamine 浓度钉在 1.0 时 pull 只扣掉 .00067，
        "过载"在行为上不存在）。除以 `(max − overload)²` 之后，pull 的含义
        变成线性的：0=关、1=顶格时正好抵消回零、>1=反号（倒 U 下行支走到对侧）。
        没有过载余量（overload ≥ max）时返回 0：那条支走不到，不假装在起作用。
        """
        pull = float(item.get("overload_pull", 0.0) or 0.0)
        if pull <= 0.0:
            return 0.0
        _lo, hi = self._bounds("modulator", name)
        span = float(hi) - float(item.get("overload", 1.0) or 1.0)
        if span <= 1e-6:
            return 0.0
        return pull / (span * span)

    def _view_of(self, name: str, item: dict) -> float:
        """(tonic, phasic) → 响应视图：合成 → 受体曲线 → 裁剪到量程。纯函数、无副作用。"""
        base = float(item.get("baseline", 0.0))
        lo, hi = self._bounds("modulator", name)
        dev = float(item.get("tonic", base)) - base
        ph = item.get("phasic")
        if ph:
            dev += float(item.get("phasic_to_level", 1.0) or 0.0) * float(ph)
        resp = receptor_response(dev, base, float(item.get("saturation", 1.0) or 1.0),
                                 float(item.get("overload", 1.0) or 1.0),
                                 self._pull_coefficient(name, item))
        return min(hi, max(lo, base + resp))

    def _compose_view(self, name: str) -> float:
        """把派生视图写回 item["level"]（唯一写者；真值仍是 tonic/phasic）。"""
        item = self._modulators.get(name)
        if item is None:
            return 0.0
        item["level"] = self._view_of(name, item)
        return item["level"]

    def _apply_momentum(self, item: dict, gain: float) -> float:
        """脉冲习惯化（§6 momentum 的消费者）：同向重复打折，反向（意外）全量。

        mv ← mv·m + gain·(1−m) 是一阶低通；效力除数 1+max(0, mv·gain) 只在**同向**
        时作用——连续的同质事件不再把系统顶上天，但一次反转的强度不受影响。
        momentum=0（旧 4 个调制器在 config 缺段时的默认）完全恒等。
        """
        m = float(item.get("momentum") or 0.0)
        if m <= 0.0 or abs(gain) < 1e-12:
            return gain
        mv = float(item.get("momentum_state") or 0.0)
        same = mv * gain
        att = 1.0 / (1.0 + same) if same > 0.0 else 1.0
        item["momentum_state"] = mv * m + gain * (1.0 - m)
        return gain * att

    def _write_numeric(self, kind: str, name: str, target: float,
                       reason: str = "", cycle_id: str = None,
                       source: str = "system", merge_window: float = 0.0,
                       stamp: float = None, slew: bool = True) -> dict:
        """数值落地的**唯一内部函数**：夹速 → 裁剪 → 记历史 → 记来源 → 重算视图。

        调制器写的是**张力性浓度 `tonic`**（唯一真值），`level` 是随后重算的派生响应
        视图；需求/性格仍写各自 level/value。apply_delta / set_value / 时间衰减 /
        上升吸收 全部经此（不变量 I1/I3）。

        merge_window>0 时，若上一条历史同源同向且时间差在窗口内，则**合并**到那条
        —— 衰减每拍都写，逐条记录会把真实事件挤出 history（审计 D-6 的正解不是
        "刷屏"，而是"走同一入口但不刷屏"）。

        slew=True 且规格设了 rise_per_min 时，上升按 rise_per_min×Δt 夹速，夹下来的
        余量进 `pending_rise`，由 tick_decay 逐拍吸收（§13 累积：持续压力确实会抬高
        水位，但要花时间）。回落与行政设定（SLEW_EXEMPT_SOURCES）不受夹速约束。
        """
        if kind not in KINDS:
            return {"ok": False, "error": f"kind 必须是 {KINDS}"}
        with self._lock:
            item = self._resolve(kind, name)
            if item is None:
                return {"ok": False, "error": f"未知 {kind}: {name}"}
            key = self._value_key(kind)
            old = float(item.get(key, 0.0))
            lo, hi = self._bounds(kind, name)
            now_ts = self._now() if stamp is None else float(stamp)
            pend0 = float(item.get("pending_rise") or 0.0)
            new = min(hi, max(lo, float(target)))
            clamped = abs(new - float(target)) > 1e-12
            slewed = False
            if (kind == "modulator" and slew and new > old
                    and item.get("rise_per_min") is not None
                    and source not in SLEW_EXEMPT_SOURCES
                    and item.get("last_slow_ts") is not None):
                dt_min = max(0.0, (now_ts - float(item["last_slow_ts"])) / 60.0)
                cap = old + float(item["rise_per_min"]) * dt_min
                if new > cap:
                    item["pending_rise"] = pend0 + (new - cap)
                    new = cap
                    slewed = True
            if new == old:
                if kind == "modulator" and slewed:
                    item["last_slow_ts"] = now_ts
                    self._dirty = True
                return {"ok": True, "kind": kind, "name": name, "old": round(old, 4),
                        "new": round(old, 4), "delta": 0.0,
                        "clamped": clamped, "reason": reason,
                        "slew_limited": slewed,
                        "pending_rise": round(float(item.get("pending_rise") or 0.0), 4),
                        "noop": True}
            # 写之前先记下视图（trace 要的是 0.51→0.59 这种"体验水位"的前后）
            lvl0 = self._view_of(name, item) if kind == "modulator" else 0.0
            item[key] = round(new, 6)
            item["last_update"] = now_ts
            if kind == "modulator":
                item["last_slow_ts"] = now_ts
            hist = item.setdefault("history", [])
            entry = {"ts": now_str(), "cycle_id": cycle_id, "from": round(old, 4),
                     "to": round(new, 4), "delta": round(new - old, 4),
                     "reason": str(reason)[:120], "source": source}
            tail = hist[-1] if hist else None
            if (merge_window > 0 and tail and tail.get("source") == source
                    and tail.get("reason") == entry["reason"]
                    and (tail.get("delta") or 0) * entry["delta"] >= 0
                    and (now_ts - float(tail.get("_ts") or 0)) <= merge_window):
                tail["to"] = entry["to"]
                tail["delta"] = round(float(tail.get("delta") or 0) + entry["delta"], 4)
                tail["ts"] = entry["ts"]
                tail["_ts"] = now_ts
                tail["cycle_id"] = entry["cycle_id"] or tail.get("cycle_id")
            else:
                entry["_ts"] = now_ts
                hist.append(entry)
            if len(hist) > HISTORY_LIMIT:
                del hist[:-HISTORY_LIMIT]
            if kind == "modulator":
                keep = max(1, int(self._mods_cfg.get("recent_sources_keep", 3) or 3))
                if reason:
                    item["last_sources"] = (
                        [str(reason)[:60]] +
                        list(item.get("last_sources") or [])[:keep - 1])[:keep]
                    item["source_detail"] = (
                        [{"ts": now_str(), "reason": str(reason)[:60],
                          "delta": round(new - old, 4), "source": source}] +
                        list(item.get("source_detail") or [])[:keep - 1])[:keep]
                self._compose_view(name)     # level 是派生视图，不另记历史
                # 单一落地点 = 单一 trace 点（§22）：所有慢分量写入都留一行
                line = (f"[MODULATION] mod={name} event={str(reason)[:60]} "
                        f"source={source} tonic:{new - old:+.3f} phasic:— "
                        f"level:{lvl0:.2f}→{item['level']:.2f}"
                        + (f" pending:{item.get('pending_rise', 0.0):.3f}"
                           if slewed else ""))
                if source in ("decay", "rise"):
                    logger.debug(line)        # 每拍都有的回归/吸收不刷屏（INFO 留给事件）
                else:
                    logger.info(line)
            if kind == "need":
                item["peak"] = max(float(item.get("peak", 0.0)), new)
            self._dirty = True
        return {"ok": True, "kind": kind, "name": name, "old": round(old, 4),
                "new": round(new, 4), "delta": round(new - old, 4),
                "clamped": clamped, "reason": reason,
                "slew_limited": slewed,
                "pending_rise": round(float(item.get("pending_rise") or 0.0), 4)
                if kind == "modulator" else None}

    def apply_delta(self, kind: str, name: str, delta: float,
                    reason: str = "", cycle_id: str = None,
                    source: str = "system") -> dict:
        """唯一数值写入接口（增量式）：裁剪到范围 + 记历史（含来源与 cycle_id）。

        其它模块不得直接改 level/value —— 所有更新（激素衰减、需求增长、
        奖励反馈、性格慢学习、人工调节）都从这里走。
        调制器的增量作用在**张力性浓度 tonic** 上（level 会随之重算）。
        """
        if kind not in KINDS:
            return {"ok": False, "error": f"kind 必须是 {KINDS}"}
        item = self._resolve(kind, name)
        if item is None:
            return {"ok": False, "error": f"未知 {kind}: {name}"}
        key = self._value_key(kind)
        return self._write_numeric(kind, name,
                                   float(item.get(key, 0.0)) + float(delta),
                                   reason=reason, cycle_id=cycle_id, source=source)

    def set_value(self, kind: str, name: str, value: float,
                  source: str = "manual", cycle_id: str = None,
                  reason: str = "") -> dict:
        """绝对设定（人工调节 / 前端滑块）。等价于一次指向目标值的 delta。

        调制器的目标值是 **tonic**（张力性浓度）：拆了快通道的调制器，level 视图
        还会带上残余 phasic，所以"设定 level 到 X"在这里的语义是"把慢分量设到 X"。
        """
        item = self._resolve(kind, name)
        if item is None:
            return {"ok": False, "error": f"未知 {kind}: {name}"}
        key = self._value_key(kind)
        try:
            target = float(value)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"value 必须是数字: {value!r}"}
        return self.apply_delta(kind, name, target - float(item.get(key, 0.0)),
                                reason=reason or f"人工设定为 {target}",
                                cycle_id=cycle_id, source=source)

    # ── 需求控制器（Phase 2+ 补账 2026-09-20）──────────────
    # 目标值来自**别处已有的事实**（provider 闭包注入），本模块仍只做
    # 唯一写入口：level 向 target 缓步收敛、urgency 由理想区间外距离导出。
    # 不接 provider 的维度不动——没有信号就没有观点。

    def set_need_signal_provider(self, name: str, fn):
        self._need_providers[name] = fn

    def need_salience(self, name: str) -> float:
        """需求的**显著性**（0~1）：紧迫度与"离靶值多远"里较大的那个。

        R2 P9 的两个新读者都读它，而不是各自挑一个字段：
          · `modulator_subgraph.apply_need_drift()`（需求→调制器慢写入）
          · `autonomy._motivation_value()`（需求→行动动机）
        只读 `level` 会把"在理想区间内的波动"当饥饿；只读 `urgency` 会在
        理想区间内恒为 0，需求刚抬起头时完全没有行为后果。
        """
        with self._lock:
            it = self._needs.get(name)
            if it is None:
                return 0.0
            lv = float(it.get("level", 0.0) or 0.0)
            gap = max(0.0, lv - float(it.get("setpoint", 0.0) or 0.0))
            return round(max(float(it.get("urgency", 0.0) or 0.0), gap), 4)

    def update_needs_from_signals(self, rate: float = 0.08,
                                  cycle_id: str = None,
                                  bias: dict = None) -> dict:
        """需求控制器：level 向"别处已有的事实"给出的靶值收敛。

        `bias`（R2 P9，来自图上的 `调制器 -[w]-> 需求` 边）是**加在靶值上的连续偏置**，
        不是开关：`target' = clamp(target + Σ w×dev(调制器))`。钳位用的是本维度的
        [min,max]，所以调制器钉在多高都不会把需求推出量程（P9 闸门的有界性断言）。
        """
        out = {}
        biases = bias or {}
        for name, fn in list(self._need_providers.items()):
            try:
                target = float(fn() if callable(fn) else fn)
            except Exception:
                continue
            item = self._needs.get(name)
            if item is None:
                continue
            lo, hi = self._bounds("need", name)
            b = float(biases.get(name, 0.0) or 0.0)
            target = min(hi, max(lo, target + b))
            cur = float(item.get("level", 0.0))
            delta = rate * (target - cur)
            if abs(delta) < 1e-4:
                continue
            r = self.apply_delta("need", name, delta,
                                 reason=(f"需求控制器: 目标 {target:.2f}"
                                         + (f"（含调制偏置 {b:+.2f}）" if abs(b) > 1e-6 else "")),
                                 cycle_id=cycle_id, source="needs_controller")
            if r.get("ok"):
                out[name] = {"from": cur, "to": r["new"], "target": round(target, 3)}
            # urgency：超出理想区间上界的程度（区间内=不急）
            with self._lock:
                it = self._needs.get(name) or {}
                ir = it.get("ideal_range") or [0.0, 1.0]
                mx = it.get("max", 1.0)
                lv = float(it.get("level", 0.0))
                span = max(1e-6, mx - float(ir[1]))
                it["urgency"] = round(min(1.0, max(0.0, (lv - float(ir[1])) / span)), 3)
        return {"ok": True, "updated": out}

    # ── 性格慢学习（Phase 5 补账）──────────────────────────
    # 每次经历只推 α（0.005），限幅 baseline±0.15——一次失败不会改性格，
    # 一百次才会。baseline 永不动（可 reset）。证据缓冲留痕，历史可审计。

    TRAIT_ALPHA = 0.005
    TRAIT_DEV_MAX = 0.15
    TRAIT_EVIDENCE_KEEP = 20

    def record_trait_evidence(self, trait: str, value: float, *,
                              source: str = "experience", reason: str = "",
                              cycle_id: str = None) -> dict:
        """value ∈ [-1,1]：该经历对这一维度的证据方向与强度。"""
        item = self._traits.get(trait)
        if item is None:
            return {"ok": False, "error": f"未知 trait: {trait}"}
        v = max(-1.0, min(1.0, float(value)))
        with self._lock:
            buf = item.setdefault("evidence_buffer", [])
            buf.append({"v": round(v, 3), "source": source, "reason": reason[:80],
                        "ts": self._now()})
            del buf[:-self.TRAIT_EVIDENCE_KEEP]
            base = float(item.get("baseline", 0.5))
            cur = float(item.get("value", base))
        lo_b, hi_b = base - self.TRAIT_DEV_MAX, base + self.TRAIT_DEV_MAX
        step = self.TRAIT_ALPHA * v
        if step > 0:                      # 限幅：不越过本维度允许的余量
            step = min(step, max(0.0, hi_b - cur))
        else:
            step = max(step, lo_b - cur)
        r = self.apply_delta("trait", trait, step,
                             reason=f"慢学习[{source}]: {reason}"[:120],
                             cycle_id=cycle_id, source=source)
        r["drift"] = round(step, 5)
        r["band"] = [round(lo_b, 3), round(hi_b, 3)]
        return r

    def reset_traits(self, source: str = "manual") -> dict:
        """恢复出厂性格基线（用户可一键回到初始）。"""
        out = {}
        for name, item in list(self._traits.items()):
            out[name] = self.set_value("trait", name, item["baseline"],
                                       source=source, reason="恢复出厂基线")
        return {"ok": True, "traits": out}

    def reset_modulators(self, source: str = "manual") -> dict:
        out = {}
        for name, item in list(self._modulators.items()):
            out[name] = self.set_value("modulator", name, item["baseline"],
                                       source=source, reason="恢复出厂基线")
            # 复位要连"还没吸收完的上升余量 / 习惯化 / 不应期"一起清掉，
            # 否则恢复出厂后水位会自己爬回去（快照测试要的是真的出厂态）
            with self._lock:
                item["pending_rise"] = 0.0
                item["momentum_state"] = 0.0
                item["refractory_until"] = 0.0
                item["phasic"] = 0.0 if item.get("split_tonic") else None
                self._compose_view(name)
        return {"ok": True, "modulators": out}

    # ── 读取（只读快照；其它模块一律走这些接口） ───────────

    def modulator_level(self, name: str) -> float:
        """此刻**体验到的**水位 = 响应视图（tonic 慢分量 + phasic 快分量 → 曲线）。

        现算不读缓存：图谱镜像/前端读的是缓存，任何旁路改了 phasic 这里也不会看到
        过期值。
        """
        item = self._modulators.get(name)
        return self._view_of(name, item) if item else 0.0

    def modulator_conc(self, name: str) -> float:
        """张力性**浓度**（未曲线化、不含 phasic 的真值）。观测/测试用。"""
        item = self._modulators.get(name) or {}
        return float(item.get("tonic", item.get("level", 0.0)) or 0.0)

    def modulator_pulse(self, name: str) -> float:
        """当前 phasic 脉冲（不拆快通道的调制器恒 0）。图谱激活注入用（P4）。"""
        item = self._modulators.get(name) or {}
        return float(item.get("phasic") or 0.0)

    def modulator_names(self) -> list:
        return list(self._modulators.keys())

    def modulator_baseline(self, name: str) -> float:
        return float((self._modulators.get(name) or {}).get("baseline", 0.5))

    def modulator_field(self, name: str, key: str, default=None):
        return (self._modulators.get(name) or {}).get(key, default)

    def modulator_tonic(self, name: str) -> float:
        """**参数通道**的读数：只含慢分量（含受体曲线），不含 phasic。

        设计决定 D2（用户批准）：tonic → hormone.* 信号 → ModulationLayer → 认知场
        参数；phasic → 图谱激活。所以"瞬时惊喜"不会把参数通道瞬间推走。
        """
        item = self._modulators.get(name)
        if not item:
            return 0.0
        base = float(item.get("baseline", 0.0))
        return base + self.modulator_dev(name)

    def modulator_dev(self, name: str) -> float:
        """参数通道的信号量：慢分量相对基线的**受体响应**（0 = 无调制）。

        R2 前 `tonic` 只是被同步、没人读（审计 D-2）；现在它是 hormone.* 的
        真输入。曲线在此生效（§14）：浓度越界后边际递减甚至反转，下游所有
        coef 乘的都是这条已经非线性的量，不需要每个目标各写一段 if-else。
        """
        item = self._modulators.get(name)
        if not item:
            return 0.0
        base = float(item.get("baseline", 0.0))
        lo, hi = self._bounds("modulator", name)
        resp = receptor_response(
            self.modulator_conc(name) - base, base,
            float(item.get("saturation", 1.0) or 1.0),
            float(item.get("overload", 1.0) or 1.0),
            self._pull_coefficient(name, item))
        return min(hi - base, max(lo - base, resp))

    def need_names(self) -> list:
        return list(self._needs.keys())

    def need_level(self, name: str) -> float:
        item = self._needs.get(name) or {}
        return float(item.get("level", 0.0))

    def need_urgency(self, name: str) -> float:
        item = self._needs.get(name) or {}
        return float(item.get("urgency", 0.0))

    def trait(self, name: str) -> float:
        item = self._traits.get(name) or {}
        return float(item.get("value", 0.5))

    def trait_baseline(self, name: str) -> float:
        item = self._traits.get(name) or {}
        return float(item.get("baseline", 0.5))

    def snapshot(self) -> dict:
        """完整状态快照（可回滚、可在测试里全量对比）。"""
        with self._lock:
            return {
                "modulators": {k: {"level": self.modulator_level(k),
                                   "tonic": v.get("tonic"),
                                   "phasic": v.get("phasic")}
                               for k, v in self._modulators.items()},
                "needs": {k: {"level": v["level"], "urgency": v["urgency"]}
                          for k, v in self._needs.items()},
                "traits": {k: v["value"] for k, v in self._traits.items()},
                "expectations": dict(self._expectations),
            }

    def restore(self, snapshot: dict, source: str = "restore") -> dict:
        """从快照恢复（人工回滚 / 测试）；逐项经 set_value，历史留痕。"""
        snap = snapshot or {}
        applied = 0
        for name, vals in (snap.get("modulators") or {}).items():
            if name in self._modulators and isinstance(vals, dict) and "level" in vals:
                # 快照里的 level 是派生视图，真值是 tonic（+phasic）——按真值回滚
                target = vals.get("tonic")
                target = float(vals["level"] if target is None else target)
                if not self.set_value("modulator", name, target, source=source,
                                      reason="快照恢复")["ok"]:
                    continue
                applied += 1
                if vals.get("phasic") is not None:
                    with self._lock:
                        item = self._modulators[name]
                        if item.get("split_tonic"):
                            item["phasic"] = float(vals["phasic"] or 0.0)
                            self._compose_view(name)
        for name, vals in (snap.get("needs") or {}).items():
            if name in self._needs and isinstance(vals, dict) and "level" in vals:
                if self.set_value("need", name, vals["level"], source=source,
                                  reason="快照恢复")["ok"]:
                    applied += 1
        for name, value in (snap.get("traits") or {}).items():
            if name in self._traits:
                if self.set_value("trait", name, value, source=source,
                                  reason="快照恢复")["ok"]:
                    applied += 1
        return {"ok": True, "applied": applied}

    # ── 图谱镜像（in-place 单节点，I4） ───────────────────

    MIRROR_MODULATOR = {"dopamine": "多巴胺样", "cortisol": "皮质醇样",
                        "serotonin": "血清素样", "oxytocin": "催产素样"}
    MIRROR_NEED = {"safety": "安全需求", "exploration": "探索需求",
                   "competence": "掌控需求", "social": "社交需求"}

    @property
    def mirror_modulator(self) -> dict:
        """调制器 → 图谱镜像节点 id（由规格生成；旧 4 个 id 逐字保留，
        这样历史快照/已有图谱/前端标签都不受影响）。"""
        out = {}
        for name, spec in self._specs_mod.items():
            out[name] = (spec.get("mirror") or
                         self.MIRROR_MODULATOR.get(name) or f"{name}样")
        return out

    def _ensure_mirror(self, node_id: str, state_type: str, desc: str) -> Node:
        with self.kg._lock:
            node = self.kg.nodes.get(node_id)
            if node is None:
                node = Node(id=node_id, weight=0.4, label="declarative-semantic",
                            graph_space="self",
                            extra_attrs={"type": state_type, "canon": "internal_state",
                                         "description": desc})
                self.kg.add_node(node)
                logger.info(f"[InternalState] 创建状态节点: {node_id}")
            return node

    def sync_graph(self) -> dict:
        """把运行时状态镜像到图（in-place，只更新属性，不建新节点）。

        刻意**不建边**：Self 的出边权重和会直接影响扩散归一化，加边会改变
        既有传播强度。需求/调制变量与 Self 的接线在 Phase 2（Drive 接管）
        时统一评估，避免为"看起来完整"而扰动现有动力学。
        """
        if self.kg is None:
            return {"synced": 0}
        synced = 0
        for name, node_id in self.mirror_modulator.items():
            item = self._modulators.get(name)
            if item is None:
                continue
            node = self._ensure_mirror(node_id, "modulator", item.get("desc", ""))
            with self._lock:                       # 视图要一次性读全 tonic+phasic
                level = self.modulator_level(name)
                attrs = {"level": round(level, 4),
                         "baseline": round(float(item["baseline"]), 4),
                         "tonic": item.get("tonic"), "phasic": item.get("phasic"),
                         "category": item.get("category"),
                         "updated": now_str()}
            with self.kg._lock:
                node.extra_attrs.update(attrs)
                node.touch()
            synced += 1
        for name, node_id in self.MIRROR_NEED.items():
            item = self._needs.get(name)
            if item is None:
                continue
            node = self._ensure_mirror(node_id, "need", item.get("desc", ""))
            with self.kg._lock:
                node.extra_attrs.update({
                    "level": round(float(item["level"]), 4),
                    "urgency": round(float(item.get("urgency", 0.0)), 4),
                    "ideal_range": item.get("ideal_range"),
                    "drivers": item.get("drivers") or {},
                    "updated": now_str(),
                })
                node.touch()
            synced += 1
        for name, item in self._traits.items():
            node_id = f"性格:{name}"
            node = self._ensure_mirror(node_id, "trait", item.get("desc", ""))
            with self.kg._lock:
                node.extra_attrs.update({
                    "value": round(float(item["value"]), 4),
                    "baseline": round(float(item["baseline"]), 4),
                    "updated": now_str(),
                })
                node.touch()
            synced += 1
        if self._save_graph_fn:
            try:
                self._save_graph_fn()
            except Exception as e:
                logger.debug(f"[InternalState] 图落盘跳过: {e}")
        return {"synced": synced}

    # ── 观测 ──────────────────────────────────────────────

    def state(self) -> dict:
        with self._lock:
            return {
                "cycle_seq": self._seq,
                "current_cycle": (self._cycle or {}).get("id"),
                "modulators": {
                    k: {"level": round(self.modulator_level(k), 4),
                        "conc": round(self.modulator_conc(k), 4),
                        "baseline": v["baseline"],
                        "min": v["min"], "max": v["max"],
                        "tonic": v.get("tonic"), "phasic": v.get("phasic"),
                        "dev": round(self.modulator_dev(k), 4),
                        "pending_rise": round(float(v.get("pending_rise") or 0.0), 4),
                        "momentum_state": round(
                            float(v.get("momentum_state") or 0.0), 4),
                        "decay_per_min": v["decay_per_min"],
                        "rise_per_min": v.get("rise_per_min"),
                        "momentum": v.get("momentum"), "sensitivity": v.get("sensitivity"),
                        "saturation": v.get("saturation"), "overload": v.get("overload"),
                        "refractory_min": v.get("refractory_min"),
                        "refractory_left_s": max(0.0, round(
                            float(v.get("refractory_until") or 0.0) - self._now(), 1)),
                        "category": v.get("category"),
                        "graph_id": self.mirror_modulator.get(k),
                        "last_sources": v.get("last_sources") or [],
                        "recent_sources": v.get("source_detail") or [],
                        "desc": v.get("desc", "")}
                    for k, v in self._modulators.items()},
                "needs": {
                    k: {"level": round(v["level"], 4), "urgency": round(v["urgency"], 4),
                        "setpoint": v["setpoint"], "ideal_range": v["ideal_range"],
                        "drivers": v.get("drivers") or {},
                        "peak": v.get("peak"), "satisfied_count": v.get("satisfied_count"),
                        "desc": v.get("desc", "")}
                    for k, v in self._needs.items()},
                "traits": {
                    k: {"value": round(v["value"], 4), "baseline": v["baseline"],
                        "evidence_count": len(v.get("evidence_buffer") or []),
                        "desc": v.get("desc", "")}
                    for k, v in self._traits.items()},
                "expectations": {k: dict(v) for k, v in self._expectations.items()},
                "recent_rewards": list(self._ledger)[-10:],
                "recent_cycles": list(self._cycles)[-10:],
            }

    def recent_cycles(self, n: int = 20) -> list:
        with self._lock:
            return list(self._cycles)[-n:]

    def recent_rewards(self, n: int = 20) -> list:
        with self._lock:
            return list(self._ledger)[-n:]

    def history(self, kind: str, name: str, n: int = 20) -> list:
        item = self._resolve(kind, name)
        if item is None:
            return []
        return list(item.get("history") or [])[-n:]

    def explain(self, cycle_id: str) -> dict:
        """解释某一轮：该轮的状态变化 + 奖励台账（可回答"为什么她想探索"）。"""
        with self._lock:
            cycles = [c for c in self._cycles if c.get("id") == cycle_id]
            rewards = [r for r in self._ledger
                       if (r or {}).get("cycle_id") == cycle_id]
            changes = []
            for kind, store in (("modulator", self._modulators),
                                ("need", self._needs), ("trait", self._traits)):
                for name, item in store.items():
                    for h in item.get("history") or []:
                        if (h or {}).get("cycle_id") == cycle_id:
                            changes.append({"kind": kind, "name": name, **h})
        return {"cycle_id": cycle_id,
                "cycle": cycles[0] if cycles else None,
                "state_changes": changes,
                "rewards": rewards,
                "summary": self._summarize(cycle_id, cycles, changes, rewards)}

    @staticmethod
    def _summarize(cycle_id, cycles, changes, rewards) -> str:
        if not cycles and not changes and not rewards:
            return f"没有 {cycle_id} 的记录（该轮未产生状态变化）"
        parts = []
        if rewards:
            r = rewards[-1]
            parts.append(f"奖励 {r.get('reward')}（{r.get('explanation', '')[:60]}）")
        if changes:
            top = sorted(changes, key=lambda c: -abs(c.get("delta", 0)))[:3]
            parts.append("主要状态变化: " + "、".join(
                f"{c['name']} {c['from']}→{c['to']}" for c in top))
        return "；".join(parts) or "该轮无显著状态变化"

    def record_reward(self, cycle_id: str, reward_value: float,
                      components: dict = None, prediction_error: float = None,
                      source_event: str = "", affected_needs: list = None,
                      affected_goals: list = None, explanation: str = "") -> dict:
        """奖励台账写入（Phase 3 的 compute_reward 会调用这里；本阶段先提供接口）。"""
        entry = {
            "cycle_id": cycle_id, "ts": now_str(),
            "reward": round(float(reward_value), 4),
            "components": {k: round(float(v), 4) for k, v in (components or {}).items()},
            "prediction_error": (None if prediction_error is None
                                 else round(float(prediction_error), 4)),
            "source_event": str(source_event)[:80],
            "affected_needs": list(affected_needs or []),
            "affected_goals": list(affected_goals or []),
            "explanation": str(explanation)[:200],
        }
        with self._lock:
            self._ledger.append(entry)
            self._dirty = True
        return entry

    def note_cycle_outcome(self, cycle_id: str, outcome: dict):
        """给当前/最近一轮补充结果字段（行动结果、LLM 调用等）。"""
        with self._lock:
            for c in reversed(self._cycles):
                if c.get("id") == cycle_id:
                    c.setdefault("outcome", {}).update(dict(outcome or {}))
                    self._dirty = True
                    return True
            if self._cycle and self._cycle.get("id") == cycle_id:
                self._cycle.setdefault("meta", {}).update(dict(outcome or {}))
                return True
        return False

    # ── 奖赏侧接口（reward.py 消费；写入口仍是本模块，I1 不破）──
    #
    # 语义边界（任务规范 §四/§七）：
    #   expectations/phasic 属于**运行时内部状态**（data/internal_state.json，
    #   既有定位），它们只调制学习率与当前激活——**不是人格**。长期人格只
    #   写 self 图（disposition）。这里不存在"激素值=人格特质"的等号。

    PHASIC_DECAY_PER_MIN = 0.30     # phasic 是脉冲：分钟级快速回归 0

    def expectation(self, key: str) -> float:
        """行为@情境 的奖赏预期（tonic 侧，慢变量）。无记录=0（中性基线）。"""
        with self._lock:
            return float((self._expectations.get(key) or {}).get("expected", 0.0))

    def update_expectation(self, key: str, valence: float, alpha: float = 0.15,
                           cycle_id: str = None):
        """预期慢更新：expected ← (1-α)·expected + α·valence。"""
        with self._lock:
            e = self._expectations.setdefault(
                key, {"expected": 0.0, "n": 0, "updates": []})
            v = (1.0 - alpha) * float(e["expected"]) + alpha * float(valence)
            e["expected"] = round(max(-1.0, min(1.0, v)), 4)
            e["n"] = int(e.get("n", 0)) + 1
            e["last_cycle"] = cycle_id
            if len(e.get("updates", [])) > 20:
                e["updates"] = e["updates"][-20:]
            self._dirty = True
        return e["expected"]

    def pulse(self, name: str, delta: float, cycle_id: str = None,
              reason: str = "", source: str = "event") -> dict:
        """调制器 phasic 脉冲（R2 推广：不再只有 dopamine 一条路）。

        - 只动 phasic（慢分量走 apply_delta），乘 sensitivity；
        - 习惯化（momentum）：同向重复打折，反向全量；
        - 不应期内按剩余时间比例打折（防同一类事件连打同一名调制器）；
        - 分上下限 ±1，量纲与 RPE 一致。
        返回 {"ok","name","old","new","tonic","level","pending_rise",...}；
        level 字段是**合成后的响应视图**（图谱激活注入 P4 用它做幅度依据）。
        """
        with self._lock:
            item = self._modulators.get(name)
            if item is None:
                return {"ok": False, "error": f"未知 modulator: {name}"}
            lvl_before = self._view_of(name, item)
            if item.get("phasic") is None:
                # 未拆 tonic/phasic 的调制器：脉冲 = 一次带习惯化的慢分量位移
                g = self._apply_momentum(item, float(delta) *
                                         float(item.get("sensitivity", 1.0) or 1.0))
                r = self._write_numeric("modulator", name,
                                        float(item.get("tonic", 0.0)) + g,
                                        reason=reason or "事件脉冲",
                                        cycle_id=cycle_id, source=source)
                r["level_old"], r["level"] = lvl_before, self._view_of(name, item)
                r["phasic_old"], r["phasic"] = None, None
                r["name"] = name
                # 未拆快通道的调制器：事件幅度就是这次真正写进 tonic 的量
                inj = self._graph_pulse(name, abs(float(r.get("delta") or 0.0)))
                r["graph_pulse"] = inj
                # trace 由 _write_numeric（统一落地点）发出；图注入幅度单独补一条
                if inj:
                    self._trace_graph_pulse(name, reason, source, inj)
                return r
            now = self._now()
            old = float(item.get("phasic") or 0.0)
            sens = float(item.get("sensitivity", 1.0) or 1.0)
            gain = self._apply_momentum(item, float(delta) * sens)
            ref_min = float(item.get("refractory_min") or 0.0)
            until = float(item.get("refractory_until") or 0.0)
            # 不应期：越新越打不动。left = 窗口剩余比例（0=不在不应期），
            # damping = 实际施加的增益比例（1=全额）。两个都进日志/返回值，
            # 因为早先只留一个"frac"时，1.0 同时表示"不在不应期"和"刚进不应期"。
            left = 0.0
            damping = 1.0
            if ref_min > 0 and now < until:
                left = max(0.0, (until - now) / (ref_min * 60.0))
                damping = 1.0 - left
                gain *= damping
            new = round(max(-1.0, min(1.0, old + gain)), 4)
            item["phasic"] = new
            item["last_update"] = now
            if ref_min > 0 and abs(gain) > 1e-6:
                item["refractory_until"] = now + ref_min * 60.0
            item["last_pulse_ts"] = now
            lvl_after = self._compose_view(name)
            self._dirty = True
            out = {"ok": True, "kind": "modulator", "name": name,
                   "old": round(old, 4), "new": new,
                   "delta": round(new - old, 4),
                   "raw_delta": round(float(delta) * sens, 4),
                   "refractory_left": round(left, 3),
                   "refractory_damping": round(damping, 3),
                   "momentum_state": round(float(item.get("momentum_state") or 0.0), 4),
                   "tonic": item.get("tonic"), "level": lvl_after,
                   "level_old": lvl_before, "phasic": new,
                   "pending_rise": round(float(item.get("pending_rise") or 0.0), 4),
                   "reason": reason}
        # phasic 图通道注入（P6）：事件点亮镜像节点，幅度进返回值与轨迹
        inj = self._graph_pulse(name, abs(new))
        out["graph_pulse"] = inj
        self._trace_modulation(name, reason, source, tonic_delta=0.0,
                               phasic_delta=out["delta"], lvl0=lvl_before,
                               lvl1=out["level"],
                               damping=out["refractory_damping"],
                               graph=inj)
        return out

    def _graph_pulse(self, name: str, magnitude: float):
        """phasic → 图谱激活注入（R2 P6，用户批准的设计决定 D2 的另一半）。

        通道分工：**tonic** 走 `hormone.*` → ModulationLayer → 认知场参数（慢、连续）；
        **phasic** 走这里，把调制器节点本身点亮，让扩散按**边上的符号**去兴奋/抑制
        下游。节点激活只有幅度没有符号（Node 构造 `max(0.0, activation)` 实测夹死），
        方向是图拓扑的职责——所以正负脉冲都点亮，扩散走向由出边权重决定。

        只在**事件**里调用（pulse 是事件入口，tick_decay 不调），量纲与 Drive 一致
        （level×5 上限 5.0）。每拍补激活＝外置能量泵，正是 09-13 全图饱和事故的形状
        （记忆 [[diffusion-energy-conservation]]），故此处刻意不开 tick 通路。
        返回注入描述 dict 或 None（关闭/无引擎/低于阈值/无节点）。
        """
        blk = self._mods_cfg or {}
        if (not blk.get("graph_pulse", False) or self.kg is None
                or self.engine is None or magnitude <= 0):
            return None
        # 没有引擎就不写激活：单写 activation 而不进 frontier，等于把能量留在
        # 节点上等下一轮"来历不明的发射"（能量守恒事故的另一个形状）。
        thr = float(blk.get("graph_pulse_threshold") or 0.0)
        if magnitude < thr:
            return None
        scale = float(blk.get("graph_pulse_scale") or 5.0)
        node_id = self.mirror_modulator.get(name)
        node = self.kg.get_node(node_id) if node_id else None
        if node is None:
            return None
        amt = min(5.0, max(0.0, magnitude * scale))
        before = float(node.activation or 0.0)
        node.activation = max(before, amt)     # 同一轮多次脉冲取最强，不相加
        node.touch()
        self.engine.mark_active([node.id])
        # 外来的事件注入：本轮发射免扣（同 Drive/感知语义），否则调制器
        # 自己为"被点亮"付能量，扩散一步就熄灭。
        self.engine.register_activation_source(
            [node.id], blk.get("graph_pulse_source") or "modulator")
        return {"node": node.id, "amount": round(amt, 3), "old": round(before, 3)}

    def _trace_graph_pulse(self, name: str, event, source: str, inj: dict):
        """未拆快通道分支的图注入轨迹（拆分分支由 _trace_modulation 一并打印）。"""
        logger.info(f"[MODULATION] mod={name} event={str(event)[:60]} "
                    f"source={source} 图激活={inj['node']}:{inj['amount']:+.2f}")

    def _trace_modulation(self, name: str, event, source: str, *,
                          tonic_delta: float, phasic_delta: float,
                          lvl0: float, lvl1: float,
                          damping: float = 1.0, graph: dict = None):
        """统一的事件轨迹（任务规范 §22 的格式）。

        `damp` 是**实际施加的增益比例**：1.00=全额生效（不在不应期），0.00=完全打不动。
        早期版本打印的是"不应期剩余比例"，于是 `refr=1.00` 同时表示"不在不应期"和
        "刚进不应期"，读日志会反着理解——换名换语义修掉。
        `图激活=` 尾段是 P6 的**另一条通道**：这次脉冲在图上点亮了谁、点亮多少。
        没有它，"激素到底有没有参与激活扩散"在日志里是不可见的（审计 P1 的答案是"没有"）。
        """
        tail = (f" 图激活={graph['node']}:{graph['old']:.2f}→{graph['amount']:.2f}"
                if graph else "")
        logger.info(f"[MODULATION] mod={name} event={str(event)[:60]} "
                    f"source={source} tonic:{tonic_delta:+.3f} "
                    f"phasic:{phasic_delta:+.3f} level:{lvl0:.2f}→{lvl1:.2f} "
                    f"damp:{damping:.2f}{tail}")

    def dopamine_phasic(self) -> float:
        with self._lock:
            return float((self._modulators.get("dopamine") or {}).get("phasic") or 0.0)

    def pulse_dopamine(self, delta: float, cycle_id: str = None,
                       reason: str = "") -> dict:
        """多巴胺 phasic 脉冲（RPE 驱动）——R2 起就是 pulse() 的一个具名调用。

        保留这个名字是因为 reward.py/测试都在用它，且"多巴胺=RPE 脉冲"是有独立
        语义的事件名；实现上不再有任何多巴胺专属代码（推广见 §四：所有调制器共用
        同一条 sensitivity / momentum / refractory / 合成 通路）。
        旧规格（config 缺段）下 sensitivity=1、momentum=0、refractory=0，
        数值与 R2 之前逐字一致。
        """
        return self.pulse("dopamine", delta, cycle_id=cycle_id, reason=reason,
                          source="reward")

    def tick_decay(self, now: float = None) -> int:
        """时间动力学（§13）：一条 tick 里依次做四件事，全部落到同一写入口。

        1. **phasic 回 0**：每个调制器自己的 phasic_decay_per_min（脉冲性质）；
        2. **上升吸收**：把 rise_per_min 夹速攒下的 pending_rise 按速率注回 tonic
           （持续事件压力确实会抬高水位，但要花时间）；
        3. **习惯化恢复**：momentum_state 向 0 退（事件停了呢就恢复敏感）；
        4. **tonic 回 baseline**：按各自 decay_per_min（没有两个调制器同速，§8）。

        数值落地仍走 _write_numeric（唯一入口，I1/I3），衰减历史合并成一条
        （不挤掉真实事件，审计 D-6）。返回"动过的调制器数"。
        """
        now = self._now() if now is None else now
        merge_s = max(0.0, float(self._mods_cfg.get("decay_history_merge_min", 5.0)
                                 or 0)) * 60.0
        moved = 0
        with self._lock:
            for name, item in self._modulators.items():
                last = item.get("last_update")
                dt_min = (now - float(last)) / 60.0 if last else 0.0
                if dt_min <= 0:
                    item["last_update"] = now
                    continue
                item["last_update"] = now
                # ① phasic：分钟级衰减回 0；每个调制器可不同
                pdec = item.get("phasic_decay_per_min")
                if pdec is None:
                    pdec = self.PHASIC_DECAY_PER_MIN
                ph = float(item.get("phasic") or 0.0)
                if ph and float(pdec) > 0:
                    new_ph = ph * max(0.0, 1.0 - float(pdec) * dt_min)
                    item["phasic"] = round(new_ph, 4) if abs(new_ph) >= 1e-3 else 0.0
                    moved += 1
                # ② 上升吸收：pending_rise → tonic（slew=False，夹速只管事件）
                pend = float(item.get("pending_rise") or 0.0)
                rpm = item.get("rise_per_min")
                cur = float(item.get("tonic", 0.0))
                base = float(item.get("baseline", 0.0))
                rate = float(item.get("decay_per_min", 0.0) or 0.0)
                if pend > 1e-6 and rpm is not None:
                    flow = min(pend, float(rpm) * dt_min)
                    if flow > 1e-6:
                        item["pending_rise"] = round(pend - flow, 6)
                        self._write_numeric("modulator", name, cur + flow,
                                            reason="上升余量吸收", source="rise",
                                            merge_window=merge_s, stamp=now,
                                            slew=False)
                        moved += 1
                        cur = float(item.get("tonic", cur))
                else:
                    item["pending_rise"] = 0.0 if pend <= 1e-6 else pend
                # ③ 习惯化恢复
                mv = float(item.get("momentum_state") or 0.0)
                if mv:
                    item["momentum_state"] = round(
                        mv * max(0.0, 1.0 - MOMENTUM_DECAY_PER_MIN * dt_min), 6)
                # ④ tonic：向 baseline 回归（仅当显著偏离，避免每拍空写）
                if rate > 0 and abs(cur - base) > 0.02:
                    target = cur + (base - cur) * min(0.9, rate * dt_min)
                    self._write_numeric("modulator", name, target,
                                        reason="自然衰减回基线", source="decay",
                                        merge_window=merge_s, stamp=now)
                    moved += 1
                self._compose_view(name)
        if moved:
            self._dirty = True
        return moved

    def modulator_dynamics(self, name: str) -> dict:
        """单个调制器的动力学读数（实验台 / trace 用；纯读，不改状态）。"""
        item = self._modulators.get(name)
        if not item:
            return {"ok": False, "error": f"未知 modulator: {name}"}
        base = float(item.get("baseline", 0.0))
        return {"ok": True, "name": name,
                "conc": round(self.modulator_conc(name), 4),
                "tonic_response": round(self.modulator_tonic(name), 4),
                "level": round(self.modulator_level(name), 4),
                "phasic": round(self.modulator_pulse(name), 4),
                "dev": round(self.modulator_dev(name), 4),
                "pending_rise": round(float(item.get("pending_rise") or 0.0), 4),
                "momentum_state": round(float(item.get("momentum_state") or 0.0), 4),
                "refractory_left_s": max(0.0, round(
                    float(item.get("refractory_until") or 0.0) - self._now(), 1)),
                "overloaded": abs(self.modulator_conc(name) - base) >= max(
                    0.0, float(item.get("overload", 1.0) or 1.0) - base),
                "curve": {"saturation": item.get("saturation"),
                          "overload": item.get("overload"),
                          "pull": item.get("overload_pull"),
                          "pull_coefficient": round(self._pull_coefficient(name, item), 4),
                          "phasic_to_level": item.get("phasic_to_level")}}

    def dump_modulator_state(self, top: int = None) -> dict:
        """可观测性（§22）：谁在起作用、被什么事件推动、处在曲线的哪一段。

        `top=None`（默认）= 全部调制器：R2 有 12 个，按 |dev| 截到 8 会把 1/3 藏起来，
        而"隐藏的那几个是不是没在工作"正是这个函数要回答的问题。显式给整数才截断。
        """
        with self._lock:
            rows = []
            for name, item in self._modulators.items():
                dev = self.modulator_dev(name)          # 参数通道看到的量（已曲线化）
                base = float(item.get("baseline", 0.5))
                conc_dev = self.modulator_conc(name) - base   # 未曲线化的浓度偏差
                rows.append({
                    "name": name, "mirror": self.mirror_modulator.get(name),
                    "category": item.get("category"),
                    "level": round(self.modulator_level(name), 4),
                    "conc": round(self.modulator_conc(name), 4),
                    "tonic": round(self.modulator_tonic(name), 4),
                    "phasic": (None if item.get("phasic") is None
                               else round(float(item["phasic"]), 4)),
                    "dev": round(dev, 4), "|dev|": round(abs(dev), 4),
                    "pending_rise": round(float(item.get("pending_rise") or 0.0), 4),
                    "momentum_state": round(
                        float(item.get("momentum_state") or 0.0), 4),
                    # 过载按**浓度**判（曲线之后的响应已经回落，不能再拿它判过载）
                    "overload": abs(conc_dev) >= max(
                        0.0, float(item.get("overload", 1.0) or 1.0) - base),
                    "saturated": abs(conc_dev) >= max(
                        0.0, float(item.get("saturation", 1.0) or 1.0) - base),
                    "refractory_s": max(0.0, round(
                        float(item.get("refractory_until") or 0.0) - self._now(), 1)),
                    "sources": list(item.get("source_detail") or [])[:3],
                })
            rows.sort(key=lambda r: -r["|dev|"])
            shown = rows if top is None else rows[:int(top)]
            out = {"modulators": shown,
                   "count": len(rows),
                   "shown": len(shown),
                   "active_count": sum(1 for r in rows if r["|dev|"] >= 0.05)}
            # R2 P11 收口、P12 补上：包络也在这张观测面上，但**只报投影**——
            # 心情的数值真源在 persona（`_mood_valence`），这里出现"心情总量"
            # 就会造出第二个落点（禁令 11 的精神，附一一 §2）。
            try:
                if self.kg is not None:
                    import modulator_subgraph as _MS
                    out["mood_envelope"] = _MS.mood_projection(
                        self.kg, self, self.config)
            except Exception:                       # noqa: BLE001
                pass
            return out
