# test_capability_feedback.py — B6：能力经验回流（§12/§13/§14）
# ============================================================================
# 钉死四件事（"越用越会"必须是经验派生，不是 RPG 等级）：
#   1 评分回流改中心+门槛：旧式 0.12×success_rate 以 0 为中心（失败史也吃
#     正加成）、一次运气即算"会"。新式：obs≥3 才生效、以 0.5 为中心、
#     钳位 ±0.05；explain 里 pb= 可见。
#   2 条件记账：record_execution 带 context 签名 → 能力节点 exec_contexts
#     按条件计 ok/fail（≤16 桶，有界）；不新建成功率账本（仍在 causal）。
#   3 读端点纯派生：experience_summary 只读，不改图。
#   4 trait 家族修正：follow_entity 是社交行为的 executor 实名，
#     旧挂 explore 家族 → social_openness 慢学习永无它的证据。
# 离线：假 causal + 临时 KG；不连桥不调 LLM。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_capability_feedback.py
# ============================================================================

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


import config as C
from graph_model import KnowledgeGraph, Node, Edge
from autonomy import AutonomousLoop
from capability_graph import CapabilityIndex
from action_system import ActionManager

BASE = tempfile.mkdtemp(prefix="fas_capfb_")
CFG = dict(C.DEFAULT_CONFIG)

kg = KnowledgeGraph()
kg.add_node(Node(id="Haru"))
loop = AutonomousLoop(kg=kg, config=CFG, data_dir=BASE)


class FakeCausal:
    def __init__(self, rate=None, obs=0, blockers=None):
        self.p = {"success_rate": rate, "obs": obs,
                  "blockers": blockers or []}

    def action_prior(self, atype, target=""):
        return dict(self.p)


CAND = {"action_type": "gather_resource", "target": "coal_ore",
        "motivation": "curiosity", "reason": ["Haru"], "params": {}}
P = {"connected": True, "health": 20, "food": 20,
     "position": {"x": 0.0, "y": 64.0, "z": 0.0},
     "players": [], "entities": [], "blocks": [],
     "unknown_entities": [], "unknown_blocks": []}


def scored(rate, obs):
    loop.actions = type("A", (), {"causal": FakeCausal(rate, obs)})()
    s, expl = loop._score_action(dict(CAND), P, 1000.0)
    return s, expl


# ═══ 1 评分回流：中心 0.5、obs≥3、钳位 ±0.05 ═══
print("\n── 1 先验加成 ──")
s_no, e_no = scored(1.0, 2)          # 全成但只有 2 个观察 → 不算经验
s_ok, e_ok = scored(1.0, 3)          # 达标 → +0.05（钳位上限）
check("观察数 <3 恒 0（一次/两次运气不是经验）",
      "pb=" not in e_no and abs(s_ok - s_no - 0.05) < 1e-9,
      f"Δ={s_ok - s_no:.4f} {e_no}")
s_low, e_low = scored(0.0, 10)       # 全败 → −0.05（以 0.5 为中心的负端）
check("纯失败史吃负加成（旧病：rate 再低也是正项）",
      "pb=-0.05" in e_low and s_low < s_no, e_low)
s_mid, _ = scored(0.5, 10)
check("成功率恰 0.5 → 无加成（中心对称）", abs(s_mid - s_no) < 1e-9,
      f"mid={s_mid} no={s_no}")
s_big, e_big = scored(1.0, 50)
check("加成钳位 ±0.05（先验不压过当下 drive）", abs(s_big - s_no - 0.05) < 1e-9, e_big)
loop.actions = None

# ═══ 2 record_execution 条件记账 + 3 读端点 ═══
print("\n── 2/3 条件记账与读端点 ──")
kg2 = KnowledgeGraph()
kg2.add_node(Node(id="挖掘方块", extra_attrs={"type": "action_concept"}))
kg2.add_node(Node(id="能力:采集", extra_attrs={"type": "capability"}))
kg2.add_edge(Edge(src="挖掘方块", dst="能力:采集", relation="需要", weight=0.5))
ci = CapabilityIndex(kg2, None, dict(CFG))
ci.record_execution("挖掘方块", "gather_resource", "行动_1", True,
                    context="hp=high|tone=calm")
ci.record_execution("挖掘方块", "gather_resource", "行动_2", False,
                    context="hp=high|tone=calm")
ci.record_execution("挖掘方块", "gather_resource", "行动_3", True,
                    context="hp=low|tone=fear")
cap = kg2.nodes["能力:采集"]
ecs = cap.extra_attrs.get("exec_contexts") or {}
check("条件签名进能力节点（同条件累计，条件间分开）",
      ecs.get("hp=high|tone=calm") == {"ok": 1, "fail": 1}
      and ecs.get("hp=low|tone=fear") == {"ok": 1, "fail": 0}, str(ecs))
check("learned_from 环保留（合同不破坏）",
      cap.extra_attrs.get("learned_from") == ["行动_1", "行动_2", "行动_3"],
      str(cap.extra_attrs.get("learned_from")))
for i in range(30):   # 造 30 个条件桶 → 有界
    ci.record_execution("挖掘方块", "gather_resource", f"行动_x{i}", i % 2 == 0,
                        context=f"cond_{i}")
check("条件桶有界（≤16，不无限膨胀）",
      len((kg2.nodes["能力:采集"].extra_attrs or {}).get("exec_contexts") or {}) <= 16,
      str(len(kg2.nodes["能力:采集"].extra_attrs["exec_contexts"])))
snap_before = str(sorted(kg2.nodes["能力:采集"].extra_attrs.items()))
summ = ci.experience_summary("能力:采集", causal=FakeCausal(0.6, 20))
snap_after = str(sorted(kg2.nodes["能力:采集"].extra_attrs.items()))
check("experience_summary 是纯派生视图（读完图上一字未动）",
      snap_before == snap_after and summ.get("found")
      and summ.get("exec_contexts"), str(summ)[:160])
check("传概念节点时列出其能力（边是真相源）",
      ci.experience_summary("挖掘方块").get("capabilities") == ["能力:采集"],
      str(ci.experience_summary("挖掘方块")))
check("读端点合并 causal 账本（传了才合）",
      summ.get("prior", {}).get("success_rate") == 0.6, str(summ.get("prior")))
check("不存在的节点如实 found=False（不造视图）",
      ci.experience_summary("没这个东西")["found"] is False)

# ═══ 4 _write_action_memory 传 context（生产调用方）═══
print("\n── 4 结算侧传签名 ──")
seen = {}


class SpyIdx:
    def record_execution(self, concept, executor, node_id, success, context=None):
        seen["args"] = (concept, executor, node_id, success, context)


kg3 = KnowledgeGraph()
am = ActionManager(kg=kg3, config={})
am.cap_index = SpyIdx()
am._write_action_memory(
    {"action_type": "gather_resource", "target": "coal_ore",
     "concept": "挖掘方块", "action_id": "act_x1", "reason": [],
     "motivation": "curiosity", "params": {}, "source": "autonomy",
     "expected_effect": "背包+1"},
    {"success": True, "result": "collected 1", "duration_s": 4.2}, True)
c, e, nid_, s_, ctx = seen.get("args", (None,) * 5)
check("生产结算把条件签名传给 record_execution（零 MC 语义，internal_state 派生）",
      c == "挖掘方块" and nid_.startswith("行动_") and s_ is True
      and isinstance(ctx, str) and ctx, str(seen))

# _TRAIT_FAMILY 家族修正
from action_system import ActionManager as AM
fam = AM._TRAIT_FAMILY
check("follow_entity 归 social 家族（executor 实名；旧错挂 explore）",
      "follow_entity" in fam["social"] and "follow_entity" not in fam["explore"],
      str(fam.get("social")))
check("am._family_of 走同一数据表",
      am._family_of("follow_entity") == "social", "")

shutil.rmtree(BASE, ignore_errors=True)

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
    sys.exit(1)
print("PASS: 能力经验回流验收通过（加成门槛/条件记账/只读端点/家族修正）")
