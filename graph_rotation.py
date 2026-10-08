# graph_rotation.py — 运行图谱定期快照 + 自动轮转（§18.3 P1，2026-09-22）
# ============================================================================
# 事故先例（报告 §8.10 / memory 图谱缩小守卫）：图谱只有单文件 + 人工
# 备份，一次空图覆写差点造成不可恢复损失。缩小守卫拦"骤减"，本模块
# 补"任意时刻都回得去"：单写入器线程每次成功落盘后按时间闸把
# runtime_graph.json 复制到 data/graph_snapshots/，只保留最近 N 份。
#
# 纯函数、无状态、无线程——调度（时间闸）由调用方 app 的单写入器
# 循环持有；离线单测直接调用本模块。
# ============================================================================

import logging
import os
import shutil
import time

logger = logging.getLogger(__name__)

PREFIX = "runtime_graph_"
SUFFIX = ".json"


def _snap_name(ts: float) -> str:
    return f"{PREFIX}{time.strftime('%Y%m%d_%H%M%S', time.localtime(ts))}{SUFFIX}"


def list_snapshots(snap_dir: str) -> list:
    """按名排序（名内嵌时间戳，字典序=时间序，跨时区安全）。"""
    try:
        return sorted(f for f in os.listdir(snap_dir)
                      if f.startswith(PREFIX) and f.endswith(SUFFIX))
    except OSError:
        return []


def prune(snap_dir: str, keep: int) -> list:
    """保留最近 keep 份，删除更旧的。返回删除的文件名。"""
    keep = max(1, int(keep))
    snaps = list_snapshots(snap_dir)
    victims = snaps[:-keep] if len(snaps) > keep else []
    for f in victims:
        try:
            os.remove(os.path.join(snap_dir, f))
        except OSError as e:
            logger.warning(f"[Rotation] 快照轮转删除失败 {f}: {e}")
    return victims


def maybe_rotate(src_path: str, snap_dir: str, *, interval_s: float,
                 keep: int, last_ts: float, now: float = None):
    """时间闸快照：距上次快照 ≥ interval_s 才复制当前图谱文件。

    返回 (新快照路径|None, 更新后的 last_ts)。复制失败不推进
    last_ts（下次落盘再试），只记 WARNING——快照失败绝不影响主落盘。
    """
    now = time.time() if now is None else float(now)
    if interval_s <= 0 or now - float(last_ts or 0.0) < float(interval_s):
        return None, last_ts
    if not os.path.exists(src_path):
        return None, last_ts
    try:
        os.makedirs(snap_dir, exist_ok=True)
        dst = os.path.join(snap_dir, _snap_name(now))
        shutil.copy2(src_path, dst)
        prune(snap_dir, keep)
        logger.info(f"[Rotation] 图谱快照: {os.path.basename(dst)}")
        return dst, now
    except Exception as e:
        logger.warning(f"[Rotation] 快照失败（不影响落盘）: {e}")
        return None, last_ts
