# test_action_eventframe.py — C21 行动侧事件框架落图（生产内建，2026-09-28）
# 验证：失败/成功结算 → 签名级事件框架节点 + 极性槽位边 + 引擎点火，
# 经 graph_model.add_edge(force_weight) 双向极性翻转，可配置可回滚。
# 模型：test_autonomy_integration.py（真实 ActionManager + check 风格）。
# 测试数据只用世界对象（oak_log/birch_log），绝不触碰用户生活事件。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_action_eventframe.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from graph_model import KnowledgeGraph, Node, Edge  # noqa: E402
from diffusion_engine import DiffusionEngine        # noqa: E402
from action_system import ActionManager             # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


CFG = {"lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 4,
       "theta_threshold": 0.01, "activation_max": 5.0, "min_spread_threshold": 0.01,
       "activation_epsilon": 1e-4, "input_similarity_floor": 0.5,
       "input_default_bonus": 0.5, "theta_action": 0.5}

EVF_ON = {"enabled": True, "success_side": True,
          "failure_polarity": -0.8, "success_polarity": 0.5,
          "result_polarity": 0.9, "reign_amt": 0.8, "activation_cap": 3.0,
          "ignition": True, "retire_unknown": False,
          "suppress_gap_anchor": False, "flip_cooldown_s": 0}

GATHER_OAK = {"action_type": "gather_resource", "target": "oak_log",
              "params": {}, "reason": [], "source": "test",
              "expected_effect": "获得橡木原木"}
FAIL_RES = {"success": False, "reason": "block_not_found"}
OK_RES = {"success": True}


def total_positive_w(kg, nid):
    """节点正权出边权重和（diffusion_engine 抑制发行条件 total_w>0 的口径）。"""
    return sum(max(float(e.weight), 0.0) for e in kg.edges if e.src == nid)


def build(evf_cfg=None, with_engine=True, oak_act=0.0):
    kg = KnowledgeGraph()
    # 感知层已建档的实体（落图只连已存在节点，绝不新建）
    kg.add_node(Node(id="oak_log", weight=0.5, graph_space="semantic",
                     activation=oak_act))
    kg.add_node(Node(id="物品:oak_log", weight=0.5, graph_space="semantic"))
    engine = DiffusionEngine(kg, config=CFG) if with_engine else None
    config = {} if evf_cfg is None else {"experience_eventframe": evf_cfg}
    am = ActionManager(kg=kg, engine=engine, config=config)
    return kg, engine, am


def settle(am, action, result, success, now=1000.0):
    return am._settle(action, result, now - 5.0, now, skip_disposition=True)


def evf_node(kg, eid):
    return kg.nodes.get(eid)


def main():
    # ── 1. 默认配置（enabled=False）结算 → 图谱零事件框架（回滚保障）──
    kg, _, am = build(None)
    settle(am, GATHER_OAK, FAIL_RES, False)
    check("默认关不落图",
          not any(n.startswith("行动经验:") for n in kg.nodes)
          and not any(n.startswith("行动结果:") for n in kg.nodes)
          and am._event_frame_nid is None)

    # ── 2. 失败结算 → 事件框架节点成形（label/space/type）──
    kg, _, am = build(EVF_ON)
    settle(am, GATHER_OAK, FAIL_RES, False)
    node = evf_node(kg, "行动经验:gather_resource(oak_log)")
    check("失败事件节点存在",
          node is not None
          and node.label == "declarative-episodic"
          and node.graph_space == "episodic"
          and node.extra_attrs.get("type") == "action_experience"
          and node.extra_attrs.get("fail_count") == 1)
    leaf = evf_node(kg, "行动结果:oak_log:failed")
    check("结果叶子存在",
          leaf is not None and leaf.graph_space == "semantic"
          and leaf.extra_attrs.get("type") == "outcome")

    # ── 3. 极性槽位边 + total_w>0 ──
    inv = kg.get_edge(node.id, "oak_log", "涉及")
    leaf_e = kg.get_edge(node.id, leaf.id, "结果")
    check("涉及负极性 -0.8",
          inv is not None and abs(float(inv.weight) + 0.8) < 1e-9
          and inv.relation_category == "cognitive_relation")
    check("结果边 +0.9",
          leaf_e is not None and abs(float(leaf_e.weight) - 0.9) < 1e-9)
    check("total_w>0（抑制可发行）",
          total_positive_w(kg, node.id) > 0)

    # ── 4. 扩散抑制机制自证：失败框架结构 → 实体激活被压低 ──
    # 隔离构造（不经 settle 链）：事件节点 act=1.2 + 涉及 -0.8 + 结果 +0.9
    # 在活跃前沿，20 拍 decay+diffuse → oak_log 大幅下降；无框架对照仅
    # natural 衰减。注意实况中同帧还有实例节点 activate_from_inputs 正推
    # （执行留痕），两者预算竞争、需多次失败才净负——见 CHANGE_LOG C21；
    # 本断言只证"落图结构对扩散层的驱动"（实验 D 数量级同源）。
    def _diffuse_isolation(with_frame):
        kg = KnowledgeGraph()
        kg.add_node(Node(id="oak_log", weight=0.5, graph_space="semantic",
                         activation=2.0))
        engine = DiffusionEngine(kg, config=CFG)
        if with_frame:
            kg.add_node(Node(id="行动经验:gather_resource(oak_log)",
                             weight=0.5, label="declarative-episodic",
                             graph_space="episodic", activation=1.2))
            kg.add_node(Node(id="行动结果:oak_log:failed", weight=0.5,
                             label="declarative-semantic",
                             graph_space="semantic"))
            kg.add_edge(Edge(src="行动经验:gather_resource(oak_log)",
                             dst="oak_log", relation="涉及", weight=-0.8,
                             relation_category="cognitive_relation"))
            kg.add_edge(Edge(src="行动经验:gather_resource(oak_log)",
                             dst="行动结果:oak_log:failed", relation="结果",
                             weight=0.9,
                             relation_category="cognitive_relation"))
            engine.mark_active(["行动经验:gather_resource(oak_log)"])
        for _ in range(20):
            engine.decay_step()
            engine.diffuse_step()
        return float(kg.nodes["oak_log"].activation)

    a_inh = _diffuse_isolation(True)
    a_ctrl = _diffuse_isolation(False)
    check(f"扩散抑制自证（inhibit {a_inh:.3f} < control {a_ctrl:.3f}）",
          a_inh < a_ctrl - 0.05)

    # ── 5. 双向极性翻转（force_weight 锁死）──
    kg, _, am = build(EVF_ON)
    settle(am, GATHER_OAK, OK_RES, True)      # 成功 +0.5
    settle(am, GATHER_OAK, FAIL_RES, False)   # 再失败 → -0.8 重入
    inv = kg.get_edge("行动经验:gather_resource(oak_log)", "oak_log", "涉及")
    check("成功→失败：负极性重入",
          inv is not None and abs(float(inv.weight) + 0.8) < 1e-9)

    kg, _, am = build(EVF_ON)
    settle(am, GATHER_OAK, FAIL_RES, False)   # 失败 -0.8
    settle(am, GATHER_OAK, OK_RES, True)      # 成功 → +0.5 洗白
    inv = kg.get_edge("行动经验:gather_resource(oak_log)", "oak_log", "涉及")
    check("失败→成功：正极性洗白",
          inv is not None and abs(float(inv.weight) - 0.5) < 1e-9)

    # ── 6. 重复失败：边仅 1 条、fail_count 累加、权重不变 ──
    kg, _, am = build(EVF_ON)
    for _ in range(3):
        settle(am, GATHER_OAK, FAIL_RES, False)
    node = evf_node(kg, "行动经验:gather_resource(oak_log)")
    n_edges = sum(1 for e in kg.edges
                  if e.src == node.id and e.relation == "涉及")
    inv = kg.get_edge(node.id, "oak_log", "涉及")
    check("重复失败归并（边1条/计数3/权-0.8）",
          n_edges == 1 and node.extra_attrs.get("fail_count") == 3
          and abs(float(inv.weight) + 0.8) < 1e-9)

    # ── 7. cancelled 不落；target=None 降级签名 ──
    kg, _, am = build(EVF_ON)
    settle(am, GATHER_OAK, {"success": False, "reason": "cancelled: 用户打断",
                            "cancelled": True}, False)
    check("cancelled 不落",
          not any(n.startswith("行动经验:") for n in kg.nodes))
    settle(am, {"action_type": "explore_area", "target": None,
                "params": {}, "reason": [], "source": "test",
                "expected_effect": "探索"}, FAIL_RES, False)
    check("target=None 降级签名",
          evf_node(kg, "行动经验:explore_area") is not None
          and evf_node(kg, "行动结果:explore_area:failed") is not None)

    # ── 8. 运行时 enabled=False → 无新增写（运行时回滚实测）──
    kg, _, am = build(dict(EVF_ON))          # 拷贝：下方原地关 enabled 不污染他组
    settle(am, GATHER_OAK, FAIL_RES, False)
    before = {n for n in kg.nodes if n.startswith("行动经验:")}
    am.evf["enabled"] = False
    settle(am, GATHER_OAK, OK_RES, True)
    after = {n for n in kg.nodes if n.startswith("行动经验:")}
    check("运行时关闭后零写", before == after)

    # ── 9. engine=None 模式不炸（点火段守卫）──
    kg, _, am = build(dict(EVF_ON), with_engine=False)
    settle(am, GATHER_OAK, FAIL_RES, False)
    check("engine=None 落图可用（不点火）",
          evf_node(kg, "行动经验:gather_resource(oak_log)") is not None)

    print(f"\n{'='*60}\n事件框架落图电池: "
          f"{sum(1 for _ in FAILURES) == 0 and '全部通过' or '有失败项'}"
          f" | FAILURES={FAILURES}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())