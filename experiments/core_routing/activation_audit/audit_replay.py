# audit_replay.py — 只读审计（三）：确定性重放，测结构一致性与浓度演化
# 安全性：只创建**新的** in-memory 图与引擎实例，逐条喂入已落盘的动作/观察序列
# （与主跑同序），不改任何生产对象、不写任何生产文件。重放产物只落 audit 目录。
# 校验：重放得到的 Top-K 应与已落盘 context 的节点集一致（自证确定性）。
import json, os, re, csv, math, collections, statistics as st

ROOT = r"E:\Project\Fascinator\experiments\core_routing"
OUT = os.path.join(ROOT, "activation_audit")
os.environ.setdefault("PYTHONHASHSEED", "0")

import sys
sys.path.insert(0, r"E:\Project\Fascinator")
sys.path.insert(0, r"E:\Project\Fascinator\scripts")

RE_ACT = re.compile(r"^-\s+(\S+)\s+\(act\s+([\d.]+)\)\s*$")


def logged_nodes(ctx):
    return {m.group(1) for line in ctx.splitlines()
            if (m := RE_ACT.match(line.rstrip()))}


def gini(vals):
    v = sorted(x for x in vals if x > 0)
    if not v:
        return None
    n, s = len(v), sum(v)
    if s <= 0:
        return None
    cum = sum((i + 1) * x for i, x in enumerate(v))
    return round((2 * cum) / (n * s) - (n + 1) / n, 4)


def entropy(vals):
    s = sum(x for x in vals if x > 0)
    if s <= 0:
        return None
    ps = [x / s for x in vals if x > 0]
    return round(-sum(p * math.log2(p) for p in ps), 4)


def topk_stats(kg, k=8):
    """对当前图状态计算 Top-K 集中度与结构一致性（只读）。"""
    nodes = [nd for nd in kg.nodes.values()
             if float(getattr(nd, "activation", 0.0) or 0.0) > 0]
    nodes.sort(key=lambda nd: -float(getattr(nd, "activation", 0.0) or 0.0))
    top = nodes[:k]
    vals = [float(getattr(nd, "activation", 0.0) or 0.0) for nd in top]
    ids = {nd.id for nd in top}
    # 诱导子图
    edges = [e for e in (getattr(kg, "edges", []) or [])
             if getattr(e, "src", None) in ids and getattr(e, "dst", None) in ids]
    n = len(ids)
    max_e = n * (n - 1) / 2 if n > 1 else 0
    dens = round(len(edges) / max_e, 4) if max_e else None
    # 连通分量（无向）
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for e in edges:
        a, b = find(e.src), find(e.dst)
        if a != b:
            parent[a] = b
    comps = len({find(i) for i in ids})
    deg = collections.Counter()
    for e in edges:
        deg[e.src] += 1
        deg[e.dst] += 1
    isolated = sum(1 for i in ids if deg[i] == 0)
    rel_types = collections.Counter(
        str(getattr(e, "relation", "?")) for e in edges)
    return {
        "n_active": len(nodes), "n_top": n,
        "top1": round(vals[0], 3) if vals else None,
        "topk_mass": round(sum(vals), 3),
        "total_mass": round(sum(float(getattr(nd, "activation", 0) or 0)
                                for nd in nodes), 3),
        "gini_topk": gini(vals), "entropy_topk": entropy(vals),
        "n_distinct_act": len({round(v, 2) for v in vals}),
        "subgraph_edges": len(edges), "density": dens,
        "n_components": comps if n else 0, "n_isolated": isolated,
        "relation_types": ";".join(f"{r}:{c}" for r, c in
                                   rel_types.most_common(4)),
        "top_ids": ";".join(nd.id for nd in top),
    }


def replay_trajectory(cond, task, seed, decisions, diffusion_on=True, k=8):
    """用已落盘的动作序列重放图演化，逐决策步记录统计。"""
    import run_exp_routing as R
    fas = R.FASContext()
    fas.use_diffusion = diffusion_on
    T = R.TASKS[task]
    rows = []
    # 按 phase 分组重放（phase 切换时 runner 会重置世界，但图是连续的）
    by_phase = collections.defaultdict(list)
    for d in decisions:
        by_phase[d["phase"]].append(d)
    step_global = 0
    for phase in sorted(by_phase):
        for d in sorted(by_phase[phase], key=lambda x: x["step"]):
            # 决策前的图状态 = 上一轮 ingest 之后
            st_before = topk_stats(fas.kg, k)
            rel = fas.context(T.phase_goal(phase)) if fas.kg.nodes else ""
            # 喂入本步经验（与主跑同序：先决策 → 后 ingest）
            fas.ingest(d["obs"], d["action"], str(d["detail"]),
                       T.goal_entities)
            fas.step_dynamics()
            st_after = topk_stats(fas.kg, k)
            rows.append({
                "condition": cond, "task": task, "seed": seed,
                "phase": phase, "step": d["step"],
                "step_global": step_global,
                "pre_n_top": st_before["n_top"],
                "pre_gini": st_before["gini_topk"],
                "pre_top1": st_before["top1"],
                "post_n_top": st_after["n_top"],
                "post_top1": st_after["top1"],
                "post_mass": st_after["topk_mass"],
                "post_total_mass": st_after["total_mass"],
                "post_gini": st_after["gini_topk"],
                "post_entropy": st_after["entropy_topk"],
                "post_distinct": st_after["n_distinct_act"],
                "post_density": st_after["density"],
                "post_components": st_after["n_components"],
                "post_isolated": st_after["n_isolated"],
                "post_rels": st_after["relation_types"],
                "n_active": st_after["n_active"],
                "logged_set_matches_replay": int(
                    logged_nodes(d["context"]) ==
                    set(st_after["top_ids"].split(";")) if d["context"].strip()
                    and "focus" in d["context"] else None),
                "action": d["action"], "ok": d["ok"],
            })
            step_global += 1
    return rows


def main():
    import itertools
    out_rows = []
    # 抽样：fas_full 每任务 4 seeds（共 16）；D-noact 每任务 2 seeds（8）
    plan = [("fas_full", True, {"T1": [1, 2, 3, 4], "T2": [1, 2, 3, 4],
                                "T3": [1, 2, 3, 4], "T4": [1, 2, 3, 4]}),
            ("D-noact", False, {"T2": [1, 2], "T3": [1, 2]})]
    for cond, diff_on, spec in plan:
        path = os.path.join(ROOT, "ablations" if cond.startswith("D-") else cond,
                            "raw_results.jsonl")
        if not os.path.exists(path):
            print("skip (no data):", cond); continue
        bucket = collections.defaultdict(list)
        for line in open(path, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") == "decision" and d.get("condition") == cond:
                bucket[(d["task"], d["seed"])].append(d)
        for task, seeds in spec.items():
            for s in seeds:
                recs = bucket.get((task, s))
                if not recs:
                    continue
                rows = replay_trajectory(cond, task, s, recs,
                                         diffusion_on=diff_on)
                out_rows.extend(rows)
                print(f"replayed {cond} {task} seed{s}: {len(rows)} steps")
    fn = os.path.join(OUT, "replay_structure.csv")
    with open(fn, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader(); w.writerows(out_rows)
    print("replay_structure.csv:", len(out_rows), "rows")
    # 校准：logged vs replay 节点集一致率
    ok = [r["logged_set_matches_replay"] for r in out_rows
          if r["logged_set_matches_replay"] is not None]
    if ok:
        print("replay↔logged Top-K 集一致率: %.1f%% (n=%d)"
              % (100 * sum(ok) / len(ok), len(ok)))


if __name__ == "__main__":
    main()
