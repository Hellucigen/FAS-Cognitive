# Action/minecraft.py — Minecraft 结构化动作（统一结果接口）
# ============================================================================
# 每个动作返回结构化结果：{success, action, target?, duration?, result|reason}
# 结果进入 FAS 认知循环（expression/记忆），失败也必须产生反馈。
# follow 是持续行为：启动后由 bot 侧低层控制器维持，stop 返回跟随时长。
# ============================================================================

import logging
import time

logger = logging.getLogger(__name__)

_follow_start = None
_follow_target = None


def move_forward(secs=1.0):
    from minecraft.bridge import move
    ok = move("forward", secs)
    return {"success": ok, "action": "move_forward", "duration": secs,
            "result": "moved" if ok else "bridge_unreachable"}


def jump():
    from minecraft.bridge import move
    ok = move("jump", 0.5)
    return {"success": ok, "action": "jump", "result": "jumped" if ok else "bridge_unreachable"}


def stop():
    from minecraft.bridge import stop
    ok = stop()
    return {"success": ok, "action": "stop", "result": "stopped" if ok else "bridge_unreachable"}


def follow_player(target: str):
    """跟随玩家：启动 bot 侧低层控制器（零 LLM 持续执行）。"""
    import minecraft.bridge
    try:
        st = minecraft.bridge.get_state()
        names = [pl.get("name") for pl in (st or {}).get("playersNearby", [])]
        if target not in names:
            return {"success": False, "action": "follow_player", "target": target,
                    "reason": "player_not_nearby"}
        ok = minecraft.bridge.follow(target)
        if ok:
            global _follow_start, _follow_target
            _follow_start, _follow_target = time.time(), target
        return {"success": ok, "action": "follow_player", "target": target,
                "result": "following" if ok else "bridge_unreachable"}
    except Exception as e:
        return {"success": False, "action": "follow_player", "reason": str(e)}


def stop_follow():
    import minecraft.bridge
    global _follow_start, _follow_target
    try:
        dur = minecraft.bridge.stopfollow()
        out = {"success": True, "action": "stop_follow", "duration": dur,
               "result": "stopped"}
        _follow_start = _follow_target = None
        return out
    except Exception as e:
        return {"success": False, "action": "stop_follow", "reason": str(e)}


# ── Phase C：游玩操作扩展（挖/寻路/装备/攻击/背包）─────────

BLOCK_MAP = {"石头": "stone", "泥土": "dirt", "木头": "oak_log", "原木": "oak_log",
             "钻石矿": "diamond_ore", "铁矿": "iron_ore", "煤矿": "coal_ore",
             "金矿": "gold_ore", "沙子": "sand", "圆石": "cobblestone"}


def _post(path, payload):
    import urllib.request, json as _json
    req = urllib.request.Request(
        "http://127.0.0.1:5010" + path, method="POST",
        data=_json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return _json.loads(r.read().decode("utf-8"))


def dig_block(block_zh: str, count: int = 1):
    name = BLOCK_MAP.get(block_zh, block_zh)
    try:
        r = _post("/dig", {"block": name, "count": count})
        if r.get("ok"):
            return {"success": True, "action": "dig", "target": name, "count": count,
                    "result": "digging"}
        return {"success": False, "action": "dig", "reason": r.get("reason", "unknown")}
    except Exception as e:
        return {"success": False, "action": "dig", "reason": str(e)}


def goto_player(target: str):
    try:
        r = _post("/goto_player", {"player": target})
        if r.get("ok"):
            return {"success": True, "action": "goto_player", "target": target,
                    "result": "pathfinding"}
        return {"success": False, "action": "goto_player", "reason": r.get("reason")}
    except Exception as e:
        return {"success": False, "action": "goto_player", "reason": str(e)}


def goto_coords(x, y, z):
    try:
        r = _post("/goto", {"x": x, "y": y, "z": z})
        if r.get("ok"):
            return {"success": True, "action": "goto", "result": "pathfinding"}
        return {"success": False, "action": "goto", "reason": "failed"}
    except Exception as e:
        return {"success": False, "action": "goto", "reason": str(e)}


def equip_item(item_zh_or_en: str):
    name = item_zh_or_en
    try:
        r = _post("/equip", {"item": name})
        if r.get("ok"):
            return {"success": True, "action": "equip", "target": name, "result": "equipped"}
        return {"success": False, "action": "equip", "reason": r.get("reason", "unknown")}
    except Exception as e:
        return {"success": False, "action": "equip", "reason": str(e)}


def attack(entity_hint: str = ""):
    try:
        r = _post("/attack", {"entity": entity_hint})
        if r.get("ok"):
            return {"success": True, "action": "attack", "result": r.get("attacked", "")}
        return {"success": False, "action": "attack", "reason": r.get("reason", "no_target")}
    except Exception as e:
        return {"success": False, "action": "attack", "reason": str(e)}


def inventory():
    import urllib.request, json as _json
    try:
        with urllib.request.urlopen("http://127.0.0.1:5010/state", timeout=3) as r:
            pass
        req = urllib.request.Request("http://127.0.0.1:5010/inventory")
        with urllib.request.urlopen(req, timeout=3) as r2:
            d = _json.loads(r2.read().decode("utf-8"))
        return {"success": True, "action": "inventory", "items": d.get("items", [])}
    except Exception as e:
        return {"success": False, "action": "inventory", "reason": str(e)}
