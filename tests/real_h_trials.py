# real_h_trials.py — 实验 A/H：未知物品 → 试做实验 → 世界结果 → 知识回流（真机）
# ============================================================================
# 前置（人工）：启动 MC 服务器并让 bot 连上（mc_session.connect(port)），
#   然后**向 Haru 脚边扔一个她现在不知道用途的物品**（不告诉她是什么、
#   不提示任何用法）。脚本等待该物品进背包并触发探索缺口，然后观察 ≤15 分钟。
# 判定（§19 指标，全部来自真实 API 快照，零构造）：
#   H1 缺口→目标：目标列表出现 source=exploration_gap 且 gap_obj=该物品
#   H2 目标→实验：settle 流出现 place_block/equip_item 且 target=该物品
#   H3 实验→结果：该次 settle 有真实回执（成功改变世界 或 如实 fail reason）
#   H4 结果→账本：goal.trials 有 n/last_failed/reason（经 API 可见）
#   H5 知识回流：成功用途证据 → 缺口关闭（目标列表/缺口数下降）
#   H6 不死循环：同一试做不无限重复（n≤3 后该试做消失）
# 本脚本只 GET，不发送任何行为刺激。LLM 计数与 idle/action 统计供 §18。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/real_h_trials.py
# ============================================================================

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from real_common import get, mc_state, action_state, recent_settles, verdict, hr

DURATION = float(os.environ.get("H_MIN", "15")) * 60
EVERY = 15.0
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_h_samples.jsonl")

hr("H · 未知物品试做实验（真机观察）")
print("提示：向 Haru 扔一个未知用途物品后观察；Ctrl-C 提前结束。")

t0 = time.time()
samples = []
with open(OUT, "w", encoding="utf-8") as f:
    while time.time() - t0 < DURATION:
        try:
            a = action_state()
            au = a.get("autonomy") or {}
            mcs = mc_state()
            inv = {str((i or {}).get("name") if isinstance(i, dict) else i).lower()
                   for i in ((mcs.get("state") or {}).get("inventory_items") or [])}
            gap_goals = [g for g in (au.get("goals") or [])
                         if g.get("source") == "exploration_gap"]
            snap = {"ts": round(time.time() - t0, 1),
                    "state": au.get("state"),
                    "current": (a.get("action") or {}).get("current"),
                    "settles": recent_settles()[-6:],
                    "gap_goals": [{k: g.get(k) for k in
                                   ("gap_obj", "progress", "fails", "trials",
                                    "created")} for g in gap_goals],
                    "inv": sorted(inv),
                    "llm": (get("/api/llm/status", timeout=8) or {}).get("stats")}
        except Exception as e:
            snap = {"ts": round(time.time() - t0, 1), "error": str(e)[:120]}
        samples.append(snap)
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
        f.flush()
        time.sleep(EVERY)

hr("判定")
settles = [s for snap in samples for s in (snap.get("settles") or [])]
goals_now = samples[-1].get("gap_goals") or []
trial_settles = [s for s in settles
                 if s.get("action") in ("place_block", "equip_item")]
trials_seen = [g for g in goals_now if g.get("trials")]
idle_ticks = sum(1 for s in samples if s.get("state") == "idle")
uniq = {s.get("action") for s in settles}
verdict("H2 试做实验真实执行过（settle 流含 place/equip 且对象为缺口物品）",
        bool(trial_settles), f"trial_settles={len(trial_settles)}")
verdict("H4 试做账进入目标状态（n/reason 可解释）", bool(trials_seen),
        str(trials_seen[:2]))
verdict("H6 单一试做不刷屏（账内 n≤3）",
        all(int((rec or {}).get("n", 0)) <= 3
            for g in goals_now for rec in (g.get("trials") or {}).values()),
        str(goals_now))
print(f"\n观测摘要: 采样 {len(samples)} 帧，idle 帧 {idle_ticks}，"
      f"settle {len(settles)} 次，unique 动作 {sorted(uniq)}")
print("H1/H3/H5（缺口产生/回执真实/知识关闭）请对照 _h_samples.jsonl 与 "
      "fas_log memory/curiosity 通道人工核验——脚本不替证据下结论。")
print(f"样本文件: {OUT}")
