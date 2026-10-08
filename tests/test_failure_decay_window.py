# test_failure_decay_window.py — 失败降权必须"随时间到期"（2026-09-24 run12）
# ============================================================================
# 事故：夜里用户一句 explore → preempted:night ×2 → _attempts["explore@world"]
# count=2 → failure 分量恒 0.25（旧实现只数次数、等一次成功清零）。天亮后
# 探索分数 0.136 < 阈值 0.24 → 永不提出 → 拿不到清零用的成功 = 自锁呆立
# 15 分钟+（正是"说一下才动一下"的机器版成因）。
# 注释里"有限窗口随时间到期"是设计意图——本测试钉实现：
#   1 分数随"距最近失败"的时长线性回升；
#   2 出 fail_decay_window_s（900s）后与零失败史同分（完全释放，非禁令）；
#   3 窗口内确实压低（压低≠封死：还在爬）；
#   4 成功清账路径不变（settle success → attempts.pop）。
# 离线：假桥；零 LLM；不写真实经历。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_failure_decay_window.py
# ============================================================================

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

import config as C
from autonomy import AutonomousLoop

fail = []


def check(n, c, d=""):
    print(f"[{'PASS' if c else 'FAIL'}] {n}" + (f" | {d}" if d and not c else ""))
    if not c:
        fail.append(n)


BASE = tempfile.mkdtemp(prefix="fas_faildecay_")
loop = AutonomousLoop(kg=None, config=dict(C.DEFAULT_CONFIG), data_dir=BASE)

CAND = {"action_type": "explore_direction", "target": "north",
        "motivation": "curiosity", "reason": ["CuriosityDrive"]}
PERC = {"connected": True, "health": 20, "food": 20, "players": [],
        "entities": [], "blocks": [], "position": {"x": 0, "y": 64, "z": 0}}
KEY = AutonomousLoop._attempt_key("explore_direction", "north")
import time
T0 = time.time()   # 与生产同一时基（settle 内部记 time.time() 的 recency）


def score_at(now, count, last_ts):
    loop._attempts = ({KEY: {"count": count, "last_ts": last_ts,
                             "next_ts": 0.0}} if count else {})
    s, expl = loop._score_action(dict(CAND), dict(PERC), now)
    return s, expl


s_clean, _ = score_at(T0 + 100000, 0, 0.0)          # 无失败史参照
s_fresh, _ = score_at(T0, 2, T0)                    # 刚失败
s_mid, _ = score_at(T0 + 450, 2, T0)                # 失败后 7.5 分钟
s_gone, _ = score_at(T0 + 1200, 2, T0)              # 超过 900s 窗口

check("1 窗口内失败确实压低（fresh < mid < clean）",
      s_fresh < s_mid < s_clean, f"{s_fresh:.3f} {s_mid:.3f} {s_clean:.3f}")
check("2 线性回升：mid ≈ fresh + (clean-fresh)/2",
      abs((s_mid - s_fresh) - (s_clean - s_fresh) * (450 / 900)) < 1e-6,
      f"{s_fresh:.3f} {s_mid:.3f} {s_clean:.3f}")
check("3 出窗口完全释放（与零失败史同分）——压低≠永久否决",
      abs(s_gone - s_clean) < 1e-9, f"{s_gone:.4f} vs {s_clean:.4f}")

# 4 explain 行诚实反映衰减后的 fail 值（raw 0.5 × 半衰 0.5 = 0.25）
_, ex = score_at(T0 + 450, 2, T0)
check("4 explain 里的 fail 与时间衰减一致（0.25）",
      "fail=0.25" in ex, ex)

# 5 成功清账不变
loop._attempts = {KEY: {"count": 3, "last_ts": T0, "next_ts": 0.0}}
loop._on_action_settled({"action_type": "explore_direction",
                         "target": "north"},
                        {"success": True, "cancelled": False}, True)
check("5 一次成功即清零（成功史路径与衰减互补，两机制共存）",
      KEY not in loop._attempts, str(loop._attempts))

shutil.rmtree(BASE, ignore_errors=True)
print("\n" + "=" * 60)
if fail:
    print(f"FAIL: {len(fail)} 项未通过: {fail}")
    sys.exit(1)
print("PASS: 失败降权随时间到期，自锁已消除")
