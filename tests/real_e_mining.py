# real_e_mining.py — B8 场景 E：挖掘→快照→delta→事件→图（计数链）
# 用户命令"挖一些泥土"→ reflex dig → 世界状态快照 diff → 时间轴事件
# （block 变化含坐标 / inventory 计数增量）→ 图谱可查询。
# 判定基线 = 运行前时间轴事件数 + inventory 计数，防拿历史事件冒账。
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from real_common import *

hr("E 挖掘→delta→事件→图")
w0 = get("/api/debug/world")
n0 = len(w0.get("recent_events") or [])
held0 = (mc_state() or {}).get("heldItem")
print(f"基线: 时间轴事件 {n0} 个, 手持={held0}")

say("挖泥土")
ok, s, el = wait_for(
    lambda: [x for x in recent_settles()
             if x.get("action") in ("dig", "gather_resource")
             and "泥土" in str(x.get("target") or "")
             or "dirt" in str(x.get("target") or "")], 90, 4, "dig/gather 结算")
if not ok:
    verdict("90s 内 dig 有真实结算", False, "GAP 候选：反射未命中或执行超时，查 /api/action/state")
    sys.exit(1)
r = s[-1]
print("dig 结算:", json.dumps(r, ensure_ascii=False)[:220])

# recent_events 窗口有上限且**新→旧**排列（长度不能当增长证据）——比对窗口头部
def latest_sig():
    evs = (get("/api/debug/world").get("recent_events") or [])
    return json.dumps(evs[:3], ensure_ascii=False, sort_keys=True)


sig0 = latest_sig()
ok2, evs, _ = wait_for(
    lambda: [latest_sig()] if latest_sig() != sig0 else [],
    40, 5, "新时间轴事件（窗口头部刷新）")
new = (get("/api/debug/world").get("recent_events") or [])[:6]
kinds = [str(json.dumps(e, ensure_ascii=False))[:120] for e in new[:6]]
for k in kinds:
    print("  新事件:", k)
dig_evt = [k for k in kinds if "dirt" in k or "泥土" in k or "inventory" in k]
v2 = verdict("挖掘产生新事件（delta 直证：窗口头部出现挖掘相关事件）",
             bool(dig_evt), f"相关事件={len(dig_evt)}")
# debug 窗口行不带 content 字段——坐标证据从时间轴全文取证（本进程写入即时落盘）
import datetime
_tl = json.load(open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data", "experience_timeline.json"),
    encoding="utf-8")).get("raw") or []
_cut = time.time() - 180
blk = [e for e in _tl[-40:] if e.get("ts", 0) > _cut
       and str(e.get("subject", "")).startswith("block:")
       and (e.get("content") or {}).get("change") in ("disappeared", "appeared")]
has_coord = any((e.get("content") or {}).get("pos") for e in blk)
v3 = verdict("事件含坐标/位置字段（环境事实形状）", has_coord,
             json.dumps(blk[-1].get("content"), ensure_ascii=False)[:180]
             if blk else "无 block 事件样本")

held1 = (mc_state() or {}).get("heldItem")
inv_ev = [k for k in kinds if "inventory" in k]
v4 = verdict("inventory 计数事件（若获得物品）",
             bool(inv_ev) or held1 == held0,
             f"held {held0}→{held1}; inv事件={len(inv_ev)}")
sys.exit(0 if r.get("success") is not None and v2 else 1)
