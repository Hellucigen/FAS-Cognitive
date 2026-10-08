# -*- coding: utf-8 -*-
# zenodo_upload_all.py — 一键完成:上传 zip(优先 bucket PUT,失败降级
# multipart POST)+ 填全元数据 + 校验。**不发布**(发布留给作者在网页
# 确认单位信息后执行,或明确指示后调用 --publish)。
import json
import os
import sys
import time
import urllib.request
import uuid

TOKEN = os.environ.get("ZEN_TOKEN", "")
DEP = 23196280
PROXY = "http://127.0.0.1:7897"
ZIP = r"E:\Project\Fascinator\deliverables\FAS_reproducibility_package_v1.0.0_pkg-be68a9dd.zip"
NAME = os.path.basename(ZIP)

opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
H = {"Authorization": "Bearer " + TOKEN}

METADATA = {
    "metadata": {
        "upload_type": "dataset",
        "publication_date": "2026-10-06",
        "title": "FAS (Fascinator) Reproducibility Package: persistent "
                 "experience-derived state in an auditable unified-graph "
                 "cognitive architecture (v1.0.0)",
        "creators": [{"name": "Yan, Kailin",
                      "affiliation": "[TODO: official English affiliation "
                                     "- must match the paper cover page]"}],
        "description": ("Reproducibility package for the manuscript "
                        "\"From Embodied Experience to Persistent "
                        "Experience-Derived State: An Auditable "
                        "Unified-Graph Architecture and a Preregistered "
                        "Boundary Analysis of Activation-Based Access\" "
                        "(submitted to PeerJ Computer Science).<p>Contains: "
                        "(i) archived run outputs for every experiment family "
                        "in the paper (mechanism campaign, beacon, shared-hub "
                        "sweep, relation-direction diagnosis, persistent-state "
                        "experiment and controls, campaign v2, routing, "
                        "cross-episode recombination); (ii) the source code "
                        "that produced them; (iii) frozen preregistration "
                        "records; (iv) a one-command verifier that recomputes "
                        "the paper's key numbers from the archives alone (no "
                        "LLM, no network).</p><p>Verification: "
                        "<code>python scripts/reproduce_key_numbers.py .</code> "
                        "--- expected outputs are listed in README.md (e.g., "
                        "H1: 10/0, McNemar p=0.00195; dJCG Holm p=1.5e-9 from "
                        "the frozen implementation; run accounting 508 "
                        "official / 390 repair runs).</p><p>This is the "
                        "sanitized copy (personal usernames and machine paths "
                        "replaced by placeholders); its payload SHA-256 is "
                        "be68a9ddac9c0f53 (MANIFEST.sha256 lists every "
                        "file).</p><p>Split license: code = MIT; data, "
                        "archives and documentation = CC BY 4.0 (see LICENSE "
                        "in the archive).</p>"),
        "keywords": ["cognitive architecture", "knowledge graph",
                     "spreading activation", "embodied agents",
                     "episodic memory", "preregistration"],
        "license": "cc-by-4.0",
        "access_right": "open",
        "version": "1.0.0+pkg.be68a9ddac9c0f53",
        "notes": "Split license: code MIT, data/docs CC-BY-4.0 (see LICENSE). "
                 "Verify: python scripts/reproduce_key_numbers.py .",
    }
}


def req(url, method="GET", data=None, headers=None, timeout=300):
    h = {"Authorization": "Bearer " + TOKEN}
    h.update(headers or {})
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    return opener.open(r, timeout=timeout)


def upload_zip():
    data = open(ZIP, "rb").read()
    d = json.load(req("https://zenodo.org/api/deposit/depositions/%d" % DEP))
    bucket = d["links"]["bucket"]
    for f in d.get("files", []):
        req(f["links"]["self"], method="DELETE")
        print("deleted old:", f["filename"])
    # 1) bucket PUT
    try:
        r = req(bucket + "/" + NAME + "?name=" + NAME, method="PUT", data=data,
                headers={"Content-Type": "application/octet-stream",
                         "Content-Length": str(len(data))}, timeout=900)
        f = json.load(r)
        print("PUT ok:", f.get("checksum"))
        return f
    except Exception as e:
        print("PUT failed:", repr(e)[:140])
    # 2) multipart POST(legacy 端点)
    boundary = uuid.uuid4().hex
    body = (("--%s\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\n%s\r\n"
             "--%s\r\nContent-Disposition: form-data; name=\"file\"; "
             "filename=\"%s\"\r\nContent-Type: application/octet-stream\r\n\r\n")
            % (boundary, NAME, boundary, NAME)).encode() + data + \
           ("\r\n--%s--\r\n" % boundary).encode()
    r = req("https://zenodo.org/api/deposit/depositions/%d/files" % DEP,
            method="POST", data=body,
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                     "Content-Length": str(len(body))}, timeout=1800)
    f = json.load(r)
    print("POST ok:", f.get("checksum"))
    return f


def main():
    do_publish = "--publish" in sys.argv
    for attempt in range(3):
        try:
            d = json.load(req("https://zenodo.org/api/deposit/depositions/%d"
                              % DEP))
            print("connected; draft", d["id"], "| doi:", d.get("doi"))
            break
        except Exception as e:
            print("connect attempt", attempt + 1, "failed:", repr(e)[:100])
            time.sleep(5)
    else:
        sys.exit("cannot reach zenodo (proxy up?)")

    f = upload_zip()
    md5 = f.get("checksum")
    # 元数据
    req("https://zenodo.org/api/deposit/depositions/%d" % DEP,
        method="PUT", data=json.dumps(METADATA).encode(),
        headers={"Content-Type": "application/json"})
    d = json.load(req("https://zenodo.org/api/deposit/depositions/%d" % DEP))
    md = d["metadata"]
    print("--- draft state ---")
    print("doi:", d.get("doi"))
    print("title:", md.get("title"))
    print("type/license/version:", md.get("upload_type"), "/",
          md.get("license"), "/", md.get("version"))
    print("files:", [(x.get("filename"), x.get("size"), x.get("checksum"))
                     for x in d.get("files", [])])
    print("creators:", md.get("creators"))
    if do_publish:
        assert md5 and md5.startswith("md5:"), "checksum missing; not publishing"
        r = req(d["links"]["publish"], method="POST")
        d2 = json.load(r)
        print("PUBLISHED:", d2.get("doi"), "| latest:",
              d2.get("links", {}).get("latest_html"))
    else:
        print("NOT published -- review the draft in the browser, then publish.")


if __name__ == "__main__":
    main()
