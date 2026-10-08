# skills/blocks.py — C 方块交互技能
# ============================================================================
# look_at_block / break_block / place_block / collect_dropped_item /
# interact_with_block / interact_with_entity。
# 每个交互都带：目标位置、工具需求（choose_best_tool）、距离检查、
# 成功检测（动作回执/世界差分）、失败检测（语义化 reason）。
# ============================================================================

import math
import time

from skills.base import (Skill, register, res_ok, res_fail, res_pending,
                         receipt_reason)
from skills.observation import _find_blocks, normalize_resource
from skills.inventory import choose_best_tool, equip_best


def _block_dist(pos: dict, p: dict) -> float:
    try:
        return math.sqrt((pos.get("x", 0) - (p.get("x", 0) + 0.5)) ** 2
                         + (pos.get("y", 0) - (p.get("y", 0) + 0.5)) ** 2
                         + (pos.get("z", 0) - (p.get("z", 0) + 0.5)) ** 2)
    except (TypeError, ValueError):
        return 1e9


@register
class LookAtBlock(Skill):
    name = "look_at_block"
    description = "看向指定/最近的某种方块"
    category = "blocks"

    def start(self, ctx, params):
        pos = ctx.position()
        p = params.get("position")
        if not p:
            name = normalize_resource(params.get("block") or params.get("target") or "")
            if not name:
                return res_fail("missing_block")
            positions = _find_blocks(ctx, name, 8, 4)
            if not positions:
                return res_fail("block_not_found")
            p = min(positions, key=lambda q: _block_dist(pos, q))
        r = ctx.bridge.look_at(p["x"] + 0.5, p.get("y", 0) + 0.5, p["z"] + 0.5)
        if not r.get("ok"):
            return res_fail(r.get("reason") or "look_failed")
        return res_ok(describe="看向方块", detail={"position": p})


@register
class BreakBlock(Skill):
    """挖单个方块：自动选工具 → 装备 → 到距离内 → 挖 → 等回执。
    失败原因语义化：tool_missing:<需的镐> / too_far / no_path / dig_failed。"""
    name = "break_block"
    description = "挖掉一个方块（按位置或方块名自动定位；自动选合适工具）"
    category = "blocks"

    def check(self, ctx, params):
        return ctx.connected(), "not_connected"

    def start(self, ctx, params):
        pos = ctx.position()
        p = params.get("position")
        name = normalize_resource(params.get("block") or params.get("target") or "")
        if not p and not name:
            return res_fail("missing_target")
        if not p:
            positions = _find_blocks(ctx, name, 12, 4)
            if not positions:
                return res_fail("block_not_found")
            p = min(positions, key=lambda q: _block_dist(pos, q))
        if _block_dist(pos or {}, p) > 5.0:
            # 太远：先记录目标，走过去（内嵌寻路 pending，不整段阻塞）
            ctx.session.update({"_break_target": p, "_break_name": name,
                                "_phase": "approach",
                                "timeout_at": time.time() + 60})
            r = ctx.bridge.goto_coords(p["x"], p.get("y", pos.get("y", 64)), p["z"])
            if not r.get("ok"):
                return res_fail(r.get("reason") or "no_path")
            return res_pending(describe="走向目标方块")
        return self._dig(ctx, p, name)

    def _dig(self, ctx, p, name):
        # 工具：按方块需求选镐（没有就如实失败——认知层会学到 tool_missing）
        tool, why = choose_best_tool(ctx, name)
        if tool:
            equip_best(ctx, tool)
        elif why:
            return res_fail(why, detail={"block": name})
        r = ctx.bridge.call("/dig_pos", {"x": p["x"], "y": p.get("y", 0), "z": p["z"]})
        if not r.get("ok"):
            return res_fail(r.get("reason") or "dig_failed", detail={"block": name})
        ctx.session.update({"_break_target": p, "_break_name": name,
                            "_phase": "digging",
                            "timeout_at": time.time() + 15})
        return res_pending(describe=f"正在挖 {name or '方块'}")

    def poll(self, ctx):
        phase = ctx.session.get("_phase")
        if phase == "approach":
            raw = ctx.bridge.get_action_result() or {}
            st = str(raw.get("status", "")).lower()
            if st in ("done", "partial"):
                ctx.session["_phase"] = "dig"
                return self._dig(ctx, ctx.session["_break_target"],
                                 ctx.session.get("_break_name"))
            if st == "failed":
                return res_fail(receipt_reason(raw, "no_path"))
            if time.time() > float(ctx.session.get("timeout_at", 0)):
                return res_fail("timeout")
            return res_pending(describe="走向目标方块")
        # digging
        raw = ctx.bridge.get_action_result() or {}
        st = str(raw.get("status", "")).lower()
        if st == "done":
            return res_ok(describe=f"挖掉了 {ctx.session.get('_break_name') or '方块'}",
                          detail={"block": ctx.session.get("_break_name"),
                                  "position": ctx.session.get("_break_target")})
        if st == "failed":
            reason = receipt_reason(raw, "dig_failed")
            return res_fail(reason)
        if time.time() > float(ctx.session.get("timeout_at", 0)):
            return res_fail("timeout")
        return res_pending(describe="挖掘中")

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
        except Exception:
            pass


@register
class PlaceBlock(Skill):
    name = "place_block"
    description = "在指定/前方位置放置背包里的方块"
    category = "blocks"

    def check(self, ctx, params):
        return ctx.connected(), "not_connected"

    def start(self, ctx, params):
        item = str(params.get("item") or params.get("block") or "").strip()
        if not item:
            held = (ctx.state().get("heldItem") or "")
            if not held or held == "空手":
                return res_fail("no_item_specified")
            item = held
        inv = ctx.inventory()
        have = next((i for i in inv if i.get("name") == item), None)
        if not have:
            return res_fail(f"item_not_in_inventory:{item}")
        p = params.get("position")
        if not p:
            pos = ctx.position()
            yaw = float(params.get("yaw", 0))
            dx, dz = -math.sin(math.radians(yaw)), -math.cos(math.radians(yaw))
            if not pos:
                return res_fail("no_position")
            # 前瞻落点：floor 进"方块列"而不是 round 回四舍五入的格——
            # round(pos.z-1) 在 .5 边界（z=-41.5）会把目标折回她自己站的
            # 列里（2026-09-27 真机：拿着熔炉连放两次"身上"，Server
            # refused → 退化成围着用户转圈反复提案）。再兜一道：目标列
            # 恰是本列就沿朝向多进一格。
            _ox, _oz = int(math.floor(pos.get("x", 0))), int(math.floor(pos.get("z", 0)))
            _tx, _tz = _ox + int(math.floor(dx)), _oz + int(math.floor(dz))
            if (_tx, _tz) == (_ox, _oz):
                _tx, _tz = _ox + (1 if dx > 0 else -1 if dx < 0 else 0), \
                           _oz + (1 if dz > 0 else -1 if dz < 0 else 0)
            p = {"x": _tx, "y": int(float(pos.get("y", 64))), "z": _tz}
        r = ctx.bridge.call("/place", {"x": p["x"], "y": p.get("y", 0), "z": p["z"],
                                       "item": item})
        if not r.get("ok"):
            return res_fail(r.get("reason") or "place_failed")
        ctx.session["timeout_at"] = time.time() + 8
        ctx.session["_place_item"] = item
        ctx.session["_place_pos"] = p
        return res_pending(describe=f"正在放置 {item}")

    def poll(self, ctx):
        raw = ctx.bridge.get_action_result() or {}
        st = str(raw.get("status", "")).lower()
        if st == "done":
            # 真实结果带坐标：世界事件层（B2）要用"在哪放了什么"沉淀经验
            return res_ok(describe=f"放好了 {ctx.session.get('_place_item') or '方块'}",
                          detail={"block": ctx.session.get("_place_item"),
                                  "position": ctx.session.get("_place_pos")})
        if st == "failed":
            return res_fail(receipt_reason(raw, "place_failed"))
        if time.time() > float(ctx.session.get("timeout_at", 0)):
            return res_fail("timeout")
        return res_pending(describe="放置中")


@register
class CollectDroppedItem(Skill):
    name = "collect_dropped_item"
    description = "走过去捡起附近的掉落物"
    category = "blocks"

    def start(self, ctx, params):
        r = ctx.bridge.call("/collect_item", {"radius": int(params.get("radius", 8))})
        if not r.get("ok"):
            return res_fail(r.get("reason") or "no_item")
        ctx.session["timeout_at"] = time.time() + 30
        return res_pending(describe="去捡掉落物", detail={"item": r.get("item")})

    def poll(self, ctx):
        raw = ctx.bridge.get_action_result() or {}
        st = str(raw.get("status", "")).lower()
        if st == "done":
            return res_ok(describe="捡起了掉落物")
        if st == "failed":
            return res_fail(receipt_reason(raw, "collect_failed"))
        if time.time() > float(ctx.session.get("timeout_at", 0)):
            return res_fail("timeout")
        return res_pending(describe="捡拾中")

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
        except Exception:
            pass


@register
class InteractWithBlock(Skill):
    name = "interact_with_block"
    description = "使用/打开方块（门/箱子/工作台…右键交互）"
    category = "blocks"

    def start(self, ctx, params):
        p = params.get("position")
        name = normalize_resource(params.get("block") or params.get("target") or "")
        if not p:
            if not name:
                return res_fail("missing_target")
            positions = _find_blocks(ctx, name, 6, 4)
            if not positions:
                return res_fail("block_not_found")
            p = positions[0]
        r = ctx.bridge.call("/interact", {"x": p["x"], "y": p.get("y", 0), "z": p["z"]})
        if not r.get("ok"):
            return res_fail(r.get("reason") or "interact_failed")
        return res_ok(describe=f"使用了方块", detail={"position": p})


@register
class InteractWithEntity(Skill):
    name = "interact_with_entity"
    description = "与实体交互（喂食/骑乘/交易…右键交互）"
    category = "blocks"

    def start(self, ctx, params):
        name = str(params.get("entity") or params.get("target") or "")
        if not name:
            return res_fail("missing_entity")
        r = ctx.bridge.call("/interact_entity", {"entity": name})
        if not r.get("ok"):
            return res_fail(r.get("reason") or "entity_not_visible")
        return res_ok(describe=f"与 {name} 互动了")
