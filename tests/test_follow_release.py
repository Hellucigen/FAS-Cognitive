# test_follow_release.py — 自主跟随"追上就了结"回归（2026-09-24 用户：
# "还是老是跟着我啊"）
# ============================================================================
# 结构病：follow 技能用 bot.follow 皮套，一挂就是 follow_timeout_s（600s），
# 追上同伴也不松口——她的时间预算被"贴着他站十分钟"占成大头。
# 修复语义（动力学，不是禁令）：
#   自主发起（_bind_follow 带 release_when_close）→ 贴住 ≤3.5 格连续 20s
#   即如实成功了结；人再走远（>4 格）下一拍自然重新提出。
#   显式"一起走"承诺（不带该标志）→ 陪到超时，兑现承诺不受影响。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_follow_release.py
# ============================================================================

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

from skills.movement import FollowEntity

fail = []


def check(n, c, d=""):
    print(f"[{'PASS' if c else 'FAIL'}] {n}" + (f" | {d}" if d and not c else ""))
    if not c:
        fail.append(n)


class _Bridge:
    def __init__(self):
        self.calls = []

    def call(self, path, payload=None, timeout=None):
        self.calls.append(path)
        return {"ok": True}

    def stopfollow(self):
        self.calls.append("stopfollow")

    def stop_goto(self):
        self.calls.append("stop_goto")


class _Ctx:
    def __init__(self, dist):
        self.bridge = _Bridge()
        self.session = {}
        self.config = {}
        self.dist = dist

    def state(self, refresh=False):
        return {"connected": True,
                "playersNearby": [{"name": "P", "dist": self.dist}],
                "nearbyEntities": [],
                "position": {"x": 0.0, "y": 67.0, "z": 0.0}}

    def position(self):
        return {"x": 0.0, "y": 67.0, "z": 0.0}


sk = FollowEntity()

# ── 自主跟随：贴住够久 → 提前了结（如实成功 + 真的 stopfollow）──
ctx = _Ctx(8.0)
r = sk.start(ctx, {"entity": "P", "release_when_close": True})
check("1 start 记录 release 语义", r.get("status") == "pending"
      and ctx.session.get("release_when_close") is True, str(r))
r = sk.poll(ctx)
check("2 还远（8 格）→ 继续陪走", r.get("status") == "pending", str(r))
ctx.dist = 2.0
r = sk.poll(ctx)
check("3 刚贴住 → 进入宽限计时（close_since 置位），不立即甩手",
      r.get("status") == "pending" and ctx.session.get("close_since"), str(r))
ctx.session["close_since"] = time.time() - 25.0   # 模拟贴了 25s
r = sk.poll(ctx)
check("4 贴住超宽限 → 如实成功了结并解除皮套",
      r.get("ok") is True and r.get("detail", {}).get("released") is True
      and "stopfollow" in ctx.bridge.calls, str(r))

# ── 半路又走远：宽限清零重来（不误"刚贴一下就甩手"）──
ctx2 = _Ctx(2.0)
FollowEntity().start(ctx2, {"entity": "P", "release_when_close": True})
FollowEntity().poll(ctx2)                          # close_since 置位
ctx2.dist = 9.0
FollowEntity().poll(ctx2)
check("5 中途走远 → 宽限清零（了结条件不累积假亲近）",
      ctx2.session.get("close_since") is None, str(ctx2.session))
ctx2.dist = 1.0
FollowEntity().poll(ctx2)
check("5b 重新贴住 → 从头计时",
      ctx2.session.get("close_since") is not None)

# ── 承诺陪走（不带标志）：贴脸也不提前甩手，陪到超时 ──
ctx3 = _Ctx(1.5)
FollowEntity().start(ctx3, {"entity": "P"})
r = FollowEntity().poll(ctx3)
ctx3.session["timeout_at"] = time.time() + 999     # 远离超时分支
r = FollowEntity().poll(ctx3)
check("6 无 release 标志（一起走承诺）→ 持续跟随，不提前了结",
      r.get("status") == "pending"
      and ctx3.session.get("release_when_close") is False, str(r))

# ── 自主层参数接线：_bind_follow 的候选真的带 release_when_close ──
src = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "autonomy.py"), encoding="utf-8").read()
seg = src[src.index("def _bind_follow"):src.index("def _bind_converse")]
check("7 生产 binder：自主跟随候选带 release_when_close=True",
      '"release_when_close": True' in seg)

print("\n" + "=" * 60)
if fail:
    print(f"FAIL: {len(fail)} 项未通过: {fail}")
    sys.exit(1)
print("PASS: 自主跟随追上就了结；承诺陪走不受影响")
