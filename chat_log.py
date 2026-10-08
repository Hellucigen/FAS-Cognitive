# chat_log.py — 自然语言输入与回复日志
# ============================================================================
# Fascinator 的对话日志模块。
#
# 职责：
#   1. 记录每次用户输入和系统回复（自然语言层面）
#   2. 持久化到 JSON 文件（data/chat_log.json）
#   3. 支持按时间/关键词检索
#   4. 独立于图谱：这是对话记录，不是认知状态
#
# 设计原则：
#   - 简单：只记录原始文本，不做图谱分析
#   - 可靠：每次写入后立即 flush 到磁盘
#   - 可查询：支持时间段和关键词过滤
# ============================================================================

import os
import json
import threading
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ── 默认日志路径 ──────────────────────────────────────────────
DEFAULT_LOG_PATH = os.path.join("data", "chat_log.json")


def _now_iso() -> str:
    """ISO 8601 时间戳"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


class ChatLog:
    """
    对话日志。

    每条记录是一个 dict：
      {
        "time": "2026-07-11T14:30:00",
        "user_input": "苹果很好吃",
        "system_response": "苹果是什么？",
        "curiosity": true,
        "curiosity_question": "苹果是什么？",
        "curiosity_resolved": false
      }
    """

    def __init__(self, path: str = None):
        self._path = path or DEFAULT_LOG_PATH
        self._lock = threading.RLock()
        self._entries: list[dict] = []
        self._load()

    # ── 持久化 ──────────────────────────────────────────────

    def _load(self):
        """从磁盘加载日志"""
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._entries = json.load(f)
                logger.info(f"[ChatLog] 加载 {len(self._entries)} 条记录")
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"[ChatLog] 加载失败 ({e})，从空日志开始")
                self._entries = []
        else:
            self._entries = []

    def _save(self):
        """保存到磁盘"""
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._entries, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"[ChatLog] 保存失败: {e}")

    # ── 写入 ────────────────────────────────────────────────

    def record(self,
               user_input: str,
               system_response: str = None,
               curiosity: bool = False,
               curiosity_question: str = None,
               curiosity_resolved: bool = False,
               curiosity_answer: str = None,
               proactive: bool = False):
        """
        记录一次对话轮次。

        Args:
          user_input: 用户原始输入
          system_response: 系统回复（LLM回答或好奇问题）
          curiosity: 本轮是否触发了好奇心
          curiosity_question: 好奇心问题文本（如果有）
          curiosity_resolved: 本轮是否解析了之前的好奇问题
          curiosity_answer: 用户对好奇问题的回答（如果有）
        """
        entry = {
            "time": _now_iso(),
            **({"proactive": True} if proactive else {}),
            "user_input": str(user_input).strip(),
            "system_response": str(system_response).strip() if system_response else None,
            "curiosity": bool(curiosity),
        }

        if curiosity_question:
            entry["curiosity_question"] = str(curiosity_question)
        if curiosity_resolved:
            entry["curiosity_resolved"] = True
        if curiosity_answer:
            entry["curiosity_answer"] = str(curiosity_answer)

        with self._lock:
            self._entries.append(entry)

            # 保持日志大小可控：最多 10000 条
            if len(self._entries) > 10000:
                self._entries = self._entries[-5000:]

            self._save()

        logger.debug(f"[ChatLog] 记录 #{len(self._entries)}: {user_input[:50]}")

    # ── 查询 ────────────────────────────────────────────────

    def recent(self, n: int = 50) -> list[dict]:
        """获取最近 n 条记录"""
        with self._lock:
            return self._entries[-n:]

    def all(self) -> list[dict]:
        """获取全部记录"""
        with self._lock:
            return list(self._entries)

    def search(self, keyword: str, n: int = 50) -> list[dict]:
        """按关键词搜索（匹配 user_input 或 system_response）"""
        kw = keyword.lower()
        with self._lock:
            matches = [
                e for e in self._entries
                if kw in str(e.get("user_input", "")).lower()
                   or kw in str(e.get("system_response", "")).lower()
            ]
            return matches[-n:]

    def stats(self) -> dict:
        """日志统计"""
        with self._lock:
            total = len(self._entries)
            curiosity_count = sum(1 for e in self._entries if e.get("curiosity"))
            resolved_count = sum(1 for e in self._entries if e.get("curiosity_resolved"))
            return {
                "total_entries": total,
                "curiosity_triggered": curiosity_count,
                "curiosity_resolved": resolved_count,
                "log_path": self._path,
            }

    def clear(self):
        """清空日志"""
        with self._lock:
            self._entries = []
            self._save()
        logger.info("[ChatLog] 已清空")

    def __len__(self):
        return len(self._entries)


# ── 全局单例 ──────────────────────────────────────────────────

_chat_log: ChatLog = None


def get_chat_log() -> ChatLog:
    """获取全局 ChatLog 实例"""
    global _chat_log
    if _chat_log is None:
        _chat_log = ChatLog()
    return _chat_log


# ═══════════════════════════════════════════════════════════════
# End of chat_log.py
# ═══════════════════════════════════════════════════════════════
