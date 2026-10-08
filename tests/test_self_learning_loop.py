# test_self_learning_loop.py — Self Model 学习闭环回归护栏（2026-09）
# ============================================================================
# 验收目标不是"图里多几个节点"，而是闭环真的转起来：
#   Experience → Outcome → Self-relevant interpretation → Candidate
#   → Evidence → (Stable) → Self Graph → Activation → 未来行为
#
# 八项测试逐条对应任务规范 §十七：
#   T1 明确正反馈 → candidate 生成、importance 可解释、evidence +1
#   T2 一次经历 ≠ 稳定人格（evidence=1 不能 stable）
#   T3 重复经历 → candidate→stable，图权重变化
#   T4 用户反馈不是唯一来源（自身目标成功也能形成证据）
#   T5 中性 outcome 不被错算成正向
#   T6 stable + 单次负反馈不被删除（降级可塑而非连坐清除）
#   T7 学习必须改变未来行为（同情境 before/after：边权→倾向→desire）
#   T8 图是唯一长期真源（不新增平行人格 JSON）
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_self_learning_loop.py
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


from graph_model import KnowledgeGraph, Node, Edge
from disposition_store import DispositionStore, BEHAVIORS
import dialogue_decision as dd_mod
from dialogue_decision import dialogue_decide


class FN:
    def __init__(s, id, act=0.0, space="semantic"):
        s.id, s.activation, s.graph_space = id, act, space
        s.extra_attrs = {}
        s.label = "declarative-semantic"


class FakeEngine:
    _running = False

    def __init__(s, topk=None):
        s.topk = topk or []
        s.marked = []

    def get_topk(s, k=15):
        return s.topk[:k], []

    def mark_active(s, ids):
        s.marked = list(ids)

    def mark_edges_active(s, edges):
        pass


def new_store():
    """临时图 + 干净的 DispositionStore（不碰 data/）。"""
    base = os.path.join(tempfile.gettempdir(), "fas_self_loop_test")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    kg = KnowledgeGraph()
    for nid in ("用户", "Self"):
        kg.add_node(Node(id=nid))
    ds = DispositionStore(kg)
    return kg, ds


def activation_edge_weight(ds, ctx, behavior):
    e = ds._activation_edge(ctx, behavior)
    return e.weight if e else 0.0


# ═══════ T1 明确正反馈 → candidate 生成 + 证据 +1 + importance 可追踪 ═══════
def test_first_experience_creates_candidate():
    print("\n── T1 明确正反馈（首条经历）──")
    kg, ds = new_store()
    before = len(kg.nodes)
    wrote = ds.apply_outcome("情境:用户提问", "share", "positive",
                             evidence_ref="用户：你主动分享挺好的",
                             source="user_feedback", allow_create=True)
    pair = ds.get_pair("情境:用户提问", "share")
    check("首条真实正反馈建立了 candidate pair",
          wrote and pair is not None and pair.extra_attrs["status"] == "candidate")
    check("evidence_count=1 且 positive_count=1",
          pair.extra_attrs["evidence_count"] == 1
          and pair.extra_attrs["positive_count"] == 1,
          str({k: pair.extra_attrs[k] for k in ("evidence_count", "positive_count")}))
    check("provenance 完整（sources/first_observed/confidence）",
          pair.extra_attrs.get("sources") == {"social:accepted": 1}
          and pair.extra_attrs.get("first_observed")
          and pair.extra_attrs.get("confidence", 0) >= 0.3)
    check("pair 长在图上（节点+产生/指向/激活边）",
          len(kg.nodes) >= before + 1
          and ds._activation_edge("情境:用户提问", "share") is not None)

    # importance 链路 trace（Self Model 侧）：跑一次真实候选提取，断言日志函数存在且可算
    import self_model
    ev = self_model.evaluate_importance(
        {"type": "preference", "target": "Minecraft", "source": "explicit_statement"},
        {"is_explicit": True, "emotion_active": [], "repeat_count": 0,
         "dialogue_act": "information_statement", "llm_confidence": 0.9},
        {})
    check("首次明确自我陈述 importance 有完整因子输出（trace 数据源）",
          {"score", "passed", "factors", "threshold"} <= set(ev.keys()), str(ev))
    # 设计事实：首次出现必被门槛挡（一次经历只产生 candidate），第二次强化才落图
    check("首次出现 importance 不过门槛（一次经历不改稳定自我，§七）",
          ev["passed"] is False and ev["score"] < 0.5, str(ev["score"]))


# ═══════ T2 一次经历不能形成稳定人格 ═══════
def test_single_experience_not_stable():
    print("\n── T2 一次经历 → 只 candidate ──")
    kg, ds = new_store()
    ds.apply_outcome("情境:用户分享", "empathize", "positive",
                     evidence_ref="唯一一次", source="user_feedback", allow_create=True)
    pr = ds.get_pair("情境:用户分享", "empathize")
    check("evidence=1 时 status != stable",
          pr.extra_attrs["evidence_count"] == 1
          and pr.extra_attrs["status"] == "candidate")

    # 回归护栏（真实图谱发现的历史隐患）：旧版 get_or_create 会把出厂播种引用
    # 计入 evidence_count（5~8），若 promotion 看 evidence_count，一次正反馈
    # 就能把出厂对（0.45+0.08=0.53）直接推到 stable。promotion 必须只数真实
    # 经历（pos+neg）。
    legacy = ds.get_or_create("情境:用户感谢", "respond",
                              evidence_ref="出厂基线倾向")
    legacy.extra_attrs["evidence_count"] = 8      # 模拟旧存档灌水
    legacy.extra_attrs["strength"] = 0.45         # 出厂先验水平
    ds.apply_outcome("情境:用户感谢", "respond", "positive",
                     evidence_ref="第一次真实正反馈", source="user_feedback",
                     allow_create=False)
    pr2 = ds.get_pair("情境:用户感谢", "respond")
    check("旧 pair evidence_count 被出厂引用灌水时，一次反馈不晋升 stable",
          pr2.extra_attrs["status"] == "candidate"
          and pr2.extra_attrs["positive_count"] == 1,
          str(pr2.extra_attrs["status"]))


# ═══════ T3 重复经历 → stable + 权重变化 ═══════
def test_repeated_experiences_promote():
    print("\n── T3 4 次一致经历 → stable ──")
    kg, ds = new_store()
    w0 = activation_edge_weight(ds, "情境:用户分享", "share")
    for i in range(5):
        ds.apply_outcome("情境:用户分享", "share", "positive",
                         evidence_ref=f"独立经历{i}",
                         source="user_feedback", allow_create=True)
    pr = ds.get_pair("情境:用户分享", "share")
    ea = pr.extra_attrs
    check("5 次社会正反馈 → stable（社会权重 0.4，需更多独立经历）",
          ea["status"] == "stable", str(ea))
    check("strength ≥ 0.5 且边权同步（图权重承载人格）",
          float(ea["strength"]) >= 0.5
          and abs(activation_edge_weight(ds, "情境:用户分享", "share")
                  - float(ea["strength"])) < 1e-6)
    check("激活边权重从 0 抬升", activation_edge_weight(ds, "情境:用户分享", "share") > w0)


# ═══════ T4 用户反馈不是唯一来源 ═══════
def test_self_goal_success_source():
    print("\n── T4 自身目标成功（无用户反馈）也产生证据 ──")
    from autonomy import AutonomousLoop
    kg, ds = new_store()
    base = os.path.join(tempfile.gettempdir(), "fas_self_loop_aut")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    loop = AutonomousLoop(kg=kg, config={}, disposition_store=ds, data_dir=base)
    # 单一行动路径（清理 2026-09-20）：disposition 证据通道在 ActionManager
    am = loop.actions

    # 自主探索成功：用户完全没说话
    cand = {"action_type": "observe", "target": "axolotl", "params": {},
            "reason": ["UnknownEntity_axolotl", "CuriosityDrive"], "explain": ""}
    # P6 证据门（2026-09-27）：observe 未知目标要算 discovery 必须带真观察
    # 证据（entity/dist/…）。纯 "looked" 字符串会被如实重分类为 goal_success。
    result = {"success": True, "result": {"entity": "axolotl", "dist": 2.5}}
    am._record_disposition_outcome(cand, result, True)
    pr = ds.get_pair("情境:主动发起", "explore")
    check("自主探索成功 → 倾向:explore@主动发起 建立", pr is not None)
    check("来源记为 self:discovery（自我发现，不是 social/user_feedback）",
          pr.extra_attrs.get("sources") == {"self:discovery": 1},
          str(pr.extra_attrs.get("sources")))

    # 失败走负证据，步长更温和（self_goal_failure）
    s0 = float(pr.extra_attrs["strength"])
    am._record_disposition_outcome(cand, {"success": False, "reason": "entity_not_visible"}, False)
    pr = ds.get_pair("情境:主动发起", "explore")
    s1 = float(pr.extra_attrs["strength"])
    check("一次失败 → negative 证据，强度下降但幅度温和(≤0.07)",
          pr.extra_attrs["negative_count"] == 1 and 0 < s0 - s1 <= 0.07,
          f"{s0}→{s1}")

    # 混合来源：同一 pair 可被自我来源 + 用户来源共同塑造（不止用户反馈）
    ds.apply_outcome("情境:主动发起", "explore", "positive",
                     evidence_ref="用户夸了主动观察", source="user_feedback",
                     allow_create=False)
    sources = ds.get_pair("情境:主动发起", "explore").extra_attrs["sources"]
    check("多来源共存：自我来源 + 用户来源同时存在（人格≠仅用户训练）",
          len(sources) >= 2
          and any(k.startswith("social:") for k in sources)
          and any(k.startswith("self:") for k in sources), str(sources))

    # cancelled 不算证据
    n_ev = ds.get_pair("情境:主动发起", "explore").extra_attrs["evidence_count"]
    am._record_disposition_outcome(cand, {"success": False, "cancelled": True}, False)
    check("取消的行动不记证据",
          ds.get_pair("情境:主动发起", "explore").extra_attrs["evidence_count"] == n_ev)
    shutil.rmtree(base, ignore_errors=True)


# ═══════ T5 中性 outcome 不错算成正向 ═══════
def test_neutral_not_positive():
    print("\n── T5 中性 outcome ──")
    kg, ds = new_store()
    ds.apply_outcome("情境:用户提问", "elaborate", "neutral",
                     evidence_ref="话题切换", source="user_feedback", allow_create=True)
    check("neutral 不建立 pair", ds.get_pair("情境:用户提问", "elaborate") is None)

    ds.apply_outcome("情境:用户分享", "respond", "positive",
                     evidence_ref="先正", source="user_feedback", allow_create=True)
    s0 = float(ds.get_pair("情境:用户分享", "respond").extra_attrs["strength"])
    ds.apply_outcome("情境:用户分享", "respond", "neutral",
                     evidence_ref="中性", source="user_feedback")
    ea = ds.get_pair("情境:用户分享", "respond").extra_attrs
    check("neutral 只计数不动强度",
          ea["neutral_count"] == 1
          and abs(float(ea["strength"]) - s0) < 1e-9)
    check("neutral 不被算进 positive_count", ea["positive_count"] == 1)

    ds.apply_outcome("情境:用户分享", "respond", "ambiguous",
                     evidence_ref="正负同现", source="user_feedback")
    ea = ds.get_pair("情境:用户分享", "respond").extra_attrs
    check("ambiguous 同样只计数", ea["ambiguous_count"] == 1
          and ea["positive_count"] == 1)

    # classify_outcome 产出四态 + ambiguous 判定
    import disposition_store as dsp
    out, _ = dsp.classify_outcome({"behavior": "respond", "user_text": "帮我倒杯水"},
                                  "你真烦，但是哈哈", None, 60)
    check("classify_outcome：抵触与积极词同现 → ambiguous", out == "ambiguous", out)
    out2, _ = dsp.classify_outcome({"behavior": "respond", "user_text": "聊聊别的"},
                                   "哦", None, 60)
    check("classify_outcome：无信号话题切换 → neutral", out2 == "neutral", out2)


# ═══════ T6 stable 不被单次负反馈抹掉 ═══════
def test_stable_survives_single_negative():
    print("\n── T6 一次负反馈不抹掉人格 ──")
    kg, ds = new_store()
    for i in range(5):
        ds.apply_outcome("情境:用户提问", "ask", "positive",
                         evidence_ref=f"好{i}", source="user_feedback", allow_create=True)
    pr = ds.get_pair("情境:用户提问", "ask")
    check("前置：已 stable", pr.extra_attrs["status"] == "stable")
    s0 = float(pr.extra_attrs["strength"])
    ds.apply_outcome("情境:用户提问", "ask", "negative",
                     evidence_ref="被怼了一次", source="user_feedback")
    pr = ds.get_pair("情境:用户提问", "ask")
    check("单次负反馈后 pair 仍存在且仍 stable",
          pr is not None and pr.extra_attrs["status"] == "stable")
    check("但强度下降（负证据真实生效，不是无视）",
          float(pr.extra_attrs["strength"]) < s0)
    check("negative_count 记录在案", pr.extra_attrs["negative_count"] == 1)

    # 反复负反馈（累计 4 个）→ 降级为 candidate（重新可塑），仍不被删除。
    # 降级之后若继续被拒绝，才按普通 candidate 的遗忘路径衰减——
    # 长期人格不会被"一次+几次的负反馈"直接清零（§七），但也不会永恒固化。
    for i in range(3):
        ds.apply_outcome("情境:用户提问", "ask", "negative",
                         evidence_ref=f"反复抵触{i}", source="user_feedback")
    pr = ds.get_pair("情境:用户提问", "ask")
    check("负证据累积 → 降级 candidate 而非删除",
          pr is not None and pr.extra_attrs["status"] == "candidate",
          str(pr and pr.extra_attrs["status"]))
    check("降级后强度有地板（≥0.15，不被同一手负证据连坐删除）",
          pr is not None and float(pr.extra_attrs["strength"]) >= 0.15,
          str(pr and pr.extra_attrs["strength"]))


# ═══════ T7 学习必须改变未来行为（before/after 同情境对比）═══════
def test_learning_changes_behavior():
    print("\n── T7 同情境学习前后行为对比 ──")
    kg = KnowledgeGraph()
    for nid in ("用户", "Minecraft", "钻石"):
        kg.add_node(Node(id=nid))
    ds = DispositionStore(kg)
    eng = FakeEngine(topk=[FN("Minecraft", 1.5)])

    def reset_inhibit():
        dd_mod._inhibit_until = 0.0

    # 用 information_statement 做 before/after：question 的拉力 0.85 会把
    # desire 顶到 cap，倾向学习的影响会被淹没；低拉力情境下增量可见。
    P = {"dialogue_act": "information_statement",
         "illocutionary_act": "assertive",
         "response_expectation": "medium"}

    def decide():
        reset_inhibit()
        return dialogue_decide(P, "我今天想到了一个东西", kg, eng,
                               ds.tendencies_for(["情境:用户陈述"]),
                               False, False, 3600.0, False)

    r0 = decide()
    t0 = ds.tendencies_for(["情境:用户陈述"])
    # 学习：同一情境 4 次正向 → 行为 respond 的倾向 stable
    for i in range(5):
        ds.apply_outcome("情境:用户陈述", "respond", "positive",
                         evidence_ref=f"经历{i}", source="user_feedback",
                         allow_create=True)
    r1 = decide()
    t1 = ds.tendencies_for(["情境:用户陈述"])

    check("学习前该情境无 respond 倾向",
          "respond" not in [x["behavior"] for x in t0], str(t0))
    check("学习后倾向出现且 stable 全强度参与（>candidate cap）",
          any(x["behavior"] == "respond" and x["status"] == "stable"
              and x["strength"] >= 0.49 for x in t1), str(t1))
    check("回应欲望因倾向而上升（行为竞争读图）",
          r1["desire"] > r0["desire"],
          f"desire {r0['desire']} → {r1['desire']}")
    # candidate 与 stable 的区分影响行为：cap 生效
    ds2_kg = KnowledgeGraph()
    ds2 = DispositionStore(ds2_kg)
    ds2.apply_outcome("情境:用户陈述", "share", "positive",
                      evidence_ref="一次", source="user_feedback", allow_create=True)
    t = ds2.tendencies_for(["情境:用户陈述"])
    check("单次经历后 candidate 被封顶（≤0.30 弱影响）",
          t and all(x["strength"] <= 0.30 for x in t), str(t))


# ═══════ T8 图是唯一长期真源 ═══════
def test_graph_is_single_source_of_truth():
    print("\n── T8 无平行持久层 ──")
    # 学习闭环新增文件不允许出现人格 JSON
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 两仓拆分后：认知模块在本仓根，app.py 在陪伴仓根。逐仓查找。
    sibling = os.path.join(os.path.dirname(root), "FAS-Companion")
    banned = ["personality_state.json", "preference_state.json",
              "learned_traits.json", "disposition_state.json"]
    hits = []
    for py in ("disposition_store.py", "self_model.py", "self_graph.py",
               "personality_baseline.py", "autonomy.py", "app.py"):
        path = os.path.join(root, py)
        if not os.path.exists(path):
            path = os.path.join(sibling, py)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for b in banned:
            if b in src:
                hits.append(f"{py}:{b}")
    check("代码中不存在平行长期人格 JSON", not hits, str(hits))

    kg, ds = new_store()
    ds.apply_experience("elaborate", "情境:用户陈述",
                        self_outcome="novelty", evidence_ref="x",
                        allow_create=True)
    # 序列化-反序列化往返：证据档案完整存活（图是真源）
    import tempfile as _tf
    p = os.path.join(_tf.gettempdir(), "fas_self_roundtrip.json")
    kg.save(p)
    from graph_model import KnowledgeGraph as KG
    kg2 = KG.load(p)
    pr = kg2.nodes.get(ds.pair_id("情境:用户陈述", "elaborate"))
    check("KG save/load 往返后证据与来源完整",
          pr is not None
          and pr.extra_attrs.get("sources") == {"self:novelty": 1}
          and pr.extra_attrs.get("positive_count") == 1,
          str(pr and pr.extra_attrs))
    os.remove(p)


# ═══════ T9 出厂先验播种幂等（不灌证据、来源正确）═══════
def test_baseline_bootstrap_idempotent():
    print("\n── T9 bootstrap 播种幂等 ──")
    from personality_baseline import PersonalityBaseline, BASELINE_PRIORS
    kg, ds = new_store()
    persona = PersonalityBaseline(kg, ds)
    persona.bootstrap()
    before = {pid: (ds.get_pair(c, b).extra_attrs["evidence_count"],
                    dict(ds.get_pair(c, b).extra_attrs.get("sources") or {}))
              for c, b, _ in BASELINE_PRIORS for pid in [ds.pair_id(c, b)]}
    # 模拟第二次启动：再次播种
    persona.bootstrap()
    persona.bootstrap()
    inflated = []
    for c, b, _ in BASELINE_PRIORS:
        ea = ds.get_pair(c, b).extra_attrs
        ev_now = ea["evidence_count"]
        src_now = ea.get("sources") or {}
        if ev_now != before[ds.pair_id(c, b)][0]:
            inflated.append(ds.pair_id(c, b))
        if "reflection" in src_now:
            inflated.append(ds.pair_id(c, b) + ":source误标")
    check("重复启动不再给先验 pair 灌证据", not inflated, str(inflated))
    src = ds.get_pair(*BASELINE_PRIORS[0][:2]).extra_attrs.get("sources")
    check("先验来源标注为 baseline（不冒 reflection）",
          src is not None and "baseline" in src, str(src))
    # 强度地板重贴也不重复记证据（先验被周期衰减压到地板下时）
    c0, b0, prior0 = BASELINE_PRIORS[0]
    p0 = ds.get_pair(c0, b0)
    count_before_refloor = p0.extra_attrs["evidence_count"]
    p0.extra_attrs["strength"] = 0.1        # 模拟衰减低于先验
    persona.bootstrap()
    check("强度低于先验时只抬地板，证据计数不变",
          p0.extra_attrs["evidence_count"] == count_before_refloor,
          f"{count_before_refloor} → {p0.extra_attrs['evidence_count']}")
    check("地板抬升生效", float(p0.extra_attrs["strength"]) >= prior0)


if __name__ == "__main__":
    test_first_experience_creates_candidate()
    test_single_experience_not_stable()
    test_repeated_experiences_promote()
    test_self_goal_success_source()
    test_neutral_not_positive()
    test_stable_survives_single_negative()
    test_learning_changes_behavior()
    test_graph_is_single_source_of_truth()
    test_baseline_bootstrap_idempotent()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAIL: {len(FAILURES)} 项未通过")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS: 学习闭环 8 项验收全部通过")
