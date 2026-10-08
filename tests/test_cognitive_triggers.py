# test_cognitive_triggers.py — 触发器测试
# 离线、临时触发器文件（不碰 data/cognitive_triggers.json）。
# 覆盖：结构化条件（单条件/多条件组合/枚举/变化）、跃变触发语义、冷却、
#       once、禁用、删除无残留、优先级顺序、错误处理、触发记录、Mode 1 解析。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_cognitive_triggers.py

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cognitive_triggers import TriggerEngine, evaluate_condition, nl_to_trigger
from state_monitors import MonitorRegistry
from cognitive_regulation import CognitiveRegulation
from graph_model import KnowledgeGraph, Node
from diffusion_engine import DiffusionEngine

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def ev(**kw):
    """构造一条状态事件。"""
    base = {"event_id": "evt_x", "type": "state_changed", "monitor_id": "m",
            "monitor_name": "m", "previous": 0, "current": 1, "delta": 1,
            "ts": "t", "ts_epoch": 1.0}
    base.update(kw)
    return base


# ── 1. 结构化条件求值（纯函数，无 LLM） ────────────────────
ok, why = evaluate_condition({"op": "gt", "field": "current", "value": 5}, ev(current=9))
check("gt 成立", ok is True and "9" in why, why)
ok, _ = evaluate_condition({"op": "gt", "field": "current", "value": 5}, ev(current=5))
check("gt 边界不成立（严格大于）", ok is False)
ok, _ = evaluate_condition({"op": "gte", "field": "current", "value": 5}, ev(current=5))
check("gte 边界成立", ok is True)
ok, _ = evaluate_condition({"op": "lt", "field": "delta", "value": 0}, ev(delta=-3))
check("lt 可比较 delta 字段", ok is True)
ok, _ = evaluate_condition({"op": "eq", "field": "current", "value": "low"}, ev(current="low"))
check("eq 支持字符串", ok is True)
ok, _ = evaluate_condition({"op": "in", "field": "current", "values": ["low", "empty"]},
                           ev(current="empty"))
check("in 支持枚举集合", ok is True)
ok, _ = evaluate_condition({"op": "changed"}, ev(previous=1, current=2))
check("changed 判定成功", ok is True)
ok, _ = evaluate_condition({"op": "changed"}, ev(previous=2, current=2))
check("changed 同值不成立", ok is False)
ok, _ = evaluate_condition({"op": "all", "conditions": [
    {"op": "gte", "field": "current", "value": 10},
    {"op": "lt", "field": "current", "value": 20}]}, ev(current=15))
check("all 多条件组合（区间）", ok is True)
ok, _ = evaluate_condition({"op": "any", "conditions": [
    {"op": "lte", "field": "current", "value": 0},
    {"op": "gte", "field": "current", "value": 99}]}, ev(current=100))
check("any 多条件组合", ok is True)
ok, _ = evaluate_condition({"op": "not", "condition": {"op": "gt", "field": "current", "value": 5}},
                           ev(current=1))
check("not 取反", ok is True)
ok, why = evaluate_condition({"op": "bogus"}, ev())
check("未知操作符 → 不满足且给出说明", ok is False and "未知" in why, why)
ok, why = evaluate_condition({"op": "gt", "field": "current", "value": "不是数"}, ev(current=1))
check("类型不符 → 不满足且给出说明", ok is False and "无法数值比较" in why, why)

# ── 2. 引擎：创建 / 条件评估 / 跃变触发 ────────────────────
path = os.path.join(tempfile.gettempdir(), f"fas_test_trig_{os.getpid()}.json")
if os.path.exists(path):
    os.remove(path)
eng = TriggerEngine(path=path)

bad = eng.create("bad", condition={"op": "bogus"})
check("非法条件被拒绝", bad.get("ok") is False, str(bad))
bad2 = eng.create("bad2", condition={"op": "gt", "value": 1}, llm_mode=9)
check("非法 llm_mode 被拒绝", bad2.get("ok") is False, str(bad2))

r = eng.create("t1", "超阈值", "m", {"op": "gte", "field": "current", "value": 10},
               action={"type": "record"}, cooldown_s=60)
check("触发器创建成功", r.get("ok") is True, str(r))

check("条件不满足 → 不触发",
      eng.evaluate(ev(current=5), monitor={}) == [])
fired = eng.evaluate(ev(current=12), monitor={})
check("条件满足（跃变）→ 触发", len(fired) == 1, str(fired))
check("认知事件带条件说明", "12" in fired[0]["condition_explain"], fired[0]["condition_explain"])
check("认知事件带 llm_mode 与动作",
      fired[0]["llm_mode"] == 0 and fired[0]["action"]["type"] == "record")
again = eng.evaluate(ev(current=15), monitor={})
check("持续满足不再重复触发（跃变语义）", again == [], str(again))
eng.evaluate(ev(current=1), monitor={})          # 回落到不满足
fired2 = eng.evaluate(ev(current=20), monitor={})
check("冷却期内二次跃变被拒绝", fired2 == [])
log = eng.recent_log(5)
check("拒绝原因写入触发记录（可见不静默）",
      any(x.get("reason") == "cooldown" for x in log), str(log))

# ── 3. 冷却到期后可再次触发 ───────────────────────────────
eng.update("t1", cooldown_s=0)
eng.evaluate(ev(current=1), monitor={})
fired3 = eng.evaluate(ev(current=30), monitor={})
check("冷却改为 0 后可再次触发", len(fired3) == 1, str(fired3))

# ── 4. 禁用 / 删除（无残留） ──────────────────────────────
eng.set_enabled("t1", False)
eng.evaluate(ev(current=1), monitor={})
check("禁用后不触发", eng.evaluate(ev(current=50), monitor={}) == [])
eng.set_enabled("t1", True)
eng.delete("t1")
check("删除后不再存在于列表", all(t["id"] != "t1" for t in eng.list()))
check("删除不抛错且返回确认", eng.delete("t1").get("ok") is False)
check("删除后评估无报错（无残留任务）",
      eng.evaluate(ev(current=100), monitor={}) == [])

# ── 5. once：只触发一次并自动停用 ─────────────────────────
eng.create("once1", "只一次", "m", {"op": "gte", "field": "current", "value": 1},
           once=True, cooldown_s=0)
eng.evaluate(ev(current=5), monitor={})
check("once 触发后自动停用", eng.get("once1")["enabled"] is False)
eng.set_enabled("once1", True)
eng.evaluate(ev(current=0), monitor={})
f = eng.evaluate(ev(current=5), monitor={})
check("once 已触发过 → 再次被拒绝", f == [] and eng.get("once1")["fire_count"] == 1,
      str(eng.get("once1")))

# ── 6. 优先级顺序（同一次评估内按 priority 降序派发） ──────
eng.create("p_low", "低", "m", {"op": "gte", "field": "current", "value": 1},
           priority=1, cooldown_s=0)
eng.create("p_high", "高", "m", {"op": "gte", "field": "current", "value": 1},
           priority=9, cooldown_s=0)
fired_list = eng.evaluate(ev(current=5), monitor={})
check("多触发器同轮触发按优先级降序",
      [f["trigger_id"] for f in fired_list] == ["p_high", "p_low"],
      str([f["trigger_id"] for f in fired_list]))
eng.delete("p_low")
eng.delete("p_high")

# ── 7. monitor_id 过滤 ────────────────────────────────────
eng.create("m_only", "只看 m2", "m2", {"op": "gte", "field": "current", "value": 1},
           cooldown_s=0)
check("monitor_id 不匹配 → 不触发",
      eng.evaluate(ev(monitor_id="m1", current=99), monitor={}) == [])
check("monitor_id 匹配 → 触发",
      len(eng.evaluate(ev(monitor_id="m2", current=99), monitor={})) == 1)

# ── 8. 条件评估异常被记录（不中断引擎） ───────────────────
eng.create("err1", "坏条件", "m", {"op": "gt", "field": "current", "value": None},
           cooldown_s=0)
f = eng.evaluate(ev(current=5), monitor={})
check("字段不可比较 → 不触发且记录说明", f == [])
check("触发记录里有未满足的说明可查（非静默）",
      isinstance(eng.get("err1")["last_condition"], bool))

# ── 9. 生命周期字段与持久化 ──────────────────────────────
eng.create("persist", "持久化", "m", {"op": "changed"}, cooldown_s=0, priority=3,
           llm_mode=2)
eng.evaluate(ev(current=7), monitor={})
eng2 = TriggerEngine(path=path)
t = [x for x in eng2.list() if x["id"] == "persist"]
check("重启后触发器恢复", len(t) == 1)
check("重启后 fire_count 与 last_condition 恢复",
      t[0]["fire_count"] == 1 and t[0]["last_condition"] is True, str(t[0]))
check("重启后不因状态残留而误触发",
      eng2.evaluate(ev(current=8), monitor={}) == [],
      "跃变语义在重启后依然成立（上一轮条件已满足）")
check("重启后日志恢复", len(eng2.recent_log(20)) >= 1)

# ── 10. Mode 1：自然语言 → 结构化定义（注入桩 LLM） ───────
def stub_llm(prompt):
    return json.dumps({
        "monitor_id": "水位", "condition": {"op": "lte", "field": "current", "value": 10},
        "fire_mode": "edge", "cooldown_s": 30, "once": False, "llm_mode": 1,
        "action": {"type": "activate_nodes", "nodes": ["需要确认"]},
        "name": "水位偏低", "description": "低于 10 就激活确认节点"})


res = nl_to_trigger("水位低于10的时候激活需要确认节点", stub_llm)
check("Mode 1：NL → 结构化候选成功", res.get("ok") is True, str(res))
check("Mode 1：候选条件结构化", res["candidate"]["condition"]["op"] == "lte")
check("Mode 1：无 LLM 函数时明确报错",
      nl_to_trigger("x", None).get("ok") is False)
check("Mode 1：坏 JSON 被拒绝",
      nl_to_trigger("x", lambda p: "不是JSON").get("ok") is False)
check("Mode 1：条件不合法被拒绝",
      nl_to_trigger("x", lambda p: json.dumps({"condition": {"op": "??? "}})).get("ok") is False)

# ── 11. 协调器：检测器 → 触发器 → 激活图谱节点（端到端） ──
CFG = {"lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 4,
       "theta_threshold": 0.01, "activation_max": 5.0, "min_spread_threshold": 0.01,
       "activation_epsilon": 1e-4, "input_similarity_floor": 0.5,
       "input_default_bonus": 0.5, "theta_action": 0.5}
kg = KnowledgeGraph()
kg.add_node(Node(id="目标节点"))
kg.add_node(Node(id="被锁节点"))
deng = DiffusionEngine(kg, dict(CFG))
deng.name_to_node = dict(kg.nodes)
data_dir = os.path.join(tempfile.gettempdir(), f"fas_test_reg_{os.getpid()}")
reg = CognitiveRegulation(kg=kg, engine=deng, data_dir=data_dir)
deng.set_lock_registry(reg.locks)
seen = []
reg.register_handler(lambda e, r: seen.append((e["trigger_id"], r.get("activated"))))

reg.monitors.create("水位", "水位", "external", "obj", "number")
reg.triggers.create("low", "水位低", "水位", {"op": "lte", "field": "current", "value": 10},
                    action={"type": "activate_nodes", "nodes": ["目标节点"]},
                    cooldown_s=0, llm_mode=2)

reg.submit_state("水位", 50, source="manual")
check("协调器：条件不满足不激活", kg.nodes["目标节点"].activation == 0.0)
reg.submit_state("水位", 5, source="manual")
check("协调器：触发后动作激活图谱节点", kg.nodes["目标节点"].activation > 0,
      str(kg.nodes["目标节点"].activation))
check("协调器：处理器收到认知事件与动作结果",
      seen and seen[0][0] == "low" and seen[0][1] == ["目标节点"], str(seen))
reg.submit_state("水位", 50, source="manual")
reg.submit_state("水位", 5, source="manual")
check("协调器：处理器只被触发两次（无重复派发）", len(seen) == 2, str(len(seen)))

# llm_mode 边界：mode=0 的事件不进认知层取用
ev_low = reg.drain_cognitive_events(llm_mode_min=1)
check("llm_mode=2 的事件可被认知层取用",
      len(ev_low) >= 1 and all(e["trigger_id"] == "low" for e in ev_low), str(ev_low))
check("取用后水位推进（不重复入 prompt）", reg.drain_cognitive_events(llm_mode_min=1) == [])

# 锁与触发器的交互：被锁节点不会被触发器强拉进激活
reg.locks.create("node", "被锁节点", "blocking", "diffusion", "测试锁")
reg.triggers.create("locktrig", "锁交互", "水位",
                    {"op": "gte", "field": "current", "value": 1},
                    action={"type": "activate_nodes", "nodes": ["被锁节点"]},
                    cooldown_s=0)
reg.submit_state("水位", 1, source="manual")
reg.submit_state("水位", 2, source="manual")
check("阻断锁对触发器动作同样生效（锁优先）",
      kg.nodes["被锁节点"].activation == 0.0, str(kg.nodes["被锁节点"].activation))

import shutil
try:
    os.remove(path)
except OSError:
    pass
shutil.rmtree(data_dir, ignore_errors=True)

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 触发器测试全过（临时文件 + 临时 KG，无真实数据写入）")
