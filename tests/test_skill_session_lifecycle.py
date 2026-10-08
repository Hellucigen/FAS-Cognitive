# -*- coding: utf-8 -*-
"""离线验收（2026-09-26）修复的最小回归测试。

覆盖两处行为级不变量：
1) 技能会话生命周期：poll 返回 partial 终态后必须清空 ctx.session，
   否则同名 run() 会"续跑"死会话（§16 抓到的目标劫持+秒回 ok 循环）。
2) 采集工具档位复核：资源具体化（组名→真名）后必须按真名重查工具，
   石镐"成功"挖钻矿不再可能（E4 诱饵暴露）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills.base import (Skill, SkillContext, run as skill_run, poll as skill_poll,
                         res_ok, res_pending, res_fail)
import skills.base as _base

FAILED = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + (f" | {info}" if info else ""))
    if not cond:
        FAILED.append(name)


# ══════════════════ 1) partial 终态清理会话 ══════════════════

class _Ghost(Skill):
    """最小假技能：start→pending；第一次 poll→partial；之后 done。"""
    name = "ghost_lifecycle_test"
    category = "test"

    def start(self, ctx, params):
        ctx.session["n"] = 0
        return res_pending(describe="ghost started")

    def poll(self, ctx):
        ctx.session["n"] = ctx.session.get("n", 0) + 1
        return res_ok(status="partial", describe="ghost partial")


class _Bridge:
    def get_state(self):
        return {"connected": True, "position": {"x": 0, "y": 64, "z": 0}}

    def inventory(self):
        return {"ok": True, "items": []}

    def call(self, path, payload=None, timeout=3):
        return {"ok": True}

    def get_action_result(self):
        return {"status": "done", "detail": {}}

    def stop_goto(self):
        return {"ok": True}


_base.REGISTRY[_Ghost.name] = _Ghost()
try:
    ctx = SkillContext(_Bridge())
    # first_poll_delay=0：否则 poll 门槛（0.8 真实秒）挡住本轮测试推进
    r1 = skill_run(_Ghost.name, ctx, {}, 0.0)
    check("G1 start 返回 pending 且会话挂名",
          r1.get("status") == "pending"
          and ctx.session.get("skill") == _Ghost.name, str(r1))
    r2 = skill_poll(ctx)
    check("G2 partial 是终态：poll 后会话清空",
          r2.get("status") == "partial" and not ctx.session.get("skill"),
          f"session={ctx.session}")
    r3 = skill_run(_Ghost.name, ctx, {}, 0.0)
    check("G3 同名 run 走 start 而非死会话 poll",
          r3.get("status") == "pending" and ctx.session.get("n") == 0,
          f"out={r3} session={ctx.session}")
    # 旧世界对照组：failed/done 同样清理（既有不变量，防回归）
    skill_poll(ctx)  # → partial → 清
    ctx.session.clear()
    r4 = skill_run("no_such_skill_xyz", ctx, {})
    check("G4 未知技能如实 failed", r4.get("status") == "failed", str(r4))
finally:
    _base.REGISTRY.pop(_Ghost.name, None)


# ══════════ 2) 组名具体化后的工具档位复核（不跑完整状态机，测判据函数） ══════════

from skills.inventory import choose_best_tool, best_pickaxe  # noqa: E402
from skills.observation import PICKAXE_REQUIREMENT  # noqa: E402


class _InvCtx:
    def __init__(self, items):
        self._items = items

    def inventory(self, refresh=False):
        return self._items


check("T1 钻矿需要铁镐（需求表事实）",
      PICKAXE_REQUIREMENT.get("diamond_ore") == "iron_pickaxe")
_t, _why = choose_best_tool(_InvCtx([{"name": "stone_pickaxe", "count": 1}]),
                            "diamond_ore")
check("T2 石镐挖钻矿 = tool_missing:iron_pickaxe（真名判据）",
      _t is None and _why == "tool_missing:iron_pickaxe", f"{_t} {_why}")
_t3, _why3 = choose_best_tool(_InvCtx([{"name": "iron_pickaxe", "count": 1}]),
                              "diamond_ore")
check("T3 铁镐挖钻矿放行", _t3 == "iron_pickaxe" and not _why3,
      f"{_t3} {_why3}")

# 状态机接线：GatherResource._phase_find 具体化分支必须复核工具
# （组名开局判"徒手可挖"→探出真名后要拦）。用假 ctx 驱动 _phase_find。
import skills.gathering as _g  # noqa: E402


class _Loc:
    def get(self, k):
        return {}

    def remember(self, *a, **kw):
        pass

    def forget(self, *a, **kw):
        pass


class _FindCtx:
    """find 命中 diamond_ore；背包只有石镐 → 必须 res_fail(tool_missing)。"""

    def __init__(self):
        self.session = {}
        self.bridge = _BridgeFind()
        self.locations = _Loc()

    def inventory(self, refresh=False):
        return [{"name": "wooden_pickaxe", "count": 1},
                {"name": "stone_pickaxe", "count": 1}]

    def position(self):
        return {"x": 0, "y": 64, "z": 0}


class _BridgeFind:
    def call(self, path, payload=None, timeout=3):
        if path == "/find_blocks":
            return {"ok": True, "positions": [
                {"x": 6.0, "y": 64.0, "z": 0.0}
            ] if str((payload or {}).get("block")) == "diamond_ore" else []}
        return {"ok": True}

    def stop_goto(self):
        return {"ok": True}

    def goto_coords(self, x, y, z):
        return {"ok": True}

    def equip(self, item):
        return {"ok": True}

    def get_action_result(self):
        return {"status": "done", "detail": {}}


_sk = _g.GatherResource()
_find_ctx = _FindCtx()
_find_ctx.session.update({
    "resource": "diamond_ore", "variants": ["diamond_ore"],
    "qty": 1, "radius": 16, "collected": 0,
    # 关键：模拟组名开局——tool_for 记的是低门槛名（当时判"徒手可挖"），
    # 真名 diamond_ore 具体化后必须触发复核并拦截
    "phase": "find", "tool_for": "diamond",
    "timeout_at": __import__("time").time() + 60,
})
_out = _sk._phase_find(_find_ctx)
check("T4 组名放行+真名复核拦截（石镐挖钻 = tool_missing）",
      _out.get("status") == "failed"
      and _out.get("reason") == "tool_missing:iron_pickaxe",
      f"{_out.get('status')} {_out.get('reason')}")

print()
if FAILED:
    print(f"✗ {len(FAILED)} 项失败: {FAILED}")
    sys.exit(1)
print("✓ 全部通过")
