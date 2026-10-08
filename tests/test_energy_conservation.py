# test_energy_conservation.py — 扩散长跑资源有界性（回归护栏）
# 起因：空闲时图谱整片饱和（实测 733 节点全亮、374 个钉在 cap 5.0，每 tick 扩散统计
# 完全相同 —— 那不是认知，是固定点）。根因是**发射语义是"复制"**：activation 在图上
# 无成本复制，任何入度够大的节点每步入流都远大于 λ=5% 的衰减，唯一平衡点就是撞上限。
# 修复 = `emission_transfer`（发射即转移：注意资源不无成本复制）+ **激活来源登记**
# （本轮注入点发射免扣，保证回合内排序不被压平）。语义表述见 docs/activation_model.md。
#
# 本测试锁住三条不变量：
#   E1 空闲长跑总能量有界、不增长、不出现满格节点（无外部刺激时场要能排空）
#   E2 回合内输入簇仍排在前列（守恒不能把排序压平）
#   E3 锚点只在回合内有效，回合边界清理（否则图里会留下永久能量源）
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_energy_conservation.py

import copy
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from graph_model import KnowledgeGraph, Node, Edge
from diffusion_engine import DiffusionEngine
import config as cfgmod

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def build_config(**overrides):
    cfg = copy.deepcopy(cfgmod.DEFAULT_CONFIG)
    cfg.update(overrides)
    return cfg


def chain_engine(extra_edges=None, **cfg_over):
    """一条小链 + 一个密集小簇：既能验证传播，也能验证不饱和。"""
    kg = KnowledgeGraph()
    for nid in ("甲", "乙", "丙", "丁"):
        kg.add_node(Node(id=nid, label="declarative-semantic"))
    kg.add_edge(Edge(src="甲", dst="乙", relation="关联", weight=0.8,
                     relation_category="semantic_relation"))
    kg.add_edge(Edge(src="乙", dst="丙", relation="关联", weight=0.8,
                     relation_category="semantic_relation"))
    kg.add_edge(Edge(src="乙", dst="甲", relation="关联", weight=0.8,
                     relation_category="semantic_relation"))   # 互泵对
    kg.add_edge(Edge(src="丙", dst="甲", relation="关联", weight=0.8,
                     relation_category="semantic_relation"))
    for e in (extra_edges or []):
        kg.add_edge(e)
    eng = DiffusionEngine(kg, build_config(**cfg_over))
    eng.name_to_node = dict(kg.nodes)
    return kg, eng


def total_energy(kg):
    return sum(n.activation for n in kg.nodes.values() if n.activation > 0)


def at_cap(kg, cap=4.99):
    return sum(1 for n in kg.nodes.values() if n.activation >= cap)


# ── E1：空闲长跑不饱和、能量不增长 ────────────────────────
kg, eng = chain_engine()
kg.nodes["甲"].activation = 3.0
eng.mark_active(["甲"])
random.seed(3)
trace = []
for t in range(1, 401):
    eng.decay_step()
    eng.diffuse_step()
    if t == 60:                       # 中途再点一次火（模拟记忆再点火）
        kg.nodes["丁"].activation = 2.0
        eng.mark_active(["丁"])
    if t % 100 == 0:
        trace.append(round(total_energy(kg), 3))
check("E1a 空闲长跑总能量单调下降（无外部刺激时场会排空）",
      trace[0] > trace[-1] and all(trace[i] >= trace[i + 1] for i in range(len(trace) - 1)),
      str(trace))
check("E1b 空闲长跑不出现满格节点", at_cap(kg) == 0, f"满格={at_cap(kg)}")
check("E1c 排空后残余能量接近 0", total_energy(kg) < 0.5, str(round(total_energy(kg), 3)))

# ── E1d：旧"复制"语义确实会饱和（对照，证明护栏测的是真问题）──
kg2, eng2 = chain_engine(emission_transfer=0.0)
kg2.nodes["甲"].activation = 3.0
eng2.mark_active(["甲"])
for t in range(400):
    eng2.decay_step()
    eng2.diffuse_step()
check("E1d 对照：emission_transfer=0.0（复制语义）会饱和到上限",
      at_cap(kg2) >= 2 and total_energy(kg2) > total_energy(kg) * 5,
      f"满格={at_cap(kg2)} 能量={round(total_energy(kg2),2)}")

# ── E1e：密集互泵簇（最容易饱和的结构）在修复后也不饱和 ──
kg3 = KnowledgeGraph()
ids = [f"节点{i}" for i in range(8)]
for nid in ids:
    kg3.add_node(Node(id=nid))
for a in ids:                        # 全连接：每对双向互泵
    for b in ids:
        if a != b:
            kg3.add_edge(Edge(src=a, dst=b, relation="关联", weight=0.8,
                              relation_category="semantic_relation"))
eng3 = DiffusionEngine(kg3, build_config())
eng3.name_to_node = dict(kg3.nodes)
kg3.nodes[ids[0]].activation = 4.0
eng3.mark_active([ids[0]])
for t in range(300):
    eng3.decay_step()
    eng3.diffuse_step()
check("E1e 全连接互泵簇不再全簇饱和",
      at_cap(kg3) == 0, f"满格={at_cap(kg3)} 能量={round(total_energy(kg3),2)}")

# ── E1f：发射确实从源节点扣除（守恒的机制本身） ──────────
kg4, eng4 = chain_engine()
kg4.nodes["甲"].activation = 2.0
eng4.mark_active(["甲"])
before = kg4.nodes["甲"].activation
eng4.diffuse_step()
check("E1f 普通节点发射后自身被扣除", kg4.nodes["甲"].activation < before,
      f"{before} → {kg4.nodes['甲'].activation}")

# ── E2：回合内排序不被压平（锚点 = 外部刺激源） ──────────
kg5, eng5 = chain_engine()
sim = {"甲": 0.9, "乙": 0.8}
eng5.activate_from_inputs(["甲", "乙"], [], similarity_map=sim)
eng5.diffuse_round()
top, _ = eng5.get_topk(k=4)
ids_top = [n.id for n in top]
check("E2a 输入节点仍占据排名前列（刺激源免于转移）",
      ids_top[0] == "甲" and kg5.nodes["甲"].activation > 1.0,
      f"top={ids_top} 甲={round(kg5.nodes['甲'].activation,2)}")
check("E2b 受刺激的下游也被点亮（能量确实流过去了）",
      kg5.nodes["丙"].activation > 0, str(round(kg5.nodes["丙"].activation, 3)))
check("E2c 回合内同样不出现满格节点", at_cap(kg5) == 0, f"满格={at_cap(kg5)}")

# ── E3：锚点只在回合内有效 ───────────────────────────────
kg6, eng6 = chain_engine()
eng6.activate_from_inputs(["甲"], [])
check("E3a 输入后被登记为激活来源", "甲" in eng6._sources)
n = eng6.clear_anchors()
check("E3b 清锚点返回数量且集合为空",
      n == 1 and eng6._sources == {}, f"n={n} sources={eng6._sources}")
kg6.nodes["甲"].activation = 3.0
eng6.mark_active(["甲"])
eng6.diffuse_step()
eng6.diffuse_step()
check("E3c 清锚点后该节点也会被转移消耗（不留永久能量源）",
      kg6.nodes["甲"].activation < 3.0, str(round(kg6.nodes["甲"].activation, 3)))

# 回合边界清理已接线在 app.py（/api/nlp 结束处）——离线断言其存在性
app_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "app.py"), encoding="utf-8").read()
check("E3d 回合边界确实调用了 clear_anchors（接线存在）",
      "engine.clear_anchors()" in app_src)

# ── E4：配置项默认值与文档一致性 ─────────────────────────
check("E4a emission_transfer 默认为 1.0（守恒）",
      abs(float(cfgmod.DEFAULT_CONFIG.get("emission_transfer", 0)) - 1.0) < 1e-9,
      str(cfgmod.DEFAULT_CONFIG.get("emission_transfer")))

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 扩散能量守恒回归测试全过（空闲不再饱和；回合内排序保持）")
