# personality_baseline.py — 基线人格层（初始倾向先验 + 心情瞬态）
# ============================================================================
# 设计原则（人格任务规范）：
#   1. 基线人格 = 出厂时的初始心理倾向先验，不是最终人格——
#      播种进现有 disposition 骨架，此后由 outcome 反馈自然演化。
#   2. 心情（mood）是完全瞬态的内部状态：valence ∈ [-1,1]，
#      随交互事件偏移、随时间衰减回 0。影响语气，不是人格本体。
#   3. 内部机制不外显：心情只作为语言层的语气上下文，禁止机制词汇。
#
# R2 P11（§15/§16）把第 2 条补全成"**派生**读数"：
#   valence = clamp(事件余韵 + 调制器包络, −1, +1)
# 包络那一项**不写死在这里**：权重是图上的 `调制器 -[w]-> 心情` 边（出厂拓扑见
# config.modulator_system.edges，编译见 modulator_subgraph.mood_projection），
# 本模块只通过 provider 闭包取那个已经算好的标量。于是：
#   · 单向：本模块只**读**调制器，全仓没有任何"心情写激素"的调用（闸门
#     tests/test_mood_one_way.py 从三个角度钉这条：数值指纹不变、心情节点出边
#     为 0、源码里没有任何调制器写入口）。
#   · 有界：包络钳在 ±mood.cap，事件余韵钳在 ±1，和也钳在 ±1。
#   · 不是万能变量：读者只有三个——语气上下文（本模块）、社交动机
#     （autonomy._motivation_value）、低落张力（drive_engine._mood_deficit）。
#     它不进 effects 表、不进 Prompt 的机制段、不决定回答内容（禁令 4）。
# 注意"没有环路"指的是**数值不反哺**：Modulator→mood→行为→结果→事件→调制器
# 这条**行为**环恰恰是规格要的（人格只能经 Modulator→behavior→outcome→
# disposition→trait 改变），它经由真实世界的一拍，不是同拍的数值反馈。
# ============================================================================

import logging
import time

logger = logging.getLogger(__name__)

# ── 出厂初始倾向（context, behavior, prior_strength）────────
# 这些是"初始倾向先验"：低强度种子，之后由真实经历 outcome 强化/衰减。
# 依据：人格访谈确定的设计约束（亲密/主动/玩梗/敢表达/成长），非固定人格。
BASELINE_PRIORS = [
    ("情境:熟悉关系", "respond", 0.45),      # 熟悉的人面前自然接话
    ("情境:熟悉关系", "empathize", 0.35),    # 亲密关系的基础敏感度
    ("情境:用户分享", "respond", 0.40),      # 对分享有基础兴趣
    ("情境:用户情绪表达", "empathize", 0.30),  # 情绪在场时的基础共情（不模板化）
    ("情境:用户提问", "respond", 0.45),
    ("情境:用户观点", "respond", 0.30),      # 观点话题愿意接
]

# 心情瞬态参数。**出厂真源是 config.personality.mood**（§24），下面是
# 没接线（裸构造）时的兜底——两者同数，仓库惯例：裸装配必须复现生产形状。
MOOD_DECAY_PER_HOUR = 0.15      # 每小时向 0 衰减（线性）
MOOD_EVENT_EFFECTS = {          # outcome/事件 → valence 偏移
    "positive": +0.12,
    "negative": -0.18,
}


def _mood_band(val: float) -> str:
    """valence → 中文档位词。**刻意不进 config**：这是展示文案不是仿真参数，
    给它开一个"出厂可覆盖"的键就是造一个没人拧的旋钮（审计 D-3 的教训）。"""
    if val > 0.25:
        return "不错"
    if val > 0.05:
        return "平静偏暖"
    if val > -0.05:
        return "平静"
    if val > -0.25:
        return "有点低落"
    return "低落"


class PersonalityBaseline:
    """基线人格先验播种 + 心情瞬态。全部长在现有 self 图上。"""

    def __init__(self, kg, disposition_store, config: dict = None, clock=None):
        self.kg = kg
        self.dispositions = disposition_store
        mood_cfg = ((config or {}).get("personality") or {}).get("mood") or {}
        self._mood_decay = float(mood_cfg.get("decay_per_hour",
                                              MOOD_DECAY_PER_HOUR))
        self._mood_effects = dict(MOOD_EVENT_EFFECTS)
        self._mood_effects.update(mood_cfg.get("event_effects") or {})
        # 时基与 InternalState 共用（R2 P10）：默认 `time.time` ⇒ 生产逐字不变；
        # 注入后心情的衰减与调制器衰减走同一条时间轴（实验/重放模式需要）。
        self._clock = clock or time.time
        self._mood_valence = 0.0
        self._mood_ts = self._clock()
        # 调制器包络的取值闭包（由装配方注入 modulator_subgraph.mood_projection）。
        # 与 `set_need_signal_provider` 同一个惯例：**没注入就没有这一项**，
        # 行为退回 R2 之前的"纯事件余韵"，不存在默认值顶替（观点来自图）。
        self._mood_source = None

    # ── 1. 出厂先验播种（幂等：只抬升低于先验的强度）─────

    def bootstrap(self):
        """把初始倾向作为先验种子写入 disposition 骨架。

        幂等约束（学习闭环修复 2026-09-18）：
          - 证据只在**首次创建**时记一条（source=baseline）——旧版每次启动
            都调 get_or_create(evidence_ref="出厂基线倾向")，导致 evidence_count
            被启动次数灌水（实测某 pair 证据环里 7 条全是启动播种），违反
            §六"每一次真实经历只能贡献有限 evidence"。
          - 强度地板重贴（衰减后低于先验时）不再重复记证据，用标记去重。
          - 先验是"起点"不是"经历"：来源标 baseline，与 reflection 区分。
        """
        seeded = 0
        for context, behavior, prior in BASELINE_PRIORS:
            was_new = self.dispositions.get_pair(context, behavior) is None
            self.dispositions.get_or_create(
                context, behavior,
                evidence_ref="出厂基线倾向" if was_new else "",
                source="baseline")
            pair = self.dispositions.get_pair(context, behavior)
            ea = pair.extra_attrs
            cur = float(ea.get("strength", 0))
            if cur < prior:
                ea["strength"] = prior
                ev = ea.setdefault("evidence", [])
                if "[baseline] 出厂先验" not in ev:
                    ev.append("[baseline] 出厂先验")
                    del ev[:-8]  # 证据环只保留最近 8 条（与 disposition_store 一致）
                self.dispositions._sync_activation_edge(pair)
                seeded += 1
        if seeded:
            logger.info(f"[Personality] 基线先验地板重贴/播种: {seeded} 条")
        return seeded

    # ── 2. 心情瞬态（事件余韵 + 调制器包络，派生且单向）────

    def set_mood_source(self, fn):
        """注入"调制器包络"的取值闭包（返回 mood_projection 那种 dict）。

        装配方（app.py）给的是 `lambda: modulator_subgraph.mood_projection(...)`。
        本对象**只读**它；反向（心情→调制器）没有任何入口（闸门测试钉这条）。
        """
        self._mood_source = fn

    def mood_event(self, kind: str):
        """交互事件对心情的偏移（正反馈/被拒绝/用户叫停等）——余韵那一项。"""
        try:
            # §16 实验屏蔽 mood：事件余韵不写（配合 app 侧包络不接线，
            # valence 恒等于装配时刻）；不改数据结构，撤盾即恢复。
            import experiment_mode as _xm
            if _xm.shield("mood"):
                return
        except Exception:
            pass
        delta = float(self._mood_effects.get(kind, 0.0) or 0.0)
        if delta:
            self._apply_mood_delta(delta)

    def _apply_mood_delta(self, delta: float):
        now = self._clock()
        hours = (now - self._mood_ts) / 3600.0
        # 衰减到事件时刻的值，再加新偏移（余韵钳在 ±1）
        self._mood_valence = max(-1.0, min(1.0,
            self._mood_valence * max(0.0, 1 - self._mood_decay * hours) + delta))
        self._mood_ts = now

    def afterglow(self) -> float:
        """事件余韵（未叠加包络的那一半），钳到 ±1。"""
        hours = (self._clock() - self._mood_ts) / 3600.0
        return max(-1.0, min(1.0,
            self._mood_valence * max(0.0, 1 - self._mood_decay * hours)))

    def _modulator_term(self) -> tuple:
        """调制器包络（图上的边算好的标量）。取不到 ⇒ 0，不编造默认值。"""
        fn = self._mood_source
        if fn is None:
            return 0.0, {"skipped": True, "edges_used": 0}
        try:
            out = fn() or {}
        except Exception as e:                       # noqa: BLE001
            logger.debug(f"[Personality] 调制器包络取值失败（本拍只算余韵）: {e}")
            return 0.0, {"error": str(e)[:120], "edges_used": 0}
        term = out.get("term")
        term = 0.0 if term is None else max(-1.0, min(1.0, float(term)))
        return term, out

    def current_mood(self) -> dict:
        """心情读数：valence = clamp(余韵 + 包络)。分项全部回传（可解释、可复算）。"""
        glow = self.afterglow()
        term, proj = self._modulator_term()
        raw = glow + term
        val = max(-1.0, min(1.0, raw))
        desc = _mood_band(val)
        return {"valence": round(val, 3), "state": desc,
                "afterglow": round(glow, 3),
                "modulator_term": round(term, 3),
                "raw": round(raw, 3),
                "clipped": abs(val - raw) > 1e-9,
                "edges_used": int(proj.get("edges_used") or 0)}

    # ── 3. 渲染（语言层语气上下文）────────────────────────

    def mood_context(self) -> str:
        m = self.current_mood()
        if abs(m["valence"]) <= 0.05:
            return ""
        return f"心情{m['state']}（valence {m['valence']}）——让语气与之一致，但不要直接报告心情"
