# test_state_monitors.py — 状态检测器测试
# 离线、临时监控文件（不碰 data/state_monitors.json）。
# 覆盖：状态维护、变化识别、重复提交去重、浮点容差、禁用、非法输入、
#       重启恢复、事件结构、轮询接口预留。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_state_monitors.py

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state_monitors import MonitorRegistry

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def fresh(tag=""):
    path = os.path.join(tempfile.gettempdir(),
                        f"fas_test_mon_{os.getpid()}_{tag}.json")
    if os.path.exists(path):
        os.remove(path)
    reg = MonitorRegistry(path=path)
    return reg, path


# ── 1. 创建 + 状态维护 ────────────────────────────────────
reg, path = fresh("a")
events = []
reg.subscribe(lambda e: events.append(e))

r = reg.create("水位", "示例对象水位", "external", "obj_1", "number", unit="单位")
check("检测器创建成功", r.get("ok") is True)
check("初始状态为空（未观测过）", reg.get("水位")["current_value"] is None)

o1 = reg.observe("水位", 37, source="manual")
check("首次观测 → 记录状态并产生事件", o1["changed"] is True and o1["current"] == 37)
check("事件带 previous/current",
      o1["event"]["previous"] is None and o1["event"]["current"] == 37.0)
check("事件带类型与目标",
      o1["event"]["type"] == "state_changed"
      and o1["event"]["target_id"] == "obj_1")
check("订阅者收到事件", len(events) == 1)

# ── 2. 相同状态重复提交 → 不产生事件（去重） ──────────────
o2 = reg.observe("水位", 37)
check("重复提交同值 → changed=False", o2["changed"] is False)
check("重复提交 → 无新事件", len(events) == 1, f"events={len(events)}")
check("重复提交 → 仍刷新观测计数",
      reg.get("水位")["observation_count"] == 2,
      str(reg.get("水位")["observation_count"]))
check("重复提交 → updated_at 有值", bool(reg.get("水位")["updated_at"]))

# ── 3. 状态变化 → previous/current/delta + 历史 ───────────
o3 = reg.observe("水位", 42)
check("状态变化产生新事件", o3["changed"] is True and len(events) == 2)
check("delta 正确", o3["event"]["delta"] == 5.0, str(o3["event"]["delta"]))
mon = reg.get("水位")
check("previous/current 正确", mon["previous_value"] == 37.0 and mon["current_value"] == 42.0)
check("变化历史留痕（含时间戳与事件 id）",
      len(mon["changes"]) == 2 and all("event_id" in c for c in mon["changes"]),
      str(len(mon["changes"])))
check("last_changed_at 更新", bool(mon["last_changed_at"]))

# ── 4. 浮点容差（同值不算变化） ───────────────────────────
reg2, path2 = fresh("b")
reg2.create("t", state_type="number", epsilon=0.01)
reg2.observe("t", 1.000)
o = reg2.observe("t", 1.005)
check("容差内视为未变化", o["changed"] is False, str(o))
o = reg2.observe("t", 1.02)
check("超过容差视为变化", o["changed"] is True, str(o))

# ── 5. 禁用后不再产生检测结果 ─────────────────────────────
reg3, path3 = fresh("c")
ev3 = []
reg3.subscribe(lambda e: ev3.append(e))
reg3.create("m", state_type="number")
reg3.observe("m", 1)
reg3.set_enabled("m", False)
o = reg3.observe("m", 999)
check("禁用后不接受新观测", o.get("accepted") is False, str(o))
check("禁用后不产生事件", len(ev3) == 1, f"events={len(ev3)}")
reg3.set_enabled("m", True)
o = reg3.observe("m", 999)
check("重新启用后恢复观测", o["changed"] is True and len(ev3) == 2)

# ── 6. 非法输入被处理（不污染状态） ───────────────────────
reg4, path4 = fresh("d")
reg4.create("n", state_type="number")
reg4.observe("n", 5)
bad = reg4.observe("n", "不是数字")
check("非法输入 → ok=False 且不更新状态",
      bad.get("ok") is False and reg4.get("n")["current_value"] == 5.0, str(bad))
check("非法输入 → 记录 last_error", bool(reg4.get("n")["last_error"]))
reg4.create("e", state_type="enum", schema={"enum": ["low", "high"]})
ok_e = reg4.observe("e", "low")
bad_e = reg4.observe("e", "middle")
check("enum 越界被拒绝", ok_e["ok"] and bad_e.get("ok") is False, str(bad_e))
reg4.create("b", state_type="bool")
rob = reg4.observe("b", "true")
check("bool 字符串可解释", rob["ok"] and rob["current"] is True)
reg4.create("rng", state_type="number", schema={"min": 0, "max": 10})
bad_r = reg4.observe("rng", 99)
check("数值越界被拒绝", bad_r.get("ok") is False, str(bad_r))
check("不存在的检测器 → 明确报错",
      reg4.observe("不存在", 1).get("ok") is False)

# ── 7. 重启恢复：状态与历史从文件回来 ─────────────────────
reg5, path5 = fresh("e")
reg5.create("持久", "状态持久化", "node", "某节点", "number")
reg5.observe("持久", 10)
reg5.observe("持久", 20)
reg5b = MonitorRegistry(path=path5)
mon5 = reg5b.get("持久")
check("重启后当前状态恢复", mon5["current_value"] == 20.0, str(mon5["current_value"]))
check("重启后上一状态恢复", mon5["previous_value"] == 10.0)
check("重启后变化历史恢复", len(mon5["changes"]) == 2)
check("重启后事件环恢复", len(reg5b.recent_events(10)) >= 1)

# ── 8. 轮询接口预留（无数据源时不采样、零成本） ────────────
reg6, path6 = fresh("f")
reg6.create("p", mode="poll", poll_interval_s=1)
check("poll 型但无数据源 → due() 为空", reg6.due() == [])
check("poll_due 无数据源时安全返回", reg6.poll_due() == [])
reg6.register_poller("p", lambda: 7)
res = reg6.poll_due(now=10**12)
check("登记数据源后被采样", bool(res) and reg6.get("p")["current_value"] == 7.0,
      f"res={res}")

# ── 9. 事件结构与来源 ────────────────────────────────────
ev = reg6.recent_events(1)[0]
check("事件含唯一 id 与时间戳", ev["event_id"].startswith("evt_") and ev["ts"])
check("事件记录来源", ev["source"] == "poll", ev.get("source"))

for p in (path, path2, path3, path4, path5, path6):
    try:
        os.remove(p)
    except OSError:
        pass

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 状态检测器测试全过（临时文件，无真实数据写入）")
