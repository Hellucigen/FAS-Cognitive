# reward.py — 奖赏评估中间层（transient runtime layer）
# ============================================================================
# 定位（人格学习架构改造 2026-09）：
#   打断「用户喜欢 → 行为倾向增加」的单通路，插入一层**事件级奖赏评估**：
#
#     experience → outcome（分 social / self 两路）
#                → RewardEvent（瞬时对象）
#                → 激素/受体系统（internal_state 的调制变量：多巴胺样 tonic/phasic
#                   = 预期与奖励预测误差；催产素样 = 社交联结；皮质醇样 = 威胁/挫败）
#                → learning modulation（调制"这次经历值多少学习"）
#                → disposition 更新（由代码计算，写入 self 图）
#
#   红线：
#     1. RewardEvent / 调制值都是**运行时**概念，不新增任何平行人格 JSON；
#        长期结果仍只写 self 图（disposition 边权 + 节点计数）。
#     2. 激素不是人格：本层绝不因为"多巴胺高"而创建/提升任何 disposition——
#        它只缩放学习率（learning_signal 的乘数）。
#     3. 奖赏数值由代码按规则计算，LLM 不参与本模块。
#     4. 与 internal_state 的关系：激素读写仍走 internal_state 唯一写入口
#        （不变量 I1）；本模块是它的第一个真实消费者（record_reward/
#        expectations 此前是预留接口）。
#
# 功能抽象声明（与 internal_state.py 一致）：多巴胺样/催产素样/皮质醇样是
# 计算模型中的功能类比（奖励预测、社交价值、威胁学习），不声称复现生理系统。
# ============================================================================

import logging
import threading
import time
import uuid

logger = logging.getLogger(__name__)

# ── 社会性 outcome（人际互动结果；由 classify_outcome 的 detail 细化）──
# 与旧的 positive/negative/neutral/ambiguous 一一对应可回推（见 social_to_legacy）。
SOCIAL_VALENCE = {
    "accepted":             0.50,   # 被接纳/肯定
    "amused":               0.60,   # 用户被逗乐（哈哈/笑死）
    "engaged":              0.45,   # 回应了她的问题（互动继续）
    "continued_discussion": 0.40,   # 用户沿同话题继续（最稳的社交正反馈）
    "recognized":           0.30,   # 一般性认可（对啊/没错）
    "neutral":              0.00,
    "ambiguous":             0.00,   # 方向不明：纯观察，不臆断正负（§九）
    "ignored":             -0.15,   # 话题切换/没接（不等于讨厌——§九）
    "timeout":             -0.10,   # 长时间中断
    "rejected":            -0.50,   # 明确抵触（"闭嘴/别问"）
}
# classify_outcome 的 (polarity, detail) → 细化 social_outcome 标签
_SOCIAL_BY_DETAIL = {
    ("positive", "用户继续同话题"): "continued_discussion",
    ("positive", "回应了FAS的提问"): "engaged",
    ("positive", "用户积极回应"):   "accepted",
    ("negative", "用户明确抵触"):   "rejected",
    ("neutral",  "话题切换"):       "ignored",
    ("neutral",  "对话长时间中断"): "timeout",
    ("ambiguous", "积极词与抵触词同现，反馈方向不明"): "ambiguous",
}
_AMUSE_MARKERS = ("哈哈", "笑死", "好家伙")


def classify_social_outcome(polarity: str, detail: str = "",
                            user_text: str = "") -> str:
    """把 classify_outcome 的 (positive/negative/neutral/ambiguous, detail)
    细化为社会性 outcome。笑场单列（amused）——它是社交信号，但不等于
    "FAS 也该喜欢这个话题"。"""
    d = str(detail or "").strip()
    p = str(polarity or "neutral").strip()
    if p == "positive" and any(m in str(user_text or "") for m in _AMUSE_MARKERS):
        return "amused"
    return _SOCIAL_BY_DETAIL.get((p, d)) or {
        "positive": "accepted", "negative": "rejected",
        "neutral": "neutral", "ambiguous": "ambiguous"}.get(p, "neutral")


def social_to_legacy(social_outcome: str) -> str:
    """旧四态字段的兼容投影（disposition 旧调用方/旧图数据仍认它）。"""
    v = SOCIAL_VALENCE.get(social_outcome, 0.0)
    if v > 0.15:
        return "positive"
    if v < -0.15:
        return "negative"
    return "ambiguous" if social_outcome == "ambiguous" else "neutral"


# ── 自身 outcome（对 FAS 自己的目标/探索/知识/行动结果）──
SELF_VALENCE = {
    "discovery":      0.80,   # 发现新东西（未知对象被看清/新知识获得）
    "knowledge_gain": 0.55,   # 本轮学到/沉淀了知识
    "completion":     0.70,   # 目标完成
    "goal_success":   0.60,   # 自身目标成功
    "progress":       0.30,   # 部分推进（done=partial）
    "novelty":        0.40,   # 单纯新奇接触
    "neutral":        0.00,
    "blocked":       -0.20,   # 环境阻碍：没做到 ≠ 做错了，轻负
    "goal_failure":  -0.50,   # 自身目标失败
    "cancelled":      0.00,   # 主动/被打断取消：**不记负奖赏**（§十）
}
# 行动结果 → self_outcome 的映射规则（供 autonomy 复用，集中一处）
def classify_self_outcome(intent: str, success: bool, result: dict = None,
                          target_is_unknown: bool = False) -> str:
    result = result or {}
    if result.get("cancelled"):
        return "cancelled"
    status = str(result.get("status") or "")
    reason = str(result.get("reason") or "")
    if not success:
        blockedish = ("not_visible", "no_target", "not_in_safe", "locked",
                      "infeasible", "threat_not_visible", "unavailable",
                      "not_connected",
                      # 2026-09-22 收尾：寻路被卡/无路/不可达/投递失败是
                      # "世界此刻没让过"，不是决策失败——不记满额负奖赏
                      # 2026-09-24 追加 preempted：探索在出发拍被 night/danger
                      # 拦下（技能如实 res_fail），属"还没到能做的时机"
                      "stuck", "no_path", "unreachable", "say_failed",
                      # 2026-09-24 追加 path_stall：腿假活（回执 ok 零位移）
                      # 2026-09-25 追加 raw_gait_stalled：原始步态接管仍零位移
                      # （地形阻挡，与 path_stall 同类，口径与 autonomy 暂态集一致）
                      # 2026-09-25 §四D 追加 dug_no_item：方块挖开但背包零增益
                      # （游戏物理没掉落，不是采集行为本身失败）
                      "preempted", "path_stall", "raw_gait_stalled",
                      "dug_no_item",
                      # 2026-09-27 闭环一致性 §P2/P11：transport 类（桥超时/
                      # 回执丢失/轮询异常/执行器异常）是"没拿到可靠回执"，
                      # 不是动作失败——轻负 blocked 而非 goal_failure；真实
                      # 世界结果由感知迟到确认（CausalLearner late-confirmed，
                      # experience.py）最终裁决。
                      "bridge_error", "poll_error", "execution_error",
                      "timeout")
        if any(b in reason.lower() for b in blockedish):
            return "blocked"
        return "goal_failure"
    # 成功：探索家族 + 目标是"不认识的东西" → discovery（自身发现）。
    # §P6 证据门（2026-09-27 收敛修复）：discovery 必须带**真观察证据**
    # （found/collected/entity/block/dist/rel）——"凑近看了看但什么也没
    # 看见"只是动作成功，不是发现（防"执行成功误等于知识进展"）。
    d = result.get("result")
    if not isinstance(d, dict):
        d = result.get("detail")
    if not isinstance(d, dict):
        d = None
    evidence = (d is not None and (
        int(d.get("found") or 0) > 0
        or int(d.get("collected") or 0) > 0
        or d.get("entity") not in (None, "")
        or d.get("block") not in (None, "")
        or d.get("dist") is not None
        or d.get("rel") is not None))
    if intent in ("observe", "approach", "explore", "collect") and target_is_unknown:
        return "discovery" if evidence else (
            "progress" if status == "partial" else "goal_success")
    if status == "partial":
        return "progress"
    # §P6：探索家族空手而归（无证据、非 partial）= 执行成功但目标零进展
    # ——按 progress（0.30）而非 goal_success（0.60）记账，且不进
    # exploration_bias 性格证据（_TRAIT_RULES 只消费 goal_success）。
    if intent in ("explore_area", "explore_direction",
                  "explore_unknown_region") and not evidence:
        return "progress"
    if intent in ("observe", "approach", "explore", "collect", "withdraw"):
        return "goal_success"
    return "goal_success"


def new_event(source: str, outcome: str, valence: float, behavior: str = "",
              context: str = "", ref: str = "", cycle_id: str = "") -> dict:
    return {
        "event_id": "rw_" + uuid.uuid4().hex[:8],
        "ts": time.time(),
        "source": source,                # social | self
        "outcome": outcome,
        "valence": round(float(valence), 4),
        "intensity": round(abs(float(valence)), 4),
        "behavior": behavior,
        "context": context,
        "ref": str(ref or "")[:80],
        "cycle_id": cycle_id,
    }


# 学习调制的出厂系数（R2 P11 从本函数体里搬出来：§24「默认值集中管理」）。
# **真源是 config.modulator_system.learning_modulation**，这里是裸装配兜底，
# 两边同数——`tests/test_mood_one_way.py` 有一组源码扫描断言这个函数体里
# 不能再出现这几个字面量（否则改 config 就是假的）。
DEFAULT_LEARNING_MOD = {
    "base": 1.0, "phasic_coef": 0.60, "tonic_coef": 0.15,
    "stress_coef": 0.25, "surprise_coef": 0.30,
    "min": 0.4, "max": 1.8,
    "phasic_mod": "dopamine", "tonic_mod": "dopamine", "stress_mod": "cortisol",
}


class RewardSystem:
    """奖赏评估 + 激素写入 + 学习调制。

    生命周期内的对象（激素台账、预期表）存在 internal_state——它是
    运行时状态的唯一真源（含 data/internal_state.json 的既有定位）；
    本类自己**不落任何文件**，也不写图（长期人格只由 disposition 更新写图）。
    """

    ALPHA_EXPECT = 0.15          # 预期更新速率（慢学习）
    # R2 P7：原先这里的 PHASIC_SCALE/SOCIAL_OXY/NEG_CORT/DOPA_LEVEL_POS/
    # DOPA_LEVEL_NEG/SERO_NEG 六个常数**已迁入事件表**
    # （config.modulator_system.event_rules → 图上的 `事件类型:* -[影响]-> 调制器` 边）。
    # 逐激素 if/else 不再是本模块的形状：release() 只构造事件，扇出由图决定。

    def __init__(self, internal_state=None, config=None):
        self.is_state = internal_state
        self.config = config or {}
        self._lock = threading.RLock()
        self._recent = []        # 运行时 trace 环（非持久层）
        self._mod_engine = None  # ModulatorEngine（惰性构造，kg 可能稍后才挂上）

    # ── 事件应用器（P7）────────────────────────────────────

    def mod_engine(self):
        """惰性拿到 ModulatorEngine；图谱/状态对象挂上后自动 attach。

        没有 internal_state 就没有应用器（release() 本来也在此情况下直接返回）。
        """
        if self.is_state is None:
            return None
        if self._mod_engine is None:
            try:
                import modulation_events as ME
                self._mod_engine = ME.ModulatorEngine(
                    getattr(self.is_state, "kg", None), self.is_state, self.config)
            except Exception as e:                      # noqa: BLE001
                logger.warning(f"[Modulation] 事件应用器构造失败，本轮激素响应退回"
                               f"旧路径（无）: {e}")
                return None
        else:
            # app.py 里 kg 可能晚于 RewardSystem 就位；补挂一次，避免永久退回出厂表
            kg = getattr(self.is_state, "kg", None)
            if kg is not None and self._mod_engine.kg is None:
                self._mod_engine.attach(kg=kg)
        return self._mod_engine

    def emit_modulation(self, ev: dict, valence: float, rpe: float) -> list:
        """把一个奖赏事件翻成**结构化调制事件**并交给图上的扇出。

        两条事件而不是一条：奖励预测误差（快、只对自己的目标结果）与
        结果效价（慢、社交/自身各有靶点）在语义上本就是两件事——旧代码把它们
        塞进同一个 if 树里，才需要逐激素分支。
        """
        eng = self.mod_engine()
        if eng is None:
            return []
        social = 1.0 if ev["source"] == "social" else 0.0
        common = dict(source=f"reward:{ev['source']}", valence=valence,
                      intensity=abs(valence), rpe=rpe,
                      social_relevance=social, goal_relevance=1.0 - social,
                      success=valence > 0, cycle_id=ev.get("cycle_id"),
                      ref=str(ev.get("outcome") or ""))
        out = [eng.emit("reward_rpe", **common)]
        out.append(eng.emit("reward_success" if valence > 0 else "reward_failure",
                            **common))
        return out

    # ── 事件评估：outcome 两路 → RewardEvent 列表 ──────────

    def evaluate(self, behavior: str = "", context: str = "",
                 social_outcome: str = None, self_outcome: str = None,
                 ref: str = "", cycle_id: str = "") -> list:
        events = []
        if social_outcome and social_outcome != "neutral":
            events.append(new_event(
                "social", social_outcome,
                SOCIAL_VALENCE.get(social_outcome, 0.0),
                behavior=behavior, context=context, ref=ref,
                cycle_id=cycle_id))
        elif social_outcome == "neutral":
            events.append(new_event(
                "social", "neutral", 0.0, behavior=behavior,
                context=context, ref=ref, cycle_id=cycle_id))
        if self_outcome and self_outcome != "neutral":
            events.append(new_event(
                "self", self_outcome,
                SELF_VALENCE.get(self_outcome, 0.0),
                behavior=behavior, context=context, ref=ref,
                cycle_id=cycle_id))
        return events

    # ── 激素/受体写入（全部经 internal_state 唯一写入口，I1）──

    def release(self, events: list) -> dict:
        """奖赏事件 → 调制事件 → 图驱动扇出 + 预期更新 + 奖励台账。

        cancelled 在这里被过滤（valence=0 不产生任何 delta）。
        **本函数不再决定哪个激素动多少**（R2 P7）：它只把"发生了什么"说清楚，
        扇出由 `事件类型:* -[影响 w]-> 调制器` 的边决定（modulation_events）。
        返回 {releases:[...], surprises:{key:rpe}, fanout:[...]} 供 trace 与调制。
        """
        releases = []
        surprises = {}
        fanout = []
        if self.is_state is None:
            return {"ok": False, "reason": "no_internal_state",
                    "releases": [], "surprises": {}, "fanout": []}
        for ev in events:
            key = f"{ev['behavior']}@{ev['context']}"
            v = float(ev["valence"])
            if v == 0.0:
                continue
            expected = self.is_state.expectation(key)
            rpe = v - expected                      # 奖励预测误差
            surprises[key] = round(rpe, 4)
            # 预期慢更新
            self.is_state.update_expectation(
                key, v, self.ALPHA_EXPECT, cycle_id=ev.get("cycle_id"))
            # 激素响应：构造结构化事件，交给图上的边（旧代码在这里是 6 个常数 ×
            # 逐激素 if/else；那些常数现在是 `影响` 边的权重）
            for r in self.emit_modulation(ev, v, rpe):
                for a in r.get("applied") or []:
                    fanout.append({"event": ev["event_id"], "type": r.get("event_type"),
                                   "mod": a["mod"], "delta": a["requested"],
                                   "channel": a["channel"], "gated": r.get("gated") or []})
            # 台账（internal_state 的记录者，不是人格存储）
            self.is_state.record_reward(
                cycle_id=ev.get("cycle_id"), reward_value=v,
                components={"valence": v, "expected": expected},
                prediction_error=rpe, source_event=ev["event_id"],
                explanation=f"{ev['source']}:{ev['outcome']} "
                            f"b={ev['behavior']}@{ev['context']}")
            releases.append({"event": ev["event_id"], "source": ev["source"],
                             "outcome": ev["outcome"], "rpe": round(rpe, 4)})
        with self._lock:
            self._recent.extend(events)
            del self._recent[:-20]
        return {"ok": True, "releases": releases, "surprises": surprises,
                "fanout": fanout}

    # ── 学习调制：激素改变"这次经历值多少学习"，不直接改人格 ──

    def _learning_mod(self) -> dict:
        spec = dict(DEFAULT_LEARNING_MOD)
        spec.update(((self.config.get("modulator_system") or {})
                     .get("learning_modulation") or {}))
        return spec

    def modulation(self, key: str = "", surprise: float = None) -> dict:
        """learning_modulation = 基线 + 脉冲项 + 慢分量项 − 压力项，×意外放大。

        系数全部来自 config.modulator_system.learning_modulation（R2 P11），
        范围 [min, max]（出厂 [0.4, 1.8]），全程可解释、可复算：
          factor = base
                 + phasic_coef × max(0, 脉冲)                 # 奖赏脉冲放大学习
                 + tonic_coef  × (tonic − baseline)           # 慢分量（不含脉冲）
                 − stress_coef × max(0, 压力侧 tonic − 其基线)  # 压力钝化学习
          若给了 surprise（|RPE|）→ ×(1 + surprise_coef × min(1,|s|))
          （意外的成功与意外的失败都值得多学一点；平淡重复少学。）
        三个调制器名（脉冲/慢分量/压力侧）也是 config 里的键——这一段没有任何
        按名字分支的 if（禁令 2），换一条通道去读只是改三个字符串。
        """
        lm = self._learning_mod()
        base = float(lm["base"])
        phasic = lev = bl = cort = cort_base = 0.0
        if self.is_state is not None:
            # 三个量各走各的接口，**不重复计数**：
            #   phasic = 快脉冲（learning 放大项）
            #   tonic  = 慢分量（不含 phasic；R2 前读的是 level，而 level 现在已经是
            #            tonic+phasic 的合成视图，再读就会把脉冲算两遍）
            phasic = self.is_state.modulator_pulse(lm["phasic_mod"])
            lev = self.is_state.modulator_tonic(lm["tonic_mod"])
            bl = self.is_state.modulator_baseline(lm["tonic_mod"])
            cort = self.is_state.modulator_tonic(lm["stress_mod"])
            cort_base = self.is_state.modulator_baseline(lm["stress_mod"])
        pc, tc, sc = (float(lm["phasic_coef"]), float(lm["tonic_coef"]),
                      float(lm["stress_coef"]))
        p_term = pc * max(0.0, phasic)
        t_term = tc * (lev - bl)
        s_term = -sc * max(0.0, cort - cort_base)
        f = base + p_term + t_term + s_term
        comp = {"base": base, "phasic": round(p_term, 3),
                "tonic": round(t_term, 3), "stress": round(s_term, 3)}
        if surprise is not None:
            s = max(0.0, min(1.0, abs(float(surprise))))
            xc = float(lm["surprise_coef"])
            f *= (1.0 + xc * s)
            comp["surprise"] = round(xc * s, 3)
        f = max(float(lm["min"]), min(float(lm["max"]), f))
        comp["factor"] = round(f, 3)
        return comp

    def recent_events(self, n: int = 10) -> list:
        with self._lock:
            return list(self._recent)[-n:]
