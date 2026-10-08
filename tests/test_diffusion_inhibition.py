# test_diffusion_inhibition.py — L1-DFU-01 回归锁
# ============================================================================
# 修复前:节点出边全负时,total_w==0 使整个发射循环被跳过——抑制量永不发射,
# 目标只吃自然衰减。修复后:纯抑制节点按 Σ|w| 归一向各目标发射抑制。
# 修复不改变混合节点(正负边共存)的逐位行为——分母仍是 total_w。
# 对应 PATCH_LOG.md L1-DFU-01;falsification 侧见 mechanism_falsification/。
# ============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import config
from graph_model import KnowledgeGraph, Node, Edge
from diffusion_engine import DiffusionEngine


def _cfg():
    c = dict(config.DEFAULT_CONFIG)
    return c


def _kg_with(nodes, edges):
    kg = KnowledgeGraph()
    for nid in nodes:
        kg.add_node(Node(id=nid))
    for e in edges:
        kg.add_edge(Edge(*e))
    return kg


def test_pure_inhibitor_emits_inhibition():
    """全负出边节点(安全需求类 hubs)必须把抑制发射出去,而不是静默跳过。"""
    kg = _kg_with(["S", "T1", "T2"],
                  [("S", "T1", "相关", -0.8),
                   ("S", "T2", "相关", -0.6)])
    eng = DiffusionEngine(kg, _cfg())
    eng.activate_from_inputs(["S"], [])
    kg.nodes["T1"].activation = 2.0
    kg.nodes["T2"].activation = 2.0
    eng.mark_active(["T1", "T2"])
    eng.diffuse_round(max_steps=2)
    # 对照:无抑制时两步自然衰减 = 2.0×0.95×0.95 = 1.805
    natural = 2.0 * 0.95 * 0.95
    assert kg.nodes["T1"].activation < natural - 0.1, \
        f"T1 应被显著抑制,实际 {kg.nodes['T1'].activation:.4f} < {natural}"
    assert kg.nodes["T2"].activation < natural - 0.1, \
        f"T2 应被显著抑制,实际 {kg.nodes['T2'].activation:.4f}"
    # 抑制强度与 |w| 成比例:T1(|w|=.8) 应低于 T2(|w|=.6)
    assert kg.nodes["T1"].activation < kg.nodes["T2"].activation


def test_mixed_edges_behavior_unchanged():
    """正负混合节点:正贡献分母仍为 Σw₊,负贡献按 Σw₊ 归一——与修复前逐位一致。"""
    kg = _kg_with(["S", "T1", "P"], [("S", "T1", "相关", -0.8),
                                     ("S", "P", "相关", 0.5)])
    eng = DiffusionEngine(kg, _cfg())
    eng.activate_from_inputs(["S"], [])
    kg.nodes["T1"].activation = 2.0
    eng.mark_active(["T1"])
    eng.diffuse_round(max_steps=2)
    natural = 2.0 * 0.95 * 0.95
    assert kg.nodes["T1"].activation < natural - 0.1   # 抑制仍发生
    assert kg.nodes["P"].activation > 0.0              # 正边照常传播


def test_backward_inhibition_sanity():
    """反向(bidirectional 入边)纯负集同样发射抑制(同修的另一半)。"""
    kg = KnowledgeGraph()
    for nid in ["S", "M"]:
        kg.add_node(Node(id=nid))
    # M → S:"相关"是 bidirectional 全局覆盖,因此 S 激活时会把信号沿
    # 入边反向送回 M;该入边为负权,构成 S 的"纯负入边集"。
    kg.add_edge(Edge("M", "S", "相关", -0.7))
    eng = DiffusionEngine(kg, _cfg())
    eng.activate_from_inputs(["S"], [])
    kg.nodes["M"].activation = 2.0
    eng.mark_active(["M"])
    eng.diffuse_round(max_steps=1)
    # 无抑制时 M 一步自然衰减 = 2.0×0.95 = 1.90;S 的反向抑制应把 M 打到更低
    natural = 2.0 * 0.95
    assert kg.nodes["M"].activation < natural - 0.05, \
        f"M 应被反向抑制,实际 {kg.nodes['M'].activation:.4f} < {natural}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-x"]))