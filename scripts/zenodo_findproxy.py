# -*- coding: utf-8 -*-
# zenodo_findproxy.py — 探测可用代理端口并实测到 Zenodo 的连通性。
import os
import socket
import urllib.request

TOKEN = os.environ.get("ZEN_TOKEN", "")
CANDIDATES = [7897, 7890, 7891, 7892, 2080, 1080, 10808, 10809, 8118,
              8888, 9090, 7895, 7896, 7898, 7899]

alive = []
for port in CANDIDATES:
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=2)
        s.close()
        alive.append(port)
    except OSError:
        pass
print("open local ports:", alive)

reg = urllib.request.getproxies()
print("registry proxies:", reg)

good = []
for port in alive:
    px = "http://127.0.0.1:%d" % port
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": px, "https": px}))
        r = opener.open(urllib.request.Request(
            "https://zenodo.org/api/deposit/depositions/23196280",
            headers={"Authorization": "Bearer " + TOKEN}), timeout=20)
        d = json.loads(r.read().decode()) if False else None
        print("PROXY OK ->", px, "(zenodo reachable)")
        good.append(px)
    except Exception as e:
        print("proxy", px, "zenodo fail:", repr(e)[:90])

if good:
    print("\nUSE PROXY:", good[0])
else:
    print("\nno working proxy found")
