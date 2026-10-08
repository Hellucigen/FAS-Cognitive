# -*- coding: utf-8 -*-
# analyze_campaign.py — Step 9-10:统计分析 + 机制分析
# 读取 raw_*.jsonl(同 (exp,condition,seed) 后写覆盖先写),输出
# RESULTS.csv / STATISTICS.csv / MECHANISM_RESULTS.csv。
# 统计:配对 McNemar(成功)、配对 Wilcoxon(决策数/tokens);族内 Holm;
# 效应量 r;校正前后 p 并报。
import csv
import json
import os
import sys
from collections import defaultdict
from math import comb

import numpy as np
from scipy.stats import wilcoxon

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["raw_A.jsonl", "raw_K.jsonl", "raw_I.jsonl", "raw_C.jsonl",
         "raw_E.jsonl", "raw_B_mech.jsonl", "raw_F.jsonl"]


def load_rows():
    seen = {}
    for fn in FILES:
        p = os.path.join(HERE, fn)
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            key = (r.get("exp"), r.get("condition") or r.get("hist"),
                   r.get("seed"), fn)
            seen[key] = r
    return list(seen.values())


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(comb(n, i) for i in range(k + 1)) / 2 ** n * 2
    return min(1.0, p)


def paired_wilcoxon(a, b):
    d = np.array(a) - np.array(b)
    nz = d[d != 0]
    if len(nz) == 0:
        return 1.0, 0.0
    try:
        method = "exact" if len(nz) <= 25 else "approx"
        st, p = wilcoxon(nz, alternative="two-sided", method=method)
        r = abs(st - len(nz) * (len(nz) + 1) / 4) / (len(nz) * (len(nz) + 1) / 4)
        return float(p), float(min(1.0, r))
    except Exception:
        return 1.0, 0.0


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    m = len(ps)
    out = [1.0] * m
    prev = 0.0
    for rank, i in enumerate(order):
        adj = min(1.0, (m - rank) * ps[i])
        prev = max(prev, adj)
        out[i] = prev
    return out


def success_of(r):
    exp = r.get("exp")
    if exp == "A":
        return r["P4"]["success"], r["P5"]["success"], r["P4"]["decisions"], \
            r["P4"]["prompt_tokens"] + r["P5"]["prompt_tokens"]
    if exp == "I":
        t = r["test"]
        return t["success"], t["success"], t["decisions"], t["prompt_tokens"]
    if exp == "K":
        t = r["test"]
        return t["success"], t["success"], t["decisions"], t["prompt_tokens"]
    if exp == "C":
        return r["P3"]["success"], r["P1"]["success"], r["P3"]["decisions"], \
            r["P3"]["prompt_tokens"]
    if exp == "E":
        t = r["test"]
        return (1 if (t.get("first_productive_step") is not None and
                      t["fail_gathers"] <= 4) else 0), t["fail_gathers"], \
            t["decisions"], t["prompt_tokens"]
    return 0, 0, 0, 0


def main():
    rows = load_rows()
    # RESULTS.csv(逐 run 扁平)
    with open(os.path.join(HERE, "RESULTS.csv"), "w", encoding="utf-8",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["exp", "condition", "seed", "success", "success2",
                    "decisions", "prompt_tokens", "failure_class"])
        for r in rows:
            if r.get("exp") in ("B", "F") or "test" not in json.dumps(r)[:2000] \
                    and r.get("exp") not in ("A", "C"):
                continue
            s1, s2, dec, pt = success_of(r)
            w.writerow([r.get("exp"), r.get("condition"), r.get("seed"),
                        s1, s2, dec, pt, r.get("failure_class", "none")])

    # STATISTICS.csv(按实验做条件间配对比较)
    by = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        exp = r.get("exp")
        if exp not in ("A", "I", "K", "C", "E"):
            continue
        cond = r.get("condition")
        seed = r.get("seed")
        s1, s2, dec, pt = success_of(r)
        by[exp][cond][seed] = (s1, dec, pt)

    stat_rows = []
    for exp, conds in by.items():
        clist = sorted(conds)
        fam = []
        for i in range(len(clist)):
            for j in range(i + 1, len(clist)):
                a, b = conds[clist[i]], conds[clist[j]]
                common = sorted(set(a) & set(b))
                if len(common) < 5:
                    continue
                sa = [a[s][0] for s in common]
                sb = [b[s][0] for s in common]
                b10 = sum(1 for x, y in zip(sa, sb) if x == 1 and y == 0)
                b01 = sum(1 for x, y in zip(sa, sb) if x == 0 and y == 1)
                p_s = mcnemar_exact(b10, b01)
                da = [a[s][1] for s in common]
                db = [b[s][1] for s in common]
                p_d, r_d = paired_wilcoxon(da, db)
                fam.append({"exp": exp, "A": clist[i], "B": clist[j],
                            "n": len(common), "succ_A": sum(sa),
                            "succ_B": sum(sb), "p_success": p_s,
                            "dec_mean_A": round(float(np.mean(da)), 2),
                            "dec_mean_B": round(float(np.mean(db)), 2),
                            "p_dec": p_d, "r_dec": round(r_d, 3)})
        if fam:
            ps = holm([x["p_success"] for x in fam])
            pd = holm([x["p_dec"] for x in fam])
            for x, q1, q2 in zip(fam, ps, pd):
                x["p_success_holm"] = round(q1, 6)
                x["p_dec_holm"] = round(q2, 6)
                stat_rows.append(x)
    with open(os.path.join(HERE, "STATISTICS.csv"), "w", encoding="utf-8",
              newline="") as f:
        if stat_rows:
            w = csv.DictWriter(f, fieldnames=list(stat_rows[0].keys()))
            w.writeheader()
            for x in stat_rows:
                w.writerow({k: (round(v, 8) if isinstance(v, float) else v)
                            for k, v in x.items()})

    # MECHANISM_RESULTS.csv(B 延迟曲线 + F + 图谱遥测)
    mech = []
    for r in rows:
        if r.get("exp") == "B" or "delay_ticks" in r:
            mech.append({"family": "B_delay", "key": "delay_%d" % r["delay_ticks"],
                         "seed": r.get("seed"),
                         "inventory_attributed": int(r.get(
                             "inventory_outcome_attributed") or 0),
                         "self_success_attributed": int(r.get(
                             "self_success_attributed") or 0)})
        if "hist" in r and r.get("exp") == "F":
            mech.append({"family": "F_selfmodel",
                         "key": "%s_selfgoal_%s" % (r["hist"], r["self_goal_on"]),
                         "seed": r.get("seed"),
                         "gathers": r["free"]["gathers"],
                         "total_actions": sum(r["free"]["actions"].values())})
        if r.get("exp") == "A":
            mech.append({"family": "A_expgraph", "key": r["condition"],
                         "seed": r["seed"],
                         "exp_edges_after_practice": r["practice"][
                             "exp_edges_after_practice"],
                         "exp_edges_at_test_P4": r["P4"].get("exp_edges_at_test")})
    with open(os.path.join(HERE, "MECHANISM_RESULTS.csv"), "w",
              encoding="utf-8", newline="") as f:
        if mech:
            keys = []
            for m in mech:
                for k in m:
                    if k not in keys:
                        keys.append(k)
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for m in mech:
                w.writerow(m)

    # 控制台摘要
    summ = defaultdict(lambda: defaultdict(list))
    for r in rows:
        exp = r.get("exp")
        if exp in ("A", "I", "K", "C", "E"):
            s1, s2, dec, pt = success_of(r)
            summ[exp][r["condition"]].append(s1)
    print("== success rates ==")
    for exp in sorted(summ):
        for cond in sorted(summ[exp]):
            v = summ[exp][cond]
            print(" %s %-16s %d/%d" % (exp, cond, sum(v), len(v)))
    print("statistics rows:", len(stat_rows))


if __name__ == "__main__":
    main()
