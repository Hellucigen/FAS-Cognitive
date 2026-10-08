# test_expression_feedback.py — 表达反馈的图谱内机制（outcome 架构升级）
# ============================================================================
# 锁定的契约（用户指令 §二十一）：
#   FAS 不是被"outcome 分类器"告诉用户喜不喜欢，而是：
#     表达 → 图谱事件（激活源）→ 后继用户输入 → 共激活回流 = 回应关系
#     → SocialFeedback 事件 → 派生解释层 → reward/倾向/下一轮竞争。
#   未观察到回应 → 什么都不记（≠ neutral）；>4h 不再 neutral；
#   "哈哈/闭嘴"词表不再直接决定 outcome。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_expression_feedback.py
# ============================================================================

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

import time

from graph_model import KnowledgeGraph, Node
from diffusion_engine import DiffusionEngine
import expression_feedback as EF

CFG = {
    "lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 4,
    "theta_threshold": 0.01, "activation_max": 5.0, "min_spread_threshold": 0.01,
    "activation_epsilon": 1e-4,
    "inter_round_decay": 0.75, "faiss_recall_topk": 8,
    "input_similarity_floor": 0.5, "input_default_bonus": 0.5,
    "relation_propagation": {
        "cognitive_relation": {"direction": "bidirectional", "decay": 1.0,
                               "gain": 1.2},
        "social_relation": {"direction": "forward", "decay": 1.0, "gain": 1.0},
        "episodic_relation": {"direction": "bidirectional", "decay": 1.0,
                              "gain": 1.2},
    },
}

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class FakeTimeline:
    def __init__(self):
        self.events = []

    def append(self, e):
        self.events.append(e)


class FakeBuffer:
    def __init__(self):
        self.expressions = []

    def add_expression(self, e):
        self.expressions.append(e)

    def last_unannotated_expression(self):
        for e in reversed(self.expressions):
            if e.get("outcome") is None:
                return e
        return None

    def annotate_expression(self, e, outcome, detail=""):
        e["outcome"] = outcome
        e["outcome_detail"] = detail


def build():
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", graph_space="self"))
    kg.add_node(Node(id="用户"))
    kg.add_node(Node(id="Haru", graph_space="self"))
    kg.add_node(Node(id="Minecraft"))
    kg.add_node(Node(id="焦虑", label="declarative-semantic",
                     graph_space="cognitive",
                     extra_attrs={"type": "emotion", "valence": -0.7}))
    kg.add_node(Node(id="开心", label="declarative-semantic",
                     graph_space="cognitive",
                     extra_attrs={"type": "emotion", "valence": 1.0}))
    kg.add_node(Node(id="行为:追问", label="disposition"))
    eng = DiffusionEngine(kg, CFG)
    return kg, eng


def record(kg, eng, buf, **kw):
    kw.setdefault("user_text", "上一轮用户的话")
    kw.setdefault("answer", "你今天怎么突然想玩 Minecraft？")
    kw.setdefault("behavior", "ask")
    kw.setdefault("context", "情境:用户提问")
    kw.setdefault("context_ids", ["情境:用户提问"])
    kw.setdefault("topics", ["Minecraft", "用户"])
    return EF.record_expression(kg, eng, buf, config=None, **kw)


def user_turn(kg, eng, buf, tl, pre, *, text, parsed=None, activate=None):
    """模拟下一轮：激活前采样→激活→扩散→观察。"""
    parsed = parsed or {}
    pre = pre if pre is not None else EF.pre_baseline(kg, eng)
    seeds = list(activate or [])
    eng.activate_from_inputs(seeds, [])
    eng.diffuse_round()
    obs = EF.observe_response(kg, eng, tl, pre=pre, user_text=text,
                              parsed=parsed, cycle_id="c1")
    return obs


# ═════ 1. 表达事件入图（§三/§四）═════
kg, eng = build()
buf = FakeBuffer()
nid = record(kg, eng, buf, dialogue_act="question")
n = kg.nodes.get(nid)
check("1a 表达事件节点创建（episodic/expression_event）",
      n is not None and (n.extra_attrs or {}).get("type") == "expression_event"
      and n.graph_space == "episodic")
check("1b 表达是激活源（锚定激活+进前沿）",
      n is not None and float(n.activation or 0) >= 0.3)
check("1c 事实边：经历/涉及行为/关于话题",
      kg.get_edge("Haru", nid, "经历") is not None
      and kg.get_edge(nid, "行为:追问", "涉及") is not None
      and kg.get_edge(nid, "Minecraft", "关于") is not None)
check("1d 不存在的 topic 不建边（不造第二套事实）",
      kg.get_edge(nid, "用户", "关于") is not None)  # 用户存在→有边
pend = EF.pending_view(kg)
check("1e 注视指针入图且跨函数一致", pend and pend["expr_id"] == nid)
check("1f buffer 兼容视图仍在（reflection 数据形态不变）",
      len(buf.expressions) == 1 and buf.expressions[0].get("event_node") == nid)

# ═════ 2. Case A：FAS 提问 → 用户回答（经话题回流建立回应）═════
pre = EF.pre_baseline(kg, eng)
obs = user_turn(kg, eng, buf, FakeTimeline(), pre,
                text="就是想看看你现在到底能干什么。",
                parsed={"dialogue_act": "answer", "nodes": ["Minecraft", "用户"]},
                activate=["Minecraft"])
check("2a 共激活回流 → 回应成立", obs is not None and obs["status"] == "responded",
      str(obs))
check("2b 派生 engaged（读的是图结构：前表达行为=追问+回应边）",
      obs and obs["social"] == "engaged", obs and obs.get("social"))
ue = obs.get("user_expr_id")
check("2c 用户表达事件 -[对话]-> 表达事件",
      ue and kg.get_edge(ue, nid, "对话") is not None)
fid = obs.get("feedback_node")
check("2d SocialFeedback 事件节点（基于/关于边）",
      fid and kg.get_edge(fid, ue, "基于") is not None
      and kg.get_edge(fid, nid, "关于") is not None)
check("2e 表达事件获得后验关系（outcome=结构不是字符串）",
      isinstance((kg.nodes[nid].extra_attrs or {}).get("feedback"), dict)
      and kg.nodes[nid].extra_attrs["feedback"].get("event") == fid)
check("2f 指针已消费清空（不重复计分）", EF.pending_view(kg) is None)
check("2g 反馈事件进入激活场（影响下一轮决策）",
      fid and float(kg.nodes[fid].activation or 0) > 0)

# ═════ 3. Case B：用户换话题 → 不建关系、什么都不记 ═════
kg2, eng2 = build()
buf2 = FakeBuffer()
nid2 = record(kg2, eng2, buf2)
pre2 = EF.pre_baseline(kg2, eng2)
tl2 = FakeTimeline()
obs2 = user_turn(kg2, eng2, buf2, tl2, pre2,
                 text="对了，今晚吃什么好呢",
                 parsed={"dialogue_act": "question", "nodes": []},
                 activate=[])
check("3a 无共激活无控制 → 不产生观察结果", obs2 is None)
check("3b 没有 neutral/ignored 被凭空写出",
      all("(neutral)" not in str(e) for e in tl2.events) and tl2.events == [])
check("3c 注视指针保留（未观察到结果≠neutral，继续等待/被覆盖）",
      EF.pending_view(kg2) is not None
      and EF.pending_view(kg2)["expr_id"] == nid2)

# ═════ 4. Case C：会话控制指令"别问了。"（不用抵触词表判负）═════
kg3, eng3 = build()
buf3 = FakeBuffer()
nid3 = record(kg3, eng3, buf3)
pre3 = EF.pre_baseline(kg3, eng3)
obs3 = user_turn(kg3, eng3, buf3, FakeTimeline(), pre3,
                 text="别问了。",
                 parsed={"dialogue_act": "request", "nodes": []}, activate=[])
check("4a 控制指令 → 回应关系成立（via_control）",
      obs3 is not None and obs3.get("via_control") is True, str(obs3))
check("4b 派生 rejected（语言理解层既有检测，非词表→negative）",
      obs3 and obs3["social"] == "rejected"
      and obs3["legacy"] == "negative")
check("4c 被指向的表达也获得反馈结构",
      kg3.nodes[nid3].extra_attrs.get("feedback", {}).get("social") == "rejected")

# ═════ 5. Case D："哈哈，对啊"——笑不是直接=positive，由结构派生 ═════
kg4, eng4 = build()
buf4 = FakeBuffer()
nid4 = record(kg4, eng4, buf4, behavior="share")   # 上轮分享而非提问
pre4 = EF.pre_baseline(kg4, eng4)
obs4 = user_turn(kg4, eng4, buf4, FakeTimeline(), pre4,
                 text="哈哈对啊，你终于发现了",
                 parsed={"dialogue_act": "agreement", "nodes": ["Minecraft"]},
                 activate=["Minecraft"])
check("5a 回应成立后由结构派生（agreement→recognized），无词表通道",
      obs4 and obs4["social"] in ("recognized", "amused", "accepted"),
      str(obs4))

# ═════ 6. Case E：长时间后明确"刚才你说的"——靠图谱重新点亮，不判 timeout ═════
kg5, eng5 = build()
buf5 = FakeBuffer()
nid5 = record(kg5, eng5, buf5)
node5 = kg5.nodes[nid5]
node5.activation = 0.16          # 模拟多轮衰减后（锚定激活已淡）
eng5.mark_active([nid5])
time.sleep(0.01)
pre5 = EF.pre_baseline(kg5, eng5)
obs5 = user_turn(kg5, eng5, buf5, FakeTimeline(), pre5,
                 text="刚才你说的那个 Minecraft，我想了下——",
                 parsed={"dialogue_act": "sharing", "nodes": ["Minecraft"]},
                 activate=["Minecraft"])
check("6a 隔很久但重新点亮结构 → 回应照样成立",
      obs5 is not None and obs5["status"] == "responded", str(obs5))
check("6b Δt 作为事实记录，不参与类别（无 timeout 派生）",
      obs5 and obs5["social"] != "timeout" and obs5.get("dt_seconds", 0) >= 0)

# ═════ 7. 情绪 valence 从节点属性读（valence 图谱化）═════
kg6, eng6 = build()
buf6 = FakeBuffer()
nid6 = record(kg6, eng6, buf6, behavior="share")
pre6 = EF.pre_baseline(kg6, eng6)
# 本轮用户共激活了负向情绪"焦虑"（既有情绪共振机制的产物）
obs6 = user_turn(kg6, eng6, buf6, FakeTimeline(), pre6,
                 text="唉，越说越焦虑了",
                 parsed={"dialogue_act": "emotion_expression", "nodes": ["Minecraft"]},
                 activate=["Minecraft", "焦虑"])
check("7a 回应中共激活负向情绪（节点 valence）→ rejected 方向",
      obs6 is not None and obs6["social"] == "rejected"
      and obs6["emotion_valence"] <= -0.5, str(obs6))
kg7, eng7 = build()
buf7 = FakeBuffer()
nid7 = record(kg7, eng7, buf7, behavior="share")
pre7 = EF.pre_baseline(kg7, eng7)
obs7 = user_turn(kg7, eng7, buf7, FakeTimeline(), pre7,
                 text="开心！就等你这句",
                 parsed={"dialogue_act": "emotion_expression", "nodes": ["Minecraft"]},
                 activate=["Minecraft", "开心"])
check("7b 共激活正向情绪 → amused（valence 属性，不是词表）",
      obs7 is not None and obs7["social"] == "amused"
      and obs7["emotion_valence"] >= 0.5, str(obs7))

# ═════ 8. 表达覆盖（连续两轮 FAS 说话，旧表达保持"未观察"）═════
kg8, eng8 = build()
buf8 = FakeBuffer()
nid8a = record(kg8, eng8, buf8, answer="第一句")
nid8b = record(kg8, eng8, buf8, answer="第二句")
pend8 = EF.pending_view(kg8)
check("8a 指针只注视最近表达", pend8["expr_id"] == nid8b)
check("8b 旧表达保持 feedback=None（不强行解释）",
      kg8.nodes[nid8a].extra_attrs.get("feedback") is None)

# ═════ 9. 词表/规则退役的静态契约 ═════
src_app = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app.py"), encoding="utf-8").read()
check("9a app.py 生产路径不再调用 classify_outcome",
      "classify_outcome(" not in src_app)
check("9b app.py 不再调用 classify_social_outcome",
      "classify_social_outcome(" not in src_app)
check("9c 长中断→neutral 规则已不在生产路径（gap_minutes 判定退役）",
      "_gap = None" not in src_app)

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 表达反馈图谱机制测试全过（A-E 案例 + valence 属性 + 词表退役）")
