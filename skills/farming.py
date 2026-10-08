# skills/farming.py — J 农业技能
# ============================================================================
# 耕地 → 播种 → 收获 → 补种 的小闭环。作物位置不硬编码，每次现查。
# ============================================================================

import math
import time

from skills.base import (Skill, register, res_ok, res_fail, res_pending)
from skills.observation import _find_blocks, normalize_resource
from skills.inventory import _inv_count, choose_best_tool


@register
class TillSoil(Skill):
    name = "till_soil"
    description = "用锄头把附近泥土/草方块耕地"
    category = "farming"

    def start(self, ctx, params):
        hoes = [h for h in ("wooden_hoe", "stone_hoe", "iron_hoe", "diamond_hoe")
                if _inv_count(ctx.inventory(refresh=True), h) > 0]
        if not hoes:
            return res_fail("tool_missing:hoe",
                            describe="没有锄头，耕不了地")
        ctx.bridge.equip(hoes[0])
        target = None
        for blk in ("grass_block", "dirt"):
            found = _find_blocks(ctx, blk, 5, 4)
            if found:
                target = found[0]
                break
        if not target:
            return res_fail("no_soil_nearby")
        r = ctx.bridge.call("/interact", {"x": target["x"], "y": target.get("y", 0),
                                          "z": target["z"]})
        if not r.get("ok"):
            return res_fail("till_failed")
        return res_ok(describe="耕好了一块地", detail={"position": target})


@register
class PlantSeed(Skill):
    name = "plant_seed"
    description = "在耕地上播种（自动找耕地）"
    category = "farming"

    def start(self, ctx, params):
        seed = str(params.get("seed") or params.get("item") or params.get("target") or "")
        if not seed:
            return res_fail("missing_seed")
        if _inv_count(ctx.inventory(refresh=True), seed) <= 0:
            return res_fail(f"item_not_in_inventory:{seed}")
        farmland = _find_blocks(ctx, "farmland", 6, 4)
        if not farmland:
            return res_fail("no_farmland_nearby", describe="附近没有耕地（先用 till_soil）")
        ctx.bridge.equip(seed)
        f = farmland[0]
        r = ctx.bridge.call("/interact", {"x": f["x"], "y": f.get("y", 0),
                                          "z": f["z"]})
        if not r.get("ok"):
            return res_fail("plant_failed")
        return res_ok(describe=f"播下了 {seed}", detail={"seed": seed})


@register
class HarvestCrop(Skill):
    name = "harvest_crop"
    description = "收割并收集成熟作物（收完顺手补种）"
    category = "farming"

    def check(self, ctx, params):
        return ctx.connected(), "not_connected"

    def start(self, ctx, params):
        crop = normalize_resource(params.get("crop") or params.get("target") or "wheat")
        ctx.session.update({"crop": crop, "harvested": 0, "phase": "find",
                            "qty": max(1, int(params.get("quantity", 4))),
                            "timeout_at": time.time() + float(params.get("timeout", 120))})
        return self._advance(ctx)

    def _advance(self, ctx):
        s = ctx.session
        if time.time() > float(s.get("timeout_at", 0)):
            self.cancel(ctx)
            return res_ok(status="partial" if s.get("harvested") else "failed",
                          describe=f"收了 {s.get('harvested', 0)} 个（超时）")
        phase = s.get("phase")
        if phase == "find":
            crops = _find_blocks(ctx, s["crop"], 16, 8)
            if not crops:
                if s.get("harvested", 0) > 0:
                    self.cancel(ctx)
                    return res_ok(describe=f"收获了 {s['crop']} x{s['harvested']}",
                                  detail={"crop": s["crop"],
                                          "count": s["harvested"]})
                self.cancel(ctx)
                return res_fail("crop_not_found", detail={"crop": s["crop"]})
            pos = ctx.position() or {}
            import math as _m

            def d2(p):
                return _m.hypot(pos.get("x", 0) - p["x"], pos.get("z", 0) - p["z"])
            nearest = min(crops, key=d2)
            s["target_pos"] = nearest
            if d2(nearest) <= 4.0:
                s["phase"] = "break"
            else:
                s["phase"] = "approach"
                ctx.bridge.stop_goto()
                r = ctx.bridge.goto_coords(nearest["x"], nearest.get("y", 64),
                                           nearest["z"])
                if not r.get("ok"):
                    self.cancel(ctx)
                    return res_fail("no_path")
            return res_pending(describe=f"收割 {s['crop']} 中")
        if phase == "approach":
            pos = ctx.position() or {}
            target = s.get("target_pos") or {}
            if math.hypot(pos.get("x", 0) - target["x"],
                          pos.get("z", 0) - target["z"]) <= 4.0:
                s["phase"] = "break"
                return res_pending(describe="到作物旁了")
            raw = ctx.bridge.get_action_result() or {}
            if str(raw.get("status", "")).lower() == "failed":
                self.cancel(ctx)
                return res_fail("no_path")
            return res_pending(describe="走向作物")
        if phase == "break":
            p = s.get("target_pos") or {}
            r = ctx.bridge.call("/dig_pos", {"x": p.get("x", 0), "y": p.get("y", 0),
                                             "z": p.get("z", 0)})
            if not r.get("ok"):
                s["phase"] = "find"
                return res_pending(describe="这块没了，找下一块")
            s["dig_deadline"] = time.time() + 12
            s["phase"] = "break_wait"
            return res_pending(describe="收割中")
        if phase == "break_wait":
            raw = ctx.bridge.get_action_result() or {}
            st = str(raw.get("status", "")).lower()
            if st in ("done", "partial"):
                s["harvested"] = int(s.get("harvested", 0)) + 1
                ctx.bridge.call("/collect_item", {"radius": 4})
                if s["harvested"] >= int(s.get("qty", 4)):
                    self.cancel(ctx)
                    return res_ok(describe=f"收获了 {s['crop']} x{s['harvested']}",
                                  detail={"crop": s["crop"], "count": s["harvested"]})
                s["phase"] = "find"
                return res_pending(describe="继续收割")
            if st == "failed" or time.time() > float(s.get("dig_deadline", 0)):
                s["phase"] = "find"
            return res_pending(describe="收割中")
        return res_fail("bad_phase")

    def poll(self, ctx):
        return self._advance(ctx)

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
        except Exception:
            pass


# 具体作物别名（认知层可直接用）
def _register_crop(crop: str, name: str, desc: str):
    class _Crop(HarvestCrop):
        pass
    inst = _Crop()
    inst.name = name
    inst.description = desc
    inst.category = "farming"

    def start(self, ctx, params, _crop=crop):
        params = dict(params)
        params.setdefault("crop", _crop)
        return HarvestCrop.start(self, ctx, params)

    inst.start = start.__get__(inst)
    from skills.base import REGISTRY
    REGISTRY[name] = inst


_register_crop("wheat", "collect_wheat", "收小麦")
_register_crop("carrots", "collect_carrot", "收胡萝卜")
_register_crop("potatoes", "collect_potato", "收马铃薯")
_register_crop("beetroot", "collect_beetroot", "收甜菜")
_register_crop("sugar_cane", "collect_sugar_cane", "收甘蔗")


@register
class ReplantCrop(Skill):
    name = "replant_crop"
    description = "在收过的耕地上补种"

    def start(self, ctx, params):
        seed = str(params.get("seed") or params.get("target") or "")
        if not seed:
            return res_fail("missing_seed")
        return PlantSeed().start(ctx, {"seed": seed})
