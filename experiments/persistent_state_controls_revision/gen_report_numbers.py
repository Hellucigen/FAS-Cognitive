# -*- coding: utf-8 -*-
# gen_report_numbers.py — 从 preflight_report.json 与零 LLM 诊断重生成
# 报告中引用的每一个数字(数字自检:报告叙述不得含手填数值)。
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

rep = json.load(open(os.path.join(HERE, "preflight_report.json"),
                     encoding="utf-8"))
seeds = rep["per_seed"]
out = {}
out["preflight_pass"] = rep["pass"]
out["n_seeds"] = len(seeds)
out["n_failures"] = len(rep["failures"])
by = {}
for f in rep["failures"]:
    key = f.split()[0] + " " + f.split()[1]
    by[key] = by.get(key, 0) + 1
out["failures_by_criterion_condition"] = by
# C3b clean?
out["C3b_failures"] = sum(1 for f in rep["failures"] if " C3b " in f)
# per-seed metric stability (phase-1 deterministic → identical across seeds)
mets = [s["metrics"] for s in seeds]
def stable(c, k):
    return len({m[c][k] for m in mets}) == 1
out["metrics_identical_across_seeds"] = all(
    stable(c, k) for c in ("C1", "C3b", "C4b", "C4c")
    for k in ("present", "slot", "chars", "n_node_lines"))
m0 = mets[0]
out["C1"] = {k: m0["C1"][k] for k in ("present", "slot", "chars",
                                      "n_node_lines", "n_lines")}
out["C3b"] = {k: m0["C3b"][k] for k in ("present", "slot", "fail_lines",
                                        "chars", "n_node_lines")}
out["C4b"] = {k: m0["C4b"][k] for k in ("present", "slot", "chars",
                                        "n_node_lines")}
out["C4c"] = {k: m0["C4c"][k] for k in ("present", "slot", "chars",
                                        "n_node_lines")}
out["C3b_char_ratio_vs_C1"] = round(m0["C3b"]["chars"] / m0["C1"]["chars"], 3)
out["C4b_char_ratio_vs_C1"] = round(m0["C4b"]["chars"] / m0["C1"]["chars"], 3)
out["neg_edges_C4b"] = seeds[0]["neg_edges"]["C4b"]
out["neg_edges_C4c"] = seeds[0]["neg_edges"]["C4c"]
out["facts_eq_C3_all_seeds"] = all(s["facts_eq_C3"] for s in seeds)
out["C4b_C4c_present_diff_all"] = any(s["C4b_C4c_present_diff"] for s in seeds)

# 截断机制诊断(零 LLM,确定性)
import run_revision as X  # noqa: E402
import harness as H  # noqa: E402
st = X.build_stores()
w, ex = X.t3_world()
obs = H.observe(w, ex)
ctx, chosen, seen = X.c4b_select(st["fc_c4"], obs)
ranked = sorted(seen.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
tgt = "结果:tool_missing:stone_pickaxe"
out["C4b_two_hop_set_size"] = len(ranked)
out["C4b_failure_node_rank_1based"] = [i for i, (n, _) in enumerate(ranked)
                                       if n == tgt][0] + 1
out["C4b_failure_discovery_weight"] = seen[tgt][0]
out["C4b_slot8_weight"] = ranked[7][1][0]
out["C4b_top8_weights"] = [round(v, 4) for _, (v, _) in ranked[:8]]
edges_in = [(e.src, e.relation, float(e.weight)) for e in st["fc_c4"].kg.edges
            if e.dst == tgt]
out["edges_into_failure_node"] = edges_in
stc = X.build_stores()
_, _, seenc = X.c4b_select(stc["fc_c4c"], obs)
out["C4c_failure_node_in_two_hop_set"] = tgt in seenc
out["C4c_failure_node_weight"] = seenc.get(tgt, (None,))[0]

# 形式运行状态
raw = os.path.join(HERE, "raw_revision.jsonl")
out["formal_runs_executed"] = (sum(1 for l in open(raw, encoding="utf-8")
                                   if l.strip()) if os.path.exists(raw) else 0)
print(json.dumps(out, ensure_ascii=False, indent=1))
json.dump(out, open(os.path.join(HERE, "report_numbers.json"), "w",
                    encoding="utf-8"), ensure_ascii=False, indent=1)
