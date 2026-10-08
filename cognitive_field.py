# cognitive_field.py — Cognitive Field: Tension→Drive→Network→Modulation 装配
# ============================================================================
# Drive 重构（2026-09-20）的四层动力学装配根。
#
# 每拍 step()（CC 周期 ~10s / 回合内 settle）顺序推进：
#   TensionField → DriveField → NetworkField → signals 组装 → ModulationLayer
# 四层互不知道对方的存在，全部通过本模块传递数据——新增一层（如 Salience
# Network 直接接进 signals）不推翻任何现有结构。
#
# 本模块刻意不触碰图谱：写回 Drive 节点、marker 节点入图是调用方
# （DriveEvaluator 兼容壳）的职责——动力学核心可离线单测。
#
# 兼容与降级：所有 config 段可缺省（内置默认）；采样器缺位/异常按 0 参与；
# 任何一层失败都不让上层抛出（CC 循环与回合管线不得因内驱系统崩溃）。
# ============================================================================

import json
import logging
import os
import threading
import time

from tension_field import TensionField
from drive_field import DriveField
from cognitive_networks import NetworkField
from modulation import ModulationLayer

logger = logging.getLogger(__name__)

# ── 内置默认（config 同名片段可覆盖/合并；新增张力/驱动/网络/参数
#    优先改 config，这里只是"config 全缺"时的系统最低可运行形态）──

DEFAULT_TENSIONS = {
    "discrepancy":        {"components": [{"source": "graph.discrepancy", "coef": 1.0}], "rise": 0.55, "fall": 0.10},
    "novelty":            {"components": [{"source": "novel_objects", "coef": 0.5, "scale": 0.33},
                                           {"source": "graph.unknown_objects", "coef": 0.6, "scale": 0.25}], "rise": 0.55, "fall": 0.10},
    "unresolved_interest": {"components": [{"source": "graph.interest", "coef": 1.0}], "rise": 0.40, "fall": 0.05},
    "satiation":          {"components": [{"source": "graph.satiation", "coef": 1.0}], "rise": 0.50, "fall": 0.12},
    "prediction_error":   {"components": [{"source": "prediction_surprise", "coef": 1.0}], "rise": 0.60, "fall": 0.10},
    "blocked_action":     {"components": [{"source": "causal_blockers", "coef": 0.7, "scale": 0.33}], "rise": 0.50, "fall": 0.08},
    "unfinished_goal":    {"components": [{"source": "recent_goal_failures", "coef": 0.6, "scale": 0.33},
                                           {"source": "graph.pending_inquiry", "coef": 0.4}], "rise": 0.50, "fall": 0.06},
    "social_absence":     {"components": [{"source": "social_idle_minutes", "coef": 1.0, "scale": 0.033}], "rise": 0.20, "fall": 0.15},
    # 刺激剥夺（2026-09-24）：距最近一次新体验的分钟数——与 social_absence
    # 同构的通用时钟（不是 MC 特判、不是逐物答案）："闲得发慌"应当自己
    # 长成想动的理由，而不是等她无聊到被用户发现。30 分钟封顶。
    "stimulation_hunger": {"components": [{"source": "stimulation_idle_minutes", "coef": 1.0, "scale": 0.033}], "rise": 0.15, "fall": 0.30},
    "belonging_need":     {"components": [{"source": "social_need", "coef": 1.0}], "rise": 0.35, "fall": 0.10},
    "social_presence":    {"components": [{"source": "players_present", "coef": 1.0}], "rise": 0.50, "fall": 0.20},
    "negative_experience": {"components": [{"source": "recent_negative_reward_ratio", "coef": 1.0}], "rise": 0.40, "fall": 0.08},
    "mood_low":           {"components": [{"source": "state.mood_deficit", "coef": 1.0}], "rise": 0.30, "fall": 0.08},
    "self_contradiction": {"components": [{"source": "pending_reflection_candidates", "coef": 1.0, "scale": 0.25}], "rise": 0.40, "fall": 0.06},
    "capability_gap":     {"components": [{"source": "competence_need", "coef": 0.7},
                                           {"source": "graph.capability_gap",
                                            "coef": 0.5}], "rise": 0.35, "fall": 0.10},
    "exploration_gap":    {"components": [{"source": "graph.exploration_gap",
                                            "coef": 1.0}], "rise": 0.45, "fall": 0.10},
    "unexpected_event":   {"components": [{"source": "graph.cognitive_events", "coef": 1.0}], "rise": 0.70, "fall": 0.20},
    "uncertainty":        {"components": [{"source": "graph.uncertainty", "coef": 1.0}], "rise": 0.40, "fall": 0.10},
}

# 旧四驱保留为高层语义标签：节点名/输出键不变（兼容契约），
# 底层换成张力亲和度聚合。affinities 权重≈旧公式各项占比。
DEFAULT_DRIVES = {
    "curiosity": {
        "node": "CuriosityDrive", "rise": 0.50, "fall": 0.06,
        "description": "想进一步了解未知事物的探索驱动力"
                       "（提问/搜索/观察都只是它的可能出口）",
        "affinities": {"discrepancy": 0.34, "novelty": 0.18,
                       "unresolved_interest": 0.22, "uncertainty": 0.10,
                       "prediction_error": 0.08, "unexpected_event": 0.08,
                       # P3/§5：探索缺口喂好奇——"有东西没搞懂"是持续张力
                       "exploration_gap": 0.15,
                       # 刺激剥夺：好久没有新体验本身就在制造好奇（2026-09-24）
                       "stimulation_hunger": 0.15,
                       "satiation": -0.35},
    },
    "social": {
        "node": "SocialDrive", "rise": 0.30, "fall": 0.08,
        "description": "社交联结驱动力 — 互动间隔/归属需求/同伴在场",
        "affinities": {"social_absence": 0.45, "belonging_need": 0.30,
                       "social_presence": 0.25},
    },
    "learning": {
        "node": "LearningDrive", "rise": 0.35, "fall": 0.07,
        "description": "学习驱动力 — 能力缺口/未竟目标/受阻行动/预测误差",
        "affinities": {"unfinished_goal": 0.28, "blocked_action": 0.24,
                       "capability_gap": 0.22, "novelty": 0.14,
                       "prediction_error": 0.12,
                       # P3：把"用途未明"也记为学习议题（想搞懂=想学）
                       "exploration_gap": 0.10},
    },
    "consistency": {
        "node": "ConsistencyDrive", "rise": 0.28, "fall": 0.05,
        "description": "一致性驱动力 — 未消化的经历/负性积累/自我矛盾",
        "affinities": {"self_contradiction": 0.36, "negative_experience": 0.26,
                       "mood_low": 0.20, "prediction_error": 0.18},
    },
    "_lateral": {},   # 侧向软竞争表（默认 0 = 各驱独立；config 可加）
}

DEFAULT_NETWORKS = {
    # CEN 任务脱离慢（fall 小=惯性），捕获快（rise 大）；slew 限幅防每秒翻转。
    "CEN": {
        "node": "CENetwork",     # 图上网络标记节点（R2 P9：调制器→网络 的边按它认领落点）
        "inputs": {"base": 0.10, "context.task_engaged": 0.45,
                   "tension.unfinished_goal": 0.25,
                   "tension.blocked_action": 0.18,
                   "tension.discrepancy": 0.05,
                   "drive.learning": 0.15,
                   "tension.social_absence": -0.10,
                   "context.time_since_task": -0.25},
        "rise": 0.30, "fall": 0.10, "slew": 0.12,
    },
    "DMN": {
        "node": "DMNetwork",
        "inputs": {"base": 0.35, "context.task_engaged": -0.55,
                   "context.time_since_task": 0.35,
                   "tension.social_absence": 0.20,
                   "tension.negative_experience": 0.15,
                   "tension.mood_low": 0.10,
                   "tension.self_contradiction": 0.20,
                   "tension.novelty": 0.20,
                   "tension.unresolved_interest": 0.25,
                   "tension.unfinished_goal": -0.10,
                   "tension.blocked_action": -0.05,
                   "drive.curiosity": 0.10},
        "rise": 0.12, "fall": 0.22, "slew": 0.08,
    },
    "_lateral": {"CEN": {"DMN": 0.25}, "DMN": {"CEN": 0.20}},
}

# 出厂"调制器→动力学"耦合表的**模块内兜底**（正式声明处仍是
# config.modulator_system.rate_gains，装配时按段深合并、config 覆盖这里）。
# 为什么要有这份兜底：本模块的既有契约是"裸装配 = 生产形态"
# （`CognitiveField(config={})` 必须与 R2 之前逐字一致，见 _normalize_mod_specs
# 的注释与 tests/test_cognitive_field.py 的"调制器语义"用例）。R2 P9 把这张表
# 从 step() 里的硬编码搬进 config 之后，兜底若不同步搬进来，config={} 的离线
# 装配就会连耦合表一起丢掉——那是搬迁引入的回归，不是设计意图。
DEFAULT_RATE_GAINS = {
    "tension": {"*": {"cortisol": 0.30}},
    "drive": {
        "curiosity.rise": {"dopamine": 0.50},
        "curiosity.fall": {"dopamine": -0.40},
        "learning.rise": {"dopamine": 0.50},
    },
    "affinity": {
        "social.belonging_need": {"oxytocin": 0.60},
        "social.social_presence": {"oxytocin": 0.40},
    },
}

DEFAULT_MODULATION = {
    "smoothing_alpha": 0.30,
    "params": {
        # 扩散：CEN 深而窄，DMN 浅而广？——不，按现有架构语义：
        # CEN 压深度/抬阈值（聚焦当下任务相关），DMN 抬深度与配比（联想扩散）。
        "diffusion.max_depth": {"baseline": 3.0, "min": 1.0, "max": 6.0,
                                 "effects": {"network.CEN": -0.40, "network.DMN": 0.60}},
        "diffusion.emission_ratio": {"baseline": 0.50, "min": 0.20, "max": 0.80,
                                      "effects": {"network.DMN": 0.30, "network.CEN": -0.25,
                                                   "hormone.dopamine": 0.80,
                                                   "tension.novelty": 0.15}},
        "diffusion.min_spread": {"baseline": 0.05, "min": 0.02, "max": 0.15,
                                  "effects": {"network.CEN": 0.60, "network.DMN": -0.40}},
        # 语义：factor 是衰减量（keep=1−factor，越大忘得越狠）。CEN 保留任务
        # 语境（−），DMN 联想场快速腾位（+），压力（皮质醇）加速清空（+）。
        "diffusion.inter_round_decay": {"baseline": 0.75, "min": 0.30,
                                         "max": 0.95,
                                         "effects": {"network.CEN": -0.20,
                                                      "network.DMN": 0.10,
                                                      "hormone.cortisol": 0.40}},
        # 既有 update_param_modulation（arousal/stress→发射增益）收编为一条参数
        "diffusion.param_gain": {"baseline": 1.0, "min": 0.7, "max": 1.6,
                                  "effects": {"context.arousal": 0.30,
                                               "context.stress": -0.15},
                                  "alpha": 1.0},
        # 工作记忆宽度（MODE_CONTEXT_BUDGET 的兑现通道）
        "attention.width_scale": {"baseline": 1.0, "min": 0.6, "max": 1.6,
                                   "effects": {"network.DMN": 0.50, "network.CEN": -0.20,
                                                 "drive.curiosity": 0.20}},
        # 检索
        "retrieval.topk_scale": {"baseline": 1.0, "min": 0.5, "max": 2.0,
                                  "effects": {"network.DMN": 0.60,
                                               "tension.negative_experience": 0.20}},
        "retrieval.reignite_every": {"baseline": 3.0, "min": 2.0, "max": 6.0,
                                      "effects": {"network.DMN": -0.35,
                                                   "network.CEN": 0.45}},
        # LLM：温度/发散许可/mode 微调/预算软系数
        "llm.temperature_answer": {"baseline": 0.70, "min": 0.30, "max": 1.10,
                                    "effects": {"network.DMN": 0.35, "network.CEN": -0.25}},
        "llm.temperature_expand": {"baseline": 0.35, "min": 0.10, "max": 0.90,
                                    "effects": {"network.DMN": 0.45, "network.CEN": -0.30}},
        "llm.mode_nudge": {"baseline": 0.0, "min": -1.0, "max": 1.0,
                            "effects": {"network.DMN": 0.55, "network.CEN": -0.45,
                                         "tension.self_contradiction": 0.30,
                                         "drive.consistency": 0.20}},
        "llm.budget_factor": {"baseline": 1.0, "min": 0.6, "max": 1.4,
                               "effects": {"context.task_engaged": 0.15,
                                            "network.CEN": 0.20,
                                            "network.DMN": -0.15}},
        # 行为竞争
        "behavior.explore_margin": {"baseline": 0.15, "min": 0.0, "max": 0.40,
                                     "effects": {"network.CEN": 0.40,
                                                  "network.DMN": -0.20,
                                                  "drive.curiosity": -0.30}},
        "behavior.exploration_rate": {"baseline": 1.0, "min": 0.4, "max": 1.8,
                                       "effects": {"drive.curiosity": 0.40,
                                                    "network.DMN": 0.30,
                                                    "tension.novelty": 0.20,
                                                    "network.CEN": -0.20}},
        # 行动空间（2026-09-20 行动重构）：候选产生与筛选的网络调制——
        # DMN 高→Top-K 更宽（候选更多样）；CEN 高→已学倾向边放大（聚焦）。
        "action_space.candidate_topk_scale": {
            "baseline": 1.0, "min": 0.6, "max": 1.8,
            "effects": {"network.DMN": 0.50, "network.CEN": -0.20,
                         "tension.novelty": 0.20}},
        "action_space.affordance_gain": {
            "baseline": 1.0, "min": 0.6, "max": 1.6,
            "effects": {"network.CEN": 0.40, "network.DMN": -0.25}},
        # ── 认知环阈值调制（2026-09-20 统一认知循环）────────────
        # 阈值不是 magic number 一刀切：Drive/网络/激素经 effects 连续移动。
        # 高 curiosity → 探索/念头更易形成（form 降）；CEN 高 → 行动更易
        # （action 阈值降）；催产素/社交需求 → 更易开口（express 降）；
        # 皮质醇/压力 → 全线收紧。DMN 高 → 认知场更易"点火"。
        "cognition.form_threshold": {
            "baseline": 0.55, "min": 0.35, "max": 0.75,
            "effects": {"drive.curiosity": -0.12, "network.DMN": -0.10,
                         "drive.social": -0.08, "network.CEN": 0.08,
                         "hormone.cortisol": 0.15,
                         "context.fatigue": 0.20}},
        "cognition.express_threshold": {
            "baseline": 0.78, "min": 0.55, "max": 0.95,
            "effects": {"need.social": -0.10, "hormone.oxytocin": -0.12,
                         "drive.social": -0.08, "hormone.cortisol": 0.10,
                         "context.recent_expression": 0.28}},
        "action.score_threshold": {
            "baseline": 0.30, "min": 0.18, "max": 0.50,
            "effects": {"network.CEN": -0.12, "drive.curiosity": -0.06,
                         "hormone.dopamine": -0.08, "hormone.cortisol": 0.10,
                         "context.task_engaged": -0.05,
                         "context.user_recent": 0.15}},
        "reactivation.cold_field": {
            "baseline": 0.35, "min": 0.20, "max": 0.60,
            "effects": {"network.DMN": 0.20, "network.CEN": -0.15}},
        "reactivation.boost_scale": {
            "baseline": 1.0, "min": 0.6, "max": 1.6,
            "effects": {"network.DMN": 0.35, "tension.novelty": 0.15,
                         "network.CEN": -0.15}},
    },
}

DEFAULT_TENDENCIES = {
    "continue_task": {"base": 0.0, "network.CEN": 0.40,
                       "drive.learning": 0.25,
                       "tension.unfinished_goal": 0.25,
                       "tension.blocked_action": 0.15,
                       "context.task_engaged": 0.10},
    "explore": {"base": 0.0, "drive.curiosity": 0.45, "network.DMN": 0.20,
                 "tension.novelty": 0.20, "tension.discrepancy": 0.15,
                 "network.CEN": -0.15},
    "ask_user": {"base": 0.0, "drive.social": 0.40,
                  "tension.social_absence": 0.20,
                  "tension.unresolved_interest": 0.25,
                  "network.DMN": 0.10, "context.player_near": 0.10},
    "reflect": {"base": 0.0, "drive.consistency": 0.45,
                 "tension.self_contradiction": 0.20,
                 "network.DMN": 0.25, "context.idle_minutes": 0.10},
}


# 调制器→动力学的增益钳位（R2 P9）：`gain` 是**乘数**，钳在 [0.2, 3.0] 内
# 意味着"最快也只比出厂值快/慢 3 倍 / 5 倍"，不会把某个速率压成 0（那等于
# 把该维度冻结，是比失焦更糟的失效模式）。
GAIN_MIN, GAIN_MAX = 0.2, 3.0


def _merged(base: dict, override: dict) -> dict:
    out = {k: dict(v) for k, v in (base or {}).items()}
    for k, v in (override or {}).items():
        m = dict(out.get(k, {}))
        m.update(v or {})
        out[k] = m
    return out


class CognitiveField:
    """四层动力学的装配与编排。

    samplers/context_providers 由装配方（DriveEvaluator 壳 / app 接线）注册：
      register_sampler(name, fn)        — 张力的原始读数源
      register_context(name, fn)        — 网络/调制的即时语境读数（task_engaged 等）
      register_state(name, fn)          — 激素等内部状态读数（进 hormone.* 信号，
                                           以基线偏移形式，见 baseline 参数）
    """

    def __init__(self, config: dict = None, state_file: str = None):
        cfg = config or {}
        cf = cfg.get("cognitive_field") or {}
        self._enabled = cf.get("enabled", True)
        mod_cfg = cfg.get("modulation") or {}
        self.tensions = TensionField(
            _merged(DEFAULT_TENSIONS, cfg.get("tension_sources")),
            samplers={})
        self.drives = DriveField(
            _merged(DEFAULT_DRIVES, cfg.get("drive_field", {}).get("drives")))
        self.networks = NetworkField(
            cfg.get("networks", {}).get("field"), defaults=DEFAULT_NETWORKS)
        self.modulation = ModulationLayer(
            _merged(DEFAULT_MODULATION["params"], mod_cfg.get("params")),
            smoothing_alpha=float(mod_cfg.get(
                "smoothing_alpha",
                DEFAULT_MODULATION["smoothing_alpha"])))
        self._tendency_specs = _merged(
            DEFAULT_TENDENCIES, cf.get("action_tendencies"))
        self._context = {}
        self._ctx_overrides = {}
        self._state_readers = {}
        # R2 P9（审计 D-4）：`need.*` 前缀在 effects 表里有行（express_threshold
        # 的 `need.social: -0.10`）但 `_build_signals` 从不生产它 ⇒ 恒乘 0。
        # 现在需求水位经注册表进信号空间；没注册的维度不进（不造平行状态）。
        self._need_readers = {}
        # 出厂"调制器→动力学"耦合表（config.modulator_system.rate_gains）：
        # 这是这类落点的**唯一声明处**，step() 按名字表通用展开（禁令 2）。
        # 段内**整行覆盖**（不是把调制器系数相加）：一行只有一个真值，
        # config 是那个真值；DEFAULT_RATE_GAINS 只在 config 没给该段时兜底。
        _gains_cfg = ((cfg.get("modulator_system") or {}).get(
            "rate_gains") or {})
        self._gain_table = {
            sec: {**DEFAULT_RATE_GAINS.get(sec, {}),
                  **(_gains_cfg.get(sec) or {})}
            for sec in set(DEFAULT_RATE_GAINS) | set(_gains_cfg)
        }
        # 图上的连续偏置（调制器→网络/驱动）：由装配方注入取值函数
        # （字段本身不认识 kg），每拍读一次。
        self._bias_source = None
        # 基线唯一真源 = config["modulator_system"]["specs"][*].baseline（R2）。
        # cf["hormone_baseline"] 保留为显式覆盖位（旧配置还能写）；都没给时
        # 才回退到 R2 之前的 4 条内置值。
        _specs = ((cfg.get("modulator_system") or {}).get("specs") or {})
        _base = {str(k): float((v or {}).get("baseline", 0.5))
                 for k, v in _specs.items()}
        _base.update(cf.get("hormone_baseline") or {})
        self._hormone_baseline = _base or {
            "dopamine": 0.5, "oxytocin": 0.4, "serotonin": 0.55,
            "cortisol": 0.3}
        self._lock = threading.RLock()
        self._step_count = 0
        self._last_saved = 0.0
        self._autosave_every = int(cf.get("autosave_steps", 24))
        # 时间闸兜底（2026-09-25 用户指令"驱动能量是持续变量"）：Windows 的
        # os.kill(SIGTERM)=TerminateProcess，atexit 不运行——按拍数自动存会
        # 丢掉最后一段。≤autosave_interval_s 必存一次，崩溃/硬杀最多丢这段。
        self._autosave_interval_s = max(10.0, float(cf.get("autosave_interval_s", 60)))
        self._state_file = state_file or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            cf.get("state_file", "data/tension_drive_state.json"))
        self._last_step_ts = time.time()
        self._resolve_baselines(cfg)
        self._tendencies = {k: 0.0 for k in self._tendency_specs}
        self._load_state()

    # ── 注册接口 ──────────────────────────────────────────

    def register_sampler(self, name, fn):
        self.tensions.register_sampler(name, fn)

    def register_context(self, name, fn):
        self._context[name] = fn

    def set_context_value(self, name, value):
        """直接覆写一个语境读数（CC 每拍喂 arousal/stress 用）。"""
        self._ctx_overrides[name] = float(value or 0.0)

    def register_state(self, name, fn):
        """激素等内部状态读数（float 或 (value, note)）。"""
        self._state_readers[name] = fn

    def register_need(self, name, fn):
        """需求水位读数（R2 P9）：进 `need.<name>` 信号空间。

        注册的是**闭包**（通常 `lambda: st.need_salience("social")`），
        所以字段仍然不认识 InternalState——与 sampler/context/state 同一套路。
        """
        self._need_readers[name] = fn

    def set_bias_source(self, fn):
        """注入图上偏置的取值函数：`fn() -> {"network": {...}, "drive": {...}}`
        （实现在 modulator_subgraph.graph_biases，装配方负责调）。"""
        self._bias_source = fn

    def node_names(self) -> dict:
        """本场所涉字段在图上的落点名：{"network": {节点id: 网络名}, "drive": {...}}。
        名册由**拥有字段的对象**给（NetworkField.node_ids / DriveField.node_id），
        所以图上的边改名与代码同步只有一处真相。"""
        net = self.networks.node_ids()
        drv = {}
        for name in self.drives.spec_names():
            nid = self.drives.node_id(name)
            if nid:
                drv[str(nid)] = name
        return {"network": net, "drive": drv}

    def _need_levels(self) -> dict:
        return {k: self._read(self._need_readers, k) for k in self._need_readers}

    def _gains(self, section: str, base: float = 1.0) -> dict:
        """把出厂表 `rate_gains.<section>` 展开成实际增益：
        `gain = clamp(base + Σ(coef × dev(调制器)), GAIN_MIN, GAIN_MAX)`。

        一张表管三种落点（张力速率 / 驱动速率 / 亲和增益）——它们唯一的区别是
        键的写法，公式相同 ⇒ 不在 step() 里为每种调制器写一段 if（禁令 2）。
        `dev` 来自 `_hormone_devs()`（受体曲线后的偏移，0=基线），所以过载
        反转在增益通道上同样成立。
        """
        horm = self._hormone_devs()
        out = {}
        for key, mods in (self._gain_table.get(section) or {}).items():
            g = base
            for mod, coef in (mods or {}).items():
                g += float(coef) * float(horm.get(mod, 0.0) or 0.0)
            out[key] = min(GAIN_MAX, max(GAIN_MIN, g))
        return out

    def _resolve_baselines(self, cfg):
        """modulation 参数可用 baseline_from 引用现有 config 值，
        避免两处维护同一默认数（装配时解析一次）。"""
        for name in list(self.modulation._specs.keys()):
            spec = self.modulation._specs[name]
            ref = spec.get("baseline_from")
            if not ref:
                continue
            cur, found = cfg, True
            for part in str(ref).split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    found = False
                    break
            if found and isinstance(cur, (int, float)):
                spec["baseline"] = float(cur)
                self.modulation._display[name] = float(cur)
                self.modulation._raw[name] = float(cur)

    # ── 每拍推进 ──────────────────────────────────────────

    def _read(self, readers, name, default=0.0):
        fn = readers.get(name)
        if fn is None:
            return default
        try:
            v = fn()
        except Exception:
            return default
        if isinstance(v, tuple):
            try:
                return float(v[0])
            except (TypeError, ValueError):
                return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _merged_context(self) -> dict:
        ctx = {}
        for k in self._context:
            ctx[k] = self._read(self._context, k)
        ctx.update(self._ctx_overrides)   # 直接覆写值优先（CC 喂入）
        return ctx

    def _hormone_devs(self) -> dict:
        devs = {}
        for name, baseline in self._hormone_baseline.items():
            if name not in self._state_readers:
                devs[name] = 0.0     # 缺读数 = 无调制，不造平行状态
            else:
                devs[name] = self._read(self._state_readers, name) - float(
                    baseline)
        return devs

    def refresh_modulation(self):
        """只重算调制层（不动张力/驱动/网络状态）。CC 每拍喂
        arousal/stress 后调用 → 参数粒度到 tick，场状态粒度到 step。"""
        with self._lock:
            signals = self._build_signals(self._merged_context(),
                                          self._hormone_devs(),
                                          self.networks.levels())
            self.modulation.compute(signals)

    def step(self, settle: bool = False) -> dict:
        """推进全场一次。settle=True：回合内强制读数（各场一步到位，
        行为=旧的无状态映射）；周期拍用动力学（连续性/迟滞在此）。"""
        with self._lock:
            now = time.time()
            dt = max(0.0, now - self._last_step_ts)
            self._last_step_ts = now

            # ── 调制器 → 动力学（R2 P9：全部来自出厂表 + 图上的边，无按名分支）──
            #
            # 三条通道，语义各不相同，所以留着三条而不是糊成一条：
            #   rate_gains  改**惯性**（rise/fall 速率）→ 只改过渡快慢，稳态不变
            #   affinity    改**张力权重**（该张力在这一驱里值多少）→ 需有张力在场
            #   bias        改**稳态水位**（图上的 调制器→网络/驱动 边）→ 持续性偏置
            # 出厂表在 config.modulator_system.rate_gains；偏置在图上（装配方注入
            # 取值函数，见 set_bias_source）。这里只剩"把表乘开"的通用循环。
            horm = self._hormone_devs()
            rate_gains_t = self._gains("tension")
            rate_gains_d = self._gains("drive")
            affinity_gain = self._gains("affinity", base=0.0)
            # 网络速率：出厂表没有 "network" 段 ⇒ 恒等 {"*": 1.0}。
            # 这一格为什么空着（审计过的问题，答案是"位置对但通道不对"）：
            # rate 只改过渡速度，稳态由 target 决定，所以"压力压走神"这类
            # **持续**偏置放这儿无效——它属于 bias（图上那条
            # `皮质醇样-[抑制]->DMNetwork`）。表留着是接入点，不是遗留。
            rate_gains_n = self._gains("network") or {"*": 1.0}
            bias = self._biases()
            t_g = float(rate_gains_t.get("*", 1.0) or 1.0)

            t_levels = self.tensions.step(settle=settle,
                                           rate_gain_rise=t_g,
                                           rate_gain_fall=t_g)
            d_levels = self.drives.step(t_levels, settle=settle,
                                         rate_gains=rate_gains_d,
                                         affinity_gain=affinity_gain,
                                         bias=bias.get("drive"))
            ctx = self._merged_context()
            n_levels = self.networks.step(t_levels, d_levels, context=ctx,
                                           settle=settle,
                                           rate_gains=rate_gains_n,
                                           bias=bias.get("network"))
            signals = self._build_signals(ctx, horm, n_levels)
            self.modulation.compute(signals)
            self._compute_tendencies(t_levels, d_levels, n_levels)
            self._step_count += 1
            if (self._step_count % max(2, self._autosave_every) == 0
                    or time.time() - self._last_saved > self._autosave_interval_s):
                self.save_state()
            return {"tensions": t_levels, "drives": d_levels,
                    "networks": n_levels}

    def _biases(self) -> dict:
        """图上的稳态偏置（`调制器→网络/驱动` 的边，装配方注入取值函数）。

        未接线或取值失败 ⇒ 返回空 ⇒ 本拍没有任何偏置，行为等于 R2 之前。
        这里**不用默认值顶替**：偏置是图给的观点，没有图就没有观点。
        """
        fn = self._bias_source
        if fn is None:
            return {}
        try:
            b = fn()
        except Exception:
            return {}
        return b if isinstance(b, dict) else {}

    def _build_signals(self, ctx, horm, nets) -> dict:
        sig = {"network." + k: v for k, v in nets.items()}
        for k, v in self.tensions.levels().items():
            sig["tension." + k] = v
        for k, v in self.drives.levels().items():
            sig["drive." + k] = v
        for k, v in ctx.items():
            sig["context." + k] = v
        for k, v in self._need_levels().items():   # R2 P9：补上 D-4 的缺产端
            sig["need." + k] = v
        for k, dev in horm.items():          # 基线偏移，0 = 无调制
            sig["hormone." + k] = dev
        return sig

    def _compute_tendencies(self, tensions, drives, nets):
        out = {}
        for tname, spec in self._tendency_specs.items():
            s = float((spec or {}).get("base", 0.0))
            for key, w in (spec or {}).items():
                if key == "base":
                    continue
                kind, _, name = key.partition(".")
                if kind == "tension":
                    v = tensions.get(name, 0.0)
                elif kind == "drive":
                    v = drives.get(name, 0.0)
                elif kind == "network":
                    v = nets.get(name, 0.0)
                else:
                    v = self._read(self._context, name)
                s += float(w) * v
            out[tname] = round(max(0.0, min(1.0, s)), 4)
        with self._lock:
            self._tendencies = out

    def action_tendencies(self) -> dict:
        with self._lock:
            return dict(self._tendencies)

    # ── 观测 ──────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return bool(self._enabled)

    def snapshot(self) -> dict:
        return {
            "tensions": {k: v["level"] for k, v in self.tensions.snapshot().items()},
            "tension_detail": self.tensions.snapshot(),
            "drives": self.drives.snapshot(),
            "networks": self.networks.levels(),
            "modulation": self.modulation.snapshot(),
            "action_tendencies": self.action_tendencies(),
            "step": self._step_count,
        }

    def explain_compact(self) -> str:
        """单行因果快照（回合日志）：张力→驱动→网络→关键参数。"""
        t = self.tensions.levels()
        d = self.drives.snapshot()
        n = self.networks.levels()
        m = self.modulation
        top_t = sorted(((v, k) for k, v in t.items() if v > 0.01), reverse=True)[:3]
        top_d = sorted(((v["level"], k) for k, v in d.items()), reverse=True)[:3]
        parts = []
        if top_t:
            parts.append("tension=" + ",".join(f"{k}:{v:.2f}" for v, k in top_t))
        if top_d:
            parts.append("drive=" + ",".join(f"{k}:{v:.2f}" for v, k in top_d))
        parts.append("CEN=%.2f DMN=%.2f" % (n.get("CEN", 0.0), n.get("DMN", 0.0)))
        parts.append("depth=%d ratio=%.2f temp=%.2f bf=%.2f nudge=%+d" % (
            m.get_int("diffusion.max_depth", 3),
            m.get("diffusion.emission_ratio", 0.5),
            m.get("llm.temperature_answer", 0.7),
            m.get("llm.budget_factor", 1.0),
            round(m.get("llm.mode_nudge", 0.0))))
        return " | ".join(parts)

    def explain(self) -> str:
        """§十八 格式的人类可读快照（日志/前端调试用）。"""
        snap = self.snapshot()
        lines = ["Current Tensions:"]
        for k, v in sorted(snap["tensions"].items(),
                            key=lambda kv: -kv[1]):
            if v > 0.01:
                lines.append(f"  {k}: {v:.2f}")
        lines.append("Current Drives:")
        for k, v in sorted(snap["drives"].items(),
                            key=lambda kv: -kv[1]["level"]):
            if v["level"] > 0.01:
                lines.append(f"  {k}: {v['level']:.2f}")
        lines.append("Networks:")
        for k, v in snap["networks"].items():
            lines.append(f"  {k}: {v:.2f}")
        lines.append("Effective modulation:")
        for k, v in snap["modulation"].items():
            lines.append(f"  {k}: {v['value']:.3f}")
        lines.append("Action tendencies:")
        for k, v in sorted(snap["action_tendencies"].items(),
                            key=lambda kv: -kv[1]):
            if v > 0.01:
                lines.append(f"  {k}: {v:.2f}")
        return "\n".join(lines)

    # ── 持久化（唯一落盘的新状态：张力/驱动/网络/参数平滑值）──

    def get_state(self) -> dict:
        return {"saved_at": time.time(),
                "tensions": self.tensions.get_state(),
                "drives": self.drives.get_state(),
                "networks": self.networks.get_state(),
                "modulation": self.modulation.get_state()}

    def save_state(self):
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            tmp = self._state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.get_state(), f, ensure_ascii=False)
            os.replace(tmp, self._state_file)
            self._last_saved = time.time()
        except Exception as e:
            logger.warning(f"[CognitiveField] 状态保存失败: {e}")

    def _load_state(self):
        try:
            if not os.path.exists(self._state_file):
                return
            with open(self._state_file, encoding="utf-8") as f:
                st = json.load(f)
            self.tensions.load_state(st.get("tensions"))
            self.drives.load_state(st.get("drives"))
            self.networks.load_state(st.get("networks"))
            self.modulation.load_state(st.get("modulation"))
            logger.info("[CognitiveField] 内驱状态已恢复（张力/驱动/网络连续性）")
        except Exception as e:
            logger.warning(f"[CognitiveField] 状态加载失败（冷启动）: {e}")
