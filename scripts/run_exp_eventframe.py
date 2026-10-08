# run_exp_eventframe.py — 实验 D：行动侧事件框架失败抑制探针
# ─────────────────────────────────────────────────────────────────────
# 因果问题：行动记忆若按愿景形态（签名级事件框架节点，槽位边连实体，
# 结果边带正/负极性）落图，失败经验的**负激活**能否经扩散统一抑制机制
# （diffusion_engine.py:24/1015-1045：负权边=抑制、不消耗发射预算）压低
# 对象实体激活，从而改变下一次扩散里的候选注意选择——完全不经过
# 表格闭包/统计账本？
#
# 装配（C20，build_stack eventframe 参数）：`行动经验:gather_resource(
# birch_log)` 事件节点（episodic 空间）-[涉及]→ birch_log 实体（正权
# 槽位）+[结果]→ 失败（fail_side=-0.8 负权结果边）；每认知 tick 低量
# 再点火（模拟持续认知 §485 记忆再点火的沙箱替身）→ 激活沿负权边持续
# 向对象发射抑制。
#
# 条件：inhibit（注入"gather birch_log 失败过"的经验）vs control（无）。
# 世界/目标同实验 A：oak@(6,0) 远（目标链）+ birch@(-2,0) 近（好奇心
# 缺口钩子），目标 obtain oak_planks。对照组应复现 A 的首动 birch；
# 若失败抑制生效 → birch_log 激活走低 → 首动翻转/延后（行为差分）。
# 零核心改动：装配层（注入边/再点火）+ 记录层（激活采样/reason 打印）。
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
import importlib
_rt = importlib.import_module("run_exp_transfer")   # 复用同构 helpers
plant_tree = _rt.plant_tree
bridge_perception_gaps = _rt.bridge_perception_gaps
open_noise_gap = _rt.open_noise_gap
collect_stats = _rt.collect_stats
sample_activations = _rt.sample_activations

TARGET = "oak_planks"
ORE = "oak_log"
NOISE = "birch_log"
GATHERABLES = [ORE, NOISE]

FAMILY = "eventframe"
SAMPLES = ["birch_log", f"物品:{NOISE}", ORE, f"物品:{TARGET}",
           f"行动经验:gather_resource({NOISE})"]


def make_stack(seed, label, inhibit, debug_reason=False, real_fail=False,
               trigger="prearm"):
    kw = {"goal": TARGET, "label": label, "seed": seed,
          "mc_gatherable": GATHERABLES, "cc_diffuse": True}
    if inhibit:
        kw["eventframe"] = (NOISE, -0.8, 1.2, trigger)
    st = build_stack(**kw)
    # 世界：oak 远 / birch 近（同实验 A/B/C 图景）
    for (x, z) in [(6, 0), (-2, 0)]:
        plant_tree(st.world, x, z, "oak_log" if x > 0 else "birch_log")
    if real_fail:
        # C20g 真实失败探针：birch 树在第一次被挖时"消失"（环境事实：
        # 树在她到达时已不在/枯死了）——感知可见（建档+缺口照常），
        # gather 一拍真实失败（block_not_found 回执）。对照/探查区别
        # 只在事件框架是否由失败回执内源落图（on_failure），不在世界。
        st.world.despawn_on_dig = {NOISE: True}
    open_noise_gap(st)
    if debug_reason:
        _orig = st.loop._score_action

        def _dbg(cand, percept, now):
            try:
                src = str(cand.get("action_type") or "")
                if src in ("gather_resource",):
                    print(f"[DBG-REASON] {src}@{cand.get('target')} "
                          f"reason={cand.get('reason')} "
                          f"act_raw={[float(getattr(st.kg.nodes.get(n), 'activation', 0.0) or 0.0) if st.kg.nodes.get(n) else None for n in (cand.get('reason') or [])]}")
            except Exception:
                pass
            return _orig(cand, percept, now)
        st.loop._score_action = _dbg
    return st


def run_cond(cond, seed, max_ticks, debug_reason=False):
    # 条件矩阵（C20 预注入 / C20e 修复 / C20g 真实失败路径）：
    #   control       树在，无事件框架 → 基线（pre-fix 首动 birch）
    #   inhibit       树在，prearm 框架 → C20e 行为差分（首动 oak 5/5）
    #   control_real  树在首挖时消失，无框架 → 真实失败纯计数/冷却基线
    #   inhibit_real  树在首挖时消失，on_failure 框架 → 失败回执内源落图
    # on_failure 不发 warmup：失败前 efi 未 armed，warmup 只是空跑；
    # 失败回执到达的下一拍起抑制随正常 tick 再点火积累。
    inhibit = cond in ("inhibit", "inhibit_real")
    real_fail = cond in ("control_real", "inhibit_real")
    trigger = "on_failure" if cond == "inhibit_real" else "prearm"
    rec = er.RunRecorder(family=FAMILY, mode="sandbox", seed=seed,
                         label=f"{cond}-ep")
    rec.start({"episode": "D", "condition": cond, "seed": seed,
               "inhibit": inhibit, "task": TARGET,
               "real_fail": real_fail, "trigger": trigger})
    clk = Clock()
    st = make_stack(seed, f"d_{cond}", inhibit, debug_reason=debug_reason,
                    real_fail=real_fail, trigger=trigger)
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    if st.efi is not None and st.efi.trigger == "prearm":
        # 蓝图②/C20e：再点火时序前置（warmup）——首次决策 tick 前先跑
        # 几拍 diffuser+efi，让失败抑制在注意力竞择发生时已积累（C20 原
        # 版再点火在决策之后，首拍决策前抑制从未入图，行为零差分的结构
        # 性时序原因之一）。装配层改动：只调两个已有驱动器，零核心。
        for _ in range(3):
            st.diffuser()
            st.efi()
    done_at = None
    fail_tick = None
    for i in range(max_ticks):
        tick(st, clk)
        bridge_perception_gaps(st)
        if st.efi is not None and st.efi.trigger == "on_failure" \
                and fail_tick is None:
            # 企图记录真实失败回执 tick——实测该探针对 timeout 失败恒定
            # None：timeout ∈ _TRANSIENT_WORLD_REASONS（autonomy.py:106），
            # 暂态失败不进 _attempts（也不进 causal/先验），`失败统计`
            # 通道对此回执静默。此字段保留 None 即如实记录这一事实
            # （失败形态=timeout 暂态；行为差分仍真实发生，见报告 §9）。
            try:
                att = getattr(st.loop, "_attempts", None) or {}
            except Exception:
                att = {}
            _k = f"gather_resource@{NOISE}"
            if _k in att and int(att[_k].get("count", 0)) > 0:
                fail_tick = i
        if i % 2 == 0:
            sample_activations(st, rec, SAMPLES, i)
            if i <= 6:
                print(f"[ACT] t{i} " + json.dumps(
                    {k: round(float(getattr(st.kg.nodes.get(k), 'activation',
                                            0.0) or 0.0), 3)
                     for k in SAMPLES}, ensure_ascii=False), flush=True)
        if have_item(st, TARGET) > 0:
            done_at = i
            break
    flush_causal(st)
    stats = collect_stats(st, rec, f"ep_{cond}", TARGET)
    stats["success"] = done_at is not None
    stats["success_tick"] = done_at
    stats["fail_tick"] = fail_tick
    stats["start_activation"] = {
        str(nid): round(float(getattr(st.kg.nodes.get(nid), "activation", 0.0)
                              or 0.0), 4)
        for nid in (ORE, NOISE, f"物品:{TARGET}", f"物品:{NOISE}",
                    f"行动经验:gather_resource({NOISE})")}
    # 缺口状态（被抑制压低 or 照旧）→ 注意力竞择的关键比较面
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra=stats)
    seq = [f"{t}@{g}" for t, g in zip(stats["types"], stats["targets"])]
    print(f"[D:Ep][{cond}] success={stats['success']}@{done_at} "
          f"n={stats['actions_total']} red={stats['actions_redundant']} "
          f"first={seq[0] if seq else None} fail_tick={fail_tick}",
          flush=True)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition",
                    choices=["inhibit", "control", "inhibit_real",
                             "control_real"],
                    required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-ticks", type=int, default=300)
    ap.add_argument("--debug-reason", action="store_true")
    args = ap.parse_args()
    out = {"experiment": "D", "condition": args.condition, "seed": args.seed,
           "task": TARGET, "max_ticks": args.max_ticks,
           "eventframe": {"obj": NOISE, "fail_side": -0.8, "reign_amt": 1.2,
                          "trigger": "on_failure"
                          if args.condition == "inhibit_real"
                          else "prearm", "real_fail":
                              args.condition in ("control_real",
                                                 "inhibit_real")}}
    out["ep"] = run_cond(args.condition, args.seed, args.max_ticks,
                         debug_reason=args.debug_reason)
    fn = os.path.join(er.root_dir(), FAMILY,
                      f"expD_{args.condition}_seed{args.seed}.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[D] {args.condition} seed={args.seed} → {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())