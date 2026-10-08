# run_exp_transfer.py — 实验 C：Cross-task Knowledge Transfer（reuse + recombination）
# ─────────────────────────────────────────────────────────────────────
# 因果问题（审计 §8.3）：任务 1 学到的知识是否向任务 2（共享结构的
# 重组+延伸）迁移？
#   任务1 链 A→B→C：gather oat_log(A) → craft oak_planks(B) → craft
#     crafting_table(C)，8 轮（晋升线 KG_SUPPORT=5/0.80 → 写回入图）。
#   任务2 链 = B→C→D 重组+延伸：craft oak_planks(B) → craft stick →
#     craft crafting_table(C) → craft wooden_pickaxe(D，needs_table)。
# 两条件（同一 world 同一配方表——环境知识对两条件相等）：
#   Transfer   = Ep2 继承 Ep1 全图（inherit_graph，含激活残值）+
#                同进程因果账本（inherit_causal/inherit_tl，C19 装配套件）。
#   No-Transfer= Ep2 干净图 + 全新账本。
# 机制通道公开预判（审计 §8.3）：a) 闭包（配方表）两条件同样可及 →
#   达成层预计"都 6/6"（表格可达性遮蔽，负结果即论文信息）；b) 真实
#   transfer 差分观测点 = action_prior 成功率记忆（autonomy.py:2790，
#   结构性阻碍满额罚 + prior_bonus ±0.05）与图激活残值。
# 观测字段（审计）：transfer_benefit（达成 tick/动作差分）、
#   shared_structure_usage（Ep2 里复用任务1 动作的计数）、
#   recombination_event（首 craft crafting_table 的 tick）、
#   no_transfer_ceiling（No-Transfer 里共享结构动作照样发生）。
# 核心机制零改动；全部修改在装配层/记录层/环境数据层（C19 在案）。
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

TASK1 = "crafting_table"        # 任务1 目标（链 A→B→C 末端）
TASK2 = "wooden_pickaxe"        # 任务2 目标（B→C→D 重组+延伸末端）
ORE = "oak_log"                 # 共享资源品种
NOISE = "birch_log"             # 干扰品种（可采、无链、用途未知）
GATHERABLES = [ORE, NOISE]      # 环境事实追加表（C17d 数据层注入）

# 世界布局（两条件同 seed 同布局）
EP1_TREES = [(-4, -4), (4, -4), (-4, 4), (4, 4), (0, 2)]
EP2_TREES = [(6, 0), (-2, 0)]   # oak@(6,0) 远（视野外/12 格内），birch@(-2,0) 近


def plant_tree(w, x, z, log):
    """纯种树：砂箱 put_tree 的树干硬编码 oak_log（sandbox_lab.py:174），
    会污染干扰谱系。环境数据层补救：树冠+树干全部种 log 品种。"""
    for i in range(3):
        for j in range(3):
            w.put_block(x + i - 1, 65 + j, z, log)
    w.put_block(x, 65, z, log)


def bridge_perception_gaps(st):
    """sandbox 感知接线补口（C17b）——同 run_exp_reuse：UnknownBlock_*
    已建档 → 补 物品:x 节点 → consider_recognition → 用途未知自动 open_gap。"""
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


def open_noise_gap(st):
    """干扰品种 birch_log：世界既存物且无配方无用途 → 预开探索缺口
    （缺口状态是认知产物，open_gap 幂等；两条件同权）。"""
    try:
        import prior_knowledge as pk
        pk.open_gap(st.kg, st.engine, NOISE, "unknown_use", st.loop.config)
    except Exception as e:
        print(f"[gap-preopen] 跳过: {e!r}", flush=True)


def sample_activations(st, rec, ids, tick_i):
    snap = {}
    for nid in ids:
        nd = st.kg.nodes.get(nid)
        snap[nid] = round(float(getattr(nd, "activation", 0.0) or 0.0), 4)
    rec.log("ACTSNAP", json.dumps(snap), tick=tick_i)


def action_prior_probe(st, sigs, rec):
    """action_prior 通道的直接探针（记录层）：Transfer 条件里共享结构
    动作应有成功率记忆（obs≥8、rate=1.0）；No-Transfer 应为空（None/0）。
    这就是"同族动作成功率记忆"跨任务迁移的因果通道。"""
    probe = {}
    for (atype, target) in sigs:
        try:
            probe[f"{atype}({target or ''})"] = st.causal.action_prior(
                atype, target or "")
        except Exception as e:
            probe[f"{atype}({target or ''})"] = {"error": repr(e)}
    rec.log("ACTION_PRIOR", json.dumps(probe))
    return probe


def collect_stats(st, rec, name, target_now, start_idx=0):
    """由 timeline 派生指标（规范字段表，同 run_exp_reuse.collect_stats）。
    schema：ACTION 事件 subject=action_type、content.target=目标；结果走
    紧随同 subject 的 SELF_STATE_CHANGE（content.change=succeeded/failed:*）。
    注意：Transfer 条件共享 Ep1 的 tl（同进程账本通道）——必须按 start_idx
    切分只统计本 episode 段，否则跨段合并（C19 坊间事故：32=24+8）。"""
    raw = getattr(st.tl, "_raw", []) or []
    raw = raw[start_idx:] if start_idx else raw
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
    inv = dict(st.world.inv_map())
    m = {"episode": name, "actions_total": len(types),
         "actions_redundant": redundant, "actions_invalid": invalid,
         "types": types, "targets": tgts,
         "inventory": inv, "graph": graph_stats(st),
         "causal": {"aggregations": len(getattr(st.causal, "_aggregations",
                                                {}) or {}),
                    "hypotheses": len(getattr(st.causal, "_hypotheses",
                                              {}) or {}),
                    "promoted": sorted(getattr(st.causal, "_promoted",
                                               set()) or set())}}
    # 任务2 共享结构使用统计（审计观测字段）
    counts = {}
    for t, tg in zip(types, tgts):
        counts[t + "@" + tg] = counts.get(t + "@" + tg, 0) + 1
    shared = [k for k in counts if k.startswith(
        ("gather_resource@oak_log", "craft_item@oak_planks",
         "craft_item@crafting_table"))]
    m["shared_structure_usage"] = {k: counts[k] for k in shared}
    # 重组事件 = 任务2 里首次 craft crafting_table（动作序号，tl 事件无 tick 键）
    recomb_idx = None
    for i, a in enumerate(acts):
        if (str(a.get("subject") or "") == "craft_item"
                and str((a.get("content") or {}).get("target") or "")
                == "crafting_table"):
            recomb_idx = i
            break
    m["recombination_event_idx"] = recomb_idx
    m["no_transfer_ceiling"] = {
        k: counts.get(k, 0) for k in (
            "gather_resource@oak_log", "craft_item@oak_planks",
            "craft_item@stick", "craft_item@crafting_table",
            "craft_item@wooden_pickaxe") if counts.get(k)}
    return m


class EpRec:
    """RunRecorder 封装：ep1 与 ep2 各一个 recorder，条件级汇总统一写。"""

    def __init__(self, seed, label):
        self.rec = er.RunRecorder(family="transfer", mode="sandbox",
                                  seed=seed, label=label)
        self.seed = seed

    def start(self, meta):
        self.rec.start(meta)

    def snapshot(self, name, **kw):
        self.rec.snapshot(name, **kw)

    def log(self, *a, **k):
        self.rec.log(*a, **k)

    def finalize(self, **kw):
        self.rec.finalize(**kw)


def ep1_practice(seed, max_ticks):
    """任务1：单 stack 串行 8 轮 obtain crafting_table（oak 链实践）。
    8 轮 = 晋升线（KG 5/0.80 → Laplace 需 support=8）——Promote 写回
    入图 + 激活残值；账本/时间轴实例传给 Ep2（Transfer 条件）。"""
    rec = EpRec(seed, "C-ep1-practice")
    rec.start({"episode": "ep1", "task": TASK1, "seed": seed})
    clk = Clock()
    st = build_stack(goal=TASK1, label="c_ep1", seed=seed,
                     mc_gatherable=GATHERABLES)
    for (x, z) in EP1_TREES:
        plant_tree(st.world, x, z, ORE)
    samples = [f"物品:{TASK1}", f"物品:{ORE}", ORE]
    rec.snapshot("graph_start_r1", graph_dict=st.kg.to_dict())
    rounds = {}
    for rnd in range(1, 9):
        if rnd > 1:
            plant_tree(st.world, 0, -4, ORE)   # 补一棵 oak 树（原空位）
            st.world.set_inv()                 # 轮次间清背包：每轮从零再达成
            st.loop.add_goal({"type": "obtain", "target": TASK1,
                              "source": "experiment_obtain",
                              "text": f"实验目标：自主获得 {TASK1}（第{rnd}轮）"})
        done_at = None
        for i in range(max_ticks):
            tick(st, clk)
            bridge_perception_gaps(st)
            if i % 2 == 0:
                sample_activations(st, rec, samples, i)
            if have_item(st, TASK1) > 0:
                done_at = i
                break
        rounds[rnd] = done_at
        print(f"[C:Ep1] round{rnd} goal={TASK1} done@{done_at} "
              f"inv={dict(st.world.inv_map())}", flush=True)
    flush_causal(st)
    stats = collect_stats(st, rec, "ep1_final", TASK1)
    if not stats["causal"]["promoted"]:
        print("[WRITEBACK-LOG] 注意: ep1 无 promote 写回！"
              f"aggs={stats['causal']['aggregations']} "
              f"hyps={stats['causal']['hypotheses']}", flush=True)
    else:
        print(f"[WRITEBACK-LOG] ep1 promote 写回 {len(stats['causal']['promoted'])} 条: "
              f"{stats['causal']['promoted']}", flush=True)
    gpath = os.path.join(er.root_dir(), "transfer",
                         f"c1_ep1_graph_seed{seed}.json")
    os.makedirs(os.path.dirname(gpath), exist_ok=True)
    with open(gpath, "w", encoding="utf-8") as f:
        json.dump(st.kg.to_dict(), f, ensure_ascii=False)
    rec.log("RESULT", f"Ep1 图谱 → {gpath}")
    rec.finalize(ok=True, extra={"rounds": rounds, "causal": stats["causal"],
                                 "graph_path": gpath})
    return {"stack": st, "path": gpath, "rounds": rounds,
            "causal": stats["causal"]}


def ep2_transfer(seed, max_ticks, inherit_graph, inherited_stack, cond):
    """任务2（B→C→D 重组+延伸）：obtain wooden_pickaxe。
    Transfer 条件 = 继承图 + 同进程账本（C19 装配套件：inherit_causal /
    inherit_tl 为 Ep1 同一实例——事件追加进同一时间轴、聚合连续）。
    No-Transfer 条件 = 干净图 + 全新账本（inherit 全 None）。
    同一 world（oak 远 birch 近）同一配方表——环境知识两条件相等。"""
    rec = EpRec(seed, f"{cond}-ep2")
    rec.start({"episode": "ep2", "task": TASK2, "condition": cond,
               "seed": seed, "inherit": bool(inherit_graph)})
    clk = Clock()
    kw = {"goal": TASK2, "label": f"ep2_{cond}", "seed": seed,
          "mc_gatherable": GATHERABLES}
    if inherit_graph:
        kw["inherit_graph"] = inherit_graph
    if inherited_stack is not None:
        kw["inherit_causal"] = inherited_stack.causal
        kw["inherit_tl"] = inherited_stack.tl
    st = build_stack(**kw)
    setup_ep2_world(st)
    open_noise_gap(st)
    # 共享 timeline（Transfer）时本 episode 的事件从这一刻才属于 ep2
    ep_start = len((getattr(st.tl, "_raw", None) or []))
    samples = [f"物品:{TASK2}", f"物品:{TASK1}", f"物品:{ORE}", ORE,
               f"物品:{NOISE}", NOISE]
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    # action_prior 通道探针（t0：账本状态；共享结构动作）
    prior_probe = action_prior_probe(
        st, [("gather_resource", ORE), ("craft_item", "oak_planks"),
             ("craft_item", TASK1), ("craft_item", "stick"),
             ("craft_item", TASK2)], rec)
    done_at = None
    for i in range(max_ticks):
        tick(st, clk)
        bridge_perception_gaps(st)
        if i % 2 == 0:
            sample_activations(st, rec, samples, i)
        if have_item(st, TASK2) > 0:
            done_at = i
            rec.log("RESULT", f"背包获 {TASK2} @tick{i}", tick=i)
            break
    flush_causal(st)
    stats = collect_stats(st, rec, f"ep2_{cond}", TASK2, start_idx=ep_start)
    wb_nodes = sorted(i for i in st.kg.nodes
                      if str(i).startswith(("操作:", "变化:")))
    stats["inherited_wb_nodes"] = len(wb_nodes)
    stats["start_activation"] = {
        str(nid): round(float(getattr(st.kg.nodes.get(nid), "activation", 0.0)
                              or 0.0), 4)
        for nid in (ORE, "oak_planks", TASK1, f"物品:{TASK1}",
                    f"物品:{TASK2}", NOISE)}
    stats["success"] = done_at is not None
    stats["success_tick"] = done_at
    stats["action_prior_t0"] = prior_probe
    stats["transfer_benefit"] = None  # 条件对比时填（tick/动作差分）
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra=stats)
    print(f"[C:Ep2][{cond}] success={stats['success']} "
          f"@{stats['success_tick']} actions={stats['actions_total']} "
          f"red={stats['actions_redundant']} "
          f"recomb_idx={stats['recombination_event_idx']} "
          f"prior={ {k: (v.get('success_rate'), v.get('obs')) for k, v in prior_probe.items()} }",
          flush=True)
    return stats


def setup_ep2_world(st):
    for (x, z) in EP2_TREES:
        # oak@(6,0) 远、birch@(-2,0) 近（同实验 A/B 世界图景）
        plant_tree(st.world, x, z, "oak_log" if x > 0 else "birch_log")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=["Transfer", "No-Transfer"],
                    required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-ticks", type=int, default=400)
    args = ap.parse_args()
    out = {"experiment": "C", "condition": args.condition, "seed": args.seed,
           "task1": TASK1, "task2": TASK2, "max_ticks": args.max_ticks}
    if args.condition == "Transfer":
        ep1 = ep1_practice(args.seed, args.max_ticks)
        out["ep1"] = {"path": ep1["path"], "rounds": ep1["rounds"],
                      "causal": ep1["causal"]}
        out["ep2"] = ep2_transfer(args.seed, args.max_ticks,
                                  inherit_graph=ep1["path"],
                                  inherited_stack=ep1["stack"],
                                  cond="Transfer")
    else:
        out["ep2"] = ep2_transfer(args.seed, args.max_ticks,
                                  inherit_graph=None, inherited_stack=None,
                                  cond="No-Transfer")
    fn = os.path.join(er.root_dir(), "transfer",
                      f"expC_{args.condition}_seed{args.seed}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[C] {args.condition} seed={args.seed} → {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())