# run_exp_hebbian.py — 实验 F：Hebbian 共激活强化的直接测量
# （Paper-I 收尾 campaign PHASE 8；R8。实验基础设施新增，核心机制零改动）
# ─────────────────────────────────────────────────────────────────────
# 因果问题：重复共激活是否真改变未来检索/激活（而不只是 weight += ε）？
#
# 机制事实（预审计 §1.5）：diffusion_engine._hebbian（:268）在扩散中共激活
#   边强化：config.hebbian.enabled（默认 True）、类别白名单
#   semantic_relation/emotional_relation、ε=0.0015、单边相对首见基线封顶
#   max_gain=0.5。**运行时不落盘** → 必须同进程串行 episode 测量。
#
# 设计：单 stack 串行 10 轮 obtain oak_planks（每轮世界重置、图谱/账本/
#   激活连续——每轮重复"oak_log 采集→oak_planks 合成"共激活序列）。
#   H0 = ON（默认）；H1 = OFF（hebbian.enabled=False 装配参数）。
# 指标（§12）：每轮结束后记录
#   - 白名单类别全部边权（逐边 trace；H0 应单调升至 cap，H1 应恒定）
#   - 目标节点（物品:oak_planks）本轮激活时延（episode 起点→首亮 ≥0.05）
#   - 本轮激活占比（activation probability：≥0.05 拍数/总拍数）
#   - 合成时刻检索名次（retrieval rank）
#   - 达成 tick / 动作数（行为面参照，不作达成声称）
# 判据（§12）：不只 weight 变，还要 future retrieval/activation 变——
#   比较轮 0/1/3/5/10 的时延与名次趋势（H0 vs H1）。
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

TARGET = "oak_planks"
ORE = "oak_log"
GATHERABLES = [ORE]
TREES = [(-3, -3)]
TRACK_NODES = ("物品:oak_planks", "物品:oak_log", "oak_log")


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


def whitelisted_edges(st):
    """白名单类别（Hebbian 可改写面）的边权快照。"""
    wl = {"semantic_relation", "emotional_relation"}
    out = {}
    for e in getattr(st.kg, "edges", []) or []:
        if str(getattr(e, "relation_category", "")) in wl:
            k = f"{e.src}|{e.relation}|{e.dst}"
            out[k] = round(float(getattr(e, "weight", 0.0) or 0.0), 5)
    return out


def rank_of(st, nid):
    a = float(getattr(st.kg.nodes.get(nid), "activation", 0.0) or 0.0)
    return 1 + sum(1 for nd in st.kg.nodes.values()
                   if float(getattr(nd, "activation", 0.0) or 0.0) > a)


def run_condition(cond, seed, rounds=10, max_ticks=60):
    hebb_on = (cond == "H0")
    rec = er.RunRecorder(family="hebbian", mode="sandbox", seed=seed,
                         label=f"F-{cond}")
    rec.start({"condition": cond, "seed": seed, "rounds": rounds,
               "hebbian_on": hebb_on})
    clk = Clock()
    st = build_stack(goal=TARGET, label=f"hb_{cond}", seed=seed,
                     mc_gatherable=GATHERABLES)
    if not hebb_on:
        st.engine.config["hebbian"] = {"enabled": False}
    plant_tree(st.world, *TREES[0])
    episodes = []
    for rnd in range(1, rounds + 1):
        if rnd > 1:
            plant_tree(st.world, *TREES[0])   # 补树（原位）
            st.world.set_inv()                # 清背包：每轮从零再达成
            # 激活淬火（测量控制，两条件同协议）：运行时激活清零，隔离
            # "权重通道"——否则上轮激活残值使 lit@0 饱和，时延不可测。
            # 边权（Hebbian 产物）与图谱结构不受影响（激活是运行时态）。
            for _nd in st.kg.nodes.values():
                _nd.activation = 0.0
            st.engine.name_to_node = dict(st.kg.nodes)
            st.loop.add_goal({"type": "obtain", "target": TARGET,
                              "source": "experiment_obtain",
                              "text": f"实验目标：自主获得 {TARGET}（第{rnd}轮）"})
        lit_tick, craft_rank, hot_ticks = None, None, 0
        done_at = None
        for i in range(max_ticks):
            tick(st, clk)
            bridge_perception_gaps(st)
            nd = st.kg.nodes.get("物品:oak_planks")
            a = float(getattr(nd, "activation", 0.0) or 0.0) if nd else 0.0
            if a >= 0.05:
                hot_ticks += 1
                if lit_tick is None:
                    lit_tick = i
            if a >= 0.5 and craft_rank is None:
                craft_rank = rank_of(st, "物品:oak_planks")
            if have_item(st, TARGET) > 0:
                done_at = i
                break
        ew = whitelisted_edges(st)
        ep = {"round": rnd, "done_tick": done_at, "lit_tick": lit_tick,
              "hot_ticks": hot_ticks, "craft_rank": craft_rank,
              "edge_weights": ew,
              "w_sum": round(sum(ew.values()), 4)}
        episodes.append(ep)
        rec.log("EPISODE", json.dumps(ep, ensure_ascii=False))
        print(f"[F:{cond}] r{rnd} done@{done_at} lit@{lit_tick} "
              f"hot={hot_ticks} rank@craft={craft_rank} wsum={ep['w_sum']}",
              flush=True)
    flush_causal(st)
    first, last = episodes[0]["edge_weights"], episodes[-1]["edge_weights"]
    deltas = {k: round(last.get(k, 0.0) - first.get(k, 0.0), 5)
              for k in set(first) | set(last)}
    stats = {"condition": cond, "seed": seed, "episodes": episodes,
             "edge_weight_deltas": deltas,
             "n_edges_changed": sum(1 for v in deltas.values() if abs(v) > 1e-9),
             "graph": graph_stats(st)}
    rec.finalize(ok=True, extra={k: v for k, v in stats.items()
                                 if k != "episodes"})
    print(f"[F:{cond}] edges_changed={stats['n_edges_changed']} "
          f"deltas_nonzero={ {k: v for k, v in deltas.items() if v} }",
          flush=True)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=["H0", "H1"], required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rounds", type=int, default=10)
    args = ap.parse_args()
    stats = run_condition(args.condition, args.seed, rounds=args.rounds)
    out = {"experiment": "F_hebbian", "condition": args.condition,
           "seed": args.seed, "result": stats}
    fn = os.path.join(er.root_dir(), "hebbian",
                      f"expF_{args.condition}_seed{args.seed}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[F] {args.condition} seed={args.seed} → {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
