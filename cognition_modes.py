# cognition_modes.py — LLM 认知参与模式（Cognitive Participation Modes）
# ============================================================================
# 阶段目标：LLM 从"认知管线固定中枢"变为"FAS 按认知需求调用的高阶资源"。
#
# 六个模式（按成本递增）：
#   MODE0_GRAPH_ONLY   图谱自足，零 LLM（扩散/竞争/倾向/意图/触发）
#   MODE1_LANGUAGE     仅语言实现（把已形成的认知状态说成人话）
#   MODE2_INTERPRET    图谱无法可靠解释输入时，LLM 作认知解释器
#   MODE3_REASON       需要多步/反事实/远距离组合推理（可看更广上下文）
#   MODE4_REFLECT      低频反思/倾向形成/人格统计
#   MODE5_DEEP         最昂贵：仅用户明确要求/重大缺口/高价值目标
#
# 认知需求（Cognitive Demand）评估：输入不确定度/新颖度/冲突/语言复杂度/
#   推理深度/目标重要性/好奇心/情绪显著性 → LLM_request_score → 选模式。
#
# BudgetManager：max_calls_per_turn / per_minute / max_tokens_per_turn、
#   daily_budget、按 mode 的权重与冷却；profile: development/balanced/economy/full。
#
# 失败策略：LLM 不可用或预算不足 → 返回 None → 调用方降级（图谱继续）。
# 本模块不做任何 LLM 调用——只做评估与配额（万物皆图：评估读图谱状态）。
# ============================================================================

import time
import threading

from graph_model import ENGINE_PART_LABELS


# ── 模式枚举 ──────────────────────────────────────────────

MODE0_GRAPH_ONLY = "graph_only"
MODE1_LANGUAGE = "language"
MODE2_INTERPRET = "interpret"
MODE3_REASON = "reason"
MODE4_REFLECT = "reflect"
MODE5_DEEP = "deep"

MODES = [MODE0_GRAPH_ONLY, MODE1_LANGUAGE, MODE2_INTERPRET,
         MODE3_REASON, MODE4_REFLECT, MODE5_DEEP]

# 模式成本权重（用于 per-call 计数）
MODE_COST = {
    MODE0_GRAPH_ONLY: 0,
    MODE1_LANGUAGE: 1,
    MODE2_INTERPRET: 2,
    MODE3_REASON: 3,
    MODE4_REFLECT: 4,
    MODE5_DEEP: 8,
}

# 模式 → 该模式的 LLM 是否"必须"
MODE_MANDATORY = {
    MODE0_GRAPH_ONLY: False,
    MODE1_LANGUAGE: False,   # 无语言实现时可返回空（沉默/极简）
    MODE2_INTERPRET: False,  # 失败则按原样继续（未知保持未知）
    MODE3_REASON: False,
    MODE4_REFLECT: False,
    MODE5_DEEP: False,
}

# 各 mode 的默认 LLM 上下文范围（Graph Attention Context 构建取数）
MODE_CONTEXT_BUDGET = {
    MODE0_GRAPH_ONLY: 0,
    MODE1_LANGUAGE: 10,
    MODE2_INTERPRET: 20,
    MODE3_REASON: 40,
    MODE4_REFLECT: 40,
    MODE5_DEEP: 80,
}


# ── 需求评分（读图谱状态的纯函数，零 LLM）──────────────

def demand_score(parsed: dict, text: str, kg, engine) -> dict:
    """从输入与图谱状态估算 LLM 需求分（0~1）。"""
    s = 0.0
    components = {}

    # 语言复杂度：长度/问号/复杂标点（浅启发，语言理解归 MODE1 保证）
    t = str(text or "")
    lang = min(1.0, len(t) / 200) + (0.1 if ("?" in t or "？" in t) else 0)
    lang = min(1.0, lang)
    components["language"] = round(lang, 2)

    # 未知概念：parsed.nodes 中不在图谱的（novelty / unknown）
    with kg._lock:
        ids = set(kg.nodes)
    unknown = 0
    for n in parsed.get("nodes") or []:
        nid = n if isinstance(n, str) else (n.get("id") or "")
        if nid and nid not in ("用户", "Fascinator") and nid not in ids:
            unknown += 1
    components["unknown"] = unknown

    # 冲突：TopK 节点间是否存在互斥关系（rare，启发）
    conflict = 0.0

    # 推理深度：疑问句 + 含图外实体
    reasoning = (0.25 if ("?" in t or "？" in t) else 0.0) + 0.1 * min(unknown, 3)
    components["reasoning"] = round(min(0.8, reasoning), 2)

    # 情绪显著性：图谱情绪节点激活
    with kg._lock:
        emo_act = max(
            (n.activation for n in kg.nodes.values()
             if (n.extra_attrs or {}).get("type") == "emotion"),
            default=0.0)
    components["emotion"] = round(min(1.0, emo_act / 2.0), 2)

    s = (0.30 * lang
         + 0.25 * min(1.0, unknown / 3)
         + 0.10 * conflict
         + 0.15 * reasoning
         + 0.20 * components["emotion"])
    return {"score": round(min(1.0, s), 3), "components": components}


def mode_for_demand(d: dict) -> str:
    """需求分 → 最低成本足够消化的模式。

    ⚠ DEPRECATED（2026-09-22 收尾标注）：运行时真路由在
    `cognitive_demand.analyze_cognitive_demand → _route`（九维需求 →
    Gap → 资源调度）；本函数与其并存但**不在生产链路上**，仅历史测试
    引用。修改路由语义请改 cognitive_demand，勿在此另立一套。

    认知需求分类（规范三）：未知实体/未知关系直接触发 INTERPRET——
    图谱无法可靠解释输入时请求 LLM 解释器，不靠加权凑分。
    """
    unknown = d.get("components", {}).get("unknown", 0)
    if unknown >= 2:
        return MODE3_REASON   # 多个未知 → 需要组合/推理
    if unknown >= 1:
        return MODE2_INTERPRET  # 未知实体 → 图谱解释不足
    sc = d["score"]
    # 问答轮的最低保底是 LANGUAGE：graph_only 属于图谱内部循环
    # （扩散/竞争/触发），不对应"回答用户"这一动作
    if sc < 0.35:
        return MODE1_LANGUAGE
    if sc < 0.55:
        return MODE2_INTERPRET
    return MODE3_REASON  # 超过阈值的日常输入不给 DEEP（DEEP 仅显式触发）


# ── 预算管理 ──────────────────────────────────────────────

class BudgetManager:
    """全局 LLM 预算。按调用次数计权（mode 权重），支持冷却与降级。"""

    def __init__(self, profile: str = "balanced"):
        self.profile = profile
        self._lock = threading.RLock()
        self._calls = []          # 时间戳列表（近 60s 计数）
        self._tokens_turn = 0
        self._turn_start = 0.0
        self._daily = 0
        self._day = time.strftime("%Y-%m-%d")
        # profile → (per_turn, per_minute, max_tokens_turn, daily)
        self._limits = {
            "economy":   (2, 4, 2048, 500),
            "balanced":  (4, 8, 6144, 2000),
            "development": (10, 20, 16384, 8000),
            "full_cognition": (20, 40, 32768, 99999),
        }

    def _refresh_day(self):
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today
            self._daily = 0

    def can_call(self, mode: str, estimated_tokens: int = 800,
                 budget_factor: float = 1.0) -> bool:
        """预算内？预算外返回 False（调用方降级，图谱继续）。

        budget_factor（2026-09-20 网络调制层）：CognitiveDemand 回答"需要
        多少资源"，llm.budget_factor 调制回答"此刻愿不愿给"——任务投入/
        CEN 高时放宽（>1），纯发散态收紧（<1）。只缩放分钟级软限，
        daily 硬顶不受调制影响（护栏仍是硬约束）。
        """
        if mode == MODE0_GRAPH_ONLY:
            return True
        try:
            # §16 实验屏蔽 cognition_llm：自发认知（自由思考/离线反思/好奇
            # 追问/自发表达）的 LLM 调用一律"预算外"→ 调用方按既有降级
            # 路径走图谱（返回 False 是现成语义，不是新分支）。
            # 对用户消息的回答路径不经过这里。
            import experiment_mode as _xm
            if _xm.shield("cognition_llm"):
                return False
        except Exception:
            pass
        lim = self._limits.get(self.profile, self._limits["balanced"])
        try:
            bf = max(0.5, min(1.5, float(budget_factor or 1.0)))
        except (TypeError, ValueError):
            bf = 1.0
        per_minute = max(1, int(lim[1] * bf))
        now = time.time()
        with self._lock:
            self._refresh_day()
            if self._daily >= lim[3]:
                return False
            recent = [t for t in self._calls if now - t < 60]
            if len(recent) >= per_minute:
                return False
            w = MODE_COST.get(mode, 1)
            if len(recent) + w > per_minute:
                # 允许单个大模式挤掉配额（权重体现）
                pass
            return True

    def register(self, mode: str, tokens: int = 0):
        """调用后登记（无论成败都登记，防重试风暴）。"""
        with self._lock:
            self._calls.append(time.time())
            self._daily += 1
            self._tokens_turn += tokens

    def turn_reset(self):
        with self._lock:
            self._tokens_turn = 0
            self._turn_start = time.time()

    def stats(self) -> dict:
        with self._lock:
            now = time.time()
            return {
                "profile": self.profile,
                "calls_last_60s": len([t for t in self._calls if now - t < 60]),
                "daily": self._daily,
                "tokens_turn": self._tokens_turn,
            }


# ── 图注意力上下文（给 LLM 看更广，但不激活）────────────

def attention_context(kg, engine, mode: str, n_active: int = 12,
                      n_episodic: int = 8, modulation=None) -> dict:
    """构建分块认知上下文（ActiveCore/RecentEpisodic/Self/Emotion/Goal/
    RelevantSemantic/Candidate/Unknown）。只读取，不改激活。

    宽度调制（2026-09-20 兑现 MODE_CONTEXT_BUDGET，此前是死表）：
    实际规模 = 基准 × (mode 预算/40 档) × attention.width_scale。
    DMN 高 → 看更广（更多联想素材）；CEN 高 → 收窄到任务相关。
    modulation 缺位（单测/离线）= 旧行为。
    """
    if modulation is not None:
        try:
            budget_f = max(0.5, min(2.0,
                                    float(MODE_CONTEXT_BUDGET.get(mode, 40)) / 40.0))
            width_f = float(modulation.get("attention.width_scale", 1.0))
            f = budget_f * width_f
            n_active = max(4, int(round(n_active * f)))
            n_episodic = max(3, int(round(n_episodic * f)))
        except Exception:
            pass
    ctx = {"active_core": [], "recent_episodic": [], "self": [],
           "emotion": [], "goal": [], "relevant_semantic": [],
           "unknowns": [], "mode": mode}
    # L1-DGR-02:锁序 engine→kg;get_topk 不得在 kg._lock 内调用
    topk, _ = engine.get_topk(k=n_active * 2)
    with kg._lock:
        INFRA = {nid for nid, n in kg.nodes.items()
                 if n.label in ENGINE_PART_LABELS}
        active = [n for n in topk if n.id not in INFRA][:n_active]
        ctx["active_core"] = [{"id": n.id, "act": round(n.activation, 3),
                               "space": getattr(n, "graph_space", "")}
                              for n in active]
        episodic = sorted(
            (n for n in kg.nodes.values()
             if getattr(n, "graph_space", "") == "episodic"
             and not n.id.startswith(("回答记录", "搜索记录", "反思_", "思考_"))),
            key=lambda n: (n.extra_attrs or {}).get("event_timestamp", ""),
            reverse=True)[:n_episodic]
        ctx["recent_episodic"] = [n.id for n in episodic]
        ctx["self"] = [n.id for n in kg.nodes.values()
                       if getattr(n, "graph_space", "") == "self"][:10]
        ctx["emotion"] = [n.id for n in kg.nodes.values()
                          if (n.extra_attrs or {}).get("type") == "emotion"
                          and n.activation > 0.05]
        ctx["goal"] = [n.id for n in kg.nodes.values()
                       if (n.extra_attrs or {}).get("type") in ("goal", "reply_goal")]
        # 相关语义：active_core 的直接邻居
        rel = set()
        for n in active:
            for e in kg.edges:
                if e.src == n.id and e.dst not in INFRA:
                    rel.add(e.dst)
                if e.dst == n.id and e.src not in INFRA:
                    rel.add(e.src)
        ctx["relevant_semantic"] = list(rel)[:n_active]
    return ctx
