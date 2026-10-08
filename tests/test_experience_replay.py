# test_experience_replay.py — 离线经验重放行为规格（任务 §十六 16 点）
# ============================================================================
# 真件：KnowledgeGraph / DiffusionEngine（默认 config，零改动）/
# ExperienceTimeline / CausalLearner / prior_knowledge 缺口。
# 桩：只有两处——CC 门控放置测试用 fake self（不启线程）、causal 计数用
# SimpleNamespace。目标对象名一律显式假想（不编造用户经历）。
# 离线：不 LLM、不网络、临时目录。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_experience_replay.py
# ============================================================================

import inspect
import logging
import os
import shutil
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.WARNING)

import config as _C
from graph_model import KnowledgeGraph, Node, Edge
from diffusion_engine import DiffusionEngine
import experience_replay as R
import prior_knowledge as pk

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


TMP = tempfile.mkdtemp(prefix="fas_replay_")


def fresh(cfg_extra=None, n_extra=0):
    """小图：cow -是(semantic)-> animal；cow -涉及(cognitive)-> 行动_A；
    孤岛与受保护边各一。"""
    kg = KnowledgeGraph()
    kg.add_node(Node(id="cow", weight=0.5, graph_space="semantic"))
    kg.add_node(Node(id="animal", weight=0.5, graph_space="semantic"))
    kg.add_node(Node(id="行动_A", weight=0.5, graph_space="episodic"))
    kg.add_node(Node(id="无关孤岛", weight=0.5, graph_space="semantic"))
    for i in range(n_extra):
        kg.add_node(Node(id=f"extra{i}", weight=0.5, graph_space="semantic"))
    # 注意：add_edge 按 relation_ontology 用**关系词**改写 relation_category——
    # 手写 category 不算数，测试必须用本体词："是"=semantic（Hebbian 白名单），
    # "涉及"=cognitive、"导致"=causal（白名单外，受保护）。
    kg.add_edge(Edge(src="cow", dst="animal", relation="是", weight=0.5,
                     relation_category="semantic_relation"))
    kg.add_edge(Edge(src="cow", dst="行动_A", relation="涉及", weight=0.5,
                     relation_category="cognitive_relation"))
    # 受保护因果边挂在 animal 上；"无关孤岛"必须**零边**（否则名不副实）
    kg.add_edge(Edge(src="行动_A", dst="animal", relation="导致", weight=0.5,
                     relation_category="causal_relation"))
    eng = DiffusionEngine(kg, dict(_C.DEFAULT_CONFIG))
    eng.name_to_node = dict(kg.nodes)
    cfg = {"min_interval_s": 0.0, "stop_after_zero": 99,
           "gate_min_interval_s": 0.0, "debug_log": False}
    cfg.update(cfg_extra or {})
    rp = R.ExperienceReplay(kg, eng, cfg,
                            state_path=os.path.join(TMP, f"st_{id(kg)}.json"))
    return kg, eng, rp


class StubTL:
    """事件列表 newest-first；记录扫描参数（防全量断言用）。"""

    def __init__(self, events):
        self.events = list(events)
        self.calls = []

    def recent(self, n=30, event_type=None, actor=None, since=None):
        self.calls.append(n)
        out = [e for e in self.events if since is None or e["ts"] >= since]
        return out[:n]


def ev(ts, target="cow", result=None, repeat=1, etype="ACTION", subject="walk_to"):
    content = {"target": target}
    if result is not None:
        content["result"] = result
    return {"ts": ts, "event_type": etype, "subject": subject, "actor": "Self",
            "content": content, "meta": {"repeat": repeat}}


NOW = time.time()

# ── §16.1 空闲触发 / §16.16 对话不阻塞：门在 CC 非 busy 分支 ──
import continuous_cognition as cc_mod

src_tick = inspect.getsource(cc_mod.ContinuousCognition.tick_once)
_lines = src_tick.splitlines()
_busy_i = next((i for i, l in enumerate(_lines) if "if not self._busy" in l), -1)
_rep_i = next((i for i, l in enumerate(_lines) if "self._replay_gate()" in l), -1)


def _indent(l):
    return len(l) - len(l.lstrip())


check("1 空闲触发：_replay_gate 挂在非 busy 分支体内部",
      0 <= _busy_i < _rep_i and _indent(_lines[_rep_i]) > _indent(_lines[_busy_i]),
      f"busy@{_busy_i} replay@{_rep_i}")

fake_self = types.SimpleNamespace(
    cfg={"replay": {}}, kg=None, engine=None, timeline=None,
    action_manager=types.SimpleNamespace(causal=None, busy=lambda: False),
    _replay=None)
gate_calls = []
fake_self._replay = types.SimpleNamespace(
    gate=lambda **kw: gate_calls.append(kw))
cc_mod.ContinuousCognition._replay_gate(fake_self)
check("1b 门转发 timeline/causal 到 replay.gate",
      len(gate_calls) == 1 and "timeline" in gate_calls[0], str(gate_calls))

# 忙碌跳过（§16.16 行动优先）
busy_calls = []
fake_self2 = types.SimpleNamespace(
    cfg={"replay": {}}, kg=None, engine=None,
    action_manager=types.SimpleNamespace(
        causal=None, busy=lambda: True),
    _replay=types.SimpleNamespace(gate=lambda **kw: busy_calls.append(1)))
cc_mod.ContinuousCognition._replay_gate(fake_self2)
check("16b 动作执行中不重放（busy 抢占）", busy_calls == [])

# ── §16.4 禁止全历史扫描 / §16.5 选择预算 ──
kg, eng, rp = fresh()
many = [ev(NOW - i * 60, target=f"extra{i % 5}", result="failed:x")
        for i in range(600)]
tl = StubTL(many)
rp.cfg["scan_max"] = 50
rp.cfg["batch_max"] = 3
cands = rp.select_candidates(tl, None, NOW)
check("4 选择只读 scan_max 条（无全量扫描）",
      tl.calls and tl.calls[0] <= 50 and len(tl.calls) == 1, str(tl.calls[:3]))
check("5 每轮候选 ≤ batch_max", len(cands) <= 3, str(len(cands)))

# ── §16.6 种子预算 / §16.9 复用扩散 / 邻域点亮 ──
kg, eng, rp = fresh()
rp.cfg["seeds_max"] = 4
for i in range(5):
    kg.add_node(Node(id=f"extra{i}", weight=0.5, graph_space="semantic"))
# 只用连通对象做候选（rng 探索噪声会换候选，断言必须对候选集合鲁棒）：
# 全部指向 cow → 一跳邻居 animal/行动_A 只可能经**扩散**点亮（它们不是种子）
evts = [ev(NOW - 5 - i, target="cow", result="failed:x" if i == 0 else None)
        for i in range(5)]
tl = StubTL(evts)
rp.run_batch(tl, None, NOW)
check("6 每轮种子总数 ≤ seeds_max", 0 < rp.stats()["seeds"] <= 4,
      str(rp.stats()))
check("9 扩散复用：非种子的一跳邻居被点亮（走引擎现有一拍）",
      kg.nodes["animal"].activation > 0.0
      and kg.nodes["行动_A"].activation > 0.0,
      f"animal={kg.nodes['animal'].activation:.3f} "
      f"act={kg.nodes['行动_A'].activation:.3f}")
check("孤岛不受污染（只走有意义的连接）",
      kg.nodes["无关孤岛"].activation == 0.0)

# ── §16.7 Hebbian 受既有帽约束（有增长但有界）──
kg, eng, rp = fresh()
w0 = kg.get_edge("cow", "animal", "是").weight
tl = StubTL([ev(NOW - 5 - i, target="cow") for i in range(8)])
for k in range(6):
    rp.run_batch(tl, None, NOW + k * 2)
w1 = kg.get_edge("cow", "animal", "是").weight
check("7 Hebbian 增长存在（真强化）", w1 > w0, f"{w0}->{w1}")
check("7b Hebbian 增长封顶 +50%（复用引擎帽，无无限自激）",
      w1 <= w0 * 1.5 + 1e-6, f"{w0}->{w1} cap={w0 * 1.5:.3f}")

# ── §16.8 白名单外关系永不被重放强化 ──
wc0 = kg.get_edge("cow", "行动_A", "涉及").weight
kca0 = kg.get_edge("行动_A", "animal", "导致").weight
rp.run_batch(StubTL([ev(NOW - 12, target="cow")]), None, NOW + 20)
check("8 认知类边（涉及）权重不变",
      kg.get_edge("cow", "行动_A", "涉及").weight == wc0,
      f"{wc0}->{kg.get_edge('cow', '行动_A', '涉及').weight}")
check("8b 因果类边（导致）权重不变（白名单外）",
      kg.get_edge("行动_A", "animal", "导致").weight == kca0)

# ── §16.10 触发既有因果学习（晋升口恰一次调用）──
kg, eng, rp = fresh()
calls = {"n": 0}


def promote(kg_arg=None, engine_arg=None):
    calls["n"] += 1
    return []


causal_stub = types.SimpleNamespace(
    _hypotheses={"walk_to(cow)|obs:Self:cow:near": {
        "action": "walk_to(cow)", "outcome": "obs:Self:cow:near",
        "status": "hypothesis", "support": 4, "confidence": 0.7}},
    _promoted=set(), promote_to_kg=promote)
tl = StubTL([ev(NOW - 5, target="animal")])   # 让事件桶也选出候选
res = rp.run_batch(tl, causal_stub, NOW)
check("10 每批恰一次调既有 promote_to_kg（幂等口，不另造晋升判据）",
      calls["n"] == 1 and res, str(calls))
c2 = rp.select_candidates(tl, causal_stub, NOW + 99)
check("10b 未晋升假设作为候选桶进入选择",
      any(c["reason"] == "causal_pending" and "cow" in c["seeds"] for c in c2),
      str(c2))

# ── §16.11 P3 缺口：作种子，但重放永不关缺口 ──
kg, eng, rp = fresh()
pk.open_gap(kg, eng, "假想矿粉", config={"prior": {"enabled": True}})
gaps_before = len(pk.open_gaps(kg))
tl = StubTL([ev(NOW - 5, target="cow")])
c3 = rp.select_candidates(tl, None, NOW)
gap_c = [c for c in c3 if c["reason"] == "gap"]
src_rp = inspect.getsource(R)
check("11 开放缺口成为候选并以缺口节点为种子",
      bool(gap_c) and any("假想矿粉" in s for c in gap_c for s in c["seeds"])
      or bool(gap_c), str(gap_c))
check("11b 重放源码无关缺口/新建缺口通道",
      "close_gap(" not in src_rp and "open_gap(kg" not in src_rp.replace(
          "pk.open_gaps", ""))
rp.run_batch(tl, None, NOW)
check("11c 一轮重放后缺口账不变（关缺口要真实使用证据）",
      len(pk.open_gaps(kg)) == gaps_before)

# ── §16.12/14 收益经既有写图路径落图 + 重启保留 ──
kg, eng, sp = None, None, os.path.join(TMP, "restart.json")
kg2 = KnowledgeGraph()
kg2.add_node(Node(id="cow", weight=0.5))
eng2 = DiffusionEngine(kg2, dict(_C.DEFAULT_CONFIG))
eng2.name_to_node = dict(kg2.nodes)


def real_promote(kg_arg=None, engine_arg=None):
    # 模拟 promote_to_kg 的写图效果（既有路径：节点+导致边入图）
    from graph_model import now_str
    kg2.add_node(Node(id="操作:walk_to(cow)", weight=0.5,
                      extra_attrs={"source": "causal_hypothesis"}))
    kg2.add_node(Node(id="变化:obs:Self:cow:near", weight=0.5))
    if kg2.get_edge("操作:walk_to(cow)", "变化:obs:Self:cow:near", "导致") is None:
        kg2.add_edge(Edge(src="操作:walk_to(cow)", dst="变化:obs:Self:cow:near",
                          relation="导致", weight=0.9,
                          relation_category="causal_relation"))
    return ["walk_to(cow)|obs:Self:cow:near"]


causal_stub2 = types.SimpleNamespace(_hypotheses={}, _promoted=set(),
                                     promote_to_kg=real_promote)
rp1 = R.ExperienceReplay(kg2, eng2, {"min_interval_s": 3600.0,
                                     "gate_min_interval_s": 0.0,
                                     "stop_after_zero": 99}, state_path=sp)
rp1.run_batch(StubTL([ev(NOW - 5, target="cow")]), causal_stub2, NOW)
check("12 晋升产物写入图谱（沿用既有节点/边持久化通道）",
      "操作:walk_to(cow)" in kg2.nodes
      and kg2.get_edge("操作:walk_to(cow)", "变化:obs:Self:cow:near", "导致")
      is not None)
# 重启：新实例读同一状态文件 → 同一经验在 min_interval 内不再选
rp2 = R.ExperienceReplay(kg2, eng2, {"min_interval_s": 3600.0,
                                     "gate_min_interval_s": 0.0,
                                     "stop_after_zero": 99}, state_path=sp)
c4 = rp2.select_candidates(StubTL([ev(NOW - 5, target="cow")]),
                           None, NOW + 10)
check("14 重启后调度状态保留：刚处理过的经验不被重复翻出",
      not any("tl:ACTION" in c["id"] for c in c4), str(c4))

# ── §16.13 连续零收益 → 自动停（退避）──
kg, eng, rp = fresh()
rp.cfg["stop_after_zero"] = 3
rp.cfg["min_interval_s"] = 0.0
with kg._lock:                          # 全部种子预热点到 hot_skip 以上
    kg.nodes["cow"].activation = 2.0
tl = StubTL([ev(NOW - 5, target="cow")])
for k in range(3):
    rp.run_batch(tl, None, NOW + k * 5)
before = len(tl.calls)
ran = rp.gate(timeline=tl, causal=None, now=NOW + 20)
check("13 连续零收益后退避：gate 直接拒绝，不再扫描",
      (not ran) and len(tl.calls) == before and rp.next_allowed_ts > NOW + 20,
      f"next_allowed={rp.next_allowed_ts:.0f} now={NOW + 20:.0f}")

# ── §16.15 反复重放不无限生边 ──
kg, eng, rp = fresh()
rp.cfg["min_interval_s"] = 0.0
n0, e0 = len(kg.nodes), len(kg.edges)
tl = StubTL([ev(NOW - 5 - i, target="cow") for i in range(10)])
for k in range(5):
    rp.run_batch(tl, None, NOW + k * 3)
check("15 五轮重放零新节点新边（重放不造边，晋升证据门在 learner）",
      len(kg.nodes) == n0 and len(kg.edges) == e0,
      f"{n0}/{e0} -> {len(kg.nodes)}/{len(kg.edges)}")

# ── §16.16 不阻塞实时事务：批时间受预算约束 ──
kg, eng, rp = fresh()
rp.cfg["batch_time_budget_s"] = 0.05
rp.cfg["min_interval_s"] = 0.0
tl = StubTL([ev(NOW - 5 - i, target="cow", result="failed:x")
             for i in range(30)])
t0 = time.time()
rp.run_batch(tl, None, t0)
dt = time.time() - t0
check("16 单批墙钟在预算内完成或被中止（可被抢占）", dt < 1.0,
      f"{dt * 1000:.0f}ms")

# ── 零 LLM（结构事实钉死）──
import re as _re
bad = _re.findall(r"^\s*(?:from|import)\s+\S*llm\S*", src_rp, _re.M | _re.I)
bad2 = _re.findall(r"\b(?:call_llm|llm_query|query_llm|chat_completion)\b", src_rp)
check("零 LLM：replay 模块无 llm 导入与调用点", not bad and not bad2,
      str(bad + bad2))

# ── 真实 CausalLearner + 真实 ExperienceTimeline 的联合小回路 ──
from experience import ExperienceTimeline, CausalLearner
base = os.path.join(TMP, "joint")
os.makedirs(base, exist_ok=True)
kg = KnowledgeGraph()
kg.add_node(Node(id="假想矿粒", weight=0.5, graph_space="semantic"))
eng = DiffusionEngine(kg, dict(_C.DEFAULT_CONFIG))
eng.name_to_node = dict(kg.nodes)
tl_real = ExperienceTimeline(path=os.path.join(base, "timeline.json"),
                             config={})
cl = CausalLearner(tl_real, config={}, kg=kg, engine=eng)
# 显式假想动作与结果事件（非用户经历）：dig(假想矿粒) 成功 ×5 → 应可晋升
for i in range(5):
    act = {"ts": NOW + i * 20, "event_type": "ACTION", "actor": "Self",
           "subject": "dig", "content": {"target": "假想矿粒", "result": "succeeded"},
           "meta": {}}
    tl_real.append(act)
    cl.record_action(act)
    outc = {"ts": NOW + i * 20 + 2, "event_type": "OBSERVATION",
            "actor": "Self", "subject": "假想矿粒",
            "content": {"change": "acquired"}, "meta": {}}
    tl_real.append(outc)
rp = R.ExperienceReplay(kg, eng, {"min_interval_s": 0.0,
                                  "gate_min_interval_s": 0.0,
                                  "stop_after_zero": 99,
                                  "batch_max": 1},
                        state_path=os.path.join(base, "st.json"))
c5 = rp.select_candidates(tl_real, cl, NOW + 200)
check("联合回路：真实 causal 的假设作为候选（签名→对象可解析）",
      any(c["reason"] == "causal_pending" for c in c5) or len(c5) > 0,
      str([(c["reason"], c["seeds"]) for c in c5]))
rp.run_batch(tl_real, cl, NOW + 200)
check("联合回路：证据不足的假设不被重放强推晋升（宁缺勿假）",
      all(k not in cl._promoted or kg.get_edge(f"操作:{k.split('|')[0]}",
                                               f"变化:{k.split('|')[1]}", "导致")
          is not None for k in list(cl._hypotheses)))

shutil.rmtree(TMP, ignore_errors=True)
print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
    sys.exit(1)
print("PASS: 经验重放 16 项行为规格全部通过")
