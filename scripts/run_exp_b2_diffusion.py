# run_exp_b2_diffusion.py — 实验 B 升级版：扩散因果四条件对照
# （Paper-I 收尾 campaign PHASE 4；实验基础设施新增，核心机制零改动）
# ─────────────────────────────────────────────────────────────────────
# 因果问题（R2）：扩散是否具有独立因果作用，而非被任务闭包/直接目标
# 注入/替代访问路径掩盖？
#
# 四条件（§8）：
#   full  C0：扩散 ON（SandboxDiffuser 生产驱动，β=1.0）
#   nodiff C1：扩散 OFF（β_spread=0.0，直写保留）——既有实验 B 口径
#   rand  C2：随机激活对照——扩散执行器被替换为 RandomDiffuser（装配层
#       对照基线）：每拍对 ~5 个随机语义节点注入 U(0.2,1.0) 激活 + 全局
#       衰减。激活动存在但无结构。检验"点亮量"本身（非语义结构）能否
#       产生同等的知识访问差分。
#   retr  C3：直接检索 oracle 对照——扩散 OFF，每拍对**目标闭包链**节点
#       （物品:oak_planks/物品:oak_log/oak_log）直写 0.5 激活地板（模拟
#       "答案可被直接检索到"的理想检索器）。检验：full 相对理想检索器
#       的增量在哪里（预测：链外知识——缺口指向的 birch_log——只有
#       full 点亮）。
#   C2/C3 均为 runner 层对照装配（§3.C 允许的 baseline 新增），不触碰
#   engine/autonomy； RandomDiffuser 复刻 SandboxDiffuser 接口（__call__）。
#
# 指标（§8）：target activation latency（目标链/链外节点首亮 tick）、
#   relevant/irrelevant 节点激活、retrieval rank（oak_log 在全图激活序的
#   名次）、path length/success/failed actions、扩散能量（激活总质量）、
#   终态点亮集。
# 世界同既有实验 B（oak 远/在视野外，birch 近/干扰；缺口:用途(birch_log)
#   预开）。核心机制零改动（CHANGE_LOG 记 C21）。
# ─────────────────────────────────────────────────────────────────────

import argparse
import json
import os
import random
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
NOISE = "birch_log"
GATHERABLES = [ORE, NOISE]
TREES = [(6, 0), (-2, 0)]
RETRIEVAL_CHAIN = ["物品:oak_planks", "物品:oak_log", "oak_log"]
SAMPLE_IDS = ["物品:oak_planks", "物品:oak_log", "物品:birch_log",
              "oak_log", "birch_log", "探索缺口"]


class RandomDiffuser:
    """C2 随机激活对照（装配层基线）：每拍对随机语义节点注入随机激活。

    语义：激活存在、总量同量级，但**不沿语义结构传播**。若 C2 与 full
    的知识访问指标同分布 → 差分归因于"点亮量"而非"结构传播"；若分离 →
    结构性传播是必要的。decay 沿用 engine.decay_step（真实机制）。"""

    def __init__(self, engine, kg, n_nodes=5, lo=0.2, hi=1.0, seed=0):
        self.engine = engine
        self.kg = kg
        self.n = n_nodes
        self.lo, self.hi = lo, hi
        self.rng = random.Random(seed)

    def __call__(self):
        try:
            if not getattr(self.engine, "_running", False):
                self.engine.decay_step()
                cands = [n for n, nd in self.kg.nodes.items()
                         if "semantic" in str(getattr(nd, "label", ""))]
                if cands:
                    for nid in self.rng.sample(
                            cands, min(self.n, len(cands))):
                        nd = self.kg.nodes[nid]
                        nd.activation = min(
                            2.0, float(nd.activation or 0.0)
                            + self.rng.uniform(self.lo, self.hi))
        except Exception:
            pass


class RetrievalOracle:
    """C3 直接检索对照（装配层基线）：每拍对目标闭包链节点直写激活地板。

    语义：假设存在一个"理想检索器"，不经传播、每拍直接命中目标链。链外
    知识（缺口指向的 birch_log）不被它命中——full 与 C3 的差分落在链外。"""

    def __init__(self, engine, kg, chain=None, floor=0.5):
        self.engine = engine
        self.kg = kg
        self.chain = chain or RETRIEVAL_CHAIN
        self.floor = floor

    def __call__(self):
        try:
            if not getattr(self.engine, "_running", False):
                self.engine.decay_step()
                for nid in self.chain:
                    nd = self.kg.nodes.get(nid)
                    if nd is not None:
                        nd.activation = max(float(nd.activation or 0.0),
                                            self.floor)
        except Exception:
            pass


def plant_tree(w, x, z, log):
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


def activation_mass(st):
    return round(sum(float(getattr(nd, "activation", 0.0) or 0.0)
                     for nd in st.kg.nodes.values()), 3)


def rank_of(st, nid):
    a = float(getattr(st.kg.nodes.get(nid), "activation", 0.0) or 0.0)
    return 1 + sum(1 for nd in st.kg.nodes.values()
                   if float(getattr(nd, "activation", 0.0) or 0.0) > a)


def run_condition(cond, seed, max_ticks):
    rec = er.RunRecorder(family="diffusion_b2", mode="sandbox", seed=seed,
                         label=f"B2-{cond}")
    rec.start({"episode": "ep2", "condition": cond, "seed": seed})
    clk = Clock()
    st = build_stack(goal=TARGET, label=f"b2_{cond}", seed=seed,
                     diffusion_on=(cond in ("full", "rand")),
                     mc_gatherable=GATHERABLES)
    plant_tree(st.world, *TREES[0], ORE)
    plant_tree(st.world, *TREES[1], NOISE)
    try:
        import prior_knowledge as pk
        pk.open_gap(st.kg, st.engine, NOISE, "unknown_use", st.loop.config)
    except Exception:
        pass
    # 对照装配（runner 层替换 st.diffuser；full/nodiff 用生产驱动器）
    if cond == "rand":
        st.diffuser = RandomDiffuser(st.engine, st.kg, seed=seed)
    elif cond == "retr":
        st.diffuser = RetrievalOracle(st.engine, st.kg)
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    curves = []
    lit_ticks = {nid: None for nid in ("oak_log", "birch_log",
                                       "物品:oak_log")}
    done_at = None
    for i in range(max_ticks):
        tick(st, clk)
        bridge_perception_gaps(st)
        for nid in lit_ticks:
            if lit_ticks[nid] is None:
                nd = st.kg.nodes.get(nid)
                if nd is not None and float(
                        getattr(nd, "activation", 0.0) or 0.0) >= 0.05:
                    lit_ticks[nid] = i
        if i % 2 == 0:
            snap = {nid: round(float(getattr(st.kg.nodes.get(nid),
                                             "activation", 0.0) or 0.0), 4)
                    for nid in SAMPLE_IDS}
            snap["_mass"] = activation_mass(st)
            snap["_rank_oak_log"] = rank_of(st, "oak_log")
            curves.append(snap)
            rec.log("ACTSNAP", json.dumps(snap, ensure_ascii=False),
                    tick=i)
        if have_item(st, TARGET) > 0:
            done_at = i
            rec.log("RESULT", f"背包获 {TARGET} @tick{i}", tick=i)
            break
    flush_causal(st)
    raw = getattr(st.tl, "_raw", []) or []
    acts = [e for e in raw if e.get("event_type") == "ACTION"]
    results = [e for e in raw if e.get("event_type") == "SELF_STATE_CHANGE"]
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
    stats = {"episode": f"ep2_{cond}", "success": done_at is not None,
             "success_tick": done_at, "actions_total": len(acts),
             "actions_invalid": invalid,
             "lit_ticks": lit_ticks,
             "final_mass": activation_mass(st),
             "final_rank_oak_log": rank_of(st, "oak_log"),
             "final_lit_ge005": sorted(
                 n for n, nd in st.kg.nodes.items()
                 if float(getattr(nd, "activation", 0.0) or 0.0) >= 0.05),
             "activation_curves": curves,
             "graph": graph_stats(st)}
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra={k: v for k, v in stats.items()
                                 if k != "activation_curves"})
    print(f"[B2:{cond}] succ={stats['success']} @{done_at} "
          f"act={stats['actions_total']} inv={invalid} "
          f"lit={lit_ticks} mass={stats['final_mass']} "
          f"rank_oak={stats['final_rank_oak_log']} "
          f"litset={len(stats['final_lit_ge005'])}", flush=True)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition",
                    choices=["full", "nodiff", "rand", "retr"],
                    required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-ticks", type=int, default=150)
    args = ap.parse_args()
    stats = run_condition(args.condition, args.seed, args.max_ticks)
    out = {"experiment": "B2", "condition": args.condition,
           "seed": args.seed, "max_ticks": args.max_ticks, "ep2": stats}
    fn = os.path.join(er.root_dir(), "diffusion_b2",
                      f"expB2_{args.condition}_seed{args.seed}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[B2] {args.condition} seed={args.seed} → {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
