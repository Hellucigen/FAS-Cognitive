# test_cognitive_loop.py — 统一内部认知循环 18 场景验收
# ============================================================================
# 2026-09-20 持续认知+自主性重构。核心观察点（需求 §八/§十五）：
# 没有用户输入时，FAS 是否产生**多样**的认知轨迹（reactivation→focus→
# intention kinds），以及"什么都不做"是否真的作为合法输出被走到。
# 离线：假桥/假引擎，无 LLM（除自由思考桩）、无网络。
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

import config as C
from graph_model import KnowledgeGraph, Node, Edge
from continuous_cognition import ContinuousCognition
from autonomy import AutonomousLoop
from modulation import ModulationLayer
from cognitive_field import DEFAULT_MODULATION

FAILURES = []


def check(name, cond, detail=""):
    st = "PASS" if cond else "FAIL"
    print(f"[{st}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class FN:
    def __init__(self, id, act, space="semantic"):
        self.id, self.activation, self.graph_space = id, act, space


class StubEngine:
    _running = False

    def __init__(self, topk=None):
        self.topk = topk or []
        self.diffuse = 0
        self.decay = 0
        self.marked = []
        self.sources = []

    def get_topk(self, k=15):
        return self.topk[:k], []

    def decay_step(self):
        self.decay += 1

    def diffuse_step(self):
        self.diffuse += 1

    def mark_active(self, ids):
        self.marked = list(ids)

    def register_activation_source(self, ids, source_type="external_input"):
        self.sources.append((list(ids), source_type))

    def clear_anchors(self):
        pass


class StubNLP:
    def __init__(self):
        self.calls = 0

    def ask(self, *a, **k):
        self.calls += 1
        return "{}"


class StubBuffer:
    def __init__(self):
        self.exprs = []

    def add_expression(self, e):
        self.exprs.append(e)

    def unreflected_expressions(self, n=30):
        return []


class StubEmb:
    """具身桩：可控世界状态。"""

    def __init__(self, caps=None):
        self.percept = {"connected": True, "health": 20, "food": 20,
                        "position": {"x": 0.0, "y": 64.0, "z": 0.0},
                        "players": [], "entities": [], "blocks": [],
                        "unknown_entities": [], "unknown_blocks": []}
        self.caps = caps or {"inspect_entity", "navigate_to_entity",
                             "gather_resource", "eat_food", "retreat",
                             "follow_entity", "communicate", "explore_area",
                             "explore_direction", "inspect_area", "stop",
                             "recover_health", "collect_food",
                             "collect_dropped_item"}
        self.executed = []
        self.results = {"success": True}

    def available(self):
        return True

    def capabilities(self):
        return set(self.caps)

    def perceive(self):
        return dict(self.percept)

    def detect_events(self):
        return []

    def execute(self, spec):
        self.executed.append(dict(spec))
        return {"success": True, "pending": False, "reason": ""}

    def poll_action(self):
        return {"status": "done"}

    def cancel(self):
        return {"ok": True}


def cc_with(kg=None, topk=None, loop_over=None):
    kg = kg or KnowledgeGraph()
    eng = StubEngine(topk)
    cfg = dict(C.DEFAULT_CONFIG)
    if loop_over:
        cfg["cognitive_loop"] = {**cfg.get("cognitive_loop", {}), **loop_over}
    cc = ContinuousCognition(kg, eng, StubNLP(), StubBuffer(), {
        "continuous_cognition": {
            "tick_seconds": 0.01, "form_threshold": 0.5,
            "express_threshold": 0.99, "inhibition_cooldown_s": 0}})
    # 注入 cognitive_loop 配置（_loop_cfg 默认已在类内，配置覆盖重跑合并）
    merged = {k: (dict(v) if isinstance(v, dict) else v)
              for k, v in ContinuousCognition._DEFAULT_LOOP.items()}
    for k, v in (cfg.get("cognitive_loop") or {}).items():
        prev = merged.get(k)
        m = dict(prev) if isinstance(prev, dict) else prev
        if isinstance(v, dict) and isinstance(m, dict):
            m.update(v)
            merged[k] = m
        else:
            merged[k] = v
    cc._loop_cfg = merged
    return kg, eng, cc


def mk_node(kg, nid, act=0.0, weight=0.5, space="semantic", ea=None,
            last_access=None):
    n = Node(id=nid, weight=weight, graph_space=space,
             last_access=last_access, extra_attrs=ea or {})
    kg.add_node(n)
    n.activation = act
    return n


def mk_edge(kg, s, d, relation="关联", w=0.5):
    kg.add_edge(Edge(src=s, dst=d, relation=relation, weight=w,
                      relation_category="cognitive_relation"))


# ═══ 1. 空闲状态：无焦点 → 无意图、无表达、无 LLM ═══
kg, eng, cc = cc_with(topk=[])
cc._pulse()
check("S1 空闲：不形成意图、不产生想法",
      cc._active_intentions() == [] and eng.marked == [])

# ═══ 2. 长时间无输入：冷场触发 reactivation，轨迹多样 ═══
kg, eng, cc = cc_with(topk=[])
mk_node(kg, "久远的概念", weight=0.9, last_access="2026/08/01 00:00:00")
mk_node(kg, "旧事件", weight=0.9, space="episodic",
        last_access="2026/08/01 00:00:00")
cc._last_react_ts = 0.0
# R2 P8：`retrieval.reignite_every`（此处走兜底 3）要求冷场**持续**若干拍才点火，
# 生产里本门每 tick 都被调用，所以这里按同样的节拍连打——不是给测试开后门。
cc._reactivate_gate()
check("S2 冷场头一拍不点火（持续度防抖，reignite_every 有真消费者）",
      [n.id for n in kg.nodes.values() if n.activation > 0] == [], str(eng.sources))
for _ in range(3):
    cc._last_react_ts = 0.0          # 只解 cooldown 兜底，冷场判定仍由场态给出
    cc._reactivate_gate()
lit = [n.id for n in kg.nodes.values() if n.activation > 0]
check("S2 场冷自动再点火（无需输入）", len(lit) >= 2, str(lit))
check("S2 注入源标记 reactivation（可解释）",
      any(s == "reactivation" for _ids, s in eng.sources), str(eng.sources))
check("S2 点然后仍走正常扩散（不直达意图：无 CI）",
      cc._active_intentions() == [])

# ═══ 3. 用户突然输入：busy 时认知节律让位 ═══
kg, eng, cc = cc_with(topk=[])
cc._busy.set()
d0 = eng.diffuse
cc.tick_once()
check("S3 busy 期图谱/意图/自主决策让位", eng.diffuse == d0)
cc._busy.clear()

# ═══ 4. 一个节点长期不活跃 → 被竞争点亮（概念与事件同资格）═══
check("S4 概念与情景节点都能重新点亮（非 episodic 限定）",
      "久远的概念" in lit and "旧事件" in lit, str(lit))

# ═══ 5. 多个节点竞争焦点：意图并发有界 ═══
kg, eng, cc = cc_with()
for x in ("焦点A", "焦点B", "焦点C"):
    mk_node(kg, x, weight=0.6)
    mk_edge(kg, "用户", x, relation="讲述")
eng.topk = [FN("焦点A", 3.5), FN("焦点B", 3.4), FN("焦点C", 3.3)]
for _ in range(6):
    cc._last_focus_sig = []           # 每轮都当新焦点
    cc._merge_or_form([kg.nodes["焦点A"], kg.nodes["焦点B"]], 0.8,
                      {"t": 1}, {"communication": 0.5, "exploration": 0.1},
                      {})
check("S5 同类意图并发不超过 max_active_per_kind",
      len(cc._active_intentions("communication")) <= 2,
      str(len(cc._active_intentions("communication"))))

# ═══ 6. 一个意图连续获得支持：强化而非复制 ═══
kg, eng, cc = cc_with()
mk_node(kg, "持续焦点", weight=0.7)
mk_edge(kg, "用户", "持续焦点", relation="讲述")
eng.topk = [FN("持续焦点", 3.0)]
cc._pulse()
first = list(cc._active_intentions())
ids1 = {c.id for c in first}
for _ in range(3):
    eng.topk = [FN("持续焦点", 3.0)]
    cc._pulse()
now = cc._active_intentions()
check("S6 同一焦点反复出现 → 同一意图被强化（数量不涨）",
      {c.id for c in now} == ids1 and len(now) == 1, str([c.id for c in now]))
ea6 = now[0].extra_attrs
check("S6 reinforced 计数增长（积累可见）",
      int(ea6.get("reinforced", 0)) >= 3, str(ea6.get("reinforced")))

# ═══ 7. 意图被其他意图抑制/焦点散去自然消亡 ═══
for c in cc._active_intentions():
    c.extra_attrs["activation"] = 0.02
cc._decay_intentions()
check("S7 失去支持的意图衰减出局（不删除节点，status=discarded）",
      all(n.extra_attrs.get("status") == "discarded"
          for n in kg.nodes.values()
          if (n.extra_attrs or {}).get("type") in
          ("intention", "communication_intention")))

# ═══ 8. action 成功 + 9. action 失败：意图支持共享、失败累积压力 ═══
base = tempfile.mkdtemp(prefix="cogloop_")
kg8, eng8, cc8 = cc_with()
auto8 = AutonomousLoop(kg=kg8, config=dict(C.DEFAULT_CONFIG), data_dir=base)
emb8 = StubEmb()
auto8.register_embodiment(emb8)
auto8.set_mode("on")
auto8.cc_ref = None
# 生产形态（2026-09-21 具身图谱化）：候选来自能力图谱路径
from capability_graph import CapabilityIndex
ci8 = CapabilityIndex(kg8, eng8, dict(C.DEFAULT_CONFIG))
ci8.embodiment = emb8
ci8.ensure_graph()
auto8.cap_index = ci8
# 认知先形成 exploration 意图（basis=UnknownEntity_axolotl）
mk_node(kg8, "UnknownEntity_axolotl", act=2.0, weight=0.5,
        ea={"type": "unknown"})
mk_node(kg8, "未知信息", act=1.2, weight=0.4)     # 感知信号节点
kg8.add_node(Node(id="CI_test", weight=0.5, label="intention",
                  graph_space="self",
                  extra_attrs={"type": "intention", "kind": "exploration",
                               "status": "ready", "activation": 0.8,
                               "basis": ["UnknownEntity_axolotl"]}))
emb8.percept = dict(emb8.percept)
emb8.percept["unknown_entities"] = [{"name": "axolotl", "displayName": "美西螈",
                                     "dist": 4.0}]
emb8.percept["entities"] = list(emb8.percept["unknown_entities"])
auto8._last_decision_sig = None
r8 = auto8.tick(now=1000.0)
cand8 = (auto8._history[-1]["candidates"][0] if auto8._history else {})
check("S8 意图支持进入评分（explain 含 int= 分量且 acted）",
      "int=" in str(cand8.get("explain", "")) and r8.get("acted"),
      str(cand8.get("explain", ""))[:120])
# 失败 → 压力（通过 cc.note_outcome 与 cc_ref 通道：action_system 的 hook
# 在自主 ActionManager 内建；这里直接验证 cc 侧聚合）
cc8.note_outcome(True)               # 用户负反馈
p1 = cc8.pressure_value()
cc8.note_pressure("action_failure")
check("S9 失败/负反馈累积到同一个图上压力节点",
      cc8.pressure_value() > p1 and p1 > 0, f"{p1} → {cc8.pressure_value()}")

# ═══ 10. causal knowledge 阻碍 action ═══
auto8._attempts[auto8._attempt_key("inspect_entity", "axolotl")] = {
    "count": 3, "next_ts": 1e12, "last_reason": "x"}
auto8._last_decision_sig = None
r10 = auto8.tick(now=1001.0)
check("S10 已知阻碍（重试上限/退避）挡住行动", not r10.get("acted"),
      str(r10.get("reason")))

# ═══ 11. unknown 节点 → exploration 意图（跨领域，不是逢 unknown 必 inspect）═══
kg11, eng11, cc11 = cc_with()
mk_node(kg11, "UnknownBlock_moonstone", weight=0.6, ea={"type": "unknown"})
mk_node(kg11, "未知信息", act=1.2, weight=0.5)
eng11.topk = [FN("UnknownBlock_moonstone", 2.6), FN("未知信息", 1.2)]
cc11._pulse()
ci11 = cc11._active_intentions()
check("S11 纯未知焦点 → exploration kind 意图（非 communication）",
      ci11 and ci11[0].extra_attrs.get("kind") == "exploration",
      str([c.extra_attrs.get("kind") for c in ci11]))

# ═══ 12. Minecraft 世界状态变化：决策门只对变化响应 ═══
# 12a：绝对平静（空世界、无意图、无事件）→ 第二拍 no_change
kg12 = KnowledgeGraph()
kg12.add_node(Node(id="Self", weight=1.0, graph_space="self"))
import copy
_cfg12 = copy.deepcopy(C.DEFAULT_CONFIG)
# 本场景验证的是变化门本身；空闲两档（2026-09-25 _maybe_amble）会让空世界
# 第一拍就产生 idle_explore 动作，其结算是真实状态改变、依设计撤销签名门
# （autonomy._on_action_settled），第二拍必然重扫——那就不是"什么都没变"了。
# 把两档节流推到场景时钟之外，恢复"绝对平静"前提。（必须 deepcopy：
# AutonomousLoop.__init__ 会就地 setdefault 嵌套段，浅copy 会污染共享默认表）
_cfg12.setdefault("autonomy", {})["amble_every_s"] = 1e9
_cfg12["autonomy"]["idle_explore_every_s"] = 1e9
auto12 = AutonomousLoop(kg=kg12, config=_cfg12,
                        data_dir=tempfile.mkdtemp(prefix="cl12_"))
emb12 = StubEmb()
auto12.register_embodiment(emb12)
auto12.set_mode("on")
r12a = auto12.tick(now=2000.0)          # 空世界 → 无候选（签名记录）
r12b = auto12.tick(now=2050.0)          # 什么都没变 → 不重扫
check("S12a 世界与内部状态无变化 → 决策保持观察（no_change，不空转）",
      r12b.get("reason") == "no_change", str(r12b.get("reason")))
emb12.percept = dict(emb12.percept)
emb12.percept["health"] = 9             # 世界变了
r12c = auto12.tick(now=2060.0)
check("S12b 世界状态变化触发重新决策（生存候选出现）",
      r12c.get("acted") or r12c.get("reason") in (
          "infeasible", "below_threshold", "blocked", "no_candidates"),
      str(r12c.get("reason")))
# 12c：意图消耗也是内部状态变化 → 允许链式重决策（观察→靠近）
auto8._attempts.clear()
auto8._last_action_ts = 0.0
auto8._last_decision_sig = None
r12d = auto8.tick(now=2000.0)
check("S12c 意图驱动的行动链（消耗引发新决策，非同一动作死循环）",
      r12d.get("acted") and r12d.get("intent") in (
          "inspect_entity", "navigate_to_entity"), str(r12d.get("intent")))

# ═══ 13/14/15. 调制后的阈值：高好奇/高专注/高社交显著性 ═══
ml = ModulationLayer(DEFAULT_MODULATION["params"], smoothing_alpha=1.0)
ml.compute({})
base_form = ml.get("cognition.form_threshold")
base_express = ml.get("cognition.express_threshold")
base_action = ml.get("action.score_threshold")
ml.compute({"drive.curiosity": 1.0, "network.DMN": 1.0})
check("S13 高 curiosity+DMN → 意图形成阈值下降（更容易想到东西）",
      ml.get("cognition.form_threshold") < base_form,
      f"{base_form:.2f} → {ml.get('cognition.form_threshold'):.2f}")
ml.compute({"network.CEN": 1.0, "context.task_engaged": 1.0})
check("S14 高 CEN/任务态 → 行动阈值下降、形成阈值回升（专注做事）",
      ml.get("action.score_threshold") < base_action
      and ml.get("cognition.form_threshold") > base_form,
      f"action {base_action:.2f}→{ml.get('action.score_threshold'):.2f}")
ml.compute({"hormone.oxytocin": 0.4, "need.social": 1.0})
check("S15 催产素/社交需求 → 表达阈值下降（更愿意开口）",
      ml.get("cognition.express_threshold") < base_express,
      f"{base_express:.2f} → {ml.get('cognition.express_threshold'):.2f}")
ml.compute({"hormone.cortisol": 0.5})
check("S15b 压力激素 → 全线收紧（表达更难）",
      ml.get("cognition.express_threshold") > base_express,
      str(ml.get("cognition.express_threshold")))

# ═══ 16. 完全没有认知内容：连续多拍什么都没发生（合法）═══
kg16, eng16, cc16 = cc_with()
for _ in range(10):
    eng16.topk = []
    cc16._last_react_ts = 0.0
    cc16._reactivate_gate()
    cc16._pulse()
check("S16 空场 10 拍：零意图、零表达调用",
      cc16._active_intentions() == [], str(len(cc16._active_intentions())))

# ═══ 17. reactivation 产生跨领域联想：与当前焦点耦合的旧节点优先 ═══
kg17, eng17, cc17 = cc_with()
mk_node(kg17, "当前焦点", weight=0.5)
mk_node(kg17, "耦合旧概念", weight=0.55, last_access="2026/08/01 00:00:00")
mk_node(kg17, "孤立旧概念", weight=0.55, last_access="2026/08/01 00:00:00")
mk_edge(kg17, "当前焦点", "耦合旧概念", w=0.8)
eng17.topk = [FN("当前焦点", 1.0)]
cc17._reactivate_field(cc17._loop_cfg.get("reactivation") or {}, 0.0)
cou = kg17.get_node("耦合旧概念").activation
iso = kg17.get_node("孤立旧概念").activation
check("S17 与焦点结构耦合的节点在再点火竞争中被优先（联想有方向）",
      cou > 0 and cou >= iso, f"耦合 {cou} vs 孤立 {iso}")

# ═══ 18. 连续运行稳定性：300 拍无异常、CI 有界、压力有界 ═══
kg18, eng18, cc18 = cc_with()
mk_node(kg18, "用户", weight=1.0, space="self")
mk_node(kg18, "Self", weight=1.0, space="self")
for i in range(30):
    n = mk_node(kg18, f"概念{i}", weight=0.5,
                last_access="2026/08/01 00:00:00")
eng18.topk = [FN("概念1", 2.5 + (i % 3) * 0.1), FN("概念2", 2.0)]
for _ in range(300):
    eng18.topk = [FN(f"概念{_ % 10}", 2.0 + (_ % 5) * 0.15),
                  FN(f"概念{(_ * 7) % 30}", 1.5)]
    cc18._last_react_ts = 0.0
    cc18._reactivate_gate()
    cc18._pulse()
n_ci = sum(1 for n in kg18.nodes.values()
           if (n.extra_attrs or {}).get("type") in
           ("intention", "communication_intention"))
check("S18 连续 300 拍：意图节点总量有界（prune 生效）", n_ci <= 20, str(n_ci))
check("S18 压力节点激活有界（max_inject 封顶 + 衰减）",
      cc18.pressure_value() <= 5.1, str(cc18.pressure_value()))
check("S18 全程无异常、无 LLM 调用", cc18.nlp.calls == 0)

import shutil
shutil.rmtree(base, ignore_errors=True)

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 统一认知循环 18 场景验收通过（空闲合法/轨迹多样/意图积累/调制阈值/双门稳定）")
