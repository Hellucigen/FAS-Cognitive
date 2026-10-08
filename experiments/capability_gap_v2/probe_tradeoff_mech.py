# -*- coding: utf-8 -*-
# probe_tradeoff_mech.py — 权衡实验的零 LLM 机制半区
# 脚本化 P1(planks 成功)→ P2(10 次 gather_iron_ore 失败)→ P3(中性观测),
# 测量 ef(负极性)与 sg(自我目标激活)对 P3 工作集的作用方向。
# 行为半区(LLM)待端点充值后由 run_tradeoff.py 完成。
import json
import os
import sys

ROOT = r"E:\Project\Fascinator"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "capability_gap_v2"))
os.chdir(ROOT)
import run_experiment as R  # noqa: E402
import harness as H  # noqa: E402
import run_exp_routing_v2 as v2  # noqa: E402

CONDS = ["fas", "fas-ef", "fas-sg", "fas-efsg"]


def run(cond):
    w = R.build_world(oak=3, birch=0)
    w.put_block(4, 65, 2, "iron_ore")
    w.put_block(5, 65, 2, "iron_ore")
    w._refresh_near()
    fc = H.CampaignFASContext(w, use_diffusion=True, use_demand=True,
                              exclude_recipes=(), writeback=True)
    fc.ef_context = ("ef" in cond)
    fc.sg_context = ("sg" in cond)
    hist = H.HistoryStore()
    if fc.sg_context:
        from self_graph import bootstrap_self, set_current_goal
        bootstrap_self(fc.kg)
        fc._sg_goal_id = set_current_goal(fc.kg, "实验目标:获得 oak_planks",
                                          source="tradeoff")
    ex = R.fresh_exec(w)
    # P1 脚本成功
    H.script_practice_A(ex, (hist, None), fc)
    # P2 十次失败 gather_iron_ore
    for i in range(10):
        obs = H.observe(w, ex)
        ok, det = ex.execute("gather_iron_ore", "iron_ore")
        if fc.sg_context and getattr(fc, "_sg_goal_id", None) in fc.kg.nodes:
            fc.eng.activate_from_inputs([fc._sg_goal_id], [],
                                        source_type="external_input")
        fc.ingest(obs, "gather_iron_ore", det, ())
        fc.step_dynamics()
    # P3 中性观测 ×3,测工作集
    out = {"cond": cond}
    for i in range(3):
        obs = H.observe(w, ex)
        if fc.sg_context and getattr(fc, "_sg_goal_id", None) in fc.kg.nodes:
            fc.eng.activate_from_inputs([fc._sg_goal_id], [],
                                        source_type="external_input")
        fc.ingest(obs, None if i else "gather_iron_ore",
                  "init" if i else "not_found", ())
        fc.step_dynamics()
    sel = fc.focus(k=8)
    ids = [n["id"] for n in sel]
    acts = {n["id"]: round(float(getattr(fc.kg.nodes[n["id"]], "activation", 0)
                               or 0), 3) for n in sel}
    out["top8"] = ids
    out["planks_in_top8"] = "物品:oak_planks" in ids
    out["iron_in_top8"] = "物品:iron_ore" in ids
    out["gather_iron_in_top8"] = "动作:gather_iron_ore" in ids
    for probe in ("物品:oak_planks", "物品:iron_ore", "动作:gather_iron_ore",
                  "结果:tool_missing:stone_pickaxe"):
        if probe in fc.kg.nodes:
            out["act:" + probe] = round(float(
                getattr(fc.kg.nodes[probe], "activation", 0) or 0), 3)
    neg = [e for e in fc.kg.edges
           if float(getattr(e, "weight", 0) or 0) < 0]
    out["neg_edges"] = len(neg)
    return out


def main():
    rows = [run(c) for c in CONDS]
    p = os.path.join(ROOT, "experiments", "capability_gap_v2",
                     "raw_T_mech.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for r in rows:
        print(json.dumps(r, ensure_ascii=False)[:400])


if __name__ == "__main__":
    main()
