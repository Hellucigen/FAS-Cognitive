# analyze.py — 统计与汇总(预注册指标,见 manifest;读 raw_results.jsonl → CSV/JSON)
# ============================================================================
# 输出: analysis.json(全部聚合)/ summary.csv / statistics.csv
# 主族: 4 条 confirmatory ΔJCG(FAS−B2)配对单尾 Wilcoxon + Holm。
# ============================================================================

import csv
import io
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metrics as M

OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(OUT, "raw_results.jsonl")


def load():
    rows = []
    with open(RAW, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    by = {}
    for r in rows:
        key = (r.get("_phase"), r.get("condition"), r.get("seed"), r.get("method"))
        by.setdefault(key, {})[r.get("variant") or "multi"] = r
    return rows, by


def json_safe(o):
    return o


def cfg_key(cond):
    """cell_label → dict。"""
    d = {}
    for part in cond.split("|"):
        if ":" in part:
            k, v = part.split(":", 1)
            d[k] = v
    return d


CF_MAP = {
    (3, 100, 100, 0.25): "C-1",
    (3, 300, 300, 0.5): "C-2",
    (5, 300, 300, 0.5): "C-3",
    (5, 1000, 1000, 0.75): "C-4",
}


def cond_cf_label(cond):
    c = cfg_key(cond)
    return CF_MAP.get((int(c.get("inputs_n", 0)), int(c.get("N", 0)),
                       int(c.get("n_distractors", 0)), float(c.get("noise", -1))),
                      cond)


# ---------------------------------------------------------------- 主流程

def confirmatory(by):
    """4 条 confirmatory: JCG/ΔJCG/MRR 统计(主族 + 次要)。"""
    tests = []
    summary_rows = []
    prim_pvals = []
    prim_names = []
    prim_detail = {}
    for cond in sorted({k[1] for k in by if k[0] == "confirmatory"}):
        label = cond_cf_label(cond)
        d = {}
        for method in ("fas", "b1", "b2", "b3"):
            jcg = []
            mrr = []
            rec1 = []
            jact = []
            srank = []
            stab = []
            for (ph, c, seed, m), rv in by.items():
                if not (ph == "confirmatory" and c == cond and m == method
                        and "multi" in rv):
                    continue
                mm = rv["multi"]
                tg = mm.get("target_activation")
                if tg is None:
                    continue
                ss = []
                for i in range(mm.get("inputs", 3)):
                    sv = rv.get(f"single:{i}", {}).get("target_activation")
                    if sv is not None:
                        ss.append(sv)
                if ss:
                    jcg.append(M.jcg_value(tg, ss))
                mrr.append(mm.get("mrr") if mm.get("mrr") is not None else 0.0)
                rec1.append(1.0 if mm.get("recall@1") == 1.0 else 0.0)
                jact.append(tg)
                r3 = set((rv["multi"].get("top3_ids") or [])[:3])
                s3 = set()
                for i in range(mm.get("inputs", 3)):
                    for cid in (rv.get(f"single:{i}", {}).get("top3_ids") or [])[:3]:
                        s3.add(cid)
                union = s3 | r3
                stab.append(len(r3 & s3) / len(union) if union else 1.0)
            d[method] = {"jcg": jcg, "mrr": mrr, "recall1": rec1,
                         "jact": jact, "stability": stab}
        # 主检验: ΔJCG FAS−B2(配对, 单尾)
        pv = list(zip(d["fas"]["jcg"], d["b2"]["jcg"]))
        diffs = [f - b for f, b in pv]
        p, r, z = M.wilcoxon_signed_rank(d["b2"]["jcg"], d["fas"]["jcg"],
                                          one_tailed=True)
        # 方向: 预注册单尾 = FAS > B2 → diff > 0;这里直接对 diffs 做符号秩单尾
        p_pos, r_pos, _ = M.wilcoxon_signed_rank([0.0] * len(diffs), diffs,
                                                 one_tailed=True)
        p_neg, r_neg, _ = M.wilcoxon_signed_rank([0.0] * len(diffs), [-x for x in diffs],
                                                 one_tailed=True)
        lo, hi = M.bootstrap_ci_median(diffs)
        tests.append({
            "family": "primary", "test_id": f"H3_{label}_djcg",
            "comparison": f"ΔJCG = JCG_FAS − JCG_B2 ({label})",
            "n": len(diffs), "median_diff": round(statistics.median(diffs), 6),
            "mean_diff": round(statistics.mean(diffs), 6),
            "p_fas_gt_b2": p_pos, "p_fas_lt_b2": p_neg, "r_effect": r_neg,
            "ci_lo": lo, "ci_hi": hi,
            "djcg_gt0_rate": round(sum(1 for x in diffs if x > 0) / len(diffs), 4),
        })
        prim_pvals.append(p_pos)
        prim_names.append(f"H3_{label}_djcg")
        prim_detail[label] = diffs
        # 次要
        mrr_f, mrr_b = d["fas"]["mrr"], d["b2"]["mrr"]
        p_mrr, r_mrr, _ = M.wilcoxon_signed_rank(mrr_b, mrr_f)
        jf, jb = d["fas"]["jcg"], d["b2"]["jcg"]
        p_j1, _, _ = M.wilcoxon_signed_rank([0.0] * len(jf), jf, one_tailed=True)
        tests.append({
            "family": "secondary", "test_id": f"S_{label}_mrr",
            "comparison": f"MRR FAS vs B2 ({label})",
            "n": len(mrr_f), "p_value": p_mrr, "r_effect": r_mrr,
            "median_diff": round(statistics.median(
                [a - b for a, b in zip(mrr_f, mrr_b)]), 5),
        })
        for method in ("fas", "b1", "b2", "b3"):
            j = d[method]["jcg"]
            pos = sum(1 for x in j if x > 0)
            p_bin = M.exact_binomial_gt0(pos, len(j)) if j else None
            summary_rows.append({
                "condition": label, "method": method, "n": len(j),
                "jcg_median": round(statistics.median(j), 6) if j else "",
                "jcg_mean": round(statistics.mean(j), 6) if j else "",
                "jcg_gt0_rate": round(pos / len(j), 4) if j else "",
                "jcg_binom_p_gt_half": p_bin,
                "mrr_median": round(statistics.median(d[method]["mrr"]), 4),
                "mrr_mean": round(statistics.mean(d[method]["mrr"]), 4),
                "recall1": round(statistics.mean(d[method]["recall1"]), 4),
                "jact_median": round(statistics.median(d[method]["jact"]), 4),
                "topk_stability_mean": round(statistics.mean(d[method]["stability"]), 4),
            })
    # Holm(只校正主族 4 条;按 prim_pvals 追加顺序配对)
    adj = M.holm_correct(prim_pvals)
    name2adj = dict(zip(prim_names, adj))
    for t in tests:
        if t["family"] == "primary":
            t["holm_p"] = name2adj.get(t["test_id"], 1.0)
            t["significant_at_05"] = t["holm_p"] < 0.05
    return tests, summary_rows, prim_pvals, prim_names


def sweeps(by):
    """五维 sweep 聚合(median MRR per cell per method)。"""
    agg = {}
    for (ph, cond, seed, method), rv in by.items():
        if ph != "sweeps" or "multi" not in rv or method not in ("fas", "b2", "b3"):
            continue
        c = cfg_key(cond)
        dim = None
        val = None
        for k in ("N", "inputs_n", "hops", "n_distractors", "noise"):
            if k in c:
                pass
        base = dict(N=300, inputs_n=3, hops=2, n_distractors=300, noise=0.5)
        for k in base:
            if c.get(k) != str(base[k]):
                dim, val = k, c.get(k)
        if dim is None:
            continue
        mm = rv["multi"]
        mrr = mm.get("mrr") if mm.get("mrr") is not None else 0.0
        agg.setdefault((dim, method), {}).setdefault(val, []).append(mrr)
    out = {}
    for (dim, method), cells in agg.items():
        out[(dim, method)] = {v: statistics.median(x) for v, x in sorted(
            cells.items(), key=lambda kv: (len(kv[0]), kv[0]) if not _isfloat(kv[0]) else (0, float(kv[0])))}
    return out


def _isfloat(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def robustness(sweep_agg):
    """noise 维: OLS 斜率 / AUC(mean) / critical noise(median MRR ≥ 0.9 的最高 noise)。"""
    out = {}
    for method in ("fas", "b2"):
        cells = sweep_agg.get(("noise", method), {})
        xs = sorted((float(k) for k in cells))
        ys = [cells[str(x)] for x in xs]
        n = len(xs)
        mx = sum(xs) / n
        my = sum(ys) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = sum((x - mx) ** 2 for x in xs)
        slope = num / den if den else 0.0
        critical = 0.0
        for x, y in zip(xs, ys):
            if y >= 0.9:
                critical = x
        out[method] = {"slope": round(slope, 4),
                       "auc_norm": round(statistics.mean(ys), 4),
                       "critical_noise": critical,
                       "points": list(zip(xs, ys))}
    return out


def dilution(by):
    """noise + distractor sweep 上 reachability vs target_activation 的 ρ(FAS multi)。"""
    import math as _m

    def _rho(x, y):
        n = len(x)
        if n < 3:
            return None
        def ranks(v):
            o = sorted(range(n), key=lambda i: v[i])
            r = [0.0] * n
            i = 0
            while i < n:
                j = i
                while j + 1 < n and v[o[j + 1]] == v[o[i]]:
                    j += 1
                avg = (i + j) / 2 + 1
                for k in range(i, j + 1):
                    r[o[k]] = avg
                i = j + 1
            return r
        rx, ry = ranks(x), ranks(y)
        mx, my = sum(rx) / n, sum(ry) / n
        cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / n
        sx = _m.sqrt(sum((a - mx) ** 2 for a in rx) / n)
        sy = _m.sqrt(sum((b - my) ** 2 for b in ry) / n)
        return cov / (sx * sy) if sx and sy else None

    pts_noise = {"reach": [], "tact": [], "entropy": [], "gini": []}
    pts_dist = {"reach": [], "tact": [], "entropy": [], "gini": []}
    for (ph, cond, seed, method), rv in by.items():
        if ph != "sweeps" or method != "fas" or "multi" not in rv:
            continue
        c = cfg_key(cond)
        mm = rv["multi"]
        reach = mm.get("reachable_nodes")
        tact = mm.get("target_activation")
        if reach is None or tact is None:
            continue
        if "noise" in c and all(c.get(k) == str(v) for k, v in
                                (("N", 300), ("inputs_n", 3), ("hops", 2),
                                 ("n_distractors", 300)) if k in c):
            pts_noise["reach"].append(reach)
            pts_noise["tact"].append(tact)
            pts_noise["entropy"].append(mm.get("entropy"))
            pts_noise["gini"].append(mm.get("gini"))
        if "n_distractors" in c and all(c.get(k) == str(v) for k, v in
                                        (("N", 300), ("inputs_n", 3), ("hops", 2),
                                         ("noise", 0.5)) if k in c):
            pts_dist["reach"].append(reach)
            pts_dist["tact"].append(tact)
            pts_dist["entropy"].append(mm.get("entropy"))
            pts_dist["gini"].append(mm.get("gini"))
    return {
        "noise_sweep_rho_reach_tact": _rho(pts_noise["reach"], pts_noise["tact"]),
        "distractor_sweep_rho_reach_tact": _rho(pts_dist["reach"], pts_dist["tact"]),
        "noise_points": pts_noise, "distractor_points": pts_dist,
    }


def topology_compare(by):
    out = {}
    for (ph, cond, seed, method), rv in by.items():
        if ph != "topology_compare" or "multi" not in rv:
            continue
        c = cfg_key(cond)
        t = c.get("topology", "?")
        mm = rv["multi"]
        mrr = mm.get("mrr") if mm.get("mrr") is not None else 0.0
        out.setdefault((t, method), []).append(mrr)
    return {f"{t}|{m}": {"mrr_median": statistics.median(v),
                          "mrr_mean": statistics.mean(v), "n": len(v)}
            for (t, m), v in out.items()}


def counterfactuals(by):
    out = {}
    for (ph, cond, seed, method), rv in by.items():
        if ph != "counterfactuals" or "multi" not in rv:
            continue
        which = cond.split("|")[0]
        mm = rv["multi"]
        tgt = mm.get("target_rank")
        out.setdefault((which, method), []).append(mm)
    res = {}
    for (which, method), rows in out.items():
        n = len(rows)
        top1 = sum(1 for r in rows if r.get("target_rank") == 0) / n if n else 0
        res[f"{which}|{method}"] = {"n": n, "target_top1": round(top1, 4)}
    # C2 附加: multi vs single 排名一致性(Spearman, FAS)
    corr = []
    for (ph, cond, seed, method), rv in by.items():
        if ph != "counterfactuals" or method != "fas" or "multi" not in rv:
            continue
        if not cond.startswith("C2"):
            continue
        mm = rv["multi"]
        top3_m = set((mm.get("top3_ids") or [])[:3])
        un = set()
        for i in range(3):
            for cid in (rv.get(f"single:{i}", {}).get("top3_ids") or [])[:3]:
                un.add(cid)
        corr.append(len(top3_m & un) / len(top3_m | un) if (top3_m | un) else 1.0)
    res["C2_fas_ranking_stability_mean_jaccard"] = (
        round(statistics.mean(corr), 4) if corr else None)
    return res


def combo(by):
    out = {}
    for (ph, cond, seed, method), rv in by.items():
        if ph != "combo" or "multi" not in rv:
            continue
        mm = rv["multi"]
        order = mm.get("top3_ids") or []
        o3_first = order[0] == "O3" if order else False
        out.setdefault(method, []).append(o3_first)
    return {m: round(statistics.mean(v), 4) for m, v in out.items()}


def weight_sweep(by):
    out = {}
    for (ph, cond, seed, method), rv in by.items():
        if ph != "weight_sweep" or "multi" not in rv:
            continue
        c = cfg_key(cond)
        w = c.get("weight")
        t = c.get("topology")
        if w is None or t is None:
            continue
        mm = rv["multi"]
        mrr = mm.get("mrr") if mm.get("mrr") is not None else 0.0
        out.setdefault((t, method, w), []).append(mm)
    res = {}
    for (t, method, w), rows in out.items():
        n = len(rows)
        res[f"{t}|{method}|w={w}"] = {
            "n": n,
            "mrr_median": round(statistics.median(r.get("mrr") or 0.0 for r in rows), 4),
            "margin_median": round(statistics.median(
                (r.get("activation_margin") or 0.0) for r in rows), 5),
            "entropy_median": round(statistics.median(r.get("entropy") or 0.0 for r in rows), 4),
        }
    return res


def neg_edges(by):
    out = {}
    for (ph, cond, seed, method), rv in by.items():
        if ph != "neg_edges" or "multi" not in rv:
            continue
        c = cfg_key(cond)
        nm = c.get("neg_mode", "none")
        mm = rv["multi"]
        out.setdefault((nm, method), []).append(mm)
    res = {}
    for (nm, method), rows in out.items():
        n = len(rows)
        res[f"neg:{nm}|{method}"] = {
            "n": n,
            "mrr_median": round(statistics.median(r.get("mrr") or 0.0 for r in rows), 4),
            "joint_top1": round(sum(1 for r in rows if r.get("target_rank") == 0) / n, 4),
            "total_act_median": round(statistics.median(
                (r.get("total_activation") or 0.0) for r in rows), 4),
            "entropy_median": round(statistics.median(r.get("entropy") or 0.0 for r in rows), 4),
        }
    return res


def insertion_order(by):
    """10 perms × 30 seeds: 每 perm 的 median MRR;CV + ΔJCG 符号稳定性。"""
    perms = {}
    jcg_diffs = {}
    for (ph, cond, seed, method), rv in by.items():
        if ph != "insert_order" or "multi" not in rv:
            continue
        perm = int(cond.split(":")[1].split("|")[0])
        mm = rv["multi"]
        tg = mm.get("target_activation")
        method_key = method
        jcg = None
        if tg is not None:
            ss = [rv.get(f"single:{i}", {}).get("target_activation")
                  for i in range(mm.get("inputs", 3))]
            ss = [s for s in ss if s is not None]
            if ss:
                jcg = tg - statistics.mean(ss)
        perms.setdefault((perm, method), []).append(
            mm.get("mrr") if mm.get("mrr") is not None else 0.0)
        if jcg is not None:
            jcg_diffs.setdefault(perm, {}).setdefault(method, []).append(jcg)
    out = {"per_perm_mrr": {}, "jcg": {}}
    for (perm, method), v in perms.items():
        out["per_perm_mrr"].setdefault(method, []).append(statistics.median(v))
    for method in ("fas", "b2"):
        vals = out["per_perm_mrr"].get(method, [])
        out.setdefault("cv", {})[method] = round(statistics.pstdev(vals) / statistics.mean(vals), 5)
    # per-perm JCG 中位数(FAS/B2 各自;ΔJCG 符号稳定性)
    for perm, d in sorted(jcg_diffs.items()):
        if "fas" in d and "b2" in d:
            out["jcg"][perm] = {"fas": round(statistics.median(d["fas"]), 6),
                                "b2": round(statistics.median(d["b2"]), 6)}
    return out


def hidden_oracle(by):
    """label/id 轮换后 target_rank 与原始标志同构(FAS multi)。"""
    res = {}
    for kind in ("label", "id"):
        ranks = []
        for (ph, cond, seed, method), rv in by.items():
            if ph != "hidden_oracle" or method != "fas" or "multi" not in rv:
                continue
            if not cond.startswith(kind):
                continue
            ranks.append(rv["multi"].get("target_rank"))
        ranks = [r for r in ranks if r is not None]
        res[kind] = {"n": len(ranks),
                     "top1_rate": round(sum(1 for r in ranks if r == 0) / len(ranks), 4),
                     "rank_median": statistics.median(ranks) if ranks else None}
    return res


def main():
    rows, by = load()
    print(f"[analyze] loaded {len(rows)} rows")
    tests, summary_rows, prim_pvals, prim_names = confirmatory(by)
    sweep_agg = sweeps(by)
    rob = robustness(sweep_agg)
    dil = dilution(by)
    top_c = topology_compare(by)
    cfs = counterfactuals(by)
    com = combo(by)
    ws = weight_sweep(by)
    ne = neg_edges(by)
    ins = insertion_order(by)
    hid = hidden_oracle(by)

    res = {
        "confirmatory_primary": tests,
        "sweeps": {f"{k[0]}|{k[1]}": v for k, v in sweep_agg.items()},
        "robustness": rob,
        "dilution": dil,
        "topology_compare": top_c,
        "counterfactuals": cfs,
        "combo": com,
        "weight_sweep": ws,
        "neg_edges": ne,
        "insertion_order": ins,
        "hidden_oracle": hid,
    }
    with open(os.path.join(OUT, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)

    with open(os.path.join(OUT, "summary.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()) if summary_rows else [])
        w.writeheader()
        w.writerows(summary_rows)

    with open(os.path.join(OUT, "statistics.csv"), "w", encoding="utf-8", newline="") as f:
        cols = ["family", "test_id", "comparison", "n", "median_diff", "mean_diff",
                "p_value", "p_fas_gt_b2", "p_fas_lt_b2", "holm_p", "significant_at_05",
                "r_effect", "ci_lo", "ci_hi", "djcg_gt0_rate"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for t in tests:
            w.writerow({c: t.get(c, "") for c in cols})
    print("[analyze] primary p (fas>B2):",
          {n: p for n, p in zip(prim_names, prim_pvals)})
    print("[analyze] robustness:", json.dumps(rob))
    print("[analyze] done → summary.csv / statistics.csv / analysis.json")


if __name__ == "__main__":
    main()