# self_model.py — Self Model: 持续性主体模型
# ============================================================================
# FAS Phase 1: Self Model 核心模块。
#
# 设计原则（最高优先级）：
#   1. 不新建独立状态管理器 — Self Graph 在 self 空间内，本质是 KnowledgeGraph 的节点/边
#   2. 知识溯源 — 所有 Belief/Preference 必须携带 provenance 字段（confidence, source,
#      evidence_count, first_observed, last_reinforced）
#   3. 重要性门槛 — 不允许每句话都修改 Self Model
#   4. 防污染 — 临时表态、玩笑话不能进入 Self
#
# 三大组件：
#   SelfGraphManager        — Self 空间的结构化读写接口
#   evaluate_importance     — 无状态重要性评估函数
#   SelfMemoryUpdater       — 编排器：接收 NLP 输出 → 评估 → 决策 → 写入
# ============================================================================

import logging
import time
import re
from datetime import datetime, timedelta
from typing import Optional

from graph_model import KnowledgeGraph, Node, Edge, now_str
from graph_evolution_log import get_evolution_log

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────

SELF_ID = "Self"

# 关系边名称
REL_BELIEF = "信念"
REL_PREFERENCE = "偏好"
REL_USER_RELATION = "用户关系"
REL_GOAL = "目标"
REL_LIKE = "喜欢"           # 旧版兼容

# 输入侧被视为"偏好表达"的关系（P0-7：只用于 FAS 第一人称主语的边）
PREFERENCE_RELATIONS = {"喜欢", "不喜欢", "讨厌", "爱", "偏爱", "偏好"}

# 有效的 source 枚举
VALID_SOURCES = {
    "explicit_statement",
    "multiple_experiences",
    "single_observation",
    "llm_inference",
}

# provenance 必须字段
REQUIRED_PROVENANCE = [
    "confidence", "source", "evidence_count", "first_observed", "last_reinforced"
]

# 用户明确的陈述模式（匹配"我喜欢/我相信/我讨厌/我是"等）
EXPLICIT_STATEMENT_PATTERNS = [
    re.compile(r'我(喜欢|爱|讨厌|不喜欢|觉得|认为|相信|是|想|想要|希望|决定|打算)'),
    re.compile(r'我的(爱好|兴趣|目标|梦想|信念|价值观).*是'),
    re.compile(r'(对我来说|在我眼里|在我看来)'),
]


# ═══════════════════════════════════════════════════════════════
# SelfGraphManager
# ═══════════════════════════════════════════════════════════════

class SelfGraphManager:
    """Self 空间的结构化读写接口。

    不存储任何状态 — 所有操作直接读写 KnowledgeGraph。
    """

    def __init__(self, kg: KnowledgeGraph):
        self.kg = kg
        self._evo_log = get_evolution_log()

    # ── 确保 Self 节点存在 ──

    def _ensure_self(self) -> Node:
        node = self.kg.get_node(SELF_ID)
        if node is None:
            node = Node(id=SELF_ID, weight=0.5, label="self", graph_space="self",
                       extra_attrs={"description": "Fascinator 自我聚合中心"})
            self.kg.add_node(node)
        return node

    # ── Belief ───────────────────────────────────────────

    def get_beliefs(self) -> list:
        """获取所有 Belief 节点（含完整 provenance）。"""
        beliefs = []
        with self.kg._lock:
            for edge in self.kg.edges:
                if edge.src == SELF_ID and edge.relation == REL_BELIEF:
                    node = self.kg.get_node(edge.dst)
                    if node:
                        beliefs.append(self._node_to_belief_dict(node, edge))
        return sorted(beliefs, key=lambda b: b.get("confidence", 0), reverse=True)

    def upsert_belief(self, content: str, provenance: dict) -> dict:
        """创建或更新一条 Belief。返回操作结果。"""
        _validate_provenance(provenance)

        self._ensure_self()
        node_id = f"信念: {content}"

        with self.kg._lock:
            existing = self.kg.get_node(node_id)
            if existing:
                # 强化已有 belief
                old_conf = existing.confidence
                ev = provenance.get("evidence_count", 1)
                existing.confidence = min(0.95, old_conf + 0.05)
                existing.extra_attrs["evidence_count"] = (
                    existing.extra_attrs.get("evidence_count", 0) + ev
                )
                existing.extra_attrs["last_reinforced"] = provenance.get(
                    "last_reinforced", now_str()
                )
                existing.extra_attrs["confidence"] = existing.confidence
                existing.touch()
                logger.info(
                    f"[SelfModel] 强化 Belief: '{content}' "
                    f"conf={old_conf:.2f}→{existing.confidence:.2f} "
                    f"evidence={existing.extra_attrs['evidence_count']}"
                )
                try:  # 观测：信念强化（旧值→新值 + 证据来源，§15）
                    import fas_log
                    fas_log.get_logger(fas_log.SELF).info(
                        "belief_reinforced", f"信念强化：{content[:60]}",
                        node_id=node_id, old_conf=round(float(old_conf), 3),
                        new_conf=round(float(existing.confidence), 3),
                        evidence=existing.extra_attrs.get("evidence_count"),
                        reason=str(provenance.get("source") or ""))
                except Exception:
                    pass
                return {"action": "reinforced", "node_id": node_id,
                        "confidence": existing.confidence}

            # 创建新 belief 节点
            node = Node(
                id=node_id, weight=0.5, label="declarative-semantic",
                graph_space="self",
                confidence=provenance.get("confidence", 0.55),
                extra_attrs={
                    "type": "belief",
                    "content": content,
                    "source": provenance["source"],
                    "evidence_count": provenance.get("evidence_count", 1),
                    "first_observed": provenance.get("first_observed", now_str()),
                    "last_reinforced": provenance.get("last_reinforced", now_str()),
                }
            )
            self.kg.add_node(node)
            self.kg.add_edge(Edge(
                src=SELF_ID, dst=node_id, relation=REL_BELIEF, weight=0.6,
                relation_category="cognitive_relation"
            ))
            logger.info(
                f"[SelfModel] + Belief: '{content}' "
                f"conf={node.confidence:.2f} src={provenance['source']}"
            )
            try:  # 观测：新信念诞生（含来源=为什么信这个）
                import fas_log
                fas_log.get_logger(fas_log.SELF).info(
                    "belief_created", f"新信念：{content[:60]}",
                    node_id=node_id, confidence=round(float(node.confidence), 3),
                    reason=str(provenance.get("source") or ""))
            except Exception:
                pass
            return {"action": "created", "node_id": node_id,
                    "confidence": node.confidence}

    def remove_belief(self, content: str) -> bool:
        """移除一条 Belief。"""
        node_id = f"信念: {content}"
        return self.kg.remove_node(node_id)

    # ── Preference ───────────────────────────────────────

    def get_preferences(self) -> list:
        """获取所有 Preference 节点（含完整 provenance + 旧版兼容）。"""
        prefs = []
        with self.kg._lock:
            for edge in self.kg.edges:
                if edge.src == SELF_ID and edge.relation in (REL_PREFERENCE, REL_LIKE):
                    node = self.kg.get_node(edge.dst)
                    if node:
                        d = self._node_to_pref_dict(node, edge)
                        prefs.append(d)
        return sorted(prefs, key=lambda p: p.get("confidence", 0), reverse=True)

    def upsert_preference(self, target: str, provenance: dict) -> dict:
        """创建或更新一条 Preference。"""
        _validate_provenance(provenance)

        self._ensure_self()
        node_id = f"偏好: {target}"

        with self.kg._lock:
            existing = self.kg.get_node(node_id)
            if existing:
                old_conf = existing.confidence
                ev = provenance.get("evidence_count", 1)
                existing.confidence = min(0.95, old_conf + 0.05)
                existing.extra_attrs["evidence_count"] = (
                    existing.extra_attrs.get("evidence_count", 0) + ev
                )
                existing.extra_attrs["last_reinforced"] = provenance.get(
                    "last_reinforced", now_str()
                )
                existing.extra_attrs["confidence"] = existing.confidence
                existing.touch()
                logger.info(
                    f"[SelfModel] 强化 Preference: '{target}' "
                    f"conf={old_conf:.2f}→{existing.confidence:.2f}"
                )
                return {"action": "reinforced", "node_id": node_id,
                        "confidence": existing.confidence}

            node = Node(
                id=node_id, weight=0.5, label="declarative-semantic",
                graph_space="self",
                confidence=provenance.get("confidence", 0.55),
                extra_attrs={
                    "type": "preference",
                    "target": target,
                    "source": provenance["source"],
                    "evidence_count": provenance.get("evidence_count", 1),
                    "first_observed": provenance.get("first_observed", now_str()),
                    "last_reinforced": provenance.get("last_reinforced", now_str()),
                }
            )
            self.kg.add_node(node)
            self.kg.add_edge(Edge(
                src=SELF_ID, dst=node_id, relation=REL_PREFERENCE, weight=0.6,
                relation_category="emotional_relation"
            ))
            logger.info(
                f"[SelfModel] + Preference: '{target}' "
                f"conf={node.confidence:.2f}"
            )
            return {"action": "created", "node_id": node_id,
                    "confidence": node.confidence}

    def adjust_preference_confidence(self, target: str, delta: float) -> bool:
        """微调已有 preference 的置信度。"""
        node_id = f"偏好: {target}"
        node = self.kg.get_node(node_id)
        if node:
            node.confidence = max(0.1, min(0.95, node.confidence + delta))
            node.extra_attrs["confidence"] = node.confidence
            node.touch()
            return True
        return False

    # ── User Relationship ────────────────────────────────

    def get_user_relationship(self) -> dict:
        """获取用户关系节点（单例）。"""
        node = self.kg.get_node("用户关系")
        if node is None:
            return self._default_user_rel()
        return {
            "node_id": node.id,
            "roles": node.extra_attrs.get("roles", []),
            "interaction_count": node.extra_attrs.get("interaction_count", 0),
            "attention_areas": node.extra_attrs.get("attention_areas", []),
            "important_experiences": node.extra_attrs.get("important_experiences", []),
            "first_interaction": node.extra_attrs.get("first_interaction", ""),
            "last_interaction": node.extra_attrs.get("last_interaction", ""),
        }

    def update_user_relationship(self, updates: dict) -> bool:
        """更新用户关系节点。"""
        self._ensure_self()
        node = self.kg.get_node("用户关系")
        if node is None:
            node = Node(
                id="用户关系", weight=0.6, label="declarative-semantic",
                graph_space="self",
                extra_attrs={
                    "type": "user_relationship",
                    "roles": [],
                    "interaction_count": 0,
                    "attention_areas": [],
                    "important_experiences": [],
                    "first_interaction": now_str(),
                    "last_interaction": now_str(),
                }
            )
            self.kg.add_node(node)
            self.kg.add_edge(Edge(
                src=SELF_ID, dst="用户关系", relation=REL_USER_RELATION,
                weight=0.7, relation_category="social_relation"
            ))

        for key, value in updates.items():
            if key in ("interaction_count",):
                node.extra_attrs[key] = value
            elif key in ("roles", "attention_areas", "important_experiences"):
                existing = node.extra_attrs.get(key, [])
                if isinstance(value, list):
                    for v in value:
                        if v not in existing:
                            existing.append(v)
                elif value not in existing:
                    existing.append(value)
                node.extra_attrs[key] = existing
            elif key in ("first_interaction", "last_interaction"):
                node.extra_attrs[key] = value
        node.touch()
        return True

    def increment_interaction(self):
        """互动计数 +1"""
        self.update_user_relationship({
            "interaction_count": self.get_user_relationship().get("interaction_count", 0) + 1,
            "last_interaction": now_str(),
        })

    def add_attention_area(self, area: str):
        """添加用户关注领域"""
        self.update_user_relationship({"attention_areas": [area]})

    # ── Goal ─────────────────────────────────────────────

    def get_goals(self) -> list:
        """获取所有目标（旧版兼容 + 新版 provenance）。"""
        goals = []
        with self.kg._lock:
            for edge in self.kg.edges:
                if edge.src == SELF_ID and edge.relation in (REL_GOAL, "目标"):
                    node = self.kg.get_node(edge.dst)
                    if node:
                        goals.append({
                            "goal": node.id.replace("目标: ", "").replace("目标：", ""),
                            "weight": edge.weight,
                            "confidence": node.confidence,
                            "extra": node.extra_attrs,
                        })
        return goals

    def set_goal(self, goal_desc: str, parent: str = None):
        """设置目标。"""
        from self_graph import set_goal as sg_set_goal
        sg_set_goal(self.kg, goal_desc, parent_goal=parent)

    # ── Summary ──────────────────────────────────────────

    def get_self_summary(self) -> dict:
        """生成 Self 结构化摘要，供 LLM 和 API 使用。

        返回带 confidence 标注的认知摘要，不是原始数据堆砌。
        """
        beliefs = self.get_beliefs()
        prefs = self.get_preferences()
        user_rel = self.get_user_relationship()
        goals = self.get_goals()

        # 按置信度分层
        high_conf_beliefs = [b for b in beliefs if b.get("confidence", 0) >= 0.7]
        medium_conf_beliefs = [b for b in beliefs if 0.4 <= b.get("confidence", 0) < 0.7]

        return {
            "self_id": SELF_ID,
            "summary": {
                "high_confidence_beliefs": [
                    {"content": b["content"], "confidence": b["confidence"]}
                    for b in high_conf_beliefs
                ],
                "developing_beliefs": [
                    {"content": b["content"], "confidence": b["confidence"],
                     "evidence_count": b.get("evidence_count", 0)}
                    for b in medium_conf_beliefs
                ],
                "preferences": [
                    {"target": p["target"], "confidence": p["confidence"],
                     "source": p.get("source", ""), "evidence_count": p.get("evidence_count", 0)}
                    for p in prefs
                ],
                "user_relationship": user_rel,
                "goals": goals,
            },
            "stats": {
                "total_beliefs": len(beliefs),
                "total_preferences": len(prefs),
                "total_goals": len(goals),
                "interaction_count": user_rel.get("interaction_count", 0),
            }
        }

    # ── 内部 helper ──────────────────────────────────────

    def _node_to_belief_dict(self, node: Node, edge: Edge) -> dict:
        return {
            "id": node.id,
            "content": node.extra_attrs.get("content", node.id.replace("信念: ", "")),
            "confidence": node.confidence,
            "source": node.extra_attrs.get("source", "unknown"),
            "evidence_count": node.extra_attrs.get("evidence_count", 0),
            "first_observed": node.extra_attrs.get("first_observed", ""),
            "last_reinforced": node.extra_attrs.get("last_reinforced", ""),
            "edge_weight": edge.weight,
        }

    def _node_to_pref_dict(self, node: Node, edge: Edge) -> dict:
        if node.extra_attrs.get("type") == "preference":
            return {
                "id": node.id,
                "target": node.extra_attrs.get("target", node.id.replace("偏好: ", "")),
                "confidence": node.confidence,
                "source": node.extra_attrs.get("source", "unknown"),
                "evidence_count": node.extra_attrs.get("evidence_count", 0),
                "first_observed": node.extra_attrs.get("first_observed", ""),
                "last_reinforced": node.extra_attrs.get("last_reinforced", ""),
                "edge_weight": edge.weight,
            }
        # 旧版兼容：Self → 喜欢 → target（无边权 provenance）
        return {
            "id": node.id,
            "target": node.id,
            "confidence": node.confidence,
            "source": "legacy",
            "evidence_count": 0,
            "first_observed": node.created or "",
            "last_reinforced": node.last_access or "",
            "edge_weight": edge.weight,
            "legacy": True,
        }

    def _default_user_rel(self) -> dict:
        return {
            "node_id": "用户关系",
            "roles": [],
            "interaction_count": 0,
            "attention_areas": [],
            "important_experiences": [],
            "first_interaction": "",
            "last_interaction": "",
        }


# ═══════════════════════════════════════════════════════════════
# SelfImportanceEvaluator
# ═══════════════════════════════════════════════════════════════

def evaluate_importance(candidate: dict, context: dict, config: dict = None) -> dict:
    """评估候选 Self 更新的重要性分数。

    Args:
        candidate: {"type": "preference", "target": "Minecraft", "source": "explicit_statement"}
        context:  {"is_explicit": bool, "emotion_active": list, "repeat_count": int,
                   "dialogue_act": str, "llm_confidence": float}

    Returns:
        {"score": float, "passed": bool, "factors": dict}
    """
    if config is None:
        config = {}

    sm_config = config.get("self_model", {})
    weights = sm_config.get("importance_weights", {
        "explicit_statement": 0.40, "repeat_count": 0.25,
        "emotion_intensity": 0.15, "llm_confidence": 0.10,
        "temporal_recency": 0.10,
    })
    threshold = sm_config.get("importance_threshold", 0.5)

    score = 0.0
    factors = {}

    # 1. 明确陈述
    is_explicit = context.get("is_explicit", False)
    if is_explicit:
        factors["explicit_statement"] = 1.0
        score += weights["explicit_statement"] * 1.0

    # 2. 重复次数（归一化到 0~1，5次及以上满分）
    repeat = context.get("repeat_count", 0)
    repeat_score = min(1.0, repeat / 5.0)
    factors["repeat_count"] = repeat_score
    score += weights["repeat_count"] * repeat_score

    # 3. 情绪强度
    emotions = context.get("emotion_active", [])
    if emotions:
        emo_score = min(1.0, len(emotions) * 0.3)
        factors["emotion_intensity"] = emo_score
        score += weights["emotion_intensity"] * emo_score

    # 4. LLM 置信度
    llm_conf = context.get("llm_confidence", 0.5)
    factors["llm_confidence"] = llm_conf
    score += weights["llm_confidence"] * llm_conf

    # 5. 时间近因（30天内线性衰减）
    recency = 0.0
    last_reinforced = candidate.get("last_reinforced", "")
    if last_reinforced:
        try:
            lr_date = _parse_date(last_reinforced)
            days_ago = (datetime.now() - lr_date).days
            recency = max(0.0, 1.0 - days_ago / 30.0)
        except Exception:
            pass
    factors["temporal_recency"] = recency
    score += weights["temporal_recency"] * recency

    # ── 社交表达惩罚 ──
    da = context.get("dialogue_act", "")
    social_das = {"emotion_expression", "greeting", "farewell", "backchannel"}
    if da in social_das:
        penalty = sm_config.get("social_dialogue_weight_penalty", 0.4)
        score *= (1.0 - penalty)
        factors["social_penalty"] = penalty

    # ── 单次观察降权 ──
    source = candidate.get("source", "")
    if source == "single_observation" and repeat < sm_config.get("min_observations", 2):
        score *= 0.5
        factors["single_observation_penalty"] = 0.5

    score = round(min(1.0, max(0.0, score)), 4)
    passed = score >= threshold

    # 学习闭环 trace（§三）：importance 决策全程可追踪，
    # 让人能回答"这条 candidate 死在哪一步"——而不是只看到"0 个偏好"。
    logger.info(
        "[SelfModel] importance %s: type=%s target=%s source=%s "
        "score=%.3f passed=%s threshold=%.2f factors=%s" % (
            "✓通过" if passed else "✗驳回",
            candidate.get("type"), candidate.get("target") or candidate.get("content"),
            candidate.get("source", "?"), score, passed, threshold,
            {k: round(v, 3) for k, v in factors.items() if isinstance(v, (int, float))}))
    if not passed:
        missing = []
        if not context.get("is_explicit"):
            missing.append("非明确自我陈述")
        if context.get("repeat_count", 0) < 5:
            missing.append("重复次数不足")
        if not context.get("emotion_active"):
            missing.append("无情绪在场")
        logger.info(f"[SelfModel] importance 驳回原因: {missing or '分数不足'}")

    return {"score": score, "passed": passed, "factors": factors, "threshold": threshold}


# ═══════════════════════════════════════════════════════════════
# SelfMemoryUpdater
# ═══════════════════════════════════════════════════════════════

class SelfMemoryUpdater:
    """编排器：接收 NLP 输出 → 评估 → 决策 → 写入 Self Graph。

    每次对话轮次（用户输入→系统响应）后调用一次。
    """

    def __init__(self, kg: KnowledgeGraph, config: dict):
        self.kg = kg
        self.config = config
        self.manager = SelfGraphManager(kg)

    def process_turn(
        self,
        user_text: str,
        parsed_nlp: dict,
        memory_draft: dict = None,
        emotion_context: dict = None,
        topk_nodes: list = None,
    ) -> dict:
        """处理一轮对话，评估并更新 Self Model。

        Returns:
            {"updates_applied": [...], "updates_deferred": [...], "user_rel_updated": bool}
        """
        applied = []
        deferred = []

        # ── 1. 互动计数 ──
        self.manager.increment_interaction()

        # ── 2. 检测明确陈述 ──
        is_explicit = _detect_explicit_statement(user_text)
        da = parsed_nlp.get("dialogue_act", "")
        illoc = parsed_nlp.get("illocutionary_act", "")

        # ── 3. 获取情绪上下文 ──
        emotions = []
        if emotion_context:
            emotions = emotion_context.get("active_emotions", [])
        else:
            with self.kg._lock:
                for nid, node in self.kg.nodes.items():
                    if (node.extra_attrs.get("type") == "emotion"
                            and node.activation > 0.1):
                        emotions.append(nid)

        # ── 4. 从 memory_draft 提取 user-centric 候选 ──
        if memory_draft and memory_draft.get("nodes"):
            candidates = _extract_self_candidates(memory_draft, user_text, self.kg)
        else:
            candidates = []
        # 学习闭环 trace（§三）：候选提取环节全程可见。
        # 0 候选不是 bug——是 P0-7 的设计后果：只有 FAS 第一人称主语的边
        # （"我喜欢…""我是…"）才算自我模型的证据，用户谈论 X 不会变成 FAS
        # 偏好 X。把这一事实打在日志里，避免"0 产出"被误读为"写入被吞"。
        if is_explicit:
            logger.info(f"[SelfModel] trace: 明确自我陈述检出 text={user_text[:40]!r}")
        if not candidates:
            _edges = (memory_draft or {}).get("edges", [])
            _self_src = [e for e in _edges
                         if str(e.get("src", "")).strip() in ("Fascinator", SELF_ID)]
            logger.debug(
                f"[SelfModel] trace: 本轮无自我候选 "
                f"(memory_draft 边={len(_edges)}，其中 FAS 主语边={len(_self_src)}；"
                f"来源稀疏为设计约束，非门槛问题)")
        else:
            logger.info(
                f"[SelfModel] trace: 候选 ×{len(candidates)} "
                f"{[(c.get('type'), c.get('target') or c.get('content'), c.get('source')) for c in candidates]}")

        # ── 5. 对每个候选评估并决策 ──
        for cand in candidates:
            # 检查是否已在 Self Graph 中
            existing = self._find_existing(cand)

            context = {
                "is_explicit": is_explicit,
                "emotion_active": emotions,
                "repeat_count": existing.get("evidence_count", 0) + 1 if existing else 0,
                "dialogue_act": da,
                "llm_confidence": cand.get("confidence", 0.5),
            }

            if existing:
                # 强化已有（第二次及以后的出现才写入——"一次经历只产生
                # candidate 证据"在 Self Model 侧的对应：首次被门槛挡下并留
                # trace，重复出现才落图。不是"第一次就改变稳定自我认知"）
                logger.info(
                    f"[SelfModel] trace: 已有条目强化 {existing['node_id']} "
                    f"(evidence={existing.get('evidence_count')}, "
                    f"confidence={existing.get('confidence')})")
                self._reinforce_existing(existing, cand, context, applied)
            else:
                # 新候选 → 评估
                result = evaluate_importance(cand, context, self.config)
                if result["passed"]:
                    self._apply_candidate(cand, context, applied)
                else:
                    deferred.append({
                        "candidate": cand,
                        "score": result["score"],
                        "factors": result["factors"],
                        "reason": f"score={result['score']:.2f} < threshold={result['threshold']}",
                    })

        # ── 6. 更新关注领域 ──
        if candidates:
            for cand in candidates:
                target = cand.get("target", "")
                if target and not target.startswith("用户"):
                    self.manager.add_attention_area(target)

        return {
            "updates_applied": applied,
            "updates_deferred": deferred,
            "user_rel_updated": True,
        }

    def _find_existing(self, candidate: dict) -> dict:
        """查找 Self Graph 中与候选匹配的已有节点。"""
        ctype = candidate.get("type", "preference")
        target = candidate.get("target", "")
        content = candidate.get("content", "")

        if ctype == "preference":
            node = self.kg.get_node(f"偏好: {target}")
            if node:
                return {"node_id": node.id, "type": ctype,
                        "evidence_count": node.extra_attrs.get("evidence_count", 0),
                        "confidence": node.confidence}
        elif ctype == "belief":
            node = self.kg.get_node(f"信念: {content}")
            if node:
                return {"node_id": node.id, "type": ctype,
                        "evidence_count": node.extra_attrs.get("evidence_count", 0),
                        "confidence": node.confidence}
        return {}

    def _reinforce_existing(self, existing: dict, candidate: dict,
                            context: dict, applied: list):
        """强化已有 Self 条目"""
        ctype = existing["type"]
        provenance = {
            "confidence": existing.get("confidence", 0.55),
            "source": candidate.get("source", "multiple_experiences"),
            "evidence_count": 1,
            "first_observed": candidate.get("first_observed", ""),
            "last_reinforced": now_str(),
        }
        if ctype == "preference":
            result = self.manager.upsert_preference(
                candidate.get("target", ""), provenance
            )
        else:
            result = self.manager.upsert_belief(
                candidate.get("content", ""), provenance
            )
        applied.append({"action": result["action"], **candidate})

    # ── Phase 2: Reflection candidate processing ──────────

    def process_reflection_candidates(self, candidates: list,
                                       reflection_id: str = "") -> dict:
        """处理反思产生的候选更新列表。

        每条候选经过 importance 评估 → threshold gating。
        过门槛的标记为 approved（若 importance >= auto_approve_threshold）或 pending。
        未过门槛的标记为 deferred。

        Returns:
            {"approved": [...], "pending": [...], "deferred": [...]}
        """
        sm_config = self.config.get("self_model", {})
        auto_threshold = self.config.get("reflection", {}).get("auto_approve_threshold", 0.75)

        approved = []
        pending = []
        deferred = []

        for cand in candidates:
            ctype = cand.get("type", "belief_update")
            content = cand.get("content", cand.get("target", ""))
            confidence = cand.get("confidence", 0.5)

            # 构建评估上下文
            context = {
                "is_explicit": cand.get("source") == "explicit_statement",
                "emotion_active": [],
                "repeat_count": 1,
                "dialogue_act": "reflection",
                "llm_confidence": confidence,
            }

            source = cand.get("source", "llm_inference")
            eval_cand = {
                "type": "preference" if ctype == "preference_update" else "belief",
                "target": content if ctype == "preference_update" else "",
                "content": content if ctype == "belief_update" else "",
                "source": source,
                "confidence": confidence,
            }

            result = evaluate_importance(eval_cand, context, self.config)

            if result["passed"]:
                importance = result["score"]
                status = "approved" if importance >= auto_threshold else "pending"

                if ctype == "belief_update":
                    prov = {
                        "confidence": confidence, "source": source,
                        "evidence_count": len(cand.get("evidence", [])),
                        "first_observed": now_str(), "last_reinforced": now_str(),
                    }
                    self.manager.upsert_belief(content, prov)
                elif ctype == "preference_update":
                    prov = {
                        "confidence": confidence, "source": source,
                        "evidence_count": len(cand.get("evidence", [])),
                        "first_observed": now_str(), "last_reinforced": now_str(),
                    }
                    self.manager.upsert_preference(content, prov)

                entry = {"candidate": cand, "importance": importance,
                        "approval_status": status, "reflection_id": reflection_id}
                if status == "approved":
                    approved.append(entry)
                else:
                    pending.append(entry)
            else:
                deferred.append({
                    "candidate": cand,
                    "score": result["score"],
                    "reason": f"score={result['score']:.2f} < threshold={result['threshold']}",
                })

        return {"approved": approved, "pending": pending, "deferred": deferred}

    def _apply_candidate(self, candidate: dict, context: dict, applied: list):
        """创建新的 Self 条目"""
        ctype = candidate.get("type", "preference")
        source = (
            "explicit_statement" if context.get("is_explicit")
            else candidate.get("source", "single_observation")
        )
        provenance = {
            "confidence": candidate.get("confidence", 0.55),
            "source": source,
            "evidence_count": 1,
            "first_observed": candidate.get("first_observed", now_str()),
            "last_reinforced": now_str(),
        }
        if ctype == "preference":
            self.manager.upsert_preference(candidate.get("target", ""), provenance)
        elif ctype == "belief":
            self.manager.upsert_belief(candidate.get("content", ""), provenance)
        applied.append({"action": "created", **candidate})


# ═══════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════

def _validate_provenance(provenance: dict):
    """验证 provenance 包含必须字段。"""
    for field in REQUIRED_PROVENANCE:
        if field not in provenance:
            raise ValueError(
                f"Self Model provenance 缺少必须字段: '{field}'。"
                f"所有 Belief/Preference 必须携带 source/confidence/evidence_count/"
                f"first_observed/last_reinforced。"
            )
    if provenance.get("source") not in VALID_SOURCES:
        raise ValueError(
            f"无效的 source: '{provenance.get('source')}'。"
            f"有效值: {VALID_SOURCES}"
        )


def _detect_explicit_statement(text: str) -> bool:
    """检测用户文本是否包含明确的自我陈述。"""
    for pat in EXPLICIT_STATEMENT_PATTERNS:
        if pat.search(text):
            return True
    return False


def _extract_self_candidates(memory_draft: dict, user_text: str,
                             kg: KnowledgeGraph) -> list:
    """从 memory_draft 中提取 Self-relevant 候选更新。

    P0-7：只接受第一人称（Fascinator/Self）视角的证据。
    原实现把 用户→X 的边当作 FAS 的偏好候选、用户是X 当作信念候选
    ——自我模型与用户模型混淆（"用户喜欢奶茶"会变成 FAS 的
    偏好:奶茶，产出"我喜欢奶茶"的虚假自我认知）。
    对用户的认知本来就以 用户 节点为中心活在语义图里，需要时查图，
    不复制进自我模型。
    """
    candidates = []
    edges = memory_draft.get("edges", [])

    _SELF_SOURCES = ("Fascinator", SELF_ID)

    for edge in edges:
        src = str(edge.get("src", "")).strip()
        dst = str(edge.get("dst", "")).strip()
        rel = str(edge.get("type", edge.get("relation", ""))).strip()
        weight = float(edge.get("weight", 0.5))

        if not src or not dst or not rel:
            continue

        # 只接受 FAS 自己主语（Self/Fascinator）的边
        if src not in _SELF_SOURCES:
            continue
        # 指向自己的自环不构成关于自我的新认知
        if dst in _SELF_SOURCES or dst == "用户":
            continue

        # 偏好类关系 → preference 候选
        if rel in PREFERENCE_RELATIONS:
            candidates.append({
                "type": "preference",
                "target": dst,
                "relation": rel,
                "confidence": abs(weight),
                "source": _infer_source(user_text, weight),
                "first_observed": now_str(),
            })

        # 身份/类属 → belief 候选（第一人称表述）
        if rel in ("是", "属于", "成为"):
            candidates.append({
                "type": "belief",
                "content": f"我是{dst}",
                "confidence": weight,
                "source": _infer_source(user_text, weight),
                "first_observed": now_str(),
            })

    return candidates


def _infer_source(text: str, weight: float) -> str:
    """推断候选的 source 类型。"""
    if _detect_explicit_statement(text):
        return "explicit_statement"
    if abs(weight) >= 0.9:
        return "single_observation"
    return "llm_inference"


def _parse_date(date_str: str) -> datetime:
    """解析日期字符串。"""
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return datetime.now()
