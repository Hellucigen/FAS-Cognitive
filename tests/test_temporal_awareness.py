# test_temporal_awareness.py — 时间状态重构验收（2026-09-20）
# ============================================================================
# 核心断言方向：时间=持续世界状态，正则=语言线索，睡眠窗口=图常识，
# 行为=交给既有 Drive/竞争/经验通路。对应需求 §十五 Test 1–8。
import sys, os, json, inspect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
import logging
logging.disable(logging.WARNING)

import temporal_awareness as ta
from graph_model import KnowledgeGraph, Node

fail = []
def check(n, c, d=''):
    print(f"[{'PASS' if c else 'FAIL'}] {n}" + (f' | {d}' if d and not c else ''))
    if not c:
        fail.append(n)


class E:
    _running = False
    def __init__(s): s.marked = []
    def mark_active(s, ids): s.marked = list(ids)


class FakeTimeline:
    def __init__(s): s.events = []
    def append(s, ev, **kw): s.events.append(ev); return ev


class FakeDT:
    _real = datetime
    dt = None
    @classmethod
    def now(cls): return cls.dt or cls._real.now()


def at(dt):
    FakeDT.dt = dt
    ta.datetime = FakeDT


def fresh():
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Haru", weight=0.5, label="self", graph_space="self"))
    return kg, E(), FakeTimeline()


# ── Test 1：没有时间词，时间状态依然在线 ──────────────────
kg, eng, tl = fresh()
at(datetime(2026, 9, 20, 3, 17))
st = ta.update_clock_state(kg, eng, now=FakeDT.dt, timeline=tl)
check("T1 时钟写入：凌晨 03:17 → bucket=凌晨/day_phase=夜间",
      st["bucket"] == "凌晨" and st["day_phase"] == "夜间", str(st))
sn = kg.get_node("当前作息感知")
check("T1 状态节点带基础事实（hour/minute/date/source）",
      sn is not None and sn.extra_attrs.get("hour") == 3
      and sn.extra_attrs.get("minute") == 17
      and sn.extra_attrs.get("source") == "real_world",
      str(sn.extra_attrs if sn else None))
check("T1 当前桶/昼夜节点持续激活（地板）",
      kg.get_node("凌晨").activation >= ta.BUCKET_FLOOR
      and kg.get_node("夜间").activation >= ta.PHASE_FLOOR)
check("T1 无关文本 ground：状态已在线，返回当前桶",
      ta.ground("我在玩 Minecraft。", kg, eng) == "凌晨")
check("T1 状态节点无裁判字段（sleep_relation/ideal_window 已删）",
      "sleep_relation" not in sn.extra_attrs
      and "ideal_window" not in sn.extra_attrs)
check("裁判函数 sleep_relation 已删除", not hasattr(ta, "sleep_relation"))

# ── Test 2：时间词 = 额外语言线索（不是一切的前提）────────
kg2, eng2, _ = fresh()
ta.update_clock_state(kg2, eng2, now=FakeDT.dt)
b_sleep = kg2.get_node("睡眠").activation
r = ta.ground("都凌晨三点了，你怎么还没睡", kg2, eng2)
check("T2 时间词命中：返回时段桶", r == "凌晨", str(r))
check("T2 命中后睡眠/作息概念获得额外激活",
      kg2.get_node("睡眠").activation > b_sleep + 0.5,
      f"{b_sleep} → {kg2.get_node('睡眠').activation}")

# ── Test 3：隐式时间（正则不命中，基础状态仍在）──────────
kg3, eng3, _ = fresh()
at(datetime(2026, 9, 20, 5, 30))
ta.update_clock_state(kg3, eng3, now=FakeDT.dt)
r3 = ta.ground("太阳都快出来了，我还在玩", kg3, eng3)
check("T3 未命中正则：时钟状态照常可读（清晨桶）",
      r3 == "清晨" and kg3.get_node("清晨").activation >= ta.BUCKET_FLOOR,
      str(r3))
check("T3 未命中不产生额外作息激活（睡眠节点保持 0）",
      kg3.get_node("睡眠").activation < 0.01)

# ── Test 4：睡眠语汇 → 概念激活，无"提醒睡觉"裁决 ─────────
kg4, eng4, _ = fresh()
at(datetime(2026, 9, 20, 2, 0))
ta.update_clock_state(kg4, eng4, now=FakeDT.dt)
ta.ground("我还没睡。", kg4, eng4)
check("T4 '还没睡' 点亮 睡眠/熬夜/时段 概念",
      kg4.get_node("睡眠").activation > 0
      and kg4.get_node("凌晨").activation > ta.BUCKET_FLOOR)
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "temporal_awareness.py"), encoding="utf-8").read()
for banned in ("提醒", "应该睡", "快去睡", "encourage"):
    check(f"T4 模块无裁决行为文本『{banned}』", banned not in src)

# ── Test 5：白天不产生任何睡眠偏离结论 ────────────────────
kg5, eng5, _ = fresh()
at(datetime(2026, 9, 20, 14, 0))
st5 = ta.update_clock_state(kg5, eng5, now=FakeDT.dt)
ta.ground("我还没睡。", kg5, eng5)   # 即便说没睡，也只有激活，没有结论
sn5 = kg5.get_node("当前作息感知")
check("T5 14:00 → bucket=下午/day_phase=白天",
      st5["bucket"] == "下午" and st5["day_phase"] == "白天", str(st5))
check("T5 状态节点无固定结论字段",
      "sleep_relation" not in json.dumps(sn5.extra_attrs, ensure_ascii=False))
check("T5 理想睡眠窗口仍只是常识节点属性（23/7 在 理想睡眠时段 节点上）",
      kg5.get_node("理想睡眠时段").extra_attrs.get("start_hour") == 23)

# ── Test 6：时段迁移 = 更新，不是重复节点 ─────────────────
kg6, eng6, tl6 = fresh()
at(datetime(2026, 9, 20, 22, 59))
ta.update_clock_state(kg6, eng6, now=FakeDT.dt, timeline=tl6)
n_before = len(kg6.nodes)
at(datetime(2026, 9, 20, 23, 0))
st6 = ta.update_clock_state(kg6, eng6, now=FakeDT.dt, timeline=tl6)
at(datetime(2026, 9, 20, 23, 1))
st6b = ta.update_clock_state(kg6, eng6, now=FakeDT.dt, timeline=tl6)
check("T6 22:59→23:00 触发迁移事件（晚上→深夜）",
      st6["transition"] and st6["transition"]["to"] == "深夜",
      str(st6["transition"]))
check("T6 迁移写统一时间轴（COGNITIVE_EVENT, source=real_world）",
      len(tl6.events) == 1
      and tl6.events[0]["content"].get("source") == "real_world", str(tl6.events))
check("T6 23:01 无重复迁移", st6b["transition"] is None and len(tl6.events) == 1)
check("T6 不产生重复节点（幂等更新）", len(kg6.nodes) == n_before,
      f"{n_before} → {len(kg6.nodes)}")
check("T6 深夜节点获迁移脉冲（激活高于纯地板）",
      kg6.get_node("深夜").activation > ta.BUCKET_FLOOR)

# ── Test 7：跨午夜正确性 ─────────────────────────────────
kg7, eng7, tl7 = fresh()
at(datetime(2026, 9, 20, 23, 59))
ta.update_clock_state(kg7, eng7, now=FakeDT.dt)
at(datetime(2026, 9, 21, 0, 0))
st7 = ta.update_clock_state(kg7, eng7, now=FakeDT.dt, timeline=tl7)
sn7 = kg7.get_node("当前作息感知")
check("T7 23:59→00:00 跨午夜：bucket 深夜→凌晨 且日期更新",
      st7["transition"] and st7["bucket"] == "凌晨"
      and sn7.extra_attrs.get("date") == "2026/09/21", str(sn7.extra_attrs))
check("T7 昼夜层跨午夜不变（凌晨 属于 夜间）",
      st7["day_phase"] == "夜间"
      and any(e.src == "凌晨" and e.dst == "夜间" for e in kg7.edges))

# ── Test 8：现实时间与 Minecraft 不互相污染 ───────────────
kg8 = kg7
check("T8 全部时段桶节点带 source=real_world",
      all((kg8.get_node(b).extra_attrs or {}).get("source") == "real_world"
          for b in ("凌晨", "清晨", "上午", "中午", "下午", "傍晚", "晚上", "深夜")))
check("T8 现实时段桶恰 8 个（MC 侧不建 time_of_day 节点，两时钟各走各路）",
      sum(1 for n in kg8.nodes.values()
          if (n.extra_attrs or {}).get("type") == "time_of_day") == 8)
check("T8 昼夜抽象共享但标源（白天/夜间 = day_phase）",
      (kg8.get_node("夜间").extra_attrs or {}).get("type") == "day_phase")
import minecraft.embodiment
check("T8 具身层不引用 temporal_awareness（MC 时间不污染现实时间）",
      "temporal_awareness" not in inspect.getsource(minecraft.embodiment))

# ── 兼容：ground/link_event_to_bucket 调用面不变 ──────────
kg9, eng9, _ = fresh()
at(datetime(2026, 9, 20, 9, 0))
ta.update_clock_state(kg9, eng9, now=FakeDT.dt)
kg9.add_node(Node(id="事件_X", weight=0.4, graph_space="episodic"))
b9 = ta.ground("今晚有比赛", kg9, eng9)
ta.link_event_to_bucket(kg9, "事件_X", b9)
check("兼容：ground 返回桶 id，link_event_to_bucket 照常建边",
      b9 == "上午" and any(e.src == "事件_X" and e.relation == "发生于时段"
                           and e.dst == "上午" for e in kg9.edges), str(b9))

print()
if fail:
    print("✗", fail)
    sys.exit(1)
print("✓ 时间状态重构测试全过（持续时钟/线索降级/裁判删除/迁移事件/双时钟隔离）")
