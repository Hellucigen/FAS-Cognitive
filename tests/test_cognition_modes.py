# test_cognition_modes.py — LLM 认知参与模式测试
# 需求评分/模式分级/预算管理/注意力上下文。离线桩，无真实 LLM 调用。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_cognition_modes.py

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cognition_modes import (demand_score, mode_for_demand, BudgetManager,
    attention_context, MODE0_GRAPH_ONLY, MODE1_LANGUAGE, MODE2_INTERPRET, MODE3_REASON)
from graph_model import KnowledgeGraph, Node, Edge

fail=[]
def check(name, cond, detail=''):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ''))
    if not cond: fail.append(name)

kg=KnowledgeGraph()
kg.add_node(Node(id='用户')); kg.add_node(Node(id='咖啡')); kg.add_node(Node(id='焦糖玛奇朵'))
kg.add_edge(Edge(src='焦糖玛奇朵',dst='咖啡',relation='是',weight=1.0))
class E:
    _running=False
    def __init__(s): s.topk=[]
    def get_topk(s,k=15): return s.topk[:k],[]
    def decay_step(s): pass
    def diffuse_step(s): pass
eng=E(); eng.topk=[kg.nodes['咖啡']]

d=demand_score({'nodes':['用户','咖啡']},'我喜欢咖啡',kg,eng)
m=mode_for_demand(d)
check('已知实体→LANGUAGE 保底',m==MODE1_LANGUAGE,m)
d2=demand_score({'nodes':['用户','未知实体XYZ']},'什么是XYZ？',kg,eng)
check('未知实体计数=1',d2['components']['unknown']==1)
m2=mode_for_demand(d2)
check('未知实体→INTERPRET',m2==MODE2_INTERPRET,m2)
d3=demand_score({'nodes':['用户','甲不存在的','乙不存在的']},'甲和乙什么关系？',kg,eng)
check('多未知→REASON',mode_for_demand(d3)==MODE3_REASON,mode_for_demand(d3))
b=BudgetManager(profile='economy')
check('economy 初始可调用',b.can_call(MODE1_LANGUAGE))
for _ in range(4): b.register('language')
check('economy 超限拒绝',not b.can_call(MODE1_LANGUAGE))
b2=BudgetManager(profile='balanced')
b2.register('reason',tokens=600)
check('balanced reason 可登记',b2.can_call(MODE3_REASON))
check('graph_only 永远允许',b2.can_call(MODE0_GRAPH_ONLY))
ctx=attention_context(kg,eng,MODE3_REASON)
check('注意力 active_core',ctx['active_core'] and ctx['active_core'][0]['id']=='咖啡')
check('注意力块齐',isinstance(ctx['self'],list) and isinstance(ctx['emotion'],list) and isinstance(ctx['relevant_semantic'],list))

print()
if fail: print('✗',len(fail),fail); sys.exit(1)
print('✓ 模式测试全过')
