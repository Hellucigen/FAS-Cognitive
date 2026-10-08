# -*- coding: utf-8 -*-
"""P0 全量验证：编译 + 导入 + 源码红线 + 图完整性。

前置：scripts/migrate_p0_2_graph.py 与 scripts/migrate_p0_6_activity.py 已运行。
断言：
  V1  全部触碰文件可编译、可导入
  V2  源码红线：无硬编码用户知识注入 / 无剧本边 / 无图外好奇状态 /
      无 config 体操 / 无死引用 / 无 回答记录 创建
  V3  图完整性：加载序复现 + bootstrap 后无剧本边、无孤儿节点、
      无 回答记录_* 节点、思考节点全部 cognitive 空间
  V4  行为结构：好奇信号边齐全；Self 身份边可落图（P0-5①）
"""
import os
import py_compile
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def main():
    print("=" * 60)
    print("P0 全量验证")
    print("=" * 60)

    # ── V1 编译 + 导入 ──
    print("\nV1. 编译与导入")
    touched = ["app.py", "curiosity_engine.py", "diffusion_engine.py",
               "self_graph.py", "self_model.py", "reflection_engine.py",
               "nlp_processor.py", "knowledge_pack_manager.py", "graph_model.py"]
    for f in touched:
        path = os.path.join(ROOT, f)
        try:
            py_compile.compile(path, doraise=True)
            check(f"编译 {f}", True)
        except Exception as e:
            check(f"编译 {f}", False, str(e)[:120])
    import importlib
    for mod in ["graph_model", "knowledge_pack_manager", "curiosity_engine",
                "diffusion_engine", "self_graph", "self_model",
                "reflection_engine", "nlp_processor"]:
        try:
            importlib.import_module(mod)
            check(f"导入 {mod}", True)
        except Exception as e:
            check(f"导入 {mod}", False, f"{type(e).__name__}: {e}"[:120])

    # V1b（2026-09-22 补）：作用域地雷静态扫描——py_compile 查不出
    # "局部 import 毒化整个函数"（/api/nlp 500 事故的根因），必须 AST 扫。
    # 验收标准：import 型命中 0（assign 型是 comprehension 假阳性高发区，
    # 由扫描器列出供人工核查，不作失败）。
    print("\nV1b. 作用域地雷扫描（scripts/check_unbound_scope.py）")
    import subprocess
    r = subprocess.run(
        [sys.executable, "-X", "utf8",
         os.path.join(ROOT, "scripts", "check_unbound_scope.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    first = (r.stdout or "").splitlines()
    check("全仓 import 型 UnboundLocalError 地雷 = 0", r.returncode == 0,
          first[0] if first else f"rc={r.returncode}")

    # ── V2 源码红线 ──
    print("\nV2. 源码红线")
    src = {}
    for f in ["app.py", "curiosity_engine.py", "self_model.py", "nlp_processor.py"]:
        src[f] = open(os.path.join(ROOT, f), encoding="utf-8").read()

    check("app.py 无 _taught_/_alias_patches 注入",
          not re.search(r"_taught_nodes|_taught_edges|_alias_patches", src["app.py"]))
    check("curiosity_engine 无 CURIOSITY_CHAIN 代码（注释除外）",
          not re.search(r"^CURIOSITY_CHAIN|CURIOSITY_CHAIN\s*=", src["curiosity_engine.py"], re.M))
    check("curiosity_engine 无模块级 _current_curiosity",
          "_current_curiosity" not in src["curiosity_engine.py"])
    check("curiosity_engine 无 EMOTION_KEYWORDS 词表",
          "EMOTION_KEYWORDS" not in src["curiosity_engine.py"])
    check("app.py 无 config[\"max_depth\"] 写入",
          not re.search(r'config\["max_depth"\]\s*=', src["app.py"]))
    check("nlp_processor 无 ChatOllama 死引用（注释提及不算）",
          not re.search(r"ChatOllama\s*\(", src["nlp_processor.py"]))
    check("app.py 不再创建 回答记录_ 节点",
          "回答记录_{int" not in src["app.py"])
    check("self_model 候选不来自 用户 主语",
          'src == "用户"' not in src["self_model.py"])

    # ── V3 图完整性 ──
    print("\nV3. 图完整性（加载序复现 + bootstrap）")
    from knowledge_pack_manager import get_pack_manager
    from graph_model import KnowledgeGraph
    import curiosity_engine

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
    curiosity_engine.bootstrap_curiosity(kg)

    def has_edge(s, d, r):
        return any(e.src == s and e.dst == d and e.relation == r for e in kg.edges)

    signal_edges = [(s, "好奇") for s in
                    ("未知概念", "未知关系", "未知属性", "未知事件", "未知信息", "需要确认")]
    check("好奇信号边齐全（6+1）",
          all(has_edge(s, d, "trigger_curiosity") for s, d in signal_edges)
          and has_edge("好奇", "生成好奇问题", "trigger_curiosity"))
    script_edges = [("知识缺口", "好奇"), ("学习目标", "主动提问"),
                    ("等待回答", "更新记忆"), ("更新记忆", "知识完善"),
                    ("知识完善", "结束好奇状态")]
    check("无剧本边残留",
          not any(has_edge(s, d, r) for s, d, r in
                  [(s, d, "trigger_curiosity") for s, d in script_edges[:2]] +
                  [(s, d, "resolve") for s, d in script_edges[2:]]))
    for orphan in ("知识缺口", "需要学习", "学习目标", "主动提问", "登记学习目标"):
        check(f"无孤儿节点 {orphan}", kg.get_node(orphan) is None)
    answers = [nid for nid in kg.nodes if nid.startswith("回答记录_")]
    check("回答记录_* 节点已清零", len(answers) == 0, f"剩余={len(answers)}")
    thoughts_bad = [nid for nid, n in kg.nodes.items()
                    if (nid.startswith("思考_") or n.extra_attrs.get("type") == "thought")
                    and getattr(n, "graph_space", "") != "cognitive"]
    check("思考_* 节点全部 cognitive 空间", len(thoughts_bad) == 0,
          f"违规={len(thoughts_bad)}")
    taught = ["奶茶", "饮料", "挖矿", "连锁挖矿模组", "连锁采集", "卓越前线", "银矿", "通用机械", "铀矿"]
    check("9 个 taught_by_user 节点在位",
          all(kg.get_node(t) and kg.get_node(t).extra_attrs.get("taught_by_user") for t in taught))

    # ── V4 行为结构 ──
    print("\nV4. 行为结构")
    curiosity_engine.record_pending_inquiry(kg, "测试问题？", "X", None, {
        "unknown_nodes": ["X"], "unknown_relations": [],
        "unknown_properties": [], "trigger_type": "unknown_concept"}, {})
    wait_node = kg.get_node("等待回答")
    st = wait_node.extra_attrs.get("pending_inquiry") if wait_node else None
    check("探索提问状态挂载于 等待回答 节点",
          isinstance(st, dict) and st.get("active") is True)
    r = curiosity_engine.settle_inquiry(kg, "X就是测试回答", parsed_nodes=[], config={})
    check("settle 消费图内状态", r.get("settled") is True)
    check("结算后状态清空", curiosity_engine.is_curiosity_active(kg) is False)

    # Self 身份边可落图（对已加载图跑 identity 段）
    import self_graph
    try:
        self_graph._ensure_node_and_edge(kg, "Self", "__验证临时节点__", "验证",
                                         node_weight=0.1, edge_weight=0.5)
        e = kg.get_edge("Self", "__验证临时节点__", "验证")
        check("P0-5① _ensure_node_and_edge 落边", e is not None)
        kg.remove_node("__验证临时节点__")
    except Exception as ex:
        check("P0-5① _ensure_node_and_edge 落边", False, f"{type(ex).__name__}: {ex}"[:120])

    print("\n" + "=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 → {failures}")
        sys.exit(1)
    print("结果: 全部通过 ✔")
    sys.exit(0)


if __name__ == "__main__":
    main()
