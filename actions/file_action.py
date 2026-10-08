# file_action.py — FAS 文件操作动作（认知动作节点执行器）
# ============================================================================
# 万物皆图：本模块只是"文件操作"图节点的执行器。触发判定、激活、
# 留痕全部在 app.py 管线经图完成。
# 安全约束：
#   - 目录白名单：桌面/文档/下载（HOME 下），拒绝绝对路径与路径穿越
#   - 文件名消毒：剥路径分隔符与非法字符，保留扩展名白名单类型
#   - 内容上限 10KB；仅支持 create/overwrite（v1 不做删除/读取）
# ============================================================================

import logging
import os
import re

logger = logging.getLogger(__name__)

_HOME = os.path.expanduser("~")
_DIRS = {
    "桌面": os.path.join(_HOME, "Desktop"),
    "文档": os.path.join(_HOME, "Documents"),
    "下载": os.path.join(_HOME, "Downloads"),
}
_EXT_OK = {".txt", ".md", ".log", ".csv"}
_MAX_CONTENT = 10 * 1024


def sanitize_name(name: str) -> str:
    name = str(name or "").strip()
    name = re.sub(r"[\\/:*?\"<>|]", "", name)      # Windows 非法字符
    name = name.replace("..", "")                   # 防穿越
    return name.strip().strip(".")[:80]


def execute_file_action(action: str, name: str, content: str = "",
                        dir_hint: str = "") -> dict:
    """执行文件动作。返回 {ok, path|error, action}。"""
    if action not in ("create_file",):
        return {"ok": False, "error": f"不支持的动作: {action}"}
    fname = sanitize_name(name)
    if not fname:
        return {"ok": False, "error": "文件名为空"}
    root = os.path.splitext(fname)[1].lower()
    if root not in _EXT_OK:
        fname += ".txt"
    d = _DIRS.get(str(dir_hint or "桌面").strip(), _DIRS["桌面"])
    path = os.path.join(d, fname)
    if not os.path.abspath(path).startswith(d):
        return {"ok": False, "error": "路径越界"}
    if len(str(content or "")) > _MAX_CONTENT:
        return {"ok": False, "error": "内容超过 10KB 上限"}
    try:
        existed = os.path.exists(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(content or ""))
        logger.info(f"[FileAction] {'覆盖' if existed else '创建'} {path} "
                    f"({len(str(content or ''))} 字符)")
        return {"ok": True, "action": "create_file", "path": path,
                "overwritten": existed, "size": len(str(content or ""))}
    except Exception as e:
        logger.warning(f"[FileAction] 失败: {e}")
        return {"ok": False, "error": str(e)}
