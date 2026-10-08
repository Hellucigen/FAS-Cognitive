# patch_worker_resilient.py — combo_worker 加宽泛容错 + 单次重试（基建类）
import io
import ast

p = r"scripts\combo_worker.py"
t = io.open(p, encoding="utf-8").read()

old = """    done = 0
    for cond, task_key, seed in combos:
        try:
            m = R.run_task(cond, task_key, seed, llm, log)
        except R.InvariantViolation as e:"""
new = """    done = 0
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
                                 ensure_ascii=False) + "\\n")
            log.flush()
          except Exception as e:
            # 基建类（网络/内存/模型加载）→ 记录 + 末轮重试一次
            m = {"condition": cond, "task": task_key, "seed": seed,
                 "success": 0, "harness_valid": True,
                 "failure_class": "INFRASTRUCTURE_FAILURE",
                 "failure_reason": str(e)[:200]}
            log.write(json.dumps({"type": "task_result", **m},
                                 ensure_ascii=False) + "\\n")
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
        except R.InvariantViolation as e:"""
assert old in t, "worker main anchor"
t = t.replace(old, new, 1)

io.open(p, "w", encoding="utf-8").write(t)
ast.parse(t)
print("worker resilient patch applied; syntax OK")
