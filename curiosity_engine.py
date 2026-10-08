# curiosity_engine.py — 探索信号 / CuriosityDrive / 探索行为候选
# ============================================================================
# Fascinator 的好奇心机制（2026-09 重构）。
#
# 架构语义（与 16 条设计原则对齐）：
#   未知 ≠ 好奇；好奇 ≠ 提问；好奇 ≠ 一个需要完成的任务。
#
#   未知/不确定性/情绪确认 只是**认知 discrepancy 信号**（论文 §任务二：
#   未知检测 → 扩散激活与未知处理相关的 Action 节点）。检测器只负责报告
#   "这里出现了可能值得探索的东西"，注入图谱信号；**是否探索、以何种方式
#   探索（问用户 / 自己搜 / 观察 / 什么都不做）由行为竞争决定**，不写死
#   unknown → ask 的固定程序。
#
#   好奇心的本体是 CuriosityDrive（cognitive 空间 Drive 节点）——一个持续
#   变化的内部驱动力，可以增强、减弱、被其他关注点压制、再次出现。
#
#   "活跃的提问"（pending_inquiry）不是好奇心本体，只是一种待处理交互：
#   可被用户忽略（答非所问 → 过期，兴趣保留），可超时失效。
#   兴趣水位挂在**目标节点** extra_attrs.interest 上，被回答后衰减而非清零
#   ——学到 X 不等于对 X 的兴趣结束，新知识可以派生新的兴趣。
#
# LLM 边界：
#   LLM 只在 generate_question() 里做"把已决定的探索目标转成自然语言问句"，
#   不判断是否好奇、不判断是否该探索、不决定探索目标。
# ============================================================================

import logging
import re
import time
from typing import Optional

from graph_model import KnowledgeGraph, Node, Edge, now_str

logger = logging.getLogger(__name__)

# ── 声明式语义节点（declarative-semantic） ──
# 历史说明：P0-2 删除了纯流水线节点（知识缺口/需要学习/学习目标/主动提问）。
# 本清单保留的节点继续存在（不删图，保持旧存档兼容），但语义已变更：
#   未知*   = cognitive discrepancy 信号节点（不确定性标记，非好奇触发器）
#   好奇     = 兼容别名（旧信号边的落点；决策读点一律改读 CuriosityDrive）
#   等待回答 = pending_inquiry 状态载体（待处理交互，非"好奇任务"）
#   知识完善/回答完成 = 探索结果的事件标记
DECLARATIVE_NODES = [
    "未知信息",
    "未知概念",
    "未知关系",
    "未知属性",
    "未知事件",
    "需要确认",
    "好奇",
    "等待回答",
    "知识完善",
    "回答完成",
]

# ── 程序性节点 ──
# 旧版的 execution 沙箱代码（result["action"] = "generate_curiosity_question"
# 等）从未被任何消费方读取——app.py 直接调用本模块函数，不经过 execute_action。
# 属于装饰性死代码，本次清除；节点本身保留（label=procedural 不变，
# 不破坏已有存档与行动队列语义）。
PROCEDURAL_NODES = {
    "生成好奇问题",
    "等待用户回答",
    "更新记忆",
    "结束好奇状态",
}

# ── 信号边（历史 P0-2 遗产 + 新通路） ──
# 旧边 未知* → 好奇 → 生成好奇问题 原样保留（存档兼容）：它们不再承载行为
# 含义（代码不读"生成好奇问题"的激活来决定提问），只是让信号仍能经扩散
# 到达 CuriosityDrive 的邻域。
# 新通路 未知* → CuriosityDrive（uncertainty_signal）是决策的正式读点：
# 扩散把 discrepancy 信号送到 Drive 节点，与 novelty/relevance/interest
# 一起形成探索驱动力。
CURIOSITY_SIGNAL_EDGES = [
    ("未知概念",   "好奇", "trigger_curiosity", 0.5),
    ("未知关系",   "好奇", "trigger_curiosity", 0.5),
    ("未知属性",   "好奇", "trigger_curiosity", 0.5),
    ("未知事件",   "好奇", "trigger_curiosity", 0.5),
    ("未知信息",   "好奇", "trigger_curiosity", 0.5),
    ("需要确认",   "好奇", "trigger_curiosity", 0.5),
    ("好奇",       "生成好奇问题", "trigger_curiosity", 0.6),
]

# 新决策通路：discrepancy 信号 → CuriosityDrive（探索驱动力）
DRIVE_SIGNAL_EDGES = [
    ("未知概念", "CuriosityDrive", "uncertainty_signal", 0.6),
    ("未知关系", "CuriosityDrive", "uncertainty_signal", 0.6),
    ("未知属性", "CuriosityDrive", "uncertainty_signal", 0.6),
    ("未知事件", "CuriosityDrive", "uncertainty_signal", 0.6),
    ("未知信息", "CuriosityDrive", "uncertainty_signal", 0.8),
]

# 无意义关系词过滤（语法性关系 ≠ 知识缺口；分类维护，非逐案例硬编码）
TRIVIAL_RELATIONS = {
    # 空间/位置
    "位于", "在", "住在", "处于", "坐落于", "位置在",
    # 动作/行为
    "参与", "参加", "做", "干", "进行", "从事", "玩", "用",
    "去", "来", "回", "到", "走", "跑", "拿", "放",
    # 心理/感知
    "想", "想要", "想玩", "想做", "想买", "想吃", "想喝",
    "喜欢", "不喜欢", "爱", "讨厌",
    "觉得", "认为", "以为", "知道", "了解", "听说",
    "感到", "感觉", "感受",
    "看见", "看到", "遇到", "碰见", "听见", "闻到",
    # 存在/归属
    "是", "有", "拥有", "具有", "属于", "包括", "包含",
    # 时间
    "开始", "结束", "发生于", "发生在", "持续",
    # 意愿/情态
    "准备", "尝试", "打算", "希望", "可能", "可以", "能够",
    "需要", "应该", "必须",
}

_TECH_TOKEN_RE = re.compile(r"[A-Za-z0-9]")

# ═══════════════════════════════════════════════════════════════
# pending_inquiry 状态（图内化；可忽略、可过期，非一次性任务）
# ═══════════════════════════════════════════════════════════════

_STATE_NODE_ID = "等待回答"
_STATE_KEY = "pending_inquiry"
_LEGACY_STATE_KEY = "curiosity"   # 旧一次性状态机的字段名，只读兼容

_CURIOSITY_STATE_NODE_ATTRS = {"type": "curiosity", "category": "cognitive"}


def _empty_state() -> dict:
    return {
        "active": False,
        "question": None,           # 提出过的探索问题文本
        "target": None,             # 探索目标名（概念/关系描述）
        "target_node": None,        # 目标节点 id（UnknownConcept_* 或已有节点）
        "unknown_nodes": [],
        "unknown_relations": [],
        "trigger_type": None,       # unknown_concept / unknown_relation
        "asked_at": None,           # time.time()（超时判定用）
        "asked_at_str": None,       # now_str()（可读）
    }


def _ensure_state_node(kg: KnowledgeGraph) -> Node:
    """获取（必要时创建）承载 pending_inquiry 的 等待回答 节点。"""
    node = kg.get_node(_STATE_NODE_ID)
    if node is None:
        node = Node(
            id=_STATE_NODE_ID,
            weight=0.3,
            label="declarative-semantic",
            graph_space="cognitive",
            extra_attrs=dict(_CURIOSITY_STATE_NODE_ATTRS),
        )
        kg.add_node(node)
        logger.info("[Curiosity] 创建状态节点: 等待回答")
    return node


def _read_state(kg: KnowledgeGraph) -> dict:
    node = kg.get_node(_STATE_NODE_ID)
    if node is None:
        return _empty_state()
    st = (node.extra_attrs or {}).get(_STATE_KEY)
    if isinstance(st, dict) and st.get("active"):
        state = _empty_state()
        state.update(st)
        return state
    # 旧一次性状态机字段兼容读取（旧存档迁移期）
    legacy = (node.extra_attrs or {}).get(_LEGACY_STATE_KEY)
    if isinstance(legacy, dict) and legacy.get("active"):
        state = _empty_state()
        state.update(legacy)
        if not state.get("target"):
            state["target"] = (legacy.get("unknown_nodes") or [None])[0]
        return state
    return _empty_state()


def _write_state(kg: KnowledgeGraph, state: dict):
    node = _ensure_state_node(kg)
    ea = node.extra_attrs
    ea[_STATE_KEY] = dict(state)
    # 迁移：清掉旧一次性状态字段（只在本节点，不动其他数据）
    if _LEGACY_STATE_KEY in ea:
        legacy = ea.pop(_LEGACY_STATE_KEY)
        if state.get("active") is False and isinstance(legacy, dict):
            logger.info("[Curiosity] 已迁移旧好奇状态字段 → pending_inquiry")


def get_curiosity_state(kg: KnowledgeGraph) -> dict:
    """当前待处理探索提问（兼容旧读取方：active/question/trigger_type）。"""
    return _read_state(kg)


def is_curiosity_active(kg: KnowledgeGraph) -> bool:
    return bool(_read_state(kg).get("active"))


def reset_curiosity(kg: KnowledgeGraph):
    _write_state(kg, _empty_state())


# ═══════════════════════════════════════════════════════════════
# 兴趣水位（挂在目标节点上；持续量，衰减不清零）
# ═══════════════════════════════════════════════════════════════

def get_interest(kg: KnowledgeGraph, target_node: str) -> dict:
    node = kg.get_node(target_node) if target_node else None
    if node is None:
        return {"level": 0.0, "ask_count": 0, "resolved_count": 0,
                "ignored_count": 0, "last_outcome": None}
    it = (node.extra_attrs or {}).get("interest")
    if not isinstance(it, dict):
        return {"level": 0.0, "ask_count": 0, "resolved_count": 0,
                "ignored_count": 0, "last_outcome": None}
    out = {"level": 0.0, "ask_count": 0, "resolved_count": 0,
           "ignored_count": 0, "last_outcome": None}
    out.update({k: v for k, v in it.items() if k in out})
    return out


def note_interest(kg: KnowledgeGraph, target_node: str, event: str,
                  config: dict = None) -> dict:
    """兴趣水位更新（确定性，无 LLM）。

    event: signal（又遇到） / fired（发出探索行动） /
           resolved（被回答） / ignored（被忽略/超时） / searched（自搜完成）
    兴趣可以增强、衰减、被压制、再次出现——但绝不因一次回答而清零。
    """
    cfg = (config or {}).get("curiosity", {})
    node = kg.get_node(target_node) if target_node else None
    if node is None:
        return {}
    it = dict(get_interest(kg, target_node))
    if event == "signal":
        it["level"] = min(1.0, it["level"] + float(cfg.get("interest_signal_gain", 0.25)))
        it["last_signal_at"] = now_str()
    elif event == "fired":
        it["ask_count"] = it.get("ask_count", 0) + 1
        it["last_explored_at"] = now_str()
        # 她决定探索这个目标——这本身就是兴趣的证明（下限 0.5）
        it["level"] = round(min(1.0, max(it["level"], 0.5)), 4)
    elif event == "searched":
        it["last_explored_at"] = now_str()
        it["level"] = round(min(1.0, max(it["level"], 0.5)), 4)
    elif event == "resolved":
        it["level"] = round(it["level"] * float(cfg.get("interest_resolve_decay", 0.4)), 4)
        it["resolved_count"] = it.get("resolved_count", 0) + 1
        it["last_outcome"] = "resolved"
        it["last_explored_at"] = now_str()
    elif event == "ignored":
        it["level"] = round(it["level"] * float(cfg.get("interest_ignore_decay", 0.85)), 4)
        it["ignored_count"] = it.get("ignored_count", 0) + 1
        it["last_outcome"] = "ignored"
    it["level"] = round(max(0.0, min(1.0, it["level"])), 4)
    node.extra_attrs["interest"] = it
    node.touch()
    return it


def ensure_exploration_target(kg: KnowledgeGraph, target: str) -> str:
    """探索目标落图（论文 §任务二的 CreateUnknownNode 语义）。

    目标已在图中 → 直接用；不在 → 建 UnknownConcept_{name}（cognitive 空间）。
    只在探索行动真正 fires 时调用（不在检测层，防节点爆炸）。
    """
    target = str(target or "").strip()
    if not target:
        return ""
    if target in kg.nodes:
        return target
    nid = f"UnknownConcept_{target}"
    if nid in kg.nodes:
        return nid
    kg.add_node(Node(
        id=nid, weight=0.4, label="declarative-semantic",
        graph_space="cognitive",
        extra_attrs={"type": "unknown", "kind": "concept", "name": target,
                     "created": now_str()}))
    logger.info(f"[Curiosity] 探索目标落图: {nid}")
    try:  # 观测：好奇目标登记（为什么图上多了这个节点）
        import fas_log
        fas_log.get_logger(fas_log.CURIOSITY).info(
            "target_registered", f"探索目标落图: {nid}", target=nid)
    except Exception:
        pass
    return nid


# ═══════════════════════════════════════════════════════════════
# Bootstrap（幂等：对已有节点做对齐，不删不重建）
# ═══════════════════════════════════════════════════════════════

def ensure_drive_signal_edges(kg: KnowledgeGraph):
    """确保 未知* → CuriosityDrive 的决策通路边存在（幂等）。

    端点缺失时跳过（bootstrap 顺序无关紧要：DriveEvaluator.bootstrap_drives
    创建 CuriosityDrive 之后也会调用本函数补边）。
    """
    created = 0
    for src, dst, rel, weight in DRIVE_SIGNAL_EDGES:
        if kg.get_node(src) is None or kg.get_node(dst) is None:
            continue
        if kg.get_edge(src, dst, rel) is None:
            kg.add_edge(Edge(src=src, dst=dst, relation=rel, weight=weight,
                             relation_category="cognitive_relation"))
            created += 1
    if created:
        logger.info(f"[Curiosity] 补建决策通路边 ×{created}（未知* → CuriosityDrive）")
    return created


def bootstrap_curiosity(kg: KnowledgeGraph, engine=None):
    """注入好奇心基础设施；对旧存档做幂等对齐。

    - 10 个 declarative + 4 个 procedural 节点：缺失才建，已存在则对齐
      graph_space / 清除死的 execution 字符串（不删节点）。
    - 旧信号边（未知* → 好奇 → 生成好奇问题）原样保留。
    - 新决策通路（未知* → CuriosityDrive）：缺失才建。
    """
    node_count = 0
    edge_count = 0

    for nid in DECLARATIVE_NODES:
        node = kg.get_node(nid)
        if node is None:
            kg.add_node(Node(
                id=nid, weight=0.3, label="declarative-semantic",
                graph_space="cognitive",
                extra_attrs={"type": "curiosity", "category": "cognitive"}))
            node_count += 1
        else:
            # 幂等对齐：旧存档的 未知*/好奇 等节点 graph_space 可能还是
            # semantic（旧版 bootstrap 只建不改）。空间标注不影响扩散数学
            # （semantic 与 cognitive 参数接近），统一为 cognitive 保持一致。
            if node.graph_space != "cognitive":
                node.graph_space = "cognitive"
            node.extra_attrs.setdefault("type", "curiosity")
            node.extra_attrs.setdefault("category", "cognitive")

    for nid in PROCEDURAL_NODES:
        node = kg.get_node(nid)
        if node is None:
            kg.add_node(Node(
                id=nid, weight=0.3, label="procedural",
                graph_space="cognitive",
                extra_attrs={"type": "curiosity", "category": "cognitive"}))
            node_count += 1
        elif node.execution:
            # 旧存档里的 execution 是从未被消费的装饰性代码，清除
            node.execution = None

    all_edges = list(CURIOSITY_SIGNAL_EDGES)
    for src, dst, rel, weight in all_edges:
        if kg.get_node(src) is None or kg.get_node(dst) is None:
            continue
        if kg.get_edge(src, dst, rel) is None:
            kg.add_edge(Edge(src=src, dst=dst, relation=rel, weight=weight,
                             relation_category="cognitive_relation"))
            edge_count += 1

    # 决策通路边（未知* → CuriosityDrive）：端点可能由 DriveEvaluator 稍后
    # 创建，那里也会调用 ensure_drive_signal_edges 补边——两边都幂等
    edge_count += ensure_drive_signal_edges(kg)

    logger.info(
        f"[Curiosity] Bootstrap 完成 (新增节点: {node_count}, 新增边: {edge_count})")


# ═══════════════════════════════════════════════════════════════
# 检测层：认知 discrepancy 信号（只报告 + 注入，不决定行为）
# ═══════════════════════════════════════════════════════════════

def check_and_inject_curiosity(kg, engine, parsed_nodes, parsed_edges,
                               user_text, cognitive_context=None):
    """兼容别名 → detect_cognitive_signals（旧调用方语义不变）。"""
    return detect_cognitive_signals(kg, engine, parsed_nodes, parsed_edges,
                                    user_text, cognitive_context)


def detect_cognitive_signals(
        kg: KnowledgeGraph,
        engine,
        parsed_nodes: list,
        parsed_edges: list,
        user_text: str,
        cognitive_context: dict = None,
        config: dict = None,
) -> dict:
    """检测认知 discrepancy（未知概念/未知关系/情绪确认需求）并注入图信号。

    与旧版 check_and_inject_curiosity 的区别：
      1. 注入强度不再恒定：raw × (floor + (1-floor)×mod)，mod 由
         novelty / relevance / 场合 / satiation / repeat 调制——
         "未知"只是输入之一，弱信号允许在扩散中自然湮灭（什么都不做合法）。
      2. sharing/information_statement 不再一刀切抑制，改为场合系数
         （0.4~0.5）——是否打断交给行为竞争（"很感兴趣时可以问"成立）。
      3. 返回结构化信号清单（含逐因子数值），供候选构建与调试输出。
      4. 情绪表达仍走 需要确认（关心用户），不产生探索候选。
    """
    cfg = (config or {}).get("curiosity", {})
    cog = cognitive_context or {}

    # 已有待处理的探索提问 → 不再注入新的概念/关系信号（一次一个）
    if is_curiosity_active(kg):
        logger.info("[Curiosity] 已有待处理的探索提问，跳过新信号检测")
        return {"triggered": False, "reason": "pending_inquiry"}

    mod_floor = float(cfg.get("mod_floor", 0.4))
    occasion = dict(cfg.get("occasion_factor", {}))

    da = str(cog.get("dialogue_act", ""))
    occ_f = float(occasion.get(da, 0.3))   # 未列举的对话行为保守取低系数

    # ── 情绪检测（图谱驱动：不硬编码情绪词表）──
    has_emotion = False
    emotion_found = None
    with kg._lock:
        for nid, node in kg.nodes.items():
            if (node.extra_attrs or {}).get("type") == "emotion" and nid in user_text:
                has_emotion = True
                emotion_found = nid
                break

    # 情绪在场：概念/关系探索大幅退让（先回应情绪；知识缺口可以之后再说）
    emotion_suppress = 0.25 if has_emotion else 1.0

    # ── 检测未知节点 ──
    unknown_nodes = []
    for nid in parsed_nodes:
        nid_str = str(nid).strip()
        if nid_str and nid_str not in engine.name_to_node:
            unknown_nodes.append(nid_str)

    # ── 检测未知关系（两端已知、关系不存在）──
    unknown_relations = []
    for edge_spec in parsed_edges:
        src = str(edge_spec.get("src", "")).strip()
        dst = str(edge_spec.get("dst", "")).strip()
        rel = str(edge_spec.get("type", edge_spec.get("relation", ""))).strip()
        if not src or not dst or not rel:
            continue
        if (src in engine.name_to_node and dst in engine.name_to_node
                and kg.get_edge(src, dst, rel) is None):
            unknown_relations.append({"src": src, "dst": dst, "relation": rel})

    meaningful_relations = [r for r in unknown_relations
                            if str(r.get("relation", "")).strip() not in TRIVIAL_RELATIONS]

    signals = []

    def _inject(signal_node_id: str, raw: float, target: str,
                sig_type: str) -> float:
        """信号注入：raw × (floor + (1-floor)×mod)。返回实际注入量。"""
        node = kg.get_node(signal_node_id)
        if node is None or raw <= 0:
            return 0.0
        tnode = kg.get_node(target) if target else None
        # novelty：图外/未知目标 > 已有目标
        novelty = 1.0 if tnode is None else (
            1.0 if str(tnode.id).startswith(("Unknown", "未知")) else 0.3)
        # relevance：目标是否在当前认知焦点里（正在聊的事相关 → 更值得注意）
        relevance = 0.2
        if tnode is not None:
            try:
                topk, _ = engine.get_topk(k=10)
                topk_ids = {n.id for n in topk}
                if tnode.id in topk_ids:
                    relevance = 1.0
            except Exception:
                pass
        # satiation：最近已被回答过 → 信号降级（学到了 ≠ 完全不好奇，但降）
        interest = get_interest(kg, _target_node_id(kg, target))
        satiation = min(1.0, interest.get("resolved_count", 0) * 0.4)
        mod = (0.45 * novelty + 0.35 * relevance + 0.20 * (1.0 - satiation))
        mod = max(0.0, min(1.0, mod))
        injected = raw * (mod_floor + (1.0 - mod_floor) * mod)
        node.activation = min(5.0, node.activation + injected)
        node.touch()
        engine.mark_active([node.id])
        signals.append({
            "type": sig_type, "target": target, "node": signal_node_id,
            "raw": raw, "mod": round(mod, 3), "injected": round(injected, 3),
            "factors": {"novelty": round(novelty, 2),
                        "relevance": round(relevance, 2),
                        "satiation": round(satiation, 2),
                        "occasion": occ_f, "emotion_suppress": emotion_suppress},
        })
        logger.info(
            f"[Curiosity] 信号 {sig_type} target={target} raw={raw} "
            f"mod={mod:.2f} injected={injected:.3f} factors={signals[-1]['factors']}")
        return injected

    # ── 情绪优先：概念/关系探索为共情让路 ──
    trigger_type = None
    if has_emotion:
        trigger_type = "emotion"
        node = kg.get_node("需要确认")
        if node:
            node.activation = min(5.0, node.activation + 1.5)
            node.touch()
            engine.mark_active([node.id])
            engine.register_activation_source([node.id], "internal_drive")
        logger.info(f"[Curiosity] 检测到情绪表达: {emotion_found}，概念/关系探索退让")
    elif occ_f <= 0.0:
        logger.info(f"[Curiosity] 场合（dialogue_act={da}）不适合概念探索，跳过注入")
    else:
        if unknown_nodes and occ_f > 0.0:
            trigger_type = "unknown_concept"
            _inject("未知概念", 0.5 * occ_f * emotion_suppress,
                    unknown_nodes[0], "unknown_concept")
        if meaningful_relations and occ_f > 0.0:
            trigger_type = trigger_type or "unknown_relation"
            rel0 = meaningful_relations[0]
            _inject("未知关系", 0.5 * occ_f * emotion_suppress,
                    rel0.get("dst") or rel0.get("src"), "unknown_relation")
        if unknown_relations and not meaningful_relations:
            logger.info(f"[Curiosity] 过滤 {len(unknown_relations)} 个语法性关系词: "
                        f"{[r.get('relation') for r in unknown_relations]}")

    # 兜底信号：未知信息（探索行为与网络搜索的图上入口）
    if trigger_type in ("unknown_concept", "unknown_relation"):
        node = kg.get_node("未知信息")
        if node:
            node.activation = min(5.0, node.activation + 0.25)
            node.touch()
            engine.mark_active([node.id])

    result = {
        "triggered": trigger_type is not None,
        "trigger_type": trigger_type,
        "unknown_nodes": unknown_nodes,
        "unknown_relations": unknown_relations,
        "has_emotion": has_emotion,
        "emotion": emotion_found,
        "signals": signals,
        "occasion_factor": occ_f,
    }
    if trigger_type and signals:
        # 兴趣水位：给主信号目标记一次"又遇到了"（仅当目标已在图中）
        tgt = signals[0].get("target")
        tn = kg.get_node(tgt) if tgt else None
        if tn is not None:
            note_interest(kg, tn.id, "signal", config)
    return result


def _target_node_id(kg: KnowledgeGraph, target: str) -> str:
    """目标名 → 节点 id（不落新节点；图外目标返回 UnknownConcept_ 预期 id）。"""
    target = str(target or "").strip()
    if target in kg.nodes:
        return target
    return f"UnknownConcept_{target}"


# ═══════════════════════════════════════════════════════════════
# 探索行为候选（供统一行为竞争；本模块只造候选，不裁决）
# ═══════════════════════════════════════════════════════════════

def build_exploration_candidates(kg: KnowledgeGraph, config: dict,
                                 gate: bool, signal_result: dict,
                                 cognitive_context: dict = None) -> dict:
    """构建探索行为候选（explore_ask / explore_search）。

    调用时机：本轮扩散完成、drive 已刷新之后（候选评分读图上真实激活）。
    gate 是 should_generate_question() 的结果——图驱动门槛：discrepancy
    信号必须真的经扩散到达了行为节点（好奇/生成好奇问题 ≥ θ_action），
    检测到未知但扩散没到达 = 好奇心不够强 = 不产生候选（合法）。

    返回 {"eligible", "candidates": [...], "reason", "drive", "target"}。
    候选只带评分与解释；**裁决在 dialogue_decision / autonomy 的统一竞争里**。
    """
    cfg = (config or {}).get("curiosity", {})
    w = dict(cfg.get("weights", {}))
    w = {**{"drive": 0.40, "interest": 0.25, "signal": 0.20, "novelty": 0.15,
            "satiation": 0.35, "searchability": 0.25, "cost": 0.15}, **w}
    drive_norm = float(cfg.get("drive_norm", 3.0)) or 3.0
    min_score = float(cfg.get("explore_min_score", 0.35))

    out = {"eligible": False, "candidates": [], "reason": "", "drive": 0.0,
           "target": None}

    # 门 0：已有待处理提问 → 本轮不再探索（一次一个）
    if is_curiosity_active(kg):
        out["reason"] = "pending_inquiry"
        return out

    drive_node = kg.get_node("CuriosityDrive")
    drive = float(getattr(drive_node, "activation", 0.0) or 0.0) if drive_node else 0.0
    out["drive"] = round(drive, 3)
    drive_n = min(1.0, drive / drive_norm)

    # 门 1：图驱动门槛（扩散必须真的到达行为节点）
    if not gate:
        out["reason"] = "signal_below_threshold"
        return out

    # 门 2：信号来源（本轮检测到的 discrepancy）
    sigs = (signal_result or {}).get("signals") or []
    concept_sigs = [s for s in sigs if s.get("type") in ("unknown_concept",
                                                         "unknown_relation")]
    if not concept_sigs:
        out["reason"] = "no_concept_signal"
        return out

    sig = concept_sigs[0]
    target = str(sig.get("target") or "").strip()
    if not target:
        out["reason"] = "no_target"
        return out
    out["target"] = target

    tnid = _target_node_id(kg, target)
    tnode = kg.get_node(tnid)
    interest = get_interest(kg, tnid if tnode else None)
    level = float(interest.get("level", 0.0))
    satiation = min(1.0, float(interest.get("resolved_count", 0)) * 0.4)
    signal_strength = min(1.0, float(sig.get("injected", 0.0)) / 0.6)
    novelty = 1.0 if (tnode is None or tnid.startswith(("Unknown", "未知"))) else 0.3

    # 冷目标：兴趣水位太低且本轮信号也弱 → 不值得任何探索行动
    if (level < float(cfg.get("interest_min_for_ask", 0.15))
            and signal_strength < 0.4):
        out["reason"] = f"target_too_cold (level={level:.2f})"
        return out

    common = {
        "drive": round(drive_n, 3),
        "interest": round(level, 3),
        "signal": round(signal_strength, 3),
        "novelty": round(novelty, 2),
        "satiation": round(satiation, 2),
    }

    def _score(ws: dict, factors: dict) -> float:
        s = sum(float(w.get(k, 0)) * v for k, v in factors.items() if not k.startswith("-"))
        s -= sum(float(w.get(k[1:], 0)) * v for k, v in factors.items() if k.startswith("-"))
        return round(max(0.0, min(1.0, s)), 3)

    # ── explore_ask：向用户提问（ExploreByAskingUser）──
    ask_factors = dict(common)
    ask_factors["satiation"] = -common["satiation"]
    ask_score = _score(w, ask_factors)
    if ask_score >= min_score:
        out["candidates"].append({
            "action": "explore_ask",
            "score": ask_score,
            "win_threshold": float(cfg.get("explore_win_threshold", 0.55)),
            "margin": float(cfg.get("explore_margin", 0.05)),
            "target": target,
            "target_node": tnid,
            "factors": ask_factors,
            "reason": f"想进一步了解 {target}"
                      f"（drive={drive:.2f} interest={level:.2f}）",
        })

    # ── explore_search：自己搜（ExploreBySearch，论文 §在线知识扩展）──
    # 可搜索性：邻域稀疏（图外/少关联）或含技术名词 → 适合外部查询
    if tnode is not None:
        try:
            n_neighbors = len({e.dst for e in kg.get_out_edges(tnid)}
                              | {e.src for e in kg.get_in_edges(tnid)})
        except Exception:
            n_neighbors = 0
    else:
        n_neighbors = 0
    sparse_thr = int(cfg.get("sparse_neighbor_threshold", 3))
    sparse = n_neighbors < sparse_thr
    searchability = (1.0 if tnode is None else (0.8 if sparse else 0.2))
    if _TECH_TOKEN_RE.search(target):
        searchability = min(1.0, searchability + 0.2)
    search_factors = dict(common)
    search_factors["searchability"] = round(searchability, 2)
    search_factors["cost"] = 0.3          # 搜索有延迟/失败成本
    search_factors["satiation"] = -common["satiation"]
    search_score = _score(w, search_factors)
    if search_score >= min_score:
        out["candidates"].append({
            "action": "explore_search",
            "score": search_score,
            "win_threshold": float(cfg.get("explore_win_threshold", 0.55)),
            "margin": float(cfg.get("explore_margin", 0.05)),
            "target": target,
            "target_node": tnid,
            "factors": search_factors,
            "reason": f"可以自己查一下 {target}"
                      f"（可搜索性={searchability:.2f} "
                      f"邻域={n_neighbors}）",
        })

    out["candidates"].sort(key=lambda c: -c["score"])
    out["eligible"] = bool(out["candidates"])
    out["reason"] = "" if out["eligible"] else \
        f"候选低于入场分 {min_score}（ask={ask_score:.2f} search={search_score:.2f}）"
    return out


# ═══════════════════════════════════════════════════════════════
# 图驱动门槛（保留：扩散是否到达行为节点，由激活值判定）
# ═══════════════════════════════════════════════════════════════

def should_generate_question(kg: KnowledgeGraph, config: dict) -> bool:
    """图驱动门槛：discrepancy 信号经扩散是否到达了探索行为节点。

    这不是"要不要提问"的裁决——裁决在行为竞争。这里只回答：
    "信号够不够强到值得把探索行为放进候选席"。
    """
    theta = config.get("theta_action", 0.5)

    gen_node = kg.get_node("生成好奇问题")
    curious_node = kg.get_node("好奇")

    gen_act = gen_node.activation if gen_node else 0.0
    curious_act = curious_node.activation if curious_node else 0.0

    should_fire = (gen_act >= theta) or (curious_act >= theta)

    if should_fire:
        logger.info(
            f"[Curiosity] 图谱信号到达行为节点: 生成好奇问题={gen_act:.4f}, "
            f"好奇={curious_act:.4f}, 阈值={theta}")

    return should_fire


def cleanup_curiosity_activation(kg: KnowledgeGraph):
    """探索信号整合相结束后，衰减信号节点激活（防过冲干扰主流程）。"""
    curiosity_node_ids = set(DECLARATIVE_NODES)
    with kg._lock:
        for nid in curiosity_node_ids:
            node = kg.get_node(nid)
            if node and node.activation > 0:
                node.activation *= 0.3
                if node.activation < 0.01:
                    node.activation = 0.0
                node.touch()
    logger.debug("[Curiosity] 探索信号节点激活已衰减")


# ═══════════════════════════════════════════════════════════════
# 探索行动的执行侧（explore_ask 胜出后由 app.py 调用）
# ═══════════════════════════════════════════════════════════════

def record_pending_inquiry(kg: KnowledgeGraph, question: str, target: str,
                           target_node: str, signal_result: dict,
                           config: dict = None, engine=None) -> dict:
    """登记待处理的探索提问（explore_ask 胜出后调用）。

    pending_inquiry 是**待处理交互**，不是好奇心本体：
    用户可以回答（settle）、可以答非所问（忽略并过期，兴趣保留）。
    """
    state = {
        "active": True,
        "question": str(question or "").strip(),
        "target": str(target or "").strip() or None,
        "target_node": str(target_node or "").strip() or None,
        "unknown_nodes": list((signal_result or {}).get("unknown_nodes", [])),
        "unknown_relations": list((signal_result or {}).get("unknown_relations", [])),
        "trigger_type": (signal_result or {}).get("trigger_type"),
        "asked_at": time.time(),
        "asked_at_str": now_str(),
    }
    _write_state(kg, state)
    # 目标节点兜底落图（探索目标必须在图上，兴趣水位才有挂载点）
    if target and not kg.get_node(target_node or ""):
        target_node = ensure_exploration_target(kg, target)
    if target_node:
        note_interest(kg, target_node, "fired", config)
    wait_node = kg.get_node("等待回答")
    if wait_node:
        wait_node.activation = min(5.0, wait_node.activation + 2.0)
        wait_node.touch()
        # 收尾修复 2026-09-22：激活直写必须进活跃前沿（不变量，同模块
        # :480/:502/:527 均如此），否则 boost 永不衰减、跨轮堆积到 5.0 封顶
        if engine is not None:
            try:
                engine.mark_active([wait_node.id])
            except Exception:
                pass
    logger.info(f"[Curiosity] pending_inquiry 登记目标={target} q={question}")
    return state


def generate_question(nlp_processor, unknown_info: dict) -> Optional[str]:
    """
    使用 LLM 根据未知信息生成一句自然的好奇问题。

    LLM 的职责非常狭窄（P7）：
      - 仅负责将"已决定的探索目标"转化为自然语言问题
      - 不判断是否应该好奇（行为竞争已裁决）
      - 不自由聊天

    Args:
      nlp_processor: NLPProcessor 实例（用于调用 LLM）
      unknown_info: 信号/候选 dict（含 trigger_type / unknown_nodes /
                    unknown_relations / target）

    Returns:
      生成的自然语言问题，或 None（如果生成失败）
    """
    trigger_type = unknown_info.get("trigger_type", "unknown_concept")
    unknown_nodes = unknown_info.get("unknown_nodes", [])
    unknown_relations = unknown_info.get("unknown_relations", [])
    unknown_properties = unknown_info.get("unknown_properties", [])
    # 候选制胜后的目标（候选 dict 直接传入时用 target）
    target = unknown_info.get("target")

    # ── 根据触发类型构建 LLM prompt ──
    if trigger_type == "unknown_concept" and (unknown_nodes or target):
        t = target or unknown_nodes[0]
        prompt = (
            f'用户的输入中出现了一个你不了解的概念："{t}"。\n'
            f'请用一句自然的、简短的中文问题，向用户询问这个概念是什么。\n'
            f'问题应该像普通人聊天时遇到不懂的东西会问的那样自然。\n'
            f'\n'
            f'参考示例：\n'
            f'- 不知道"苹果" → "苹果是什么？"\n'
            f'- 不知道"漫展" → "漫展是什么样的地方？"\n'
            f'- 不知道"SAO" → "SAO是什么？"\n'
        )

    elif trigger_type == "unknown_relation" and unknown_relations:
        rel_info = unknown_relations[0]
        prompt = (
            f'用户提到 {rel_info["src"]} 和 {rel_info["dst"]} 之间存在'
            f'"{rel_info["relation"]}" 的关系，但你并不了解这个关系。\n'
            f'请用一句自然的、简短的中文问题，向用户询问这个关系。\n'
            f'\n'
            f'参考示例：\n'
            f'- 不知道"喜欢"关系 → "为什么你喜欢它？"\n'
        )

    elif trigger_type == "emotion" and unknown_properties:
        emotion = unknown_properties[0]
        prompt = (
            f'用户表达了"{emotion}"的情绪。\n'
            f'请用一句自然的、温和的、简短的中文问题，关心用户，询问发生了什么。\n'
            f'\n'
            f'参考示例：\n'
            f'- 用户说"今天很难过" → "发生了什么？"\n'
            f'- 用户说"好开心" → "有什么好事发生了吗？"\n'
        )

    else:
        # 兜底
        if unknown_nodes or target:
            t = target or unknown_nodes[0]
            prompt = (
                f'用户提到了"{t}"，但你对此不了解。\n'
                f'请用一句自然的中文问题向用户提问。\n'
            )
        else:
            logger.warning("[Curiosity] 没有足够信息生成问题")
            return None

    # ── 调用 LLM 生成问题 ──
    try:
        from langchain_core.prompts import ChatPromptTemplate
        # 模板经语言注入面取（system_prompt 缺席=遗留环境，回退旧直连，逐字等价；
        # 失败落入下方 _fallback_question 规则兜底）
        _sp = getattr(nlp_processor, "system_prompt", None)
        if _sp is not None:
            CURIOSITY_QUESTION = _sp("curiosity_question")
        else:
            from prompt_templates import CURIOSITY_QUESTION

        question_prompt = ChatPromptTemplate.from_messages([
            ("system", CURIOSITY_QUESTION),
            ("human", "{input}")
        ])

        chain = question_prompt | nlp_processor.chat_llm
        import fas_log
        with fas_log.llm_purpose("curiosity_question"):
            response = chain.invoke({"input": prompt})
        question = response.content if hasattr(response, 'content') else str(response)
        question = question.strip().strip('"').strip("'").strip("。").strip()

        if not question or len(question) < 2:
            logger.warning(f"[Curiosity] LLM 生成的问题为空或过短: {repr(question)}")
            return _fallback_question(unknown_info)

        logger.info(f"[Curiosity] LLM 生成问题: {question}")
        return question

    except Exception as e:
        logger.error(f"[Curiosity] LLM 生成问题失败: {e}")
        return _fallback_question(unknown_info)


def _fallback_question(unknown_info: dict) -> Optional[str]:
    """LLM 调用失败时的兜底问题生成（纯规则，不依赖 LLM）。"""
    trigger_type = unknown_info.get("trigger_type", "")
    unknown_nodes = unknown_info.get("unknown_nodes", [])
    unknown_relations = unknown_info.get("unknown_relations", [])
    unknown_properties = unknown_info.get("unknown_properties", [])
    target = unknown_info.get("target")

    if trigger_type == "unknown_concept" and (unknown_nodes or target):
        return f"{target or unknown_nodes[0]}是什么？"
    elif trigger_type == "unknown_relation" and unknown_relations:
        rel = unknown_relations[0]
        return f"{rel['src']}和{rel['dst']}之间有什么关系？"
    elif trigger_type == "emotion" and unknown_properties:
        return f"为什么{unknown_properties[0]}？"
    elif unknown_nodes or target:
        return f"{target or unknown_nodes[0]}是什么？"
    return None


# ═══════════════════════════════════════════════════════════════
# pending_inquiry 的结算（下一轮用户输入进来时调用）
# ═══════════════════════════════════════════════════════════════

def settle_inquiry(kg: KnowledgeGraph, user_text: str,
                   parsed_nodes: list = None, engine=None,
                   config: dict = None) -> dict:
    """结算待处理的探索提问（可回答、可忽略）。

    与旧版 resolve_curiosity 的本质区别：
      旧版把下一轮用户输入**无条件**当作回答——好奇成为强制打断。
      新版先做相关性判定（确定性，零 LLM）：
        - 目标名（或其别名/子串）出现在用户文本或解析节点里 → 视为回答
        - 答非所问 → **忽略**：inquiry 过期、兴趣水位轻衰减（不清零）、
          用户输入照常走自己的处理流程
        - 超时（inquiry_timeout_s）→ 自动失效
    """
    cfg = (config or {}).get("curiosity", {})
    state = _read_state(kg)
    if not state.get("active"):
        return {"settled": False, "dismissed": False,
                "reason": "no_pending_inquiry"}

    question = state.get("question", "")
    target = state.get("target") or (state.get("unknown_nodes") or [None])[0]
    target_node = state.get("target_node")
    unknown_nodes = list(state.get("unknown_nodes", []))
    unknown_relations = list(state.get("unknown_relations", []))
    trigger_type = state.get("trigger_type")

    asked_at = state.get("asked_at")
    timeout_s = float(cfg.get("inquiry_timeout_s", 900))
    if asked_at and (time.time() - float(asked_at)) > timeout_s:
        if target_node:
            note_interest(kg, target_node, "ignored", config)
        _write_state(kg, _empty_state())
        logger.info(f"[Curiosity] pending_inquiry 超时失效（目标={target}，兴趣保留）")
        return {"settled": False, "dismissed": True, "reason": "timeout",
                "question": question, "target": target}

    # ── 相关性判定（确定性，零 LLM；宁可错当回答，不可错杀教学）──
    # 三级判据：
    #   1) 目标名出现在用户文本或解析节点里（最可靠）
    #   2) 触发信号里的未知名出现在文本里（"SAO啊，那是…"变体）
    #   3) 解释性谓词兜底：回答常以"是/就是/指的是/叫"开头但不提目标名
    #      （"是一部动漫"）——发育期脚手架：低门槛偏向前者，因为错杀一次
    #      真回答的代价（教学丢失）高于错收一句寒暄（有抽取守卫兜底）。
    parsed = [str(n) for n in (parsed_nodes or [])]
    text = str(user_text or "")
    relevant = False
    if target:
        if target in parsed or target in text:
            relevant = True
        else:
            # 子串双向匹配（短目标如 "SAO" 也适用；≥2 字防误吞单字）
            if len(target) >= 2:
                for p in parsed:
                    if (len(p) >= 2
                            and (target in p or p in target)):
                        relevant = True
                        break
    if not relevant:
        for un in unknown_nodes:
            if un and len(un) >= 2 and un in text:
                relevant = True
                break
    if not relevant and re.search(
            r"(是|就是|是指|指的是|叫|叫做|是一种|算是一种)", text.strip()) \
            and len(text.strip()) >= 2:
        relevant = True
        logger.debug("[Curiosity] 解释性谓词命中，视为回答（发育期脚手架）")

    if not relevant:
        if target_node:
            note_interest(kg, target_node, "ignored", config)
        _write_state(kg, _empty_state())
        logger.info(
            f"[Curiosity] 用户没有回答探索提问（目标={target}）→ 忽略并过期，兴趣保留")
        return {"settled": False, "dismissed": True, "reason": "not_relevant",
                "question": question, "target": target}

    # ── 视为回答：激活记忆更新链路 + 兴趣衰减（不清零）──
    wait_node = kg.get_node("等待回答")
    if wait_node:
        wait_node.activation = max(0.0, wait_node.activation)

    for nid, boost in (("更新记忆", 2.0), ("知识完善", 2.0),
                       ("结束好奇状态", 2.0), ("回答完成", 2.5)):
        node = kg.get_node(nid)
        if node:
            node.activation = min(5.0, node.activation + boost)
            node.touch()
            if engine:
                engine.mark_active([node.id])

    if target_node:
        note_interest(kg, target_node, "resolved", config)

    _write_state(kg, _empty_state())
    logger.info(f"[Curiosity] 探索提问被回答（目标={target}），兴趣衰减保留")

    return {
        "settled": True,
        "resolved": True,          # 兼容旧消费方（_auto_consolidate 等）
        "dismissed": False,
        "question": question,
        "answer": user_text,
        "target": target,
        "unknown_nodes": unknown_nodes,
        "unknown_relations": unknown_relations,
        "trigger_type": trigger_type,
    }
