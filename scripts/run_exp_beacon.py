#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# run_exp_beacon.py — A10 信标任务战役(closed-book,2026-09-30)
# ============================================================================
# 规格: research_audit/_audits/experiment_A10_beacon_task.md(§4 预注册修订)。
# 复用 run_exp_routing_v2.py 的装配(任务/执行器/图/上下文/LLM 决策头),只加
# 一个信标任务 B1 与两个实验装配点(全部属实验 harness,非生产):
#   1. craft 菜单门控: 工艺书对未知配方不可见 -- craft_X 仅当 物品:X 出现在
#      本轮上下文 selected node ids 才列菜单(baseline 无图上下文 → 恒不可见)
#   2. seed_goal=False: 目标实体不进激活种子(v2 各任务该行为在信标中会杀死
#      信息不对称:goal 可由宣告直接注入,无需"回忆起配方")
# Facts 全条件同一且配方剥离。条件: fas_full / D-noact / D-flat / llm_direct
# × seeds 0..19(temp=0,高复制率如实声明)。主对比 3 组 × 2 指标 = 6 检验,
# Bonferroni α=0.0083;success 用配对 McNemar, decisions 用配对 Wilcoxon。
# 运行: python scripts/run_exp_beacon.py --out experiments/beacon_v1
#       python scripts/run_exp_beacon.py --preflight   (零 LLM 装配自检)
# 分析: python scripts/analyze_beacon.py --out experiments/beacon_v1
# ============================================================================

import argparse
import json
import os
import re
import sys
import time

# 导入 v2 装配模块(其 __main__ 守卫保证 import 安全;os.chdir 至 ROOT 属其既有行为)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

import run_exp_routing_v2 as v2  # noqa: E402
from sandbox_lab import install_fake_bridge  # noqa: E402  (装配修订 (d)1)

GOAL_VOCAB = v2.GOAL_VOCAB
GOAL_VOCAB.update({"oak", "log", "planks", "craft", "recipe", "produce",
                   "memory", "remember", "unknown"})


# ── B1 信标任务:配方知识只存在于图(记忆),facts 全条件同一且剥离 ──────
class B1Beacon(v2.Task):
    name = "B1_beacon_closed_book"
    goal_text = "Obtain 1 oak_planks in inventory."
    facts = ("You do not know the recipe for oak_planks, and nothing in the "
             "current observation explains how it is made. Recipes you have "
             "learned are remembered in the knowledge graph (物品 nodes with "
             "产出 edges).")
    gatherable = ("oak_log",)
    goal_entities = ("oak_log", "oak_planks")

    def phases(self):
        return 1

    def setup_phase(self, w, ex, phase):
        w.__init__()
        w.pos = {"x": 0.0, "y": 64.0, "z": 0.0}
        ex.hunger = 20
        for i in range(3):
            for j in range(3):
                w.put_block(3 + i - 1, 65 + j, 0, "oak_log")
        w.put_block(3, 65, 0, "oak_log")
        w._refresh_near()

    def done(self, w):
        return w.inv_map().get("oak_planks", 0) >= 1

    def max_decisions(self, phase):
        return 8


# ── 工艺书门控执行器:未知配方的 craft 项不进菜单 ──────────────────────
class BeaconExecutor(v2.Executor):
    def __init__(self, world, gate=None):
        super().__init__(world)
        self.gate = gate or set()

    def menu(self, task):
        items = v2.Executor.menu(self, task)
        locked = 0
        out = []
        for act, obj in items:
            if act.startswith("craft_") and obj not in self.gate:
                locked += 1
                continue
            out.append((act, obj))
        return out, locked


# ── seed_goal=False:goal 实体不进激活种子(防泄漏订正,见预注册 §4) ────
class BeaconFASContext(v2.FASContext):
    def __init__(self, world, use_diffusion=True, use_demand=True,
                 use_flat=False):
        """装配修订 (d)1+(d)2(2026-09-30 预检阶段锁定,零 LLM):
        1. install_fake_bridge：配方先验经生产建图路径(ensure_mc_world +
           build_recipe_closure)用沙箱世界自身配方表生成——v2 从未接线,
           其图里配方闭包为零(史实已记入规格 §4.1(a))；
        2. 检索通道：物品:{ing} -相关-> 物品:{result}——生产 8 白名单双向
           关系、S7 同族 treatment;产生/需要 方向语义保持不变(forward)。
        """
        install_fake_bridge(world)
        super().__init__(world, use_diffusion=use_diffusion,
                         use_demand=use_demand, use_flat=use_flat)
        try:
            for out, recs in (world.recipe_table or {}).items():
                for r in recs or []:
                    rn = self.node_id(str(r.get("result") or out).lower())
                    for mat in (r.get("ingredients") or {}):
                        mn = self.node_id(str(mat).lower())
                        if mn in self.kg.nodes and rn in self.kg.nodes:
                            self._edge(mn, rn, rel="相关")
        except Exception as e:
            raise v2.CoreModuleError(
                f"beacon association seeding failed: {e!r}") from e
        self._sync_index()

    def entities_of(self, text):
        """修订 (d)3(2026-09-30 预检发现,预数据): 观测词表解析。

        环境观测用 'oak_logx9' 计数后缀格式,原 tokenizer([a-zA-Z_]+)把
        整个 'oak_logx9' 当一个 token,物品名永远落不进 GOAL_VOCAB——
        v2 战役中该通道失效被 goal 实体直注入掩盖(t1 物品边全靠 goal)。
        信标 seed_goal=False 后此通道必须真实可用: 拆掉数字后缀再匹配。
        """
        found = set(v2.FASContext.entities_of(self, text))
        import re as _re
        for tok in _re.findall(r"[a-zA-Z_]+", str(text)):
            # 'oak_logx9' → token 'oak_logx'(数字被截) → 'oak_log'(计数后缀
            # xN);tokenizer 把 x 留在 token 尾,一并剥掉
            base = _re.sub(r"[xX]+\d*$", "", tok).rstrip("_")
            if not base:
                continue
            if base in GOAL_VOCAB and base not in v2.GENERIC_TOKENS:
                found.add(base)
            elif base in self.kg.nodes or ("物品:" + base) in self.kg.nodes:
                found.add(base)
        return sorted(found)

    def ingest(self, obs_text, action, result, goal_entities):
        """同 v2,仅跳过 goal 实体注入激活(节点/边照常)。"""
        try:
            ents = [self.node_id(e) for e in self.entities_of(obs_text)]
            an = self._node("动作:" + str(action), label="procedural") \
                if action else None
            rn = self._node("结果:" + str(result)[:24]) if result else None
            for i in range(len(ents)):
                for j in range(i + 1, len(ents)):
                    self._edge(ents[i], ents[j])
            if an:
                for nid in ents:
                    self._edge(an, nid)
                    self._edge(nid, an)
            if an and rn:
                self._edge(an, rn)
            self._sync_index()
            seeds = [n for n in ents if n in self.eng.name_to_node]
            if seeds:
                self.eng.activate_from_inputs(seeds, [],
                                              source_type="external_input")
        except v2.CoreModuleError:
            raise
        except Exception as e:
            raise v2.CoreModuleError(f"graph ingest failed: {e!r}") from e


TASKS_B = {"B1": B1Beacon()}
COND_MODE = {"fas_full": "full", "D-noact": "no-diffusion",
             "D-flat": "flat"}
CONDITIONS = ("fas_full", "D-noact", "D-flat", "llm_direct")


# ── B1 专用装配自检(零 LLM):配方链必须能由扩散点亮 ──────────────────
def beacon_preflight(world):
    rep = v2.preflight(world)
    install_fake_bridge(world)  # 修订 (d)1: 生产建图路径的配方先验
    fc = BeaconFASContext(world)
    seeds = [n for n in ("物品:oak_log", "oak_log", "实体:oak_log")
             if n in fc.kg.nodes]
    if not seeds:
        raise v2.InvariantViolation("beacon: no oak_log seed node in graph")
    fc.eng.activate_from_inputs([seeds[0]], [], source_type="external_input")
    for _ in range(4):
        fc.eng.diffuse_step()
    lit = {n for n in fc.kg.nodes
           if float(getattr(fc.kg.nodes[n], "activation", 0) or 0) > 0.001}
    rep["beacon_probe"] = {
        "seed": seeds[0],
        "oak_log_lit": bool(lit & set(seeds)),
        "oak_planks_lit": ("物品:oak_planks" in lit),
        "lit_sample": sorted(lit)[:12],
        "has_produce_edge": any(
            getattr(e, "relation", "") == "产出"
            or getattr(e, "type", "") == "产出"
            for e in fc.kg.edges),
    }
    if not rep["beacon_probe"]["oak_planks_lit"]:
        raise v2.InvariantViolation(
            "beacon preflight: diffusion did not light 物品:oak_planks "
            "(配方链不可达 → 装配无效,按预注册退出)")
    return rep


# ── 主循环(镜像 v2.run_task,加门控接线与 B1 指标)────────────────────
def run_task(cond, seed, llm, log, fail_fast=True):
    task = TASKS_B["B1"]
    is_baseline = cond == "llm_direct"
    mode = ("baseline-direct") if is_baseline else COND_MODE[cond]
    world = v2.SandboxWorld()
    ex = BeaconExecutor(world, gate=set())
    t_start = time.time()
    fc = None
    store = None
    if not is_baseline:
        fc = BeaconFASContext(world,
                              use_diffusion=(mode != "no-diffusion"),
                              use_demand=True,
                              use_flat=(mode == "flat"))
    else:
        store = v2.MemoryStore()
    trace = []
    tok0 = (llm.prompt_tokens, llm.completion_tokens, llm.calls)
    done = False
    for phase in range(1, task.phases() + 1):
        task.setup_phase(world, ex, phase)
        for step in range(task.max_decisions(phase)):
            near = ", ".join(f"{b['name']}x{b['count']}"
                             for b in world.near_names)
            inv = json.dumps(world.inv_map(), ensure_ascii=False)
            obs_line = f"near: {near or '(none)'}; inventory: {inv}; hunger: {ex.hunger}"
            if is_baseline:
                store.add(f"obs: {obs_line} | action: "
                          f"{trace[-1]['action'] if trace else '(start)'} | "
                          f"result: {trace[-1]['detail'] if trace else 'init'}")
                ctx_text = "No additional memory or context."
                tel = {"graph_nodes": None, "graph_edges": None,
                       "activated_nodes": None, "activated_edges": None}
                selected, demand, gap, routing, edges = [], None, None, None, []
            else:
                fc.ingest(obs_line, trace[-1]["action"] if trace else None,
                          trace[-1]["detail"] if trace else "init",
                          (),  # goal 实体不进种子(seed_goal=False)
                          )
                fc.step_dynamics()
                tel = fc.telemetry()
                if mode == "flat":
                    selected = fc.flat_focus(task.phase_goal(phase), k=8)
                    edges = []
                else:
                    selected = fc.focus(k=8)
                    ids = {n["id"] for n in selected}
                    edges = [f"{e.src} -{e.relation}-> {e.dst}"
                             for e in fc.kg.edges
                             if e.src in ids and e.dst in ids][:8]
                demand, gap, routing, _m = fc.demand_block(
                    task.phase_goal(phase))
                payload, ctx_text = v2.serialize_context(
                    cond, mode, selected, demand, gap, routing,
                    memory_context=None, edges=edges,
                    activation_summary={"n_selected": len(selected), **tel})
            if not selected and not is_baseline:
                raise v2.InvariantViolation(
                    f"{cond} B1 phase{phase} step{step}: empty context")

            # ── 工艺书门控: 物品:X 在上下文才解锁 craft_X ──
            ctx_ids = {n["id"] for n in selected}
            ex.gate = {nid[len("物品:"):] for nid in ctx_ids
                       if nid.startswith("物品:")}
            menu, locked = ex.menu(task)
            menu_text = "\n".join(f"{i}. {a}" for i, (a, _) in enumerate(menu))

            prompt = v2.BASE_PROMPT.format(goal=task.phase_goal(phase),
                                           facts=task.facts,
                                           state=obs_line, context=ctx_text,
                                           menu=menu_text)
            reply = llm.decide(prompt)
            idx = -1
            m = re.search(r'"action"\s*:\s*(\d+)', reply)
            if m and int(m.group(1)) < len(menu):
                idx = int(m.group(1))
            if idx < 0:
                low = reply.lower()
                for i, (a, _) in enumerate(menu):
                    key = a.split("_", 1)[-1] if a.startswith("gather_") else a
                    if a.lower() in low or key in low:
                        idx = i
                        break
            if idx < 0:
                act, obj, ok, detail = "wait", None, True, "unparseable_reply"
            else:
                act, obj = menu[idx]
                ok, detail = ex.execute(act, obj)
            trace.append({
                "task": "B1", "phase": phase, "step": step, "seed": seed,
                "condition": cond, "mode": mode, "obs": obs_line,
                "action": act, "ok": ok, "detail": str(detail)[:60],
                "selected_nodes": [n["id"] for n in selected],
                "graph_nodes": tel["graph_nodes"],
                "graph_edges": tel["graph_edges"],
                "activated_nodes": tel["activated_nodes"],
                "activated_edges": tel["activated_edges"],
                "menu_craft_visible": sum(
                    1 for a, _ in menu if a.startswith("craft_")),
                "menu_craft_locked": locked,
            })
            if task.done(world):
                done = True
                break
        if done:
            break
    acts = [t["action"] for t in trace]
    craft_idx = next((i for i, a in enumerate(acts)
                      if a.startswith("craft_")), None)
    result = {
        "task": "B1", "condition": cond, "mode": mode, "seed": seed,
        "success": int(done), "decisions": len(acts),
        "steps_to_craft": (craft_idx + 1) if craft_idx is not None else None,
        "first_action": acts[0] if acts else None, "acts": acts,
        "unparseable": sum(1 for t in trace
                           if t["detail"] == "unparseable_reply"),
        "explores": sum(1 for a in acts if a.startswith("explore_")),
        "craft_menu_visible_steps": sum(
            t["menu_craft_visible"] > 0 for t in trace),
        "prompt_tokens": llm.prompt_tokens - tok0[0],
        "completion_tokens": llm.completion_tokens - tok0[1],
        "llm_calls": llm.calls - tok0[2],
        "latency_s": round(time.time() - t_start, 1),
        "harness_valid": True, "failure_class": "none",
        "failure_reason": "",
        "step0_n_nodes": len(trace[0]["selected_nodes"]) if trace else 0,
        "graph_edges_final": (trace[-1]["graph_edges"]
                              if trace and trace[-1]["graph_edges"] is not None
                              else None),
    }
    if fail_fast and not is_baseline:
        reason = None
        if (result["graph_edges_final"] or 0) <= 0:
            reason = "graph_edges == 0 at end"
        elif (result["step0_n_nodes"] or 0) <= 0:
            reason = "step-0 context empty"
        if reason:
            result["harness_valid"] = False
            result["failure_class"] = "INVALID_HARNESS_RUN"
            result["failure_reason"] = reason
            log.write(json.dumps({"type": "task_result", **result},
                                 ensure_ascii=False) + "\n")
            log.flush()
            raise v2.InvariantViolation(f"{cond} B1 seed{seed}: {reason}")
    log.write(json.dumps({"type": "task_result", **result},
                         ensure_ascii=False) + "\n")
    for t in trace:
        log.write(json.dumps({"type": "decision", **t},
                             ensure_ascii=False) + "\n")
    log.flush()
    return result


def main():
    ap = argparse.ArgumentParser(description="A10 信标战役 (closed-book)")
    ap.add_argument("--out", default="experiments/beacon_v1")
    ap.add_argument("--seeds", default="0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19")
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    ap.add_argument("--preflight", action="store_true")
    ap.add_argument("--no-limit", action="store_true")
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    conds = [c for c in args.conditions.split(",") if c]
    os.makedirs(args.out, exist_ok=True)

    # 装配修订 (h): 同 out 目录单实例锁 —— 2026-09-30 双进程交错写同一
    # raw_results.jsonl 污染战役(preflight/一启/二启遗留孤儿进程),加独占锁
    # 防复发(进程退出自动释放,锁文件无残留困扰)。
    import msvcrt
    _lock_path = os.path.join(args.out, ".campaign.lock")
    _lockf = open(_lock_path, "a+")
    try:
        msvcrt.locking(_lockf.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print(f"[beacon-lock] {args.out} 已被另一战役进程占用,拒绝启动",
              flush=True)
        _lockf.close()
        return 1

    w0 = v2.SandboxWorld()
    rep = beacon_preflight(w0)
    print("[beacon-preflight] " + json.dumps(rep, ensure_ascii=False),
          flush=True)
    with open(os.path.join(args.out, "preflight.json"), "w",
              encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    if args.preflight:
        print("preflight only (zero LLM)")
        return 0

    log = open(os.path.join(args.out, "raw_results.jsonl"), "w",
               encoding="utf-8")
    rows = []
    for seed in seeds:
        for cond in conds:
            llm = v2.LLMHead()
            llm.max_tokens = 200  # 装配修订 (g): 40 预算被 reasoning 吃光
            try:
                m = run_task(cond, seed, llm, log)
            except v2.InvariantViolation as e:
                m = {"condition": cond, "task": "B1", "seed": seed,
                     "success": 0, "harness_valid": False,
                     "failure_class": "INVALID_HARNESS_RUN",
                     "failure_reason": str(e)[:200]}
                log.write(json.dumps({"type": "task_result", **m},
                                     ensure_ascii=False) + "\n")
                log.flush()
            except v2.InfrastructureError as e:
                m = {"condition": cond, "task": "B1", "seed": seed,
                     "success": 0, "harness_valid": True,
                     "failure_class": "INFRASTRUCTURE_FAILURE",
                     "failure_reason": str(e)[:200]}
                log.write(json.dumps({"type": "task_result", **m},
                                     ensure_ascii=False) + "\n")
                log.flush()
            except v2.CoreModuleError as e:
                m = {"condition": cond, "task": "B1", "seed": seed,
                     "success": 0, "harness_valid": False,
                     "failure_class": "INVALID_HARNESS_RUN",
                     "failure_reason": str(e)[:200]}
                log.write(json.dumps({"type": "task_result", **m},
                                     ensure_ascii=False) + "\n")
                log.flush()
            rows.append(m)
            print(f"[beacon] {cond} B1 seed{seed}: succ={m.get('success', 0)} "
                  f"dec={m.get('decisions', '?')} "
                  f"craft_step={m.get('steps_to_craft')} "
                  f"craft_visible={m.get('craft_menu_visible_steps', 0)} "
                  f"exc={m.get('failure_class', 'none')}", flush=True)
            time.sleep(0.2)
    with open(os.path.join(args.out, "summary.csv"), "w", newline="",
              encoding="utf-8") as f:
        import csv as _csv
        wtr = _csv.DictWriter(f, fieldnames=[
            "condition", "mode", "task", "seed", "success", "decisions",
            "steps_to_craft", "first_action", "unparseable", "explores",
            "craft_menu_visible_steps", "prompt_tokens", "completion_tokens",
            "llm_calls", "latency_s", "harness_valid", "failure_class",
            "failure_reason", "step0_n_nodes", "graph_edges_final"])
        wtr.writeheader()
        for m in rows:
            wtr.writerow({k: m.get(k) for k in wtr.fieldnames})
    print(f"done: {len(rows)} runs -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())