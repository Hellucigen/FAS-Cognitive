# real_g_watch.py — B8 场景 G：≥15 分钟自主连跑取样器
# 每 sample_every 秒抓一帧全景（动作/自主决策/因果/好奇/驱动/通道/聊天/LLM 计数），
# 追加到 /tmp/real_g_samples.jsonl。事后由 real_cdf_analysis.py 离线分析 C/D/F/G。
# 采样本身只 GET，不产生任何行为刺激（纯观察）。
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from real_common import get

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "tests", "_g_samples.jsonl")
DURATION = float(os.environ.get("G_MIN", "16")) * 60
EVERY = float(os.environ.get("G_EVERY", "20"))

t0 = time.time()
with open(OUT, "w", encoding="utf-8") as f:
    while time.time() - t0 < DURATION:
        snap = {"ts": time.strftime("%H:%M:%S")}
        for key, path in (("action", "/api/action/state"),
                          ("causal", "/api/debug/causal"),
                          ("curiosity", "/api/debug/curiosity"),
                          ("drives", "/api/debug/drives"),
                          ("world", "/api/debug/world"),
                          ("channels", "/api/channels/state"),
                          ("mc", "/api/mc/status"),
                          ("llm", "/api/llm/status"),
                          ("cog", "/api/cognition/state")):
            try:
                d = get(path, timeout=10)
                if key == "action":
                    a = d.get("action") or {}
                    au = d.get("autonomy") or {}
                    snap[key] = {"current": (a.get("current") or {}).get("action")
                                 if isinstance(a.get("current"), dict) else a.get("current"),
                                 "recent": a.get("recent") or [],
                                 "stats": a.get("stats"),
                                 "goals": au.get("goals"),
                                 "state": au.get("state")}
                elif key == "channels":
                    snap[key] = {"stats": d.get("stats"),
                                 "recent": (d.get("recent") or [])[-4:]}
                elif key == "mc":
                    s = d.get("state") or {}
                    snap[key] = {"connected": s.get("connected"),
                                 "pos": s.get("position"), "chat": (s.get("chat") or [])[-6:],
                                 "follow": s.get("follow"), "held": s.get("heldItem"),
                                 "players": [p.get("name") for p in s.get("playersNearby") or []]}
                elif key == "cog":
                    its = d.get("intentions") or (d.get("cognition") or {}).get("intentions") or []
                    snap[key] = {"n": len(its),
                                 "delivered": [i.get("id") for i in its
                                               if (i.get("delivery") or {}).get("status")],
                                 "last_express": None}
                    for i in its:
                        dv = i.get("delivery") or {}
                        if dv.get("status"):
                            snap[key]["last_express"] = {"ci": i.get("id"), **dv}
                elif key == "curiosity":
                    snap[key] = {"species": d.get("learned_species"),
                                 "n_unknown": len(d.get("live_unknowns") or [])}
                elif key == "causal":
                    snap[key] = d
                elif key == "drives":
                    snap[key] = d
                elif key == "llm":
                    snap[key] = d
                elif key == "world":
                    snap[key] = {"n_events": len(d.get("recent_events") or []),
                                 "pending": d.get("pending_windows")}
            except Exception as e:
                snap[key] = {"err": str(e)[:80]}
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
        f.flush()
        time.sleep(EVERY)
print(f"G 取样完成: {OUT} 时长 {round((time.time()-t0)/60,1)}min")
