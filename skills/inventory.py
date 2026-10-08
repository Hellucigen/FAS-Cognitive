# skills/inventory.py — F 背包与物品管理技能
# ============================================================================
# choose_best_tool(block) 是本模块的核心决策函数：技能层自己知道
# "挖铁矿需要石镐以上"——认知层永远不需要背工具表。
# ============================================================================

from skills.base import Skill, register, res_ok, res_fail
from skills.observation import PICKAXE_REQUIREMENT, PICK_TIER, FOODS


def _inv_count(inv: list, name: str) -> int:
    for i in inv:
        if i.get("name") == name:
            return int(i.get("count", 0))
    return 0


def _usable(item: dict) -> bool:
    """耐久可用判断：dur_percent 缺失按满耐久（旧桥兼容）。快断=不可用。"""
    dp = item.get("dur_percent")
    if dp is None:
        return True
    try:
        return float(dp) > 8.0        # 剩不到 8% 不再指望它
    except (TypeError, ValueError):
        return True


def best_pickaxe(inv: list):
    """背包里最好的**可用**镐（按挖掘等级；快断的不用）。"""
    picks = [(PICK_TIER.get(i.get("name"), 0), i.get("name"))
             for i in inv if i.get("name") in PICK_TIER and _usable(i)]
    if not picks:
        return None
    picks.sort(reverse=True)
    return picks[0][1]


def choose_best_tool(ctx, block_name: str):
    """block → (tool_name|None, fail_reason|None)。

    返回 tool=None 且 reason 非空 = 无法采集（认知层会把这个原因记进
    经验时间轴，下次"想挖铁没镐"在候选阶段就会被预期惩罚压下去）。
    """
    name = str(block_name or "")
    need = PICKAXE_REQUIREMENT.get(name)
    inv = ctx.inventory()
    if need is None:
        return None, ""            # 徒手可挖（泥土/沙子/原木…）
    have = best_pickaxe(inv)
    if have is None:
        # 有镐但快断了 → 也是 tool_missing（附 durability 原因，认知层可学）
        if any(i.get("name") in PICK_TIER for i in inv):
            return None, "tool_worn_out"
        return None, f"tool_missing:{need}"
    if PICK_TIER.get(have, 0) < PICK_TIER.get(need, 0):
        return None, f"tool_missing:{need}"
    return have, ""


def equip_best(ctx, tool: str) -> bool:
    if not tool:
        return True
    r = ctx.bridge.equip(tool)
    return bool(r.get("ok"))


def choose_food(inv: list):
    """从背包里挑最合适的食物（优先熟食，不挑金苹果留着）。"""
    for f in FOODS:
        if f == "golden_apple":
            continue
        for i in inv:
            if i.get("name") == f and int(i.get("count", 0)) > 0:
                return i.get("name")
    for i in inv:
        if i.get("name") == "golden_apple" and int(i.get("count", 0)) > 0:
            return "golden_apple"
    return None


def choose_weapon(inv: list):
    """挑武器：剑 > 斧（同类型取质料最好的）。"""
    order = ["netherite_sword", "diamond_sword", "iron_sword", "stone_sword",
             "golden_sword", "wooden_sword", "netherite_axe", "diamond_axe",
             "iron_axe", "stone_axe", "golden_axe", "wooden_axe"]
    names = {i.get("name") for i in inv if int(i.get("count", 0)) > 0 and _usable(i)}
    for w in order:
        if w in names:
            return w
    return None


@register
class CountItem(Skill):
    name = "count_item"
    description = "数背包里某物品的数量"
    category = "inventory"

    def start(self, ctx, params):
        name = str(params.get("item") or params.get("target") or "")
        if not name:
            return res_fail("missing_item")
        inv = ctx.inventory(refresh=True)
        n = _inv_count(inv, name)
        return res_ok(describe=f"背包里有 {name} x{n}",
                      detail={"item": name, "count": n})


@register
class FindItem(Skill):
    name = "find_item"
    description = "在背包中找某物品（数量+是否有）"
    category = "inventory"

    def start(self, ctx, params):
        name = str(params.get("item") or params.get("target") or "")
        inv = ctx.inventory(refresh=True)
        hit = next((i for i in inv if name and name in str(i.get("name"))), None)
        if not hit:
            return res_ok(describe=f"背包里没有 {name}",
                          detail={"item": name, "found": False})
        return res_ok(describe=f"找到了 {hit.get('name')} x{hit.get('count')}",
                      detail={"item": hit.get("name"), "found": True,
                              "count": hit.get("count")})


@register
class EquipItem(Skill):
    name = "equip_item"
    description = "手持/装备指定物品"
    category = "inventory"

    def start(self, ctx, params):
        name = str(params.get("item") or params.get("target") or "")
        if not name:
            return res_fail("missing_item")
        r = ctx.bridge.equip(name)
        if not r.get("ok"):
            reason = r.get("reason") or "equip_failed"
            if reason == "item_not_in_inventory":
                return res_fail(f"item_not_in_inventory:{name}")
            return res_fail(reason)
        return res_ok(describe=f"拿起了 {name}")


@register
class UnequipItem(Skill):
    name = "unequip_item"
    description = "放下手中的物品（收进背包）"
    category = "inventory"

    def start(self, ctx, params):
        r = ctx.bridge.call("/unequip")
        if not r.get("ok"):
            return res_fail(r.get("reason") or "unequip_failed")
        return res_ok(describe="收起了手里的东西")


@register
class DropItem(Skill):
    name = "drop_item"
    description = "丢掉指定物品若干个（清理背包/给玩家）"
    category = "inventory"

    def start(self, ctx, params):
        name = str(params.get("item") or params.get("target") or "")
        count = int(params.get("count", 1))
        if not name:
            return res_fail("missing_item")
        r = ctx.bridge.call("/drop", {"item": name, "count": count})
        if not r.get("ok"):
            return res_fail(r.get("reason") or "drop_failed")
        return res_ok(describe=f"丢掉了 {name} x{count}")


@register
class PickupItem(Skill):
    name = "pickup_item"
    description = "捡起脚边的掉落物（与 collect_dropped_item 同执行面）"
    category = "inventory"

    def start(self, ctx, params):
        from skills.base import get as get_skill
        return get_skill("collect_dropped_item").start(ctx, params)

    def poll(self, ctx):
        from skills.base import get as get_skill
        return get_skill("collect_dropped_item").poll(ctx)


@register
class DetectInventoryFull(Skill):
    name = "detect_inventory_full"
    description = "检查背包是否快满（空格 < 2）"
    category = "inventory"

    def start(self, ctx, params):
        # /inventory_slots 是 GET 路由（bot.js），call() 走 POST 必 404——
        # 用 bridge 的 GET 包装（顺带修复 2026-09-23）
        r = ctx.bridge.inventory_slots()
        free = int(r.get("free", 0)) if r.get("ok") else 0
        full = free < 2
        return res_ok(describe=("背包快满了" if full else f"背包还有 {free} 格空位"),
                      detail={"free_slots": free, "full": full})


@register
class SortInventory(Skill):
    name = "sort_inventory"
    description = "整理背包（同类物品合并堆叠）"
    category = "inventory"

    def start(self, ctx, params):
        r = ctx.bridge.call("/sort_inventory")
        if not r.get("ok"):
            return res_fail(r.get("reason") or "sort_failed")
        return res_ok(describe="整理好了背包")


@register
class ChestStore(Skill):
    name = "chest_store"
    description = "把背包杂物存进附近箱子（工具/武器/食物默认保留）"
    category = "inventory"

    def start(self, ctx, params):
        r = ctx.bridge.chest_store(keep=params.get("keep") or [],
                                   all_items=bool(params.get("all")))
        if not r.get("ok"):
            return res_fail(r.get("reason") or "chest_failed")
        moved = int(r.get("moved", 0))
        ctx.invalidate()
        return res_ok(describe=f"存进箱子 {moved} 件" if moved else "箱子就在旁边，没有要存的东西",
                      detail={"moved": moved})


@register
class ChestTake(Skill):
    name = "chest_take"
    description = "从附近箱子里取回指定物品"
    category = "inventory"

    def start(self, ctx, params):
        item = str(params.get("item") or params.get("target") or "")
        if not item:
            return res_fail("missing_item")
        r = ctx.bridge.chest_take(item, int(params.get("count", 1)))
        if not r.get("ok"):
            return res_fail(r.get("reason") or "chest_failed")
        ctx.invalidate()
        return res_ok(describe=f"从箱子里取回了 {item}")


@register
class ChooseBestTool(Skill):
    name = "choose_best_tool"
    description = "为方块选最合适的工具（技能层决策，不调 LLM）"
    category = "inventory"

    def start(self, ctx, params):
        block = str(params.get("block") or params.get("target") or "")
        if not block:
            return res_fail("missing_block")
        tool, why = choose_best_tool(ctx, block)
        if tool is None and why:
            return res_fail(why, detail={"block": block})
        return res_ok(describe=(f"挖 {block} 用 {tool or '徒手'}"),
                      detail={"block": block, "tool": tool or "hand"})


@register
class ChooseFood(Skill):
    name = "choose_food"
    description = "从背包里挑食物"

    def start(self, ctx, params):
        food = choose_food(ctx.inventory(refresh=True))
        if not food:
            return res_fail("no_food_in_inventory")
        return res_ok(describe=f"可以吃 {food}", detail={"food": food})


@register
class ChooseWeapon(Skill):
    name = "choose_weapon"
    description = "从背包里挑武器"

    def start(self, ctx, params):
        w = choose_weapon(ctx.inventory(refresh=True))
        if not w:
            return res_fail("no_weapon_in_inventory")
        return res_ok(describe=f"武器是 {w}", detail={"weapon": w})
