# run_exp_a2_reuse.py — 实验 A 升级版：Knowledge Write-back → Future Reuse
# （Paper-I 收尾 campaign PHASE 3；实验基础设施新增，核心机制零改动）
# ─────────────────────────────────────────────────────────────────────
# 因果问题（R1）：经验写回是否导致未来相似任务中的知识访问与行为差异？
#
# 升级点（相对 run_exp_reuse v3）：
#   1. 环境更复杂：A1 新任务 = obtain oak_fence（oak_log→oak_planks→
#      stick→crafting_table(needs_table)→oak_fence——资源→加工→中间物→
#      目标物，含工具性中介与重组）；A2 复用任务 = obtain wooden_pickaxe
#      （结构同构：planks+stick+table；表面不同：围栏 vs 镐）。
#   2. 条件补齐 B0/B1/B2（§7）：
#      B0 fresh（无 Ep1）；B1 = 继承"写回禁用 Ep1"的图（learned graph
#      但无写回产物）；B2 = 继承"写回启用 Ep1"的图。
#      三条件账本均为新实例（inherit_causal 不传）——差分隔离在图谱
#      写回通道，不混入 C 实验已证的同进程账本通道。
#   3. 指标补齐（§7）：time-to-solution / action count / failed actions /
#      graph retrieval（关键节点激活）/ path length / reuse count
#      （Ep2 动作命中 Ep1 晋升"操作:"节点的次数）/ action_prior 命中。
# 防捷径（§19）：三条件同世界同 seed 同布局；目标注入（experiment_obtain）
#   三条件同构；reuse 不可经 if/else——走既有 graph/diffusion/closure/
#   action_prior 机制（autonomy.py:2790 消费点）。
# 世界数据：oak_fence/wooden_pickaxe 配方在 sandbox RECIPE_TABLE（环境
#   数据层，非 agent 代码）；needs_table 结算经 craft 技能 _find_blocks。
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

TASK1 = "oak_fence"        # A1 新任务（4 步重组链）
TASK2 = "wooden_pickaxe"   # A2 复用任务（结构同构、表面不同）
ORE = "oak_log"
GATHERABLES = [ORE]

# 世界布局（每条件同 seed 同布局；A2 复用任务的世界与 A1 同构重置）
TREES = [(-4, -4), (4, -4), (-4, 4), (4, 4), (0, 2)]

# 共享结构动作（A1/A2 同构的中间物动作）——reuse/路径效率差分的命中面
SHARED_ACTIONS = ("gather_resource@oak_log", "craft_item@oak_planks",
                  "craft_item@stick", "craft_item@crafting_table")
# 激活采样面（知识访问层）
SAMPLE_IDS = ("oak_log", "物品:oak_planks", "物品:stick",
              "物品:crafting_table", "物品:oak_fence", "物品:wooden_pickaxe")


def plant_tree(w, x, z, log=ORE):
    for i in range(3):
        for j in range(3):
            w.put_block(x + i - 1, 65 + j, z, log)
    w.put_block(x, 65, z, log)


def bridge_perception_gaps(st):
    """感知接线补口（C17b 同款，见 run_exp_reuse.py）。"""
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


def sample_activations(st, rec, tick_i):
    snap = {}
    for nid in SAMPLE_IDS:
        nd = st.kg.nodes.get(nid)
        if nd is not None:
            snap[nid] = round(float(getattr(nd, "activation", 0.0) or 0.0), 4)
    rec.log("ACTSNAP", json.dumps(snap, ensure_ascii=False), tick=tick_i)


def collect_stats(st, rec, name):
    """由 timeline 派生指标（ACTION + 紧随 SELF_STATE_CHANGE 判失败）。"""
    raw = getattr(st.tl, "_raw", []) or []
    acts = [e for e in raw if e.get("event_type") == "ACTION"]
    results = [e for e in raw if e.get("event_type") == "SELF_STATE_CHANGE"]
    types = [str(a.get("subject") or "") for a in acts]
    tgts = [str((a.get("content") or {}).get("target") or "") for a in acts]
    invalid = 0
    res_i = 0
    for a in acts:
        subj = str(a.get("subject") or "")
        while (res_i < len(results)
               and str(results[res_i].get("subject") or "") != subj):
            res_i += 1
        if (res_i < len(results)
                and str((results[res_i].get("content") or {}).get(
                    "change") or "").startswith("failed")):
            invalid += 1
        res_i += 1
    seen, redundant = set(), 0
    for t, tg in zip(types, tgts):
        k = f"{t}@{tg}"
        if k in seen:
            redundant += 1
        seen.add(k)
    # path length = 去重后动作序列长度；首动 = 第一个动作
    path_len = len(seen)
    first_action = f"{types[0]}@{tgts[0]}" if types else None
    # reuse count：命中共享结构动作的次数
    seq = [f"{t}@{tg}" for t, tg in zip(types, tgts)]
    reuse_hits = sum(1 for s in seq if s in SHARED_ACTIONS)
    # action_prior（同进程账本通道——本实验三条件均为新账本，命中应仅
    # 来自本 episode 内累计；记录以核对非跨条件泄漏）
    ap = {}
    try:
        priors = getattr(st.causal, "action_priors", None) or {}
        for k, v in list(priors.items()):
            ap[str(k)] = (getattr(v, "successes", None), ) if not isinstance(
                v, (int, float, tuple)) else (v, )
    except Exception:
        pass
    m = {"episode": name, "actions_total": len(types),
         "actions_redundant": redundant, "actions_invalid": invalid,
         "path_len_unique": path_len, "first_action": first_action,
         "shared_action_hits": reuse_hits, "types": types, "targets": tgts,
         "inventory": dict(st.world.inv_map()), "graph": graph_stats(st),
         "action_prior_sample": ap,
         "causal": {"aggregations": len(getattr(st.causal, "_aggregations",
                                                {}) or {}),
                    "hypotheses": len(getattr(st.causal, "_hypotheses",
                                              {}) or {}),
                    "promoted": sorted(getattr(st.causal, "_promoted",
                                               set()) or set())}}
    rec.log("EPISODE", json.dumps(m, ensure_ascii=False))
    return m


def run_episode(goal, seed, label, max_ticks, rounds=1, writeback=True,
                inherit=None):
    """跑一个 episode（rounds>1 时单 stack 串行多轮 practice）。"""
    rec = er.RunRecorder(family="reuse_a2", mode="sandbox", seed=seed,
                         label=label)
    rec.start({"goal": goal, "seed": seed, "rounds": rounds,
               "writeback": writeback, "inherit": bool(inherit)})
    clk = Clock()
    st = build_stack(goal=goal, label=label, seed=seed,
                     mc_gatherable=GATHERABLES, writeback_on=writeback,
                     inherit_graph=inherit)
    for (x, z) in TREES:
        plant_tree(st.world, x, z)
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    rounds_done = {}
    for rnd in range(1, rounds + 1):
        if rnd > 1:
            plant_tree(st.world, 0, -4, ORE)
            st.world.set_inv()
            st.loop.add_goal({"type": "obtain", "target": goal,
                              "source": "experiment_obtain",
                              "text": f"实验目标：自主获得 {goal}（第{rnd}轮）"})
        done_at = None
        for i in range(max_ticks):
            tick(st, clk)
            bridge_perception_gaps(st)
            if i % 2 == 0:
                sample_activations(st, rec, i)
            if have_item(st, goal) > 0:
                done_at = i
                rec.log("RESULT", f"第{rnd}轮达成 {goal} @tick{i}", tick=i)
                break
        rounds_done[rnd] = done_at
        print(f"[{label}] round{rnd} {goal} done@{done_at} "
              f"inv={dict(st.world.inv_map())}", flush=True)
    flush_causal(st)
    stats = collect_stats(st, rec, f"{label}_final")
    stats["rounds_done"] = rounds_done
    if rounds > 1:
        if not stats["causal"]["promoted"]:
            print(f"[WRITEBACK-LOG] {label}: 无 promote 写回！"
                  f"aggs={stats['causal']['aggregations']} "
                  f"hyps={stats['causal']['hypotheses']}", flush=True)
        else:
            print(f"[WRITEBACK-LOG] {label}: promote "
                  f"{len(stats['causal']['promoted'])} 条", flush=True)
    gpath = os.path.join(er.root_dir(), "reuse_a2", f"{label}_graph_s{seed}.json")
    os.makedirs(os.path.dirname(gpath), exist_ok=True)
    with open(gpath, "w", encoding="utf-8") as f:
        json.dump(st.kg.to_dict(), f, ensure_ascii=False)
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra={"graph_path": gpath,
                                 "causal": stats["causal"],
                                 "rounds_done": rounds_done})
    print(f"[{label}] causal={stats['causal']}", flush=True)
    return {"path": gpath, "stats": stats}


def start_activation_snapshot(st_key_values, rec):
    rec.log("ACTSTART", json.dumps(st_key_values, ensure_ascii=False))


def ep2_condition(cond, seed, max_ticks, inherit=None):
    """A2 复用 episode：obtain wooden_pickaxe。"""
    rec = er.RunRecorder(family="reuse_a2", mode="sandbox", seed=seed,
                         label=f"A2-{cond}")
    rec.start({"episode": "ep2", "condition": cond, "goal": TASK2,
               "seed": seed, "inherit": bool(inherit)})
    clk = Clock()
    st = build_stack(goal=TASK2, label=f"a2_{cond}", seed=seed,
                     mc_gatherable=GATHERABLES, inherit_graph=inherit)
    for (x, z) in TREES:
        plant_tree(st.world, x, z)
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    start_act = {}
    for nid in SAMPLE_IDS:
        nd = st.kg.nodes.get(nid)
        if nd is not None:
            start_act[nid] = round(float(getattr(nd, "activation", 0.0)
                                         or 0.0), 4)
    start_activation_snapshot(start_act, rec)
    done_at = None
    for i in range(max_ticks):
        tick(st, clk)
        bridge_perception_gaps(st)
        if i % 2 == 0:
            sample_activations(st, rec, i)
        if have_item(st, TASK2) > 0:
            done_at = i
            rec.log("RESULT", f"A2 背包获 {TASK2} @tick{i}", tick=i)
            break
    flush_causal(st)
    stats = collect_stats(st, rec, f"A2_{cond}")
    wb_nodes = sorted(i for i in st.kg.nodes
                      if str(i).startswith(("操作:", "变化:")))
    stats["inherited_wb_nodes"] = len(wb_nodes)
    stats["start_activation"] = start_act
    stats["success"] = done_at is not None
    stats["success_tick"] = done_at
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra=stats)
    print(f"[A2:{cond}] success={stats['success']} @{done_at} "
          f"actions={stats['actions_total']} red={stats['actions_redundant']} "
          f"shared_hits={stats['shared_action_hits']} "
          f"wb_nodes={len(wb_nodes)} first={stats['first_action']}",
          flush=True)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=["pilot", "B0", "B1", "B2"],
                    required=True,
                    help="pilot=A1 单谱系试跑（验证 oak_fence 链可达）")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-ticks", type=int, default=250)
    args = ap.parse_args()
    out = {"experiment": "A2", "condition": args.condition,
           "seed": args.seed, "max_ticks": args.max_ticks}
    if args.condition == "pilot":
        out["ep1"] = run_episode(TASK1, args.seed, "A1_pilot",
                                 args.max_ticks, rounds=2, writeback=True)
        out["ep2"] = ep2_condition("B2", args.seed, args.max_ticks,
                                   inherit=out["ep1"]["path"])
    elif args.condition in ("B1", "B2"):
        # Ep1 谱系：B2 = 写回启用；B1 = 写回禁用（learned graph 无写回产物）
        out["ep1"] = run_episode(TASK1, args.seed, f"A1_{args.condition}",
                                 args.max_ticks, rounds=8,
                                 writeback=(args.condition == "B2"))
        out["ep2"] = ep2_condition(args.condition, args.seed, args.max_ticks,
                                   inherit=out["ep1"]["path"])
    else:  # B0
        out["ep2"] = ep2_condition("B0", args.seed, args.max_ticks,
                                   inherit=None)
    fn = os.path.join(er.root_dir(), "reuse_a2",
                      f"a2_{args.condition}_seed{args.seed}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n[A2] {args.condition} seed={args.seed} → {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
