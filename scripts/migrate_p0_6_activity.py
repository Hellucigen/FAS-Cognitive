# -*- coding: utf-8 -*-
"""P0-5③ + P0-6 存量迁移：清理活动痕迹节点，修正思考节点空间。

动作（对 data/runtime_graph.json）：
  1. 备份 → data/runtime_graph.json.bak_p0_5_6
  2. 删除全部 回答记录_* 节点及其 源自 边（回答文本归宿=chat_log，非图）
  3. 思考_* 节点 graph_space → cognitive（认知活动不是经历）
  4. 删除因回答记录删除而悬空的 源自 边
  5. 保存并报告

注意：搜索记录_* 节点保留（有 执行/涉及 结构边，数量少，
留待 P1 行为竞争化时统一评估）。
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_model import KnowledgeGraph

GRAPH_PATH = os.path.join("data", "runtime_graph.json")
BACKUP_PATH = os.path.join("data", "runtime_graph.json.bak_p0_5_6")


def main():
    if not os.path.exists(GRAPH_PATH):
        print(f"未找到 {GRAPH_PATH}")
        sys.exit(1)

    shutil.copy2(GRAPH_PATH, BACKUP_PATH)
    print(f"已备份 → {BACKUP_PATH}")

    kg = KnowledgeGraph.load(GRAPH_PATH)
    n0, e0 = len(kg.nodes), len(kg.edges)
    print(f"迁移前: {n0} 节点, {e0} 边")

    # ── 1. 回答记录_* 节点 ──
    answer_ids = [nid for nid in kg.nodes if nid.startswith("回答记录_")]
    print(f"回答记录_* 节点: {len(answer_ids)} 个")
    for nid in answer_ids:
        kg.remove_node(nid)   # remove_node 同时清除关联边（含 源自）

    # ── 2. 悬空的 源自 边（以防历史数据有 反向/漏网） ──
    dangling = [e for e in kg.edges if e.relation == "源自" and e.src.startswith("回答记录_")]
    for e in dangling:
        kg.edges.remove(e)
    print(f"额外清除悬空 源自 边: {len(dangling)} 条")

    # ── 3. 思考_* 节点空间迁移 ──
    thought_fixed = 0
    for nid, node in kg.nodes.items():
        if nid.startswith("思考_") or node.extra_attrs.get("type") == "thought":
            if getattr(node, "graph_space", "") != "cognitive":
                node.graph_space = "cognitive"
                thought_fixed += 1
    print(f"思考_* 节点 graph_space → cognitive: {thought_fixed} 个")

    # ── 4. 报告搜索记录（保留） ──
    sr = [nid for nid in kg.nodes if nid.startswith("搜索记录_")]
    print(f"搜索记录_* 保留: {len(sr)} 个 {sr}")

    kg.save(GRAPH_PATH)
    print(f"迁移后: {len(kg.nodes)} 节点, {len(kg.edges)} 边 "
          f"(Δ{len(kg.nodes)-n0:+d}/{len(kg.edges)-e0:+d})")
    print("完成")


if __name__ == "__main__":
    main()
