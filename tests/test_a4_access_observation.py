# test_a4_access_observation.py — 晋升 dry-run 观测回归锁（2026-09-30,审计 A4）
# ============================================================================
# A4 结论: `mark_accessed`(episodic_buffer.py) 修复前全仓零调用,access_count
# 恒 0,晋升排序/阈值退化到仅 importance。本文件锁住接入后的三个不变式:
#   1. find_by_node: 按图节点 id 检索经历（可重复、去重语义、只读）
#   2. access 计数只改变候选排序/达标,不触发晋升（候选 ≠ 晋升,锁死"dry-run
#      观测不激活自动晋升"的安全边界）
#   3. 遗忘时高频访问经历优先保留（容量超额排序 key 恢复真实性）
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_a4_access_observation.py
# ============================================================================

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from episodic_buffer import EpisodicBuffer, EpisodicExperience  # noqa: E402

_FAILURES = []


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    if not cond:
        _FAILURES.append(name)
    print(f"[{mark}] {name}", ("  " + detail) if (not cond and detail) else "")


def add_exp(buf, text, node_ids, importance=0.5):
    """加一条经历并返回 buffer 实际存储的实例（add_experience 内部会新建）"""
    exp = EpisodicExperience(
        raw_text=text,
        nodes=[{"id": n, "node_type": "实体"} for n in node_ids],
        edges=[],
    )
    exp.importance = importance
    stored = buf.add_experience(exp.raw_text, exp.nodes, exp.edges)
    stored.importance = importance  # 重要度写在存储实例上
    return stored


# ── 1. find_by_node: 命中/未命中/多条/截断/只读 ──────────────────────────
buf = EpisodicBuffer(capacity=100)
e1 = add_exp(buf, "去过一次餐厅", ["餐厅", "用户"])
e2 = add_exp(buf, "家里修过水管", ["家", "水管"])
e3 = add_exp(buf, "水管又在滴", ["水管"])

hit_water = buf.find_by_node("水管")
check("find_by_node: 按节点 id 命中全部引用经历",
      [e.raw_text for e in hit_water] == ["家里修过水管", "水管又在滴"],
      f"命中 {len(hit_water)} 条: {[e.raw_text for e in hit_water]}")

check("find_by_node: 未命中节点返回空",
      buf.find_by_node("不存在节点") == [])

check("find_by_node: max_n 截断",
      len(buf.find_by_node("水管", max_n=1)) == 1)

check("find_by_node: 纯检索不改计数(只读)",
      all(e.access_count == 0 for e in buf._experiences))

# ── 2. access 计数只影响候选分布,不触发晋升 ─────────────────────────────
# 2a. 未达标前不是候选(importance 0.5 < 0.7,access 0 < 3)
check("check_promotion: 初始(acc=0,imp=0.5)不达标",
      buf.check_promotion(e1) is False)

# 2b. mark_accessed 计数生效,达标后进入候选
for _ in range(3):
    buf.mark_accessed(e1)
check("mark_accessed: 计数累加", e1.access_count == 3)
check("check_promotion: acc=3 ≥ 阈值 → 达标(access 分支复活)",
      buf.check_promotion(e1) is True)

cands = buf.get_promotion_candidates()
cand_texts = [c.raw_text for c in cands]
check("get_promotion_candidates: access 驱动候选在列",
      e1.raw_text in cand_texts,
      f"候选: {cand_texts}")

# 2c. 锁定安全边界: 候选 ≠ 晋升。拿到候选列表不改变 promoted 状态,
#     自动路径(mark_accessed 随 FAISS 命中被调用)绝不 promote。
check("安全边界: 候选查询后 promoted 仍为 False(不自动晋升)",
      e1.promoted is False and buf.get_promotion_candidates() is not None)

# 2d. 排序: access 项参与排序(0.5+3*0.1=0.8 > 0.75+0=0.75)
e4 = add_exp(buf, "大学时学过嵌条", ["嵌条"], importance=0.75)
cands2 = buf.get_promotion_candidates()
order = [c.raw_text for c in cands2]
check("排序: access 驱动的候选排在高 importance 零 access 之前",
      order.index(e1.raw_text) < order.index(e4.raw_text),
      f"顺序: {order}")

# ── 3. 遗忘: 高频访问经历优先保留 ────────────────────────────────────────
buf3 = EpisodicBuffer(capacity=3)
fa = add_exp(buf3, "常被想起的事", ["A"], importance=0.1)
fb = add_exp(buf3, "只提过一回的事", ["B"], importance=0.1)
fc = add_exp(buf3, "也有一次", ["C"], importance=0.1)
for _ in range(5):
    buf3.mark_accessed(fa)
fd = add_exp(buf3, "新来的", ["D"], importance=0.1)  # 超额 → 遗忘

alive = [e.raw_text for e in buf3._experiences]
check("遗忘: 高频访问经历保留(access 项恢复排序真实性)",
      fa.raw_text in alive,
      f"存活: {alive}")
check("遗忘: 未访问的低 importance 经历先被遗忘",
      "只提过一回的事" in str(buf3._forgotten[0]),
      f"被遗忘第一条: {buf3._forgotten[0]}")

print()
if _FAILURES:
    print(f"✗ {len(_FAILURES)} 项失败: {_FAILURES}")
    sys.exit(1)
print("✓ A4 观测接入不变式全部通过(零生成图触碰)")
sys.exit(0)