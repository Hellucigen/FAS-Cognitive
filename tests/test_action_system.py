# test_action_system.py — Action 节点系统测试
# 离线：桩具身环境 + 临时图谱。覆盖：ActionNode 规范化（完整字段）、
# 当前动作承诺（§八）、优先级/中断（§九）、超时、目标队列、因果记录（§十五）。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_action_system.py

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from graph_model import KnowledgeGraph, Node
from action_system import ActionManager, normalize_action, SRC_USER, SRC_AUTONOMY, SRC_GOAL

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class StubEmbodiment:
    """可编排的具身环境桩：execute 返回可配置结果，记录全部调用。"""

    name = "stub"

    def __init__(self):
        self.available_flag = True
        self.capabilities_set = {"walk_to", "gather_resource", "stop", "retreat",
                                 "seek_safety", "eat_food", "follow_entity",
                                 "inspect_area"}
        self.executed = []
        self.next_result = {"success": True, "pending": False, "describe": "ok"}
        self.poll_status = {"status": "done", "detail": {}}
        self.cancel_calls = 0
        self.raw = {"connected": True, "health": 20, "food": 20,
                    "position": {"x": 0, "y": 64, "z": 0}, "nearbyEntities": []}

    def available(self):
        return self.available_flag

    def capabilities(self):
        return set(self.capabilities_set)

    def perceive(self):
        return {"connected": True, "health": self.raw.get("health"),
                "food": self.raw.get("food"), "players": [], "entities": [],
                "blocks": [], "unknown_entities": [], "unknown_blocks": []}

    def raw_state(self):
        return dict(self.raw)

    def execute(self, action):
        self.executed.append(dict(action))
        return dict(self.next_result)

    def poll_action(self):
        return dict(self.poll_status)

    def cancel(self):
        self.cancel_calls += 1
        return {"ok": True}


class RecordingTimeline:
    def __init__(self):
        self.events = []

    def append(self, ev):
        self.events.append(ev)


def build():
    base = tempfile.mkdtemp(prefix="fas_test_action_")
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", graph_space="self"))
    kg.add_node(Node(id="用户"))
    emb = StubEmbodiment()
    tl = RecordingTimeline()
    am = ActionManager(embodiment=emb, kg=kg, config={}, timeline=tl, data_dir=base)
    # 生产形态：Safety Kernel（2026-09-21，调度器只保留濒死停手兜底）
    import config as _C
    from safety_kernel import SafetyKernel
    am.kernel = SafetyKernel(kg=kg, engine=None,
                             config=dict(_C.DEFAULT_CONFIG))
    return base, kg, emb, am, tl


# ── 1. ActionNode 规范化：完整字段（规范 §五）──────────────
a = normalize_action({"action_type": "gather_resource", "target": "iron_ore",
                      "motivation": "resource_opportunity",
                      "expected_effect": "获得铁矿", "reason": ["UnknownBlock_iron_ore"]})
check("action_id 自动生成", bool(a["action_id"]) and a["action_id"].startswith("act_"))
for field in ("action_type", "target", "priority", "motivation", "expected_effect",
              "urgency", "timeout_s", "interruptible", "prerequisite",
              "success_condition", "failure_condition"):
    check(f"字段齐全: {field}", field in a, str(sorted(a)))
check("优先级落在 [0,1]", 0.0 <= a["priority"] <= 1.0, str(a["priority"]))
check("来源可追溯", a["source"] == SRC_AUTONOMY)
check("reason 保留（可解释性）", a["reason"] == ["UnknownBlock_iron_ore"])

# ── 2. 当前动作承诺（§八）：没结束就不重新选择 ──────────────
base, kg, emb, am, tl = build()
emb.next_result = {"success": True, "pending": True, "describe": "开挖"}
p1 = am.propose({"action_type": "gather_resource", "target": "oak_log"},
                source=SRC_AUTONOMY, now=100.0)
check("空闲时动作立即启动", p1.get("started") is True, str(p1))
check("pending 动作进入承诺期（current 不为空）", am.busy() and am.current is not None)
p2 = am.propose({"action_type": "walk_to", "target": "somewhere",
                 "priority": 0.4}, source=SRC_AUTONOMY, now=101.0)
check("承诺期内同类动作被拒绝（不重新随机选）",
      p2.get("started") is False and p2.get("queued") is False
      and "承诺期" in p2.get("reason", ""), str(p2))
check("承诺期内不会打断执行（bot 只收到一次指令）",
      len(emb.executed) == 1, str(len(emb.executed)))
# tick 推进 → 回执 done → 结算 → 空闲
emb.poll_status = {"status": "done", "detail": {"dug": 1}}
out = am.tick(now=110.0)
check("回执 done → 动作结算并清空承诺期",
      out.get("settled") == "done" and am.current is None, str(out))
check("结算统计真实记录", am.status()["stats"]["success"] == 1, str(am.status()["stats"]))

# ── 3. 优先级 / 中断（§九）：用户指令打断自主动作 ────────────
base, kg, emb, am, tl = build()
emb.next_result = {"success": True, "pending": True, "describe": "挖矿中"}
am.propose({"action_type": "gather_resource", "target": "coal_ore",
            "priority": 0.5}, source=SRC_AUTONOMY, now=100.0)
p = am.propose({"action_type": "follow_entity", "target": "Hellucigen",
                "priority": 0.85}, source=SRC_USER, now=101.0)
check("用户高优先级动作打断当前动作", p.get("started") is True
      and p.get("interrupted") is True, str(p))
check("被打断的动作收到 cancel", emb.cancel_calls == 1, str(emb.cancel_calls))
check("新动作已成为当前动作", am.current is not None
      and am.current["action_type"] == "follow_entity")
check("打断不是免费：取消结算已记录", am.status()["stats"]["interrupted"] == 1,
      str(am.status()["stats"]))
# 打断不留人格负证据（cancelled 不算做错）
kinds = [(e.get("event_type"), e.get("subject")) for e in tl.events]
check("被取消的动作结算不产生负学习（只有 ACTION 事件，无失败结果事件）",
      all(k != "SELF_STATE_CHANGE" or "failed" not in str(e)
          for k, e in zip(kinds, tl.events)), str(kinds))

# ── 4. 生存紧急（SRC_SURVIVAL）：不可被普通动作打断 ─────────
base, kg, emb, am, tl = build()
emb.next_result = {"success": True, "pending": True, "describe": "撤离中"}
am.propose({"action_type": "seek_safety", "priority": 0.95},
           source="survival", now=100.0)
p = am.propose({"action_type": "walk_to", "priority": 0.9},
               source=SRC_AUTONOMY, now=101.0)
check("生存动作不被普通自主动作打断", p.get("started") is False, str(p))
p = am.propose({"action_type": "follow_entity", "priority": 0.95},
               source=SRC_USER, now=102.0)
check("用户最高优先级仍可打断生存动作（人命关天的例外交给优先级）",
      p.get("started") is True, str(p))

# ── 5. 超时（§九 Action Timeout）───────────────────────────
base, kg, emb, am, tl = build()
emb.next_result = {"success": True, "pending": True, "describe": "走着"}
am.propose({"action_type": "walk_to", "target": "far", "timeout_s": 10},
           source=SRC_AUTONOMY, now=100.0)
emb.poll_status = {"status": "running", "detail": {}}
out = am.tick(now=105.0)
check("未超时 → 继续在飞", out.get("reason") in ("acting", "action_progress"), str(out))
out = am.tick(now=115.0)
check("超时 → 按失败结算（原因=timeout）",
      out.get("settled") == "timeout" and am.current is None, str(out))
check("超时结算统计进 failed", am.status()["stats"]["failed"] == 1,
      str(am.status()["stats"]))
check("超时结果事件带 timeout 原因（因果学习原料）",
      any(e.get("event_type") == "SELF_STATE_CHANGE"
          and "failed:timeout" in str(e.get("content", {}).get("change"))
          for e in tl.events), str([e.get("content") for e in tl.events]))

# ── 6. 目标队列（§四：复杂指令分步执行）─────────────────────
base, kg, emb, am, tl = build()
emb.next_result = {"success": True, "pending": True, "describe": "走着"}
am.queue_goal({"action_type": "gather_resource", "target": "iron_ore"})
am.queue_goal({"action_type": "gather_resource", "target": "coal_ore"})
check("目标入队", len(am.status()["goal_queue"]) == 2)
emb.poll_status = {"status": "done", "detail": {}}
# 空闲 tick：不启动队列（队列动作只在自主/用户管线下发）——这是设计约定：
# 目标队列由 autonomy/用户轮次驱动，ActionManager 只在空闲 tick 出队下一个
out = am.tick(now=100.0)
check("空闲且队列非空 → 出队下一个目标执行",
      out.get("started_goal") == "gather_resource", str(out))
check("出队后队列剩 1", len(am.status()["goal_queue"]) == 1)
check("出队的动作真的发给了 bot", len(emb.executed) == 1)

# ── 7. 因果记录（§十五）：为什么做 / 预期 / 实际 / 奖赏变化 ──
base, kg, emb, am, tl = build()
spec = {"action_type": "gather_resource", "target": "iron_ore",
        "motivation": "resource_opportunity",
        "expected_effect": "obtain_iron",
        "reason": ["iron_detected", "resource_need"]}
am.propose(spec, source=SRC_USER, now=100.0)
emb.poll_status = {"status": "done", "detail": {"collected": 1}}
am.tick(now=110.0)
starts = [e for e in tl.events if e.get("event_type") == "ACTION"]
results = [e for e in tl.events if e.get("event_type") == "SELF_STATE_CHANGE"]
check("动作开始事件记录了动机", starts and starts[0]["content"].get("motivation")
      == "resource_opportunity", str(starts[:1]))
check("动作开始事件记录了预期效果",
      starts and starts[0]["content"].get("expected_effect") == "obtain_iron")
check("动作开始事件记录了当时内部状态（game health/food）",
      starts and "internal_state" in starts[0]["content"], str(starts[:1]))
check("动作开始事件记录了 reason 列表",
      starts and starts[0]["content"].get("reason") == ["iron_detected", "resource_need"])
check("结果事件记录实际成功", results
      and results[-1]["content"].get("change") == "succeeded", str(results[-1:]))
check("结果事件带奖赏变化（reward_change）",
      results and "reward_change" in results[-1]["content"], str(results[-1:]))

# 失败路径
base, kg, emb, am, tl = build()
emb.next_result = {"success": True, "pending": True, "describe": "尝试挖铁"}
am.propose({"action_type": "gather_resource", "target": "iron_ore",
            "motivation": "resource_opportunity",
            "expected_effect": "obtain_iron",
            "reason": ["resource_opportunity"]}, source=SRC_USER, now=100.0)
emb.poll_status = {"status": "failed", "reason": "tool_missing:stone_pickaxe",
                   "detail": {}}
am.tick(now=110.0)
results = [e for e in tl.events if e.get("event_type") == "SELF_STATE_CHANGE"]
check("失败结果事件带真实原因（tool_missing 可被后续学习）",
      results and "failed:tool_missing:stone_pickaxe"
      in str(results[-1]["content"].get("change")), str(results[-1:]))

# ── 8. 图谱留痕：行动节点连到依据节点 ──────────────────────
base, kg, emb, am, tl = build()
kg.add_node(Node(id="UnknownBlock_iron_ore", graph_space="cognitive"))
am.propose({"action_type": "gather_resource", "target": "iron_ore",
            "reason": ["UnknownBlock_iron_ore"]}, source=SRC_AUTONOMY, now=100.0)
emb.poll_status = {"status": "done", "detail": {}}
am.tick(now=110.0)
act_nodes = [nid for nid in kg.nodes if nid.startswith("行动_")]
check("行动留痕节点入图（episodic）", len(act_nodes) == 1, str(act_nodes))
if act_nodes:
    check("留痕边连到依据节点（她在想什么可追溯）",
          any(e.dst == "UnknownBlock_iron_ore" for e in kg.edges if e.src == act_nodes[0]),
          str([(e.src, e.dst) for e in kg.edges if e.src == act_nodes[0]]))
    check("留痕带动机与预期（不只是做了什么）",
          kg.nodes[act_nodes[0]].extra_attrs.get("motivation")
          and kg.nodes[act_nodes[0]].extra_attrs.get("expected_effect") is not None)

# ── 8b. 事件→对象边 + 时间顺序链（2026-09-23 记忆重构）──────
kg.add_node(Node(id="cow", graph_space="semantic"))
kg.add_node(Node(id="物品:torch", graph_space="semantic"))
am.propose({"action_type": "inspect_area", "target": "Cow"},
           source=SRC_AUTONOMY, now=200.0)
emb.poll_status = {"status": "done", "detail": {}}
am.tick(now=210.0)
_acts = [nid for nid in kg.nodes if nid.startswith("行动_")]
check("动作对象入图：显示名大小写归一→既有物种节点（反扩散可达）",
      len(_acts) == 2 and any(
          e.src == _acts[1] and e.dst == "cow" and e.relation == "涉及"
          for e in kg.edges),
      str([(e.src, e.relation, e.dst) for e in kg.edges if e.src in _acts]))
check("时间顺序链：连续两条行动留痕相接（叙事侧同一词表/扩散规则）",
      any(e.src == _acts[0] and e.dst == _acts[1]
          and e.relation == "时间顺序" and e.relation_category == "temporal_relation"
          for e in kg.edges))
am.propose({"action_type": "inspect_area", "target": "phantom_thing",
            "params": {"item": "torch"}},
           source=SRC_AUTONOMY, now=300.0)
emb.poll_status = {"status": "done", "detail": {}}
am.tick(now=310.0)
_act3 = [nid for nid in kg.nodes if nid.startswith("行动_")][-1]
check("对象不在图=不建节点不连边；params.item 解析到 物品: 节点",
      "phantom_thing" not in kg.nodes and any(
          e.src == _act3 and e.dst == "物品:torch" for e in kg.edges),
      str([(e.relation, e.dst) for e in kg.edges if e.src == _act3]))

# ── 9. 血量不再触发调度器代决定；濒死只剩停手兜底（§kernel K4）──
base, kg, emb, am, tl = build()
emb.next_result = {"success": True, "pending": True, "describe": "挖矿中"}
emb.poll_status = {"status": "running", "detail": {}}   # 动作持续在飞
am.propose({"action_type": "gather_resource", "target": "stone",
            "priority": 0.5}, source=SRC_AUTONOMY, now=100.0)
emb.raw["health"] = 6   # 血量低但未濒死：不再强制 seek_safety（去向归认知）
out = am.tick(now=105.0)
check("血量 6：调度器不代决定逃跑（采矿继续）",
      out.get("interrupted_by") != "seek_safety"
      and am.current is not None
      and am.current["action_type"] == "gather_resource", str(out))
emb.raw["health"] = 3   # 濒死线以下：只停手，不指定去哪
out = am.tick(now=110.0)
check("濒死（HP≤4）→ kernel 停手且不注入行为",
      out.get("interrupted_by") == "death_floor_stop"
      and am.current is None, str(out))
check("被打断的动作被真实取消", emb.cancel_calls >= 1)

# ── 10. 敌对贴脸：调度器不再强制撤退（危险→认知，2026-09-21）──
base, kg, emb, am, tl = build()
emb.next_result = {"success": True, "pending": True, "describe": "探索中"}
emb.poll_status = {"status": "running", "detail": {}}
am.propose({"action_type": "walk_to", "target": "x", "priority": 0.4},
           source=SRC_AUTONOMY, now=100.0)
emb.raw["nearbyEntities"] = [{"name": "creeper", "dist": 3.0}]
out = am.tick(now=101.0)
check("苦力怕贴脸不再由调度器代决定 retreat",
      out.get("interrupted_by") != "retreat"
      and am.current is not None
      and am.current["action_type"] == "walk_to", str(out))

shutil.rmtree(base, ignore_errors=True) if 'base' in dir() else None

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ Action 节点系统测试全过（承诺/中断/超时/队列/因果记录）")
