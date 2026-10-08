# test_mc_approach_race.py — 2026-09-22"进游戏还是呆呆站在那里"事故回归
# 三颗雷各锁一节：
#   雷1  MonitorRegistry.list() 不接受过滤参数 → app._wire_bridge_liveness
#        每次启动 TypeError，桥活性接线整体被跳过（§18.2 #7/#11 白做）。
#   雷2  navigate_to_entity 在连接瞬间实体未同步 → 0.0s entity_not_visible
#        死刑，无宽限 → "去找用户"第一跳必死。
#   雷3  causal 先验把暂态"世界没准备好"当永久阻碍：一次 entity_not_visible
#        后 approach 一系被 -0.30 满额惩罚永久压低（变相硬禁令，正是
#        2026-09-21 §13 要拆掉的东西）。
# 离线：假桥 + 临时目录；不碰真实数据、不连 Minecraft、不调 LLM、
# 不写任何用户生活事件（目标名用显式假想值）。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_mc_approach_race.py

import os
import shutil
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ── 雷1：检测器注册表 list 过滤参数（与锁注册表同名同义）──
import state_monitors

tmp_dir = tempfile.mkdtemp(prefix="fas_mon_list_")
try:
    reg = state_monitors.MonitorRegistry(path=os.path.join(tmp_dir, "mon.json"))
    a = reg.create(name="MC 桥活性", target_type="external",
                   target_id="minecraft_bridge", state_type="bool", mode="poll")
    b = reg.create(name="假想血量读数", target_type="self_state",
                   target_id="hp_probe", state_type="number", mode="event")
    check("create 正常返回 monitor", bool((a.get("monitor") or {}).get("id")), str(a)[:120])
    ext = reg.list(target_type="external")
    check("list(target_type=…) 不再 TypeError 且只回外部项",
          [m["name"] for m in ext] == ["MC 桥活性"], str(ext))
    check("list(target_id=…) 过滤",
          [m["name"] for m in reg.list(target_id="hp_probe")] == ["假想血量读数"])
    check("list() 无参兼容旧调用", len(reg.list()) == 2)
    check("两过滤参数叠加不冲突",
          reg.list(target_type="external", target_id="hp_probe") == [])
    # app._wire_bridge_liveness 的调用形状（曾每次启动炸掉）
    mon = next((m for m in reg.list(target_type="external")
                if m.get("target_id") == "minecraft_bridge"), None)
    check("接线函数同款查询式可执行", mon is not None)
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

# ── 雷2：navigate_to_entity 可见宽限（连接竞态 pending 化）──
import minecraft.bridge as bridge
from skills.base import SkillContext
from skills.movement import NavigateToEntity

goto_calls = []
goto_entity_calls = []
follow_calls = []


def fake_follow(name):
    # 玩家目标走 bot.follow（GoalFollow 认玩家；goto_entity 只认非玩家实体）
    # 真实签名返回 bool（HTTP 200 → True）
    follow_calls.append(("follow", str(name)))
    return True


def fake_goto(x, y, z):
    goto_calls.append(("goto", round(float(x), 1), round(float(y), 1),
                       round(float(z), 1)))
    return {"ok": True}


def fake_goto_entity(name, range_=1.5):
    # B1 起 navigate_to_entity 走 bot 侧 GoalFollow 动态追踪，不再追冻结快照点
    goto_entity_calls.append(("goto_entity", str(name), round(float(range_), 2)))
    return {"ok": True}


state = {"connected": True, "position": {"x": 0.0, "y": 64.0, "z": 0.0},
         "nearbyEntities": [], "playersNearby": []}
bridge.get_state = lambda: dict(state)
bridge.goto_coords = fake_goto
bridge.goto_entity = fake_goto_entity
bridge.follow = fake_follow
bridge.stopfollow = lambda: 0
bridge.stop_goto = lambda: {"ok": True}
bridge.sprint = lambda on=True: {"ok": True}
bridge.get_action_result = lambda: {"status": "pending"}

nav = NavigateToEntity()

# 刚连接：目标玩家还没同步 → 不再判死，转入"先找找"
ctx = SkillContext(bridge=bridge, config={"navigate_visible_grace_s": 20})
r = nav.start(ctx, {"entity": "玩家甲", "keep_distance": 2.0})
check("不可见不判死：进入搜索宽限（pending，未发起任何寻路/追击）",
      r["status"] == "pending" and "target" not in ctx.session
      and not goto_calls and not goto_entity_calls,
      str(r))
r2 = nav.poll(ctx)
check("宽限内仍不可见：继续找（不假成功不空转到超时）",
      r2["status"] == "pending" and "target" not in ctx.session, str(r2))
# 实体同步出现 → 立即出发（B1：动态追击，不再按快照缩放冻结点）
state["playersNearby"] = [{"name": "玩家甲",
                           "rel": {"dx": 10.0, "dy": 0.0, "dz": 0.0},
                           "dist": 10.0}]
ctx.invalidate()
r3 = nav.poll(ctx)
# 玩家目标：bot.follow（GoalFollow 认玩家）是发起追击的原语；keep_distance
# 不进 follow 参数（桥签名只有名字），而是存 session 作 3D 到达判定阈值。
check("同步出现 → 发起动态追击（pending，follow 恰一发，无冻结点寻路）",
      r3["status"] == "pending" and ctx.session.get("nav_dynamic")
      and follow_calls[:1] == [("follow", "玩家甲")]
      and not goto_entity_calls and not goto_calls,
      str(r3) + str(follow_calls) + str(goto_calls))
check("追击记录 keep_distance 为到达阈值（3D 实测用）",
      ctx.session.get("keep") == 2.0 and ctx.session.get("nav_player"),
      str({k: ctx.session.get(k) for k in ("keep", "nav_player")}))

# 宽限到期仍不可见 → 如实失败（细节带 searched_s，符合"失败必须真实返回"）
ctx2 = SkillContext(bridge=bridge, config={"navigate_visible_grace_s": 20})
state["playersNearby"] = []
ctx2.invalidate()
nav.start(ctx2, {"entity": "玩家乙"})
ctx2.session["search_until"] = time.time() - 1.0
r4 = nav.poll(ctx2)
check("宽限到期仍不可见 → 如实 entity_not_visible",
      r4["ok"] is False and r4["reason"] == "entity_not_visible"
      and "searched_s" in (r4.get("detail") or {}), str(r4))

# grace=0 → 保持旧行为（配置可关）
ctx3 = SkillContext(bridge=bridge, config={"navigate_visible_grace_s": 0})
state["playersNearby"] = []
ctx3.invalidate()
r5 = nav.start(ctx3, {"entity": "玩家丙"})
check("宽限设为 0 时立即如实失败（兼容旧行为）",
      r5["ok"] is False and r5["reason"] == "entity_not_visible", str(r5))

# 搜索期间桥失联 → 如实 not_connected
ctx4 = SkillContext(bridge=bridge, config={"navigate_visible_grace_s": 20})
nav.start(ctx4, {"entity": "玩家丁"})
state["connected"] = False
ctx4.invalidate()
r6 = nav.poll(ctx4)
check("搜索中失联 → not_connected 如实返回",
      r6["ok"] is False and r6["reason"] == "not_connected", str(r6))
state["connected"] = True

# ── 雷3：causal 先验只惩罚结构性阻碍，不惩罚暂态世界状态 ──
from graph_model import KnowledgeGraph, Node
from autonomy import AutonomousLoop, _TRANSIENT_WORLD_REASONS
from cognitive_regulation import CognitiveRegulation

base = os.path.join(tempfile.gettempdir(), "fas_test_causal")
shutil.rmtree(base, ignore_errors=True)
os.makedirs(base, exist_ok=True)
kg = KnowledgeGraph()
kg.add_node(Node(id="用户", activation=0.0))


class _P:
    def mood_event(self, kind):
        pass

    def current_mood(self):
        return {}


loop = AutonomousLoop(kg=kg, config={"autonomy": {"mode_default": "off"}},
                      regulation=CognitiveRegulation(kg=kg, data_dir=base),
                      persona=_P(), data_dir=base,
                      note_outcome_fn=lambda neg: None)

CAND = {"action_type": "navigate_to_entity", "target": "玩家甲",
        "motivation": "user_invitation", "reason": []}
PERCEPT = {"connected": True, "health": 20.0, "food": 20.0,
           "position": {"x": 0.0, "y": 64.0, "z": 0.0},
           "players": [], "entities": [], "blocks": []}
NOW = time.time()


def set_blockers(bl):
    loop._actions_ref = types.SimpleNamespace(
        causal=types.SimpleNamespace(
            action_prior=lambda i, t: {"success_rate": None,
                                       "blockers": bl, "obs": 1}))


def score():
    s, e = loop._score_action(dict(CAND), dict(PERCEPT), NOW)
    return s, e


s0, _ = set_blockers([]) or score()
s1, e1 = set_blockers(["entity_not_visible"]) or score()
check("暂态阻碍不再满额惩罚（事故路径：去找用户没被永久判死）",
      abs(s0 - s1) < 1e-9, f"clean={s0:.3f} transient={s1:.3f} {e1}")
s2, e2 = set_blockers(["not_connected", "entity_not_visible"]) or score()
check("多个暂态同样不惩罚", abs(s0 - s2) < 1e-9, f"{s2:.3f} {e2}")
s3, e3 = set_blockers(["tool_missing:iron_pickaxe"]) or score()
w_c = loop.cfg["weights"]["causal"]
check("结构性阻碍保留满额惩罚（教训还得记）",
      abs((s0 - w_c) - s3) < 1e-9, f"{s0:.3f}→{s3:.3f} w={w_c}")
s4, e4 = set_blockers(["tool_missing:iron_pickaxe", "entity_not_visible"]) or score()
check("混合阻碍：结构性主导（暂态不稀释也不叠加）",
      abs(s3 - s4) < 1e-9, f"{s3:.3f} vs {s4:.3f}")
check("暂态清单覆盖 AUTONOMY.md §5 列举的世界未就绪原因",
      {"entity_not_visible", "not_connected", "no_target_coords"}
      <= _TRANSIENT_WORLD_REASONS, str(sorted(_TRANSIENT_WORLD_REASONS)))

shutil.rmtree(base, ignore_errors=True)

print(f"\n结果: {len(FAILURES) == 0 and '全部通过 ✔' or str(len(FAILURES)) + ' 项失败 → ' + str(FAILURES)}")
sys.exit(1 if FAILURES else 0)
