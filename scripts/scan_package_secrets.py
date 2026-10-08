# scan_package_secrets.py — 复现包敏感内容扫描(清理扫描,只读)
# 扫描: 绝对路径、API key/token/secret、邮箱、主机名/用户名;
# 输出: 按类别计数 + 逐文件明细(前 N 条)。
import os
import re
import sys

PKG = sys.argv[1] if len(sys.argv) > 1 else r"E:\Project\Fascinator\deliverables\zenodo_package"
PATTERNS = {
    "abs_path_win": re.compile(r"[A-Z]:\\\\?Users\\\\?[A-Za-z]+|[Ee]:\\\\?Project|E:/Project|E:/Miniforge|E:/Models|E:/LaTeX"),
    "api_key": re.compile(r"sk-[A-Za-z0-9]{8,}|api[_-]?key\s*[=:]\s*['\"][^'\"]{8,}|secret[_-]?key|Bearer [A-Za-z0-9]{10,}"),
    "token_like": re.compile(r"(token|password|passwd|credential)s?\s*[=:]\s*['\"][^'\"]{6,}", re.I),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "username": re.compile(r"Hellucigen|C:\\\\?Users"),
}
EXCLUDE_EXT = {".png", ".pdf", ".zip", ".index", ".faiss", ".model", ".bin", ".pt", ".pkl"}
hits = {}
for root, _dirs, files in os.walk(PKG):
    for fn in files:
        ext = os.path.splitext(fn)[1].lower()
        if ext in EXCLUDE_EXT:
            continue
        p = os.path.join(root, fn)
        try:
            with open(p, encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
        except OSError:
            continue
        for name, pat in PATTERNS.items():
            for m in pat.finditer(txt):
                rel = os.path.relpath(p, PKG)
                line = txt.count("\n", 0, m.start()) + 1
                snippet = txt[max(0, m.start()-40):m.end()+40].replace("\n", " ")
                hits.setdefault(name, []).append((rel, line, snippet[:110]))

for name in PATTERNS:
    lst = hits.get(name, [])
    print("== %s: %d hits ==" % (name, len(lst)))
    seen = set()
    for rel, line, snip in lst:
        key = rel
        if key in seen and len([1 for r, _, _ in lst if r == key]) > 3:
            continue
        seen.add(key)
        print("   %s:%d | %s" % (rel, line, snip))
        if len(seen) > 12:
            print("   ... (truncated)")
            break
