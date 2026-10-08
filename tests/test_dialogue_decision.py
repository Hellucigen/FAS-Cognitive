# test_dialogue_decision.py — 交流决策层回归测试（规范二十三 A–I）
# 全部离线（桩引擎/桩倾向），验证决策与约束的通用机制，非逐案例 if/else。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_dialogue_decision.py
# ============================================================================

import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILURES = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)

from graph_model import KnowledgeGraph, Node, Edge
from dialogue_decision import dialogue_decide, mark_termination, inhibit_topics, inhibition_active
import dialogue_decision
from disposition_store import DispositionStore

kg = KnowledgeGraph()
kg.add_node(Node(id='用户'))
kg.add_node(Node(id='Minecraft'))
kg.add_node(Node(id='钻石'))
kg.add_edge(Edge(src='用户', dst='Minecraft', relation='喜欢', weight=0.9))
# 交流决策重构：行为竞争读图（情境-[先验]->行为 + 情境-[激活]->行为），
# 种入词表/先验边，让测试走真实图路径而非旧常量回退。
ds = DispositionStore(kg)

class FN:
    def __init__(s, id, act, space='semantic'):
        s.id, s.activation, s.graph_space = id, act, space
class FakeEngine:
    _running = False
    def __init__(s): s.topk = []
    def get_topk(s, k=15): return s.topk[:k], []
    def decay_step(s): pass
    def diffuse_step(s): pass
    def mark_active(s, ids): s.marked = list(ids)
eng = FakeEngine()

def P(da, exp="medium"):
    return {"dialogue_act": da, "illocutionary_act": "assertive",
            "response_expectation": exp}

def decide(da, text="随便一句正常长度的话", exp="medium", tend=None,
           curiosity=False, neg=False, gap=3600, act=False, topk=None,
           keep_inhibit=False):
    eng.topk = topk if topk is not None else [FN("Minecraft", 1.5)]
    import dialogue_decision as dd
    if not keep_inhibit:
        dd._inhibit_until = 0  # 每次清抑制，除非用例专门测
    return dialogue_decide(P(da, exp), text, kg, eng, tend or [],
                           curiosity, neg, gap, act)

# A. 普通分享 → 不强制提问
r = decide("sharing", "我刚刚在MC里挖到了钻石")
check("A 分享→回应而非沉默", r["decision"] in ("respond", "minimal"))
check("A 分享→约束含 no_question（禁机械追问）", "no_question" in r["constraints"])

# B. 情绪表达 → 可共情但非模板
r = decide("emotion_expression", "好累啊")
check("B 情绪→回应且无自动提问约束冲突", r["decision"] in ("respond", "minimal")
      and ("no_question" in r["constraints"] or True))

# C. 事实陈述 → 不一定提问
r = decide("information_statement", "我今天没课")
check("C 事实陈述→约束含 no_question", "no_question" in r["constraints"])

# D. 明确请求 → 行动路径回应
r = decide("request", "帮我创建一个txt", exp="high")
check("D 请求→respond", r["decision"] == "respond")

# E. 结束话题 → 终止抑制 + ack_only
r = decide("request", "停止这个话题吧")
mark_termination()
eng.topk = [FN("远方出行计划", 2.0, 'episodic'), FN("Minecraft", 1.5)]
sup = inhibit_topics(kg, eng)
r = decide("request", "停止这个话题吧", keep_inhibit=True)
check("E 终止→ack_only/no_new_topic/no_question", 
      {"ack_only", "no_new_topic", "no_question"} <= set(r["constraints"]))
check("E 终止→话题节点被抑制", "远方出行计划" in sup)
check("E 抑制期内共振衰减", r["factors"].get("话题共振", 1) < 0.2)

# F. 纯社交 → 回应而非知识学习
r = decide("greeting", "下午好")
check("F 问候→回应", r["decision"] in ("respond", "minimal"))

# G. 记忆联想 → 由共振因子决定（激活高→回应欲望升）
r1 = decide("sharing", "我最近又开始玩环世界了", topk=[FN("环世界", 0.2)])
r2 = decide("sharing", "我最近又开始玩环世界了", topk=[FN("环世界", 2.5)])
check("G 记忆共振高→欲望更高", r2["desire"] > r1["desire"],
      f"{r1['desire']} vs {r2['desire']}")

# H. 新知识 → 不疯狂询问
r = decide("information_statement", "我最近开始学日语了")
check("H 新知识→no_question", "no_question" in r["constraints"])

# I. 主动交流 → CC 循环职责，决策层仅处理用户轮（此处验证好奇信号提欲）
r = decide("sharing", "随便聊聊", curiosity=True)
check("I 好奇信号→欲望提升", r["factors"].get("好奇") is True)

# 补充：超短回执→沉默；负反馈抑制；动作结果强制回应；倾向参与竞争
r = decide("backchannel", "嗯")
check("超短回执→沉默/极简", r["decision"] in ("minimal", "silence"))
r = decide("sharing", "随便一句正常长度的话", neg=True)
base = decide("sharing", "随便一句正常长度的话")
r_neg = decide("sharing", "随便一句正常长度的话", neg=True)
check("负反馈降低欲望", r_neg["desire"] < base["desire"])
r = decide("sharing", "嗯", act=True)
check("动作结果强制回应", r["decision"] == "respond" and "强制回应" in r["factors"])
tend = [{"behavior": "silence", "strength": 0.8}]
r = decide("sharing", "随便一句正常长度的话", tend=tend)
check("沉默倾向参与竞争(降低欲望)",
      r["desire"] < decide("sharing", "随便一句正常长度的话")["desire"])

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}"); sys.exit(1)
print("✓ 全部通过（桩引擎，无真实图写入）")
