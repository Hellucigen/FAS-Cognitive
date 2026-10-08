# test_internal_state.py — 内部状态层测试（Phase 1）
# 离线、临时 KG、临时状态文件（不碰 data/internal_state.json 与真实图谱）。
# 覆盖不变量：单写入口 / cycle_id / 数值边界与历史 / 持久化往返 / 环上限 /
#             镜像节点 in-place（不新增节点）/ 人工可调 / 恢复出厂。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_internal_state.py

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from graph_model import KnowledgeGraph, Node
from internal_state import InternalState, MODULATOR_SPEC, NEED_SPEC, TRAIT_SPEC

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def near(a, b, eps=1e-9):
    """浮点比较：状态层内部是加法累积，等值判断必须带容差。"""
    return abs(float(a) - float(b)) < eps


def build(tag=""):
    base = os.path.join(tempfile.gettempdir(), f"fas_test_internal_{tag}")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", graph_space="self", label="self"))
    synced = []
    st = InternalState(kg=kg, config={}, data_dir=base,
                       save_graph_fn=lambda: synced.append(1))
    return kg, st, base, synced


# ── 1. 出厂基线 ───────────────────────────────────────────
kg, st, base, synced = build("a")
check("4 个调制变量就位", set(st.state()["modulators"]) ==
      {"dopamine", "cortisol", "serotonin", "oxytocin"}, str(list(st.state()["modulators"])))
check("4 个需求就位", set(st.state()["needs"]) ==
      {"safety", "exploration", "competence", "social"}, str(list(st.state()["needs"])))
check("8 个性格维度就位", len(st.state()["traits"]) == 8, str(list(st.state()["traits"])))
check("多巴胺拆了 tonic/phasic（不是单一水平）",
      st.state()["modulators"]["dopamine"]["tonic"] is not None
      and st.state()["modulators"]["dopamine"]["phasic"] is not None)
check("每个变量都有 baseline / 范围 / 描述",
      all(v["baseline"] is not None and v["max"] > v["min"] and v["desc"]
          for v in st.state()["modulators"].values()))
check("需求带理想区间与紧迫度字段",
      all("ideal_range" in n and "urgency" in n for n in st.state()["needs"].values()))

# ── 2. 唯一写入口：边界裁剪 + 历史留痕 ────────────────────
r = st.apply_delta("modulator", "cortisol", 0.25, reason="危险靠近", cycle_id="c_x")
check("apply_delta 生效并返回 from/to",
      r["ok"] and near(r["old"], 0.30) and near(r["new"], 0.55), str(r))
r2 = st.apply_delta("modulator", "cortisol", 9.0, reason="越界尝试")
check("越界被裁剪到 max 并标记 clamped",
      near(r2["new"], 1.0) and r2["clamped"] is True, str(r2))
r3 = st.apply_delta("modulator", "cortisol", -9.0)
check("下界同样裁剪", near(r3["new"], 0.0), str(r3))
h = st.history("modulator", "cortisol", 10)
check("每次写入都有历史（含 cycle_id / 来源 / 原因）",
      len(h) == 3 and h[0]["cycle_id"] == "c_x" and h[0]["reason"] == "危险靠近", str(h))
check("未知变量被拒绝", st.apply_delta("modulator", "不存在", 0.1)["ok"] is False)
check("未知类别被拒绝", st.apply_delta("hormone", "cortisol", 0.1)["ok"] is False)
check("非数字被拒绝", st.set_value("trait", "caution", "高")["ok"] is False)

# 性格范围 [0,1] 且不越界
st.set_value("trait", "caution", 5.0)
check("性格裁剪到 [0,1]", near(st.trait("caution"), 1.0), str(st.trait("caution")))

# ── 3. 多巴胺 tonic 与 level 同步（避免两套数） ───────────
st.set_value("modulator", "dopamine", 0.8)
check("多巴胺 level 与 tonic 一致",
      near(st.state()["modulators"]["dopamine"]["level"], 0.8)
      and near(st.state()["modulators"]["dopamine"]["tonic"], 0.8))

# ── 4. 周期生命周期与 cycle_id（I2） ──────────────────────
kg2, st2, base2, _ = build("b")
c1 = st2.begin_cycle("turn", {"text_len": 12})
c2 = st2.begin_cycle("autonomy_tick")
check("cycle_id 单调且唯一", c1 != c2 and st2.state()["current_cycle"] == c2, f"{c1} {c2}")
check("当前轮可见", st2.state()["current_cycle"] == c2)
entry = st2.end_cycle(c2, outcome={"acted": True})
check("结束轮次写入记录（含耗时与结果）",
      entry["id"] == c2 and entry["outcome"]["acted"] is True
      and entry["duration_s"] is not None, str(entry))
check("结束后当前轮清空", st2.state()["current_cycle"] is None)
check("轮次记录可查", len(st2.recent_cycles(10)) == 1)
st2.note_cycle_outcome(c2, {"llm_calls": 1})
check("轮次结果可补充", st2.recent_cycles(1)[0]["outcome"]["llm_calls"] == 1)

# ── 5. 持久化往返 + 状态文件是原子写产物 ─────────────────
kg3, st3, base3, _ = build("c")
st3.begin_cycle("turn")
st3.apply_delta("need", "exploration", 0.2, reason="久未探索")
st3.apply_delta("trait", "curiosity" if False else "novelty_preference", 0.03, reason="慢学习")
st3.record_reward("c_000001", 0.25, components={"internal_novelty": 0.3},
                  prediction_error=0.1, source_event="action_result",
                  affected_needs=["exploration"], explanation="发现新生物")
st3.end_cycle()
path = os.path.join(base3, "internal_state.json")
check("状态文件已落盘", os.path.exists(path))
saved = json.load(open(path, encoding="utf-8"))
check("落盘含 version/cycle_seq/四类状态",
      saved.get("version") == 1 and saved.get("cycle_seq", 0) >= 1
      and set(saved["modulators"]) and set(saved["needs"]) and set(saved["traits"]))
st3b = InternalState(kg=kg3, config={}, data_dir=base3)
check("重启恢复：调制变量水平", round(st3b.modulator_level("dopamine"), 4)
      == round(st3.modulator_level("dopamine"), 4))
check("重启恢复：需求水平", round(st3b.need_level("exploration"), 4)
      == round(st3.need_level("exploration"), 4))
check("重启恢复：性格值", round(st3b.trait("novelty_preference"), 4)
      == round(st3.trait("novelty_preference"), 4))
check("重启恢复：奖励台账", len(st3b.recent_rewards(5)) == 1)
check("重启恢复：cycle_seq 继续（不重号）",
      st3b.state()["cycle_seq"] == st3.state()["cycle_seq"])
check("重启恢复：性格基线不被历史值污染",
      near(st3b.trait_baseline("novelty_preference"), 0.55),
      str(st3b.trait_baseline("novelty_preference")))

# ── 6. 环形上限（文件不会无限膨胀） ──────────────────────
kg4, st4, base4, _ = build("d")
for i in range(260):
    st4.apply_delta("need", "safety", 0.001, reason=f"tick{i}")
check("单个变量的历史被环形截断（≤200）", len(st4.history("need", "safety", 500)) == 200,
      str(len(st4.history("need", "safety", 500))))
for i in range(230):
    st4.record_reward("c_x", 0.01)
check("奖励台账被环形截断（≤200）", len(st4.recent_rewards(500)) == 200)

# ── 7. 镜像节点 in-place（I4）：不按轮次新增节点 ─────────
kg5, st5, base5, synced5 = build("e")
n0 = len(kg5.nodes)
r = st5.sync_graph()
n1 = len(kg5.nodes)
check("镜像同步创建 4+4+8=16 个状态节点", r["synced"] == 16 and n1 - n0 == 16,
      f"synced={r['synced']} delta={n1 - n0}")
st5.set_value("modulator", "cortisol", 0.9, reason="测试")
st5.set_value("need", "safety", 0.8, reason="测试")
st5.sync_graph()
st5.sync_graph()
check("反复同步不新增节点（in-place）", len(kg5.nodes) == n1, str(len(kg5.nodes)))
node = kg5.nodes["皮质醇样"]
check("镜像节点属性反映运行时状态（含来源时间）",
      near(node.extra_attrs["level"], 0.9) and node.extra_attrs.get("updated"),
      str(node.extra_attrs))
check("镜像节点在 self 空间（可解释状态，不混进语义知识）",
      node.graph_space == "self" and node.extra_attrs.get("type") == "modulator")
check("需求镜像带紧迫度与驱动项字段",
      "urgency" in kg5.nodes["安全需求"].extra_attrs
      and "ideal_range" in kg5.nodes["安全需求"].extra_attrs)
check("性格镜像带 baseline（可对比是否漂移）",
      near(kg5.nodes["性格:caution"].extra_attrs["baseline"], 0.50))
check("同步后触发了图落盘", len(synced5) >= 1)

# ── 8. 人工可调 + 恢复出厂（用户要求 1/2） ───────────────
check("激素可由人工设定", st5.set_value("modulator", "serotonin", 0.15,
                                   source="manual")["ok"]
      and near(st5.modulator_level("serotonin"), 0.15),
      str(st5.modulator_level("serotonin")))
check("人工设定留痕（source=manual）",
      st5.history("modulator", "serotonin", 1)[0]["source"] == "manual")
check("性格可由人工设定", st5.set_value("trait", "social_openness", 0.9,
                                   source="manual")["ok"]
      and near(st5.trait("social_openness"), 0.9))
res = st5.reset_traits()
check("一键恢复出厂性格", res["ok"] and all(
    st5.trait(k) == TRAIT_SPEC[k]["baseline"] for k in TRAIT_SPEC),
      str({k: st5.trait(k) for k in TRAIT_SPEC}))
res2 = st5.reset_modulators()
check("一键恢复出厂激素", res2["ok"] and all(
    near(st5.modulator_level(k), MODULATOR_SPEC[k]["baseline"]) for k in MODULATOR_SPEC),
    str({k: st5.modulator_level(k) for k in MODULATOR_SPEC}))
check("恢复出厂不影响需求（需求是经历驱动的，不是出厂设定）",
      near(st5.need_level("safety"), 0.8), str(st5.need_level("safety")))

# ── 9. 快照 / 恢复 ────────────────────────────────────────
snap = st5.snapshot()
st5.set_value("modulator", "dopamine", 0.01)
st5.set_value("need", "exploration", 0.02)
st5.set_value("trait", "persistence", 0.99)
res3 = st5.restore(snap)
check("快照恢复覆盖三类状态",
      near(st5.modulator_level("dopamine"), snap["modulators"]["dopamine"]["level"])
      and near(st5.need_level("exploration"), snap["needs"]["exploration"]["level"])
      and near(st5.trait("persistence"), snap["traits"]["persistence"]),
      str(res3))
check("快照恢复也走唯一入口（留痕）",
      any(h["source"] == "restore" for h in st5.history("trait", "persistence", 5)))

# ── 10. explain（可观测性：为什么她想探索） ──────────────
kg6, st6, base6, _ = build("f")
cid = st6.begin_cycle("turn", {"text": "..."})
st6.apply_delta("need", "exploration", 0.3, reason="很久没有新奇刺激", cycle_id=cid)
st6.apply_delta("modulator", "dopamine", 0.05, reason="发现新对象", cycle_id=cid)
st6.record_reward(cid, 0.2, components={"internal_novelty": 0.25},
                  source_event="perception", affected_needs=["exploration"],
                  explanation="发现不认识的生物")
st6.end_cycle(cid, outcome={"acted": True, "intent": "observe"})
ex = st6.explain(cid)
check("explain 能回答'这一轮发生了什么'",
      ex["cycle"]["id"] == cid and len(ex["state_changes"]) == 2 and len(ex["rewards"]) == 1,
      str(ex)[:160])
check("explain 给出人话摘要", "奖励" in ex["summary"] or "状态变化" in ex["summary"],
      ex["summary"])
check("未产生变化的轮次也如实回答", "没有" in st6.explain("c_999999")["summary"])

for d in (base, base2, base3, base4, base5, base6):
    shutil.rmtree(d, ignore_errors=True)

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 内部状态层测试全过（临时 KG + 临时状态文件，无真实数据写入）")
