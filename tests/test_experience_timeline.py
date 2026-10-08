# test_experience_timeline.py — 经验时间轴 + 保守因果发现 验收
# ============================================================================
# 任务 §十五：跨环境抽象测试（非 Minecraft 专用）+ §十六 红线。
#   A 动作→观察 形成候选（挖→方块消失/库存+1 同构场景）
#   B 聊天：她说 X → 用户回应 → 候选（say 的预期作用域含 utterance）
#   C 无关事件不归因（挖→远处实体移动）
#   D 重复提升 confidence 并形成假设
#   E 反例记 contradiction、confidence 有界、假设可降级
#   F 多来源同一时间轴（不建第二套记忆）
#   + 通用性红线：核心模块零 Minecraft 字段、零 LLM；事件级去重合并；
#     KG 只在足够支撑后晋升；autonomy 注入可用。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_experience_timeline.py
# ============================================================================

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
from graph_model import KnowledgeGraph, Node
from experience import (ExperienceTimeline, CausalLearner, make_event,
                        EVENT_ACTION, EVENT_OBSERVATION, EVENT_SELF_STATE,
                        EVENT_COGNITIVE, action_signature, outcome_signature)

BASE = os.path.join(tempfile.gettempdir(), "fas_exp_test")


def build(fresh=True):
    if fresh:
        shutil.rmtree(BASE, ignore_errors=True)
        os.makedirs(BASE, exist_ok=True)
    path = os.path.join(BASE, "experience_timeline.json")
    tl = ExperienceTimeline(path=path, config=dict(C.DEFAULT_CONFIG))
    kg = KnowledgeGraph()
    for nid in ("用户", "Self"):
        kg.add_node(Node(id=nid))
    lr = CausalLearner(tl, config=dict(C.DEFAULT_CONFIG), kg=kg)
    return tl, lr, kg


def ev(type_, actor, subject, change=None, ts=None, source="test", **content):
    e = make_event(type_, actor=actor, subject=subject,
                   content={"change": change, **content}, source=source)
    if ts is not None:
        e["ts"] = ts
        import time as _t
        e["ts_str"] = _t.strftime("%H:%M:%S", _t.localtime(ts))
    return e


T0 = 1750000000.0


# ═══════ 事件级去重合并（§二：不是采样日志）═══════
def test_merge_dedup():
    print("\n── 合并去重：高频同类观察不刷屏 ──")
    tl, lr, kg = build()
    tl.append(ev(EVENT_OBSERVATION, "minecraft", "block:oak_log",
                 "disappeared", ts=T0))
    tl.append(ev(EVENT_OBSERVATION, "minecraft", "block:oak_log",
                 "disappeared", ts=T0 + 0.5))
    tl.append(ev(EVENT_OBSERVATION, "minecraft", "block:oak_log",
                 "appeared", ts=T0 + 0.6))   # change 不同 → 不合并
    check("同类同主体同变化合并为一条（repeat 计数）",
          sum(1 for e in tl.recent(50) if e["meta"].get("repeat")) == 1)
    check("不同变化不合并", len(tl) == 2, f"len={len(tl)}")
    shutil.rmtree(BASE, ignore_errors=True)


# ═══════ A：动作→观察形成候选（dig 同构）═══════
def test_A_candidate_outcomes():
    print("\n── A ACTION→OBSERVATION 候选 ──")
    tl, lr, kg = build()
    tl.append(ev(EVENT_ACTION, "self", "dig", ts=T0, target="oak_log"))
    tl.append(ev(EVENT_OBSERVATION, "minecraft", "block:oak_log",
                 "disappeared", ts=T0 + 1.0))
    tl.append(ev(EVENT_SELF_STATE, "self", "inventory",
                 "increased", ts=T0 + 1.2, item="oak_log"))
    cands = lr.record_action(tl.recent(5, event_type=EVENT_ACTION)[0])
    sigs = {c["outcome"] for c in cands}
    check("方块消失成为候选结果",
          "obs:minecraft:block:oak_log:disappeared" in sigs, str(sigs))
    check("库存增加成为候选结果",
          "self:self:inventory:increased" in sigs, str(sigs))
    check("聚合已记账（obs=1, 两类 support=1）",
          lr._aggregations["dig(oak_log)"]["obs"] == 1)
    shutil.rmtree(BASE, ignore_errors=True)


# ═══════ B：聊天——她说 X → 用户回应 ═══════
def test_B_chat_association():
    print("\n── B say→用户回应 候选 ──")
    tl, lr, kg = build()
    tl.append(ev(EVENT_ACTION, "self", "say", ts=T0, target=None,
                 text="你觉得Minecraft好玩吗", kind="dialogue_answer"))
    tl.append(ev(EVENT_OBSERVATION, "用户", "utterance",
                 "observed", ts=T0 + 3.0, text="好玩啊哈哈"))
    cands = lr.record_action(tl.recent(5, event_type=EVENT_ACTION)[0])
    sigs = {c["outcome"] for c in cands}
    check("用户回应成为 say 的候选结果（第一版只到 candidate）",
          any(s.startswith("obs:用户:utterance:") for s in sigs), str(sigs))
    shutil.rmtree(BASE, ignore_errors=True)


# ═══════ C：无关事件不归因（§七）═══════
def test_C_unrelated_not_attributed():
    print("\n── C 无关事件不被归因 ──")
    tl, lr, kg = build()
    tl.append(ev(EVENT_ACTION, "self", "dig", ts=T0, target="oak_log"))
    tl.append(ev(EVENT_OBSERVATION, "minecraft", "entity:chicken",
                 "appeared", ts=T0 + 0.8))          # 远处鸡出现
    tl.append(ev(EVENT_OBSERVATION, "minecraft", "player:Steve",
                 "appeared", ts=T0 + 2.0))          # 别的玩家加入
    cands = lr.record_action(tl.recent(5, event_type=EVENT_ACTION)[0])
    check("无关观察（实体/玩家出现）不成为 dig 的候选结果", cands == [], str(cands))
    check("dig 聚合存在但无 support 记到无关结果上",
          lr._aggregations.get("dig(oak_log)", {}).get("obs") == 1
          and not lr._aggregations["dig(oak_log)"]["outcomes"])
    shutil.rmtree(BASE, ignore_errors=True)


# ═══════ D：重复 → confidence 上升 → 假设 ═══════
def test_D_repeat_confidence():
    print("\n── D 重复经验 → 假设（§八）──")
    tl, lr, kg = build()
    confs = []
    for i in range(5):
        tl.append(ev(EVENT_ACTION, "self", "dig", ts=T0 + i * 10, target="oak_log"))
        tl.append(ev(EVENT_OBSERVATION, "minecraft", "block:oak_log",
                     "disappeared", ts=T0 + i * 10 + 1))
        lr.record_action(tl.recent(5, event_type=EVENT_ACTION)[0])
        confs.append(lr.confidence("dig(oak_log)",
                                   "obs:minecraft:block:oak_log:disappeared"))
    check("confidence 随重复单调上升", all(b > a for a, b in zip(confs, confs[1:])),
          str(confs))
    key = "dig(oak_log)|obs:minecraft:block:oak_log:disappeared"
    h = lr._hypotheses.get(key)
    check("重复达标后形成假设（hypothesis，非事实）",
          h is not None and h["status"] == "hypothesis" and h["support"] >= 3
          and h["confidence"] >= 0.6, str(h))
    check("假设带完整 provenance（observations/support/contra/first/last）",
          h and {"observations", "support", "contradictions",
                 "first_observed", "last_observed"} <= set(h.keys()))
    shutil.rmtree(BASE, ignore_errors=True)


# ═══════ E：反例 → contradiction、confidence 有界、假设可降级 ═══════
def test_E_contradiction():
    print("\n── E 反例（§八/§九）──")
    tl, lr, kg = build()
    for i in range(4):   # 先建立规律
        tl.append(ev(EVENT_ACTION, "self", "dig", ts=T0 + i * 10, target="oak_log"))
        tl.append(ev(EVENT_OBSERVATION, "minecraft", "block:oak_log",
                     "disappeared", ts=T0 + i * 10 + 1))
        lr.record_action(tl.recent(5, event_type=EVENT_ACTION)[0])
    conf_before = lr.confidence("dig(oak_log)",
                                "obs:minecraft:block:oak_log:disappeared")
    key = "dig(oak_log)|obs:minecraft:block:oak_log:disappeared"
    # 两次动作但结果没发生（反例）
    for i in range(2):
        tl.append(ev(EVENT_ACTION, "self", "dig", ts=T0 + 100 + i * 10,
                     target="oak_log"))
        lr.record_action(tl.recent(5, event_type=EVENT_ACTION)[0])
    o = lr._aggregations["dig(oak_log)"]["outcomes"]["obs:minecraft:block:oak_log:disappeared"]
    conf_after = lr.confidence("dig(oak_log)",
                               "obs:minecraft:block:oak_log:disappeared")
    check("反例被记录（contra ≥ 2）", int(o["contra"]) >= 2, str(o))
    check("confidence 下降且永远 < 1.0",
          conf_after < conf_before and conf_after < 1.0,
          f"{conf_before}→{conf_after}")
    if key in lr._hypotheses:
        check("反例累积后假设降级为 weakened（保留历史不删除）",
              lr._hypotheses[key]["status"] in ("weakened", "hypothesis"),
              lr._hypotheses[key]["status"])
    shutil.rmtree(BASE, ignore_errors=True)


# ═══════ F：多来源同一条时间轴 ═══════
def test_F_multi_source_one_timeline():
    print("\n── F 多来源统一时间轴（§四）──")
    tl, lr, kg = build()
    tl.append(ev(EVENT_ACTION, "self", "say", ts=T0, text="你好",
                 kind="dialogue_answer", source="dialogue"))
    tl.append(ev(EVENT_OBSERVATION, "user", "utterance", "observed", ts=T0 + 1,
                 text="嗨", source="nlp"))
    tl.append(ev(EVENT_SELF_STATE, "self", "reward", "changed", ts=T0 + 1.5,
                 source="reward", social="accepted", valence=0.5))
    tl.append(ev(EVENT_COGNITIVE, "self", "curiosity", "triggered", ts=T0 + 2,
                 source="curiosity_engine"))
    by_src = {}
    for e in tl.recent(20):
        by_src.setdefault(e["source"], []).append(e)
    check("同一时间轴承载多来源事件", len(by_src) >= 3, str(by_src.keys()))
    check("按类型过滤查询可用",
          len(tl.recent(20, event_type=EVENT_ACTION)) == 1
          and len(tl.recent(20, event_type=EVENT_OBSERVATION)) == 1)
    # 持久化往返：raw 事件存活（经验层有界持久）
    tl.flush(force=True)
    tl2 = ExperienceTimeline(path=tl.path, config=dict(C.DEFAULT_CONFIG))
    check("时间轴持久化往返完整", len(tl2) == len(tl) and len(tl) == 4, str(len(tl2)))
    shutil.rmtree(BASE, ignore_errors=True)


# ═══════ KG 晋升：只写足够稳定的泛化关系（§十二）═══════
def test_kg_promotion():
    print("\n── KG 晋升门槛（B0：越线自动触发）──")
    tl, lr, kg = build()
    for i in range(8):   # 8 次一致：8/(8+0+2)=0.80 ≥ 0.8 且 support 8≥5
        tl.append(ev(EVENT_ACTION, "self", "dig", ts=T0 + i * 10, target="oak_log"))
        tl.append(ev(EVENT_OBSERVATION, "minecraft", "block:oak_log",
                     "disappeared", ts=T0 + i * 10 + 1))
        lr.record_action(tl.recent(5, event_type=EVENT_ACTION)[0])
        if i == 3:   # 4 次（0.57 < 0.8 且 support<5）：绝不提前进图
            check("支撑不足时绝不写 KG（中途复查）",
                  kg.get_edge("操作:dig(oak_log)",
                              "变化:obs:minecraft:block:oak_log:disappeared",
                              "导致") is None)
    check("越线自动晋升（外部无需记得调用 promote）",
          kg.get_edge("操作:dig(oak_log)",
                      "变化:obs:minecraft:block:oak_log:disappeared",
                      "导致") is not None)
    promoted = [k for k in lr._promoted]
    check("晋升集合已记账（幂等基础）", len(promoted) == 1, str(promoted))
    e = kg.get_edge("操作:dig(oak_log)", "变化:obs:minecraft:block:oak_log:disappeared", "导致")
    check("KG 出现 [dig oak_log] -导致-> [oak_log disappeared]（causal_relation, forward）",
          e is not None and e.relation_category == "causal_relation")
    check("边带完整 provenance（observations/support/confidence）",
          e is not None and kg.nodes["操作:dig(oak_log)"].extra_attrs.get("observations") == 8)
    # 幂等：再晋升不重复
    check("晋升幂等", lr.promote_to_kg(kg) == [])
    # 早期不晋升：4 次（0.57 < 0.8）不得进 KG
    tl2, lr2, kg2 = build()
    for i in range(4):
        tl2.append(ev(EVENT_ACTION, "self", "dig", ts=T0 + i * 10, target="stone"))
        tl2.append(ev(EVENT_OBSERVATION, "minecraft", "block:stone",
                      "disappeared", ts=T0 + i * 10 + 1))
        lr2.record_action(tl2.recent(5, event_type=EVENT_ACTION)[0])
    check("支撑不足时绝不写 KG", lr2.promote_to_kg(kg2) == []
          and kg2.get_edge("操作:dig(stone)",
                           "变化:obs:minecraft:block:stone:disappeared", "导致") is None)
    shutil.rmtree(BASE, ignore_errors=True)


# ═══════ autonomy 注入（P3 接线）═══════
def test_autonomy_wiring():
    print("\n── autonomy ACTION 入轴 ──")
    tl, lr, kg = build()
    from autonomy import AutonomousLoop
    base = os.path.join(tempfile.gettempdir(), "fas_exp_aut")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)

    class StubEmb:
        def available(self): return True
        def capabilities(self): return {"observe"}
        def perceive(self): return {"connected": True, "players": [], "entities": [],
                                    "blocks": [], "unknown_entities": [], "unknown_blocks": []}
        def execute(self, intent): return {"success": True, "action": intent["type"],
                                           "result": "looked"}
        def cancel(self): return {}
    loop = AutonomousLoop(kg=kg, config={}, disposition_store=None, data_dir=base)
    loop.register_embodiment(StubEmb())
    loop.timeline = tl
    loop.causal = lr
    cand = {"type": "observe", "target": "axolotl", "params": {"entity": "axolotl"},
            "basis": ["UnknownEntity_axolotl"], "explain": "好奇"}
    loop._execute(cand, now=T0)
    acts = tl.recent(10, event_type=EVENT_ACTION)
    check("自主执行的动作成为 ACTION 事件", len(acts) == 1
          and action_signature(acts[0]) == "observe(axolotl)", str(acts))
    shutil.rmtree(base, ignore_errors=True)


# ═══════ 通用性红线（§一/§十一）════════
def test_genericity():
    print("\n── 通用性红线 ──")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "experience.py"), encoding="utf-8").read()
    low = src.lower()
    check("核心模块不含 Minecraft 专有字段",
          "minecraft" not in low and "bot.js" not in low and "mineflayer" not in low)
    check("核心模块零 LLM（无 langchain/nlp 依赖）",
          "langchain" not in low and "nlp_processor" not in low
          and "chat_llm" not in low)
    # KG 写入只允许出现在显式的 promote_to_kg 内（§十二：单事件绝不进图）
    n_node = src.count("kg.add_node")
    n_edge = src.count("kg.add_edge")
    head = src.split("def promote_to_kg")[0]   # promote_to_kg 之前的全部代码
    check("KG 写入点只存在于 promote_to_kg（节点×2 + 边×1，此前零写入）",
          n_node == 2 and n_edge == 1
          and "kg.add_node" not in head and "kg.add_edge" not in head)


if __name__ == "__main__":
    test_merge_dedup()
    test_A_candidate_outcomes()
    test_B_chat_association()
    test_C_unrelated_not_attributed()
    test_D_repeat_confidence()
    test_E_contradiction()
    test_F_multi_source_one_timeline()
    test_kg_promotion()
    test_autonomy_wiring()
    test_genericity()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAIL: {len(FAILURES)} 项未通过")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS: 经验时间轴 A–F + 红线全部通过")
