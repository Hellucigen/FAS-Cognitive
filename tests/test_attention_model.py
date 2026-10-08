# test_attention_model.py — 注意资源模型契约测试（理论—实现重新对齐 2026-09-19b）
# ============================================================================
# 锁定的契约：激活扩散 = 有限注意资源约束下的认知激活传播机制
#   1. ActivationSource：类型化来源登记，来源发射免扣，普通节点发射即扣
#   2. 来源生命周期：clear_anchors 清空（回合边界）
#   3. Emission budget：base ratio + CurrentActivity 语境调制
#   4. CurrentActivity 接入 emission：活动局部语境的节点发射配更高资源
#      （§12：影响注意力，不做全图广播——语境外的节点配比不变）
#   5. 环路有界：A→B→A 不再注入能量，多轮后场严格缩水（无指数放大）
#   6. 抑制：负权重传播降低目标激活
#   7. step-level decay：深度越深，到达能量越少（max_depth 有意义）
#
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_attention_model.py
# ============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_model import KnowledgeGraph, Node, Edge
from diffusion_engine import DiffusionEngine

fail = []
def check(name, cond, detail=''):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ''))
    if not cond:
        fail.append(name)

CFG = {
    "lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 3,
    "theta_threshold": 0.01, "activation_max": 5.0, "min_spread_threshold": 0.01,
    "activation_epsilon": 1e-4, "input_similarity_floor": 0.5,
    "input_default_bonus": 0.5, "emission_ratio": 0.5, "emission_transfer": 1.0,
    "activity_emission_bonus": 0.15,
}


# ── 1. ActivationSource ─────────────────────────────────────
def two_nodes():
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Src", graph_space="semantic"))
    kg.add_node(Node(id="Dst", graph_space="semantic"))
    kg.add_edge(Edge(src="Src", dst="Dst", relation="相关", weight=1.0))
    return kg

kg = two_nodes()
eng = DiffusionEngine(kg, dict(CFG))
eng.activate_from_inputs(["Src"], [])          # external_input 来源
eng.diffuse_step()
check("来源节点发射免扣（激活保持）", kg.nodes["Src"].activation > 2.0,
      kg.nodes["Src"].activation)
check("来源类型登记为 external_input", eng._source_type("Src") == "external_input")

kg2 = two_nodes()
eng2 = DiffusionEngine(kg2, dict(CFG))
kg2.nodes["Src"].activation = 2.0
eng2.mark_active(["Src"])                      # 普通图节点（无来源登记）
eng2.diffuse_step()
check("普通节点发射即扣（资源转移）", abs(kg2.nodes["Src"].activation - 1.0) < 0.2,
      kg2.nodes["Src"].activation)
check("普通节点来源身份为 graph", eng2._source_type("Src") == "graph")

eng2.register_activation_source(["Src"], "memory_recall")
check("来源类型可显式登记", eng2._source_type("Src") == "memory_recall")
eng2.clear_anchors()
check("clear_anchors 清空来源", eng2._source_type("Src") == "graph")

# ── 3+4. Emission budget 与 CurrentActivity 语境调制 ────────
def activity_graph(with_activity: bool):
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Haru", label="self", graph_space="self"))
    kg.add_node(Node(id="活动_探索", label="declarative-episodic", graph_space="self"))
    kg.add_node(Node(id="活动类型:探索", graph_space="cognitive"))
    kg.add_node(Node(id="stone", graph_space="semantic"))
    kg.add_node(Node(id="无关节点", graph_space="semantic"))
    kg.add_node(Node(id="stone邻居", graph_space="semantic"))
    kg.add_edge(Edge(src="stone", dst="stone邻居", relation="相关", weight=1.0))
    if with_activity:
        kg.add_edge(Edge(src="Haru", dst="活动_探索", relation="当前活动", weight=0.9))
        kg.add_edge(Edge(src="活动_探索", dst="活动类型:探索", relation="类型", weight=0.7))
        kg.add_edge(Edge(src="活动_探索", dst="stone", relation="目标", weight=0.6))
    return kg

kg3 = activity_graph(True)
eng3 = DiffusionEngine(kg3, dict(CFG))
eng3._refresh_activity_context()
check("语境集合含活动/类型/目标", {"活动_探索", "活动类型:探索", "stone"} <= eng3._activity_context)
check("语境外节点不在语境集合", "无关节点" not in eng3._activity_context)
act_node = kg3.nodes["活动_探索"]
tgt_node = kg3.nodes["stone"]
check("语境节点发射配比 = base + bonus",
      abs(eng3._emission_ratio_for(act_node) - 0.65) < 1e-9,
      eng3._emission_ratio_for(act_node))
check("语境外节点配比 = base",
      abs(eng3._emission_ratio_for(kg3.nodes["无关节点"]) - 0.5) < 1e-9)
check("budget = activation × 配比",
      abs(eng3._emission_budget(act_node) - act_node.activation * 0.65) < 1e-9)

# 行为级：有活动语境时，目标节点的邻域收到更多传播
def neighbor_receive(with_activity):
    kgx = activity_graph(with_activity)
    engx = DiffusionEngine(kgx, dict(CFG))
    kgx.nodes["stone"].activation = 2.0
    engx.mark_active(["stone"])
    engx.diffuse_step()
    return kgx.nodes["stone邻居"].activation
r_with, r_without = neighbor_receive(True), neighbor_receive(False)
check("活动语境提升目标节点的可传播资源（行为级）", r_with > r_without,
      f"{r_with:.3f} vs {r_without:.3f}")

# ── 5. 环路有界（A→B→A 不指数增长）─────────────────────────
kg4 = KnowledgeGraph()
for nid in ("A", "B", "C"):
    kg4.add_node(Node(id=nid, graph_space="semantic"))
kg4.add_edge(Edge(src="A", dst="B", relation="相关", weight=1.0))
kg4.add_edge(Edge(src="B", dst="C", relation="相关", weight=1.0))
kg4.add_edge(Edge(src="C", dst="A", relation="相关", weight=1.0))
eng4 = DiffusionEngine(kg4, dict(CFG))
kg4.nodes["A"].activation = 3.0
eng4.mark_active(["A"])          # 一次性注入，之后不再注入
eng4.diffuse_round()
totals = [sum(n.activation for n in kg4.nodes.values())]
for _ in range(10):
    eng4.diffuse_round()
    totals.append(sum(n.activation for n in kg4.nodes.values()))
check("环路场总量单调不增（无放大）",
      all(totals[i + 1] <= totals[i] + 1e-9 for i in range(len(totals) - 1)),
      [round(t, 3) for t in totals])
# 排水速率 = 每步 λ=5% × 3 步/轮 ≈ 14%/轮；6 轮后理论余量 ≈ 3.0×0.86⁶ ≈ 1.2。
# 关键契约是有界单调（上一条断言），此处只断言"确实在排空"。
check("环路场总量被衰减排空（10 轮后 < 初始一半）", totals[-1] < totals[0] * 0.5, totals[-1])

# ── 6. 抑制 ─────────────────────────────────────────────────
kg5 = KnowledgeGraph()
kg5.add_node(Node(id="Src", graph_space="cognitive"))
kg5.add_node(Node(id="兴奋", graph_space="semantic"))
kg5.add_node(Node(id="被抑制", graph_space="semantic"))
kg5.add_edge(Edge(src="Src", dst="兴奋", relation="导致", weight=0.8,
                  relation_category="causal_relation"))
kg5.add_edge(Edge(src="Src", dst="被抑制", relation="抑制", weight=-0.8,
                  relation_category="causal_relation"))
eng5 = DiffusionEngine(kg5, dict(CFG))
eng5.activate_from_inputs(["Src"], [])
eng5.diffuse_round()
check("负权重 = 抑制（被抑制者低于兴奋者且不为正）",
      kg5.nodes["被抑制"].activation < kg5.nodes["兴奋"].activation
      and kg5.nodes["被抑制"].activation <= 1e-9,
      f"{kg5.nodes['被抑制'].activation:.3f} vs {kg5.nodes['兴奋'].activation:.3f}")

# ── 7. step-level decay：深度衰减可见 ───────────────────────
kg6 = KnowledgeGraph()
kg6.add_node(Node(id="S", graph_space="semantic"))
kg6.add_node(Node(id="H1", graph_space="semantic"))
kg6.add_node(Node(id="H2", graph_space="semantic"))
kg6.add_node(Node(id="H3", graph_space="semantic"))
kg6.add_edge(Edge(src="S", dst="H1", relation="相关", weight=1.0))
kg6.add_edge(Edge(src="H1", dst="H2", relation="相关", weight=1.0))
kg6.add_edge(Edge(src="H2", dst="H3", relation="相关", weight=1.0))
eng6 = DiffusionEngine(kg6, dict(CFG))
eng6.activate_from_inputs(["S"], [])
eng6.diffuse_round(max_steps=3)
a1, a2, a3 = (kg6.nodes[n].activation for n in ("H1", "H2", "H3"))
check("step-level decay：能量随深度递减", a1 > a2 > a3, f"{a1:.3f} > {a2:.3f} > {a3:.3f}")

print()
if fail:
    print(f"✗ {len(fail)} 项失败: {fail}")
    sys.exit(1)
print("✓ 注意资源模型契约测试全过")
