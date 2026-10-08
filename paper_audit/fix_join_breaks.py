# fix_join_breaks.py — 修复源码中 'return "' + 换行 + '".join' 的断串
import io
import ast

P = r"scripts\run_exp_routing_v2.py"
t = io.open(P, encoding="utf-8").read()
lines = t.split("\n")
out = []
i = 0
fixed = 0
target_suffix = 'return '
while i < len(lines):
    l = lines[i]
    stripped = l.rstrip()
    if stripped.endswith(target_suffix + '"') and i + 1 < len(lines) \
            and lines[i + 1].startswith('".join'):
        out.append(stripped + '\\n' + lines[i + 1])
        fixed += 1
        i += 2
        continue
    out.append(l)
    i += 1
t = "\n".join(out)
try:
    ast.parse(t)
except SyntaxError as e:
    print("STILL BROKEN at line", e.lineno)
    raise SystemExit(1)
io.open(P, "w", encoding="utf-8").write(t)
print("fixed", fixed, "broken joins; syntax OK")
