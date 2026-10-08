# -*- coding: utf-8 -*-
# analyze_psa.py — 统计 + FINAL_REPORT(H1-H5 检验 + 成功层级判定)
import json
import os
from collections import defaultdict
from math import comb

from scipy.stats import wilcoxon

HERE = r"E:\Project\Fascinator\experiments\persistent_state_advantage"
rows = [json.loads(l) for l in open(os.path.join(HERE, "raw_results.jsonl"),
                                    encoding="utf-8") if l.strip()]
rows = [r for r in rows if "error" not in r]

TASKS = ("T1", "T2", "T3")


def mcnemar(a, b):
    common = sorted(set(a) & set(b))
    b10 = sum(1 for s in common if a[s] == 1 and b[s] == 0)
    b01 = sum(1 for s in common if a[s] == 0 and b[s] == 1)
    n = b10 + b01
    p = min(1.0, 2 * sum(comb(n, i) for i in range(min(b10, b01) + 1))
            / 2 ** n) if n else 1.0
    return b10, b01, p


def get(c, task, field):
    return {r["seed"]: r[field] for r in rows
            if r["condition"] == c and r["task"] == task}


# ── 汇总 ────────────────────────────────────────────────────────────
summary = defaultdict(dict)
for task in TASKS:
    conds = sorted({r["condition"] for r in rows if r["task"] == task})
    for c in conds:
        rs = [r for r in rows if r["condition"] == c and r["task"] == task]
        s = {"n": len(rs)}
        if task in ("T1", "T2"):
            s["success"] = sum(r["success"] for r in rs)
        if task == "T3":
            s["mean_fails"] = round(sum(r["fail_repeats"] for r in rs)
                                    / max(1, len(rs)), 2)
            s["pivot"] = sum(1 for r in rs if r["first_pivot"] is not None)
        if task == "T1":
            s["hunger_restored"] = sum(1 for r in rs
                                       if (r.get("hunger_final") or 0) >= 14)
        if task == "T2":
            s["tokens_per_run"] = round(sum(
                r["prompt_tokens"] + r["completion_tokens"]
                for r in rs) / max(1, len(rs)))
        summary[task][c] = s

# ── 假设检验 ────────────────────────────────────────────────────────
tests = []
# H1(主):T2 FAS-full > FAS-reset
a = get("FAS-full", "T2", "success")
b = get("FAS-reset", "T2", "success")
b10, b01, p = mcnemar(a, b)
tests.append({"h": "H1 T2 FAS-full>FAS-reset (primary)", "n": len(set(a) & set(b)),
              "wins": b10, "losses": b01, "p": round(p, 6)})
h1_pass = p < 0.05 and b10 > b01
# H2:T2 FAS-full > 各 history 预算
for hb in ("B1-h128", "B1-h512", "B1-h2048"):
    b = get(hb, "T2", "success")
    b10, b01, p = mcnemar(a, b)
    tests.append({"h": "H2 T2 FAS-full>%s" % hb, "n": len(set(a) & set(b)),
                  "wins": b10, "losses": b01, "p": round(p, 6)})
# H3:T2 FAS-full > B0
b = get("B0-direct", "T2", "success")
b10, b01, p = mcnemar(a, b)
tests.append({"h": "H3 T2 FAS-full>B0", "n": len(set(a) & set(b)),
              "wins": b10, "losses": b01, "p": round(p, 6)})
# H5:T3 FAS-full < FAS-reset(失败重复,单侧)
fa = get("FAS-full", "T3", "fail_repeats")
fb = get("FAS-reset", "T3", "fail_repeats")
common = sorted(set(fa) & set(fb))
if len(common) >= 6:
    st, p = wilcoxon([fb[s] for s in common], [fa[s] for s in common],
                     alternative="greater")
    tests.append({"h": "H5 T3 reset_fails>full_fails", "n": len(common),
                  "wilcoxon_p": round(float(p), 6)})
# vs RAG(L4,不要求)
b = get("B2-rag", "T2", "success")
b10, b01, p = mcnemar(a, b)
tests.append({"h": "L4 T2 FAS-full vs B2-rag (not required)",
              "n": len(set(a) & set(b)), "wins": b10, "losses": b01,
              "p": round(p, 6)})

for t in tests:
    print("%-38s %s" % (t["h"], t))

# ── 判定(§26)───────────────────────────────────────────────────────
l3_all_pass = all(t["p"] < 0.05 and t["wins"] > t["losses"]
                  for t in tests if t["h"].startswith("H2"))
if h1_pass and summary["T2"]["B0-direct"]["success"] < \
        summary["T2"]["FAS-full"]["success"]:
    if l3_all_pass:
        verdict = ("FAS demonstrates a measurable behavioral advantage "
                   "attributable to persistent relational state under "
                   "constrained external-context conditions.")
    else:
        verdict = ("FAS demonstrates a conditional behavioral advantage: "
                   "persistent relational state is causally necessary "
                   "(FAS-full > FAS-reset, FAS-full > Direct) and survives "
                   "context removal, but history/RAG baselines that can "
                   "still read the experience stream remain equal or "
                   "stronger under the budgets tested here — retrieval "
                   "remains stronger when external memory is available.")
elif summary["T2"]["FAS-full"]["success"] > 0 or \
        summary["T1"]["FAS-full"].get("hunger_restored", 0) > 0:
    verdict = ("FAS persistent relational state is demonstrably formed and "
               "remains accessible after context removal, but the current "
               "behavioral consumer does not convert this mechanism into a "
               "measurable advantage over conventional baselines.")
else:
    verdict = ("The experiment does not provide evidence that persistent "
               "relational state currently yields a behavioral advantage "
               "over the evaluated baselines.")

# ── FINAL_REPORT ────────────────────────────────────────────────────
rep = ["# FINAL_REPORT.md — Persistent State Advantage 实验\n"]
rep.append("## 直接回答\n```text\n" + verdict + "\n```\n")
rep.append("## 总体结果\n")
rep.append("| Task | Condition | 关键指标 | n |\n|---|---|---|---|")
for task in TASKS:
    for c, s in sorted(summary[task].items()):
        if task == "T3":
            rep.append("| %s | %s | 失败重复=%.1f 转向=%d | %d |" % (
                task, c, s["mean_fails"], s["pivot"], s["n"]))
        elif task == "T1":
            rep.append("| %s | %s | 饥饿恢复=%d/%d | %d |" % (
                task, c, s["hunger_restored"], s["n"], s["n"]))
        else:
            rep.append("| %s | %s | success=%d/%d tokens/run=%s | %d |" % (
                task, c, s["success"], s["n"], s.get("tokens_per_run"),
                s["n"]))
rep.append("\n## 预注册检验(Holm 前的原始 p;主假设 H1 α=0.05)\n")
rep.append("| 假设 | n | wins | losses | p |\n|---|---|---|---|---|")
for t in tests:
    rep.append("| %s | %s | %s | %s | %s |" % (
        t["h"], t.get("n"), t.get("wins", "-"), t.get("losses", "-"),
        t.get("p", t.get("wilcoxon_p"))))
rep.append("\n## 成功层级(§18)\n")
lv1 = h1_pass
rep.append("- L1 FAS-full>FAS-reset: **%s**" % ("PASS" if lv1 else "FAIL"))
l2p = next(t for t in tests if t["h"] == "H3 T2 FAS-full>B0")
rep.append("- L2 FAS-full>B0: %s (p=%s)" % (
    "PASS" if l2p["p"] < 0.05 and l2p["wins"] > l2p["losses"] else "FAIL",
    l2p["p"]))
l3 = [t for t in tests if t["h"].startswith("H2")]
rep.append("- L3 FAS-full>history-limited: %s" % "; ".join(
    "%s p=%s" % (t["h"].replace("H2 T2 FAS-full>", ""), t["p"])
    for t in l3))
l4 = next(t for t in tests if "B2-rag" in t["h"])
rep.append("- L4 FAS-full>RAG: %s (不要求成立)" % (
    "PASS" if l4["p"] < 0.05 and l4["wins"] > l4["losses"] else "FAIL/不成立"))
rep.append("\n(数字与机制探针详见 mechanism_results / raw_results;逐层解释"
           "见 ARCHITECTURE_DIAGNOSTIC 与 DESIGN。)")
open(os.path.join(HERE, "FINAL_REPORT.md"), "w",
     encoding="utf-8").write("\n".join(rep))
json.dump({"summary": {k: dict(v) for k, v in summary.items()},
           "tests": tests, "verdict": verdict},
          open(os.path.join(HERE, "statistics.json"), "w",
               encoding="utf-8"), ensure_ascii=False, indent=1)
print("verdict:", verdict)
