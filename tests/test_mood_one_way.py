# tests/test_mood_one_way.py — R2 P11 闸门：心情是**单向有界**的派生读数 + 结果侧收口 + 死代码账
# ============================================================================
# P11 的主题不是"心情变了多少"，而是**方向**：
#
#   A 图是真源     `调制器 -[w]-> 心情` 的边定义包络；删一条边效力就少一分；
#                  心情节点**出边为 0**（单向性写在图上）；子图关闭/端点缺失报
#                  `skipped`/`no_node`，不是"静默算成 0 通过"
#   B 数值形状     手工复算 Σ(w×dev) 对得上、顶格被钳在 ±cap、只用 tonic（脉冲不
#                  进包络）、过载反转在这条通道上同样生效、基线上不贡献
#   C 单向性       反复读心情、给心情记事件，**全状态指纹一字不动**；源码里没有
#                  任何调制器/事件写入口；出厂拓扑里心情不是任何边的源
#   D 结果→学习    `learning_modulation` 的系数从 reward.py 搬进 config 之后：
#                  改 config 就改行为、函数体里不再有字面量、缺键时兜底与出厂同数
#   E 人格侧       心情参数（衰减/事件偏移）以 config 为真源，时基与调制器共用，
#                  未接线时逐字退回"纯事件余韵"
#   F 死代码账     审计 §2 的 D-1/D-2/D-3/D-4 复核（P11 计划明列）+ D-7/D-10/D-11
#                  的收口证据 + 「心情不得成为万能变量」
#
# **注意"无环路"测的是数值反哺**：Modulator→心情→行为→结果→事件→调制器 这条
# 行为环恰恰是规格要的（人格只能这样改变），它经由真实世界的一拍，不是同拍的数值反馈。
# 全程不碰真实图谱：自建小图 + 临时目录。
# ============================================================================
import copy
import inspect
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config as C                                          # noqa: E402
from graph_model import Edge, KnowledgeGraph, Node           # noqa: E402
from internal_state import InternalState                     # noqa: E402
from personality_baseline import (MOOD_DECAY_PER_HOUR,       # noqa: E402
                                  MOOD_EVENT_EFFECTS,
                                  PersonalityBaseline)
import modulator_subgraph as MS                              # noqa: E402
import reward as RW                                          # noqa: E402

FAILURES = []
PASSED = [0]
MODS = tuple(sorted(((C.DEFAULT_CONFIG.get("modulator_system") or {})
                     .get("specs") or {}).keys()))
MOOD_NODE = "心情"
CAP = C.DEFAULT_CONFIG["modulator_system"]["mood"]["cap"]


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
    else:
        FAILURES.append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" | {detail}" if detail and not cond else ""))


def code_only(path):
    """读源码并剥掉注释（整行与行内）：扫描判据该看代码，不该看注释。"""
    out = []
    for line in open(os.path.join(ROOT, path), encoding="utf-8").read().splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        out.append(re.sub(r"(?<![\w'\"])#.*$", "", line))
    return "\n".join(out)


def build(config=None):
    """小图 + 真 InternalState + 调制子图（本闸门只需要调制器镜像与心情端点）。"""
    cfg = copy.deepcopy(config if config is not None else C.DEFAULT_CONFIG)
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", label="declarative-semantic",
                     graph_space="self", weight=1.0))
    for nid in ("CENetwork", "DMNetwork", "CuriosityDrive", "SocialDrive",
                "LearningDrive", "ConsistencyDrive"):
        if kg.get_node(nid) is None:
            kg.add_node(Node(id=nid, label="declarative-semantic",
                             graph_space="cognitive", weight=0.5))
    st = InternalState(kg=kg, config=cfg, data_dir=tempfile.mkdtemp())
    st.sync_graph()
    boot = MS.ensure_modulator_subgraph(kg, st, cfg)
    return kg, st, cfg, boot


def fingerprint(kg, st):
    """"心情反哺激素"能改到的所有量：12 个通道的两个真值 + 需求水位 + 图上全部激活。"""
    return (
        tuple((m, round(st.modulator_tonic(m), 10), round(st.modulator_pulse(m), 10))
              for m in MODS),
        tuple((n, round(st.need_level(n), 10)) for n in st.need_names()),
        tuple(sorted((nid, round(float(n.activation or 0.0), 10))
                     for nid, n in kg.nodes.items())),
        len(kg.edges),
    )


def mood_edges(kg):
    return [(e.src, e.relation, e.dst, float(e.weight))
            for e in kg.get_in_edges(MOOD_NODE)]


def spec_of(node_id, st):
    return next((k for k, v in st.mirror_modulator.items() if v == node_id), node_id)


# ── A 图是真源 ────────────────────────────────────────────
kg, st, cfg, boot = build()
check("A1 心情节点被种入（bootstrap 报 mood_node_seeded=1）",
      boot.get("mood_node_seeded") == 1 and kg.get_node(MOOD_NODE) is not None,
      str({k: boot[k] for k in ("mood_node_seeded", "edges", "edges_skipped")}))
_mn = kg.get_node(MOOD_NODE)
check("A2 心情在 self 空间、类型 mood_state、标明数值真源在 personality",
      _mn.graph_space == "self" and (_mn.extra_attrs or {}).get("type") == "mood_state"
      and (_mn.extra_attrs or {}).get("numeric_owner") == "personality_baseline",
      str(_mn.extra_attrs))
check("A3 出厂 5 条 `调制器→心情` 边全部落地（没有一条被静默跳过）",
      len(mood_edges(kg)) == 5, str(mood_edges(kg)))
check("A4 心情节点**出边为 0**（单向性写在图上，不靠口头约定）",
      len(kg.get_out_edges(MOOD_NODE)) == 0, str(kg.get_out_edges(MOOD_NODE)))
_n0 = len(mood_edges(kg))
MS.ensure_modulator_subgraph(kg, st, cfg)
check("A5 重复 bootstrap 幂等（心情相关边不翻倍）",
      len(mood_edges(kg)) == _n0, str(len(mood_edges(kg))))
check("A6 边表里没有一行以心情为**源**（出厂拓扑里不存在反向通道）",
      not any(spec_of(str(r[0]), st) == MOOD_NODE
              for r in cfg["modulator_system"]["edges"]), "")
kg_x, st_x, cfg_x, _ = build(config={**copy.deepcopy(C.DEFAULT_CONFIG),
                                     "modulator_system": {
                                         **C.DEFAULT_CONFIG["modulator_system"],
                                         "subgraph": False}})
_p_off = MS.mood_projection(kg_x, st_x, cfg_x)
check("A7 子图关闭 ⇒ mood_projection 报 skipped（不是 term=0 假装通过）",
      _p_off.get("skipped") is True, str(_p_off))
_kg_nc, _st_nc, _cfg_nc, _ = build()
_cfg_nc["modulator_system"]["mood"] = {"node": "心情（没这个点）", "cap": CAP}
_p_nn = MS.mood_projection(_kg_nc, _st_nc, _cfg_nc)
check("A8 端点节点不存在 ⇒ no_node（明说不存在，而不是给个 0 混过去）",
      _p_nn.get("no_node") is True and _p_nn["term"] == 0.0, str(_p_nn))
kg3, st3, cfg3, _ = build()
st3.set_value("modulator", "serotonin", 0.95, reason="probe")
_before = MS.mood_projection(kg3, st3, cfg3)["term"]
_e = kg3.get_edge("血清素样", MOOD_NODE, "增强")
kg3.remove_edge(_e.src, _e.dst, _e.relation)
_after9 = MS.mood_projection(kg3, st3, cfg3)["term"]
check("A9 删一条边 ⇒ 包络立刻变小（图是权威，不是 config 里另算一遍）",
      abs(_after9) < abs(_before), f"{_before} → {_after9}")

# ── B 数值形状 ────────────────────────────────────────────
kg, st, cfg, boot = build()
for m, v in (("serotonin", 0.95), ("cortisol", 0.62), ("dopamine", 0.80),
             ("oxytocin", 0.75), ("melatonin", 0.20)):
    st.set_value("modulator", m, v, reason="probe")
_p = MS.mood_projection(kg, st, cfg)
_manual = sum(w * float(st.modulator_dev(spec_of(src, st)))
              for src, _rel, _dst, w in mood_edges(kg))
check("B1 term == 手工复算的 Σ(边权 × dev)（与参数通道同一个 dev，不另起曲线）",
      abs(_manual - _p["raw"]) < 1e-6, f"manual={_manual:.6f} raw={_p['raw']}")
check("B2 每条贡献行都对得上它自己那条边（可解释、可复算）",
      len(_p["rows"]) == _p["edges_used"]
      and all(abs(r["weight"] * r["dev"] - r["contrib"]) < 5e-4 for r in _p["rows"]),
      str(_p["rows"]))
_exp_rows = {spec_of(src, st) for src, _r, _d, _w in mood_edges(kg)
             if abs(float(st.modulator_dev(spec_of(src, st)))) > 1e-9}
check("B2b 基线上的调制器不出现（贡献集正好等于'偏离基线的那几个'，不多不少）",
      {r["src"] for r in _p["rows"]} == _exp_rows,
      f"rows={sorted(r['src'] for r in _p['rows'])} expect={sorted(_exp_rows)}")
# 顶格 ≠ 最大贡献：多数通道过了过载点就反转（B5 证的就是这个），所以"把包络推到
# 界外"要按**受体曲线的峰值**来摆，再配上加宽的边（|w|=2 是 Edge 的权重上限）。
for m in ("serotonin", "cortisol", "dopamine", "oxytocin", "melatonin"):
    st.set_value("modulator", m, float(st.modulator_field(m, "overload", 0.8)),
                 reason="pin peak")
    kg.add_edge(Edge(src=st.mirror_modulator[m], dst=MOOD_NODE, relation="增强",
                     weight=2.0, relation_category="causal_relation"))
_pmax = MS.mood_projection(kg, st, cfg)
check("B3 包络被钳在 ±cap 且报 capped（拓扑被改宽也钉不住心情）",
      abs(_pmax["term"]) <= CAP + 1e-9 and _pmax["capped"] is True
      and _pmax["raw"] > CAP, str({k: _pmax[k] for k in ("raw", "term", "cap")}))
kg, st, cfg, boot = build()
st.set_value("modulator", "serotonin", 0.90, reason="probe")
_t0 = MS.mood_projection(kg, st, cfg)["term"]
st.pulse("dopamine", 0.6, reason="probe pulse")
check("B4 phasic 脉冲**不进包络**（tonic 没动 ⇒ term 一字不变；快信号归事件余韵管）",
      MS.mood_projection(kg, st, cfg)["term"] == _t0
      and st.modulator_pulse("dopamine") > 0.0,
      f"{_t0} vs {MS.mood_projection(kg, st, cfg)['term']}")
kg, st, cfg, boot = build()


def _cort_contrib():
    rows = {r["src"]: r for r in MS.mood_projection(kg, st, cfg)["rows"]}
    return (rows.get("cortisol") or {}).get("contrib")


st.set_value("modulator", "cortisol", 0.62, reason="probe")
_mid = _cort_contrib()
st.set_value("modulator", "cortisol", 0.99, reason="probe overload")
_over = _cort_contrib()
check("B5 过载反转在心情通道上同样生效（皮质醇钉过过载点后贡献翻正，不再往下钉）",
      _mid is not None and _mid < 0 and _over is not None and _over > 0,
      f"mid={_mid} overload={_over}")
kg, st, cfg, boot = build()
_p0 = MS.mood_projection(kg, st, cfg)
check("B6 一切都在基线 ⇒ edges_used=0 且 term=0（无调制就不贡献）",
      _p0["edges_used"] == 0 and _p0["term"] == 0.0, str(_p0))
kg, st, cfg, boot = build()
pb = PersonalityBaseline(kg, None, config=cfg)
pb.set_mood_source(lambda: MS.mood_projection(kg, st, cfg))
st.set_value("modulator", "serotonin", 0.85, reason="probe")   # 正向包络一项
for _ in range(20):
    pb.mood_event("positive")                                  # 余韵堆到顶（钳 ±1）
_m = pb.current_mood()
check("B7 valence 恒在 [-1,1]：余韵钉满 + 包络同号 ⇒ raw 越界必须被钳并报 clipped",
      _m["valence"] == 1.0 and _m["raw"] > 1.0 and _m["clipped"] is True
      and _m["modulator_term"] > 0, str(_m))

# ── C 单向性（本闸门主题）──────────────────────────────────
kg, st, cfg, boot = build()
pb = PersonalityBaseline(kg, None, config=cfg)
pb.set_mood_source(lambda: MS.mood_projection(kg, st, cfg))
st.set_value("modulator", "cortisol", 0.70, reason="probe")
for m in MODS:
    st.pulse(m, 0.2, reason="probe")
fp0 = fingerprint(kg, st)
for _ in range(6):
    pb.current_mood()
    pb.mood_context()
    pb.afterglow()
fp1 = fingerprint(kg, st)
check("C1 反复读心情（含包络编译 + 语气渲染）⇒ 全状态指纹一字不动（纯读）",
      fp0 == fp1, "")
for kind in ("positive", "negative", "positive"):
    pb.mood_event(kind)
    pb.current_mood()
fp2 = fingerprint(kg, st)
check("C2 给心情记事件 ⇒ 调制器/需求/图上激活/边数仍一字不动（不反哺激素）",
      fp1 == fp2, "")
_src = code_only("personality_baseline.py")
_WRITERS = (r"\bapply_delta\s*\(", r"\bset_value\s*\(", r"\bpulse\w*\s*\(",
            r"\bapply_event\s*\(", r"\bemit\s*\(", r"\brelease\s*\(",
            r"\btick_decay\s*\(", r"\bmark_active\s*\(", r"\bset_modulator")
_hits = [p for p in _WRITERS if re.search(p, _src)]
check("C3 源码扫描：personality_baseline.py 里没有任何调制器/事件写入口",
      not _hits, str(_hits))
check("C4 心情节点出边为 0（C3 的图侧对偶：结构上也没有反哺路径）",
      len(kg.get_out_edges(MOOD_NODE)) == 0, "")
_pb_plain = PersonalityBaseline(kg, None, config=cfg)
_pb_plain.mood_event("positive")
_v_plain = _pb_plain.current_mood()
check("C5 未注入包络 ⇒ 心情 == 纯事件余韵（R2 之前的行为一字不变）",
      _v_plain["modulator_term"] == 0.0 and _v_plain["edges_used"] == 0
      and abs(_v_plain["valence"] - _v_plain["afterglow"]) < 1e-9, str(_v_plain))
_pb_err = PersonalityBaseline(kg, None, config=cfg)
_pb_err.mood_event("positive")
_pb_err.set_mood_source(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
_v_err = _pb_err.current_mood()
check("C6 包络闭包抛异常 ⇒ 心情仍只出余韵那半（不抛、不编造默认值）",
      _v_err["modulator_term"] == 0.0
      and abs(_v_err["valence"] - _v_err["afterglow"]) < 1e-9, str(_v_err))

# ── D 结果→学习 的收口（§24）──────────────────────────────
kg, st, cfg, boot = build()
lm = cfg["modulator_system"]["learning_modulation"]


def cfg_phasic(base_cfg, coef):
    c2 = copy.deepcopy(base_cfg)
    c2["modulator_system"]["learning_modulation"]["phasic_coef"] = coef
    return c2


st.set_value("modulator", lm["tonic_mod"], 0.66, reason="probe")
st.set_value("modulator", lm["stress_mod"], 0.55, reason="probe")
st.pulse(lm["phasic_mod"], 0.40, reason="probe")
rs = RW.RewardSystem(internal_state=st, config=cfg)
comp = rs.modulation(surprise=0.5)
_d_tonic = (st.modulator_tonic(lm["tonic_mod"])
            - st.modulator_baseline(lm["tonic_mod"]))
_d_cort = max(0.0, st.modulator_tonic(lm["stress_mod"])
              - st.modulator_baseline(lm["stress_mod"]))
_expect = (float(lm["base"]) + float(lm["phasic_coef"]) * 0.40
           + float(lm["tonic_coef"]) * _d_tonic
           - float(lm["stress_coef"]) * _d_cort)
_expect = max(float(lm["min"]), min(float(lm["max"]),
                                    _expect * (1.0 + float(lm["surprise_coef"]) * 0.5)))
check("D1 factor 与 config 系数手算一致（真源在 config，不在函数体）",
      abs(comp["factor"] - round(_expect, 3)) < 1e-9,
      f"{comp['factor']} vs {_expect:.3f} {comp}")
_f_zero = RW.RewardSystem(internal_state=st,
                          config=cfg_phasic(cfg, 0.0)).modulation(surprise=0.5)["factor"]
check("D2 改 config 的 phasic_coef=0 ⇒ factor 变小（config 有牙）",
      _f_zero < comp["factor"], f"{comp['factor']} → {_f_zero}")
_body = inspect.getsource(RW.RewardSystem.modulation)
# 扫描**代码**，不扫 docstring：函数头那段公式里出现 0.4/1.8 是对兜底值的说明，
# 不是第二条真源（真源丢了的话 D4 那条断言会红）。
_code_part = _body.split('"""')[2] if _body.count('"""') >= 2 else _body
_lit = [x for x in ("0.60", "0.15", "0.25", "0.30", "1.8", "0.4")
        if re.search(r"(?<![\w.])" + re.escape(x) + r"(?![\w])", _code_part)]
check("D3 源码扫描：modulation() 函数体里不再留着那六个字面量",
      not _lit, str(_lit))
check("D4 缺 learning_modulation 键时退回模块常量，且兜底与出厂同数（裸装配复现生产形状）",
      RW.DEFAULT_LEARNING_MOD == {k: lm[k] for k in RW.DEFAULT_LEARNING_MOD},
      f"{RW.DEFAULT_LEARNING_MOD} vs {lm}")
check("D5 输出键集合向后兼容（base/phasic/tonic/stress/factor 都在）",
      {"base", "phasic", "tonic", "stress", "factor"} <= set(comp), str(sorted(comp)))
kg, st, cfg, boot = build()
rs = RW.RewardSystem(internal_state=st, config=cfg)
_t_before = rs.modulation()["tonic"]
st.pulse(lm["tonic_mod"], 0.5, reason="probe pulse only")
check("D6 三个读数互不重复计数：只打脉冲 ⇒ 慢分量项不变（脉冲只进 phasic 项）",
      rs.modulation()["tonic"] == _t_before
      and rs.modulation()["phasic"] > 0.0,
      f"tonic {_t_before} → {rs.modulation()['tonic']}")

# ── E 人格侧参数集中 + 统一时基 ────────────────────────────
kg, st, cfg, boot = build()
_mcfg = cfg["personality"]["mood"]
check("E1 config 的出厂值与模块兜底同数（§24：config 是真源，模块常量是没接线时的兜底）",
      abs(float(_mcfg["decay_per_hour"]) - MOOD_DECAY_PER_HOUR) < 1e-12
      and _mcfg["event_effects"] == MOOD_EVENT_EFFECTS, str(_mcfg))
clock = {"t": 1000.0}
pb_slow = PersonalityBaseline(kg, None, config=cfg, clock=lambda: clock["t"])
pb_slow.mood_event("positive")                        # +0.12
clock["t"] += 3600.0
check("E2 余韵按 config 的 decay_per_hour 线性衰减（1 小时 ⇒ 0.12→0.102）",
      abs(pb_slow.afterglow() - 0.12 * (1 - 0.15)) < 1e-6, str(pb_slow.afterglow()))
c_fast = copy.deepcopy(cfg)
c_fast["personality"]["mood"]["decay_per_hour"] = 0.50
pb_fast = PersonalityBaseline(kg, None, config=c_fast, clock=lambda: clock["t"])
pb_fast.mood_event("positive")
clock["t"] += 3600.0
check("E3 改 config 的 decay ⇒ 衰减速度真的跟着变（不是硬编码在模块里）",
      abs(pb_fast.afterglow() - 0.12 * (1 - 0.50)) < 1e-6, str(pb_fast.afterglow()))
c_fx = copy.deepcopy(cfg)
c_fx["personality"]["mood"]["event_effects"] = {"positive": 0.03}
pb_fx = PersonalityBaseline(kg, None, config=c_fx)
pb_fx.mood_event("positive")
check("E4 事件偏移也走 config 表（0.03 生效，出厂 0.12 不残留）",
      abs(pb_fx.afterglow() - 0.03) < 1e-9, str(pb_fx.afterglow()))
st.set_clock(lambda: clock["t"])
clock["t"] = 5000.0
pb_clk = PersonalityBaseline(kg, None, config=cfg, clock=lambda: st.now())
st.set_value("modulator", "cortisol", 0.90, reason="probe")
_cort_before = st.modulator_tonic("cortisol")
pb_clk.mood_event("positive")
clock["t"] += 2 * 3600.0
_after_e5 = pb_clk.afterglow()
st.tick_decay()                                       # 调制器在同一条时间轴上老 2 小时
check("E5 心情与调制器共用时基：注入后一起老（余韵按注入钟衰减）",
      abs(_after_e5 - 0.12 * (1 - 0.15 * 2)) < 1e-6, str(_after_e5))
check("E5b 同一次钟推进也让调制器衰减（两侧同源，不是一侧走一侧停）",
      st.modulator_tonic("cortisol") < _cort_before,
      f"{_cort_before} → {st.modulator_tonic('cortisol')}")
st.set_clock(None)
check("E6 不注入时基时 `now()==time.time()`（生产逐字不变）",
      abs(st.now() - time.time()) < 5.0, "")

# ── F 死代码账 §2 复核 ────────────────────────────────────
kg, st, cfg, boot = build()
st.set_value("modulator", "serotonin", 0.80, reason="probe")
_proj = MS.projection(kg, st, cfg)
_ser = sorted(p for p, row in _proj["rows"].items() if "hormone.serotonin" in row)
check(f"F1 审计 D-1：血清素现在真的改变参数（投影出 {len(_ser)} 行系数，不是图上好看、场里恒 0）",
      len(_ser) >= 1, str(_ser))
_rm_body = inspect.getsource(RW.RewardSystem.modulation)
check("F2 审计 D-2：结果→学习读 tonic 与 pulse，不读 level（脉冲不被算两遍）",
      "modulator_tonic(" in _rm_body and "modulator_pulse(" in _rm_body
      and "modulator_level(" not in _rm_body, "")
_cc = code_only("continuous_cognition.py")
check("F3 审计 D-3：retrieval.topk_scale 与 retrieval.reignite_every 都有了真消费者",
      "retrieval.topk_scale" in _cc and "retrieval.reignite_every" in _cc, "")
_cf = code_only("cognitive_field.py")
check("F4 审计 D-4：`need.*` 前缀在 _build_signals 里有生产端（effects 那行不再是空乘数）",
      re.search(r'sig\[\s*"need\."\s*\+', _cf) is not None, "")
_kg7 = KnowledgeGraph()
_kg7.add_node(Node(id="Self", label="declarative-semantic",
                   graph_space="self", weight=1.0))
_kg7.add_node(Node(id="CENetwork", label="declarative-semantic",
                   graph_space="cognitive"))
_kg7.add_edge(Edge(src="Self", dst="CENetwork", relation="网络", weight=0.3,
                   relation_category="cognitive_relation"))
check("F5 审计 D-7：`网络` 已注册进关系词表，写出的边落盘仍是 `网络`（不再被兜底成 `关联`）",
      "网络" in cfg["relation_ontology"]
      and _kg7.get_edge("Self", "CENetwork", "网络") is not None,
      str([(e.src, e.relation, e.dst) for e in _kg7.edges]))
check("F6 审计 D-10：`novel_experience` 这一档压力现在有 emit 点（此前有表有金额、没有源）",
      'note_pressure("novel_experience")' in _cc and "novel_min_novelty" in _cc, "")
from drive_engine import DriveEvaluator                      # noqa: E402
kg4, st4, cfg4, _ = build()
ev = DriveEvaluator(kg4, cfg4)
ev.bootstrap_drives()
ev.evaluate(force=True)
_u0 = ev.field.tensions.levels().get("unexpected_event", -1.0)
kg4.add_node(Node(id="事件类型:prediction_violation", label="declarative-semantic",
                  graph_space="cognitive", weight=0.5))
kg4.nodes["事件类型:prediction_violation"].activation = 4.0
ev.evaluate(force=True)
_u1 = ev.field.tensions.levels().get("unexpected_event", -1.0)
check("F7 审计 D-11：`graph.cognitive_events` 采样器注册了——点亮预测违背事件节点后"
      " unexpected_event 张力抬起来（此前恒 0）",
      _u0 <= 0.001 and _u1 > _u0, f"{_u0} → {_u1}")
_is = code_only("internal_state.py")
check("F8 审计 D-8：紧迫度只有一个口径——`need_salience()` 读 urgency 字段，"
      "没有第二条并行的紧迫度算法",
      re.search(r'it\.get\(\s*"urgency"', _is) is not None, "")
check("F9 「心情不得成为万能变量」：它不出现在任何 effects 系数行，也不是任何边的源",
      not any("mood" in k for row in _proj["rows"].values() for k in row)
      and not any(spec_of(str(r[0]), st) == MOOD_NODE
                  for r in cfg["modulator_system"]["edges"]), "")

print()
if FAILURES:
    print(f"✗ R2 P11 闸门 {len(FAILURES)}/{PASSED[0] + len(FAILURES)} 未过："
          + "；".join(FAILURES))
    sys.exit(1)
print(f"✓ R2 P11 闸门全部通过（{PASSED[0]} 项）")
