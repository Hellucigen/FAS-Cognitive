# Action/wasd.py — WASD 键盘动作模块
# 每个函数返回 dict 结果

import ctypes
import time

# Win32 常量
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

# 键位映射
KEY_MAP = {
    "W": (0x11, 0x57),
    "A": (0x1E, 0x41),
    "S": (0x1F, 0x53),
    "D": (0x20, 0x44),
}


def _press(scancode: int, vk: int, hold_s: float = 0.05):
    """通用按键"""
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, scancode, KEYEVENTF_SCANCODE, 0)
    time.sleep(hold_s)
    user32.keybd_event(vk, scancode, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0)


def press_w():
    """单点 W"""
    sc, vk = KEY_MAP["W"]
    _press(sc, vk, 0.05)
    return {"key": "W", "action": "press", "status": "done"}

def hold_w(duration: float = 2.0):
    """长按 W"""
    sc, vk = KEY_MAP["W"]
    _press(sc, vk, duration)
    return {"key": "W", "action": "hold", "duration": duration, "status": "done"}

def press_a():
    """单点 A"""
    sc, vk = KEY_MAP["A"]
    _press(sc, vk, 0.05)
    return {"key": "A", "action": "press", "status": "done"}

def hold_a(duration: float = 2.0):
    """长按 A"""
    sc, vk = KEY_MAP["A"]
    _press(sc, vk, duration)
    return {"key": "A", "action": "hold", "duration": duration, "status": "done"}

def press_s():
    """单点 S"""
    sc, vk = KEY_MAP["S"]
    _press(sc, vk, 0.05)
    return {"key": "S", "action": "press", "status": "done"}

def hold_s(duration: float = 2.0):
    """长按 S"""
    sc, vk = KEY_MAP["S"]
    _press(sc, vk, duration)
    return {"key": "S", "action": "hold", "duration": duration, "status": "done"}

def press_d():
    """单点 D"""
    sc, vk = KEY_MAP["D"]
    _press(sc, vk, 0.05)
    return {"key": "D", "action": "press", "status": "done"}

def hold_d(duration: float = 2.0):
    """长按 D"""
    sc, vk = KEY_MAP["D"]
    _press(sc, vk, duration)
    return {"key": "D", "action": "hold", "duration": duration, "status": "done"}
