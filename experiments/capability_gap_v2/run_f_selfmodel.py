# -*- coding: utf-8 -*-
# run_f_selfmodel.py — 实验 F:Self-model 行为相关性(生产环,零 LLM)
# 设计(EXPERIMENT_PROTOCOL.md F):
#   历史类型:success(obtain oak_planks 达成)/ failure(obtain iron_ingot 必然失败)/ novel(无)
#   自由相:继承练习图谱、无目标注入,跑 40 拍,记录自主行为
#   条件:self_goal_on ∈ {True, False}(生产参数级开关,零新代码)
#   n=5 配对种子。成功/失败练习与自由相在同一 stack 连续(账本连续)。
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.chdir(ROOT)

from sandbox_lab import build_stack, tick, have_item, Clock, flush_causal  # noqa: E402
from run_exp_a2_reuse import plant_tree, TREES, ORE  # noqa: E402
import self_graph as _sg  # noqa: E402
_ORIG_SCG = _sg.set_current_goal  # v6:保留原函数,OFF 运行后必须恢复(否则毒化后续 ON)

HISTS = ("success", "failure", "novel")
SEEDS = list(range(5))
PRACTICE_TICKS = 60
FREE_TICKS = 40


def run(hist, seed, self_goal_on):
    t0 = time.time()
    goal = {"success": "oak_planks", "failure": "iron_ingot",
            "novel": "oak_planks"}[hist]
    st = build_stack(goal=goal, label=f"F_{hist}", seed=seed,
                     mc_gatherable=[ORE], self_goal_on=self_goal_on)
    # v7(修复验证轮):被试变量=统一 state consumer(autonomy._obtain_goals
    # 读 self_graph.current_goal,config self_model.goal_attention 门控)。
    # 两条件都做目标同步(set_goal_context);ON=consumer 开,OFF=consumer 关。
    # (v6 的 set_current_goal 封写补丁不再使用——隔离 consumer 单一变量。)
    _sg.set_current_goal = _ORIG_SCG
    st.loop.config.setdefault("self_model", {})["goal_attention"] = \
        bool(self_goal_on)
    for (x, z) in TREES:
        plant_tree(st.world, x, z, ORE)
    # v3:任务开始时调用生产同步入口 ActionManager.set_goal_context
    # (上一版没人调用它 → ON 条件同步也从未发生,0/5)。OFF 条件已在
    # self_graph 模块层封写,此调用按构造变成 no-op。
    # v4:沙盒装配缺口修复(F3)——build_stack 只建 "Haru" 人格节点,而
    # self_graph.SELF_ID="Self",目标同步的边因端点缺失被丢弃。生产
    # app.py 经 SelfMemoryUpdater 初始化 Self 节点;沙盒对齐之:
    # 调生产公开 API bootstrap_self(创建 Self 及身份/能力/偏好/目标结构)。
    try:
        from self_graph import bootstrap_self
        bootstrap_self(st.kg)
    except Exception as e:
        print(f"[F] bootstrap_self err {e!r}"[:100], flush=True)
    try:
        st.am.set_goal_context(f"实验目标:获得 {goal}",
                               source="experiment_obtain")
    except Exception as e:
        print(f"[F] set_goal_context err {e!r}"[:100], flush=True)
    sync_probe = {}
    try:
        from self_graph import current_goal as _cg0
        sync_probe["start"] = bool(_cg0(st.kg))
    except Exception:
        sync_probe["start"] = None
    clk = Clock()
    practice = {"done_tick": None, "actions": []}
    if hist != "novel":
        for i in range(PRACTICE_TICKS):
            tick(st, clk)
            if hist == "success" and have_item(st, goal) > 0:
                practice["done_tick"] = i
                break
        flush_causal(st)
    # 自由相(v7):清空 registry 中的 obtain 目标(实验设置,装配层),
    # 使 self-graph consumer 成为唯一目标来源——ON 应经派生目标恢复
    # 追求,OFF 无目标可用。
    try:
        st.loop._goals = [g for g in st.loop._goals
                          if str(g.get("source") or "")
                          != st.loop.OBTAIN_GOAL_SOURCE]
    except Exception:
        pass
    free = {"actions": {}, "gathers": 0, "explores": 0, "waits": 0,
            "self_nodes": 0}
    try:
        free["derived_goals"] = len(st.loop._obtain_goals())
    except Exception as e:
        free["derived_goals"] = f"err:{e!r}"[:60]
    tl = st.tl
    t_mark = __import__("time").time()
    for i in range(FREE_TICKS):
        tick(st, clk)
    try:
        from experience import EVENT_ACTION
        acts = [e for e in tl.events_since(t_mark)
                if e.get("event_type") == EVENT_ACTION]
        for e in acts:
            key = str(e.get("subject")) + ":" + str(
                (e.get("content") or {}).get("target"))
            free["actions"][key] = free["actions"].get(key, 0) + 1
            if "gather" in key:
                free["gathers"] += 1
            elif "explore" in key:
                free["explores"] += 1
            elif "wait" in key:
                free["waits"] += 1
    except Exception as e:
        free["telemetry_error"] = repr(e)[:120]
    try:
        free["self_nodes"] = sum(1 for n in st.kg.nodes if "self" in n.lower()
                                 or n.startswith("Haru"))
    except Exception:
        pass
    # 开关有效性遥测(v2):当前目标是否真的写进了自我图(开始/结束双点)
    from self_graph import current_goal as _cg
    try:
        sync_probe["mid_practice"] = bool(_cg(st.kg))
    except Exception:
        pass
    try:
        cg = _cg(st.kg)
        out_goal = (cg.get("goal") or cg.get("text") or cg) if isinstance(cg, dict) else cg
        free["self_goal_synced"] = bool(out_goal)
        free["self_goal_value"] = str(out_goal)[:40]
        free["self_goal_sync_probe"] = sync_probe
    except Exception as e:
        free["self_goal_synced"] = f"err:{e!r}"[:60]
    out = {"exp": "F", "hist": hist, "seed": seed,
           "self_goal_on": self_goal_on,
           "practice": {"done_tick": practice["done_tick"],
                        "n_actions": len(practice["actions"])},
           "free": free, "graph": {"nodes": len(st.kg.nodes),
                                   "edges": len(st.kg.edges)},
           "latency_s": round(time.time() - t0, 1)}
    return out


def main():
    rows = []
    for hist in HISTS:
        for seed in SEEDS:
            for sgo in (True, False):
                try:
                    r = run(hist, seed, sgo)
                except Exception as e:
                    r = {"exp": "F", "hist": hist, "seed": seed,
                         "self_goal_on": sgo, "error": repr(e)[:200]}
                rows.append(r)
                print("[F]", json.dumps(r, ensure_ascii=False)[:170], flush=True)
    out = os.path.join(ROOT, "experiments", "capability_gap_v2",
                       "raw_F.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("saved ->", out)


if __name__ == "__main__":
    main()
