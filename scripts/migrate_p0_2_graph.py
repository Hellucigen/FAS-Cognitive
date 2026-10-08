# -*- coding: utf-8 -*-
"""P0-2 数据迁移：从持久化图谱中清除旧 CURIOSITY_CHAIN 剧本边与孤儿流水线节点。

代码已删除 CURIOSITY_CHAIN 并改用 CURIOSITY_SIGNAL_EDGES（bootstrap 只增不删），
因此旧边必须从 data/runtime_graph.json 中移除，否则剧本仍在图上运行。

动作：
  1. 备份 data/runtime_graph.json → data/runtime_graph.json.bak_p0_2
  2. 删除旧链的 14 条边（保留 好奇→生成好奇问题，它是新信号集成员）
  3. 删除孤儿流水线节点（知识缺口/需要学习/学习目标/主动提问/登记学习目标），
     仅当删除上述边后它们不再有任何关联边
  4. 保存并报告
"""
import json
import shutil
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_model import KnowledgeGraph

GRAPH_PATH = os.path.join("data", "runtime_graph.json")
BACKUP_PATH = os.path.join("data", "runtime_graph.json.bak_p0_2")

# 旧 CURIOSITY_CHAIN 全部 15 条边（按 curiosity_engine.py 迁移前定义）
OLD_CHAIN = [
    ("未知概念", "知识缺口", "trigger_curiosity", 0.5),
    ("未知关系", "知识缺口", "trigger_curiosity", 0.5),
    ("未知属性", "知识缺口", "trigger_curiosity", 0.5),
    ("未知事件", "知识缺口", "trigger_curiosity", 0.5),
    ("未知信息", "知识缺口", "trigger_curiosity", 0.5),
    ("需要确认", "知识缺口", "trigger_curiosity", 0.5),
    ("需要学习", "学习目标", "trigger_curiosity", 0.5),
    ("学习目标", "主动提问", "trigger_curiosity", 0.5),
    ("知识缺口", "好奇", "trigger_curiosity", 0.6),
    ("好奇", "生成好奇问题", "trigger_curiosity", 0.6),   # 新信号集成员，不删
    ("主动提问", "生成好奇问题", "trigger_curiosity", 0.5),
    ("生成好奇问题", "等待回答", "trigger_curiosity", 0.5),
    ("等待回答", "更新记忆", "resolve", 0.5),
    ("更新记忆", "知识完善", "resolve", 0.5),
    ("知识完善", "结束好奇状态", "resolve", 0.5),
]
KEEP = {("好奇", "生成好奇问题", "trigger_curiosity")}
REMOVE_EDGES = [(s, d, r) for (s, d, r, _w) in OLD_CHAIN if (s, d, r) not in KEEP]

ORPHAN_CANDIDATES = ["知识缺口", "需要学习", "学习目标", "主动提问", "登记学习目标"]


def main():
    if not os.path.exists(GRAPH_PATH):
        print(f"未找到 {GRAPH_PATH}，无迁移对象")
        sys.exit(1)

    shutil.copy2(GRAPH_PATH, BACKUP_PATH)
    print(f"已备份 → {BACKUP_PATH}")

    kg = KnowledgeGraph.load(GRAPH_PATH)
    n0, e0 = len(kg.nodes), len(kg.edges)
    print(f"迁移前: {n0} 节点, {e0} 边")

    removed_edges = []
    for src, dst, rel in REMOVE_EDGES:
        e = kg.get_edge(src, dst, rel)
        if e is not None:
            kg.edges.remove(e)
            removed_edges.append(f"{src} -[{rel}]→ {dst}")
    print(f"删除剧本边 {len(removed_edges)} 条:")
    for line in removed_edges:
        print(f"  - {line}")
    missing = len(REMOVE_EDGES) - len(removed_edges)
    if missing:
        print(f"  ({missing} 条本就不存在)")

    removed_nodes = []
    kept_nodes = []
    for nid in ORPHAN_CANDIDATES:
        node = kg.get_node(nid)
        if node is None:
            continue
        incident = [e for e in kg.edges if e.src == nid or e.dst == nid]
        if not incident:
            kg.remove_node(nid)
            removed_nodes.append(nid)
        else:
            kept_nodes.append(f"{nid} (仍有 {len(incident)} 条关联边: "
                              + "; ".join(f"{e.src}-[{e.relation}]→{e.dst}" for e in incident[:3]) + ")")
    print(f"删除孤儿节点 {len(removed_nodes)} 个: {removed_nodes or '无'}")
    if kept_nodes:
        print(f"保留（仍有其他关联边，需人工评估）: {kept_nodes}")

    kg.save(GRAPH_PATH)
    print(f"迁移后: {len(kg.nodes)} 节点, {len(kg.edges)} 边 (Δ{len(kg.nodes)-n0:+d}/{len(kg.edges)-e0:+d})")
    print("完成")


if __name__ == "__main__":
    main()
