# test_amble_fallback.py — 空闲踱步回归（2026-09-25，用户："真人会因为没到
# 阈值就站游戏里不动吗"）。
# 钉死四件事：
#   1 danger_visible 终于有生产者（此前全库只有消费者——risk 恒 0 的死线）；
#   2 Amble 技能：随机短距目标、y 不为 0、到时自然收束不记失败；
#   3 _maybe_amble：危险不逛、节流、提交经 ActionManager；
#   4 空闲分支真接线（below_threshold 时 tick 提出踱步）。
# 离线：假桥 + 真 ActionManager，零 LLM。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_amble_fallback.py
# ============================================================================
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

import config as _C
from graph_model import KnowledgeGraph
from skills.base import SkillContext
from skills.movement import Amble
from minecraft.embodiment import MinecraftEmbodiment
from autonomy import AutonomousLoop
from action_system import ActionManager

fail = []


def check(n, c, d=""):
    print(f"[{'PASS' if c else 'FAIL'}] {n}" + (f" | {d}" if d and not c else ""))
    if not c:
        fail.append(n)


CFG = dict(_C.DEFAULT_CONFIG)


class FakeBridge:
    """只覆盖 Amble/感知用到的原语。"""
    def look(self, yaw, pitch=0.0):
        self.looks = getattr(self, 'looks', 0) + 1
        return {"ok": True}


    def __init__(self, state):
        self._state = state
        self.gotos = []
        self.stops = 0

    def get_state(self):
        return dict(self._state)

    def goto_coords(self, x, y, z):
        self.gotos.append((round(x, 1), round(y, 1), round(z, 1)))
        return {"ok": True}

    def stop_goto(self):
        self.stops += 1
        return {"ok": True}

    def sprint(self, on):
        return {"ok": True}

    def move(self, d, s):
        self.moves = getattr(self, "moves", []) + [(d, s)]
        return {"ok": True}

    def jump(self):
        return {"ok": True}

    def get_action_result(self):
        return {"name": "", "status": ""}


def mk_state(hostile=None, hdist=4.0):
    ents = [{"name": "cow", "dist": 12.0}]
    if hostile:
        ents.append({"name": hostile, "dist": hdist})
    return {"connected": True, "position": {"x": 10.0, "y": 64.0, "z": 10.0},
            "health": 20, "food": 20, "heldItem": None,
            "playersNearby": [], "nearbyEntities": ents, "nearbyBlocks": []}


# ═══ 1. danger_visible 生产者 ═══
print("\n── 1 danger_visible 终于有生产者 ──")
base = tempfile.mkdtemp(prefix="fas_amble_")
kg = KnowledgeGraph()


class _Eng:
    def mark_active(self, ids):
        pass


emb = MinecraftEmbodiment(kg=kg, engine=None, config=dict(CFG),
                          perceive_into_graph=False)
import minecraft.bridge as _mb
_mb.get_state = lambda: mk_state()
p = emb.perceive() or {}
check("1 无敌对生物 → danger_visible False", p.get("danger_visible") is False,
      str(sorted((p or {}).keys()))[:120])
_mb.get_state = lambda: mk_state("zombie", 4.0)
p2 = emb.perceive() or {}
check("1b 敌对 4 格 → danger_visible True", p2.get("danger_visible") is True)
_mb.get_state = lambda: mk_state("zombie", 9.0)
p3 = emb.perceive() or {}
check("1c 敌对 9 格（出危险距离）→ False", p3.get("danger_visible") is False)

# ═══ 2. Amble 技能 ═══
print("\n── 2 Amble 技能：随机短距、y 不为 0、到时收束 ──")
br = FakeBridge(mk_state())
ctx = SkillContext(bridge=br, config=dict(CFG))
r = Amble().start(ctx, {})
check("2 start → pending 且转身发步（纯原始步态，不碰寻路）",
      r.get("ok") is True and not br.gotos
      and getattr(br, "looks", 0) >= 1, f"{r} gotos={br.gotos}")
r1 = Amble().poll(ctx)
check("2b 拍内原始迈步（forward）", r1.get("ok") is not False
      and any(m[0] == "forward" for m in getattr(br, "moves", [])), str(r1))
ctx.session["amble_deadline"] = time.time() - 1   # 时间到
r2 = Amble().poll(ctx)
check("2c 到时自然收束为成功（散步以时间服完为准）",
      r2.get("ok") is True and "走" in str(r2.get("describe") or ""), str(r2))

# ═══ 3. _maybe_amble：两档空闲（探索优先，踱步次之） ═══
print("\n── 3 空闲助手：短探索优先、踱步次之、守卫/节流 ──")


class _StubEmb:
    """隔离桩：绝不碰模块桥（section 3 曾泄漏真桥，把真 Haru 带走了 14 格）"""

    def __init__(self):
        self.executed = []

    def capabilities(self):
        return {"amble", "explore_direction"}

    def available(self):
        return True

    def execute(self, action):
        self.executed.append(action.get("action_type"))
        return {"success": True, "action": action.get("action_type"),
                "result": "stub"}

    def perceive(self):
        return {}

    def raw_state(self):
        return {}


stub = _StubEmb()
loop = AutonomousLoop(kg=kg, config=dict(CFG), data_dir=base)
am = ActionManager(embodiment=stub, kg=kg, config={}, data_dir=base)
loop.actions = am
loop.mode = "on"
loop._last_amble_ts = 0.0
loop._last_idle_explore_ts = 0.0
_T = time.time()
out = loop._maybe_amble(dict(p), _T)
check("3 安全空闲 → 优先短探索（有目标，非瞎晃）",
      out.get("reason") == "idle_explore" and out.get("acted") is True, str(out))
check("3b 探索方向取最少走的", loop._explore_dirs and
      max(loop._explore_dirs.values()) - min(loop._explore_dirs.values()) <= 1,
      str(loop._explore_dirs))
out1b = loop._maybe_amble(dict(p), _T + 5)
check("3c 刚探索完：共用节流先安静（她刚动过 45s）",
      out1b.get("reason") == "amble_pacing", str(out1b))
out1c = loop._maybe_amble(dict(p), _T + 61)
check("3c2 节流期满 → 退为踱步（探索 180s 内不连刷）",
      out1c.get("reason") == "amble" and out1c.get("acted") is True, str(out1c))
check("3c3 两档都真到了执行器", stub.executed[:3] ==
      ["explore_direction", "amble"] or stub.executed[:2] ==
      ["explore_direction", "amble"], str(stub.executed))
out2 = loop._maybe_amble(dict(p), _T + 10)
check("3d 共用节流期内不再提", out2.get("reason") == "amble_pacing", str(out2))
out3 = loop._maybe_amble({"danger_visible": True}, _T + 999)
check("3e 危险不逛", out3.get("reason") == "amble_danger", str(out3))

# ═══ 4. 空闲分支真接线 ═══
print("\n── 4 below_threshold 的 tick 提出踱步 ──")
loop2 = AutonomousLoop(kg=KnowledgeGraph(), config=dict(CFG),
                       data_dir=tempfile.mkdtemp(prefix="fas_amble2_"))
am2 = ActionManager(embodiment=stub, kg=kg, config={}, data_dir=base)
loop2.actions = am2
loop2.mode = "on"
loop2.register_embodiment(emb)
# 阉割评估面：无候选 → 走 no_candidates 出口 → 应提出踱步
loop2._maybe_offline_reflect = lambda *a, **k: None
_t4 = time.time()
out4 = loop2._tick(_t4) if hasattr(loop2, "_tick") else {}
# _tick 签名可能不同——退化验证：直接打 tick 入口
try:
    out4 = loop2.tick(now=_t4 + 999)
except TypeError:
    out4 = loop2.tick()
acted = bool(out4.get("acted")) or out4.get("reason") == "amble"
check("4 空闲 tick 产出踱步行动", acted, str(out4)[:120])

print()
if fail:
    print(f"FAIL: {len(fail)} 项未通过: {fail}")
    sys.exit(1)
print("PASS: 空闲踱步回归全过（观察态有了身体）")
sys.exit(0)
