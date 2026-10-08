# test_world_events.py — B2：世界状态与事件层验收（§7/§8/§9）
# ============================================================================
# 钉死四件事：
#   1 单一写者：对话回合不再裸调 update_perception（app.py grep 断言）——
#      旧双写缺 hostile_names 实参，每轮对话把"附近的生物"敌对标注抹平；
#      本测试用一对正反用例把病灶与修后行为都钉住（裸调确实抹平=病灶真实，
#      具身 perceive 路径跨轮存活=已修）。
#   2 背包计数差分：物品增减 → 恰好一条 inventory:{name} 事件（before/after）。
#   3 动作回执 → 含坐标的方块事件（on_settled 协议钩子；快照差分盖不住
#      "五块石头挖掉一块"这种类型计数不变的世界变化）。
#   4 content 键标准化（change/target/before/after）+ debug_world_events 开关。
# 离线：假桥 + 临时目录 + 内存图；不碰真实数据、不调 LLM、不写用户经历。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_world_events.py
# ============================================================================

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
from graph_model import KnowledgeGraph, Node
from diffusion_engine import DiffusionEngine
from experience import ExperienceTimeline, EVENT_OBSERVATION
from minecraft.embodiment import MinecraftEmbodiment
from minecraft.perception import update_perception

BASE = os.path.join(tempfile.gettempdir(), "fas_world_events")
shutil.rmtree(BASE, ignore_errors=True)
os.makedirs(BASE, exist_ok=True)

ENG_CFG = {"lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 4,
           "theta_threshold": 0.01, "activation_max": 5.0,
           "min_spread_threshold": 0.01, "activation_epsilon": 1e-4,
           "input_similarity_floor": 0.5, "input_default_bonus": 0.5,
           "theta_action": 0.5}


def mk_eng(kg):
    e = DiffusionEngine(kg, dict(ENG_CFG))
    e.name_to_node = dict(kg.nodes)
    return e

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


def build_emb(kg=None, hostile=("zombie",), dbg=False):
    cfg = dict(C.DEFAULT_CONFIG)
    cfg["hostile_entities"] = list(hostile)
    cfg["debug_world_events"] = dbg
    tl = ExperienceTimeline(path=os.path.join(
        BASE, f"exp_{time.time_ns()}.json"), config=cfg)
    eng = mk_eng(kg) if kg is not None else None
    emb = MinecraftEmbodiment(kg=kg, engine=eng, config=cfg)
    emb.timeline = tl
    # 生产背包刷新是时间门（20s，2026-09-26 改造；旧的 _inv_every 计数
    # 字段已删）。测试每拍都要看到 INV 变化：每拍前把上次拉取时间归零。
    _orig_perceive = emb.perceive

    def _perceive_now(*a, **k):
        emb._inv_last_pull = 0.0
        return _orig_perceive(*a, **k)
    emb.perceive = _perceive_now
    return emb, tl


def evs(tl, n=80):
    return tl.events_since(0.0)[:n]        # 时间序（recent 是最新优先，断言易反）


# ═══ 1. 敌对标注：裸调抹平（病灶实证） vs 具身路径跨轮存活（已修） ═══
print("\n── 1 敌对标注单一写者 ──")
install()
kg = KnowledgeGraph()
kg.add_node(Node(id="Haru"))
eng = mk_eng(kg)
set_state(nearbyEntities=[{"name": "zombie", "dist": 4.0,
                           "rel": {"dx": 4.0, "dy": 0.0, "dz": 0.0}}])
update_perception(kg, eng, dict(STATE), hostile_names=["zombie"])
hd1 = (kg.nodes.get("附近的生物").extra_attrs or {}).get("hostile_dist") or {}
check("带名单的写图给敌对标注", "zombie" in hd1, str(hd1))
# 旧对话双写的调用形状：缺 hostile_names → 标注被抹平（病灶真实存在）
update_perception(kg, eng, dict(STATE))
hd2 = (kg.nodes.get("附近的生物").extra_attrs or {}).get("hostile_dist") or {}
check("裸调（缺名单实参）确实抹平标注——这就是当初要删的双写",
      "zombie" not in hd2, str(hd2))

emb, tl = build_emb(kg)
_zombie = [{"name": "zombie", "dist": 4.0, "rel": {"dx": 4.0, "dy": 0.0, "dz": 0.0}}]
set_state(nearbyEntities=_zombie)
emb.perceive()
set_state(position={"x": 1.0, "y": 64.0, "z": 0.0}, nearbyEntities=_zombie)
emb.perceive()
hd3 = (kg.nodes.get("附近的生物").extra_attrs or {}).get("hostile_dist") or {}
check("统一走具身 perceive 门控路径后，敌对标注跨轮存活",
      "zombie" in hd3, str(hd3))

# app.py 病灶回归锁：对话路径不得再出现裸 update_perception 调用
src = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app.py"), encoding="utf-8").read()
import re
_app_calls = [m.start() for m in re.finditer(
    r"(?<!def )\bmc_update_perception\(|\bupdate_perception\(", src)]
check("app.py 中 update_perception 生产调用为零（双写已删）",
      _app_calls == [], f"{len(_app_calls)} 处")

# ═══ 2. 背包计数差分 ═══
print("\n── 2 背包事件 ──")
install()
kg = KnowledgeGraph()
kg.add_node(Node(id="Haru"))
emb, tl = build_emb(kg)
set_state()
INV["items"] = [{"name": "oak_log", "count": 2}]
emb.perceive()                       # 首拍只立基准，不发"变化"
set_state(position={"x": 2.0, "y": 64.0, "z": 0.0})
INV["items"] = [{"name": "oak_log", "count": 3}]
emb.perceive()
inv_events = [e for e in evs(tl) if str(e["subject"]).startswith("inventory:")]
check("多一块木头 → 恰好一条 inventory 事件",
      len(inv_events) == 1, str([(e['subject'], e['content']) for e in inv_events]))
c = inv_events[0]["content"] if inv_events else {}
check("事件说真话：subject=inventory:oak_log，方向+before/after 完整",
      inv_events and inv_events[0]["subject"] == "inventory:oak_log"
      and c.get("before") == 2 and c.get("after") == 3
      and c.get("change") == "count_increased", str(c))
set_state(position={"x": 3.0, "y": 64.0, "z": 0.0})
INV["items"] = []
emb.perceive()
inv_events = [e for e in evs(tl) if str(e["subject"]).startswith("inventory:")]
check("清空背包 → 减少也算（decreased 不被 increased 的合并语义吞掉）",
      len(inv_events) == 2
      and inv_events[-1]["content"].get("change") == "count_decreased"
      and inv_events[-1]["content"].get("before") == 3
      and inv_events[-1]["content"].get("after") == 0, str(inv_events[-1:]))

# ═══ 3. 动作回执 → 含坐标的方块事件（on_settled 协议钩子）═══
print("\n── 3 坐标级世界事件 ──")
emb, tl = build_emb()
emb.on_settled(
    {"action_type": "gather_resource", "target": "coal_ore"},
    {"action": "gather_resource", "success": True,
     "result": {"resource": "coal_ore", "collected": 2,
                "dug_positions": [{"x": 5, "y": 61, "z": -3},
                                  {"x": 6, "y": 61, "z": -3}]}}, True)
b_evs = [e for e in evs(tl) if str(e["subject"]).startswith("block:")]
check("挖两处 → 一条含全部坐标的 block 事件（快照差分给不了这个）",
      len(b_evs) == 1 and b_evs[0]["subject"] == "block:coal_ore"
      and b_evs[0]["content"].get("positions") == [{"x": 5, "y": 61, "z": -3},
                                                   {"x": 6, "y": 61, "z": -3}],
      str([(e["subject"], e["content"].get("positions")) for e in b_evs]))
check("事件带 action/result/before(count)/after（标准化键）",
      b_evs and b_evs[0]["content"].get("action") == "gather_resource"
      and b_evs[0]["content"].get("result") == "success"
      and b_evs[0]["content"].get("before") == 2
      and b_evs[0]["content"].get("after") == 0
      and b_evs[0]["content"].get("pos") == {"x": 5, "y": 61, "z": -3},
      str(b_evs[0]["content"]) if b_evs else "")
emb.on_settled(
    {"action_type": "place_block", "target": "oak_planks"},
    {"action": "place_block", "success": True,
     "result": {"block": "oak_planks", "position": {"x": 0, "y": 64, "z": 1}}}, True)
b_evs = [e for e in evs(tl) if str(e["subject"]).startswith("block:")]
placed = [e for e in b_evs if e["content"].get("change") == "appeared"]
check("放置 → appeared 事件（方向不反）", len(placed) == 1, str(b_evs))
n_before = len(evs(tl))
emb.on_settled({"action_type": "say", "target": None},
               {"action": "say", "success": True, "result": {}}, True)
emb.on_settled({"action_type": "gather_resource", "target": "stone"},
               {"action": "gather_resource", "success": False,
                "reason": "no_path", "result": {}}, False)
check("无坐标事实的回执/失败结算 → 不造事件（宁缺毋假）",
      len(evs(tl)) == n_before, f"{n_before}→{len(evs(tl))}")

# timeline=None 的具身（未接线）调用钩子不炸
bare = MinecraftEmbodiment(kg=None, engine=None, config={})
try:
    bare.on_settled({"action_type": "place_block"},
                    {"success": True, "result": {"block": "dirt",
                     "position": {"x": 0, "y": 0, "z": 0}}}, True)
    check("timeline 未接线的具身：钩子静默无害", True)
except Exception as e:
    check("timeline 未接线的具身：钩子静默无害", False, str(e))

# ═══ 4. debug_world_events 开关 ═══
print("\n── 4 观测开关 ──")
_seen = []
_orig_emit = fas_log.emit
fas_log.emit = lambda sub, lvl, evt, msg="", **f: _seen.append((sub, evt, f))
try:
    _kg4 = KnowledgeGraph()
    _kg4.add_node(Node(id="Haru"))
    emb_on, tl_on = build_emb(_kg4, dbg=True)
    set_state(position={"x": 9.0, "y": 64.0, "z": 0.0})
    INV["items"] = [{"name": "dirt", "count": 1}]
    emb_on.perceive()
    INV["items"] = [{"name": "dirt", "count": 2}]
    set_state(position={"x": 10.0, "y": 64.0, "z": 0.0})
    emb_on.perceive()
    we = [s for s in _seen if s[1] == "world_event"]
    emb_off, tl_off = build_emb(_kg4, dbg=False)
    emb_off._inv_prev = [{"name": "dirt", "count": 2}]   # 已有基准 → 2→3 会产生 diff
    set_state(position={"x": 11.0, "y": 64.0, "z": 0.0})
    INV["items"] = [{"name": "dirt", "count": 3}]
    emb_off.perceive()
    we_off = [s for s in _seen if s[1] == "world_event"]
    off_events = [e for e in evs(tl_off) if str(e["subject"]).startswith("inventory:")]
    check("开关开：世界事件在 fas_log PERCEPTION 可见",
          len(we) >= 1 and we[0][0] == fas_log.PERCEPTION, str(we[:2]))
    check("开关关：不发观测行，但事件仍进时间轴",
          len(we_off) == len(we) and len(off_events) >= 1,
          f"log {len(we)}→{len(we_off)} tl={len(off_events)}")
finally:
    fas_log.emit = _orig_emit

shutil.rmtree(BASE, ignore_errors=True)

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
    sys.exit(1)
print("PASS: 世界事件层验收全部通过（单一写者 / 背包差分 / 坐标事件 / 观测开关）")
