# real_common.py — B8 真机场景共用件（只走 HTTP，不碰进程内部）
# ============================================================================
# 所有 real_* 脚本对着**运行中的 app**（127.0.0.1:5000）取证：
#   say()      = 用户路径（/api/nlp，反射快路径零 LLM）
#   get()      = 观测面（/api/action/state、/api/debug/*、/api/mc/status…）
#   wait_for() = 带预算的轮询（真机不 sleep 装样子）
# 证据语义：PASS=真实回执；GAP=机制在场但本窗口未凑齐条件（如实记录）；
# FAIL=行为与诚实契约相悖。永不编造。
# ============================================================================

import json
import time
import urllib.request

BASE = "http://127.0.0.1:5000"


def get(path, timeout=15):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, payload, timeout=120):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def say(text, timeout=120):
    """真实用户路径：一句话进 /api/nlp（反射/会话快路径零 LLM）。"""
    return post("/api/nlp", {"text": text}, timeout=timeout)


def mc_state():
    try:
        return (get("/api/mc/status") or {}).get("state") or {}
    except Exception:
        return {}


def action_state():
    return get("/api/action/state") or {}


def recent_settles():
    return (action_state().get("action") or {}).get("recent") or []


def wait_for(fn, budget_s, interval_s=2.0, desc=""):
    """轮询到 fn() 返回真值；返回 (ok, last_value, elapsed)。"""
    t0 = time.time()
    val = None
    while time.time() - t0 < budget_s:
        try:
            val = fn()
        except Exception as e:
            val = None
        if val:
            return True, val, round(time.time() - t0, 1)
        time.sleep(interval_s)
    return False, val, round(time.time() - t0, 1)


def hr(title):
    print("\n" + "=" * 64)
    print(f"REAL·{title}")
    print("=" * 64)


def verdict(name, cond, detail="", gap=False):
    tag = "GAP " if gap and not cond else ("PASS" if cond else "FAIL")
    print(f"[{tag}] {name}" + (f" | {str(detail)[:200]}" if detail else ""))
    return cond

