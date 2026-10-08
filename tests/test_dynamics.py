# test_dynamics.py — 认知动力学调制测试（Hebbian / 软遗忘 / 参数增益）
# 真 DiffusionEngine。重点验证"安全护栏"而不只是"功能存在"：
#   Hebbian 只碰知识/情绪边、增幅封顶、可关断；
#   软遗忘只降可达性不删数据、豁免 self/基础设施/程序性/disposition；
#   参数增益改变传播量纲但保持数值稳定。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_dynamics.py

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from config import DEFAULT_CONFIG
from graph_model import KnowledgeGraph, Node, Edge, now_str
from diffusion_engine import DiffusionEngine

FAILURES = []


def check(name, cond, detail=""):
    st = "PASS" if cond else "FAIL"
    print(f"[{st}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


CFG = dict(DEFAULT_CONFIG)


def fresh(cfg=None):
    cfg = cfg or CFG
    kg = KnowledgeGraph()
    kg.add_node(Node(id="A", graph_space="semantic"))
    kg.add_node(Node(id="B", graph_space="semantic"))
    kg.add_node(Node(id="S", graph_space="self"))
    e = Edge(src="A", dst="B", relation="使用", weight=0.5,
             relation_category="semantic_relation")
    kg.add_edge(e)
    # disposition 型边（cognitive_relation）——必须永不被 Hebbian 触碰
    e2 = Edge(src="S", dst="B", relation="先验", weight=0.4,
              relation_category="cognitive_relation")
    kg.add_edge(e2)
    eng = DiffusionEngine(kg, dict(cfg))
    eng.name_to_node = dict(kg.nodes)
    return kg, eng, e, e2


def fire(eng, node, n):
    for _ in range(n):
        node.activation = 4.0
        eng._active_nodes[node.id] = None
        eng._fired_round = set()
        eng.diffuse_step()


# ── Hebbian ──
kg, eng, e_sem, e_cog = fresh()
w0 = e_sem.weight
fire(eng, kg.nodes["A"], 30)
check("共激活的知识边权重上升", e_sem.weight > w0, f"{w0}->{e_sem.weight}")
check("cognitive_relation（先验/pair）永不被改写",
      e_cog.weight == 0.4, str(e_cog.weight))
check("单次强化步长极小（30 次 < 0.1）", e_sem.weight - w0 < 0.1,
      str(e_sem.weight - w0))
fire(eng, kg.nodes["A"], 3000)
cap = w0 * 1.5 + 1e-9
check("增幅封顶 baseline×(1+max_gain)", e_sem.weight <= cap,
      f"{e_sem.weight} vs cap {cap}")
# （小步长断言移到 30 次后、3000 次前——见下）
# ε 极小性：30 次发放只应动 ~0.04（0.0015*0.8*30=0.036）
CFG2 = dict(CFG); CFG2["hebbian"] = {"enabled": False}
kg2, eng2, e2_sem, _ = fresh(CFG2)
fire(eng2, kg2.nodes["A"], 20)
check("开关关闭后行为与旧引擎一致", e2_sem.weight == 0.5, str(e2_sem.weight))

# ── 软遗忘 ──
kg, eng, *_ = fresh()
old = kg.add_node(Node(id="旧知识", graph_space="semantic"))
n_old = kg.nodes["旧知识"]
n_old.last_access = time.strftime("%Y/%m/%d %H:%M:%S",
                                  time.localtime(time.time() - 30 * 86400))
eng.name_to_node["旧知识"] = n_old
kg.add_node(Node(id="新事情", graph_space="semantic"))
eng.name_to_node["新事情"] = kg.nodes["新事情"]
eng.apply_inter_round_decay(1.0, 0.5)     # 全部归零
eng.activate_from_inputs(["旧知识", "新事情"], [])
a_old = kg.nodes["旧知识"].activation
a_new = kg.nodes["新事情"].activation
check("30 天未访问 → 种子强度衰减到地板",
      0 < a_old < a_new and abs(a_old - 0.25 * a_new) < 0.15,
      f"old={a_old:.3f} new={a_new:.3f}")
check("软遗忘不删节点、不改权重（只降可达性）",
      "旧知识" in kg.nodes
      and kg.nodes["旧知识"].weight == Node(id="探针").weight,   # 与新建节点权重一致
      str(getattr(kg.nodes.get("旧知识"), "weight", None)))
exempt = kg.add_node(Node(id="某disposition", graph_space="self",
                          label="disposition"))
eng.name_to_node["某disposition"] = kg.nodes["某disposition"]
kg.nodes["某disposition"].last_access = time.strftime(
    "%Y/%m/%d %H:%M:%S", time.localtime(time.time() - 400 * 86400))
eng.apply_inter_round_decay(1.0, 0.5)
eng.activate_from_inputs(["某disposition"], [])
check("self 空间/disposition 豁免（完全可达）",
      kg.nodes["某disposition"].activation > a_new * 0.95,
      str(kg.nodes["某disposition"].activation))
# 触碰即恢复
kg.nodes["旧知识"].touch()
eng.apply_inter_round_decay(1.0, 0.5)
eng.activate_from_inputs(["旧知识"], [])
check("touch 后立即恢复完全可达", kg.nodes["旧知识"].activation > a_new * 0.95,
      str(kg.nodes["旧知识"].activation))

# ── 参数增益 ──
kg, eng, *_ = fresh()
g1 = eng.update_param_modulation(arousal=0.0, stress=0.0)
g_hi = eng.update_param_modulation(arousal=1.0, stress=0.0)
g_lo = eng.update_param_modulation(arousal=0.0, stress=1.0)
check("中性状态增益=1.0", abs(g1 - 1.0) < 1e-9, str(g1))
check("高唤醒 → 增益>1", g_hi > 1.2, str(g_hi))
check("高压力 → 增益<1", g_lo < 0.95, str(g_lo))
check("增益有界", all(0.7 <= g <= 1.6 for g in (g1, g_hi, g_lo)))
# 效果验证：同一发射，增益高者目标收到更多
def measure(gain):
    eng._param_gain = gain
    for n in kg.nodes.values():
        n.activation = 0
    eng._active_nodes = {k: None for k in []}
    kg.nodes["A"].activation = 3.0
    eng._active_nodes["A"] = None
    eng._fired_round = set()
    eng.diffuse_step()
    return kg.nodes["B"].activation
lo = measure(1.0)
hi = measure(1.4)
check("增益确实放大传播量", hi > lo, f"lo={lo:.3f} hi={hi:.3f}")

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 认知动力学调制全过（Hebbian 安全版 / 软遗忘 / 参数增益）")
