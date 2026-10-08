# test_personality.py — 基线人格层测试（先验播种/心情瞬态/零机制外显）
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from graph_model import KnowledgeGraph, Node
from personality_baseline import PersonalityBaseline
from disposition_store import DispositionStore

fail=[]
def check(n,c,d=""):
    print(f"[{'PASS' if c else 'FAIL'}] {n}"+(f" | {d}" if d and not c else ""))
    if not c: fail.append(n)

kg=KnowledgeGraph(); kg.add_node(Node(id="Self", graph_space="self"))
ds=DispositionStore(kg)
pb=PersonalityBaseline(kg, ds)
pb.bootstrap()

# 先验播种：走现有 disposition 骨架
p=ds.get_pair("情境:熟悉关系","respond")
check("先验播种进 disposition 骨架", p is not None and p.extra_attrs["strength"]==0.45)
check("先验带证据引用", "[baseline]" in str(p.extra_attrs.get("evidence",[])))
check("激活边同步", ds._activation_edge("情境:熟悉关系","respond") is not None)
# 幂等：不覆盖已由经历形成的更高强度
p.extra_attrs["strength"]=0.8
pb.bootstrap()
check("不覆盖经历形成的更高强度", ds.get_pair("情境:熟悉关系","respond").extra_attrs["strength"]==0.8)

# 心情瞬态
pb._mood_valence=0.0; pb._mood_ts=__import__('time').time()
pb.mood_event("positive")
m=pb.current_mood()
check("正反馈→心情偏暖", m["valence"]>0.05, str(m))
pb.mood_event("negative"); pb.mood_event("negative")
m=pb.current_mood()
check("连续负反馈→心情低落", m["valence"]<-0.05, str(m))
import time as _t
pb._mood_ts=_t.time()-72*3600  # 72h 衰减→回中性
m=pb.current_mood()
check("长时间衰减回中性", abs(m["valence"])<0.05, str(m))
check("心情上下文渲染", isinstance(pb.mood_context(),str))

print()
if fail: print("✗",len(fail),fail); sys.exit(1)
print("✓ 基线人格测试全过")
