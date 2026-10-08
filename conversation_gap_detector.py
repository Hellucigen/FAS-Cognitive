# conversation_gap_detector.py — Conversation Gap Detector
# ============================================================================
# ⚠ 状态（2026-09-22 收尾标注）：**未接线的离线手动工具**。生产链路的缺口
# 分析在 cognitive_demand（九维需求 → Gap → 资源调度），本类不在该链路上，
# 仅保留供人工实验/复盘使用；勿在此另立一套缺口语义。
#
# After each chat session, analyzes conversation for cognitive gaps:
#   - LLM had to fabricate answers (no graph support)
#   - Association chains failed (broken edges)
#   - No relevant nodes found (hole in graph)
#   - Diffusion stopped too early (sparse connections)
#   - Answers were repetitive (lack of topic breadth)
#
# Gaps are recorded as suggestions for human review, NOT auto-created.
# ============================================================================

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 收尾修复 2026-09-22：record_turn 之前无界增长（长会话=内存泄漏）；
# 环形上限，溢出丢最旧（分析只取近若干条，语义不变）。
_CONV_LOG_CAP = 200


class ConversationGap:
    """A single detected cognitive gap."""

    def __init__(self, gap_type: str, description: str,
                 suggested_nodes: list = None, suggested_edges: list = None,
                 context: str = ""):
        self.gap_type = gap_type
        self.description = description
        self.suggested_nodes = suggested_nodes or []
        self.suggested_edges = suggested_edges or []
        self.context = context
        self.timestamp = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        self.reviewed = False

    def to_dict(self) -> dict:
        return {
            "gap_type": self.gap_type,
            "description": self.description,
            "suggested_nodes": self.suggested_nodes,
            "suggested_edges": self.suggested_edges,
            "context": self.context[:200],
            "timestamp": self.timestamp,
            "reviewed": self.reviewed,
        }


class ConversationGapDetector:
    """Detects cognitive gaps in conversations."""

    def __init__(self):
        self.gaps = []
        self._conversation_log = []

    def record_turn(self, user_input: str, assistant_response: str,
                    topk_nodes: list = None, topk_edges: list = None,
                    fabrication_score: float = 0.0):
        self._conversation_log.append({
            "user": user_input, "assistant": assistant_response,
            "topk_count": len(topk_nodes) if topk_nodes else 0,
            "edge_count": len(topk_edges) if topk_edges else 0,
            "fabrication_score": fabrication_score,
            "timestamp": datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
        })
        if len(self._conversation_log) > _CONV_LOG_CAP:
            del self._conversation_log[:-_CONV_LOG_CAP]

    def analyze_session(self, kg, nlp_processor=None) -> list:
        if not self._conversation_log:
            return []
        gaps = []
        log = self._conversation_log

        sparse = [t for t in log if t.get("topk_count", 0) == 0]
        for t in sparse[-3:]:
            gaps.append(ConversationGap(
                gap_type="sparse_area",
                description=f"No active nodes for: '{t['user'][:60]}'",
                context=t["user"],
                suggested_nodes=self._extract_keywords(t["user"])
            ))

        no_edges = [t for t in log if t.get("topk_count", 0) > 0 and t.get("edge_count", 0) == 0]
        for t in no_edges[-3:]:
            gaps.append(ConversationGap(
                gap_type="broken_link",
                description=f"Nodes present but no edges: '{t['user'][:60]}'",
                context=t["user"],
                suggested_edges=[{"relation": "会想到", "reason": "Missing association"}]
            ))

        fab = [t for t in log if t.get("fabrication_score", 0) > 0.5]
        for t in fab[-3:]:
            gaps.append(ConversationGap(
                gap_type="fabrication",
                description=f"Possible fabrication: '{t['user'][:60]}'",
                context=t["user"],
                suggested_nodes=self._extract_keywords(t["user"])
            ))

        responses = [t.get("assistant", "") for t in log]
        unique = set(r[:80] for r in responses if r)
        if len(unique) < len(responses) * 0.5 and len(responses) > 3:
            gaps.append(ConversationGap(
                gap_type="repetition",
                description="Answers repetitive - need topic variety",
                suggested_nodes=["转移话题", "继续深入"]
            ))

        self.gaps.extend(gaps)
        self._conversation_log = []
        logger.info(f"[GapDetector] Found {len(gaps)} gaps")
        return [g.to_dict() for g in gaps]

    def _extract_keywords(self, text: str) -> list:
        words = set(text.replace("？"," ").replace("，"," ").replace("。"," ").split())
        stop = {"的","了","是","在","我","你","他","她","不","这","那","有","和","就","都","也","要","会","能","吗","呢","啊"}
        return [w for w in words if len(w) >= 2 and w not in stop][:5]

    def get_pending(self) -> list:
        return [g.to_dict() for g in self.gaps if not g.reviewed]

    def mark_reviewed(self, gap_index: int):
        if 0 <= gap_index < len(self.gaps):
            self.gaps[gap_index].reviewed = True

    def clear(self):
        self.gaps = []
        self._conversation_log = []

    def stats(self) -> dict:
        types = {}
        for g in self.gaps:
            types[g.gap_type] = types.get(g.gap_type, 0) + 1
        return {"total_gaps": len(self.gaps), "pending": sum(1 for g in self.gaps if not g.reviewed), "by_type": types}
