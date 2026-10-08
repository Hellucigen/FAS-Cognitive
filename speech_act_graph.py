# speech_act_graph.py — 言语行为图谱化（架构级：言语行为 → 认知激活入口）
# ============================================================================
# 万物皆图：用户这句话的**言外行为**必须作为图谱中的认知对象参与本轮激活，
# 而不是只活在解析字段里。本模块提供三样东西：
#
#  1) 概念层（长期，semantic 空间，bootstrap 幂等播种）
#     言语行为 ← 断言 / 指令 / 承诺 / 表达 / 宣告（Searle 五类）
#     指令 -[细化]-> 情境:用户请求 …（**层级合并既有的 情境:X 层**——
#     不建第二套 dialogue_act 表示；细化边为 semantic 双向，情境被种子
#     激活时概念同样被点亮，两套信号在图上合流）
#     概念 -[倾向]-> 回应方式（行动回应 / 信息回应 / 社交回应 / 承诺确认）
#     回应方式 -[倾向]-> 行为:X（disposition 的既有行为竞争词表）
#     "倾向"是**扩散通道**不是映射表：这些边只贡献激活量，最终回应
#     由 dialogue_decision 的既有竞争裁决（先验+经验+实时激活三通道）。
#
#  2) 本轮话语事件节点（短期，episodic 空间，有界清理）
#     用户 -[说出]-> 话语_20260920_020100
#     话语 -[言外行为]-> 指令
#     话语 -[涉及]-> 内容实体（仅已存在节点）
#     episodic 空间的传播规则（快衰减/低上限）天然实现"本轮激活、
#     不留长期痕迹"；概念节点被点亮靠扩散+回合间衰减回落，
#     **说了一次指令不会永久强化"用户经常命令我"**——长期学习仍只走
#     disposition/outcome/因果 的既有通道。
#
#  3) 投影（OGCTX 消费）
#     projection(): 本轮言外行为概念的激活值 + 由其扩散共激活的近邻清单。
#     OGCTX 拿到的不是"speech_act→该怎么回"的判断，而是**图谱认知状态的
#     投影**——它参与决定，但不单独决定。
#
# 与执行链的关系：Minecraft 用户指令派发（ActionManager）不因本模块改变；
# 概念层的 行动回应/信息回应 节点让"该不该按语言方式配合、以什么姿态回应"
# 回到图谱竞争里，而不是新增 if speech_act==... 分支。
# ============================================================================

import logging
import threading
import time

from graph_model import Node, Edge, now_str

logger = logging.getLogger(__name__)

# ── 词表 ──────────────────────────────────────────────────

CONCEPT_ROOT = "言语行为"
CONCEPTS = ["断言", "指令", "承诺", "表达", "宣告"]
RESPONSE_MODES = ["行动回应", "信息回应", "社交回应", "承诺确认"]

# illocutionary_act（英文/Searle）→ 概念节点名
ILLOC_TO_CONCEPT = {
    "assertive": "断言", "directive": "指令", "commissive": "承诺",
    "expressive": "表达", "declaration": "宣告",
    # 旧中文言语行为词表兼容
    "断言类": "断言", "指令类": "指令", "承诺类": "承诺",
    "表达类": "表达", "宣告类": "宣告",
}
# dialogue_act 兜底（解析缺 illocutionary 时按细粒度行为归类）
DA_TO_CONCEPT = {
    "question": "指令", "request": "指令", "suggestion": "指令",
    "invitation": "指令",
    "sharing": "断言", "information_statement": "断言", "opinion": "断言",
    "answer": "断言", "agreement": "断言", "disagreement": "断言",
    "backchannel": "断言",
    "greeting": "表达", "thanking": "表达", "apology": "表达",
    "farewell": "表达", "emotion_expression": "表达", "comfort": "表达",
    "congratulation": "表达", "curiosity_expression": "表达",
}
# 概念 → 情境节点（层级合并：不重复建 dialogue_act 表示，用 细化 边相连）
CONCEPT_REFINES_CONTEXTS = {
    "指令": ["情境:用户请求", "情境:用户提问"],
    "表达": ["情境:用户情绪表达", "情境:用户问候", "情境:用户感谢",
             "情境:用户道别", "情境:用户回应", "情境:用户情绪低落"],
    "断言": ["情境:用户陈述", "情境:用户观点", "情境:用户分享"],
    "承诺": [],
    "宣告": [],
}
# 概念 → 回应方式（倾向边，认知类=前向传播；权重=常识强度，非确定性）
CONCEPT_TO_RESPONSE = {
    "指令": [("行动回应", 0.50), ("信息回应", 0.30)],
    "表达": [("社交回应", 0.50)],
    "断言": [("信息回应", 0.40), ("社交回应", 0.20)],
    "承诺": [("承诺确认", 0.50), ("行动回应", 0.25)],
    "宣告": [("社交回应", 0.30)],
}
# 回应方式 → 行为竞争词表（disposition.BEHAVIORS 的节点名）
RESPONSE_TO_BEHAVIOR = {
    "行动回应": [("行为:回应", 0.50), ("行为:延续", 0.30)],
    "信息回应": [("行为:详述", 0.50), ("行为:回应", 0.35), ("行为:追问", 0.25)],
    "社交回应": [("行为:共情", 0.45), ("行为:确认", 0.40), ("行为:分享", 0.35)],
    "承诺确认": [("行为:确认", 0.50), ("行为:回应", 0.30)],
}
# 行动回应与具身能力的关联（仅当图里存在该节点时连线——Minecraft 跟随等
# 技能概念是扩散可到达的下一跳，不是代码映射）
RESPONSE_CAPABILITY_EDGES = {"行动回应": ["进入Minecraft世界", "跟随"]}

UTTERANCE_KEEP = 12          # 话语事件节点保留上限（短期认知，超出即清）
_COG_REL = "cognitive_relation"
_SEM_REL = "semantic_relation"
_EPI_REL = "episodic"


def resolve_concept(parsed: dict) -> str:
    """parsed → 言语行为概念节点名（illocutionary 优先，dialogue_act 兜底）。"""
    p = parsed or {}
    ill = str(p.get("illocutionary_act") or "").strip().lower()
    if ill in ILLOC_TO_CONCEPT:
        return ILLOC_TO_CONCEPT[ill]
    zh = str(p.get("speech_act") or "").strip()   # L1 中文值（"指令"）
    if zh in CONCEPTS:
        return zh
    da = str(p.get("dialogue_act") or "").strip()
    return DA_TO_CONCEPT.get(da, "断言")


def _ensure_concept_node(kg, engine, nid: str, *, desc: str,
                         graph_space: str, label: str) -> bool:
    """幂等建节点并注册进引擎名字索引（新节点不注册则激活恒为 0——
    与行动留痕同坑）。返回是否新建。"""
    created = False
    with kg._lock:
        if nid not in kg.nodes:
            kg.add_node(Node(id=nid, weight=0.5, label=label,
                             graph_space=graph_space,
                             extra_attrs={"type": "speech_act_concept",
                                          "description": desc,
                                          "created": now_str()}))
            created = True
        node = kg.nodes.get(nid)
    if node is not None and engine is not None:
        with engine._lock:
            engine.name_to_node[nid] = node
    return created


def _ensure_edge(kg, src: str, dst: str, rel: str, w: float, cat: str) -> None:
    with kg._lock:
        if (src in kg.nodes and dst in kg.nodes
                and kg.get_edge(src, dst, rel) is None):
            kg.add_edge(Edge(src=src, dst=dst, relation=rel, weight=w,
                             relation_category=cat))


def bootstrap_speech_acts(kg, engine=None) -> dict:
    """概念层播种（幂等，app 启动时调用一次，inject 时也会自愈补边）。"""
    n_nodes = 0
    _ensure_concept_node(kg, engine, CONCEPT_ROOT,
                         desc="言外行为的总概念：话语在做什么（断言/指令/…）",
                         graph_space="semantic", label="declarative-semantic")
    for c in CONCEPTS + RESPONSE_MODES:
        if _ensure_concept_node(
                kg, engine, c,
                desc=f"言语行为概念：{c}",
                graph_space="semantic", label="declarative-semantic"):
            n_nodes += 1
    # 概念 → 根
    for c in CONCEPTS:
        _ensure_edge(kg, c, CONCEPT_ROOT, "属于", 0.6, _SEM_REL)
    # 概念 → 情境（层级合并既有 dialogue_act 图谱表示）
    for c, ctxs in CONCEPT_REFINES_CONTEXTS.items():
        for ctx in ctxs:
            _ensure_edge(kg, c, ctx, "细化", 0.4, _SEM_REL)
    # 概念 → 回应方式 → 行为词表（倾向通道，不决策）
    for c, modes in CONCEPT_TO_RESPONSE.items():
        for mode, w in modes:
            _ensure_edge(kg, c, mode, "倾向", w, _COG_REL)
    for mode, behaviors in RESPONSE_TO_BEHAVIOR.items():
        for bhv, w in behaviors:
            _ensure_edge(kg, mode, bhv, "倾向", w, _COG_REL)
    # 回应方式 → 具身能力概念（存在才连）
    for mode, caps in RESPONSE_CAPABILITY_EDGES.items():
        for cap in caps:
            _ensure_edge(kg, mode, cap, "关联", 0.35, _SEM_REL)
    return {"concepts": len(CONCEPTS) + 1, "response_modes": len(RESPONSE_MODES),
            "created_nodes": n_nodes}


def _prune_utterances(kg, engine) -> int:
    """话语事件节点有界清理（短期认知状态：episodic 快衰减之外再加数量帽）。"""
    with kg._lock:
        utts = sorted((nid for nid in kg.nodes
                       if str(nid).startswith("话语_")),
                      key=lambda n: (kg.nodes[n].created or "", n))
    doomed = utts[:max(0, len(utts) - UTTERANCE_KEEP)]
    for nid in doomed:
        try:
            kg.remove_node(nid)     # remove_node 会带走关联边并通知引擎钩子
            if engine is not None:
                with engine._lock:
                    engine.name_to_node.pop(nid, None)
        except Exception as e:
            logger.debug(f"[SpeechAct] 话语清理失败 {nid}: {e}")
    return len(doomed)


def inject_utterance(kg, engine, text: str, parsed: dict,
                     cycle_id: str = None) -> dict:
    """本轮话语 → 图谱事件节点 + 概念归类边。

    返回 {utterance_id, concept, context_id, seeds:[…]}；seeds 由调用方
    并入 activate_from_inputs 的输入（与 社交互动 节点同一扇门——统一
    激活入口，不建第二套扩散）。
    """
    bootstrap_speech_acts(kg, engine)          # 幂等自愈（首包/热重建后都在）
    concept = resolve_concept(parsed)
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    uid = f"话语_{ts}_{int(time.time() * 1000) % 1000:03d}"
    utt_node = Node(
        id=uid, weight=0.4, label="declarative-episodic",
        graph_space=_EPI_REL,
        extra_attrs={
            "type": "utterance_event",
            "text": str(text or "")[:160],
            "illocutionary_act": (parsed or {}).get("illocutionary_act"),
            "dialogue_act": (parsed or {}).get("dialogue_act"),
            "concept": concept,
            "cycle_id": cycle_id,
            "created": now_str(),
        })
    with kg._lock:
        kg.add_node(utt_node)
    if engine is not None:
        with engine._lock:
            engine.name_to_node[uid] = utt_node
    # 结构边（只连已存在节点，不造新实体——与搜索留痕同规则）
    _ensure_edge(kg, "用户", uid, "说出", 0.8, _COG_REL)
    _ensure_edge(kg, uid, concept, "言外行为", 0.8, _COG_REL)
    content_linked = []
    with kg._lock:
        entities = [n for n in ((parsed or {}).get("nodes") or [])
                    if isinstance(n, str) and n in kg.nodes
                    and n not in ("用户", "Fascinator")][:3]
    for ent in entities:
        _ensure_edge(kg, uid, ent, "涉及", 0.5, _COG_REL)
        content_linked.append(ent)
    removed = _prune_utterances(kg, engine)
    logger.info(f"[SpeechAct] 话语入图: {uid} 言外行为={concept} "
                f"内容={content_linked} 清理旧话语={removed}")
    return {"utterance_id": uid, "concept": concept,
            "context_id": None, "content": content_linked,
            "seeds": [concept, uid]}


def projection(kg, engine, sa_info: dict) -> dict:
    """扩散之后对本轮言外行为的图谱投影：概念激活值 + 共激活近邻。

    这是给 OGCTX 的状态摘要（projection of current graph state），
    供语言层感知"这个言语行为此刻在认知里点亮了什么"。
    """
    if not sa_info or not sa_info.get("concept"):
        return {}
    concept = sa_info["concept"]
    uid = sa_info.get("utterance_id")
    nodes = {concept}
    if uid:
        nodes.add(uid)
    with kg._lock:
        cnode = kg.nodes.get(concept)
        activation = round(float(getattr(cnode, "activation", 0) or 0), 3) if cnode else 0.0
        pool = {}
        for e in kg.edges:
            if e.src == concept and e.dst in kg.nodes:
                pool[e.dst] = round(float(getattr(kg.nodes[e.dst], "activation", 0) or 0), 3)
            elif uid and (e.src == uid or (e.dst == uid and e.src in kg.nodes
                                           and str(e.src).startswith("话语_"))):
                other = e.dst if e.src == uid else e.src
                if other in kg.nodes and other != concept:
                    pool[other] = round(
                        float(getattr(kg.nodes[other], "activation", 0) or 0), 3)
    # 0.05 门槛：二跳（概念→行动回应）的典型激活落点在此附近；
    # 回合间衰减地板（0.5）保证轮间不会积累噪声，这里只滤当轮的浮点尾差。
    coact = [{"id": nid, "act": a} for nid, a in
             sorted(pool.items(), key=lambda kv: -kv[1]) if a >= 0.05][:8]
    return {"concept": concept, "activation": activation,
            "utterance_id": uid, "coactivated": coact}
