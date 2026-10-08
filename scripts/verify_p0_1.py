# -*- coding: utf-8 -*-
"""P0-1 验证：1:1 复现 app.py 启动加载序，断言用户教学知识 pack 化后完整无损。

复现序列（与 app.py:121-170 一致）：
  scan_packs → merge_runtime_graph → 应用 rename_map → 叠加 data/runtime_graph.json

断言：
  A1  9 个 taught_by_user 节点存在且属性完整
  A2  Minecraft/Fascinator 别名完整（别名补丁删除后仍存活）
  A3  9 条教学边存在且 weight=0.9
  A4  图规模与基线一致（401 节点 / 529 边），无重复
  A5  merge 幂等（二次合并规模不变）
  A6  源代码无 _taught/_alias_patches 残留
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledge_pack_manager import get_pack_manager
from graph_model import KnowledgeGraph

TAUGHT_NODES = {
    "奶茶": {"category": "drink"},
    "饮料": {"category": "food_category"},
    "挖矿": {"category": "game_activity"},
    "连锁挖矿模组": {"category": "minecraft_mod", "aliases": ["连锁挖矿", "VeinMiner"]},
    "连锁采集": {"category": "game_mechanic"},
    "卓越前线": {"category": "minecraft_mod"},
    "银矿": {"category": "ore"},
    "通用机械": {"category": "minecraft_mod", "aliases": ["Mekanism", "mekanism"]},
    "铀矿": {"category": "ore"},
}
TAUGHT_EDGES = [
    ("奶茶", "饮料", "属于"),
    ("挖矿", "Minecraft", "属于"),
    ("连锁挖矿模组", "Minecraft", "属于"),
    ("连锁挖矿模组", "挖矿", "功能是"),
    ("连锁挖矿模组", "连锁采集", "特性"),
    ("卓越前线", "Minecraft", "属于"),
    ("卓越前线", "银矿", "产出"),
    ("通用机械", "Minecraft", "属于"),
    ("通用机械", "铀矿", "产出"),
]
# P0-2/P0-6 迁移后基线（2026-09-02）：删 14 剧本边+5 孤儿节点、52 回答记录、思考节点迁移
BASELINE_NODES, BASELINE_EDGES = 401, 529

failures = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def load_like_startup():
    """复现 app.py 启动加载序。"""
    pack_mgr = get_pack_manager()
    pack_mgr.scan_packs()
    kg = pack_mgr.merge_runtime_graph()

    # rename_map（app.py:126-146）
    rename_map_path = os.path.join("data", "rename_map.json")
    if os.path.exists(rename_map_path):
        rmap = json.load(open(rename_map_path, encoding="utf-8"))
        for new_id, old_id in list(rmap.items()):
            if old_id in kg.nodes and new_id not in kg.nodes:
                node = kg.nodes.pop(old_id)
                node.id = new_id
                kg.nodes[new_id] = node
                for edge in kg.edges:
                    if edge.src == old_id:
                        edge.src = new_id
                    if edge.dst == old_id:
                        edge.dst = new_id
                if old_id in pack_mgr.runtime_node_map:
                    pack_mgr.runtime_node_map[new_id] = pack_mgr.runtime_node_map.pop(old_id)

    # runtime overlay（app.py:149-170）
    runtime_backup = os.path.join("data", "runtime_graph.json")
    if os.path.exists(runtime_backup):
        saved_kg = KnowledgeGraph.load(runtime_backup)
        for nid, node in saved_kg.nodes.items():
            if nid not in kg.nodes:
                kg.nodes[nid] = node
                pack_mgr.runtime_node_map[nid] = "_runtime"
            else:
                if node.label:
                    kg.nodes[nid].label = node.label
                if hasattr(node, "weight") and node.weight != 0.5:
                    kg.nodes[nid].weight = node.weight
        for edge in saved_kg.edges:
            if edge.src in kg.nodes and edge.dst in kg.nodes:
                exists = any(e.src == edge.src and e.dst == edge.dst and e.relation == edge.relation
                             for e in kg.edges)
                if not exists:
                    kg.edges.append(edge)
    return kg


def main():
    print("=" * 60)
    print("P0-1 验证：用户教学知识 pack 化")
    print("=" * 60)

    kg = load_like_startup()

    # A1 taught 节点
    print("\nA1. taught_by_user 节点（9 个）")
    for nid, expect_attrs in TAUGHT_NODES.items():
        node = kg.get_node(nid)
        ok = node is not None and node.extra_attrs.get("taught_by_user") is True
        attrs_ok = all(node.extra_attrs.get(k) == v for k, v in expect_attrs.items()) if node else False
        check(f"节点 {nid}", ok and attrs_ok,
              f"attrs={json.dumps(node.extra_attrs, ensure_ascii=False)}" if node else "缺失")

    # A2 别名
    print("\nA2. 别名（启动补丁已删除）")
    mc = kg.get_node("Minecraft")
    fas = kg.get_node("Fascinator")
    mc_al = set((mc.extra_attrs.get("aliases") if mc else []) or [])
    fas_al = set((fas.extra_attrs.get("aliases") if fas else []) or [])
    check("Minecraft aliases ⊇ {Mc, MC, 我的世界, Minecraft}",
          {"Mc", "MC", "我的世界", "Minecraft"} <= mc_al, f"实际={sorted(mc_al)}")
    check("Fascinator aliases ⊇ {FAS, fas}", {"FAS", "fas"} <= fas_al, f"实际={sorted(fas_al)}")

    # A3 教学边
    print("\nA3. 教学边（9 条）")
    edge_map = {}
    for e in kg.edges:
        edge_map.setdefault((e.src, e.dst, e.relation), []).append(e)
    for src, dst, rel in TAUGHT_EDGES:
        found = edge_map.get((src, dst, rel), [])
        check(f"{src} -[{rel}]→ {dst}",
              len(found) == 1 and abs(found[0].weight - 0.9) < 1e-9,
              f"count={len(found)} weight={found[0].weight if found else None}")

    # A4 规模与重复
    print("\nA4. 图规模与无重复")
    check(f"节点数 == {BASELINE_NODES}", len(kg.nodes) == BASELINE_NODES, f"实际={len(kg.nodes)}")
    check(f"边数 == {BASELINE_EDGES}", len(kg.edges) == BASELINE_EDGES, f"实际={len(kg.edges)}")
    dup_nodes = len(kg.nodes) != len(set(kg.nodes.keys()))
    check("节点 ID 无重复", not dup_nodes)
    dup_edges = sum(1 for v in edge_map.values() if len(v) > 1)
    check("边 无重复(s,d,r)", dup_edges == 0, f"重复组={dup_edges}")

    # A5 幂等
    print("\nA5. merge 幂等")
    kg2 = load_like_startup()
    check("二次加载节点数不变", len(kg2.nodes) == len(kg.nodes),
          f"{len(kg.nodes)} → {len(kg2.nodes)}")
    check("二次加载边数不变", len(kg2.edges) == len(kg.edges),
          f"{len(kg.edges)} → {len(kg2.edges)}")

    # A6 源代码无残留
    print("\nA6. 源代码无 _taught/_alias_patches 残留")
    src = open("app.py", encoding="utf-8").read()
    check("app.py 无 _taught_nodes/_taught_edges/_alias_patches",
          not re.search(r"_taught_nodes|_taught_edges|_alias_patches", src))
    check("app.py 仍引用 user_teaching pack（迁移指针）", "user_teaching_2026-07" in src)

    print("\n" + "=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 → {failures}")
        sys.exit(1)
    print("结果: 全部通过 ✔")
    sys.exit(0)


if __name__ == "__main__":
    main()
