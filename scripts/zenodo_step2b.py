# -*- coding: utf-8 -*-
# zenodo_step2b.py — bucket PUT(整块读入,显式 Content-Length;?name= 参数)。
import hashlib
import json
import os
import sys
import urllib.request

TOKEN = os.environ.get("ZEN_TOKEN", "")
DEP = 23196280
ZIP = r"E:\Project\Fascinator\deliverables\FAS_reproducibility_package_v1.0.0_pkg-be68a9dd.zip"
NAME = os.path.basename(ZIP)
PROXY = "http://127.0.0.1:7897"

opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))

d = json.load(opener.open(urllib.request.Request(
    "https://zenodo.org/api/deposit/depositions/%d" % DEP,
    headers={"Authorization": "Bearer " + TOKEN}), timeout=120))
bucket = d["links"]["bucket"]
for f in d.get("files", []):
    opener.open(urllib.request.Request(f["links"]["self"],
               headers={"Authorization": "Bearer " + TOKEN},
               method="DELETE"), timeout=120)
    print("deleted:", f["filename"])

data = open(ZIP, "rb").read()
md5 = hashlib.md5(data).hexdigest()
print("local md5:", md5, "size:", len(data))

url = bucket + "/" + NAME + "?name=" + NAME
req = urllib.request.Request(url, data=data, method="PUT",
                             headers={"Authorization": "Bearer " + TOKEN,
                                      "Content-Type": "application/octet-stream",
                                      "Content-Length": str(len(data))})
resp = json.load(opener.open(req, timeout=600))
print("uploaded:", resp.get("filename"), resp.get("size"), resp.get("checksum"))
ok = resp.get("checksum") == "md5:%s" % md5 and resp.get("size") == len(data)
print("checksum match:", ok)
sys.exit(0 if ok else 1)
