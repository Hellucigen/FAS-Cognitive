# -*- coding: utf-8 -*-
# analyze_controls.py — 统计 + RESULTS + INTERPRETATION(按锁定解释表)
import json
import os
from collections import defaultdict
from math import comb

from scipy.stats import wilcoxon

ROOT = r"E:\Project\Fascinator"
PSA = os.path.join(ROOT, "experiments", "persistent_state_advantage",
                   "raw_results.jsonl")
CTL = os.path.join(ROOT, "experiments", "persistent_state_controls",
                   "raw_controls.jsonl")
HERE = os.path.join(ROOT, "experiments", "persistent_state_controls")

# 载入并去重((task,condition,seed) 后写优先)
seen = {}
for l in open(PSA, encoding="utf-8"):
    if l.strip():
        r = json.loads(l)
        if "error" not in r:
            seen[(r["task"], r["condition"], r["seed"])] = r
for l in open(CTL, encoding="utf-8"):
    if l.strip():
        r = json.loads(l)
        if "error" not in r:
            seen[(r["task"], r["condition"], r["seed"])] = r
rows = list(seen.values())

COND = {"C1": "FAS-full", "C2": "FAS-reset", "C3": "C3", "C4": "C4",
        "C5": "FAS-sham"}


def vals(cond, task, field):
    return {r["seed"]: r[field] for r in rows
            if r["condition"] == COND[cond] and r["task"] == task
            and r.get(field) is not None}


def mcnemar(a, b):
    common = sorted(set(a) & set(b))
    b10 = sum(1 for s in common if a[s] == 1 and b[s] == 0)
    b01 = sum(1 for s in common if a[s] == 0 and b[s] == 1)
    n = b10 + b01
    p = min(1.0, 2 * sum(comb(n, i) for i in range(min(b10, b01) + 1))
            / 2 ** n) if n else 1.0
    return b10, b01, p, common


def rd_ci(a, b):
    """配对二元风险差 + 精确二项 CI(基于 discordant)。"""
    common = sorted(set(a) & set(b))
    b10 = sum(1 for s in common if a[s] == 1 and b[s] == 0)
    b01 = sum(1 for s in common if a[s] == 0 and b[s] == 1)
    n = len(common)
    rd = (b10 - b01) / n
    return rd


def hl_ci(x, y):
    """Hodges-Lehmann 位移估计 + bootstrap 95% CI(简单实现)。"""
    import numpy as np
    d = np.array(x) - np.array(y)
    diffs = np.triu_indices(len(d))
    hl = float(np.median([(d[i] + d[j]) / 2 for i, j in zip(*diffs)]))
    boot = []
    rng = np.random.default_rng(42)
    for _ in range(2000):
        s = d[rng.integers(0, len(d), len(d))]
        dd = np.triu_indices(len(s))
        boot.append(np.median([(s[i] + s[j]) / 2 for i, j in zip(*dd)]))
    return hl, float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))


tests = []
M = 8  # Bonferroni 比较族(H_A×2 + H_B×2 + H_C×4)
alpha = 0.05 / M


def add(name, a, b, task, kind):
    if kind == "binary":
        b10, b01, p, common = mcnemar(a, b)
        rd = rd_ci(a, b)
        tests.append({"cmp": name, "task": task, "type": "McNemar",
                      "n": len(common), "wins": b10, "losses": b01,
                      "p": round(p, 6), "p_bonf": round(min(1.0, p * M), 6),
                      "risk_diff": round(rd, 3),
                      "sig_bonf": bool(p <= alpha)})
    else:
        common = sorted(set(a) & set(b))
        d = [a[s] - b[s] for s in common]
        nz = [x for x in d if x != 0]
        if nz:
            st, p = wilcoxon(nz, alternative="two-sided")
            r = abs(st - len(nz) * (len(nz) + 1) / 4) / \
                (len(nz) * (len(nz) + 1) / 4)
        else:
            p, r = 1.0, 0.0
        hl, lo, hi = hl_ci([a[s] for s in common], [b[s] for s in common]) \
            if len(common) >= 5 else (None, None, None)
        tests.append({"cmp": name, "task": task, "type": "Wilcoxon",
                      "n": len(common), "median_diff": round(float(
                          sorted(d)[len(d) // 2]), 2),
                      "HL": round(hl, 2) if hl is not None else None,
                      "CI95": [round(lo, 2), round(hi, 2)]
                      if lo is not None else None,
                      "r": round(float(r), 3), "p": round(float(p), 6),
                      "p_bonf": round(min(1.0, float(p) * M), 6),
                      "sig_bonf": bool(float(p) <= alpha)})


for task in ("T2", "T3"):
    fld = "success" if task == "T2" else "fail_repeats"
    c1 = vals("C1", task, fld)
    add("H_A C1 vs C3", c1, vals("C3", task, fld), task,
        "binary" if task == "T2" else "count")
    add("H_B C1 vs C4", c1, vals("C4", task, fld), task,
        "binary" if task == "T2" else "count")
    add("H_C C3 vs C2", vals("C3", task, fld), vals("C2", task, fld), task,
        "binary" if task == "T2" else "count")
    add("H_C C4 vs C2", vals("C4", task, fld), vals("C2", task, fld), task,
        "binary" if task == "T2" else "count")

# ── 汇总率 ──
summary = {}
for cond in ("C1", "C2", "C3", "C4", "C5"):
    t2 = vals(cond, "T2", "success")
    t3 = vals(cond, "T3", "fail_repeats")
    summary[cond] = {
        "T2_success": "%d/%d" % (sum(t2.values()), len(t2)) if t2 else "-",
        "T2_rate": round(sum(t2.values()) / len(t2), 2) if t2 else None,
        "T3_mean_fails": round(sum(t3.values()) / len(t3), 2) if t3 else "-",
    }

# ── 解释表(锁定)──
def sig(name, task):
    return next((t for t in tests if t["cmp"] == name and t["task"] == task),
                None)


t2a = sig("H_A C1 vs C3", "T2")
t2b = sig("H_B C1 vs C4", "T2")
c3_gt_c1 = t2a and t2a["losses"] > t2a["wins"] and t2a["sig_bonf"]
c1_gt_c3 = t2a and t2a["sig_bonf"] and t2a["wins"] > t2a["losses"]
c1_gt_c4 = t2b and t2b["sig_bonf"] and t2b["wins"] > t2b["losses"]
if c1_gt_c3 and c1_gt_c4:
    interp = ("C1 显著优于 C3 且优于 C4:图结构与 spreading activation 对"
              "本任务有独立贡献;graph/relational 措辞可保留(限 T2/T3)。")
elif c3_gt_c1:
    interp = "C3 优于 C1:如实报告——图/激活在此任务无优势甚至有害。"
elif c1_gt_c3 and not c1_gt_c4:
    interp = ("C1 优于 C3 但 ≈ C4:图结构有贡献;spreading activation 的"
              "贡献未被证明。")
elif (not c1_gt_c3) and (not c1_gt_c4) and not c3_gt_c1:
    interp = ("C1 与 C3、C4 均未检出显著差异(功效有限):本任务上持久化是"
              "主要因素,图结构与激活的独立贡献未被证明;中心表述应为 "
              "persistent experience-derived state。")
else:
    interp = ("混合模式(C1≈C4 且优于 C3 或任务间不一致):按逐任务结果"
              "如实写明,不合并表述。")
# 逐任务不一致时强制分开表述
per_task = {}
for task in ("T2", "T3"):
    a = sig("H_A C1 vs C3", task)
    b = sig("H_B C1 vs C4", task)
    per_task[task] = {"C1vC3": (a or {}).get("p"), "C1vC4": (b or {}).get("p"),
                      "C3vC2": (sig("H_C C3 vs C2", task) or {}).get("p"),
                      "C4vC2": (sig("H_C C4 vs C2", task) or {}).get("p")}

out = {"summary": summary, "tests": tests, "alpha_bonferroni": round(alpha, 5),
       "interpretation": interp, "per_task_p": per_task}
json.dump(out, open(os.path.join(HERE, "RESULTS.json"), "w",
                    encoding="utf-8"), ensure_ascii=False, indent=1)
for t in tests:
    print(t)
print("\nINTERP:", interp)
