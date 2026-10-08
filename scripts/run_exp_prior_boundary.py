# run_exp_prior_boundary.py — 实验 J：先验知识依赖边界（多 episode 累积）
# （Paper-I 收尾 campaign PHASE 12。实验基础设施新增，核心机制零改动）
# ─────────────────────────────────────────────────────────────────────
# 问题（§16）：不试图证明"无先验也能学习"（A=0/6 已否定），而是回答：
#   最小先验 + 经验累积能否完成任务？——FAS 对先验的依赖边界在哪。
#
# 条件（复用 build_stack prior_level 既有操作化）：
#   P0 = A：图谱先验查询关 + 配方/方块机制表清空 + 无任务先验
#        （行为只能从感知/扩散/因果累积涌现——机制上无从"计划合成"）
#   P1 = B：机制表保留（世界机制知识），图谱先验边关
#   P2 = C：完整先验（图谱先验边 + 机制表 + 任务先验）
# 维度（新）：单 stack 串行 5 episode——经验跨轮累积（图/账本/激活连续），
#   测"累积能否替代先验"。
# 指标：逐轮达成/tick/动作、图谱增长曲线、写回计数、跨轮效率趋势。
# 预登记判据：P0 全 0（机制表=计划合成必要先验，边界确认）；
#   P1≈P2 ceiling（先验边在短链可或缺）；累积不改边界（诚实接受）。
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
PRIOR_LEVEL = {"P0": "A", "P1": "B", "P2": "C"}


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


def run_condition(cond, seed, rounds=5, max_ticks=120):
    rec = er.RunRecorder(family="prior_boundary", mode="sandbox", seed=seed,
                         label=f"J-{cond}")
    rec.start({"condition": cond, "prior_level": PRIOR_LEVEL[cond],
               "seed": seed, "rounds": rounds})
    clk = Clock()
    st = build_stack(goal=TARGET, label=f"pb_{cond}", seed=seed,
                     mc_gatherable=GATHERABLES,
                     prior_level=PRIOR_LEVEL[cond])
    plant_tree(st.world, *TREES[0])
    g0 = graph_stats(st)
    episodes = []
    for rnd in range(1, rounds + 1):
        if rnd > 1:
            plant_tree(st.world, *TREES[0])
            st.world.set_inv()
            st.loop.add_goal({"type": "obtain", "target": TARGET,
                              "source": "experiment_obtain",
                              "text": f"实验目标：自主获得 {TARGET}（第{rnd}轮）"})
        done_at, n_actions = None, 0
        for i in range(max_ticks):
            tick(st, clk)
            bridge_perception_gaps(st)
            pass  # 动作计数由事件层统计（此处省略，不阻塞主指标）
            if have_item(st, TARGET) > 0:
                done_at = i
                break
        ep = {"round": rnd, "success": done_at is not None,
              "done_tick": done_at, "graph": graph_stats(st),
              "promoted": len(getattr(st.causal, "_promoted", set()) or set())
              if st.causal else 0}
        episodes.append(ep)
        rec.log("EPISODE", json.dumps(ep))
        print(f"[J:{cond}] r{rnd} succ={ep['success']} @{done_at} "
              f"graph={ep['graph']}", flush=True)
    flush_causal(st)
    stats = {"condition": cond, "prior_level": PRIOR_LEVEL[cond],
             "seed": seed, "episodes": episodes,
             "n_success": sum(1 for e in episodes if e["success"]),
             "graph_growth": {k: episodes[-1]["graph"][k] - g0[k]
                              for k in g0}}
    rec.finalize(ok=True, extra=stats)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=list(PRIOR_LEVEL), required=True)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    stats = run_condition(args.condition, args.seed)
    out = {"experiment": "J_prior_boundary", "result": stats}
    fn = os.path.join(er.root_dir(), "prior_boundary",
                      f"expJ_{args.condition}_seed{args.seed}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[J] {args.condition} seed={args.seed} → {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
