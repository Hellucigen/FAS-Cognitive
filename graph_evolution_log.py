# graph_evolution_log.py — 图谱演化日志
# ============================================================================
# Fascinator "Everything is Graph" 核心组件。
#
# 设计原则：
#   成长不是变量递增，而是图结构发生改变。
#   新增节点、新增边、边权改变、节点新增、知识加强 —— 都属于成长。
# ============================================================================

import threading
from datetime import datetime
from enum import Enum
from typing import Optional


class ChangeType(Enum):
    NODE_ADDED = "node_added"
    NODE_REMOVED = "node_removed"
    NODE_UPDATED = "node_updated"
    EDGE_ADDED = "edge_added"
    EDGE_REMOVED = "edge_removed"
    EDGE_UPDATED = "edge_updated"
    WEIGHT_CHANGED = "weight_changed"
    CONFIDENCE_CHANGED = "confidence_changed"
    ACTIVATION_SPIKE = "activation_spike"


class EvolutionEntry:
    """单条演化记录"""

    def __init__(self, change_type: ChangeType, target_type: str,
                 target_id: str, details: dict = None, timestamp: str = None):
        self.change_type = change_type
        self.target_type = target_type
        self.target_id = target_id
        self.details = details or {}
        self.timestamp = timestamp or datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    def to_dict(self) -> dict:
        return {
            "change_type": self.change_type.value,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "details": self.details,
            "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvolutionEntry":
        ct_raw = d.get("change_type", "node_added")
        try:
            ct = ChangeType(ct_raw)
        except ValueError:
            ct = ChangeType.NODE_ADDED
        return cls(change_type=ct, target_type=d.get("target_type", "node"),
                   target_id=d.get("target_id", ""),
                   details=d.get("details", {}), timestamp=d.get("timestamp"))


class GraphEvolutionLog:
    """图谱演化日志 —— 记录"成长"的证据"""

    def __init__(self, max_entries: int = 10000):
        self.entries: list[EvolutionEntry] = []
        self.max_entries = max_entries
        self._lock = threading.RLock()

    def record(self, change_type: ChangeType, target_type: str,
               target_id: str, details: dict = None):
        with self._lock:
            entry = EvolutionEntry(change_type=change_type, target_type=target_type,
                                   target_id=target_id, details=details or {})
            self.entries.append(entry)
            if len(self.entries) > self.max_entries:
                self.entries = self.entries[-self.max_entries:]

    def note_node_added(self, node_id: str, label: str = "", weight: float = 0.0):
        self.record(ChangeType.NODE_ADDED, "node", node_id,
                    {"label": label, "weight": weight})

    def note_node_removed(self, node_id: str):
        self.record(ChangeType.NODE_REMOVED, "node", node_id)

    def note_node_updated(self, node_id: str, changes: dict):
        self.record(ChangeType.NODE_UPDATED, "node", node_id, changes)

    def note_edge_added(self, src: str, dst: str, relation: str, weight: float = 0.5):
        self.record(ChangeType.EDGE_ADDED, "edge", f"{src}→{dst}",
                    {"src": src, "dst": dst, "relation": relation, "weight": weight})

    def note_edge_removed(self, src: str, dst: str, relation: str):
        self.record(ChangeType.EDGE_REMOVED, "edge", f"{src}→{dst}",
                    {"src": src, "dst": dst, "relation": relation})

    def note_weight_changed(self, target_type: str, target_id: str,
                            old_weight: float, new_weight: float, reason: str = ""):
        self.record(ChangeType.WEIGHT_CHANGED, target_type, target_id,
                    {"old_weight": round(old_weight, 6),
                     "new_weight": round(new_weight, 6), "reason": reason})

    def note_confidence_changed(self, node_id: str, old_conf: float,
                                new_conf: float, reason: str = ""):
        self.record(ChangeType.CONFIDENCE_CHANGED, "node", node_id,
                    {"old_confidence": round(old_conf, 4),
                     "new_confidence": round(new_conf, 4), "reason": reason})

    def note_activation_spike(self, node_id: str, activation: float):
        self.record(ChangeType.ACTIVATION_SPIKE, "node", node_id,
                    {"activation": round(activation, 4)})


    # ── 查询 ──────────────────────────────────────────────

    def recent(self, n: int = 50) -> list:
        with self._lock:
            return [e.to_dict() for e in self.entries[-n:]]

    def by_type(self, change_type: ChangeType, n: int = 50) -> list:
        with self._lock:
            results = [e for e in self.entries if e.change_type == change_type]
            return [e.to_dict() for e in results[-n:]]

    def by_target(self, target_id: str, n: int = 50) -> list:
        with self._lock:
            results = [e for e in self.entries if target_id in e.target_id]
            return [e.to_dict() for e in results[-n:]]

    def since(self, timestamp: str) -> list:
        with self._lock:
            return [e.to_dict() for e in self.entries if e.timestamp >= timestamp]

    def stats(self) -> dict:
        with self._lock:
            counts = {}
            for e in self.entries:
                key = e.change_type.value
                counts[key] = counts.get(key, 0) + 1
            return {
                "total_entries": len(self.entries),
                "by_type": counts,
                "oldest": self.entries[0].timestamp if self.entries else None,
                "newest": self.entries[-1].timestamp if self.entries else None,
            }

    def clear(self):
        with self._lock:
            self.entries.clear()

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "entries": [e.to_dict() for e in self.entries],
                "max_entries": self.max_entries
            }

    def save(self, path: str):
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "GraphEvolutionLog":
        import json, os
        log = cls()
        if not os.path.exists(path):
            return log
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            log.max_entries = data.get("max_entries", 10000)
            for entry_dict in data.get("entries", []):
                log.entries.append(EvolutionEntry.from_dict(entry_dict))
        except Exception as _gel:
            import logging; logging.getLogger(__name__).warning(f"Evolution log load failed: {_gel}")
        return log


# ── 全局单例 ──────────────────────────────────────────────

_evolution_log: Optional[GraphEvolutionLog] = None


def get_evolution_log() -> GraphEvolutionLog:
    global _evolution_log
    if _evolution_log is None:
        _evolution_log = GraphEvolutionLog()
    return _evolution_log
