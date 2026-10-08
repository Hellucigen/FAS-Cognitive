# run_sandbox_scenarios.py — 沙箱场景执行器（§16-26；Recorder 绑定）
# 用法:
#   E:/Miniforge.envs/Fascinator/python.exe -X utf8 scripts/run_sandbox_scenarios.py \
#       --scenario s3 --seed 1 --ticks 120
# 零 LLM、零改动认知栈；世界=SandboxWorld（环境事实），目标=goal 注入。
# ============================================================================

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(1, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
if os.getcwd() != os.path.dirname(os.path.dirname(os.path.abspath(__file__))):
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandbox_lab import (build_stack, tick, goal_state, have_item,     # noqa
                         graph_stats, Stack, Clock, flush_causal)
import experiment_recorder as er                                       # noqa


def _setup_standard_world(st, tree_far=6, ore_far=20):
    """标准世界：树(近处，find_blocks 默认半径 12 内) + 石头 + 铁矿石（深层）。
    树在 6 格内保证 gather 的 find_blocks(radius=12) 能命中（真机 fallback 前
    的默认探测半径）。一切可放可改，无答案注入。"""
    w = st.world
    w.put_tree(tree_far, 0, "oak_log")
    for i in range(4):
        w.put_block(tree_far + 1 + i, 63, 2 + i, "stone")
    w.put_ore(ore_far, 3, "iron_ore", 2)
    w.put_block(8, 62, -8, "stone")
    w.put_block(9, 62, -8, "stone")


def goal_done(st, target):
    """达成 = 背包实物。goal_state 的 success 是 last_success_at（§P5 进展
    锚点，中间步骤成功也会写），不能当达成判据（2026-09-28 S4 Ep2 假达成
    修）；autonomy 真实销账同样以背包真有为准（autonomy.py _obtain_candidates
    step.done→销账）。观测层保持一致语义。"""
    return have_item(st, target) > 0


def count_actions(events):
    n = 0
    for row in events:
        tag = str(row.get("tag") or "")
        if tag in ("ACTION", "CANDIDATE", "TARGET"):
            n += 1
    return n


def run_scenario(spec, seed=1, ticks=120, recorder=None):
    """执行一个场景说明 spec = {label, family, goal, ticks, setup, env_steps}。
    env_steps: [(at_tick, callable(st))] 环境变化时间表。
    返回 (metrics dict, Stack)。"""
    t0 = time.time()
    rec = recorder or er.RunRecorder(family=spec.get("family", "sandbox"),
                                     mode=spec.get("mode", "learning_closed_loop"),
                                     seed=seed, label=spec["label"])
    rec.start({"tick_s": 16.0, "ticks": ticks})
    label = f"{spec['label']}_s{seed}"
    parent = os.path.join(rec.dir, "appdata")
    st = build_stack(goal=spec.get("goal"), label=label, seed=seed,
                     base_parent=parent,
                     diffusion_on=spec.get("diffusion_on", True),
                     causal_on=spec.get("causal_on", True),
                     prior_on=spec.get("prior_on", True),
                     goal_on=spec.get("goal_on", True),
                     writeback_on=spec.get("writeback_on", True),
                     self_goal_on=spec.get("self_goal_on", True))
    (spec.get("setup") or _setup_standard_world)(st)
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    rec.log("RUN", f"场景 {spec['label']} 开始（seed={seed}）",
            goal=spec.get("goal"))

    clk = Clock()
    metrics = {"ticks_run": 0, "success": False, "success_tick": None,
               "actions": 0, "invalid_candidates": 0, "goals": [],
               "graph": [], "inv": {}}
    steps_ix = 0
    env_steps = sorted(spec.get("env_steps") or [],
                       key=lambda kv: kv[0])
    prev_nodes = len(st.kg.nodes)
    for i in range(ticks):
        while steps_ix < len(env_steps) and i >= env_steps[steps_ix][0]:
            name, fn = env_steps[steps_ix][1], env_steps[steps_ix][2]
            fn(st)
            rec.log("ENVCHANGE", f"环境变化 @tick{i}: {name}")
            steps_ix += 1
        out = tick(st, clk)
        acted = bool(out.get("acted"))
        reason = out.get("reason")
        if acted or reason:
            rec.log("ACTION", f"tick{i} reason={reason}", acted=acted)
        gs = graph_stats(st)
        if gs["nodes"] != prev_nodes:
            rec.log("GRAPH", f"tick{i} 规模 {prev_nodes}→{gs['nodes']}",
                    **gs)
            prev_nodes = gs["nodes"]
        if spec.get("goal") and goal_done(st, spec["goal"]):
            metrics["success"] = True
            metrics["success_tick"] = i
            rec.log("RESULT", f"tick{i} 目标达成", tick=i)
        if metrics["success"] and i > metrics["success_tick"] + 6:
            pass
    metrics["ticks_run"] = ticks
    metrics["actions"] = count_actions(rec._events)
    metrics["goals"] = goal_state(st)
    metrics["graph"] = graph_stats(st)
    metrics["inv"] = dict(st.world.inv_map())
    metrics["runtime_s"] = round(time.time() - t0, 2)
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    if st.causal is not None:
        try:
            metrics["causal_hypotheses"] = len(st.causal._hypotheses)
            metrics["causal_promoted"] = len(st.causal._promoted)
        except Exception:
            pass
    events = rec._events
    # 无效动作近似：RESULT fail / CANDIDATE 未执行的探针计数
    rec.log("RESULT", f"场景结束 success={metrics['success']} "
                      f"at tick {metrics['success_tick']}",
            **{k: v for k, v in metrics.items()
               if k not in ("goals", "graph", "inv")})
    rec.finalize(ok=True, extra=dict(spec))
    print(f"[{spec['label']}] seed={seed} ticks={ticks} "
          f"success={metrics['success']}@{metrics['success_tick']} "
          f"actions={metrics['actions']} graph={metrics['graph']} "
          f"inv={metrics['inv']} runtime={metrics['runtime_s']}s")
    return metrics, st


SCENARIOS = {}


def s3_knowledge_discovery(seed=1, ticks=110):
    """S3 知识发现→写入→复用（M2 episode 结构）：第一阶段学会 oak_log→planks，
    第二阶段任务 stick（复用料材）看是否直接复用而非重新探索。"""
    calls = {"total": 0}

    def _run_phase(goal, relabel, start_inv=None, prior_on=True, inherit=None):
        rec = er.RunRecorder(family="sandbox", label=relabel, seed=seed,
                             mode="learning_closed_loop")
        rec.start({"phase": relabel, "tick_s": 16.0, "ticks": ticks})
        parent = os.path.join(rec.dir, "appdata")
        user_prior = True  # 阶段 2 同配置（不因阶段不同而改先验）
        st = build_stack(goal=goal, label=relabel, seed=seed,
                         base_parent=parent, prior_on=prior_on,
                         inherit_graph=inherit)
        _setup_standard_world(st)
        if start_inv:
            st.world.set_inv(**start_inv)
        rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
        rec.log("RUN", f"阶段开始 goal={goal}")
        clk = Clock()
        done_ix = None
        for i in range(ticks):
            out = tick(st, clk)
            if done_ix is None and goal_done(st, goal):
                done_ix = i
                rec.log("RESULT", f"目标达成 @tick{i}", tick=i)
        m = {"phase": relabel, "goal": goal, "ticks": ticks,
             "success": done_ix is not None, "success_tick": done_ix,
             "graph_start": len([1]) and None,
             "graph": graph_stats(st), "inv": dict(st.world.inv_map())}
        rec.log("RESULT", "阶段结束", **m)
        rec.finalize(ok=True, extra={"goal": goal})
        print(f"[S3:{relabel}] success={m['success']}@{m['success_tick']} "
              f"graph={m['graph']} inv={m['inv']}")
        calls["total"] += 1
        # 图谱快照落盘：供下一阶段继承（跨 episode 复用）
        gpath = os.path.join(rec.dir, "snapshots", "graph_ep.json")
        with open(gpath, "w", encoding="utf-8") as f:
            json.dump(st.kg.to_dict(), f, ensure_ascii=False, default=str)
        return st, gpath

    # Episode A：新手学 oak_log→planks（零背包）
    stA, gpathA = _run_phase("oak_planks", "S3-EpA-发现planks")
    # Episode B：继承图谱，任务 stick（复用料材：planks 配方已在图里）
    stB, gpathB = _run_phase("stick", "S3-EpB-复用stick", inherit=gpathA)
    return {"epA": stA, "epB": stB}, stB


SCENARIOS["s3"] = s3_knowledge_discovery
SCENARIOS["s3a"] = lambda seed=1, ticks=110: s3_knowledge_discovery(seed, ticks)


def s4_composition(seed=1, ticks=120):
    """S4 知识组合：Ep1 学 oak_log→planks；Ep2 学 planks→stick；
    Ep3 任务 stick（两步组合可达）观察是否通过组合复用（经 KG/扩散）
    而非从零重新科学。三阶段同一认知栈（记忆延续）。"""
    rec = er.RunRecorder(family="sandbox", label="S4-知识组合", seed=seed)
    rec.start({"tick_s": 16.0, "ticks": ticks})
    parent = os.path.join(rec.dir, "appdata")
    st = build_stack(goal="oak_planks", label="S4", seed=seed,
                     base_parent=parent)
    _setup_standard_world(st)
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    clk = Clock()

    def _phase(goal, label, max_t):
        for i in range(max_t):
            tick(st, clk)
            if goal_done(st, goal):
                rec.log("RESULT", f"{label} 达成 @tick{i} ({clk.now})",
                        tick=i)
                return i
        rec.log("RESULT", f"{label} 未达成（{max_t} tick）", ticks=max_t)
        return None

    t1 = _phase("oak_planks", "Ep1-planks", ticks // 3)
    t2 = None
    if t1 is not None:
        # Ep2 目标必须显式注入（否则达成后系统无目标，纯探索）
        st.loop.add_goal({"type": "obtain", "target": "oak_fence",
                          "source": "experiment_obtain",
                          "text": "实验目标：组合 oak_fence(planks+stick)"})
        t2 = _phase("oak_fence", "Ep2-oak_fence(planks+stick 组合)",
                    ticks // 3)
    t3 = None
    if t2 is not None:
        # Ep3：清理掉全部 planks 材料，重新要求同产物 → 观察是否走组合复用
        st.world.set_inv()   # 清空背包：必须重新收集
        st.loop.add_goal({"type": "obtain", "target": "oak_fence",
                          "source": "experiment_obtain",
                          "text": "实验目标：再次获得 oak_fence"})
        # 种新树（远处）——环境不变，只有材料要重新收集
        st.world.put_tree(14, 0, "oak_log")
        t3 = _phase("oak_fence", "Ep3-复用(清空重来)", ticks // 3)
    m = {"t1_planks": t1, "t2_fence": t2, "t3_reuse": t3,
         "graph": graph_stats(st), "inv": dict(st.world.inv_map()),
         "calls": st.world.calls[:30]}
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.log("RESULT", "S4 结束", **m)
    rec.finalize(ok=True, extra=m)
    print(f"[S4] Ep1={t1} Ep2={t2} Ep3={t3} graph={m['graph']}")
    return m, st


SCENARIOS["s4"] = s4_composition


def s7_env_change(seed=1, ticks=150):
    """S7 环境变化：先学 oak_log→planks（树 A）并达成。@tick60 环境突变：
    树 A 连根移除，改种 birch_log（配方表无 birch，不可制板）。@tick62
    清空背包并重新注入同目标——已学知识链（oak→planks）失效后，观察
    失效检测/放弃旧链/描边家具等新行为（adaptation 观察窗）。"""
    rec = er.RunRecorder(family="sandbox", label="S7-环境变化", seed=seed)
    rec.start({"tick_s": 16.0, "ticks": ticks})
    parent = os.path.join(rec.dir, "appdata")
    st = build_stack(goal="oak_planks", label="S7", seed=seed,
                     base_parent=parent)
    _setup_standard_world(st)
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    clk = Clock()
    phase = {"p": 0}

    def _env_change(st):
        # @tick60：oak 树连根移除，改种 birch_log（不可制板）
        for key in [k for k in st.world.blocks
                    if st.world.blocks[k] == "oak_log"]:
            st.world.blocks.pop(key)
        st.world.put_tree(14, 0, "birch_log")
        st.world.put_block(14, 65, 0, "birch_log")
        st.world._refresh_near()
        phase["p"] = 1

    def _re_arm_goal(st):
        # @tick62：清空背包 + 重新注入同目标 → 旧链失效后必须重新找路
        st.world.set_inv()
        for g in list(st.loop._goals):
            if str(g.get("type")) == "obtain":
                st.loop._goals.remove(g)
        st.loop.add_goal({"type": "obtain", "target": "oak_planks",
                          "source": "experiment_obtain",
                          "text": "环境变化后重新获得 oak_planks"})
        phase["p"] = 2

    env_steps = [(60, "remove_oak_plant_birch", _env_change),
                 (62, "rearm_oak_planks_goal", _re_arm_goal)]
    done_ix = None
    done2_ix = None
    for i in range(ticks):
        for at, name, fn in env_steps:
            if i == at:
                fn(st)
                rec.log("ENVCHANGE", f"@tick{i} {name}")
        out = tick(st, clk)
        if done_ix is None and goal_done(st, "oak_planks"):
            done_ix = i
        # phase2 达成（注意：phase1 达成算 done_ix；重注入后其背包已清空）
        if phase["p"] == 2 and goal_done(st, "oak_planks"):
            done2_ix = i
    m = {"env_change_tick": 60, "success": done_ix is not None,
         "success_tick": done_ix, "phase2_success": done2_ix is not None,
         "phase2_tick": done2_ix, "graph": graph_stats(st),
         "inv": dict(st.world.inv_map()),
         "birch_seen": any(b["name"] == "birch_log" for b in
                           st.world.near_names)}
    rec.log("RESULT", "S7 结束", **m)
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra=m)
    print(f"[S7] env_change tick60 s1={done_ix} "
          f"phase2_success={m['phase2_success']}@{done2_ix} "
          f"graph={m['graph']}")
    return m, st


SCENARIOS["s7"] = s7_env_change


def s8_multi_goal(seed=1, ticks=120):
    """S8 多目标竞争：双目标 obtain oak_planks（近树14）+ obtain stone_pickaxe
    （远石20，需表+合成链）。观察注意/资源分配/目标选择——不预设优先级。"""
    rec = er.RunRecorder(family="sandbox", label="S8-多目标", seed=seed)
    rec.start({"tick_s": 16.0, "ticks": ticks})
    parent = os.path.join(rec.dir, "appdata")
    st = build_stack(goal=None, label="S8", seed=seed, base_parent=parent)
    _setup_standard_world(st)
    st.world.put_tree(44, 0, "oak_log")    # 远树：两目标在空间上分离
    st.loop.add_goal({"type": "obtain", "target": "oak_planks",
                      "source": "experiment_obtain"})
    st.loop.add_goal({"type": "obtain", "target": "stone_pickaxe",
                      "source": "experiment_obtain"})
    rec.log("GOAL", "注入双目标 obtain:oak_planks + obtain:stone_pickaxe")
    clk = Clock()
    order = []
    for i in range(ticks):
        out = tick(st, clk)
        for tgt in ("oak_planks", "stone_pickaxe"):
            if tgt not in order and goal_done(st, tgt):
                order.append(tgt)
                rec.log("RESULT", f"@{i} 背包实得 {tgt}", tick=i)
    m = {"completion_order": order, "graph": graph_stats(st),
         "inv": dict(st.world.inv_map()),
         "goal_states": goal_state(st)}
    rec.log("RESULT", "S8 结束", **m)
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra=m)
    print(f"[S8] 完成序={order} graph={m['graph']}")
    return m, st


SCENARIOS["s8"] = s8_multi_goal


def s9_interrupt_resume(seed=1, ticks=140):
    """S9 中断恢复：目标 A（crafting_table，近距离优先）→ 中段注入高紧迫目标
    B（obtain iron_ore 深矿）→ 观察 B 是否抢占、A 是否保持/恢复。"""
    rec = er.RunRecorder(family="sandbox", label="S9-中断恢复", seed=seed)
    rec.start({"tick_s": 16.0, "ticks": ticks})
    parent = os.path.join(rec.dir, "appdata")
    st = build_stack(goal="oak_planks", label="S9", seed=seed,
                     base_parent=parent)
    _setup_standard_world(st)
    st.world.put_ore(40, 3, "iron_ore", 2)   # 远矿
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    clk = Clock()
    interrupted = {"at": None}

    def _add_b(st):
        # 沙箱世界 iron_ore 掉落 raw_iron（BLOCK_META），目标名必须与
        # 背包实物一致（2026-09-28：原写 iron_ore 造成目标永不可达）
        st.loop.add_goal({"type": "obtain", "target": "raw_iron",
                          "source": "experiment_obtain",
                          "text": "中断目标：获得原铁"})
        interrupted["at"] = int(clk.now)

    env_steps = [(45, "add_goal_iron_ore", _add_b)]
    order = []
    for i in range(ticks):
        for at, name, fn in env_steps:
            if i == at:
                fn(st)
                rec.log("ENVCHANGE", f"@tick{i} {name}")
        tick(st, clk)
        for tgt in ("oak_planks", "raw_iron"):
            if tgt not in order and goal_done(st, tgt):
                order.append(tgt)
                rec.log("RESULT", f"@{i} 背包实得 {tgt}", tick=i)
    m = {"completion_order": order, "interrupt_at_tick": 45,
         "goal_states": goal_state(st), "graph": graph_stats(st)}
    rec.log("RESULT", "S9 结束", **m)
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra=m)
    print(f"[S9] 完成序={order} 中断@45 goals={m['goal_states']}")
    return m, st


SCENARIOS["s9"] = s9_interrupt_resume


def s11_stale_knowledge(seed=1, ticks=140):
    """S11 过时知识：Phase1 学 oak_log→planks（成功多次）。
    Phase2 环境突变：世界不再有 oak_log（树全部移除且不再刷新），但保留
    配方先验。观察旧知识权重、失效检测、行为变化（是否放弃 oak 链）。"""
    rec = er.RunRecorder(family="sandbox", label="S11-过时知识", seed=seed)
    rec.start({"tick_s": 16.0, "ticks": ticks})
    parent = os.path.join(rec.dir, "appdata")
    st = build_stack(goal="oak_planks", label="S11", seed=seed,
                     base_parent=parent)
    _setup_standard_world(st)
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    clk = Clock()

    def _remove_all_oak(st):
        for k in [k for k in st.world.blocks
                  if st.world.blocks[k] in ("oak_log",)]:
            st.world.blocks.pop(k)
        st.world._refresh_near()

    def _re_arm_goal(st):
        # @tick72：清空背包 + 重新注入同目标 → 旧知识（oak 链）失效后
        # 必须面对"世界上不再有 oak_log"，观察是否放弃旧链/寻找替代
        st.world.set_inv()
        for g in list(st.loop._goals):
            if str(g.get("type")) == "obtain":
                st.loop._goals.remove(g)
        st.loop.add_goal({"type": "obtain", "target": "oak_planks",
                          "source": "experiment_obtain",
                          "text": "世界不再有 oak，重新获得 oak_planks"})

    env_steps = [(70, "remove_all_oak", _remove_all_oak),
                 (72, "rearm_goal_after_oak_gone", _re_arm_goal)]
    succeeded_before = {}
    rearmed = {"at": None}
    phase2_ix = None
    for i in range(ticks):
        for at, name, fn in env_steps:
            if i == at:
                fn(st)
                if name.endswith("rearm_goal_after_oak_gone"):
                    rearmed["at"] = i
                rec.log("ENVCHANGE", f"@tick{i} {name}")
        tick(st, clk)
        if "oak_planks" not in succeeded_before and goal_done(st, "oak_planks"):
            succeeded_before["oak_planks"] = i
        # phase2：仅 rearm 之后、且背包已清零再涨（变化前达成不计数）
        if rearmed["at"] is not None and phase2_ix is None \
                and i >= rearmed["at"] and goal_done(st, "oak_planks"):
            phase2_ix = i
    m = {"success_tick": succeeded_before.get("oak_planks"),
         "env_change_tick": 70, "phase2_success": phase2_ix is not None,
         "phase2_tick": phase2_ix, "graph": graph_stats(st),
         "inv": dict(st.world.inv_map()),
         "goal_states": goal_state(st)}
    rec.log("RESULT", "S11 结束", **m)
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra=m)
    print(f"[S11] 成功@s1={succeeded_before.get('oak_planks')} "
          f"phase2={phase2_ix} env_change@70 graph={m['graph']}")
    return m, st


SCENARIOS["s11"] = s11_stale_knowledge


def s1_diffusion_association(seed=1, ticks=60):
    """S1 扩散联想：无目标无任务，种子激活经扩散在图上传播。
    测：种子节点激活后，远端关联节点（1-2 跳）激活是否抬升/衰减。"""
    rec = er.RunRecorder(family="sandbox", label="S1-扩散联想", seed=seed)
    rec.start({"tick_s": 16.0, "ticks": ticks})
    parent = os.path.join(rec.dir, "appdata")
    st = build_stack(goal=None, label="S1", seed=seed, base_parent=parent,
                     prior_on=False)
    _setup_standard_world(st)
    clk = Clock()
    # 激活一个种子节点（模拟见到 oak_log），跟踪 3 跳内衰减
    seed_pid = "oak_log"
    n = st.kg.get_node(seed_pid)
    if n is None:
        print("[S1] 种子节点不存在（图谱未含 oak_log）", file=sys.stderr)
        m = {"seed_found": False, "ticks": ticks}
        rec.finalize(ok=True, extra=m)
        return m, st
    n.activation = 1.0
    n.touch()
    trace = []
    for i in range(ticks):
        out = tick(st, clk)
        act = {pid: round(float(nd.activation or 0), 4)
               for pid, nd in st.kg.nodes.items()
               if float(nd.activation or 0) > 0.1}
        if i % 10 == 0:
            trace.append({"tick": i, "active": len(act),
                          "top": sorted(act.items(), key=lambda kv: -kv[1])[:5]})
            rec.log("LEARNING", f"tick{i} 激活节点 {len(act)}",
                    top=str(trace[-1]["top"]))
    m = {"seed": seed_pid, "seed_found": True, "ticks": ticks,
         "trace": trace, "graph": graph_stats(st)}
    rec.log("RESULT", "S1 结束", active_traces=len(trace))
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra=m)
    print(f"[S1] 扩散联想: 种子 {seed_pid} 传播 {len(trace)} 采样 "
          f"graph={m['graph']}")
    return m, st


SCENARIOS["s1"] = s1_diffusion_association


def s2_diffusion_ablation(seed=1, ticks=80):
    """S2 扩散消融对照（−Diffusion）：同 S1 种子激活，关闭扩散
    （beta_spread=0）。测：无扩散时激活是否只在种子局部。"""
    rec = er.RunRecorder(family="sandbox", label="S2-扩散消融", seed=seed)
    rec.start({"tick_s": 16.0, "ticks": ticks})
    parent = os.path.join(rec.dir, "appdata")
    st = build_stack(goal=None, label="S2", seed=seed, base_parent=parent,
                     prior_on=False, diffusion_on=False)
    _setup_standard_world(st)
    clk = Clock()
    seed_pid = "oak_log"
    n = st.kg.get_node(seed_pid)
    if n is None:
        print("[S2] 种子节点不存在", file=sys.stderr)
        m = {"seed_found": False}
        rec.finalize(ok=True, extra=m)
        return m, st
    n.activation = 1.0
    n.touch()
    trace = []
    for i in range(ticks):
        out = tick(st, clk)
        act = {pid: round(float(nd.activation or 0), 4)
               for pid, nd in st.kg.nodes.items()
               if float(nd.activation or 0) > 0.1}
        if i % 10 == 0:
            trace.append({"tick": i, "active": len(act),
                          "top": sorted(act.items(), key=lambda kv: -kv[1])[:5]})
    m = {"seed": seed_pid, "ticks": ticks, "trace": trace,
         "graph": graph_stats(st)}
    rec.log("RESULT", "S2 结束", active_traces=len(trace))
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra=m)
    print(f"[S2] 扩散消融(−β): 种子 {seed_pid} graph={m['graph']}")
    return m, st


SCENARIOS["s2"] = s2_diffusion_ablation


def s5_episodic_causal(seed=1, ticks=100):
    """S5 episodic causal：连续 episode 内做因果链（挖矿→掉落→收集）。
    测：CausalLearner 是否从 timeline 事件中形成工具→掉落物的因果假设并
    提升置信（无 LLM）。
    2026-09-28：单轮工具链 gather 仅 2 次 < MIN_SUPPORT=3，假设不可能形成
    ——原部署样本量不足。改为三轮重复挖石（obtain stone 三连），累积
    gather_resource|stone 同义观测 ≥3，观察假设跨轮形成。"""
    rec = er.RunRecorder(family="sandbox", label="S5-episodic-causal", seed=seed)
    rec.start({"tick_s": 16.0, "ticks": ticks})
    parent = os.path.join(rec.dir, "appdata")
    st = build_stack(goal="cobblestone", label="S5", seed=seed,
                     base_parent=parent)
    _setup_standard_world(st)
    rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
    clk = Clock()

    def _rearm_stone(st):
        st.world.set_inv()   # 挖出的石头全部上缴 → 需再挖（重复观测样本）
        st.loop.add_goal({"type": "obtain", "target": "cobblestone",
                          "source": "experiment_obtain",
                          "text": "重复观测：再次获得 cobblestone"})

    # 三轮重复（tick 25/50/75 各一）。第一轮约 15 tick 达成，之后每轮约
    # 50 tick（第二轮实测 25→81），拉够间距保证三轮采样 ≥MIN_SUPPORT=3。
    env_steps = [(25, "rearm_stone_2nd", _rearm_stone),
                 (50, "rearm_stone_3rd", _rearm_stone),
                 (75, "rearm_stone_4th", _rearm_stone)]
    steps_ix = 0
    done_ixs = []
    armed = {"v": True}    # 达成后 disarm，rearm 时再武装（边沿记账）
    for i in range(ticks):
        while steps_ix < len(env_steps) and i >= env_steps[steps_ix][0]:
            name, fn = env_steps[steps_ix][1], env_steps[steps_ix][2]
            fn(st)
            armed["v"] = True
            rec.log("ENVCHANGE", f"@tick{i} {name}")
            steps_ix += 1
        out = tick(st, clk)
        if armed["v"] and goal_done(st, "cobblestone"):
            done_ixs.append(i)
            armed["v"] = False
            rec.log("RESULT", f"轮 {len(done_ixs)} 达成 @tick{i}", tick=i)
    # 沙箱加速时钟 vs 壁钟窗口：因果窗（90s 量级）挂在动作的壁钟时间上，
    # run 几真实秒内跑完 → 窗永不自然到期。flush_causal 确定性推进全部
    # pending 窗关窗记账（sweep 的公开兜底语义，非篡改账本）。
    n_closed = flush_causal(st)
    rec.log("RESULT", "因果窗关窗", closed=n_closed, tick=i)
    hyps = []
    try:
        # _hypotheses 是 dict：action_sig|outcome → h
        for key, h in (st.causal._hypotheses or {}).items():
            hyps.append({"key": str(key)[:80],
                         "support": int(h.get("support") or 0),
                         "conf": round(float(h.get("confidence") or 0), 3),
                         "status": h.get("status")})
    except Exception:
        pass
    agg = {}
    try:
        for sig, a in (st.causal._aggregations or {}).items():
            agg[sig] = int(a.get("obs") or 0)
    except Exception:
        pass
    m = {"rounds": done_ixs, "hypotheses": hyps[:12],
         "hyp_count": len(hyps), "aggregations": agg,
         "graph": graph_stats(st), "inv": dict(st.world.inv_map())}
    rec.log("RESULT", "S5 结束", hyp_count=len(hyps),
            rounds=len(done_ixs), aggs=len(agg))
    rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
    rec.finalize(ok=True, extra=m)
    print(f"[S5] 轮次={done_ixs} 假设={len(hyps)} 条 "
          f"聚合组={len(agg)} graph={m['graph']}")
    return m, st


SCENARIOS["s5"] = s5_episodic_causal


def s6_continual_learning(seed=1, ticks=60):
    """S6 持续学习（多 episode 累积）：三阶段同世界递进目标，图谱继承。
    oak_planks → stick（需 planks 链）→ 熔炼链预热。测跨 episode 知识
    累积、无灾难性遗忘（早期知识保持可达）。"""
    labels = ["S6-EpA-planks", "S6-EpB-stick", "S6-EpC-pickaxe"]
    goals = ["oak_planks", "stick", "wooden_pickaxe"]
    starts = [None, None, None]
    holder = {"gpath": None}
    results = {}

    def _ep(goal, label, start_inv, gpath):
        rec = er.RunRecorder(family="sandbox", label=label, seed=seed,
                             mode="learning_closed_loop")
        rec.start({"episode": label, "tick_s": 16.0, "ticks": ticks})
        parent = os.path.join(rec.dir, "appdata")
        st = build_stack(goal=goal, label=label, seed=seed,
                         base_parent=parent, inherit_graph=gpath)
        _setup_standard_world(st)
        if start_inv:
            st.world.set_inv(**start_inv)
        rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
        clk = Clock()
        done_ix = None
        for i in range(ticks):
            out = tick(st, clk)
            if done_ix is None and goal_done(st, goal):
                done_ix = i
                rec.log("RESULT", f"目标达成 @tick{i}", tick=i)
        m = {"episode": label, "goal": goal, "success": done_ix is not None,
             "success_tick": done_ix, "graph": graph_stats(st),
             "inv": dict(st.world.inv_map())}
        rec.log("RESULT", "episode 结束", **m)
        rec.finalize(ok=True, extra=m)
        print(f"[S6:{label}] success={m['success']}@{done_ix} "
              f"graph={m['graph']}")
        gp = os.path.join(rec.dir, "snapshots", "graph_ep.json")
        try:
            if not os.path.exists(os.path.dirname(gp)):
                os.makedirs(os.path.dirname(gp))
            with open(gp, "w", encoding="utf-8") as f:
                json.dump(st.kg.to_dict(), f, ensure_ascii=False,
                          default=str)
        except Exception as e:
            print(f"[S6] 图谱落盘失败: {e}", file=sys.stderr)
        return m, st, gp

    for goal, label, inv in zip(goals, labels, starts):
        m, st, gp = _ep(goal, label, inv, holder["gpath"])
        results[label] = m
        holder["gpath"] = gp
    rec = er.RunRecorder(family="sandbox", label="S6-汇总", seed=seed)
    rec.start({"ticks": ticks})
    rec.log("RESULT", "S6 三段完成", **results)
    rec.finalize(ok=True, extra={"episodes": results})
    print(f"[S6] 持续学习三段: "
          + " ".join(f"{k}={v['success']}@{v['success_tick']}"
                     for k, v in results.items()))
    return results, st


SCENARIOS["s6"] = s6_continual_learning


def s10_self_model_ablation(seed=1, ticks=100):
    """S10 Self Model 消融：full（默认）vs −relevant self（self_goal_on=False：
    不走 current_goal 参照）。同目标同世界。测 self-model 相关分量对
    达成的影响。"""
    outs = {}
    for mode, cfg in (("full", {}),
                      ("minus_self", {"self_goal_on": False})):
        rec = er.RunRecorder(family="sandbox", label=f"S10-{mode}", seed=seed,
                             mode="learning_closed_loop")
        rec.start({"tick_s": 16.0, "ticks": ticks, "ablation": mode})
        parent = os.path.join(rec.dir, "appdata")
        st = build_stack(goal="oak_planks", label=f"S10_{mode}", seed=seed,
                         base_parent=parent, **cfg)
        _setup_standard_world(st)
        rec.snapshot("graph_start", graph_dict=st.kg.to_dict())
        clk = Clock()
        done_ix = None
        for i in range(ticks):
            out = tick(st, clk)
            if done_ix is None and goal_done(st, "oak_planks"):
                done_ix = i
                rec.log("RESULT", f"{mode} 目标达成 @tick{i}", tick=i)
        m = {"ablation": mode, "success": done_ix is not None,
             "success_tick": done_ix, "graph": graph_stats(st),
             "inv": dict(st.world.inv_map())}
        rec.log("RESULT", f"S10 {mode} 结束", **m)
        rec.snapshot("graph_end", graph_dict=st.kg.to_dict())
        rec.finalize(ok=True, extra=m)
        outs[mode] = m
        print(f"[S10:{mode}] success={m['success']}@{done_ix} "
              f"graph={m['graph']}")
    print(f"[S10] self-model 消融: full@{outs['full']['success_tick']} "
          f"minus@{outs['minus_self']['success_tick']}")
    return outs, st


SCENARIOS["s10"] = s10_self_model_ablation


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--ticks", type=int, default=120)
    args = ap.parse_args()
    fn = SCENARIOS.get(args.scenario)
    if fn is None:
        print("场景可选:", sorted(SCENARIOS))
        return 2
    m, st = fn(seed=args.seed, ticks=args.ticks)
    print(json.dumps(m, ensure_ascii=False, default=str))


if __name__ == "__main__":
    sys.exit(main())