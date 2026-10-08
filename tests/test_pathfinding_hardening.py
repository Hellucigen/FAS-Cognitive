# test_pathfinding_hardening.py — 寻路韧性（2026-09-25 真机教训离线回归）
# 离线：桩桥（不连 Minecraft、不调 LLM）。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_pathfinding_hardening.py
#
# 背景（2026-09-25 真机日志）：26.1 的 pathfinder 静默无路，bot.js 原始步态
# 兜底又撞地形如实认输，上层 explore 却每条腿都朝同一方向重发 goto，
# 一路耗满 max_time（240s）才 zero_displacement 收场——4 分钟站着不动。
# 回归点：
#   A. 连续失败腿快败（3 条腿 + 全程零位移 → path_stall:repeated_leg_failure）
#   B. 失败方向记忆：下一腿换方向，不在同一个门框上撞四次
#   C. 真到达一个路点 → 失败计数清零（腿是好的，不该被历史污染）
#   D. 空间正常时（status=done 到达）语义不变：observe→pick 照旧推进

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


from skills.base import SkillContext, LocationMemory
from skills.exploration import ExploreArea


def _isolated_locations():
    import tempfile
    return LocationMemory(path=os.path.join(
        tempfile.gettempdir(), f"fas_test_locs_{os.getpid()}.json"))


class StubBridge:
    """动作面桩：goto 永远"成功发出"，但行走结果可以按脚本喂。"""

    def __init__(self, result_sequence=None, position=None):
        self.seq = list(result_sequence or [])
        self.i = 0
        self.position = position or {"x": 100.0, "y": 64.0, "z": 100.0}
        self.goto_calls = []
        self.stops = 0

    def get_state(self):
        return {"connected": True, "position": dict(self.position),
                "nearbyEntities": [], "nearbyBlocks": []}

    def inventory(self):
        return {"ok": True, "items": []}

    def call(self, path, params=None, timeout=None):
        return {"ok": True}

    def get_action_result(self):
        if not self.seq:
            return {"status": "failed"}
        r = self.seq[min(self.i, len(self.seq) - 1)]
        self.i += 1
        return r

    def stop_goto(self):
        self.stops += 1
        return {"ok": True}

    def goto_coords(self, x, y, z):
        self.goto_calls.append({"x": x, "y": y, "z": z})
        return {"ok": True}

    def look(self, yaw, pitch):
        return {"ok": True}

    def move(self, act, secs):
        return {"ok": True}


def _cardinal(wp, pos):
    dx = wp["x"] - pos["x"]
    dz = wp["z"] - pos["z"]
    return ("east" if dx > 0 else "west") if abs(dx) >= abs(dz) \
        else ("south" if dz > 0 else "north")


def run_failed_legs(n_legs):
    """出发 + n 条失败腿，返回 (ctx, 结果列表)。位置钉死在原点=零位移。"""
    br = StubBridge(result_sequence=[{"status": "failed"}] * n_legs)
    ctx = SkillContext(bridge=br, config={"explore_max_time_s": 300},
                      locations=_isolated_locations())
    res = [ExploreArea().start(ctx, {"goal": "any"})]
    for _ in range(n_legs + 3):
        ctx.invalidate()             # 桩桥无时钟推进，手动过期状态缓存
        res.append(ExploreArea().poll(ctx))
    return ctx, br, res


# ═══ A. 快败 ═══
ctx, br, res = run_failed_legs(3)
fail = [r for r in res if r.get("status") == "failed"]
check("连续 3 条腿失败且全程零位移 → 快败（不再耗满 max_time）",
      any(r.get("reason") == "path_stall:repeated_leg_failure" for r in fail),
      str([(r.get('status'), r.get('reason')) for r in res]))
check("快败前先 stop_goto（不留下一个幽灵寻路目标）", br.stops >= 1, str(br.stops))
check("快败早于 deadline（远小于 max_time 的拍数内结算）",
      len(res) < 10, str(len(res)))

# ═══ B. 失败方向记忆 ═══
ctx, br, res = run_failed_legs(2)
d0 = _cardinal(br.goto_calls[0], br.position)
ctx.session.get("failed_dir")
check("第一条腿失败后记住失败方向",
      ctx.session.get("failed_dir") == d0,
      f"goto0={br.goto_calls[0]} dir={d0} 记忆={ctx.session.get('failed_dir')}")
if len(br.goto_calls) >= 2:
    d1 = _cardinal(br.goto_calls[1], br.position)
    check("下一腿换方向（不在同一门框上撞四次）", d1 != d0,
          f"dir0={d0} dir1={d1}")

# ═══ C. 真到达 → 计数清零 ═══
br = StubBridge(result_sequence=[{"status": "done"}])
ctx = SkillContext(bridge=br, config={"explore_max_time_s": 300},
                      locations=_isolated_locations())
ExploreArea().start(ctx, {"goal": "any"})
wp = dict(ctx.session.get("waypoint") or {})
br.position = {"x": wp.get("x", 0), "y": wp.get("y", 64), "z": wp.get("z", 0)}
ctx.invalidate()                 # 位置变了：手动过期 0.8s 状态缓存
ctx.session["stall_legs"] = 2
ctx.session["failed_dir"] = "west"
ExploreArea().poll(ctx)              # 到达那一拍：walk 切 observe（"移动中"）
ctx.invalidate()
r = ExploreArea().poll(ctx)          # 下一拍：observe 快照
check("到达路点进入 observe（正常语义不变）",
      r.get("status") == "pending" and "观察" in str(r.get("describe", "")),
      str(r))
r2 = ExploreArea().poll(ctx)         # 下一拍回 pick，且计数已清零
check("真到达清零失败计数与方向记忆（历史不污染下一腿）",
      ctx.session.get("stall_legs") == 0 and "failed_dir" not in ctx.session,
      f"stall={ctx.session.get('stall_legs')} failed_dir={ctx.session.get('failed_dir')}")

# ═══ D. 有位移时不快败 ═══
class MovingBridge(StubBridge):
    """每次 poll 都"走远一点"：位移>2 格时连续失败也不许快败。"""

    def __init__(self):
        super().__init__(result_sequence=[{"status": "failed"}] * 8)
        self.n = 0

    def get_state(self):
        self.n += 1
        return {"connected": True,
                "position": {"x": 100.0 + self.n * 5.0, "y": 64.0, "z": 100.0},
                "nearbyEntities": [], "nearbyBlocks": []}


br = MovingBridge()
ctx = SkillContext(bridge=br, config={"explore_max_time_s": 300},
                      locations=_isolated_locations())
res = [ExploreArea().start(ctx, {"goal": "any"})]
for _ in range(6):
    ctx.invalidate()             # 缓存会让桩桥的位置推进读不到
    res.append(ExploreArea().poll(ctx))
check("有真实位移时连续失败不触发快败（快败只管原地打转）",
      not any(r.get("reason") == "path_stall:repeated_leg_failure"
              for r in res),
      str([(r.get('status'), r.get('reason')) for r in res]))

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未过：{FAILURES}")
    sys.exit(1)
print("PASS: 寻路韧性回归全部通过（离线桩桥）")
