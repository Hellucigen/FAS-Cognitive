# skills/observation.py — B 环境观察技能 + 世界事件检测
# ============================================================================
# 观察类技能全部读 Mineflayer 已有状态（/state /find_blocks /inventory），
# 零 LLM、零图操作——把"看到什么"如实返回给 Action 层，图谱激活由
# 具身层的事件桥完成（minecraft.embodiment._apply_event_activations）。
#
# detect_environment_events() 是自主认知的事件源：把世界快照差分翻译成
# 结构化事件（资源发现/威胁/生存状态/时段天气/社交在场…），认知层据此
# 走 事件 → 图激活 → 需求/动机 → 行为倾向 → 候选 Action 的通路。
# ============================================================================

import math
import time

from skills.base import Skill, register, res_ok, res_fail

# 常见敌对生物（世界名 → 分类）。真相源统一 2026-09-20：优先读
# config["hostile_entities"]（感知标注/gates/危险检测共用同一名单），
# 此常量仅作 config 缺位的兜底（离线单测）。
HOSTILE_ENTITIES = {
    "zombie", "skeleton", "creeper", "spider", "cave_spider", "enderman",
    "witch", "slime", "pillager", "husk", "drowned", "stray", "phantom",
    "zombie_villager", "silverfish", "guardian", "blaze", "ghast",
}


def hostile_set(ctx):
    try:
        cfg_list = (getattr(ctx, "config", None) or {}).get(
            "hostile_entities")
        if cfg_list:
            return {str(x).lower() for x in cfg_list}
    except Exception:
        pass
    return set(HOSTILE_ENTITIES)
# 常见被动动物
PASSIVE_ANIMALS = {"cow", "pig", "sheep", "chicken", "rabbit", "horse",
                   "donkey", "goat", "mooshroom", "llama"}
# 常见食物（优先级从高到低——够吃就行，不是美食评分）
FOODS = [
    "cooked_beef", "cooked_porkchop", "cooked_mutton", "cooked_chicken",
    "cooked_rabbit", "cooked_cod", "cooked_salmon", "bread", "baked_potato",
    "apple", "golden_apple", "beef", "porkchop", "mutton", "chicken",
    "rabbit", "cod", "salmon", "carrot", "potato", "beetroot", "melon_slice",
    "pumpkin_pie", "cookie", "sweet_berries", "rotten_flesh",
]
# 结构特征方块（detect_structure 用）
STRUCTURE_BLOCKS = {
    "crafting_table": "工作台", "furnace": "熔炉", "chest": "箱子",
    "bell": "村庄钟", "bookshelf": "书架", "loom": "织布机",
    "smoker": "烟熏炉", "blast_furnace": "高炉", "barrel": "木桶",
    "villager": "村民",
}

# 资源方块 → 挖掘所需最低镐（choose_best_tool / gather 前置检查）
PICKAXE_REQUIREMENT = {
    "coal_ore": "wooden_pickaxe", "stone": "wooden_pickaxe",
    "cobblestone": "wooden_pickaxe", "copper_ore": "stone_pickaxe",
    "iron_ore": "stone_pickaxe", "lapis_ore": "stone_pickaxe",
    "gold_ore": "iron_pickaxe", "diamond_ore": "iron_pickaxe",
    "redstone_ore": "iron_pickaxe", "emerald_ore": "iron_pickaxe",
    "deepslate_coal_ore": "wooden_pickaxe", "deepslate_iron_ore": "stone_pickaxe",
    "deepslate_gold_ore": "iron_pickaxe", "deepslate_diamond_ore": "iron_pickaxe",
}

PICK_TIER = {"wooden_pickaxe": 1, "stone_pickaxe": 2, "iron_pickaxe": 3,
             "golden_pickaxe": 2, "diamond_pickaxe": 4, "netherite_pickaxe": 5}

# 中文名 → 世界方块/资源名（用户/LLM 说中文）
RESOURCE_ALIASES = {
    "木头": "oak_log", "原木": "oak_log", "橡木": "oak_log", "树": "oak_log",
    "石头": "stone", "圆石": "cobblestone", "泥土": "dirt", "沙子": "sand",
    "砂砾": "gravel", "煤矿": "coal_ore", "煤": "coal_ore",
    "铁矿": "iron_ore", "铁": "iron_ore", "铜矿": "copper_ore", "铜": "copper_ore",
    "金矿": "gold_ore", "金": "gold_ore", "钻石矿": "diamond_ore",
    "钻石": "diamond_ore", "红石矿": "redstone_ore", "红石": "redstone_ore",
    "青金石矿": "lapis_ore", "青金石": "lapis_ore", "绿宝石矿": "emerald_ore",
    "绿宝石": "emerald_ore", "羊毛": "wool", "小麦": "wheat",
    "胡萝卜": "carrot", "马铃薯": "potato", "土豆": "potato",
    "甜菜": "beetroot", "甘蔗": "sugar_cane", "村庄": "village",
    "洞穴": "cave", "水": "water", "熔岩": "lava", "岩浆": "lava",
}


def normalize_resource(name: str) -> str:
    n = str(name or "").strip()
    if n in RESOURCE_ALIASES:
        return RESOURCE_ALIASES[n]
    # 描述性说法兜底（"一些泥土"→泥土→dirt）：取最长命中的表键，纯词表归一
    best = ""
    for k in RESOURCE_ALIASES:
        if len(k) >= 2 and k in n and len(k) > len(best):
            best = k
    return RESOURCE_ALIASES.get(best, n)


# 名称组（环境事实，不是行为规则）：口语 "tree/wood" 在方块注册表里没有唯一
# 指向——各族原木都是它的成员。执行层（gather）按组各查一次 find_blocks 取
# 最近者；纯命名学，不含"该不该做"的判断。
_LOG_VARIANTS = ("oak_log", "spruce_log", "birch_log", "jungle_log",
                 "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log",
                 "log", "wood")
RESOURCE_GROUPS = {
    "tree": _LOG_VARIANTS, "wood": _LOG_VARIANTS,
    "树": _LOG_VARIANTS, "木头": _LOG_VARIANTS, "原木": _LOG_VARIANTS,
}


def resolve_variants(name):
    """任意资源名 → (展示名, 候选方块 tuple)。组名展开为成员，其余单候选。
    组判定在别名之前：口语"树/木头/原木"（含描述性说法）指的是原木族，
    不是某一种原木；"橡木"等特指名不含组键，仍走别名精确映射。"""
    n = str(name or "").strip()
    g = RESOURCE_GROUPS.get(n) or RESOURCE_GROUPS.get(n.lower())
    if not g:
        for k in RESOURCE_GROUPS:
            if len(k) >= 2 and (k in n or k in n.lower()):
                g = RESOURCE_GROUPS[k]
                break
    if g:
        return n, g
    single = normalize_resource(n)
    return single, (single,)


def _dist2d(a: dict, b: dict) -> float:
    try:
        return math.hypot(float(a.get("x", 0)) - float(b.get("x", 0)),
                          float(a.get("z", 0)) - float(b.get("z", 0)))
    except (TypeError, ValueError):
        return 1e9


def time_of_day_label(timeofday) -> str:
    try:
        t = int(timeofday) % 24000
    except (TypeError, ValueError):
        return "unknown"
    if 0 <= t < 12000:
        return "day"
    if 12000 <= t < 13000:
        return "sunset"
    if 13000 <= t < 23000:
        return "night"
    return "sunrise"


# ── 事件检测（世界快照 → 结构化事件列表）─────────────────

def detect_environment_events(ctx, prev_state: dict = None) -> list:
    """世界快照 → 事件列表（不做任何行动决策；认知层消费）。

    事件形如 {"type": "resource_detected", "resource": "iron_ore",
              "pos": {...}, "urgency": 0.4, "salience": ...}
    认知通路：事件 → 图节点激活 → 需求/动机 → 行为倾向 → 候选 Action。
    """
    s = ctx.state()
    if not s.get("connected"):
        return []
    pos = s.get("position") or {}
    events = []
    health = float(s.get("health", 20) or 20)
    food = float(s.get("food", 20) or 20)

    # ── 资源发现：附近方块里的有价值资源 ──
    valuable = {"coal_ore": 0.35, "iron_ore": 0.5, "copper_ore": 0.4,
                "gold_ore": 0.6, "diamond_ore": 0.85, "redstone_ore": 0.5,
                "lapis_ore": 0.45, "emerald_ore": 0.7}
    blocks = s.get("nearbyBlocks") or []
    for b in blocks:
        name = b.get("name")
        if name in valuable:
            events.append({"type": "resource_detected", "resource": name,
                           "count": b.get("count", 1),
                           "urgency": valuable[name] * 0.6, "pos": pos})
    logs = [b for b in blocks if str(b.get("name", "")).endswith("_log")]
    if logs:
        events.append({"type": "resource_detected", "resource": logs[0]["name"],
                       "count": logs[0].get("count", 1), "urgency": 0.3, "pos": pos})

    # ── 威胁：敌对生物接近 ──
    _hostiles = hostile_set(ctx)
    for e in s.get("nearbyEntities", []) or []:
        nm = str(e.get("name") or "")
        d = float(e.get("dist") or 99)
        if nm in _hostiles:
            events.append({"type": "hostile_detected", "entity": nm,
                           "dist": d, "pos": pos,
                           "urgency": max(0.3, 1.0 - d / 16.0) * (1.2 if health <= 10 else 1.0)})

    # ── 生存状态 ──
    if health <= 10:
        events.append({"type": "health_low", "health": health, "urgency": 0.9,
                       "pos": pos})
    if food <= 6:
        events.append({"type": "hunger_low", "food": food, "urgency": 0.8,
                       "pos": pos})
    elif food <= 12:
        events.append({"type": "hunger_moderate", "food": food, "urgency": 0.4,
                       "pos": pos})
    if bool(s.get("inWater")):
        events.append({"type": "in_water", "urgency": 0.3, "pos": pos})

    # ── 动物（食物机会）──
    animals = [e for e in s.get("nearbyEntities", []) or []
               if str(e.get("name") or "") in PASSIVE_ANIMALS]
    if animals and food <= 14:
        events.append({"type": "animal_nearby", "animal": animals[0].get("name"),
                       "dist": animals[0].get("dist"), "urgency": 0.35, "pos": pos})

    # ── 时段/天气 ──
    tod = time_of_day_label(s.get("timeOfDay"))
    prev_tod = time_of_day_label((prev_state or {}).get("timeOfDay"))
    if tod != prev_tod:
        events.append({"type": "time_changed", "to": tod, "urgency":
                       0.6 if tod == "night" else 0.2, "pos": pos})
    raining = bool(s.get("isRaining"))
    if raining != bool((prev_state or {}).get("isRaining", raining)):
        events.append({"type": "weather_changed", "to": "rain" if raining else "clear",
                       "urgency": 0.15, "pos": pos})

    # ── 社交：玩家出现/离开 ──
    players = {p.get("name") for p in s.get("playersNearby", []) or []}
    prev_players = {p.get("name") for p in (prev_state or {}).get("playersNearby", []) or []}
    for nm in sorted(players - prev_players):
        events.append({"type": "player_arrived", "player": nm, "urgency": 0.5, "pos": pos})
    for nm in sorted(prev_players - players):
        events.append({"type": "player_left", "player": nm, "urgency": 0.4, "pos": pos})

    # ── 掉落物（捡拾机会）──
    items = [e for e in s.get("nearbyEntities", []) or []
             if str(e.get("name") or "") == "item" and float(e.get("dist") or 99) <= 6]
    if items:
        events.append({"type": "item_on_ground", "dist": items[0].get("dist"),
                       "urgency": 0.4, "pos": pos})

    # ── 结构发现 ──
    struct_hits = []
    for b in blocks:
        if b.get("name") in STRUCTURE_BLOCKS:
            struct_hits.append(b["name"])
    for e in s.get("nearbyEntities", []) or []:
        if e.get("name") == "villager":
            struct_hits.append("villager")
    if struct_hits:
        events.append({"type": "structure_detected", "structures": sorted(set(struct_hits)),
                       "urgency": 0.45, "pos": pos})

    return events


# ── 观察技能（Action 层调用；结果进 detail 供认知消费）────

def _find_blocks(ctx, name: str, radius: int, count: int) -> list:
    r = ctx.bridge.call("/find_blocks", {"block": name, "radius": radius,
                                         "count": count})
    return (r.get("positions") or []) if r.get("ok") else []


class _InspectBase(Skill):
    category = "observation"

    def poll(self, ctx):
        return res_ok(describe="观察完成")


@register
class InspectBlock(_InspectBase):
    name = "inspect_block"
    description = "查看附近某种方块（数量/最近位置/距离）"

    def start(self, ctx, params):
        name = normalize_resource(params.get("block") or params.get("target") or "")
        if not name:
            return res_fail("missing_block")
        radius = int(params.get("radius", 12))
        positions = _find_blocks(ctx, name, radius, 8)
        if not positions:
            return res_ok(describe=f"附近 {radius} 格内没有 {name}",
                          detail={"block": name, "found": 0})
        pos = ctx.position()
        nearest = min(positions, key=lambda p: _dist2d(p, pos))
        return res_ok(describe=f"附近有 {len(positions)} 处 {name}，最近 {round(_dist2d(nearest, pos),1)} 格",
                      detail={"block": name, "found": len(positions),
                              "nearest": nearest, "positions": positions[:8]})


@register
class InspectEntity(_InspectBase):
    name = "inspect_entity"
    description = "查看附近某种/最近生物（看过去 + 距离/相对方位）"

    def start(self, ctx, params):
        s = ctx.state()
        want = params.get("entity") or params.get("target")
        ents = s.get("nearbyEntities", []) or []
        if want:
            ents = [e for e in ents if str(want) in (e.get("name"), e.get("displayName"))]
        if not ents:
            return res_ok(describe=f"没有看到{want or '生物'}",
                          detail={"entity": want, "found": 0})
        e = ents[0]
        # 具身观察 = 真的把视线转过去（不只是一条数据）
        pos = s.get("position") or {}
        rel = e.get("rel") or {}
        if rel and pos:
            try:
                ctx.bridge.look_at(float(pos.get("x", 0)) + float(rel.get("dx", 0)),
                                   float(pos.get("y", 0)) + float(rel.get("dy", 0)) + 1.0,
                                   float(pos.get("z", 0)) + float(rel.get("dz", 0)))
            except Exception:
                pass
        return res_ok(describe=f"看到{e.get('displayName') or e.get('name')}在 {e.get('dist')} 格外",
                      detail={"entity": e.get("name"), "dist": e.get("dist"),
                              "rel": e.get("rel")})


@register
class InspectArea(_InspectBase):
    name = "inspect_area"
    description = "环顾四周（可见方块/生物/玩家/时段/天气摘要）"

    def start(self, ctx, params):
        s = ctx.state()
        summary = {
            "position": s.get("position"),
            "health": s.get("health"), "food": s.get("food"),
            "held": s.get("heldItem"),
            "time": time_of_day_label(s.get("timeOfDay")),
            "weather": "rain" if s.get("isRaining") else "clear",
            "blocks": [b.get("name") for b in (s.get("nearbyBlocks") or [])][:8],
            "entities": [e.get("name") for e in (s.get("nearbyEntities") or [])][:6],
            "players": [p.get("name") for p in (s.get("playersNearby") or [])],
        }
        return res_ok(describe="观察了周围", detail=summary)


@register
class DetectHostile(_InspectBase):
    name = "detect_hostile"
    description = "检查附近敌对生物（最近距离）"

    def start(self, ctx, params):
        s = ctx.state()
        hostiles = [e for e in (s.get("nearbyEntities") or [])
                    if str(e.get("name") or "") in hostile_set(ctx)]
        if not hostiles:
            return res_ok(describe="附近没有敌对生物",
                          detail={"hostile": None})
        hostiles.sort(key=lambda e: float(e.get("dist") or 99))
        return res_ok(describe=f"附近有 {hostiles[0].get('name')}（{hostiles[0].get('dist')} 格）",
                      detail={"hostile": hostiles[0]["name"],
                              "dist": hostiles[0].get("dist")})


@register
class DetectResource(_InspectBase):
    name = "detect_nearby_resource"
    description = "检查附近某种资源（无特定目标时扫描常见资源）"

    def start(self, ctx, params):
        s = ctx.state()
        want = normalize_resource(params.get("resource") or params.get("target") or "")
        blocks = s.get("nearbyBlocks") or []
        if want:
            hit = next((b for b in blocks if b.get("name") == want), None)
            return res_ok(describe=(f"附近有 {want}" if hit else f"附近没有 {want}"),
                          detail={"resource": want, "found": bool(hit),
                                  "count": (hit or {}).get("count", 0)})
        found = {b.get("name"): b.get("count") for b in blocks
                 if b.get("name") in PICKAXE_REQUIREMENT
                 or str(b.get("name", "")).endswith("_log")}
        return res_ok(describe=f"附近资源: {', '.join(sorted(found)) or '无'}",
                      detail={"resources": found})


@register
class DetectFood(_InspectBase):
    name = "detect_food"
    description = "检查食物来源（背包里的食物 + 附近可猎动物）"

    def start(self, ctx, params):
        s = ctx.state()
        inv = ctx.inventory()
        foods = [i for i in inv if i.get("name") in FOODS]
        animals = [e for e in (s.get("nearbyEntities") or [])
                   if str(e.get("name") or "") in PASSIVE_ANIMALS]
        return res_ok(describe=(f"背包有食物 x{sum(i.get('count', 0) for i in foods)}"
                                if foods else "背包没有食物"),
                      detail={"inventory_foods": foods,
                              "nearby_animals": [a.get("name") for a in animals]})


@register
class DetectEnvironment(_InspectBase):
    name = "detect_environment"
    description = "时段/天气/光照/水情 一次性检查"

    def start(self, ctx, params):
        s = ctx.state()
        return res_ok(describe="环境检查完成", detail={
            "time": time_of_day_label(s.get("timeOfDay")),
            "weather": "rain" if s.get("isRaining") else "clear",
            "light": s.get("lightAtFeet"),
            "in_water": bool(s.get("inWater")),
            "on_fire": bool(s.get("onFire")),
        })


@register
class DetectCave(_InspectBase):
    name = "detect_cave"
    description = "检查附近有没有洞穴（洞口空气/黑暗/洞口方块）"

    def start(self, ctx, params):
        cave_air = _find_blocks(ctx, "cave_air", 10, 1)
        lava = _find_blocks(ctx, "lava", 10, 1)
        found = bool(cave_air)
        return res_ok(describe="附近有洞穴入口" if found else "附近没看到洞穴",
                      detail={"cave": found, "lava_nearby": bool(lava)})


@register
class DetectStructure(_InspectBase):
    name = "detect_structure"
    description = "检查附近有没有人造结构/村庄特征"

    def start(self, ctx, params):
        hits = []
        for blk in STRUCTURE_BLOCKS:
            if blk == "villager":
                continue
            if _find_blocks(ctx, blk, 24, 1):
                hits.append(blk)
        for e in ctx.state().get("nearbyEntities") or []:
            if e.get("name") in ("villager", "iron_golem"):
                hits.append(e["name"])
        return res_ok(describe=(f"发现结构特征: {', '.join(sorted(set(hits)))}"
                                if hits else "附近没有结构"),
                      detail={"structures": sorted(set(hits))})


@register
class RememberLocation(_InspectBase):
    name = "remember_location"
    description = "记住当前位置（地名可选）"

    def start(self, ctx, params):
        pos = ctx.position()
        if not pos:
            return res_fail("no_position")
        name = str(params.get("name") or f"place_{int(pos.get('x', 0))}_{int(pos.get('z', 0))}")
        ctx.locations.remember(name, pos, kind=str(params.get("kind") or "place"))
        return res_ok(describe=f"记住了位置「{name}」", detail={"name": name, "pos": pos})


@register
class MarkInteresting(_InspectBase):
    name = "mark_interesting_location"
    description = "标记一个值得再来的位置"

    def start(self, ctx, params):
        pos = ctx.position()
        if not pos:
            return res_fail("no_position")
        name = str(params.get("name") or f"interesting_{int(time.time()) % 10000}")
        ctx.locations.remember(name, pos, kind="interesting")
        return res_ok(describe=f"标记了「{name}」，下次可以再来", detail={"name": name})


@register
class InspectInventory(_InspectBase):
    name = "inspect_inventory"
    description = "查看背包物品清单"

    def start(self, ctx, params):
        inv = ctx.inventory(refresh=True)
        return res_ok(describe=f"背包里有 {len(inv)} 种物品",
                      detail={"items": inv})
