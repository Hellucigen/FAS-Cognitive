# -*- coding: utf-8 -*-
# zenodo_step2.py — 向 draft 23196280 上传 zip(先删旧文件,再 bucket PUT,
# 流式 + 完成后校验 md5)。
import hashlib
import json
import os
import sys
import urllib.request

TOKEN = os.environ.get("ZEN_TOKEN", "")
DEP = 23196280
ZIP = r"E:\Project\Fascinator\deliverables\FAS_reproducibility_package_v1.0.0_pkg-be68a9dd.zip"
NAME = os.path.basename(ZIP)

def api(url, method="GET", data=None, headers=None):
    h = {"Authorization": "Bearer " + TOKEN}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    return urllib.request.urlopen(req, timeout=300)

d = json.load(api("https://zenodo.org/api/deposit/depositions/%d" % DEP))
bucket = d["links"]["bucket"]
existing = [f["filename"] for f in d.get("files", [])]
print("bucket ok; existing files:", existing)

# 删除同名/旧文件(网页上没落盘,但保险)
for f in d.get("files", []):
    u = f["links"]["self"]
    api(u, method="DELETE")
    print("deleted old file:", f["filename"])

md5 = hashlib.md5()
size = 0
with open(ZIP, "rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b""):
        md5.update(chunk)
        size += len(chunk)
md5hex = md5.hexdigest()
print("local md5:", md5hex, "size:", size)

url = bucket + "/" + NAME
with open(ZIP, "rb") as fh:
    req = api(url, method="PUT", data=fh,
              headers={"Content-Type": "application/octet-stream"})
    resp = json.load(req)
print("uploaded:", resp.get("filename"), resp.get("size"), resp.get("checksum"))
ok = resp.get("checksum") == "md5:%s" % md5hex and resp.get("size") == size
print("checksum match:", ok)
sys.exit(0 if ok else 1)
