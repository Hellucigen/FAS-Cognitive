# -*- coding: utf-8 -*-
# delayed_credit.py — 实验 B 机制部分(零 LLM):延迟后果→因果归因曲线
# 走生产 CausalLearner/ExperienceTimeline 公开 API(合成事件,与
# tests/test_causal_pending_attribution.py 同款时序回放);零 FAS 修改。
# gather_resource 归因窗=90s(生产数据表),MIN_SCORE=0.60 要求 dt≤⅔窗(60s)。
# delay ∈ {0,2,5,10} tick × 16s = {0,32,80,160}s → 预期 0/32 内、80/160 外。
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import logging
logging.disable(logging.WARNING)

import config as C  # noqa: E402
from graph_model import KnowledgeGraph  # noqa: E402
from experience import (ExperienceTimeline, CausalLearner, make_event,  # noqa: E402
                        EVENT_ACTION, EVENT_OBSERVATION, EVENT_SELF_STATE)

BASE = os.path.join(tempfile.gettempdir(), "fas_capB")
TICK = 16.0
DELAYS = (0, 2, 5, 10)
SEEDS = list(range(10))


def ev(type_, subject, actor="self", change=None, ts=None, **content):
    e = make_event(type_, actor=actor, subject=subject,
                   content={"change": change, **content}, source="capB")
    if ts is not None:
        e["ts"] = ts
        e["ts_str"] = time.strftime("%H:%M:%S", time.localtime(ts))
    return e


def build():
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE, exist_ok=True)
    tl = ExperienceTimeline(path=os.path.join(BASE, "timeline.json"),
                            config=dict(C.DEFAULT_CONFIG))
    kg = KnowledgeGraph()
    lr = CausalLearner(tl, config=dict(C.DEFAULT_CONFIG), kg=kg)
    return tl, lr, kg


def run_delay(delay, seed):
    tl, lr, kg = build()
    t = time.time()
    act = ev(EVENT_ACTION, "gather_resource", ts=t, target="oak_log",
             context="day|alone")
    tl.append(act)
    lr.record_action(act)
    # 无关填充事件(主体不相关 → 不得搭车归因)
    for k in range(delay):
        tl.append(ev(EVENT_OBSERVATION, "entity:chicken", actor="minecraft",
                     change="appeared", ts=t + TICK * (k + 1) - 1.0))
    # 延迟后果:库存增量观察(主体相关)
    cons_ts = t + TICK * delay
    tl.append(ev(EVENT_OBSERVATION, "inventory:oak_log", actor="minecraft",
                 change="count_increased", ts=cons_ts))
    # 动作自身成功回执(规则 0 天然相关)
    tl.append(ev(EVENT_SELF_STATE, "gather_resource", change="succeeded",
                 ts=cons_ts + 0.5))
    # 哨兵关窗
    tl.append(ev(EVENT_OBSERVATION, "position", actor="minecraft",
                 change="changed", ts=cons_ts + 200.0))
    agg = lr._aggregations.get("gather_resource(oak_log)") or {}
    outcomes = agg.get("outcomes") or {}
    inv_key = [k for k in outcomes if "count_increased" in k]
    res = {"delay_ticks": delay, "delay_s": TICK * delay, "seed": seed,
           "obs": agg.get("obs"),
           "inventory_outcome_attributed": bool(inv_key),
           "inventory_support": (outcomes[inv_key[0]].get("support")
                                 if inv_key else 0),
           "self_success_attributed": any("succeeded" in k for k in outcomes),
           "promoted": len(lr._promoted) if hasattr(lr, "_promoted") else None}
    shutil.rmtree(BASE, ignore_errors=True)
    return res


def main():
    rows = []
    for delay in DELAYS:
        for seed in SEEDS:
            try:
                r = run_delay(delay, seed)
            except Exception as e:
                r = {"delay_ticks": delay, "seed": seed, "error": repr(e)[:160]}
            rows.append(r)
            print("[B-mech]", json.dumps(r, ensure_ascii=False)[:150], flush=True)
    out = os.path.join(ROOT, "experiments", "capability_gap_v2", "raw_B_mech.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("saved ->", out)


if __name__ == "__main__":
    main()
