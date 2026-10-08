# run_exp_reuse.py — 实验 A v3：Knowledge Write-back → Future Reuse
# ─────────────────────────────────────────────────────────────────────
# 因果问题：CausalLearner 写回（promote_to_kg + mark_active）是否让知识在
# 后续 episode 被访问并影响行为？
#
# v3：真实 MC 词汇（oak 链）双谱系方案。
#   Ep1 发现 = oak 树林实践 oak_planks 链 8 轮（表格有链、矩阵 11 组已证
#     可达）→ 因果写回（support=8/conf≥0.80 过 KG 晋升线）+ oak 系激活
#     残值入图。
#   Ep2 复用 = 目标 oak_planks，世界 oak 远（12 格内可达可寻）birch 近
#     （干扰谱系，无配方无链）→ 差分 = 首动作品种/达成 tick/废动作。
# 审计实证的负发现（报告 A/Negative-1）：自制无表世界的"发现→采集"
#   闭环在核心里不存在——目标无链时 explore_area@目标物 永续
#   （user_goal 0.345 恒压过 curiosity investigate 0.075）。结构性设计
#   限制，不绕。
# 条件 B（write-back）：Ep1 单 stack 串行 3 轮 acquire → 图谱 JSON 存盘
#   （含激活快照）→ Ep2 继承图。
# 条件 A（no knowledge）：仅有 Ep2（干净图、同世界同 seed）。
# 核心机制零改动（C17/C17b 均为记录/环境层；v3 无 C17d 依赖）。
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
ORE = "oak_log"                     # 目标品种（可采+配方链）
NOISE = "birch_log"                 # 干扰品种（可采、无链、用途未知）
GATHERABLES = [ORE, NOISE]          # 环境事实追加表（C17d 数据层注入）

# 世界布局（每条件同 seed 同布局）
EP1_TREES = [(-4, -4), (4, -4), (-4, 4), (4, 4), (0, 2)]
EP2_TREES = [(6, 0), (-2, 0)]       # oak@(6,0) 远（视野外/12 格内），birch@(-2,0) 近


def plant_tree(w, x, z, log):
    """纯种树：砂箱 put_tree 的树干硬编码 oak_log（sandbox_lab.py:174），
    会污染干扰谱系（birch 树也产 oak_log）。环境数据层补救：树冠+树干
    全部种 log 品种，保证 oak 树纯 oak、birch 树纯 birch。"""
    for i in range(3):
        for j in range(3):
            w.put_block(x + i - 1, 65 + j, z, log)
    w.put_block(x, 65, z, log)


def setup_world(st, ores, noises, y=64):
    # v3：sandbox 默认配方/掉落表（oak 链在表内，birch 无链即 null）
    for (x, z) in ores:
        plant_tree(st.world, x, z, ORE)
    for (x, z) in noises:
        plant_tree(st.world, x, z, NOISE)


def bridge_perception_gaps(st):
    """sandbox 感知接线补口（C17b）：minecraft/perception.py 的"建档→
    用途检查→探索缺口"只在真机感知通道被调用；fake-bridge 装配走
    EmbodiedStateMapper 旁路。本文档复现同职责：UnknownBlock_* 已建档
    → 补 物品:x 节点 → consider_recognition → 用途未知自动 open_gap。"""
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


def sample_activations(st, rec, ids, tick_i):
    snap = {}
    for nid in ids:
        nd = st.kg.nodes.get(nid)
        snap[nid] = round(float(getattr(nd, "activation", 0.0) or 0.0), 4)
    rec.log("ACTSNAP", json.dumps(snap), tick=tick_i)


def collect_stats(st, rec, name):
    """由 timeline 派生指标（规范字段表）。
    schema（action_system._emit_*，experience.make_event）：ACTION 事件
    subject=action_type、content.target=目标；结果走紧随同 subject 的
    SELF_STATE_CHANGE（content.change = succeeded / failed:*）。"""
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
    first_relevant = None
    for i, tg in enumerate(tgts):
        if ORE in tg.lower() or TARGET in tg.lower():
            first_relevant = i
            break
    first_noise = None
    for i, tg in enumerate(tgts):
        if NOISE in tg.lower():
            first_noise = i
            break
    inv = dict(st.world.inv_map())
    m = {"episode": name, "actions_total": len(types),
         "actions_redundant": redundant, "actions_invalid": invalid,
         "first_ore_action_idx": first_relevant,
         "first_noise_action_idx": first_noise,
         "types": types, "targets": tgts,
         "inventory": inv, "graph": graph_stats(st),
         "causal": {"aggregations": len(getattr(st.causal, "_aggregations",
                                                {}) or {}),
                    "hypotheses": len(getattr(st.causal, "_hypotheses",
                                              {}) or {}),
                    "promoted": sorted(getattr(st.causal, "_promoted",
                                               set()) or set())}}
    rec.log("EPISODE", json.dumps(m))
    return m


def make_ep(goal, seed, label, inherit=None, ores=EP1_TREES, noises=(),
            max_ticks=250):
    kw = {"goal": goal, "label": label, "seed": seed,
          "mc_gatherable": GATHERABLES}
    if inherit:
        kw["inherit_graph"] = inherit
    st = build_stack(**kw)
    setup_world(st, ores, noises)
    # 环境事实声明：birch_log（干扰品种）无配方无用途 → 预开探索缺口
    # （世界既存物且未明用途；缺口状态是认知产物，open_gap 幂等）。
    try:
        import prior_knowledge as pk
        pk.open_gap(st.kg, st.engine, NOISE, "unknown_use", st.loop.config)
    except Exception as e:
        print(f"[gap-preopen] 跳过: {e!r}", flush=True)
    return st


def ep1_discover(seed, max_ticks):
    """单 stack 串行 8 轮 acquire（图谱/账本/激活连续）。每轮砍树挖
    oak_log → craft oak_planks 达成 → 清背包 + 补树 → 新一轮。
    8 轮 = 8 次"操作→变化"观测：promote 门槛是 KG_SUPPORT=5 且
    confidence≥0.80（Laplace 平滑 s/(s+c+2) 下纯成功链 support=8 才
    到 0.80）——3 轮只到假设层（审计引 3/0.60 是聚合→假设门槛，易误读）。"""
    rec = er.RunRecorder(family="reuse", mode="sandbox", seed=seed,
                         label="A-ep1-discover")
    rec.start({"episode": "ep1", "seed": seed})
    clk = Clock()
    st = make_ep(TARGET, seed, "ep1", ores=EP1_TREES, noises=(),
                 max_ticks=max_ticks)
    samples = [f"物品:{TARGET}", f"物品:{ORE}"]
    rec.snapshot("graph_start_r1", graph_dict=st.kg.to_dict())
    for rnd in range(1, 9):
        if rnd > 1:
            plant_tree(st.world, 0, -4, ORE)   # 补一棵 oak 树（原空位）
            st.world.set_inv()                 # 轮次间清背包：每轮从零再达成
            st.loop.add_goal({"type": "obtain", "target": TARGET,
                              "source": "experiment_obtain",
                              "text": f"实验目标：自主获得 {TARGET}（第{rnd}轮）"})
        done_at = None
        for i in range(max_ticks):
            tick(st, clk)
            bridge_perception_gaps(st)
            if i % 2 == 0:
                sample_activations(st, rec, samples, i)
            if have_item(st, TARGET) > 0:
                done_at = i
                rec.log("RESULT", f"第{rnd}轮达成 @tick{i}", tick=i)
                break
        print(f"[A:Ep1] round{rnd} done@{done_at} "
              f"inv={dict(st.world.inv_map())}", flush=True)
    flush_causal(st)
    stats = collect_stats(st, rec, "ep1_final")
    # M5 验证探针（pilot 断言）：因果写回必须真发生；失败即修正实验条件
    # （轮数/支持度），不是改核心机制。
    if not stats["causal"]["promoted"]:
        print("[WRITEBACK-LOG] 注意: ep1 无 promote 写回！"
              f"aggs={stats['causal']['aggregations']} "
              f"hyps={stats['causal']['hypotheses']}", flush=True)
    else:
        print(f"[WRITEBACK-LOG] ep1 promote 写回 {len(stats['causal']['promoted'])} 条: "
              f"{stats['causal']['promoted']}", flush=True)
    print(f"[A:Ep1] causal={stats['causal']}\n  graph={stats['graph']}",
          flush=True)
    gpath = os.path.join(er.root_dir(), "reuse",
                         f"ep1_graph_seed{seed}.json")
    os.makedirs(os.path.dirname(gpath), exist_ok=True)
    with open(gpath, "w", encoding="utf-8") as f:
        json.dump(st.kg.to_dict(), f, ensure_ascii=False)
    rec.log("RESULT", f"Ep1 图谱 → {gpath}")
    rec.finalize(ok=True, extra={"graph_path": gpath,
                                 "causal": stats["causal"]})
    return {"path": gpath, "causal": stats["causal"]}


def ep2(seed, max_ticks, inherit=None, cond="A"):
    clk = Clock()
    rec = er.RunRecorder(family="reuse", mode="sandbox", seed=seed,
                         label=f"{cond}-ep2")
    rec.start({"episode": "ep2", "condition": cond, "seed": seed,
               "inherit": bool(inherit)})
    kw = {"goal": TARGET, "label": f"ep2_{cond}", "seed": seed,
          "mc_gatherable": GATHERABLES}
    if inherit:
        kw["inherit_graph"] = inherit
    st = build_stack(**kw)
    setup_world(st, [EP2_TREES[0]], [EP2_TREES[1]])
    try:
        import prior_knowledge as pk
        pk.open_gap(st.kg, st.engine, NOISE, "unknown_use", st.loop.config)
    except Exception:
        pass
    samples = [f"物品:{TARGET}", f"物品:{ORE}", ORE,
               f"物品:{NOISE}", NOISE]
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    done_at = None
    for i in range(max_ticks):
        tick(st, clk)
        bridge_perception_gaps(st)
        if i % 2 == 0:
            sample_activations(st, rec, samples, i)
        if have_item(st, TARGET) > 0:
            done_at = i
            rec.log("RESULT", f"背包获 {TARGET} @tick{i}", tick=i)
            break
    flush_causal(st)
    stats = collect_stats(st, rec, f"ep2_{cond}")
    # 知识访问层指标（记录层）：继承的写回节点数 + 关键节点起始激活
    wb_nodes = sorted(i for i in st.kg.nodes
                      if str(i).startswith(("操作:", "变化:")))
    stats["inherited_wb_nodes"] = len(wb_nodes)
    stats["start_activation"] = {
        str(nid): round(float(getattr(st.kg.nodes.get(nid), "activation", 0.0)
                              or 0.0), 4)
        for nid in ("oak_log", "birch_log", f"物品:{TARGET}")}
    stats["success"] = done_at is not None
    stats["success_tick"] = done_at
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra=stats)
    print(f"[A:Ep2][{cond}] success={stats['success']} "
          f"@{stats['success_tick']} actions={stats['actions_total']} "
          f"red={stats['actions_redundant']} "
          f"first_ore={stats['first_ore_action_idx']} "
          f"first_noise={stats['first_noise_action_idx']}",
          flush=True)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=["A", "B"], required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-ticks", type=int, default=300)
    args = ap.parse_args()
    out = {"experiment": "A", "condition": args.condition, "seed": args.seed,
           "max_ticks": args.max_ticks}
    if args.condition == "B":
        ep1 = ep1_discover(args.seed, args.max_ticks)
        out["ep1"] = ep1
        out["ep2"] = ep2(args.seed, args.max_ticks, inherit=ep1["path"],
                         cond="B")
    else:
        out["ep2"] = ep2(args.seed, args.max_ticks, cond="A")
    fn = os.path.join(er.root_dir(), "reuse",
                      f"reuse_{args.condition}_seed{args.seed}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[A] {args.condition} seed={args.seed} → {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())