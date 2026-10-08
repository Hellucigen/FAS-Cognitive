# -*- coding: utf-8 -*-
# probe_consolidation.py — 巩固审计只读探针(零生产修改)
# P1: Hebbian 连续强化轨迹 — 同一条 semantic_relation 边被扩散使用 10 次,
#     逐步记录 weight(期望: w0 → w0+10·ε·a,封顶 1.5·w0;不允许 1,1,1,1,5)。
# P2: CausalLearner 阈值链轨迹 — 同一动作签名+同一结果重复 10 次,
#     逐步记录 aggregation.support / hypothesis 事件 / promotion 事件
#     (期望: 第 3 次成假设,第 8 次晋升;J 协议为什么 0 的对照)。
# P3: J 协议真实核算 — 重放 J 的情节注入(1 种子),逐 checkpoint 转储
#     causal._aggregations 的每个 outcome support,回答 hypothesis=0 的
#     精确原因。
import json
import os
import sys

ROOT = r"E:\Project\Fascinator"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.chdir(ROOT)

OUT = []


def emit(s):
    OUT.append(s)
    print(s)


def probe1_hebbian_trajectory():
    emit("=" * 72)
    emit("P1: Hebbian 连续强化轨迹(同一边,扩散使用 10 次)")
    emit("=" * 72)
    from graph_model import KnowledgeGraph, Node, Edge
    from diffusion_engine import DiffusionEngine
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Src", weight=1.0, label="declarative-semantic",
                     graph_space="semantic"))
    kg.add_node(Node(id="Dst", weight=1.0, label="declarative-semantic",
                     graph_space="semantic"))
    kg.add_node(Node(id="Haru", graph_space="self"))
    kg.add_edge(Edge(src="Src", dst="Dst", relation="相关", weight=0.5,
                     relation_category="semantic_relation"))
    e = kg.get_edge("Src", "Dst", "相关")
    eng = DiffusionEngine(kg, {"beta_spread": 1.0})
    eng.name_to_node = dict(kg.nodes)
    w_traj = [round(float(e.weight), 6)]
    for i in range(10):
        eng.activate_from_inputs(["Src"], [], source_type="probe")
        # 生产/沙盒节拍语义:每 tick = decay_step(清 fire-once,开新回合)
        # + diffuse_step。直接连调 diffuse_step 会因 fire-once 不再发射
        # (第一版探针的平线即此伪影)。
        eng.decay_step()
        eng.diffuse_step()
        w_traj.append(round(float(e.weight), 6))
    emit("weight trajectory: %s" % w_traj)
    emit("deltas: %s" % [round(w_traj[i + 1] - w_traj[i], 6)
                          for i in range(len(w_traj) - 1)])
    w0, wmax = 0.5, 0.5 * 1.5
    emit("upper bound (w0*1.5) = %.3f | final w = %.4f | monotone increase: %s"
         % (wmax, w_traj[-1],
            all(w_traj[i + 1] >= w_traj[i] for i in range(len(w_traj) - 1))))
    emit("step-like jump detected: %s"
         % any(w_traj[i + 1] - w_traj[i] > 0.05 for i in range(len(w_traj) - 1)))


def probe2_causal_thresholds():
    emit("=" * 72)
    emit("P2: CausalLearner 阈值链(同一动作+同一结果 ×10)")
    emit("=" * 72)
    import time as _t
    import logging
    logging.disable(logging.WARNING)
    import config as C
    from graph_model import KnowledgeGraph
    from experience import (ExperienceTimeline, CausalLearner, make_event,
                            EVENT_ACTION, EVENT_SELF_STATE, EVENT_OBSERVATION)
    BASE = os.path.join(os.path.join(ROOT, "experiments",
                                     "architecture_diagnostic"), "_probe_causal")
    import shutil
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE, exist_ok=True)
    tl = ExperienceTimeline(path=os.path.join(BASE, "timeline.json"),
                            config=dict(C.DEFAULT_CONFIG))
    kg = KnowledgeGraph()
    lr = CausalLearner(tl, config=dict(C.DEFAULT_CONFIG), kg=kg)
    t = _t.time()
    traj = []
    for i in range(10):
        act = make_event(EVENT_ACTION, actor="self", subject="gather_resource",
                         content={"target": "oak_log"}, source="probe")
        act["ts"] = t + i * 100.0
        tl.append(act)
        lr.record_action(act)
        se = make_event(EVENT_SELF_STATE, actor="self",
                        subject="gather_resource",
                        content={"change": "succeeded"})
        se["ts"] = t + i * 100.0 + 1.0
        tl.append(se)
        oe = make_event(EVENT_OBSERVATION, actor="minecraft",
                        subject="inventory:oak_log",
                        content={"change": "count_increased"})
        oe["ts"] = t + i * 100.0 + 2.0
        tl.append(oe)
        lr._hypothesize("gather_resource(oak_log)")
        sup = 0
        conf = 0.0
        for o_sig, o in ((lr._aggregations.get("gather_resource(oak_log)")
                          or {}).get("outcomes") or {}).items():
            if "count_increased" in o_sig:
                sup = o["support"]
                from experience import CONF_SMOOTHING
                c = o.get("contra", 0)
                conf = round(sup / (sup + c + CONF_SMOOTHING), 3)
        traj.append({"i": i + 1, "support": sup, "conf": conf,
                     "hypotheses": len(lr._hypotheses),
                     "promoted": len(lr._promoted)})
    for t_ in traj:
        emit("  iter %2d: support=%d conf=%.3f hypotheses=%d promoted=%d"
             % (t_["i"], t_["support"], t_["conf"], t_["hypotheses"],
                t_["promoted"]))
    emit("  thresholds: MIN_SUPPORT=3 HYP_CONF=0.60 | KG_SUPPORT=5 "
         "KG_CONF=0.80 (c=0 → 晋升实际要求 s≥8)")
    emit("  promotion fired at iteration: %s"
         % next((t_["i"] for t_ in traj if t_["promoted"]), "never"))
    shutil.rmtree(BASE, ignore_errors=True)


def probe3_j_accounting():
    emit("=" * 72)
    emit("P3: J 协议真实核算(重放 1 种子,checkpoint 转储聚合内容)")
    emit("=" * 72)
    from sandbox_lab import build_stack, tick, Clock, flush_causal
    from run_exp_a2_reuse import plant_tree, TREES, ORE
    CYCLES = 100
    EPISODES = ("success_gather", "success_craft", "irrelevant_explore",
                "failure_gather", "env_change")
    st = build_stack(goal="oak_planks", label="J_probe", seed=0,
                     mc_gatherable=[ORE], writeback_on=True)
    for (x, z) in TREES:
        plant_tree(st.world, x, z, ORE)
    clk = Clock()
    from collections import Counter
    actions = Counter()
    for c in range(1, CYCLES + 1):
        if c % 10 == 1:
            ep = EPISODES[((c - 1) // 10) % len(EPISODES)]
            try:
                if ep == "success_gather":
                    plant_tree(st.world, 2, 2, ORE)
                    st.world._refresh_near()
                    st.loop.add_goal({"type": "obtain", "target": "oak_log",
                                      "source": "j_episode", "text": "J:采集橡木"})
                elif ep == "success_craft":
                    st.loop.add_goal({"type": "obtain", "target": "oak_planks",
                                      "source": "j_episode", "text": "J:合成木板"})
                elif ep == "irrelevant_explore":
                    st.loop.add_goal({"type": "obtain", "target": "cobblestone",
                                      "source": "j_episode", "text": "J:探索石头"})
                elif ep == "failure_gather":
                    st.loop.add_goal({"type": "obtain", "target": "diamond",
                                      "source": "j_episode", "text": "J:不可达目标"})
                elif ep == "env_change":
                    st.world.blocks = {k: v for k, v in st.world.blocks.items()
                                       if v != "oak_log"}
                    st.world._refresh_near()
            except Exception:
                pass
        tick(st, clk)
        try:
            am = st.am
            if am is not None:
                pass
        except Exception:
            pass
        # 统计时间轴上的 ACTION 事件签名(与 _finalize 的记账同源)
        try:
            from experience import EVENT_ACTION
            acts = [e for e in st.tl.events_since(0)
                    if e.get("event_type") == EVENT_ACTION]
            cnt = Counter(str(e.get("subject")) + "(" +
                          str((e.get("content") or {}).get("target")) + ")"
                          for e in acts)
            actions = cnt
        except Exception:
            pass
        if c in (10, 50, 100):
            flush_causal(st)
            emit("  cycle %d: distinct action sigs=%d | top: %s"
                 % (c, len(actions),
                    actions.most_common(6)))
            aggs = getattr(st.causal, "_aggregations", {}) or {}
            for a_sig, agg in list(aggs.items()):
                outs = {os_: (o["support"], o.get("contra", 0))
                        for os_, o in (agg.get("outcomes") or {}).items()}
                emit("    agg %s obs=%d outcomes(sup,contra)=%s"
                     % (a_sig[:34], agg.get("obs"), outs))
            emit("    hypotheses=%d promoted=%d"
                 % (len(getattr(st.causal, "_hypotheses", {}) or {}),
                    len(getattr(st.causal, "_promoted", []) or [])))
    emit("  (hebbian weights not tracked by J metrics — see P1)")


if __name__ == "__main__":
    probe1_hebbian_trajectory()
    probe2_causal_thresholds()
    probe3_j_accounting()
    with open(os.path.join(ROOT, "experiments", "architecture_diagnostic",
                           "CONSOLIDATION_PROBE_OUTPUT.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("saved CONSOLIDATION_PROBE_OUTPUT.txt")
