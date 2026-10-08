# skills/animals.py — K 动物与食物技能
# ============================================================================
# find_animal / approach_animal / attack_animal / collect_drop /
# feed_animal / breed_animal。攻击执行面复用 combat.HuntAnimal；
# 喂食/繁殖用 interact_entity（右键）。
# ============================================================================

import math
import time

from skills.base import (Skill, register, res_ok, res_fail, res_pending)
from skills.observation import PASSIVE_ANIMALS
from skills.inventory import _inv_count


def _find_animal(ctx, want: str = ""):
    for e in ctx.state(refresh=True).get("nearbyEntities") or []:
        nm = str(e.get("name") or "")
        if nm in PASSIVE_ANIMALS and (not want or want in (nm, e.get("displayName"))):
            return e
    return None


@register
class FindAnimal(Skill):
    name = "find_animal"
    description = "找附近的动物（牛/猪/羊/鸡…）"
    category = "animals"

    def start(self, ctx, params):
        want = str(params.get("animal") or params.get("target") or "")
        ent = _find_animal(ctx, want)
        if not ent:
            return res_ok(describe="附近没有看到动物",
                          detail={"found": False, "animal": want or None})
        return res_ok(describe=f"看到了 {ent.get('name')}（{ent.get('dist')} 格）",
                      detail={"found": True, "animal": ent.get("name"),
                              "dist": ent.get("dist"), "rel": ent.get("rel")})


@register
class ApproachAnimal(Skill):
    name = "approach_animal"
    description = "走近动物（保持 2.5 格）"
    category = "animals"

    def start(self, ctx, params):
        want = str(params.get("animal") or params.get("entity") or params.get("target") or "")
        ent = _find_animal(ctx, want)
        if not ent:
            return res_fail("animal_not_visible")
        pos = ctx.position() or {}
        rel = ent.get("rel") or {}
        if not rel:
            return res_fail("no_target_coords")
        dist = float(ent.get("dist") or 1) or 1.0
        scale = max(0.0, dist - 2.5) / dist
        ctx.bridge.stop_goto()
        r = ctx.bridge.goto_coords(float(pos.get("x", 0)) + float(rel.get("dx", 0)) * scale,
                                   float(pos.get("y", 64)),
                                   float(pos.get("z", 0)) + float(rel.get("dz", 0)) * scale)
        if not r.get("ok"):
            return res_fail("no_path")
        ctx.session["deadline"] = time.time() + 40
        return res_pending(describe=f"走向 {ent.get('name')}")

    def poll(self, ctx):
        raw = ctx.bridge.get_action_result() or {}
        st = str(raw.get("status", "")).lower()
        if st in ("done", "partial"):
            return res_ok(describe="走近了")
        if st == "failed":
            return res_fail("no_path")
        if time.time() > float(ctx.session.get("deadline", 0)):
            return res_fail("timeout")
        return res_pending(describe="接近中")

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
        except Exception:
            pass


@register
class AttackAnimal(Skill):
    name = "attack_animal"
    description = "攻击指定动物（复用战斗执行面；认知层决定要不要猎）"
    category = "animals"

    def start(self, ctx, params):
        from skills.combat import HuntAnimal
        params = dict(params)
        params.setdefault("entity", params.get("animal") or params.get("target") or "")
        return HuntAnimal().start(ctx, params)

    def poll(self, ctx):
        from skills.combat import HuntAnimal
        return HuntAnimal().poll(ctx)

    def cancel(self, ctx):
        from skills.combat import HuntAnimal
        HuntAnimal().cancel(ctx)


@register
class CollectDrop(Skill):
    name = "collect_drop"
    description = "收集脚边的掉落物"
    category = "animals"

    def start(self, ctx, params):
        from skills.blocks import CollectDroppedItem
        return CollectDroppedItem().start(ctx, params)

    def poll(self, ctx):
        from skills.blocks import CollectDroppedItem
        return CollectDroppedItem().poll(ctx)


@register
class FeedAnimal(Skill):
    name = "feed_animal"
    description = "用食物喂动物（右键交互）"
    category = "animals"

    def start(self, ctx, params):
        animal = str(params.get("animal") or params.get("entity") or "")
        food = str(params.get("food") or "")
        if not animal:
            return res_fail("missing_animal")
        if not food:
            return res_fail("missing_food")
        if _inv_count(ctx.inventory(refresh=True), food) <= 0:
            return res_fail(f"item_not_in_inventory:{food}")
        ctx.bridge.equip(food)
        r = ctx.bridge.call("/interact_entity", {"entity": animal})
        if not r.get("ok"):
            return res_fail(r.get("reason") or "entity_not_visible")
        return res_ok(describe=f"喂了 {animal}", detail={"animal": animal, "food": food})


@register
class BreedAnimal(Skill):
    name = "breed_animal"
    description = "喂两只同类动物让它们繁殖（食物自动按动物选）"
    category = "animals"

    BREED_FOOD = {"cow": "wheat", "sheep": "wheat", "pig": "carrot",
                  "chicken": "wheat_seeds", "rabbit": "carrot"}

    def start(self, ctx, params):
        animal = str(params.get("animal") or params.get("target") or "")
        if not animal:
            return res_fail("missing_animal")
        food = str(params.get("food") or self.BREED_FOOD.get(animal, "wheat"))
        if _inv_count(ctx.inventory(refresh=True), food) < 2:
            return res_fail(f"item_not_in_inventory:{food}x2",
                            describe=f"繁殖需要两份 {food}")
        ents = [e for e in ctx.state(refresh=True).get("nearbyEntities") or []
                if e.get("name") == animal]
        if len(ents) < 2:
            return res_fail("not_enough_animals",
                            describe=f"附近只看到 {len(ents)} 只 {animal}，繁殖要两只")
        ctx.bridge.equip(food)
        for e in ents[:2]:
            r = ctx.bridge.call("/interact_entity", {"entity": animal})
            if not r.get("ok"):
                return res_fail(r.get("reason") or "feed_failed")
            time.sleep(0.6)
        return res_ok(describe=f"喂了两只 {animal}，看看会不会繁殖",
                      detail={"animal": animal, "food": food})
