# cognitive_context.py — Cognitive Context：认知系统 → 语言/行动实现层的工作接口
# ============================================================================
# 定位（认知上下文重构 2026-09-20）：
#
#   感知 / NLP → 图谱认知与状态更新 → Cognitive State → Decision Layer
#       → Cognitive Context（本模块构建）→ Context Compiler（本模块筛选）
#       → Dialogue（LLM 语言实现）/ Action（执行器）
#
#   图谱、记忆、情绪、动机、行为倾向、好奇心、世界状态负责形成**状态**；
#   dialogue_decide / ActionManager 负责形成**决定**；本模块把已经形成的
#   状态与决定组织成可追溯的七分区结构，交给语言层。LLM 只决定"怎么说"，
#   不重新决定"为什么说是、是否说、说什么意图、做了什么"。
#
# 七分区（内部结构，带 provenance）：
#   perception   本轮实际感知：用户输入 + NLP 解析 + 环境在场性
#   state        内部状态：attention（图激活）/ mood / 世界状态 / 认知事件
#   memory       本轮被认知系统实际召回的内容（FAISS 命中 + 焦点注入），
#                不是整个记忆库——只取真正参与了本轮激活/回答的部分
#   motivation   当前真正参与本轮决策的驱动：行为倾向 / CuriosityDrive /
#                激素调制 / 探索候选与竞争输入
#   decision     她"准备做什么"的裁决（dialogue_decide 的输出 + 行动意图）
#   evidence     本轮实际发生了什么的硬事实：每个动作结果都归一化成
#                executing / queued / done / failed / refused / cancelled——
#                **语言层严禁把未完成的说成已完成**（渲染器由本模块提供）
#   constraints  语言实现必须遵守的硬约束（来自 dialogue decision / 场合，
#                不由 LLM 自行推导）
#
# Context Compiler（compile_for_language）规则：
#   1. 当前相关性优先；2. 已形成的 decision 优先；3. 实际 evidence 优先于
#   计划；4. attention 优先于无关历史；5. 不把整图谱塞给 LLM（TopK 截断）；
#   6. 不把整个 memory 塞给 LLM。
#   L1（短句快路径）→ 精简编译：只给决定、证据状态、语气底色。
#   L2（全管线）→ 完整编译：输出与旧 _cog_ctx 扁平键**完全兼容**的结构，
#                 nlp_processor 消费侧零行为变化（除证据渲染升级）。
#
# provenance 叶子：{"value":…, "source":…, "strength":…} —— 内部保留，
# 编译时默认只取 value；debug_view 保留全量供追溯"奇怪的话从哪来"：
#   语言 → Dialogue Context → Decision → Cognitive State → Activation/Graph
# ============================================================================

import logging

logger = logging.getLogger(__name__)


# ── provenance 小工具 ─────────────────────────────────────

def pv(value, source: str, strength=None):
    """带来源的叶子值。strength 是 [0,1]/[0,5] 的置信/强度（可选）。"""
    d = {"value": value, "source": source}
    if strength is not None:
        try:
            d["strength"] = round(float(strength), 3)
        except (TypeError, ValueError):
            pass
    return d


def _val(x):
    """取 provenance 叶子的值（非 dict 原样返回）。"""
    return x.get("value") if isinstance(x, dict) and "source" in x else x


# ── 证据状态归一化（§三 evidence：计划≠事实）─────────────

# 动作结果状态：这是给语言层的硬事实，不是措辞
EV_EXECUTING = "executing"     # 指令已发出，还在进行中（不能说已完成）
EV_QUEUED = "queued"           # 已排入队列，还没开始
EV_DONE = "done"               # 已完成（或旧接口的"已下发即算送达"语义）
EV_FAILED = "failed"           # 执行失败（带真实原因）
EV_REFUSED = "refused"         # 认知层主动拒绝执行（未点名目标/否定句）
EV_CANCELLED = "cancelled"     # 被中断/取消
EV_NONE = "none"


def classify_action_evidence(res: dict, source: str = "action") -> dict:
    """把任意动作结果 dict（反射/技能/会话/工具）归一化成带状态标签的证据。

    不改变既有真实结果语义——只是把"已发出/排队中/已完成/失败/拒绝"
    这几种被混在一起的情况分开标注，供语言层如实措辞。
    """
    if not res:
        return {"status": EV_NONE, "source": source}
    status = EV_NONE
    if res.get("cancelled"):
        status = EV_CANCELLED
    elif res.get("queued"):
        status = EV_QUEUED
    elif res.get("success"):
        status = EV_EXECUTING if res.get("pending") else EV_DONE
    elif res.get("action") == "refused" or "refus" in str(res.get("reason") or ""):
        status = EV_REFUSED
    else:
        status = EV_FAILED
    return {
        "status": status,
        "source": source,
        "action": str(res.get("action") or res.get("type") or ""),
        "describe": str(res.get("describe") or ""),
        "reason": str(res.get("reason") or ""),
        "raw": res,
    }


# 各状态的如实措辞（语言层拿到的就是这个句子；执行器/回合层都复用）
_STATUS_LINES = {
    EV_EXECUTING: "【动作执行结果】你刚发出指令：{desc}。**还在进行中，"
                  "尚未完成**——只能说开始做了/正在做，绝不能说做完了。",
    EV_QUEUED: "【动作执行结果】你已把「{desc}」排入队列，等当前动作结束后"
               "才会开始。**还没开始做**，如实表达（例如：好，等我这边忙完就去）。",
    EV_DONE: "【动作执行结果】你已完成：{desc}。自然地告知即可，一两句话。",
    EV_FAILED: "【动作执行结果】你尝试「{desc}」但失败了：{reason}。"
               "如实告诉用户失败与原因，不找借口、不假装成功。",
    EV_REFUSED: "【动作执行结果】你拒绝执行「{desc}」：{reason}。"
                "如实说明为什么没做（例如没点名目标时不擅自就近挑东西）。",
    EV_CANCELLED: "【动作执行结果】你的动作「{desc}」中途被打断/取消了。如实说。",
}


def render_action_evidence(evidence: dict, *, default_desc: str = "动作") -> str:
    """把归一化证据渲染成给 LLM 的硬事实句子（带状态约束措辞）。"""
    if not evidence or evidence.get("status") == EV_NONE:
        return ""
    desc = (evidence.get("describe") or evidence.get("action")
            or default_desc)
    tmpl = _STATUS_LINES.get(evidence.get("status"), "")
    if not tmpl:
        return ""
    return tmpl.format(desc=desc[:80], reason=evidence.get("reason") or "未知原因")


# ── 构建器 ────────────────────────────────────────────────

def build_cognitive_context(*,
                            text: str,
                            channel: str = "web",
                            parsed: dict,
                            perception_connected: bool = False,
                            environment: dict = None,
                            attention_context: dict = None,
                            mode: str = None,
                            demand: dict = None,
                            world_state: dict = None,
                            mood: str = None,
                            cognitive_events: list = None,
                            recent_dialogue: list = None,
                            faiss_hits: list = None,
                            recalled_nodes: list = None,
                            tendencies: list = None,
                            drives: dict = None,
                            networks: dict = None,
                            action_tendencies: dict = None,
                            hormone: dict = None,
                            exploration: dict = None,
                            decision: dict = None,
                            action_intent: dict = None,
                            action_queue_len: int = 0,
                            evidence: dict = None,
                            speech_act_landscape: dict = None,
                            demand_analysis: dict = None,
                            extra_constraints: list = None):
    """把回合内各来源组装成七分区 Cognitive Context。

    evidence 入参是**已执行**的结果字典（键任意，如 mc_action/mc_session/
    web_search/file_action/eye_result），本函数统一归一化状态；
    decision 入参是 dialogue_decide 的原始输出，本函数提炼成语言层契约。
    """
    evidence_in = dict(evidence or {})
    ev = {}
    for key, res in evidence_in.items():
        if res is None or (isinstance(res, (list, tuple)) and not res):
            continue
        if key in ("web_results",) and isinstance(res, list):
            ev[key] = {"status": EV_DONE, "source": "tool:web_search",
                       "action": "web_search", "count": len(res), "raw": res}
        elif key in ("file_result", "eye_result") and isinstance(res, dict) \
                and not res.get("success"):
            # 工具类结果保留其自身的 ok/success 语义
            ev[key] = classify_action_evidence(res, source=f"tool:{key}")
        elif isinstance(res, dict):
            ev[key] = classify_action_evidence(res, source=f"turn:{key}")
        else:
            ev[key] = {"status": EV_DONE, "source": f"turn:{key}", "raw": res}

    dd = decision or {}
    constraints = list(dd.get("constraints") or [])
    if channel and channel != "web":
        constraints = constraints + [
            "场合约束：这是一句话就能说完的即时聊天场合，"
            "回复要短、单行、不要标题/列表/Markdown"]
    if extra_constraints:
        constraints = constraints + list(extra_constraints)

    length_constraint = None
    if "ack_only" in constraints:
        length_constraint = "一句极短确认（两个字级别）"
    elif "minimal_len" in constraints:
        length_constraint = "一两句以内"
    elif channel and channel != "web":
        length_constraint = "短、单行"

    # decision 分区：语言层契约（should_respond 的预算门在调用处叠加；
    # 这里表达的是"决策层的裁决"本身）
    dec_val = dd.get("decision")
    should_respond = dec_val in ("respond", "minimal", "explore_ask",
                                 "explore_search")
    decision_sec = {
        "should_respond": should_respond,
        "response_mode": dec_val,
        "response_intent": pv(dd.get("winner_behavior") or dec_val or "respond",
                              "dialogue_decision"),
        # 次级行动（2026-09-20 行动重构）：组合表达的原料，语言层可
        # 自然带出（如分享时顺带玩笑），但不另起执行
        "secondary_actions": pv(dd.get("secondary_actions") or [],
                                "dialogue_decision"),
        "speech_act": pv(parsed.get("illocutionary_act", "assertive"), "nlp_parse"),
        "dialogue_act": pv(parsed.get("dialogue_act", "information_statement"),
                           "nlp_parse"),
        "initiative": "reactive",          # 主动表达走 CC 的 CI 通道（同源决策先行）
        "ask_question": (dec_val == "explore_ask"
                         or dd.get("winner_behavior") == "ask"),
        "response_intent_strength": pv(dd.get("desire"), "dialogue_decision",
                                       dd.get("desire")),
        "action_intent": action_intent,
        "action_queue_len": action_queue_len,
        "length_constraint": length_constraint,
        "factors": dd.get("factors") or {},     # 竞争过程可追溯
    }

    return {
        "perception": {
            "user_text": pv(text, "user_input"),
            "channel": channel,
            "parsed": pv({
                "nodes": parsed.get("nodes", []),
                "edges": parsed.get("edges", []),
                "memory_type": parsed.get("memory_type"),
                "response_expectation": parsed.get("response_expectation"),
                "suggested_reply_goals": parsed.get("suggested_reply_goals"),
                "intent": parsed.get("intent"),
                "urgency": parsed.get("urgency"),
                "needs_reply": parsed.get("needs_reply"),
                "needs_action": parsed.get("needs_action"),
            }, "nlp_parse"),
            "environment": pv({"connected": bool(perception_connected),
                               **(environment or {})}, "embodiment_perception"),
        },
        "state": {
            "mode": mode,
            "demand": demand,
            "attention": pv(attention_context or {}, "graph_diffusion"),
            "mood": pv(mood or "", "persona"),
            "world_state": pv(world_state or {}, "graph_dynamic_nodes"),
            "cognitive_events": pv(cognitive_events or [], "regulation_monitors"),
            # 言外行为的图谱投影（speech_act_graph 扩散结果的状态摘要，
            # 不是判断——OGCTX 只投影，不另起认知系统）
            "speech_act_landscape": pv(speech_act_landscape or {},
                                       "speech_act_graph_diffusion"),
            # Cognitive Demand → Gap → Resource 三层结构（cognitive_demand
            # 分析器的产物；state.demand 是 legacy 标量兼容字段，新消费方
            # 读这三个带 provenance 的结构）。
            **({"cognitive_demand": pv((demand_analysis or {}).get("demand") or {},
                                       "cognitive_demand_analyzer"),
                "cognitive_gap": pv((demand_analysis or {}).get("gap") or {},
                                    "cognitive_demand_analyzer"),
                "cognitive_resource": pv((demand_analysis or {}).get("resource") or {},
                                         "cognitive_demand_routing"),
                "demand_evidence": (demand_analysis or {}).get("explain") or {},
                "why_resources": (demand_analysis or {}).get("why") or []}
               if demand_analysis else {}),
        },
        "memory": {
            "recalled": pv(recalled_nodes or [], "activation_topk"),
            "faiss_hits": pv(faiss_hits or [], "semantic_recall"),
            "recent_dialogue": pv(recent_dialogue or [], "chat_log"),
        },
        "motivation": {
            "tendencies": pv(tendencies or [], "disposition_store"),
            "drives": {k: pv(v, "graph_activation", v) for k, v in
                       (drives or {}).items()},
            # 认知网络态与派生行动倾向（2026-09-20 Drive 重构；观测字段，
            # 语言层可见"她此刻如何组织注意力"，但不据此决定行为）
            "networks": pv(networks or {}, "cognitive_field"),
            "action_tendencies": pv(action_tendencies or {}, "cognitive_field"),
            "hormone": hormone,
            "exploration": pv(exploration or {}, "curiosity_candidates"),
        },
        "decision": decision_sec,
        "evidence": ev,
        "constraints": pv(constraints, "dialogue_decision+channel"),
    }


# ── Context Compiler ──────────────────────────────────────

# ── 发言提纲（V2"发言提纲机制"补账）─────────────────────
# 决定 → **提纲** → 措辞。提纲把"这轮话必须交代什么/禁止什么"显式化，
# LLM 只在提纲内自由措辞——它不再需要从上下文堆里自己推断该说什么。
def build_outline(decision: dict, evidence: dict, constraints: list,
                  length_constraint: str = None) -> dict:
    dec = decision or {}
    must, must_not = [], []
    for key, ev in (evidence or {}).items():
        if not isinstance(ev, dict):
            continue
        st, desc = ev.get("status"), (ev.get("describe") or ev.get("action") or key)
        if st == EV_EXECUTING:
            must.append(f"交代「{desc}」已开始、仍在进行")
            must_not.append(f"不许把「{desc}」说成已经完成")
        elif st == EV_QUEUED:
            must.append(f"交代「{desc}」已排进队列、还没开始")
            must_not.append(f"不许声称「{desc}」已在执行")
        elif st == EV_FAILED:
            must.append(f"如实说明「{desc}」失败了，原因：{ev.get('reason') or '未知'}")
            must_not.append("不许淡化或隐瞒失败")
        elif st == EV_REFUSED:
            must.append(f"如实说明拒绝了「{desc}」及原因（{ev.get('reason') or '未点名目标'}）")
        elif st == EV_DONE:
            must.append(f"可自然提及已完成：{desc}")
        elif st == EV_CANCELLED:
            must.append(f"交代「{desc}」中途被打断，不粉饰")
    if dec.get("ask_question") is False and "no_question" in (constraints or []):
        must_not.append("本轮不向用户提问")
    if dec.get("ask_question") and dec.get("response_mode") == "explore_ask":
        must.append("把认知层选定的探索问题自然问出来")
    if not must:
        must.append("无强制交代事项——回应认知焦点即可")
    must_not.append("不虚构图谱/感知之外的既成事实")
    return {
        "move": dec.get("response_mode"),
        "goal": (dec.get("dialogue_act") or {}).get("value") if isinstance(
            dec.get("dialogue_act"), dict) else dec.get("response_intent"),
        "intent": (_val(dec.get("response_intent"))
                   if isinstance(dec.get("response_intent"), dict)
                   else dec.get("response_intent")),
        "length": length_constraint or ("短、单行" if constraints
                                        and any("场合约束" in str(c) for c in constraints)
                                        else None),
        "must": must,
        "must_not": must_not,
        "tone": "以当前心情为语气依据（不报告机制）",
    }


def compile_for_language(ctx: dict, *, path: str = "L2") -> dict:
    """从完整 Cognitive Context 编译出语言实现层的输入（扁平、向后兼容）。

    L2：输出键与重构前的 _cog_ctx 完全一致（nlp_processor 直接消费），
        另加 "decision"/"action_evidence"（升级的证据渲染契约）。
    L1：精简——只给短回应真正需要的：决定/约束/证据状态/心情/最近对话。
    相关性筛选原则：decision 优先、evidence 高于计划、当前 attention
    优先于无关历史；图谱记忆条目由调用方（topk_nodes）控制，本函数
    不再扩大上下文。
    """
    if not ctx:
        return {}
    perception = ctx.get("perception") or {}
    state = ctx.get("state") or {}
    memory = ctx.get("memory") or {}
    motivation = ctx.get("motivation") or {}
    decision = ctx.get("decision") or {}
    evidence = ctx.get("evidence") or {}
    constraints = _val(ctx.get("constraints")) or []

    parsed = _val(perception.get("parsed")) or {}
    env = _val(perception.get("environment")) or {}

    if path == "L1":
        return {
            "path": "L1",
            "decision": {
                "should_respond": decision.get("should_respond", True),
                "response_mode": decision.get("response_mode"),
                "action_intent": decision.get("action_intent"),
                "length_constraint": decision.get("length_constraint"),
                "ask_question": decision.get("ask_question", False),
            },
            "action_evidence": evidence.get("mc_action") or {},
            "session_evidence": evidence.get("mc_session") or {},
            "constraints": constraints,
            "mood": _val(state.get("mood")),
            "recent_dialogue": (_val(memory.get("recent_dialogue")) or [])[-2:],
            "outline": build_outline(
                decision,
                {"mc_action": evidence.get("mc_action") or {},
                 "mc_session": evidence.get("mc_session") or {}},
                constraints,
                length_constraint=decision.get("length_constraint")),
            "speech_act": {
                "concept": (_val(state.get("speech_act_landscape")) or {}).get("concept"),
                "activation": (_val(state.get("speech_act_landscape")) or {}).get("activation"),
            },
        }

    # ── L2：与旧 _cog_ctx 键完全兼容 + decision/evidence 升级 ──
    compiled = {
        "mode": state.get("mode"),
        "demand": state.get("demand"),
        "attention_context": _val(state.get("attention")) or {},
        "illocutionary_act": decision.get("speech_act",
                                          {}).get("value", "assertive")
        if isinstance(decision.get("speech_act"), dict) else "assertive",
        "dialogue_act": decision.get("dialogue_act", {}).get(
            "value", "information_statement")
        if isinstance(decision.get("dialogue_act"), dict) else "information_statement",
        "response_expectation": parsed.get("response_expectation", "medium"),
        "suggested_reply_goals": parsed.get("suggested_reply_goals") or ["acknowledge"],
        "behavior_tendencies": [t for t in (_val(motivation.get("tendencies")) or [])
                                if t.get("behavior") != "silence"],
        "web_results": (evidence.get("web_results") or {}).get("raw", [])
        if isinstance(evidence.get("web_results"), dict) else [],
        "file_action": (evidence.get("file_result") or evidence.get("file_action")
                        or {}).get("raw") or None,
        "eye_result": (evidence.get("eye_result") or {}).get("raw") or None,
        "response_constraints": constraints,
        "mc_action": (evidence.get("mc_action") or {}).get("raw") or None,
        "mc_session": (evidence.get("mc_session") or {}).get("raw") or None,
        "recent_dialogue": _val(memory.get("recent_dialogue")) or [],
        "world_state": _val(state.get("world_state")) or {},
        "mood": _val(state.get("mood")),
        "cognitive_events": _val(state.get("cognitive_events")) or [],
        "speech_act_landscape": _val(state.get("speech_act_landscape")) or {},
        # 三层结构（新调用方消费；mode 仍为 LLM Resource Level 单一档位）
        "cognitive_demand": _val(state.get("cognitive_demand")) or {},
        "cognitive_gap": _val(state.get("cognitive_gap")) or {},
        "cognitive_resource": _val(state.get("cognitive_resource")) or {},
        # ── 升级契约（消费侧优先读这些）──
        "decision": decision,
        "outline": build_outline(decision, evidence, constraints,
                                 length_constraint=(decision or {}).get(
                                     "length_constraint")),
        "action_evidence": evidence.get("mc_action") or {},
        "session_evidence": evidence.get("mc_session") or {},
        "search_evidence": evidence.get("web_results") or {},
        "file_evidence": evidence.get("file_result") or evidence.get("file_action") or {},
        "eye_evidence": evidence.get("eye_result") or {},
    }
    return compiled


def debug_view(ctx: dict) -> dict:
    """紧凑调试视图：本轮回答的"为什么"全程可追溯。

    语言 → dialogue → decision → state → activation 的追踪链路在这里
    留下快照（放进 /api/nlp 响应的 cog_ctx 字段，前端可展示）。
    """
    if not ctx:
        return {}
    decision = ctx.get("decision") or {}
    evidence = ctx.get("evidence") or {}
    state = ctx.get("state") or {}
    attention = _val(state.get("attention")) or {}
    recalled = _val((ctx.get("memory") or {}).get("recalled")) or []
    return {
        "decision": {
            "response_mode": decision.get("response_mode"),
            "should_respond": decision.get("should_respond"),
            "winner": _val(decision.get("response_intent")),
            "ask_question": decision.get("ask_question"),
            "action_intent": decision.get("action_intent"),
            "action_queue_len": decision.get("action_queue_len"),
            "length_constraint": decision.get("length_constraint"),
        },
        "constraints": _val(ctx.get("constraints")) or [],
        "evidence": {k: {"status": v.get("status"),
                         "describe": v.get("describe"),
                         "reason": v.get("reason")}
                     for k, v in evidence.items()},
        "motivation": {
            "drives": {k: _val(v) for k, v in
                       ((ctx.get("motivation") or {}).get("drives") or {}).items()},
            "tendencies": [t.get("behavior") for t in
                           (_val((ctx.get("motivation") or {}).get("tendencies")) or [])],
            "hormone": (ctx.get("motivation") or {}).get("hormone"),
        },
        "attention_top": [n.get("id") if isinstance(n, dict) else n
                          for n in (attention.get("active_core") or [])[:8]],
        "recalled": [n if isinstance(n, str) else n.get("id")
                     for n in recalled[:10]],
        "mood": _val(state.get("mood")),
        "speech_act": _val(state.get("speech_act_landscape")) or {},
        # 认知需求链路：每个数字为什么出现（§17 evidence 可追踪）
        "cognitive_demand": {
            "demand": _val(state.get("cognitive_demand")) or {},
            "gap": _val(state.get("cognitive_gap")) or {},
            "resource": _val(state.get("cognitive_resource")) or {},
            "evidence": {k: v.get("evidence") for k, v in
                         (state.get("demand_evidence") or {}).items()
                         if v.get("evidence")},
            "why": state.get("why_resources") or [],
            "mode": state.get("mode"),
            "legacy_score": (state.get("demand") or {}).get("score")
            if isinstance(state.get("demand"), dict) else state.get("demand"),
        },
    }
