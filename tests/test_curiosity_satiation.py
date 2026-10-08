# test_curiosity_satiation.py — B3：好奇饱食与物种学习验收（§6/§15）
# ============================================================================
# 钉死"认识"作为一个学习事件的全部合同：
#   1 首遇：感知建 UnknownEntity_cow + 满注意力地板 + 未知信息信号注入；
#   2 真实观察回执（inspect_entity 带 dist/rel）→ 经验识别：
#     物种节点沉淀（type=mc_species，source=experience，对齐 mc_knowledge
#     查询层）、证据边、Unknown **标记退役而非删除**（历史保留）、
#     note_interest(resolved) 水平 ×0.4 **且 >0**（永不清零合同）、
#     fas_log CURIOSITY "curiosity_satiated" 前后对照。
#   3 二遇：不再建 Unknown、不再注入信号（饱食链靠 _unknown 既有幂等）。
#   4 地板与集合：退役节点只拿在视普通地板 0.6，不再被报成未知物。
#   5 空手回执不饱食：found:0 的 inspect 是"没看到"，不是"认识了"。
#   6 消费点守卫：drive graph.unknown_objects 计数、capability
#     unknown_present 门、autonomy novelty 分量都只认"活未知"。
# 离线：假桥 + 内存图；不碰真实数据、不调 LLM、不写用户经历。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_curiosity_satiation.py
# ============================================================================

import math
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


import config as C
import minecraft.bridge as bridge
import fas_log
from graph_model import (KnowledgeGraph, Node, is_live_unknown,
                         is_live_unknown_id)
from diffusion_engine import DiffusionEngine
import curiosity_engine as ce
from minecraft.perception import update_perception, promote_species
from minecraft.embodiment import MinecraftEmbodiment
from drive_engine import DriveEvaluator
from capability_graph import CapabilityIndex
from autonomy import AutonomousLoop

BASE = os.path.join(tempfile.gettempdir(), "fas_curiosity_sat")
shutil.rmtree(BASE, ignore_errors=True)
os.makedirs(BASE, exist_ok=True)

CFG = dict(C.DEFAULT_CONFIG)
DECAY = float((CFG.get("curiosity") or {}).get(
    "interest_resolve_decay", 0.4))

ENG_CFG = {"lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 4,
           "theta_threshold": 0.01, "activation_max": 5.0,
           "min_spread_threshold": 0.01, "activation_epsilon": 1e-4,
           "input_similarity_floor": 0.5, "input_default_bonus": 0.5,
           "theta_action": 0.5}

STATE = {}


def set_state(**over):
    STATE.clear()
    STATE.update({
        "connected": True, "username": "Haru",
        "position": {"x": 0.0, "y": 64.0, "z": 0.0},
        "health": 20, "food": 20, "heldItem": None,
        "playersNearby": [], "nearbyEntities": [], "nearbyBlocks": [],
        "chat": [], "connectedAt": 1, "timeOfDay": 6000, "isRaining": False,
    })
    STATE.update(over)


INV = {"items": []}


def install():
    bridge.get_state = lambda: dict(STATE)
    bridge.health = lambda: True
    bridge.inventory = lambda: {"ok": True, "items": list(INV["items"])}
    bridge.call = lambda p, payload=None, timeout=3: {"ok": True}
    bridge.stop_goto = lambda: {"ok": True}
    bridge.sprint = lambda on=True: {"ok": True}
    bridge.move = lambda d, s=1.0: True
    bridge.get_action_result = lambda: {}


install()
COW = [{"name": "cow", "dist": 3.2, "rel": {"dx": 3.2, "dy": 0.0, "dz": 0.0}}]


def mk(kg):
    eng = DiffusionEngine(kg, dict(ENG_CFG))
    eng.name_to_node = dict(kg.nodes)
    emb = MinecraftEmbodiment(kg=kg, engine=eng, config=dict(CFG))
    # 背包刷新是时间门（首拍即拉），旧计数旋钮 _inv_every 已退役
    return emb, eng


# ═══ 1. 首遇：Unknown 建档 + 满地板 + 信号注入 + 兴趣抬升 ═══
print("\n── 1 首遇 ──")
kg = KnowledgeGraph()
kg.add_node(Node(id="Haru"))
kg.add_node(Node(id="未知信息"))
kg.nodes["未知信息"].activation = 0.0
emb, eng = mk(kg)
set_state(nearbyEntities=COW)
r1 = emb.perceive()
uid = "UnknownEntity_cow"
check("首遇建档：UnknownEntity_cow 被报为未知",
      any(i["name"] == "cow" for i in r1.get("unknown_entities", []))
      and uid in kg.nodes, str(r1.get("unknown_entities")))
check("首遇是新颖信号：未知信息被注入（activation 抬升）",
      kg.nodes["未知信息"].activation >= 0.3,
      str(kg.nodes["未知信息"].activation))
check("首遇满注意力地板 1.0",
      abs(kg.nodes[uid].activation - 1.0) < 1e-9,
      str(kg.nodes[uid].activation))
ce.note_interest(kg, uid, "signal", CFG)      # 生产同函数：又遇到 → 兴趣抬升
lv0 = ce.get_interest(kg, uid)["level"]
check("首遇兴趣水平 > 0（峰）", lv0 > 0, str(lv0))

# ═══ 2. 真实观察结算 → 经验识别（on_settled 钩子，零 LLM）═══
print("\n── 2 inspect 结算沉淀 ──")
_seen = []
_orig_emit = fas_log.emit
fas_log.emit = lambda sub, lvl, evt, msg="", **f: _seen.append((sub, evt, f))
try:
    emb.on_settled(
        {"action_type": "inspect_entity", "target": "cow"},
        {"action": "inspect_entity", "success": True,
         "result": {"entity": "cow", "dist": 3.2,
                    "rel": {"dx": 3.2, "dy": 0.0, "dz": 0.0}}}, True)
    spn = kg.nodes.get("cow")
    check("物种节点沉淀：raw-id cow，type=mc_species，source=experience",
          spn is not None
          and (spn.extra_attrs or {}).get("type") == "mc_species"
          and (spn.extra_attrs or {}).get("source") == "experience",
          str(spn.extra_attrs) if spn else "缺节点")
    try:
        from mc_knowledge import species_node
        check("与 mc_knowledge 查询层对齐（species_node 能找到）",
              species_node(kg, "Cow") is spn)
    except Exception as e:
        check("与 mc_knowledge 查询层对齐（species_node 能找到）", False, str(e))
    check("证据边：当前Minecraft状态 -[出现过]-> cow",
          kg.get_edge("当前Minecraft状态", "cow", "出现过") is not None)
    node_u = kg.nodes.get(uid)
    check("Unknown 不删除，只标记退役（历史保留）",
          node_u is not None
          and (node_u.extra_attrs or {}).get("retired") == "recognized"
          and (node_u.extra_attrs or {}).get("recognized_as") == "cow")
    check("退役后不再是活未知", not is_live_unknown(node_u))
    it = ce.get_interest(kg, uid)
    check("兴趣部分饱食：×0.4（合同 decay），且**永不清零**",
          math.isclose(it["level"], round(lv0 * DECAY, 4), abs_tol=1e-9)
          and it["level"] > 0, f"{lv0}→{it['level']}")
    check("resolved_count 进入 satiation 通道", it["resolved_count"] == 1,
          str(it))
    sat = [s for s in _seen if s[1] == "curiosity_satiated"]
    check("观测面：fas_log CURIOSITY curiosity_satiated 带前后水平",
          len(sat) == 1 and sat[0][0] == fas_log.CURIOSITY
          and sat[0][2].get("level_before") == round(lv0, 3)
          and sat[0][2].get("level_after") == round(it["level"], 3),
          str(sat))

    # ═══ 3. 二遇：不再建 Unknown、不再注入（饱食链的主体）═══
    print("\n── 3 二遇 ──")
    sig_before = kg.nodes["未知信息"].activation
    set_state(position={"x": 4.0, "y": 64.0, "z": 0.0},
              nearbyEntities=COW)
    p2 = emb.perceive()
    check("二遇不再报未知（unknown_entities 里没有 cow）",
          not any(i["name"] == "cow" for i in p2.get("unknown_entities", [])),
          str(p2.get("unknown_entities")))
    check("二遇不再注入未知信息信号",
          kg.nodes["未知信息"].activation == sig_before,
          f"{sig_before}→{kg.nodes['未知信息'].activation}")

    # ═══ 4. 地板：退役节点只拿在视普通地板 ═══
    print("\n── 4 注意力地板降级 ──")
    kg.nodes[uid].activation = 0.05
    set_state(position={"x": 8.0, "y": 64.0, "z": 0.0},
              nearbyEntities=COW)
    emb.perceive()
    check("退役 Unknown 在视 → 0.6（不再是 1.0 的新颖托举）",
          abs(kg.nodes[uid].activation - 0.6) < 1e-9,
          str(kg.nodes[uid].activation))

    # ═══ 5. 空手回执不饱食（诚实边界）═══
    print("\n── 5 found:0 不算认识 ──")
    set_state(position={"x": 9.0, "y": 64.0, "z": 0.0},
              nearbyEntities=COW + [{"name": "chicken", "dist": 5.0,
                                     "rel": {"dx": 5.0}}],
              nearbyBlocks=[{"name": "amethyst_shard", "count": 2}])
    emb.perceive()
    cid, bid = "UnknownEntity_chicken", "UnknownBlock_amethyst_shard"
    check("新物建档（chicken/amethyst_shard 是活未知）",
          is_live_unknown_id(kg, cid) and is_live_unknown_id(kg, bid))
    emb.on_settled(
        {"action_type": "inspect_entity", "target": "chicken"},
        {"action": "inspect_entity", "success": True,
         "result": {"entity": "chicken", "found": 0}}, True)
    emb.on_settled(
        {"action_type": "inspect_block", "target": "amethyst_shard"},
        {"action": "inspect_block", "success": True,
         "result": {"block": "amethyst_shard", "found": 0}}, True)
    check("空手回执（「没看到」）不退役、不沉淀（宁缺毋假）",
          is_live_unknown_id(kg, cid) and is_live_unknown_id(kg, bid)
          and "chicken" not in kg.nodes
          and "amethyst_shard" not in kg.nodes)
    emb.on_settled(
        {"action_type": "inspect_entity", "target": "chicken"},
        {"action": "inspect_entity", "success": True,
         "result": {"entity": "chicken", "dist": 5.0,
                    "rel": {"dx": 5.0}}}, True)
    emb.on_settled(
        {"action_type": "inspect_block", "target": "amethyst_shard"},
        {"action": "inspect_block", "success": True,
         "result": {"block": "amethyst_shard", "found": 2,
                    "nearest": {"x": 8, "y": 63, "z": 0}}}, True)
    check("真实观察回执 → 实体与方块都能退役沉淀",
          "chicken" in kg.nodes and not is_live_unknown_id(kg, cid)
          and "amethyst_shard" in kg.nodes
          and not is_live_unknown_id(kg, bid))

    # ═══ 7. 幂等：再 inspect 已认识的物种 ═══
    print("\n── 7 重复观察幂等 ──")
    n_sat = len([s for s in _seen if s[1] == "curiosity_satiated"])
    lv_before = ce.get_interest(kg, uid)["level"]
    emb.on_settled(
        {"action_type": "inspect_entity", "target": "cow"},
        {"action": "inspect_entity", "success": True,
         "result": {"entity": "cow", "dist": 2.0,
                    "rel": {"dx": 2.0}}}, True)
    check("不重复发饱食日志（只在首次退役时）",
          len([s for s in _seen if s[1] == "curiosity_satiated"]) == n_sat)
    check("物种节点累计观察次数（observed_count 2）",
          int((kg.nodes["cow"].extra_attrs or {}).get("observed_count", 0))
          == 2, str(kg.nodes["cow"].extra_attrs))
    lv_after = ce.get_interest(kg, uid)["level"]
    check("重复确认继续部分饱食但永不清零", 0 < lv_after < lv_before,
          f"{lv_before}→{lv_after}")

    # promote_species 单元边界：图里没有的未知 / 空名 不炸不编
    check("无 Unknown 节点的名字 → 不沉淀（不造知识）",
          promote_species(kg, None, "ghostmob")["reason"] == "no_unknown_node"
          and "ghostmob" not in kg.nodes)
    check("空名/None 安全", promote_species(kg, None, "")["promoted"] is False
          and promote_species(None, None, "cow")["promoted"] is False)
finally:
    fas_log.emit = _orig_emit

# ═══ 6. 消费点守卫（drive 计数 / capability 门 / autonomy novelty）═══
print("\n── 6 消费点 ──")
kg5 = KnowledgeGraph()
kg5.add_node(Node(id="Haru"))
for nid, retired in ((uid, True), ("UnknownEntity_pig", False)):
    kg5.add_node(Node(id=nid, extra_attrs={"type": "unknown"}))
    kg5.nodes[nid].extra_attrs["retired"] = "recognized" if retired else None
    if not retired:
        kg5.nodes[nid].extra_attrs.pop("retired")
    kg5.nodes[nid].activation = 1.0

ev = DriveEvaluator(kg5, dict(CFG))
ev.bootstrap_drives()
ev.evaluate(force=True)
raws = ev.field.tensions.raws()
hit = None
for _tn, comps in raws.items():
    if "graph.unknown_objects" in comps:
        hit = comps["graph.unknown_objects"]
        break
check("drive graph.unknown_objects 只数活未知（1，不是 2）",
      hit is not None and hit["note"]["count"] == 1
      and abs(hit["raw"] - 0.25) < 1e-9, str(hit))

ci = CapabilityIndex(kg5, None, dict(CFG))
check("unknown_present 门：有活未知（pig, 激活≥0.5）→ 开",
      ci._gates_ok([{"op": "unknown_present"}], None) is True)
kg5.nodes["UnknownEntity_pig"].extra_attrs["retired"] = "recognized"
kg5.nodes["UnknownEntity_pig"].extra_attrs["recognized_as"] = "pig"
check("unknown_present 门：全部退役 → 关（live 快照也空）",
      ci._gates_ok([{"op": "unknown_present"}], None) is False)

loop = AutonomousLoop(kg=kg5, config=dict(CFG), data_dir=BASE)
pig = kg5.nodes["UnknownEntity_pig"]
pig.extra_attrs.pop("retired")          # 恢复活未知
_s, expl_live = loop._score_action(
    {"action_type": "inspect_entity", "target": "pig",
     "reason": ["UnknownEntity_pig"]},
    {"connected": True, "health": 20, "food": 20,
     "position": {"x": 0, "y": 64, "z": 0},
     "players": [], "entities": [], "blocks": [],
     "unknown_entities": [], "unknown_blocks": []}, time.time())
pig.extra_attrs["retired"] = "recognized"
_s, expl_ret = loop._score_action(
    {"action_type": "inspect_entity", "target": "pig",
     "reason": ["UnknownEntity_pig"]},
    {"connected": True, "health": 20, "food": 20,
     "position": {"x": 0, "y": 64, "z": 0},
     "players": [], "entities": [], "blocks": [],
     "unknown_entities": [], "unknown_blocks": []}, time.time())
check("autonomy novelty：活未知为基底 → nov=1.00",
      "nov=1.00" in expl_live, expl_live)
check("autonomy novelty：退役基底不再钉满新颖性",
      "nov=1.00" not in expl_ret, expl_ret)

shutil.rmtree(BASE, ignore_errors=True)

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
    sys.exit(1)
print("PASS: 好奇饱食与物种学习全部通过（建档/沉淀/二遇/地板/空手/消费点/幂等）")
