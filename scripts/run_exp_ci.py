# run_exp_ci.py — 实验 D：持续认知 / 交流意图（Communication Intent）
# （Paper-I 收尾 campaign PHASE 6；R6，本轮最重要新实验之一）
# ─────────────────────────────────────────────────────────────────────
# 因果问题：已发生但未立即表达的内部内容，能否在持续认知中保持、再点火、
# 进入交流意图，并依既有决策机制最终表达或保持沉默？
#
# 装配（全为既有机制，核心零改动；CHANGE_LOG 记 C22）：
#   CCLoop(kg, engine, nlp=None, buffer=None, config) 挂入沙箱 stack：
#     - tick 体零 LLM（decay/diffuse/脉冲/CI 生命周期/表达决策）；
#     - llm_budget 桩（can_call 恒 False，既有预算机制的装配替身）：
#       _express 到预算门降级"不表达（CI 保持）"、_free_think 跳过——
#       表达**决策**发生且可观测（expression_eval），语言实现按预算静默；
#     - 实验 mode=off（shield 表不生效）——idle_companion 不屏蔽，
#       tick_express_check 完整执行。
#   C0 CC ON：CC 每拍 tick_once（自带 decay+diffuse，接替 SandboxDiffuser）
#   C1 CC OFF：无 CC，SandboxDiffuser 照常（CI 无出生机制——对照）
#   C2 再点火禁用：CC ON 且 reignite_every_pulses=10^6（冷场永不撒种）
#
# 场景（§10；事件全部环境侧真实发生，无 if trigger→speak）：
#   Stage1 种子经历：注入 obtain oak_planks 目标，agent 真实完成（经历
#     入情景记忆/图谱）；此后不再注入任何目标（=无用户输入）。
#   Stage2 静默延续：跑 CC 拍，观察 CI 形成/滞留/衰减/丢弃。
#   Stage3 环境事件序列：T_rel 相关事件（世界 oak_log 重新出现在视野）/
#     T_unrel 无关事件（clay 方块出现，未知物）/ T_trig 触发事件
#     （crafting_table 出现在视野）——只改世界，不写任何规则。
#
# 指标（§10）：CI 创建数/状态迁移（forming→ready→expressed/discarded）、
#   CI 激活曲线、reinforced 次数、滞留时长、表达决策值（expression_eval）、
#   丢弃时刻、再点火计数（旧情景节点无感知触碰下复亮）、事件→CI 就绪时延。
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
TREES = [(-3, -3), (3, 3)]


class _BudgetStub:
    """既有 llm_budget 机制的沙箱替身：语言/反思预算恒不可用。

    效果（既有代码路径，非新逻辑）：_express 在预算门返回（CI 保持滞留，
    表达决策值照常写入 expression_eval）；_free_think 静默。CI 生命周期
    的全部图上机制不受影响。"""

    def can_call(self, purpose, tokens=0):
        return False

    def register(self, purpose, tokens=0):
        return False


def plant_tree(w, x, z, log=ORE):
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


def ci_snapshot(cc):
    """cc.state() 的 CI 面压缩（记录层）。"""
    st = cc.state()
    return {"tick": st.get("tick"),
            "cis": st.get("intentions", []),
            "thresholds": st.get("thresholds", {})}


def episodic_event_ids(st):
    """当前图上的情景空间事件节点 id（再点火观测面）。"""
    return [nid for nid, nd in st.kg.nodes.items()
            if str(getattr(nd, "graph_space", "")) == "episodic"][:8]


def run_condition(cond, seed, max_ticks):
    rec = er.RunRecorder(family="ci", mode="sandbox", seed=seed,
                         label=f"D-{cond}")
    rec.start({"condition": cond, "seed": seed})
    clk = Clock()
    cc_on = cond != "C1"
    # mode=off（goal=None）：shield 表不生效，表达决策可见
    st = build_stack(goal=None, label=f"ci_{cond}", seed=seed,
                     mc_gatherable=GATHERABLES)
    for (x, z) in TREES:
        plant_tree(st.world, x, z)
    cc = None
    reignite_log = []
    if cc_on:
        from continuous_cognition import ContinuousCognition
        cc = ContinuousCognition(st.kg, st.engine, None, None, st.loop.config)
        cc.llm_budget = _BudgetStub()
        if cond == "C2":
            cc.cfg["reignite_every_pulses"] = 10 ** 9   # 再点火永不触发
        # 单驱动器：CC.tick_once 自带 decay+diffuse（engine 未运行门控同
        # SandboxDiffuser），沙箱 tick 的 diffuser 让位，避免每拍双驱动
        st.diffuser = None
        # 再点火门直接插桩（记录层包装，非机制改动）：真再点火计数
        _orig_react = cc._reactivate_field

        def _logged_react(rc, temp):
            reignite_log.append({"tick_j": None, "field_temp": round(temp, 4)})
            return _orig_react(rc, temp)

        cc._reactivate_field = _logged_react
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())

    # ── Stage1：种子经历（真实完成一次 obtain）──────────────────
    st.loop.add_goal({"type": "obtain", "target": TARGET,
                      "source": "experiment_obtain",
                      "text": f"实验目标：自主获得 {TARGET}"})
    seed_done = None
    epi_ids = []
    for i in range(120):
        tick(st, clk)
        if cc is not None:
            cc.tick_once()
        bridge_perception_gaps(st)
        if have_item(st, TARGET) > 0:
            seed_done = i
            break
    epi_ids = episodic_event_ids(st)
    rec.log("STAGE", json.dumps({"stage": "seed_done", "tick": seed_done,
                                 "episodic": epi_ids}, ensure_ascii=False))
    st.loop.add_goal({"type": "obtain", "target": "__none__",
                      "source": "experiment_none",
                      "text": ""}) if False else None
    # 目标完成后不再注入任何目标（= 无用户输入期）

    # ── Stage2+3：静默延续 + 环境事件序列（只改世界）────────────
    T_UNREL, T_REL, T_TRIG = 10, 25, 40        # 相对静默期的拍序
    ci_trace = []
    react_hits = []                             # 再点火复亮观测
    prev_act = {}
    for j in range(max_ticks):
        if j == T_UNREL:
            st.world.put_block(1, 65, 1, "clay")        # 无关事件：未知方块
            rec.log("EVENT", "unrelated: clay 出现在视野")
        if j == T_REL:
            plant_tree(st.world, 0, 3, ORE)             # 相关事件：新 oak 树
            rec.log("EVENT", "related: oak_log 重新出现在视野")
        if j == T_TRIG:
            st.world.put_block(-1, 65, -1, "crafting_table")  # 触发事件
            rec.log("EVENT", "trigger: crafting_table 出现在视野")
        tick(st, clk)
        if cc is not None:
            cc.tick_once()
        bridge_perception_gaps(st)
        # 再点火观测：情景事件节点在无行动触碰的静默期激活上升
        for nid in epi_ids:
            nd = st.kg.nodes.get(nid)
            if nd is None:
                continue
            a = float(getattr(nd, "activation", 0.0) or 0.0)
            p = prev_act.get(nid, 0.0)
            if a > p + 0.05 and j > seed_done + 2:
                react_hits.append({"tick": j, "node": nid,
                                   "from": round(p, 3), "to": round(a, 3)})
            prev_act[nid] = a
        if cc is not None and j % 2 == 0:
            snap = ci_snapshot(cc)
            try:
                snap["field_temp"] = round(float(cc._field_temperature()), 4)
                snap["cold_beats"] = int(getattr(cc, "_cold_beats", 0))
            except Exception:
                pass
            ci_trace.append(snap)
        if j % 10 == 0:
            rec.log("PROGRESS", f"silence tick {j} cis="
                    f"{len(ci_trace[-1]['cis']) if ci_trace else 0}")
    flush_causal(st)
    cis_final = cc.state()["intentions"] if cc is not None else []
    statuses = [c.get("status") for c in cis_final]
    stats = {"condition": cond, "seed": seed,
             "seed_done_tick": seed_done,
             "cc_on": cc_on,
             "ci_created": len({c["id"] for tr in ci_trace
                                for c in tr["cis"]}),
             "ci_final": cis_final, "ci_statuses_final": statuses,
             "ci_trace": ci_trace, "reignitions": reignite_log,
             "ambient_act_rises": react_hits,
             "episodic_ids": epi_ids,
             "graph": graph_stats(st)}
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra={k: v for k, v in stats.items()
                                 if k not in ("ci_trace", "ci_final")})
    print(f"[D:{cond}] seed_done@{seed_done} ci_created="
          f"{stats['ci_created']} statuses={statuses} "
          f"reignitions={len(reignite_log)} rises={len(react_hits)}", flush=True)
    for c in cis_final[:4]:
        print(f"    CI {c['id']} kind={c.get('kind')} status="
              f"{c.get('status')} act={c.get('activation')} "
              f"basis={c.get('basis', [])[:3]}", flush=True)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=["C0", "C1", "C2"], required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-ticks", type=int, default=60)
    args = ap.parse_args()
    stats = run_condition(args.condition, args.seed, args.max_ticks)
    out = {"experiment": "D_ci", "condition": args.condition,
           "seed": args.seed, "max_ticks": args.max_ticks, "result": stats}
    fn = os.path.join(er.root_dir(), "ci",
                      f"expD_{args.condition}_seed{args.seed}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n[D] {args.condition} seed={args.seed} → {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
