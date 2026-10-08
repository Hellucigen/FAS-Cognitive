# -*- coding: utf-8 -*-
# fix_table_bs.py — 修复 S16 表格行尾单反斜杠(应为双反斜杠)
for f in ("supplementary.tex", "supplementary_zh.tex"):
    lines = open(f, encoding="utf-8").read().split("\n")
    fixed = 0
    BS = chr(92)  # backslash
    for i, l in enumerate(lines):
        r = l.rstrip()
        if r.endswith(BS) and not r.endswith(BS + BS):
            lines[i] = r + BS
            fixed += 1
    open(f, "w", encoding="utf-8").write("\n".join(lines))
    print(f, "fixed", fixed)
