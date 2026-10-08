# merge_routing.py — 合并各条件目录的 summary.csv（按 condition,task,seed 去重留最后）
import csv, glob, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rows = {}
for f in glob.glob(os.path.join(ROOT, "experiments", "core_routing", "*", "summary.csv")):
    for r in csv.DictReader(open(f, encoding="utf-8")):
        key = (r["condition"], r["task"], int(r["seed"] or 0))
        rows[key] = r            # 后写覆盖先写
out = os.path.join(ROOT, "experiments", "core_routing", "summary.csv")
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["condition","task","seed","success","decisions",
        "target_utilization","distractor_rate","t_reroute","obsolete_actions",
        "wasted_eats","retrieval_precision","prompt_tokens","completion_tokens","efficiency"])
    w.writeheader()
    for k in sorted(rows):
        w.writerow(rows[k])
print("merged:", len(rows), "rows ->", out)
