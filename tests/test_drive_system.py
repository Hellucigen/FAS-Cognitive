# test_drive_system.py — Drive 系统完整版测试（四驱真实评估）
# Phase 3 补齐（2026-09-20）：Social/Learning/Consistency 从 planned 转正——
# 信号全部来自既有系统的读数（provider 注入），评估器不造平行状态。
# 离线：真 KnowledgeGraph + 假 provider。运行:
#   E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_drive_system.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from graph_model import KnowledgeGraph
from drive_engine import DriveEvaluator, DRIVE_NODE_IDS

FAILURES = []


def check(name, cond, detail=""):
    st = "PASS" if cond else "FAIL"
    print(f"[{st}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


CFG = {"drive_system": {
    "social": {"status": "active", "gap_norm_min": 30.0},
    "learning": {"status": "active"},
    "consistency": {"status": "active"},
    "curiosity": {},
}}

kg = KnowledgeGraph()
ev = DriveEvaluator(kg, dict(CFG))
ev.bootstrap_drives()
for nid in ("CuriosityDrive", "SocialDrive", "LearningDrive", "ConsistencyDrive"):
    check(f"节点存在且 active: {nid}",
          nid in kg.nodes and kg.nodes[nid].extra_attrs.get("status") == "active")

# 无 provider：评估仍产出（值为 0），不抛错
res = ev.evaluate(force=True)
ids = {d["drive"] for d in res["drives"]}
check("四驱都参与评估（信号缺位=0，不缺席）",
      {"curiosity", "social", "learning", "consistency"} <= ids, str(ids))

# ── Social：互动间隔 + 社交需求 + 同伴在场 ──
ev.set_signal_provider("social_idle_minutes", lambda: 45.0)   # 超归一阈值 → 1.0
ev.set_signal_provider("social_need", lambda: 0.8)
ev.set_signal_provider("oxytocin", lambda: 0.5)
ev.set_signal_provider("players_present", lambda: 1.0)
res = ev.evaluate(force=True)
soc = next(d for d in res["drives"] if d["drive"] == "social")
check("SocialDrive 由间隔/需求/在场抬升", soc["activation"] > 2.0, str(soc))
check("Social 因子可解释", any("分钟没互动" in f for f in soc["factors"]), str(soc["factors"]))
# 激活写入节点（参与扩散的前提）
check("SocialDrive 节点激活同步", kg.nodes["SocialDrive"].activation == soc["activation"],
      str(kg.nodes["SocialDrive"].activation))

# ── Learning：失败 + 阻碍 + 新颖对象 ──
ev.set_signal_provider("recent_goal_failures", lambda: 2.0)
ev.set_signal_provider("causal_blockers", lambda: 1.0)      # 如 tool_missing
ev.set_signal_provider("novel_objects", lambda: 3.0)
ev.set_signal_provider("competence_need", lambda: 0.6)
res = ev.evaluate(force=True)
lrn = next(d for d in res["drives"] if d["drive"] == "learning")
check("LearningDrive 反映能力缺口", lrn["activation"] > 1.5, str(lrn))
check("Learning 因子含阻碍", any("阻碍" in f for f in lrn["factors"]), str(lrn["factors"]))

# ── Consistency：pending 反思候选 + 负性积累 + 心情 ──
ev.set_signal_provider("pending_reflection_candidates", lambda: 3.0)
ev.set_signal_provider("recent_negative_reward_ratio", lambda: 0.5)
ev.set_signal_provider("mood_valence", lambda: -0.4)
res = ev.evaluate(force=True)
con = next(d for d in res["drives"] if d["drive"] == "consistency")
check("ConsistencyDrive 由未消化经历抬升", con["activation"] > 2.2, str(con))
check("dominant 在四者中产生", res["dominant"]["drive"] in (
    "curiosity", "social", "learning", "consistency"), str(res["dominant"]))

# provider 异常时按缺省（不炸循环）
ev.set_signal_provider("social_idle_minutes", lambda: 1 / 0)
res2 = ev.evaluate(force=True)
check("provider 异常降级为 0（不抛出）", isinstance(res2.get("drives"), list))

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ Drive 系统四驱全部转正（信号 provider 架构，Phase 3 补账完成）")
