# graph_schema.py — 图谱 schema 层：关系本体归一 / hub 白名单 / 活动词表
# ============================================================================
# 架构对齐（2026-09-19）新增的单一真源模块。论文设计里"关系有明确语义"的
# 工程落点：LLM 与各子系统产出的关系字符串在写图/查图前必须经过这里归一，
# 图里只允许存在规范原子关系（config.relation_ontology）。
#
# 三个职责：
#   1. 关系归一   normalize_relation / category_for / direction_for
#                 —— config.relation_ontology + relation_synonyms + 兜底
#   2. hub 守卫   check_hub_edge
#                 —— Haru/Self/用户 是主体不是 Hub：直连边必须在白名单内
#   3. 活动词表   ACTIVITY_KINDS / ACTIVITY_STATUSES / activity_kind_for
#                 —— CurrentActivity 的闭集词表（activity_tracker 消费）
#
# 本模块只依赖 config，不依赖 graph_model——graph_model 反过来 lazy import
# 这里，避免循环。
# ============================================================================

import logging
import re

import config

logger = logging.getLogger(__name__)

_FALLBACK_RELATION = config.DEFAULT_CONFIG.get("relation_fallback", "关联")
_RELATION_ONTOLOGY = config.DEFAULT_CONFIG["relation_ontology"]
_RELATION_SYNONYMS = config.DEFAULT_CONFIG.get("relation_synonyms", {})
_RELATION_DIRECTION = config.DEFAULT_CONFIG.get("relation_direction", {})
_RELATION_PROPAGATION = config.DEFAULT_CONFIG.get("relation_propagation", {})
_DEFAULT_CATEGORY = "semantic_relation"

# ── 规范关系查找表（模块级缓存）────────────────────────────────

_canon_relations = frozenset(_RELATION_ONTOLOGY.keys())
_synonym_map = {}
for _surface, _canon in _RELATION_SYNONYMS.items():
    if _canon in _canon_relations:
        _synonym_map[_surface] = _canon
    else:
        # 配置错误防御：同义映射的值必须是规范词，静默丢弃并告警一次
        logger.warning("[graph_schema] relation_synonyms 中 '%s -> %s' 不是规范关系，已忽略",
                       _surface, _canon)


def normalize_relation(relation: str) -> str:
    """把任意关系字符串归一为规范原子关系。

    规范词原样返回；同义表映射；未收录的自由文本兜底为 relation_fallback
    （"关联"，认知共现）。归一是幂等的：normalize(normalize(x)) == normalize(x)。
    """
    rel = str(relation or "").strip()
    if not rel:
        return _FALLBACK_RELATION
    if rel in _canon_relations:
        return rel
    # 同义映射（值必为规范词，见上面构建时的校验）
    mapped = _synonym_map.get(rel)
    if mapped is not None:
        return mapped
    # 英文关系名统一小写后再试一次（LLM 有时输出 Has_Attribute 这类）
    lowered = rel.lower()
    if lowered != rel:
        if lowered in _canon_relations:
            return lowered
        mapped = _synonym_map.get(lowered)
        if mapped is not None:
            return mapped
    return _FALLBACK_RELATION


def is_canonical_relation(relation: str) -> bool:
    """关系字符串是否为规范原子关系（不经同义归一）。"""
    return str(relation or "").strip() in _canon_relations


def category_for(relation: str) -> str:
    """规范关系的 relation_category；未收录返回默认语义类。"""
    return _RELATION_ONTOLOGY.get(str(relation or "").strip(), _DEFAULT_CATEGORY)


def direction_for(relation: str, relation_category: str = None) -> str:
    """关系的传播方向：per-relation 覆盖 > 类别默认 > forward。"""
    rel = str(relation or "").strip()
    override = _RELATION_DIRECTION.get(rel)
    if override:
        return override
    cat = relation_category or category_for(rel)
    rules = _RELATION_PROPAGATION.get(cat)
    if rules and rules.get("direction"):
        return rules["direction"]
    return "forward"


def global_direction_override(relation: str):
    """只查规范语义覆盖表（关于/涉及/参与 等对称关系）。

    供扩散引擎使用：语义覆盖是本体层面的约定，优先级高于引擎实例
    config 里的类别规则——防止一份旧 config 把对称语义改回单向。
    返回 None 表示无覆盖。
    """
    return _RELATION_DIRECTION.get(str(relation or "").strip())


def relation_surface_stats() -> dict:
    """词表规模速览（审计/自检用）。"""
    return {
        "canonical": len(_canon_relations),
        "synonyms": len(_synonym_map),
        "fallback": _FALLBACK_RELATION,
    }


# ── hub 守卫：主体不是 Hub ─────────────────────────────────────

# 这些节点是"主体"（自我/用户）。任何以它们为端点的边，关系必须能回答
# "这是关于主体本身的事实吗？"。世界信息（看见的方块、瞬时的想法共现）
# 一律不允许直连——必须经由 当前状态/活动/事件/思考 等中间结构。
HUB_WATCHLIST = ("Haru", "Self", "用户")

# 允许直接触碰主体的关系白名单（按 hub 分列；值为规范关系名）。
# Haru（具身自我）：身份、当前状态/活动/焦点、能力偏好、经历知识。
HUB_ALLOWED = {
    "Haru": {
        "名字叫", "当前状态", "当前活动", "当前目标", "认知焦点",
        "能", "能力", "擅长", "喜欢", "偏好", "目标",
        "经历", "记得", "知道", "处于", "意图",
    },
    # Self（反思自我）：bootstrap 的身份/能力/价值观/目标 + 情绪感受 + 经历。
    "Self": {
        "名字叫", "名字", "类型", "能", "能力", "擅长", "正在学习",
        "价值观", "信念", "偏好", "喜欢", "目标", "当前目标", "驱动力", "驱动",
        "感受", "经历", "意图", "处于", "用户关系", "相关能力", "实现于",
        # R2 P11（审计 D-7）：`Self-[网络]->CENetwork/DMNetwork` 是**自我模型的
        # 结构事实**（"我的认知网络是哪些"），与 `处于`（Self→调制器/需求）同族，
        # 不是世界信息，所以不需要经由 状态/活动/事件 中间结构。
        # 只加白名单、不改任何权重或扩散量：hub 守卫是 warn-only（写图时提醒），
        # 这条改动的效果是"每次启动少一条假警告"，动力学一字不动。
        "网络",
    },
    # 用户（对话者）：参与事件、偏好、社交、请求。用户是主要对话者，
    # 参与/讲述类 episodic 出边是正当语义，因此白名单比自我宽。
    "用户": {
        "名字叫", "名字", "是", "属于", "具有", "喜欢", "讨厌", "认识",
        "参与", "经历", "讲述", "观察", "请求", "感谢", "问候", "对话",
        "拥有", "使用", "完成", "相关", "目标", "记得", "处于", "导致",
        "说出",   # 言语行为图谱化：用户→本轮话语事件（speech_act_graph）
    },
}

_HUB_WARNED = set()   # (hub, relation) 去重，避免日志刷屏

# 思维/反思记录节点 id 前缀（自动共现边的制造者）
_RECORD_PREFIXES = ("思考_", "反思_")
# 这类记录直连主体枢纽时使用的共现性关系（关于/涉及/关联）——
# 它们不是"关于主体的事实"，是"生成时刻谁恰好在注意场里"。
_RECORD_HUB_RELATIONS = {"关于", "涉及", "关联"}


def repair_hub_pollution(kg) -> list:
    """清理 思考_*/反思_* → 主体枢纽 的共现边（幂等，启动自愈）。

    Haru 治理的推广：generate_thought 改成全局注意场取对象后，用户
    （Top-K 常驻）一天内被堆了 45 条 关于 边——同一个病换了汇点。
    源头已由 self_graph._thought_link_targets 封堵（枢纽需思考文本
    佐证才连边），此处清存量。只删三类共现关系，不动 感受/经历/
    名字叫 等正当枢纽边，也不动记录节点指向普通知识节点的边
    （那是合法的"当时在想什么"）。
    """
    removed = []
    hubs = set(HUB_WATCHLIST)
    for e in list(kg.edges):
        if (e.src.startswith(_RECORD_PREFIXES) and e.dst in hubs
                and normalize_relation(e.relation) in _RECORD_HUB_RELATIONS):
            kg.remove_edge(e.src, e.dst, e.relation)
            removed.append(f"{e.src[:16]}-[{e.relation}]->{e.dst}")
    if removed:
        logger.warning("[graph_schema] 已清理 思维/反思→主体枢纽 共现边 %d 条"
                       "（源头封堵见 self_graph._thought_link_targets）",
                       len(removed))
    return removed


def check_hub_edge(src: str, dst: str, relation: str) -> bool:
    """检查一条以主体为端点的边是否在白名单内。

    返回 True=合法。非法边**不阻止写入**（各写入方自律 + 审计报告兜底），
    但会打一条去重后的 warning——新代码接错线时日志里立刻可见。
    """
    rel = str(relation or "").strip()
    for hub in HUB_WATCHLIST:
        if src == hub or dst == hub:
            allowed = HUB_ALLOWED.get(hub, set())
            if rel not in allowed and (hub, rel) not in _HUB_WARNED:
                _HUB_WARNED.add((hub, rel))
                logger.warning(
                    "[graph_schema] 主体直连边不在白名单: %s -[%s]-> %s "
                    "(hub=%s 允许: %s)——世界信息应经由 状态/活动/事件/思考 中间结构",
                    src, rel, dst, hub, sorted(allowed))
            return rel in allowed
    return True


# ── CurrentActivity 词表（activity_tracker 消费）────────────────

# 活动 = 认知层"我正在做什么"（coarse-grained），动作 = 执行层一次技能调用。
# 多个同族动作归入同一活动；词表闭集，防止活动类型无限增殖。
ACTIVITY_KINDS = ("观察", "探索", "移动", "跟随", "采集", "交流",
                  "制作", "恢复", "撤离", "等待", "行动")

ACTIVITY_STATUSES = ("执行中", "已完成", "失败", "取消", "暂停")

ACTIVITY_HISTORY_CAP = 100   # 已结束活动的历史留存上限（超出删最旧）

# ActionNode.action_type / 技能名 → 活动类型。未命中 → "行动"。
ACTION_TO_ACTIVITY = {
    # 观察
    "observe": "观察", "inspect_area": "观察", "inspect": "观察", "look": "观察",
    "look_at": "观察", "wait_observe": "观察",
    # 探索
    "explore_area": "探索", "explore_direction": "探索", "explore": "探索",
    # 移动
    "approach": "移动", "navigate_to_entity": "移动", "navigate": "移动",
    "goto": "移动", "goto_entity": "移动", "move": "移动", "move_to": "移动",
    # 跟随
    "follow": "跟随", "follow_entity": "跟随",
    # 采集
    "gather_resource": "采集", "collect_dropped_item": "采集", "collect": "采集",
    "gather": "采集", "dig": "采集",
    # 交流
    "communicate": "交流", "say": "交流", "explore_ask": "交流", "chat": "交流",
    # 制作
    "craft": "制作", "smelt": "制作", "craft_item": "制作",
    # 恢复
    "eat_food": "恢复", "collect_food": "恢复", "recover_health": "恢复",
    "eat": "恢复", "rest": "恢复", "sleep": "恢复",
    # 撤离
    "retreat": "撤离", "seek_safety": "撤离", "withdraw": "撤离", "flee": "撤离",
    # 等待
    "stop": "等待", "idle": "等待", "wait": "等待",
}


def activity_kind_for(action_type: str) -> str:
    """动作类型 → 活动类型（闭集）。未映射的落 '行动' 兜底。"""
    key = str(action_type or "").strip().lower()
    if key in ACTION_TO_ACTIVITY:
        return ACTION_TO_ACTIVITY[key]
    for known, kind in ACTION_TO_ACTIVITY.items():
        if key and (key in known or known in key):
            return kind
    return "行动"


# ── 节点原子性（审计与守卫共用）────────────────────────────────

# 事件命名式 X—Y—Z（如 旅游—地点—远方市）——把谓词和槽位烤进节点 id，
# 违反原子性。存量保留（是记忆），增量由 prompt 规则 + _wire_event_structure
# 守卫阻止；本模式供审计与守卫识别。
COMPOUND_NODE_RE = re.compile(r'^[\u4e00-\u9fa5A-Za-z0-9]+—[\u4e00-\u9fa5A-Za-z0-9]+—')

# 纯日期节点（2026-09-19）——时间应为事件属性或固定时段桶，不是枢纽节点。
DATE_NODE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# 句子型节点（超过此长度且含空格/标点的 id 视为可疑，审计报告用）
SUSPICIOUS_ID_LEN = 20
