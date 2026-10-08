# -*- coding: utf-8 -*-
# run_controls.py — C3 Flat-Store / C4 Graph-NoActivation 对照(预注册见
# PREREGISTRATION.md)。复用 persistent_state_advantage 的 phase1/make_fas/
# 任务设置;新增代码全部在本目录,零生产修改,无 broad-except。
import json
import os
import re
import sys
import time

ROOT = r"E:\Project\Fascinator"
PSA = os.path.join(ROOT, "experiments", "persistent_state_advantage")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "capability_gap_v2"))
sys.path.insert(0, PSA)
os.chdir(ROOT)

import run_exp as P  # noqa: E402
import run_experiment as R  # noqa: E402
import harness as H  # noqa: E402
import run_exp_routing_v2 as v2  # noqa: E402

HERE = os.path.join(ROOT, "experiments", "persistent_state_controls")
WS_SIZE = 8
TOKEN_RE = re.compile(r"[a-zA-Z_]+")


# ── C3:Flat-Store ───────────────────────────────────────────────────
def facts_from_stream(stream):
    """解析 phase-1 事件流为扁平事实(与 FAS 写回内容信息等价)。"""
    facts = []
    for i, line in enumerate(stream):
        m = re.match(r"obs: (.*) \| action: (\S+) \| result: (.*)", line)
        if not m:
            raise RuntimeError("stream line unparsable: %r" % line[:80])
        obs, act, res = m.group(1), m.group(2), m.group(3)
        pol = -1 if re.match(r"(tool_missing|not_found|missing_ingredients|"
                             r"needs_crafting_table|not_in_inventory)", res) \
            else 1
        ents = [e for e in KNOWN_ENTS if e in obs]
        facts.append({"entities": ents, "action": act, "result": res[:64],
                      "polarity": pol, "index": i})
    return facts


KNOWN_ENTS = ["oak_log", "oak_planks", "birch_log", "dirt", "bread",
              "iron_ore", "raw_iron", "stick", "crafting_table",
              "cobblestone", "stone_pickaxe", "wooden_pickaxe"]


def c3_select(facts, obs):
    """预注册规则:精确实体匹配 → (polarity desc, index desc) → 补未命中。"""
    obs_ents = [e for e in KNOWN_ENTS if e in obs]
    hit = [f for f in facts if any(e in f["entities"] or e in f["action"]
                                   or e in f["result"] for e in obs_ents)]
    miss = [f for f in facts if f not in hit]
    key = lambda f: (-f["polarity"], -f["index"])
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
    return ("[cognitive-context mode=flat-store]\nselected nodes:\n"
            + "\n".join(lines[:WS_SIZE * 2]))


# ── C4:Graph-NoActivation(1 跳邻接)────────────────────────────────
def c4_select(fc, obs):
    """预注册规则:observation 实体 + 1 跳邻接(双向),边权降序、插入序。"""
    ents = fc.entities_of(obs)
    seeds = [fc.node_id(e) for e in ents if fc.node_id(e) in fc.kg.nodes]
    seen = {}
    order = 0
    for s in seeds:
        if s not in seen:
            seen[s] = (1.0, order)
            order += 1
        for e in fc.kg.edges:
            src = getattr(e, "src", None)
            dst = getattr(e, "dst", None)
            w = float(getattr(e, "weight", 0) or 0)
            if src == s and dst not in seen:
                seen[dst] = (w, order)
                order += 1
            elif dst == s and src not in seen:
                seen[src] = (w, order)
                order += 1
    ranked = sorted(seen.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
    chosen = ranked[:WS_SIZE]
    lines = ["- %s (act %s)" % (nid, round(w, 4)) for nid, (w, _) in chosen]
    return ("[cognitive-context mode=graph-1hop]\nselected nodes:\n"
            + "\n".join(lines)), [nid for nid, _ in chosen], set(seen)


# ── Phase 3 任务运行(C3/C4 版)──────────────────────────────────────
def run_task_c(task, cond, seed, llm, store):
    kind = cond
    if task == "T2":
        w = R.build_world(oak=3, birch=3)
        ex = R.fresh_exec(w)
        goal = "obtain oak_planks"
        facts_ctx = R.CLOSED_FACTS
        budget = 10
        success_fn = lambda: w.inv_map().get("oak_planks", 0) >= 1
        track = False
    else:
        w = R.build_world(oak=3, birch=0)
        w.put_block(4, 65, 2, "iron_ore")
        w.put_block(5, 65, 2, "iron_ore")
        w._refresh_near()
        ex = R.fresh_exec(w)
        goal = "obtain raw_iron"
        facts_ctx = ("You do not know the full recipe chain to raw_iron; "
                     "nothing in the current observation explains it.")
        budget = 12
        success_fn = lambda: w.inv_map().get("raw_iron", 0) >= 1
        track = True
    trace = []
    tok0 = (llm.prompt_tokens, llm.completion_tokens)
    fails = 0
    hop_check = []
    for step in range(budget):
        obs = H.observe(w, ex)
        prev = trace[-1] if trace else None
        if kind == "C3":
            ctx_text = c3_select(store["facts"], obs)
            gate = H.gate_from_context(facts_ctx + "\n" + obs + "\n" + ctx_text)
            store["facts"].append({
                "entities": [e for e in KNOWN_ENTS if e in obs],
                "action": prev["action"] if prev else "observe",
                "result": (prev["detail"] if prev else "init")[:64],
                "polarity": (1 if (prev and prev["ok"]) else -1)
                if prev else 1,
                "index": 1000 + step})
        else:  # C4
            store["fc"].ingest(obs, prev["action"] if prev else None,
                               prev["detail"] if prev else "init", ())
            ctx_text, chosen, reach1 = c4_select(store["fc"], obs)
            gate = H.gate_from_context(facts_ctx + "\n" + obs + "\n" + ctx_text)
            hop_check.append(len(chosen))
        act, obj, ok, detail, tb, nmenu, locked = H.run_menu_step(
            ex, goal, facts_ctx, ctx_text, llm, gate)
        if track and act.startswith("gather_") and not ok:
            fails += 1
        trace.append({"step": step, "action": act, "ok": ok,
                      "detail": str(detail)[:60]})
        if success_fn():
            break
    return {"task": task, "condition": cond, "seed": seed,
            "decisions": len(trace), "success": int(success_fn()),
            "fail_repeats": fails if track else None,
            "first_action": trace[0]["action"] if trace else None,
            "acts": [t["action"] for t in trace],
            "prompt_tokens": llm.prompt_tokens - tok0[0],
            "completion_tokens": llm.completion_tokens - tok0[1],
            "hop_check": hop_check if kind == "C4" else None}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="0,1,2,3,4,5,6,7,8,9")
    ap.add_argument("--out", default="raw_controls.jsonl")
    ap.add_argument("--dry", action="store_true",
                    help="preflight:1 seed,零 LLM 注入文本审计")
    args = ap.parse_args()
    seeds = [int(x) for x in args.seeds.split(",")]
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
        # Phase 1(与 C1 完全同路径;C3 事实从同一事件流派生;
        #  store 在 seed 内跨任务持续演化,与 C1 图谱行为一致)
        w_full = R.build_world()
        fc_full = P.make_fas(w_full, "full")
        meta = P.phase1(w_full, fc_full, None)
        facts = facts_from_stream(meta["stream"])
        w_c4 = R.build_world()
        fc_c4 = P.make_fas(w_c4, "full")
        P.phase1(w_c4, fc_c4, None)
        store_c3 = {"facts": facts}
        store_c4 = {"fc": fc_c4}
        for task in ("T2", "T3"):
            for cond in ("C3", "C4"):
                if args.dry and seed != 0:
                    continue
                if args.dry:
                    obs = "near: oak_logx3, dirtx1; inventory: {}; hunger: 20"
                    ctx3 = c3_select(store_c3["facts"], obs)
                    ctx4, chosen4, _ = c4_select(store_c4["fc"], obs)
                    print("[dry] C3 ctx(%d chars):\n%s" % (len(ctx3), ctx3))
                    print("[dry] C4 ctx:\n%s" % ctx4)
                    continue
                llm = v2.LLMHead()
                llm.max_tokens = 300
                store = store_c3 if cond == "C3" else store_c4
                m = run_task_c(task, cond, seed, llm, store)
                log.write(json.dumps(m, ensure_ascii=False) + "\n")
                log.flush()
                n += 1
                print("[CTL] %s %s s%d succ=%s fails=%s" % (
                    task, cond, seed, m["success"],
                    m["fail_repeats"]), flush=True)
                time.sleep(0.15)
        if args.dry:
            break
    print("done: %d runs" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
