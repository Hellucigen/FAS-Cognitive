# audit_compare.py — 只读审计（二）：跨条件焦点重叠 + T3 新旧上下文演化
# 产出 selection_overlap.csv 与 t3_reroute_analysis.csv
import json, os, re, csv, collections, statistics as st

ROOT = r"E:\Project\Fascinator\experiments\core_routing"
OUT = os.path.join(ROOT, "activation_audit")

RE_ACT = re.compile(r"^-\s+(\S+)\s+\(act\s+([\d.]+)\)\s*$")
RE_PLAIN = re.compile(r"^-\s+(\S+)\s*$")


def parse_nodes(ctx):
    """返回 [(node, act|None)]，兼容带激活与不带激活两种格式。"""
    out = []
    for line in ctx.splitlines():
        line = line.rstrip()
        m = RE_ACT.match(line)
        if m:
            out.append((m.group(1), float(m.group(2))))
            continue
        if line.startswith("Graph nodes by text similarity"):
            continue
        m = RE_PLAIN.match(line)
        if m and ":" in m.group(1):
            out.append((m.group(1), None))
    return out


def load(cond):
    """载入某条件全部 decision，索引 (task, seed, phase, step)。"""
    path = os.path.join(ROOT, "ablations" if cond.startswith("D-") else cond,
                        "raw_results.jsonl")
    idx = {}
    if not os.path.exists(path):
        return idx
    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "decision" or d.get("condition") != cond:
            continue
        idx[(d["task"], d["seed"], d["phase"], d["step"])] = d
    return idx


def jaccard(a, b):
    if not a and not b:
        return None
    return len(a & b) / max(len(a | b), 1)


def main():
    fas, flat = load("fas_full"), load("D-flat")
    noact, nodem = load("D-noact"), load("D-nodemand")
    rag, hist = load("llm_rag"), load("llm_history")

    # ── 1. 跨条件焦点集重叠（同 task/seed/phase/step 配对）──
    rows = []
    for key in sorted(fas):
        if key not in flat:
            continue
        task, seed, phase, step = key
        fs = {n for n, _ in parse_nodes(fas[key]["context"])}
        fl = {n for n, _ in parse_nodes(flat[key]["context"])}
        if not fs and not fl:
            continue
        rows.append({
            "task": task, "seed": seed, "phase": phase, "step": step,
            "n_fas": len(fs), "n_flat": len(fl),
            "jaccard_fas_flat": round(jaccard(fs, fl), 3),
            "jaccard_fas_noact": round(jaccard(
                fs, {n for n, _ in parse_nodes(noact[key]["context"])}), 3)
            if key in noact else None,
            "jaccard_fas_nodemand": round(jaccard(
                fs, {n for n, _ in parse_nodes(nodem[key]["context"])}), 3)
            if key in nodem else None,
            "fas_only": ";".join(sorted(fs - fl))[:200],
            "flat_only": ";".join(sorted(fl - fs))[:200],
        })
    with open(os.path.join(OUT, "selection_overlap.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print("selection_overlap.csv:", len(rows), "rows")
    for k in ("jaccard_fas_flat", "jaccard_fas_noact", "jaccard_fas_nodemand"):
        v = [r[k] for r in rows if r[k] is not None]
        if v:
            print(f"  mean {k} = {st.mean(v):.3f}  (n={len(v)})")

    # ── 2. T3 新旧上下文演化 ──
    # t_change = phase 2 step 0（环境已变）；old = oak 系；new = birch 系
    OLD = {"oak_log", "oak_planks", "gather_oak_log", "craft_oak_planks"}
    NEW = {"birch_log", "birch_planks", "gather_birch_log", "craft_birch_planks"}
    t3 = []
    for key in sorted(fas):
        task, seed, phase, step = key
        if task != "T3" or phase != 2:
            continue
        items = parse_nodes(fas[key]["context"])
        names = {n for n, _ in items}
        acts = {n: a for n, a in items if a is not None}
        old_mass = sum(a for n, a in acts.items()
                       if any(o in n for o in OLD))
        new_mass = sum(a for n, a in acts.items()
                       if any(v in n for v in NEW))
        t3.append({
            "seed": seed, "step": step,
            "n_old": sum(1 for n in names if any(o in n for o in OLD)),
            "n_new": sum(1 for n in names if any(v in n for v in NEW)),
            "old_mass": round(old_mass, 2), "new_mass": round(new_mass, 2),
            "old_gt_new": int(old_mass > new_mass) if acts else None,
            "action": fas[key]["action"], "ok": fas[key]["ok"],
            "detail": str(fas[key]["detail"])[:40],
            "context": fas[key]["context"][:400].replace("\n", " | "),
        })
    with open(os.path.join(OUT, "t3_reroute_analysis.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(t3[0].keys()))
        w.writeheader(); w.writerows(t3)
    print("t3_reroute_analysis.csv:", len(t3), "rows")
    # 按 step 聚合 old/new 质量
    bystep = collections.defaultdict(list)
    for r in t3:
        bystep[r["step"]].append(r)
    print("\nT3 phase2 逐步 old vs new 激活质量（fas_full, 20 seeds 均值）:")
    print(f"{'step':>4}{'n':>4}{'old_mass':>10}{'new_mass':>10}{'old>new 比例':>13}")
    for s in sorted(bystep):
        rs = bystep[s]
        om = st.mean(r["old_mass"] for r in rs)
        nm = st.mean(r["new_mass"] for r in rs)
        frac = st.mean(r["old_gt_new"] for r in rs if r["old_gt_new"] is not None) \
            if any(r["old_gt_new"] is not None for r in rs) else 0
        print(f"{s:>4}{len(rs):>4}{om:>10.2f}{nm:>10.2f}{frac:>13.2f}")


if __name__ == "__main__":
    main()
