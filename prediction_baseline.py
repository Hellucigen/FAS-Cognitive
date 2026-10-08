# prediction_baseline.py — 话语/认知预测误差基线（V2 §预测误差基线补账）
# ============================================================================
# 设计：每轮扩散结束后，把"本轮认知焦点"（Top-K）作为对下一轮的**预期**；
# 下一轮输入激活完再比一次：现实离预期越远 → surprise 越高。
#
# surprise 的去处（都是既有消费口，不建第二套系统）：
#   1. 经验时间轴 COGNITIVE_EVENT（"预期落空"作为经历，供因果/反思）；
#   2. LearningDrive/ConsistencyDrive 信号（世界不像我以为的那样演化 =
#      能力缺口/一致性张力的第二来源）；
#   3. Curiosity 的第二类 discrepancy（V2：不止"实体不在图里"）。
#
# 持久化只有本文件自己的一份运行时快照（预测本就是瞬时的；不写图、
# 不进人格）。历史窗口很小（环形 12）。
# ============================================================================

import logging
import time

logger = logging.getLogger(__name__)


class PredictionBaseline:
    """认知焦点流的预测误差基线（Jaccard 距离，0=按预期演化，1=完全意外）。"""

    def __init__(self, config=None, history_keep: int = 12):
        cfg = (config or {}).get("prediction_baseline", {})
        self.enabled = bool(cfg.get("enabled", True))
        self.focus_n = int(cfg.get("focus_n", 8))
        self.decay = float(cfg.get("keep_stale", 0.4))   # 旧焦点在预期里的保留比
        self._predicted: set = set()
        self._history = []                                # [(ts, surprise)]
        self._history_keep = history_keep

    def observe(self, topk_ids) -> dict:
        """一轮激活/扩散结束时调用：比对预期 → 记录现实 → 滚动预期。

        返回 {surprise, predicted, actual}；首轮无预期时 surprise=None
        （没有基线就不假装测量）。
        """
        ordered = [str(x) for x in (topk_ids or []) if str(x or "").strip()]
        actual = set(ordered[: self.focus_n])
        if not self.enabled or not actual:
            return {"surprise": None, "predicted": sorted(self._predicted),
                    "actual": sorted(actual)}
        if not self._predicted:
            surprise = None
        else:
            inter = len(self._predicted & actual)
            union = len(self._predicted | actual) or 1
            surprise = round(1.0 - inter / union, 3)      # Jaccard 距离
        # 滚动预期：以本轮现实为主，保留少量旧焦点（认知对变化有惯性）
        merged = set(actual)
        keep = max(1, int(self.focus_n * self.decay))
        stale = [n for n in self._predicted if n not in actual]
        merged.update(stale[:keep])
        self._predicted = merged
        if surprise is not None:
            self._history.append((time.time(), surprise))
            del self._history[:-self._history_keep]
        return {"surprise": surprise, "predicted": sorted(self._predicted),
                "actual": sorted(actual)}

    def recent_avg_surprise(self, window_s: float = 1800.0) -> float:
        now = time.time()
        vals = [v for t, v in self._history if now - t <= window_s]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    def state(self) -> dict:
        return {"enabled": self.enabled, "predicted": sorted(self._predicted),
                "recent": [v for _, v in self._history[-6:]],
                "avg_surprise_30m": self.recent_avg_surprise()}

    def reset(self):
        self._predicted = set()
        self._history = []
