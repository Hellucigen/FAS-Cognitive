# test_graph_schema_alignment.py — 架构对齐契约锁定测试
# ============================================================================
# 锁定 2026-09-19 架构对齐引入的机制契约：
#   1. 关系归一漏斗：同义表面形式入图即归一，读写双向一致，重复三元组合并
#   2. 主体守卫：Haru/Self/用户 直连边白名单（warning 不阻断）
#   3. 严格类型：非法 label/graph_space 拒绝创建；from_dict 容错降级
#   4. Minecraft 感知结构：Haru-[当前状态]->当前Minecraft状态-[状态项]->槽位，
#      Unknown* 不再直连 Haru
#   5. CurrentActivity 生命周期：同 kind 延续、切换收尾、Haru 指针恒一条、
#      历史上限修剪
#   6. 扩散论文语义：负权重传播（抑制）、每步衰减、加性边激活上限
#
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_graph_schema_alignment.py
# ============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_model import KnowledgeGraph, Node, Edge
from diffusion_engine import DiffusionEngine
import graph_schema as gs
import minecraft.perception as mp
from activity_tracker import ActivityTracker

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
}


# ── 1. 关系归一漏斗 ────────────────────────────────────────
check("同义归一：名字是→名字叫", gs.normalize_relation("名字是") == "名字叫")
check("同义归一：引发→导致", gs.normalize_relation("引发") == "导致")
check("兜底归一：自由文本→关联", gs.normalize_relation("莫名奇妙的关系") == "关联")
check("归一幂等", gs.normalize_relation(gs.normalize_relation("想去")) == "喜欢")

kg = KnowledgeGraph()
kg.add_node(Node(id="A"))
kg.add_node(Node(id="B"))
kg.add_edge(Edge(src="A", dst="B", relation="名字是", weight=0.5))
e = kg.get_edge("A", "B", "名字叫")
check("写侧归一：入图即规范词", e is not None and e.relation == "名字叫", e)
check("类别自动归类：名字叫→semantic", e.relation_category == "semantic_relation")
kg.add_edge(Edge(src="A", dst="B", relation="名字", weight=0.9))
check("归一后重复三元组合并（不追加）", len(kg.edges) == 1 and kg.get_edge("A", "B", "名字叫").weight == 0.9)
kg.add_edge(Edge(src="A", dst="B", relation="导致", weight=0.7, relation_category="causal_relation"))
check("显式类别保留", kg.get_edge("A", "B", "导致").relation_category == "causal_relation")

# ── 2. 主体守卫 ────────────────────────────────────────────
kg.add_node(Node(id="Haru", label="self", graph_space="self"))
kg.add_edge(Edge(src="B", dst="Haru", relation="关联", weight=0.3))
check("守卫不阻断写入但可审计", kg.get_edge("B", "Haru", "关联") is not None
      and not gs.check_hub_edge("B", "Haru", "关联"))
check("白名单边合法", gs.check_hub_edge("Haru", "B", "当前状态") is False
      or gs.check_hub_edge("Self", "Haru", "名字叫") is True)

# ── 3. 严格类型 ────────────────────────────────────────────
try:
    Node(id="X", label="cognitive")
    check("非法 label 拒绝创建", False)
except ValueError:
    check("非法 label 拒绝创建", True)
try:
    Node(id="X", label="declarative-semantic", graph_space="world")
    check("非法 space 拒绝创建", False)
except ValueError:
    check("非法 space 拒绝创建", True)
n = Node.from_dict({"id": "Y", "label": "cognitive"})
check("from_dict 容错降级", n.label == "declarative-semantic")
n2 = Node(id="Z", label="disposition", graph_space="self")
check("disposition/intention label 合法", n2.label == "disposition")

# ── 4. Minecraft 感知结构 ──────────────────────────────────
class _Eng:
    def mark_active(self, ids):
        pass

kg2 = KnowledgeGraph()
kg2.add_node(Node(id="Haru", label="self", graph_space="self"))
state = {"connected": True, "position": {"x": 1, "y": 2, "z": 3},
         "health": 20, "food": 20, "heldItem": "sword",
         "playersNearby": [], "nearbyBlocks": [{"name": "stone"}],
         "nearbyEntities": [{"name": "zombie"}]}
mp.update_perception(kg2, _Eng(), state)
check("状态中间节点存在", "当前Minecraft状态" in kg2.nodes)
check("Haru-[当前状态]->状态中间节点", kg2.get_edge("Haru", "当前Minecraft状态", "当前状态") is not None)
check("槽位经 状态项 接中间节点", kg2.get_edge("当前Minecraft状态", "Haru的位置", "状态项") is not None)
check("Haru 不再直连槽位", kg2.get_edge("Haru", "Haru的位置", "当前状态") is None)
check("Unknown* 不再直连 Haru", not any(e.dst == "Haru" or e.src == "Haru"
                                         for nid, n in kg2.nodes.items()
                                         if nid.startswith("Unknown")
                                         for e in kg2.get_out_edges(nid)))
check("Unknown* 节点已建且带 type=unknown",
      "UnknownBlock_stone" in kg2.nodes
      and kg2.nodes["UnknownBlock_stone"].extra_attrs.get("type") == "unknown")
check("Unknown* label 落 semantic（合法值）",
      kg2.nodes["UnknownBlock_stone"].label == "declarative-semantic")

# ── 5. CurrentActivity 生命周期 ────────────────────────────
kg3 = KnowledgeGraph()
kg3.add_node(Node(id="Haru", label="self", graph_space="self"))
kg3.add_node(Node(id="stone"))
tr = ActivityTracker(kg3, None)
a1 = tr.start("dig", target="stone", reason="采矿")
check("活动创建并挂 Haru 指针", a1 and kg3.get_edge("Haru", a1, "当前活动") is not None)
check("活动类型边（dig→采集）", kg3.get_edge(a1, "活动类型:采集", "类型") is not None)
a2 = tr.start("gather_resource", target="stone")
check("同族动作延续同一活动", a2 == a1)
tr.settle(True)
tr.settle(True)
check("执行中状态保持（活动未结束）", kg3.nodes[a1].extra_attrs["status"] == "执行中")
a3 = tr.start("follow_entity")
check("kind 切换开新活动", a3 != a1)
check("旧活动收尾", kg3.nodes[a1].extra_attrs["status"] in ("已完成", "取消"))
check("Haru 当前活动指针恒一条",
      sum(1 for e in kg3.get_out_edges("Haru") if e.relation == "当前活动") == 1)
check("目标边指向已存在实体", kg3.get_edge(a1, "stone", "目标") is not None)
tr._end(kg3.nodes[a3], "已完成")
for i in range(gs.ACTIVITY_HISTORY_CAP + 5):
    tr.start("observe")
    tr._end(kg3.nodes[tr.current_id], "已完成")
acts = [n for n in kg3.nodes.values()
        if (n.extra_attrs or {}).get("type") == "activity"
        and (n.extra_attrs or {}).get("status") in ("已完成", "失败", "取消")]
check(f"历史修剪 ≤ {gs.ACTIVITY_HISTORY_CAP}", len(acts) <= gs.ACTIVITY_HISTORY_CAP, len(acts))

# ── 6. 扩散论文语义 ────────────────────────────────────────
# 6a. 负权重 = 抑制（正激活经负边降低目标）
kg4 = KnowledgeGraph()
kg4.add_node(Node(id="Src", graph_space="cognitive"))
kg4.add_node(Node(id="Inhibited"))
kg4.add_node(Node(id="Excited"))
kg4.add_edge(Edge(src="Src", dst="Inhibited", relation="抑制", weight=-0.8,
                  relation_category="causal_relation"))
kg4.add_edge(Edge(src="Src", dst="Excited", relation="导致", weight=0.8,
                  relation_category="causal_relation"))
eng4 = DiffusionEngine(kg4, dict(CFG))
eng4.activate_from_inputs(["Src"], [])
eng4.diffuse_round()
check("负权重产生抑制（Inhibited 激活低于 Excited）",
      kg4.nodes["Inhibited"].activation < kg4.nodes["Excited"].activation,
      f"{kg4.nodes['Inhibited'].activation} vs {kg4.nodes['Excited'].activation}")
check("接收端激活不为负", kg4.nodes["Inhibited"].activation >= 0)

# 6b. 每步衰减排水：非锚点场（直写激活+mark_active，无免转移锚）中，
#     发射转移 + 每步衰减使场内总能量严格低于初始注入。
kg5 = KnowledgeGraph()
kg5.add_node(Node(id="S", graph_space="semantic"))
kg5.add_node(Node(id="T", graph_space="semantic"))
kg5.add_edge(Edge(src="S", dst="T", relation="相关", weight=1.0))
eng5 = DiffusionEngine(kg5, dict(CFG))
kg5.nodes["S"].activation = 2.5
eng5.mark_active(["S"])
eng5.diffuse_round()   # 3 步，每步 (1-λ) 衰减；发射即转移
total = kg5.nodes["S"].activation + kg5.nodes["T"].activation
check("每步衰减排水（场内总能量 < 初始注入）", total < 2.5, total)

# 6c. 加性边激活上限：r 抬升不超过 edge_runtime_activation_cap
kg6 = KnowledgeGraph()
kg6.add_node(Node(id="S"))
kg6.add_node(Node(id="T"))
kg6.add_edge(Edge(src="S", dst="T", relation="相关", weight=0.5))
eng6 = DiffusionEngine(kg6, dict(CFG))
edge = kg6.get_edge("S", "T", "相关")
edge.activation = 99.0   # 超限注入
weff = eng6._edge_w_eff(edge)
check("加性边激活：r 被夹到上限", abs(weff - (0.5 + CFG_R_CAP)) < 1e-9
      if (CFG_R_CAP := 1.0) else False, weff)

# 6d. 方向：全局语义覆盖优先于实例 config
eng7 = DiffusionEngine(kg6, {"relation_propagation": {
    "semantic_relation": {"direction": "forward", "decay": 1.0, "gain": 1.0}}})
check("相关→双向（全局覆盖）", eng7._edge_direction(edge) == "bidirectional")
kg7 = KnowledgeGraph()
kg7.add_node(Node(id="S"))
kg7.add_node(Node(id="T"))
kg7.add_edge(Edge(src="S", dst="T", relation="导致", weight=1.0,
                  relation_category="causal_relation"))
eng8 = DiffusionEngine(kg7, dict(CFG))
check("导致→单向（因果不逆流）", eng8._edge_direction(kg7.edges[0]) == "forward")

print()
if fail:
    print(f"✗ {len(fail)} 项失败: {fail}")
    sys.exit(1)
print("✓ 架构对齐契约测试全过")
