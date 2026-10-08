# -*- coding: utf-8 -*-
# run_exp.py — Persistent State Advantage 实验(三阶段 × 8 条件)
# Phase 1 零 LLM 脚本化经历(全条件同一事件流);Phase 2 上下文移除;
# Phase 3 LLM 行为测试(T1 sanity / T2 主任务 / T3 failure-informed)。
import json
import os
import sys
import time
import hashlib

ROOT = r"E:\Project\Fascinator"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "capability_gap_v2"))
os.chdir(ROOT)

import run_experiment as R  # noqa: E402
import harness as H  # noqa: E402
import run_exp_routing_v2 as v2  # noqa: E402

HERE = os.path.join(ROOT, "experiments", "persistent_state_advantage")
SEEDS = list(range(10))
CHARS_PER_TOKEN = 4.0
HIST_BUDGETS = {"B1-h128": 128, "B1-h512": 512, "B1-h2048": 2048}


def tok(text):
    return int(len(text) / CHARS_PER_TOKEN)


def slice_history(lines, budget_tokens):
    if budget_tokens is None:
        return "\n".join(lines)
    keep, total = [], 0
    budget_chars = int(budget_tokens * CHARS_PER_TOKEN)
    for l in reversed(lines):
        if total + len(l) > budget_chars:
            break
        keep.insert(0, l)
        total += len(l)
    return "\n".join(keep) if keep else "(history empty after truncation)"


def edge_weights(kg):
    return {"%s->%s(%s)" % (e.src, e.dst, e.relation): round(float(e.weight), 5)
            for e in kg.edges}


def graph_state(kg):
    if kg is None:
        return {"nodes": 0, "edges": 0, "hash": ""}
    h = hashlib.sha256(json.dumps(
        {"n": sorted(kg.nodes.keys()),
         "e": sorted((e.src, e.dst, e.relation, round(float(e.weight), 5))
                     for e in kg.edges)},
        ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]
    return {"nodes": len(kg.nodes), "edges": len(kg.edges), "hash": h}


def make_fas(world, variant):
    """variant: full / reset / sham(装配差异见 DESIGN)
    exclude_recipes=("oak_planks",):生产先验闭包不播种该配方——
    T2 的配方知识必须来自 Phase-1 经历,否则 FAS-full/reset 都能靠
    先验闭包解锁,H1 无法检验持久状态贡献(与 campaign A 同一教训)。"""
    fc = H.CampaignFASContext(world, use_diffusion=True, use_demand=True,
                              exclude_recipes=("oak_planks",),
                              writeback=(variant != "sham"))
    fc.ef_context = True  # 生产事件框架负极性(E2 修复,装配层)
    return fc


# ── Phase 1:脚本化经历(零 LLM)──────────────────────────────────────
def phase1(w, fc, hist):
    ex = R.fresh_exec(w)
    w.add_item("bread", 2)
    ex.hunger = 8
    w.put_block(4, 65, 2, "iron_ore")
    w.put_block(5, 65, 2, "iron_ore")
    w._refresh_near()
    steps = [("eat_bread", None)]
    for _ in range(2):
        steps.append(("gather_oak_log", "oak_log"))
    steps.append(("craft_oak_planks", "oak_planks"))
    for _ in range(3):
        steps.append(("gather_iron_ore", "iron_ore"))
    for _ in range(4):
        steps.append(("explore_north", None))
    for _ in range(2):
        steps.append(("gather_dirt", "dirt"))
    weights_before = edge_weights(fc.kg) if fc is not None else {}
    lines = []
    for act, obj in steps:
        obs = H.observe(w, ex)
        ok, detail = ex.execute(act, obj)
        line = "obs: %s | action: %s | result: %s" % (obs, act, detail)
        lines.append(line)
        if hist is not None:
            hist.lines.append(line)
        if fc is not None:
            fc.ingest(obs, act, detail, ())
            fc.step_dynamics()
    return {"stream": lines, "inv": dict(w.inv_map()),
            "hunger": ex.hunger,
            "weights_before": weights_before,
            "weights_after": edge_weights(fc.kg) if fc is not None else {}}


def context_for(cond, meta, goal):
    stream = meta["stream"]
    if cond in HIST_BUDGETS:
        return ("Past experience stream (most recent %d tokens):\n"
                % HIST_BUDGETS[cond]) + slice_history(stream,
                                                      HIST_BUDGETS[cond])
    if cond == "B2-rag":
        from embedding_manager import EmbeddingProvider
        import numpy as np
        emb = EmbeddingProvider()
        if not stream:
            return "(no experiences)"
        M_ = emb.encode(stream)
        qv = emb.encode_single(goal)
        sims = M_ @ (qv / (np.linalg.norm(qv) + 1e-9))
        idx = sorted(range(len(stream)), key=lambda i: -float(sims[i]))[:6]
        return "Relevant past experiences:\n" + "\n".join(
            stream[i] for i in idx)
    return "(no memory of past experiences)"


# ── Phase 3:行为测试 ─────────────────────────────────────────────────
def run_task(task, cond, seed, llm, holder):
    fc = holder["fc"]
    meta = holder["phase1"]
    kind_fas = cond.startswith("FAS")
    spec = R.WORLD_SPEC
    if task == "T1":
        w = R.build_world(oak=0, birch=0)
        w.add_item("bread", 3)
        ex = R.fresh_exec(w)
        ex.hunger = 8
        goal = "Manage your hunger before it becomes critical."
        facts = "Hunger is a survival resource. Choose actions from the menu."
        budget = 6
        success_fn = lambda: ex.hunger >= 14
        success_item = None
        track_fails = False
    elif task == "T2":
        w = R.build_world(oak=3, birch=3)
        ex = R.fresh_exec(w)
        goal = "obtain oak_planks"
        facts = R.CLOSED_FACTS
        budget = 10
        success_fn = lambda: w.inv_map().get("oak_planks", 0) >= 1
        success_item = "oak_planks"
        track_fails = False
    else:
        w = R.build_world(oak=3, birch=0)
        w.put_block(4, 65, 2, "iron_ore")
        w.put_block(5, 65, 2, "iron_ore")
        w._refresh_near()
        ex = R.fresh_exec(w)
        goal = "obtain raw_iron"
        facts = ("You do not know the full recipe chain to raw_iron; "
                 "nothing in the current observation explains it.")
        budget = 12
        success_fn = lambda: w.inv_map().get("raw_iron", 0) >= 1
        success_item = "raw_iron"
        track_fails = True
    trace = []
    tok0 = (llm.prompt_tokens, llm.completion_tokens)
    fails = 0
    first_pivot = None
    for step in range(budget):
        obs = H.observe(w, ex)
        if kind_fas:
            fc.ingest(obs, trace[-1]["action"] if trace else None,
                      trace[-1]["detail"] if trace else "init", ())
            fc.step_dynamics()
            ctx_text, gate, tel, _ = R.build_ctx("fas", fc, None, goal, step)
        else:
            if cond in ("B1-h128", "B1-h512", "B1-h2048", "B2-rag"):
                ctx_text = R.context_for(cond, meta, goal) \
                    if hasattr(R, "context_for") else \
                    context_for(cond, meta, goal)
            else:
                ctx_text = "(no memory of past experiences)"
            gate = H.gate_from_context(facts + "\n" + obs + "\n" + ctx_text)
        act, obj, ok, detail, tb, nmenu, locked = H.run_menu_step(
            ex, goal, facts, ctx_text, llm, gate)
        if track_fails and act.startswith("gather_") and not ok:
            fails += 1
        if track_fails and first_pivot is None and \
                (act == "gather_oak_log" or act.startswith("craft_")):
            first_pivot = step
        trace.append({"step": step, "action": act, "ok": ok,
                      "detail": str(detail)[:60],
                      "prompt_tokens": llm.prompt_tokens,
                      "completion_tokens": llm.completion_tokens})
        if success_fn():
            break
    res = {"task": task, "condition": cond, "seed": seed,
           "decisions": len(trace), "success": int(success_fn()),
           "fail_repeats": fails if track_fails else None,
           "first_pivot": first_pivot if track_fails else None,
           "first_action": trace[0]["action"] if trace else None,
           "acts": [t["action"] for t in trace],
           "prompt_tokens": llm.prompt_tokens - tok0[0],
           "completion_tokens": llm.completion_tokens - tok0[1],
           "hunger_final": ex.hunger if task == "T1" else None}
    return res


CONDS_T1 = ["B0-direct", "B1-h2048", "B2-rag",
            "FAS-full", "FAS-reset", "FAS-sham"]
CONDS_T2 = ["B0-direct", "B1-h128", "B1-h512", "B1-h2048", "B2-rag",
            "FAS-full", "FAS-reset", "FAS-sham"]
CONDS_T3 = ["B0-direct", "B1-h2048", "B2-rag", "FAS-full", "FAS-reset"]


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="0,1,2,3,4,5,6,7,8,9")
    ap.add_argument("--out", default="raw_results.jsonl")
    ap.add_argument("--conditions", default="all")
    args = ap.parse_args()
    seeds = [int(x) for x in args.seeds.split(",")]
    all_conds = set(CONDS_T1 + CONDS_T2 + CONDS_T3)
    lock = open(os.path.join(HERE, args.out + ".lock"), "a+")
    try:
        import msvcrt
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("[lock] occupied")
        return 1
    log = open(os.path.join(HERE, args.out), "a", encoding="utf-8")
    n = 0
    for seed in seeds:
        # Phase 0:基图(prior closure;reset 对照的基准)
        base_world = R.build_world()
        fc_reset = make_fas(R.build_world(), "reset")
        base_state = graph_state(fc_reset.kg)
        # Phase 1:FAS-full 与 FAS-sham 各自经历同一事件流
        w_full = R.build_world()
        fc_full = make_fas(w_full, "full")
        meta_full = phase1(w_full, fc_full, None)
        meta_full["stream"] = meta_full["stream"]
        w_sham = R.build_world()
        fc_sham = make_fas(w_sham, "sham")
        phase1(w_sham, fc_sham, None)
        meta = {"stream": meta_full["stream"],
                "stream_tokens": tok("\n".join(meta_full["stream"])),
                "weights_before": meta_full["weights_before"],
                "weights_after": meta_full["weights_after"]}
        state_audit = {"base(prior-only)": base_state,
                       "full_after_phase1": graph_state(fc_full.kg),
                       "reset_equals_base": graph_state(fc_reset.kg) == base_state,
                       "sham_after_phase1": graph_state(fc_sham.kg),
                       "hebbian_delta_example": {
                           k: [meta["weights_before"].get(k),
                               meta["weights_after"].get(k)]
                           for k in meta["weights_after"]
                           if meta["weights_before"].get(k) is not None
                           and meta["weights_after"].get(k)
                           != meta["weights_before"].get(k)}}
        print("[phase1] stream_tokens=%d hebbian_changed=%d" % (
            meta["stream_tokens"],
            len(state_audit["hebbian_delta_example"])), flush=True)
        # Phase 3
        for task in ("T1", "T2", "T3"):
            conds = {"T1": CONDS_T1, "T2": CONDS_T2, "T3": CONDS_T3}[task]
            if args.conditions != "all":
                want = set(args.conditions.split(","))
                conds = [c for c in conds if c in want]
            for cond in conds:
                llm = v2.LLMHead()
                llm.max_tokens = 300
                if cond == "FAS-full":
                    holder = {"fc": fc_full, "phase1": meta}
                elif cond == "FAS-reset":
                    holder = {"fc": fc_reset, "phase1": meta}
                elif cond == "FAS-sham":
                    holder = {"fc": fc_sham, "phase1": meta}
                else:
                    holder = {"fc": None, "phase1": meta}
                try:
                    m = run_task(task, cond, seed, llm, holder)
                except Exception as e:
                    m = {"task": task, "condition": cond, "seed": seed,
                         "error": repr(e)[:200]}
                m["seed"] = seed
                if task == "T2":
                    m["state_audit"] = state_audit
                log.write(json.dumps(m, ensure_ascii=False) + "\n")
                log.flush()
                n += 1
                print("[PSA] %s %s s%d succ=%s fails=%s" % (
                    task, cond, seed, m.get("success"),
                    m.get("fail_repeats")), flush=True)
                time.sleep(0.15)
    print("done: %d runs" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
