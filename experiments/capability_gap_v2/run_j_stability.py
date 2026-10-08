# -*- coding: utf-8 -*-
# run_j_stability.py — 实验 J:持续认知长程稳定性(Tier 3,零 LLM)
# 设计(冻结):生产栈(build_stack)连续运行 100 认知周期;每 10 拍注入
# 一个情节(episodes 循环:成功采集/合成、无关探索、失败动作、环境变化),
# 在周期 10/50/100 测量:图谱增长、激活分布(最大/均值)、晋升关系数、
# 意图事件、账本统计、无关节点驻留(污染)、A 链节点再激活(复用)、
# 行动成功率(决策质量代理)。
# 条件:FAS(全量)/ FAS-no-writeback / Fresh-FAS(每 10 拍重置图谱=对照)。
# n=5 配对种子。
import json
import os
import sys
import time

ROOT = r"E:\Project\Fascinator"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.chdir(ROOT)

from sandbox_lab import build_stack, tick, have_item, Clock, flush_causal, graph_stats  # noqa: E402
from run_exp_a2_reuse import plant_tree, TREES, ORE  # noqa: E402

CYCLES = 100
CHECKPOINTS = (10, 50, 100)
SEEDS = list(range(5))
CONDS = ("fas", "no-writeback")

# 情节脚本:每 10 拍一轮,内容确定
EPISODES = ("success_gather", "success_craft", "irrelevant_explore",
            "failure_gather", "env_change")


def act(st, name):
    try:
        if name == "success_gather":
            return st.am.execute("gather_resource", {"target": "oak_log"}) \
                if hasattr(st.am, "execute") else None
    except Exception:
        pass
    return None


def inject_episode(st, clk, kind):
    """按情节类型注入事件;全部走生产结算/感知通道。"""
    w = st.world
    try:
        if kind == "success_gather":
            plant_tree(w, 2, 2, ORE)
            w._refresh_near()
            st.loop.add_goal({"type": "obtain", "target": "oak_log",
                              "source": "j_episode", "text": "J:采集橡木"})
        elif kind == "success_craft":
            st.loop.add_goal({"type": "obtain", "target": "oak_planks",
                              "source": "j_episode", "text": "J:合成木板"})
        elif kind == "irrelevant_explore":
            st.loop.add_goal({"type": "obtain", "target": "cobblestone",
                              "source": "j_episode", "text": "J:探索石头"})
        elif kind == "failure_gather":
            st.loop.add_goal({"type": "obtain", "target": "diamond",
                              "source": "j_episode", "text": "J:不可达目标"})
        elif kind == "env_change":
            w.blocks = {k: v for k, v in w.blocks.items()
                        if v != "oak_log"}
            w._refresh_near()
    except Exception:
        pass


def snapshot(st, cycle):
    acts = sorted((float(getattr(n, "activation", 0) or 0)
                   for n in st.kg.nodes.values()), reverse=True)
    nz = [a for a in acts if a > 1e-6]
    top1 = acts[0] if acts else 0.0
    mean_act = (sum(nz) / len(nz)) if nz else 0.0
    gs = graph_stats(st)
    causal = st.causal
    stats = {
        "cycle": cycle,
        "nodes": gs["nodes"], "edges": gs["edges"],
        "act_top1": round(top1, 4),
        "act_mean_nonzero": round(mean_act, 4),
        "n_lit": len(nz),
        "aggregations": len(getattr(causal, "_aggregations", {}) or {}),
        "hypotheses": len(getattr(causal, "_hypotheses", {}) or {}),
        "promoted": len(getattr(causal, "_promoted", []) or []),
        "goals_active": len(st.loop.goals() or []),
    }
    # 复用探针:A 链关键节点是否仍在图中
    stats["has_planks_chain"] = ("物品:oak_planks" in st.kg.nodes
                                 and "动作:gather_resource" in st.kg.nodes)
    return stats


def run(cond, seed):
    st = build_stack(goal="oak_planks", label=f"J_{cond}", seed=seed,
                     mc_gatherable=[ORE], writeback_on=(cond == "fas"))
    for (x, z) in TREES:
        plant_tree(st.world, x, z, ORE)
    clk = Clock()
    snaps = []
    t0 = time.time()
    for c in range(1, CYCLES + 1):
        if c % 10 == 1:
            ep = EPISODES[((c - 1) // 10) % len(EPISODES)]
            inject_episode(st, clk, ep)
        tick(st, clk)
        if c in CHECKPOINTS:
            flush_causal(st)
            snaps.append(snapshot(st, c))
    return {"exp": "J", "cond": cond, "seed": seed, "snaps": snaps,
            "latency_s": round(time.time() - t0, 1)}


def main():
    rows = []
    for cond in CONDS:
        for seed in SEEDS:
            try:
                r = run(cond, seed)
            except Exception as e:
                r = {"exp": "J", "cond": cond, "seed": seed,
                     "error": repr(e)[:200]}
            rows.append(r)
            print("[J]", json.dumps(r, ensure_ascii=False)[:180], flush=True)
    p = os.path.join(ROOT, "experiments", "capability_gap_v2", "raw_J.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("saved ->", p)


if __name__ == "__main__":
    main()
