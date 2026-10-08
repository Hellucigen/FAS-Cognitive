# combo_worker.py — 组合清单并行 worker（正式 campaign 加速用）
# 输入 slice 文件：每行 "cond task seed"；跑完各自写独立 out 目录。
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(1, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
if os.getcwd() != os.path.dirname(os.path.dirname(os.path.abspath(__file__))):
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_exp_routing_v2 as R
from sandbox_lab import SandboxWorld


def main():
    slice_file, worker_id, out_base = sys.argv[1], sys.argv[2], sys.argv[3]
    combos = []
    for line in open(slice_file, encoding="utf-8"):
        parts = line.split()
        if len(parts) == 3:
            combos.append((parts[0], parts[1], int(parts[2])))
    out_dir = os.path.join(out_base, f"w{worker_id}")
    os.makedirs(out_dir, exist_ok=True)
    log = io.open(os.path.join(out_dir, "raw_results.jsonl"), "a",
                  encoding="utf-8")
    llm = R.LLMHead()
    done = 0
    pending = list(combos)
    pass_no = 0
    while pending:
        combo = pending
        pending = []
        pass_no += 1
        for cond, task_key, seed in combo:
          try:
            m = R.run_task(cond, task_key, seed, llm, log)
          except (R.InvariantViolation, R.CoreModuleError) as e:
            m = {"condition": cond, "task": task_key, "seed": seed,
                 "success": 0, "harness_valid": False,
                 "failure_class": "INVALID_HARNESS_RUN",
                 "failure_reason": str(e)[:200]}
            log.write(json.dumps({"type": "task_result", **m},
                                 ensure_ascii=False) + "\n")
            log.flush()
          except Exception as e:
            # 基建类（网络/内存/模型加载）→ 记录 + 末轮重试一次
            m = {"condition": cond, "task": task_key, "seed": seed,
                 "success": 0, "harness_valid": True,
                 "failure_class": "INFRASTRUCTURE_FAILURE",
                 "failure_reason": str(e)[:200]}
            log.write(json.dumps({"type": "task_result", **m},
                                 ensure_ascii=False) + "\n")
            log.flush()
            if pass_no == 1:
                pending.append((cond, task_key, seed))
            done += 1 if False else 0
            continue
          done += 1
          print(f"[w{worker_id}] {cond} {task_key} seed{seed}: "
                f"succ={m.get('success')} class={m.get('failure_class','none')}",
                flush=True)
    print(f"[w{worker_id}] slice complete: {done} runs (+retries)")
    log.close()
    return


def _old_main():
    done = 0
    for cond, task_key, seed in combos:
        try:
            m = R.run_task(cond, task_key, seed, llm, log)
        except R.InvariantViolation as e:
            m = {"condition": cond, "task": task_key, "seed": seed,
                 "success": 0, "harness_valid": False,
                 "failure_class": "INVALID_HARNESS_RUN",
                 "failure_reason": str(e)[:200]}
            log.write(json.dumps({"type": "task_result", **m},
                                 ensure_ascii=False) + "\n")
            log.flush()
        except R.InfrastructureError as e:
            m = {"condition": cond, "task": task_key, "seed": seed,
                 "success": 0, "harness_valid": True,
                 "failure_class": "INFRASTRUCTURE_FAILURE",
                 "failure_reason": str(e)[:200]}
            log.write(json.dumps({"type": "task_result", **m},
                                 ensure_ascii=False) + "\n")
            log.flush()
        except R.CoreModuleError as e:
            m = {"condition": cond, "task": task_key, "seed": seed,
                 "success": 0, "harness_valid": False,
                 "failure_class": "INVALID_HARNESS_RUN",
                 "failure_reason": str(e)[:200]}
            log.write(json.dumps({"type": "task_result", **m},
                                 ensure_ascii=False) + "\n")
            log.flush()
        done += 1
        print(f"[w{worker_id}] {cond} {task_key} seed{seed}: "
              f"succ={m.get('success')} class={m.get('failure_class','none')}",
              flush=True)
    print(f"[w{worker_id}] slice complete: {done} runs")
    log.close()


if __name__ == "__main__":
    main()
