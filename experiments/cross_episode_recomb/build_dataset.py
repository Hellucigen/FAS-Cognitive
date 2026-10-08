# -*- coding: utf-8 -*-
# build_dataset.py — Step 1-7:registry/episodes/三图/完整性/泄漏/捷径审计
# 输出:canonical_entities.json, episodes.json, graph_{isolated,shared,
#       eventframe}.json, graph_audit.json, leakage_audit.json,
#       shortcut_audit.json, experiment_config.json
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# ── Step 1: canonical entity registry ────────────────────────────────
ENTITIES = ["人", "苹果", "树", "篮子", "厨房", "钥匙", "门", "箱子",
            "地窖", "面包"]
ALIASES = {"apple": "苹果", "一个苹果": "苹果", "那个人": "人",
           "厨房里": "厨房", "basket": "篮子"}

# ── Step 2: episodes(单事件框架)────────────────────────────────────
EPISODES = [
    {"id": "E1", "subject": "人", "action": "发现", "object": "苹果"},
    {"id": "E2", "subject": "苹果", "action": "位于", "object": "树"},
    {"id": "E3", "subject": "人", "action": "拿取", "object": "苹果"},
    {"id": "E4", "subject": "苹果", "action": "放入", "object": "篮子"},
    {"id": "E5", "subject": "人", "action": "携带", "object": "篮子"},
    {"id": "E6", "subject": "篮子", "action": "位于", "object": "厨房"},
    {"id": "E7", "subject": "钥匙", "action": "打开", "object": "门"},
    {"id": "E8", "subject": "门", "action": "位于", "object": "厨房"},
    {"id": "E9", "subject": "钥匙", "action": "位于", "object": "箱子"},
    {"id": "E10", "subject": "人", "action": "需要", "object": "钥匙"},
    {"id": "E11", "subject": "箱子", "action": "位于", "object": "地窖"},
    {"id": "E12", "subject": "地窖", "action": "位于", "object": "厨房"},
    {"id": "E13", "subject": "人", "action": "吃", "object": "面包"},
]

# ── 测试问题(金色链,预注册)────────────────────────────────────────
QUESTIONS = [
    {"id": "Q1", "type": "A", "text": "苹果 和 厨房 之间是什么关系?",
     "seeds": ["苹果", "厨房"],
     "gold_chain": ["苹果", "篮子", "厨房"],
     "gold_actions": ["放入", "位于"], "gold_episodes": ["E4", "E6"]},
    {"id": "Q2", "type": "A", "text": "钥匙 和 厨房 之间是什么关系?",
     "seeds": ["钥匙", "厨房"],
     "gold_chain": ["钥匙", "门", "厨房"],
     "gold_actions": ["打开", "位于"], "gold_episodes": ["E7", "E8"]},
    {"id": "Q3", "type": "B", "text": "树 和 厨房 之间是什么关系?",
     "seeds": ["树", "厨房"],
     "gold_chain": ["树", "苹果", "篮子", "厨房"],
     "gold_actions": ["位于", "放入", "位于"], "gold_episodes": ["E2", "E4", "E6"]},
    {"id": "Q4", "type": "B", "text": "篮子 和 钥匙 之间是什么关系?",
     "seeds": ["篮子", "钥匙"],
     "gold_chain": ["篮子", "人", "钥匙"],
     "gold_actions": ["携带", "需要"], "gold_episodes": ["E5", "E10"]},
    {"id": "Q5", "type": "C", "text": "树 和 门 都和什么实体或地点有关?",
     "seeds": ["树", "门"],
     "gold_chain": ["厨房"],
     "gold_actions": ["位于"], "gold_episodes": ["E6", "E8"]},
    {"id": "Q6", "type": "C", "text": "箱子 和 苹果 都和什么实体或地点有关?",
     "seeds": ["箱子", "苹果"],
     "gold_chain": ["厨房"],
     "gold_actions": ["位于"], "gold_episodes": ["E12", "E6"]},
]


def ent_node(entity, ep=None, isolated=False):
    """G1:实体按经历拆分(entity@E{id});G2/G3:canonical 共享节点。"""
    if isolated and ep is not None:
        return "%s@%s" % (entity, ep)
    return entity


def build_edges(isolated=False, eventframe=False):
    """返回 (nodes, edges)。
    G2:实体节点 + 主体-动作->客体 直连边(semantic_relation)。
    G3:G2 + 事件节点(declarative-episodic,cognitive_relation 边:
       事件-[主体]->subject、事件-[客体]->object、事件-[后继]->下一事件),
       事件节点保留 provenance 但实体仍共享。
    G1:isolated 实体拆分。"""
    nodes, edges = [], []
    seen = set()

    def add_node(nid, label="declarative-semantic", space="semantic"):
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id": nid, "label": label, "graph_space": space})

    def add_edge(src, dst, rel, w=0.9, cat="semantic_relation", src_sp="semantic", dst_sp="semantic"):
        edges.append({"src": src, "dst": dst, "relation": rel, "weight": w,
                      "relation_category": cat})
        add_node(src, "declarative-semantic", src_sp)
        add_node(dst, "declarative-semantic", dst_sp)

    for i, ep in enumerate(EPISODES):
        s = ent_node(ep["subject"], ep["id"], isolated)
        o = ent_node(ep["object"], ep["id"], isolated)
        if eventframe:
            eid = "事件:%s" % ep["id"]
            add_node(eid, "declarative-episodic", "episodic")
            add_edge(eid, s, "主体", 0.9, "cognitive_relation", "episodic")
            add_edge(eid, o, "客体", 0.9, "cognitive_relation", "episodic")
            if i > 0:
                prev = "事件:%s" % EPISODES[i - 1]["id"]
                edges.append({"src": eid, "dst": prev, "relation": "后继",
                              "weight": 0.6, "relation_category": "cognitive_relation"})
        else:
            add_edge(s, o, ep["action"], 0.9, "semantic_relation")
    return nodes, edges


def audit_graph(nodes, edges, isolated, eventframe, questions, episodes):
    """Audit A 实体重复 / B 经历隔离 / C 泄漏 / D 捷径。"""
    from collections import defaultdict
    adj = defaultdict(set)
    for e in edges:
        adj[e["src"]].add(e["dst"])
        adj[e["dst"]].add(e["src"])
    # 连通分量
    comps = []
    seen = set()
    for n in adj:
        if n in seen:
            continue
        comp, stack = set(), [n]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            stack.extend(adj[x] - comp)
        seen |= comp
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    largest = comps[0] if comps else set()
    # episode 归属(节点名 -> episode 集合)
    ep_of = defaultdict(set)
    for ep in episodes:
        for role in ("subject", "object"):
            name = ep[role]
            if isolated:
                ep_of["%s@%s" % (name, ep["id"])].add(ep["id"])
            else:
                ep_of[name].add(ep["id"])
    # Audit A:canonical 实体在非隔离图中应只有单一节点
    dup = []
    if not isolated:
        for e in ENTITIES:
            hits = [n for n in nodes if n["id"] == e]
            if len(hits) != 1:
                dup.append(e)
    # Audit B:非隔离图中 episode 间应连通(经共享实体)
    iso_components = [len(c) for c in comps if len(c) > 1]
    # Audit C/D:每问题金色链的边级存在性与捷径检查
    leak, shortcut = [], []
    edge_set = {(e["src"], e["dst"], e["relation"]) for e in edges}
    for q in questions:
        chain = q["gold_chain"]
        # 需要的边序列:chain 相邻实体对的动作边
        need = []
        for i in range(len(chain) - 1):
            a, b = chain[i], chain[i + 1]
            # 找到连接 a-b 的 episode(可能多跳中一个动作)
            eps_ = [ep for ep in episodes
                    if (ep["subject"] == a and ep["object"] == b)
                    or (ep["subject"] == b and ep["object"] == a)]
            need.append((a, b, eps_))
        # 单 episode 是否已含完整链?episode 数 >= 链长-1 才可能;
        # 关键:整条链的边集不能全部落在同一 episode 内
        ep_sets = [set(q["gold_episodes"])]
        full_in_one = any(set(q["gold_episodes"]) <= {ep} for ep in
                          set().union(*[set(x) for x in ep_sets]) if True) \
            if len(q["gold_episodes"]) == 1 else False
        if len(q["gold_episodes"]) == 1:
            leak.append({"q": q["id"], "reason": "gold chain in single episode"})
        # 捷径:直接边 首端->末端 或 反向
        a0, b0 = chain[0], chain[-1]
        if len(chain) > 2:
            if (a0, b0) in {(x[0], x[1]) for x in [(e[0], e[1]) for e in edge_set]} \
                    or any((x, y) in {(e["src"], e["dst"]) for e in edges}
                           for x in (a0,) for y in (b0,)):
                shortcut.append({"q": q["id"], "reason": "direct shortcut edge"})
    return {
        "num_nodes": len(nodes), "num_edges": len(edges),
        "num_components": len(comps),
        "largest_component": len(largest),
        "largest_component_fraction": round(len(largest) / max(1, len(nodes)), 3),
        "isolated_entity_dup": dup,
        "episode_isolated_components": sorted(iso_components, reverse=True)[:8],
        "leakage": leak, "shortcut": shortcut,
    }


def shared_entity_stats(nodes, edges, episodes, isolated):
    from collections import defaultdict
    ep_of = defaultdict(set)
    name_of = {}
    for ep in episodes:
        for role in ("subject", "object"):
            name = ep[role]
            if isolated:
                base = name
                nid = "%s@%s" % (name, ep["id"])
            else:
                base = name
                nid = name
            ep_of[nid].add(ep["id"])
            name_of[nid] = base
    shared = {n: eps for n, eps in ep_of.items() if len(eps) >= 2}
    return {
        "num_shared_entities": len(shared) if not isolated else 0,
        "shared_entities": {n: sorted(e) for n, e in sorted(shared.items())}
        if not isolated else {},
    }


def main():
    os.makedirs(HERE, exist_ok=True)
    graphs = {}
    audits = {}
    for name, iso, ef in (("isolated", True, False),
                          ("shared", False, False),
                          ("eventframe", False, True)):
        nodes, edges = build_edges(isolated=iso, eventframe=ef)
        graphs[name] = {"nodes": nodes, "edges": edges}
        audits[name] = audit_graph(nodes, edges, iso, ef, QUESTIONS, EPISODES)
        audits[name].update(shared_entity_stats(nodes, edges, EPISODES, iso))
        # 每节点 episode 归属(机制指标用)
        from collections import defaultdict
        ep_of = defaultdict(set)
        for ep in EPISODES:
            for role in ("subject", "object"):
                nid = ("%s@%s" % (ep[role], ep["id"])) if iso else ep[role]
                ep_of[nid].add(ep["id"])
        graphs[name]["episode_of"] = {k: sorted(v) for k, v in ep_of.items()}

    out = {
        "canonical_entities": {"entities": ENTITIES, "aliases": ALIASES},
        "episodes": EPISODES,
        "questions": QUESTIONS,
        "graphs": graphs,
        "audits": audits,
    }
    for key, obj in (("canonical_entities.json", out["canonical_entities"]),
                     ("episodes.json", out["episodes"]),
                     ("experiment_config.json", out),
                     ("graph_audit.json", out["audits"])):
        with open(os.path.join(HERE, key), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    for name, g in graphs.items():
        with open(os.path.join(HERE, "graph_%s.json" % name), "w",
                  encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False, indent=1)
    # 泄漏/捷径审计(全局)
    leak_all = {k: v["leakage"] for k, v in audits.items()}
    short_all = {k: v["shortcut"] for k, v in audits.items()}
    json.dump(leak_all, open(os.path.join(HERE, "leakage_audit.json"), "w",
                             encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(short_all, open(os.path.join(HERE, "shortcut_audit.json"), "w",
                              encoding="utf-8"), ensure_ascii=False, indent=1)
    for name in graphs:
        a = audits[name]
        print("%-11s nodes=%d edges=%d comps=%d largest=%.2f shared=%d "
              "leak=%d shortcut=%d" % (
                  name, a["num_nodes"], a["num_edges"], a["num_components"],
                  a["largest_component_fraction"], a["num_shared_entities"],
                  len(a["leakage"]), len(a["shortcut"])))


if __name__ == "__main__":
    main()
