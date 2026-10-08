# -*- coding: utf-8 -*-
# zenodo_diag.py — 小/中/大 PUT 阶梯诊断。
import json
import os
import urllib.request

TOKEN = os.environ.get("ZEN_TOKEN", "")
PROXY = "http://127.0.0.1:7897"
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
H = {"Authorization": "Bearer " + TOKEN}

def put(name, size_mb):
    data = os.urandom(size_mb * 1024 * 1024) if size_mb else b"x"
    url = ("https://zenodo.org/api/files/"
           "b66ffbdc-6789-446e-ac44-8398036ffa97/" + name + "?name=" + name)
    req = urllib.request.Request(url, data=data, method="PUT",
                                 headers=dict(H, **{"Content-Type": "application/octet-stream",
                                                    "Content-Length": str(len(data))}))
    try:
        r = opener.open(req, timeout=300)
        d = json.load(r)
        print("PUT %2dMB -> OK %s %s" % (size_mb, d.get("filename"), d.get("checksum")))
        # cleanup
        for f in json.load(opener.open(urllib.request.Request(
                "https://zenodo.org/api/deposit/depositions/23196280",
                headers=H), timeout=120)).get("files", []):
            if f["filename"] == name:
                opener.open(urllib.request.Request(f["links"]["self"],
                            headers=H, method="DELETE"), timeout=120)
    except Exception as e:
        print("PUT %2dMB -> FAIL %s" % (size_mb, repr(e)[:120]))

put("diag_0.bin", 0)
put("diag_1.bin", 1)
put("diag_5.bin", 5)
put("diag_17.bin", 17)
