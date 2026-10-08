# -*- coding: utf-8 -*-
# zenodo_step1.py — 列出草稿,找到含 zenodo_package_sanitized.zip 或
# 预留 DOI 10.5281/zenodo.23196280 的 deposition。
import json
import os
import sys
import urllib.request

TOKEN = os.environ.get("ZEN_TOKEN", "")
BASE = "https://zenodo.org/api/deposit/depositions"
req = urllib.request.Request(BASE + "?size=25",
                             headers={"Authorization": "Bearer " + TOKEN,
                                      "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        deps = json.load(r)
except Exception as e:
    print("API ERROR:", repr(e)[:300])
    sys.exit(1)

print("depositions found:", len(deps))
for d in deps:
    md = d.get("metadata", {})
    files = [(f.get("filename"), f.get("size"), f.get("checksum"))
             for f in d.get("files", [])]
    print("-" * 60)
    print("id:", d["id"], "| state:", d["state"])
    print("  doi:", d.get("doi"), "| conceptdoi:", d.get("conceptdoi"))
    print("  title:", md.get("title"))
    print("  files:", files)
