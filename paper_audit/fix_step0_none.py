# fix_step0_none.py — step0 对 baseline 可为 None（selected 空列表合法）
import io
import ast

p = r"scripts\run_exp_routing_v2.py"
t = io.open(p, encoding="utf-8").read()
old = '''        elif result["step0_n_nodes"] <= 0:'''
new = '''        elif (result["step0_n_nodes"] or 0) <= 0 and not cond.startswith("llm") \\
                and cond != "random_ctx":'''
assert old in t
t = t.replace(old, new, 1)
io.open(p, "w", encoding="utf-8").write(t)
ast.parse(t)
print("fixed step0 None scope; syntax OK")
