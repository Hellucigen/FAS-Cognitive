# finalize_v2.py — 正式 campaign 收尾：合并 + v1 指标集导出 + 完整性核验
import json, glob, csv, re, collections, statistics as st

ROOT = r"E:\Project\Fascinator"
BASE = ROOT + r"\experiments\core_routing_v2\campaign"

# ── 1. 收集全部 task_result（按 key 保留最后写入 = 重跑优先）──
results = {}
decisions = collections.defaultdict(list)
for f in glob.glob(os.path.join(BASE, "*", "raw_results.jsonl")) \
        if (os := __import__("os")) else []:
    for line in open(f, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") == "task_result":
            key = (d["condition"], d["task"], int(d["seed"]))
            results[key] = d
        elif d.get("type") == "decision":
            dkey = (d["condition"], d["task"], int(d["seed"]))
            decisions[dkey].append(d)

# 逐轨迹排序
for k in decisions:
    decisions[k].sort(key=lambda r: (r["phase"], r["step"]))

print("task_results:", len(results), " decision traces:", len(decisions))

# ── 2. 完整性分类 ──
integ = collections.Counter()
for k, r in results.items():
    integ[r.get("failure_class", "none")] += 1
print("integrity:", dict(integ))
invalid = [k for k, r in results.items()
           if r.get("failure_class") == "INVALID_HARNESS_RUN"]
infra = [k for k, r in results.items()
         if r.get("failure_class") == "INFRASTRUCTURE_FAILURE"]
print("INVALID_HARNESS_RUN:", len(invalid), invalid[:6])
print("INFRASTRUCTURE_FAILURE:", len(infra), infra[:6])

# ── 3. v1 指标集导出（从 decision trace 忠实重derive）──
TASK_STALE = {"T3": {2: {"oak"}}}
GOAL_TAGS = {"T1": ("oak",), "T2": ("oak",), "T3": ("birch", "oak"),
             "T4": ("oak", "bread", "hunger")}

def is_goal_action(a, task):
    return any(tag in a.replace("_", "") or tag in a for tag in GOAL_TAGS[task])

summary = {}
for key, r in results.items():
    cond, task, seed = key
    tr = decisions.get(key, [])
    acts = [t["action"] for t in tr]
    p2 = [t for t in tr if t.get("phase") == 2]
    # efficiency
    eff = round(r["success"] / max(r["prompt_tokens"] / 1000.0, 0.001), 4)
    row = {"condition": cond, "mode": r.get("mode", ""), "task": task,
           "seed": seed, "success": r["success"],
           "decisions": r["decisions"], "latency_s": r.get("latency_s"),
           "prompt_tokens": r["prompt_tokens"],
           "completion_tokens": r["completion_tokens"],
           "efficiency": eff,
           "harness_valid": r.get("harness_valid", True),
           "failure_class": r.get("failure_class", "none"),
           "first_action": acts[0] if acts else None,
           "step0_n_nodes": r.get("step0_n_nodes"),
           "graph_edges_final": r.get("graph_edges_final"),
           "activated_edges_mean": r.get("activated_edges_mean"),
           "core_exceptions": r.get("core_exceptions")}
    # distractor / target utilization
    if task in GOAL_TAGS:
        goal_acts = [a for a in acts if is_goal_action(a, task)]
        row["target_utilization"] = round(len(goal_acts) / max(len(acts), 1), 3)
        row["distractor_rate"] = round(
            len([a for a in acts if a not in goal_acts and a != "wait"])
            / max(len(acts), 1), 3)
    # T3 重路由与陈旧行为
    if task == "T3":
        rr = None
        for i, t in enumerate(p2):
            if "birch" in t["action"] and t["ok"]:
                rr = i
                break
        row["t_reroute"] = rr
        row["obsolete_actions"] = sum(1 for t in p2 if "oak" in t["action"])
        # 旧/新焦点质量（phase2 每步，来自 selected_nodes+selected_acts）
        omass, nmass = [], []
        for t in p2:
            om = sum(a for n, a in zip(t["selected_nodes"], t["selected_acts"])
                     if a is not None and "oak" in n)
            nm = sum(a for n, a in zip(t["selected_nodes"], t["selected_acts"])
                     if a is not None and "birch" in n)
            omass.append(round(om, 2)); nmass.append(round(nm, 2))
        row["old_mass_p2_s0"] = omass[0] if omass else None
        row["new_mass_p2_s0"] = nmass[0] if nmass else None
    # T4 浪费进食
    if task == "T4":
        row["wasted_eats"] = sum(1 for t in tr if "wasted_eat" in str(t["detail"]))
    # 检索精度（D 族：selected 节点中 relevant 占比）
    if cond in ("fas_full", "D-noact", "D-nodemand", "D-flat"):
        rel, tot = 0, 0
        for t in tr:
            for n in t["selected_nodes"]:
                tot += 1
                nm = n.split(":", 1)[-1]
                if any(tag in nm for tag in GOAL_TAGS[task]):
                    rel += 1
        row["retrieval_precision"] = round(rel / max(tot, 1), 3)
    summary[key] = row

FIELDS = list(next(iter(summary.values())).keys())
with open(BASE + r"\summary.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    for k in sorted(summary):
        w.writerow(summary[k])
print("summary.csv:", len(summary), "rows")

# 机制 telemetry 聚合（D 族）
tel_rows = []
for key, tr in decisions.items():
    cond, task, seed = key
    if cond not in ("fas_full", "D-noact", "D-nodemand", "D-flat"):
        continue
    vals = [a for t in tr for a in t["selected_acts"] if a is not None]
    ae = [t["activated_edges"] for t in tr if t["activated_edges"] is not None]
    tel_rows.append({
        "condition": cond, "task": task, "seed": seed,
        "n_decisions": len(tr),
        "graph_edges_mean": round(st.mean([t["graph_edges"] for t in tr
                                           if t["graph_edges"] is not None]), 2)
        if any(t["graph_edges"] is not None for t in tr) else None,
        "activated_edges_mean": round(st.mean(ae), 2) if ae else None,
        "activated_nodes_mean": round(st.mean(
            [t["activated_nodes"] for t in tr
             if t["activated_nodes"] is not None]), 2)
        if any(t["activated_nodes"] is not None for t in tr) else None,
        "n_selected_total": len(vals),
        "n_distinct_act": len({round(v, 2) for v in vals}),
        "act_precision": round(sum(
            1 for t in tr for n, a in zip(t["selected_nodes"],
                                          t["selected_acts"])
            if a is not None and any(tag in n.split(":")[-1]
                                     for tag in GOAL_TAGS.get(task, ()))
            ) / max(len(vals), 1), 3) if vals else None,
    })
with open(BASE + r"\mechanism_telemetry.csv", "w", newline="",
          encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(tel_rows[0].keys()))
    w.writeheader(); w.writerows(tel_rows)
print("mechanism_telemetry.csv:", len(tel_rows), "rows")
