# run_exp_event_transfer.py — 实验 G：层级事件结构 / 跨情境复用
# （Paper-I 收尾 campaign PHASE 10；R9。实验基础设施新增，核心机制零改动）
# ─────────────────────────────────────────────────────────────────────
# 因果问题：结构化事件知识（事件框架/行动经验节点经共享实体与语义耦合）
#   的跨情境复用，是否强于"扁平事实记忆"？
#
# 不做类比推理声称（§13）；只判"结构化事件知识的跨情境 reuse 是否存在"。
#
# 设计：
#   Ep1（情境 A：橡木带在西，裸地）：8 轮 obtain crafting_table 实践
#     （晋升线驱动写回：操作/变化 节点 + 导致边 + mark_active）。
#   Ep2（情境 B：橡木带在东 + birch 干扰 + 不同布局，世界重置）：
#     obtain wooden_pickaxe（结构同构延伸：planks+stick+table）。
#   三条件（账本均新实例——隔离图谱结构通道，不混 C 实验的账本通道）：
#     G0 结构继承：继承完整图谱（含情景事件结构）
#     G1 扁平记忆：继承图**剥离情景空间节点**（episodic 事件框架/行动
#        经验不上场，语义事实保留）——装配层过滤，两条件事实量同构
#     G2 无复用：干净图
# 指标（§13）：跨事件检索（Ep1 行动_/操作: 节点在 Ep2 的激活）、
#   结构关系激活（导致/涉及边端点）、迁移成功、动作数/失败数/路径效率、
#   共享结构动作命中。
# ─────────────────────────────────────────────────────────────────────

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(1, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
if os.getcwd() != os.path.dirname(os.path.dirname(os.path.abspath(__file__))):
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandbox_lab import build_stack, tick, have_item, Clock, flush_causal, \
    graph_stats  # noqa
import experiment_recorder as er                                          # noqa

TASK1 = "crafting_table"
TASK2 = "wooden_pickaxe"
ORE = "oak_log"
NOISE = "birch_log"
GATHERABLES = [ORE, NOISE]
EP1_TREES = [(-4, -4), (-2, -4), (-4, -2)]     # 情境 A：西
EP2_TREES = [(5, 3), (7, 3)]                   # 情境 B：东（不同布局）
EP2_NOISE = [(0, 4)]
SHARED_ACTIONS = ("gather_resource@oak_log", "craft_item@oak_planks",
                  "craft_item@stick", "craft_item@crafting_table")


def plant_tree(w, x, z, log=ORE):
    for i in range(3):
        for j in range(3):
            w.put_block(x + i - 1, 65 + j, z, log)
    w.put_block(x, 65, z, log)


def bridge_perception_gaps(st):
    try:
        import prior_knowledge as pk
        from graph_model import Node
        for nid in list(st.kg.nodes):
            if not str(nid).startswith("UnknownBlock_"):
                continue
            nm = str(nid)[len("UnknownBlock_"):]
            if not nm:
                continue
            iid = f"物品:{nm}"
            if iid not in st.kg.nodes:
                st.kg.add_node(Node(
                    id=iid, weight=0.4, label="declarative-semantic",
                    graph_space="semantic",
                    extra_attrs={"type": "inventory_item",
                                 "name": nm, "count": 0}),
                    source="exp-perception-bridge")
            pk.consider_recognition(st.kg, st.engine, nm, st.loop.config)
    except Exception:
        pass


def flatten_graph(src_path, dst_path):
    """G1 装配过滤：剥离情景空间节点及其边（事件框架/行动经验不上场），
    语义/认知空间事实全保留。世界事实量与 G0 同构，只差事件结构。"""
    from graph_model import KnowledgeGraph
    g = KnowledgeGraph.load(src_path)
    drop = {nid for nid, nd in g.nodes.items()
            if str(getattr(nd, "graph_space", "")) == "episodic"}
    keep_nodes = {nid: nd for nid, nd in g.nodes.items()
                  if nid not in drop}
    keep_edges = [e for e in (getattr(g, "edges", []) or [])
                  if getattr(e, "src", None) not in drop
                  and getattr(e, "dst", None) not in drop]
    out = KnowledgeGraph()
    for nid, nd in keep_nodes.items():
        out.nodes[nid] = nd
    out.edges = list(keep_edges)
    out.rebuild_indexes()
    with open(dst_path, "w", encoding="utf-8") as f:
        json.dump(out.to_dict(), f, ensure_ascii=False)
    return dst_path, len(drop)


def collect_stats(st, rec, name):
    raw = getattr(st.tl, "_raw", []) or []
    acts = [e for e in raw if e.get("event_type") == "ACTION"]
    results = [e for e in raw if e.get("event_type") == "SELF_STATE_CHANGE"]
    types = [str(a.get("subject") or "") for a in acts]
    tgts = [str((a.get("content") or {}).get("target") or "") for a in acts]
    invalid = sum(
        1 for e in results
        if str((e.get("content") or {}).get("change") or "").startswith(
            "failed"))
    seen, redundant = set(), 0
    for t, tg in zip(types, tgts):
        k = f"{t}@{tg}"
        if k in seen:
            redundant += 1
        seen.add(k)
    seq = [f"{t}@{tg}" for t, tg in zip(types, tgts)]
    m = {"episode": name, "actions_total": len(types),
         "actions_redundant": redundant, "actions_invalid": invalid,
         "path_len_unique": len(seen),
         "first_action": seq[0] if seq else None,
         "shared_action_hits": sum(1 for s in seq if s in SHARED_ACTIONS),
         "inventory": dict(st.world.inv_map()), "graph": graph_stats(st)}
    rec.log("EPISODE", json.dumps(m, ensure_ascii=False))
    return m


def ep1_practice(seed, max_ticks):
    rec = er.RunRecorder(family="event_transfer", mode="sandbox", seed=seed,
                         label="G-ep1")
    rec.start({"episode": "ep1", "task": TASK1, "seed": seed})
    clk = Clock()
    st = build_stack(goal=TASK1, label="g_ep1", seed=seed,
                     mc_gatherable=GATHERABLES)
    for (x, z) in EP1_TREES:
        plant_tree(st.world, x, z)
    rounds_done = {}
    for rnd in range(1, 9):
        if rnd > 1:
            plant_tree(st.world, -3, -3)
            st.world.set_inv()
            st.loop.add_goal({"type": "obtain", "target": TASK1,
                              "source": "experiment_obtain",
                              "text": f"实验目标：自主获得 {TASK1}（第{rnd}轮）"})
        done_at = None
        for i in range(max_ticks):
            tick(st, clk)
            bridge_perception_gaps(st)
            if have_item(st, TASK1) > 0:
                done_at = i
                break
        rounds_done[rnd] = done_at
    flush_causal(st)
    stats = collect_stats(st, rec, "ep1_final")
    promoted = len(stats["causal"]["promoted"]) if stats.get("causal") else 0
    gpath = os.path.join(er.root_dir(), "event_transfer",
                         f"g1_ep1_graph_seed{seed}.json")
    os.makedirs(os.path.dirname(gpath), exist_ok=True)
    with open(gpath, "w", encoding="utf-8") as f:
        json.dump(st.kg.to_dict(), f, ensure_ascii=False)
    rec.finalize(ok=True, extra={"rounds": rounds_done,
                                 "promoted": promoted,
                                 "graph_path": gpath})
    print(f"[G:Ep1] rounds={rounds_done} promoted={promoted}", flush=True)
    return {"path": gpath, "promoted": promoted}


def ep2_condition(cond, seed, max_ticks, inherit_path=None):
    rec = er.RunRecorder(family="event_transfer", mode="sandbox", seed=seed,
                         label=f"G-{cond}")
    rec.start({"episode": "ep2", "condition": cond, "task": TASK2,
               "seed": seed, "inherit": bool(inherit_path)})
    clk = Clock()
    st = build_stack(goal=TASK2, label=f"g_{cond}", seed=seed,
                     mc_gatherable=GATHERABLES, inherit_graph=inherit_path)
    for (x, z) in EP2_TREES:
        plant_tree(st.world, x, z)
    for (x, z) in EP2_NOISE:
        plant_tree(st.world, x, z, NOISE)
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    # 跨事件检索观测面：Ep1 写回的 操作:/行动_ 节点在 Ep2 的激活
    ep1_marks = sorted(
        i for i in st.kg.nodes
        if str(i).startswith(("操作:", "变化:", "行动_")))[:12]
    done_at = None
    for i in range(max_ticks):
        tick(st, clk)
        bridge_perception_gaps(st)
        if have_item(st, TASK2) > 0:
            done_at = i
            break
    flush_causal(st)
    stats = collect_stats(st, rec, f"ep2_{cond}")
    mark_act = {nid: round(float(getattr(st.kg.nodes.get(nid), "activation",
                                          0.0) or 0.0), 4)
                for nid in ep1_marks}
    stats["ep1_mark_nodes"] = len(ep1_marks)
    stats["ep1_mark_activated"] = sum(1 for v in mark_act.values()
                                      if v >= 0.05)
    stats["ep1_mark_activations"] = mark_act
    stats["success"] = done_at is not None
    stats["success_tick"] = done_at
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra={k: v for k, v in stats.items()
                                 if k != "ep1_mark_activations"})
    print(f"[G:{cond}] succ={stats['success']} @{done_at} "
          f"act={stats['actions_total']} red={stats['actions_redundant']} "
          f"shared={stats['shared_action_hits']} "
          f"marks={stats['ep1_mark_activated']}/{stats['ep1_mark_nodes']}",
          flush=True)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=["G0", "G1", "G2"], required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-ticks", type=int, default=200)
    args = ap.parse_args()
    out = {"experiment": "G_event_transfer", "condition": args.condition,
           "seed": args.seed}
    if args.condition == "G0":
        ep1 = ep1_practice(args.seed, args.max_ticks)
        out["ep1"] = {"promoted": ep1["promoted"]}
        out["ep2"] = ep2_condition("G0", args.seed, args.max_ticks,
                                   inherit_path=ep1["path"])
    elif args.condition == "G1":
        ep1 = ep1_practice(args.seed, args.max_ticks)
        flat = os.path.join(er.root_dir(), "event_transfer",
                            f"g1_flat_graph_seed{args.seed}.json")
        flat, dropped = flatten_graph(ep1["path"], flat)
        out["ep1"] = {"promoted": ep1["promoted"], "dropped_episodic": dropped}
        out["ep2"] = ep2_condition("G1", args.seed, args.max_ticks,
                                   inherit_path=flat)
    else:
        out["ep2"] = ep2_condition("G2", args.seed, args.max_ticks,
                                   inherit_path=None)
    fn = os.path.join(er.root_dir(), "event_transfer",
                      f"expG_{args.condition}_seed{args.seed}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[G] {args.condition} seed={args.seed} → {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
