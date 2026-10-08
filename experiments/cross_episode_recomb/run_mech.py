# -*- coding: utf-8 -*-
# run_mech.py — 零 LLM 机制探针:每问题 × {G1,G2,G3,G3-nospread}
# 测量:activation_mass / n_activated / cross-episode fraction /
#       金色中间实体进 Top-8。生产 KnowledgeGraph+DiffusionEngine,零修改。
import json
import os
import sys

ROOT = r"E:\Project\Fascinator"
sys.path.insert(0, ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

from graph_model import KnowledgeGraph, Node, Edge  # noqa: E402
from diffusion_engine import DiffusionEngine  # noqa: E402

cfg = json.load(open(os.path.join(HERE, "experiment_config.json"),
                     encoding="utf-8"))
QUESTIONS = cfg["questions"]
GRAPHS = cfg["graphs"]


def build_engine(gname):
    g = GRAPHS[gname]
    kg = KnowledgeGraph()
    iso = gname == "isolated"
    for n in g["nodes"]:
        kg.add_node(Node(id=n["id"], weight=1.0, label=n["label"],
                         graph_space=n.get("graph_space", "semantic")))
    for e in g["edges"]:
        kg.add_edge(Edge(src=e["src"], dst=e["dst"], relation=e["relation"],
                         weight=e["weight"],
                         relation_category=e["relation_category"]))
    eng = DiffusionEngine(kg, {"beta_spread": 1.0})
    eng.name_to_node = dict(kg.nodes)
    return kg, eng


def seeds_for(q, gname):
    g = GRAPHS[gname]
    ids = [n["id"] for n in g["nodes"]]
    out = []
    for s in q["seeds"]:
        if gname == "isolated":
            out += [i for i in ids if i.startswith(s + "@")]
        else:
            out += [i for i in ids if i == s]
    return out


def gold_mid_in_topk(q, topk_ids, isolated):
    """金色中间实体(除两端点)是否进 Top-8(G1 下检查其任一拷贝)。"""
    mids = q["gold_chain"][1:-1]
    hits = []
    for m in mids:
        if isolated:
            ok = any(t.startswith(m + "@") for t in topk_ids)
        else:
            ok = m in topk_ids
        hits.append(ok)
    return hits


def run_question(q, gname, nospread=False):
    kg, eng = build_engine(gname)
    seeds = seeds_for(q, gname)
    if not seeds:
        return {"error": "no seeds"}
    if nospread:
        # -spreading 消融:嵌入相似度选 Top-8(不传播)
        from embedding_manager import EmbeddingProvider
        import numpy as np
        g = GRAPHS[gname]
        ids = [n["id"] for n in g["nodes"]]
        emb = EmbeddingProvider()
        M = emb.encode(ids)
        qv = emb.encode_single(q["text"])
        sims = M @ (qv / (np.linalg.norm(qv) + 1e-9))
        top = [{"id": ids[i]} for i in sorted(range(len(ids)),
                                             key=lambda i: -float(sims[i]))][:8]
        topk = [t["id"] for t in top]
        mass = 0.0
        n_act = len(topk)
    else:
        eng.activate_from_inputs(seeds, [], source_type="question")
        eng.diffuse_round(max_steps=4)
        top = eng.get_topk(k=8)[0]
        topk = [n.id for n in top]
        acts = {n.id: float(getattr(n, "activation", 0) or 0) for n in top}
        mass = round(sum(acts.values()), 3)
        n_act = sum(1 for n in kg.nodes.values()
                    if float(getattr(n, "activation", 0) or 0) > 1e-6)
    ep_of = GRAPHS[gname]["episode_of"]
    ep_sets = [set(ep_of.get(t, [])) for t in topk]
    cross_nodes = sum(1 for e in ep_sets if len(e) >= 2)
    # 激活质量:被点亮节点覆盖的 episode 数
    all_eps = set()
    for t in topk:
        all_eps |= set(ep_of.get(t, []))
    return {"question": q["id"], "graph": gname, "nospread": nospread,
            "top8": topk, "activation_mass": mass, "n_activated": n_act,
            "cross_episode_nodes": cross_nodes,
            "cross_episode_fraction": round(cross_nodes / max(1, len(topk)), 3),
            "episodes_covered": sorted(all_eps),
            "gold_mid_in_top8": gold_mid_in_topk(q, topk, gname == "isolated"),
            "gold_all_in_top8": all(gold_mid_in_topk(q, topk,
                                                     gname == "isolated"))}


def main():
    rows = []
    for q in QUESTIONS:
        for gname in ("isolated", "shared", "eventframe"):
            rows.append(run_question(q, gname, nospread=False))
        rows.append(run_question(q, "eventframe", nospread=True))
    with open(os.path.join(HERE, "mechanism_results.json"), "w",
              encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    for r in rows:
        print("%s %-10s spread=%d gold_in_top8=%s mid=%s cross=%.2f mass=%s"
              % (r["question"], r["graph"], int(not r["nospread"]),
                 r.get("gold_all_in_top8"), r.get("gold_mid_in_top8"),
                 r.get("cross_episode_fraction", 0), r.get("activation_mass")))


if __name__ == "__main__":
    main()
