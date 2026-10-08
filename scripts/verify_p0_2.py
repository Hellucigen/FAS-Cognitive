# -*- coding: utf-8 -*-
"""P0-2/P0-3/P0-4 验证：剧本边清除 + 好奇状态图内化 + 显式扩散 API。

复现启动序（pack 合并 + rename + runtime overlay）→ 跑 bootstrap_curiosity
→ 断言：
  B1  新好奇信号边 7 条存在
  B2  旧剧本边 14 条不存在（好奇→生成好奇问题 保留）
  B3  孤儿流水线节点不存在（知识缺口/需要学习/学习目标/主动提问/登记学习目标）
  B4  核心好奇节点仍在（好奇/生成好奇问题/等待回答/更新记忆/结束好奇状态）
  B5  好奇状态图内化：set/is/get/reset 读写 等待回答 节点属性，不依赖模块变量
  B6  diffuse_round(max_steps=N) 不改写 config
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledge_pack_manager import get_pack_manager
from graph_model import KnowledgeGraph
import curiosity_engine
from diffusion_engine import DiffusionEngine

failures = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def load_like_startup():
    pack_mgr = get_pack_manager()
    pack_mgr.scan_packs()
    kg = pack_mgr.merge_runtime_graph()
    runtime_backup = os.path.join("data", "runtime_graph.json")
    if os.path.exists(runtime_backup):
        saved_kg = KnowledgeGraph.load(runtime_backup)
        for nid, node in saved_kg.nodes.items():
            if nid not in kg.nodes:
                kg.nodes[nid] = node
        for edge in saved_kg.edges:
            if edge.src in kg.nodes and edge.dst in kg.nodes:
                if not any(e.src == edge.src and e.dst == edge.dst and e.relation == edge.relation
                           for e in kg.edges):
                    kg.edges.append(edge)
    return kg


NEW_SIGNAL_EDGES = [
    ("未知概念", "好奇", "trigger_curiosity"),
    ("未知关系", "好奇", "trigger_curiosity"),
    ("未知属性", "好奇", "trigger_curiosity"),
    ("未知事件", "好奇", "trigger_curiosity"),
    ("未知信息", "好奇", "trigger_curiosity"),
    ("需要确认", "好奇", "trigger_curiosity"),
    ("好奇", "生成好奇问题", "trigger_curiosity"),
]
OLD_SCRIPT_EDGES = [
    ("未知概念", "知识缺口", "trigger_curiosity"),
    ("未知关系", "知识缺口", "trigger_curiosity"),
    ("未知属性", "知识缺口", "trigger_curiosity"),
    ("未知事件", "知识缺口", "trigger_curiosity"),
    ("未知信息", "知识缺口", "trigger_curiosity"),
    ("需要确认", "知识缺口", "trigger_curiosity"),
    ("需要学习", "学习目标", "trigger_curiosity"),
    ("学习目标", "主动提问", "trigger_curiosity"),
    ("知识缺口", "好奇", "trigger_curiosity"),
    ("主动提问", "生成好奇问题", "trigger_curiosity"),
    ("生成好奇问题", "等待回答", "trigger_curiosity"),
    ("等待回答", "更新记忆", "resolve"),
    ("更新记忆", "知识完善", "resolve"),
    ("知识完善", "结束好奇状态", "resolve"),
]
ORPHANS = ["知识缺口", "需要学习", "学习目标", "主动提问", "登记学习目标"]
CORE_NODES = ["好奇", "生成好奇问题", "等待回答", "更新记忆", "结束好奇状态", "知识完善", "回答完成",
              "未知概念", "未知关系", "未知属性", "未知事件", "未知信息", "需要确认"]


def main():
    print("=" * 60)
    print("P0-2/P0-3/P0-4 验证")
    print("=" * 60)

    kg = load_like_startup()
    curiosity_engine.bootstrap_curiosity(kg)

    def has_edge(s, d, r):
        return any(e.src == s and e.dst == d and e.relation == r for e in kg.edges)

    print("\nB1. 新好奇信号边（7 条）")
    for s, d, r in NEW_SIGNAL_EDGES:
        check(f"{s} -[{r}]→ {d}", has_edge(s, d, r))

    print("\nB2. 旧剧本边已清除（14 条）")
    for s, d, r in OLD_SCRIPT_EDGES:
        check(f"无 {s} -[{r}]→ {d}", not has_edge(s, d, r))

    print("\nB3. 孤儿流水线节点已清除")
    for nid in ORPHANS:
        check(f"无节点 {nid}", kg.get_node(nid) is None)

    print("\nB4. 核心好奇节点仍在")
    for nid in CORE_NODES:
        check(f"节点 {nid}", kg.get_node(nid) is not None)

    print("\nB5. 好奇状态图内化")
    # 初始：无活跃好奇
    check("初始 is_curiosity_active=False", curiosity_engine.is_curiosity_active(kg) is False)
    # 设置问题 → 状态写入 等待回答 节点属性
    curiosity_engine.record_pending_inquiry(kg, "奶茶是什么？", "奶茶", None, {
        "unknown_nodes": ["奶茶"], "unknown_relations": [],
        "unknown_properties": [], "trigger_type": "unknown_concept",
    }, {})
    wait_node = kg.get_node("等待回答")
    st = wait_node.extra_attrs.get("pending_inquiry") if wait_node else None
    check("状态写入 等待回答.extra_attrs['pending_inquiry']",
          isinstance(st, dict) and st.get("active") is True and st.get("question") == "奶茶是什么？",
          f"attrs={st}")
    check("is_curiosity_active(kg)=True", curiosity_engine.is_curiosity_active(kg) is True)
    check("get_curiosity_state(kg) 返回完整状态",
          curiosity_engine.get_curiosity_state(kg).get("trigger_type") == "unknown_concept")
    check("状态可 JSON 序列化（随图持久化）",
          bool(__import__("json").dumps(wait_node.extra_attrs, ensure_ascii=False)))
    # resolve → 状态清空
    result = curiosity_engine.settle_inquiry(kg, "奶茶是一种碳酸饮料", parsed_nodes=[], config={})
    check("settle_inquiry 消费图内状态",
          result.get("settled") is True and result.get("question") == "奶茶是什么？")
    check("结算后状态清空", curiosity_engine.is_curiosity_active(kg) is False)
    check("无模块级 _current_curiosity 残留",
          not hasattr(curiosity_engine, "_current_curiosity"))

    print("\nB6. 显式扩散 API（max_steps 不碰 config）")
    config = {"theta_threshold": 0.01, "max_depth": 6}
    eng = DiffusionEngine(kg, config)
    eng.diffuse_round(max_steps=3)
    check("config['max_depth'] 未被改写", config.get("max_depth") == 6)
    check("diffuse_round 接受 max_steps 参数", "max_steps" in DiffusionEngine.diffuse_round.__code__.co_varnames)

    print("\n" + "=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 → {failures}")
        sys.exit(1)
    print("结果: 全部通过 ✔")
    sys.exit(0)


if __name__ == "__main__":
    main()
