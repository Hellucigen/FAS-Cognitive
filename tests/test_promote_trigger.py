# test_promote_trigger.py — B0：晋升链末端的生产触发器验收
# ============================================================================
# 病灶：promote_to_kg 全库零生产调用点（除测试）——"候选→聚合→假设→
# 泛化知识"四段路在最后一百米断头。修法：_hypothesize 里越线（support≥5
# 且 conf≥0.8）当场自动晋升并记 promoted 集。本测试全程走**真实事件驱动
# 路径**（动作挂窗→结果迟到入窗→哨兵关窗→记账→假设→自动晋升），
# 不用出生即关窗的补录捷径，确保生产链形态被钉死。
# 输入全为合成事件；KG 为内存图。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_promote_trigger.py
# ============================================================================

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
from graph_model import KnowledgeGraph
from experience import (ExperienceTimeline, CausalLearner, make_event,
                        EVENT_ACTION, EVENT_OBSERVATION,
                        KG_SUPPORT, KG_CONFIDENCE)

BASE = os.path.join(tempfile.gettempdir(), "fas_promote")


def ev(type_, subject, actor="self", change=None, ts=None, **content):
    e = make_event(type_, actor=actor, subject=subject,
                   content={"change": change, **content}, source="test")
    if ts is not None:
        e["ts"] = ts
    return e


def build(path, kg):
    tl = ExperienceTimeline(path=path, config=dict(C.DEFAULT_CONFIG))
    lr = CausalLearner(tl, config=dict(C.DEFAULT_CONFIG), kg=kg)
    return tl, lr


def run_cycle(tl, lr, t):
    """一次完整的生产时序：动作→(2s后)结果→(越过归因窗线)哨兵关窗。
    关窗事件必须晚于该类动作的窗 deadline（break_block=90s，见
    ACTION_WINDOW_S）——未越线的 tick 不关窗。2026-09-25 定位：本文件
    初版把哨兵放在 +10s，永远够不到 90s 线，支撑滞后 3 拍入账，
    8 轮 conf 只到 5/7=0.714，晋升自然一次都不触发。"""
    act = ev(EVENT_ACTION, "break_block", ts=t, target="coal_ore")
    tl.append(act)
    lr.record_action(act)
    tl.append(ev(EVENT_OBSERVATION, "block:coal_ore", actor="env",
                 change="disappeared", ts=t + 2.0))
    tl.append(ev(EVENT_OBSERVATION, "clock:tick", actor="env",
                 change="ticked", ts=t + CausalLearner.window_for(act) + 5.0))


if __name__ == "__main__":
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE, exist_ok=True)
    path = os.path.join(BASE, "experience_timeline.json")
    kg = KnowledgeGraph()
    tl, lr = build(path, kg)
    a_id = "操作:break_block(coal_ore)"
    o_id = "变化:obs:env:block:coal_ore:disappeared"
    t0 = time.time()

    print("\n── 逐轮支撑（窗口事件驱动，非补录）──")
    for i in range(KG_SUPPORT + 3):     # 8 次一致：conf 8/10=0.80 ≥ 0.8
        run_cycle(tl, lr, t0 + i * 30)
        s = lr._aggregations.get("break_block(coal_ore)", {}) \
            .get("outcomes", {}).get(
                "obs:env:block:coal_ore:disappeared", {}).get("support", 0)
        if i + 1 == KG_SUPPORT and s >= KG_SUPPORT:
            conf = lr.confidence("break_block(coal_ore)",
                                 "obs:env:block:coal_ore:disappeared")
            below = kg.get_edge(a_id, o_id, "导致") is None
            check(f"到晋升线 support 时若 conf({conf:.2f})<{KG_CONFIDENCE} "
                  f"仍不进图（双门槛缺一不可）",
                  (conf < KG_CONFIDENCE and below) or conf >= KG_CONFIDENCE)

    e = kg.get_edge(a_id, o_id, "导致")
    check("无人外部调用：越线自动晋升 操作-[导致]->变化",
          e is not None and e.relation_category == "causal_relation", str(e))
    hkey = "break_block(coal_ore)|obs:env:block:coal_ore:disappeared"
    check("promoted 集已记账", hkey in lr._promoted, str(sorted(lr._promoted)))
    node = kg.nodes.get(a_id)
    check("晋升节点带 provenance（source=causal_hypothesis + support/observations）",
          node is not None
          and node.extra_attrs.get("source") == "causal_hypothesis"
          and node.extra_attrs.get("observations", 0) >= KG_SUPPORT,
          str(node.extra_attrs if node else None))
    check("边权重=confidence 且有界 <1.0",
          e is not None and 0.0 < float(e.weight) < 1.0,
          str(e.weight if e else None))

    # 幂等：继续再来 3 轮，不重复建边/节点
    n_nodes = len(kg.nodes)
    for i in range(3):
        run_cycle(tl, lr, t0 + (KG_SUPPORT + 3 + i) * 30)
    check("后续支撑不重复晋升（节点数不变、幂等）",
          len(kg.nodes) == n_nodes and lr.promote_to_kg() == [],
          f"{n_nodes}→{len(kg.nodes)}")

    # 重启回读：promoted 集持久化 → 不重复写图
    tl2, lr2 = build(path, kg)
    check("重启后 promoted 集完整回读", hkey in lr2._promoted,
          str(sorted(lr2._promoted)))
    run_cycle(tl2, lr2, t0 + 600)
    check("重启后再遇同类经验仍不重复晋升",
          kg.get_edge(a_id, o_id, "导致") is not None
          and len([k for k in kg.nodes if k.startswith("操作:")]) == 1)

    # 已晋升组继续积累支撑：仍只有一条边（不重复晋升的持续验证）
    t1 = time.time() + 10000
    for i in range(3):
        run_cycle(tl2, lr2, t1 + i * 30)
    n_op = len([k for k in kg.nodes if k.startswith("操作:")])
    check("全部周期后 KG 晋升节点仍恰好一个（无重复写入）", n_op == 1, str(n_op))
    check("debug_report 含 pending 窗口观测面",
          "pending_windows" in lr2.debug_report())

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
        sys.exit(1)
    print("PASS: 因果晋升链末端触发验收全部通过")
