# test_cognitive_locks.py — 认知锁机制测试
# 离线、临时内存 KG、临时锁文件（不碰 data/runtime_graph.json 与真实锁文件）。
# 覆盖：blocking/non_blocking 的扩散语义分界、启用/禁用、多锁组合、删除锁不删对象、
#       锁不改动对象数据、过期、作用范围隔离、悬空锁、审计。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_cognitive_locks.py

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_model import KnowledgeGraph, Node, Edge
from diffusion_engine import DiffusionEngine
from cognitive_locks import LockRegistry, edge_key, parse_edge_key

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


CFG = {
    "lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 4,
    "theta_threshold": 0.01, "activation_max": 5.0, "min_spread_threshold": 0.01,
    "activation_epsilon": 1e-4, "input_similarity_floor": 0.5,
    "input_default_bonus": 0.5, "theta_action": 0.5,
}


def build():
    """甲 → 乙 → 丙 链 + 一个 procedural 节点丁（动作队列入选资格）。"""
    kg = KnowledgeGraph()
    for nid in ("甲", "乙", "丙"):
        kg.add_node(Node(id=nid, label="declarative-semantic"))
    kg.add_node(Node(id="丁", label="procedural"))
    kg.add_edge(Edge(src="甲", dst="乙", relation="关联", weight=0.9,
                     relation_category="semantic_relation"))
    kg.add_edge(Edge(src="乙", dst="丙", relation="关联", weight=0.9,
                     relation_category="semantic_relation"))
    kg.add_edge(Edge(src="甲", dst="丁", relation="触发", weight=0.9,
                     relation_category="semantic_relation"))
    eng = DiffusionEngine(kg, dict(CFG))
    eng.name_to_node = dict(kg.nodes)
    # 临时锁文件：不建文件（Windows 下 mkstemp 的 fd 不关就删不掉）
    tmp = os.path.join(tempfile.gettempdir(), f"fas_test_locks_{os.getpid()}_{id(kg)}.json")
    reg = LockRegistry(path=tmp, kg=kg)
    eng.set_lock_registry(reg)
    return kg, eng, reg, tmp


def reset_run(kg, eng, seeds):
    """清激活 → 种子 → 扩散 → 返回 (topk ids, 各节点激活)。"""
    for n in kg.nodes.values():
        n.activation = 0.0
    eng._active_nodes.clear()
    eng._fired_round.clear()
    eng._active_edges.clear()
    eng.activate_from_inputs(seeds, [])
    eng.diffuse_round()
    top, _ = eng.get_topk(k=10)
    return [n.id for n in top], {nid: kg.nodes[nid].activation for nid in kg.nodes}


# ── 1. 基线：无锁时链式传播正常 ────────────────────────────
kg, eng, reg, path = build()
top0, act0 = reset_run(kg, eng, ["甲"])
check("基线：链式传播（乙、丙均被点亮）",
      act0["乙"] > 0 and act0["丙"] > 0, str(act0))
check("基线：TopK 含全链", set(top0) >= {"甲", "乙", "丙"}, str(top0))

# ── 2. blocking：被锁对象不参与扩散（激活恒 0，链断） ──────
r = reg.create("node", "乙", "blocking", "diffusion", "阻塞测试")
check("blocking 锁创建成功", r.get("ok"))
top1, act1 = reset_run(kg, eng, ["甲"])
check("blocking：被锁节点激活恒为 0", act1["乙"] == 0.0, str(act1["乙"]))
check("blocking：下游拿不到信号（链断）", act1["丙"] == 0.0, str(act1["丙"]))
check("blocking：被锁节点不在输出", "乙" not in top1, str(top1))
check("blocking：锁不改动节点原始数据",
      kg.nodes["乙"].label == "declarative-semantic"
      and abs(kg.edges[0].weight - 0.9) < 1e-9)

# ── 3. non_blocking：参与扩散但不进输出（核心分界） ────────
reg.delete(r["lock"]["id"])
r2 = reg.create("node", "乙", "non_blocking", "diffusion", "非阻塞测试")
top2, act2 = reset_run(kg, eng, ["甲"])
check("non_blocking：被锁节点照常获得激活", act2["乙"] > 0, str(act2["乙"]))
check("non_blocking：下游仍收到信号（内部参与）", act2["丙"] > 0, str(act2["丙"]))
check("non_blocking：被锁节点不进 TopK 输出", "乙" not in top2, str(top2))
check("non_blocking：非被锁节点不受影响", "丙" in top2, str(top2))

# ── 4. 启用/禁用即时生效 ──────────────────────────────────
reg.set_enabled(r2["lock"]["id"], False)
top3, act3 = reset_run(kg, eng, ["甲"])
check("禁用锁后恢复参与与输出", ("乙" in top3) and act3["乙"] > 0, str(top3))
reg.set_enabled(r2["lock"]["id"], True)
top4, _ = reset_run(kg, eng, ["甲"])
check("重新启用后再次被隐藏", "乙" not in top4, str(top4))

# ── 5. 多锁组合：并集（高优先级不能解锁低优先级） ──────────
reg.create("node", "乙", "blocking", "diffusion", "高优先级阻塞", priority=99)
top5, act5 = reset_run(kg, eng, ["甲"])
check("多锁组合：任一 blocking 命中即阻塞（并集）", act5["乙"] == 0.0, str(act5["乙"]))
eff = reg.effective_for("node", "乙")
check("效果查询：blocked=True 且给出生效锁", eff["blocked"] and len(eff["locks"]) >= 2,
      str(eff))

# ── 6. 边锁：被锁边不传播 ────────────────────────────────
kg2, eng2, reg2, path2 = build()
r6 = reg2.create("edge", edge_key("甲", "关联", "乙"), "blocking", "all", "边锁")
top6, act6 = reset_run(kg2, eng2, ["甲"])
check("边锁：下游拿不到信号", act6["乙"] == 0.0 and act6["丙"] == 0.0, str(act6))
check("边锁：标识解析往返一致",
      parse_edge_key(edge_key("甲", "关联", "乙")) == ("甲", "关联", "乙"))
check("边锁：另一条边仍通（甲→丁）", act6["丁"] > 0, str(act6["丁"]))

# ── 7. 作用范围隔离：action 锁不影响扩散 ──────────────────
kg3, eng3, reg3, path3 = build()
reg3.create("node", "丁", "blocking", "action", "禁止执行")
top7, act7 = reset_run(kg3, eng3, ["甲"])
check("scope=action 的锁不拦扩散", act7["丁"] > 0, str(act7["丁"]))
eng3._refresh_action_queue()
check("scope=action 的锁把节点排除在动作队列外",
      all(nid != "丁" for _act, nid in eng3.action_queue), str(eng3.action_queue))
kg4, eng4, reg4, path4 = build()
# 动作队列入队要求激活 ≥ theta_action(0.5)：先把丁点亮再验证加锁的排除效果
kg4.nodes["丁"].activation = 2.0
eng4.mark_active(["丁"])
eng4._refresh_action_queue()
has_before = any(nid == "丁" for _act, nid in eng4.action_queue)
reg4.create("node", "丁", "non_blocking", "action", "隐藏出队")
eng4._refresh_action_queue()
has_after = any(nid == "丁" for _act, nid in eng4.action_queue)
check("动作队列：加锁前在队、加锁后不在队", has_before and not has_after,
      f"before={has_before} after={has_after}")

# ── 8. 删除锁不删对象；悬空锁可见 ─────────────────────────
kg5, eng5, reg5, path5 = build()
r8 = reg5.create("node", "丙", "blocking", "diffusion", "待删测试")
reg5.delete(r8["lock"]["id"])
check("删除锁不影响目标对象存在", "丙" in kg5.nodes)
check("删除锁后拦截效果消失",
      not reg5.blocks_node("丙", "diffusion") and not reg5.list())
r8b = reg5.create("node", "丙", "blocking", "diffusion", "悬空测试")
kg5.remove_node("丙")
locks_now = reg5.list()
check("目标被删后锁仍保留且标记悬空",
      len(locks_now) == 1 and locks_now[0].get("target_missing") is True, str(locks_now))

# ── 9. 过期：到期自动失效（无需显式操作） ─────────────────
kg6, eng6, reg6, path6 = build()
import time as _t
reg6.create("node", "乙", "blocking", "diffusion", "短命锁",
            expires_at=_t.time() - 1)
top9, act9 = reset_run(kg6, eng6, ["甲"])
check("过期锁立即失效（激活恢复）", act9["乙"] > 0, str(act9["乙"]))

# ── 10. 目标校验 + 审计 ───────────────────────────────────
kg7, eng7, reg7, path7 = build()
bad = reg7.create("node", "不存在的节点", "blocking", "diffusion", "x")
check("目标不存在时拒绝建锁（可准确定位要求）", bad.get("ok") is False, str(bad))
bad2 = reg7.create("node", "乙", "bad_kind", "diffusion", "x")
check("非法锁型被拒绝", bad2.get("ok") is False, str(bad2))
bad3 = reg7.create("node", "乙", "blocking", "bad_scope", "x")
check("非法 scope 被拒绝", bad3.get("ok") is False, str(bad3))
reg7.create("node", "乙", "blocking", "diffusion", "审计测试")
reg7.set_enabled("lock_1", False)
reg7.delete("lock_1")
state = reg7.state()
actions = [h["action"] for h in state["history"]]
check("锁状态变化可追踪（创建/停用/删除都在审计里）",
      "created" in actions and "updated" in actions and "deleted" in actions,
      str(actions))

# ── 11. 持久化：重建注册表后锁与拦截效果恢复 ──────────────
kg8, eng8, reg8, path8 = build()
reg8.create("node", "乙", "blocking", "diffusion", "持久化测试")
reg8b = LockRegistry(path=path8, kg=kg8)
check("重启后锁记录恢复", len(reg8b.list()) == 1)
check("重启后拦截效果恢复", reg8b.blocks_node("乙", "diffusion"))

for p in (path, path2, path3, path4, path5, path6, path7, path8):
    try:
        os.remove(p)
    except OSError:
        pass

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 认知锁测试全过（临时 KG + 临时锁文件，无真实数据写入）")
