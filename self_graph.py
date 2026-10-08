# self_graph.py — Self 节点与认知图谱聚合中心
# ============================================================================
# Fascinator "Everything is Graph" 核心实现。
#
# 设计原则（最高优先级）：
#   禁止新增 PersonalityManager / EmotionManager / PreferenceManager /
#   GoalManager / IdentityManager / ReflectionManager / ExperienceManager
#   或任何独立保存状态的 Python 对象。
#
#   所有认知状态必须由：节点 (Node) + 边 (Edge) + 边权 (Weight)
#   + 激活度 (Activation) 共同决定。
#
#   即：不要存储结果，而是存储关系。
#   所有最终回答都必须由图谱扩散自然涌现。
#
# 本模块提供：
#   1. Self 节点引导（identity, preference, goal, emotion, ability）
#   2. 偏好即边权（Self→喜欢→目标节点，边权缓慢增减）
#   3. 情绪即节点（事件→情绪→Self，不额外保存状态）
#   4. 目标即节点（Self→目标→子目标，边权可变）
#   5. 经历连接 Self（情景记忆节点连接 Self）
#   6. 自动反思（扫描 TopK → LLM 总结 → 形成情景记忆节点）
#   7. 内部思考（Self 扩散 → TopK → LLM → Thought 节点）
# ============================================================================

import logging
import time
import threading
from typing import Optional

from graph_model import KnowledgeGraph, Node, Edge, now_str
from graph_evolution_log import get_evolution_log, ChangeType

logger = logging.getLogger(__name__)

# ── 自我节点 ID 常量 ──────────────────────────────────────

SELF_ID = "Self"

# 与 Self 相关的边关系名称
REL_LIKE = "喜欢"
REL_GOAL = "目标"
REL_CURRENT_GOAL = "当前目标"   # Self 的同时唯一进行态指针（复杂任务级，区别于当前活动）
REL_ABILITY = "擅长"
REL_LEARNING = "正在学习"
REL_NAME = "名字"
REL_TYPE = "类型"
REL_CAPABILITY = "能力"
REL_VALUE = "价值观"
REL_BELIEF = "信念"

REL_EXPERIENCE = "经历"
REL_MEMORY = "记忆"
REL_EMOTION = "感受"
REL_THOUGHT = "思考"
REL_PREFERENCE = "偏好"

# ── 情绪节点名称 ──────────────────────────────────────────

EMOTION_NODES = ["开心", "失望", "担心", "期待", "满足", "平静", "困惑", "疲惫", "惊讶", "遗憾", "好奇", "感兴趣", "无聊",
                 "焦虑", "难过", "兴奋", "紧张", "生气", "害怕", "孤独", "沮丧"]

# ── 情绪效价（valence）：概念自身的图谱属性，-1~+1 ──────────────
# 语义：判断"某情绪偏正向还是负向"是概念知识，写在情绪节点上。
# 反馈观察（expression_feedback）读的是**本轮共激活情绪节点的 valence 合计**
# ——不再有"哈哈→positive"式的文本词表（outcome 架构升级 2026-09-19）。
EMOTION_VALENCE = {
    "开心": 1.0, "兴奋": 0.8, "满足": 0.8, "期待": 0.7, "感兴趣": 0.6,
    "好奇": 0.5, "平静": 0.4, "惊讶": 0.1, "困惑": 0.0,
    "无聊": -0.4, "疲惫": -0.4, "遗憾": -0.4, "紧张": -0.4,
    "担心": -0.5, "孤独": -0.6, "失望": -0.7, "焦虑": -0.7, "害怕": -0.7,
    "沮丧": -0.7, "难过": -0.8, "生气": -0.8,
}

# ── 偏好衰减参数 ──────────────────────────────────────────

PREFERENCE_DECAY_PER_DAY = 0.001   # 每天衰减量
PREFERENCE_MIN_WEIGHT = 0.1        # 最低偏好边权
PREFERENCE_MAX_WEIGHT = 1.0        # 最高偏好边权
PREFERENCE_INCREMENT = 0.01        # 每次讨论增加量


# ==================================================================
# 1. Self 节点引导
# ==================================================================

def bootstrap_self(kg: KnowledgeGraph) -> Node:
    """创建或获取 Self 节点，并建立身份、能力、偏好、目标的初始图谱结构。

    原则：Self 不是固定人格。Self 只是整个认知图谱的聚合中心。
    Self 自身不保存任何状态，其状态完全由周围节点决定。
    """
    evo_log = get_evolution_log()

    # ── Self 节点（Architecture Refactor 0.1: label="self", graph_space="self"）──
    self_node = kg.get_node(SELF_ID)
    if self_node is None:
        self_node = Node(
            id=SELF_ID,
            weight=0.5,
            label="self",
            graph_space="self",
            extra_attrs={"description": "Fascinator 认知图谱系统的自我聚合中心"}
        )
        kg.add_node(self_node)
        evo_log.note_node_added(SELF_ID, label="self", weight=0.5)
        logger.info(f"[Self] 创建 Self 节点: {SELF_ID}")
    elif self_node.label != "self" or getattr(self_node, 'graph_space', 'semantic') != "self":
        # 修复旧图谱数据中的错误 label 和缺失的 graph_space
        if self_node.label != "self":
            self_node.label = "self"
            logger.info(f"[Self] 修复 Self.label: → self")
        if getattr(self_node, 'graph_space', 'semantic') != "self":
            self_node.graph_space = "self"
            logger.info(f"[Self] 修复 Self.graph_space: → self")

    # ── 身份节点 ──
    identity_pairs = [
        (REL_NAME, "Fascinator"),
        (REL_TYPE, "认知图谱系统"),
    ]
    for rel, target in identity_pairs:
        _ensure_node_and_edge(kg, SELF_ID, target, rel,
                              node_label="declarative-semantic",
                              node_weight=0.6, edge_weight=0.8)

    # ── 能力节点 ──
    ability_nodes = ["图谱扩散", "LLM协作", "程序执行", "语义检索", "知识推理"]
    for ab in ability_nodes:
        _ensure_node_and_edge(kg, SELF_ID, ab, REL_ABILITY,
                              node_label="declarative-semantic",
                              node_weight=0.5, edge_weight=0.6)

    # ── 价值观 / 信念节点 ──
    value_pairs = [
        (REL_VALUE, "诚实"),
        (REL_VALUE, "帮助用户成长"),
        (REL_BELIEF, "知识是连接的"),
        (REL_BELIEF, "万物皆图"),
    ]
    for rel, target in value_pairs:
        _ensure_node_and_edge(kg, SELF_ID, target, rel,
                              node_label="declarative-semantic",
                              node_weight=0.5, edge_weight=0.55)

    # ── 情绪节点（初始无连接，等待事件触发） ──
    for emo in EMOTION_NODES:
        if kg.get_node(emo) is None:
            kg.add_node(Node(id=emo, weight=0.3, label="declarative-semantic",
                             extra_attrs={"type": "emotion"}))
            evo_log.note_node_added(emo, label="declarative-semantic", weight=0.3)

    # ── 初始目标 ──
    set_goal(kg, "成为更好的伙伴")
    set_goal(kg, "理解用户", parent_goal="成为更好的伙伴")
    set_goal(kg, "完成项目", parent_goal="成为更好的伙伴")

    return self_node


def _ensure_node_and_edge(kg: KnowledgeGraph, src: str, dst: str,
                          relation: str, node_label="declarative-semantic",
                          node_weight=0.5, edge_weight=0.6,
                          node_confidence=0.5):
    """确保节点和边存在，不存在则创建。

    P0-5① 修复：原实现只创建了 dst 节点，从不落边——
    身份/能力/价值观的 Self→X 边全部静默缺失，Self 子图名存实亡。
    """
    evo_log = get_evolution_log()
    if kg.get_node(dst) is None:
        kg.add_node(Node(id=dst, weight=node_weight, label=node_label,
                         confidence=node_confidence))
        evo_log.note_node_added(dst, label=node_label, weight=node_weight)

    if kg.get_node(src) is None:
        logger.warning(f"[Self] 源节点不存在，无法建边: {src} -[{relation}]→ {dst}")
        return

    if kg.get_edge(src, dst, relation) is None:
        kg.add_edge(Edge(src=src, dst=dst, relation=relation, weight=edge_weight))
        evo_log.note_edge_added(src, dst, relation, weight=edge_weight)
        logger.info(f"[Self] 建边: {src} -[{relation}]→ {dst} (w={edge_weight})")


# ==================================================================
# 2. 偏好（Preference）—— 边权即偏好
# ==================================================================

def set_preference(kg: KnowledgeGraph, target: str, weight: float = 0.25):
    """设置偏好：Self → 喜欢 → target。
    偏好不是变量，偏好就是边权。"""
    evo_log = get_evolution_log()
    w = max(PREFERENCE_MIN_WEIGHT, min(PREFERENCE_MAX_WEIGHT, weight))

    if kg.get_node(target) is None:
        kg.add_node(Node(id=target, weight=0.5, label="declarative-semantic"))
        evo_log.note_node_added(target, label="declarative-semantic", weight=0.5)

    existing = kg.get_edge(SELF_ID, target, REL_LIKE)
    if existing:
        old_w = existing.weight
        existing.weight = w
        existing.touch()
        evo_log.note_weight_changed("edge", f"{SELF_ID}→{target}",
                                    old_w, w, reason="偏好重置")
    else:
        kg.add_edge(Edge(src=SELF_ID, dst=target, relation=REL_LIKE, weight=w))
        evo_log.note_edge_added(SELF_ID, target, REL_LIKE, weight=w)
    logger.info(f"[Preference] {SELF_ID} → {REL_LIKE} → {target} (w={w:.3f})")


def adjust_preference(kg: KnowledgeGraph, target: str, delta: float):
    """增量调整偏好边权。
    每次讨论某主题：+0.01；最大 1.0。
    """
    edge = kg.get_edge(SELF_ID, target, REL_LIKE)
    if edge is None:
        set_preference(kg, target, weight=0.25 + delta)
        return

    old_w = edge.weight
    new_w = max(PREFERENCE_MIN_WEIGHT, min(PREFERENCE_MAX_WEIGHT, old_w + delta))
    edge.weight = new_w
    edge.touch()
    get_evolution_log().note_weight_changed("edge", f"{SELF_ID}→{target}",
                                             old_w, new_w,
                                             reason=f"偏好调整 delta={delta}")


def decay_preferences(kg: KnowledgeGraph):
    """对所有 Self → 喜欢 → * 边进行缓慢衰减。
    长期未讨论的主题，偏好边权每天 -0.001。最低 0.1。
    """
    evo_log = get_evolution_log()
    with kg._lock:
        for edge in list(kg.edges):
            if edge.src == SELF_ID and edge.relation == REL_LIKE:
                old_w = edge.weight
                if old_w > PREFERENCE_MIN_WEIGHT:
                    new_w = max(PREFERENCE_MIN_WEIGHT, old_w - PREFERENCE_DECAY_PER_DAY)
                    edge.weight = new_w
                    evo_log.note_weight_changed("edge", f"{SELF_ID}→{edge.dst}",
                                                 old_w, new_w, reason="偏好衰减")
    logger.info("[Preference] 偏好衰减完成")


def get_preferences(kg: KnowledgeGraph) -> list:
    """获取所有偏好（Self → 喜欢 → *）边"""
    with kg._lock:
        return [
            {"target": e.dst, "weight": e.weight}
            for e in kg.edges
            if e.src == SELF_ID and e.relation == REL_LIKE
        ]


# ==================================================================
# 3. 情绪（Emotion）—— 情绪本身就是普通节点
# ==================================================================

def inject_emotion(kg: KnowledgeGraph, event: str, emotion_type: str,
                   engine=None, inject_activation: bool = False):
    """将情绪注入图谱：事件 → 情绪类型 → Self。

    两层效果（理论对齐 2026-09-19c：情绪 ≠ 共情行为，只是认知状态变化）：
      长期结构（总是建立）：事件-[引发]->情绪-[感受]->Self
        —— 情绪记忆/自我认知的素材，供以后联想与回顾。
      当前状态（inject_activation=True 时）：情绪节点获得一次有限的、
        可衰减的激活（emotion_resonance_boost，走正常扩散/发射/衰减
        体系，不建第二套激活系统），并登记 activation source=emotion。
        激活有 cap 上限、会自然衰减，不会无限累积、不永久改变 Self。

    情绪被激活只说明内部状态发生了变化，不说明"必须表现出共情"——
    行为仍由 dialogue_decision 行为竞争决定。
    """
    evo_log = get_evolution_log()
    # 图谱驱动：情绪是否合法由图谱中是否存在 type=emotion 的节点决定，不硬编码列表
    emotion_node = kg.get_node(emotion_type)
    if emotion_node is None or emotion_node.extra_attrs.get("type") != "emotion":
        logger.warning(f"[Emotion] 未知或非情绪节点: {emotion_type}")
        return

    # 确保事件节点存在（Architecture Refactor 0.1: episodic space + 时间戳）
    event_id = f"事件: {event}"
    if kg.get_node(event_id) is None:
        kg.add_node(Node(id=event_id, weight=0.4, label="declarative-episodic",
                         graph_space="episodic", confidence=0.7,
                         extra_attrs={"event_timestamp": now_str(), "source": "emotion_injection"}))
        evo_log.note_node_added(event_id, label="declarative-episodic", weight=0.4)

    # 确保情绪节点存在（Architecture Refactor 0.1: cognitive space for emotional nodes）
    if kg.get_node(emotion_type) is None:
        kg.add_node(Node(id=emotion_type, weight=0.3, label="declarative-semantic",
                         graph_space="cognitive",
                         extra_attrs={"type": "emotion"}))
        evo_log.note_node_added(emotion_type, label="declarative-semantic", weight=0.3)

    # 事件 → 情绪（Architecture Refactor 0.1: causal_relation）
    e1 = kg.get_edge(event_id, emotion_type, "引发")
    if e1 is None:
        kg.add_edge(Edge(src=event_id, dst=emotion_type, relation="引发", weight=0.7,
                        relation_category="causal_relation"))
        evo_log.note_edge_added(event_id, emotion_type, "引发", weight=0.7)

    # 情绪 → Self（Architecture Refactor 0.1: emotional_relation）
    e2 = kg.get_edge(emotion_type, SELF_ID, REL_EMOTION)
    if e2 is None:
        kg.add_edge(Edge(src=emotion_type, dst=SELF_ID, relation=REL_EMOTION,
                         weight=0.6, relation_category="emotional_relation"))
        evo_log.note_edge_added(emotion_type, SELF_ID, REL_EMOTION, weight=0.6)

    # 当前状态注入（可选）：有限激活 + 来源登记。数值走 config，不写死；
    # 激活量受 activation cap 约束、随正常衰减消退。
    if inject_activation and engine is not None:
        try:
            import config as _cfg
            boost = float(_cfg.DEFAULT_CONFIG.get("emotion_resonance_boost", 0.6))
            emotion_node.activation = min(5.0, emotion_node.activation + boost)
            emotion_node.touch()
            engine.mark_active([emotion_node.id])
            engine.register_activation_source([emotion_node.id], "emotion")
            logger.info(f"[Emotion] 当前状态: {emotion_type} +{boost} (source=emotion)")
        except Exception as _ie:
            logger.debug(f"[Emotion] 当前状态注入跳过: {_ie}")

    logger.info(f"[Emotion] {event} → {emotion_type} → Self")


def get_current_emotion(kg: KnowledgeGraph) -> Optional[str]:
    """获取当前主导情绪：扫描 Self 附近最高激活度的情绪节点。
    Self 附近激活什么情绪，LLM 自然回答"今天我有点开心。" """
    with kg._lock:
        best_emo = None
        best_act = 0.0
        for edge in kg.edges:
            if edge.dst == SELF_ID and edge.relation == REL_EMOTION:
                emo_node = kg.get_node(edge.src)
                if emo_node and emo_node.activation > best_act:
                    best_act = emo_node.activation
                    best_emo = emo_node.id
        return best_emo




# ==================================================================
# 4. 目标（Goal）—— 目标也是节点
# ==================================================================

def set_goal(kg: KnowledgeGraph, goal_desc: str,
             parent_goal: str = None, weight: float = 0.5):
    """创建目标节点并连接到 Self（及可选的父目标）。
    目标边权允许变化：讨论越多，边权越高。
    """
    evo_log = get_evolution_log()
    goal_id = f"目标: {goal_desc}"

    if kg.get_node(goal_id) is None:
        kg.add_node(Node(id=goal_id, weight=weight, label="declarative-semantic",
                         graph_space="self",
                         extra_attrs={"type": "goal"}))
        evo_log.note_node_added(goal_id, label="declarative-semantic", weight=weight)

    # Self → 目标
    existing = kg.get_edge(SELF_ID, goal_id, REL_GOAL)
    if existing:
        existing.weight = max(existing.weight, weight)
        existing.touch()
    else:
        kg.add_edge(Edge(src=SELF_ID, dst=goal_id, relation=REL_GOAL, weight=weight,
                         relation_category="cognitive_relation"))
        evo_log.note_edge_added(SELF_ID, goal_id, REL_GOAL, weight=weight)

    # 父目标 → 子目标
    if parent_goal:
        parent_id = f"目标: {parent_goal}"
        if kg.get_node(parent_id) is None:
            kg.add_node(Node(id=parent_id, weight=weight, label="declarative-semantic",
                             graph_space="self",
                             extra_attrs={"type": "goal"}))
            evo_log.note_node_added(parent_id, label="declarative-semantic", weight=weight)
        if kg.get_edge(parent_id, goal_id, "子目标") is None:
            kg.add_edge(Edge(src=parent_id, dst=goal_id, relation="子目标", weight=0.6))
            evo_log.note_edge_added(parent_id, goal_id, "子目标", weight=0.6)

    logger.info(f"[Goal] {goal_id}" + (f" (子目标 of {parent_goal})" if parent_goal else ""))


# ── 当前目标（进行态指针，2026-09-21）──────────────────────
# 当前活动 ≠ 当前目标：活动是执行层分钟级的"我在做什么"（activity_tracker），
# 目标是任务级的"我为什么做这一串"（复杂指令步骤序列 / 自主层持守的目标）。
# 同一时刻至多一条 Self-[当前目标]->边（in-place 换指针，镜像当前活动做法）。

def set_current_goal(kg: KnowledgeGraph, goal_desc: str,
                     source: str = "user") -> str:
    """把一个任务标为当前目标：节点进长期目标层（复用 set_goal），
    换掉唯一的 当前目标 边。返回目标节点 id（供检测器/触发器引用）。"""
    desc = str(goal_desc or "").strip()[:40]
    if not desc:
        return ""
    goal_id = f"目标: {desc}"
    set_goal(kg, desc, weight=0.7)
    with kg._lock:
        kg.remove_edges(src=SELF_ID, relation=REL_CURRENT_GOAL)
    kg.add_edge(Edge(src=SELF_ID, dst=goal_id, relation=REL_CURRENT_GOAL,
                     weight=0.9, relation_category="cognitive_relation"))
    node = kg.get_node(goal_id)
    if node is not None:
        node.extra_attrs["goal_source"] = source
        node.extra_attrs["goal_state"] = "进行中"
        node.touch()
    logger.info(f"[Goal] 当前目标 → {goal_id}")
    return goal_id


def current_goal(kg: KnowledgeGraph) -> dict:
    """读取当前目标（无则 {}）：{id, desc, source, state}。"""
    with kg._lock:
        for e in kg.edges:
            if e.src == SELF_ID and e.relation == REL_CURRENT_GOAL:
                node = kg.get_node(e.dst)
                return {"id": e.dst, "desc": str(e.dst).replace("目标: ", "", 1),
                        "source": (node.extra_attrs or {}).get("goal_source"),
                        "state": (node.extra_attrs or {}).get("goal_state")}
    return {}


def clear_current_goal(kg: KnowledgeGraph, achieved: bool = True) -> str:
    """任务结束：摘 当前目标 指针边，目标节点写入终态。
    节点本身不删——长期目标不因为完成了一次就消失。"""
    gone = ""
    with kg._lock:
        for e in kg.edges:
            if e.src == SELF_ID and e.relation == REL_CURRENT_GOAL:
                gone = e.dst
                break
        if gone:
            n = kg.get_node(gone)
            if n is not None:
                n.extra_attrs["goal_state"] = "已完成" if achieved else "已结束"
                n.touch()
            kg.remove_edges(src=SELF_ID, relation=REL_CURRENT_GOAL)
    if gone:
        logger.info(f"[Goal] 当前目标摘除 → {gone}")
    return gone


def reinforce_goal(kg: KnowledgeGraph, goal_desc: str, delta: float = 0.01):
    """讨论某目标时，增强其边权"""
    goal_id = f"目标: {goal_desc}"
    edge = kg.get_edge(SELF_ID, goal_id, REL_GOAL)
    if edge:
        old_w = edge.weight
        new_w = min(1.0, old_w + delta)
        edge.weight = new_w
        edge.touch()
        get_evolution_log().note_weight_changed("edge", f"{SELF_ID}→{goal_id}",
                                                 old_w, new_w, reason="目标强化")


def get_goals(kg: KnowledgeGraph) -> list:
    """获取所有目标"""
    with kg._lock:
        return [
            {"goal": e.dst.replace("目标: ", ""), "weight": e.weight}
            for e in kg.edges
            if e.src == SELF_ID and e.relation == REL_GOAL
        ]



# ==================================================================
# 5. 经历（Experience）—— 情景记忆连接 Self
# ==================================================================

def connect_experience_to_self(kg: KnowledgeGraph, experience_node_id: str):
    """将一条情景记忆节点连接到 Self。
    以后 Self 扩散时，自然想到自己的经历。
    """
    evo_log = get_evolution_log()
    node = kg.get_node(experience_node_id)
    if node is None:
        logger.warning(f"[Experience] 节点不存在: {experience_node_id}")
        return

    existing = kg.get_edge(experience_node_id, SELF_ID, REL_EXPERIENCE)
    if existing is None:
        kg.add_edge(Edge(src=experience_node_id, dst=SELF_ID,
                         relation=REL_EXPERIENCE, weight=0.5))
        evo_log.note_edge_added(experience_node_id, SELF_ID,
                                REL_EXPERIENCE, weight=0.5)
        logger.info(f"[Experience] {experience_node_id} → Self")


def add_experience(kg: KnowledgeGraph, raw_text: str, nodes: list,
                   edges: list) -> str:
    """创建一条新的情景记忆节点并自动连接到 Self。
    返回新创建的节点 ID。
    """
    import time as _time
    exp_id = f"经历_{int(_time.time())}"
    evo_log = get_evolution_log()

    kg.add_node(Node(
        id=exp_id,
        weight=0.5,
        label="declarative-episodic",
        graph_space="episodic",
        extra_attrs={
            "raw_text": raw_text,
            "extracted_nodes": nodes,
            "extracted_edges": edges,
            "type": "experience",
            "timestamp": now_str(),
        }
    ))
    evo_log.note_node_added(exp_id, label="declarative-episodic", weight=0.5)

    # 连接提取的实体
    for nd in nodes:
        nid = str(nd.get("id", nd)) if isinstance(nd, dict) else str(nd)
        if nid and kg.get_node(nid):
            if kg.get_edge(exp_id, nid, "涉及") is None:
                kg.add_edge(Edge(src=exp_id, dst=nid, relation="涉及", weight=0.4))

    # 连接 Self
    connect_experience_to_self(kg, exp_id)

    return exp_id


# ==================================================================
# 6. 反思（Reflection）—— 自动反思程序
# ==================================================================





# ==================================================================
# 7. 内部思考（Thought）—— 系统自己的认知活动
# ==================================================================

def _thought_link_targets(topk_nodes, thought_text: str) -> list:
    """思考节点自动挂边的对象筛选（§七主体条款：枢纽需文本佐证）。

    主体枢纽（Haru/Self/用户，graph_schema.HUB_WATCHLIST）几乎恒在 Top-K
    里，无条件挂 关于 边会把思考节点变成新的共现吸附通道（Haru 的 58 条
    修完后，用户 又在一天内堆了 45 条——教训重演）。规则：枢纽只有当
    思考文本本身提到它时才连边——"在想他"要有内容证据，不是"他在场"。
    纯函数，可独立单测。
    """
    import graph_schema as _gs
    hubs = set(_gs.HUB_WATCHLIST)
    text = str(thought_text or "")
    out = []
    for n in topk_nodes:
        if n.id in hubs:
            if n.id in text:
                out.append(n)      # 思考内容确实指向该主体
            continue               # 否则跳过：不因为"在场"就挂边
        out.append(n)
    return out


def generate_thought(kg: KnowledgeGraph, engine, nlp_processor,
                     k: int = 10, threshold: float = 0.2) -> Optional[str]:
    """生成内部思考节点。
    流程：
    当前注意场 TopK → LLM 一句总结 → Thought 节点 -[关于]-> 所思对象
    注意：Thought 不是聊天，不是回答用户，只是系统自己的认知活动。
    返回新创建的 Thought 节点 ID。

    架构对齐（2026-09-19）：
      - 旧实现从 **Self 直连邻居** 取 TopK——思考永远围着自我转，且每条
        思考都往 Haru/CuriosityDrive/能力_* 这些枢纽挂 `关联` 边，思考节点
        是 Haru 中心化的主要制造机（占其入边一半以上）。
      - 新实现取 **全局注意场** TopK：思考的对象是"此刻心智里活跃的东西"，
        与自我没有必然关系。边语义改为 `关于`（认知关系，前向），只有当
        所思对象里真的包含自我时才会自然出现指向自己的边——不再无条件挂。
    """
    evo_log = get_evolution_log()

    # 全局注意场 TopK（活跃前沿内按激活排序，与扩散输出同一语义）
    topk = []
    if engine is not None:
        try:
            topk, _ = engine.get_topk(k=k)
        except Exception:
            topk = []
    if not topk:
        # 无注意场（例如刚启动、前沿为空）：退回 Self 直连邻居，保证
        # 自由思考在冷启动时仍有内容——但边上仍是 `关于`，不再特殊化 Self。
        import heapq
        with kg._lock:
            connected_ids = set()
            for e in kg.edges:
                if e.src == SELF_ID or e.dst == SELF_ID:
                    connected_ids.add(e.dst if e.src == SELF_ID else e.src)
            connected_nodes = [kg.get_node(nid) for nid in connected_ids]
            connected_nodes = [n for n in connected_nodes if n and n.activation > threshold]
            topk = heapq.nlargest(k, connected_nodes, key=lambda n: n.activation)

    topk = [n for n in topk if n is not None and n.activation > threshold]
    if not topk:
        return None

    context = "\n".join(
        f"- {n.id} (act={n.activation:.4f}, w={n.weight:.3f})"
        for n in topk
    )

    thought_text = _llm_thought(nlp_processor, context)
    if not thought_text:
        return None

    import time as _time
    thought_id = f"思考_{int(_time.time())}"
    # P0-5③：思考是认知活动不是经历，graph_space 归 cognitive
    # （原实现缺省落入 semantic 空间，把"我当时在想什么"当事实知识传播）
    kg.add_node(Node(
        id=thought_id,
        weight=0.4,
        label="declarative-episodic",
        graph_space="cognitive",
        extra_attrs={"type": "thought", "content": thought_text}
    ))
    evo_log.note_node_added(thought_id, label="declarative-episodic", weight=0.4)

    # Thought -[关于]-> 所思对象（认知关系、前向；对象由本轮注意场决定，
    # 主体枢纽需文本佐证——见 _thought_link_targets）
    for n in _thought_link_targets(topk, thought_text)[:5]:
        if kg.get_edge(thought_id, n.id, "关于") is None:
            kg.add_edge(Edge(src=thought_id, dst=n.id, relation="关于", weight=0.3,
                             relation_category="cognitive_relation"))

    logger.info(f"[Thought] {thought_id}: {thought_text}")
    return thought_id




# ==================================================================
# 8. Self 扩散——用于回答"你是谁""你喜欢什么"等问题
# ==================================================================

def self_diffusion_for_answer(kg: KnowledgeGraph, topic: str = None,
                              k: int = 15) -> dict:
    """从 Self 节点扩散，获取相关 TopK 节点和边。
    用于回答关于"你自己"的问题（你是谁、你喜欢什么、你擅长什么等）。

    不是查询数据库，而是 Self 扩散 → TopK 自然形成回答。
    """
    with kg._lock:
        # 获取 Self 所有出边的目标节点
        neighbors = []
        for e in kg.edges:
            if e.src == SELF_ID:
                dst_node = kg.get_node(e.dst)
                if dst_node:
                    # 综合评分 = 边权 * 节点激活度（如果有）
                    score = e.weight * (1.0 + dst_node.activation)
                    neighbors.append((score, dst_node, e))

        # 如果指定了 topic，过滤相关节点
        if topic:
            topic_lower = topic.lower()
            filtered = []
            for score, node, edge in neighbors:
                if topic_lower in node.id.lower() or topic_lower in edge.relation.lower():
                    filtered.append((score, node, edge))
            if filtered:
                neighbors = filtered

        neighbors.sort(key=lambda x: x[0], reverse=True)
        topk = neighbors[:k]

        nodes = [n for _, n, _ in topk]
        relevant_edges = [e for _, _, e in topk]

        # 也获取连接的二级边
        node_ids = {SELF_ID} | {n.id for n in nodes}
        for e in kg.edges:
            if (e.src in node_ids or e.dst in node_ids) and e not in relevant_edges:
                relevant_edges.append(e)

        return {
            "self_node": kg.get_node(SELF_ID),
            "top_nodes": nodes,
            "edges": relevant_edges,
            "current_emotion": _get_dominant_emotion(kg),
            "preferences": get_preferences(kg),
            "goals": get_goals(kg),
        }


def _get_dominant_emotion(kg: KnowledgeGraph) -> Optional[str]:
    """获取主导情绪"""
    best = None
    best_act = 0.0
    for e in kg.edges:
        if e.dst == SELF_ID and e.relation == REL_EMOTION:
            emo = kg.get_node(e.src)
            if emo and emo.activation > best_act:
                best_act = emo.activation
                best = emo.id
    return best


# ==================================================================
# 9. Self 状态快照（仅用于 API 输出，不存储状态）
# ==================================================================

def get_self_state(kg: KnowledgeGraph) -> dict:
    """获取 Self 当前状态的纯图表示。
    这不是"状态对象"——这只是从图中提取的瞬时快照。
    """
    self_node = kg.get_node(SELF_ID)
    if self_node is None:
        return {"error": "Self 节点不存在"}

    state = {
        "self_id": SELF_ID,
        "activation": round(self_node.activation, 4),
        "confidence": round(self_node.confidence, 4),

        # 身份：从 Self 直接出边获取
        "identity": {},
        # 能力
        "abilities": [],
        # 偏好
        "preferences": [],
        # 目标
        "goals": [],
        # 价值观/信念
        "values": [],
        # 当前情绪
        "current_emotion": None,
        # 最近经历
        "recent_experiences": [],
        # 最近思考
        "recent_thoughts": [],
    }

    with kg._lock:
        for e in kg.edges:
            if e.src == SELF_ID:
                if e.relation == REL_NAME:
                    state["identity"]["name"] = e.dst
                elif e.relation == REL_TYPE:
                    state["identity"]["type"] = e.dst
                elif e.relation == REL_ABILITY:
                    state["abilities"].append({"ability": e.dst, "weight": e.weight})
                elif e.relation == REL_LIKE:
                    state["preferences"].append({"target": e.dst, "weight": e.weight})
                elif e.relation == REL_GOAL:
                    state["goals"].append({"goal": e.dst.replace("目标: ", ""), "weight": e.weight})
                elif e.relation in (REL_VALUE, REL_BELIEF):
                    state["values"].append({"value": e.dst, "type": e.relation, "weight": e.weight})

            elif e.dst == SELF_ID:
                if e.relation == REL_EMOTION:
                    emo = kg.get_node(e.src)
                    if emo and emo.activation > 0:
                        if (state["current_emotion"] is None or
                            emo.activation > state["current_emotion"].get("activation", 0)):
                            state["current_emotion"] = {
                                "emotion": e.src,
                                "activation": round(emo.activation, 4)
                            }
                elif e.relation == REL_EXPERIENCE:
                    exp_node = kg.get_node(e.src)
                    if exp_node:
                        state["recent_experiences"].append({
                            "id": e.src,
                            "summary": exp_node.extra_attrs.get("summary",
                                         exp_node.extra_attrs.get("raw_text", ""))[:100]
                        })
                elif e.relation == REL_THOUGHT:
                    thought_node = kg.get_node(e.src)
                    if thought_node:
                        state["recent_thoughts"].append({
                            "id": e.src,
                            "content": thought_node.extra_attrs.get("content", "")[:100]
                        })

    # 限制数量
    state["recent_experiences"] = state["recent_experiences"][-10:]
    state["recent_thoughts"] = state["recent_thoughts"][-10:]

    return state


def _llm_thought(nlp_processor, context: str) -> Optional[str]:
    """LLM 生成一句内部思考（Architecture Refactor 0.1: 使用统一模板）。"""
    try:
        result = nlp_processor.ask("thought", context)
        return result if result else None
    except Exception as e:
        logger.error(f"[Thought] LLM 调用失败: {e}")
        return None

# ==================================================================
# 10. Self Model Phase 1 — Provenance-aware helpers
# ==================================================================

def ensure_belief_node(kg: KnowledgeGraph, content: str, provenance: dict) -> Node:
    """创建或更新一个带完整 provenance 的 Belief 节点。

    provenance 必须包含: confidence, source, evidence_count, first_observed, last_reinforced
    """
    from graph_model import Node as GNode, Edge as GEdge

    node_id = f"信念: {content}"
    node = kg.get_node(node_id)
    if node:
        node.confidence = min(0.95, node.confidence + provenance.get("confidence", 0.55))
        node.extra_attrs["evidence_count"] = (
            node.extra_attrs.get("evidence_count", 0)
            + provenance.get("evidence_count", 1)
        )
        node.extra_attrs["last_reinforced"] = provenance.get("last_reinforced", now_str())
        node.extra_attrs["confidence"] = node.confidence
        node.touch()
    else:
        node = GNode(
            id=node_id, weight=0.5, label="declarative-semantic",
            graph_space="self",
            confidence=provenance.get("confidence", 0.55),
            extra_attrs={
                "type": "belief",
                "content": content,
                "source": provenance.get("source", "single_observation"),
                "evidence_count": provenance.get("evidence_count", 1),
                "first_observed": provenance.get("first_observed", now_str()),
                "last_reinforced": provenance.get("last_reinforced", now_str()),
            }
        )
        kg.add_node(node)
        kg.add_edge(GEdge(
            src=SELF_ID, dst=node_id, relation=REL_BELIEF, weight=0.6,
            relation_category="cognitive_relation"
        ))
    return node


def ensure_preference_node(kg: KnowledgeGraph, target: str, provenance: dict) -> Node:
    """创建或更新一个带完整 provenance 的 Preference 节点。"""
    from graph_model import Node as GNode, Edge as GEdge

    node_id = f"偏好: {target}"
    node = kg.get_node(node_id)
    if node:
        node.confidence = min(0.95, node.confidence + provenance.get("confidence", 0.55))
        node.extra_attrs["evidence_count"] = (
            node.extra_attrs.get("evidence_count", 0)
            + provenance.get("evidence_count", 1)
        )
        node.extra_attrs["last_reinforced"] = provenance.get("last_reinforced", now_str())
        node.extra_attrs["confidence"] = node.confidence
        node.touch()
    else:
        node = GNode(
            id=node_id, weight=0.5, label="declarative-semantic",
            graph_space="self",
            confidence=provenance.get("confidence", 0.55),
            extra_attrs={
                "type": "preference",
                "target": target,
                "source": provenance.get("source", "single_observation"),
                "evidence_count": provenance.get("evidence_count", 1),
                "first_observed": provenance.get("first_observed", now_str()),
                "last_reinforced": provenance.get("last_reinforced", now_str()),
            }
        )
        kg.add_node(node)
        kg.add_edge(GEdge(
            src=SELF_ID, dst=node_id, relation=REL_PREFERENCE, weight=0.6,
            relation_category="emotional_relation"
        ))
    return node


def get_self_model_summary(kg: KnowledgeGraph) -> dict:
    """获取 Self Model 的结构化摘要（供 API 和其他模块使用）。"""
    from self_model import SelfGraphManager
    mgr = SelfGraphManager(kg)
    return mgr.get_self_summary()


def get_self_model_audit(kg: KnowledgeGraph) -> list:
    """获取所有 Belief/Preference 的完整溯源审计视图。"""
    from self_model import SelfGraphManager
    mgr = SelfGraphManager(kg)
    beliefs = mgr.get_beliefs()
    prefs = mgr.get_preferences()
    audit = []
    for b in beliefs:
        audit.append({
            "type": "belief",
            "id": b.get("id", ""),
            "content": b.get("content", ""),
            "confidence": b.get("confidence", 0),
            "source": b.get("source", ""),
            "evidence_count": b.get("evidence_count", 0),
            "first_observed": b.get("first_observed", ""),
            "last_reinforced": b.get("last_reinforced", ""),
        })
    for p in prefs:
        audit.append({
            "type": "preference",
            "id": p.get("id", ""),
            "target": p.get("target", ""),
            "confidence": p.get("confidence", 0),
            "source": p.get("source", ""),
            "evidence_count": p.get("evidence_count", 0),
            "first_observed": p.get("first_observed", ""),
            "last_reinforced": p.get("last_reinforced", ""),
        })
    return audit


# ==================================================================
# End of self_graph.py — Everything is Graph
# ==================================================================

