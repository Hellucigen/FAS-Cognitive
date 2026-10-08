# action_intents.py — 语义假设 → 行动候选的翻译器（候选生成，不是执行通道）
# ============================================================================
# 定位（2026-09-21 降级重构）：NLP 的 intent 是**语义假设/证据**——对输入的一种
# 候选解读，不是封闭的行为类别，更不是行动指令。本模块把解析产物翻译成
# ActionNode **候选**；候选是否成立由图谱激活、行动竞争与 ActionManager 决定：
#   输入 → 语言理解(假设) → 候选翻译(本模块) → ActionManager 竞争
#        → 能力(embodiment/skill) → 执行
#   - 语义假设：intent=follow_user（来自 Level1 快速解析或反射解析）
#   - 候选产物：{action_type: follow_entity, params: {entity: 用户名}}
# 表的未命中不是拒绝：假设找不到候选能力，输入照常走认知管线。
# 本模块不直接执行任何东西，也从不绕过 ActionManager（全仓 execute 单一
# 入口在 action_system.ActionManager._start）。表是兼容翻译层，不是白名单。
# ============================================================================

import logging

from skills.observation import normalize_resource
from skills.exploration import GOAL_ALIASES

logger = logging.getLogger(__name__)

# Level 1 快速解析的语义假设 → (行动候选类型, 参数构造器)
# 兼容翻译表（冻结）：只把已有 L1 prompt 契约的假设词翻成候选，未命中≠拒绝。
# 新的行动概念请长进图谱（action_concepts/capability graph），不要再加进本表。
# target=None 表示不需要目标；user 占位符在调用时替换成实际用户名
L1_INTENT_TABLE = {
    "follow_user":    ("follow_entity", "user"),
    "approach_user":  ("navigate_to_entity", "user"),
    "come_here":      ("navigate_to_entity", "user"),
    "stop_action":    ("stop", None),
    "wait":           ("stop", None),
    "jump":           ("jump", None),
    "mine_block":     ("gather_resource", "resource"),
    "attack_entity":  ("attack_entity", "entity"),
    "collect_item":   ("collect_dropped_item", None),
    "eat_food":       ("eat_food", None),
    "explore":        ("explore_area", "goal"),
    "go_direction":   ("go_direction", "direction"),
    "look_at_target": ("look_at", "entity"),
    "craft_item":     ("craft_item", "item"),
    "place_block":    ("place_block", "item"),
    "equip_tool":     ("equip_item", "item"),
    "inspect_self":   ("monitor_vitals", None),
    "store_items":    ("chest_store", None),
    "gather_resource": ("gather_resource", "resource"),
    "sleep":          ("sleep", None),
    "return_home":    ("navigate_home", None),
}

# Level 2 意图分解的类型 → action_type（大体同名；个别翻译）
# 同为兼容翻译表（冻结）：L2 的 type 是叙事级假设，产物同样只是候选。
L2_TYPE_TABLE = {
    "go_to": "go_to", "explore_direction": "explore_direction",
    "follow_user": "follow_entity", "come_here": "navigate_to_entity",
    "stop_action": "stop", "mine_block": "gather_resource",
    "gather_resource": "gather_resource", "collect_food": "collect_food",
    "eat_food": "eat_food", "craft_item": "craft_item",
    "smelt_item": "smelt_item", "equip_tool": "equip_item",
    "attack_entity": "attack_entity", "retreat": "retreat",
    "seek_safety": "seek_safety", "place_block": "place_block",
    "build_shelter": "build_simple_shelter", "place_light": "place_torch",
    "sleep": "sleep", "inspect_target": "inspect_block",
    "remember_location": "remember_location", "return_home": "navigate_home",
    "give_item": "drop_item", "wait": "stop",
}

# 反射解析（minecraft.reflex.parse_reflex_command）的动作 → Level1 intent
REFLEX_INTENT_TABLE = {
    "follow": "follow_user", "approach": "approach_user",
    "stop": "stop_action", "jump": "jump", "dig": "mine_block",
    "attack": "attack_entity", "move": "go_direction",
}


def _translate_target(kind: str, target) -> object:
    """中文目标词 → 世界名（资源/生物/合成品）；其余原样。"""
    if target is None:
        return None
    t = str(target).strip()
    if not t:
        return None
    if kind in ("resource", "block"):
        return normalize_resource(t)
    if kind == "entity":
        from minecraft.reflex import translate_entity
        return translate_entity(t)
    if kind == "item":
        # 合成/装备目标：先查配方别名（木镐→wooden_pickaxe），再查资源别名
        from skills.crafting import CRAFT_ALIASES
        if t in CRAFT_ALIASES:
            return CRAFT_ALIASES[t]
        return normalize_resource(t)
    return t


def l1_to_action(parsed: dict, user_name: str = "user") -> dict | None:
    """Level1 语义假设（intent/target/count）→ ActionNode **候选**。

    返回的是给 ActionManager 的候选，不是执行指令。假设未命中兼容表时
    返回 None：表示"这个解读暂没有已知的候选能力"，输入本身不受影响、
    不被拒绝（照常走图谱激活与认知管线），未知词也不会报错。
    """
    intent = str((parsed or {}).get("intent") or "").strip()
    if not intent or intent == "none":
        return None
    entry = L1_INTENT_TABLE.get(intent)
    if entry is None:
        return None   # 假设无对应候选：静默降级为纯语义证据，不拒绝输入
    action_type, target_kind = entry
    raw_target = (parsed or {}).get("target")
    params = {}
    target = None
    if target_kind == "user":
        target = raw_target if raw_target and raw_target != "用户" else user_name
        params["entity"] = target
        params["player"] = target
    elif target_kind == "resource":
        target = _translate_target("resource", raw_target)
        params["resource"] = target
        if (parsed or {}).get("count"):
            params["quantity"] = int(parsed["count"])
    elif target_kind == "entity":
        target = _translate_target("entity", raw_target)
        params["entity"] = target
    elif target_kind == "item":
        target = _translate_target("resource", raw_target)
        params["item"] = target
    elif target_kind == "direction":
        target = raw_target
        params["direction"] = raw_target
    elif target_kind == "goal":
        target = GOAL_ALIASES.get(str(raw_target or ""), str(raw_target or "any"))
        params["goal"] = target
    spec = {"action_type": action_type, "target": target, "params": params}
    if (parsed or {}).get("urgency") is not None:
        spec["urgency"] = float(parsed["urgency"])
        spec["priority"] = min(0.95, 0.5 + float(parsed["urgency"]) * 0.45)
    return spec


def intention_to_action(intent: dict, user_name: str = "user") -> dict | None:
    """Level2 意图分解产物 → ActionNode 规格。"""
    if not isinstance(intent, dict):
        return None
    itype = str(intent.get("type") or "").strip()
    if itype not in L2_TYPE_TABLE:
        return None
    action_type = L2_TYPE_TABLE[itype]
    raw_target = intent.get("target")
    params = dict(intent.get("params") or {})
    target = None
    if action_type == "go_to":
        # "去村庄/回家"：有记忆的地名 → 回去；没有 → 带目标探索
        place = str(raw_target or "")
        if place and any(k in place for k in ("家", "回", "home", "base")):
            return {"action_type": "navigate_home", "target": None, "params": {}}
        goal = GOAL_ALIASES.get(place, "any")
        return {"action_type": "explore_area", "target": goal,
                "params": {"goal": goal}}
    if action_type == "gather_resource":
        target = _translate_target("resource", raw_target)
        params["resource"] = target
    elif action_type == "attack_entity":
        target = _translate_target("entity", raw_target)
        params["entity"] = target
    elif action_type in ("craft_item", "smelt_item", "equip_item",
                         "place_block", "inspect_block"):
        target = _translate_target("item", raw_target)
        params["item"] = target
        if action_type == "inspect_block":
            params["block"] = target
    elif action_type == "follow_entity":
        target = raw_target if raw_target and raw_target != "用户" else user_name
        params["entity"] = target
        params["player"] = target
    elif action_type == "explore_direction":
        target = raw_target
        params["direction"] = raw_target
        if intent.get("note"):
            params["goal"] = GOAL_ALIASES.get(str(raw_target or ""), "any")
    elif action_type == "navigate_home":
        target = None
    elif action_type == "stop":
        target = None
    elif action_type == "drop_item":
        target = _translate_target("resource", raw_target)
        params["item"] = target
    elif action_type == "remember_location":
        target = raw_target
        params["name"] = raw_target
    note = intent.get("note")
    if note:
        params["note"] = str(note)[:80]
    return {"action_type": action_type, "target": target, "params": params}


def reflex_to_action(reflex: dict, user_name: str = "user") -> dict | None:
    """反射解析（零 LLM 快通道）→ ActionNode 规格（进同一条 Action 管路）。"""
    action = (reflex or {}).get("action")
    if not action or action == "none":
        return None
    p = (reflex or {}).get("params") or {}
    if action == "follow":
        return {"action_type": "follow_entity", "target": user_name,
                "params": {"entity": user_name, "player": user_name},
                "urgency": 0.9, "priority": 0.85}
    if action == "approach":
        return {"action_type": "navigate_to_entity", "target": user_name,
                "params": {"entity": user_name, "player": user_name,
                           "keep_distance": 2.0},
                "urgency": 0.9, "priority": 0.85}
    if action == "stop":
        return {"action_type": "stop", "target": None, "params": {},
                "urgency": 0.95, "priority": 0.9}
    if action == "jump":
        return {"action_type": "jump", "target": None, "params": {},
                "urgency": 0.6, "priority": 0.5}
    if action == "dig":
        block = p.get("block") or p.get("target")
        # 数量由参数绑定层给出（Action Concept 2026-09-19："挖三个铁矿"→3）；
        # 未绑定则保持旧默认 1
        try:
            qty = max(1, int(p.get("quantity") or 1))
        except (TypeError, ValueError):
            qty = 1
        return {"action_type": "gather_resource", "target": block,
                "params": {"resource": normalize_resource(str(block or "")),
                           "quantity": qty},
                "urgency": 0.8, "priority": 0.75}
    if action == "attack":
        entity = p.get("entity")
        return {"action_type": "attack_entity", "target": entity,
                "params": {"entity": entity},
                "urgency": 0.85, "priority": 0.8}
    if action == "move":
        return {"action_type": "go_direction", "target": p.get("direction"),
                "params": {"direction": p.get("direction") or "forward",
                           "distance": 3.0},
                "urgency": 0.6, "priority": 0.5}
    return None
