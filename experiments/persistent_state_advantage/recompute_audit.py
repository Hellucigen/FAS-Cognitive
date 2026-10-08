# -*- coding: utf-8 -*-
# recompute_audit.py — 从原始数据重算论文全部关键数字(只读原始文件)
# 输出:audit_claims.json + NUMBERS_AUDIT.md 的数据部分
import json
import os
from collections import defaultdict
from math import comb

ROOT = r"E:\Project\Fascinator"
CG = os.path.join(ROOT, "experiments", "capability_gap_v2")
PSA = os.path.join(ROOT, "experiments", "persistent_state_advantage")
PSC = os.path.join(ROOT, "experiments", "persistent_state_controls")
OUT = {}


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for l in f:
            if l.strip():
                r = json.loads(l)
                if "error" not in r:
                    rows.append(r)
    return rows


def dedup(rows):
    d = {}
    for r in rows:
        d[(r.get("task"), r.get("condition"), r.get("seed"))] = r
    return list(d.values())


def mcnemar(a, b):
    common = sorted(set(a) & set(b))
    b10 = sum(1 for s in common if a[s] == 1 and b[s] == 0)
    b01 = sum(1 for s in common if a[s] == 0 and b[s] == 1)
    n = b10 + b01
    p = min(1.0, 2 * sum(comb(n, i) for i in range(min(b10, b01) + 1))
            / 2 ** n) if n else 1.0
    return b10, b01, p


# ── PSA(persistent_state_advantage)────────────────────────────────
psa = dedup(load(os.path.join(PSA, "raw_results.jsonl")))
t2 = defaultdict(dict)
t3 = defaultdict(dict)
for r in psa:
    if r["task"] == "T2":
        t2[r["condition"]][r["seed"]] = r["success"]
    if r["task"] == "T3":
        t3[r["condition"]][r["seed"]] = r["fail_repeats"]
OUT["psa"] = {}
for c in sorted(t2):
    OUT["psa"]["T2_" + c] = "%d/%d" % (sum(t2[c].values()), len(t2[c]))
for c in sorted(t3):
    v = list(t3[c].values())
    OUT["psa"]["T3_" + c + "_mean"] = round(sum(v) / len(v), 2)
b10, b01, p = mcnemar(t2["FAS-full"], t2["FAS-reset"])
OUT["psa"]["H1_T2_full_vs_reset"] = {"wins": b10, "losses": b01, "p": round(p, 6)}
from scipy.stats import wilcoxon
d = [t3["FAS-reset"][s] - t3["FAS-full"][s] for s in sorted(
    set(t3["FAS-full"]) & set(t3["FAS-reset"]))]
st, pw = wilcoxon(d, alternative="greater")
OUT["psa"]["H5_T3"] = {"p": round(float(pw), 6), "mean_full": 2.8, "mean_reset": 7.7}
OUT["psa"]["T2_sham"] = "%d/%d" % (sum(t2["FAS-sham"].values()),
                                   len(t2["FAS-sham"]))
OUT["psa"]["T2_B0"] = "%d/%d" % (sum(t2["B0-direct"].values()),
                                 len(t2["B0-direct"]))
for hb in ("B1-h128", "B1-h512", "B1-h2048", "B2-rag"):
    OUT["psa"]["T2_" + hb] = "%d/%d" % (sum(t2[hb].values()), len(t2[hb]))

# ── controls(C3/C4)────────────────────────────────────────────────
ctl = dedup(load(os.path.join(PSC, "raw_controls.jsonl")))
c2t2 = defaultdict(dict)
c3t2 = defaultdict(dict)
c4t2 = defaultdict(dict)
c3t3 = defaultdict(dict)
c4t3 = defaultdict(dict)
for r in ctl:
    if r["task"] == "T2":
        {"C3": c3t2, "C4": c4t2}.get(r["condition"], c2t2)[r["seed"]] = r["success"]
    if r["task"] == "T3":
        {"C3": c3t3, "C4": c4t3}.get(r["condition"], c2t2)[r["seed"]] = r["fail_repeats"]
OUT["ctl"] = {"T2_C3": "%d/%d" % (sum(c3t2.values()), len(c3t2)),
              "T2_C4": "%d/%d" % (sum(c4t2.values()), len(c4t2)),
              "T3_C3_mean": round(sum(c3t3.values()) / len(c3t3), 2),
              "T3_C4_mean": round(sum(c4t3.values()) / len(c4t3), 2),
              "T3_C2_mean": round(sum(t3["FAS-reset"].values())
                                  / len(t3["FAS-reset"]), 2)}

# ── campaign v2(C/E/F/I/T/J)────────────────────────────────────────
cv = dedup(load(os.path.join(CG, "raw_C.jsonl")))
cvE = dedup(load(os.path.join(CG, "raw_E.jsonl")))
cvF = dedup(load(os.path.join(CG, "raw_F.jsonl")))
cvI = dedup(load(os.path.join(CG, "raw_I.jsonl")))
cvT = dedup(load(os.path.join(CG, "raw_T.jsonl")))
cvT2 = dedup(load(os.path.join(CG, "raw_T2.jsonl")))
cvJ = dedup(load(os.path.join(CG, "raw_J.jsonl")))
# C
c3p3 = defaultdict(list)
for r in cv:
    if r.get("exp") == "C":
        c3p3[r["condition"]].append(r["P3"]["success"])
OUT.setdefault("cv", {})["C_P3"] = {c: "%d/%d" % (sum(v), len(v)) for c, v in c3p3.items()}
# E2(fas vs fas-ef T3 fails)
ef = defaultdict(dict)
for r in cvE:
    if r.get("exp") == "E":
        fr = r["test"].get("fail_repeats")
        if fr is not None:
            ef[r["condition"]][r["seed"]] = fr
OUT["cv"]["E_fails"] = {c: round(sum(v.values()) / len(v), 2)
                        for c, v in ef.items() if v}
common = sorted(set(ef["fas-ef"]) & set(ef["fas"]))
d = [ef["fas-ef"][s] - ef["fas"][s] for s in common]
st, pe = wilcoxon(d, alternative="less")
OUT["cv"]["E2_p"] = round(float(pe), 5)
OUT["cv"]["E_fails"] = {c: round(sum(v.values()) / len(v), 2)
                        for c, v in ef.items() if v}
# F v7
fv7 = defaultdict(dict)
for r in load(os.path.join(CG, "raw_F.jsonl")):
    fv7[(r["hist"], r["self_goal_on"])].setdefault("g", []).append(
        r["free"]["gathers"])
OUT["cv"]["F_failure_ON_gathers"] = round(sum(
    fv7[("failure", True)]["g"]) / len(fv7[("failure", True)]["g"]), 2)
OUT["cv"]["F_failure_OFF_gathers"] = round(sum(
    fv7[("failure", False)]["g"]) / len(fv7[("failure", False)]["g"]), 2)
# I
i_succ = defaultdict(lambda: [0, 0])
i_succ = defaultdict(lambda: [0, 0])
for r in cvI:
    i_succ[r["condition"]][0] += r["test"]["success"]
    i_succ[r["condition"]][1] += 1
OUT["cv"]["I"] = {c: "%d/%d" % tuple(v) for c, v in i_succ.items()}
# T(tradeoff T1 轮:ef 复现)
t1f = [r for r in cvT if r.get("P2")]
ef_fails = [r["P2"]["fail_gathers"] for r in cvT
            if r["condition"] == "fas-ef" and "P2" in r]
fas_fails = [r["P2"]["fail_gathers"] for r in cvT
             if r["condition"] == "fas" and "P2" in r]
OUT["cv"]["T_ef_mean_fails"] = round(sum(ef_fails) / max(1, len(ef_fails)), 1)
OUT["cv"]["T_fas_mean_fails"] = round(sum(fas_fails) / max(1, len(fas_fails)), 1)
# T2b(重拾)
t2b = defaultdict(lambda: [0, 0])
for r in cvT2:
    t2b[r["condition"]][0] += r["P3"]["success"]
    t2b[r["condition"]][1] += 1
OUT["cv"]["T2b_P3"] = {c: "%d/%d" % tuple(v) for c, v in t2b.items()}
# J
jn = [r for r in cvJ if r["cond"] == "fas" and r["seed"] == 0]
if jn:
    s10 = next(s for s in jn[0]["snaps"] if s["cycle"] == 10)
    s100 = next(s for s in jn[0]["snaps"] if s["cycle"] == 100)
    OUT["cv"]["J_nodes"] = [s10["nodes"], s100["nodes"]]
    OUT["cv"]["J_edges"] = [s10["edges"], s100["edges"]]

json.dump(OUT, open(os.path.join(CG, "audit_claims.json"), "w",
                    encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(OUT, ensure_ascii=False, indent=1))
