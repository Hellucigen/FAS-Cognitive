# -*- coding: utf-8 -*-
# diag_backslash.py — 精确计数 (Section~ 后的反斜杠数与匹配测试
import re

FILES = [
    r"E:\Project\Fascinator\FAS_Paper_I\main.tex",
    r"E:\Project\Fascinator\FAS_Paper_I\main_zh.tex",
    r"E:\Project\Fascinator\FAS_Paper_I\supplementary.tex",
    r"E:\Project\Fascinator\FAS_Paper_I\supplementary_zh.tex",
]

s = open(FILES[0], encoding="utf-8").read()
i = s.find("re-running (Section")
seg = s[i:i + 45]
print("seg repr:", repr(seg))
print("backslash count after 'Section~':", seg.count("\\", 19))
pat1 = re.compile(r"~\\[A-Za-z]+")     # ~ + 单反斜杠
pat2 = re.compile(r"~\\\\[A-Za-z]+")   # ~ + 双反斜杠
print("single-bs sites:", len(pat1.findall(s)), "| double-bs sites:", len(pat2.findall(s)))
for m in list(pat2.finditer(s))[:6]:
    print("  double site ctx:", repr(s[m.start() - 25:m.end() + 5]))
