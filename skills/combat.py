# skills/combat.py — H 战斗技能
# ============================================================================
# 分层红线（规范 §十H）：
#   认知层决定"要不要打"（ActionNode 由动机/威胁评估产生）；
#   技能层只负责"既然打了，怎么打"：锁定目标、接近、按冷却挥剑、
#   残血撤退、战后恢复——短时程实时控制全部在本层，零 LLM。
# 战斗主循环在 bot 侧（/combat，实时帧级控制）；Python 侧轮询回执 +
# 生存兜底（血量过低强制撤退）。
# ============================================================================

import time

from skills.base import Skill, register, res_ok, res_fail, res_pending
from skills.observation import HOSTILE_ENTITIES, PASSIVE_ANIMALS
from skills.inventory import choose_weapon, _inv_count


@register
class AttackEntity(Skill):
    name = "attack_entity"
    description = "战斗：锁定指定生物，接近并攻击（残血自动撤退）"
    category = "combat"

    def check(self, ctx, params):
        if not (params.get("entity") or params.get("target")):
            return False, "missing_target"
        return ctx.connected(), "not_connected"

    def start(self, ctx, params):
        entity = str(params.get("entity") or params.get("target"))
        # 武器：有剑拿剑（技能层的常识，不需要认知层操心）
        weapon = choose_weapon(ctx.inventory(refresh=True))
        if weapon:
            ctx.bridge.equip(weapon)
        retreat_health = float(params.get("retreat_health",
                                          ctx.config.get("combat_retreat_health", 8)))
        max_s = float(params.get("max_duration",
                                 ctx.config.get("combat_max_duration_s", 45)))
        r = ctx.bridge.call("/combat", {"entity": entity,
                                        "retreat_health": retreat_health,
                                        "max_ms": int(max_s * 1000)}, timeout=8)
        if not r.get("ok"):
            return res_fail(r.get("reason") or "combat_start_failed")
        ctx.session.update({"entity": entity, "retreat_health": retreat_health,
                            "started": time.time(),
                            "timeout_at": time.time() + max_s + 5})
        return res_pending(describe=f"开始战斗：{entity}" +
                           (f"（武器 {weapon}）" if weapon else "（徒手）"),
                           detail={"entity": entity, "weapon": weapon})

    def poll(self, ctx):
        s = ctx.state(refresh=True)
        # 生存兜底：bot 侧循环之上，Python 侧再查一次血量
        health = float(s.get("health", 20) or 20)
        if health <= float(ctx.session.get("retreat_health", 8)) - 2.0:
            self.cancel(ctx)
            ctx.bridge.call("/flee", {"distance": 10})
            return res_fail("retreated_low_health",
                            describe="血量太低，先撤了",
                            detail={"health": health})
        raw = ctx.bridge.get_action_result() or {}
        st = str(raw.get("status", "")).lower()
        detail = raw.get("detail") or {}
        if st == "done":
            killed = detail.get("killed")
            return res_ok(describe=(f"击败了 {ctx.session.get('entity')}"
                                    if killed else "战斗结束"),
                          detail=detail)
        if st == "failed":
            return res_fail(str(detail.get("reason") or "combat_failed"),
                            detail=detail)
        if st == "cancelled":
            return res_fail("combat_cancelled", detail=detail)
        if time.time() > float(ctx.session.get("timeout_at", 0)):
            self.cancel(ctx)
            return res_fail("timeout", detail=detail)
        return res_pending(describe=f"战斗中（{ctx.session.get('entity')}）",
                           detail=detail)

    def cancel(self, ctx):
        try:
            ctx.bridge.call("/stop_combat")
            ctx.bridge.stop_goto()
        except Exception:
            pass


@register
class HuntAnimal(AttackEntity):
    """猎动物：与战斗同执行面，但目标只能是被动动物。"""
    name = "hunt_animal"
    description = "猎捕动物获取食物（牛/猪/羊/鸡）"
    category = "combat"

    def check(self, ctx, params):
        target = str(params.get("entity") or params.get("target") or "")
        if not target:
            return False, "missing_target"
        if target in HOSTILE_ENTITIES:
            return False, "not_an_animal"
        return ctx.connected(), "not_connected"


@register
class EquipWeapon(Skill):
    name = "equip_weapon"
    description = "拿起背包里最好的武器"

    def start(self, ctx, params):
        w = choose_weapon(ctx.inventory(refresh=True))
        if not w:
            return res_fail("no_weapon_in_inventory")
        r = ctx.bridge.equip(w)
        if not r.get("ok"):
            return res_fail(r.get("reason") or "equip_failed")
        return res_ok(describe=f"拿起了 {w}", detail={"weapon": w})


@register
class EquipShield(Skill):
    name = "equip_shield"
    description = "副手装备盾牌"

    def start(self, ctx, params):
        if _inv_count(ctx.inventory(refresh=True), "shield") <= 0:
            return res_fail("item_not_in_inventory:shield")
        r = ctx.bridge.call("/equip_off", {"item": "shield"})
        if not r.get("ok"):
            return res_fail(r.get("reason") or "equip_failed")
        return res_ok(describe="举起了盾牌")


@register
class RetreatFromEntity(Skill):
    name = "retreat_from_entity"
    description = "边打边撤：与敌对生物拉开距离（风筝）"
    sustained = True
    category = "combat"

    def check(self, ctx, params):
        return bool(params.get("entity")), "missing_entity"

    def start(self, ctx, params):
        ctx.session.update({"entity": str(params["entity"]),
                            "distance": float(params.get("distance", 8)),
                            "deadline": time.time() + float(params.get("timeout", 30))})
        return res_pending(describe=f"与 {params['entity']} 拉开距离")

    def poll(self, ctx):
        if time.time() > float(ctx.session.get("deadline", 0)):
            return res_ok(describe="拉开距离完成")
        s = ctx.state(refresh=True)
        pos = s.get("position") or {}
        ent = next((e for e in (s.get("nearbyEntities") or [])
                    if e.get("name") == ctx.session.get("entity")), None)
        if not ent:
            return res_ok(describe="甩开了")
        rel = ent.get("rel") or {}
        d = max(1.0, float(ent.get("dist") or 1))
        want = float(ctx.session.get("distance", 8))
        if float(ent.get("dist") or 0) >= want:
            return res_ok(describe="已经拉开安全距离")
        tx = float(pos.get("x", 0)) - float(rel.get("dx", 0)) / d * 4.0
        tz = float(pos.get("z", 0)) - float(rel.get("dz", 0)) / d * 4.0
        ctx.bridge.stop_goto()
        ctx.bridge.goto_coords(tx, float(pos.get("y", 64)), tz)
        return res_pending(describe="后撤中")

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
        except Exception:
            pass


@register
class RecoverAfterCombat(Skill):
    name = "recover_after_combat"
    description = "战后恢复：吃食物 + 原地休整回血"
    category = "combat"

    def start(self, ctx, params):
        ctx.session["deadline"] = time.time() + float(params.get("timeout", 90))
        return self._step(ctx)

    def _step(self, ctx):
        s = ctx.state(refresh=True)
        health = float(s.get("health", 20) or 20)
        food = float(s.get("food", 20) or 20)
        if food < 18:
            out = EatFood().start(ctx, {})
            if out.get("status") == "failed":
                return res_ok(describe="没有食物，只能慢慢恢复",
                              detail={"health": health})
            ctx.session["phase"] = "eating"
            return res_pending(describe="战后先吃点东西")
        if health >= 18:
            return res_ok(describe="缓过来了", detail={"health": health})
        ctx.session["phase"] = "waiting"
        return res_pending(describe="休整恢复中")

    def poll(self, ctx):
        if time.time() > float(ctx.session.get("deadline", 0)):
            return res_ok(describe="战后休整结束")
        if ctx.session.get("phase") == "eating":
            out = EatFood().poll(ctx)
            if out.get("status") in ("done", "failed"):
                ctx.session["phase"] = "waiting"
            return res_pending(describe="进食中")
        return self._step(ctx)
