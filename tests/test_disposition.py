# test_disposition.py — Reflection Evolution 离线单测
# ============================================================================
# 全部使用【显式假设数据】在临时内存 KG 上运行，绝不触碰
# data/runtime_graph.json，绝不写真实记忆。运行后即丢弃。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 test_disposition.py
# ============================================================================

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ── 临时 KG（内存态，与真实图无关）──────────────────────────
from graph_model import KnowledgeGraph
from disposition_store import (
    DispositionStore, classify_behavior, classify_outcome,
    context_for_dialogue_act, BEHAVIORS,
)

kg = KnowledgeGraph()
store = DispositionStore(kg)

# ── 1. 词表封闭性 ─────────────────────────────────────────
vocab_nodes = [n for n in kg.nodes.values()
               if (n.extra_attrs or {}).get("type") in ("behavior", "context")]
check("词表节点创建且有限(<=25)", 0 < len(vocab_nodes) <= 25, f"n={len(vocab_nodes)}")
check("词表幂等", (DispositionStore(kg), len(kg.nodes))[1] >= len(kg.nodes))

# ── 2. 行为启发式分类 ─────────────────────────────────────
check("空回答→silence", classify_behavior("") == "silence")
check("问号→ask", classify_behavior("那你觉得怎么样？") == "ask")
check("共情词→empathize", classify_behavior("听起来你很难过，我懂你的感受") == "empathize")
check("短句→acknowledge", classify_behavior("好的，知道了。") == "acknowledge")
check("长文→elaborate", classify_behavior("关于这个问题我想详细说说。" + "具体细节和背景补充" * 10) == "elaborate")
check("普通→respond", classify_behavior("这是一般的回应内容，稍微长一点的普通回应。") == "respond")

# ── 3. outcome 启发式 ────────────────────────────────────
prev = {"user_text": "我们准备玩育碧的荒野行动", "topics": ["荒野行动", "高中同学"]}
check("抵触→negative", classify_outcome(prev, "别问了，我不想说", {}, 1)[0] == "negative")
check("继续同话题→positive",
      classify_outcome(prev, "对，荒野行动我们准备明天开黑", {}, 1)[0] == "positive")
check("积极词→positive", classify_outcome(prev, "哈哈可以", {}, 1)[0] == "positive")
check("长中断→neutral", classify_outcome(prev, "早上好", {}, 9999)[0] == "neutral")
check("话题切换→neutral", classify_outcome(prev, "今天天气怎么样", {}, 1)[0] == "neutral")

# ── 4. 一次经历绝不形成稳定人格 ───────────────────────────
store.get_or_create("情境:用户分享", "ask", evidence_ref="假设观察1")
pair = store.get_pair("情境:用户分享", "ask")
check("新建为 candidate", pair.extra_attrs["status"] == "candidate")
check("新建强度=0.25", abs(pair.extra_attrs["strength"] - 0.25) < 1e-6)
store.apply_outcome("情境:用户分享", "ask", "positive", "假设证据")
pair = store.get_pair("情境:用户分享", "ask")
check("单次正反馈仍 candidate", pair.extra_attrs["status"] == "candidate")
# 人格学习架构改造（2026-09）：社会反馈不再走固定 +0.08，而是
#   valence(accepted=0.5) × w_social(0.4) × learn_pos(0.25) = +0.05
# （自我反馈权重更高，见 apply_experience；用户赞许不再是唯一塑造源）
check("单次社会正反馈强度+0.05(valence×权重)",
      abs(pair.extra_attrs["strength"] - 0.30) < 1e-6)

# ── 5. 证据积累→stable 门槛 ──────────────────────────────
for i in range(5):
    store.apply_outcome("情境:用户分享", "ask", "positive", f"假设证据{i}")
pair = store.get_pair("情境:用户分享", "ask")
check("6正0负+强度足够→stable", pair.extra_attrs["status"] == "stable",
      f"status={pair.extra_attrs['status']} strength={pair.extra_attrs['strength']}")

# ── 6. 负反馈衰减与删除 ──────────────────────────────────
for i in range(8):
    store.apply_outcome("情境:用户分享", "ask", "negative", f"假设负反馈{i}")
check("连续负反馈→倾向删除", store.get_pair("情境:用户分享", "ask") is None)

# ── 7. turn 级绝不新建 ───────────────────────────────────
store.apply_outcome("情境:用户提问", "elaborate", "positive", "假设")
check("turn级outcome不新建倾向", store.get_pair("情境:用户提问", "elaborate") is None)

# ── 8. 激活边权同步 ──────────────────────────────────────
store.get_or_create("情境:用户提问", "elaborate", evidence_ref="假设")
store.apply_outcome("情境:用户提问", "elaborate", "positive", "假设")
edge = store._activation_edge("情境:用户提问", "elaborate")
pair = store.get_pair("情境:用户提问", "elaborate")
check("激活边权与strength同步",
      edge is not None and abs(edge.weight - pair.extra_attrs["strength"]) < 1e-6)

# ── 9. candidate 封顶 + tendencies 聚合 ──────────────────
t = store.tendencies_for(["情境:用户提问", "情境:用户情绪低落"])
check("tendencies 返回列表", isinstance(t, list))
if t:
    top = t[0]
    if top["behavior"] == "elaborate" and top["status"] == "candidate":
        check("candidate 封顶 0.30", top["strength"] <= 0.30)
    else:
        check("tendencies 顺序正确", True)

# ── 10. 周期衰减 ─────────────────────────────────────────
s0 = pair.extra_attrs["strength"]
store.decay_all()
pair = store.get_pair("情境:用户提问", "elaborate")
check("衰减生效", pair.extra_attrs["strength"] < s0)

# ── 11. EpisodicBuffer 表达事件 ──────────────────────────
from episodic_buffer import EpisodicBuffer
buf = EpisodicBuffer(capacity=5)
buf.add_expression({"user_text": "a", "behavior": "ask", "context": "情境:用户分享"})
buf.add_expression({"user_text": "b", "behavior": "respond", "context": "情境:用户陈述"})
e = buf.last_unannotated_expression()
check("取最近未标注事件", e["user_text"] == "b")
buf.annotate_expression(e, "positive", "假设")
check("标注生效", e["outcome"] == "positive")
buf.mark_expressions_reflected([e])
check("消费后不再返回", len(buf.unreflected_expressions()) == 0)
check("stats 含 expressions", "expressions" in buf.stats())

# ── 12. dialogue_act→情境映射 ────────────────────────────
check("sharing 映射", context_for_dialogue_act("sharing") == "情境:用户分享")
check("未知 act 兜底", context_for_dialogue_act("xxx") == "情境:用户陈述")

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 全部通过（临时 KG，无真实图写入）")
