# -*- coding: utf-8 -*-
# audit_probes.py — 架构诊断的三个确定性探针(零 LLM,可重复)
# P-C: C 实验 P3 payload 复现(含零经历对照) — 定位 C 的驱动机制
# P-E: E 失败后 payload + 失败证据节点激活 + 负边计数
# P-I: I3 测试 step-0 payload + Layer1-6 检查
# 用法: python audit_probes.py > PROBE_OUTPUT.txt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "capability_gap_v2"))
os.chdir(ROOT)

import run_experiment as R  # noqa: E402
import run_exp_routing_v2 as v2  # noqa: E402

H = R.H
OUT = []


def emit(s):
    OUT.append(s)
    print(s)


def payload_of(fc, goal, edges=True):
    sel = fc.focus(k=int(getattr(fc, "ctx_k", 8) or 8))
    edges_l = []
    if edges:
        ids = {n["id"] for n in sel}
        edges_l = [f"{e.src}-{e.relation}->{e.dst}" for e in fc.kg.edges
                   if getattr(e, "src", None) in ids and getattr(e, "dst", None) in ids][:8]
    demand, gap, routing, _m = fc.demand_block(goal)
    return v2.serialize_context("fas", "full", sel, demand, gap, routing,
                                edges=edges_l,
                                activation_summary={"n_selected": len(sel),
                                                    **fc.telemetry()})


def probe_C():
    emit("=" * 70)
    emit("P-C: C 实验 P3 payload(有经历)与零经历对照")
    emit("=" * 70)
    w = R.build_world()
    kind, fc, hist = R.make_condition("fas", w, ())
    ex = R.fresh_exec(w)
    H.script_practice_A(ex, (hist, None), fc)
    ok, det = ex.execute("gather_dirt", "dirt")
    fc.ingest(H.observe(w, ex), "gather_dirt", det, ())
    fc.step_dynamics()
    fc.ingest(H.observe(w, ex), None, "init", ())
    fc.step_dynamics()
    _, ctx = payload_of(fc, "You have free choice of actions.")
    emit("--- with P1 experience ---")
    emit(ctx)
    # 零经历对照
    w2 = R.build_world()
    _, fc2, _ = R.make_condition("fas", w2, ())
    ex2 = R.fresh_exec(w2)
    fc2.ingest(H.observe(w2, ex2), None, "init", ())
    fc2.step_dynamics()
    _, ctx2 = payload_of(fc2, "You have free choice of actions.")
    emit("--- ZERO experience control ---")
    emit(ctx2)
    emit("gate(with exp): %s | gate(zero exp): %s" % (
        sorted(H.gate_from_context(ctx)), sorted(H.gate_from_context(ctx2))))


def probe_E():
    emit("=" * 70)
    emit("P-E: E 失败后 payload + 失败证据节点激活 + 负边计数")
    emit("=" * 70)
    w = R.build_world(oak=3, birch=0)
    w.put_block(4, 65, 2, "iron_ore")
    w._refresh_near()
    kind, fc, hist = R.make_condition("fas", w, ())
    ex = R.fresh_exec(w)
    fc.ingest(H.observe(w, ex), None, "init", ())
    fc.step_dynamics()
    ok, det = ex.execute("gather_iron_ore", "iron_ore")
    emit("action result: %s %s" % (ok, det))
    fc.ingest(H.observe(w, ex), "gather_iron_ore", det, ())
    fc.step_dynamics()
    _, ctx = payload_of(fc, "obtain raw_iron")
    emit(ctx)
    for n in fc.kg.nodes:
        if "tool_missing" in n or "gather_iron" in n:
            emit("  node %-52s act=%.3f" % (
                n, float(getattr(fc.kg.nodes[n], "activation", 0) or 0)))
    neg = [1 for e in fc.kg.edges if float(getattr(e, "weight", 0) or 0) < 0]
    emit("negative edges in graph: %d" % len(neg))


def probe_I():
    emit("=" * 70)
    emit("P-I: I3 测试 step-0 payload + Layer1-6 检查")
    emit("=" * 70)
    w = R.build_world()
    kind, fc, hist = R.make_condition("fas", w, R.EXCL_I)
    ex = R.fresh_exec(w)
    H.script_practice_A(ex, (hist, None), fc)
    H.script_practice_B(ex, (hist, None), fc)
    w2 = R.build_world(**R.WORLD_SPEC)
    ex2 = R.fresh_exec(w2)
    fc.ingest(H.observe(w2, ex2), None, "init", ())
    fc.step_dynamics()
    _, ctx = payload_of(fc, "obtain oak_fence")
    emit(ctx)
    emit("L1 knowledge nodes: %s" % [n for n in fc.kg.nodes
                                     if "oak_planks" in n or "stick" in n][:8])
    emit("L2 action->result edges: %s" % [(e.src[:26], e.dst[:24])
                                          for e in fc.kg.edges
                                          if str(getattr(e, "src", "")).startswith("动作:")][:6])
    emit("L2 node.created present: %s | edge.created present: %s" % (
        any(getattr(n, "created", None) for n in list(fc.kg.nodes)[:5]),
        any(getattr(e, "created", None) for e in list(fc.kg.edges)[:5])))
    emit("L6 episode identity anywhere: %s" % any(
        "episode" in str(getattr(n, "extra_attrs", None) or {}) for n in fc.kg.nodes))


if __name__ == "__main__":
    probe_C()
    probe_E()
    probe_I()
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "PROBE_OUTPUT.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
