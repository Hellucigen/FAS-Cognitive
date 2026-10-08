# skills/survival.py — G 生存需求技能
# ============================================================================
# 这些不是"自主行为"，是**生存系统提供的能力**：饥饿/血量/失火/落水/危险
# 状态先经内部状态层（需求/动机）影响上层认知，认知层决定调用哪个能力。
# 本模块只负责"怎么做"：吃、撤、找安全、睡觉、补水、回血。
# ============================================================================

import math
import time

from skills.base import (Skill, register, res_ok, res_fail, res_pending)
from skills.observation import (_find_blocks, HOSTILE_ENTITIES)
from skills.inventory import choose_food


@register
class EatFood(Skill):
    name = "eat_food"
    description = "吃背包里的食物（自动挑；可指定）"
    category = "survival"

    def start(self, ctx, params):
        food = str(params.get("item") or params.get("food") or "") or \
            choose_food(ctx.inventory(refresh=True))
        if not food:
            return res_fail("no_food_in_inventory",
                            describe="背包里没有能吃的东西")
        r = ctx.bridge.call("/eat", {"item": food}, timeout=15)
        if not r.get("ok"):
            return res_fail(r.get("reason") or "eat_failed")
        ctx.session["deadline"] = time.time() + 10
        ctx.session["food_before"] = ctx.state().get("food")
        return res_pending(describe=f"正在吃 {food}", detail={"food": food})

    def poll(self, ctx):
        s = ctx.state(refresh=True)
        now_food = float(s.get("food", 0) or 0)
        before = float(ctx.session.get("food_before") or 0)
        if now_food > before:
            return res_ok(describe=f"吃饱了点（饥饿 {now_food:.0f}）",
                          detail={"food": now_food})
        if time.time() > float(ctx.session.get("deadline", 0)):
            return res_fail("eat_failed")
        return res_pending(describe="进食中")


@register
class MonitorVitals(Skill):
    name = "monitor_vitals"
    description = "读取血量/饥饿/失火/水情（程序状态，零 LLM）"
    category = "survival"

    def start(self, ctx, params):
        s = ctx.state(refresh=True)
        return res_ok(describe=(f"血量 {s.get('health')}，饥饿 {s.get('food')}"),
                      detail={"health": s.get("health"), "food": s.get("food"),
                              "on_fire": bool(s.get("onFire")),
                              "in_water": bool(s.get("inWater")),
                              "fall_speed": s.get("fallSpeed")})


@register
class Retreat(Skill):
    """撤离：远离威胁方向走（认知层决定要不要撤，技能只管撤）。"""
    name = "retreat"
    description = "远离威胁/危险方向撤一段距离"
    category = "survival"

    def check(self, ctx, params):
        if not params.get("from") and not params.get("direction"):
            return False, "missing_threat_or_direction"
        return ctx.connected(), "not_connected"

    def start(self, ctx, params):
        s = ctx.state(refresh=True)
        pos = s.get("position") or {}
        if not pos:
            return res_fail("no_position")
        back = float(params.get("distance", 8))
        threat = str(params.get("from") or "")
        dx, dz = 0.0, 0.0
        if params.get("direction"):
            d = str(params["direction"])
            m = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
            dx, dz = m.get(d, (0, 1))
        else:
            ent = None
            for e in s.get("nearbyEntities", []) or []:
                if e.get("name") == threat:
                    ent = e
                    break
            if not ent:
                return res_fail("threat_not_visible")
            rel = ent.get("rel") or {}
            d = max(1.0, float(ent.get("dist") or 1))
            dx, dz = -float(rel.get("dx", 0)) / d, -float(rel.get("dz", 0)) / d
        # 记住撤离前的位置（seek_safety 之后可回来）
        ctx.locations.remember("last_safe", pos, kind="safe")
        tx = float(pos.get("x", 0)) + dx * back
        tz = float(pos.get("z", 0)) + dz * back
        ctx.bridge.stop_goto()
        r = ctx.bridge.goto_coords(tx, float(pos.get("y", 64)), tz)
        if not r.get("ok"):
            return res_fail(r.get("reason") or "no_path")
        ctx.session["deadline"] = time.time() + 40
        return res_pending(describe="撤离中")

    def poll(self, ctx):
        raw = ctx.bridge.get_action_result() or {}
        st = str(raw.get("status", "")).lower()
        if st in ("done", "partial"):
            return res_ok(describe="撤到安全些的地方了")
        if st == "failed":
            return res_fail("no_path")
        if time.time() > float(ctx.session.get("deadline", 0)):
            return res_ok(describe="撤离完成")
        return res_pending(describe="撤离中")

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
        except Exception:
            pass


@register
class SeekSafety(Skill):
    """找安全点：远离敌对生物 + 记住安全点。"""
    name = "seek_safety"
    description = "跑到没有敌对生物的地方（优先撤离子弹/怪物方向）"
    category = "survival"

    def start(self, ctx, params):
        s = ctx.state(refresh=True)
        hostiles = [e for e in (s.get("nearbyEntities") or [])
                    if str(e.get("name") or "") in HOSTILE_ENTITIES]
        if not hostiles:
            return res_ok(describe="附近本来就没有威胁")
        hostiles.sort(key=lambda e: float(e.get("dist") or 99))
        nearest = hostiles[0]
        return Retreat().start(ctx, {"from": nearest.get("name"),
                                     "distance": 10})

    def poll(self, ctx):
        return Retreat().poll(ctx)

    def cancel(self, ctx):
        Retreat().cancel(ctx)


@register
class AvoidLava(Skill):
    name = "avoid_lava"
    description = "检查并避开附近熔岩"

    def start(self, ctx, params):
        s = ctx.state(refresh=True)
        pos = s.get("position") or {}
        lava = _find_blocks(ctx, "lava", 4, 1)
        if not lava:
            return res_ok(describe="附近没有熔岩")
        ctx.locations.remember("last_safe", pos, kind="safe")
        r = ctx.bridge.stop_goto()
        return res_ok(describe="附近有熔岩，已停止移动",
                      detail={"lava_pos": lava[0]})


@register
class EscapeWater(Skill):
    name = "escape_water"
    description = "游回岸上（在水中时朝来路方向游）"
    category = "survival"

    def start(self, ctx, params):
        if not bool(ctx.state(refresh=True).get("inWater")):
            return res_ok(describe="不在水里")
        ctx.session["deadline"] = time.time() + 25
        return res_pending(describe="往岸上游")

    def poll(self, ctx):
        s = ctx.state(refresh=True)
        if not s.get("inWater"):
            return res_ok(describe="上岸了")
        if time.time() > float(ctx.session.get("deadline", 0)):
            return res_fail("still_in_water")
        ctx.bridge.move("forward", 0.6)
        ctx.bridge.move("jump", 0.4)
        return res_pending(describe="游泳上岸中")


@register
class SleepSkill(Skill):
    name = "sleep"
    description = "找床睡觉（跳过夜晚）"
    category = "survival"

    def check(self, ctx, params):
        return ctx.connected(), "not_connected"

    def start(self, ctx, params):
        beds = []
        for b in ("white_bed", "red_bed", "blue_bed", "green_bed", "yellow_bed",
                  "black_bed", "oak_bed"):
            beds = _find_blocks(ctx, b, 32, 1)
            if beds:
                break
        if not beds:
            return res_fail("bed_not_found")
        pos = ctx.position() or {}
        b = beds[0]
        ctx.bridge.stop_goto()
        r = ctx.bridge.goto_coords(b["x"], b.get("y", pos.get("y", 64)), b["z"])
        if not r.get("ok"):
            return res_fail("no_path")
        ctx.session.update({"bed": b, "phase": "approach",
                            "deadline": time.time() + 60})
        return res_pending(describe="走向床")

    def poll(self, ctx):
        phase = ctx.session.get("phase")
        if phase == "approach":
            raw = ctx.bridge.get_action_result() or {}
            st = str(raw.get("status", "")).lower()
            if st in ("done", "partial"):
                b = ctx.session.get("bed") or {}
                r = ctx.bridge.call("/sleep_at", {"x": b["x"], "y": b.get("y", 0),
                                                  "z": b["z"]})
                if not r.get("ok"):
                    return res_fail(r.get("reason") or "sleep_failed")
                ctx.session["phase"] = "sleeping"
                return res_pending(describe="睡觉中")
            if st == "failed":
                return res_fail("no_path")
            if time.time() > float(ctx.session.get("deadline", 0)):
                return res_fail("timeout")
            return res_pending(describe="走向床")
        if phase == "sleeping":
            # 醒来（早晨）→ done
            raw = ctx.bridge.get_action_result() or {}
            st = str(raw.get("status", "")).lower()
            if st == "done":
                return res_ok(describe="睡醒了")
            if st == "failed":
                return res_fail("sleep_failed")
            return res_pending(describe="睡觉中")


@register
class RecoverHealth(Skill):
    name = "recover_health"
    description = "回血：先吃饱（饥饿满才会自然回血），然后原地等待恢复"
    category = "survival"
    sustained = True

    def start(self, ctx, params):
        target = float(params.get("target_health", 18))
        ctx.session["target"] = target
        ctx.session["deadline"] = time.time() + float(params.get("timeout", 120))
        return self._step(ctx)

    def _step(self, ctx):
        s = ctx.state(refresh=True)
        health = float(s.get("health", 20) or 20)
        food = float(s.get("food", 20) or 20)
        target = float(ctx.session.get("target", 18))
        if health >= target:
            return res_ok(describe=f"血量恢复到 {health:.0f}",
                          detail={"health": health})
        if food < 18:
            out = EatFood().start(ctx, {})
            if out.get("status") == "failed":
                return out   # no_food_in_inventory：如实上抛
            ctx.session["phase"] = "eating"
            return res_pending(describe="先吃东西才能回血")
        ctx.session["phase"] = "waiting"
        return res_pending(describe="原地休整回血中")

    def poll(self, ctx):
        if time.time() > float(ctx.session.get("deadline", 0)):
            s = ctx.state(refresh=True)
            return res_ok(status="done",
                          describe=f"休整结束（血量 {s.get('health')}）",
                          detail={"health": s.get("health")})
        if ctx.session.get("phase") == "eating":
            out = EatFood().poll(ctx)
            if out.get("status") in ("done", "failed"):
                ctx.session["phase"] = "waiting"
            return res_pending(describe="进食回血中")
        return self._step(ctx)
