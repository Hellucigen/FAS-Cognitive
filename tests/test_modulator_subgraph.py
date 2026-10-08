# test_modulator_subgraph.py — R2 P6 闸门：调制器成为图谱公民
# ============================================================================
# 锁住四件事（缺一件，"激素参与扩散"就仍然是假话，见审计 §1 Q6）：
#   A 幂等 bootstrap：14 靶点节点 + 心情锚点 + 边表全部行（现 69 条：P7 +4 `交互`、
#     P9 +7 需求↔调制器、P10 +6 昼夜、P11 +5 调制器→心情）+ 12 条 Self 锚定边，
#     重复调用零新增
#   B 投影正确：coef = 权重 × sensitivity；R2 前那 7 行出厂系数被逐字复现（迁移无损）
#   C **图是真源**：改边→系数变；删边→系数被回收（不是留在调制层里当影子真源）
#   D 双通道：phasic→图激活只在**事件**里发生（阈值/开关/无引擎都不得注入），
#     每拍衰减不得注入——每拍补激活就是 09-13 全图饱和事故的形状
#   E 能量有界：空闲长跑排空；持续脉冲期间不出现满格钉死
#   F 孤岛结论作废：12 个调制器节点不再 0 出边（P4 的 D-15 由本阶段关闭）
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_modulator_subgraph.py
# ============================================================================
import copy
import logging
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.WARNING)

import config as cfgmod
from graph_model import KnowledgeGraph, Node, Edge
from diffusion_engine import DiffusionEngine
from internal_state import InternalState
from modulation import ModulationLayer
import modulator_subgraph as MS
import temporal_awareness as TA

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class FakeEngine:
    """只记录调用：证明注入走了 Drive 那套（frontier + 来源登记），不是裸写属性。"""

    def __init__(self, kg=None):
        self.kg = kg
        self.marked = []
        self.sources = []

    def mark_active(self, ids):
        self.marked.append(list(ids))

    def register_activation_source(self, ids, source_type="external_input"):
        self.sources.append((list(ids), source_type))


def tiny_kg(with_graph_nodes=False):
    """小图：够放调制器镜像；`with_graph_nodes=True` 时补上边表引用的真实节点。"""
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", label="self", graph_space="self"))
    for nid in ("甲", "乙"):
        kg.add_node(Node(id=nid, label="declarative-semantic"))
        kg.add_edge(Edge(src="Self", dst=nid, relation="关联", weight=0.6,
                         relation_category="semantic_relation"))
    if with_graph_nodes:
        for nid in ("CENetwork", "DMNetwork", "CuriosityDrive", "SocialDrive",
                    "ConsistencyDrive", "LearningDrive", "行为:探索", "行为:分享",
                    "行为:沉默", "行为:共情", "行为:延续"):
            kg.add_node(Node(id=nid, label="declarative-semantic",
                             graph_space="cognitive"))
        for nid in ("安全需求", "探索需求", "掌控需求", "社交需求", "生存需求"):
            kg.add_node(Node(id=nid, label="declarative-semantic",
                             graph_space="self"))
        # P10 的昼夜边（夜间/白天/深夜/凌晨 → 褪黑素样/组胺样）引用时段节点。
        # 生产里这些节点由 `update_clock_state`(→ensure_bucket_nodes) 在 bootstrap
        # 之前建好（app.py 时钟初始化早于调制子图装配），所以这里用**同一个播种函数**
        # 补上真实依赖，而不是放宽 A2 的"零跳过"——边表引用不存在的端点仍必须是错。
        TA.ensure_bucket_nodes(kg)
    return kg


def build(config=None, kg=None, engine=None, sync=True):
    cfg = config if config is not None else copy.deepcopy(cfgmod.DEFAULT_CONFIG)
    kg = kg if kg is not None else tiny_kg(with_graph_nodes=True)
    tmp = tempfile.mkdtemp()
    st = InternalState(kg=kg, config=cfg, engine=engine, data_dir=tmp)
    if sync:
        st.sync_graph()
    return st, kg, cfg, tmp


# ── A 幂等 bootstrap ──────────────────────────────────────

print("── A bootstrap 形状与幂等 ──")
st, kg, cfg, _ = build()
blk = cfg["modulator_system"]
boot = MS.ensure_modulator_subgraph(kg, st, cfg)
n_edges_before = len(kg.edges)
check("A1 靶点节点数 == config.targets 数",
      boot["targets"] == len(blk["targets"]), str(boot["targets"]))
check("A2 边表全部可落地（零跳过）", boot["edges_skipped"] == 0,
      str(boot.get("skipped")))
check("A3 建边数 == 边表行数", boot["edges"] == len(blk["edges"]),
      f"{boot['edges']} vs {len(blk['edges'])}")
check("A4 锚定边 == 12（每调制器一条 Self-[处于]->）", boot["anchors"] == 12,
      str(boot["anchors"]))
check("A5 无符号约定矛盾", not boot["sign_mismatch"], str(boot["sign_mismatch"]))
tgt = kg.get_node("调制目标:扩散增益")
check("A6 靶点节点是 infrastructure/cognitive 且带 param",
      tgt is not None and tgt.label == "infrastructure"
      and tgt.graph_space == "cognitive"
      and (tgt.extra_attrs or {}).get("param") == "diffusion.param_gain",
      str(tgt and (tgt.label, tgt.graph_space, tgt.extra_attrs)))
check("A7 调制边带因果类别",
      all(e.relation_category == "causal_relation"
          for e in kg.get_out_edges("多巴胺样")))
boot2 = MS.ensure_modulator_subgraph(kg, st, cfg)
check("A8 二次调用零新增（幂等）",
      boot2["edges"] == 0 and boot2["targets"] == 0 and boot2["anchors"] == 0
      and len(kg.edges) == n_edges_before,
      str((boot2, len(kg.edges) - n_edges_before)))
# 端点缺失只跳过、不为此造点
kg_small = tiny_kg(with_graph_nodes=False)
st_s, _, cfg_s, _ = build(kg=kg_small)
boot_s = MS.ensure_modulator_subgraph(kg_small, st_s, cfg_s)
check("A9 端点缺失的边被跳过且不新建语义空洞节点",
      boot_s["edges_skipped"] > 0 and kg_small.get_node("CENetwork") is None
      and boot_s["edges"] + boot_s["edges_skipped"] == len(cfg_s["modulator_system"]["edges"]),
      str((boot_s["edges"], boot_s["edges_skipped"])))
# subgraph=False：整段关闭，不建任何东西
cfg_off = copy.deepcopy(cfgmod.DEFAULT_CONFIG)
cfg_off["modulator_system"]["subgraph"] = False
st_o, kg_o, _, _ = build(config=cfg_off)
b_o = MS.ensure_modulator_subgraph(kg_o, st_o, cfg_off)
check("A10 subgraph=False → 零节点零边（开关有真消费者）",
      b_o["targets"] == 0 and b_o["edges"] == 0 and b_o.get("skipped")
      and kg_o.get_node("调制目标:回答温度") is None)
cfg_ns = copy.deepcopy(cfgmod.DEFAULT_CONFIG)
cfg_ns["modulator_system"]["link_to_self"] = False
st_n, kg_n, _, _ = build(config=cfg_ns)
b_n = MS.ensure_modulator_subgraph(kg_n, st_n, cfg_ns)
check("A11 link_to_self=False → 零锚定边（另一个开关也有消费者）",
      b_n["anchors"] == 0 and kg_n.get_edge("Self", "多巴胺样", "处于") is None,
      str(b_n["anchors"]))

# ── B 投影正确性 ──────────────────────────────────────────

print("── B 投影：边 → 系数 ──")
mod = ModulationLayer(cfg["modulation"]["params"])
proj = MS.project_coefficients(kg, st, mod, cfg)
pr = proj["projection"]
check("B1 拓扑健康：无冲突/无孤儿/无纯负出边调制器",
      not pr["conflicts"] and not pr["orphans"] and not pr["no_positive_out"],
      str((pr["conflicts"], pr["orphans"], pr["no_positive_out"])))
check("B2 读到的边 == 写出的系数行（没有静默丢边）",
      pr["edges_read"] == proj["applied"], f"{pr['edges_read']} vs {proj['applied']}")
# 迁移无损：R2 之前 config.modulation 里的 7 行 hormone.* 系数必须逐字复现
legacy = {p: {k: v for k, v in ((s or {}).get("effects") or {}).items()
              if k.startswith("hormone.")}
          for p, s in cfgmod.DEFAULT_CONFIG["modulation"]["params"].items()}
legacy = {p: e for p, e in legacy.items() if e}
n_legacy = sum(len(e) for e in legacy.values())
bad = [(p, k, v, mod.effects(p).get(k))
       for p, e in legacy.items() for k, v in e.items()
       if abs(float(mod.effects(p).get(k, -999)) - float(v)) > 1e-9]
check(f"B3 R2 前的 {n_legacy} 行出厂系数被图投影逐字复现（迁移无损）",
      not bad and n_legacy == 7, str(bad))
check("B4 系数 = 权重 × sensitivity（抽样 5 条）",
      all(abs(mod.effects(p)[k] -
              float(kg.get_edge(st.mirror_modulator[k.split('.')[1]],
                                f"调制目标:{short}",
                                rel).weight) *
              float(st.modulator_field(k.split('.')[1], "sensitivity", 1.0))) < 1e-6
          for p, short, rel, k in [
              ("diffusion.emission_ratio", "扩散发射配比", "增强", "hormone.dopamine"),
              ("cognition.express_threshold", "表达阈值", "抑制", "hormone.oxytocin"),
              ("action.score_threshold", "行动门槛", "调制", "hormone.serotonin"),
              ("diffusion.inter_round_decay", "扩散轮间衰减", "增强", "hormone.cortisol"),
              ("llm.budget_factor", "认知预算", "增强", "hormone.histamine")]),
      "见 config.modulator_system.edges")
kg.add_edge(Edge(src="多巴胺样", dst="谷氨酸样", relation="交互", weight=0.5,
                 relation_category="causal_relation"))
pr_sym = MS.projection(kg, st, cfg)
check("B5 交互边（调制器↔调制器，对称耦合）不产参数系数",
      pr_sym["edges_read"] == pr["edges_read"] and pr_sym["rows"] == pr["rows"],
      f"{pr['edges_read']} → {pr_sym['edges_read']}")
kg.remove_edge("多巴胺样", "谷氨酸样", "交互")
n_hormone_rows = sum(1 for p in mod.snapshot() for k in mod.effects(p)
                     if k.startswith("hormone."))
check("B6 图上没有的调制器不会凭空出现在系数表里",
      n_hormone_rows == pr["edges_read"], f"{n_hormone_rows} vs {pr['edges_read']}")
check("B7 投影不污染 DEFAULT_CONFIG（effects 子表按实例隔离）",
      ModulationLayer(cfgmod.DEFAULT_CONFIG["modulation"]["params"]).effects(
          "action.score_threshold").get("hormone.melatonin") is None)

# ── C 图是真源 ────────────────────────────────────────────

print("── C 改图 = 改效力 ──")
e = kg.get_edge("多巴胺样", "调制目标:扩散发射配比", "增强")
old_w, old_c = float(e.weight), mod.effects("diffusion.emission_ratio")["hormone.dopamine"]
e.weight = 0.35
MS.project_coefficients(kg, st, mod, cfg)
check("C1 改边权重 → 系数跟着变（图是权威，不是 config）",
      abs(mod.effects("diffusion.emission_ratio")["hormone.dopamine"] - 0.35) < 1e-9,
      str(mod.effects("diffusion.emission_ratio")))
kg.remove_edge("多巴胺样", "调制目标:扩散发射配比", "增强")
others_before = {k: v for k, v in mod.effects("diffusion.emission_ratio").items()
                 if not k.startswith("hormone.")}
MS.project_coefficients(kg, st, mod, cfg)
check("C2 删边 → 系数被回收（调制层里不留影子真源）",
      "hormone.dopamine" not in mod.effects("diffusion.emission_ratio"),
      str(mod.effects("diffusion.emission_ratio")))
check("C3 回收只动激素行，非激素信号行（network./tension.）原位不动",
      mod.effects("diffusion.emission_ratio") == others_before and others_before,
      str(mod.effects("diffusion.emission_ratio")))
kg.add_edge(Edge(src="多巴胺样", dst="调制目标:扩散发射配比", relation="增强",
                 weight=old_w, relation_category="causal_relation"))
MS.project_coefficients(kg, st, mod, cfg)
check("C4 把边加回去 → 系数恢复（往返可逆）",
      abs(mod.effects("diffusion.emission_ratio")["hormone.dopamine"] - old_c) < 1e-9,
      str(mod.effects("diffusion.emission_ratio")))
# 撞键报告（靶点节点的 param 由 bootstrap 就地对齐，所以改完表要重跑一次）
kg.add_edge(Edge(src="多巴胺样", dst="调制目标:扩散深度", relation="增强",
                 weight=0.95, relation_category="causal_relation"))
cfg["modulator_system"]["targets"]["扩散深度"] = "diffusion.emission_ratio"
MS.ensure_modulator_subgraph(kg, st, cfg)      # 幂等；把节点 param 对齐到新映射
pr2 = MS.projection(kg, st, cfg)
check("C5 撞键边被报告并保留 |系数| 大的那条（不随遍历顺序漂移）",
      any(c["param"] == "diffusion.emission_ratio" and
          c["signal"] == "hormone.dopamine" and abs(c["kept"] - 0.95) < 1e-9
          and abs(c["dropped"] - old_c) < 1e-9 for c in pr2["conflicts"]),
      str(pr2["conflicts"]))
kg.remove_edge("多巴胺样", "调制目标:扩散深度", "增强")
cfg["modulator_system"]["targets"]["扩散深度"] = "diffusion.max_depth"
MS.ensure_modulator_subgraph(kg, st, cfg)
check("C6 恢复拓扑后回到零冲突（报告不残留）",
      not MS.projection(kg, st, cfg)["conflicts"])

# ── D phasic → 图激活 ─────────────────────────────────────

print("── D 图通道：只在事件里点亮 ──")
eng = FakeEngine()
st2, kg2, cfg2, _ = build(engine=eng)
MS.ensure_modulator_subgraph(kg2, st2, cfg2)
dop_node = kg2.get_node("多巴胺样")
small = st2.pulse("dopamine", 0.02, reason="微脉冲")   # < threshold 0.08
check("D1 低于阈值不注入",
      float(dop_node.activation or 0.0) == 0.0 and not eng.marked,
      str((small.get("graph_pulse"), dop_node.activation)))
# D2/D3 用去甲肾上腺素：多巴胺刚打过（refractory_min>0 → 第二次增益被不应期
# 按比例打折），那正是 P3 的语义，会把"注入幅度"的断言变成在测不应期。
scale = float(cfg2["modulator_system"]["graph_pulse_scale"])
big = st2.pulse("norepinephrine", 0.7, reason="被打断")
node_ne = kg2.get_node("去甲肾上腺素样")
inj = big.get("graph_pulse") or {}
want = min(5.0, abs(float(big["phasic"])) * scale)
check("D2 事件脉冲点亮镜像节点（幅度=|phasic|×scale，上限 5）",
      inj.get("node") == "去甲肾上腺素样" and abs(inj.get("amount", 0) - want) < 1e-6
      and abs(float(node_ne.activation) - want) < 1e-6,
      str((inj, want, node_ne.activation)))
check("D3 走 Drive 范式：mark_active + 来源登记（本轮发射免扣）",
      [node_ne.id] in eng.marked and
      any(ids == [node_ne.id] and src == "modulator" for ids, src in eng.sources),
      str((eng.marked, eng.sources)))
# D4 取最强不相加：直接调注入原语（同一调制器连打两次会被不应期打折，
# 那是另一条已测语义；这里要锁的是"能量不会因重复事件而累加"）
i1 = st2._graph_pulse("dopamine", 0.5)
i2 = st2._graph_pulse("dopamine", 0.3)
i3 = st2._graph_pulse("dopamine", 0.9)
check("D4 重复注入取最强而非累加（同轮多次事件不放大能量）",
      abs(i1["amount"] - 2.5) < 1e-6 and abs(i2["amount"] - 1.5) < 1e-6
      and float(dop_node.activation) > 0 and dop_node.activation == 4.5
      and i3["old"] == 2.5,
      str((i1, i2, i3, dop_node.activation)))
# 慢通道（衰减/上升吸收）不得注入
eng.marked.clear(); eng.sources.clear()
node_glu = kg2.get_node("谷氨酸样")
node_glu.activation = 0.0
st2.apply_delta("modulator", "glutamate", 0.3, reason="持续压力", source="event")
eng.marked.clear(); eng.sources.clear()
st2.tick_decay(now=__import__("time").time() + 600)
check("D5 每拍衰减/上升吸收不注入图激活（无外置能量泵）",
      not eng.marked and not eng.sources, str(eng.marked))
# 未拆快通道的调制器：|tonic 位移| 就是事件幅度
eng2 = FakeEngine()
st3, kg3, cfg3, _ = build(engine=eng2)
MS.ensure_modulator_subgraph(kg3, st3, cfg3)
p3 = st3.pulse("cortisol", 0.4, reason="持续威胁")
n_cort = kg3.get_node("皮质醇样")
check("D6 非拆分调制器也进图通道（幅度取实际写入的慢位移）",
      (p3.get("graph_pulse") or {}).get("node") == "皮质醇样"
      and float(n_cort.activation) > 0, str(p3.get("graph_pulse")))
# 开关关闭
cfg4 = copy.deepcopy(cfgmod.DEFAULT_CONFIG)
cfg4["modulator_system"]["graph_pulse"] = False
eng4 = FakeEngine()
st4, kg4, cfg4, _ = build(config=cfg4, engine=eng4)
MS.ensure_modulator_subgraph(kg4, st4, cfg4)
r4 = st4.pulse("dopamine", 0.5, reason="关闭时不该点亮")
check("D7 graph_pulse=False → 零注入（第 3 个死开关也有消费者了）",
      r4.get("graph_pulse") is None and not eng4.marked
      and float(kg4.get_node("多巴胺样").activation or 0.0) == 0.0,
      str(r4.get("graph_pulse")))
# 无引擎（离线/测试）时不裸写 activation
st5, kg5, cfg5, _ = build(engine=None)
MS.ensure_modulator_subgraph(kg5, st5, cfg5)
st5.pulse("dopamine", 0.5, reason="无引擎")
check("D8 无引擎时不写 activation（避免来历不明的发射）",
      float(kg5.get_node("多巴胺样").activation or 0.0) == 0.0)

# ── E 能量有界（真扩散引擎）──────────────────────────────

print("── E 能量守恒：脉冲不是永动机 ──")
gp = os.path.join(tempfile.mkdtemp(), "g.json")
shutil.copy(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "runtime_graph.json"), gp)
kg6 = KnowledgeGraph.load(gp)
cfg6 = copy.deepcopy(cfgmod.DEFAULT_CONFIG)
eng6 = DiffusionEngine(kg6, cfg6)
eng6.name_to_node = dict(kg6.nodes)
st6 = InternalState(kg=kg6, config=cfg6, engine=eng6, data_dir=os.path.dirname(gp))
st6.sync_graph()
MS.ensure_modulator_subgraph(kg6, st6, cfg6)


def energy(kg):
    return sum(n.activation for n in kg.nodes.values() if n.activation > 0)


def capped(kg):
    return sum(1 for n in kg.nodes.values() if n.activation >= 4.99)


def _round(eng):
    """一轮真实扩散节拍（衰减 → 传播 → 回合边界清来源）。"""
    eng.decay_step()
    eng.diffuse_step()
    eng.clear_anchors()


e0 = energy(kg6)
for _ in range(300):
    _round(eng6)
e_idle = energy(kg6)
check("E1 装配子图后空闲长跑仍然排空（300 轮能量不升）",
      e_idle <= e0 + 1e-6, f"{e0:.3f} → {e_idle:.3f}")
st6.pulse("norepinephrine", 0.9, reason="强事件")
st6.pulse("dopamine", 0.7, reason="意外好消息")
after_pulse = energy(kg6)
check("E2 脉冲确实注入了能量（否则图通道是空的）",
      after_pulse > e_idle, f"{e_idle:.3f} → {after_pulse:.3f}")
for _ in range(600):
    _round(eng6)
e_drained = energy(kg6)
check("E3 事件停手后能量再次排空（不是固定点饱和）",
      e_drained < after_pulse and e_drained < 1.0,
      f"{after_pulse:.3f} → {e_drained:.3f}")
check("E4 排空后没有节点钉在上限", capped(kg6) == 0, str(capped(kg6)))
# 持续脉冲（每 20 轮一次）期间能量有界：外源有，但不会无界累积
peak = 0.0
for i in range(600):
    if i % 20 == 0:
        st6.pulse("norepinephrine" if i % 40 == 0 else "dopamine",
                  0.5, reason=f"周期事件{i}")
    _round(eng6)
    peak = max(peak, energy(kg6))
check("E5 周期性事件下能量有界（<20，远低于全图饱和）",
      peak < 20.0 and energy(kg6) < 20.0, f"peak={peak:.2f} end={energy(kg6):.2f}")
# 2000 轮长护栏（09-13 那类）：事件密度取**超出真实生活**的每 10 轮一次
st6.reset_modulators()
for n in kg6.nodes.values():
    n.activation = 0.0
eng6.clear_anchors()
peak_hi, cap_hi = 0.0, 0
for i in range(2000):
    if i % 10 == 0:
        st6.pulse("norepinephrine" if (i // 10) % 2 else "dopamine",
                  0.6, reason=f"密集事件{i}")
    _round(eng6)
    peak_hi = max(peak_hi, energy(kg6))
    cap_hi = max(cap_hi, capped(kg6))
check("E6 2000 轮密集脉冲（200 个事件）总能量仍有界（<40，非 374 节点满格的暴动）",
      peak_hi < 40.0, f"peak={peak_hi:.2f} 满格峰值={cap_hi}")
check("E7 上限钉死只发生在单个被反复点亮的调制器（≤2 个），不是全图",
      cap_hi <= 2, f"满格峰值={cap_hi} peak={peak_hi:.2f}")

# ── G 一次事件能否沿调制边传播（审计 §1 Q6 的答案由"否"变"是"）──

print("── G 传播证据 ──")
import time as _t
st6.reset_modulators()                       # 清掉 E 组攒下的 phasic/习惯化
st6.tick_decay(now=_t.time() + 3600)         # 不应期与动量退场，从安静态开始
for n in kg6.nodes.values():
    n.activation = 0.0
eng6.clear_anchors()
g_r = st6.pulse("dopamine", 0.6, reason="意外好消息")
eng6.decay_step()
eng6.diffuse_step()
lit = {n.id: n.activation for n in kg6.nodes.values() if n.activation > 1e-6}
targets_lit = sorted(i for i in lit if i.startswith("调制目标:"))
check("G1 事件沿调制边传到参数靶点（激素真的在参与扩散）",
      bool(g_r.get("graph_pulse")) and "多巴胺样" in lit and bool(targets_lit),
      str(sorted(lit.items(), key=lambda x: -x[1])[:5]))
check("G2 传播是事件尺度的局部点亮（能量 <10、节点 <60，不是全图饱和）",
      sum(lit.values()) < 10.0 and len(lit) < 60,
      f"E={sum(lit.values()):.2f} n={len(lit)}")
eng6.clear_anchors()
for _ in range(200):
    _round(eng6)
check("G3 传播出去的能量同样会排空（图通道不是永久光源）",
      energy(kg6) < 0.5, f"{sum(n.activation for n in kg6.nodes.values()):.3f}")
no_out = [nid for nid in st6.mirror_modulator.values() if not kg6.get_out_edges(nid)]
check("G4 真实图谱上 12 个调制器全部有正权出边（没有死掉的抑制源）",
      not no_out, str(no_out))

# ── F P4 的孤岛结论作废 ──────────────────────────────────

print("── F 孤岛（D-15）关闭 ──")
st7, kg7, cfg7, _ = build()
MS.ensure_modulator_subgraph(kg7, st7, cfg7)
no_out = [nid for nid in st7.mirror_modulator.values() if not kg7.get_out_edges(nid)]
no_in = [nid for nid in st7.mirror_modulator.values() if not kg7.get_in_edges(nid)]
check("F1 12 个调制器节点全部有出边", not no_out, str(no_out))
check("F2 12 个调制器节点全部有入边（Self/需求）", not no_in, str(no_in))
check("F3 调制目标:* 节点存在且被指到",
      sum(1 for n in kg7.nodes.values()
          if (n.extra_attrs or {}).get("type") == "modulator_target") == 14)
exp = MS.explain_subgraph(kg7, st7, cfg7)
check("F4 explain 观测面可用（人读拓扑，P12 实验模式靠它）",
      len(exp) == 12 and all("edges" in v for v in exp.values()))

print()
if FAILURES:
    print(f"[FAIL] {len(FAILURES)} 项: {FAILURES}")
    sys.exit(1)
print("[OK] P6 调制子图全部通过")
