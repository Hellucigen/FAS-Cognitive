# run_prior_conditions.py — §10 先验知识条件 A/B/C 对照（sandbox；零 LLM）
# 用法:
#   E:/Miniforge.envs/Fascinator/python.exe -X utf8 scripts/run_prior_conditions.py \
#       [--goal oak_planks] [--seeds 1,2,3] [--max-ticks 200]
# 操作化定义（与报告 §15 一致）：
#   C（more complete）= 现状：图谱先验 + 配方表 + 方块表 + 任务先验
#   B（limited）      = 图谱先验查询通道关（prior.enabled=False），机制表保留
#   A（zero/minimal） = 配方表/方块表也清空：任何计划知识都不给
# 全部 run 在同样世界/种子/目标下，唯一变体是 prior_level。
# 期望（先验假设，看数据说话）：A 下无合成计划 → gather 能采但目标不达成
#   （若 A 也达成，说明行为纯由感知-扩散-因果涌现涌现，是更强的结果）。
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(1, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
if os.getcwd() != os.path.dirname(os.path.dirname(os.path.abspath(__file__))):
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandbox_lab import build_stack, tick, have_item, Clock, flush_causal  # noqa
import experiment_recorder as er                                         # noqa
from run_ablation_matrix import setup_world, GOAL_DEFAULT                # noqa

LEVELS = ["A", "B", "C"]


def run_one(level, goal, seed, max_ticks):
    kw = {"goal": goal, "label": f"prior-{level}", "seed": seed,
          "prior_level": level}
    st = build_stack(**kw)
    setup_world(st, goal)
    clk = Clock()
    rec = er.RunRecorder(family="prior_conditions", mode="sandbox",
                         seed=seed, label=f"prior-{level}-{goal}")
    rec.start({"prior_level": level, "goal": goal, "max_ticks": max_ticks})
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    done_at = None
    for i in range(max_ticks):
        tick(st, clk)
        if have_item(st, goal) > 0:
            done_at = i
            rec.log("RESULT", f"达成 @tick{i}", tick=i)
            break
    flush_causal(st)
    n_actions = sum(1 for e in st.tl._raw
                    if e.get("event_type") == "ACTION")
    if done_at is None:
        rec.log("RESULT", f"未达成（上限 {max_ticks} ticks）", tick=max_ticks)
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    m = {"prior_level": level, "seed": seed, "success": done_at is not None,
         "success_tick": done_at,
         "graph": {"nodes": len(st.kg.nodes), "edges": len(st.kg.edges)},
         "inv": dict(st.world.inv_map()),
         "action_count": n_actions}
    rec.finalize(ok=True, extra=m)
    print(f"[PRIOR] {level} goal={goal} seed={seed} "
          f"达成@{done_at if done_at is not None else '—'} "
          f"动作={n_actions}", flush=True)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", default=GOAL_DEFAULT)
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--max-ticks", type=int, default=200)
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    results = {lv: [] for lv in LEVELS}
    for lv in LEVELS:
        for s in seeds:
            results[lv].append(run_one(lv, args.goal, s, args.max_ticks))
    out = {"goal": args.goal, "seeds": seeds,
           "max_ticks": args.max_ticks, "results": results}
    fn = os.path.join(er.root_dir(), "prior_conditions",
                      f"prior_{args.goal}_{'_'.join(map(str, seeds))}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[Prior] 条件对照完成 → {fn}")
    for lv in LEVELS:
        hits = [(r["seed"], r["success_tick"]) for r in results[lv]
                if r["success"]]
        print(f"  {lv}: 达成={hits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())