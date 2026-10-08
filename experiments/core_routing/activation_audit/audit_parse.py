# audit_parse.py — 只读审计：从已落盘 trajectory 解析 FAS 焦点集并计算统计量
# 不修改任何生产代码；不重跑任务；只读 raw_results.jsonl。
import json, os, re, csv, math, collections, statistics as st

ROOT = r"E:\Project\Fascinator\experiments\core_routing"
CONDS = ["fas_full", "llm_direct", "llm_history", "llm_rag", "random_ctx",
         "D-noact", "D-nodemand", "D-flat"]
OUT = os.path.join(ROOT, "activation_audit")

# 任务→（各 phase 的目标词表）用于 relevance 标注（程序化，非人工）
TASK_VOCAB = {
    "T1": {1: {"oak_log", "oak_planks"}, 2: {"oak_log", "oak_planks"}},
    "T2": {1: {"oak_log", "oak_planks"}},
    "T3": {1: {"oak_log", "oak_planks"}, 2: {"birch_log", "birch_planks"}},
    "T4": {1: {"oak_log", "oak_planks", "bread", "hunger"}},
}
# 各 phase 的"过时"词（先前 phase 相关、当前 phase 不再需要）
TASK_STALE = {
    "T3": {2: {"oak_log", "oak_planks"}},
}
NOISE_TOKENS = {"unparseable_reply", "wait", "none", "ok"}


def parse_context(ctx):
    """从 context 文本提取 (node_id, activation) 列表。"""
    out = []
    for line in ctx.splitlines():
        line = line.strip()
        m = re.match(r"-\s+(\S+)\s+\(act\s+([\d.]+)\)", line)
        if m:
            out.append((m.group(1), float(m.group(2))))
    return out


def gini(vals):
    if not vals or sum(vals) <= 0:
        return None
    v = sorted(vals)
    n = len(v)
    cum = sum((i + 1) * x for i, x in enumerate(v))
    return round((2 * cum) / (n * sum(v)) - (n + 1) / n, 4)


def entropy(vals):
    s = sum(vals)
    if s <= 0:
        return None
    ps = [x / s for x in vals if x > 0]
    return round(-sum(p * math.log2(p) for p in ps), 4)


def node_kind(nid):
    if nid.startswith("实体:"):
        return "entity"
    if nid.startswith("动作:"):
        return "action"
    if nid.startswith("结果:"):
        return "result"
    return "other"


def node_name(nid):
    return nid.split(":", 1)[1] if ":" in nid else nid


def classify(nid, task, phase):
    name = node_name(nid)
    if name in NOISE_TOKENS:
        return "noise"
    vocab = TASK_VOCAB.get(task, {}).get(phase, set())
    stale = TASK_STALE.get(task, {}).get(phase, set())
    if name in vault_safe(stale):
        return "stale"
    if any(v in name for v in vault_safe(vocab)):
        return "relevant"
    return "irrelevant"


def vault_safe(s):
    return s or set()


def main():
    traj_rows, act_rows = [], []
    PATHS = {}
    for cond in CONDS:
        for cand in (cond, "ablations"):
            p = os.path.join(ROOT, cand, "raw_results.jsonl")
            if os.path.exists(p):
                PATHS.setdefault(cand, p)
    for cond in CONDS:
        # 主条件在各自目录；消融三条件同在 ablations/
        if cond.startswith("D-"):
            path = os.path.join(ROOT, "ablations", "raw_results.jsonl")
        else:
            path = os.path.join(ROOT, cond, "raw_results.jsonl")
        if not os.path.exists(path):
            continue
        # 按 (task, seed) 分组 decision
        groups = collections.defaultdict(list)
        for line in open(path, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") == "decision" and d.get("condition") == cond:
                groups[(d["task"], d["seed"])].append(d)
        for (task, seed), recs in groups.items():
            recs.sort(key=lambda r: (r["phase"], r["step"]))
            prev_nodes = set()
            for r in recs:
                items = parse_context(r["context"])
                names = [n for n, _ in items]
                vals = [a for _, a in items]
                labels = [classify(n, task, r["phase"]) for n in names]
                kinds = [node_kind(n) for n in names]
                n_ent = sum(1 for k in kinds if k == "entity")
                n_act = sum(1 for k in kinds if k == "action")
                n_res = sum(1 for k in kinds if k == "result")
                top_overlap = (len(set(names) & prev_nodes) /
                               max(len(set(names) | prev_nodes), 1)) if prev_nodes else None
                act_rows.append({
                    "condition": cond, "task": task, "seed": seed,
                    "phase": r["phase"], "step": r["step"],
                    "n_nodes": len(items),
                    "top1": vals[0] if vals else None,
                    "topk_mass": round(sum(vals), 3) if vals else 0,
                    "top5_mass": round(sum(vals[:5]), 3) if vals else 0,
                    "gini": gini(vals), "entropy": entropy(vals),
                    "n_distinct_act": len(set(vals)),
                    "n_entity": n_ent, "n_action": n_act, "n_result": n_res,
                    "n_relevant": labels.count("relevant"),
                    "n_irrelevant": labels.count("irrelevant"),
                    "n_stale": labels.count("stale"),
                    "n_noise": labels.count("noise"),
                    "precision_relevant": round(labels.count("relevant") /
                                                max(len(labels), 1), 3),
                    "jaccard_prev": None if top_overlap is None else round(top_overlap, 3),
                    "action_chosen": r["action"], "ok": r["ok"],
                    "nodes": ";".join(names),
                })
                prev_nodes = set(names)
                traj_rows.append({"condition": cond, "task": task, "seed": seed,
                                  "phase": r["phase"], "step": r["step"],
                                  "n_nodes": len(items), "action": r["action"],
                                  "ok": r["ok"]})
    with open(os.path.join(OUT, "activation_statistics.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(act_rows[0].keys()))
        w.writeheader()
        w.writerows(act_rows)
    print("activation_statistics.csv:", len(act_rows), "rows")
    # 轨迹汇总
    with open(os.path.join(OUT, "trajectory_summary.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(traj_rows[0].keys()))
        w.writeheader()
        w.writerows(traj_rows)
    print("trajectory_summary.csv:", len(traj_rows), "rows")


if __name__ == "__main__":
    main()
