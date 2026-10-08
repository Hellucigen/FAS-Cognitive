# -*- coding: utf-8 -*-
# run_experiment.py — Capability Gap Campaign v2 正式实验 runner(A/I/K/C/E 行为部分)
# 用法: python run_experiment.py --exp A --seeds 0..9 --conditions all --smoke
# 单实例锁;raw_results.jsonl 幂等追加;失败分类按任务书 §24。
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harness as H  # noqa: E402
import run_exp_routing_v2 as v2  # noqa: E402

CLOSED_FACTS = ("You do not know the recipe for the target, and nothing in the "
                "current observation explains how it is made.")
# 实验 I 专用 facts:目标配方对全条件对称公开(被测知识=两条子链的汇聚)
CLOSED_FACTS_I = ("You do not know how to obtain oak_planks or stick, and "
                  "nothing in the current observation explains their recipes. "
                  "Known: oak_fence requires 4 oak_planks and 2 sticks.")
OPEN_FACTS = "Recipes you have practiced are available to you as before."

WORLD_SPEC = {"oak": 6, "birch": 2}


def build_world(oak=6, birch=2, include_dirt=True):
    w = v2.SandboxWorld()
    w.__init__()
    w.pos = {"x": 0.0, "y": 64.0, "z": 0.0}
    # 橡树(目标资源)与桦树(干扰)成列摆放;地面 dirt 可采(无关经历用)
    for i in range(oak):
        w.put_block(3 + i, 65, 0, "oak_log")
        w.put_block(3 + i, 66, 0, "oak_log")
    for i in range(birch):
        w.put_block(10 + i, 65, 0, "birch_log")
    if include_dirt:
        w.put_block(0, 64, 0, "dirt")
    w._refresh_near()
    return w


def fresh_exec(w):
    return H.ExecutorWithGate(w, gate=set())


# ── 条件记忆系统装配 ────────────────────────────────────────────────────
def make_condition(cond, world, exclude, exp_seed_graph=True):
    """返回 (kind, fc, hist) ;kind∈{fas,fas-nowb,fas-nodiff,history,retrieval,direct}"""
    if cond == "direct":
        return "direct", None, H.HistoryStore()  # store 不写入(direct 零记忆)
    if cond in ("history", "retrieval", "history10", "history25", "history50",
                "history100", "history200"):
        return ("retrieval" if cond == "retrieval" else "history"), None, H.HistoryStore()
    if cond == "fresh-fas":
        fc = H.CampaignFASContext(world, use_diffusion=True, use_demand=True,
                                  exclude_recipes=exclude, writeback=True)
        return "fas", fc, H.HistoryStore()
    if cond == "fas-ef":
        fc = H.CampaignFASContext(world, use_diffusion=True, use_demand=True,
                                  exclude_recipes=exclude, writeback=True)
        fc.ef_context = True
        return "fas", fc, H.HistoryStore()
    if cond == "fas-nowb":
        fc = H.CampaignFASContext(world, use_diffusion=True, use_demand=True,
                                  exclude_recipes=exclude, writeback=False)
        return "fas", fc, H.HistoryStore()
    if cond == "fas-nodiff":
        fc = H.CampaignFASContext(world, use_diffusion=False, use_demand=True,
                                  exclude_recipes=exclude, writeback=True)
        return "fas", fc, H.HistoryStore()
    # fas 默认全量
    fc = H.CampaignFASContext(world, use_diffusion=True, use_demand=True,
                              exclude_recipes=exclude, writeback=True)
    return "fas", fc, H.HistoryStore()


def build_ctx(kind, fc, hist, goal, step_i):
    """返回 (ctx_text, gate, telemetry, tbreak)。统一门控规则:gate=ctx 中出现的物品名。"""
    tb = {}
    if kind == "fas":
        k = int(getattr(fc, "ctx_k", 8) or 8)
        rich = bool(getattr(fc, "ctx_rich", False))
        sel = fc.focus(k=k) if fc.use_diffusion else fc.flat_focus(goal, k=k)
        ids = {n["id"] for n in sel}
        edges = [f"{e.src}-{e.relation}->{e.dst}" for e in fc.kg.edges
                 if getattr(e, "src", None) in ids and getattr(e, "dst", None) in ids][:8]
        demand, gap, routing, _m = fc.demand_block(goal)
        payload, ctx_text = v2.serialize_context(
            "fas", "full", sel, demand, gap, routing, memory_context=None,
            edges=edges, activation_summary={"n_selected": len(sel),
                                             **fc.telemetry()})
        if rich:
            # 富序列化(消费者格式参数):为选中集里的 动作:/结果: 节点
            # 追加其图谱邻接(动作→结果 配对 与 动作~实体 共现),
            # 表达"可执行配方结构"。同一图谱内容,仅呈现格式不同。
            extra = []
            for n in sel:
                nid = n["id"]
                if not (nid.startswith("动作:") or nid.startswith("结果:")):
                    continue
                for e in fc.kg.edges:
                    if getattr(e, "src", None) == nid:
                        extra.append(f"{nid} -{e.relation}-> {e.dst}")
                    elif getattr(e, "dst", None) == nid and nid.startswith("动作:"):
                        extra.append(f"{nid} ~ {e.src}")
            seen = set()
            extra = [x for x in extra if not (x in seen or seen.add(x))][:16]
            if extra:
                ctx_text += chr(10) + "action-outcome structure:" + chr(10) +                     chr(10).join("- " + x for x in extra)
        tel = {"graph_nodes": len(fc.kg.nodes), "selected": [n["id"] for n in sel][:8],
               "exp_edges": fc.exp_edges, "ctx_k": k, "ctx_rich": rich}
    elif kind == "retrieval":
        ctx_text = "Relevant past experience:\n" + hist.retrieve(goal, k=8)
        tel = {"n_history": len(hist.lines)}
    elif kind == "history":
        ctx_text = "Past experience stream:\n" + hist.window(None)
        tel = {"n_history": len(hist.lines)}
    else:
        ctx_text = "No additional memory or context."
        tel = {}
    gate = H.gate_from_context(ctx_text)
    return ctx_text, gate, tel, tb


# ── 闭卷测试相(A P4/P5、I 全条件、K)────────────────────────────────
def run_closedbook_test(cond_kind, fc, hist, goal, llm, seed, phase, max_dec=10,
                        world_spec=None, facts=None):
    w = build_world(**(world_spec or WORLD_SPEC))
    ex = fresh_exec(w)
    trace = []
    done = False
    tok0 = (llm.prompt_tokens, llm.completion_tokens)
    for step in range(max_dec):
        obs = H.observe(w, ex)
        if cond_kind == "fas":
            fc.ingest(obs, trace[-1]["action"] if trace else None,
                      trace[-1]["detail"] if trace else "init", ())
            fc.step_dynamics()
        elif cond_kind in ("history", "retrieval"):
            hist.add(obs, trace[-1]["action"] if trace else None,
                     trace[-1]["detail"] if trace else "init")
        ctx_text, gate, tel, _tb = build_ctx(cond_kind, fc, hist, goal, step)
        act, obj, ok, detail, tb, nmenu, locked = H.run_menu_step(
            ex, goal, facts or CLOSED_FACTS, ctx_text, llm, gate)
        trace.append({"step": step, "action": act, "ok": ok,
                      "detail": str(detail)[:60], "gate_size": len(gate),
                      "menu_n": nmenu, "locked": locked,
                      "selected": tel.get("selected", []),
                      "exp_edges": tel.get("exp_edges"), **tb})
        if w.inv_map().get(goal.replace("obtain ", ""), 0) >= 1:
            done = True
            break
    target = goal.replace("obtain ", "")
    return {"phase": phase, "success": int(done), "decisions": len(trace),
            "first_action": trace[0]["action"] if trace else None,
            "acts": [t["action"] for t in trace],
            "prompt_tokens": llm.prompt_tokens - tok0[0],
            "completion_tokens": llm.completion_tokens - tok0[1],
            "exp_edges_at_test": (fc.exp_edges if fc is not None else None),
            "trace": trace}


# ── 实验 A:Continual Learning ─────────────────────────────────────────
EXCL_A = ("oak_planks",)


def exp_A(cond, seed, llm, log):
    w = build_world()
    kind, fc, hist = make_condition(cond, w, EXCL_A)
    t0 = time.time()
    # 练习相(零 LLM,同一脚本;direct 条件不写入记忆)
    prac = []
    if cond not in ("direct", "fresh-fas"):
        ex = fresh_exec(w)
        prac += H.script_practice_A(ex, (hist, None), fc)
        prac += H.script_practice_C(ex, (hist, None), fc)
        prac += H.script_filler(ex, (hist, None), fc)
    pre = {"exp_edges_after_practice": (fc.exp_edges if fc is not None else 0),
           "n_history": len(hist.lines) if kind != "direct" else 0,
           "practice_ok": all(p["ok"] for p in prac)}
    r4 = run_closedbook_test(kind, fc, hist, "obtain oak_planks", llm, seed,
                             "P4_test")
    r5 = run_closedbook_test(kind, fc, hist, "obtain oak_planks", llm, seed,
                             "P5_retest")
    return {"exp": "A", "condition": cond, "kind": kind, "seed": seed,
            "practice": pre, "P4": r4, "P5": r5,
            "latency_s": round(time.time() - t0, 1),
            "harness_valid": True, "failure_class": "none"}


# ── 实验 I:Multi-experience Convergence ─────────────────────────────
EXCL_I = ("oak_planks", "stick")  # fence 配方为生产先验(全条件播种);汇聚点=planks
I_SETS = {
    "I1-fas-A": ("fas", {"A"}),
    "I2-fas-B": ("fas", {"B"}),
    "I3-fas-AB": ("fas", {"A", "B"}),
    "I4-fas-ABC": ("fas", {"A", "B", "C"}),
    "I5-llm-raw-AB": ("history", {"A", "B"}),
    "I6-llm-ret-AB": ("retrieval", {"A", "B"}),
    "I7-fas-nodiff-ABC": ("fas-nodiff", {"A", "B", "C"}),
    # 表示研究(协议修订 v1.2):同一图谱内容,只动消费者格式/预算
    "I3-k16": ("fas", {"A", "B"}),
    "I3-k32": ("fas", {"A", "B"}),
    "I3-rich": ("fas", {"A", "B"}),
}
SCRIPTS = {"A": H.script_practice_A, "B": H.script_practice_B,
           "C": H.script_practice_C}


def exp_I(cond, seed, llm, log):
    tag, kinds = I_SETS[cond]
    w = build_world()
    kind, fc, hist = make_condition(
        "fas-nodiff" if cond == "I7-fas-nodiff-ABC" else
        ("fas" if tag.startswith("fas") else tag),
        w, EXCL_I)
    # 表示研究参数(消费者格式/预算,零机制改动)
    if cond == "I3-k16":
        fc.ctx_k = 16
    elif cond == "I3-k32":
        fc.ctx_k = 32
    elif cond == "I3-rich":
        fc.ctx_rich = True
    t0 = time.time()
    ex = fresh_exec(w)
    if kind != "direct" and kinds:
        for k in sorted(kinds):
            SCRIPTS[k](ex, (hist, None), fc)
    r = run_closedbook_test(kind, fc, hist, "obtain oak_fence", llm, seed,
                            "test", max_dec=20, facts=CLOSED_FACTS_I)
    return {"exp": "I", "condition": cond, "kind": kind, "seed": seed,
            "experiences": sorted(kinds),
            "test": r, "latency_s": round(time.time() - t0, 1),
            "harness_valid": True, "failure_class": "none"}


# ── 实验 K:History vs FAS State Compression ──────────────────────────
K_CONDS = ["fas", "history10", "history25", "history50", "history100",
           "history200", "retrieval", "direct"]


def exp_K(cond, seed, llm, log):
    w = build_world()
    kind, fc, hist = make_condition(cond, w, EXCL_A)
    t0 = time.time()
    if kind != "direct":
        ex = fresh_exec(w)
        H.script_practice_long(ex, (hist, None), fc)
    if cond.startswith("history"):
        kwin = int(cond.replace("history", ""))
    else:
        kwin = None
    # 复用 run_closedbook_test,但 history 条件带窗口
    w2 = build_world(**WORLD_SPEC)
    ex2 = fresh_exec(w2)
    trace = []
    done = False
    tok0 = (llm.prompt_tokens, llm.completion_tokens)
    goal = "obtain oak_planks"
    for step in range(10):
        obs = H.observe(w2, ex2)
        if kind == "fas":
            fc.ingest(obs, trace[-1]["action"] if trace else None,
                      trace[-1]["detail"] if trace else "init", ())
            fc.step_dynamics()
        elif kind in ("history", "retrieval"):
            hist.add(obs, trace[-1]["action"] if trace else None,
                     trace[-1]["detail"] if trace else "init")
        if kind == "history" and kwin is not None:
            ctx_text = "Past experience stream:\n" + hist.window(kwin)
            gate = H.gate_from_context(ctx_text)
            tel = {"n_history": len(hist.lines), "window": kwin}
        else:
            ctx_text, gate, tel, _ = build_ctx(kind, fc, hist, goal, step)
        act, obj, ok, detail, tb, nmenu, locked = H.run_menu_step(
            ex2, goal, CLOSED_FACTS, ctx_text, llm, gate)
        trace.append({"step": step, "action": act, "ok": ok,
                      "detail": str(detail)[:60], **tb})
        if w2.inv_map().get("oak_planks", 0) >= 1:
            done = True
            break
    r = {"phase": "test", "success": int(done), "decisions": len(trace),
         "first_action": trace[0]["action"] if trace else None,
         "acts": [t["action"] for t in trace],
         "prompt_tokens": llm.prompt_tokens - tok0[0],
         "completion_tokens": llm.completion_tokens - tok0[1], "trace": trace}
    return {"exp": "K", "condition": cond, "kind": kind, "seed": seed,
            "window": kwin, "test": r, "latency_s": round(time.time() - t0, 1),
            "harness_valid": True, "failure_class": "none"}


# ── 实验 C:Persistent Intention ──────────────────────────────────────
OPEN_FACTS_C = ("Recipes are known: oak_planks is crafted from 1 oak_log; "
                "stick from 2 oak_planks.")


def exp_C(cond, seed, llm, log):
    w = build_world()
    kind, fc, hist = make_condition(cond, w, ())  # C 考目标维持,不考配方知识
    t0 = time.time()
    p1 = run_open_phase(kind, fc, hist, "obtain oak_planks", llm, seed,
                        "P1_goal", max_dec=5, success_item="oak_planks",
                        facts=OPEN_FACTS_C)
    p2 = run_open_phase(kind, fc, hist, "obtain dirt", llm, seed,
                        "P2_distract", max_dec=3, success_item="dirt",
                        facts=OPEN_FACTS_C)
    p3 = run_open_phase(kind, fc, hist,
                        "You have free choice of actions.", llm, seed,
                        "P3_free", max_dec=6, success_item="oak_planks",
                        facts=CLOSED_FACTS)  # P3 闭卷:知识只能来自记忆
    return {"exp": "C", "condition": cond, "kind": kind, "seed": seed,
            "P1": p1, "P2": p2, "P3": p3,
            "latency_s": round(time.time() - t0, 1),
            "harness_valid": True, "failure_class": "none"}


def run_open_phase(kind, fc, hist, goal, llm, seed, phase, max_dec=6,
                   world="new", success_item=None, facts=None):
    w = build_world()
    ex = fresh_exec(w)
    trace = []
    tok0 = (llm.prompt_tokens, llm.completion_tokens)
    for step in range(max_dec):
        obs = H.observe(w, ex)
        if kind == "fas":
            fc.ingest(obs, trace[-1]["action"] if trace else None,
                      trace[-1]["detail"] if trace else "init", ())
            fc.step_dynamics()
        elif kind in ("history", "retrieval"):
            hist.add(obs, trace[-1]["action"] if trace else None,
                     trace[-1]["detail"] if trace else "init")
        ctx_text, gate, tel, _ = build_ctx(kind, fc, hist, goal, step)
        act, obj, ok, detail, tb, nmenu, locked = H.run_menu_step(
            ex, goal, facts or OPEN_FACTS, ctx_text, llm, gate)
        trace.append({"step": step, "action": act, "ok": ok,
                      "detail": str(detail)[:60], **tb})
        if success_item and w.inv_map().get(success_item, 0) >= 1:
            break
    return {"phase": phase, "decisions": len(trace),
            "success": int(bool(success_item) and
                           w.inv_map().get(success_item, 0) >= 1),
            "acts": [t["action"] for t in trace],
            "first_action": trace[0]["action"] if trace else None,
            "prompt_tokens": llm.prompt_tokens - tok0[0],
            "completion_tokens": llm.completion_tokens - tok0[1], "trace": trace}


# ── 实验 E:Goal Revision ─────────────────────────────────────────────
def exp_E(cond, seed, llm, log, eventframe=False):
    # 失败源:iron_ore 需 stone_pickaxe(不可达链)→ gather_iron_ore 必失败
    # (tool_missing);替代链:oak_log→planks→stick→wooden_pickaxe→...→iron。
    # 测:重复失败次数、转向生产性动作的决策序号。
    w = build_world(oak=3, birch=0)
    w.put_block(4, 65, 2, "iron_ore")
    w.put_block(5, 65, 2, "iron_ore")
    w._refresh_near()
    kind, fc, hist = make_condition(cond, w, ())
    t0 = time.time()
    ex = fresh_exec(w)
    trace = []
    tok0 = (llm.prompt_tokens, llm.completion_tokens)
    goal = "obtain raw_iron"
    facts_e = ("You do not know the full recipe chain to raw_iron; nothing in "
               "the current observation explains it.")
    fail_gathers = 0
    first_productive = None
    for step in range(12):
        obs = H.observe(w, ex)
        if kind == "fas":
            fc.ingest(obs, trace[-1]["action"] if trace else None,
                      trace[-1]["detail"] if trace else "init", ())
            fc.step_dynamics()
        elif kind in ("history", "retrieval"):
            hist.add(obs, trace[-1]["action"] if trace else None,
                     trace[-1]["detail"] if trace else "init")
        ctx_text, gate, tel, _ = build_ctx(kind, fc, hist, goal, step)
        act, obj, ok, detail, tb, nmenu, locked = H.run_menu_step(
            ex, goal, facts_e, ctx_text, llm, gate)
        if act.startswith("gather_") and not ok:
            fail_gathers += 1
        if first_productive is None and (act == "gather_oak_log" or
                                         act.startswith("craft_")):
            first_productive = step
        trace.append({"step": step, "action": act, "ok": ok,
                      "detail": str(detail)[:60], **tb})
    r = {"phase": "revision", "decisions": len(trace),
         "fail_gathers": fail_gathers,
         "first_productive_step": first_productive,
         "explores": sum(1 for t in trace if t["action"].startswith("explore_")),
         "first_action": trace[0]["action"] if trace else None,
         "acts": [t["action"] for t in trace],
         "prompt_tokens": llm.prompt_tokens - tok0[0],
         "completion_tokens": llm.completion_tokens - tok0[1], "trace": trace}
    return {"exp": "E", "condition": cond, "kind": kind, "seed": seed,
            "test": r, "latency_s": round(time.time() - t0, 1),
            "harness_valid": True, "failure_class": "none"}


EXPS = {"A": (exp_A, ["fas", "fas-nowb", "history", "retrieval", "fresh-fas"]),
        "I": (exp_I, list(I_SETS.keys())),
        "K": (exp_K, K_CONDS),
        "C": (exp_C, ["fas", "history", "direct", "fresh-fas"]),
        "E": (exp_E, ["fas", "fas-ef", "history", "direct"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", required=True, choices=list(EXPS.keys()))
    ap.add_argument("--out", default=None)
    ap.add_argument("--seeds", default="0,1,2,3,4,5,6,7,8,9")
    ap.add_argument("--conditions", default="all")
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    fn, conds = EXPS[args.exp]
    if args.conditions != "all":
        conds = [c for c in args.conditions.split(",") if c]
    seeds = [int(s) for s in args.seeds.split(",")]
    if args.smoke:
        seeds = seeds[:3]
        conds = conds[:3] if len(conds) > 3 else conds
    out = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"raw_{args.exp}{'_smoke' if args.smoke else ''}.jsonl")
    lock = open(out + ".lock", "a+")
    try:
        import msvcrt
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print(f"[lock] {out} 被另一进程占用,拒绝启动")
        return 1
    # 冒烟装配自检(每次运行先做,零 LLM)
    w0 = build_world()
    rep = v2.preflight(w0)
    print("[preflight]", json.dumps(rep, ensure_ascii=False)[:300], flush=True)
    log = open(out, "a", encoding="utf-8")
    t_start = time.time()
    n = 0
    for seed in seeds:
        for cond in conds:
            llm = v2.LLMHead()
            llm.max_tokens = 400
            try:
                m = fn(cond, seed, llm, log)
            except v2.CoreModuleError as e:
                m = {"exp": args.exp, "condition": cond, "seed": seed,
                     "harness_valid": False, "failure_class": "INVALID_HARNESS_RUN",
                     "failure_reason": str(e)[:200]}
            except v2.InfrastructureError as e:
                m = {"exp": args.exp, "condition": cond, "seed": seed,
                     "harness_valid": True, "failure_class": "INFRASTRUCTURE_FAILURE",
                     "failure_reason": str(e)[:200]}
            log.write(json.dumps(m, ensure_ascii=False) + "\n")
            log.flush()
            n += 1
            print(f"[{args.exp}] {cond} seed{seed}: "
                  f"{json.dumps({k: v for k, v in m.items() if k in ('P4','P5','test','P3','latency_s','failure_class')}, ensure_ascii=False)[:200]}",
                  flush=True)
            time.sleep(0.2)
    print(f"done: {n} runs -> {out} ({time.time()-t_start:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
