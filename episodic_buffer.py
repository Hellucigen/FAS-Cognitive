# episodic_buffer.py - 情景缓冲区模块
# ============================================================================
# 介于 Working Memory (扩散激活) 和 Long-term Memory (永久图谱) 之间的
# 短期经历存储层。
#
# 基于 Baddeley (2000) 的情景缓冲区理论：
#   - 存储用户输入的原始文本、提取的节点/边、断言类型
#   - 支持遗忘机制（容量超额时按重要性+访问频率淘汰）
#   - 支持晋升机制（高频访问/高重要性的经历可晋升为长期记忆）
#   - 提供按时间/关键词/最近N条等维度的检索
# ============================================================================

import threading
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class EpisodicExperience:
    """单条情景经历"""

    def __init__(self, raw_text: str, nodes: list, edges: list,
                 assertion_type: str = "episodic", timestamp: str = None):
        self.raw_text = raw_text
        self.nodes = nodes        # [{"id":"用户","node_type":"实体"}, ...]
        self.edges = edges        # [{"src":"用户","dst":"考试","type":"参加","weight":1.0,"reason":"..."}, ...]
        self.assertion_type = assertion_type
        self.timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.importance = 0.5       # 初始重要度 0~1
        self.access_count = 0       # 被引用次数
        self.activation_count = 0   # 扩散中被激活次数
        self.promoted = False       # 是否已晋升长期记忆
        self.promoted_at = None

    def to_dict(self):
        return {
            "raw_text": self.raw_text,
            "nodes": self.nodes,
            "edges": self.edges,
            "assertion_type": self.assertion_type,
            "timestamp": self.timestamp,
            "importance": round(self.importance, 3),
            "access_count": self.access_count,
            "activation_count": self.activation_count,
            "promoted": self.promoted,
            "promoted_at": self.promoted_at
        }


class EpisodicBuffer:
    """情景缓冲区"""

    def __init__(self, capacity: int = 100,
                 promote_threshold_access: int = 3,
                 promote_threshold_importance: float = 0.7,
                 promote_threshold_activation: int = 5):
        self.capacity = capacity
        self.promote_threshold_access = promote_threshold_access
        self.promote_threshold_importance = promote_threshold_importance
        self.promote_threshold_activation = promote_threshold_activation

        self._experiences: list[EpisodicExperience] = []
        self._forgotten: list[dict] = []  # 被遗忘经历的记录（保留摘要）
        self._promotion_log: list[dict] = []
        # Reflection Evolution R1: FAS 表达事件（每轮一条，不落图，仅供反思）
        self._expressions: list[dict] = []
        self._lock = threading.RLock()

    # ── 基本操作 ──────────────────────────────────────────

    def add_experience(self, raw_text: str, nodes: list, edges: list,
                       assertion_type: str = "episodic") -> EpisodicExperience:
        """添加一条新经历，超额时自动触发遗忘"""
        with self._lock:
            exp = EpisodicExperience(
                raw_text=raw_text, nodes=nodes, edges=edges,
                assertion_type=assertion_type
            )
            self._experiences.append(exp)

            logger.info(
                f"[EpisodicBuffer] Added Experience: "
                f"\"{raw_text[:50]}...\" ({len(nodes)} nodes, {len(edges)} edges)"
            )

            # 超额 → 遗忘
            if len(self._experiences) > self.capacity:
                self._forget_overflow()

            return exp

    # ── 遗忘机制 ──────────────────────────────────────────

    def _forget_overflow(self):
        """删除最不重要/最久未访问的经历"""
        excess = len(self._experiences) - self.capacity
        if excess <= 0:
            return

        # 排序：重要性低 + 访问少 + 最久未访问 → 优先删除
        candidates = sorted(
            self._experiences,
            key=lambda e: (e.importance, e.access_count, e.timestamp)
        )
        to_remove = candidates[:excess]

        for exp in to_remove:
            self._forgotten.append({
                "text": exp.raw_text[:80],
                "nodes": [n.get("id", "?") for n in exp.nodes[:5]],
                "assertion_type": exp.assertion_type,
                "timestamp": exp.timestamp,
                "forgotten_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "importance": exp.importance,
                "access_count": exp.access_count,
            })
            self._experiences.remove(exp)

            logger.info(
                f"[Forgetting] Removed: \"{exp.raw_text[:40]}...\" "
                f"(imp={exp.importance:.2f}, acc={exp.access_count})"
            )

    # ── 晋升机制 ──────────────────────────────────────────

    def check_promotion(self, experience: EpisodicExperience) -> bool:
        """检查一条经历是否满足晋升条件"""
        return (
            experience.access_count >= self.promote_threshold_access
            or experience.importance >= self.promote_threshold_importance
            or experience.activation_count >= self.promote_threshold_activation
        )

    def get_promotion_candidates(self) -> list:
        """获取所有符合条件的晋升候选项"""
        with self._lock:
            candidates = [
                exp for exp in self._experiences
                if not exp.promoted and self.check_promotion(exp)
            ]
            return sorted(candidates, key=lambda e: (
                e.importance + e.access_count * 0.1 + e.activation_count * 0.05
            ), reverse=True)

    def promote_to_long_term(self, experience: EpisodicExperience):
        """标记经历已晋升"""
        with self._lock:
            experience.promoted = True
            experience.promoted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._promotion_log.append({
                "text": experience.raw_text[:60],
                "promoted_at": experience.promoted_at,
                "importance": experience.importance,
                "access_count": experience.access_count,
            })

            logger.info(
                f"[Promotion] Promoted to Long-term Memory: "
                f"\"{experience.raw_text[:50]}...\""
            )

    def find_by_text(self, raw_text: str) -> Optional[EpisodicExperience]:
        """按原文查找经历"""
        with self._lock:
            for exp in self._experiences:
                if exp.raw_text.strip() == raw_text.strip():
                    return exp
        return None

    def find_by_node(self, node_id: str, max_n: int = 30) -> list:
        """按图节点 id 找出引用该节点的经历（只读检索，观测/计数用，不晋升）"""
        with self._lock:
            hits = [
                e for e in self._experiences
                if any(isinstance(n, dict) and n.get("id") == node_id for n in e.nodes)
            ]
            return hits[:max_n]

    def mark_accessed(self, experience: EpisodicExperience):
        """增加引用计数"""
        with self._lock:
            experience.access_count += 1

    # ── 检索接口 ──────────────────────────────────────────

    def retrieve_recent(self, n: int = 10) -> list:
        """获取最近 n 条经历（包含已晋升）"""
        with self._lock:
            return [
                exp.to_dict()
                for exp in self._experiences[-n:]
            ][::-1]

    def retrieve_related(self, keyword: str, n: int = 10) -> list:
        """按关键词检索相关经历（节点/边/原文包含该词）"""
        kw = keyword.strip().lower()
        if not kw:
            return self.retrieve_recent(n)

        results = []
        with self._lock:
            for exp in self._experiences:
                score = 0
                if kw in exp.raw_text.lower():
                    score += 5
                for nd in exp.nodes:
                    nid = str(nd.get("id", "")).lower()
                    if kw in nid:
                        score += 3
                    ntype = str(nd.get("node_type", "")).lower()
                    if kw in ntype:
                        score += 1
                for ed in exp.edges:
                    if kw in str(ed.get("type", "")).lower():
                        score += 2
                    if kw in str(ed.get("reason", "")).lower():
                        score += 1
                if score > 0:
                    results.append((score, exp.to_dict()))

        results.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in results[:n]]

    def retrieve_by_time(self, start: str, end: str = None, n: int = 50) -> list:
        """按时间范围检索经历"""
        if end is None:
            end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            return [
                exp.to_dict()
                for exp in self._experiences
                if start <= exp.timestamp <= end
            ][:n]

    # ── 维护 ──────────────────────────────────────────────

    def cleanup(self):
        """清理已晋升的旧经历（保留最近 20 条已晋升记录，其余从缓冲区移除但记录在 log 中）"""
        with self._lock:
            promoted = [e for e in self._experiences if e.promoted]
            if len(promoted) <= 20:
                return
            keep = promoted[-20:]
            for exp in promoted:
                if exp not in keep:
                    self._experiences.remove(exp)

            logger.info(f"[EpisodicBuffer] Cleanup: kept {len(keep)} promoted, removed {len(promoted)-len(keep)}")

    def clear(self):
        """清空所有经历（不删遗忘记录）"""
        with self._lock:
            self._experiences.clear()
            logger.info("[EpisodicBuffer] Cleared all experiences")

    # ── 状态查询 ──────────────────────────────────────────

    def size(self) -> int:
        return len(self._experiences)

    # ── Reflection Evolution R1: 表达事件（Expression Event）──

    def add_expression(self, expr: dict):
        """记录一轮 FAS 表达事件（behavior/context/topics/answer）。仅存内存。"""
        with self._lock:
            expr.setdefault("outcome", None)
            expr.setdefault("ts", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self._expressions.append(expr)
            if len(self._expressions) > self.capacity:
                self._expressions = self._expressions[-self.capacity:]

    def last_unannotated_expression(self):
        """最近一条尚无 outcome 的表达事件（无则 None）。"""
        with self._lock:
            for e in reversed(self._expressions):
                if e.get("outcome") is None:
                    return e
            return None

    def annotate_expression(self, expr: dict, outcome: str, detail: str = ""):
        with self._lock:
            expr["outcome"] = outcome
            expr["outcome_detail"] = detail
            expr["outcome_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def unreflected_expressions(self, n: int = 30) -> list:
        """已标注 outcome 且尚未被反思消费的表达事件。"""
        with self._lock:
            return [e for e in self._expressions
                    if e.get("outcome") is not None
                    and not e.get("reflected")][-n:]

    def mark_expressions_reflected(self, exprs: list):
        with self._lock:
            for e in exprs:
                e["reflected"] = True

    def get_expressions(self, n: int = 20) -> list:
        with self._lock:
            return list(self._expressions[-n:])

    def stats(self) -> dict:
        """统计信息"""
        with self._lock:
            total = len(self._experiences)
            promoted = sum(1 for e in self._experiences if e.promoted)
            return {
                "total_experiences": total,
                "promoted": promoted,
                "pending": total - promoted,
                "capacity": self.capacity,
                "usage_pct": round(total / self.capacity * 100, 1),
                "forgotten_count": len(self._forgotten),
                "promotion_log_count": len(self._promotion_log),
                "expressions": len(self._expressions),
            }

    def get_all(self) -> dict:
        """获取完整快照"""
        with self._lock:
            return {
                "stats": self.stats(),
                "recent": self.retrieve_recent(20),
                "promotion_candidates": [
                    e.to_dict() for e in self.get_promotion_candidates()[:10]
                ],
                "forgotten": self._forgotten[-20:],
            }
