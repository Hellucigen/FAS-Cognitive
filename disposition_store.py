# disposition_store.py — 行为倾向存储层（Reflection Evolution R2 核心）
# ============================================================================
# 设计原则（Reflection Evolution 设计文档）：
#   1. 不预设人格 — FAS 的表达方式来自经历积累，不来自规则
#   2. 原子化 — 封闭词表（行为/情境节点有限可数），禁止复合人格节点
#   3. Context→Behavior 权重就是边权（情境-[激活]->行为），强化/衰减改边权，
#      不写 if-else
#   4. 一次经历只能产生 candidate；stable 需要 evidence_count>=4 且正反馈优势
#   5. LLM 只能提候选与调整建议，数值由代码按证据计算
#
# 学习闭环修复（2026-09）：
#   - 旧版 turn 级 outcome **只回写已存在的 pair**，而 pair 仅由出厂先验和
#     反思产出创建 → 绝大多数 (情境,行为) 组合的真实反馈被静默丢弃，
#     evidence 恒 0。现在一次真实的 positive/negative 经历**首条即建立
#     candidate**（封闭词表内，数量有界），neutral/ambiguous 不建、只计数。
#   - outcome 从 {positive, negative} 扩为四态 {positive, negative, neutral,
#     ambiguous}；evidence 允许多来源（user_feedback / self_goal_success /
#     self_goal_failure / repeated_action / reflection / environment）——
#     人格不收敛成"讨用户喜欢"，用户反馈只是来源之一。
#   - stable 不会被一次负反馈删除；负证据积累只把它**降级回 candidate**
#     （图权重随之回落，学习不被抹杀也不固化和）。
#   - 全部图存储（pair 节点 extra_attrs），**无平行 JSON 持久层**。
#   - 每一步都有 [Disposition] trace：candidate_text / source / outcome /
#     计数变化 / 强度变化 / promotion/demotion 判定。
#
# 图结构（self 空间）：
#   情境:用户分享 (context)  -[产生]->  倾向:追问@用户分享 (pair)  -[指向]->  行为:追问 (behavior)
#   情境:用户分享 (context)  -[激活 weight=strength]->  行为:追问
#   pair 节点 extra_attrs 存证据计数/正负反馈/证据引用，激活边权重与 strength 同步
# ============================================================================

import logging
import threading
import time
from datetime import datetime

from graph_model import KnowledgeGraph, Node, Edge
from graph_evolution_log import get_evolution_log, ChangeType

logger = logging.getLogger(__name__)

# ── 封闭词表（防节点爆炸：节点总数上界 ~21）─────────────────────
# 行为词表：FAS 的候选行为集合（设计文档 §八）
# explore（探索）为学习闭环修复新增：自主行动的探索类意图（observe/approach/
# explore/collect）在图上的行为落点——自身目标的成功/失败也必须能形成人格
# 证据（来源不只用户反馈）。
BEHAVIORS = {
    "respond":     "行为:回应",
    "ask":         "行为:追问",
    "acknowledge": "行为:确认",
    "elaborate":   "行为:详述",
    "empathize":   "行为:共情",
    "share":       "行为:分享",
    "continue":    "行为:延续",
    "end":         "行为:收尾",
    "silence":     "行为:沉默",
    "explore":     "行为:探索",
}

# ── Outcome 四态与证据来源（§四）──
OUTCOME_POSITIVE = "positive"
OUTCOME_NEGATIVE = "negative"
OUTCOME_NEUTRAL = "neutral"
OUTCOME_AMBIGUOUS = "ambiguous"
VALID_OUTCOMES = (OUTCOME_POSITIVE, OUTCOME_NEGATIVE,
                  OUTCOME_NEUTRAL, OUTCOME_AMBIGUOUS)
# 改变强度的来源（neutral/ambiguous 只计数不动强度）
STRENGTH_OUTCOMES = (OUTCOME_POSITIVE, OUTCOME_NEGATIVE)

EVIDENCE_SOURCES = (
    "user_feedback",        # 用户对 FAS 表达的反应
    "self_goal_success",    # FAS 自身目标达成
    "self_goal_failure",    # FAS 自身目标失败
    "repeated_action",      # 无外部要求的自发重复行为
    "reflection",           # 反思周期归纳
    "environment",          # 环境/世界状态反馈
    "baseline",             # 出厂先验（非经历）
)

# 情境词表：dialogue_act 映射 + 情境修饰符（设计文档 §九 Context）
DIALOGUE_ACT_CONTEXT = {
    "sharing":               "情境:用户分享",
    "question":              "情境:用户提问",
    "emotion_expression":    "情境:用户情绪表达",
    "greeting":              "情境:用户问候",
    "farewell":              "情境:用户道别",
    "thanking":              "情境:用户感谢",
    "request":               "情境:用户请求",
    "information_statement": "情境:用户陈述",
    "opinion":               "情境:用户观点",
    "agreement":             "情境:用户回应",
    "disagreement":          "情境:用户回应",
    "backchannel":           "情境:用户回应",
    "answer":                "情境:用户回应",
    "comfort":               "情境:用户回应",
}
DEFAULT_CONTEXT = "情境:用户陈述"

# 情境修饰符（与主情境并存，共同参与激活）
MODIFIER_EMOTION_LOW = "情境:用户情绪低落"
MODIFIER_FAMILIAR = "情境:熟悉关系"
PROACTIVE_CONTEXT = "情境:主动发起"

# ── 强化/衰减参数（代码规则，非 LLM）──────────────────────────
POS_DELTA = 0.08          # 正 outcome 强化量（用户反馈基准）
NEG_DELTA = 0.12          # 负 outcome 衰减量（用户反馈基准）
LLM_ADJUST_DELTA = 0.05   # 反思 LLM 建议 的轻量调整
NEW_CANDIDATE_STRENGTH = 0.25
CANDIDATE_CAP = 0.30      # candidate 阶段激活封顶（对回答影响极小）
STABLE_MIN_EVIDENCE = 4   # stable 门槛：证据数
STABLE_MIN_RATIO = 3.0    # stable 门槛：正:负 ≥ 3:1
STABLE_MIN_STRENGTH = 0.5
REMOVE_BELOW = 0.15       # candidate 强度过低 → 删除该倾向
#   stable **永不删除**：负证据只会把它降级回 candidate（§七/§十六：
#   经历可以改变 Self，但不能一次抹掉长期形成的人格）
DEMOTE_NEG_RATIO = 0.7    # stable 降级：neg ≥ 0.7×pos 且
DEMOTE_MIN_NEG = 3        #              negative_count ≥ 3
DECAY_FACTOR = 0.97       # 周期衰减系数
EVIDENCE_KEEP = 8         # pair 上保留的证据引用条数上限

# 来源感知的强度步长：用户反馈与"自身目标成败/自发重复"不应对称等价。
# 人格不能只被用户训练——自我来源用更温和的步长，靠**反复**积累。
SOURCE_DELTAS = {
    "user_feedback":     (POS_DELTA, NEG_DELTA),
    "self_goal_success": (0.05, 0.0),
    "self_goal_failure": (0.0, 0.06),
    "repeated_action":   (0.05, 0.0),
    "reflection":        (0.04, 0.06),
    "environment":       (0.04, 0.05),
    "baseline":          (0.0, 0.0),
}


def _now() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


# ── 行为竞争先验（交流决策重构 2026-09）────────────────────────
# 旧 dialogue_decision.ACT_PULL 是"独立决策器体内的固定公式参数"；现在把它
# 变成**图谱初始先验**：情境节点 -[先验 w]-> 行为节点。语义：
#   先验边 = 出厂时"这类对话情境下通常怎么做"的行为学常识种子（含沉默/探索）；
#   激活边 = 经验学习通道（apply_experience 增减，人格在这里长）。
# 竞争时两通道都读：先验提供基线偏置，经验在其上积累或反超（§四：
# 先验→经验→权重改变→人格形成，而不是先验永远固定）。
# 刻意包含 silence：沉默是候选行为之一，不是"没达到说话条件"（§七）。
BEHAVIOR_PRIOR_SEEDS = {
    "情境:用户提问":   {"respond": 0.85, "ask": 0.55, "acknowledge": 0.30,
                        "elaborate": 0.25, "silence": 0.05, "explore": 0.10},
    "情境:用户请求":   {"respond": 0.90, "acknowledge": 0.50, "share": 0.20,
                        "silence": 0.05, "explore": 0.05},
    "情境:用户问候":   {"respond": 0.60, "acknowledge": 0.55, "share": 0.20,
                        "empathize": 0.15, "silence": 0.10, "explore": 0.05},
    "情境:用户分享":   {"respond": 0.50, "acknowledge": 0.40, "share": 0.30,
                        "ask": 0.30, "continue": 0.30, "empathize": 0.25,
                        "elaborate": 0.25, "silence": 0.10, "explore": 0.10},
    "情境:用户情绪表达": {"empathize": 0.60, "respond": 0.50, "acknowledge": 0.35,
                        "share": 0.15, "silence": 0.08, "explore": 0.05},
    "情境:用户观点":   {"respond": 0.45, "share": 0.40, "elaborate": 0.35,
                        "acknowledge": 0.30, "continue": 0.25, "ask": 0.20,
                        "silence": 0.12, "explore": 0.08},
    "情境:用户陈述":   {"respond": 0.40, "acknowledge": 0.35, "elaborate": 0.25,
                        "share": 0.25, "continue": 0.20, "ask": 0.15,
                        "silence": 0.18, "explore": 0.08},
    "情境:用户回应":   {"respond": 0.50, "acknowledge": 0.45, "share": 0.20,
                        "continue": 0.20, "silence": 0.10, "explore": 0.05},
    "情境:用户道别":   {"acknowledge": 0.55, "respond": 0.35, "end": 0.40,
                        "silence": 0.25, "explore": 0.02},
    "情境:用户感谢":   {"acknowledge": 0.55, "respond": 0.45, "empathize": 0.20,
                        "share": 0.15, "silence": 0.10, "explore": 0.02},
    "情境:主动发起":   {"share": 0.30, "ask": 0.30, "explore": 0.30,
                        "respond": 0.20, "continue": 0.20, "silence": 0.20},
}
# 探索行为与好奇心驱动之间的图通路（§九：好奇经图扩散进行为候选，
# 而不是 curiosity 外挂直连决策器）
CURIOSITY_BEHAVIOR_EDGE = ("CuriosityDrive", "行为:探索", "驱动", 0.6)


def _pair_counts_defaults() -> dict:
    """pair 的完整证据档案（全部长在节点 extra_attrs 上，无平行存储）。"""
    return {
        "evidence_count": 0,
        "positive_count": 0,
        "negative_count": 0,
        "neutral_count": 0,
        "ambiguous_count": 0,
        "pos_feedback": 0,      # 兼容旧字段名（= positive_count）
        "neg_feedback": 0,
        "sources": {},          # {来源: 计数}
        "evidence": [],         # 最近证据引用环
        "first_observed": None,
        "last_reinforced": None,
        "confidence": 0.3,      # 对这条倾向的置信度（随证据量增长）
    }


def ensure_competition_edges(kg: KnowledgeGraph):
    """行为竞争的跨系统图通路（幂等，模块级）：好奇心驱动 → 探索行为候选。

    CuriosityDrive 由 DriveEvaluator 创建（可能晚于本模块），故
    DispositionStore.ensure_vocab 与 DriveEvaluator.bootstrap_drives 两边都调。
    """
    src, dst, rel, w = CURIOSITY_BEHAVIOR_EDGE
    with kg._lock:
        if (kg.get_node(src) is not None
                and kg.get_node(dst) is not None
                and kg.get_edge(src, dst, rel) is None):
            kg.add_edge(Edge(src=src, dst=dst, relation=rel, weight=w,
                             relation_category="cognitive_relation"))
            logger.info("[Disposition] 行为竞争通路: %s -[%s]-> %s", src, rel, dst)


def context_for_dialogue_act(dialogue_act: str) -> str:
    return DIALOGUE_ACT_CONTEXT.get(str(dialogue_act or "").strip(), DEFAULT_CONTEXT)


# ═══════════════════════════════════════════════════════════════
# 行为启发式分类器（R1：turn-level，零 LLM 成本）
# ═══════════════════════════════════════════════════════════════

_EMPATHY_MARKERS = ("难过", "不开心", "开心", "辛苦", "我懂", "理解你", "抱抱",
                    "别难", "恭喜", "心疼", "不容易", "失落", "委屈")


def classify_behavior(answer_text: str) -> str:
    """启发式分类 FAS 本轮实际表达行为。优先级：沉默>追问>共情>确认>详述>回应。"""
    text = str(answer_text or "").strip()
    if not text:
        return "silence"
    if "？" in text or "?" in text:
        return "ask"
    if any(m in text for m in _EMPATHY_MARKERS):
        return "empathize"
    if len(text) <= 12:
        return "acknowledge"
    if len(text) > 100:
        return "elaborate"
    return "respond"


_NEGATIVE_MARKERS = ("闭嘴", "别问", "不想说", "烦", "啰嗦", "废话", "不想聊",
                     "别说了", "打断", "单调", "停止", "换个话题", "换话题",
                     "打住", "到此为止")
_POSITIVE_MARKERS = ("哈哈", "谢谢", "对啊", "嗯嗯", "是的", "好的", "厉害",
                     "确实", "没错", "真的吗", "好家伙", "笑死")


def _char_bigrams(s: str) -> set:
    s = "".join(c for c in str(s or "") if c.strip())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else set()


def classify_outcome(prev_expr: dict, current_user_text: str,
                     current_parsed: dict, gap_minutes: float) -> tuple:
    """【DEPRECATED 2026-09-19】文本→outcome 规则分类器已退役。

    生产路径改用 expression_feedback：回应关系由图谱共激活回流后验形成，
    "哈哈→positive"式词表与 ">4h→neutral" 均已废弃（词表只作为普通语言
    节点参与激活，不决定 outcome）。本函数与 _POSITIVE/_NEGATIVE_MARKERS
    仅为既有测试与回滚参考保留，app.py 不再调用。
    历史文档：标注上一轮 FAS 表达的 outcome：positive / negative / neutral / ambiguous。
    """
    text = str(current_user_text or "")
    neg_hit = any(m in text for m in _NEGATIVE_MARKERS)
    pos_hit = any(m in text for m in _POSITIVE_MARKERS)
    if neg_hit and pos_hit:
        return ("ambiguous", "积极词与抵触词同现，反馈方向不明")
    if neg_hit:
        return ("negative", "用户明确抵触")
    if gap_minutes is not None and gap_minutes > 240:
        return ("neutral", "对话长时间中断")
    # 上轮 FAS 在提问、用户本轮回应 → 回答问题本身就是互动延续，
    # 即使词面无重合也不是话题切换（如被问"常点哪款"答"焦糖玛奇朵"）
    if prev_expr.get("behavior") == "ask":
        return ("positive", "回应了FAS的提问")

    prev_user = str(prev_expr.get("user_text", ""))
    prev_topics = set(prev_expr.get("topics", []) or [])
    # 主题延续判定：上一轮话题节点出现在本轮文本，或用户文本二元组重合度高
    topic_hit = any(str(t) and str(t) in text for t in prev_topics)
    bi_prev, bi_cur = _char_bigrams(prev_user), _char_bigrams(text)
    jaccard = (len(bi_prev & bi_cur) / len(bi_prev | bi_cur)) if (bi_prev | bi_cur) else 0.0
    if topic_hit or jaccard > 0.2:
        return ("positive", "用户继续同话题")

    if pos_hit:
        return ("positive", "用户积极回应")
    return ("neutral", "话题切换")


# ═══════════════════════════════════════════════════════════════
# 行为倾向存储
# ═══════════════════════════════════════════════════════════════

class DispositionStore:
    """行为倾向（context→behavior pair）的图存储与强化/衰减。"""

    def __init__(self, kg: KnowledgeGraph, config: dict = None):
        self.kg = kg
        self.config = config or {}
        self._evo = get_evolution_log()
        self._lock = threading.RLock()
        self.ensure_vocab()

    # ── 词表节点 ──────────────────────────────────────────

    def ensure_vocab(self):
        """创建封闭词表节点 + 行为竞争先验边。幂等。"""
        with self._lock, self.kg._lock:
            for key, nid in BEHAVIORS.items():
                if nid not in self.kg.nodes:
                    self.kg.add_node(Node(
                        id=nid, weight=0.5, label="disposition",
                        graph_space="self",
                        extra_attrs={"type": "behavior", "key": key},
                    ))
            all_contexts = set(DIALOGUE_ACT_CONTEXT.values()) | {
                DEFAULT_CONTEXT, MODIFIER_EMOTION_LOW, MODIFIER_FAMILIAR,
                PROACTIVE_CONTEXT}
            for nid in all_contexts:
                if nid not in self.kg.nodes:
                    self.kg.add_node(Node(
                        id=nid, weight=0.5, label="disposition",
                        graph_space="self",
                        extra_attrs={"type": "context"},
                    ))
            # 行为竞争先验（§四）：情境-[先验 w]->行为，出厂种子，幂等
            for ctx, seeds in BEHAVIOR_PRIOR_SEEDS.items():
                if ctx not in self.kg.nodes:
                    continue
                for bhv_key, w in seeds.items():
                    bhv_node = BEHAVIORS.get(bhv_key)
                    if bhv_node is None or self.kg.get_edge(ctx, bhv_node, "先验"):
                        continue
                    self.kg.add_edge(Edge(
                        src=ctx, dst=bhv_node, relation="先验", weight=w,
                        relation_category="cognitive_relation"))
        self.ensure_competition_edges()

    def ensure_competition_edges(self):
        """行为竞争的跨系统图通路（幂等）：好奇心驱动 → 探索行为候选。"""
        ensure_competition_edges(self.kg)

    @staticmethod
    def pair_id(context_id: str, behavior_key: str) -> str:
        return f"倾向:{behavior_key}@{context_id.replace('情境:', '')}"

    def _known_action_node(self, behavior_key: str):
        """行动节点解析（2026-09-20 行动重构）：LEGACY 词表 ∪ 图谱中
        存在且类型合法的行动节点（proposed 行动概念与旧行为共享同一
        学习载体——情境-[激活]->行动 边）。"""
        if behavior_key in BEHAVIORS:
            return BEHAVIORS[behavior_key]
        if not behavior_key:
            return None
        for nid in (f"行为:{behavior_key}", f"行动:{behavior_key}",
                    behavior_key):
            node = self.kg.nodes.get(nid)
            if node is not None and (node.extra_attrs or {}).get(
                    "type") in ("behavior", "action_proposed",
                    "action_concept"):
                return nid
        return None

    def _activation_edge(self, context_id: str, behavior_key: str):
        nid = self._known_action_node(behavior_key)
        if not nid:
            return None
        for e in self.kg.edges:
            if (e.src == context_id and e.dst == nid
                    and e.relation == "激活"):
                return e
        return None

    # ── 读写 ──────────────────────────────────────────────

    def get_pair(self, context_id: str, behavior_key: str):
        pid = self.pair_id(context_id, behavior_key)
        return self.kg.nodes.get(pid)

    def get_or_create(self, context_id: str, behavior_key: str,
                      evidence_ref: str = "", source: str = "reflection"):
        """获取或新建 candidate pair。

        学习闭环修复：允许在**第一条真实经历**到达时建立 candidate。
        行动空间泛化（2026-09-20 行动重构）：behavior_key 不再限死
        LEGACY 词表——图谱里的行动概念节点（含 proposed 新概念）同样可学，
        新概念的 pair 获得与旧行为完全相同的学习载体
        （情境-[激活 w=strength]->行动节点）。上界由生命周期与
        candidate 封顶机制约束，不靠词表封闭。
        新建只从 positive/negative 经历发生（apply_outcome 内部把关），
        neutral/ambiguous 不建——防止把语法性互动当作人格证据。
        """
        bhv_node = self._known_action_node(behavior_key)
        if bhv_node is None:
            return None
        pid = self.pair_id(context_id, behavior_key)
        with self._lock, self.kg._lock:
            node = self.kg.nodes.get(pid)
            if node is None:
                node = Node(
                    id=pid, weight=0.5, label="disposition",
                    graph_space="self",
                    extra_attrs={
                        "type": "disposition",
                        "behavior": behavior_key,
                        "context": context_id,
                        "status": "candidate",
                        "strength": NEW_CANDIDATE_STRENGTH,
                        **_pair_counts_defaults(),
                    },
                )
                self.kg.add_node(node)
                self.kg.add_edge(Edge(
                    src=context_id, dst=pid, relation="产生", weight=0.7,
                    relation_category="cognitive_relation"))
                self.kg.add_edge(Edge(
                    src=pid, dst=bhv_node, relation="指向", weight=0.7,
                    relation_category="cognitive_relation"))
                self.kg.add_edge(Edge(
                    src=context_id, dst=bhv_node, relation="激活",
                    weight=NEW_CANDIDATE_STRENGTH,
                    relation_category="cognitive_relation"))
                self._evo.note_node_added(pid, label="disposition", weight=0.5)
                logger.info(
                    f"[Disposition] +candidate: {pid} "
                    f"(首条经历建立，强度={NEW_CANDIDATE_STRENGTH}，"
                    f"source={source} evidence={evidence_ref[:40]!r})")
            if evidence_ref:
                self._append_evidence(node, evidence_ref, source=source,
                                      outcome=None)
                node.extra_attrs["evidence_count"] = \
                    int(node.extra_attrs.get("evidence_count", 0)) + 1
            return node

    def _append_evidence(self, pair: Node, evidence_ref: str, source: str,
                         outcome: str = None):
        """证据入档案：引用环 + 四态计数 + 来源计数 + 时间戳 + 置信度。

        防"字符串说了、计数器没记"的漂移：outcome 未显式传入但证据文本自带
        `[positive]/[negative]/[neutral]/[ambiguous]` 标签时（反思产出或历史
        格式的原始追加），按标签记账。
        """
        ea = pair.extra_attrs
        if outcome is None:
            _m = str(evidence_ref).lstrip().find("]")
            if str(evidence_ref).lstrip().startswith("[") and 0 < _m < 14:
                tag = str(evidence_ref).lstrip()[1:_m].strip()
                if tag in VALID_OUTCOMES:
                    outcome = tag
        tag_out = outcome or "neutral"
        ea.setdefault("evidence", []).append(
            f"[{tag_out}|{source}] {str(evidence_ref)[:80]}")
        del ea["evidence"][:-EVIDENCE_KEEP]
        if outcome in (OUTCOME_POSITIVE, OUTCOME_NEGATIVE,
                       OUTCOME_NEUTRAL, OUTCOME_AMBIGUOUS):
            ea[f"{outcome}_count"] = int(ea.get(f"{outcome}_count", 0)) + 1
        if outcome == OUTCOME_POSITIVE:
            ea["pos_feedback"] = int(ea.get("pos_feedback", 0)) + 1
        elif outcome == OUTCOME_NEGATIVE:
            ea["neg_feedback"] = int(ea.get("neg_feedback", 0)) + 1
        src = ea.setdefault("sources", {})
        src[source] = int(src.get(source, 0)) + 1
        if not ea.get("first_observed"):
            ea["first_observed"] = _now()
        ea["last_reinforced"] = _now()
        # 置信度：随独立证据量缓慢增长（一次经历贡献有限，§七）
        total = sum(src.values())
        ea["confidence"] = round(
            min(0.9, 0.3 + total * 0.08
                + (0.1 if ea.get("status") == "stable" else 0.0)), 4)

    def _sync_activation_edge(self, pair: Node):
        edge = self._activation_edge(
            pair.extra_attrs.get("context", ""),
            pair.extra_attrs.get("behavior", ""))
        if edge:
            edge.weight = float(pair.extra_attrs.get("strength", 0.25))

    def _check_promotion(self, pair: Node):
        ea = pair.extra_attrs
        pos, neg = int(ea.get("pos_feedback", 0)), int(ea.get("neg_feedback", 0))
        before = ea.get("status")
        # 晋升只数真实经历（social+self 合并计，见 _append_evidence/apply_experience
        # 的 pos_feedback/neg_feedback 由净学习信号方向决定）。
        #
        # NOTE: promotion thresholds below are an **engineering stabilization
        # rule**, not a psychological claim. evidence>=4 / ratio>=3:1 /
        # strength>=0.5 是为了抑制单事件噪声与早发固化而选的数值；它们不对应
        # 任何"人格科学"命题。改它们=改工程稳定性，不改变架构语义。
        real_evidence = pos + neg
        ok_ev = real_evidence >= STABLE_MIN_EVIDENCE
        ok_ratio = (neg == 0 or pos / max(neg, 1) >= STABLE_MIN_RATIO)
        ok_st = float(ea.get("strength", 0)) >= STABLE_MIN_STRENGTH
        if ea.get("status") == "candidate" and ok_ev and ok_ratio and ok_st:
            ea["status"] = "stable"
            self._evo.note_node_updated(pair.id, {"status": "stable"})
            logger.info(
                f"[Disposition] → stable: {pair.id} "
                f"(real_evidence={real_evidence} pos={pos} neg={neg} "
                f"strength={float(ea['strength']):.2f} "
                f"sources={ea.get('sources')})")
        elif before == "candidate":
            logger.debug(
                f"[Disposition] promotion 未达 {pair.id}: real_evidence={real_evidence}"
                f"≥{STABLE_MIN_EVIDENCE}={ok_ev} "
                f"ratio={pos}:{neg}≥{STABLE_MIN_RATIO}={ok_ratio} "
                f"strength≥{STABLE_MIN_STRENGTH}={ok_st}")

    def _check_demotion(self, pair: Node) -> bool:
        """stable 只降级、永不删除（一次/少量负反馈不抹掉长期人格）。

        降级时强度设地板：candidate 重新可塑，但不因"刚降级恰好低于
        REMOVE_BELOW"而被同一手负证据连坐删除——删除只该属于被长期
        冷落的普通 candidate。
        """
        ea = pair.extra_attrs
        if ea.get("status") != "stable":
            return False
        pos = int(ea.get("pos_feedback", 0))
        neg = int(ea.get("neg_feedback", 0))
        if neg >= DEMOTE_MIN_NEG and neg >= pos * DEMOTE_NEG_RATIO:
            ea["status"] = "candidate"
            ea["strength"] = max(float(ea.get("strength", 0.25)),
                                 REMOVE_BELOW + 0.01)
            self._sync_activation_edge(pair)
            self._evo.note_node_updated(pair.id, {"status": "candidate(demoted)"})
            logger.info(
                f"[Disposition] stable→candidate 降级: {pair.id} "
                f"(neg={neg} pos={pos}——负证据累积，人格重新可塑；"
                f"强度落到地板 {ea['strength']:.2f}，不删除)")
            return True
        return False

    def _check_removal(self, pair: Node) -> bool:
        # stable 永不因强度过低删除（只由降级处理）；candidate 过低才移除
        if (pair.extra_attrs or {}).get("status") == "stable":
            return False
        if float(pair.extra_attrs.get("strength", 0)) < REMOVE_BELOW:
            self.remove(pair)
            return True
        return False

    def remove(self, pair: Node):
        with self._lock, self.kg._lock:
            pid = pair.id
            ctx, bhv = pair.extra_attrs.get("context", ""), pair.extra_attrs.get("behavior", "")
            bhv_node = self._known_action_node(bhv)
            # 走 kg 方法维护派生邻接索引（并记录 evolution log）
            self.kg.remove_edges(src=pid)
            self.kg.remove_edges(dst=pid)
            if ctx and bhv_node:
                self.kg.remove_edges(src=ctx, dst=bhv_node, relation="激活")
            self.kg.remove_node(pid)
            self._evo.note_node_removed(pid)
            logger.info(f"[Disposition] -removed: {pid} (candidate 强度过低)")

    def apply_outcome(self, context_id: str, behavior_key: str,
                      outcome: str, evidence_ref: str = "",
                      source: str = "user_feedback",
                      allow_create: bool = False) -> bool:
        """兼容入口：旧的单路 outcome 调用。

        人格学习架构改造后，这里只是把 legacy 四态投影为 social_outcome
        并转发到 apply_experience（自我反馈路径不经此处）。
        """
        legacy_to_social = {
            "positive": "accepted", "negative": "rejected",
            "neutral": "neutral", "ambiguous": "ambiguous",
        }
        # source=baseline/reflection 的历史语义保留为"社会/外部归纳"类信号，
        # 但仍只通过 social 通道进入统一学习（reflection 权重低是 reward 层的事）
        if outcome not in legacy_to_social:
            return False
        r = self.apply_experience(
            behavior_key, context_id,
            social_outcome=legacy_to_social[outcome],
            evidence_ref=evidence_ref or f"[legacy:{source}]",
            allow_create=allow_create)
        return bool(r.get("written"))

    def apply_experience(self, behavior_key: str, context_id: str,
                         social_outcome: str = None, self_outcome: str = None,
                         hormone: dict = None, evidence_ref: str = "",
                         allow_create: bool = True) -> dict:
        """一次经历的统一学习入口（替代 outcome→strength 直连）。

            learning_signal =
                  social_valence × w_social × LEARN_BASE(social)
                + self_valence   × w_self   × LEARN_BASE(self)
              然后 × hormone_modulation

        要点：
          - 社会反馈权重 0.4、自我反馈 0.6——长期倾向不能主要由用户满意度塑造。
          - 激素只调制**这次经历值多少学习**（hormone=factor），绝不直接建/改
            disposition 的存在性；factor 缺省 1.0（无奖赏系统时退化为线性学习）。
          - cancelled（self_outcome="cancelled"）valence=0 → 不产生任何学习。
            没完成 ≠ 做错了。
          - 首条有意义的学习信号可建立 candidate；neutral 双方 → 只记观察。
        返回 trace dict（§十四 全字段）。
        """
        from reward import SOCIAL_VALENCE, SELF_VALENCE, social_to_legacy
        cfg = (self.config or {}).get("disposition_learning", {}) \
            if isinstance(getattr(self, "config", None), dict) else {}
        w = {**{"social": 0.4, "self": 0.6}, **(cfg.get("weights") or {})}
        learn_pos = float(cfg.get("learn_pos", 0.25))
        learn_neg = float(cfg.get("learn_neg", 0.40))
        exp_id = f"x_{int(time.time() * 1000) % 10**9}"

        sv = float(SOCIAL_VALENCE.get(social_outcome, 0.0)) if social_outcome else 0.0
        tv = float(SELF_VALENCE.get(self_outcome, 0.0)) if self_outcome else 0.0
        social_sig = sv * (learn_pos if sv >= 0 else learn_neg) * w["social"]
        self_sig = tv * (learn_pos if tv >= 0 else learn_neg) * w["self"]
        raw = social_sig + self_sig
        mod = dict(hormone or {})
        factor = float(mod.get("factor", 1.0))
        signal = round(raw * factor, 4)

        pid = self.pair_id(context_id, behavior_key)
        trace = {"experience_id": exp_id, "behavior": behavior_key,
                 "context": context_id, "social_outcome": social_outcome,
                 "self_outcome": self_outcome, "social_signal": round(social_sig, 4),
                 "self_signal": round(self_sig, 4), "hormone": mod,
                 "learning_signal": signal, "written": False,
                 "old": None, "new": None, "state": None}

        if signal == 0.0:
            # 净学习为零：仍可能是一次**中性/含糊观察**（用户没接话、方向不明）
            # ——记录但不动强度、不建新城（§四：neutral 不是奖励）。若无任何
            # outcome 标签（如双方都为 None 的纯调制）→ 完全 no-op。
            has_experience = (social_outcome is not None or self_outcome is not None)
            if not has_experience:
                logger.info(
                    f"[Disposition] {exp_id} 无学习信号（social={social_outcome} "
                    f"self={self_outcome}）: behavior={behavior_key}@{context_id}")
                return trace
            with self._lock, self.kg._lock:
                pair = self.get_pair(context_id, behavior_key)
                if pair is None:
                    # 中性观察不新建 pair（避免把语法性互动当人格）
                    logger.info(
                        f"[Disposition] {exp_id} 中性观察且无既有倾向，不建: "
                        f"{behavior_key}@{context_id}")
                    return trace
                ea = pair.extra_attrs
                if (social_outcome == "ambiguous" or self_outcome == "ambiguous"
                        or (sv and tv and (sv > 0) != (tv > 0))):
                    bucket = "ambiguous"
                else:
                    bucket = "neutral"
                self._append_evidence(
                    pair, evidence_ref or f"{social_outcome or '-'}/{self_outcome or '-'}",
                    source=f"social:{social_outcome}" if sv else f"self:{self_outcome}",
                    outcome=None)
                ea[f"{bucket}_count"] = int(ea.get(f"{bucket}_count", 0)) + 1
                ea["evidence_count"] = int(ea.get("evidence_count", 0)) + 1
                trace.update({"written": True, "new": float(ea.get("strength", 0)),
                              "old": float(ea.get("strength", 0)),
                              "state": ea.get("status"), "observation_only": True})
            logger.info(
                f"[Disposition] {exp_id} 净学习=0，记为 {bucket} 观察 "
                f"(behavior={behavior_key}@{context_id} 强度不变)")
            return trace

        with self._lock, self.kg._lock:
            pair = self.get_pair(context_id, behavior_key)
            if pair is None:
                if not allow_create:
                    return trace
                src_tag = ("social:" + social_outcome if sv else "") or \
                          ("self:" + self_outcome if tv else "") or "experience"
                pair = self.get_or_create(context_id, behavior_key,
                                          evidence_ref="", source=src_tag or "experience")
                if pair is None:
                    return trace
            ea = pair.extra_attrs
            old = float(ea.get("strength", NEW_CANDIDATE_STRENGTH))
            new = round(max(0.0, min(1.0, old + signal)), 4)
            ea["strength"] = new
            outcome = ("positive" if signal > 0 else "negative")
            # 来源标签：以主导通道为准（|valence| 大者），便于复盘"这次学习
            # 是社会给的还是她自己挣的"
            if sv and (not tv or abs(sv) >= abs(tv)):
                src_tag = f"social:{social_outcome}"
            elif tv:
                src_tag = f"self:{self_outcome}"
            else:
                src_tag = "experience"
            # _append_evidence 记证据环/来源/时间戳/置信度；四态计数与净方向
            # 在下面按**净学习信号**统一处理（两通道可能相抵）。
            self._append_evidence(
                pair, evidence_ref or f"{social_outcome or '-'}/{self_outcome or '-'}",
                source=src_tag, outcome=None)
            ea[f"{outcome}_count"] = int(ea.get(f"{outcome}_count", 0)) + 1
            if outcome == "positive":
                ea["pos_feedback"] = int(ea.get("pos_feedback", 0)) + 1
            else:
                ea["neg_feedback"] = int(ea.get("neg_feedback", 0)) + 1
            ea["evidence_count"] = int(ea.get("evidence_count", 0)) + 1
            # 学习账本：每次更新的完整可复算记录（运行期写节点属性=图存储）
            led = ea.setdefault("learning_ledger", [])
            led.append({"id": exp_id, "ts": _now(), "social": social_outcome,
                        "self": self_outcome, "sig": signal, "raw": round(raw, 4),
                        "mod": mod, "old": old, "new": new})
            del led[:-10]
            self._sync_activation_edge(pair)
            self._evo.note_weight_changed(
                "edge", f"{context_id}-激活->{self._known_action_node(behavior_key)}",
                old_weight=old, new_weight=new,
                reason=f"exp {social_outcome or '-'}/{self_outcome or '-'}")
            trace.update({"written": True, "old": old, "new": new,
                          "state": ea.get("status")})
            logger.info(
                f"[Disposition] {exp_id} behavior={behavior_key} context={context_id} "
                f"social_outcome={social_outcome} self_outcome={self_outcome} "
                f"social_sig={social_sig:+.3f} self_sig={self_sig:+.3f} "
                f"hormone_mod={factor:.2f} learning_signal={signal:+.3f} "
                f"strength={old:.2f}→{new:.2f} state={ea['status']}")
            self._check_promotion(pair)
            self._check_demotion(pair)
            self._check_removal(pair)
            trace["state"] = ea.get("status")
            return trace

    def llm_adjust(self, context_id: str, behavior_key: str,
                   direction: str, reason: str = ""):
        """反思 LLM 的轻量调整建议（小步长，无权决定 stable）。"""
        with self._lock, self.kg._lock:
            pair = self.get_pair(context_id, behavior_key)
            if pair is None:
                return
            ea = pair.extra_attrs
            old = float(ea.get("strength", 0.25))
            delta = LLM_ADJUST_DELTA if direction == "reinforce" else -LLM_ADJUST_DELTA
            ea["strength"] = max(0.0, min(1.0, old + delta))
            self._sync_activation_edge(pair)
            self._evo.note_weight_changed(
                "edge", pair.id, old_weight=old, new_weight=ea["strength"],
                reason=f"reflection_llm: {reason[:40]}")
            self._check_removal(pair)

    def decay_all(self):
        """周期衰减（挂 reflection 周期）。"""
        with self._lock, self.kg._lock:
            for node in list(self.kg.nodes.values()):
                if (node.extra_attrs or {}).get("type") != "disposition":
                    continue
                ea = node.extra_attrs
                old = float(ea.get("strength", 0))
                ea["strength"] = round(old * DECAY_FACTOR, 4)
                self._sync_activation_edge(node)
                self._check_removal(node)

    # ── 查询 ──────────────────────────────────────────────

    def tendencies_for(self, context_ids: list, top_n: int = 4) -> list:
        """聚合多个激活情境的行为倾向（供回答注入/竞争消费）。

        人格学习架构改造（§十一）：有效强度 = 学到的边权 **混入行为节点的
        实时激活**。情境节点被点亮 → 沿 `情境-[激活 weight]->行为` 边扩散 →
        行为节点获得激活 → 这里读回来。于是"倾向"不只是静态表，而是走
        attention 通路的动态量（app 回合内在扩散前注入情境种子激活）。

        candidate 学习分量仍受 cap 封顶（§八：中期低权重参与）；实时激活项
        单独叠加、总量夹到 1.0。行为节点激活本身被全局衰减/回合间衰减自然
        清理，不需要额外生命周期。
        """
        blend = float((self.config or {}).get(
            "disposition_learning", {}).get("activation_blend", 0.15))
        a_max = float((self.config or {}).get("activation_max", 5.0)) or 5.0
        result = {}
        with self.kg._lock:
            for node in self.kg.nodes.values():
                ea = node.extra_attrs or {}
                if ea.get("type") != "disposition":
                    continue
                if ea.get("context") not in context_ids:
                    continue
                bhv = ea.get("behavior", "")
                strength = float(ea.get("strength", 0))
                # candidate 封顶：未稳定的倾向对行为影响受限
                if ea.get("status") == "candidate":
                    strength = min(strength, CANDIDATE_CAP)
                # 实时激活项：行动节点此刻的注意力（走图扩散来的）
                bhv_node = self.kg.nodes.get(
                    self._known_action_node(bhv) or "")
                act = (float(getattr(bhv_node, "activation", 0.0) or 0.0)
                       if bhv_node is not None else 0.0)
                eff = max(0.0, min(1.0, strength + blend * min(1.0, act / a_max)))
                if eff > result.get(bhv, (0, None, 0.0, ""))[0]:
                    result[bhv] = (
                        eff, ea.get("status", "candidate"), act,
                        str(((bhv_node.extra_attrs
                              if bhv_node is not None else None)
                             or {}).get("name_zh") or ""))
        ranked = sorted(
            ((b, e, st, a, lb) for b, (e, st, a, lb) in result.items()
             if e > 0.05),
            key=lambda x: -x[1])[:top_n]
        return [{"behavior": b, "strength": round(e, 2), "status": st,
                 "activation": round(a, 2),
                 # 概念显示名（行动重构 2026-09-20）：图谱提议的新行动
                 # 在 prompt 渲染里有中文名，不泄漏内部短键
                 "label": lb}
                for b, e, st, a, lb in ranked]

    def list_dispositions(self) -> list:
        out = []
        with self.kg._lock:
            for node in self.kg.nodes.values():
                ea = node.extra_attrs or {}
                if ea.get("type") != "disposition":
                    continue
                out.append({
                    "id": node.id,
                    "behavior": ea.get("behavior"),
                    "context": ea.get("context"),
                    "status": ea.get("status"),
                    "strength": ea.get("strength"),
                    "evidence_count": ea.get("evidence_count", 0),
                    "positive_count": ea.get("positive_count", 0),
                    "negative_count": ea.get("negative_count", 0),
                    "neutral_count": ea.get("neutral_count", 0),
                    "ambiguous_count": ea.get("ambiguous_count", 0),
                    "sources": ea.get("sources", {}),
                    "first_observed": ea.get("first_observed"),
                    "last_reinforced": ea.get("last_reinforced"),
                    "confidence": ea.get("confidence"),
                    "pos_feedback": ea.get("pos_feedback", 0),
                    "neg_feedback": ea.get("neg_feedback", 0),
                    "evidence": ea.get("evidence", [])[-5:],
                    "created": node.created,
                })
        return sorted(out, key=lambda d: -(d.get("strength") or 0))
