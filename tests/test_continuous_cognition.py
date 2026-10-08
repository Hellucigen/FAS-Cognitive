# test_continuous_cognition.py — 持续认知离线测试
# ============================================================================
# 临时内存 KG + 桩引擎/桩LLM（计数 LLM 调用）+ 显式假设数据。
# 覆盖规范测试 1/2/5/6/7 的核心断言（Test3/4 依赖真实 LLM，实机验证）。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 test_continuous_cognition.py
# ============================================================================

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys, time

FAILURES = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)

from graph_model import KnowledgeGraph, Node, Edge
from continuous_cognition import ContinuousCognition, ST_READY, ST_EXPRESSED, ST_DISCARDED

kg = KnowledgeGraph()
kg.add_node(Node(id="用户", weight=1.0, graph_space="semantic"))
kg.add_node(Node(id="Self", weight=1.0, graph_space="self"))
kg.add_node(Node(id="Minecraft", weight=0.8, graph_space="semantic"))
kg.add_node(Node(id="钻石", weight=0.7, graph_space="semantic"))
kg.add_edge(Edge(src="用户", dst="Minecraft", relation="喜欢", weight=0.9))

class FN:
    def __init__(self, id, act, space="semantic"):
        self.id, self.activation, self.graph_space = id, act, space

class FakeEngine:
    def register_activation_source(self, ids, source_type="external_input"):
        pass
    def __init__(self):
        self.diffuse_calls = 0; self.decay_calls = 0
        self._running = False; self.topk = []
        import threading; self._lock = threading.RLock()
    def get_topk(self, k=15): return self.topk[:k], []
    def decay_step(self): self.decay_calls += 1
    def diffuse_step(self): self.diffuse_calls += 1
    def mark_active(self, ids): pass
    def mark_edges_active(self, edges): pass
    def clear_anchors(self): pass

class _Resp: content = "（生成的语言）"
from langchain_core.runnables import RunnableLambda
class FakeNLP:
    def __init__(self): self.calls = 0; self.chat_llm = RunnableLambda(self._gen)
    def _gen(self, *a, **k): self.calls += 1; return _Resp()
    def ask(self, *a, **k): self.calls += 1; return "{}"
    def system_prompt(self, task): return f"<{task} 模板>"

class FakeBuffer:
    def __init__(self): self.exprs = []
    def add_expression(self, e): self.exprs.append(e)
    def unreflected_expressions(self, n=30):
        return [e for e in self.exprs if e.get('outcome') is not None and not e.get('reflected')][-n:]
    def mark_expressions_reflected(self, exprs):
        for e in exprs: e['reflected'] = True

eng = FakeEngine(); nlp = FakeNLP(); buf = FakeBuffer()
config = {"continuous_cognition": {
    "tick_seconds": 0.01, "pulse_every_ticks": 1,
    "form_threshold": 0.50, "express_threshold": 1.01,  # 1.01=阶段性禁表达
    "inhibition_cooldown_s": 0.1}}
cc = ContinuousCognition(kg, eng, nlp, buf, config)

# ── Test 1: 完全无输入 ───────────────────────────────────
eng.topk = []
cc._pulse()
check("无输入不产生CI", len(cc._active_intentions()) == 0)
check("无输入不调LLM", nlp.calls == 0)
cc_t = ContinuousCognition(kg, eng, nlp, buf, config)
cc_t.start(); time.sleep(0.12); cc_t.stop()
check("tick循环在跑扩散/衰减", eng.diffuse_calls > 0 and eng.decay_calls > 0)
eng.diffuse_calls = 0; eng.decay_calls = 0  # 线程检验后清零

# ── Test 2/6: 分享事实→强激活形成CI；弱激活不成 ──────────
eng.topk = [FN("Minecraft", 2.2), FN("钻石", 1.6), FN("无关概念X", 0.2)]
cc._pulse()
cis = cc._active_intentions()
check("强激活形成意图（kind 由焦点方向决定）", len(cis) == 1)
if cis:
    ea = cis[0].extra_attrs
    check("CI basis 来自激活节点", "Minecraft" in ea.get("basis", []))
    check("CI 有 Self-意图 边", any(e.src == "Self" and e.dst == cis[0].id for e in kg.edges))
    check("CI 可解释性字段齐", all(k in ea for k in ("explain", "score", "activation")))
    cc._pulse()
    check("重复增强而非新建", len(cc._active_intentions()) == 1)

# ── Test 7: CI 积累（保持/衰减/丢弃/再形成）──────────────
eng.topk = []  # 撤激活源：merge 不再救活
cis[0].extra_attrs["activation"] = 0.09
cc._pulse()
check("弱CI衰减后丢弃", all(n.extra_attrs["status"] == ST_DISCARDED
                          for n in kg.nodes.values()
                          if (n.extra_attrs or {}).get("type") in ("communication_intention", "intention")
                          and n.extra_attrs.get("activation", 1) < 0.10))
kg.add_node(Node(id='挖矿经历', graph_space='episodic', label='declarative-episodic'))
kg2 = KnowledgeGraph()
for nid, n in kg.nodes.items():
    if (n.extra_attrs or {}).get('type') in ('communication_intention', 'intention'):
        continue  # 旧 CI 不复制（含旧 status），避免污染新实例的竞争与衰减
    kg2.add_node(Node(id=nid, weight=n.weight, graph_space=n.graph_space,
                      label=n.label, extra_attrs=dict(n.extra_attrs)))
for e in kg.edges:
    kg2.add_edge(Edge(src=e.src, dst=e.dst, relation=e.relation, weight=e.weight))
class FE2:
    def register_activation_source(self, ids, source_type="external_input"):
        pass
    _running = False
    def __init__(self, topk):
        self.topk = topk
        import threading; self._lock = threading.RLock()
    def get_topk(self, k=15): return self.topk[:k], []
    def decay_step(self): pass
    def diffuse_step(self): pass
    def mark_active(self, ids): pass
    def generate_thought(self, nlp=None, k=8):
        # 桩：模拟真实实现——调一次 nlp 的 LLM，返回思考节点 id
        if nlp is None: return None
        nlp.thought_calls = getattr(nlp, 'thought_calls', 0) + 1
        return "思考_桩"
eng2 = FE2([FN("Minecraft", 2.8), FN("钻石", 2.0), FN("挖矿经历", 2.2, 'episodic')])
cc3 = ContinuousCognition(kg2, eng2, nlp, buf, config)
cc3._pulse()
cis = cc3._active_intentions()
check("再次激活重新形成", len(cis) >= 1)
if cis:
    check("高激活+用户相关→ready(表达家族已定)",
          cis[0].extra_attrs["status"] == ST_READY
          and cis[0].extra_attrs.get("reply_goal") is not None)
    cc3._pulse()
    cis = cc3._active_intentions()
    check("重复增强而非新建(新实例)", len(cis) == 1)

# 后续断言改用 cc3/kg2/eng2（独立实例，不受早期状态污染）
cc = cc3
kg = kg2
eng = eng2
# ── 表达抑制（Test 5/6 反面保障）─────────────────────────
cis[0].extra_attrs["activation"] = 0.95
cc.note_outcome(True)  # 用户刚叫停
before = nlp.calls
cc._pulse()
check("负反馈抑制表达(不调LLM)", nlp.calls == before)
cc.note_outcome(False)

# ── 表达路径（LLM 桩）────────────────────────────────────
cc.cfg["express_threshold"] = 0.3
cc.cfg["inhibition_cooldown_s"] = 0
cc._express_count_hour.clear()
cc._last_express_ts = 0.0
nlp.calls = 0
cc._pulse()
expressed = [n for n in kg.nodes.values()
             if (n.extra_attrs or {}).get("type") in ("communication_intention", "intention")
             and n.extra_attrs.get("status") == ST_EXPRESSED]
check("过阈值→表达并调1次LLM", nlp.calls >= 1 and len(expressed) >= 1,
      f"calls={nlp.calls}, expressed={len(expressed)}")
check("消息进入待取队列", len(cc.poll()) >= 1)
check("队列取后清空", len(cc.poll()) == 0)
if buf.exprs:
    check("主动表达进表达事件(反思闭环)", buf.exprs[-1].get("context") == "情境:主动发起")

# ── 竞争：MAX_ACTIVE ─────────────────────────────────────
for x in ["A1", "A2", "A3", "A4"]:
    kg.add_node(Node(id=x, weight=0.5, graph_space="semantic"))
    kg.add_edge(Edge(src="用户", dst=x, relation="喜欢", weight=0.5))
eng.topk = [FN("A1", 3.0), FN("A2", 2.9), FN("A3", 2.8), FN("A4", 2.7)]
cc._pulse()
check("活跃CI竞争上限", len(cc._active_intentions()) <= 3, f"n={len(cc._active_intentions())}")

# ── 线程启停 ─────────────────────────────────────────────
cc2 = ContinuousCognition(kg, eng, nlp, buf, config)
cc2.start(); time.sleep(0.12); cc2.stop()
check("循环线程可启停", not cc2._thread.is_alive() or cc2._stop.is_set())

# ── 论文 §3.3 离线三机制 ─────────────────────────────────
class FakeReflection:
    def __init__(s): s.calls = 0
    def run(s, **k): s.calls += 1; return {"reflection_id": "R1"}

nlp.thought_calls = 0
refl = FakeReflection()
cc3 = ContinuousCognition(kg, eng, nlp, buf, {"continuous_cognition": {
    "tick_seconds": 0.01, "pulse_every_ticks": 1, "form_threshold": 0.5,
    "express_threshold": 1.01, "reignite_every_pulses": 1, "reignite_count": 2,
    "thought_every_pulses": 1, "thought_min_interval_s": 0,
    "offline_reflection": True, "inhibition_cooldown_s": 0.1}})
cc3.reflection = refl
# 造可再点火的旧事件 + 一个同样久未激活的普通概念（验证不再限定 episodic）
for x in ["旧事件甲", "旧事件乙"]:
    kg.add_node(Node(id=x, weight=0.95, graph_space="episodic",
                     label="declarative-episodic",
                     last_access="2026/08/01 00:00:00",
                     extra_attrs={"event_timestamp": "2026-08-01"}))
kg.add_node(Node(id="久未想起的概念", weight=0.95, graph_space="semantic",
                 label="declarative-semantic",
                 last_access="2026/08/01 00:00:00"))
eng.topk = []
cc3._reactivate_field(cc3._loop_cfg.get('reactivation') or {}, 0.0)
acts = {n.id: n.activation for n in kg.nodes.values()
        if n.id in ("旧事件甲", "旧事件乙", "久未想起的概念")}
check("再点火唤醒旧事件", all(v > 0 for v in acts.values()), str(acts))
check("再点火不限情景记忆（概念节点同样有资格）",
      acts.get("久未想起的概念", 0) > 0, str(acts))
n0 = nlp.thought_calls
cc3._free_think()
check("自由思考调用LLM(1次)", nlp.thought_calls - n0 == 1)
eng.topk = []
cc3._pulse()  # 空池脉冲不产生表达
cc3.cfg["thought_min_interval_s"] = 999
cc3._free_think()
check("思考间隔门控", nlp.thought_calls - n0 == 1)
buf.exprs = [{"outcome": "positive", "reflected": False} for _ in range(4)]
cc3.reflection = refl  # 接上离线反思桩
n0 = refl.calls
# 压力不足：不反思（旧版是"攒够表达+到时间"就反思——现在由压力驱动）
cc3._maybe_offline_reflect()
check("压力不足不反思（合法静默）", refl.calls - n0 == 0)
# 行动失败/负反馈把反思压力推过阈值 → 触发
for _ in range(5):
    cc3.note_pressure("action_failure")
cc3._maybe_offline_reflect()
check("反思压力过阈值→离线反思触发", refl.calls - n0 == 1)
cc3._maybe_offline_reflect()
check("反思有 cooldown 门控(刚触发过→应被挡)", refl.calls - n0 == 1)

# ── 回合 busy 期：具身行动推进不停（2026-09-20 修复"一思考就站住"）──
class CountingAction:
    def __init__(s): s.calls = 0
    def tick(s, now=None): s.calls += 1; return {"acted": False}

class CountingAuto:
    def __init__(s): s.calls = 0
    def tick(s, now=None): s.calls += 1; return {"acted": False}

am_stub, au_stub = CountingAction(), CountingAuto()
eng4 = FakeEngine()
cc4 = ContinuousCognition(kg, eng4, nlp, buf, {"continuous_cognition": {
    "tick_seconds": 2.5, "pulse_every_ticks": 4, "reignite_every_pulses": 99,
    "thought_every_pulses": 99, "autonomy_every_ticks": 1,
    "monitor_poll_every_ticks": 99, "index_sync_every_ticks": 99}})
cc4.action_manager = am_stub
cc.autonomy = None
cc4.autonomy = au_stub
d0 = eng4.decay_calls
cc4.set_busy(True)          # 模拟 /api/nlp 回合进行中
cc4.tick_once()
check("busy 期 action_manager 仍被推进（动作不悬挂）", am_stub.calls == 1,
      f"am={am_stub.calls}")
check("busy 期自主决策让位（用户正在交互）", au_stub.calls == 0,
      f"au={au_stub.calls}")
check("busy 期图谱扩散照旧让位", eng4.decay_calls == d0 and cc4._tick == 0,
      f"decay+{eng4.decay_calls - d0} tick={cc4._tick}")
cc4.set_busy(False)
cc4.tick_once()
check("非 busy 期两者都跑", am_stub.calls == 2 and au_stub.calls == 1,
      f"am={am_stub.calls} au={au_stub.calls} tick={cc4._tick}")

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}"); sys.exit(1)
print("✓ 全部通过（临时 KG + 桩引擎，无真实图写入）")
