# test_current_goal.py — 自我图谱"当前目标"指针 + ActionManager 任务挂接
# 离线、临时图、假具身（不连游戏、不调 LLM）。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_current_goal.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from graph_model import KnowledgeGraph
from self_graph import (bootstrap_self, current_goal, set_current_goal,
                        clear_current_goal, SELF_ID, REL_CURRENT_GOAL)
from action_system import ActionManager

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def goal_edges(kg):
    with kg._lock:
        return [e for e in kg.edges
                if e.src == SELF_ID and e.relation == REL_CURRENT_GOAL]


class Emb:
    def __init__(self):
        self.n = 0

    def execute(self, action):
        self.n += 1
        return {"success": True, "pending": True, "action": action["action_type"]}

    def poll_action(self):
        return {"status": "done", "detail": {"ok": True}}


kg = KnowledgeGraph()
bootstrap_self(kg)
kg.save = lambda *a, **k: None

# ── 1. 纯指针语义 ──────────────────────────────────────
gid = set_current_goal(kg, "挖三组铁矿去熔炼")
check("当前目标挂上后 Self 有唯一 当前目标 边",
      len(goal_edges(kg)) == 1 and goal_edges(kg)[0].dst == gid, str(goal_edges(kg)))
check("指针可被认知读取（desc/source/state）",
      current_goal(kg).get("desc") == "挖三组铁矿去熔炼"
      and current_goal(kg).get("state") == "进行中", str(current_goal(kg)))
set_current_goal(kg, "给村民建房子")
check("换任务是 in-place 挪指针（不累积旧边、不造新词表）",
      len(goal_edges(kg)) == 1 and current_goal(kg)["desc"] == "给村民建房子",
      str(goal_edges(kg)))

# ── 2. 任务未完成不得摘除；排空自动摘除并写终态 ────────
mgr = ActionManager(embodiment=Emb(), kg=kg)
mgr.set_goal_context("给村民建房子")
mgr.propose({"action_type": "place_block", "params": {"item": "planks"}},
            source="user")
mgr.queue_goal({"action_type": "place_block", "params": {"item": "planks"}})
mgr.tick()
check("还有在飞/队列步骤时指针保持（任务≠一步）", len(goal_edges(kg)) == 1,
      str(len(goal_edges(kg))))
mgr.tick()   # 步骤1 settle
mgr.tick()   # 步骤2 start
mgr.tick()   # 步骤2 settle
mgr.tick()   # idle tick → 排空摘除
check("队列排空且无在飞 → 指针摘除、节点记 已完成",
      not goal_edges(kg) and current_goal(kg) == {}
      and kg.get_node("目标: 给村民建房子").extra_attrs["goal_state"] == "已完成")

# ── 3. 放弃路径（clear_goals）写 已结束 ─────────────────
mgr.set_goal_context("造一座桥")
mgr.queue_goal({"action_type": "place_block", "params": {"item": "planks"}})
mgr.clear_goals()
check("取消后续步骤 = 任务放弃（已结束≠已完成）",
      current_goal(kg) == {}
      and kg.get_node("目标: 造一座桥").extra_attrs["goal_state"] == "已结束")

# ── 4. 目标节点留在长期目标层（一次完成不销毁记忆） ────
check("完成不删节点：目标仍是 Self-[目标]-> 的长期结构",
      kg.get_node("目标: 给村民建房子") is not None
      and kg.get_edge(SELF_ID, "目标: 给村民建房子", "目标") is not None)

# ── 5. 无图降级安全（测试/无 kg 环境） ─────────────────
m2 = ActionManager(embodiment=Emb(), kg=None)
m2.set_goal_context("任何任务")
check("无 kg 时静默跳过（不抛错）", m2._goal_ctx is None)
check("空描述不挂指针", set_current_goal(kg, "   ") == "")

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 当前目标指针测试全过（挂/挪/保持/完成/放弃/降级）")
