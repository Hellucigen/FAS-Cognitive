# 一次性工具：对 renderInternalModulators 的 JS 做括号/引号平衡检查（P12 前端改动后）
import re
import sys

s = open("index.html", encoding="utf-8").read()
m = re.search(r"function renderInternalModulators\(ms\)\{.*?\n\}", s, re.S)
if not m:
    print("function not found")
    sys.exit(1)
code = m.group(0)
# 先剥掉 // 行注释（注释文本里有直引号，会把朴素扫描器带偏）
code = "\n".join(ln.split("//")[0] if not re.search(r"https?://", ln) else ln
                 for ln in code.splitlines())
BS = chr(92)
bal = {"(": 0, "{": 0, "[": 0}
pairs = {")": "(", "}": "{", "]": "["}
q = None
esc = False
for c in code:
    if q:
        if esc:
            esc = False
        elif c == BS:
            esc = True
        elif c == q:
            q = None
    elif c in ("'", '"'):
        q = c
    elif c in bal:
        bal[c] += 1
    elif c in pairs:
        bal[pairs[c]] -= 1
print("end-in-string:", repr(q))
print("balance:", bal)
sys.exit(0 if all(v == 0 for v in bal.values()) and q is None else 1)
