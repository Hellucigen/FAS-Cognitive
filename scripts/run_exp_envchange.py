# run_exp_envchange.py — 实验 I：环境变化 / 信念更新
# （Paper-I 收尾 campaign PHASE 9。实验基础设施新增，核心机制零改动）
# ─────────────────────────────────────────────────────────────────────
# 因果问题：环境改变（A 不再产出 X / A 消失）后，既有机制能否检测变化、
#   抑制旧知识、修正行动？
#
# 更新通道的操作化（全部为既有机制，无 if environment_changed）：
#   - 感知面：树消失 → 附近方块/ find_blocks 失败 → gather 动作真实失败
#     （failed:* 进 SELF_STATE_CHANGE）；
#   - 统计面：失败进因果账本（contra 证据）→ action_prior 成功率记忆
#     下修（autonomy.py:2790 消费 prior_bonus ±0.05）→ 行动竞择变化；
#   - 图谱面：旧供给节点激活随感知消失而衰退（S11 口径）。
# 条件：
#   U0 update-ON：Phase1 4 轮实践（建 action_prior 成功记忆）→ 环境改变
#     （旧橡木带消失、新橡木带出现在视野外）→ 同一目标继续；账本活着
#     （失败实时下修 action_prior）。
#   U1 stale-memory：同 Phase1/同世界变化/同图谱继承，但 Phase2 换**新
#     CausalLearner**（账本冻结在旧成功记忆上，无更新通道）——
#     "stale prior" 对照。差异只能来自账本更新通道。
# 指标（§15）：变化检测时延（首次失败 gather→首次成功新带 gather）、
#   旧带重复尝试次数（stale 行为）、失败动作数、旧供给节点激活轨迹、
#   action_prior 变化（U0 应下修，U1 应钉住）、最终成功。
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
OLD_BAND = [(-4, -4), (-2, -4), (-4, -2)]      # Phase1 橡木带（近，视野内）
NEW_BAND = [(19, 4), (21, 4), (19, 6)]         # Phase2 新带（距原点 ~19-21，
                                               # 超出 find_blocks 半径 16）


def plant_tree(w, x, z, log=ORE):
    for i in range(3):
        for j in range(3):
            w.put_block(x + i - 1, 65 + j, z, log)
    w.put_block(x, 65, z, log)


def band(w, trees, log=ORE):
    for (x, z) in trees:
        plant_tree(w, x, z, log)


def remove_band(world, trees):
    """环境改变：旧资源带整带消失（世界模拟器侧操作）。"""
    for (x, z) in trees:
        for i in range(3):
            for j in range(3):
                world.remove_block(x + i - 1, 65 + j, z)
        world.remove_block(x, 65, z)


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


def read_action_priors(st):
    out = {}
    try:
        for k, v in (getattr(st.causal, "action_priors", None) or {}).items():
            if hasattr(v, "_succ") or hasattr(v, "successes"):
                out[str(k)] = {"succ": getattr(v, "successes", None)
                               or getattr(v, "_succ", None),
                               "obs": getattr(v, "observations", None)
                               or getattr(v, "_obs", None)}
            elif isinstance(v, dict):
                out[str(k)] = {kk: v.get(kk) for kk in list(v)[:4]}
            else:
                out[str(k)] = str(v)
    except Exception:
        pass
    return out


def count_failures(st):
    raw = getattr(st.tl, "_raw", []) or []
    n = 0
    for e in raw:
        if (e.get("event_type") == "SELF_STATE_CHANGE"
                and str((e.get("content") or {}).get("change") or "")
                .startswith("failed")):
            n += 1
    return n


def run_condition(cond, seed, max_ticks=200):
    rec = er.RunRecorder(family="envchange", mode="sandbox", seed=seed,
                         label=f"I-{cond}")
    rec.start({"condition": cond, "seed": seed})
    clk = Clock()
    st = build_stack(goal=TARGET, label=f"env_{cond}", seed=seed,
                     mc_gatherable=GATHERABLES)
    band(st.world, OLD_BAND)
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())

    # ── Phase1：旧带实践 4 轮（建成功记忆）────────────────────
    phase1_done = []
    for rnd in range(1, 5):
        if rnd > 1:
            plant_tree(st.world, -3, -3)
            st.world.set_inv()
            st.loop.add_goal({"type": "obtain", "target": TARGET,
                              "source": "experiment_obtain",
                              "text": f"实验目标：自主获得 {TARGET}（第{rnd}轮）"})
        for i in range(max_ticks):
            tick(st, clk)
            bridge_perception_gaps(st)
            if have_item(st, TARGET) > 0:
                phase1_done.append(i)
                break
    priors_p1 = read_action_priors(st)
    rec.log("PHASE1", json.dumps({"done": phase1_done,
                                  "priors": priors_p1}, ensure_ascii=False))
    print(f"[I:{cond}] phase1 rounds_done={phase1_done}", flush=True)

    # ── 环境改变：旧带消失、新带出现（视野外）─────────────────
    remove_band(st.world, OLD_BAND)
    st.world.set_inv()
    band(st.world, NEW_BAND)
    st.loop.add_goal({"type": "obtain", "target": TARGET,
                      "source": "experiment_obtain",
                      "text": f"实验目标：自主获得 {TARGET}（环境改变后）"})
    rec.log("EVENT", "环境改变：旧橡木带消失，新带出现在视野外")

    # ── Phase2：同一目标继续（更新通道差异在此）───────────────
    # U1 stale：换新 CausalLearner（成功记忆冻结在 Phase1 值上？——
    # 注意：新账本是**空**的，同样没有更新通道；与 U0 的差异 =
    # "旧成功记忆仍在指导"（U0 账本里 Phase1 成功记忆会被失败实时下修）
    # vs "无记忆也无更新"（U1）。stale 口径以 Phase1 图谱继承不现实
    # （同 stack），故 U1 = 冻结面：把 causal 置 None（统计通道整体
    # 静默，action_prior 不再更新——Phase1 的 prior_bonus 也消失）。
    # 诚实记录：U1 是"无更新通道"，不是"冻结旧值"——两种 stale 中
    # 更保守的一种（不会错误强化旧带）。
    if cond == "U1":
        st.causal = None
        st.am.causal = None
    oak_act_trace = []
    done_at, fails_p2_start = None, count_failures(st)
    for i in range(max_ticks):
        tick(st, clk)
        bridge_perception_gaps(st)
        nd = st.kg.nodes.get(ORE)
        oak_act_trace.append(round(float(getattr(nd, "activation", 0.0)
                                          or 0.0), 4)
                             if nd else 0.0)
        if have_item(st, TARGET) > 0:
            done_at = i
            break
    flush_causal(st)
    fails_total = count_failures(st)
    priors_p2 = read_action_priors(st)
    stats = {"condition": cond, "seed": seed,
             "phase1_done": phase1_done,
             "phase2_success": done_at is not None,
             "phase2_done_tick": done_at,
             "phase2_failures": fails_total - fails_p2_start,
             "priors_phase1": priors_p1, "priors_phase2": priors_p2,
             "oak_activation_trace": oak_act_trace,
             "graph": graph_stats(st)}
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra={k: v for k, v in stats.items()
                                 if k != "oak_activation_trace"})
    print(f"[I:{cond}] phase2 succ={stats['phase2_success']} @{done_at} "
          f"fails={stats['phase2_failures']}", flush=True)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=["U0", "U1"], required=True)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    stats = run_condition(args.condition, args.seed)
    out = {"experiment": "I_envchange", "condition": args.condition,
           "seed": args.seed, "result": stats}
    fn = os.path.join(er.root_dir(), "envchange",
                      f"expI_{args.condition}_seed{args.seed}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[I] {args.condition} seed={args.seed} → {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
