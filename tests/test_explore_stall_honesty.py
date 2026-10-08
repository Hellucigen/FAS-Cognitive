# test_explore_stall_honesty.py — "回执 ok ≠ 腿动了"（2026-09-24 真机复发案）
# ============================================================================
# 真机时间轴：bot 的 pathfinder 内部死（raw /move 正常、/goto 回执 ok 但零
# 位移）→ 18:38 自主 explore 烧满 225s 以 success 结算，坐标纹丝不动。
# preempted 修复只挡"出发拍被拦"，挡不住"出发后假活"。本测试钉两道新门：
#   1 walk 相位宽限期（默认 45s）内位移 <1 格 → res_fail("path_stall:…")，
#     不陪它演完整段会话；
#   2 time_limit 收尾且 findings 空、起点↔当下位移 <1 格 → 终审 path_stall
#     （真走过又回到起点、路上有发现的不误伤——4/5 号用例专门放它过去）；
#   3 path_stall 两侧登记为暂态（causal 豁免 + 奖赏记 blocked），与
#     preempted/stuck 同族：腿病重启即愈，不是"探索做不到"。
# 离线：假桥，零 LLM。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_explore_stall_honesty.py
# ============================================================================

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

from skills.exploration import ExploreDirection

fail = []


def check(n, c, d=""):
    print(f"[{'PASS' if c else 'FAIL'}] {n}" + (f" | {d}" if d and not c else ""))
    if not c:
        fail.append(n)


DAY = 6000


class _Bridge:
    def look(self, yaw, pitch=0.0):
        self.looks = getattr(self, 'looks', 0) + 1
        return {"ok": True}

    def __init__(self):
        self.calls = []

    def call(self, path, payload=None, timeout=None):
        self.calls.append(path)
        return {"ok": True}

    def move(self, d, s=1.0):
        self.moves = getattr(self, "moves", []) + [(d, s)]
        return {"ok": True}

    def jump(self):
        self.jumps = getattr(self, "jumps", 0) + 1
        return {"ok": True}

    def goto_coords(self, x, y, z):
        self.calls.append("goto")
        return {"ok": True}            # 回执永远 ok——病在回执之后

    def stop_goto(self):
        self.calls.append("stop")
        return {"ok": True}

    def get_action_result(self):
        return {"status": "running"}   # pathfinder 假活：从不 failed


class _Locs:
    def mark_visited(self, pos):
        pass

    def visit_count(self, pos):
        return 0


class _Ctx:
    def __init__(self):
        self.bridge = _Bridge()
        self.session = {}
        self.config = {}
        self.locations = _Locs()
        self.state_dict = {"position": {"x": 0.0, "y": 64.0, "z": 0.0},
                           "timeOfDay": DAY,
                           "nearbyEntities": [], "nearbyBlocks": []}

    def connected(self):
        return True

    def position(self):
        return dict(self.state_dict["position"])

    def state(self, refresh=False):
        return dict(self.state_dict)


def fresh_walk_ctx():
    ctx = _Ctx()
    r = ExploreDirection().start(ctx, {"direction": "north"})
    assert r.get("status") in ("pending", "running") and "goto" in ctx.bridge.calls, r
    return ctx


# ═══ 1. 假活腿：宽限期外零位移 → 原始步态兜底，连续 6 步仍不动才认账 ═══
ctx = fresh_walk_ctx()
ctx.session["leg_t0"] = time.time() - 46        # 已等过 45s 宽限
r = ExploreDirection().poll(ctx)                # 位置仍 (0,64,0)
check("1 walk 相位 46s 零位移 → 原始步态接管（look+forward 已发）",
      r.get("ok") is not False
      and "原始步态" in str(r.get("describe", ""))
      and getattr(ctx.bridge, "looks", 0) >= 1
      and any(m[0] == "forward" for m in getattr(ctx.bridge, "moves", [])),
      f"{r} looks={getattr(ctx.bridge, 'looks', 0)}")
for _ in range(8):                               # 原始步态也走不动 → 认账
    ctx.session["leg_t0"] = time.time() - 46
    r = ExploreDirection().poll(ctx)
check("1b 原始步态 6 步仍零位移 → 如实 path_stall 失败并叫停",
      r.get("ok") is False and str(r.get("reason")).startswith("path_stall")
      and "stop" in ctx.bridge.calls, str(r))

ctx = fresh_walk_ctx()
ctx.session["leg_t0"] = time.time() - 10        # 宽限期内
r = ExploreDirection().poll(ctx)
check("2 宽限期内不误判（刚出发/卡门框属正常）→ 仍 pending",
      r.get("status") in ("pending", "running"), str(r))

ctx = fresh_walk_ctx()
ctx.session["leg_t0"] = time.time() - 46
ctx.state_dict["position"] = {"x": 6.0, "y": 64.0, "z": 1.0}   # 真走了 6 格
r = ExploreDirection().poll(ctx)
check("3 真在走（位移>1格）→ 不因超时误伤", r.get("status") in ("pending", "running"),
      str(r))

# ═══ 4. 终审门：time_limit 收尾、全程没挪窝、无发现 → 不算成功 ═══
ctx = fresh_walk_ctx()
ctx.session["deadline"] = time.time() - 1       # 到点收尾
r = ExploreDirection().poll(ctx)                # 位置=origin，零位移、findings 空
check("4 time_limit 收尾但零位移无发现 → path_stall:zero_displacement",
      r.get("ok") is False and str(r.get("reason")) == "path_stall:zero_displacement",
      str(r))

ctx = fresh_walk_ctx()
ctx.session["deadline"] = time.time() - 1
ctx.session["findings"] = {"dirt": 2}           # 路上真有见闻
r = ExploreDirection().poll(ctx)
check("5 有发现的收尾仍如实成功（终审不误伤）",
      r.get("ok") is True and "探索结束" in str(r.get("describe")), str(r))

# ═══ 6. 暂态登记两侧同步（preempted 同族教训）═══
from autonomy import _TRANSIENT_WORLD_REASONS
from reward import classify_self_outcome
check("6 autonomy 暂态集合含 path_stall（前缀匹配）",
      "path_stall:legs_not_moving".split(":", 1)[0] in _TRANSIENT_WORLD_REASONS)
check("7 reward：path_stall 失败记 blocked 不记 goal_failure",
      classify_self_outcome("explore", False,
                            {"reason": "path_stall:legs_not_moving"}) == "blocked")

print("\n" + "=" * 60)
if fail:
    print(f"FAIL: {len(fail)} 项未通过: {fail}")
    sys.exit(1)
print("PASS: 假活腿不再能领成功的赏（两道门 + 两侧暂态登记）")
