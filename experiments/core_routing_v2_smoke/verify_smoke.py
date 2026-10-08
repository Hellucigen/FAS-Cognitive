# verify_smoke.py — smoke test 七项验证（只读产物）
import json, os, glob, csv, collections, statistics as st

ROOT = os.path.dirname(os.path.abspath(__file__))
CONDS = ["fas_full", "D-noact", "D-nodemand", "D-flat"]


def load_decs():
    rows = []
    for f in glob.glob(os.path.join(ROOT, "*", "raw_results.jsonl")):
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") == "decision":
                rows.append(d)
    return rows


def main():
    decs = load_decs()
    res = [json.loads(l) for c in CONDS
           for l in open(os.path.join(ROOT, c, "raw_results.jsonl"),
                         encoding="utf-8") if '"task_result"' in l] \
        if all(os.path.exists(os.path.join(ROOT, c, "raw_results.jsonl"))
               for c in CONDS) else []
    print("=== 1. Graph ===")
    for c in CONDS:
        d = [r for r in decs if r["condition"] == c]
        if not d:
            print(f"  {c}: NO DATA"); continue
        ne = [r["graph_nodes"] for r in d]
        ee = [r["graph_edges"] for r in d]
        print(f"  {c:<12} decisions={len(d):<4} nodes={st.mean(ne):.1f} "
              f"edges={st.mean(ee):.1f} (min {min(ee)})")

    print("\n=== 2. Diffusion（逐决策 activated_edges）===")
    for c in CONDS:
        d = [r for r in decs if r["condition"] == c]
        ae = [r["activated_edges"] for r in d]
        print(f"  {c:<12} activated_edges mean={st.mean(ae):.2f} "
              f"max={max(ae)} zero_frac="
              f"{sum(1 for x in ae if x == 0)/max(len(ae),1):.2f}")

    print("\n=== 3. Activation landscape ===")
    for c in CONDS:
        d = [r for r in decs if r["condition"] == c]
        vals = [a for r in d for a in r["selected_acts"] if a is not None]
        if vals:
            print(f"  {c:<12} distinct={len({round(v,2) for v in vals})} "
                  f"top1_mean={st.mean([max([a for a in r['selected_acts'] if a is not None] or [0]) for r in d]):.2f}")
        else:
            print(f"  {c:<12} activations=None (flat retrieval, by design)")

    print("\n=== 4. Demand / Gap / Routing / exceptions ===")
    for c in CONDS:
        d = [r for r in decs if r["condition"] == c]
        n = max(len(d), 1)
        dd = sum(1 for r in d if r["demand"])
        gg = sum(1 for r in d if r["gap"])
        rr = sum(1 for r in d if r["routing"])
        exc = sum(r["exception_count"] for r in d)
        print(f"  {c:<12} demand={dd}/{n} gap={gg}/{n} routing={rr}/{n} "
              f"exceptions={exc}")

    print("\n=== 5. First decision (step0) ===")
    for c in CONDS:
        d = [r for r in decs if r["condition"] == c and r["step"] == 0]
        if d:
            z = sum(1 for r in d if len(r["selected_nodes"]) == 0)
            print(f"  {c:<12} step0 n={len(d)} empty={z} "
                  f"mean_nodes={st.mean(len(r['selected_nodes']) for r in d):.1f}")

    print("\n=== 6. Serialization（统一 schema）===")
    for c in CONDS:
        d = [r for r in decs if r["condition"] == c]
        if not d:
            continue
        keys = set(d[0].keys())
        need = {"selected_nodes", "selected_acts", "graph_nodes", "graph_edges",
                "activated_nodes", "activated_edges", "demand", "gap",
                "routing", "mode", "exception_count"}
        print(f"  {c:<12} mode={d[0]['mode']:<13} schema_ok={need <= keys}")

    print("\n=== 7. Ablation validity（telemetry 判据）===")
    a = [r for r in decs if r["condition"] == "fas_full"]
    b = [r for r in decs if r["condition"] == "D-nodemand"]
    c_ = [r for r in decs if r["condition"] == "D-noact"]
    print(f"  fas_full   mode=full        demand_present={sum(1 for r in a if r['demand'])}/{len(a)}")
    print(f"  D-nodemand mode=no-demand   demand_present={sum(1 for r in b if r['demand'])}/{len(b)}")
    print(f"  D-noact    mode=no-diffusion demand_present={sum(1 for r in c_ if r['demand'])}/{len(c_)}")
    print("  判据：D 与 D-nodemand 只在 demand/gap/routing 在场性上不同 →",
          "PASS" if sum(1 for r in a if r['demand']) > 0
          and sum(1 for r in b if r['demand']) == 0 else "FAIL")


if __name__ == "__main__":
    main()
