# fix_graph_none.py — graph_edges_final 对 baseline 为 None 时的比较防护
import io
import ast

p = r"scripts\run_exp_routing_v2.py"
t = io.open(p, encoding="utf-8").read()
old = '''        if result["graph_edges_final"] <= 0 and not is_baseline:'''
new = '''        if (result["graph_edges_final"] or 0) <= 0 and not is_baseline:'''
assert old in t
t = t.replace(old, new, 1)
io.open(p, "w", encoding="utf-8").write(t)
ast.parse(t)
print("fixed graph_edges None guard; syntax OK")
