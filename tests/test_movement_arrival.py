# test_movement_arrival.py — B1：移动诚实（§1/§2/§3）
# ============================================================================
# 钉死四件事：
#   1 y 缺省绝不默认 0（床岩寻路 = 必 stuck + 假世界知识）；
#   2 到达判定用 3D 真距离（XZ 贴近但头顶差 10 格 = 没到）；
#   3 回执按名字采信（goto_entity 的 done 不能冒充 goto 的到达；
#     noPath 归一为 no_path 进暂态豁免集合）；
#   4 追动态目标走 goto_entity（GoalFollow），到达/失联/不可达/卡住
#     各说各的真话：unreachable / target_lost / stuck，不假成功。
# 离线：FakeBridge 纯内存读数（合成坐标），不碰真桥、不调 LLM。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_movement_arrival.py
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


from skills.base import SkillContext, LocationMemory
from skills.movement import WalkTo, NavigateToEntity

BASE = os.path.join(tempfile.gettempdir(), "fas_movement")
shutil.rmtree(BASE, ignore_errors=True)
os.makedirs(BASE, exist_ok=True)


class FakeBridge:
    def __init__(self, pos=None):
        self.goto_calls = []
        self.goto_entity_calls = []
        self.follows = []
        self.stopfollows = 0
        self.moves = []
        self.stops = 0
        self.receipt = {}
        self.state_data = {
            "connected": True,
            "position": dict(pos or {"x": 0.0, "y": 64.0, "z": 0.0}),
            "nearbyEntities": [], "playersNearby": [],
        }

    def get_state(self):
        return dict(self.state_data)

    def goto_coords(self, x, y, z):
        self.goto_calls.append((float(x), float(y), float(z)))
        return {"ok": True}

    def goto_entity(self, name, range_=1.5):
        self.goto_entity_calls.append((str(name), float(range_)))
        return {"ok": True}

    def stop_goto(self):
        self.stops += 1
        return {"ok": True}

    def sprint(self, on=True):
        return {"ok": True}

    def move(self, d, s=1.0):
        self.moves.append(d)
        return True

    def get_action_result(self):
        return dict(self.receipt)

    def inventory(self):
        return {"ok": True, "items": []}

    def call(self, path, payload=None, timeout=3):
        return {"ok": True}

    def stop(self):
        return True

    def stopfollow(self):
        self.stopfollows += 1
        return 0

    def follow(self, player):
        self.follows.append(str(player))
        return True


def new_ctx(fb, config=None):
    return SkillContext(bridge=fb, config=config or {},
                        locations=LocationMemory(
                            path=os.path.join(BASE, "loc.json")))


def entity(name="cow", dx=10.0, dy=0.0, dz=0.0):
    return {"name": name,
            "rel": {"dx": dx, "dy": dy, "dz": dz},
            "dist": (dx * dx + dy * dy + dz * dz) ** 0.5}


# ═══ 1. y 缺省 = 当前高度，绝不 0 ═══
print("\n── 1 缺省 y（旧版：床岩寻路）──")
fb = FakeBridge()
ctx = new_ctx(fb)
r = WalkTo().start(ctx, {"x": 10.0, "z": 5.0})
check("WalkTo 无 y → 用当前高度 64 发寻路",
      fb.goto_calls == [(10.0, 64.0, 5.0)], str(fb.goto_calls))
fb2 = FakeBridge()
ctx2 = new_ctx(fb2)
WalkTo().start(ctx2, {"x": 10.0, "y": 70.0, "z": 5.0})
check("显式 y 尊重给定值", fb2.goto_calls == [(10.0, 70.0, 5.0)], str(fb2.goto_calls))
fb3 = FakeBridge()
ctx3 = new_ctx(fb3)
WalkTo().start(ctx3, {"x": 10.0, "y": None, "z": 5.0})
check("y=None 同样落回当前高度（不是 0.0）",
      fb3.goto_calls == [(10.0, 64.0, 5.0)], str(fb3.goto_calls))

# ═══ 2. 到达判定是 3D 的 ═══
print("\n── 2 3D 到达诚实 ──")
fb = FakeBridge()
ctx = new_ctx(fb)
wt = WalkTo()
wt.start(ctx, {"x": 10.0, "y": 64.0, "z": 5.0})
ctx.session["started_at"] = time.time() - 5.0        # 越过 2s 冷启动保护
fb.state_data["position"] = {"x": 10.1, "y": 74.0, "z": 5.0}   # XZ 到了，头顶 10 格
ctx.invalidate()
r = wt.poll(ctx)
check("XZ 贴近但高度差 10 格 → 不算到达（旧版假 done）",
      r["status"] == "pending", str(r))
fb.state_data["position"] = {"x": 10.2, "y": 64.3, "z": 5.1}
ctx.invalidate()
r = wt.poll(ctx)
check("真 3D 距离进圈 → 到达且 detail 带 dist3d",
      r["ok"] and r["status"] == "done" and "dist3d" in r["detail"], str(r["detail"]))

# ═══ 3. 回执按名字采信 + noPath 归一 ═══
print("\n── 3 回执守卫 ──")
fb = FakeBridge()
ctx = new_ctx(fb)
wt = WalkTo()
wt.start(ctx, {"x": 10.0, "z": 5.0})
ctx.session["started_at"] = time.time() - 5.0
fb.receipt = {"name": "goto_entity", "status": "done",
              "startedAt": int(time.time() * 1000)}
fb.state_data["position"] = {"x": 0.0, "y": 64.0, "z": 0.0}
ctx.invalidate()
r = wt.poll(ctx)
check("别的动作的 done 回执不冒充本次到达（name≠goto → 不采信）",
      r["status"] == "pending", str(r))
fb.receipt = {"name": "goto", "status": "failed",
              "detail": {"reason": "noPath"}}
ctx.invalidate()
r = wt.poll(ctx)
check("noPath 归一为 no_path（暂态豁免/因果按同一口径认）",
      r["ok"] is False and r["reason"] == "no_path", str(r))

# ═══ 4. 动态追击：goto_entity + 实测 3D 到达 ═══
print("\n── 4 动态目标追击 ──")
fb = FakeBridge()
fb.state_data["nearbyEntities"] = [entity("cow", 10.0, 1.0, 2.0)]
ctx = new_ctx(fb)
nav = NavigateToEntity()
r = nav.start(ctx, {"entity": "cow", "timeout": 300})
check("出发即发 goto_entity（动态追踪），不再发冻结快照 goto",
      fb.goto_entity_calls == [("cow", 2.5)] and not fb.goto_calls, str(r))
# 目标移动后我们贴近：rel=(2,1,0) → d3=√5≈2.24 ≤ keep=2.5
fb.state_data["nearbyEntities"] = [entity("cow", 2.0, 1.0, 0.0)]
ctx.invalidate()
r = nav.poll(ctx)
check("实测 3D 进入跟随距离 → 真到达（detail 带 dist3d/dy）",
      r["ok"] and r["status"] == "done"
      and r["detail"].get("dist3d") is not None and "dy" in r["detail"], str(r["detail"]))
# 旧 done 回执（startedAt 早于本次出发）不采信
fb.state_data["nearbyEntities"] = [entity("cow", 9.0, 0.0, 9.0)]
fb.receipt = {"name": "goto_entity", "status": "done", "startedAt": 1}
ctx.invalidate()
ctx.session["last_progress"] = time.time()
r = nav.poll(ctx)
check("陈旧 goto_entity done 回执 → 不判到达（继续追）",
      r["status"] == "pending", str(r))
# 新鲜 done 回执（bot 侧 GoalFollow goal_reached）采信，且带 receipt 标记
fb.receipt = {"name": "goto_entity", "status": "done",
              "startedAt": int(time.time() * 1000)}
ctx.invalidate()
r = nav.poll(ctx)
check("新鲜 goal_reached 回执 → 采信为到达",
      r["ok"] and r["status"] == "done"
      and r["detail"].get("receipt") == "goal_reached", str(r["detail"]))

# ═══ 5. noPath → unreachable（带最后目标快照）；实体消失 20s → target_lost ═══
print("\n── 5 如实失败分类 ──")
fb = FakeBridge()
fb.state_data["nearbyEntities"] = [entity("cow", 12.0, 0.0, 0.0)]
ctx = new_ctx(fb)
nav = NavigateToEntity()
nav.start(ctx, {"entity": "cow", "timeout": 300})
ctx.session["started_at"] = time.time()
fb.receipt = {"name": "goto_entity", "status": "failed", "detail": {"reason": "noPath"},
              "startedAt": int(time.time() * 1000)}
ctx.invalidate()
r = nav.poll(ctx)
check("追击 noPath → unreachable（含当时位置与最后目标，供因果记账）",
      r["ok"] is False and r["reason"] == "unreachable"
      and "position" in r["detail"] and "target_last" in r["detail"], str(r["detail"]))
fb = FakeBridge()
fb.state_data["nearbyEntities"] = [entity("pig", 8.0, 0.0, 0.0)]
ctx = new_ctx(fb)
nav = NavigateToEntity()
nav.start(ctx, {"entity": "pig", "timeout": 300})
ctx.session["started_at"] = time.time() - 1
fb.state_data["nearbyEntities"] = []          # 目标走丢
ctx.invalidate()
r = nav.poll(ctx)
check("目标刚消失 → 先给宽限（不立即判死）", r["status"] == "pending", str(r))
ctx.session["gone_since"] = time.time() - 21.0
ctx.session["last_progress"] = time.time()
ctx.invalidate()
r = nav.poll(ctx)
check("消失超 20s → target_lost（带最后目标坐标）",
      r["ok"] is False and r["reason"] == "target_lost"
      and "last_target" in r["detail"], str(r["detail"]))

# ═══ 6. 卡住阶梯：跳 → 侧移 → 重发 goto_entity → 如实 stuck ═══
print("\n── 6 卡住阶梯（重追发动态指令）──")
fb = FakeBridge()
fb.state_data["nearbyEntities"] = [entity("cow", 15.0, 0.0, 0.0)]
ctx = new_ctx(fb)
nav = NavigateToEntity()
nav.start(ctx, {"entity": "cow", "timeout": 300})
ctx.session["started_at"] = time.time() - 1
outs = []
for _ in range(4):
    ctx.session["last_progress"] = time.time() - 4.0   # 伪造"3 秒没位移"
    ctx.invalidate()
    outs.append(nav.poll(ctx))
check("前三级分别 jump / 侧移 / 重发 goto_entity",
      fb.moves[:2] == ["jump", "left"] and len(fb.goto_entity_calls) == 2,
      f"moves={fb.moves} ge={fb.goto_entity_calls}")
check("第四级如实 stuck（带实体/位置/距离，绝不假成功）",
      outs[-1]["ok"] is False and outs[-1]["reason"] == "stuck"
      and outs[-1]["detail"].get("entity") == "cow"
      and "position" in outs[-1]["detail"], str(outs[-1]))

# ═══ B8 真机回归 1：玩家目标是 goto_entity 的盲区 → 必须走 bot.follow ═══
print("\n── 8 玩家追击（真机 22:33『过来』entity_not_visible 的修复）──")
fb = FakeBridge()
fb.state_data["playersNearby"] = [
    {"name": "Hellucigen", "rel": {"dx": 20.0, "dy": 2.0, "dz": 0.0},
     "dist": 20.1}]
ctx = new_ctx(fb)
nav = NavigateToEntity()
r = nav.start(ctx, {"entity": "Hellucigen"})
check("玩家经 playersNearby 可见 → 发起的是 follow 不是 goto_entity",
      r["ok"] and fb.follows == ["Hellucigen"] and not fb.goto_entity_calls,
      f"follows={fb.follows} ge={fb.goto_entity_calls}")
check("追击态标记 nav_player（超时/终止时随停跟随）",
      ctx.session.get("nav_player") is True, str(ctx.session.get("nav_player")))
# 玩家贴近（3D 实测 ≤ keep）→ 到达，goto+follow 都清
fb.state_data["playersNearby"] = [
    {"name": "Hellucigen", "rel": {"dx": 1.0, "dy": 1.0, "dz": 0.0},
     "dist": 1.4}]
ctx.invalidate()
r2 = nav.poll(ctx)
check("3D 实测到达 → 成功且 stop_goto+stopfollow 都执行",
      r2["ok"] and fb.stops >= 1 and fb.stopfollows >= 1
      and r2.get("detail", {}).get("dist3d") is not None,
      f"r2={r2.get('reason') or r2.get('describe')} stops={fb.stops} sf={fb.stopfollows}")

# ═══ B8 真机回归 2：持续型技能不被 120s 看门狗杀成失败 ═══
print("\n── 9 sustained 超时上限（真机 120.41s timeout 误判修复）──")
from action_system import normalize_action, DEFAULT_TIMEOUT, SUSTAINED_TIMEOUT
a1 = normalize_action({"action_type": "follow_entity"},
                      sustained_names={"follow_entity"})
a2 = normalize_action({"action_type": "inspect_entity"},
                      sustained_names={"follow_entity"})
a3 = normalize_action({"action_type": "follow_entity", "timeout_s": 45},
                      sustained_names={"follow_entity"})
check("sustained 技能 → 管理器上限放 660s（技能自判 600s 先落地）",
      a1["timeout_s"] == SUSTAINED_TIMEOUT == 660.0, str(a1["timeout_s"]))
check("普通技能仍 120s 默认", a2["timeout_s"] == DEFAULT_TIMEOUT,
      str(a2["timeout_s"]))
check("显式 timeout_s 优先于一切", a3["timeout_s"] == 45.0, str(a3["timeout_s"]))

shutil.rmtree(BASE, ignore_errors=True)

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
    sys.exit(1)
print("PASS: 移动诚实验收全部通过（y 缺省 / 3D 判定 / 回执守卫 / 动态追击 / 如实失败）")
