# test_experience_single_writer.py — B0：经验文件单一写者验收
# ============================================================================
# 磁盘实证病灶（2026-09-22）：ExperienceTimeline.flush 只写 {"raw":…}，
# CausalLearner._persist 写同一路径的 aggregations/hypotheses/promoted，
# 谁后写谁赢——data/experience_timeline.json 现存 383 事件、顶层 keys 仅
# ['raw']，全部因果统计被抹光。修法是唯一落盘通道 put_section/flush。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_experience_single_writer.py
# ============================================================================

import json
import os
import shutil
import sys
import tempfile

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
                        EVENT_ACTION, EVENT_OBSERVATION, EVENT_SELF_STATE)

BASE = os.path.join(tempfile.gettempdir(), "fas_single_writer")
T0 = 1750000000.0


def fresh_path():
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE, exist_ok=True)
    return os.path.join(BASE, "experience_timeline.json")


def ev(type_, subject, actor="self", change=None, ts=None, **content):
    e = make_event(type_, actor=actor, subject=subject,
                   content={"change": change, **content}, source="test")
    if ts is not None:
        e["ts"] = ts
    return e


def build(path):
    tl = ExperienceTimeline(path=path, config=dict(C.DEFAULT_CONFIG))
    kg = KnowledgeGraph()
    lr = CausalLearner(tl, config=dict(C.DEFAULT_CONFIG), kg=kg)
    return tl, lr, kg


# ═══ 1. 交替写入互不抹除（旧版必失一侧）═══
def test_interleaved_writes():
    print("\n── 1 时间轴与因果交替落盘 ──")
    path = fresh_path()
    tl, lr, kg = build(path)
    # 因果先写（过去时刻 → 出生即关窗，立即记账+persist）
    for i in range(3):
        tl.append(ev(EVENT_ACTION, "dig", ts=T0 + i * 10, target="stone"))
        tl.append(ev(EVENT_OBSERVATION, "block:stone", actor="env",
                     change="disappeared", ts=T0 + i * 10 + 1))
        lr.record_action(tl.recent(5, event_type=EVENT_ACTION)[0])
    # 之后再单纯追加事件（旧版 flush 会在这里抹掉 causal 三节）
    for i in range(5):
        tl.append(ev(EVENT_OBSERVATION, f"misc:{i}", actor="env",
                     change="changed", ts=T0 + 100 + i))
    tl.flush(force=True)   # 常规 append 走 2s 节流写；断言读盘前强制落一次
    doc = json.load(open(path, encoding="utf-8"))
    check("磁盘文档同时含 raw 与 causal",
          set(doc) >= {"raw", "causal"}, str(sorted(doc)))
    check("causal 节含完整统计结构",
          set(doc["causal"]) >= {"aggregations", "hypotheses", "promoted"},
          str(sorted(doc.get("causal", {}))))
    check("raw 事件未被统计写覆盖", len(doc["raw"]) == 11, str(len(doc["raw"])))
    check("聚合内容存活", doc["causal"]["aggregations"].get("dig(stone)", {})
          .get("obs") == 3, str(doc["causal"]["aggregations"]))
    # 重启回读
    tl2, lr2, _ = build(path)
    check("重启后因果统计完整回读",
          lr2._aggregations.get("dig(stone)", {}).get("obs") == 3
          and len(tl2) == 11, f"obs={lr2._aggregations} len={len(tl2)}")
    shutil.rmtree(BASE, ignore_errors=True)


# ═══ 2. 旧顶层形状一次性迁移 ═══
def test_legacy_migration():
    print("\n── 2 旧形状迁移 ──")
    path = fresh_path()
    legacy = {
        "raw": [ev(EVENT_OBSERVATION, "x", change="observed", ts=T0)],
        "aggregations": {"dig(old)": {"obs": 7, "outcomes": {
            "obs:env:block:old:gone": {"support": 5, "contra": 0,
                                       "first": "x", "last": "y"}}}},
        "hypotheses": {"dig(old)|obs:env:block:old:gone": {
            "action": "dig(old)", "outcome": "obs:env:block:old:gone",
            "support": 5, "confidence": 0.71, "status": "hypothesis"}},
        "promoted": [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(legacy, f, ensure_ascii=False)
    tl, lr, kg = build(path)
    check("顶层 aggregations/hypotheses/promoted 迁移为 causal 节",
          tl.get_section("causal") is not None
          and tl.get_section("causal")["aggregations"]
          .get("dig(old)", {}).get("obs") == 7, str(tl.get_section("causal")))
    check("CausalLearner 从迁移后的节载入",
          lr._aggregations.get("dig(old)", {}).get("obs") == 7
          and len(lr._hypotheses) == 1)
    # 触发任意写入 → 新形状落盘，旧顶层键消失
    tl.append(ev(EVENT_OBSERVATION, "y", change="observed", ts=T0 + 5))
    tl.flush(force=True)
    doc = json.load(open(path, encoding="utf-8"))
    check("迁移后文档只含新形状（raw+causal，无裸统计顶层键）",
          set(doc) == {"raw", "causal"}, str(sorted(doc)))
    check("迁移不丢数据", doc["causal"]["aggregations"]["dig(old)"]["obs"] == 7)
    shutil.rmtree(BASE, ignore_errors=True)


# ═══ 3. put_section 通用性 + 多节共存 ═══
def test_sections_api():
    print("\n── 3 通用节 API ──")
    path = fresh_path()
    tl = ExperienceTimeline(path=path, config=dict(C.DEFAULT_CONFIG))
    tl.append(ev(EVENT_OBSERVATION, "z", change="observed", ts=T0))
    tl.put_section("demo", {"a": 1})
    tl.put_section("causal", {"aggregations": {}})
    doc = json.load(open(path, encoding="utf-8"))
    check("任意统计节共存且各自保留", set(doc) == {"raw", "demo", "causal"},
          str(sorted(doc)))
    tl2 = ExperienceTimeline(path=path, config=dict(C.DEFAULT_CONFIG))
    check("节回读", tl2.get_section("demo") == {"a": 1}
          and tl2.get_section("missing") is None)
    check("flush 节流不丢 put_section（强制落盘）",
          json.load(open(path, encoding="utf-8"))["demo"]["a"] == 1)
    shutil.rmtree(BASE, ignore_errors=True)


if __name__ == "__main__":
    test_interleaved_writes()
    test_legacy_migration()
    test_sections_api()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
        sys.exit(1)
    print("PASS: 经验文件单一写者验收全部通过")
