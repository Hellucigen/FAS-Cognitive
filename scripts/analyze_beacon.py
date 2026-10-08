#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# analyze_beacon.py — A10 信标战役分析(预注册 §4,2026-09-30)
# ============================================================================
# 输出: experiments/beacon_v1/analysis.json (+ 终端表)。
# 主检验 6 项(3 对比 × 2 指标),Bonferroni α=0.0083:
#   对比:  fas_full vs llm_direct / fas_full vs D-noact / fas_full vs D-flat
#   指标:  success(配对 McNemar,双侧) / decisions(配对 Wilcoxon,单侧
#          预期方向 fas_full 更少;按预注册"决策步差分子 0",用双侧保守一致,
#          单侧更宽松,双侧更严——取双侧,如实标注)
# 判据(预注册): fas_full 成功率 0 或与 llm_direct 无差 → H_A10 不支持。
# ============================================================================

import argparse
import json
import math
import os
import statistics

sys_path_setup = __import__("sys")
sys_path_setup.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "research_audit", "mechanism_falsification"))
import metrics as M  # noqa: E402


def exact_mcnemar(b_plus, b_minus, n):
    """配对二值指标: b_plus=1,0 数; b_minus=0,1 数。精确双侧(T=min 侧尾部×2)。"""
    t = min(b_plus, b_minus)
    total = b_plus + b_minus
    if total == 0:
        return 1.0
    # 双侧 P = 2 × P(X <= t | Bin(total, 0.5)),封顶 1
    tail = 0.0
    for k in range(t + 1):
        tail += math.comb(total, k) * 0.5 ** total
    return min(1.0, 2.0 * tail)


def main():
    ap = argparse.ArgumentParser(description="A10 beacon 分析")
    ap.add_argument("--out", default="experiments/beacon_v1")
    ap.add_argument("--alpha", type=float, default=0.0083)
    args = ap.parse_args()

    raw = os.path.join(args.out, "raw_results.jsonl")
    rows = []
    with open(raw, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("{"):
                continue  # 坏行(写入撕裂/空行/NUL)跳过
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("type", "task_result") != "task_result":
                continue
            rows.append(r)
    rows.sort(key=lambda r: (r.get("condition", ""), r.get("seed", 0)))
    test_rows = [r for r in rows if r.get("harness_valid", True)]
    invalid = [r for r in rows if not r.get("harness_valid", True)]
    conds = ("fas_full", "D-noact", "D-flat", "llm_direct")

    summary = {}
    for c in conds:
        rr = [r for r in test_rows if r.get("condition") == c]
        succ = sum(r.get("success", 0) for r in rr)
        dec = [r.get("decisions", 0) for r in rr]
        craft = [r.get("steps_to_craft") for r in rr
                 if r.get("steps_to_craft") is not None]
        calls = sum(r.get("llm_calls", 0) for r in rr)
        ptok = sum(r.get("prompt_tokens", 0) for r in rr)
        ctok = sum(r.get("completion_tokens", 0) for r in rr)
        unpar = sum(r.get("unparseable", 0) for r in rr)
        visible = statistics.mean(
            [r.get("craft_menu_visible_steps", 0) for r in rr]) if rr else 0
        firsts = {}
        for r in rr:
            fa = r.get("first_action")
            firsts[fa] = firsts.get(fa, 0) + 1
        summary[c] = {
            "n": len(rr),
            "success": succ,
            "success_rate": round(succ / max(len(rr), 1), 3),
            "decisions_mean": round(statistics.mean(dec), 2) if dec else None,
            "decisions_median": (statistics.median(dec) if dec else None),
            "steps_to_craft_mean": (
                round(statistics.mean(craft), 2) if craft else None),
            "steps_to_craft_set": sorted(set(craft)) if craft else [],
            "llm_calls": calls, "prompt_tokens": ptok,
            "completion_tokens": ctok,
            "unparseable_total": unpar,
            "craft_menu_visible_steps_mean": round(visible, 2),
            "first_action_dist": firsts,
        }

    # ── 6 主检验 ─────────────────────────────────────────────
    CONTR = (("fas_full", "llm_direct"), ("fas_full", "D-noact"),
             ("fas_full", "D-flat"))
    tests = []
    for a, b in CONTR:
        by_seed = {}
        for r in test_rows:
            if r.get("condition") in (a, b):
                by_seed.setdefault(r.get("seed"), {})[r.get("condition")] = r
        pairs = [(sa.get(a), sa.get(b)) for sa in by_seed.values()
                 if sa.get(a) and sa.get(b)]
        n = len(pairs)
        # success: exact McNemar(配对 0/1)
        b_plus = sum(1 for x, y in pairs if x["success"] == 1 and y["success"] == 0)
        b_minus = sum(1 for x, y in pairs if x["success"] == 0 and y["success"] == 1)
        p_succ = exact_mcnemar(b_plus, b_minus, n)
        # decisions: 配对 Wilcoxon(双侧,同 A9 口径;diffs = D-flat 侧 − fas_full 侧)
        d1 = [x["decisions"] for x, _ in pairs]
        d2 = [y["decisions"] for _, y in pairs]
        pw, r_eff = M.wilcoxon_signed_rank(d1, d2)
        tests.append({
            "contrast": f"{a} vs {b}", "n_pairs": n,
            "succ_a": sum(1 for x, y in pairs if x["success"] == 1),
            "succ_b": sum(1 for x, y in pairs if y["success"] == 1),
            "discordant_10": b_plus, "discordant_01": b_minus,
            "mcnemar_p_exact": round(p_succ, 6),
            "dec_mean_a": round(statistics.mean([x["decisions"] for x, _ in pairs]), 2),
            "dec_mean_b": round(statistics.mean([y["decisions"] for _, y in pairs]), 2),
            "dec_median_a": statistics.median([x["decisions"] for x, _ in pairs]),
            "dec_median_b": statistics.median([y["decisions"] for _, y in pairs]),
            "wilcoxon_p": None, "wilcoxon_r": None,
            "dec_wilcoxon_p": round(pw, 6), "dec_wilcoxon_r": round(r_eff, 6),
            "significant_at_alpha": (p_succ < args.alpha
                                     or pw < args.alpha),
        })

    out = {
        "experiment": "A10 beacon closed-book",
        "predeclared": True,
        "spec": "research_audit/_audits/experiment_A10_beacon_task.md",
        "alpha_bonferroni": args.alpha,
        "n_primary_tests": 6,
        "contamination_checks": {
            "invalid_harness_runs": len(invalid),
            "invalid_list": [
                {k: r.get(k) for k in ("condition", "seed", "failure_class",
                                       "failure_reason")}
                for r in invalid[:10]],
            "infra_failures": [
                {k: r.get(k) for k in ("condition", "seed", "failure_reason")}
                for r in test_rows
                if r.get("failure_class", "none") != "none"][:10],
        },
        "summary": summary,
        "primary_tests": tests,
    }
    with open(os.path.join(args.out, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("=== A10 beacon 汇总 ===")
    print(f"{'cond':<10}{'n':>3}{'succ':>5}{'rate':>7}{'dec_m':>7}"
          f"{'dec_med':>7}{'craft_m':>8}{'calls':>6}{'unpar':>6}")
    for c in conds:
        s = summary[c]
        print(f"{c:<10}{s['n']:>3}{s['success']:>5}{s['success_rate']:>7}"
              f"{s['decisions_mean']:>7}{s['decisions_median']:>7}"
              f"{s['steps_to_craft_mean'] if s['steps_to_craft_mean'] is not None else '—':>8}"
              f"{s['llm_calls']:>6}{s['unparseable_total']:>6}")
    print("\n=== 6 主检验 (α=%g) ===" % args.alpha)
    for t in tests:
        print(f"{t['contrast']:<28} n={t['n_pairs']:>2} "
              f"succ {t['succ_a']:>2}/{t['succ_b']:<2} mcnemar p={t['mcnemar_p_exact']:<8.5f} "
              f"dec {t['dec_median_a']}/{t['dec_median_b']} wilcoxon p={t['dec_wilcoxon_p']:<8.5f} "
              f"r={t['dec_wilcoxon_r']:.3f} sig={t['significant_at_alpha']}")
    print("\n判定(预注册): fas_full 成功率 =", summary["fas_full"]["success_rate"],
          "| 与 llm_direct 对比 mcnemar p =",
          tests[0]["mcnemar_p_exact"])


if __name__ == "__main__":
    main()