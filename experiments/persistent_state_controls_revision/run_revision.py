# -*- coding: utf-8 -*-
# run_revision.py — T3 呈现等价修订对照(C3b/C4b/C4c)。
# 预注册:PREREGISTRATION_REVISION.md(post-hoc,独立比较族)。
# 复用 persistent_state_advantage / persistent_state_controls 的 harness
# 函数;新增代码全部在本目录;零生产修改;无 broad-except 静默。
import hashlib
import json
import os
import re
import sys
import time

ROOT = r"E:\Project\Fascinator"
PSA = os.path.join(ROOT, "experiments", "persistent_state_advantage")
CTRL = os.path.join(ROOT, "experiments", "persistent_state_controls")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "capability_gap_v2"))
sys.path.insert(0, PSA)
sys.path.insert(0, CTRL)
os.chdir(ROOT)

import run_exp as P  # noqa: E402
import run_experiment as R  # noqa: E402
import harness as H  # noqa: E402
import run_exp_routing_v2 as v2  # noqa: E402
import run_controls as K  # noqa: E402

HERE = os.path.join(ROOT, "experiments", "persistent_state_controls_revision")
WS_SIZE = 8
FAIL_TOKEN = "tool_missing"
FAIL_PREFIXES = ("tool_missing", "not_found", "missing_ingredients",
                 "needs_crafting_table", "not_in_inventory")


def code_hashes():
    out = {}
    for name in ("run_revision.py",):
        p = os.path.join(HERE, name)
        out[name] = hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
    for name in ("run_exp.py",):
        p = os.path.join(PSA, name)
        out["psa/" + name] = hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
    for name in ("run_controls.py", "harness.py", "run_experiment.py"):
        base = CTRL if name == "run_controls.py" else os.path.join(ROOT, "experiments", "capability_gap_v2")
        p = os.path.join(base, name)
        out[name] = hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
    out["run_exp_routing_v2.py"] = hashlib.sha256(open(
        os.path.join(ROOT, "scripts", "run_exp_routing_v2.py"), "rb").read()).hexdigest()[:16]
    return out


# ── C3b:与 C3 唯一差异 = 排序键(polarity 升序 → 失败优先)──────
def c3b_select(facts, obs):
    obs_ents = [e for e in K.KNOWN_ENTS if e in obs]
    hit = [f for f in facts if any(e in f["entities"] or e in f["action"]
                                   or e in f["result"] for e in obs_ents)]
    miss = [f for f in facts if f not in hit]
    key = lambda f: (f["polarity"], -f["index"])   # 失败(-1)在前;新在前
    hit.sort(key=key)
    miss.sort(key=key)
    chosen = (hit + miss)[:WS_SIZE]
    lines = []
    for rank, f in enumerate(chosen):
        score = round(1.0 - 0.05 * rank, 4)
        lines.append("- 动作:%s (act %s)" % (f["action"], score))
        lines.append("- 结果:%s (act %s)" % (f["result"], score))
        for e in f["entities"][:2]:
            lines.append("- %s (act %s)" % (e, score))
    return ("[cognitive-context mode=flat-store-failurefirst]\nselected nodes:\n"
            + "\n".join(lines[:WS_SIZE * 2]))


# ── C4b/C4c:双向 2 跳邻接(BFS),权重降序、插入序,截断 8 ──────
def c4b_select(fc, obs):
    ents = fc.entities_of(obs)
    seeds = [fc.node_id(e) for e in ents if fc.node_id(e) in fc.kg.nodes]
    seen = {}
    order = 0
    frontier = list(seeds)
    for s in seeds:
        seen[s] = (1.0, order)
        order += 1
    for depth in (1, 2):          # 两跳扩展,无衰减,仅结构可达
        nxt = []
        for s in frontier:
            for e in fc.kg.edges:
                src = getattr(e, "src", None)
                dst = getattr(e, "dst", None)
                w = float(getattr(e, "weight", 0) or 0)
                if src == s and dst not in seen:
                    seen[dst] = (w, order)
                    order += 1
                    nxt.append(dst)
                elif dst == s and src not in seen:
                    seen[src] = (w, order)
                    order += 1
                    nxt.append(src)
        frontier = nxt
    ranked = sorted(seen.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
    chosen = ranked[:WS_SIZE]
    lines = ["- %s (act %s)" % (nid, round(w, 4)) for nid, (w, _) in chosen]
    return ("[cognitive-context mode=graph-2hop]\nselected nodes:\n"
            + "\n".join(lines)), [nid for nid, _ in chosen], seen


# ── 注入文本度量(预检与正式运行共用)────────────────────────────
NODE_HEADER = "selected nodes:"
OTHER_HEADERS = ("selected edges:", "demand", "gap (top)", "routing:",
                 "memory:")


def ctx_metrics(ctx):
    """返回 {present, slot, fail_lines, n_lines, chars, tokens_est}。
    slot = 失败串首个出现的条目槽位(selected nodes 列表内 1-based 行号;
    列表外(如 edges 行)记 -1 但 present=True)。"""
    present = FAIL_TOKEN in ctx
    slot = None
    fail_lines = 0
    node_lines = []
    in_nodes = False
    for ln in ctx.splitlines():
        if ln.strip() == NODE_HEADER:
            in_nodes = True
            continue
        if in_nodes:
            if ln.startswith("- "):
                node_lines.append(ln)
            else:
                in_nodes = False
    for i, ln in enumerate(node_lines, 1):
        if any(p in ln for p in FAIL_PREFIXES):
            fail_lines += 1
            if slot is None and FAIL_TOKEN in ln:
                slot = i
    if slot is None and present:
        for i, ln in enumerate(ctx.splitlines(), 1):
            if FAIL_TOKEN in ln:
                slot = -i          # 在节点列表之外(负号标记)
                break
    n_lines = ctx.count("\n") + 1
    return {"present": present, "slot": slot, "fail_lines": fail_lines,
            "n_node_lines": len(node_lines), "n_lines": n_lines,
            "chars": len(ctx), "tokens_est": round(len(ctx) / 4.0, 1)}


def neg_edge_count(fc):
    return sum(1 for e in fc.kg.edges if float(getattr(e, "weight", 0) or 0) < 0)


# ── Phase-1 存储构建(每 seed 一次;全部确定性、零 LLM)──────────
def build_stores():
    w_full = R.build_world()
    fc_full = P.make_fas(w_full, "full")
    meta = P.phase1(w_full, fc_full, None)
    facts = K.facts_from_stream(meta["stream"])
    w_c4 = R.build_world()
    fc_c4 = P.make_fas(w_c4, "full")
    P.phase1(w_c4, fc_c4, None)                      # ef_context=True(同 C1/C4)
    w_c4c = R.build_world()
    fc_c4c = P.make_fas(w_c4c, "full")
    fc_c4c.ef_context = False                        # C4c:无负极性边
    P.phase1(w_c4c, fc_c4c, None)
    return {"fc_full": fc_full, "facts": facts, "fc_c4": fc_c4,
            "fc_c4c": fc_c4c, "stream": meta["stream"]}


def t3_world():
    w = R.build_world(oak=3, birch=0)
    w.put_block(4, 65, 2, "iron_ore")
    w.put_block(5, 65, 2, "iron_ore")
    w._refresh_near()
    return w, R.fresh_exec(w)


def c1_step0_ctx(fc_full):
    """run_task(T3) step-0 的 C1 上下文(Phase-1 基线,零 LLM)。"""
    w, ex = t3_world()
    obs = H.observe(w, ex)
    fc_full.ingest(obs, None, "init", ())
    fc_full.step_dynamics()
    ctx_text, gate, tel, _ = R.build_ctx("fas", fc_full, None,
                                         "obtain raw_iron", 0)
    return ctx_text


# ── 预检(§4 硬门槛)─────────────────────────────────────────────
def preflight(seeds):
    report = {"pass": True, "per_seed": [], "failures": []}
    for seed in seeds:
        st = build_stores()
        w, ex = t3_world()
        obs = H.observe(w, ex)
        ctxs = {
            "C1": c1_step0_ctx(st["fc_full"]),
            "C3b": c3b_select(st["facts"], obs),
            "C4b": c4b_select(st["fc_c4"], obs)[0],
            "C4c": c4b_select(st["fc_c4c"], obs)[0],
        }
        mets = {c: ctx_metrics(t) for c, t in ctxs.items()}
        neg = {"C4b": neg_edge_count(st["fc_c4"]),
               "C4c": neg_edge_count(st["fc_c4c"])}
        rec = {"seed": seed, "metrics": mets, "neg_edges": neg,
               "facts_eq_C3": st["facts"] == K.facts_from_stream(st["stream"])}
        # P1 可见性
        for c in ("C1", "C3b", "C4b", "C4c"):
            if not mets[c]["present"]:
                report["failures"].append("P1 %s seed%d: tool_missing absent" % (c, seed))
        # P2 位次(一侧界):slot ≤ slot(C1)+2;slot 为 None→失败;
        #    C3b 条目槽位 = ⌈行号/2⌉;负 slot(列表外)按 +8 处理并记录
        def eff_slot(c):
            s = mets[c]["slot"]
            if s is None:
                return None
            if s < 0:
                return 8
            if c == "C3b":
                return (s + 1) // 2
            return s
        s1 = eff_slot("C1")
        for c in ("C3b", "C4b", "C4c"):
            sc = eff_slot(c)
            if s1 is None or sc is None:
                report["failures"].append("P2 %s seed%d: slot undefined" % (c, seed))
            elif sc > s1 + 2:
                report["failures"].append(
                    "P2 %s seed%d: slot %d > C1 slot %d + 2" % (c, seed, sc, s1))
        # P3 长度
        for c in ("C3b", "C4b", "C4c"):
            r = mets[c]["chars"] / max(1, mets["C1"]["chars"])
            if not (0.5 <= r <= 2.0):
                report["failures"].append("P3 %s seed%d: char ratio %.2f" % (c, seed, r))
        # P4 结构
        if neg["C4c"] != 0:
            report["failures"].append("P4 C4c seed%d: %d negative edges" % (seed, neg["C4c"]))
        if neg["C4b"] < 1:
            report["failures"].append("P4 C4b seed%d: no negative edges" % seed)
        # 混杂记录:C4b/C4c 失败事实可达性是否一致
        rec["C4b_C4c_present_diff"] = (mets["C4b"]["present"]
                                       != mets["C4c"]["present"])
        rec["C4b_C4c_slot_diff"] = (mets["C4b"]["slot"], mets["C4c"]["slot"])
        report["per_seed"].append(rec)
    report["pass"] = len(report["failures"]) == 0
    return report


# ── 正式运行(仅 T3;镜像 run_task_c,新增 first_pivot 与注入审计)──
def run_task_r(task, cond, seed, llm, store):
    assert task == "T3"
    w, ex = t3_world()
    goal = "obtain raw_iron"
    facts_ctx = ("You do not know the full recipe chain to raw_iron; "
                 "nothing in the current observation explains it.")
    budget = 12
    success_fn = lambda: w.inv_map().get("raw_iron", 0) >= 1
    trace = []
    ctx_audit = []
    tok0 = (llm.prompt_tokens, llm.completion_tokens)
    fails = 0
    first_pivot = None
    for step in range(budget):
        obs = H.observe(w, ex)
        prev = trace[-1] if trace else None
        if cond == "C3b":
            ctx_text = c3b_select(store["facts"], obs)
            store["facts"].append({
                "entities": [e for e in K.KNOWN_ENTS if e in obs],
                "action": prev["action"] if prev else "observe",
                "result": (prev["detail"] if prev else "init")[:64],
                "polarity": (1 if (prev and prev["ok"]) else -1)
                if prev else 1,
                "index": 1000 + step})
        elif cond in ("C4b", "C4c"):
            fc = store["fc_c4"] if cond == "C4b" else store["fc_c4c"]
            fc.ingest(obs, prev["action"] if prev else None,
                      prev["detail"] if prev else "init", ())
            ctx_text, chosen, _seen = c4b_select(fc, obs)
        else:
            raise RuntimeError("unknown cond %r" % cond)
        gate = H.gate_from_context(facts_ctx + "\n" + obs + "\n" + ctx_text)
        m = ctx_metrics(ctx_text)
        m["step"] = step
        ctx_audit.append(m)
        act, obj, ok, detail, tb, nmenu, locked = H.run_menu_step(
            ex, goal, facts_ctx, ctx_text, llm, gate)
        if act.startswith("gather_") and not ok:
            fails += 1
        if first_pivot is None and (act == "gather_oak_log"
                                    or act.startswith("craft_")):
            first_pivot = step
        trace.append({"step": step, "action": act, "ok": ok,
                      "detail": str(detail)[:60]})
        if success_fn():
            break
    return {"task": task, "condition": cond, "seed": seed,
            "decisions": len(trace), "success": int(success_fn()),
            "fail_repeats": fails, "first_pivot": first_pivot,
            "first_action": trace[0]["action"] if trace else None,
            "acts": [t["action"] for t in trace],
            "prompt_tokens": llm.prompt_tokens - tok0[0],
            "completion_tokens": llm.completion_tokens - tok0[1],
            "ctx_step0": ctx_audit[0], "ctx_audit": ctx_audit,
            "harness_valid": True}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="0,1,2,3,4,5,6,7,8,9")
    ap.add_argument("--out", default="raw_revision.jsonl")
    ap.add_argument("--preflight", action="store_true",
                    help="§4 呈现等价预检(零 LLM);不通过则退出码 2")
    args = ap.parse_args()
    seeds = [int(x) for x in args.seeds.split(",")]

    if args.preflight:
        rep = preflight(seeds)
        with open(os.path.join(HERE, "preflight_report.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
        print("PREFLIGHT pass=%s failures=%d" % (rep["pass"],
                                                 len(rep["failures"])))
        for f in rep["failures"][:20]:
            print("  -", f)
        return 0 if rep["pass"] else 2

    lock = open(os.path.join(HERE, args.out + ".lock"), "a+")
    try:
        import msvcrt
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("[lock] occupied")
        return 1
    log = open(os.path.join(HERE, args.out), "a", encoding="utf-8")
    hashes = code_hashes()
    n = 0
    for seed in seeds:
        stores = build_stores()
        for cond in ("C3b", "C4b", "C4c"):
            store = {"facts": [dict(f) for f in stores["facts"]],
                     "fc_c4": stores["fc_c4"], "fc_c4c": stores["fc_c4c"]}
            llm = v2.LLMHead()
            llm.max_tokens = 300
            try:
                m = run_task_r("T3", cond, seed, llm, store)
            except Exception as e:
                m = {"task": "T3", "condition": cond, "seed": seed,
                     "error": repr(e)[:200], "harness_valid": False}
            m["seed"] = seed
            m["code_hashes"] = hashes
            log.write(json.dumps(m, ensure_ascii=False) + "\n")
            log.flush()
            n += 1
            print("[REV] %s s%d fails=%s pivot=%s err=%s" % (
                cond, seed, m.get("fail_repeats"), m.get("first_pivot"),
                m.get("error")), flush=True)
            time.sleep(0.15)
    print("done: %d runs" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
