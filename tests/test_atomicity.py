# test_atomicity.py — 原子化审计/迁移/CI 二次原子化 离线测试
# 临时内存 KG + 显式假设数据，无真实图写入。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 test_atomicity.py
# ============================================================================

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys
FAILURES = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)

from atomicity_analyzer import analyze_node

# ── 分析器判定 ────────────────────────────────────────────
r = analyze_node({'id': '事件: 马上要开学了，好不开心', 'label': 'declarative-episodic', 'graph_space': 'episodic'})
check("句子型(事件:前缀) → merge_to_atomic", r['recommended_action'] == 'merge_to_atomic'
      and r['atomicity_score'] == 0.25)
check("句子型拆出情绪成分", '好不开心' in ''.join(r['suspected_components']) or len(r['suspected_components']) >= 2)

r = analyze_node({'id': '焦糖玛奇朵', 'label': 'declarative-semantic', 'graph_space': 'semantic'})
check("专有实体(焦糖玛奇朵) → keep", r['recommended_action'] == 'keep' and r['atomicity_score'] >= 0.9)

r = analyze_node({'id': '环世界', 'label': 'declarative-semantic', 'graph_space': 'semantic'})
check("游戏名(环世界) → keep", r['recommended_action'] == 'keep')

r = analyze_node({'id': '旅游—地点—远方市', 'label': 'declarative-episodic', 'graph_space': 'episodic'})
check("命名式事件 → keep(结构已原子)", r['recommended_action'] == 'keep'
      and r['suggested_canon'] == 'event_instance')

r = analyze_node({'id': '收获日2游玩结束', 'label': 'declarative-episodic', 'graph_space': 'episodic'})
check("事件+结果混合 → keep_with_log", r['recommended_action'] == 'keep_with_log'
      and '结束' in r['suspected_components'])

r = analyze_node({'id': 'LLM回答', 'label': 'procedural'})
check("过程节点 → keep(action)", r['recommended_action'] == 'keep' and r['suggested_canon'] == 'action')

r = analyze_node({'id': '出发日群聊无动静', 'label': 'declarative-episodic', 'graph_space': 'episodic'})
check("事件+结果(无动静) → keep_with_log", r['recommended_action'] == 'keep_with_log')

r = analyze_node({'id': 'CI_123', 'label': 'intention', 'graph_space': 'self'})
check("CI 记录节点 → keep(intention)", r['suggested_canon'] == 'intention_instance')

# ── 迁移 repoint 逻辑（临时 KG）────────────────────────────
import json
from graph_model import KnowledgeGraph, Node, Edge
kg = KnowledgeGraph()
kg.add_node(Node(id='用户'))
kg.add_node(Node(id='开学'))
kg.add_node(Node(id='不开心'))
kg.add_node(Node(id='开心'))
kg.add_node(Node(id='事件: 马上要开学了，好不开心'))
kg.add_edge(Edge(src='事件: 马上要开学了，好不开心', dst='开心', relation='引发', weight=0.7))
kg.add_edge(Edge(src='用户', dst='事件: 马上要开学了，好不开心', relation='参与', weight=1.0))

# 模拟 repoint（与迁移脚本一致的核心逻辑）
old = '事件: 马上要开学了，好不开心'
moved = [e for e in kg.edges if e.src == old or e.dst == old]
kg.edges = [e for e in kg.edges if e.src != old and e.dst != old]
del kg.nodes[old]
check("句节点删除", old not in kg.nodes)
check("其边一并移除(信息由原子等价物覆盖)", len(moved) == 2 and
      not any(e.src == old or e.dst == old for e in kg.edges))

# repoint 到原子节点的场景（按键句 → 按下W）
kg.add_node(Node(id='按下W'))
kg.add_node(Node(id='沮丧'))
kg.add_node(Node(id='事件: 程序失败: 按下W'))
kg.add_edge(Edge(src='事件: 程序失败: 按下W', dst='沮丧', relation='引发', weight=0.7))
new = '按下W'
for e in list(kg.edges):
    if e.src == '事件: 程序失败: 按下W':
        if not kg.get_edge(new, e.dst, e.relation):
            kg.add_edge(Edge(src=new, dst=e.dst, relation=e.relation, weight=e.weight))
kg.edges = [e for e in kg.edges if e.src != '事件: 程序失败: 按下W' and e.dst != '事件: 程序失败: 按下W']
del kg.nodes['事件: 程序失败: 按下W']
check("情绪因果边保留在原子节点上", kg.get_edge('按下W', '沮丧', '引发') is not None)

# ── CI 二次原子化（goal/target 用边表达）──────────────────
from continuous_cognition import ContinuousCognition
from graph_model import KnowledgeGraph as GK
kg2 = GK()
for x in ('用户', 'Self', '开学', '不开心'):
    kg2.add_node(Node(id=x))
kg2.nodes['不开心'].extra_attrs = {'type': 'emotion'}
kg2.nodes['开学'].graph_space = 'episodic'
from continuous_cognition import ContinuousCognition
import logging; logging.disable(logging.INFO)
cc = ContinuousCognition(kg2, None, None, None, {"continuous_cognition": {}})
ci = cc.get_or_create_ci_for_test = None
# 直接构造一个 CI 节点走 promote_goal
kg2.add_node(Node(id='CI_T', weight=0.5, label='intention', graph_space='self',
                  extra_attrs={'type': 'communication_intention', 'status': 'forming',
                               'basis': ['开学'], 'activation': 0.6, 'score': 0.6}))
kg2.add_edge(Edge(src='Self', dst='CI_T', relation='意图', weight=0.5))
cc._promote_goal(kg2.nodes['CI_T'])
ea = kg2.nodes['CI_T'].extra_attrs
check("goal 定型(事件基础→follow_up)", ea.get('reply_goal') == 'follow_up'
      and ea.get('status') == 'ready')
check("CI-目标→独立目标节点(边表达非blob)",
      kg2.get_edge('CI_T', '目标:延续话题', '目标') is not None
      and '目标:延续话题' in kg2.nodes)
check("CI-指向→用户(边表达)", kg2.get_edge('CI_T', '用户', '指向') is not None)
check("目标节点封闭词表", [n for n in kg2.nodes if n.startswith('目标:')] and
      len([n for n in kg2.nodes if n.startswith('目标:')]) <= 4)

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}"); sys.exit(1)
print("✓ 全部通过（临时 KG，无真实图写入）")
