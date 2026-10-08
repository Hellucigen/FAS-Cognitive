# launch_remaining2.py — 补跑剩余 80 组合（8 路并发，为系统预留资源）
import glob
import json
import os
import subprocess
import sys
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "experiments", "core_routing_v2", "campaign")
PY = sys.executable

have = set()
for f in glob.glob(os.path.join(BASE, "*", "raw_results.jsonl")):
    for line in open(f, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") == "task_result" and d.get("harness_valid", True):
            # 已有 harness-invalid 结果的也重跑一次（记录分类），harness-valid 的跳过
            if d.get("failure_class", "none") in ("none", "INFRASTRUCTURE_FAILURE"):
                have.add((d["condition"], d["task"], int(d["seed"])))
            elif d.get("failure_class") == "INFRASTRUCTURE_FAILURE":
                have.discard((d["condition"], d["task"], int(d["seed"])))

manifest = []
for c in ["fas_full", "llm_direct", "llm_history", "llm_rag", "random_ctx"]:
    for t in ["T1", "T2", "T3", "T4"]:
        for s in range(1, 21):
            manifest.append((c, t, s))
for c in ["D-noact", "D-nodemand", "D-flat"]:
    for t in ["T2", "T3"]:
        for s in range(1, 21):
            manifest.append((c, t, s))

remaining = [m for m in manifest if m not in have]
print(f"manifest={len(manifest)} have={len(have & set(manifest))} remaining={len(remaining)}")
byc = collections.Counter(c for c, t, s in remaining)
print("分布:", dict(byc))

NW = 8
slices = [[] for _ in range(NW)]
for i, m in enumerate(remaining):
    slices[i % NW].append(m)

procs = []
nfiles = 0
for i, sl in enumerate(slices):
    if not sl:
        continue
    fn = os.path.join(BASE, f"slice2_w{i:02d}.txt")
    with open(fn, "w", encoding="utf-8") as f:
        for c, t, s in sl:
            f.write(f"{c} {t} {s}\n")
    procs.append(subprocess.Popen(
        [PY, "-X", "utf8", os.path.join(ROOT, "scripts", "combo_worker.py"),
         fn, f"r2_{i:02d}", BASE],
        stdout=open(os.path.join(BASE, f"log_r2_{i:02d}.txt"), "w",
                    encoding="utf-8"),
        stderr=subprocess.STDOUT))
    nfiles += 1
print(f"launched {nfiles} workers (8-way)")
for p in procs:
    p.wait()
print("ROUND2 DONE")
