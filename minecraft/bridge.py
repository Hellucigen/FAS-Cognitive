# minecraft.bridge.py — FAS ↔ Mineflayer 桥（Python 侧）
# ============================================================================
# 万物皆图：Mineflayer bot（Haru）的游戏状态经 HTTP 桥读入。
# 状态→图的映射在 minecraft.perception.update_perception（原子槽位 +
# 当前Minecraft状态 中间节点）；本模块只做纯 HTTP 客户端。
#
# 接口：
#   get_state()        读 bot 状态（位置/血量/背包/聊天）
#   say(text)          游戏内聊天（Haru 在游戏里说话）
#   move(dir, secs)    基础移动指令
# 失败策略：桥不可达一律返回 None/False，图谱与对话继续（LLM/桥都是增强器）。
# ============================================================================

import logging
import urllib.request
import urllib.parse
import json as _json

logger = logging.getLogger(__name__)

BASE = "http://127.0.0.1:5010"
TIMEOUT = 3


def get_state() -> dict | None:
    try:
        with urllib.request.urlopen(BASE + "/state", timeout=TIMEOUT) as r:
            return _json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def say(text: str) -> bool:
    try:
        req = urllib.request.Request(
            BASE + "/say", method="POST",
            data=_json.dumps({"text": str(text)[:240]}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status == 200
    except Exception as e:
        logger.warning(f"[Minecraft] say 失败: {e}")
        return False


def move(direction: str, secs: float = 1.0) -> bool:
    try:
        req = urllib.request.Request(
            BASE + "/move", method="POST",
            data=_json.dumps({"dir": direction, "secs": secs}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status == 200
    except Exception:
        return False


def stop() -> bool:
    try:
        req = urllib.request.Request(BASE + "/stop", method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status == 200
    except Exception:
        return False


def follow(player: str) -> bool:
    try:
        req = urllib.request.Request(
            BASE + "/follow", method="POST",
            data=_json.dumps({"player": player}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status == 200
    except Exception as e:
        logger.warning(f"[Minecraft] follow 失败: {e}")
        return False


def stopfollow() -> int:
    try:
        req = urllib.request.Request(BASE + "/stopfollow", method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = _json.loads(r.read().decode("utf-8"))
            return body.get("followed_seconds", 0)
    except Exception as e:
        logger.warning(f"[Minecraft] stopfollow 失败: {e}")
        return 0


# ── 动作回执与完整动作面（Phase D：自主行动闭环需要真实成败） ──────────
# 旧接口返回 bool（只看 HTTP 200），自主行动必须知道动作**真的**成没成、
# 失败原因是什么，所以下面统一走 call()：返回解析后的 JSON（含 ok/reason）。

def call(path: str, payload: dict = None, timeout: float = TIMEOUT) -> dict:
    """POST 到 bot 桥并返回解析后的 JSON。

    失败一律返回 {"ok": False, 原因: ...}，绝不假装成功。
    """
    import time as _t
    _t0 = _t.perf_counter()
    try:
        data = _json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(BASE + path, method="POST", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8")
            out = _json.loads(body) if body else {"ok": True}
        _log_mc(path, bool(out.get("ok")), out.get("reason"), _t0)
        return out
    except Exception as e:
        logger.warning(f"[Minecraft] {path} 失败: {e}")
        _log_mc(path, False, f"bridge_error: {e}", _t0)
        return {"ok": False, "reason": f"bridge_error: {e}"}


def _log_mc(path, ok, reason, t0):
    """观测：桥命令回执（低频，用户/自主发起，非轮询）。"""
    try:
        import fas_log
        lv = "INFO" if ok else "WARNING"
        fas_log.emit(fas_log.MINECRAFT, lv, "bridge_command",
                     f"{path} {'ok' if ok else '失败'}",
                     path=path, ok=ok, reason=str(reason or "")[:120],
                     latency_ms=round((__import__("time").perf_counter() - t0) * 1000))
    except Exception:
        pass


def get_action_result() -> dict:
    """取 bot 最近一个动作的真实回执（状态/明细/起止时间）。"""
    try:
        with urllib.request.urlopen(BASE + "/action_result", timeout=TIMEOUT) as r:
            body = _json.loads(r.read().decode("utf-8"))
            return body.get("action") or {}
    except Exception:
        return {}


def health() -> bool:
    """桥是否在线（bot 进程活着）。"""
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def look(yaw: float, pitch: float = 0.0) -> dict:
    return call("/look", {"yaw": float(yaw), "pitch": float(pitch)})


def look_at(x: float, y: float, z: float) -> dict:
    """把视角转向世界坐标点（坐标→yaw/pitch 由 bot 自己算）。"""
    return call("/look_at", {"x": float(x), "y": float(y), "z": float(z)})


def sneak(on: bool = True) -> dict:
    return call("/sneak", {"on": bool(on)})


def sprint(on: bool = True) -> dict:
    return call("/sprint", {"on": bool(on)})


def dig(block: str, count: int = 1) -> dict:
    return call("/dig", {"block": str(block), "count": int(count)})


def goto_coords(x: float, y: float, z: float) -> dict:
    return call("/goto", {"x": float(x), "y": float(y), "z": float(z)})


def goto_player(player: str) -> dict:
    return call("/goto_player", {"player": str(player)})


def stop_goto() -> dict:
    """取消寻路（/stop 只清控制状态，pathfinder 会继续走完当前 goal）。"""
    return call("/stop_goto")


def equip(item: str) -> dict:
    return call("/equip", {"item": str(item)})


def attack(entity: str = "") -> dict:
    return call("/attack", {"entity": str(entity)})


def inventory() -> dict:
    try:
        with urllib.request.urlopen(BASE + "/inventory", timeout=TIMEOUT) as r:
            return _json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "reason": f"bridge_error: {e}"}


def recipes() -> dict:
    """P3/§8 环境事实："当前背包能做出什么"（bot 侧真配方表计算+30s 缓存）。
    失败静默降级 {ok:false}——配方线索是加成，绝不能阻塞感知循环。"""
    try:
        with urllib.request.urlopen(BASE + "/recipes", timeout=TIMEOUT) as r:
            return _json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "reason": f"bridge_error: {e}"}


def recipe_for(item: str) -> dict:
    """学习实验 §6：查"什么配方能**产生** item"（运行时 minecraft-data，
    版本=服务器协商版本）。返回 {ok, recipes:[{result,yield,ingredients,
    needs_table}], mc_version, mcd_version}；数据缺失如实报 recipes 为空。"""
    try:
        q = urllib.parse.quote(str(item or ""))
        with urllib.request.urlopen(BASE + f"/recipe_for?item={q}",
                                    timeout=TIMEOUT) as r:
            return _json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "reason": f"bridge_error: {e}"}


def block_meta(name: str) -> dict:
    """学习实验 §5：方块掉落物/工具要求的运行时版本正确查询。
    返回 {ok, block, drops:[名], harvest_tools:[名], mc_version}。"""
    try:
        q = urllib.parse.quote(str(name or ""))
        with urllib.request.urlopen(BASE + f"/block_meta?name={q}",
                                    timeout=TIMEOUT) as r:
            return _json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "reason": f"bridge_error: {e}"}


# ── 技能层扩展动作面（Skill Library 2026-09）─────────────────────────
# 全部走 call()：返回解析后的 JSON（ok/reason），失败绝不假装成功。

def find_blocks(block: str, radius: int = 12, count: int = 8) -> dict:
    return call("/find_blocks", {"block": str(block), "radius": int(radius),
                                 "count": int(count)})


def dig_pos(x: float, y: float, z: float) -> dict:
    return call("/dig_pos", {"x": float(x), "y": float(y), "z": float(z)})


def place_block(x: float, y: float, z: float, item: str = "") -> dict:
    return call("/place", {"x": float(x), "y": float(y), "z": float(z),
                           "item": str(item)}, timeout=8)


def craft(item: str, count: int = 1, table: dict = None) -> dict:
    return call("/craft", {"item": str(item), "count": int(count),
                           "table": table}, timeout=25)


def smelt(input_item: str, fuel: str, furnace: dict,
          fuel_count: int = 1) -> dict:
    # fuel_count：放几根燃料——够不够烧完这批由 world_prior 燃料表算
    # （一根木棍只烧 5s，熔一件要 10s+，放一根=炉子中途熄火）
    return call("/smelt", {"input": str(input_item), "fuel": str(fuel),
                           "furnace": furnace,
                           "fuel_count": max(1, int(fuel_count or 1))},
                timeout=25)


def furnace_take(furnace: dict) -> dict:
    return call("/furnace_take", {"furnace": furnace}, timeout=25)


def eat(item: str) -> dict:
    return call("/eat", {"item": str(item)}, timeout=15)


def combat(entity: str, retreat_health: float = 8, max_ms: int = 45000) -> dict:
    return call("/combat", {"entity": str(entity),
                            "retreat_health": float(retreat_health),
                            "max_ms": int(max_ms)}, timeout=8)


def stop_combat() -> dict:
    return call("/stop_combat")


def flee(distance: float = 10) -> dict:
    return call("/flee", {"distance": float(distance)}, timeout=15)


def collect_item(radius: int = 8) -> dict:
    return call("/collect_item", {"radius": int(radius)}, timeout=20)


def interact(x: float, y: float, z: float) -> dict:
    return call("/interact", {"x": float(x), "y": float(y), "z": float(z)})


def interact_entity(entity: str) -> dict:
    return call("/interact_entity", {"entity": str(entity)})


def equip_off(item: str) -> dict:
    return call("/equip_off", {"item": str(item)})


def unequip() -> dict:
    return call("/unequip")


def drop(item: str, count: int = 1) -> dict:
    return call("/drop", {"item": str(item), "count": int(count)})


def sort_inventory() -> dict:
    return call("/sort_inventory")


def inventory_slots() -> dict:
    try:
        with urllib.request.urlopen(BASE + "/inventory_slots", timeout=TIMEOUT) as r:
            return _json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "reason": f"bridge_error: {e}"}


def sleep_at(x: float, y: float, z: float) -> dict:
    return call("/sleep_at", {"x": float(x), "y": float(y), "z": float(z)})


def chest_store(keep: list = None, all_items: bool = False) -> dict:
    return call("/chest_store", {"keep": keep or [], "all": bool(all_items)},
                timeout=25)


def chest_take(item: str, count: int = 1) -> dict:
    return call("/chest_take", {"item": str(item), "count": int(count)},
                timeout=25)


def goto_entity(entity: str, range_: float = 1.5) -> dict:
    return call("/goto_entity", {"entity": str(entity), "range": float(range_)},
                timeout=15)


# （update_game_state 已删除：Phase A 单 blob 状态节点被 minecraft.perception 的
#  原子槽位 + 当前Minecraft状态 中间节点取代，且该函数已无调用方。）
