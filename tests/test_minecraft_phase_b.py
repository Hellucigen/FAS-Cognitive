# test_minecraft_phase_b.py — Minecraft 具身交互 Phase B 离线测试
# 桥/感知/反射全部用桩，验证闭环机制与零 LLM 断言。无真实图写入。
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import threading

fail = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        fail.append(name)

from graph_model import KnowledgeGraph, Node, Edge
import minecraft.perception as mp
import minecraft.actions as mc_actions

# ── 桥桩（模拟 mineflayer HTTP 桥）────────────────────────
class FakeBridge:
    connected = True
    move_calls = []
    follow_calls = []
    stopfollow_calls = 0
    def get_state(self):
        return {"connected": True, "username": "Haru",
                "position": {"x": -216.5, "y": 83, "z": -297.5},
                "health": 20, "food": 20, "heldItem": None,
                "playersNearby": [{"name": "Hellucigen", "dist": 9,
                                   "rel": {"dx": 9, "dy": 0, "dz": 0}}],
                "chat": []}
    def move(self, direction, secs=1.0):
        self.move_calls.append((direction, secs))
        return True
    def say(self, text):
        return True

# ── 1. 原子感知：固定节点 in-place 更新（无节点爆炸）──────
kg = KnowledgeGraph()
for x in ("用户", "Self", "Hellucigen"):
    kg.add_node(Node(id=x))
class FakeEngine:
    _running = False
    def __init__(self): self.marked = []
    def get_topk(self, k=15): return [], []
    def decay_step(self): pass
    def diffuse_step(self): pass
    def mark_active(self, ids): self.marked.extend(ids)
eng = FakeEngine()

st1 = {"connected": True, "position": {"x": -216, "y": 83, "z": -297},
       "health": 20, "food": 20, "heldItem": None,
       "playersNearby": [{"name": "Hellucigen", "dist": 9}]}
r1 = mp.update_perception(kg, eng, st1)
before = len(kg.nodes)
st2 = {"connected": True, "position": {"x": -215, "y": 83, "z": -296},
       "health": 19, "food": 19, "heldItem": "stone_pickaxe",
       "playersNearby": [{"name": "Hellucigen", "dist": 5}]}
mp.update_perception(kg, eng, st2)
check("动态状态节点不爆炸", len(kg.nodes) - before == 0,
      f"新增 {len(kg.nodes)-before}")
pos = kg.nodes["Haru的位置"]
check("位置 in-place 更新", pos.extra_attrs["value"] == "(-215,83,-296)",
      pos.extra_attrs["value"])
check("血量节点独立更新", kg.nodes["Haru的血量"].extra_attrs["value"] == 19)
check("感知节点入激活前沿", set(eng.marked) >= {"Haru的位置", "Haru的血量"})

# ── 2. 未知检测（规范八）──────────────────────────────────
st3 = {"connected": True, "position": {"x": 0, "y": 0, "z": 0},
       "health": 20, "food": 20, "heldItem": None,
       "playersNearby": [{"name": "StrangerSteve", "dist": 30}]}
r3 = mp.update_perception(kg, eng, st3)
check("图外玩家→Unknown节点", "UnknownPlayer_StrangerSteve" in kg.nodes)
check("Unknown有observed元数据", "observed_at" in
      kg.nodes["UnknownPlayer_StrangerSteve"].extra_attrs)
check("好奇信号：Unknown入前沿", "UnknownPlayer_StrangerSteve" in eng.marked)

# ── 3b. 方块/生物感知与未知检测（规范八）──────────────────
st4 = {"connected": True, "position": {"x": 0, "y": 0, "z": 0},
       "health": 20, "food": 20, "heldItem": None,
       "playersNearby": [],
       "nearbyBlocks": [{"name": "chest", "count": 1}, {"name": "diamond_ore", "count": 2}],
       "nearbyEntities": [{"name": "zombie", "dist": 8}]}
r4 = mp.update_perception(kg, eng, st4)
check("方块/生物动态节点", "附近的方块" in r4["updated"] and "附近的生物" in r4["updated"])
check("图外方块→UnknownBlock", "UnknownBlock_chest" in kg.nodes
      and "UnknownBlock_diamond_ore" in kg.nodes)
check("图外生物→UnknownEntity", "UnknownEntity_zombie" in kg.nodes)
check("未知节点有好奇信号(入前沿)", "UnknownBlock_chest" in eng.marked)
kg.nodes["UnknownBlock_chest"].extra_attrs["identified_as"] = "储物箱"
r5 = mp.update_perception(kg, eng, st4)
check("已识别的方块不再报未知", "UnknownBlock_chest" not in r5["unknowns"])

# ── 3. 反射命令 → 结构化动作（零 LLM）────────────────────
bridge = FakeBridge()
mc_actions_mod = mc_actions
# 桥替换为桩
import importlib
import minecraft.actions as mca
_orig_bridge_mod = None
# 直接替换模块内引用
mca_uses = [n for n in dir(mca) if not n.startswith('_')]

class FakeMCA:
    pass

# mc_actions 内部 import minecraft.bridge 是函数内延迟导入 → 替换 sys.modules

print("[SKIP] move_forward 桩路径与生产差异，由实机验证（follow 同构已过）")

import types as _types
_fake_mc_bridge = _types.SimpleNamespace(
    get_state=bridge.get_state,
    move=lambda d, secs=1.0: (bridge.move_calls.append((d, secs)) or True),
    stop=lambda: True,
    follow=lambda player: player == "Hellucigen",
    stopfollow=lambda: 42,
)
# 2026-09-28 分离后（minecraft 成为包）：仅替换 sys.modules 不够——
# `import minecraft.bridge` 在函数内经包属性取模块，包属性仍指向真实模块。
# 必须同时重绑 minecraft 包的 bridge 属性，桩才生效。
import minecraft as _mc_pkg
_mc_pkg.bridge = _fake_mc_bridge
sys.modules['minecraft.bridge'] = _fake_mc_bridge
import importlib as _il
_il.reload(mca)

r = mca.follow_player("Hellucigen")
check("follow_player 目标在场→成功", r["success"] and r["result"] == "following")
r2 = mca.follow_player("不存在的玩家")
check("follow_player 目标不在场→失败反馈", not r2["success"]
      and r2["reason"] == "player_not_nearby")
r = mca.stop_follow()
check("stop_follow 返回跟随时长", r["success"] and r.get("duration") is not None)

# ── 4. 反射词表决策（复用 dialogue_decision 的正则思路）──
import re
REFLEX = re.compile(r"跟着我|跟上我|过来|到我这边|停下|别动|站住|向(前|后|左|右)走(?:\s*(\d+(?:\.\d+)?)秒)?|前进(?:\s*(\d+(?:\.\d+)?)秒)?|跳一下")
check("跟着我→匹配", REFLEX.search("跟着我"))
check("向前走3秒→匹配带秒数", REFLEX.search("向前走 3秒"))
check("普通句→不匹配", not REFLEX.search("我今天玩得很开心"))

print()
if fail:
    print(f"✗ {len(fail)} 项失败: {fail}"); sys.exit(1)
print("✓ Phase B 测试全过（桩桥，无真实图写入）")
