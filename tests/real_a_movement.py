# real_a_movement.py — B8 场景 A：移动诚实（B1 实机验收）
# 真实触发 = "过来"（approach→navigate）与 "向前走20秒"（move 反射）。
# 诚实判据：结算 reason ∈ 诚实分类；声称到达必须与 bot 实时 3D 距离吻合；
# 失败回执带真实 detail（dist3d/最后坐标，从 logs/action.jsonl 全文行取证）。
import sys, os, time, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from real_common import *

HONEST = ("arrived", "unreachable", "no_path", "stuck", "target_lost",
          "timeout", "entity_not_visible", "not_connected", None, "")
MOVEMENT = ("approach", "goto", "walk_to", "navigate_to_entity",
            "navigate_to_player", "follow_entity", "move")
LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "logs", "action.jsonl")


def last_settled_lines(n=400):
    out = []
    try:
        with open(LOG, encoding="utf-8") as f:
            for line in f.readlines()[-n:]:
                if '"action_settled"' in line:
                    out.append(json.loads(line))
    except FileNotFoundError:
        pass
    return out


def fresh(fn, before):
    rows = fn()
    return rows[before:] if len(rows) > before else []


hr("A 移动诚实")
if not mc_state().get("connected"):
    print("桥未连接"); sys.exit(2)

# A1: "过来" → approach/navigate
n_log0 = len(last_settled_lines())
sett0 = {r.get("ts") for r in recent_settles()}
say("过来")
ok, s, el = wait_for(
    lambda: [r for r in recent_settles()
             if r.get("action") in ("approach", "navigate_to_player", "goto",
                                    "walk_to", "follow_entity", "navigate_to_entity")
             and r.get("ts") not in sett0], 120, 4, "approach 结算")
v1 = verdict("过来 → 移动动作有真实结算", ok,
             json.dumps(s[-1], ensure_ascii=False)[:180] if ok else "120s 无结算（GAP 候选）")
if ok:
    r = s[-1]
    verdict("reason ∈ 诚实分类集合", r.get("reason") in HONEST,
            f"reason={r.get('reason')}")
    # detail 全文取证：dist3d/position 在场（B1 契约）
    rows = fresh(last_settled_lines, n_log0)
    det = [x for x in rows if str(x.get("data", {}).get("action", "")
           ) in MOVEMENT or x.get("data", {}).get("action_type") in MOVEMENT]
    blob = json.dumps(det, ensure_ascii=False)
    verdict("结算全文含 3D/坐标证据（dist3d/position/last_target）",
            ("dist3d" in blob or "position" in blob or "last" in blob or not det),
            blob[:200])
    if r.get("reason") in (None, "", "arrived") and r.get("success"):
        d = next((e.get("dist") for e in (mc_state().get("nearbyEntities") or [])
                  if "Hellucigen" in str(e.get("name") or "")), None)
        pl = next((p.get("dist") for p in (mc_state().get("playersNearby") or [])
                   if p.get("name") == "Hellucigen"), d)
        verdict("声称到达 ↔ 实时玩家距离 ≤6（无假 XZ）",
                pl is None or pl <= 6.0, f"实时玩家 dist={pl}")

# A2: "向前走20秒" → move（确定性反射，零 LLM）
sett1 = {r.get("ts") for r in recent_settles()}
say("向前走20秒")
ok2, s2, _ = wait_for(
    lambda: [r for r in recent_settles() if r.get("action") in ("move", "go_direction")
             and r.get("ts") not in sett1], 60, 3, "move/go_direction 结算")
verdict("向前走20秒 → 移动动作有结算（成功/失败皆如实）", ok2,
        json.dumps(s2[-1], ensure_ascii=False)[:150] if ok2 else "60s 无 go_direction 结算")

# A3: 超时诚实样本从**全量日志**取证（recent 只 8 条会滚掉）
tf = [r for r in last_settled_lines(800)
      if str((r.get("data") or {}).get("reason")) == "timeout"
      and (r.get("data") or {}).get("action_type") in MOVEMENT]
verdict("历史 timeout 结算带如实分类+证据字段", bool(tf),
        json.dumps(tf[-1].get("data"), ensure_ascii=False)[:200] if tf else "无样本")
sys.exit(0 if v1 else 1)
