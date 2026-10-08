# run_exp_emotion.py — 实验 C：情绪/动机状态 → 扩散动力学调制
# （Paper-I 收尾 campaign PHASE 5；R5。实验基础设施新增，核心机制零改动）
# ─────────────────────────────────────────────────────────────────────
# 因果问题：情绪/动机状态是否对既有扩散机制产生可测量调制？
#
# 已核实的生产接线（预审计 §1.9）：
#   continuous_cognition.py:272-288 每拍 arousal=图上情绪节点最大激活/3、
#   stress=cortisol 慢分量 → engine.update_param_modulation(ar, st)；
#   diffusion_engine.py:337-358 param_gain = 1 + 0.3·arousal − 0.15·stress，
#   钳位 [0.7, 1.6]；:1271 消费点 = 发射比率乘子（a·emission_ratio·gain）。
#
# 条件（§9；状态值只用仓库已有机制——update_param_modulation 是 CC 生产
#   调用的同一公开入口，arousal/stress 是既有状态变量，值域取生产可达值）：
#   E0 neutral：不喂调制（沙箱默认；param_gain=1.0）
#   E1-high：arousal=1.0, stress=0（生产可达：情绪节点激活 3.0 封顶）
#       → param_gain=1.3
#   E1-low：arousal=0, stress=1.0（生产可达：cortisol dev 满偏）
#       → param_gain=0.85
#   E2 disabled：E1-high 的状态值 + 调制禁用（config param_modulation={}
#       → :352 param_gain 钉 1.0）——分离"状态存在"与"调制生效"
# 每拍经 st.engine.update_param_modulation(...) 喂值（CC.tick_once 同语义），
#   装配在 runner 的调制钩子里；gain 回读记录在案。
#
# 指标（§9，重点非成功率）：param_gain 回读、激活总质量、点亮域
#   （≥0.05 节点集）、目标链首亮 tick（物品:oak_log/oak_log）、竞争节点
#   （birch_log）激活、Top-5 焦点构成、行动时延/成功（记录不做声称）。
# 世界 = 实验 B 同款（oak 远视野外 + birch 近干扰）。
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
TREES = [(6, 0), (-2, 0)]
SAMPLE_IDS = ["物品:oak_planks", "物品:oak_log", "物品:birch_log",
              "oak_log", "birch_log"]

COND_STATE = {
    "E0": None,                       # 不喂（沙箱默认，gain=1.0）
    "E1_high": (1.0, 0.0),            # arousal=1, stress=0 → gain 1.3
    "E1_low": (0.0, 1.0),             # arousal=0, stress=1 → gain 0.85
    "E2_disabled": (1.0, 0.0),        # 同 E1_high 状态值，调制禁用
}


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


def top5_focus(st):
    top = sorted(st.kg.nodes.values(),
                 key=lambda nd: -float(getattr(nd, "activation", 0.0)
                                       or 0.0))[:5]
    return [getattr(nd, "id", "?") for nd in top]


def run_condition(cond, seed, max_ticks):
    state = COND_STATE[cond]
    rec = er.RunRecorder(family="emotion", mode="sandbox", seed=seed,
                         label=f"C-{cond}")
    rec.start({"episode": "ep2", "condition": cond, "seed": seed,
               "arousal": state[0] if state else None,
               "stress": state[1] if state else None})
    clk = Clock()
    cfg_extra = {}
    if cond == "E2_disabled":
        # :351 守卫 pm.get("enabled", True) → enabled=False 钉 gain=1.0
        cfg_extra["param_modulation"] = {"enabled": False}
    st = build_stack(goal=TARGET, label=f"emo_{cond}", seed=seed,
                     mc_gatherable=GATHERABLES)
    if cfg_extra:
        st.engine.config.update(cfg_extra)
    plant_tree(st.world, *TREES[0], ORE)
    plant_tree(st.world, *TREES[1], NOISE)
    try:
        import prior_knowledge as pk
        pk.open_gap(st.kg, st.engine, NOISE, "unknown_use", st.loop.config)
    except Exception:
        pass
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    curves = []
    lit_ticks = {nid: None for nid in ("oak_log", "物品:oak_log")}
    gains = []
    done_at = None
    for i in range(max_ticks):
        tick(st, clk)
        # CC.tick_once 同语义喂调制（生产入口）；E0 不喂
        if state is not None:
            st.engine.update_param_modulation(*state)
        gains.append(round(float(getattr(st.engine, "_param_gain", 1.0)), 4))
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
            snap["_mass"] = round(sum(float(getattr(nd, "activation", 0.0)
                                            or 0.0)
                                      for nd in st.kg.nodes.values()), 3)
            snap["_gain"] = gains[-1]
            snap["_top5"] = top5_focus(st)
            curves.append(snap)
            rec.log("ACTSNAP", json.dumps(snap, ensure_ascii=False), tick=i)
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
             "actions_invalid": invalid, "lit_ticks": lit_ticks,
             "gain_first": gains[0] if gains else None,
             "gain_last": gains[-1] if gains else None,
             "final_mass": curves[-1]["_mass"] if curves else None,
             "final_top5": curves[-1]["_top5"] if curves else [],
             "final_lit_ge005": len([
                 n for n, nd in st.kg.nodes.items()
                 if float(getattr(nd, "activation", 0.0) or 0.0) >= 0.05]),
             "activation_curves": curves, "graph": graph_stats(st)}
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra={k: v for k, v in stats.items()
                                 if k != "activation_curves"})
    print(f"[C:{cond}] succ={stats['success']} @{done_at} "
          f"act={stats['actions_total']} gain={stats['gain_first']}→"
          f"{stats['gain_last']} mass={stats['final_mass']} "
          f"lit={lit_ticks} litset={stats['final_lit_ge005']} "
          f"top5={stats['final_top5']}", flush=True)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=list(COND_STATE), required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-ticks", type=int, default=150)
    args = ap.parse_args()
    stats = run_condition(args.condition, args.seed, args.max_ticks)
    out = {"experiment": "C_emotion", "condition": args.condition,
           "seed": args.seed, "max_ticks": args.max_ticks, "ep2": stats}
    fn = os.path.join(er.root_dir(), "emotion",
                      f"expCemo_{args.condition}_seed{args.seed}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[C-emo] {args.condition} seed={args.seed} → {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
