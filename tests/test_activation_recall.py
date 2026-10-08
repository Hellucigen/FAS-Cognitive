# test_activation_recall.py — 语义召回 / 激活区可见性 / 注意力并列测试
#
# 锁定的契约（用户的机制要求："输入 Minecraft 送入激活后，应该同时激活
# Minecraft 和玩 Minecraft 的能力（通过向量相似度），然后再送到激活区产生回答"）：
#   1. 能力节点（self_capability）参与召回与回答区；引擎零件不参与
#   2. 相似度 → 种子强度的映射有地板：低于地板的匹配不构成焦点
#   3. 注意力并列（都撞 cap）时，本轮刚点亮的节点优先于早年旧节点
#   4. 补传播只从指定种子发射，不会让整片前沿重新发一轮
#
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_activation_recall.py

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from graph_model import KnowledgeGraph, Node, Edge, is_cognitive_visible
from diffusion_engine import DiffusionEngine

fail = []
def check(name, cond, detail=''):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ''))
    if not cond:
        fail.append(name)

CFG = {
    "lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 4,
    "theta_threshold": 0.01, "activation_max": 5.0, "min_spread_threshold": 0.01,
    "activation_epsilon": 1e-4,
    "inter_round_decay": 0.75, "faiss_recall_topk": 8,
    "input_similarity_floor": 0.5, "input_default_bonus": 0.5,
    "relation_propagation": {"cognitive_relation": {"direction": "bidirectional",
                                                     "decay": 1.0, "gain": 1.2}},
}

def build_engine():
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Minecraft"))
    kg.add_node(Node(id="进入Minecraft世界", label="procedural",
                     extra_attrs={"self_capability": True,
                                  "description": "进入用户的 Minecraft 世界"}))
    kg.add_node(Node(id="社交互动", label="infrastructure"))
    kg.add_node(Node(id="搜索记录_1", label="procedural"))
    kg.add_edge(Edge(src="Minecraft", dst="进入Minecraft世界",
                     relation="相关能力", weight=0.9,
                     relation_category="cognitive_relation"))
    eng = DiffusionEngine(kg, dict(CFG))
    return kg, eng

# ── 1. 认知可见性：能力进回答区，零件不进 ──────────────
kg, eng = build_engine()
check("能力节点参与召回/回答", is_cognitive_visible(kg.nodes["进入Minecraft世界"]))
check("知识点参与召回/回答", is_cognitive_visible(kg.nodes["Minecraft"]))
check("引擎零件(infrastructure)不参与", not is_cognitive_visible(kg.nodes["社交互动"]))
check("执行留痕(procedural 无标记)不参与", not is_cognitive_visible(kg.nodes["搜索记录_1"]))

# ── 2. 相似度地板：弱匹配不构成焦点 ────────────────────
kg, eng = build_engine()
eng.activate_from_inputs(["Minecraft", "进入Minecraft世界", "社交互动"],
                         [], similarity_map={"Minecraft": 1.0,
                                            "进入Minecraft世界": 0.75,
                                            "社交互动": 0.55})
a_mc = kg.nodes["Minecraft"].activation
a_cap = kg.nodes["进入Minecraft世界"].activation
a_noise = kg.nodes["社交互动"].activation
check("相似度 1.0 → 满量程", abs(a_mc - 5.0) < 1e-6, a_mc)
check("相似度 0.75 → 半量程附近", 2.0 < a_cap < 3.0, a_cap)
check("相似度 0.55 → 远弱于主要内容", 0.0 < a_noise < 1.0, a_noise)

kg2, eng2 = build_engine()
eng2.activate_from_inputs(["进入Minecraft世界"], [],
                          similarity_map={"进入Minecraft世界": 0.45})
check("低于地板(0.5)的命中不激活", kg2.nodes["进入Minecraft世界"].activation == 0.0,
      kg2.nodes["进入Minecraft世界"].activation)

kg3, eng3 = build_engine()
eng3.activate_from_inputs(["进入Minecraft世界"], [])
check("无相似度的抽取实体拿默认档", abs(kg3.nodes["进入Minecraft世界"].activation - 2.5) < 1e-6,
      kg3.nodes["进入Minecraft世界"].activation)

# ── 3. 注意力并列：近点亮者优先，不让早年旧节点靠入图序取胜 ──
kg, eng = build_engine()
kg.add_node(Node(id="旧簇节点"))     # 先入图（序号小）
kg.add_node(Node(id="本轮节点"))     # 后入图
kg.nodes["旧簇节点"].activation = 5.0
kg.nodes["本轮节点"].activation = 5.0
eng.mark_active(["旧簇节点"])
eng.mark_active(["本轮节点"])
kg.nodes["旧簇节点"].activation = 5.0
kg.nodes["本轮节点"].activation = 5.0
top, _ = eng.get_topk(k=2)
check("并列时本轮点亮者在前", top[0].id == "本轮节点", [n.id for n in top])

# ── 4. 补传播只从种子发射 ──────────────────────────────
kg, eng = build_engine()
kg.add_node(Node(id="旁观者"))
kg.add_node(Node(id="旁观者邻居"))
kg.add_edge(Edge(src="旁观者", dst="旁观者邻居", relation="关联", weight=1.0))
# 旁观者是本轮扩散已收敛的旧焦点（已被 fire 过一轮），激活仍高
eng.mark_active(["旁观者"])
kg.nodes["旁观者"].activation = 5.0
# 补激活：新实体拿到激活（模拟记忆抽取新实体）
eng.activate_from_inputs(["进入Minecraft世界"], [])
eng.diffuse_from(["进入Minecraft世界"], steps=1)
check("补传播：种子邻域被点亮", kg.nodes["Minecraft"].activation > 0.0,
      kg.nodes["Minecraft"].activation)
check("补传播：旁观者不再发一轮", kg.nodes["旁观者邻居"].activation == 0.0,
      kg.nodes["旁观者邻居"].activation)

print()
if fail:
    print('✗', len(fail), fail); sys.exit(1)
print('✓ 召回/可见性/并列/补传播测试全过')
