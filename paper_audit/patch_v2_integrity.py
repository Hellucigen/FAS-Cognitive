# patch_v2_integrity.py — 给 v2 runner 加完整性分类与 telemetry 字段
import io
import ast

p = r"scripts\run_exp_routing_v2.py"
t = io.open(p, encoding="utf-8").read()
BS = "\\n"   # 目标文件中需要的两字符转义

# 1) trace 记录加 harness_valid
old = '''                "routing": routing,
                "exception_count": core_exceptions,
            })'''
new = '''                "routing": routing,
                "exception_count": core_exceptions,
                "harness_valid": True,
            })'''
assert old in t, "trace anchor"
t = t.replace(old, new, 1)

# 2) task_result 加 latency / harness_valid / failure_class
old2 = '''        "ctx_chars_mean": round(sum(ctx_sizes) / max(len(ctx_sizes), 1), 1),
        "core_exceptions": core_exceptions,'''
new2 = '''        "ctx_chars_mean": round(sum(ctx_sizes) / max(len(ctx_sizes), 1), 1),
        "latency_s": round(time.time() - t_start, 1),
        "harness_valid": True,
        "failure_class": "none",
        "failure_reason": "",
        "core_exceptions": core_exceptions,'''
assert old2 in t, "result anchor"
t = t.replace(old2, new2, 1)

# 3) fail_fast → 分类标记而非裸抛
old3 = '''    if fail_fast:
        if result["graph_edges_final"] <= 0:
            raise InvariantViolation(f"{cond}: graph_edges == 0 at end")
        if result["core_exceptions"] != 0:
            raise InvariantViolation(f"{cond}: core_exceptions != 0")
        if result["step0_n_nodes"] <= 0:
            raise InvariantViolation(f"{cond}: step-0 context empty")'''
new3 = ('''    if fail_fast:
        reason = None
        if result["graph_edges_final"] <= 0 and not is_baseline:
            reason = "graph_edges == 0 at end"
        elif result["core_exceptions"] != 0:
            reason = f"core_exceptions={result['core_exceptions']}"
        elif result["step0_n_nodes"] <= 0:
            reason = "step-0 context empty"
        if reason:
            result["harness_valid"] = False
            result["failure_class"] = "INVALID_HARNESS_RUN"
            result["failure_reason"] = reason
            log.write(json.dumps({"type": "task_result", **result},
                                 ensure_ascii=False) + "''' + BS + '")\n' +
        '''            log.flush()
            raise InvariantViolation(f"{cond} {task_key} seed{seed}: {reason}")''')
assert old3 in t, "fail_fast anchor"
t = t.replace(old3, new3, 1)

# 4) main() per-run 分类捕获
old4 = '''            for tk in tasks:
                m = run_task(cond, tk, seed, llm, log)
                rows.append(m)'''
new4 = '''            for tk in tasks:
                try:
                    m = run_task(cond, tk, seed, llm, log)
                except InvariantViolation as e:
                    m = {"condition": cond, "task": tk, "seed": seed,
                         "success": 0, "harness_valid": False,
                         "failure_class": "INVALID_HARNESS_RUN",
                         "failure_reason": str(e)[:200]}
                    log.write(json.dumps({"type": "task_result", **m},
                                         ensure_ascii=False) + "''' + BS + '''")
                    log.flush()
                except InfrastructureError as e:
                    m = {"condition": cond, "task": tk, "seed": seed,
                         "success": 0, "harness_valid": True,
                         "failure_class": "INFRASTRUCTURE_FAILURE",
                         "failure_reason": str(e)[:200]}
                    log.write(json.dumps({"type": "task_result", **m},
                                         ensure_ascii=False) + "''' + BS + '''")
                    log.flush()
                except CoreModuleError as e:
                    m = {"condition": cond, "task": tk, "seed": seed,
                         "success": 0, "harness_valid": False,
                         "failure_class": "INVALID_HARNESS_RUN",
                         "failure_reason": str(e)[:200]}
                    log.write(json.dumps({"type": "task_result", **m},
                                         ensure_ascii=False) + "''' + BS + '''")
                    log.flush()
                rows.append(m)'''
assert old4 in t, "main catch anchor"
t = t.replace(old4, new4, 1)

io.open(p, "w", encoding="utf-8").write(t)
ast.parse(t)
print("integrity patch applied + syntax OK")
