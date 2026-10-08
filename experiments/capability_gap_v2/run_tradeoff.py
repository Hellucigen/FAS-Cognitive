# -*- coding: utf-8 -*-
# run_tradeoff.py — 权衡联合实验(T1/T2):失败抑制(P1-E)与目标激活
# (P1-F consumer 的决策头镜像 fas-sg)在同一任务序列上的成对测量。
# 任务: P1 开卷获得 oak_planks(形成目标+成功)→ P2 obtain raw_iron
# (iron_ore 工具门槛,失败累积)→ P3 中性自由相(闭卷,重拾=planks 达成)。
# 条件: fas / fas-ef(负极性) / fas-sg(自我目标进注意) / fas-efsg / history
# n=10 配对种子。假设(先冻结):
#  H1 ef 降低 P2 fail_gathers(复现 E2)且不损害 P3 重拾;
#  H2 sg 提高 P3 重拾率(若 C 路径已饱和则无差)但增加 P2 失败重复
#     (goal 压力增强);
#  H3 efsg 组合两效应近似可加。
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(HERE))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, HERE)
os.chdir(ROOT)

import run_experiment as R  # noqa: E402
import harness as H  # noqa: E402
import run_exp_routing_v2 as v2  # noqa: E402

CONDS = ["fas", "fas-ef", "fas-sg", "fas-efsg", "history"]
SEEDS = list(range(10))
OPEN_FACTS_T = ("Recipes are known: oak_planks is crafted from 1 oak_log; "
                "stick from 2 oak_planks.")
IRON_FACTS = ("You do not know the full recipe chain to raw_iron; nothing in "
              "the current observation explains it.")


def make_sg_context(cond, world):
    """决策头装配 + 自我目标 consumer 镜像(fas-sg/efsg)。"""
    fc = H.CampaignFASContext(world, use_diffusion=True, use_demand=True,
                              exclude_recipes=(), writeback=True)
    fc.ef_context = ("fas-ef" in cond or cond == "fas-efsg")
    fc.sg_context = (cond in ("fas-sg", "fas-efsg"))
    if fc.sg_context:
        from self_graph import bootstrap_self, set_current_goal
        bootstrap_self(fc.kg)
        fc._sg_goal_id = set_current_goal(fc.kg, "实验目标:获得 oak_planks",
                                          source="tradeoff")
    return fc


def phase(kind, fc, hist, goal, facts, llm, seed, phase_name, max_dec,
          _mock=False,
          success_item=None, world=None, track_fails=False):
    w = world if world is not None else R.build_world()
    ex = R.fresh_exec(w)
    trace = []
    tok0 = (llm.prompt_tokens, llm.completion_tokens)
    fails = 0
    first_pivot = None
    for step in range(max_dec):
        obs = H.observe(w, ex)
        if kind == "fas":
            if fc is not None and getattr(fc, "sg_context", False) \
                    and getattr(fc, "_sg_goal_id", None) in fc.kg.nodes:
                fc.eng.activate_from_inputs([fc._sg_goal_id], [],
                                            source_type="external_input")
            fc.ingest(obs, trace[-1]["action"] if trace else None,
                      trace[-1]["detail"] if trace else "init", ())
            fc.step_dynamics()
            ctx_text, gate, tel, _ = R.build_ctx(kind, fc, hist, goal, step)
        else:
            hist.add(obs, trace[-1]["action"] if trace else None,
                     trace[-1]["detail"] if trace else "init")
            ctx_text = "Past experience stream:\n" + hist.window(None)
            gate = H.gate_from_context(ctx_text)
        _llm = llm
        if getattr(llm, "is_mock", False):
            ex.gate = set(gate or ())
            _menu, _ = ex.menu(type("T", (), {
                "gatherable": ("oak_log", "birch_log", "dirt", "cobblestone"),
                "phase_goal": staticmethod(lambda: goal)})())
            _llm = _BoundMock(_menu, llm)
        act, obj, ok, detail, tb, nmenu, locked = H.run_menu_step(
            ex, goal, facts, ctx_text, _llm, gate)
        if track_fails:
            if act.startswith("gather_") and not ok:
                fails += 1
            if first_pivot is None and (act == "gather_oak_log"
                                        or act.startswith("craft_")):
                first_pivot = step
        trace.append({"step": step, "action": act, "ok": ok,
                      "detail": str(detail)[:60], **tb})
        if success_item and w.inv_map().get(success_item, 0) >= 1:
            break
    return {"phase": phase_name, "decisions": len(trace),
            "success": int(bool(success_item) and
                           w.inv_map().get(success_item, 0) >= 1),
            "fail_gathers": fails, "first_pivot": first_pivot,
            "acts": [t["action"] for t in trace],
            "prompt_tokens": llm.prompt_tokens - tok0[0],
            "completion_tokens": llm.completion_tokens - tok0[1]}


def run(cond, seed, llm):
    t0 = time.time()
    w = R.build_world(oak=3, birch=0)
    w.put_block(4, 65, 2, "iron_ore")
    w.put_block(5, 65, 2, "iron_ore")
    w._refresh_near()
    kind = "history" if cond == "history" else "fas"
    fc = make_sg_context(cond, w) if kind == "fas" else None
    hist = H.HistoryStore()
    # P1 开卷获得 planks(形成目标与成功经历;同一世界延续)
    p1 = phase(kind, fc, hist, "obtain oak_planks", OPEN_FACTS_T, llm, seed,
               "P1_goal", 5, success_item="oak_planks", world=w)
    # P2 换目标 raw_iron(工具门槛失败源),track 失败与转向
    p2 = phase(kind, fc, hist, "obtain raw_iron", IRON_FACTS, llm, seed,
               "P2_fail", 12, world=w, track_fails=True)
    # P3 中性自由相(闭卷):重拾=在无人指令下再拿 planks
    # P3 用新世界(修正 carryover 混淆):重拾=在无库存继承下重建 planks
    w3 = R.build_world(**R.WORLD_SPEC)
    p3 = phase(kind, fc, hist, "You have free choice of actions.",
               R.CLOSED_FACTS, llm, seed, "P3_free", 6,
               success_item="oak_planks", world=w3)
    return {"exp": "T", "condition": cond, "seed": seed,
            "P1": p1, "P2": p2, "P3": p3,
            "latency_s": round(time.time() - t0, 1),
            "harness_valid": True, "failure_class": "none"}


class _BoundMock:
    def __init__(self, menu, parent):
        self.menu = menu
        self.parent = parent

    @property
    def prompt_tokens(self):
        return self.parent.prompt_tokens

    @property
    def completion_tokens(self):
        return self.parent.completion_tokens

    def decide(self, prompt):
        import re as _re
        goal = ""
        m = _re.search(r"Task: (.*)", prompt)
        if m:
            goal = m.group(1)
        for i, (a, obj) in enumerate(self.menu):
            if a.startswith("craft_") and obj and str(obj) in goal:
                return '{"action": %d}' % i
        for i, (a, obj) in enumerate(self.menu):
            if a.startswith("gather_"):
                return '{"action": %d}' % i
        return '{"action": %d}' % (len(self.menu) - 1)


class MockHead:
    """确定性假决策头:仅冒烟验证管线(不计入任何结果)。
    规则:菜单里有 craft_<目标物> 就选之;否则第一个 gather;否则 wait。"""
    is_mock = True
    prompt_tokens = 0
    completion_tokens = 0

    def for_menu(self):
        return self


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="0,1,2,3,4,5,6,7,8,9")
    ap.add_argument("--conditions", default="all")
    ap.add_argument("--mock", action="store_true",
                    help="零 LLM 冒烟(管线验证,不计入结果)")
    ap.add_argument("--out", default="raw_T.jsonl")
    args = ap.parse_args()
    global SEEDS, CONDS
    SEEDS = [int(x) for x in args.seeds.split(",")]
    if args.conditions != "all":
        CONDS = [c for c in args.conditions.split(",") if c]
    lock = open(os.path.join(HERE, args.out + ".lock"), "a+")
    try:
        import msvcrt
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("[lock] occupied")
        return 1
    log = open(os.path.join(HERE, args.out), "a", encoding="utf-8")
    n = 0
    for seed in SEEDS:
        for cond in CONDS:
            llm = MockHead() if args.mock else v2.LLMHead()
            if not args.mock:
                llm.max_tokens = 400
            try:
                m = run(cond, seed, llm)
            except Exception as e:
                m = {"exp": "T", "condition": cond, "seed": seed,
                     "harness_valid": False, "failure_class": "ERROR",
                     "failure_reason": repr(e)[:200]}
            log.write(json.dumps(m, ensure_ascii=False) + "\n")
            log.flush()
            n += 1
            print("[T] %s seed%d P1=%s P2fails=%s pivot=%s P3=%s" % (
                cond, seed, m.get("P1", {}).get("success"),
                m.get("P2", {}).get("fail_gathers"),
                m.get("P2", {}).get("first_pivot"),
                m.get("P3", {}).get("success")), flush=True)
            time.sleep(0.2)
    print("done: %d runs" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
