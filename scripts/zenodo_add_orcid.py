# -*- coding: utf-8 -*-
# zenodo_add_orcid.py — 用 legacy deposit API 的 edit 动作给已发布记录
# 的 creators 加 ORCID(元数据编辑,不新建版本,version DOI 不变)。
import json
import os
import urllib.request

TOKEN = os.environ.get("ZEN_TOKEN", "")
PROXY = "http://127.0.0.1:7897"
DEP = 23196280
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
H = {"Authorization": "Bearer " + TOKEN}


def call(url, method="GET", data=None, hdrs=None):
    hh = dict(H)
    hh.update(hdrs or {})
    return opener.open(urllib.request.Request(
        url, data=data, headers=hh, method=method), timeout=180)


# 1) 进入编辑态(返回 draft deposition)
try:
    r = json.load(call("https://zenodo.org/api/deposit/depositions/%d"
                       "/actions/edit" % DEP, method="POST"))
    draft_id = r["id"]
    print("edit mode; draft id:", draft_id)
except urllib.error.HTTPError as e:
    print("edit action http", e.code, e.read().decode()[:200])
    raise SystemExit(1)

# 2) 读当前元数据,改 creators
d = json.load(call("https://zenodo.org/api/deposit/depositions/%s"
                   % draft_id))
md = d["metadata"]
md["creators"] = [{
    "name": "Yan, Kailin",
    "affiliation": "Department of Computer Science, China University of "
                   "Petroleum-Beijing at Karamay, Karamay, China",
    "orcid": "0009-0009-6762-2684",
}]
call("https://zenodo.org/api/deposit/depositions/%s" % draft_id,
     method="PUT", data=json.dumps({"metadata": md}).encode(),
     hdrs={"Content-Type": "application/json"})
print("metadata PUT ok")

# 3) 发布(仅元数据变更)
r = json.load(call("https://zenodo.org/api/deposit/depositions/%s"
                   "/actions/publish" % draft_id, method="POST"))
print("published. doi:", r.get("doi"))

# 4) 校验
d = json.load(call("https://zenodo.org/api/records/%d" % DEP))
print("creators now:", json.dumps(d["metadata"]["creators"], indent=1))
