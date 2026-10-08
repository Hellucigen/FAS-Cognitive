# action_engine.py — Action System: 认知层 Capability
# ============================================================================
# FAS Phase 4 MVP: Cognitive Action System。
#
# 设计原则（最高优先级）：
#   1. Capability 触发条件全部来自图谱状态 — 不硬编码 if-else
#   2. ActionIntent 是内部状态 — 不自动产生对外行为
#   3. 执行结果写入 Episodic Graph — Reflection → Self Model → Drive → Action 闭环
#   4. 认知 Capability ≠ 按键 Primitive — 这是"应不应该做"的判断层
# ============================================================================

import logging
import time
from typing import Optional

from graph_model import KnowledgeGraph, Node, Edge, now_str

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Cognitive Capability 定义
# ═══════════════════════════════════════════════════════════════

COGNITIVE_CAPABILITIES = [
    {
        "id": "Capability_Respond",
        "name": "Respond",
        "description": "回应用户的消息——基于图谱扩散后的认知焦点生成自然语言回答",
        "requirements": ["用户输入存在"],
        "consumes": ["user_input", "topk_activation"],
        "produces": "llm_answer",
        "success_condition": "用户继续对话（非错误/空回复）",
        "status": "acquired",
    },
    {
        "id": "Capability_Ask",
        "name": "Ask",
        "description": "主动向用户提问——探索行为（explore_ask）在统一行为竞争中胜出后的执行记录",
        "requirements": ["行为竞争选出 explore_ask（CuriosityDrive 高 + 候选胜出）"],
        "consumes": ["exploration_decision", "curiosity_drive", "discrepancy_signals"],
        "produces": "action_intent",
        "success_condition": "用户回答了探索提问（答非所问也算合法结果，兴趣保留）",
        "status": "acquired",
    },
    {
        "id": "Capability_Record",
        "name": "Record",
        "description": "记录重要经历——将 Reflection 批准的候选归档为经历摘要",
        "requirements": ["Reflection 有 approved 候选"],
        "consumes": ["reflection_approved"],
        "produces": "episodic_summary",
        "success_condition": "经历摘要节点成功创建并连接 Self",
        "status": "acquired",
    },
    {
        "id": "Capability_Suggest",
        "name": "Suggest",
        "description": "主动建议——基于 Self Model 中的用户偏好和当前驱动力",
        "requirements": [
            "Self Model 中有 confidence >= 0.6 的 preference",
            "有活跃的 drive (any drives.activation > 0)"
        ],
        "consumes": ["self_model_preferences", "drive_state"],
        "produces": "action_intent",
        "success_condition": "用户对建议有回应（无论正负）",
        "status": "acquired",
    },
]


class ActionSelector:
    """认知层 Capability 选择器。

    读取图谱状态（Drive、Self Model、Reflection 产出），
    匹配 Capability requirements，产生 ActionIntent。
    """

    def __init__(self, kg: KnowledgeGraph, config: dict,
                 drive_evaluator=None, self_updater=None):
        self.kg = kg
        self.config = config
        self.drive_evaluator = drive_evaluator
        self.self_updater = self_updater
        self._last_eval_time = 0.0
        self._active_intents: list = []

    # ── 核心评估 ──────────────────────────────────────────

    def evaluate(self, force: bool = False,
                 user_input: str = None,
                 exploration: dict = None) -> dict:
        """评估所有 Capability，返回 ActionIntent 列表。

        exploration: 本轮行为竞争的探索结果（app.py 传入）。
            Ask 不再独立触发——只有竞争真的选出了 explore_ask /
            explore_search，这里才把它记录为 ActionIntent。
            没有探索胜出 = 没有 Ask intent（好奇但不行动是合法状态）。

        Returns:
            {"intents": [...], "selected": {...} or None,
             "capabilities": [...]}
        """
        now = time.time()
        if not force and now - self._last_eval_time < 3.0 and not user_input:
            return {"intents": self._active_intents,
                    "selected": self._active_intents[0] if self._active_intents else None,
                    "capabilities": self._get_capability_status()}

        intents = []

        # ── Check Ask（消费竞争结果，不独立触发）──
        ask_intent = self._check_ask(exploration)
        if ask_intent:
            intents.append(ask_intent)

        # ── Check Suggest ──
        suggest_intent = self._check_suggest()
        if suggest_intent:
            intents.append(suggest_intent)

        # ── Check Record ──
        record_intent = self._check_record()
        if record_intent:
            intents.append(record_intent)

        # 按置信度排序
        intents.sort(key=lambda i: i.get("confidence", 0), reverse=True)
        self._active_intents = intents
        self._last_eval_time = now

        return {
            "intents": intents,
            "selected": intents[0] if intents else None,
            "capabilities": self._get_capability_status(),
        }

    # ── Capability Checkers ────────────────────────────────

    def _check_ask(self, exploration: dict = None) -> Optional[dict]:
        """把本轮行为竞争的探索结果记录为 Ask intent。

        旧版这里独立判断 CuriosityDrive ≥ 1.5 就产生 intent——那是与
        dialogue_decide/autonomy 并行的第三套决策旁路，且产物没有执行器
        消费。现在 Ask 的触发权完全属于统一行为竞争：本函数只做记录。
        """
        if not exploration or not exploration.get("action"):
            return None

        action = str(exploration.get("action"))
        target = str(exploration.get("target") or "")
        cd_node = self.kg.get_node("CuriosityDrive")
        drive_act = float(getattr(cd_node, "activation", 0.0) or 0.0) if cd_node else 0.0

        # 收集活跃的 discrepancy 信号节点（留痕用）
        unknown_signals = []
        for nid in ["未知概念", "未知关系", "未知属性", "未知事件", "未知信息"]:
            node = self.kg.get_node(nid)
            if node and node.activation > 0.01:
                unknown_signals.append({"id": nid, "activation": round(node.activation, 4)})

        return {
            "capability": "Ask",
            "capability_id": "Capability_Ask",
            "confidence": round(min(0.9, 0.5 + drive_act * 0.1), 3),
            "trigger_reason": (
                f"行为竞争选出 {action}（目标={target}，"
                f"CuriosityDrive={drive_act:.2f}，"
                f"{len(unknown_signals)} 个信号节点活跃）"
            ),
            "target_nodes": unknown_signals,
            "exploration_action": action,
            "exploration_target": target,
            "exploration_question": exploration.get("question"),
            "exploration_executed": bool(exploration.get("executed")),
            "timestamp": now_str(),
        }

    def _check_suggest(self) -> Optional[dict]:
        """检查 Suggest Capability 是否应该触发。

        条件：Self Model 中有 confidence >= 0.6 的 preference
             + 有活跃 drive
        """
        # 检查 Self Model preferences
        high_conf_prefs = []
        with self.kg._lock:
            for edge in self.kg.edges:
                if edge.src == "Self" and edge.relation in ("偏好", "喜欢"):
                    node = self.kg.get_node(edge.dst)
                    if node and node.confidence >= 0.6:
                        high_conf_prefs.append({
                            "id": node.id,
                            "target": node.extra_attrs.get("target", node.id),
                            "confidence": node.confidence,
                        })

        if not high_conf_prefs:
            return None

        # 检查是否有活跃 drive
        cd_node = self.kg.get_node("CuriosityDrive")
        has_active_drive = cd_node and cd_node.activation > 0.1

        if not has_active_drive:
            return None

        confidence = min(0.85, 0.5 + len(high_conf_prefs) * 0.1)
        return {
            "capability": "Suggest",
            "capability_id": "Capability_Suggest",
            "confidence": round(confidence, 3),
            "trigger_reason": (
                f"有 {len(high_conf_prefs)} 个高置信度偏好，"
                f"CuriosityDrive={cd_node.activation:.2f}"
            ),
            "target_preferences": high_conf_prefs,
            "timestamp": now_str(),
        }

    def _check_record(self) -> Optional[dict]:
        """检查 Record Capability 是否应该触发。

        条件：有 Reflection 批准的候选（反思节点中 approved_count > 0）
        """
        approved_reflections = []
        with self.kg._lock:
            for nid, node in self.kg.nodes.items():
                if not nid.startswith("反思_"):
                    continue
                extra = node.extra_attrs or {}
                if extra.get("type") != "reflection":
                    continue
                if extra.get("approved_count", 0) > 0:
                    approved_reflections.append({
                        "id": nid,
                        "created": node.created,
                        "approved_count": extra["approved_count"],
                        "what_happened": extra.get("what_happened", "")[:100],
                    })

        if not approved_reflections:
            return None

        latest = approved_reflections[-1]
        return {
            "capability": "Record",
            "capability_id": "Capability_Record",
            "confidence": 0.7,
            "trigger_reason": (
                f"最近反思 {latest['id']} 有 {latest['approved_count']} 条批准候选"
            ),
            "source_reflections": approved_reflections[-3:],
            "timestamp": now_str(),
        }

    # ── Capability 状态 ───────────────────────────────────

    def _get_capability_status(self) -> list:
        """获取所有 Capability 节点的当前状态。"""
        status = []
        for cap_def in COGNITIVE_CAPABILITIES:
            node = self.kg.get_node(cap_def["id"])
            cap_info = dict(cap_def)
            cap_info["node_exists"] = node is not None
            if node:
                cap_info["node_activation"] = round(node.activation, 4)
            status.append(cap_info)
        return status

    # ── Bootstrap ─────────────────────────────────────────

    def bootstrap_capabilities(self):
        """创建认知 Capability 节点（幂等；旧节点 type 就地归一）。

        统一 Capability 层（2026-09-20）：type 归一为 capability、
        channel=cognitive 标域——本 4 节点保留为**留痕/展示视图**
        （裁决在行为竞争、执行接口在 行为:* 概念→能力:言语表达 链上），
        不再扩建（docs fas_unwanted §6 判词生效）。
        """
        for cap_def in COGNITIVE_CAPABILITIES:
            cap_id = cap_def["id"]
            node = self.kg.get_node(cap_id)
            if node is None:
                node = Node(
                    id=cap_id, weight=0.5, label="declarative-semantic",
                    graph_space="cognitive",
                    extra_attrs={
                        "type": "capability", "channel": "cognitive",
                        "executors": [],
                        "name": cap_def["name"],
                        "description": cap_def["description"],
                        "requirements": cap_def["requirements"],
                        "consumes": cap_def["consumes"],
                        "produces": cap_def["produces"],
                        "success_condition": cap_def["success_condition"],
                        "status": cap_def["status"],
                    }
                )
                self.kg.add_node(node)
                self.kg.add_edge(Edge(
                    src="Self", dst=cap_id, relation="能力",
                    weight=0.6, relation_category="cognitive_relation"
                ))
                logger.info(f"[Action] + Capability 节点: {cap_id}")
            else:
                ea = dict(node.extra_attrs or {})
                if ea.get("type") == "cognitive_capability":
                    ea["type"] = "capability"
                    ea.setdefault("channel", "cognitive")
                    ea.setdefault("executors", [])
                    node.extra_attrs = ea

        logger.info(f"[Action] {len(COGNITIVE_CAPABILITIES)} 个认知 Capability 已就绪")

    # ── Intent 持久化 ─────────────────────────────────────

    def persist_intent(self, intent: dict) -> str:
        """将 ActionIntent 持久化为图谱节点。"""
        import time as _time
        cap_name = intent.get("capability", "Unknown")
        intent_id = f"ActionIntent_{cap_name}_{int(_time.time())}"

        node = Node(
            id=intent_id, weight=0.4, label="declarative-episodic",
            graph_space="cognitive",
            extra_attrs={
                "type": "action_intent",
                "capability": cap_name,
                "confidence": intent.get("confidence", 0.5),
                "trigger_reason": intent.get("trigger_reason", ""),
                "target_nodes": intent.get("target_nodes", []),
                "timestamp": intent.get("timestamp", now_str()),
            }
        )
        self.kg.add_node(node)
        self.kg.add_edge(Edge(
            src="Self", dst=intent_id, relation="意图",
            weight=0.4, relation_category="cognitive_relation"
        ))

        # 连接 Capability 节点
        cap_id = intent.get("capability_id", "")
        if cap_id and self.kg.get_node(cap_id):
            self.kg.add_edge(Edge(
                src=cap_id, dst=intent_id, relation="产生",
                weight=0.5, relation_category="cognitive_relation"
            ))

        logger.info(f"[Action] ActionIntent 已持久化: {intent_id} ({cap_name})")
        return intent_id

    # ── 查询接口 ──────────────────────────────────────────

    def get_active_intents(self) -> list:
        """获取当前活跃的 ActionIntent。"""
        self.evaluate()
        return self._active_intents

    def get_state(self) -> dict:
        """获取完整 Action System 状态。"""
        eval_result = self.evaluate()
        cap_nodes = []
        with self.kg._lock:
            for nid, node in self.kg.nodes.items():
                if nid.startswith("Capability_"):
                    cap_nodes.append({
                        "id": nid,
                        "name": node.extra_attrs.get("name", nid),
                        "status": node.extra_attrs.get("status", "unknown"),
                        "activation": round(node.activation, 4),
                    })
            # 最近的 ActionIntent
            recent_intents = []
            for nid, node in self.kg.nodes.items():
                if nid.startswith("ActionIntent_"):
                    recent_intents.append({
                        "id": nid,
                        "capability": node.extra_attrs.get("capability", ""),
                        "confidence": node.extra_attrs.get("confidence", 0),
                        "reason": node.extra_attrs.get("trigger_reason", ""),
                        "created": node.created,
                    })
            recent_intents.sort(key=lambda i: i.get("created", ""), reverse=True)

        return {
            "capabilities": cap_nodes,
            "active_intents": eval_result.get("intents", []),
            "recent_intents": recent_intents[:10],
            "selected": eval_result.get("selected"),
        }
