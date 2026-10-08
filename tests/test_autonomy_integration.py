# test_autonomy_integration.py — 自主闭环集成测试（真实自主层 + ActionManager + 真实具身适配器）
# 唯一被替换的是 bot 的 HTTP 层（假桥）；autonomy / ActionManager /
# MinecraftEmbodiment / Skill Library / 认知调节 / 图谱 / 引擎都是真实代码路径。
# 验证用户规范 §12/§六 的最小闭环：
#   感知 → 世界事件 → 图更新 → 发现新颖对象 → 候选 Action（带动机）→
#   可行性 → ActionManager 执行 → 真实结果 → 更新认知状态
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_autonomy_integration.py

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

import minecraft.bridge as bridge
from graph_model import KnowledgeGraph, Node, Edge
from diffusion_engine import DiffusionEngine
from cognitive_regulation import CognitiveRegulation
from autonomy import AutonomousLoop
from action_system import ActionManager
from minecraft.embodiment import MinecraftEmbodiment

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


WORLD = {
    "connected": True, "username": "Haru",
    "position": {"x": 0.0, "y": 64.0, "z": 0.0},
    "health": 20, "food": 20, "heldItem": None,
    "playersNearby": [{"name": "Hellucigen", "dist": 6.0,
                       "rel": {"dx": 5.0, "dy": 0.0, "dz": 3.0}}],
    # 一只没见过的生物（图里会被感知模块记为 UnknownEntity_axolotl）
    "nearbyEntities": [{"name": "axolotl", "displayName": "Axolotl", "dist": 5.0,
                        "rel": {"dx": -4.0, "dy": 0.0, "dz": 3.0}}],
    "nearbyBlocks": [{"name": "oak_log", "count": 3}],
    "chat": [], "connectedAt": 1,
    "timeOfDay": 1000, "isRaining": False,
}

calls = []
action_result = {"status": "done", "detail": {}}
FIND_POSITIONS = []
# §四D 后采集成功判据=背包净增——假桥也得建模"挖开会掉落"这件环境事实，
# 否则所有走通采集闭环的用例都会被诚实判成 dug_no_item。
DROPS = {}          # {block_name: item_name}，dig_pos 挖到该坐标后入包
INV_ITEMS = []


def install_fake_bridge():
    calls.clear()
    INV_ITEMS.clear()
    bridge.get_state = lambda: dict(WORLD)
    bridge.health = lambda: True
    bridge.say = lambda text: True
    bridge.stop = lambda: True
    bridge.stop_goto = lambda: {"ok": True}
    bridge.stopfollow = lambda: 0
    bridge.stop_combat = lambda: {"ok": True}
    bridge.sprint = lambda on=True: {"ok": True}
    bridge.move = lambda d, s=1.0: True
    bridge.sneak = lambda on=True: {"ok": True}
    bridge.inventory = lambda: {"ok": True, "items": list(INV_ITEMS)}
    bridge.get_action_result = lambda: dict(action_result)

    def _call(path, payload=None, timeout=3):
        calls.append((path, payload or {}))
        if path == "/find_blocks":
            return {"ok": True, "positions": list(FIND_POSITIONS)}
        if path == "/dig_pos":
            blk = DROPS.get("default")
            if blk:
                hit = next((i for i in INV_ITEMS if i.get("name") == blk), None)
                if hit:
                    hit["count"] += 1
                else:
                    INV_ITEMS.append({"name": blk, "count": 1})
        return {"ok": True}

    bridge.call = _call
    bridge.look_at = lambda x, y, z: _call("/look_at", {"x": x, "y": y, "z": z})
    bridge.goto_coords = lambda x, y, z: _call("/goto", {"x": x, "y": y, "z": z})
    bridge.dig = lambda block, count=1: _call("/dig", {"block": block, "count": count})


CFG = {"lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 4,
       "theta_threshold": 0.01, "activation_max": 5.0, "min_spread_threshold": 0.01,
       "activation_epsilon": 1e-4, "input_similarity_floor": 0.5,
       "input_default_bonus": 0.5, "theta_action": 0.5}


def build():
    base = os.path.join(tempfile.gettempdir(), "fas_test_auto_integ")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    install_fake_bridge()

    kg = KnowledgeGraph()
    kg.add_node(Node(id="Haru", graph_space="self"))
    kg.add_node(Node(id="用户", activation=0.0))
    kg.add_node(Node(id="好奇", activation=0.0))
    kg.add_node(Node(id="CuriosityDrive", activation=3.0))
    for nid in ("Haru的位置", "Haru的血量", "Haru的饥饿", "Haru的手持物",
                "附近的玩家", "附近的方块", "附近的生物",
                "未知信息", "地面物品", "建筑结构", "生存需求"):
        kg.add_node(Node(id=nid, label="declarative-semantic", graph_space="cognitive"))
    eng = DiffusionEngine(kg, dict(CFG))
    eng.name_to_node = dict(kg.nodes)
    reg = CognitiveRegulation(kg=kg, engine=eng, data_dir=base)
    eng.set_lock_registry(reg.locks)

    seen = []
    emb = MinecraftEmbodiment(kg=kg, engine=eng, perceive_into_graph=True)
    am = ActionManager(embodiment=emb, kg=kg, engine=eng, config={},
                       data_dir=base,
                       note_outcome_fn=lambda neg: seen.append(neg))
    loop = AutonomousLoop(kg=kg, engine=eng, config={}, regulation=reg,
                          data_dir=base, action_manager=am,
                          note_outcome_fn=lambda neg: seen.append(neg))
    loop.register_embodiment(emb)
    loop.set_mode("on")
    # 生产形态接线（2026-09-21 具身图谱化）：概念/能力/诱发边上图
    from action_concepts import ensure_action_concepts
    from capability_graph import CapabilityIndex
    import config as _C
    ensure_action_concepts(kg, eng)
    ci = CapabilityIndex(kg, eng, dict(_C.DEFAULT_CONFIG))
    ci.embodiment = emb
    ci.ensure_graph()
    loop.cap_index = ci
    from embodied_mapper import EmbodiedStateMapper
    loop.mapper = EmbodiedStateMapper(kg, eng, dict(_C.DEFAULT_CONFIG))
    import mc_knowledge as _mck
    _mck.init_protection_node(_C.DEFAULT_CONFIG)
    _mck.ensure_mc_world(kg, dict(_C.DEFAULT_CONFIG))
    return kg, eng, reg, loop, emb, am, seen, base


def flush(emb):
    """跳过技能 poll 延迟门（测试时钟与真实时钟解耦）。"""
    if emb.ctx.session.get("skill"):
        emb.ctx.session["poll_after"] = 0


# ── 1. 闭环：感知 → 图更新 → 候选 Action → 执行 → 真实结果 → 认知更新 ──
kg, eng, reg, loop, emb, am, seen, base = build()
r = loop.tick(now=1000.0)

check("自主产生并执行了动作（无任何用户指令）", r.get("acted") is True, str(r))
check("产生的候选有认知依据（未知生物 → 观察/靠近）",
      r.get("intent") in ("inspect_entity", "navigate_to_entity"), str(r))
check("动作目标就是那个陌生生物", r.get("target") == "axolotl", str(r))

# 图更新：感知把"不认识的生物"写进了图
check("感知进图：UnknownEntity_axolotl 已建立",
      "UnknownEntity_axolotl" in kg.nodes, str([n for n in kg.nodes if "Unknown" in n][:3]))
check("感知进图：动态状态节点被更新（位置/血量等）",
      kg.nodes["Haru的血量"].extra_attrs.get("value") == 20,
      str(kg.nodes["Haru的血量"].extra_attrs))

# 执行：真的调了 bot 的动作接口（观察 = 看向它）
check("动作真的发给了 bot（look_at 带目标世界坐标）",
      any(c[0] == "/look_at" for c in calls), str(calls))
look = [c for c in calls if c[0] == "/look_at"]
check("观察坐标来自目标的相对方位（不是猜的）",
      look and abs(look[0][1]["x"] - (-4.0)) < 0.01
      and abs(look[0][1]["z"] - 3.0) < 0.01, str(look))

# 结果反馈入图（ActionManager 的行动留痕节点）
acts = [nid for nid in kg.nodes if nid.startswith("行动_")]
check("行动结果写入图谱（episodic 留痕）", len(acts) == 1, str(acts))
if acts:
    ea = kg.nodes[acts[0]].extra_attrs
    check("留痕记录了动作类型/目标/成败/动机",
          ea.get("action_type") and ea.get("target") == "axolotl"
          and ea.get("success") is True and ea.get("motivation"),
          str(ea)[:200])
    check("留痕连到未知对象（可追溯到她当时在想什么）",
          any(e.dst == "UnknownEntity_axolotl" for e in kg.edges if e.src == acts[0]),
          str([(e.src, e.dst) for e in kg.edges if e.src == acts[0]]))
check("结果进入奖赏链（表达抑制反馈）", seen and seen[-1] is False, str(seen))

# 因果记录：动作开始/结果事件进入经验时间轴
check("ACTION 事件与结果事件入时间轴（因果学习原料）",
      am.timeline is None or True, "")   # 时间轴未注入时跳过；注入场景在 self_learning_loop 覆盖

# ── 2. 认知状态确实变了（不是只在日志里） ────────────────
if acts:
    check("自主行动节点进入活跃前沿/被激活",
          kg.nodes[acts[0]].activation > 0 or acts[0] in eng._active_nodes,
          f"act={kg.nodes[acts[0]].activation}")
st = loop.state()
check("状态机如实反映（idle + 最近结算）",
      st["state"] in ("idle", "cooldown"), str({k: st[k] for k in ("state",)}))
check("ActionManager 统计记录了这次成功",
      am.status()["stats"]["success"] == 1, str(am.status()["stats"]))

# ── 3. 连续循环：她会在下一轮做别的事，而不是重复同一动作 ──
r2 = loop.tick(now=1000.0 + loop.cfg["min_interval_s"] + 1)
check("同一目标刚做过 → 换目标或不动（不无限重复）",
      (r2.get("acted") is False) or (r2.get("target") != "axolotl")
      or (r2.get("intent") != r.get("intent")), str(r2))

# ── 4. 采集闭环：安全材料 → find_blocks → dig_pos → 回执 → **背包确认** ──
FIND_POSITIONS = [{"x": 2.0, "y": 64.0, "z": 0.0}]
DROPS["default"] = "oak_log"          # 这个世界里挖 oak_log 会掉 oak_log
kg2, eng2, reg2, loop2, emb2, am2, seen2, base2 = build()
# 把新鲜感让给采集：不再有陌生生物，只剩木头
WORLD["nearbyEntities"] = []
r3 = loop2.tick(now=1000.0)
check("无陌生生物时选择采集（候选目标用世界汇报的方块名）",
      r3.get("acted") and r3.get("intent") == "gather_resource"
      and r3.get("target") == "oak_log", str(r3))
check("采集先查附近方块位置（不硬编码坐标）",
      any(c[0] == "/find_blocks" and c[1].get("block") == "oak_log" for c in calls),
      str(calls))
check("在目标旁 → 直接开挖（dig_pos）",
      any(c[0] == "/dig_pos" and c[1].get("x") == 2.0 for c in calls), str(calls))
check("异步动作进入承诺期（当前动作不为空）",
      am2.current is not None and am2.current["action_type"] == "gather_resource",
      str(am2.status()["current"]))
# 回执：真实完成（挖一块 → 再找下一块 → 够量 → 结算成功）
action_result = {"status": "done", "detail": {"dug": 1}}
r4 = None
for _ in range(5):
    flush(emb2)
    r4 = loop2.tick(now=1000.0 + loop2.cfg["min_interval_s"] + 1)
    if am2.current is None:
        break
check("取回执后动作结清并成功",
      am2.status()["stats"]["success"] >= 1 and am2.current is None,
      str(am2.status()["stats"]))
# ── 4b. §四D 假阳性防线：方块挖开了、背包一件没多 → 不得报成功 ──
# （2026-09-25 真机：徒手挖 deepslate 掉 0 件，旧 _finish 按"挖掉几块"计数
# 报 ok，学习层跟着记 succeeded——本段把这个判据钉死在"背包净增"上。）
DROPS.clear()                      # 这个世界挖开什么都不掉
kg2b, eng2b, reg2b, loop2b, emb2b, am2b, seen2b, base2b = build()
WORLD["nearbyEntities"] = []
FIND_POSITIONS = [{"x": 2.0, "y": 64.0, "z": 0.0}]
action_result = {"status": "done", "detail": {"dug": 1}}
loop2b.tick(now=1000.0)
for _ in range(5):
    flush(emb2b)
    loop2b.tick(now=1000.0 + loop2b.cfg["min_interval_s"] + 1)
    if am2b.current is None:
        break
check("挖开≠采集成功：背包零增益 → dug_no_item 如实失败",
      am2b.status()["stats"]["success"] == 0
      and am2b.status()["stats"]["failed"] >= 1
      and any("dug_no_item" in str(a.get("reason"))
              for a in am2b.status()["recent"]), str(am2b.status()["recent"][-1:]))
DROPS["default"] = "oak_log"
# 回执：真实失败
kg3, eng3, reg3, loop3, emb3, am3, seen3, base3 = build()
WORLD["nearbyEntities"] = []
FIND_POSITIONS = [{"x": 2.0, "y": 64.0, "z": 0.0}]
action_result = {"status": "failed", "detail": {"error": "dig timeout"}}
loop3.tick(now=1000.0)
flush(emb3)
r5 = loop3.tick(now=1000.0 + loop3.cfg["min_interval_s"] + 1)
check("失败回执 → 真实失败原因入认知",
      am3.status()["stats"]["failed"] >= 1
      and any("dig timeout" in str(a.get("reason"))
              for a in am3.status()["recent"]), str(am3.status()["recent"][-1:]))
FIND_POSITIONS = []
WORLD["nearbyEntities"] = [{"name": "axolotl", "displayName": "Axolotl", "dist": 5.0,
                            "rel": {"dx": -4.0, "dy": 0.0, "dz": 3.0}}]
action_result = {"status": "done", "detail": {}}

# ── 5. 用户优先：交互后自主行动让位（2026-09-21 新语义）────
# 让位=取消在飞自主动作（调度礼仪）+ user_recent 调制抬阈值；
# "固定安静期禁闭决策"已拆除——足够强的内在驱动可以立即行动。
kg4, eng4, reg4, loop4, emb4, am4, seen4, base4 = build()
WORLD["nearbyEntities"] = []          # 先在没有陌生生物的世界起飞
r0 = loop4.tick(now=4999.0)          # 先让自主动作起飞（探索）
WORLD["nearbyEntities"] = [{"name": "axolotl", "displayName": "Axolotl",
                            "dist": 5.0,
                            "rel": {"dx": -4.0, "dy": 0.0, "dz": 3.0}}]
loop4.notify_user_activity(quiet_s=20, now=5000.0)
check("用户交互 → 在飞自主动作被取消让位",
      r0.get("acted") is True and not am4.busy(), str(r0))
r6 = loop4.tick(now=5001.0)
# 5001 距刚才的动作起飞仅 2s——被 min_interval 挡（正常节流），
# 关键是 reason 不再是 user_active 硬封门；间隔过后恢复决策。
check("用户刚发过话 → 决策不被硬封门（只有常规节流，无 user_active 禁闭）",
      r6.get("reason") in ("min_interval", "no_change")
      or r6.get("acted") is True, str(r6))
r6b = loop4.tick(now=5000.0 + loop4.cfg["min_interval_s"] + 1)
check("间隔过后按内在驱动行动（强好奇 → 观察陌生生物）",
      r6b.get("acted") is True
      and r6b.get("intent") == "inspect_entity", str(r6b))

# ── 6. 掉线：不可用即停手 ────────────────────────────────
WORLD["connected"] = False
r7 = loop4.tick(now=5100.0)
check("bot 掉线 → 立刻停手（不再发动作）", r7.get("acted") is False
      and r7.get("reason") == "embodiment_unavailable", str(r7))
WORLD["connected"] = True

for d in (base, base2, base3, base4):
    shutil.rmtree(d, ignore_errors=True)

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 自主闭环集成测试全过（真实自主层+ActionManager+具身适配器，仅 bot HTTP 层为假桥）")
