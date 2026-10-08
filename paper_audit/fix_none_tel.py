# fix_none_tel.py — baseline 的 graph telemetry 为 None 时安全聚合
import io
import ast

p = r"scripts\run_exp_routing_v2.py"
t = io.open(p, encoding="utf-8").read()

old = '''        "step0_n_nodes": len(trace[0]["selected_nodes"]) if trace else 0,
        "graph_edges_final": trace[-1]["graph_edges"] if trace else 0,
        "activated_edges_mean": round(
            sum(t["activated_edges"] for t in trace) / max(len(trace), 1), 2),
    }'''
new = '''        "step0_n_nodes": len(trace[0]["selected_nodes"]) if trace else 0,
        "graph_edges_final": trace[-1]["graph_edges"] if trace else 0,
        "activated_edges_mean": (
            round(sum(t["activated_edges"] for t in trace
                      if t["activated_edges"] is not None)
                  / max(sum(1 for t in trace
                            if t["activated_edges"] is not None), 1), 2)
            if any(t["activated_edges"] is not None for t in trace) else None),
    }'''
assert old in t, "anchor"
t = t.replace(old, new, 1)

# graph_edges_final 对 baseline 为 None
t = t.replace('"graph_edges_final": trace[-1]["graph_edges"] if trace else 0,',
              '"graph_edges_final": (trace[-1]["graph_edges"] if trace and trace[-1]["graph_edges"] is not None else None),', 1)

io.open(p, "w", encoding="utf-8").write(t)
ast.parse(t)
print("fixed None telemetry handling; syntax OK")
