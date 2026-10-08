# test_minecraft.embodiment.py — Minecraft 具身适配器测试
# 离线：用假桥（monkeypatch bridge 函数）验证"ActionNode/语义意图 → 技能 → 真实结果"的映射。
# 不连真实 Minecraft、不写真实数据。
# 具身改造 2026-09：动作面已从"8 个手写意图"升级为"Skill Library（skills/）"，
# 旧 8 个语义意图映射到对应技能（observe→look_at/inspect_area 等），安全白名单
# 迁移到 skills.gathering.SAFE_GATHER_BLOCKS（箱子/工作台/熔炉等资产绝不自动挖）。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_minecraft.embodiment.py

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

import minecraft.bridge as bridge
from minecraft.embodiment import MinecraftEmbodiment, SAFE_DIG_BLOCKS
from graph_model import KnowledgeGraph, Node

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


STATE = {
    "connected": True, "username": "Haru",
    "position": {"x": 10.0, "y": 64.0, "z": 20.0},
    "health": 20, "food": 18, "heldItem": "wooden_pickaxe",
    "playersNearby": [{"name": "Hellucigen", "dist": 4.0,
                       "rel": {"dx": 3.0, "dy": 0.0, "dz": 2.0}}],
    "nearbyEntities": [{"name": "cow", "displayName": "Cow", "dist": 5.0,
                        "rel": {"dx": -4.0, "dy": 0.0, "dz": 3.0}}],
    "nearbyBlocks": [{"name": "oak_log", "count": 4}, {"name": "stone", "count": 9}],
    "chat": [], "connectedAt": 1,
}

calls = []


def install_fake_bridge(action_result=None, find_positions=None,
                        inventory_items=None):
    calls.clear()
    bridge.get_state = lambda: dict(STATE)
    bridge.health = lambda: True
    bridge.inventory = lambda: {"ok": True,
                                "items": inventory_items if inventory_items is not None
                                else [{"name": "wooden_pickaxe", "count": 1}]}

    def _call(path, payload=None, timeout=3):
        calls.append((path, payload or {}))
        if path == "/look_at":
            return {"ok": True}
        if path == "/goto":
            p = payload or {}
            if not all(isinstance(p.get(k), float) for k in ("x", "y", "z")):
                return {"ok": False, "reason": "invalid_coords"}
            return {"ok": True}
        if path == "/dig":
            return {"ok": True, "digging": payload.get("count", 1)}
        if path == "/find_blocks":
            return {"ok": True, "positions": list(find_positions or [])}
        if path == "/dig_pos":
            return {"ok": True}
        if path == "/follow":
            return {"ok": True}
        return {"ok": True}

    bridge.call = _call
    bridge.look_at = lambda x, y, z: _call("/look_at", {"x": x, "y": y, "z": z})
    bridge.goto_coords = lambda x, y, z: _call("/goto", {"x": x, "y": y, "z": z})
    bridge.dig = lambda block, count=1: _call("/dig", {"block": block, "count": count})
    bridge.stop = lambda: True
    bridge.stop_goto = lambda: {"ok": True}
    bridge.stop_combat = lambda: {"ok": True}
    bridge.stopfollow = lambda: 3
    bridge.say = lambda text: True
    bridge.get_action_result = lambda: dict(action_result or {})


def build(action_result=None, with_unknown=True, **kw):
    install_fake_bridge(action_result, **kw)
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Haru"))
    if with_unknown:
        kg.add_node(Node(id="UnknownEntity_cow", graph_space="cognitive"))
    return kg, MinecraftEmbodiment(kg=kg, perceive_into_graph=False)


def flush(emb):
    """跳过技能的 poll 延迟门（测试里轮询是即时的）。"""
    if emb.ctx.session.get("skill"):
        emb.ctx.session["poll_after"] = 0
    return emb


# ── 1. 可用性 / 能力面 ────────────────────────────────────
kg, emb = build()
check("连接时可用", emb.available() is True)
check("能力面覆盖旧 8 个语义意图（兼容自主层）",
      set(emb.capabilities()) >= {"observe", "approach", "follow", "explore",
                                  "collect", "rest", "communicate", "withdraw"},
      str(emb.capabilities()))
check("能力面包含技能库（walk_to/gather_resource/explore_area…）",
      {"walk_to", "gather_resource", "explore_area", "craft_item",
       "attack_entity", "eat_food"} <= set(emb.capabilities()),
      str(len(emb.capabilities())))
bridge.get_state = lambda: {"connected": False}
check("未连接时不可用", emb.available() is False)
check("未连接时能力面为空（自主层会因此不动手）", emb.capabilities() == set())
r = emb.execute({"type": "observe", "target": "cow", "params": {}})
check("未连接时执行动作如实失败", r.get("success") is False
      and r.get("reason") == "not_connected", str(r))

# ── 2. 感知：未知识别由图决定 ─────────────────────────────
kg, emb = build(with_unknown=False)
p = emb.perceive()
check("感知带位置/血量/饥饿/手持", p["health"] == 20 and p["food"] == 18
      and p["held"] == "wooden_pickaxe" and p["position"]["x"] == 10.0, str(p)[:120])
check("玩家带相对坐标（靠近/跟随要用）",
      p["players"][0]["rel"]["dx"] == 3.0, str(p["players"]))
check("实体带相对坐标", p["entities"][0]["rel"]["dz"] == 3.0, str(p["entities"]))
check("图里没有 UnknownEntity_cow 时不算未知",
      all(e["name"] != "cow" for e in p["unknown_entities"]), str(p["unknown_entities"]))
kg2, emb2 = build(with_unknown=True)
p2 = emb2.perceive()
check("图里有 UnknownEntity_cow 时算未知（认识与否由图决定）",
      any(e["name"] == "cow" for e in p2["unknown_entities"]), str(p2["unknown_entities"]))

# ── 3. observe：转向目标是真实动作 ───────────────────────
kg, emb = build()
emb.perceive()
r = emb.execute({"type": "observe", "target": "cow", "params": {}})
check("observe 调用 look_at 并带目标世界坐标",
      r["success"] and any(c[0] == "/look_at" for c in calls), str(calls))
look = [c for c in calls if c[0] == "/look_at"][0][1]
check("目标坐标 = Haru 位置 + 相对偏移",
      abs(look["x"] - 6.0) < 0.01 and abs(look["z"] - 23.0) < 0.01, str(look))

# ── 4. approach：走到安全距离（异步 → pending + 回执） ────
kg, emb = build()
emb.perceive()
r = emb.execute({"type": "approach", "target": "cow",
                 "params": {"entity": "cow", "keep_distance": 3.0}})
check("approach 走寻路且标记 pending", r["success"] and r.get("pending"), str(r))
# B1 起：追动态目标交给 bot 侧 GoalFollow（/goto_entity），不再按快照冻结点寻路
ge = [c for c in calls if c[0] == "/goto_entity"][0][1]
check("追击指令带实体名与跟随时距（keep_distance → range）",
      ge.get("entity") == "cow" and abs(float(ge.get("range", 0)) - 3.0) < 0.01,
      str(ge))
check("不再发出按快照缩放的 /goto（旧版追不上移动目标）",
      not any(c[0] == "/goto" for c in calls), str(calls))
install_fake_bridge({"status": "running"})
flush(emb)
check("回执 running → 未结束", emb.poll_action()["status"] == "running")
install_fake_bridge({"name": "goto_entity", "status": "done",
                     "startedAt": int(time.time() * 1000), "detail": {}})
flush(emb)
check("新鲜 goto_entity done 回执 → 结束", emb.poll_action()["status"] == "done")

# ── 5. collect：安全清单 + 真实失败原因 ──────────────────
kg, emb = build(find_positions=[{"x": 12.0, "y": 64.0, "z": 20.0}])
emb.perceive()
r = emb.execute({"type": "collect", "target": "原木", "params": {"block": "原木", "count": 1}})
check("采集安全方块走技能（find_blocks 定位 + dig_pos 挖）", r["success"] and
      any(c[0] == "/find_blocks" for c in calls)
      and any(c[0] == "/dig_pos" for c in calls), str(calls))
flush(emb)
dig = [c for c in calls if c[0] == "/dig_pos"]
check("挖掘的是找到的方块位置（不硬编码坐标）",
      dig and dig[0][1].get("x") == 12.0, str(dig))
check("异步动作标记为进行中", r.get("pending") is True, str(r))
kg, emb = build()
emb.perceive()
r2 = emb.execute({"type": "collect", "params": {"block": "箱子"}})
check("资产方块（箱子）被拒绝（技能层物理兜底：无 kernel 授权标记）",
      r2["success"] is False and "requires_user_permission" in r2["reason"],
      str(r2))
for zh in ("工作台", "熔炉", "铁门"):
    rr = emb.execute({"type": "collect", "params": {"block": zh}})
    check(f"危险/资产方块 {zh} 被拒绝", rr["success"] is False, str(rr))
check("白名单仍是安全材料（原木/石头/煤矿…）",
      {"oak_log", "stone", "coal_ore", "iron_ore"} <= set(SAFE_DIG_BLOCKS)
      and "chest" not in SAFE_DIG_BLOCKS and "crafting_table" not in SAFE_DIG_BLOCKS,
      str(sorted(SAFE_DIG_BLOCKS)[:8]))
kg, emb = build({"status": "failed", "detail": {"error": "dig timeout"}},
                find_positions=[{"x": 12.0, "y": 64.0, "z": 20.0}])
emb.perceive()
emb.execute({"type": "collect", "params": {"block": "原木"}})
flush(emb)
wa = emb.poll_action()
check("dig 失败回真实原因（不假装成功）",
      wa["status"] == "failed" and "dig timeout" in wa["reason"], str(wa))

# ── 6. 其它意图的动作映射 ────────────────────────────────
kg, emb = build()
emb.perceive()
check("follow → /follow", emb.execute({"type": "follow", "params": {"player": "Hellucigen"}})["success"]
      and any(c[0] == "/follow" for c in calls))
install_fake_bridge()
kg, emb = build()
emb.perceive()
r = emb.execute({"type": "explore", "params": {"direction": "east", "step": 8.0}})
check("explore → 朝方向偏移的坐标", r["success"] and r.get("pending"), str(r))
gx = [c for c in calls if c[0] == "/goto"][0][1]
check("explore 目标x = 当前位置 + 步长", abs(gx["x"] - 18.0) < 0.01, str(gx))
install_fake_bridge()
kg, emb = build()
emb.perceive()
r = emb.execute({"type": "communicate", "params": {"text": "我在这儿"}})
check("communicate → say", r["success"], str(r))
check("communicate 无文本时拒绝（不编台词）",
      emb.execute({"type": "communicate", "params": {}})["success"] is False)
check("rest → 停止移动/寻路",
      emb.execute({"type": "rest", "params": {}})["success"])
install_fake_bridge()
kg, emb = build()
emb.perceive()
r = emb.execute({"type": "withdraw", "params": {"from": "cow", "distance": 6.0}})
wy = [c for c in calls if c[0] == "/goto"][0][1]
check("withdraw → 朝远离威胁的方向", r["success"] and wy["x"] > 10.0, str(wy))

# ── 7. 新技能直接执行（ActionNode → 技能）────────────────
install_fake_bridge()
kg, emb = build()
emb.perceive()
r = emb.execute({"action_type": "eat_food", "params": {},
                 "motivation": "survival"})
check("eat_food 无食物时如实失败（tool/food 缺失语义化）",
      r["success"] is False and "no_food_in_inventory" in r["reason"], str(r))
kg, emb = build(inventory_items=[{"name": "bread", "count": 2}])
emb.perceive()
r = emb.execute({"action_type": "eat_food", "params": {}})
check("eat_food 有食物 → /eat", r["success"]
      and any(c[0] == "/eat" and c[1].get("item") == "bread" for c in calls), str(calls))
install_fake_bridge()
kg, emb = build()
emb.perceive()
r = emb.execute({"action_type": "walk_to", "params": {"x": 15.0, "z": 25.0}})
check("walk_to → 寻路 pending", r["success"] and r.get("pending"), str(r))
check("未知技能如实报错（unknown_skill）",
      emb.execute({"action_type": "fly_to_moon", "params": {}})["success"] is False)

# ── 8. 攻击不在旧自主动作面（规范明确禁止自动攻击；攻击只由认知层
#      明确产生 ActionNode 时经 attack_entity 技能执行） ─────
check("旧语义意图 attack 不被支持",
      emb.execute({"type": "attack", "params": {}})["success"] is False)

# ── 9. cancel 是真取消（停技能 + 停状态 + 停寻路） ────────
install_fake_bridge()
kg, emb = build()
emb.perceive()
emb.execute({"action_type": "walk_to", "params": {"x": 30.0, "z": 30.0}})
stopped = {"stop": 0, "goto": 0}
bridge.stop = lambda: (stopped.__setitem__("stop", stopped["stop"] + 1), True)[1]
bridge.stop_goto = lambda: (stopped.__setitem__("goto", stopped["goto"] + 1), {"ok": True})[1]
emb.cancel()
check("cancel 同时停控制状态与寻路，并清空技能会话",
      stopped["stop"] == 1 and stopped["goto"] >= 1
      and emb.ctx.session.get("skill") is None, str(stopped))

# ── 10. 感知入图的指纹（避免每 tick 无意义刷图） ───────────
install_fake_bridge()
kg, emb = build()
emb.perceive_into_graph = True
wrote = []
import minecraft.perception
orig_update = minecraft.perception.update_perception
minecraft.perception.update_perception = lambda kg_, eng_, st, **kw: wrote.append(1) or {"updated": [], "unknowns": []}
emb.perceive(); emb.perceive()
check("状态没变 → 只写一次图（有变化检测）", len(wrote) == 1, str(len(wrote)))
STATE["position"] = {"x": 11.0, "y": 64.0, "z": 20.0}
emb.perceive()
check("状态变化 → 重新写图", len(wrote) == 2, str(len(wrote)))
minecraft.perception.update_perception = orig_update
STATE["position"] = {"x": 10.0, "y": 64.0, "z": 20.0}

# ── 11. 世界事件检测（事件 → 认知的通路存在且结构化）──────
install_fake_bridge()
kg, emb = build()
emb.perceive()
evs = emb.detect_events()
types = [e.get("type") for e in evs]
check("事件检测产出结构化事件", isinstance(evs, list) and len(evs) > 0, str(types))
check("资源事件在列（oak_log 是资源）", "resource_detected" in types, str(types))
check("事件带资源名与紧迫度字段",
      all("resource" in e and "urgency" in e
          for e in evs if e.get("type") == "resource_detected"), str(evs[:2]))

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ Minecraft 具身适配器测试全过（假桥 + Skill Library，无真实连接）")
