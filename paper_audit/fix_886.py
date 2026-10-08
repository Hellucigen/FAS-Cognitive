# fix_886.py v2 — 修复所有 '+ "<newline>")' 断串对（字符码构造，避免转义歧义）
import io
import ast

P = r"scripts\run_exp_routing_v2.py"
DQ = chr(34)   # "
BS = chr(92)   # \
NL = chr(10)   # newline

while True:
    lines = io.open(P, encoding="utf-8").read().split(NL)
    broken = None
    for i in range(len(lines) - 1):
        # 上行形如  ... + "      下行恰为  ")
        if lines[i].rstrip().endswith("+ " + DQ) and lines[i + 1].strip() == DQ + ")":
            broken = i
            break
    if broken is None:
        break
    head = lines[broken].rstrip()
    head = head[: len(head) - len('+ ' + DQ)]          # 去掉尾部 '+ "'
    lines[broken] = head + ' + "' + BS + "n" + DQ + ")"  # + "\n")
    del lines[broken + 1]
    io.open(P, "w", encoding="utf-8").write(NL.join(lines))
    print("fixed occurrence at line", broken + 1)

try:
    ast.parse(NL.join(io.open(P, encoding="utf-8").read().split(NL)))
    print("syntax OK — all broken joins repaired")
except SyntaxError as e:
    lines = io.open(P, encoding="utf-8").read().split(NL)
    print("remaining error at line", e.lineno, repr(lines[e.lineno - 1][:90]))
    raise SystemExit(1)
