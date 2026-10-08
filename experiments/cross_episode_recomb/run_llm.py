# -*- coding: utf-8 -*-
# run_llm.py — 行为轮:7 条件 × 6 问题 × 5 种子(单次 LLM 决策/run)
# 条件:B0-direct / B1-history / B2-retrieval / FAS-G3 / FAS-G2 /
#       FAS-G1 / FAS-G3-nospread
import json
import os
import sys

ROOT = r"E:\Project\Fascinator"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

import run_mech as M  # noqa: E402
import run_exp_routing_v2 as v2  # noqa: E402

cfg = M.cfg
QUESTIONS = M.QUESTIONS
EPISODES = cfg["episodes"]

CONDS = ["B0-direct", "B1-history", "B2-retrieval",
         "FAS-G3", "FAS-G2", "FAS-G1", "FAS-G3-nospread"]

PROMPT = """You are answering questions about a small world.
Question: {question}
{context}
Answer in Chinese. If the context connects the entities through
intermediate entities/relations, describe the connecting chain.
If the context does not contain enough information, say so.
Answer:"""


def episode_lines(seed):
    eps = list(EPISODES)
    import random
    random.Random(seed * 104729).shuffle(eps)
    return "\n".join("%s: %s %s %s" % (e["id"], e["subject"], e["action"],
                                       e["object"]) for e in eps)


def build_context(cond, q, seed):
    if cond == "B0-direct":
        return "(no context)"
    if cond == "B1-history":
        return "Experience stream:\n" + episode_lines(seed)
    if cond == "B2-retrieval":
        from embedding_manager import EmbeddingProvider
        import numpy as np
        emb = EmbeddingProvider()
        lines = ["%s: %s %s %s" % (e["id"], e["subject"], e["action"],
                                   e["object"]) for e in EPISODES]
        M_ = emb.encode(lines)
        qv = emb.encode_single(q["text"])
        sims = M_ @ (qv / (np.linalg.norm(qv) + 1e-9))
        idx = sorted(range(len(lines)), key=lambda i: -float(sims[i]))[:6]
        return "Relevant experiences:\n" + "\n".join(lines[i] for i in idx)
    # FAS 条件
    gname = {"FAS-G3": "eventframe", "FAS-G2": "shared",
             "FAS-G1": "isolated"}.get(cond, "eventframe")
    if cond == "FAS-G3-nospread":
        row = next(r for r in M.ROWS if r["question"] == q["id"]
                   and r["nospread"])
        topk = row["top8"]
        g = M.GRAPHS[gname]
        edges = [e for e in g["edges"]]
    else:
        kg, eng = M.build_engine(gname)
        seeds = M.seeds_for(q, gname)
        eng.activate_from_inputs(seeds, [], source_type="question")
        eng.diffuse_round(max_steps=4)
        top = eng.get_topk(k=8)[0]
        topk = [n.id for n in top]
        g = M.GRAPHS[gname]
    ids = set(topk)
    edges = ["%s -%s-> %s" % (e["src"], e["relation"], e["dst"])
             for e in g["edges"]
             if e["src"] in ids and e["dst"] in ids][:10]
    ctx = "Working set (nodes with activation):\n" + "\n".join(
        "- " + t for t in topk)
    if edges:
        ctx += "\nRelations:\n" + "\n".join("- " + e for e in edges)
    return ctx


def check_answer(ans, q):
    chain = q["gold_chain"]
    mids = chain[1:-1]
    mentioned = [e for e in chain if e in ans]
    mid_hits = [m for m in mids if m in ans]
    act_hits = [a for a in q["gold_actions"] if a in ans]
    exact = len(mid_hits) == len(mids) and len(mentioned) >= 2
    partial = len(mid_hits) >= 1
    path_ok = all(mentioned[i] in chain and
                  (i + 1 >= len(mentioned) or
                   abs(chain.index(mentioned[i]) -
                       chain.index(mentioned[i + 1])) == 1)
                  for i in range(len(mentioned) - 1)) if mentioned else False
    return {"exact_success": int(exact), "partial_success": int(partial),
            "path_validity": int(path_ok and len(mentioned) >= 2),
            "relation_validity": int(len(act_hits) > 0),
            "mentioned": mentioned}


def main():
    rows = []
    n = 0
    for cond in CONDS:
        for q in QUESTIONS:
            for seed in range(5):
                ctx = build_context(cond, q, seed)
                prompt = PROMPT.format(question=q["text"], context=ctx)
                llm = v2.LLMHead()
                llm.max_tokens = 300
                try:
                    ans = llm.decide(prompt)
                    err = ""
                except Exception as e:
                    ans = ""
                    err = repr(e)[:150]
                res = check_answer(ans, q) if ans else {
                    "exact_success": 0, "partial_success": 0,
                    "path_validity": 0, "relation_validity": 0,
                    "mentioned": []}
                rows.append({"condition": cond, "question": q["id"],
                             "qtype": q["type"], "seed": seed,
                             "answer": ans[:400], "error": err, **res})
                n += 1
                print("[T] %s %s s%d exact=%d partial=%d %s" % (
                    cond, q["id"], seed, res["exact_success"],
                    res["partial_success"], err[:40]), flush=True)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "raw_results.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("done: %d runs" % n)


if __name__ == "__main__":
    M.ROWS = json.load(open(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "mechanism_results.json"), encoding="utf-8"))
    main()
