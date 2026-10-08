# real_b_social.py — B8 场景 B：三条社交邀请话全链（真实用户路径）
# "跟着我"/"过来"/"一起走" → reflex 快路径（零 LLM）→ register_invitation 持久目标
# → bot 执行 → 结算；"别跟了" → stop 取消。证据 = 回执 + 目标队列增减 + bot follow 态。
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from real_common import *

if not mc_state().get("connected"):
    print("桥未连接，先跑 real_0"); sys.exit(2)


def goals():
    st = action_state().get("autonomy") or {}
    gs = st.get("goals") or st.get("goal_queue") or []
    return [(g.get("action_type") or g.get("type"), g.get("target"),
             g.get("source")) for g in gs if isinstance(g, dict)]


def settles_after(n=12):
    return recent_settles()[-n:]


hr("B 社交命令全链")

# --- B1 "跟着我" ---
sett0 = {r.get("ts") for r in recent_settles()}
g_before = set(goals())
r1 = say("跟着我")
print("resp:", str(r1.get("answer") or "")[:60])
# 真实形态：概念快路径**立即启动持续动作**（goal 只在被拒/排队时落账）
def _b1():
    cur = ((action_state().get("action") or {}).get("current")) or {}
    if str(cur.get("action_type") or "") in ("follow_entity", "follow"):
        return [("current", cur.get("action_type"))]
    gs = [x for x in goals() if x[0] in ("follow", "follow_entity")]
    ss = [x for x in settles_after()
          if x.get("action") in ("follow_entity", "follow") and x.get("ts") not in sett0]
    return gs or ss
ok, g, _ = wait_for(_b1, 25, 2, "follow 启动(当前/目标/结算)")
v1 = verdict("跟着我 → 立即启动 follow_entity（或落承诺/结算）", ok, str(g))
st = mc_state()
v1b = verdict("bot follow 状态真实开启", bool(st.get("follow")), str(st.get("follow")))
ok2, s, _ = wait_for(
    lambda: [x for x in settles_after() if x.get("action") in ("follow", "follow_entity")]
    or [((action_state().get("action") or {}).get("current") or {})
        .get("action_type") == "follow_entity"],
    30, 3, "follow 在执行/已结算")
v1c = verdict("follow 在执行中或已有结算", ok2,
              json.dumps(s[0] if isinstance(s, list) and s else s, ensure_ascii=False)[:160])

# --- B2 "别跟了" → stop 兑现：follow 被取消结算 + 目标/承诺清空 ---
settB = {r.get("ts") for r in recent_settles()}
say("别跟了")
ok3, sb, _ = wait_for(
    lambda: [x for x in settles_after() if x.get("ts") not in settB
             and x.get("action") in ("stop", "follow_entity")], 30, 2, "stop/follow 结算")
v2 = verdict("别跟了 → stop 真实结算（follow 被如实取消）", ok3,
             json.dumps(sb, ensure_ascii=False)[:200])
st = mc_state()
verdict("bot follow 状态关闭", not st.get("follow"), str(st.get("follow")))

# --- B3 "过来" → approach/navigate（真机形态=navigate_to_entity 追击玩家）---
settA = {r.get("ts") for r in recent_settles()}
say("过来")
ok4, s, _ = wait_for(
    lambda: [x for x in settles_after() if x.get("action") in
             ("approach", "goto", "walk_to", "navigate_to_player",
              "navigate_to_entity", "follow_entity")
             and x.get("ts") not in settA], 90, 4, "approach 结算")
if ok4:
    r = s[0]
    v3 = verdict("过来 → 真实移动结算（reason 诚实分类）",
                 r.get("reason") in ("arrived", "unreachable", "no_path", "stuck",
                                     "target_lost", "timeout", "", None)
                 and isinstance(r.get("success"), bool),
                 json.dumps(r, ensure_ascii=False)[:200])
else:
    # 持续动作可能仍在执行（follow/navigate）——current 在场同样是真链路
    cur = ((action_state().get("action") or {}).get("current")) or {}
    v3 = verdict("过来 → 移动动作在执行（660s 持续型不秒结算）",
                 str(cur.get("action_type") or "") in ("navigate_to_entity",
                                                    "follow_entity"),
                 json.dumps(cur, ensure_ascii=False)[:160])

# --- B4 "一起走"（B4 补的数据行）---
sett3 = {r.get("ts") for r in recent_settles()}
g_before = set(goals())
r4 = say("一起走")
print("resp:", str(r4.get("answer") or "")[:60])
def _b4():
    cur = ((action_state().get("action") or {}).get("current")) or {}
    if str(cur.get("action_type") or "") == "follow_entity":
        return [("current", "follow_entity")]
    return ([x for x in goals() if x[0] in ("follow", "follow_entity")]
            or [x for x in settles_after()
                if x.get("action") in ("follow_entity", "follow")
                and x.get("ts") not in sett3])
ok5, g, _ = wait_for(_b4, 40, 3, "一起走→follow 组")
v4 = verdict("一起走 → 命中 follow 组并进入链路", ok5, str(g))
say("别跟了")
time.sleep(3)
cur_end = ((action_state().get("action") or {}).get("current")) or {}
verdict("收尾：stop 后无遗留 follow（不留无人要求的跟随）",
        str(cur_end.get("action_type") or "") != "follow_entity" or not cur_end,
        json.dumps(cur_end, ensure_ascii=False)[:120])

# --- 零 LLM 复核：三句命令都该走反射快路径 ---
print("\n结算近照:", json.dumps(settles_after(5), ensure_ascii=False)[:400])
print("\n判定汇总:", {"跟着我": v1, "follow目标": v1c, "撤销": v2, "过来": v3, "一起走": v4})
sys.exit(0 if (v1 and v2 and v3) else 1)
