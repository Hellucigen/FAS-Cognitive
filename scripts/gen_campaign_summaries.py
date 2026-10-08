# gen_campaign_summaries.py — 收尾 campaign 汇总生成（§22：summary.json +
# failure_analysis.md per family；只读各实验 JSON/manifest，不改数据）
import json
import glob
import os
import collections

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "FAS_Research_Experiments")

FAMILIES = {
    "reuse_a2": {
        "experiment": "A2 写回→复用（升级环境）", "question": "R1",
        "conds": ["B0", "B1", "B2"], "pattern": "a2_{c}_seed*.json",
        "summarize": lambda d: {
            "success": d["ep2"]["success"],
            "success_tick": d["ep2"]["success_tick"],
            "actions": d["ep2"]["actions_total"],
            "redundant": d["ep2"]["actions_redundant"],
            "shared_hits": d["ep2"]["shared_action_hits"],
            "wb_nodes_inherited": d["ep2"]["inherited_wb_nodes"],
            "ep1_promoted_graph": None},
    },
    "diffusion_b2": {
        "experiment": "B2 扩散因果四条件", "question": "R2",
        "conds": ["full", "nodiff", "rand", "retr"],
        "pattern": "expB2_{c}_seed*.json",
        "summarize": lambda d: {
            "success": d["ep2"]["success"],
            "success_tick": d["ep2"]["success_tick"],
            "final_mass": d["ep2"]["final_mass"],
            "lit_set": len(d["ep2"]["final_lit_ge005"]),
            "lit_ticks": d["ep2"]["lit_ticks"],
            "rank_oak_log": d["ep2"]["final_rank_oak_log"]},
    },
    "emotion": {
        "experiment": "C 情绪/动机调制", "question": "R5",
        "conds": ["E0", "E1_high", "E1_low", "E2_disabled"],
        "pattern": "expCemo_{c}_seed*.json",
        "summarize": lambda d: {
            "success": d["ep2"]["success"],
            "gain": [d["ep2"]["gain_first"], d["ep2"]["gain_last"]],
            "final_mass": d["ep2"]["final_mass"],
            "lit_set": d["ep2"]["final_lit_ge005"],
            "actions": d["ep2"]["actions_total"]},
    },
    "ci": {
        "experiment": "D 持续认知/交流意图", "question": "R6",
        "conds": ["C0", "C1", "C2"], "pattern": "expD_{c}_seed*.json",
        "summarize": lambda d: {
            "ci_created": d["result"]["ci_created"],
            "statuses": d["result"]["ci_statuses_final"],
            "reignitions": len(d["result"].get("reignitions", [])),
            "ambient_act_rises": len(d["result"].get(
                "ambient_act_rises", []))},
    },
    "hebbian": {
        "experiment": "F Hebbian 直接测量", "question": "R8",
        "conds": ["H0", "H1"], "pattern": "expF_{c}_seed*.json",
        "summarize": lambda d: {
            "n_success_rounds": d["result"] and sum(
                1 for e in d["result"]["episodes"] if e["done_tick"]),
            "edges_changed": d["result"]["n_edges_changed"],
            "wsum_first": d["result"]["episodes"][0]["w_sum"],
            "wsum_last": d["result"]["episodes"][-1]["w_sum"],
            "lit_ticks": [e["lit_tick"] for e in d["result"]["episodes"]],
            "craft_ranks": [e["craft_rank"]
                            for e in d["result"]["episodes"]]},
    },
    "envchange": {
        "experiment": "I 环境变化/信念更新", "question": "§15",
        "conds": ["U0", "U1"], "pattern": "expI_{c}_seed*.json",
        "summarize": lambda d: {
            "phase2_success": d["result"]["phase2_success"],
            "phase2_done_tick": d["result"]["phase2_done_tick"],
            "phase2_failures": d["result"]["phase2_failures"]},
    },
    "event_transfer": {
        "experiment": "G 层级事件/跨情境复用", "question": "R9",
        "conds": ["G0", "G1", "G2"], "pattern": "expG_{c}_seed*.json",
        "summarize": lambda d: {
            "success": d["ep2"]["success"],
            "success_tick": d["ep2"]["success_tick"],
            "actions": d["ep2"]["actions_total"],
            "shared_hits": d["ep2"]["shared_action_hits"],
            "ep1_marks_activated": f"{d['ep2']['ep1_mark_activated']}"
                                   f"/{d['ep2']['ep1_mark_nodes']}"},
    },
    "longchain": {
        "experiment": "H 长链因果行动（含 K 非天花板消融读数）",
        "question": "R10/§17",
        "conds": ["L0", "L1", "L2", "L3", "L3b"],
        "pattern": "expH_{c}_seed*.json",
        "summarize": lambda d: {
            "success": d["result"]["success"],
            "success_tick": d["result"]["success_tick"],
            "actions": d["result"]["actions_total"],
            "redundant": d["result"]["actions_redundant"],
            "failed": d["result"]["actions_failed"],
            "writeback_promoted": len(d["result"]["writeback_promoted"])},
    },
    "prior_boundary": {
        "experiment": "J 先验依赖边界（多 episode）", "question": "§16",
        "conds": ["P0", "P1", "P2"], "pattern": "expJ_{c}_seed*.json",
        "summarize": lambda d: {
            "n_success": d["result"]["n_success"],
            "rounds": len(d["result"]["episodes"]),
            "graph_growth": d["result"]["graph_growth"]},
    },
}


def main():
    for fam, spec in FAMILIES.items():
        rows = collections.defaultdict(list)
        for c in spec["conds"]:
            for f in sorted(glob.glob(os.path.join(
                    ROOT, fam, spec["pattern"].format(c=c)))):
                try:
                    d = json.load(open(f, encoding="utf-8"))
                    rows[c].append(spec["summarize"](d))
                except Exception as e:
                    rows[c].append({"ERROR": str(e)})
        summary = {"experiment": spec["experiment"],
                   "research_question": spec["question"],
                   "per_condition": {}}
        for c in spec["conds"]:
            rs = rows[c]
            summary["per_condition"][c] = {
                "n": len(rs), "runs": rs}
        with open(os.path.join(ROOT, fam, "summary.json"), "w",
                  encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)
        # failure_analysis.md（诚实口径：负结果/失败逐类）
        lines = [f"# failure_analysis — {spec['experiment']}", ""]
        for c in spec["conds"]:
            rs = rows[c]
            fails = [r for r in rs if r.get("success") is False
                     or r.get("n_success", 1) == 0
                     or r.get("phase2_success") is False
                     or r.get("ERROR")]
            if fails:
                lines.append(f"## {c}: {len(fails)}/{len(rs)} 未达成/异常")
                for r in fails[:3]:
                    lines.append(f"- {json.dumps(r, ensure_ascii=False)}")
            else:
                lines.append(f"## {c}: {len(rs)}/{len(rs)} 全达成（或无失败"
                             "判据；负结果见 summary.json 与最终报告）")
        lines.append("")
        lines.append("> 分类口径见 FINAL_PAPER_EXPERIMENT_REPORT.md §4；"
                     "失败不删除，raw run_* 与 summary.json 为准。")
        with open(os.path.join(ROOT, fam, "failure_analysis.md"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"[gen] {fam}: summary.json + failure_analysis.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
