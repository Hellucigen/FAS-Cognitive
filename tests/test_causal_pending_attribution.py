# test_causal_pending_attribution.py — B0：因果归因改 pending-window 的验收
# ============================================================================
# 病灶（2026-09-22 审计）：record_action 在动作开始**瞬间**扫 [now, now+8s]，
# 而结果事件秒级之后才入轴 → 生产端永远空手而归；测试当年全绿是因为合成
# 数据把结果事件排在 record_action 之前（绕开了时序）。本测试按**生产时序**
# 回放：动作先入账、结果迟到、哨兵事件关窗，全程走时间轴观察者。
# 另验：窗口数据表（言语类 90s）、执行器名→作用域别名、条件签名 contexts、
# 暂态理由集合（依赖锁：接归因必须同时接暂态，否则 stuck 成永久抑制）。
# 全部输入为合成事件，不含任何真实用户生活经历。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_causal_pending_attribution.py
# ============================================================================

import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


import config as C
from graph_model import KnowledgeGraph, Node
from experience import (ExperienceTimeline, CausalLearner, make_event,
                        context_signature,
                        EVENT_ACTION, EVENT_OBSERVATION, EVENT_SELF_STATE)

BASE = os.path.join(tempfile.gettempdir(), "fas_causal_pending")


def build():
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE, exist_ok=True)
    path = os.path.join(BASE, "experience_timeline.json")
    tl = ExperienceTimeline(path=path, config=dict(C.DEFAULT_CONFIG))
    kg = KnowledgeGraph()
    lr = CausalLearner(tl, config=dict(C.DEFAULT_CONFIG), kg=kg)
    return tl, lr, kg


def ev(type_, subject, actor="self", change=None, ts=None, **content):
    e = make_event(type_, actor=actor, subject=subject,
                   content={"change": change, **content}, source="test")
    if ts is not None:
        e["ts"] = ts
        e["ts_str"] = time.strftime("%H:%M:%S", time.localtime(ts))
    return e


# ═══ 1. 生产时序：动作 → （秒后）结果迟到 → 关窗才记账（旧实现此链恒空）═══
def test_late_result_attributed():
    print("\n── 1 迟到结果被归因（pending-window 核心回归）──")
    tl, lr, kg = build()
    t = time.time()
    act = ev(EVENT_ACTION, "navigate_to_entity", ts=t, target="cow",
             context="day|alone")
    tl.append(act)
    cands_at_birth = lr.record_action(act)   # 结果还没发生 → 如实为空
    check("登记时窗口未关：不当场记账、不当场记反例",
          cands_at_birth == [] and not lr._aggregations,
          str(lr._aggregations))
    check("归因窗已挂起", len(lr._pending) == 1)
    # 3 秒后真实回执入轴（动作自身状态结果 = 规则0 天然相关）
    tl.append(ev(EVENT_SELF_STATE, "navigate_to_entity",
                 change="failed:stuck", ts=t + 3.0))
    check("结果入窗后 pending 仍开着（未提前结账）", len(lr._pending) == 1)
    # 哨兵：越过 8s 窗口的下一个事件驱动关窗
    tl.append(ev(EVENT_OBSERVATION, "position", actor="minecraft",
                 change="changed", ts=t + 9.5))
    agg = lr._aggregations.get("navigate_to_entity(cow)") or {}
    outcomes = agg.get("outcomes") or {}
    check("关窗后聚合出现（obs=1）", agg.get("obs") == 1, str(agg))
    check("failed:stuck 结果被归因（rule 0：动作自身状态事件）",
          outcomes.get("self:self:navigate_to_entity:failed:stuck", {})
          .get("support") == 1, str(outcomes))
    ctxs = (outcomes.get("self:self:navigate_to_entity:failed:stuck") or {}) \
        .get("contexts") or {}
    check("条件签名入 contexts（§十一：失败留条件不留断言）",
          ctxs.get("day|alone", {}).get("support") == 1, str(ctxs))
    # 无关事件不得搭车（远处鸡出现 ≠ 导航的结果）
    tl.append(ev(EVENT_ACTION, "dig", ts=t + 20.0, target="dirt"))
    lr.record_action(tl.recent(5, event_type=EVENT_ACTION)[0])
    tl.append(ev(EVENT_OBSERVATION, "entity:chicken", actor="minecraft",
                 change="appeared", ts=t + 20.5))
    tl.append(ev(EVENT_OBSERVATION, "position", actor="minecraft",
                 change="changed", ts=t + 30.0))
    dirt = lr._aggregations.get("dig(dirt)") or {}
    check("无关观察仍不归因（§七主体门不变）",
          dirt.get("obs") == 1 and not dirt.get("outcomes"), str(dirt))
    shutil.rmtree(BASE, ignore_errors=True)


# ═══ 2. 窗口数据表：言语类动作 90s；执行器名别名域生效 ═══
def test_window_table_and_aliases():
    print("\n── 2 communicate=90s 窗口 + 动作名→作用域别名 ──")
    tl, lr, kg = build()
    t = time.time()
    say = ev(EVENT_ACTION, "communicate", ts=t, target="用户",
             text="那边的洞穴有什么？", context="day")
    tl.append(say)
    lr.record_action(say)
    tl.append(ev(EVENT_OBSERVATION, "utterance", actor="用户",
                 change="observed", text="是蝙蝠！", ts=t + 50.0))
    check("50 秒后的用户回应仍在窗内（communicate=90s，pending 未关先收编）",
          len(lr._pending) == 1
          and any("utterance" in c["outcome"] for c in lr._pending[0]["cands"]),
          str([c["outcome"] for c in lr._pending[0]["cands"]]))
    tl.append(ev(EVENT_OBSERVATION, "position", change="changed", ts=t + 95.0))
    agg = lr._aggregations.get("communicate(用户)") or {}
    check("迟到 50s 的用户回应被归因为言语动作的结果",
          any(o.startswith("obs:用户:utterance:")
              for o in (agg.get("outcomes") or {})), str(agg))
    # 别名：gather_resource 的库存观察应有归因资格（collect 域）
    t2 = time.time() + 1000.0
    g = ev(EVENT_ACTION, "gather_resource", ts=t2, target="dirt")
    tl.append(g)
    lr.record_action(g)
    tl.append(ev(EVENT_OBSERVATION, "inventory:dirt", actor="minecraft",
                 change="increased", ts=t2 + 2.0))
    check("inventory:dirt 落进 gather_resource 的预期作用域（别名表）",
          any("inventory:dirt" in c["outcome"]
              for c in lr._pending[0]["cands"]) if lr._pending else False,
          str([c["outcome"] for c in (lr._pending[0]["cands"] if lr._pending else [])]))
    shutil.rmtree(BASE, ignore_errors=True)


# ═══ 3. 条件签名：同动作同结果不同条件 → 分桶记账（夜失败≠白天失败）═══
def test_context_buckets():
    print("\n── 3 contexts 分桶 ──")
    tl, lr, kg = build()
    for i, ctx in enumerate(("night|hostile:near", "day|alone",
                             "night|hostile:near")):
        t = time.time() + i * 120
        act = ev(EVENT_ACTION, "walk_to", ts=t, target="x", context=ctx)
        tl.append(act)
        lr.record_action(act)
        tl.append(ev(EVENT_OBSERVATION, "position", actor="minecraft",
                     change="changed", ts=t + 2.0))
        tl.append(ev(EVENT_OBSERVATION, "clock", change="tick", ts=t + 30.0))
    agg = lr._aggregations.get("walk_to(x)") or {}
    o = (agg.get("outcomes") or {}).get("obs:minecraft:position:changed") or {}
    ctxs = o.get("contexts") or {}
    check("总 support=3", o.get("support") == 3, str(o))
    check("按条件分桶：night|hostile:near×2，day|alone×1",
          ctxs.get("night|hostile:near", {}).get("support") == 2
          and ctxs.get("day|alone", {}).get("support") == 1, str(ctxs))
    check("context_signature 从 internal_state 通用提取（跳过 pos 等噪声键）",
          "pos" not in context_signature({
              "content": {"internal_state": {
                  "game": {"health": 20, "food": 19,
                           "pos": {"x": 1.1, "y": 64.2, "z": -3.4}}}}})
          .replace("position", ""),
          context_signature({"content": {"internal_state": {
              "game": {"health": 20, "food": 19,
                       "pos": {"x": 1.1, "y": 64.2, "z": -3.4}}}}}))
    shutil.rmtree(BASE, ignore_errors=True)


# ═══ 4. action_prior：结果事件真的能回流成成功率/阻碍（B6 读端点地基）═══
def test_action_prior_flows_back():
    print("\n── 4 action_prior 有数据（旧链恒空）──")
    tl, lr, kg = build()
    T0 = 1750000000.0   # 过去时刻 → 出生即关窗，纯记账验证
    for i in range(3):
        tl.append(ev(EVENT_ACTION, "dig", ts=T0 + i * 10, target="stone"))
        tl.append(ev(EVENT_SELF_STATE, "dig", change="succeeded",
                     ts=T0 + i * 10 + 1))
        lr.record_action(tl.recent(5, event_type=EVENT_ACTION)[0])
    prior = lr.action_prior("dig", "stone")
    check("成功率可从自身经历读出（obs=3, success_rate>0）",
          prior["obs"] == 3 and (prior["success_rate"] or 0) > 0, str(prior))
    tl.append(ev(EVENT_ACTION, "dig", ts=T0 + 40, target="obsidian"))
    tl.append(ev(EVENT_SELF_STATE, "dig", change="failed:tool_missing",
                 ts=T0 + 41))
    lr.record_action(tl.recent(5, event_type=EVENT_ACTION)[0])
    p2 = lr.action_prior("dig", "obsidian")
    check("阻碍原因回流（tool_missing 上榜）",
          "tool_missing" in (p2["blockers"] or []), str(p2))
    shutil.rmtree(BASE, ignore_errors=True)


# ═══ 5. 依赖锁：接了归因，暂态理由必须同时豁免（永久抑制防线）═══
def test_transient_guard_shipped_together():
    print("\n── 5 暂态理由集合 + 奖赏分类同步 ──")
    from autonomy import _TRANSIENT_WORLD_REASONS
    from reward import classify_self_outcome
    check("stuck/unreachable/say_failed 已入暂态集合",
          {"stuck", "unreachable", "say_failed"} <= _TRANSIENT_WORLD_REASONS,
          str(sorted(_TRANSIENT_WORLD_REASONS)))
    for reason in ("stuck", "no_path", "unreachable", "say_failed"):
        check(f"失败理由 {reason} 归为 blocked（非满额 goal_failure）",
              classify_self_outcome("navigate_to_entity", False,
                                    {"reason": reason}) == "blocked", reason)
    check("结构性阻碍仍判 goal_failure（不被豁免稀释）",
          classify_self_outcome("craft_item", False,
                                {"reason": "materials_missing"}) == "goal_failure")
    shutil.rmtree(BASE, ignore_errors=True)


if __name__ == "__main__":
    test_late_result_attributed()
    test_window_table_and_aliases()
    test_context_buckets()
    test_action_prior_flows_back()
    test_transient_guard_shipped_together()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
        sys.exit(1)
    print("PASS: pending-window 因果归因验收全部通过")
