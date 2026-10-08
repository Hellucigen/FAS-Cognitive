# sanitize_package.py — 生成复现包的脱敏副本(原始包不动)。
# 替换: 个人用户名 → player_main;开发者机器绝对路径 → 相对/占位。
# 用法: python sanitize_package.py <src_pkg> <dst_pkg>
import os
import re
import shutil
import sys

SRC = sys.argv[1]
DST = sys.argv[2]
TEXT_EXT = {".py", ".md", ".json", ".jsonl", ".csv", ".txt", ".log", ".cff",
            ".yml", ".yaml", ".tex", ".bib", ".cfg", ".ini", ".js", ".html"}
REPL = [
    ("Hellucigen", "player_main"),
    (r"E:\Project\Fascinator", "<REPO_ROOT>"),
    ("E:/Project/Fascinator", "<REPO_ROOT>"),
    (r"E:\\Project\\Fascinator", "<REPO_ROOT>"),
    (r"E:\\Project", "<PROJECTS_DIR>"),
    ("E:/Project", "<PROJECTS_DIR>"),
    ("E:/Miniforge", "<PYTHON_ENV>"),
    ("E:\\Miniforge", "<PYTHON_ENV>"),
    ("E:/Models", "<MODELS_DIR>"),
    ("E:\\Models", "<MODELS_DIR>"),
    ("E:/LaTeX/MiKTeX", "<TEX_INSTALL>"),
    ("E:\\LaTeX", "<TEX_INSTALL>"),
    ("C:\\Users\\Hellucigen", "<HOME>"),
    ("C:\\Users\\player_main", "<HOME>"),
    (r"C:\\Users\\player_main", "<HOME>"),
    ("The Institute Eyes", "<EXTERNAL_PROJECT>"),
]

if os.path.exists(DST):
    shutil.rmtree(DST)


def transform(path):
    rel = os.path.relpath(path, SRC)
    dst = os.path.join(DST, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.splitext(path)[1].lower() in TEXT_EXT:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            txt = fh.read()
        for a, b in REPL:
            txt = txt.replace(a, b)
        with open(dst, "w", encoding="utf-8", newline="") as fh:
            fh.write(txt)
    else:
        shutil.copy2(path, dst)


n = 0
for root, _dirs, files in os.walk(SRC):
    for fn in files:
        transform(os.path.join(root, fn))
        n += 1
print("sanitized copy: %d files -> %s" % (n, DST))
