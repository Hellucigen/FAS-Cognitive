# launch_remaining_parallel.py — 扫描已完成组合 → 生成剩余清单 → 24 路并行
import glob
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "experiments", "core_routing_v2", "campaign")
PY = sys.executable

CONDS_MAIN = ["fas_full", "llm_direct", "llm_history", "llm_rag", "random_ctx"]
CONDS_ABL = ["D-noact", "D-nodemand", "D-flat"]
TASKS_MAIN = ["T1", "T2", "T3", "T4"]
TASKS_ABL = ["T2", "T3"]
SEEDS = list(range(1, 21))

done = set()
# 两个来源：campaign 目录（并行首波）+ 各条件旧目录（若存在）
patterns = [os.path.join(BASE, "*", "raw_results.jsonl"),
            os.path.join(ROOT, "experiments", "core_routing_v2_smoke", "*",
                         "raw_results.jsonl")]
for f in glob.glob(os.path.join(BASE, "*", "raw_results.jsonl")):
    for line in open(f, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") == "task_result":
            done.add((d["condition"], d["task"], int(d["seed"])))

manifest = []
for c in CONDS_MAIN:
    for t in TASKS_MAIN:
        for s in SEEDS:
            manifest.append((c, t, s))
for c in CONDS_ABL:
    for t in TASKS_ABL:
        for s in SEEDS:
            manifest.append((c, t, s))

remaining = [m for m in manifest if m not in done]
print(f"manifest={len(manifest)} done={len(done)} remaining={len(remaining)}")

NW = int(sys.argv[1]) if len(sys.argv) > 1 else 24
slices = [[] for _ in range(NW)]
for i, m in enumerate(remaining):
    slices[i % NW].append(m)

slice_files = []
for i, sl in enumerate(slices):
    if not sl:
        continue
    fn = os.path.join(BASE, f"slice_w{i:02d}.txt")
    with open(fn, "w", encoding="utf-8") as f:
        for c, t, s in sl:
            f.write(f"{c} {t} {s}\n")
    slice_files.append((fn, i))

procs = []
for fn, i in slice_files:
    p = subprocess.Popen(
        [PY, "-X", "utf8", os.path.join(ROOT, "scripts", "combo_worker.py"),
         fn, f"{i:02d}", BASE],
        stdout=open(os.path.join(BASE, f"log_w{i:02d}.txt"), "w",
                    encoding="utf-8"),
        stderr=subprocess.STDOUT)
    procs.append(p)
print(f"launched {len(procs)} workers")
for p in procs:
    p.wait()
print("ALL WORKERS DONE")
