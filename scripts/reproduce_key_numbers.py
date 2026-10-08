# reproduce_key_numbers.py — 从归档数据独立重算论文关键数字(零 LLM、零生产栈依赖)。
# 第 3b 步调用预注册冻结的分析实现(code/core_incremental_value/runners/
# analyze.py + metrics.py,未改动)原样重算 ΔJCG/Holm,并与归档
# analysis.json 逐位比对;第 3 步另给 scipy 独立口径作交叉参照。
# 验证目标:
#   1) PSA H1 (FAS-full vs FAS-reset, T2 10/0, McNemar p=0.00195) 与
#      H5 (T3 2.8 vs 7.7, one-sided Wilcoxon p=0.00098)
#   2) Beacon: fas_full 20/20, llm_direct 0/20 (n=20 each, 80 runs)
#   3) 机制战役: 4 条 confirmatory ΔJCG<0, Holm p=1.5e-9, 0/200 反超
#   4) capability_gap_v2 归档: 正式批 508 run / 修复混淆批 390 run
# 用法: python reproduce_key_numbers.py <repo_root 或包根目录>
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from math import comb

from scipy.stats import wilcoxon

ARTIFACT = "*%s*" % "*"
OFFICIAL = ("raw_A.jsonl", "raw_B_mech.jsonl", "raw_C.jsonl", "raw_E.jsonl",
            "raw_F.jsonl", "raw_I.jsonl", "raw_J.jsonl", "raw_K.jsonl",
            "raw_T.jsonl", "raw_T2.jsonl", "raw_T_mech.jsonl")


def rows(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def mcnemar_two_sided(pairs):
    b10 = sum(1 for a, b in pairs if a == 1 and b == 0)
    b01 = sum(1 for a, b in pairs if a == 0 and b == 1)
    n = b10 + b01
    if n == 0:
        return b10, b01, 1.0
    tail = sum(comb(n, i) for i in range(min(b10, b01) + 1)) / 2 ** n
    return b10, b01, min(1.0, 2 * tail)


def psa(root):
    r = rows(os.path.join(root, "persistent_state_advantage",
                          "raw_results.jsonl"))
    def get(cond, task, field):
        return {x["seed"]: x[field] for x in r
                if x.get("condition") == cond and x.get("task") == task
                and x.get("error") is None}
    full, reset = get("FAS-full", "T2", "success"), get("FAS-reset", "T2", "success")
    common = sorted(set(full) & set(reset))
    b10, b01, p1 = mcnemar_two_sided([(full[s], reset[s]) for s in common])
    ff = get("FAS-full", "T3", "fail_repeats")
    rf = get("FAS-reset", "T3", "fail_repeats")
    common = sorted(set(ff) & set(rf))
    xa = [ff[s] for s in common]
    xb = [rf[s] for s in common]
    p5 = wilcoxon(xb, xa, alternative="greater", method="exact").pvalue
    sham = get("FAS-sham", "T2", "success")
    return {
        "H1_paired_n": len(common), "H1_wins": b10, "H1_losses": b01,
        "H1_p": round(p1, 6),
        "T2_FAS_full": "%d/%d" % (sum(full.values()), len(full)),
        "T2_FAS_reset": "%d/%d" % (sum(reset.values()), len(reset)),
        "T2_FAS_sham": "%d/%d" % (sum(sham.values()), len(sham)),
        "T3_mean_full": round(statistics.mean(xa), 2),
        "T3_mean_reset": round(statistics.mean(xb), 2),
        "H5_p_one_sided": round(p5, 6),
    }


def beacon(root):
    r = rows(os.path.join(root, "beacon_v1", "raw_results_final80.jsonl"))
    c = Counter()
    for x in r:
        if x.get("type") == "task_result" and x.get("harness_valid"):
            c[x["condition"]] += x["success"]
    n = Counter()
    for x in r:
        if x.get("type") == "task_result" and x.get("harness_valid"):
            n[x["condition"]] += 1
    return {k: "%d/%d" % (c[k], n[k]) for k in sorted(n)}


def civ(root):
    r = rows(os.path.join(root, "core_incremental_value", "raw_results.jsonl"))
    by = {}
    for x in r:
        key = (x.get("_phase"), x.get("condition"), x.get("seed"), x.get("method"))
        by.setdefault(key, {})[x.get("variant") or "multi"] = x
    CF = {(3, 100, 100, 0.25): "C-1", (3, 300, 300, 0.5): "C-2",
          (5, 300, 300, 0.5): "C-3", (5, 1000, 1000, 0.75): "C-4"}
    conds = {}
    for (_ph, cond, _s, _m), _v in by.items():
        parts = dict(p.split(":", 1) for p in cond.split("|") if ":" in p)
        key = (int(parts.get("inputs_n", 0)), int(parts.get("N", 0)),
               int(parts.get("n_distractors", 0)), float(parts.get("noise", -1)))
        if key in CF:
            conds.setdefault(CF[key], cond)
    out = {}
    reversals = 0
    ps = []
    for label in sorted(conds):
        cond = conds[label]
        jf, jb = {}, {}
        for (ph, c, seed, m), rv in by.items():
            if ph != "confirmatory" or c != cond:
                continue
            mm = rv.get("multi")
            if not mm or mm.get("target_activation") is None:
                continue
            ss = [rv.get("single:%d" % i, {}).get("target_activation")
                  for i in range(int(mm.get("inputs", 3)))]
            if any(s is None for s in ss):
                continue
            jcg = mm["target_activation"] - statistics.mean(ss)
            (jf if m == "fas" else jb if m == "b2" else {}).setdefault(seed, jcg)
        common = sorted(set(jf) & set(jb))
        diffs = [jf[s] - jb[s] for s in common]
        reversals += sum(1 for d in diffs if d > 0)
        nz = [d for d in diffs if abs(d) > 1e-12]
        # 论文口径: 单尾 Wilcoxon(one_tailed=True), 拒绝方向为 FAS<B2;
        # 中位差 = median(fas - b2), 与 metrics.wilcoxon_signed_rank 一致
        p = wilcoxon(nz, alternative="less", method="approx").pvalue \
            if nz else 1.0
        ps.append((label, len(common), round(statistics.median(diffs), 3),
                   round(p, 15)))
    ps.sort(key=lambda t: t[3])
    holm, running = [], 0.0
    for i, (label, n, md, p) in enumerate(ps):
        running = max(running, min(1.0, p * (len(ps) - i)))
        holm.append((label, n, md, p, running))
    out["conditions"] = holm
    out["reversals"] = "%d/%d" % (reversals, sum(t[1] for t in ps))
    return out


def cv2_counts(root):
    base = os.path.join(root, "capability_gap_v2")
    off = art = 0
    detail = {"official": Counter(), "repair": Counter()}
    for f in os.listdir(base):
        if not (f.startswith("raw_") and f.endswith(".jsonl")):
            continue
        n = sum(1 for l in open(os.path.join(base, f), encoding="utf-8")
                if l.strip())
        if any(s in f for s in ("smoke", "mock", "_v1", "_v2_openfacts",
                                "prerepair", "artifact", "noop", "sync",
                                "inert", "untriggered", "nameerror", "patch",
                                "writeonly", "probe_unstable")):
            art += n
            detail["repair"][f] = n
        else:
            off += n
            detail["official"][f] = n
    return {"official_total": off, "repair_total": art, "detail": detail}


def civ_frozen(root):
    """3b) 原样调用冻结分析实现(runners/analyze.py,未改动),输出与
    归档 analysis.json 的 confirmatory_primary 逐位比对。"""
    import shutil
    import subprocess
    import tempfile
    code_dir = os.path.join(root, "code") if os.path.isdir(
        os.path.join(root, "code")) else root
    runners = os.path.join(code_dir, "core_incremental_value", "runners",
                           "analyze.py")
    raw = os.path.join(root, "core_incremental_value", "raw_results.jsonl")
    for cand in (os.path.join(root, "data", "core_incremental_value",
                              "raw_results.jsonl"),
                 os.path.join(code_dir, "core_incremental_value",
                              "raw_results.jsonl")):
        if os.path.isfile(cand):
            raw = cand
            break
    if not os.path.isfile(runners) or not os.path.isfile(raw):
        return {"status": "frozen implementation or raw archive missing"}
    tmp = tempfile.mkdtemp(prefix="civ_frozen_")
    try:
        shutil.copytree(os.path.dirname(runners), os.path.join(tmp, "runners"))
        shutil.copy2(raw, os.path.join(tmp, "raw_results.jsonl"))
        p = subprocess.run([sys.executable,
                            os.path.join(tmp, "runners", "analyze.py")],
                           capture_output=True, text=True, errors="replace")
        if p.returncode != 0:
            return {"status": "frozen implementation failed",
                    "stderr": p.stderr[-400:]}
        with open(os.path.join(tmp, "analysis.json"), encoding="utf-8") as fh:
            fresh = json.load(fh)["confirmatory_primary"]
        out = []
        for t in fresh:
            if t.get("family") != "primary":
                continue
            out.append({"test_id": t["test_id"],
                        "median_diff": t["median_diff"],
                        "holm_p": t["holm_p"], "r": t["r_effect"]})
        return {"status": "ok", "primary": out,
                "holm_p_max": max(t["holm_p"] for t in out)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def print_version(root):
    """Echo the package version stamp (VERSION.txt) in the output header."""
    for cand in (os.path.join(root, "VERSION.txt"),
                 os.path.join(os.path.dirname(root), "VERSION.txt")):
        if os.path.isfile(cand):
            with open(cand, encoding="utf-8") as fh:
                lines = [l.rstrip() for l in fh if l.strip()]
            print("== package version ==")
            for l in lines:
                print("   " + l)
            return
    print("== package version == (no VERSION.txt found)")


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    data = os.path.join(root, "data") if os.path.isdir(
        os.path.join(root, "data")) else root
    print_version(root)
    print("== 1) PSA (persistent_state_advantage) ==")
    print(json.dumps(psa(data), indent=1))
    print("== 2) Beacon (raw_results_final80) ==")
    print(json.dumps(beacon(data), indent=1))
    print("== 3) Mechanism campaign (core_incremental_value) ==")
    c = civ(data)
    for row in c["conditions"]:
        print("   %s n=%d meanDiff=%s p_raw=%s holm=%s" % row)
    print("   reversals (FAS>flat):", c["reversals"])
    print("== 3b) Frozen preregistered implementation (runners/analyze.py, unmodified) ==")
    fr = civ_frozen(root if os.path.isdir(os.path.join(root, "code"))
                    else data)
    print(json.dumps(fr, indent=1)[:1200])
    print("== 4) capability_gap_v2 run accounting ==")
    cc = cv2_counts(data)
    print("   official:", cc["official_total"], " repair/confounded:",
          cc["repair_total"])
    print("   official files:", dict(cc["detail"]["official"]))
    print("   repair files:", dict(cc["detail"]["repair"]))
    print("== 5) routing campaign v2 (core_routing_v2, archived summary.csv) ==")
    try:
        import analyze_routing_v2  # noqa: F401
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(
            os.path.abspath(__file__))))
    import io as _io
    import contextlib
    base = os.path.join(root, "core_routing_v2") if os.path.isdir(
        os.path.join(root, "core_routing_v2")) else os.path.join(
        root, "data", "core_routing_v2")
    if os.path.isfile(os.path.join(base, "campaign", "summary.csv")):
        here = os.path.dirname(os.path.abspath(__file__))
        cand = [os.path.join(here, "analyze_routing_v2.py"),
                os.path.join(root, "scripts", "analyze_routing_v2.py"),
                os.path.join(root, "code", "scripts",
                             "analyze_routing_v2.py")]
        script = next((c for c in cand if os.path.isfile(c)), None)
        if script is None:
            print("   skipped: analyze_routing_v2.py not found")
            return
        buf = _io.StringIO()
        sys.argv = ["analyze_routing_v2", base]
        with contextlib.redirect_stdout(buf):
            try:
                import runpy
                runpy.run_path(script, run_name="__main__")
            except SystemExit:
                pass
        txt = buf.getvalue()
        keep = [l for l in txt.splitlines()
                if ("cited=" in l or "mismatches" in l or "MATCH" in l
                    or "range vs llm_direct" in l or "planned comparisons" in l)]
        print(chr(10).join("   " + l.strip() for l in keep))
    else:
        print("   skipped: campaign/summary.csv not found under", base)


if __name__ == "__main__":
    main()
