# skills/crafting.py — E 工具制造与使用技能
# ============================================================================
# 配方表是技能层知识（认知层不需要背"木板+木棍=木镐"）。
# craft 流程：查配方 → 检查原料（缺 → missing_ingredients，认知层可学习
# "先砍树"）→ 需要工作台 → 找（没有 → needs_crafting_table）→ bot 侧合成。
# 配方不追求全覆盖——架构允许往 RECIPES 里加条目扩展。
# ============================================================================

import time

import world_prior as wp

from skills.base import Skill, register, res_ok, res_fail, res_pending
from skills.observation import _find_blocks
from skills.inventory import _inv_count

# ── 配方表（result → {count, ingredients, table}）─────────
# ingredients 允许 "any_log" 组（任意原木 1 组可互替）。
PLANKS = ("oak_planks", "spruce_planks", "birch_planks", "jungle_planks",
          "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks")
LOGS = ("oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log",
        "dark_oak_log", "mangrove_log", "cherry_log")

RECIPES = {
    # 木制阶段
    "oak_planks":     {"count": 4, "ingredients": {"any_log": 1}, "table": False},
    "stick":          {"count": 4, "ingredients": {"planks": 2}, "table": False},
    "crafting_table": {"count": 1, "ingredients": {"planks": 4}, "table": False},
    "wooden_pickaxe": {"count": 1, "ingredients": {"planks": 3, "stick": 2}, "table": True},
    "wooden_axe":     {"count": 1, "ingredients": {"planks": 3, "stick": 2}, "table": True},
    "wooden_shovel":  {"count": 1, "ingredients": {"planks": 1, "stick": 2}, "table": True},
    "wooden_hoe":     {"count": 1, "ingredients": {"planks": 2, "stick": 2}, "table": True},
    "wooden_sword":   {"count": 1, "ingredients": {"planks": 2, "stick": 1}, "table": True},
    # 石制阶段
    "stone_pickaxe":  {"count": 1, "ingredients": {"cobblestone": 3, "stick": 2}, "table": True},
    "stone_axe":      {"count": 1, "ingredients": {"cobblestone": 3, "stick": 2}, "table": True},
    "stone_shovel":   {"count": 1, "ingredients": {"cobblestone": 1, "stick": 2}, "table": True},
    "stone_hoe":      {"count": 1, "ingredients": {"cobblestone": 2, "stick": 2}, "table": True},
    "stone_sword":    {"count": 1, "ingredients": {"cobblestone": 2, "stick": 1}, "table": True},
    # 铁制阶段
    "iron_pickaxe":   {"count": 1, "ingredients": {"iron_ingot": 3, "stick": 2}, "table": True},
    "iron_axe":       {"count": 1, "ingredients": {"iron_ingot": 3, "stick": 2}, "table": True},
    "iron_sword":     {"count": 1, "ingredients": {"iron_ingot": 2, "stick": 1}, "table": True},
    "iron_shovel":    {"count": 1, "ingredients": {"iron_ingot": 1, "stick": 2}, "table": True},
    "iron_hoe":       {"count": 1, "ingredients": {"iron_ingot": 2, "stick": 2}, "table": True},
    "bucket":         {"count": 1, "ingredients": {"iron_ingot": 3}, "table": True},
    "shears":         {"count": 1, "ingredients": {"iron_ingot": 2}, "table": True},
    "flint_and_steel": {"count": 1, "ingredients": {"iron_ingot": 1, "flint": 1}, "table": True},
    "iron_helmet":    {"count": 1, "ingredients": {"iron_ingot": 5}, "table": True},
    "iron_chestplate": {"count": 1, "ingredients": {"iron_ingot": 8}, "table": True},
    "iron_leggings":  {"count": 1, "ingredients": {"iron_ingot": 7}, "table": True},
    "iron_boots":     {"count": 1, "ingredients": {"iron_ingot": 4}, "table": True},
    # 高级阶段
    "golden_pickaxe": {"count": 1, "ingredients": {"gold_ingot": 3, "stick": 2}, "table": True},
    "diamond_pickaxe": {"count": 1, "ingredients": {"diamond": 3, "stick": 2}, "table": True},
    "diamond_axe":    {"count": 1, "ingredients": {"diamond": 3, "stick": 2}, "table": True},
    "diamond_sword":  {"count": 1, "ingredients": {"diamond": 2, "stick": 1}, "table": True},
    "diamond_helmet": {"count": 1, "ingredients": {"diamond": 5}, "table": True},
    "diamond_chestplate": {"count": 1, "ingredients": {"diamond": 8}, "table": True},
    "diamond_leggings": {"count": 1, "ingredients": {"diamond": 7}, "table": True},
    "diamond_boots":  {"count": 1, "ingredients": {"diamond": 4}, "table": True},
    # 功能方块与杂物
    "furnace":        {"count": 1, "ingredients": {"cobblestone": 8}, "table": True},
    # 欠账补齐（2026-09-20）：床/盾牌/弓——睡眠链与防御链的入口
    "white_bed":      {"count": 1, "ingredients": {"white_wool": 3, "planks": 3}, "table": True},
    "shield":         {"count": 1, "ingredients": {"iron_ingot": 1, "planks": 6}, "table": True},
    "bow":            {"count": 1, "ingredients": {"stick": 3, "string": 3}, "table": True},
    "torch":          {"count": 4, "ingredients": {"coal": 1, "stick": 1}, "table": False},
    "chest":          {"count": 1, "ingredients": {"planks": 8}, "table": True},
    "oak_door":       {"count": 3, "ingredients": {"planks": 6}, "table": True},
    "oak_fence":      {"count": 3, "ingredients": {"planks": 4, "stick": 2}, "table": True},
}

# 中文别名
CRAFT_ALIASES = {
    "木板": "oak_planks", "木棍": "stick", "工作台": "crafting_table",
    "木镐": "wooden_pickaxe", "木斧": "wooden_axe", "木铲": "wooden_shovel",
    "木锄": "wooden_hoe", "木剑": "wooden_sword",
    "石镐": "stone_pickaxe", "石斧": "stone_axe", "石铲": "stone_shovel",
    "石锄": "stone_hoe", "石剑": "stone_sword",
    "铁镐": "iron_pickaxe", "铁斧": "iron_axe", "铁剑": "iron_sword",
    "铁铲": "iron_shovel", "铁锄": "iron_hoe", "铁桶": "bucket",
    "剪刀": "shears", "打火石": "flint_and_steel",
    "铁头盔": "iron_helmet", "铁胸甲": "iron_chestplate",
    "铁护腿": "iron_leggings", "铁靴子": "iron_boots",
    "金镐": "golden_pickaxe", "钻石镐": "diamond_pickaxe",
    "钻石斧": "diamond_axe", "钻石剑": "diamond_sword",
    "钻石头盔": "diamond_helmet", "钻石胸甲": "diamond_chestplate",
    "钻石护腿": "diamond_leggings", "钻石靴子": "diamond_boots",
    "熔炉": "furnace", "火把": "torch", "箱子": "chest",
    "床": "white_bed", "白床": "white_bed", "盾牌": "shield", "盾": "shield",
    "弓": "bow",
}


def resolve_recipe(item: str):
    item = CRAFT_ALIASES.get(str(item or "").strip(), str(item or "").strip())
    return item, RECIPES.get(item)


def _runtime_recipe(ctx, item: str):
    """硬编码表没收录的物品：查**运行时配方**（bot 侧 minecraft-data，版本=
    服务器协商版本，与先验闭包同一数据源）。返回与 RECIPES 同形的
    {"count", "ingredients", "table"}；查不到/桥不带该接口 → None。

    为什么要兜这一层（2026-09-25 真机）：规划器已改为读图（图上的配方来自
    /recipe_for 运行时数据，任意物品都能提出 craft 候选），执行层若还死守
    静态表，就会出现"计划要合成 acacia_planks、执行层报 unknown_recipe"的
    断层。配方不追求全覆盖的表结构保留，运行时数据补它的缺口。多条并列
    配方时挑原料缺口最小的那条（凑不齐也要挑最接近的，报缺如实）。"""
    f = getattr(ctx.bridge, "recipe_for", None)
    if f is None:
        return None
    try:
        r = f(item)
    except Exception:
        return None
    recs = (r or {}).get("recipes") or []
    if not recs:
        return None
    inv = ctx.inventory(refresh=True)

    def _gap(rec):
        need = {k: 1 for k in (rec.get("ingredients") or {})}
        return len(missing_ingredients(inv, {"ingredients": need}))

    best = sorted(recs, key=_gap)[0]
    return {"count": int(best.get("yield") or 1),
            "ingredients": dict(best.get("ingredients") or {}),
            "table": bool(best.get("needs_table"))}


def missing_ingredients(inv: list, recipe: dict) -> dict:
    """检查原料缺口。any_log/planks 是组别名（组内任意物品可抵扣）。"""
    need = dict(recipe.get("ingredients") or {})
    have = {}
    for i in inv:
        have[i.get("name")] = have.get(i.get("name"), 0) + int(i.get("count", 0))
    missing = {}
    for key, cnt in need.items():
        if key == "any_log":
            total = sum(have.get(l, 0) for l in LOGS)
        elif key == "planks":
            total = sum(have.get(p, 0) for p in PLANKS)
        else:
            total = have.get(key, 0)
        if total < cnt:
            missing[key] = cnt - total
    return missing


@register
class CraftItem(Skill):
    name = "craft_item"
    description = "合成物品（自动查配方/原料/工作台；bot 侧执行）"
    category = "crafting"

    def start(self, ctx, params):
        item, recipe = resolve_recipe(params.get("item") or params.get("target") or "")
        if not recipe:
            recipe = _runtime_recipe(ctx, item)
        if not recipe:
            return res_fail(f"unknown_recipe:{item}")
        count = max(1, int(params.get("count", 1)))
        inv = ctx.inventory(refresh=True)
        missing = missing_ingredients(inv, recipe)
        if missing:
            return res_fail("missing_ingredients",
                            describe=f"原料不够：{missing}",
                            detail={"item": item, "missing": missing})
        table_block = None
        if recipe.get("table"):
            tables = _find_blocks(ctx, "crafting_table", 8, 1)
            if not tables:
                return res_fail("needs_crafting_table",
                                describe=f"合成 {item} 需要工作台，附近没有",
                                detail={"item": item})
            table_block = tables[0]
        # 超时 45s（原 20）：26.1 下 bot.craft 走窗口事务，实测可超 20s——
        # 超时报失败但合成其实已完成，账面与背包错位（2026-09-26 真机）。
        r = ctx.bridge.call("/craft", {"item": item, "count": count,
                                       "table": table_block}, timeout=45)
        if not r.get("ok"):
            return res_fail(r.get("reason") or "craft_failed",
                            detail={"item": item})
        return res_ok(describe=f"合成了 {item} x{recipe['count'] * count // max(1, recipe['count'])}",
                      detail={"item": item, "count": count,
                              "crafted": r.get("crafted", count)})


@register
class CraftBatch(Skill):
    name = "craft_batch"
    description = "批量合成（count 由 params.count 决定）"

    def start(self, ctx, params):
        params = dict(params)
        params.setdefault("count", int(params.get("batch", 4)))
        return CraftItem().start(ctx, params)

    def poll(self, ctx):
        return res_ok(describe="合成完成")


@register
class OpenCraftingTable(Skill):
    name = "open_crafting_table"
    description = "打开附近的工作台"

    def start(self, ctx, params):
        tables = _find_blocks(ctx, "crafting_table", 6, 1)
        if not tables:
            return res_fail("crafting_table_not_found")
        t = tables[0]
        r = ctx.bridge.call("/interact", {"x": t["x"], "y": t.get("y", 0), "z": t["z"]})
        if not r.get("ok"):
            return res_fail(r.get("reason") or "interact_failed")
        return res_ok(describe="打开了工作台")


@register
class SmeltItem(Skill):
    name = "smelt_item"
    description = "熔炼物品（自动找熔炉、放燃料与原料）"
    category = "crafting"

    def start(self, ctx, params):
        item = str(params.get("item") or params.get("input") or params.get("target") or "")
        if not item:
            return res_fail("missing_item")
        inv = ctx.inventory(refresh=True)
        if _inv_count(inv, item) <= 0 and not params.get("use_chest"):
            return res_fail(f"item_not_in_inventory:{item}")
        need = max(1, int(params.get("count", 1)))
        fuel = str(params.get("fuel") or "")
        # 燃料选择是**世界机制知识**，不再由技能手抄品类清单
        # （2026-09-27 用户定："木头/煤炭可烧是公共知识，要通用"）——
        # 问 world_prior 的燃料表：精确名 + 木质后缀家族自动覆盖，
        # 并算出"烧得完这批货要几根"（一根木棍 5s < 熔一件 10s，
        # 放一根炉子先饿死，2026-09-27 真机 raw_iron 卡在炉里）。
        if not fuel:
            have_counts = {}
            for i in inv:
                nm = i.get("name")
                have_counts[nm] = have_counts.get(nm, 0) + int(i.get("count", 0))
            fuel, fuel_count = wp.best_fuel(have_counts, need)
            if not fuel:
                return res_fail("no_fuel",
                                describe="背包里没有烧得完这批货的燃料（木头/煤炭类）")
        else:
            _fc = _inv_count(inv, fuel)
            if _fc <= 0:
                return res_fail(f"fuel_not_in_inventory:{fuel}")
            fuel_count = max(1, min(wp.fuel_pieces(fuel, need), _fc))
        furnaces = []
        for ftype in ("furnace", "blast_furnace", "smoker"):
            furnaces = _find_blocks(ctx, ftype, 16, 1)
            if furnaces:
                params["_ftype"] = ftype
                break
        if not furnaces:
            return res_fail("furnace_not_found",
                            describe="附近没有熔炉/高炉/烟熏炉")
        r = ctx.bridge.call("/smelt", {"input": item, "fuel": fuel,
                                       "furnace": furnaces[0], "count": 1,
                                       "fuel_count": fuel_count}, timeout=25)
        if not r.get("ok"):
            return res_fail(r.get("reason") or "smelt_failed")
        return res_pending(describe=f"熔炼 {item} 中（去取结果还要等一会儿）")

    def poll(self, ctx):
        return res_ok(describe="熔炼进行中（之后用 furnace_take 取结果）")


@register
class FurnaceTake(Skill):
    name = "furnace_take"
    description = "从附近熔炉取出炼好的物品"

    def start(self, ctx, params):
        furnaces = []
        for ftype in ("furnace", "blast_furnace", "smoker"):
            furnaces = _find_blocks(ctx, ftype, 16, 1)
            if furnaces:
                break
        if not furnaces:
            return res_fail("furnace_not_found")
        r = ctx.bridge.call("/furnace_take", {"furnace": furnaces[0]}, timeout=25)
        if not r.get("ok"):
            return res_fail(r.get("reason") or "furnace_take_failed")
        taken = r.get("taken") or {}
        if not taken:
            # 没取到东西=没完成，不是成功（§9：产物必须经取出/背包实证）。
            # 如实失败，autonomy 的 smelt_pending 会保留并在 300s 窗口内重试
            # （熔炼约需 10-20s，早到的 take 本来就该扑空）。
            return res_fail("furnace_empty",
                            detail={"taken": {}})
        return res_ok(describe=f"取出了 {taken.get('name')} x{taken.get('count', 0)}",
                      detail={"taken": taken})


@register
class UseFurnace(Skill):
    name = "use_furnace"
    description = "打开附近熔炉"

    def start(self, ctx, params):
        furnaces = _find_blocks(ctx, "furnace", 8, 1)
        if not furnaces:
            return res_fail("furnace_not_found")
        f = furnaces[0]
        r = ctx.bridge.call("/interact", {"x": f["x"], "y": f.get("y", 0), "z": f["z"]})
        if not r.get("ok"):
            return res_fail(r.get("reason") or "interact_failed")
        return res_ok(describe="打开了熔炉")
