# -*- coding: utf-8 -*-
# zenodo_step3.py — legacy multipart POST 上传:
# POST /api/deposit/depositions/{id}/files  (name=..., file=...)
import json
import os
import sys
import urllib.request
import uuid

TOKEN = os.environ.get("ZEN_TOKEN", "")
DEP = 23196280
ZIP = r"E:\Project\Fascinator\deliverables\FAS_reproducibility_package_v1.0.0_pkg-be68a9dd.zip"
NAME = os.path.basename(ZIP)
PROXY = "http://127.0.0.1:7897"

opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
H = {"Authorization": "Bearer " + TOKEN}

# multipart body(手工构造,避免 requests 依赖)
data = open(ZIP, "rb").read()
boundary = uuid.uuid4().hex
body = (("--%s\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\n%s\r\n"
         "--%s\r\nContent-Disposition: form-data; name=\"file\"; "
         "filename=\"%s\"\r\nContent-Type: application/octet-stream\r\n\r\n")
        % (boundary, NAME, boundary, NAME)).encode() + data + \
       ("\r\n--%s--\r\n" % boundary).encode()

req = urllib.request.Request(
    "https://zenodo.org/api/deposit/depositions/%d/files" % DEP,
    data=body, method="POST",
    headers={"Authorization": "Bearer " + TOKEN,
             "Content-Type": "multipart/form-data; boundary=" + boundary,
             "Content-Length": str(len(body))})
try:
    resp = json.load(opener.open(req, timeout=900))
    print("uploaded:", resp.get("filename"), resp.get("size"),
          resp.get("checksum"))
except Exception as e:
    print("FAIL:", repr(e)[:200])
    # 打印响应体(如果有)
    try:
        print(e.read().decode()[:300])
    except Exception:
        pass
    sys.exit(1)
