# continuous_cognition.py — 持续认知循环 + 交流意图（Communication Intention）
# ============================================================================
# 设计原则（FAS Continuous Cognition 规范）：
#   1. 主动交流是认知运行的结果，不是定时聊天功能
#   2. 持续运行 ≠ 持续调 LLM：tick 只跑图谱机制（衰减/扩散/检索/评估），
#      仅当一个 CI 通过表达决策后才调 1 次 LLM 做语言实现
#   3. CI 是图节点（self 空间），不是动作枚举：可保持/增强/竞争/抑制/消亡
#   4. "不表达"是合法且常态的结果
#   5. 可解释性：每个 CI 携带 basis/激活路径/得分/决策原因
#
# 状态机：forming → ready → expressed
#                ↘ discarded（激活衰减出局）
# ready 且未达表达阈值 = WAIT（自然滞留，可被后续脉冲再次增强）
#
# LLM 调用点：仅 _express() 内 1 次 nlp.ask("proactive_express")。
# ============================================================================

import logging
import threading
import time
from datetime import datetime

from graph_model import Node, Edge, now_str, ENGINE_PART_LABELS
from graph_evolution_log import get_evolution_log

logger = logging.getLogger(__name__)

# 状态常量
ST_FORMING = "forming"
ST_READY = "ready"          # 达到形成阈值；未达表达阈值即 WAIT
ST_EXPRESSED = "expressed"
ST_DISCARDED = "discarded"

MAX_CI_NODES = 20           # 图中 CI 节点保留上限（含历史，防膨胀）
MAX_ACTIVE = 3              # 同时活跃（forming/ready）的 CI 数上限（竞争）
BASIS_KEEP = 6


class ContinuousCognition:
    """持续认知循环：无输入时继续图谱认知活动，并从图状态形成交流意图。"""

    # ── 统一认知循环默认参数（config["cognitive_loop"] 同构可覆盖；
    #    与 cognitive_field 的 DEFAULT_* 同一模式：config 缺位时系统
    #    最低可运行，调参走 config）────────────────────────────
    _DEFAULT_LOOP = {
        "reactivation": {
            "weights": {"importance": 0.25, "recency_gap": 0.20,
                         "field_coupling": 0.25, "drive_tie": 0.15,
                         "novelty": 0.10, "noise": 0.15},
            "half_life_s": 900, "cold_field_activation": 0.35,
            "min_interval_ticks": 2, "count": 4, "boost": 1.2,
            "recent_penalty": 0.6, "recent_window_s": 180},
        "intention_kinds": {
            "communication": {"signals": {
                "social_relevance": 0.45, "user_relevance": 0.40,
                "emotion_present": 0.20, "drive.social": 0.35,
                "network.DMN": -0.15}},
            "exploration": {"signals": {
                "drive.curiosity": 0.50, "unknown_present": 0.35,
                "novelty": 0.10, "network.DMN": 0.25,
                "social_relevance": -0.20}},
            "action": {"signals": {
                "world_resource": 0.30, "world_hostile": 0.35,
                "goal_present": 0.30, "drive.learning": 0.25,
                "network.CEN": 0.25, "social_relevance": -0.10}},
            "reflection": {"signals": {
                "tension.self_contradiction": 0.45,
                "drive.consistency": 0.45, "network.DMN": 0.20}},
            "attention_shift": {"signals": {
                "salience": 0.35, "novelty": 0.30, "network.DMN": 0.30,
                "field_change": 0.20}}},
        "max_active_per_kind": 2,
        "focus": {"topk_base": 15, "weights": {
            "concentration": 0.60, "coherence": 0.15, "continuity": 0.08,
            "novelty": 0.10, "drive_support": 0.07}},
        "pulse_gate": {"min_field_change": 0.8, "max_interval_s": 25},
        "pressure": {
            "node": "反思压力", "threshold": 1.2,
            "sources": {"action_failure": 0.25, "negative_feedback": 0.30,
                         "prediction_error": 0.15, "intention_lost": 0.20,
                         "novel_experience": 0.05},
            # 注意力焦点"新到什么程度"才算一次新经历（R2 P11/D-10：这一档压力原先
            # 有表、有金额，但**全仓没有一个 emit 点**，等于这个来源不存在）。
            # 判据用已经算好的 `novelty = 1 − 连续性`（焦点集合与上一拍的 Jaccard），
            # 不另起一套"新颖度"——两处各算一个新颖度才是真正的平行状态。
            "novel_min_novelty": 0.5,
            "max_inject": 2.5, "cooldown_s": 600},
    }

    def __init__(self, kg, engine, nlp, buffer, config, chat_log=None):
        self.kg = kg
        self.engine = engine
        self.nlp = nlp
        self.buffer = buffer
        self.config = config
        self.chat_log = chat_log
        self._evo = get_evolution_log()

        cfg = config.setdefault("continuous_cognition", {})
        cfg.setdefault("tick_seconds", 2.5)
        cfg.setdefault("pulse_every_ticks", 4)        # ~10s 一次认知脉冲
        cfg.setdefault("form_threshold", 0.55)        # CI 形成阈值
        cfg.setdefault("express_threshold", 0.78)     # 表达阈值（高于形成）
        cfg.setdefault("discard_below", 0.10)
        cfg.setdefault("decay_per_pulse", 0.90)
        cfg.setdefault("inhibition_cooldown_s", 60)   # 调度 floor：两条主动消息的最小物理间隔
        cfg.setdefault("max_expressions_per_hour", 6)
        # 论文 §3.3 自主意识模块：离线自由思考/思维跳跃/反思自问
        cfg.setdefault("reignite_every_pulses", 3)    # 冷场需持续的拍数（调制层缺位时的兜底）
        cfg.setdefault("reignite_count", 3)           # 每次唤醒的记忆事件数
        cfg.setdefault("reignite_boost", 1.2)
        # 离线经验重放微调（batch_max/seeds_max/预算/退避等，默认值集中在
        # experience_replay.DEFAULTS——这里空 dict = 全用默认，勿在此复制）
        cfg.setdefault("replay", {})
        cfg.setdefault("thought_every_pulses", 12)    # 低频自由思考（1 次 LLM）
        cfg.setdefault("thought_min_interval_s", 900) # 思考最小间隔 15 分钟
        cfg.setdefault("offline_reflection", True)    # 长时间静默+有未反思表达时自问
        cfg.setdefault("index_sync_every_ticks", 5)   # 每 5 tick 把新节点增量编入语义索引
        cfg.setdefault("monitor_poll_every_ticks", 4)  # 每 4 tick 轮询到期状态检测器
        cfg.setdefault("autonomy_every_ticks", 4)      # 每 4 tick 跑一次自主决策（≈10s）
        cfg.setdefault("clock_every_ticks", 24)         # 每 24 tick（≈60s）更新世界时钟状态
        self.cfg = cfg
        # 统一认知循环（2026-09-20）：cognitive_loop 段是反应性参数真源
        # （config 逐键覆盖内置默认——离线/测试 config 缺段也能运行）
        merged = {k: (dict(v) if isinstance(v, dict) else v)
                  for k, v in self._DEFAULT_LOOP.items()}
        for k, v in ((config or {}).get("cognitive_loop") or {}).items():
            prev = merged.get(k)
            if isinstance(v, dict) and isinstance(prev, dict):
                m = dict(prev)
                m.update(v)
                merged[k] = m
            else:
                merged[k] = v
        self._loop_cfg = merged
        self._last_focus_sig = []       # 上一脉冲焦点签名（连续性/新颖性）
        self._last_pulse_ts = 0.0       # 脉冲时间兜底门
        self._last_react_ts = 0.0       # 再点火 cooldown
        self._cold_beats = 0          # 连续观测到"场过冷"的拍数（R2 P8 防抖）
        self._reactivated_recent = {}   # node → 最近点亮时刻（重复惩罚）
        self._field_sig_prev = None     # 激活场签名（变化量门）

        self._thread = None
        self._stop = threading.Event()
        self._busy = threading.Event()   # /api/nlp 处理中 → 暂停 tick 扩散
        self._lock = threading.RLock()
        self._tick = 0
        self._pulse_seq = 0          # 脉冲序号（B5 出生拍豁免的键；_tick 在
                                     # busy 期不前进，不能当"同一拍"的判据）
        self._last_express_ts = 0.0
        self._express_count_hour = []
        self._queue = []                 # 待取主动消息 [{ci_id, text, ...}]
        self._last_outcome_negative = False
        self._last_thought_ts = 0.0
        self._last_offline_reflect = 0.0
        self.reflection = None           # 可选：ReflectionEngine 引用（离线自问）
        self._emb_mgr = None             # 可选：EmbeddingManager（新节点增量入索引）
        self.regulation = None           # 可选：CognitiveRegulation（检测器轮询）
        self.autonomy = None             # 可选：AutonomousLoop（自主行动节拍）
        self.action_manager = None       # 可选：ActionManager（动作推进/中断/结算）
        self.drive_evaluator = None      # 可选：DriveEvaluator（自主期驱动力刷新）
        self.internal_state = None       # 可选：InternalState（激素衰减 tick）
        self.timeline = None             # 可选：ExperienceTimeline（时钟迁移事件入统一时间轴）
        self._replay = None              # 可选：ExperienceReplay（离线经验重放调度层，惰性建）

    # ── 生命周期 ──────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="continuous-cognition", daemon=True)
        self._thread.start()
        logger.info("[CC] 持续认知循环已启动 "
                    f"(tick={self.cfg['tick_seconds']}s, pulse_every={self.cfg['pulse_every_ticks']})")
        import fas_log
        fas_log.get_logger(fas_log.COGNITION).info(
            "loop_started", "持续认知循环已启动",
            tick_s=self.cfg["tick_seconds"],
            pulse_every=self.cfg["pulse_every_ticks"])

    def stop(self):
        self._stop.set()

    def set_busy(self, busy: bool):
        """/api/nlp 处理期间置位，tick 扩散让位，避免与请求内扩散叠加。"""
        if busy:
            self._busy.set()
        else:
            self._busy.clear()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as e:
                logger.warning(f"[CC] 循环异常: {e}")
                import fas_log
                fas_log.get_logger(fas_log.COGNITION).exception(
                    "cc_loop_exception", f"循环 tick 异常: {e}")
            self._stop.wait(self.cfg["tick_seconds"])
        import fas_log
        fas_log.get_logger(fas_log.COGNITION).info(
            "loop_stopped", "持续认知循环已停止", tick=self._tick)

    def tick_once(self):
        """一个认知 tick。图谱扩散/脉冲在对话回合期间让位（busy）；
        **具身行动推进永不停**（Bug 修复 2026-09-20）：回合内 busy 曾冻结
        整个 tick 体，一次 40~80 秒的 LLM 回合把在飞动作的轮询/结算也冻住
        ——她"一思考就站住"，走到了也不结算（实测日志 01:09:36 出发的
        navigate 直到回合结束都没 poll）。行动推进不碰扩散前沿，
        与回合内的 activate/diffuse 没有资源冲突。"""
        if not self._busy.is_set():
            self._tick += 1
            # 手动 auto 模式开启时让位其扩散，避免双重扩散
            if not getattr(self.engine, "_running", False):
                self.engine.decay_step()
                self.engine.diffuse_step()
                import fas_log
                fas_log.aggregator(
                    "cc_diffusion_ticks", fas_log.ACTIVATION,
                    "diffusion_ticks", flush_every_s=120.0,
                    msg="后台图谱扩散（聚合）").add()
            # 激素衰减：phasic 快速回 0、level 向 baseline 回归。
            # 只动内存值（落盘仍走既有 cycle 边界），不新增磁盘压力。
            # §16 实验屏蔽 internal_state_heartbeat：decay/drift/needs 三个
            # 写入节拍全跳过 ⇒ 内部状态钉在装配时刻（排除心情/激素漂移干扰）。
            _is_shielded = False
            try:
                import experiment_mode as _xm
                _is_shielded = _xm.shield("internal_state_heartbeat")
            except Exception:
                pass
            if self.internal_state is not None and not _is_shielded:
                try:
                    self.internal_state.tick_decay()
                    # 需求控制器（Phase 2+ 补账）：有 provider 的维度向
                    # 世界信号缓步收敛；没接 provider 的不动。
                    #
                    # R2 P9：这一拍把**图上**的 需求↔调制器 耦合走两步——
                    #   ① 需求→调制器（`apply_need_drift`）：缺口按边权慢慢抬/压通道；
                    #   ② 调制器→需求（`bias=`）：通道水位反过来给靶值一个连续位移。
                    # 一正一反成**负反馈**（缺口→抬通道→靶值收敛缺口→通道回落），
                    # 有界性由两侧钳位保证（① 唯一写入口+受体曲线，② 需求量程）。
                    # R2 P10 加第 ⓪ 步：昼夜→调制器（褪黑素样/组胺样的日内水位）。
                    # dt 用本拍的实际时长（tick 节拍被回合暂停时不会补跳）。
                    _need_bias = {}
                    try:
                        import modulator_subgraph as _ms
                        _dt = float(self.cfg["tick_seconds"]) / 60.0
                        _ms.apply_circadian_drift(self.kg, self.internal_state,
                                                  self.config, dt_min=_dt)
                        _ms.apply_need_drift(self.kg, self.internal_state,
                                             self.config, dt_min=_dt)
                        _need_bias = (_ms.graph_biases(
                            self.kg, self.internal_state, self.config)
                            or {}).get("need") or {}
                    except ImportError:
                        pass
                    self.internal_state.update_needs_from_signals(
                        bias=_need_bias)
                except Exception as _de:
                    logger.debug(f"[CC] 内部状态控制跳过: {_de}")
            # 参数调制（论文"参数节点化"最小兑现；2026-09-20 Drive 重构
            # 收编）：情绪唤醒/皮质醇压力喂入 CognitiveField 语境，
            # diffusion.param_gain 由统一调制层合成（网络态/激素/张力一并
            # 生效），引擎经 engine.modulation 读回。只读既有状态。
            try:
                _ar = 0.0
                with self.kg._lock:
                    for _n in self.kg.nodes.values():
                        if (_n.extra_attrs or {}).get("type") == "emotion"                                 and _n.activation > _ar:
                            _ar = min(1.0, _n.activation / 3.0)
                _st = 0.0
                if self.internal_state is not None:
                    # 参数通道（context.stress）读**慢分量偏移**：modulator_dev 已经
                    # 减过基线并过受体曲线，这里不再自己 level−baseline（R2 P3）。
                    _st = max(0.0, min(1.0,
                                       float(self.internal_state.modulator_dev(
                                           "cortisol") or 0.0)))
                if self.drive_evaluator is not None:
                    try:
                        self.drive_evaluator.set_context_value("arousal", _ar)
                        self.drive_evaluator.set_context_value("stress", _st)
                        self.drive_evaluator.refresh_modulation()
                    except Exception:
                        pass
                self.engine.update_param_modulation(_ar, _st)
            except Exception as _pme:
                logger.debug(f"[CC] 参数调制跳过: {_pme}")
            # ── 再点火 / 认知脉冲：由"场状态"决定是否运行（§十一）──
            # 不堆固定闹钟：reactivation 由场冷度触发（火种不足才撒），
            # pulse 由激活场累积变化触发（有认知素材才采样），
            # cooldown/时间上限只作兜底，不是主要条件。
            self._reactivate_gate()
            self._pulse_gate()
            self._replay_gate()      # 空闲经验重放（对话期不到此拍，天然可抢占）
            if self._tick % self.cfg["thought_every_pulses"] == 0:
                self._free_think()
            if (self._tick % self.cfg["monitor_poll_every_ticks"] == 0
                    and self.regulation is not None):
                # 状态检测器轮询：只采样已登记数据源的 poll 型检测器，
                # 无数据源时零成本返回（不新建线程，借用既有 tick 节拍）
                try:
                    self.regulation.poll_due()
                except Exception as _re:
                    logger.warning(f"[CC] 检测器轮询跳过: {_re}")
            if (self._tick % self.cfg["index_sync_every_ticks"] == 0
                    and self._emb_mgr is not None):
                # 索引跟随图谱：新节点（能力/事件/未知）进语义召回
                self._emb_mgr.flush_pending(self.kg)
            self._maybe_offline_reflect()
            # tick 边界：清空本轮激活来源（本 tick 的注入只在本 tick
            # 免扣；下一 tick 的注入重新登记）。对话期 CC 暂停，
            # 来源由 app 回合末的 clear_anchors 清空。
            self.engine.clear_anchors()
        # ── 具身推进：busy 与否都要跑的部分在下面 ──
        # 世界时钟（temporal 重构 2026-09-20）：时间状态与用户输入无关、
        # 与 busy 无关——现实时间持续写入图谱（分钟级地板 + 迁移脉冲）。
        # 用真实时间门而非 _tick 取模：busy 期 _tick 冻结，时钟不能停。
        if time.time() - getattr(self, "_last_clock_ts", 0.0) >= \
                max(10.0, float(self.cfg["clock_every_ticks"])
                    * float(self.cfg["tick_seconds"])):
            self._last_clock_ts = time.time()
            try:
                from temporal_awareness import update_clock_state
                _clk = update_clock_state(self.kg, self.engine,
                                          timeline=self.timeline)
                # R2 P10：跨时段是一次**调制事件**（快通道：那一步的推/拉），
                # 与每拍的昼夜漂移（慢通道：水位）不重叠也不矛盾。
                # 幅度是有符号的夜间度之差（temporal_awareness 从图上事实算好），
                # 所以"上午→中午"是 0 ⇒ 被事件门自己挡掉，不会每小时抖一下。
                _tr = (_clk or {}).get("transition") or {}
                if _tr.get("from") and self.internal_state is not None \
                        and float(_tr.get("strength_delta") or 0.0) > 0:
                    try:
                        import modulation_events as _ME
                        _eng = getattr(self, "_mod_engine", None)
                        if _eng is None:
                            _eng = self._mod_engine = _ME.ModulatorEngine(
                                self.kg, self.internal_state, self.config)
                        _eng.emit("time_bucket_changed", source="circadian",
                                  valence=float(_tr["load_to"])
                                  - float(_tr["load_from"]),
                                  intensity=float(_tr["strength_delta"]),
                                  novelty=float(_tr["strength_delta"]),
                                  ref=f"{_tr['from']}→{_tr['to']}")
                    except Exception as _mee:                 # noqa: BLE001
                        logger.debug(f"[CC] 时段迁移事件跳过: {_mee}")
            except Exception as _ce:
                logger.debug(f"[CC] 时钟更新跳过: {_ce}")
        # 自主"决策"仍在非 busy 期做（回合期用户正在交互，回合开头的
        # notify_user_activity 已经接管；busy 期 _tick 不前进，取模调度
        # 会失效）。但动作推进/结算（action_manager）永远继续——
        # 这正是"一边做事一边说话"的执行面。
        _auto_out = None
        if self.autonomy is not None and not self._busy.is_set() \
                and self._tick % self.cfg["autonomy_every_ticks"] == 0:
            # 自主行动决策：确定性、零 LLM、最小间隔由 autonomy 自己把关
            # （模式关闭/未连接/无依据时它只会如实返回"没做"）
            try:
                # 驱动力先刷新（Bug 修复 2026-09-19：evaluate 原本只在
                # 对话轮跑，纯自主模式下 CuriosityDrive 节点被衰减吃光
                # （实测掉到 0.023），observe/approach 候选永远低于
                # 阈值 → 她在游戏里结构性瘫痪不动。5s 缓存，成本低）
                if self.drive_evaluator is not None:
                    self.drive_evaluator.evaluate()
                _auto_out = self.autonomy.tick()
            except Exception as _ae:
                _auto_out = None
                logger.warning(f"[CC] 自主行动跳过: {_ae}")
        if self.action_manager is not None:
            # Action 节点推进（每个 tick）：在飞动作轮询回执、
            # 超时结算、生存紧急中断、目标队列出队。
            # 独立于自主模式：用户指令产生的动作也要推进。
            # 收尾修复 2026-09-22：autonomy.tick 在承诺期内已自行 tick 过
            # ActionManager（reason=action_progress），本拍不再补第二下
            # ——否则技能每拍推进两步，动作节奏翻倍。
            _am_ticked_by_autonomy = (isinstance(_auto_out, dict)
                                      and _auto_out.get("reason") == "action_progress")
            if not _am_ticked_by_autonomy:
                try:
                    self.action_manager.tick()
                except Exception as _ame:
                    logger.debug(f"[CC] Action 推进跳过: {_ame}")

    # ── 认知脉冲：图谱评估，零 LLM ────────────────────────

    # ── 认知脉冲：观察激活场正在形成什么（不是"找话跟用户说"）────
    # activation field → coherent focus → candidate intention(kind)
    # → competition（同类合并/强化，满额抑制）→ formation / nothing。
    # "什么都不产生"是合法结果：焦点弱、无明确 kind、同类满员都属正常。

    def _field_signature(self):
        try:
            topk, _ = self.engine.get_topk(k=8)
        except Exception:
            # L1-DGR-05:失败返回 None(而非 []),避免与"空场"混淆——
            # 空列表会被 _sig_change 解读成"全场消失"的重大变化,触发脉冲
            # 并给意图集体降一口衰减,全程零日志。
            return None
        return [(n.id, round(float(n.activation or 0), 1)) for n in topk]

    def _pulse_gate(self):
        gc = self._loop_cfg.get("pulse_gate") or {}
        now = time.time()
        sig = self._field_signature()
        if sig is None:
            # L1-DGR-05:探测失败本拍跳过,不采样也不触发脉冲
            import logging
            logging.getLogger(__name__).debug(
                "[CC] 场签名探测失败，脉冲门本拍跳过")
            return
        change = self._sig_change(self._field_sig_prev, sig)
        overdue = now - self._last_pulse_ts >= float(gc.get("max_interval_s", 25))
        if change < float(gc.get("min_field_change", 0.8)) and not overdue:
            return                     # 场没动，没必要采样
        self._field_sig_prev = sig
        self._last_pulse_ts = now
        self._last_field_change = change
        # ── 观测：脉冲周期（internal_state 账本外的短周期，x_ 前缀）──
        import fas_log
        _cc_cyc = fas_log.ephemeral_cycle()
        fas_log.set_cycle(_cc_cyc)
        fas_log.get_logger(fas_log.COGNITION).info(
            "cycle_start", "认知脉冲开始", trigger="cc_pulse",
            tick=self._tick, field_change=round(change, 3),
            overdue=overdue)
        _t0 = time.time()
        try:
            self._pulse({"field_change": min(1.0, change / 5.0)})
        finally:
            fas_log.get_logger(fas_log.COGNITION).info(
                "cycle_end", "认知脉冲结束", kind="cc_pulse", tick=self._tick,
                duration_s=round(time.time() - _t0, 3))
            fas_log.set_cycle(None)

    @staticmethod
    def _sig_change(prev, now_sig):
        if not prev:
            return 5.0
        p = dict(prev)
        n = dict(now_sig)
        ids = set(p) | set(n)
        return sum(abs(n.get(i, 0.0) - p.get(i, 0.0)) for i in ids)

    def _cognitive_snapshot(self):
        """Drive/网络/张力信号（供意图 kind 亲和度用；缺位按 0）。"""
        snap = {"drive": {}, "network": {}, "tension": {}}
        de = self.drive_evaluator
        if de is None:
            return snap
        try:
            f = de.field
            snap["drive"] = f.drives.levels()
            snap["network"] = f.networks.levels()
            snap["tension"] = f.tensions.levels()
        except Exception:
            pass
        return snap

    def _world_signals(self):
        """图上世界信号（focus 的 kind 亲和度输入，读既有状态节点）。"""
        out = {"unknown_present": 0.0, "world_hostile": 0.0,
               "world_resource": 0.0, "goal_present": 0.0, "salience": 0.0}
        with self.kg._lock:
            for nid, n in self.kg.nodes.items():
                ea = n.extra_attrs or {}
                act = float(n.activation or 0.0)
                if act < 0.1:
                    continue
                if str(nid).startswith(("UnknownEntity_", "UnknownBlock_",
                                         "UnknownPlayer_")):
                    out["unknown_present"] = max(out["unknown_present"],
                                                 min(1.0, act / 3.0))
                if ea.get("type") in ("goal", "reply_goal"):
                    out["goal_present"] = max(out["goal_present"],
                                              min(1.0, act / 3.0))
            hub = self.kg.get_node("附近的生物")
            if hub is not None and (hub.extra_attrs or {}).get("hostile"):
                out["world_hostile"] = 1.0
            blocks = self.kg.get_node("附近的方块")
            if blocks is not None and str(
                    (blocks.extra_attrs or {}).get("value") or "") \
                    not in ("", "无"):
                out["world_resource"] = 0.6
            ui = self.kg.get_node("未知信息")
            if ui is not None:
                out["salience"] = min(1.0, float(ui.activation or 0.0) / 3.0)
        return out

    def _pulse(self, extra_signals: dict = None):
        self._pulse_seq += 1         # B5：本拍号（形成与衰减共用，出生拍豁免判据）
        # R2 P8（D-3）：`retrieval.topk_scale` 的真消费者 —— 这次脉冲从认知场里
        # 取多宽的素材面。DMN 高→读得更宽（发散时翻更多角落），CEN 高时该参数
        # 不降（它没有 CEN 行），但 negative_experience 抬升也会放宽（反刍时更容易
        # 捞进旧账），这就是"同一份场，不同状态看见不同东西"。
        width = max(3, int(round(15.0 * self._mod_th("retrieval.topk_scale", 1.0))))
        topk, _ = self.engine.get_topk(k=width)
        with self.kg._lock:
            INFRA = {nid for nid, n in self.kg.nodes.items()
                     if n.label in ENGINE_PART_LABELS}
        basis_pool = [n for n in topk
                      if n.id not in INFRA and n.activation > 0.15
                      and not n.id.startswith(("坐标(", "CI_", "思考_", "反思_",
                                               "回答记录_", "搜索记录_",
                                               "文件操作记录_"))]
        if not basis_pool:
            self._decay_intentions()
            return

        # ── focus 质量（"认知场里正在形成什么"，先于任何表达考虑）──
        ids = [n.id for n in basis_pool]
        idset = set(ids)
        internal_edges = 0
        with self.kg._lock:
            for nid in ids:
                for e in self.kg.get_out_edges(nid):
                    if e.dst in idset:
                        internal_edges += 1
        max_pairs = max(1, len(ids) * (len(ids) - 1) / 2.0)
        coherence = min(1.0, internal_edges / (max_pairs * 0.4))
        prev_focus = set(self._last_focus_sig)
        overlap = len(idset & prev_focus) / max(len(idset | prev_focus), 1)
        continuity = min(1.0, overlap * 2.0)
        novelty = 1.0 - continuity
        top_act = max(n.activation for n in basis_pool)
        concentration = min(top_act / 3.0, 1.0)
        with self.kg._lock:
            drive_support = 0.0
            for n in basis_pool:
                ea = getattr(n, "extra_attrs", None) or {}
                if ea.get("type") in ("drive", "emotion", "cognitive_network"):
                    drive_support = max(drive_support, min(1.0,
                                      float(n.activation or 0.0) / 3.0))
        sig = self._cognitive_snapshot()
        with self.kg._lock:
            user_edges = {e.dst: float(e.weight or 0)
                          for e in self.kg.get_out_edges("用户")}
            emotion_ids = {nid for nid, n in self.kg.nodes.items()
                           if (n.extra_attrs or {}).get("type") == "emotion"
                           and float(n.activation or 0) > 0.1}
        user_relevant = sum(1 for n in basis_pool
                            if user_edges.get(n.id, 0) > 0.2)
        emotion_present = 1.0 if any(n.id in emotion_ids for n in basis_pool) else 0.0
        social_relevance = min(1.0, (0.5 if emotion_present else 0.0)
                               + 0.15 * min(user_relevant, 3))
        affordance_w = (self._loop_cfg.get("focus") or {}).get("weights") or {}
        focus_score = (
            float(affordance_w.get("concentration", 0.60)) * concentration
            + float(affordance_w.get("coherence", 0.15)) * coherence
            + float(affordance_w.get("continuity", 0.08)) * continuity
            + float(affordance_w.get("novelty", 0.10)) * novelty
            + float(affordance_w.get("drive_support", 0.07)) * drive_support)
        focus_explain = {
            "concentration": round(concentration, 2), "coherence": round(coherence, 2),
            "continuity": round(continuity, 2), "novelty": round(novelty, 2),
            "drive_support": round(drive_support, 2),
            "focus_score": round(focus_score, 3),
            "top_activation": round(top_act, 3)}

        # ── focus → 候选意图 kinds（affinity 数据表；kind 开放可扩展）──
        signals = {
            "concentration": concentration, "coherence": coherence,
            "novelty": novelty, "salience": 0.0,
            "social_relevance": social_relevance,
            "user_relevance": min(1.0, user_relevant / 3.0),
            "emotion_present": emotion_present,
            "unknown_present": 0.0, "world_hostile": 0.0,
            "world_resource": 0.0, "goal_present": 0.0,
            "field_change": float((extra_signals or {}).get("field_change", 0.0)),
        }
        try:
            signals.update(self._world_signals())
        except Exception:
            pass
        for d, lv in sig["drive"].items():
            signals["drive." + d] = lv
        for k, lv in sig["network"].items():
            signals["network." + k] = lv
        for t, lv in sig["tension"].items():
            signals["tension." + t] = lv
        kinds_aff = {}
        for kind, spec in (self._loop_cfg.get("intention_kinds") or {}).items():
            a = 0.0
            for key, w in (spec.get("signals") or {}).items():
                a += float(w) * float(signals.get(key, 0.0) or 0.0)
            kinds_aff[kind] = round(a, 3)

        self._last_focus_sig = ids
        # R2 P11（审计 D-10）：焦点底子换掉大半 = 一次"新经历"，进反思压力池。
        # 金额一直在 config 的 `pressure.sources.novel_experience`（0.05）里，缺的
        # 从来不是数值而是这个 emit 点 ⇒ 那个来源此前等于不存在。判据复用**已经算好**
        # 的 `novelty = 1 − 连续性`（本拍焦点集合与上一拍的 Jaccard），不另起一套
        # 新颖度算法——两处各算一个"新"才是真正的平行状态。
        if novelty >= float((self._loop_cfg.get("pressure") or {})
                            .get("novel_min_novelty", 0.5)):
            self.note_pressure("novel_experience")
        self._merge_or_form(basis_pool, focus_score, focus_explain,
                            kinds_aff, signals)
        self._decay_intentions()
        self._prune_history()
        self.tick_express_check()

    # ── CI 形成与竞争 ─────────────────────────────────────

    # ── 意图形成与竞争（kind 感知：communication/action/exploration/…开放表）──

    def _mod_th(self, key: str, fallback: float) -> float:
        """调制层读点（阈值不是静态 magic number）。"""
        de = self.drive_evaluator
        if de is None:
            return float(fallback)
        try:
            v = de.modulation().get(key)
            return float(v) if v is not None else float(fallback)
        except Exception:
            return float(fallback)

    def _merge_or_form(self, basis_nodes, focus_score, focus_explain,
                       kinds_aff, signals):
        basis = [n.id for n in basis_nodes[:BASIS_KEEP]]
        basis_set = set(basis)
        # 意图形成阈值：受 Drive/网络/激素连续调制。
        # 注意形成判定与 kind 判定分离——focus 够强就形成（"认知场里正在
        # 形成什么"优先）；kind 只是给它类型（没有明确方向时退为
        # attention_shift：一次注意力转向，不参与表达，自然衰减）。
        form_th = self._mod_th("cognition.form_threshold",
                               float(self.cfg["form_threshold"]))
        if focus_score < form_th or not kinds_aff:
            return     # 焦点不足：不形成（合法状态）
        # attention_shift 是兜底类型（不参与 argmax）：焦点有方向就用方向，
        # 完全没方向才算一次注意力转向
        directed = {k: v for k, v in kinds_aff.items()
                    if k != "attention_shift"}
        kind, affinity = (max(directed.items(), key=lambda kv: kv[1])
                          if directed else (None, 0.0))
        if kind is None or affinity < 0.05:
            kind = "attention_shift"
            affinity = float(kinds_aff.get("attention_shift", 0.0))

        # 同一认知焦点（basis 重叠 ≥40%）= 同一意图的持续积累——kind 无关：
        # 焦点的"方向"随场演化时 kind 跟着漂移（affinity 显著更高才换，
        # 防抖），但意图本体保持连续（reinforce 非新建）。
        for ci in self._active_intentions():
            ea = ci.extra_attrs
            old_basis = set(ea.get("basis", []))
            if old_basis & basis_set:
                overlap = len(old_basis & basis_set) / max(len(old_basis | basis_set), 1)
                if overlap >= 0.4:
                    ea["activation"] = min(1.0, float(ea.get("activation", 0)) +
                                           0.5 * (focus_score -
                                                  float(ea.get("score", 0))) + 0.06)
                    ea["score"] = focus_score
                    ea["reinforced"] = int(ea.get("reinforced", 0)) + 1
                    if affinity > float(ea.get("affinity", 0)) + 0.1:
                        ea["kind"] = kind      # 焦点方向明确改变 → 意图漂移
                        ea["affinity"] = affinity
                    ea["basis"] = list((old_basis | basis_set))[:BASIS_KEEP]
                    ea["explain"] = focus_explain
                    ea["last_boosted"] = now_str()
                    if ea.get("type") == "communication_intention":
                        self._promote_goal(ci)
                    return
        # 同类并发上限：满了 → 该型新意图竞争失败（inhibition 的图谱化）
        if len(self._active_intentions(kind)) >= \
                int(self._loop_cfg.get("max_active_per_kind", 2)):
            focus_explain["suppressed"] = f"kind_full:{kind}"
            return

        ci_id = f"CI_{int(time.time() * 1000) % 10 ** 9}"
        # 意图强度 = 焦点强度（focus 够强才形成，形成即多强）；affinity
        # 只决定"朝哪个方向"，不给强度打折——表达与否另有更高门槛把关
        intensity = round(min(1.0, focus_score), 4)
        is_comm = kind == "communication"
        with self.kg._lock:
            self.kg.add_node(Node(
                id=ci_id, weight=0.5, label="intention",
                graph_space="self",
                extra_attrs={
                    # 沟通类沿用旧 type（兼容测试/前端），其余为通用意图
                    "type": ("communication_intention" if is_comm else "intention"),
                    "kind": kind,
                    "affinity": affinity,
                    "status": (ST_FORMING if is_comm else ST_READY),
                    "activation": intensity,
                    "score": focus_score,
                    "basis": basis,
                    "goal": None, "illocutionary_act": None,
                    "dialogue_act": None, "reply_goal": None,
                    "explain": focus_explain,
                    "created": now_str(),
                    # B5：出生脉冲号——同一拍里先形成（L575）后衰减（L577），
                    # 不记拍号就没法豁免"出生即被 ×decay 咬掉一口"。
                    "born_pulse": self._pulse_seq,
                }))
            if not self.kg.add_edge(Edge(src="Self", dst=ci_id, relation="意图",
                                         weight=0.5,
                                         relation_category="cognitive_relation")):
                # PIN-11 留痕: hub 边(Self→CI)依赖 Self 节点存在;
                # 若缺失(异常图态)静默丢边会让意图脱离纽带,留 warning。
                logger.warning(f"[CC] 意图 hub 边失败: Self -> {ci_id} 意图 "
                               f"(Self 节点可能不在图内)")
            for b in basis:
                if self.kg.get_node(b):
                    self.kg.add_edge(Edge(src=ci_id, dst=b, relation="基于",
                                          weight=0.5,
                                          relation_category="cognitive_relation"))
            self._evo.note_node_added(ci_id, label="intention", weight=0.5)
        logger.info(f"[CC] 意图形成: {ci_id} kind={kind} "
                    f"intensity={intensity:.2f} affinity={affinity:.2f} "
                    f"basis={basis[:3]}…")

    def _promote_goal(self, ci):
        """goal 推导（图谱规则，非 LLM）：basis 构成决定表达家族。

        规范八（CI 二次原子化）：goal/target 用边连接到独立节点表达，
        不塞 JSON blob——goal 连到封闭词表的目标节点，target 连 用户。
        """
        ea = ci.extra_attrs
        if ea.get("illocutionary_act"):
            return  # 已定型
        basis = ea.get("basis", [])
        with self.kg._lock:
            is_emotion = any(
                (self.kg.nodes.get(b) and
                 (self.kg.nodes[b].extra_attrs or {}).get("type") == "emotion")
                for b in basis)
            is_event = any(
                self.kg.nodes.get(b) and self.kg.nodes[b].graph_space == "episodic"
                for b in basis)
        if is_emotion:
            fam = ("expressive", "emotion_expression", "empathize", "共情回应")
        elif ("未知信息" in basis or "好奇" in basis
              or "CuriosityDrive" in basis):
            fam = ("directive", "question", "information_seeking", "追问未知")
        elif is_event:
            fam = ("assertive", "sharing", "follow_up", "延续话题")
        else:
            fam = ("assertive", "information_statement", "share_related_knowledge", "分享知识")
        ea.update(illocutionary_act=fam[0], dialogue_act=fam[1], reply_goal=fam[2])
        # 原子化表达：意图目标/指向对象 用边连接（目标词表 4 节点，cognitive 空间）
        with self.kg._lock:
            goal_id = f"目标:{fam[3]}"
            if goal_id not in self.kg.nodes:
                self.kg.add_node(Node(
                    id=goal_id, weight=0.5, label="declarative-semantic",
                    graph_space="cognitive",
                    extra_attrs={"type": "reply_goal", "key": fam[2]}))
            if not self.kg.get_edge(ci.id, goal_id, "目标"):
                self.kg.add_edge(Edge(src=ci.id, dst=goal_id, relation="目标",
                                      weight=0.6, relation_category="cognitive_relation"))
            if self.kg.get_node("用户") and not self.kg.get_edge(ci.id, "用户", "指向"):
                self.kg.add_edge(Edge(src=ci.id, dst="用户", relation="指向",
                                      weight=0.5, relation_category="social_relation"))
        ea["status"] = ST_READY

    _INTENTION_TYPES = ("communication_intention", "intention")

    def _active_intentions(self, kind: str = None) -> list:
        with self.kg._lock:
            return [n for n in self.kg.nodes.values()
                    if (n.extra_attrs or {}).get("type") in self._INTENTION_TYPES
                    and (kind is None
                         or n.extra_attrs.get("kind") == kind)
                    and n.extra_attrs.get("status") in (ST_FORMING, ST_READY)]

    def _active_cis(self):
        with self.kg._lock:
            return [n for n in self.kg.nodes.values()
                    if (n.extra_attrs or {}).get("type") == "communication_intention"
                    and n.extra_attrs.get("status") in (ST_FORMING, ST_READY)]

    def _decay_intentions(self):
        """意图衰减/晋升/丢弃。ready 未达表达阈值 = WAIT（自然滞留）。
        衰减出局 → 反思压力（说过/想做而没做到的念头会攒疑问）。"""
        with self.kg._lock:
            for n in list(self.kg.nodes.values()):
                ea = n.extra_attrs or {}
                if ea.get("type") not in self._INTENTION_TYPES:
                    continue
                st = ea.get("status")
                if st in (ST_EXPRESSED, ST_DISCARDED):
                    continue
                # B5/§5 出生拍豁免：沟通类在形成拍的同一脉冲里就会被 ×
                # decay_per_pulse 咬掉（0.79/0.77/0.60 出生即濒死的根因）。
                # 只豁免"不吃这一口衰减"——晋升/淘汰照常评估；EXPRESSED
                # 是终态，双表达仍不可能。
                born_exempt = (ea.get("type") == "communication_intention"
                               and ea.get("born_pulse") == self._pulse_seq)
                if not born_exempt:
                    ea["activation"] = round(
                        float(ea.get("activation", 0)) * self.cfg["decay_per_pulse"], 4)
                if ea["activation"] >= self.cfg["form_threshold"] \
                        and st == ST_FORMING:
                    self._promote_goal(n)
                if ea["activation"] < self.cfg["discard_below"]:
                    ea["status"] = ST_DISCARDED
                    ea["discarded_at"] = now_str()
                    self._evo.note_node_updated(n.id, {"status": ST_DISCARDED})
                    logger.info(f"[CC] 意图丢弃: {n.id} kind={ea.get('kind')} "
                                f"(激活衰减出局)")
            dead = [n for n in self.kg.nodes.values()
                    if (n.extra_attrs or {}).get("status") == ST_DISCARDED
                    and (n.extra_attrs or {}).get("discarded_at")
                    and not (n.extra_attrs or {}).get("pressure_counted")]
            for n in dead:
                n.extra_attrs["pressure_counted"] = True
        if dead:
            for _ in dead:
                self.note_pressure("intention_lost")

    def _prune_history(self):
        with self.kg._lock:
            cis = [n for n in self.kg.nodes.values()
                   if (n.extra_attrs or {}).get("type") in self._INTENTION_TYPES]
            if len(cis) <= MAX_CI_NODES:
                return
            cis.sort(key=lambda n: n.extra_attrs.get("created", ""))
            for n in cis[:len(cis) - MAX_CI_NODES]:
                cid = n.id
                self.kg.edges = [e for e in self.kg.edges
                                 if e.src != cid and e.dst != cid]
                del self.kg.nodes[cid]

    # ── 表达决策（图谱+抑制分，非 LLM）────────────────────

    def _evaluate_expression(self):
        best, best_val = None, 0.0
        now = time.time()
        cooldown_ok = now - self._last_express_ts >= self.cfg["inhibition_cooldown_s"]
        hour_expr = [t for t in self._express_count_hour if now - t < 3600]
        # 表达抑制调制化（2026-09-21）：300s 冷却/每小时上限等"固定社交
        # 规则"拆除——融合成一个连续的 recent_expression 通道（小时密度 +
        # 刚说完的指数衰减）喂给 express_threshold 调制；只保留一个调度
        # floor（最小间隔，防抽风）。有强社交/意图支撑时，她可以连续说话。
        _decay = 0.0
        if self._last_express_ts:
            _decay = __import__("math").exp(
                -(now - self._last_express_ts) / 300.0)
        if self.drive_evaluator is not None:
            try:
                self.drive_evaluator.set_context_value(
                    "recent_expression",
                    min(1.0, len(hour_expr) / 6.0 * 0.6 + _decay * 0.6))
            except Exception:
                pass
        for ci in self._active_cis():
            ea = ci.extra_attrs
            if ea.get("status") != ST_READY:
                continue
            rich = False
            with self.kg._lock:
                for b in ea.get("basis", []):
                    bn = self.kg.nodes.get(b)
                    if bn and (bn.graph_space == "episodic"
                               or (bn.extra_attrs or {}).get("type") == "emotion"):
                        rich = True
                        break
            if not rich:
                ea["expression_eval"] = {"suppressed": "basis lacking concrete tension"}
                continue
            expr = float(ea.get("activation", 0))
            supp = 0.0
            if not cooldown_ok:
                supp += 0.4   # 最小间隔 floor（调度防抽风，非社交规则）
            if self._last_outcome_negative:
                supp += 0.3   # 上次交流被用户叫停/抵触 → 抑制（学习门控）
            explain = {"expression_score": round(expr, 3),
                       "suppression_score": round(supp, 3),
                       "cooldown_floor_ok": cooldown_ok}
            ea["expression_eval"] = explain
            val = expr - supp
            if val >= self._mod_th("cognition.express_threshold",
                                   float(self.cfg["express_threshold"])) \
                    and val > best_val:
                best, best_val = ci, val
        return best, best_val

    # ── 反思压力（pressure-driven reflection，2026-09-20）─────────
    # 各信号源（行动失败/负反馈/预测误差/意图消散/新经验）把压力写进图上
    # 一个持续节点；压力够高才进入离线反思——时间只是 cooldown 下限，
    # 不再是触发器本身。节点激活靠现有 cognitive 空间衰减自然消退。

    def _pressure_cfg(self) -> dict:
        return (self._loop_cfg.get("pressure") or {})

    def note_pressure(self, source: str, amount: float = None):
        """压力信号注入（图节点 activation，上限封顶防滚雪球）。"""
        pcfg = self._pressure_cfg()
        if amount is None:
            amount = float((pcfg.get("sources") or {}).get(source, 0.0))
        if amount <= 0:
            return
        pid = str(pcfg.get("node") or "反思压力")
        cap = float(pcfg.get("max_inject", 2.5))
        with self.kg._lock:
            node = self.kg.get_node(pid)
            if node is None:
                node = Node(id=pid, weight=0.5, label="declarative-semantic",
                            graph_space="cognitive",
                            extra_attrs={"type": "cognitive_pressure",
                                         "canon": "state",
                                         "sources": {}})
                self.kg.add_node(node)
            node.activation = min(5.0, float(node.activation or 0.0) + amount)
            srcs = dict(node.extra_attrs.get("sources") or {})
            srcs[source] = round(float(srcs.get(source, 0)) + amount, 3)
            node.extra_attrs["sources"] = srcs
            node.extra_attrs["last_source"] = source
            node.touch()
        if self.engine is not None:
            try:
                self.engine.mark_active([pid])
                self.engine.register_activation_source([pid], "internal_drive")
            except Exception:
                pass
        if self.engine is not None and cap and amount > cap:
            logger.debug("[CC] 压力注入超上限（已由 activation min 封顶）")

    def pressure_value(self) -> float:
        pcfg = self._pressure_cfg()
        with self.kg._lock:
            node = self.kg.get_node(str(pcfg.get("node") or "反思压力"))
            return float(node.activation or 0.0) if node else 0.0

    def note_outcome(self, negative: bool):
        """由 app.py 在 outcome 标注处回调：用户正反馈解除抑制，负反馈加强。"""
        self._last_outcome_negative = negative
        if negative:
            self.note_pressure("negative_feedback")

    # ── 表达（唯一 LLM 调用点）────────────────────────────

    def _express(self, ci):
        from langchain_core.prompts import ChatPromptTemplate
        if getattr(self, "llm_budget", None) is not None:
            if not self.llm_budget.can_call("language"):
                logger.info("[LLMBudget] 主动表达语言预算不足，降级为不表达（CI 保持）")
                return False
            self.llm_budget.register("language", tokens=200)
        # 模板文本经语言注入面取（CognitiveLanguageAccess.system_prompt，
        # 2026-10 两仓拆分：认知模块不再直接 import 陪伴侧 prompt_templates；
        # system_prompt 缺席=遗留环境，回退旧直连，逐字等价）。
        _sp = getattr(self.nlp, "system_prompt", None)
        ea = ci.extra_attrs
        with self.kg._lock:
            ctx = "\n".join(
                f"- {b} ({self.kg.nodes[b].label},{self.kg.nodes[b].graph_space})"
                for b in ea.get("basis", [])[:5] if self.kg.get_node(b))
        goal = ea.get("reply_goal") or "share_related_knowledge"
        if _sp is not None:
            system = _sp("proactive_express")
        else:
            from prompt_templates import build_prompt
            system = build_prompt("proactive_express")
        user = f"""【想法来源（图谱节点，是你的记忆与联想，不是查询结果）】
{ctx}

【为什么想表达】
激活度 {ea.get('activation')}；表达目标 {ea.get('reply_goal')}；
言外行为 {ea.get('illocutionary_act')}；对话行为 {ea.get('dialogue_act')}

请以 FAS 的身份，把这个想法用一两句自然的话向用户表达（第一人称，像想起什么随口说，禁止机制词汇）："""
        try:
            prompt = ChatPromptTemplate.from_messages(
                [("system", system), ("human", "{input}")],
                template_format="mustache")
            import fas_log
            with fas_log.llm_purpose("proactive_expression"):
                resp = (prompt | self.nlp.chat_llm).invoke({"input": user})
            text = (resp.content if hasattr(resp, "content") else str(resp)).strip()
        except Exception as e:
            logger.warning(f"[CC] 语言生成失败: {e}")
            return False
        if not text:
            return False

        now = time.time()
        self._last_express_ts = now
        self._express_count_hour.append(now)
        with self.kg._lock:
            ea = ci.extra_attrs
            ea["status"] = ST_EXPRESSED
            ea["expressed_at"] = now_str()
            ea["expressed_text"] = text[:200]
            self._evo.note_node_updated(ci.id, {"status": ST_EXPRESSED})
        if self.chat_log:
            try:
                self.chat_log.record(user_input="(主动)", system_response=text,
                                     proactive=True)
            except Exception as e:
                logger.warning(f"[CC] 对话记忆写入失败: {e}")
        if getattr(self, "_save_fn", None):
            try: self._save_fn()
            except Exception as e: logger.warning(f"[CC] 落盘失败: {e}")
        self._queue.append({
            "ci_id": ci.id, "text": text,
            "basis": ea.get("basis", [])[:5],
            "goal": ea.get("reply_goal"),
            "illocutionary_act": ea.get("illocutionary_act"),
            "dialogue_act": ea.get("dialogue_act"),
            "expression_eval": ea.get("expression_eval"),
            "time": now_str(),
        })
        # 主动表达也是一次 FAS 表达事件（进入 Reflection Evolution 闭环）
        try:
            if self.buffer:
                self.buffer.add_expression({
                    "user_text": "",
                    "answer": text,
                    "behavior": "share",
                    "context": "情境:主动发起",
                    "context_ids": ["情境:主动发起"],
                    "topics": ea.get("basis", [])[:5],
                    "proactive": True,
                })
        except Exception:
            pass
        logger.info(f"[CC] 主动表达: {ci.id} → {text[:60]}")
        # B5/§5：话要有嘴——产出的句子交给行动层作为 communicate 动作执行
        # （过 propose 守卫/调度/结算回执），不绕过通道层直接 say。
        # 网页 _queue 轮询保留：同一次表达的两个出口（屏幕+世界），非双写。
        try:
            self._propose_communicate(ci, text)
        except Exception as e:
            logger.debug(f"[CC] 主动表达投递异常（不影响表达留痕）: {e}")
        return True

    # ── 主动投递（B5/§5：CI → communicate 动作 → 通道 → 真实回执）──

    def _propose_communicate(self, ci, text, note=None):
        """把已表达的话提交为 communicate 动作。

        关键时序：**先**把 action_id 写进 CI.delivery.pending，**再** propose
        ——同步执行的 communicate 会在 propose 调用栈内完成结算并回调
        note_delivery；顺序反了回执就找不到这个 CI（假丢失）。
        被拒（忙/内核守卫）→ pending_retry，≥retry_min_s 后由
        _retry_pending_delivery 一次性再试；不是插队，是一次执行机会。
        """
        import uuid
        am = getattr(self, "action_manager", None)
        aid = f"act_{uuid.uuid4().hex[:8]}"
        with self.kg._lock:
            ea = ci.extra_attrs
            prev_att = int((ea.get("delivery") or {}).get("attempts", 0))
            ea["delivery"] = {"status": "pending", "action_id": aid,
                              "proposed_at": now_str(), "attempts": prev_att + 1}
        if am is None:
            with self.kg._lock:
                ci.extra_attrs["delivery"] = {
                    "status": "no_action_manager", "action_id": aid,
                    "proposed_at": now_str(), "attempts": prev_att + 1}
            return False
        reason = [ci.id] + ([str(note)] if note else [])
        spec = {"action_type": "communicate", "target": "用户",
                "action_id": aid, "params": {"text": str(text)[:120]},
                "motivation": "self_expression", "priority": 0.55,
                "reason": reason}
        try:
            res = am.propose(spec, source="cognition") or {}
        except Exception as e:
            res = {"started": False, "queued": False, "reason": str(e)[:100]}
        accepted = bool(res.get("started") or res.get("queued"))
        if not accepted:
            rc = float((self._loop_cfg.get("delivery") or {})
                       .get("retry_min_s", 60.0))
            with self.kg._lock:
                ea = ci.extra_attrs
                d = dict(ea.get("delivery") or {})
                d.update({"status": "pending_retry",
                          "retry_after": time.time() + rc,
                          "reason": str(res.get("reason") or "")[:120]})
                ea["delivery"] = d
            import fas_log
            fas_log.get_logger(fas_log.COMMUNICATION).info(
                "delivery_deferred", "主动表达被排队/守卫拒绝，延后一次再试",
                ci_id=ci.id, action_id=aid,
                reason=str(res.get("reason") or "")[:120])
        return accepted

    def note_delivery(self, action_id, result, success):
        """行动层结算回执（由 action_system._settle 经 self.cc 回调）。

        只认领 delivery.action_id 匹配且仍在 pending 的 CI——其余动作
        过一遍就返回。送达与否**只认回执**：没有回执就一直 pending，
        绝不自判"说了"。"""
        if not action_id:
            return False
        hit = None
        with self.kg._lock:
            for n in self.kg.nodes.values():
                ea = n.extra_attrs or {}
                if ea.get("type") not in self._INTENTION_TYPES:
                    continue
                d = ea.get("delivery") or {}
                if d.get("action_id") == action_id \
                        and d.get("status") == "pending":
                    d["status"] = "delivered" if success else "say_failed"
                    d["settled_at"] = now_str()
                    d["success"] = bool(success)
                    d["detail"] = str((result or {}).get("result")
                                      or (result or {}).get("detail") or "")[:120]
                    if not success:
                        d["reason"] = str((result or {}).get("reason") or "")[:120]
                    ea["delivery"] = d
                    hit = (n.id, ea.get("expressed_text"), d["status"])
                    break
        if hit:
            import fas_log
            fas_log.get_logger(fas_log.COMMUNICATION).info(
                "delivery_settled",
                f"{hit[2]}: {str(hit[1] or '')[:40]}",
                ci_id=hit[0], action_id=action_id, success=bool(success))
        return bool(hit)

    def _retry_pending_delivery(self):
        """pending_retry 的一次性再试（≥retry_min_s 到点才动）。

        再试仍被拒 → abandoned：不反复贴嘴（防刷屏语义），也不假装送达。"""
        now = time.time()
        due = []
        with self.kg._lock:
            for n in self.kg.nodes.values():
                ea = n.extra_attrs or {}
                if ea.get("type") not in self._INTENTION_TYPES:
                    continue
                d = ea.get("delivery") or {}
                if d.get("status") == "pending_retry" \
                        and float(d.get("retry_after", 0)) <= now \
                        and int(d.get("attempts", 1)) < 2:
                    due.append((n, str(ea.get("expressed_text") or "")))
        for n, text in due:
            if not text:
                with self.kg._lock:
                    d = dict(n.extra_attrs.get("delivery") or {})
                    d["status"] = "abandoned"
                    d["reason"] = d.get("reason") or "no_text"
                    n.extra_attrs["delivery"] = d
                continue
            try:
                self._propose_communicate(n, text, note="retry_once")
            except Exception as e:
                logger.debug(f"[CC] 投递再试异常: {e}")
            with self.kg._lock:
                d = n.extra_attrs.get("delivery") or {}
                if d.get("status") == "pending_retry":
                    d["status"] = "abandoned"   # 一次性机会，到此为止
                    n.extra_attrs["delivery"] = d
                    import fas_log
                    fas_log.get_logger(fas_log.COMMUNICATION).info(
                        "delivery_abandoned", "再试仍被拒，放弃本次投递",
                        ci_id=n.id, reason=str(d.get("reason") or "")[:120])

    # ── 论文 §3.3：离线自主思考三机制 ─────────────────────

    # ── 重新点亮（reactivation）：全图 eligible 竞争，非随机回忆 ──
    # 语义：让已衰减、暂时离开认知场的**任何普通节点**重获进入认知过程的
    # 机会——概念/情景/人物/情绪/Drive/能力/未知/MC 对象一律有资格；
    # 点亮后仍须经过正常 diffusion/竞争，不会直达意图。

    # ── 经验重放（replay）：空闲时把少量高价值经验重新接进扩散 ──
    # 定位：调度/入口层，不是第二套学习机制（experience_replay 模块注释）。
    # 本门只判断"现在是否轮到它跑"——节流/预算/零收益退避在 replay 层内；
    # 抢占由所在分支保证：对话回合整个非 busy 段不执行，动作执行中不进。

    def _replay_gate(self):
        try:
            rp = self._replay
            if rp is None:
                if not (self.cfg.get("replay") or {}).get("enabled", True):
                    return
                from experience_replay import ExperienceReplay
                rp = self._replay = ExperienceReplay(
                    self.kg, self.engine, self.cfg.get("replay") or {})
            am = self.action_manager
            if am is not None and am.busy():
                return                   # 行动优先：做事时不重放
            rp.gate(timeline=self.timeline,
                    causal=getattr(am, "causal", None))
        except Exception as e:
            logger.debug(f"[CC] 经验重放跳过: {e}")

    def _reactivate_gate(self):
        rc = (self._loop_cfg.get("reactivation") or {})
        now = time.time()
        if now - self._last_react_ts < \
                float(rc.get("min_interval_ticks", 2)) * \
                float(self.cfg["tick_seconds"]):
            return                       # cooldown 下限（防连点），非闹钟
        # 认知场过冷：当前 top-k 平均激活不足以维持多样火种
        temp = self._field_temperature()
        if temp is None:
            # L1-DGR-05:温度探测失败,本拍跳过冷场判定
            import logging
            logging.getLogger(__name__).debug(
                "[CC] 场温度探测失败，再点火门本拍跳过")
            return
        cold = float(self._mod_th("reactivation.cold_field",
                                  float(rc.get("cold_field_activation", 0.35))))
        if temp >= cold:
            self._cold_beats = 0               # 回暖即清零（不是闹钟，是持续度）
            return
        # R2 P8（D-3）：`retrieval.reignite_every` 的真消费者 —— **冷场要持续多久**才
        # 值得再点火（防抖，不是定时器：触发条件仍然是"场冷"，这里只规定它必须连续
        # 成立 N 拍）。CEN 高→拉长（别打断聚焦），DMN 高→缩短（发散态欢迎翻旧账）。
        # 计数单位是本门自己观测到的冷拍数，不用 `self._tick`：tick 节拍与门节拍
        # 不同源（对话期 tick 会停），用绝对 tick 差值会让"冷场持续度"被暂停污染。
        every = max(1, int(round(self._mod_th(
            "retrieval.reignite_every", float(self.cfg["reignite_every_pulses"])))))
        self._cold_beats += 1
        if self._cold_beats < every:
            return
        self._last_react_ts = now
        self._cold_beats = 0
        self._reactivate_field(rc, temp)

    def _field_temperature(self):
        """当前激活场温度：top-k 平均激活（engine 缺位回退图扫描）。"""
        try:
            topk, _ = self.engine.get_topk(k=10)
        except Exception:
            # L1-DGR-05:失败返回 None 由门跳过判定,避免 0.0 被误判
            # "过冷"→触发再点火,方向性放大错误。
            return None
        acts = [float(n.activation or 0.0) for n in topk if n.activation > 0]
        return (sum(acts) / len(acts)) if acts else 0.0

    def _reactivate_field(self, rc: dict, temp: float):
        import math
        import random
        w = rc.get("weights") or {}
        half_life = max(30.0, float(rc.get("half_life_s", 900)))
        window = float(rc.get("recent_window_s", 180))
        count = max(1, int(rc.get("count", 4)))
        now_ts = time.time()
        with self.kg._lock:
            nodes = self.kg.nodes
            # 排除：引擎零件、坐标、进行中的意图/思考/记录类产物、
            # 以及还热着（≥0.3 激活）的节点——它们不需要重新点亮
            INFRA = {nid for nid, n in nodes.items()
                     if n.label in ENGINE_PART_LABELS}
            hot = {n.id for n in nodes.values()
                   if float(n.activation or 0.0) >= 0.3}
        # L1-DGR-02:锁序 engine→kg;get_topk 不得在 kg._lock 内取(此处两锁混用)
        try:
            focus_ids = {n.id for n in self.engine.get_topk(k=8)[0]}
        except Exception as e:
            # L1-DGR-05:探测失败不静默——空集让再点火失去焦点约束,退化为全图随机
            import logging
            logging.getLogger(__name__).warning(
                "[CC] focus_ids 探测失败，再点火进入无焦点模式: %r", e)
            focus_ids = set()
        with self.kg._lock:
            nodes = self.kg.nodes
            drive_ids = {nid for nid, n in nodes.items()
                         if (n.extra_attrs or {}).get("type") in
                         ("drive", "emotion", "cognitive_network")}
            drive_nbrs = set()
            for d in list(drive_ids)[:8]:
                for e in self.kg.get_out_edges(d):
                    drive_nbrs.add(e.dst)
            scored = []
            for nid, n in nodes.items():
                if nid in INFRA or nid in hot or nid in focus_ids:
                    continue
                if str(nid).startswith(("CI_", "思考_", "反思_", "回答记录_",
                                         "搜索记录_", "文件操作记录_",
                                         "表达_", "坐标(")):
                    continue
                ea = n.extra_attrs or {}
                if ea.get("type") in ("dynamic_state", "inventory_item"):
                    continue   # 世界状态由感知节拍维护，不参与联想点火
                la = getattr(n, "last_access", None)
                gap = half_life                      # 无时间戳：视为已久
                if hasattr(la, "timestamp"):
                    gap = now_ts - la.timestamp()
                elif isinstance(la, str) and la:
                    try:
                        from datetime import datetime as _dt
                        t = _dt.strptime(
                            la, "%Y/%m/%d %H:%M:%S").timestamp()
                        gap = now_ts - t
                    except ValueError:
                        gap = half_life
                # eligibility 分量（权重是数据表，非硬公式）
                recency_gap = 1.0 - math.exp(-max(0.0, gap) / half_life)
                coupling = 0.0
                for e in self.kg.get_out_edges(nid):
                    if e.dst in focus_ids:
                        coupling = max(coupling, float(e.weight or 0))
                for e in self.kg.get_in_edges(nid):
                    if e.src in focus_ids:
                        coupling = max(coupling, float(e.weight or 0))
                drive_tie = 0.5 if nid in drive_nbrs else 0.0
                novel = 0.0 if (now_ts - self._reactivated_recent.get(
                    nid, 0.0)) < window else 1.0
                s = (float(w.get("importance", 0.25)) * min(1.0, float(n.weight))
                     + float(w.get("recency_gap", 0.20)) * recency_gap
                     + float(w.get("field_coupling", 0.25)) * coupling
                     + float(w.get("drive_tie", 0.15)) * drive_tie
                     + float(w.get("novelty", 0.10)) * novel
                     + float(w.get("noise", 0.15)) * random.random())
                s *= float(rc.get("recent_penalty", 0.6)) if novel == 0.0 else 1.0
                scored.append((s, nid, {"gap_s": round(gap),
                                        "coupling": round(coupling, 2),
                                        "drive_tie": drive_tie,
                                        "novelty": novel}))
            scored.sort(key=lambda x: -x[0])
            picks = scored[:count]
            if not picks:
                return
            boost = float(rc.get("boost", 1.2)) * self._mod_th(
                "reactivation.boost_scale", 1.0)
            wake, detail = [], []
            for s, nid, why in picks:
                n = nodes[nid]
                n.activation = min(3.0, float(n.activation or 0.0) + boost
                                   * max(0.25, min(1.0, s)))
                n.touch()
                wake.append(nid)
                self._reactivated_recent[nid] = now_ts
                detail.append(f"{nid}(score={s:.2f},gap={why['gap_s']}s,"
                              f"tie={why['coupling']})")
            self.engine.mark_active(wake)
            # 内源注入（reactivation）：发射免扣——点亮是"回忆的火种"
            # 不是注意资源的转移
            self.engine.register_activation_source(wake, "reactivation")
        self._last_reactivation = {"field_temperature": round(temp, 3),
                                    "picked": detail}
        logger.info(f"[CC] 认知场再点火（场温 {temp:.2f}<{self._mod_th('reactivation.cold_field', 0.35):.2f}）: "
                    + "; ".join(detail[:4]))

    def _free_think(self):
        """自由思考/思维跳跃：Self 扩散 TopK → 一句内心独白（1 次 LLM）。
        LLM 参与模式 MODE4_REFLECT 级；预算不足时跳过（慢通道降级，图谱继续）。"""
        if self.nlp is None:
            return
        now = time.time()
        if now - self._last_thought_ts < self.cfg["thought_min_interval_s"]:
            return
        if getattr(self, "llm_budget", None) is not None:
            if not self.llm_budget.can_call("reflect"):
                logger.info("[LLMBudget] 自由思考预算不足，跳过（图谱继续）")
                return
            self.llm_budget.register("reflect", tokens=300)
        self._last_thought_ts = now
        try:
            tid = self.engine.generate_thought(self.nlp, k=8)
            if tid:
                logger.info(f"[CC] 自由思考: {tid}")
        except Exception as e:
            logger.warning(f"[CC] 自由思考失败: {e}")

    def _maybe_offline_reflect(self):
        """反思性自问：由反思压力驱动（行动失败/负反馈/预测误差/意图消散
        等信号在图上攒出的持续压力），压力过阈值才反思；时间只是 cooldown，
        不是触发器。反思输出结构化进图谱（self-model/disposition/causal）。"""
        if self.reflection is None or not self.cfg.get("offline_reflection"):
            return
        pcfg = self._pressure_cfg()
        now = time.time()
        if now - self._last_offline_reflect < float(pcfg.get("cooldown_s", 600)):
            return                       # cooldown：两次反思的最小距离
        pressure = self.pressure_value()
        if pressure < float(pcfg.get("threshold", 1.2)):
            return                       # 压力不足：不反思（合法）
        # 收尾修复 2026-09-22：离线反思也是 LLM 慢通道，须与 _free_think
        # 同走预算账（旧版只有压力阈值一道闸，预算体系对它失明）。
        # 预算不足时不消耗压力、不进 cooldown——压力仍在，下拍重试。
        if getattr(self, "llm_budget", None) is not None:
            if not self.llm_budget.can_call("reflect"):
                logger.info("[LLMBudget] 离线反思预算不足，跳过（压力保留，下拍重试）")
                return
            self.llm_budget.register("reflect", tokens=600)
        self._last_offline_reflect = now
        try:
            with self.engine._lock:
                topk, _ = self.engine.get_topk(k=15)
            src = None
            with self.kg._lock:
                pn = self.kg.get_node(str(pcfg.get("node") or "反思压力"))
                if pn is not None:
                    src = dict((pn.extra_attrs or {}).get("sources") or {})
                    # 消耗压力（反思本身就是消化动作）
                    ea = dict(pn.extra_attrs or {})
                    ea["last_reflection_at"] = now_str()
                    ea["last_reflection_pressure"] = round(pressure, 2)
                    ea["sources"] = {k: round(v * 0.3, 3) for k, v in src.items()}
                    pn.extra_attrs = ea
                    pn.activation = float(pn.activation or 0.0) * 0.3
                    pn.touch()
            logger.info(f"[CC] 反思压力过阈值（{pressure:.2f}，来源 "
                        f"{sorted(src or {}, key=lambda k: -(src or {}).get(k, 0))[:3]}"
                        f"），触发离线反思")
            r = self.reflection.run(trigger="pressure",
                                     topk_nodes=topk,
                                     chat_log_entries=self.chat_log.recent(n=10) if self.chat_log else None)
            if r and not r.get("skipped"):
                logger.info(f"[CC] 离线反思完成: {r.get('reflection_id')}")
        except Exception as e:
            logger.warning(f"[CC] 离线反思失败: {e}")

    # ── 对外接口 ──────────────────────────────────────────

    def poll(self) -> list:
        """前端轮询：取走已表达的主动消息。"""
        with self._lock:
            out, self._queue = self._queue, []
        return out

    def tick_express_check(self):
        """在 pulse 后调用：表达决策 + 语言生成（唯一 LLM 入口）。"""
        try:
            # §16 实验屏蔽 idle_companion：空闲自发表达节拍停摆（CI 照常
            # 形成/滞留，只是不再产生"随口说话"这一行为出口）。
            import experiment_mode as _xm
            if _xm.shield("idle_companion"):
                return
        except Exception:
            pass
        ci, val = self._evaluate_expression()
        if ci is not None:
            self._express(ci)
        # B5：到期的延后投递（每拍扫一遍 pending_retry，零 LLM）
        try:
            self._retry_pending_delivery()
        except Exception as e:
            logger.debug(f"[CC] 投递再试跳过: {e}")

    def state(self) -> dict:
        with self.kg._lock:
            cis = [{
                "id": n.id,
                "kind": n.extra_attrs.get("kind"),
                "status": n.extra_attrs.get("status"),
                "activation": n.extra_attrs.get("activation"),
                "basis": n.extra_attrs.get("basis", []),
                "goal": n.extra_attrs.get("reply_goal"),
                "illocutionary_act": n.extra_attrs.get("illocutionary_act"),
                "explain": n.extra_attrs.get("explain"),
                "expression_eval": n.extra_attrs.get("expression_eval"),
                "delivery": n.extra_attrs.get("delivery"),
                "created": n.extra_attrs.get("created"),
            } for n in self.kg.nodes.values()
                if (n.extra_attrs or {}).get("type") in self._INTENTION_TYPES]
        return {"tick": self._tick, "busy": self._busy.is_set(),
                "thresholds": {
                    "form": round(self._mod_th("cognition.form_threshold",
                                               float(self.cfg["form_threshold"])), 3),
                    "express": round(self._mod_th("cognition.express_threshold",
                                                  float(self.cfg["express_threshold"])), 3),
                    "form_base": self.cfg["form_threshold"],
                    "express_base": self.cfg["express_threshold"]},
                "reflection_pressure": round(self.pressure_value(), 3),
                "last_reactivation": getattr(self, "_last_reactivation", None),
                "field_change": getattr(self, "_last_field_change", None),
                "queue": len(self._queue),
                "cooldown_remaining": max(0, int(self.cfg["inhibition_cooldown_s"]
                    - (time.time() - self._last_express_ts))),
                "intentions": sorted(cis, key=lambda c: -(c.get("activation") or 0))}
