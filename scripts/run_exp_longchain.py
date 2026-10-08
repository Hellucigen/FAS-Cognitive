# run_exp_longchain.py — 实验 H：长链因果行动选择
# （Paper-I 收尾 campaign PHASE 11；R10。实验基础设施新增，核心机制零改动）
# ─────────────────────────────────────────────────────────────────────
# 因果问题：在显著长于短链（4–10 步）的任务里，各机制是否产生可观察差异？
#
# 旧失败重审（§14/§20）：iron_ingot 链旧负结果 = 环境限制（furnace_take
#   重试窗 300s 壁钟 vs 沙箱秒级真实时长——autonomy.smelt_pending 的重试
#   节拍在沙箱内不可达；探索壁钟伪影 C12 已修）。本轮环境侧修复：
#   沙箱 /smelt 即时结算（产物直入背包，runner 层包装 world.call，不动
#   skills/autonomy）——furnace_take 环节由环境简化吸收，如实记档
#   （ENVIRONMENT_SIMPLIFICATION，论文引用需带此口径）。
#
# 任务（≥6 步 + 无关资源 + 替代路径 + 干扰节点 + 失败可能 + 环境变化）：
#   obtain iron_ingot：
#   oak_log→oak_planks→stick→crafting_table→wooden_pickaxe
#     →stone(需镐)→cobblestone→stone_pickaxe（ cobblestone+stick+table）
#     →iron_ore(需石镐)→raw_iron→coal_ore(需木镐)→coal(燃料)
#     →furnace(8 cobblestone)→place→smelt(raw_iron+coal)→iron_ingot
#   干扰：birch 树（可采无链）、diamond_ore（不可采，需铁镐）。
# 条件（同一环境/动作 API）：
#   L0 full / L1 −Diffusion（β=0，SandboxDiffuser 保留跑衰减）
#     / L2 −MemoryWriteback（causal_on=False：无账本无写回）
#     / L3 −GraphPrior（prior_level=B：图谱先验边关，机制表保留）
# 指标（§14）：达成/tick、动作序列、冗余/失败动作、路径长度、检索名次、
#   图谱增量、写回计数、共享结构命中。
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

TARGET = "iron_ingot"
GATHERABLES = ["oak_log", "coal"]
OAK_TREES = [(-6, -6), (-8, -6)]
BIRCH_TREES = [(2, -7), (4, -7)]               # 干扰：可采无链
STONE_POS = [(x, 6) for x in range(2, 14)]     # stone×12（丰度容纳规划器
                                               # 重复合成对圆石的消耗）
IRON_POS = [(8, 2), (9, 2)]                    # iron_ore（需石镐）
COAL_POS = [(-2, 8), (-3, 8)]                  # coal_ore（需木镐）→ 燃料
DIAMOND_POS = [(10, 8)]                        # 干扰：不可采（需铁镐）


def plant_tree(w, x, z, log="oak_log"):
    for i in range(3):
        for j in range(3):
            w.put_block(x + i - 1, 65 + j, z, log)
    w.put_block(x, 65, z, log)


def setup_world(st):
    w = st.world
    for (x, z) in OAK_TREES:
        plant_tree(w, x, z)
    for (x, z) in BIRCH_TREES:
        plant_tree(w, x, z, "birch_log")
    for (x, z) in STONE_POS:
        w.put_block(x, 60, z, "stone")
    for (x, z) in IRON_POS:
        w.put_block(x, 60, z, "iron_ore")
    for (x, z) in COAL_POS:
        w.put_block(x, 60, z, "coal_ore")
    for (x, z) in DIAMOND_POS:
        w.put_block(x, 60, z, "diamond_ore")
    # 环境事实（C11 先例）：预置工作台（长链多次需要邻台合成；世界既存
    # 事实，非 agent 代码分支）
    w.put_block(0, 64, -2, "crafting_table")
    # 环境数据扩展（世界侧）：煤的掉落/工具需求（原表无 coal_ore）
    w.block_meta["coal_ore"] = {"drops": ["coal"],
                                "harvest_tools": ["wooden_pickaxe"]}
    # 环境简化（ENVIRONMENT_SIMPLIFICATION，记档）：/smelt 即时结算入包
    # ——原 furnace_take 重试窗（autonomy.smelt_pending，300s 壁钟）在
    # 沙箱时钟量程不可达（旧 iron 链负结果根因之一，§16/§19.4）。
    orig_call = w.call

    def call2(path, payload=None, timeout=3):
        r = orig_call(path, payload, timeout)
        if path == "/smelt" and getattr(w, "_smelt_done_queue", None):
            out = w._smelt_done_queue
            w._smelt_done_queue = None
            w.add_item(out)
            w.receipt = {"status": "done", "detail": {"took": out}}
        return r

    w.call = call2


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


def rank_of(st, nid):
    a = float(getattr(st.kg.nodes.get(nid), "activation", 0.0) or 0.0)
    return 1 + sum(1 for nd in st.kg.nodes.values()
                   if float(getattr(nd, "activation", 0.0) or 0.0) > a)


def collect_stats(st, rec, name, g_start):
    raw = getattr(st.tl, "_raw", []) or []
    acts = [e for e in raw if e.get("event_type") == "ACTION"]
    results = [e for e in raw if e.get("event_type") == "SELF_STATE_CHANGE"]
    types = [str(a.get("subject") or "") for a in acts]
    tgts = [str((a.get("content") or {}).get("target") or "") for a in acts]
    fails = [str((e.get("content") or {}).get("change") or "")
             for e in results
             if str((e.get("content") or {}).get("change") or "")
             .startswith("failed")]
    seen = set()
    redundant = 0
    for t, tg in zip(types, tgts):
        k = f"{t}@{tg}"
        if k in seen:
            redundant += 1
        seen.add(k)
    g_end = graph_stats(st)
    m = {"episode": name, "actions_total": len(types),
         "actions_redundant": redundant, "actions_failed": len(fails),
         "fail_reasons": fails[:10],
         "path_len_unique": len(seen),
         "types": types, "targets": tgts,
         "inventory": dict(st.world.inv_map()),
         "graph_delta": {k: g_end[k] - g_start[k] for k in g_start},
         "writeback_promoted": sorted(
             getattr(st.causal, "_promoted", set()) or set())
         if st.causal else [],
         "graph": g_end}
    rec.log("EPISODE", json.dumps(m, ensure_ascii=False))
    return m


def run_condition(cond, seed, max_ticks=900):
    rec = er.RunRecorder(family="longchain", mode="sandbox", seed=seed,
                         label=f"H-{cond}")
    rec.start({"condition": cond, "seed": seed})
    kw = {"goal": TARGET, "label": f"lc_{cond}", "seed": seed,
          "mc_gatherable": GATHERABLES}
    if cond == "L1":
        kw["diffusion_on"] = False
    if cond == "L2":
        kw["causal_on"] = False
        kw["writeback_on"] = False
    if cond in ("L3", "L3b"):
        kw["prior_level"] = "B"
    if cond == "L3b":
        # 分解条件（C31）：机制表保留 + 熔炼任务先验注入（与 L3 唯一差异
        # = SMELT_PRIOR 在场）——归因分解"图谱先验边" vs "任务先验"
        import sandbox_lab as _sl
        kw["prior_extra"] = [_sl.SMELT_PRIOR]
    st = build_stack(**kw)
    setup_world(st)
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    g_start = graph_stats(st)
    clk = Clock()
    done_at = None
    ranks = []
    for i in range(max_ticks):
        tick(st, clk)
        bridge_perception_gaps(st)
        if i % 10 == 0:
            rk = rank_of(st, "物品:iron_ingot")
            ranks.append({"tick": i, "rank_iron": rk})
            rec.log("ACTSNAP", json.dumps(ranks[-1]), tick=i)
        if have_item(st, TARGET) > 0:
            done_at = i
            rec.log("RESULT", f"背包获 {TARGET} @tick{i}", tick=i)
            break
    flush_causal(st)
    stats = collect_stats(st, rec, f"lc_{cond}", g_start)
    stats["success"] = done_at is not None
    stats["success_tick"] = done_at
    stats["rank_trace"] = ranks
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra={k: v for k, v in stats.items()
                                 if k not in ("rank_trace", "types",
                                              "targets")})
    print(f"[H:{cond}] succ={stats['success']} @{done_at} "
          f"act={stats['actions_total']} red={stats['actions_redundant']} "
          f"fail={stats['actions_failed']} "
          f"inv_has_iron={stats['inventory'].get('iron_ingot', 0)}",
          flush=True)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=["L0", "L1", "L2", "L3", "L3b"],
                    required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-ticks", type=int, default=900)
    args = ap.parse_args()
    stats = run_condition(args.condition, args.seed, args.max_ticks)
    out = {"experiment": "H_longchain", "condition": args.condition,
           "seed": args.seed, "max_ticks": args.max_ticks, "result": stats}
    fn = os.path.join(er.root_dir(), "longchain",
                      f"expH_{args.condition}_seed{args.seed}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n[H] {args.condition} seed={args.seed} → {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
