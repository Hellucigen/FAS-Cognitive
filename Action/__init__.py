# Action/__init__.py — Action 节点执行代码集中管理
# ============================================================================
# 所有 procedural 节点的 execution 代码都放在此目录中。
# 每个 action 一个 .py 文件，函数签名统一为:
#   def run(**kwargs) -> dict:
#       ...
#       return {"status": "done", ...}
#
# Action 注册表允许通过节点 ID 查找对应的执行函数。
# ============================================================================

import os
import importlib
import logging

logger = logging.getLogger(__name__)

# ── Action 注册表 ──────────────────────────────────────────
# { "node_id": "module.function" }
ACTION_REGISTRY: dict[str, str] = {}


def register(node_id: str, func_path: str):
    """注册一个 action 函数映射"""
    ACTION_REGISTRY[node_id] = func_path
    logger.info(f"[Action] 注册: {node_id} → {func_path}")


def get_action_func(node_id: str):
    """根据节点 ID 获取对应的 action 函数"""
    func_path = ACTION_REGISTRY.get(node_id)
    if not func_path:
        # 尝试模糊匹配
        for nid, path in ACTION_REGISTRY.items():
            if nid.lower() == node_id.lower():
                func_path = path
                break
    if not func_path:
        return None

    if "." in func_path:
        module_name, func_name = func_path.rsplit(".", 1)
        try:
            mod = importlib.import_module(f"Action.{module_name}")
            return getattr(mod, func_name, None)
        except Exception as e:
            logger.warning(f"[Action] 加载失败: {func_path} ({e})")
            return None
    else:
        # 无 "." 前缀，函数在 Action 包自身 (__init__.py)
        try:
            mod = importlib.import_module("Action")
            return getattr(mod, func_path, None)
        except Exception as e:
            logger.warning(f"[Action] 加载失败: {func_path} ({e})")
            return None


def execute(node_id: str, **kwargs) -> dict:
    """执行已注册的 action"""
    func = get_action_func(node_id)
    if func is None:
        return {"success": False, "error": f"未注册的 action: {node_id}"}
    try:
        result = func(**kwargs)
        return {"success": True, "output": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_actions() -> list[str]:
    """列出所有注册的 action"""
    return list(ACTION_REGISTRY.keys())


# ── 自动发现 Action 目录下的所有 .py 文件 ────────────────

def discover_actions():
    """扫描 Action 目录，自动导入所有 action 模块"""
    action_dir = os.path.dirname(os.path.abspath(__file__))
    for fname in sorted(os.listdir(action_dir)):
        if fname.startswith("_") or not fname.endswith(".py"):
            continue
        mod_name = fname[:-3]  # 去掉 .py
        try:
            importlib.import_module(f"Action.{mod_name}")
            logger.debug(f"[Action] 发现模块: {mod_name}")
        except Exception as e:
            logger.warning(f"[Action] 导入失败: {mod_name} ({e})")


# ── 内置键盘动作 ──────────────────────────────────────────

def _key_press(key_code: int, vk_code: int, hold_s: float = 0.05) -> dict:
    """通用键盘按键 (Windows)"""
    import ctypes
    import time

    KEYEVENTF_SCANCODE = 0x0008
    KEYEVENTF_KEYUP = 0x0002
    user32 = ctypes.windll.user32

    user32.keybd_event(vk_code, key_code, KEYEVENTF_SCANCODE, 0)
    time.sleep(hold_s)
    user32.keybd_event(vk_code, key_code, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0)

    return {"action": "key_press", "key_code": key_code, "hold_s": hold_s, "status": "done"}


# ── WASD 键盘动作函数 ─────────────────────────────────────

def action_press_w(**kwargs) -> dict:
    return _key_press(0x11, 0x57, hold_s=0.05)

def action_hold_w(**kwargs) -> dict:
    return _key_press(0x11, 0x57, hold_s=2.0)

def action_press_a(**kwargs) -> dict:
    return _key_press(0x1E, 0x41, hold_s=0.05)

def action_hold_a(**kwargs) -> dict:
    return _key_press(0x1E, 0x41, hold_s=2.0)

def action_press_s(**kwargs) -> dict:
    return _key_press(0x1F, 0x53, hold_s=0.05)

def action_hold_s(**kwargs) -> dict:
    return _key_press(0x1F, 0x53, hold_s=2.0)

def action_press_d(**kwargs) -> dict:
    return _key_press(0x20, 0x44, hold_s=0.05)

def action_hold_d(**kwargs) -> dict:
    return _key_press(0x20, 0x44, hold_s=2.0)


# ── 批量注册 WASD 动作 ────────────────────────────────────

register("按下W", "action_press_w")
register("长按W", "action_hold_w")
register("按下A", "action_press_a")
register("长按A", "action_hold_a")
register("按下S", "action_press_s")
register("长按S", "action_hold_s")
register("按下D", "action_press_d")
register("长按D", "action_hold_d")
