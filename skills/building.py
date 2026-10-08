# skills/building.py — I 建造技能（简单几何结构）
# ============================================================================
# 只做简单几何（墙/地板/火把/门/箱子…），不追求自动建复杂建筑。
# 所有放置：先确认背包有方块（没有 → 如实失败，认知层可先去采集），
# 再逐块 /place（每步一个 poll，放失败如实报 place_failed）。
# ============================================================================

import math
import time

from skills.base import (Skill, register, res_ok, res_fail, res_pending)
from skills.observation import _find_blocks
from skills.inventory import _inv_count


def _ensure_item(ctx, item: str):
    if _inv_count(ctx.inventory(refresh=True), item) <= 0:
        return False
    ctx.bridge.equip(item)
    return True


class _PlaceSequence(Skill):
    """按位置序列逐块放置的基类（pending 状态机）。"""
    category = "building"
    item_default = ""

    def plan(self, ctx, params) -> list:
        raise NotImplementedError

    def start(self, ctx, params):
        item = str(params.get("item") or params.get("block") or self.item_default)
        if not item:
            return res_fail("missing_item")
        positions = self.plan(ctx, params)
        if not positions:
            return res_fail("nothing_to_build")
        need = len(positions)
        have = _inv_count(ctx.inventory(refresh=True), item)
        if have < need:
            return res_fail("not_enough_blocks",
                            describe=f"需要 {item} x{need}，背包只有 {have}",
                            detail={"item": item, "need": need, "have": have})
        ctx.bridge.equip(item)
        ctx.session.update({"positions": positions, "item": item, "placed": 0,
                            "idx": 0, "phase": "placing",
                            "deadline": time.time() + float(params.get("timeout", 120))})
        return res_pending(describe=f"开始放置 {item} x{need}")

    def poll(self, ctx):
        s = ctx.session
        if time.time() > float(s.get("deadline", 0)):
            placed = int(s.get("placed", 0))
            self.cancel(ctx)
            if placed > 0:
                return res_ok(status="partial",
                              describe=f"放了一部分（{placed}/{len(s.get('positions') or [])}）",
                              detail={"placed": placed})
            return res_fail("timeout")
        if s.get("phase") == "wait":
            raw = ctx.bridge.get_action_result() or {}
            st = str(raw.get("status", "")).lower()
            if st in ("done", ""):
                s["placed"] = int(s.get("placed", 0)) + 1
                s["phase"] = "placing"
            elif st == "failed":
                reason = str(raw.get("detail", {}).get("reason") or "place_failed")
                self.cancel(ctx)
                return res_fail(reason, detail={"placed": s.get("placed", 0)})
            else:
                return res_pending(describe="放置中")
        positions = s.get("positions") or []
        idx = int(s.get("idx", 0))
        if idx >= len(positions):
            placed = int(s.get("placed", 0))
            self.cancel(ctx)
            return res_ok(describe=f"放好了 {placed} 块 {s.get('item')}",
                          detail={"placed": placed, "item": s.get("item")})
        p = positions[idx]
        s["idx"] = idx + 1
        r = ctx.bridge.call("/place", {"x": p["x"], "y": p["y"], "z": p["z"],
                                       "item": s.get("item")})
        if not r.get("ok"):
            # 放不了（脚手架不在/悬空）：跳过这块继续，最后如实汇报
            s["skip"] = int(s.get("skip", 0)) + 1
            return res_pending(describe="这块放不了，跳过")
        s["phase"] = "wait"
        return res_pending(describe=f"放置中（{idx + 1}/{len(positions)}）")

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
        except Exception:
            pass


@register
class BuildWall(_PlaceSequence):
    name = "build_wall"
    description = "建一面简单直墙（沿 facing 方向，长×高）"
    item_default = "cobblestone"

    def plan(self, ctx, params):
        pos = ctx.position()
        if not pos:
            return []
        length = max(1, min(int(params.get("length", 4)), 12))
        height = max(1, min(int(params.get("height", 2)), 4))
        yaw = math.radians(float(params.get("yaw", 0)))
        dx, dz = round(-math.sin(yaw)), round(-math.cos(yaw))
        y0 = int(float(pos.get("y", 64)))
        x0, z0 = round(float(pos.get("x", 0))) + dx, round(float(pos.get("z", 0))) + dz
        cells = []
        for i in range(length):
            for h in range(height):
                cells.append({"x": x0 + dx * i, "y": y0 + h, "z": z0 + dz * i})
        return cells


@register
class BuildFloor(_PlaceSequence):
    name = "build_floor"
    description = "铺一块简单地板（size×size）"
    item_default = "cobblestone"

    def plan(self, ctx, params):
        pos = ctx.position()
        if not pos:
            return []
        size = max(1, min(int(params.get("size", 3)), 7))
        y = int(float(pos.get("y", 64))) - 1   # 脚下一层
        x0, z0 = round(float(pos.get("x", 0))), round(float(pos.get("z", 0)))
        return [{"x": x0 + i, "y": y, "z": z0 + j}
                for i in range(-(size // 2), size // 2 + 1)
                for j in range(-(size // 2), size // 2 + 1)]


@register
class BuildSimpleShelter(_PlaceSequence):
    """简易庇护所：3x3 围墙留门口 + 顶盖。放置序列从脚下向外圈扩展。"""
    name = "build_simple_shelter"
    description = "搭一个简易庇护所（3x3 墙留门 + 顶）"
    item_default = "cobblestone"

    def plan(self, ctx, params):
        pos = ctx.position()
        if not pos:
            return []
        x0, y0 = round(float(pos.get("x", 0))), int(float(pos.get("y", 64)))
        z0 = round(float(pos.get("z", 0)))
        cells = []
        for h in range(2):
            ring = [(x0 - 1 + i, z0 - 1), (x0 - 1 + i, z0 + 1),
                    (x0 - 1, z0 - 1 + i), (x0 + 1, z0 - 1 + i)]
            seen = set()
            for (cx, cz) in ring:
                if (cx, cz) in seen or (cx == x0 and cz == z0 + 1 and h == 0):
                    continue    # 留门口（南侧）
                seen.add((cx, cz))
                cells.append({"x": cx, "y": y0 + h, "z": cz})
        for (cx, cz) in [(x0 - 1, z0 - 1), (x0, z0 - 1), (x0 + 1, z0 - 1),
                         (x0 - 1, z0), (x0, z0), (x0 + 1, z0),
                         (x0 - 1, z0 + 1), (x0, z0 + 1), (x0 + 1, z0 + 1)]:
            cells.append({"x": cx, "y": y0 + 2, "z": cz})    # 顶
        return cells


@register
class PlaceTorch(Skill):
    name = "place_torch"
    description = "在脚下放火把照明"
    category = "building"

    def start(self, ctx, params):
        if not _ensure_item(ctx, "torch"):
            return res_fail("item_not_in_inventory:torch",
                            describe="背包里没有火把")
        pos = ctx.position()
        if not pos:
            return res_fail("no_position")
        r = ctx.bridge.call("/place", {"x": round(float(pos.get("x", 0))),
                                       "y": int(float(pos.get("y", 64))),
                                       "z": round(float(pos.get("z", 0))),
                                       "item": "torch"})
        if not r.get("ok"):
            return res_fail(r.get("reason") or "place_failed")
        ctx.session["deadline"] = time.time() + 6
        return res_pending(describe="放火把")

    def poll(self, ctx):
        raw = ctx.bridge.get_action_result() or {}
        st = str(raw.get("status", "")).lower()
        if st == "failed":
            return res_fail("place_failed")
        if st == "done" or time.time() > float(ctx.session.get("deadline", 0)):
            return res_ok(describe="火把放好了")


@register
class PlaceNamedBlock(Skill):
    """place_door / place_chest / place_crafting_table / place_furnace /
    place_bed 的统一执行面：手里有就放（按名字找背包物品）。"""
    name = "place_named_block"
    description = "放置指定功能方块（门/箱子/工作台/熔炉/床）"
    category = "building"

    def start(self, ctx, params):
        item = str(params.get("item") or params.get("block") or params.get("target") or "")
        if not item:
            return res_fail("missing_item")
        if not _ensure_item(ctx, item):
            return res_fail(f"item_not_in_inventory:{item}",
                            describe=f"背包里没有 {item}（可以先合成）")
        pos = ctx.position()
        if not pos:
            return res_fail("no_position")
        yaw = math.radians(float(params.get("yaw", 0)))
        dx, dz = round(-math.sin(yaw)), round(-math.cos(yaw))
        r = ctx.bridge.call("/place", {"x": round(float(pos.get("x", 0))) + dx,
                                       "y": int(float(pos.get("y", 64))),
                                       "z": round(float(pos.get("z", 0))) + dz,
                                       "item": item})
        if not r.get("ok"):
            return res_fail(r.get("reason") or "place_failed")
        ctx.session["deadline"] = time.time() + 8
        ctx.session["item"] = item
        return res_pending(describe=f"正在放 {item}")

    def poll(self, ctx):
        raw = ctx.bridge.get_action_result() or {}
        st = str(raw.get("status", "")).lower()
        if st == "done":
            return res_ok(describe=f"{ctx.session.get('item')} 放好了")
        if st == "failed":
            return res_fail(str(raw.get("detail", {}).get("reason") or "place_failed"))
        if time.time() > float(ctx.session.get("deadline", 0)):
            return res_fail("timeout")
        return res_pending(describe="放置中")


# 便利注册：按名放置的具体技能（认知层可直接用这些名字）。
# 用工厂注册，不走 @register 装饰器（避免类体阶段 name 为空的报错）。
def _register_named(item: str, name: str, desc: str):
    class _Named(PlaceNamedBlock):
        pass
    inst = _Named()
    inst.name = name
    inst.description = desc
    inst.category = "building"

    def start(self, ctx, params, _item=item):
        params = dict(params)
        params.setdefault("item", _item)
        return PlaceNamedBlock.start(self, ctx, params)

    inst.start = start.__get__(inst)
    from skills.base import REGISTRY
    REGISTRY[name] = inst


_register_named("oak_door", "place_door", "放一扇门")
_register_named("chest", "place_chest", "放一个箱子")
_register_named("crafting_table", "place_crafting_table", "放一个工作台")
_register_named("furnace", "place_furnace", "放一个熔炉")
_register_named("white_bed", "place_bed", "放一张床")
