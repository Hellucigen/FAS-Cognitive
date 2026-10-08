# test_explore_honest_stop.py — 探索"0秒假成功"回归（2026-09-24 真机）
# ============================================================================
# 真机时间轴：11:59:23 自主提出 explore_direction(north) → 技能 start 第一拍
# _stop_conditions 命中 night → cancel(/stop_goto) → res_ok，duration 0.0，
# 没发过 /goto。后果三连：①能力记忆 success_rate 被"没做的事"灌满；
# ②_success_by_key 记成功 → habituation 600s 压低整个探索族（10 分钟决策
# 空窗，12:09 才真正迈第一步）；③坐标冻结，看起来"呆呆的"。
# 钉死四件事（全走技能真实代码，只有桥是假的）：
#   1 出发拍被拦（night/danger）且没看到目标 → 如实 res_fail("preempted:…")，
#     一步没走绝不报成功；
#   2 真的走完一段再入夜 → 照常 res_ok（那是真探索，结束诚实）；
#   3 preempted 前后缀两侧登记为暂态（causal 不判结构罪、奖赏记 blocked
#     不记 goal_failure）——否则一个夜晚的失败史会永久压低探索族（§5 教训）；
#   4 describe 说真话：goal=any 时不再"朝any的方向前进"。
# 离线：假桥，零 LLM，零真实服务器。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_explore_honest_stop.py
# ============================================================================

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

from skills.exploration import ExploreDirection, ExploreArea
from skills.observation import HOSTILE_ENTITIES

fail = []


def check(n, c, d=""):
    print(f"[{'PASS' if c else 'FAIL'}] {n}" + (f" | {d}" if d and not c else ""))
    if not c:
        fail.append(n)


DAY, NIGHT = 6000, 18000


class _Bridge:
    def __init__(self, world_blocks=None):
        self.calls = []
        self.world = world_blocks or {}

    def call(self, path, payload=None, timeout=None):
        self.calls.append((path, payload))
        if path == "/find_blocks":
            pos = self.world.get((payload or {}).get("block"))
            return {"ok": True, "positions": [pos]} if pos else \
                {"ok": False, "reason": "unknown_block"}
        return {"ok": True}

    def goto_coords(self, x, y, z):
        self.calls.append(("goto", (x, y, z)))
        return {"ok": True}

    def stop_goto(self):
        self.calls.append(("stop", None))
        return {"ok": True}

    def get_action_result(self):
        return {"status": "running"}


class _Locs:
    def mark_visited(self, pos):
        pass

    def visit_count(self, pos):
        return 0


class _Ctx:
    def __init__(self, timeofday=DAY, entities=None, world=None):
        self.bridge = _Bridge(world)
        self.session = {}
        self.config = {}
        self.locations = _Locs()
        self.state_dict = {"position": {"x": 0.0, "y": 64.0, "z": 0.0},
                           "timeOfDay": timeofday,
                           "nearbyEntities": entities or [],
                           "nearbyBlocks": []}

    def connected(self):
        return True

    def position(self):
        return self.state_dict["position"]

    def state(self, refresh=False):
        return dict(self.state_dict)


def gotos(ctx):
    return [c for c in ctx.bridge.calls if c[0] == "goto"]


# ═══ 1. 出发拍入夜：失败，不是 0 秒成功 ═══
print("\n── 1 夜间出发被拦 = 如实失败 ──")
ctx = _Ctx(timeofday=NIGHT)
r = ExploreDirection().start(ctx, {"direction": "north"})
check("1 夜间 start → ok=False 且 reason=preempted:night",
      r.get("ok") is False and str(r.get("reason")) == "preempted:night", str(r))
check("1b 没迈出一步（无 /goto）却也没假装成功", not gotos(ctx))

hostile = sorted(HOSTILE_ENTITIES)[0]
ctx2 = _Ctx(timeofday=DAY, entities=[{"name": hostile, "dist": 3.0}])
r2 = ExploreArea().start(ctx2, {"goal": "any"})
check("2 白天但敌对贴脸（danger）→ preempted:danger，同族语义",
      r2.get("ok") is False and str(r2.get("reason")) == "preempted:danger",
      str(r2))

# 出发瞬间入夜、但目标就在眼前：那是真发现，仍算成功（不许把诚实也吞了）
ctx3 = _Ctx(timeofday=NIGHT, world={"iron_ore": {"x": 5, "y": 64, "z": 5}})
r3 = ExploreArea().start(ctx3, {"goal": "iron"})
check("3 未移动但目标已在视野 → 仍如实成功（发现优先于 preempted）",
      r3.get("ok") is True and "铁矿" in str(r3.get("describe")), str(r3))

# ═══ 4. 走完一段再入夜：正常收尾，不是失败 ═══
print("\n── 4 真探索的收尾不变 ──")
ctx4 = _Ctx(timeofday=DAY)
r4 = ExploreDirection().start(ctx4, {"direction": "north"})
check("4 白天 start → pending 且真的发出 /goto",
      r4.get("status") in ("pending", "running") and len(gotos(ctx4)) == 1,
      str(r4))
check("4b describe 说真话：goal=any 不再出现『朝any的方向』",
      "any" not in str(r4.get("describe")), str(r4.get("describe")))
check("4c moved 标记已置位（成功后不再被 preempted 路径遮蔽）",
      ctx4.session.get("moved") is True, str(ctx4.session.get("moved")))
ctx4.state_dict["timeOfDay"] = NIGHT          # 途中天黑了
r4b = ExploreDirection().poll(ctx4)
check("4d 走过后入夜 poll → 如实成功收尾『探索结束（night）』（非 fail）",
      r4b.get("ok") is True and "探索结束" in str(r4b.get("describe")), str(r4b))

# ═══ 5. 暂态登记两侧同步（依赖锁，§5"呆呆站着"教训）═══
print("\n── 5 preempted 是暂态，不是结构罪 ──")
from autonomy import _TRANSIENT_WORLD_REASONS
from reward import classify_self_outcome
check("6 autonomy 暂态集合含 preempted（causal 满分惩罚豁免，前缀匹配）",
      "preempted:night".split(":", 1)[0] in _TRANSIENT_WORLD_REASONS)
check("7 reward：preempted 失败记 blocked 轻负，不记 goal_failure",
      classify_self_outcome("explore", False,
                            {"reason": "preempted:night"}) == "blocked"
      and classify_self_outcome("explore", False,
                                {"reason": "preempted:danger"}) == "blocked")

# ═══ 6. 暂态失败不进 recency/attempts 账（2026-09-25：夜间拦截把清晨探索拖住的实测）═══
print("\n── 8 一步没走的失败不算『刚试过』 ──")
import tempfile
import config as _C
from graph_model import KnowledgeGraph
from autonomy import AutonomousLoop
_base8 = tempfile.mkdtemp(prefix="fas_explore_honest_")
kg8 = KnowledgeGraph()
loop8 = AutonomousLoop(kg=kg8, config=dict(_C.DEFAULT_CONFIG),
                       data_dir=_base8)
_t0 = time.time()
_act8 = {"action_type": "explore_direction", "target": "east",
         "motivation": "curiosity"}
loop8._on_action_settled(_act8, {"success": False,
                                 "reason": "preempted:night"}, False, now=_t0)
check("8 preempted:night 不记 recency（她没真的试过）",
      "explore@world" not in loop8._recent_by_key,
      str(loop8._recent_by_key))
check("8b 不进退避账", "explore@world" not in loop8._attempts,
      str(loop8._attempts))
loop8._on_action_settled(_act8, {"success": False,
                                 "reason": "not_found_in_radius"}, False,
                         now=_t0 + 10)
check("8c 真失败（radius 内没有）照常记账",
      loop8._attempts.get("explore@world", {}).get("count") == 1
      and "explore@world" in loop8._recent_by_key, str(loop8._attempts))

print("\n" + "=" * 60)
if fail:
    print(f"FAIL: {len(fail)} 项未通过: {fail}")
    sys.exit(1)
print("PASS: 探索诚实停止回归全过（假成功已堵死，暂态两侧登记）")
