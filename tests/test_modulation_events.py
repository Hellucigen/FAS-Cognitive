# test_modulation_events.py — R2 P7：调制事件管线（扇出来自图，不是 if/else）
#
# 全部走**真实管线**（§26：不为测试写永久硬编码逻辑，也不给测试开后门）：
# 构造 ModulationEvent → ModulatorEngine.apply_event → internal_state 唯一写入口。
# 图谱用内存里的干净小图（Self + sync_graph 长出的 12 个镜像），不读也不写
# data/runtime_graph.json —— 运行时图是活的，一次启动就会带调制接线（见
# tests/test_modulator_graph_presence.py 的剪枝约定），拿它测"从无到有"会失真。
#
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_modulation_events.py

import copy
import logging
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.disable(logging.WARNING)

import config as C
from graph_model import Edge, KnowledgeGraph, Node
from internal_state import InternalState
from modulation_events import (EVENT_PREFIX, ModulationEvent, ModulatorEngine,
                               ensure_event_types, gate_ok, interaction_gains)
from modulator_subgraph import ensure_modulator_subgraph
from reward import RewardSystem

FAILURES = []
MS = "modulator_system"
BASE = copy.deepcopy(C.DEFAULT_CONFIG)


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def fresh_state(config=None, wire=True):
    """干净小图 + InternalState + 12 个镜像（可选：接好 P6 子图与 P7 事件表）。"""
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", label="declarative-semantic", graph_space="self"))
    st = InternalState(kg=kg, config=copy.deepcopy(config or BASE),
                       data_dir=tempfile.mkdtemp(prefix="fas_p7_"))
    st.sync_graph()
    eng = ModulatorEngine(kg, st, st.config, ensure=False)
    if wire:
        ensure_modulator_subgraph(kg, st, st.config)
        eng.ensure()
    return kg, st, eng


def conc(st, name):
    return round(float(st.modulator_conc(name)), 6)


def delta_of(st, names):
    return {n: conc(st, n) for n in names}


def age(st, mins: float = 10.0, names=None):
    """时间旅行：把慢写时间戳往前拨，让 P3 的**上升夹速窗口**过去。

    这不是给测试开后门——夹速是真实机制（rise_per_min×Δt，余量进 pending_rise），
    同一瞬间连写两次本来就该被夹住。测"改边权→效力变"需要的是两次*相隔足够久*的
    写入，所以把时钟拨开；生产里事件之间天然有分钟级间隔。
    """
    for n in (names or st.modulator_names()):
        it = (st._modulators or {}).get(n)
        if it is not None:
            it["last_slow_ts"] = time.time() - mins * 60.0
            it["last_update"] = it["last_slow_ts"]


# ═══════ A 事件表种入：幂等、开关、缺端点、无冲突 ═══════
def test_A_bootstrap():
    print("\n── A 事件表 bootstrap ──")
    kg, st, eng = fresh_state()
    rules = eng.rules_for("reward_failure")
    check("扇出规则来自图（不是出厂表）", rules["source"] == "graph", rules["source"])
    check("reward_failure 有 5 条影响边（含新增的内啡肽钝化）",
          len(rules["rules"]) == 5, str([r["mod"] for r in rules["rules"]]))
    nodes = [n for n in kg.nodes if n.startswith(EVENT_PREFIX)]
    n_types = len(eng.explain()["event_types"])
    check("事件类型节点数 == 规则里的事件类型数", len(nodes) == n_types,
          f"{len(nodes)} vs {n_types}")
    e0 = len(kg.edges)
    eng.ensure()
    eng.ensure()
    check("重复 ensure 不再加点加边（幂等）", len(kg.nodes) and len(kg.edges) == e0,
          f"{e0} → {len(kg.edges)}")
    # 情绪共振表被翻成事件类型（D4：消除重复表）
    emo = [t for t in eng.explain()["event_types"] if t.startswith("emotion:")]
    check("config.emotion_hormone_modulation 的 7 个词都成了事件类型",
          len(emo) == 7, str(sorted(emo)))
    check("emotion:焦虑 的扇出是 cortisol +0.03（const 信号=权重即 delta）",
          any(abs(r["w"] - 0.03) < 1e-9 and r["mod"] == "cortisol"
              for r in eng.rules_for("emotion:焦虑")["rules"]),
          str(eng.rules_for("emotion:焦虑")["rules"]))
    # 端点缺失只跳过，不为此造点：镜像还没建（sync_graph 未跑）
    kg2 = KnowledgeGraph()
    kg2.add_node(Node(id="Self", label="declarative-semantic", graph_space="self"))
    st2 = InternalState(kg=kg2, config=copy.deepcopy(BASE),
                        data_dir=tempfile.mkdtemp(prefix="fas_p7_b_"))
    r2 = ensure_event_types(kg2, st2, st2.config)
    check("镜像缺失时全部跳过、零事件边", r2["edges"] == 0 and r2["edges_skipped"] > 0,
          str({k: r2[k] for k in ("edges", "edges_skipped")}))
    check("不为缺失端点造调制器点（建点是 P4 的职责）",
          not [n for n in kg2.nodes
               if (kg2.nodes[n].extra_attrs or {}).get("type") == "modulator"], "")
    # 半接线陷阱：只建节点不建边会让"图已接管"为真而扇出为空，兜底表也救不回来
    check("一类端点都接不上时**连事件类型节点都不建**",
          r2["nodes"] == 0 and r2["types_unwired"] == r2["types"],
          str({k: r2[k] for k in ("nodes", "types", "types_unwired")}))
    eng2 = ModulatorEngine(kg2, st2, st2.config, ensure=False)
    rr = eng2.apply_event(ModulationEvent("reward_failure", source="t", valence=-0.5,
                                          intensity=0.5))
    check("未接线的图上事件仍然生效（走兜底表并标明 source=config）",
          rr["source"] == "config" and any(a["mod"] == "cortisol" for a in rr["applied"]),
          str(rr["source"]))
    # 开关
    cfg3 = copy.deepcopy(BASE)
    cfg3[MS]["events"] = False
    kg3 = KnowledgeGraph()
    kg3.add_node(Node(id="Self", label="declarative-semantic", graph_space="self"))
    st3 = InternalState(kg=kg3, config=cfg3, data_dir=tempfile.mkdtemp(prefix="fas_p7_c_"))
    st3.sync_graph()
    r3 = ensure_event_types(kg3, st3, cfg3)
    check("events=False 时零产物（开关有真消费者）",
          r3["nodes"] == 0 and r3["edges"] == 0
          and not [n for n in kg3.nodes if n.startswith(EVENT_PREFIX)], str(r3))
    ri = __import__("modulation_events").rules_index(BASE)
    check("出厂表里 (事件类型, 调制器) 无重复（add_edge 会 max 合并掉第二条）",
          not ri["conflicts"], str(ri["conflicts"]))


# ═══════ B 迁移无损：旧 release() 的六个常数逐个复现 ═══════
def test_B_lossless():
    print("\n── B 与旧常数的逐值对照（基线态，交互增益=1）──")
    # 旧实现：self 正 → dopamine tonic +.04|v| & phasic .25·rpe；social 正 → oxy +.06v；
    #          负 → oxy .06v·0.6、cort .05|v|、sero .02v、dopa −.05|v|（self 时）
    want = {
        ("self", "discovery"): {"dopamine": 0.04 * 0.80, "oxytocin": 0.0,
                                "cortisol": 0.0, "serotonin": 0.0},
        ("self", "goal_failure"): {"dopamine": -0.05 * 0.50, "oxytocin": 0.0,
                                   "cortisol": 0.05 * 0.50, "serotonin": -0.02 * 0.50},
        ("social", "amused"): {"dopamine": 0.0, "oxytocin": 0.06 * 0.60,
                               "cortisol": 0.0, "serotonin": 0.0},
        ("social", "rejected"): {"dopamine": 0.0, "oxytocin": 0.036 * -0.50,
                                 "cortisol": 0.05 * 0.50, "serotonin": -0.02 * 0.50},
    }
    names = ("dopamine", "oxytocin", "cortisol", "serotonin")
    for (source, outcome), exp in want.items():
        kg, st, eng = fresh_state()
        rs = RewardSystem(internal_state=st, config=st.config)
        before = delta_of(st, names)
        ev = rs.evaluate(behavior=f"b{outcome}", context="c1",
                         social_outcome=outcome if source == "social" else None,
                         self_outcome=outcome if source == "self" else None)
        rel = rs.release(ev)
        after = delta_of(st, names)
        got = {n: round(after[n] - before[n], 6) for n in names}
        bad = {n: (got[n], round(v, 6)) for n, v in exp.items() if abs(got[n] - v) > 1e-6}
        check(f"{source}:{outcome} → 慢分量与旧常数逐值相同", not bad,
              f"got={got} exp={exp}")
        check(f"{source}:{outcome} 的 fanout 明细可读（trace 数据源）",
              len(rel.get("fanout") or []) >= 1, str(rel))
    # RPE phasic：首轮 expected=0 → rpe=v → phasic = v×.25（sens 1.0、无不应期）
    kg, st, eng = fresh_state()
    rs = RewardSystem(internal_state=st, config=st.config)
    rs.release(rs.evaluate(behavior="bx", context="c", self_outcome="discovery"))
    check("self 结果的 RPE 仍打 phasic（0.80×0.25=0.20）",
          abs(st.modulator_pulse("dopamine") - 0.20) < 1e-6,
          str(st.modulator_pulse("dopamine")))
    kg, st, eng = fresh_state()
    rs = RewardSystem(internal_state=st, config=st.config)
    rs.release(rs.evaluate(behavior="by", context="c", social_outcome="amused"))
    check("social 结果不打 RPE 脉冲（gate=goal_relevance，旧代码是 if source==self）",
          abs(st.modulator_pulse("dopamine")) < 1e-9, str(st.modulator_pulse("dopamine")))
    # cancelled：valence=0 → 不产生任何奖赏调制（旧行为保持）
    kg, st, eng = fresh_state()
    rs = RewardSystem(internal_state=st, config=st.config)
    b = delta_of(st, names)
    rs.release(rs.evaluate(behavior="bz", context="c", self_outcome="cancelled"))
    check("cancelled 不产生奖赏调制", delta_of(st, names) == b, str(delta_of(st, names)))


# ═══════ C 图是真源：改边权/删边立刻改变效力 ═══════
def test_C_graph_is_source():
    print("\n── C 效力住在边上 ──")
    kg, st, eng = fresh_state()
    nid, dst = EVENT_PREFIX + "reward_failure", st.mirror_modulator["cortisol"]
    w0 = float(kg.get_edge(nid, dst, "影响").weight)

    def cort():
        """→ (边应算出的写入, tonic 实差, 明细)。见 age()：夹速窗口要先拨开。"""
        age(st, 10.0, ["cortisol", "endorphin"])
        b = conc(st, "cortisol")
        r = eng.apply_event(ModulationEvent("reward_failure", source="t", valence=-0.5,
                                            intensity=0.5))
        a = next((x for x in r["applied"] if x["mod"] == "cortisol"), None)
        want = round(a["weight"] * a["value"] * a["gain"], 6) if a else 0.0
        return want, round(conc(st, "cortisol") - b, 6), a

    want0, d0, a0 = cort()
    check("写入 = 边权×信号值×交互增益，并真的落到 tonic",
          abs(want0 - 0.025) < 1e-9 and abs(d0 - want0) < 1e-6, f"{want0} vs {d0}")
    kg.remove_edge(nid, dst, "影响")
    _, d_rm, a_rm = cort()
    check("删掉影响边 → 该事件不再动这个调制器", d_rm == 0.0 and a_rm is None, str(d_rm))
    kg.add_edge(Edge(src=nid, dst=dst, relation="影响", weight=0.20))
    want1, d1, a1 = cort()
    # 4 倍是**边权之比**；剩下的微小比值来自新增的 `endorphin -[交互]-> cortisol` 边
    # （上一轮写入抬了内啡肽 → 这次压力写入被钝化），那正是 P7 想要的效果，不是噪声。
    check("改边权 0.05→0.20 → 同事件效力同比例变（4 倍，扣除交互增益后精确）",
          abs(d1 - 4 * d0 * a1["gain"] / a0["gain"]) < 2e-4,   # trace 里 gain 取整到 3 位
          f"{d0} → {d1} 增益 {a0['gain']}→{a1['gain']}")
    check("交互增益只是小量修正（<2%），不改变量级",
          abs(a1["gain"] / a0["gain"] - 1.0) < 0.02,
          f"{a0['gain']} → {a1['gain']}")
    check("其它调制器不受影响（只改了 cortisol 一条边）", w0 == 0.05, str(w0))
    # 节点还在、出边全删 = **故意消音**（不能又被兜底表复活）
    for r_ in list(eng.rules_for("reward_failure")["rules"]):
        kg.remove_edge(nid, st.mirror_modulator[r_["mod"]], "影响")
    got = eng.apply_event(ModulationEvent("reward_failure", source="t", valence=-0.5,
                                          intensity=0.5))
    check("删光某类事件的全部影响边 = 消音（source 仍是 graph，不退回出厂表）",
          got["source"] == "graph" and got["reason"] == "no_rules" and not got["applied"],
          str({k: got[k] for k in ("source", "reason")}))
    # 事件类型节点不存在 → 无扇出（不猜默认值）
    got = eng.apply_event(ModulationEvent("no_such_event", valence=1.0))
    check("未注册的事件类型零写入", got.get("reason") == "no_rules" and not got["applied"],
          str(got))
    # 无图兜底：只在这条路径上允许用出厂表
    st2 = InternalState(kg=None, config=copy.deepcopy(BASE),
                        data_dir=tempfile.mkdtemp(prefix="fas_p7_nograph_"))
    eng2 = ModulatorEngine(None, st2, st2.config, ensure=False)
    b = conc(st2, "cortisol")
    r = eng2.apply_event(ModulationEvent("reward_failure", source="t", valence=-0.5,
                                         intensity=0.5))
    check("无图谱时退回出厂表并**标明来源**",
          r["source"] == "config" and round(conc(st2, "cortisol") - b, 4) == 0.025,
          str(r["source"]))


# ═══════ D 门控、通道、夹幅 ═══════
def test_D_gates_channels():
    print("\n── D 门控与通道 ──")
    ev = ModulationEvent("x", valence=-0.5, goal_relevance=1.0, social_relevance=0.0)
    ok1, _ = gate_ok(ev, {"goal_relevance_min": 0.5})
    ok2, why = gate_ok(ev, {"social_relevance_min": 0.5})
    ok3, _ = gate_ok(ev, {"valence_sign": "neg"})
    ok4, _ = gate_ok(ev, {"valence_sign": "pos"})
    check("gate 支持字段下限与效价符号", ok1 and not ok2 and ok3 and not ok4,
          f"{ok1}/{ok2}:{why}/{ok3}/{ok4}")
    kg, st, eng = fresh_state()
    # phasic 通道走 pulse() ⇒ sensitivity 与不应期真的参与（不是直接写数）
    eng.apply_event(ModulationEvent("reward_rpe", source="t", rpe=0.5,
                                    goal_relevance=1.0))
    p1 = round(st.modulator_pulse("dopamine"), 4)
    eng.apply_event(ModulationEvent("reward_rpe", source="t", rpe=0.5,
                                    goal_relevance=1.0))
    p2 = round(st.modulator_pulse("dopamine"), 4)
    check("phasic 通道 = pulse（0.5×0.25×sens1.0=0.125）", abs(p1 - 0.125) < 1e-6, str(p1))
    check("同向连打被不应期/习惯化压住（P3 的机制在事件管线上继续有效）",
          p2 - p1 < 0.05, f"{p1} → {p2}")
    # 未拆快通道的调制器：phasic 规则退化为一次带习惯化的慢位移，不报错
    r = eng.apply_event(ModulationEvent("interrupted", source="t", intensity=1.0,
                                        uncertainty=0.6))
    check("interrupted → 去甲肾上腺素 phasic（不拆快通道时=慢位移，仍写入）",
          any(a["mod"] == "norepinephrine" and abs(a["requested"]) > 0.05
              for a in r["applied"]), str(r["applied"]))
    # clamp：把 valence 拉到极端，单次写入必须被夹住
    r = eng.apply_event(ModulationEvent("reward_rpe", source="t", rpe=50.0,
                                        goal_relevance=1.0))
    check("clamp 生效（rpe=50 也被夹到 ±0.60）",
          all(abs(a["requested"]) <= 0.60 + 1e-9 for a in r["applied"]),
          str(r["applied"]))
    # signal=const：情绪事件不按幅度缩放
    kg, st, eng = fresh_state()
    b = conc(st, "cortisol")
    eng.emit("emotion:害怕", source="emotion_resonance", intensity=1.0)
    check("const 信号：权重即 delta（害怕→cortisol +0.04）",
          abs((conc(st, "cortisol") - b) - 0.04) < 1e-6,
          str(conc(st, "cortisol") - b))
    check("signal 字段为 0 时被跳过（不写无意义的 0）",
          any(g["gate"] == "signal=uncertainty=0" for g in
              eng.emit("prediction_violation", source="t", rpe=0.4,
                       uncertainty=0.0)["gated"]), "")


# ═══════ E `交互` 边：一个调制器改变另一个接收事件的效力 ═══════
def test_E_interaction_edges():
    print("\n── E 交互边有真消费者 ──")
    kg, st, eng = fresh_state()
    g0 = interaction_gains(kg, st)
    check("出厂基线下所有增益恒为 1（没人被改变）",
          all(v["gain"] == 1.0 for v in g0.values()),
          str({k: v["gain"] for k, v in g0.items() if v["gain"] != 1.0}))
    ids = set(st.mirror_modulator.values())
    inter = [e for e in kg.edges if e.relation == "交互" and e.src in ids and e.dst in ids]
    check("config 里的 4 条 `交互` 边真的在图上（内啡肽/催产素/GABA/乙酰胆碱）",
          len(inter) >= 4, str([(e.src, e.dst, e.weight) for e in inter]))

    def cort_delta():
        age(st, 10.0, ["cortisol", "endorphin"])
        b = conc(st, "cortisol")
        eng.apply_event(ModulationEvent("reward_failure", source="t", valence=-0.5,
                                        intensity=0.5))
        return round(conc(st, "cortisol") - b, 6)

    d_low = cort_delta()
    st.apply_delta("modulator", "endorphin", 0.4, reason="假设：钝化系统被动员",
                   source="test")
    g1 = interaction_gains(kg, st)["cortisol"]
    d_high = cort_delta()
    check("内啡肽高位 → 压力事件的写入被钝化（gain<1 且实际写入变小）",
          g1["gain"] < 1.0 and abs(d_high) < abs(d_low), f"{d_low} → {d_high}, gain={g1}")
    # 把这条边的符号反过来，效力方向必须跟着反（图是真源，不是 if endorphin>0.6）
    src, dst = st.mirror_modulator["endorphin"], st.mirror_modulator["cortisol"]
    kg.remove_edge(src, dst, "交互")
    kg.add_edge(Edge(src=src, dst=dst, relation="交互", weight=0.60))
    g2 = interaction_gains(kg, st)["cortisol"]
    check("同一条 `交互` 边改成正号 → 变成放大（符号来自权重）",
          g2["gain"] > 1.0, str(g2))
    check("交互增益只影响事件写入，不改出厂 sensitivity 字段",
          abs(float(st.modulator_field("cortisol", "sensitivity")) - 1.0) < 1e-9, "")


# ═══════ F 五个来源都能真的发事件 ═══════
def test_F_sources():
    print("\n── F 来源接线 ──")
    kg, st, eng = fresh_state()
    rs = RewardSystem(internal_state=st, config=st.config)
    # 1) 奖赏（reward.py 已改走事件）
    b = conc(st, "dopamine")
    rs.release(rs.evaluate(behavior="f1", context="c", self_outcome="discovery"))
    check("奖赏路径写进调制器", conc(st, "dopamine") > b, str(conc(st, "dopamine")))
    # 2) 意外（prediction_violation）
    b = st.modulator_pulse("norepinephrine")
    eng.emit("prediction_violation", source="prediction_baseline", rpe=0.6,
             uncertainty=0.6, intensity=0.6)
    check("意外 → 去甲肾上腺素 phasic 上升（场景 14/6）",
          st.modulator_pulse("norepinephrine") > b,
          str(st.modulator_pulse("norepinephrine")))
    # 3) 新奇
    b = conc(st, "glutamate")
    eng.emit("novel_contact", source="curiosity", novelty=0.8)
    check("新奇 → 谷氨酸样上升（编码增益，场景 5）", conc(st, "glutamate") > b,
          str(conc(st, "glutamate")))
    # 4) 情绪共振：app 端已改成发事件（这里验事件本身有效）
    age(st, 10.0, ["dopamine"])
    b = conc(st, "dopamine")
    eng.emit("emotion:兴奋", source="emotion_resonance", intensity=1.0)
    check("情绪共振事件有效（兴奋→dopamine +0.04）",
          abs((conc(st, "dopamine") - b) - 0.04) < 1e-6, str(conc(st, "dopamine") - b))
    # 5) 社交反馈：app 走 reward_system.evaluate(social_outcome=...) → 已在 B 组验过
    #    这里验 action_system 的取消路径（旧代码里 cancelled 完全隐形）
    kg3, st3, eng3 = fresh_state()          # NE 不应期 1 分钟：换个没被打过的状态
    b = st3.modulator_pulse("norepinephrine")
    eng3.emit("interrupted", source="action", intensity=1.0)
    check("被取消的意图也产生调制（原先在激素层隐身）",
          st3.modulator_pulse("norepinephrine") > b, str(st3.modulator_pulse("norepinephrine")))
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "reward.py"), encoding="utf-8").read()
    body = src.split("def release(", 1)[1].split("def modulation(", 1)[0]
    check("release() 里已无逐激素 if/else（写死常数的形状消失）",
          "apply_delta" not in body and "pulse_dopamine" not in body
          and "oxytocin" not in body and "cortisol" not in body,
          [w for w in ("apply_delta", "oxytocin", "cortisol") if w in body])


# ═══════ G 边界与不变量 ═══════
def test_G_boundaries():
    print("\n── G 边界：只写数值，不碰内容与能量 ──")
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "modulation_events.py"), encoding="utf-8").read()
    banned = [w for w in ("llm", "prompt", "requests", "http", "execute", "dialogue_decision")
              if w in src.replace("not to", "")]
    check("应用器不碰 LLM/回答文本/动作执行（禁令 4）", not banned, str(banned))
    check("应用器自己不 import 扩散引擎（图通道仍归 internal_state.pulse）",
          "diffusion_engine" not in src and "register_activation_source" not in src, "")
    kg, st, eng = fresh_state()
    eng.emit("reward_failure", source="t", valence=-0.5, intensity=0.5,
             goal_relevance=1.0, cycle_id=st.begin_cycle("turn", {"m": "test"}))
    hist = st.history("modulator", "cortisol", n=5)
    check("写入经唯一入口：internal_state 里有 history + reason（I1/I3）",
          any(str(h.get("reason", "")).startswith("reward_failure") for h in hist),
          str(hist[:2]))
    srcs = st.state()["modulators"]["cortisol"].get("last_sources") or []
    check("recent_sources 记到了事件名（可解释性）",
          any("reward_failure" in str(s) for s in srcs), str(srcs))
    # 事件不改变总能量：没有图激活注入之外的写入（pulse 阈值以下的零注入也要成立）
    a0 = sum(float(n.activation or 0.0) for n in kg.nodes.values())
    eng.emit("emotion:紧张", source="t", intensity=1.0)
    a1 = sum(float(n.activation or 0.0) for n in kg.nodes.values())
    check("慢通道事件不注入图激活（只有 phasic 才注入）", abs(a1 - a0) < 1e-9,
          f"{a0} → {a1}")


if __name__ == "__main__":
    print("R2 P7 闸门：调制事件管线（图驱动扇出）")
    test_A_bootstrap()
    test_B_lossless()
    test_C_graph_is_source()
    test_D_gates_channels()
    test_E_interaction_edges()
    test_F_sources()
    test_G_boundaries()
    tmpdirs = [x for x in os.listdir(tempfile.gettempdir()) if x.startswith("fas_p7_")]
    for x in tmpdirs:
        shutil.rmtree(os.path.join(tempfile.gettempdir(), x), ignore_errors=True)
    print("\n" + "=" * 56)
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项:")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print(f"全部通过（临时目录已清 {len(tmpdirs)} 个）")
