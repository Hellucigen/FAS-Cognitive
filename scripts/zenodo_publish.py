# -*- coding: utf-8 -*-
# zenodo_publish.py — 更新 affiliation 为真实单位,然后发布,返回 DOI。
import json
import os
import sys
import urllib.request

TOKEN = os.environ.get("ZEN_TOKEN", "")
DEP = 23196280
PROXY = "http://127.0.0.1:7897"
AFFIL = "Department of Computer Science, China University of Petroleum-Beijing at Karamay, Karamay, China"

opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
H = {"Authorization": "Bearer " + TOKEN}

d = json.load(opener.open(urllib.request.Request(
    "https://zenodo.org/api/deposit/depositions/%d" % DEP, headers=H),
    timeout=120))
md = d["metadata"]
md["creators"] = [{"name": "Yan, Kailin", "affiliation": AFFIL}]

r = opener.open(urllib.request.Request(
    "https://zenodo.org/api/deposit/depositions/%d" % DEP,
    data=json.dumps({"metadata": md}).encode(), method="PUT",
    headers=dict(H, **{"Content-Type": "application/json"})), timeout=120)
d = json.load(r)
print("affiliation updated:", d["metadata"]["creators"])

r = opener.open(urllib.request.Request(d["links"]["publish"],
               headers=H, method="POST"), timeout=120)
d2 = json.load(r)
print("PUBLISHED")
print("version DOI:", d2.get("doi"))
print("concept DOI:", d2.get("conceptdoi"))
print("record html:", d2.get("links", {}).get("latest_html"))
