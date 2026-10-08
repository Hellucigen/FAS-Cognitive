# test_pers01_guard.py — PERS-01 三重守卫回归锁（2026-09-30,审计 A5）
# ============================================================================
# 事故基线（2026-09-13）: `KnowledgeGraph.load` 是 classmethod,空图覆写
# 运行图谱 894→162 节点。本文件锁住三个守卫路径:
#   守卫1 load: 损坏文件 → _load_failed=True 标记（不静默）
#   守卫2 save: _load_failed 实例拒绝保存（防空图覆写损坏源）
#   守卫3 save: 旧文件读不出(损坏) → 备份 + 拒绝保存
# 另锁 PIN-11: add_edge 端点缺失 → 留 warning（不再静默丢边）
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_pers01_guard.py
# ============================================================================

import json
import logging
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 不全局 disable(会连带屏蔽 handler 捕获);只把 graph_model logger 的
# 传播导向内存(见 PIN-11 段),其余噪音由模块级 level 过滤。
logging.disable(logging.NOTSET)

from graph_model import KnowledgeGraph, Node, Edge  # noqa: E402

_FAILURES = []


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    if not cond:
        _FAILURES.append(name)
    print(f"[{mark}] {name}", ("  " + detail) if (not cond and detail) else "")


def make_kg(n=5):
    kg = KnowledgeGraph()
    for i in range(n):
        kg.nodes[f"n{i}"] = Node(id=f"n{i}")
    return kg


with tempfile.TemporaryDirectory() as td:
    p = pathlib.Path(td) / "runtime_graph.json"

    # ── 守卫 1: load 损坏文件 → _load_failed 标记 + 返回空实例 ──
    p.write_text("{ this is broken json", encoding="utf-8")
    kg1 = KnowledgeGraph.load(str(p))
    check("守卫1: load(损坏) 返回空实例且带 _load_failed 标记",
          len(kg1.nodes) == 0 and getattr(kg1, "_load_failed", False) is True)

    # 对照: 正常文件 load 不带标记
    good = make_kg()
    p2 = pathlib.Path(td) / "good.json"
    good.save(str(p2))
    kg_ok = KnowledgeGraph.load(str(p2))
    check("守卫1: load(正常) 不带 _load_failed 标记",
          getattr(kg_ok, "_load_failed", False) is False)

    # ── 守卫 2: _load_failed 实例拒绝保存(不覆盖损坏源) ──
    before = p.read_bytes()
    r = kg1.save(str(p))
    check("守卫2: _load_failed 实例 save 拒绝(返回 False)且源文件未变",
          r is False and p.read_bytes() == before,
          f"save 返回 {r}, 文件是否被改: {p.read_bytes() != before}")

    # ── 守卫 3: 构造过程源文件损坏 → save 拒绝 + 留 unreadable 副本 ──
    # 模拟: 实例正常(非 load 而来), 但磁盘源文件在 save 前被写坏
    kg3 = make_kg()
    p.write_text("{ also broken", encoding="utf-8")  # 合法新内容,模拟损坏
    r3 = kg3.save(str(p))
    check("守卫3: 源文件损坏时 save 拒绝(返回 False)",
          r3 is False, f"save 返回 {r3}")
    baks = list(pathlib.Path(td).glob("runtime_graph.json.bak.unreadable_*"))
    check("守卫3: 源损坏时留下 unreadable 副本",
          len(baks) == 1, f"副本数 {len(baks)}")

    # 对照: 正常保存路径不受阻(新文件首存)
    p3 = pathlib.Path(td) / "fresh.json"
    r_ok = kg_ok.save(str(p3))
    check("对照: 正常实例 save 照常成功(返回 None)且文件有效",
          r_ok is None and json.loads(p3.read_text(encoding="utf-8"))["nodes"] is not None,
          f"save 返回 {r_ok}")

    # ── PIN-11: add_edge 端点缺失留 warning(handler 捕获) ──
    import io
    from logging import StreamHandler
    stream = io.StringIO()
    h = StreamHandler(stream)
    _fm = logging.getLogger("graph_model")
    h_level = _fm.level
    h_handlers = list(_fm.handlers)
    h_prop = _fm.propagate
    _fm.setLevel(logging.WARNING)
    _fm.handlers = [h]
    _fm.propagate = False
    try:
        kg4 = make_kg(2)
        ok = kg4.add_edge(Edge(src="n0", dst="ghost", relation="涉及"))
        got = stream.getvalue()
        check("PIN-11: add_edge 端点缺失 → False + warning 留痕",
              ok is False and "add_edge 被丢弃" in got,
              f"ok={ok}, 日志含标记={bool(got)}")
        # 正常端点不受影响
        ok2 = kg4.add_edge(Edge(src="n0", dst="n1", relation="涉及"))
        check("PIN-11: 正常端点 add_edge 照常成功(True)",
              ok2 is True, f"ok2={ok2}")
    finally:
        _fm.handlers = h_handlers
        _fm.setLevel(h_level)
        _fm.propagate = h_prop

print()
if _FAILURES:
    print(f"✗ {len(_FAILURES)} 项失败: {_FAILURES}")
    sys.exit(1)
print("✓ PERS-01 三重守卫 + PIN-11 全部通过(零生产图触碰)")
sys.exit(0)