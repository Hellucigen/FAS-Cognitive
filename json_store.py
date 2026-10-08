# json_store.py — 原子 JSON 存储原语
# ============================================================================
# 认知调节三模块（锁 / 状态检测器 / 触发器）各有一份独立 JSON 配置，
# 与其把"同目录临时文件 + fsync + os.replace"抄三遍，不如共用一份。
#
# 抄的是 graph_model.save() 的写法（全库唯一真正原子的落盘）：
# 同目录 mkstemp（同卷才能 rename）→ flush → fsync → os.replace。
# capability_registry / chat_log 目前是裸 open("w")，写到一半崩溃即损坏，
# 新模块不走那条路。
# ============================================================================

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def load_json(path: str, default=None):
    """读 JSON。文件不存在返回 default；损坏时告警并返回 default（不抛）。"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[JsonStore] 读取失败（按空处理）{path}: {e}")
        return default


def atomic_write_json(path: str, data) -> bool:
    """原子写 JSON：同目录临时文件 → fsync → os.replace。返回是否成功。"""
    directory = os.path.dirname(os.path.abspath(path))
    tmp_path = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
        return True
    except Exception as e:
        logger.warning(f"[JsonStore] 写入失败 {path}: {e}")
        return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
