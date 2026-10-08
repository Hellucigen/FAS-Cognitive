# run_ablation_matrix.py — §7 消融矩阵执行器（sandbox；零 LLM 零认知改动）
# 用法:
#   E:/Miniforge.envs/Fascinator/python.exe -X utf8 scripts/run_ablation_matrix.py \
#       [--seeds 1,2,3] [--max-ticks 120]
# 每组：同一标准世界 + 目标 oak_planks，仅切换 build_stack 开关组合，
# tick 世界到达成或上限。输出：控制台对比表 + FAS_Research_Experiments/
# ablation/run_*/（metadata/events/snapshots/metrics）。main（full 配置）
# 先跑，消融组随后，保证基线世界一致（同一 RecipeTable/世界种法）。
#
# 诚实边界（2026-09-28 定版）：
#   * −Drive 不在矩阵内——沙箱栈结构上不含张力/驱动引擎（CognitiveField
#     /drive_engine 从未装配，drive 激活天然≈0），无差分基础，报告记为 N/A。
#   * −Gap 走 config["prior"]["enabled"]=False（唯一消费点 _gap_candidates）。
#   * −GoalFormation：不注入目标（goal_on=False）——目标由部署侧注入是
#     实验模式语义（target 注入禁令覆盖问题之外，specs §7 要求"目标形成的
#     缺席"）；无目标时的自发行为即该消融的观测面。
#   * 多 seed 保证：同开关组合在 seed 1..N 重复，随机性统一播种。
# ============================================================================

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
from run_sandbox_scenarios import _setup_standard_world, goal_done        # noqa

GOAL_DEFAULT = "oak_planks"

# 消融矩阵（§7）：full 基线 + 单变消融 + 最低组合消融。
# build_stack 开关映射：-Diffusion→diffusion_on / -Episodic→causal_on /
# -Prior→prior_on / -Writeback→writeback_on / -RelevantSelf→self_goal_on /
# -GoalFormation→goal_on / -Gap→gap_on。省略键 = True（全开）。
ABLATIONS = [
    {"name": "full"},
    {"name": "-Diffusion", "diffusion_on": False},
    {"name": "-Episodic", "causal_on": False},
    {"name": "-Prior", "prior_on": False},
    {"name": "-Gap", "gap_on": False},
    {"name": "-Writeback", "causal_on": True, "writeback_on": False},
    # §7 规格组合（2026-09-28 补跑）：至少组合作为
    {"name": "-Episodic-Writeback", "causal_on": False, "writeback_on": False},
    {"name": "-Diffusion-Episodic", "diffusion_on": False, "causal_on": False},
    {"name": "-Diffusion-Gap", "diffusion_on": False, "gap_on": False},
    {"name": "-RelevantSelf", "self_goal_on": False},
    {"name": "-GoalFormation", "goal_on": False},
]


def setup_world(st, goal=GOAL_DEFAULT):
    """标准世界；iron_ingot 任务补近区矿簇（spawn ≤8 格内，否则技能层
    find_blocks radius=12 探测不到）。
    stone ×8（熔炉 = cobblestone×8）与 coal ×3（熔炼燃料）。"""
    _setup_standard_world(st)
    if goal == "iron_ingot":
        for i in range(8):
            st.world.put_block(0 + (i % 4), 62, -2 - (i // 4), "stone")
        for i in range(3):
            st.world.put_block(4 + i, 62, -1, "coal")
        # 近区铁供给（spawn ≤8 格）：iron_ore 与 deepslate_iron_ore 双形
        # （闭包链可能从 M1 经验学到 deepslate 形——世界供应双形对齐知识）
        for i in range(2):
            st.world.put_block(i, 62, 2, "iron_ore")
            st.world.put_block(2 + i, 62, 2, "deepslate_iron_ore")
        # 预置工作台×1（矿区补给站：真实世界里村庄/旧营地常见；环境事实，
        # 非知识注入——否则熔炉配方 needs_table 判定卡在手工台缺失上）
        st.world.put_block(0, 62, 0, "crafting_table")


def run_one(name, seed, switches, max_ticks, label=None, goal=GOAL_DEFAULT):
    kw = {"goal": goal, "label": label or name, "seed": seed}
    kw.update(switches)
    goal_on = bool(switches.get("goal_on", True))
    st = build_stack(**kw)
    setup_world(st, goal)
    clk = Clock()
    rec = er.RunRecorder(family="ablation", mode="sandbox",
                         seed=seed, label=label or name)
    rec.start({"ablation": name, "goal": goal, "switches": switches,
               "max_ticks": max_ticks})
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    done_at = None
    for i in range(max_ticks):
        tick(st, clk)
        if goal_on and goal_done(st, goal):
            done_at = i
            rec.log("RESULT", f"达成 @tick{i}", tick=i)
            break
    flush_causal(st)
    n_actions = 0
    try:
        n_actions = sum(1 for e in st.tl._raw
                        if e.get("event_type") == "ACTION")
    except Exception:
        pass
    if done_at is None:
        rec.log("RESULT", f"未达成（上限 {max_ticks} ticks）", tick=max_ticks)
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    m = {"ablation": name, "seed": seed, "success": done_at is not None,
         "success_tick": done_at,
         "graph": {"nodes": len(st.kg.nodes), "edges": len(st.kg.edges)},
         "inv": dict(st.world.inv_map()),
         "action_count": n_actions}
    rec.finalize(ok=True, extra=m)
    print(f"[ABL] {name:22s} seed={seed} 达成@{done_at if done_at is not None else '—'}"
          f" 动作={n_actions} 图={m['graph']['nodes']}节点/"
          f"{m['graph']['edges']}边", flush=True)
    return m, st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--max-ticks", type=int, default=120)
    ap.add_argument("--only", default=None, help="逗号分隔的矩阵项名子串过滤")
    ap.add_argument("--goal", default=GOAL_DEFAULT,
                    help="目标物品（默认 oak_planks 短链；cobblestone 更长链更易出差分）")
    args = ap.parse_args()
    goal = args.goal
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    entries = [a for a in ABLATIONS
               if not args.only or args.only in a["name"]]
    t0 = time.time()
    results = {}
    for e in entries:
        rs = []
        for s in seeds:
            m, _st = run_one(e["name"], s, {k: v for k, v in e.items()
                                            if k != "name"},
                             args.max_ticks, goal=goal)
            rs.append(m)
        results[e["name"]] = rs
    out = {"goal": goal, "seeds": seeds, "max_ticks": args.max_ticks,
           "elapsed_s": round(time.time() - t0, 1), "results": results}
    fn = os.path.join(er.root_dir(), "ablation", "matrix_summary.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[Ablation] 矩阵完成 → {fn}（{out['elapsed_s']}s）")
    for name, rs in results.items():
        hits = [(r["seed"], r["success_tick"]) for r in rs
                if r["success"]]
        print(f"  {name:24s} 达成={hits}")


if __name__ == "__main__":
    sys.exit(main())