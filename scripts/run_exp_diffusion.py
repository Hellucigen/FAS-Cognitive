# run_exp_diffusion.py — 实验 B：扩散 → 相关知识访问
# ─────────────────────────────────────────────────────────────────────
# 因果问题：Diffusion（β_spread=1.0）是否让"相关知识"在注意力地板直写
# 之外的邻居上被点亮（知识访问层），且是否改变行为？
#
# 条件：full（diffusion_on=True，β=1.0）vs nodiff（diffusion_on=False，
#   β_spread=0.0——直写保留，仅传播截断，audit §2 精确操作化）。
#
# 观测点（audit §5.2 预判的唯一可测差分落点）：
#   open_gap(birch_log) 建 缺口:用途(birch_log) 节点 + 指向边；活缺口
#   目标每步 floor 0.9 + mark_active（autonomy._gap_candidates:
#   1511-1523，两条件同权）→ Full 下该源经"指向"边扩散点亮 birch_log，
#   nodiff 下 birch_log 只吃直写（感知 touch/建档），无扩散加成。
#   同时探目标知识链（物品:oak_planks←配方←物品:oak_log）：goal floor
#   直写 物品:oak_planks 后，Full 下是否经配方边点亮 物品:oak_log
#   （oak 在视野外，感知不可及=纯扩散信号）。
#
# 偏离审计 §8.2 的"深度≥4 自制链"（明示）：沙箱长链不可达
#   （iron 链 furnace_take 驱动缺口 2026-09-28 + Negative-1 同类结构），
#   扩散的可测因果空间与链深无关（§5.2 落点在"指向/邻接点亮"），
#   故用最短可达成链 + 干扰谱系（birch），观测不失真。
#
# 达成层零差分是结构保证（表格闭包，矩阵 11 组已证）→ 负结果如实入库。
# 核心机制零改动；本脚本只落 环境数据 + 记录层（CHANGE_LOG C18）。
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
NOISE = "birch_log"
GATHERABLES = [ORE, NOISE]
TREES = [(6, 0), (-2, 0)]           # oak 远（视野外），birch 近（干扰谱系）


def plant_tree(w, x, z, log):
    """纯种树（同实验 A v3：sandbox put_tree 树干硬编码 oak_log 会污染
    干扰谱系；环境数据层补救：树冠+树干全部种 log 品种）。"""
    for i in range(3):
        for j in range(3):
            w.put_block(x + i - 1, 65 + j, z, log)
    w.put_block(x, 65, z, log)


def setup_world(st):
    plant_tree(st.world, *TREES[0], ORE)
    plant_tree(st.world, *TREES[1], NOISE)


def bridge_perception_gaps(st):
    """同实验 A（C17b）：sandbox 感知走 EmbodiedStateMapper 旁路，缺
    真机的"建档→用途检查→探索缺口"段；此处在脚本层复制同一职责。"""
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
    """同实验 A：timeline ACTION 事件 schema（subject=动作、content.target
    =目标、结果走 SELF_STATE_CHANGE）。"""
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
    m = {"episode": name, "actions_total": len(types),
         "actions_redundant": redundant, "actions_invalid": invalid,
         "first_ore_action_idx": first_relevant,
         "first_noise_action_idx": first_noise,
         "types": types, "targets": tgts,
         "inventory": dict(st.world.inv_map()), "graph": graph_stats(st)}
    rec.log("EPISODE", json.dumps(m))
    return m


def bfs_lit_le2(st, gap_gid, thresh=0.05):
    """记录层探针：以缺口节点为源、无向 2 跳内、激活≥thresh 的节点数
    （除源自身）。Full 下扩散会点亮邻接知识；β=0 下只有直写点。
    扩散引擎的传播是否无向在此不预判——2 跳双向都算，曲线说话。"""
    from collections import deque
    q = deque([(gap_gid, 0)])
    visited = {gap_gid}
    lit = 0
    while q:
        nid, d = q.popleft()
        if d >= 2:
            continue
        for e in getattr(st.kg, "edges", []) or []:
            other = None
            if str(getattr(e, "src", "")) == nid:
                other = getattr(e, "dst", None)
            elif str(getattr(e, "dst", "")) == nid:
                other = getattr(e, "src", None)
            if other is None or other in visited:
                continue
            visited.add(other)
            nd = st.kg.nodes.get(other)
            if nd is not None and float(nd.activation or 0.0) >= thresh:
                lit += 1
            q.append((other, d + 1))
    return lit


def run_condition(cond, seed, max_ticks):
    diffusion_on = (cond == "full")
    rec = er.RunRecorder(family="diffusion", mode="sandbox", seed=seed,
                         label=f"B-{cond}")
    rec.start({"episode": "ep2", "condition": cond, "seed": seed,
               "diffusion_on": diffusion_on})
    clk = Clock()
    st = build_stack(goal=TARGET, label=f"ep2_{cond}", seed=seed,
                     diffusion_on=diffusion_on, mc_gatherable=GATHERABLES)
    setup_world(st)
    try:
        import prior_knowledge as pk
        pk.open_gap(st.kg, st.engine, NOISE, "unknown_use", st.loop.config)
    except Exception:
        pass
    gap_gid = "缺口:用途(birch_log)"
    try:
        import prior_knowledge as pk
        gap_gid = pk.gap_node_id(NOISE)
    except Exception:
        pass
    samples = ["物品:oak_planks", "物品:oak_log", "物品:birch_log",
               "oak_log", "birch_log", gap_gid, "探索缺口"]
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    curves = []
    done_at = None
    for i in range(max_ticks):
        tick(st, clk)
        bridge_perception_gaps(st)
        if i % 2 == 0:
            snap = {}
            for nid in samples:
                nd = st.kg.nodes.get(nid)
                snap[nid] = round(float(getattr(nd, "activation", 0.0)
                                        or 0.0), 4)
            curves.append(snap)
            sample_activations(st, rec, samples, i)
        if have_item(st, TARGET) > 0:
            done_at = i
            rec.log("RESULT", f"背包获 {TARGET} @tick{i}", tick=i)
            break
    flush_causal(st)
    stats = collect_stats(st, rec, f"ep2_{cond}")
    stats["success"] = done_at is not None
    stats["success_tick"] = done_at
    stats["diffusion_on"] = diffusion_on
    stats["gap_reach_le2_lit_end"] = bfs_lit_le2(st, gap_gid)
    stats["activation_curves"] = curves        # 记录层原样存档（报告取用）
    stats["start_activation"] = curves[0] if curves else {}
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra=stats)
    print(f"[B:{cond}] success={stats['success']} "
          f"@{stats['success_tick']} actions={stats['actions_total']} "
          f"red={stats['actions_redundant']} "
          f"reach_le2_lit={stats['gap_reach_le2_lit_end']} "
          f"t0={stats['start_activation']}", flush=True)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=["full", "nodiff"], required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-ticks", type=int, default=150)
    args = ap.parse_args()
    stats = run_condition(args.condition, args.seed, args.max_ticks)
    out = {"experiment": "B", "condition": args.condition, "seed": args.seed,
           "max_ticks": args.max_ticks, "ep2": stats}
    fn = os.path.join(er.root_dir(), "diffusion",
                      f"expB_{args.condition}_seed{args.seed}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[B] {args.condition} seed={args.seed} → {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())