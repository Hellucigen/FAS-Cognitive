# expression_feedback.py — 表达反馈的图谱内机制（outcome 架构升级 2026-09-19）
# ============================================================================
# 设计原则（用户指令，核心）：万物皆图。
#   "FAS 上一句话产生了什么结果"**不再是图谱外的 Python 分类器**，而是
#   图谱中后验形成的关系：
#
#     FAS 表达
#       ↓ record_expression：表达成为 episodic 事件节点（涉及行为/关于话题/
#         经历边），进入激活前沿（表达本身就是激活源），并挂上"反馈注视"
#         认知指针（跨重启存活——旧机制的 buffer 只在内存，重启即盲）
#       ↓ 用户下一轮输入走既有管线（解析→激活→扩散）
#     observe_response（本轮扩散完成后调用）：
#       用户输入激活的话题/实体，经 关于/涉及 边回流点亮上一轮的 Expression
#       事件节点 → **共激活增量**就是"回应关系"的物理事实
#       （时间越久，表达节点经既有回合间衰减越淡 → 关联强度天然衰减；
#         但用户"刚才你说的那个我想了下"会重新点亮原结构，隔 6 小时照样成立）
#       ↓ 形成 用户表达事件 -[对话]-> 表达事件，并生成 SocialFeedback 事件
#         节点（基于/关于 边连着两个事件与本轮共激活的情绪节点——
#         情绪节点带 valence 图谱属性，"哈哈→positive"式词表判定已废弃）
#       ↓ 派生 social 标签 = 纯图态读取（回应增量/前表达行为属性/情绪
#         valence 合计/解析出的 dialogue_act），**只是解释层**：供
#         reward/surprise/hormone 与日志消费，不再是认知原始事实。
#       ↓ 反馈事件节点 mark_active → 进入下一轮注意力场 → dialogue_decision
#
#   没有回应关系、也没有控制信号 → **什么都不记**：
#   没观察到结果 ≠ neutral。旧的 ">4h→neutral" 已删除；Δt 作为时间轴
#   事实记录，不决定类别。
#
# 红线（沿用 dialogue_signals 的原则）：检测 ≠ 行为决定；本模块不创建
# 第二张图、不做文本→outcome 的直接映射、不调 LLM。词表类信号只允许
# 复用既有语言理解检测（会话控制指令 _TOPIC_CONTROL_RE、NLP dialogue_act
# 解析），判"反馈"读的是节点激活与边结构。
# ============================================================================

import logging
import time

import dialogue_signals as _dsig
from graph_model import Node, Edge, now_str
from experience import EVENT_COGNITIVE, make_event
from disposition_store import BEHAVIORS
from reward import social_to_legacy

logger = logging.getLogger(__name__)

# 注视指针（与 _FOCUS_POINTER_ID 同构的认知指针节点）
PENDING_ID = "_反馈注视"
EXPR_TYPE = "expression_event"
USER_EXPR_TYPE = "user_expression_event"
FEEDBACK_TYPE = "social_feedback_event"

# 缺省参数（config["expression_feedback"] 可覆盖）
_DEFAULTS = {
    "anchor_activation": 0.4,   # 表达事件的初始注意力锚（之后交给既有衰减）
    "response_min_delta": 0.15,  # 共激活增量下限：低于此不算"回应"
    "emotion_min_activation": 0.30,  # 情绪节点算"本轮共激活"的门槛
    "valence_reject": -0.5,     # 共激活情绪 valence 合计 → rejected 方向
    "valence_amuse": 0.5,
}


def _cfg(config: dict = None) -> dict:
    c = dict(_DEFAULTS)
    if config:
        try:
            c.update(config.get("expression_feedback", {}) or {})
        except Exception:
            pass
    return c


def _ms_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000) % 10**9}"


# ── 表达侧：每轮 FAS 开口（或选择沉默）时调用 ──────────────────

def record_expression(kg, engine, buffer, *, user_text: str, answer,
                      behavior: str, context: str, context_ids=None,
                      dialogue_act: str = "", response_expectation: str = "",
                      topics=None, silence_chosen: bool = False,
                      cycle_id=None, config=None):
    """把本轮 FAS 表达写进统一图谱：事件节点 + 事实边 + 激活锚 + 注视指针，
    并保留 buffer 兼容视图（reflection/旧日志仍读它）。

    返回表达事件节点 id（失败返回 None，不阻塞对话回合）。
    """
    try:
        cfg = _cfg(config)
        topics = [str(t).strip() for t in (topics or []) if str(t).strip()]
        ts_now = time.time()
        nid = _ms_id("表达")
        bnode = BEHAVIORS.get(behavior, "")
        with kg._lock:
            in_graph = [t for t in topics if t in kg.nodes and t != nid]
            kg.add_node(Node(
                id=nid, weight=0.5, label="declarative-episodic",
                graph_space="episodic",
                extra_attrs={
                    "type": EXPR_TYPE,
                    "behavior": behavior or "",
                    "behavior_node": bnode,
                    "context": context or "",
                    "context_ids": list(context_ids or []),
                    "dialogue_act": dialogue_act or "",
                    "response_expectation": response_expectation or "",
                    "topics": in_graph[:8],
                    "answer_head": str(answer or "")[:160],
                    "user_text_head": str(user_text or "")[:80],
                    "silence": bool(silence_chosen),
                    "event_time": now_str(),
                    "ts": ts_now,
                    "cycle_id": cycle_id,
                    "feedback": None,
                }))
            hub = "Haru" if "Haru" in kg.nodes else ("Self" if "Self" in kg.nodes else None)
            if hub:
                kg.add_edge(Edge(src=hub, dst=nid, relation="经历",
                                 weight=0.8,
                                 relation_category="episodic_relation"))
            if bnode and bnode in kg.nodes and not kg.get_edge(nid, bnode, "涉及"):
                kg.add_edge(Edge(src=nid, dst=bnode, relation="涉及",
                                 weight=0.8,
                                 relation_category="cognitive_relation"))
            for t in in_graph:
                if not kg.get_edge(nid, t, "关于"):
                    kg.add_edge(Edge(src=nid, dst=t, relation="关于",
                                     weight=0.8,
                                     relation_category="cognitive_relation"))
        # 表达是激活源：给锚定激活并进前沿（此后衰减/扩散全走既有机制——
        # 关联强度随时间自然下降，不写第二套衰减函数）
        node = kg.nodes.get(nid)
        if node is not None:
            node.activation = max(float(node.activation or 0),
                                  float(cfg["anchor_activation"]))
            engine.mark_active([nid])
        # 注视指针：下一轮的 observe_response 据此找"上一句表达"
        _set_pending(kg, {"expr_id": nid, "behavior": behavior or "",
                          "context": context or "", "ts": ts_now,
                          "event_time": node.extra_attrs.get("event_time")
                          if node else now_str()})
        # 兼容视图（不落图的一份内存镜像；outcome 字段由反馈派生后回填）
        try:
            if buffer is not None:
                buffer.add_expression({
                    "user_text": user_text,
                    "answer": answer,
                    "behavior": behavior,
                    "context": context,
                    "context_ids": list(context_ids or []),
                    "dialogue_act": dialogue_act,
                    "response_expectation": response_expectation,
                    "topics": topics[:8],
                    "silence_chosen": bool(silence_chosen),
                    "event_node": nid,
                })
        except Exception as _be:
            logger.debug(f"[ExprFB] buffer 视图跳过: {_be}")
        logger.info(f"[ExprFB] 表达入图: {nid} behavior={behavior}"
                    f" topics={in_graph[:4]} context={context}")
        return nid
    except Exception as e:
        logger.warning(f"[ExprFB] 表达记录失败（不阻塞回合）: {e}")
        return None


# ── 注视指针（graph 内存活，跨重启）──────────────────────────

def _set_pending(kg, info: dict):
    with kg._lock:
        p = kg.nodes.get(PENDING_ID)
        if p is None:
            p = Node(id=PENDING_ID, weight=0.4, label="infrastructure",
                     graph_space="cognitive", extra_attrs={})
            kg.add_node(p)
        p.extra_attrs = {"type": "feedback_pending", **info}
        p.touch()


def _get_pending(kg):
    with kg._lock:
        p = kg.nodes.get(PENDING_ID)
        if p is None:
            return None
        info = dict(p.extra_attrs or {})
        expr_id = info.get("expr_id")
        if not expr_id or expr_id not in kg.nodes:
            return None
        return info


def _clear_pending(kg):
    with kg._lock:
        p = kg.nodes.get(PENDING_ID)
        if p is not None:
            p.extra_attrs = {"type": "feedback_pending"}
            p.touch()


# ── 观察侧：下一轮用户输入的回应检测 ─────────────────────────

def pre_baseline(kg, engine, config=None):
    """本轮 activate_from_inputs **之前**调用：取被注视表达的激活基线。"""
    pend = _get_pending(kg)
    if not pend:
        return None
    node = kg.nodes.get(pend.get("expr_id", ""))
    if node is None:
        _clear_pending(kg)
        return None
    return {"pending": pend, "expr_id": node.id,
            "baseline": float(node.activation or 0.0)}


def observe_response(kg, engine, timeline=None, *, pre, user_text: str,
                     parsed: dict = None, cycle_id=None, config=None,
                     user_name: str = "user"):
    """本轮激活+扩散**之后**调用：后验观察用户这句是否、以及如何"回应"了
    上一轮表达。全部读图谱状态（共激活增量、情绪节点 valence 属性、
    表达事件的行为属性、既有会话控制检测）——不做文本→outcome 分类。

    返回：
      None / {"status":"open"}       未观察到回应关系（不记、不解释）
      {"status":"responded", "social":<标签>, "legacy":..., "detail":...,
       "feedback_node":..., "response_delta":..., "dt_seconds":...,
       "emotion_valence":..., "user_expr_id":...}
    """
    if not pre:
        return None
    cfg = _cfg(config)
    parsed = parsed or {}
    expr_id = pre["expr_id"]
    pend = pre["pending"]
    node = kg.nodes.get(expr_id)
    if node is None:
        _clear_pending(kg)
        return None
    delta = float(node.activation or 0.0) - float(pre["baseline"])
    # 会话控制指令（"别问了/打住/换个话题"）是对当前会话状态——即上一句
    # 表达——的显式指向。复用既有检测（dialogue_signals，语言理解层），
    # 不再用"抵触词表→negative"。
    control = _dsig.is_topic_control(user_text)
    responded = control or delta >= float(cfg["response_min_delta"])
    if not responded:
        # §七：没有观察到结果 ≠ neutral。不建关系、不派生、不奖励。
        return None

    text = str(user_text or "").strip()
    ts_now = time.time()
    dt = max(0.0, ts_now - float(pend.get("ts") or ts_now))
    # ── 用户表达事件（episodic，统一图谱）──
    uid = _ms_id("用户表达")
    with kg._lock:
        topics = [n if isinstance(n, str) else (n.get("id", "") if isinstance(n, dict) else "")
                  for n in (parsed.get("nodes") or [])][:8]
        in_graph = [t for t in topics if t and t in kg.nodes]
        kg.add_node(Node(
            id=uid, weight=0.5, label="declarative-episodic",
            graph_space="episodic",
            extra_attrs={"type": USER_EXPR_TYPE,
                         "user_text_head": text[:120],
                         "topics": in_graph[:8],
                         "dialogue_act": parsed.get("dialogue_act", ""),
                         "event_time": now_str(), "ts": ts_now,
                         "cycle_id": cycle_id}))
        if "用户" in kg.nodes and not kg.get_edge("用户", uid, "讲述"):
            kg.add_edge(Edge(src="用户", dst=uid, relation="讲述", weight=0.8,
                             relation_category="episodic_relation"))
        for t in in_graph:
            if not kg.get_edge(uid, t, "关于"):
                kg.add_edge(Edge(src=uid, dst=t, relation="关于", weight=0.8,
                                 relation_category="cognitive_relation"))
        # 回应关系：用户表达 -[对话]-> 上一轮 FAS 表达
        if not kg.get_edge(uid, expr_id, "对话"):
            kg.add_edge(Edge(src=uid, dst=expr_id, relation="对话",
                             weight=0.9,
                             relation_category="social_relation"))
        # ── 情绪：读本轮共激活的情绪节点（激活场事实 + valence 图谱属性）──
        emo_sum = 0.0
        emos = []
        try:
            topk, _ = engine.get_topk(k=20)
            for n in topk:
                ea = n.extra_attrs or {}
                if (ea.get("type") == "emotion"
                        and float(n.activation or 0)
                        >= float(cfg["emotion_min_activation"])):
                    emos.append(n.id)
                    emo_sum += float(ea.get("valence") or 0.0)
        except Exception:
            pass
        # ── SocialFeedback 事件节点 ──
        fid = _ms_id("反馈")
        kg.add_node(Node(
            id=fid, weight=0.5, label="declarative-episodic",
            graph_space="cognitive",
            extra_attrs={
                "type": FEEDBACK_TYPE,
                "based_on": [uid, expr_id],
                "prev_behavior": pend.get("behavior", ""),
                "prev_context": pend.get("context", ""),
                "response_delta": round(delta, 3),
                "via_control": bool(control),
                "emotion_nodes": emos,
                "emotion_valence": round(emo_sum, 3),
                "dialogue_act": parsed.get("dialogue_act", ""),
                "dt_seconds": round(dt, 1),
                "event_time": now_str(), "ts": ts_now, "cycle_id": cycle_id}))
        kg.add_edge(Edge(src=fid, dst=uid, relation="基于", weight=0.8,
                         relation_category="cognitive_relation"))
        kg.add_edge(Edge(src=fid, dst=expr_id, relation="关于", weight=0.8,
                         relation_category="cognitive_relation"))
        for e in emos:
            if not kg.get_edge(fid, e, "关于"):
                kg.add_edge(Edge(src=fid, dst=e, relation="关于", weight=0.6,
                                 relation_category="cognitive_relation"))
        # 后验关系写回表达事件本身（outcome=表达上的结构，不是字符串）
        fb = node.extra_attrs.get("feedback")
        if not isinstance(fb, dict):
            fb = {}
            node.extra_attrs["feedback"] = fb
        fb.update({"event": fid, "from": uid, "social": "",  # 派生后回填
                   "response_delta": round(delta, 3), "dt_seconds": round(dt, 1),
                   "at": now_str()})
        node.touch()
    if timeline is not None:
        try:
            timeline.append(make_event(
                EVENT_COGNITIVE, actor=user_name, subject="social_feedback",
                content={"expression": expr_id, "user_expression": uid,
                         "feedback": fid, "response_delta": round(delta, 3),
                         "via_control": bool(control),
                         "emotion_valence": round(emo_sum, 3),
                         "dt_seconds": round(dt, 1)},
                source="expression_feedback", meta={"cycle_id": cycle_id}))
        except Exception:
            pass
    # 派生解释层标签（§十七：图谱 → Feedback Event → 派生，方向不反）
    social, detail = _derive(pend, delta, emo_sum, control,
                             parsed.get("dialogue_act", ""), cfg)
    with kg._lock:
        _fb_field = node.extra_attrs.get("feedback")
        if isinstance(_fb_field, dict):
            _fb_field["social"] = social
    # 反馈事件进入注意力场 → 影响下一次行为竞争（§14）
    fnode = kg.nodes.get(fid)
    if fnode is not None:
        fnode.activation = max(float(fnode.activation or 0),
                               float(cfg["anchor_activation"]))
        engine.mark_active([fid])
    _clear_pending(kg)
    legacy = social_to_legacy(social)
    logger.info(
        f"[ExprFB] 回应观察: {expr_id}←{uid} delta={delta:.2f}"
        f"{'(control)' if control else ''} Δt={dt:.0f}s emo_v={emo_sum:.2f}"
        f" → social={social} ({detail}) legacy={legacy}")
    return {"status": "responded", "social": social, "detail": detail,
            "legacy": legacy, "feedback_node": fid, "user_expr_id": uid,
            "expr_id": expr_id, "response_delta": round(delta, 3),
            "via_control": bool(control), "emotion_valence": round(emo_sum, 3),
            "dt_seconds": round(dt, 1),
            "behavior": pend.get("behavior", ""),
            "context": pend.get("context", "")}


def _derive(pend: dict, delta: float, emo_valence: float, control: bool,
            dialogue_act: str, cfg: dict):
    """纯图态读取的派生（解释层，非认知原始事实）。

    输入只有：回应增量、前表达事件的行为属性、本轮共激活情绪的 valence
    合计、既有会话控制检测、NLP 解析的 dialogue_act。
    没有积极/抵触词表，没有 Jaccard，没有时间→类别。
    """
    if control:
        return "rejected", "会话控制指令指向上一表达（打断/叫停）"
    if emo_valence <= float(cfg["valence_reject"]):
        return "rejected", "回应中共激活负向情绪节点"
    if pend.get("behavior") == "ask":
        # 前表达事件节点自己记着"我当时在追问"；回应边成立 = 问答闭环
        if dialogue_act == "answer":
            return "engaged", "回应了表达事件（其图谱行为=追问）"
        return "engaged", "追问后出现后继回应关系"
    if emo_valence >= float(cfg["valence_amuse"]):
        return "amused", "回应中共激活正向情绪节点"
    if dialogue_act in ("thanking", "agreement"):
        return ("accepted" if dialogue_act == "thanking" else "recognized",
                "回应且 NLP 语义=致谢/认同")
    return "continued_discussion", "回应关系成立（共激活回流）"


# ── 表达侧的 behavior 归类（沿用既有 classify_behavior，行为节点名）────

def pending_view(kg):
    """调试/前端可见：当前注视的未获反馈表达。"""
    return _get_pending(kg)
