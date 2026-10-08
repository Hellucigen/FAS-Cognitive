# -*- coding: utf-8 -*-
# fix_double_backslash.py — 检查并修复 tex 中 "~\\cmd" 形式的双反斜杠笔误
# 只处理 `~\\<letter>`(波浪号后双反斜杠)——真正的 \\ 换行不会出现在 ~ 后。
import re

FILES = [
    r"E:\Project\Fascinator\FAS_Paper_I\main.tex",
    r"E:\Project\Fascinator\FAS_Paper_I\main_zh.tex",
    r"E:\Project\Fascinator\FAS_Paper_I\supplementary.tex",
    r"E:\Project\Fascinator\FAS_Paper_I\supplementary_zh.tex",
]

PAT = re.compile(r"~\\\\[A-Za-z]+")


def main():
    for f in FILES:
        s = open(f, encoding="utf-8").read()
        hits = PAT.findall(s)
        print(f, "double-backslash-after-tilde:", len(hits), hits[:10])
        if hits:
            fixed = PAT.sub(lambda m: "~\\" + m.group(0)[3:], s)
            # 把 ~\\cmd 换成 ~\cmd(修复双反斜杠笔误)
            open(f, "w", encoding="utf-8").write(fixed)
            print("  fixed ->", len(hits), "sites")
    # 复核
    for f in FILES:
        s = open(f, encoding="utf-8").read()
        print("recheck", f, len(PAT.findall(s)))


if __name__ == "__main__":
    main()
