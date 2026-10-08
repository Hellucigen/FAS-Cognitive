# cognitive_demand.py — Cognitive Demand → Cognitive Gap → Resource Routing
# ============================================================================
# 认知需求机制重构（2026-09-20）。旧 demand_score 的语义是"这句话需要
# LLM 以多高规格参与"——太窄，且把三件事混成了一个标量：
#
#   Layer 1  Cognitive Demand  当前输入/情境对各认知系统提出多大需求
#            （memory/knowledge/reasoning/emotion/social/action/
#             curiosity/self_model/language —— 九个维度，都不是 LLM 分数）
#   Layer 2  Cognitive Gap     需求 − 现有认知能力能否满足
#            （图谱已有 → gap 小；记忆已召回 → gap 小；KG 无法多步
#             推理 → gap 大。需求高 ≠ 缺口大）
#   Layer 3  Resource Routing  按缺口调度资源：
#            kg / memory / action executor / curiosity / emotion 系统 /
#            LLM(language|interpret|reason) —— LLM 只是资源路由的一个出口
#            最终落到既有 LLM Resource Level（MODE0~5，预算机制不变）
#
# 三条硬规则（重构的存在理由）：
#   1. **emotion 不进 LLM 路由**。高情绪提高的是重要性/情绪系统需求
#      （行为竞争、心情、激素、社交回应通道都已在别处消费它）；只有当
#      情绪确实产生语言解释需求时才经 language 维度间接体现。
#      "哈哈哈哈" 的 emotion/social demand 可以很高 → MODE 仍是语言档。
#   2. **unknown ≠ 必须调 LLM**。图里没有某节点只是知识缺口的证据之一：
#      只有当它阻塞理解（在断言的关系边里 / 是被追问的对象 / 指代无着落）
#      才形成 knowledge_gap。"我今天去了星巴克"（星巴克 unknown，陈述，
#      不阻塞任何推理）→ knowledge_gap≈0.1 → 不升档。
#   3. **需求评分永远无权决定"是否说话"**。是否回应只由 dialogue_decision
#      行为竞争裁决；本模块只**读取**它的裁决来决定语言实现资源的调度方向。
#
# 证据全部来自确定性来源：图谱节点/激活值/边、NLP 解析产物、FAISS 召回、
# 情绪共振、行动状态、决策结果——规则只提供结构化证据，不替代认知。
# 新增维度只需加一个 provider（见 DEMAND_DIMENSIONS），不重写评分器。
# ============================================================================

import logging
import re

logger = logging.getLogger(__name__)

DEMAND_DIMENSIONS = (
    "memory", "knowledge", "reasoning", "emotion", "social",
    "action", "curiosity", "self_model", "language",
)

# LLM 资源档位（与 cognition_modes 的常量字符串一致，避免循环 import）
MODE_GRAPH_ONLY = "graph_only"
MODE_LANGUAGE = "language"
MODE_INTERPRET = "interpret"
MODE_REASON = "reason"

_QUESTION_RE = re.compile(r"[?？]")
_REASON_MARKERS = re.compile(
    r"为什么|为何|怎么|怎样|如果|要是|假如|会怎样|长期|规划|比较|区别|"+
    r"哪些|分别|理由|原因|推导|预[测案]|打算|方案|然后.{0,6}再|先.{0,6}然后")
_MEMORY_MARKERS = re.compile(
    r"记得|上次|之前(说|提|过)|昨天|前天|刚才|刚刚|以前说|跟你说|还想起来|那时候")
_SELF_MARKERS = re.compile(r"你(自己|觉得|怎么看|想要|喜欢吗|会|能不能)|你的")
_DEICTIC = re.compile(r"这个|那个|这|那")


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def _node_activation(kg, nid):
    try:
        n = kg.nodes.get(nid) if kg is not None else None
        return float(getattr(n, "activation", 0.0) or 0.0) if n else 0.0
    except Exception:
        return 0.0


# ── Layer 1：各维度需求（独立 provider，证据 → 0~1）────────

def _d_language(s):
    ev = []
    t = s["text"]
    v = _clamp(len(t) / 60.0, 0.0, 1.0) * 0.6
    if _QUESTION_RE.search(t):
        v += 0.15
        ev.append("疑问句")
    if len(re.findall(r"[，。！？；]", t)) >= 3:
        v += 0.2
        ev.append("多分句")
    if len(t) <= 6:
        ev.append("极短输入")
    return _clamp(v), ev


def _d_knowledge(s):
    """unknown 只作为缺口证据：区分"出现在关系边/被追问"（阻塞理解）
    与"仅出现一次的名词"（不阻塞）。"""
    unknowns = s.get("unknown_nodes") or []
    blocking = 0
    ev = []
    edge_ents = set()
    for e in (s.get("parsed_edges") or []):
        for side in ("src", "dst"):
            v = str(e.get(side) or "").strip()
            if v:
                edge_ents.add(v)
    unknown_in_edges = [u for u in unknowns if u in edge_ents]
    if unknown_in_edges:
        blocking += 1
        ev.append(f"未知实体出现在关系断言里: {unknown_in_edges}")
    is_q = bool(_QUESTION_RE.search(s["text"]))
    if is_q and unknowns:
        blocking += 1
        ev.append(f"追问对象不在图谱: {unknowns}")
    if is_q and _DEICTIC.search(s["text"]) and unknowns:
        ev.append("指代+未知实体（解释需求）")
    v = _clamp(0.28 * len(unknowns) + 0.34 * blocking)
    if unknowns and not blocking:
        ev.append("未知实体为陈述性提及，不阻塞理解")
    return v, {"evidence": ev, "unknowns": unknowns,
               "blocking": blocking, "unknown_in_edges": unknown_in_edges}


def _d_reasoning(s):
    t = s["text"]
    marks = sorted(set(_REASON_MARKERS.findall(t)))
    ev = [f"推理标记: {marks[:4]}"] if marks else []
    v = _clamp(0.28 * len(marks))
    # 条件/假设 + 因果/方式 连用 = 典型多步推理句（无未知实体也必须算需求）
    if marks and any(m in ("如果", "要是") for m in marks)             and any(m in ("为什么", "为何", "怎么", "怎样", "会怎样", "长期", "规划")
                    for m in marks):
        v = _clamp(v + 0.35)
        ev.append("条件/假设 + 因果/规划连用（多步推理特征）")
    # 两个未知实体之间求关系 = 多步组合需求（图内无既有链路可查）
    unk = s.get("unknown_nodes") or []
    edge_ents = set()
    for e in (s.get("parsed_edges") or []):
        edge_ents.add(str(e.get("src") or "").strip())
        edge_ents.add(str(e.get("dst") or "").strip())
    if len([u for u in unk if u in edge_ents]) >= 2 or len(unk) >= 2:
        v = max(v, 0.6)
        ev.append("多个未知实体间求关系（图内无既有路径）")
    return v, ev


def _d_emotion(s):
    """情绪需求：读图谱情绪激活与共振结果。注意——只喂情绪/行为侧，
    不参与 LLM 资源路由（Layer 3 里被显式排除）。"""
    kg = s.get("kg")
    v = 0.0
    ev = []
    try:
        with kg._lock:
            top = max((float(getattr(n, "activation", 0) or 0)
                       for n in kg.nodes.values()
                       if (n.extra_attrs or {}).get("type") == "emotion"),
                      default=0.0)
    except Exception:
        top = 0.0
    if top > 0:
        v = _clamp(top / 3.0)
        ev.append(f"情绪节点最大激活 {top:.2f}")
    for emo in (s.get("resonance_activated") or []):
        ev.append(f"本轮情绪共振: {emo}")
    if (s.get("parsed") or {}).get("dialogue_act") in (
            "emotion_expression", "comfort", "thanking", "apology"):
        v = max(v, 0.45)
        ev.append("表达类对话行为")
    return v, ev


def _d_social(s):
    v = 0.0
    ev = []
    act = _node_activation(s.get("kg"), "社交互动")
    if act > 0:
        v = _clamp(act / 3.0)
        ev.append(f"社交互动节点激活 {act:.2f}")
    if (s.get("parsed") or {}).get("dialogue_act") in (
            "greeting", "farewell", "invitation", "thanking"):
        v = max(v, 0.5)
        ev.append("社交对话行为")
    users = [p for p in (s.get("players_nearby") or [])]
    if users:
        ev.append(f"同伴在场: {users}")
    return v, ev


def _d_action(s):
    v = 0.0
    ev = []
    p = s.get("parsed") or {}
    if p.get("needs_action"):
        v = max(v, 0.7)
        ev.append(f"L1 needs_action intent={p.get('intent')}")
    if s.get("intentions"):
        v = max(v, 0.75)
        ev.append(f"多意图分解 n={len(s['intentions'])}")
    if s.get("current_action"):
        v = max(v, 0.5)
        ev.append(f"动作执行中: {s['current_action'].get('action_type')}")
    res = s.get("action_result")
    if res and res.get("pending"):
        ev.append("已有动作在飞（执行器资源被占用）")
    if res:
        v = max(v, 0.55)
        ev.append("有行动请求需要处理/汇报")
    return v, ev


def _d_curiosity(s):
    drive = _node_activation(s.get("kg"), "CuriosityDrive")
    v = _clamp(drive / 3.0)
    ev = [f"CuriosityDrive 激活 {drive:.2f}"] if drive > 0 else []
    expl = s.get("exploration") or {}
    if expl.get("candidates"):
        ev.append(f"探索候选 {len(expl['candidates'])} 个")
    return v, ev


def _d_self_model(s):
    t = s["text"]
    p = s.get("parsed") or {}
    da = str(p.get("dialogue_act") or "")
    refers_self = bool(_SELF_MARKERS.search(t)) or any(
        n in ("Fascinator", "Haru") for n in (p.get("nodes") or []))
    asks_info = da in ("question",)
    v = 0.7 if (refers_self and asks_info) else (0.35 if refers_self else 0.0)
    ev = []
    if refers_self:
        ev.append("话语指向 FAS 自身（自我模型参与）")
    return v, ev


def _d_memory(s):
    t = s["text"]
    marks = _MEMORY_MARKERS.findall(t)
    ev = [f"回指/记忆请求标记: {marks[:3]}"] if marks else []
    v = _clamp(0.5 * len(marks) + (0.15 if _QUESTION_RE.search(t) and marks else 0))
    return v, ev


_PROVIDERS = {
    "language": _d_language, "knowledge": _d_knowledge,
    "reasoning": _d_reasoning, "emotion": _d_emotion, "social": _d_social,
    "action": _d_action, "curiosity": _d_curiosity,
    "self_model": _d_self_model, "memory": _d_memory,
}


# ── Layer 2：缺口 = 需求 − 内部能力能否满足 ────────────────

def _gaps(demand, gap_ev, s):
    g = {}
    # knowledge：只有"阻塞性 unknown"才构成缺口；陈述性提及 → 0.1 量级
    kb = gap_ev["knowledge"]
    if kb["blocking"] >= 2:
        g["knowledge"] = 0.75
    elif kb["blocking"] == 1:
        g["knowledge"] = 0.55
    elif kb["unknowns"]:
        g["knowledge"] = 0.10          # 有未知但完全不阻塞 → 近乎无缺口
    else:
        g["knowledge"] = 0.0
    # memory：本轮是否真的召回到了东西（FAISS/graph/chat-log 三路证据）
    recall_power = 0.0
    if s.get("faiss_hits"):
        recall_power += 0.45
    if s.get("chat_recall"):
        recall_power += 0.35
    if _node_activation(s.get("kg"), "用户") > 0.3:
        recall_power += 0.2
    g["memory"] = _clamp(demand["memory"] - min(1.0, recall_power)) \
        if demand["memory"] > 0 else 0.0
    # reasoning：KG 没有确定性多步求解器——高推理需求即高缺口（部分）
    g["reasoning"] = demand["reasoning"] * 0.9
    # emotion/social/curiosity/self_model/action：由内部系统承接（行为竞争、
    # 心情/激素、curiosity engine、self-graph、执行器），语言侧只有
    # 实现需求，不构成 LLM 推理缺口。emotion 永远不进 LLM（§五 硬规则）。
    g["emotion"] = demand["emotion"] * 0.15
    g["social"] = demand["social"] * 0.20
    g["curiosity"] = demand["curiosity"] * 0.20
    g["self_model"] = demand["self_model"] * 0.30
    g["action"] = max(0.0, demand["action"] - (0.4 if s.get("executor_ready") else 0.0))
    # language：需要说话（决策层裁决非沉默）→ 语言实现缺口；沉默 → 0。
    should_speak = bool(s.get("should_speak"))
    g["language"] = 0.9 if should_speak else 0.0
    if not should_speak:
        for k in ("knowledge", "reasoning", "memory"):
            g[k] = g[k] * 0.5      # 不说话的轮次缺口只服务于内部消化
    return g


# ── Layer 3：资源路由 → LLM Resource Level（MODE0~5 语义不变）──

def _route(demand, gap):
    r = {
        "kg": _clamp(demand["knowledge"] * (1 - gap["knowledge"])
                     + 0.4 * demand["curiosity"] * (1 - gap["curiosity"])),
        "memory": _clamp(demand["memory"] * (1 - gap["memory"])),
        "action": gap["action"] if demand["action"] > 0 else 0.0,
        "external_tool": _clamp(gap["knowledge"] - 0.5) * 2.0,  # 缺口大且有事实性追问
        "emotion_system": _clamp(demand["emotion"]),   # 内部资源，不换算成 LLM
        "llm_language": _clamp(gap["language"]),
        "llm_interpret": _clamp(gap["knowledge"] * 1.1),
        "llm_reasoning": _clamp(gap["reasoning"]
                                + (0.25 if gap["knowledge"] >= 0.7
                                   and demand["reasoning"] >= 0.5 else 0.0)),
    }
    # 决策→档位：只有 reasoning/interpret/language 三类缺口能决定 LLM 档位；
    # emotion/social/curiosity/self_model 的任何高度都不影响这里（硬规则）。
    if r["llm_reasoning"] >= 0.55:
        mode = MODE_REASON
    elif r["llm_interpret"] >= 0.45:
        mode = MODE_INTERPRET
    elif r["llm_language"] >= 0.4:
        mode = MODE_LANGUAGE
    else:
        mode = MODE_GRAPH_ONLY
    return r, mode


def _legacy_score(s):
    """兼容层：旧标量公式原样计算（旧日志/旧 UI/旧接口）。
    不再是核心认知模型——仅 legacy resource-routing score。"""
    try:
        from cognition_modes import demand_score
        return demand_score(s.get("parsed") or {}, s.get("text", ""),
                            s.get("kg"), s.get("engine"))
    except Exception:
        return {"score": 0.0, "components": {}}


def _why(demand, gap, r, mode):
    lines = []
    for d in DEMAND_DIMENSIONS:
        if demand[d] >= 0.3:
            cap = {"memory": "记忆系统", "knowledge": "知识图谱",
                   "reasoning": "LLM 推理", "emotion": "情绪/行为系统（内部）",
                   "social": "社交通道（内部）", "action": "行动执行器",
                   "curiosity": "好奇心引擎（内部）",
                   "self_model": "自我模型（内部）", "language": "语言实现"}[d]
            lines.append(f"{d} 需求 {demand[d]:.2f} → 缺口 {gap[d]:.2f} → {cap}")
    lines.append({"graph_only": "本轮无 LLM 资源需求（内部系统消化）",
                  "language": "需要语言实现（MODE1 档）",
                  "interpret": "存在阻塞性知识缺口 → LLM 解释器（MODE2 档）",
                  "reason": "存在多步推理缺口 → LLM 高阶推理（MODE3 档）"}[mode])
    return lines


def analyze_cognitive_demand(*, text, parsed, kg=None, engine=None,
                             unknown_nodes=None, parsed_edges=None,
                             faiss_hits=None, resonance_activated=None,
                             chat_recall=None, exploration=None,
                             intentions=None, action_result=None,
                             current_action=None, executor_ready=True,
                             players_nearby=None, should_speak=True,
                             decision=None, legacy_demand=None):
    """三层分析总入口（零 LLM）。

    should_speak 由调用方从 dialogue_decision 结果**读取**（决策决定资源，
    本模块绝不反向决定是否回应——§八 权限边界）。
    """
    sig = {
        "text": str(text or ""), "parsed": parsed or {}, "kg": kg, "engine": engine,
        "unknown_nodes": unknown_nodes or [],
        "parsed_edges": parsed_edges or (parsed or {}).get("edges") or [],
        "faiss_hits": faiss_hits or [], "resonance_activated": resonance_activated or [],
        "chat_recall": chat_recall, "exploration": exploration or {},
        "intentions": intentions or [], "action_result": action_result,
        "current_action": current_action, "executor_ready": executor_ready,
        "players_nearby": players_nearby or [], "should_speak": should_speak,
        "decision": decision or {},
    }
    demand, evidence = {}, {}
    for dim in DEMAND_DIMENSIONS:
        provider = _PROVIDERS[dim]
        out = provider(sig)
        if dim == "knowledge":
            val, meta = out
            demand[dim] = round(val, 3)
            evidence[dim] = meta["evidence"]
            sig["_kb"] = meta
        else:
            val, ev = out
            demand[dim] = round(val, 3)
            evidence[dim] = ev
    gap_ev = {
        "knowledge": sig.get("_kb") or {"evidence": [], "unknowns": [],
                                        "blocking": 0, "unknown_in_edges": []},
    }
    gaps = _gaps(demand, gap_ev, sig)
    gaps = {k: round(v, 3) for k, v in gaps.items()}
    resource, mode = _route(demand, gaps)
    resource = {k: round(v, 3) for k, v in resource.items()}
    legacy = legacy_demand if legacy_demand is not None else _legacy_score(sig)
    result = {
        "demand": demand,
        "gap": gaps,
        "resource": resource,
        "mode": mode,                       # LLM Resource Level（MODE0~5 语义）
        "legacy": legacy,                   # 兼容层：旧 {"score","components"}
        # §14 命名兼容：legacy_score 只是旧标量（旧日志/UI/旧调用方），
        # 架构不再依赖它做路由。
        "legacy_score": legacy.get("score", 0.0) if isinstance(legacy, dict) else 0.0,
        "reasons": None,                    # 占位，下面指向 why（spec §14 别名）
        "explain": {d: {"demand": demand[d], "gap": gaps.get(d),
                        "evidence": evidence.get(d, [])}
                    for d in DEMAND_DIMENSIONS},
        "why": _why(demand, gaps, resource, mode),
    }
    result["reasons"] = result["why"]        # §14 reasons 别名
    return result
