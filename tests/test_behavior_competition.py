# test_behavior_competition.py — 图谱驱动行为竞争器 验收（交流决策重构 2026-09）
# ============================================================================
# 核心命题：决策不再来自固定 desire 公式，而是"图谱行为候选激活 → 竞争"。
# 覆盖任务要求的场景：普通分享 / 极短消息 / 用户提问 / 好奇心与正事冲突 /
# 沉默胜出 / 主动表达（共享行为图），外加三条"图真的在读"的证明：
#   P1 改图中先验边 → 决策改变（证明读的是图，不是常量）
#   P2 人格学习（激活边） → 竞争胜者改变
#   P3 激素调制改变表达类激活、不改变 silence、不直接创造行为
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_behavior_competition.py
# ============================================================================

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


import config as C
from graph_model import KnowledgeGraph, Node, Edge
from disposition_store import DispositionStore, BEHAVIOR_PRIOR_SEEDS
from dialogue_decision import dialogue_decide, mark_termination
import dialogue_decision as dd_mod


class FN:
    def __init__(s, id, act, space="semantic"):
        s.id, s.activation, s.graph_space = id, act, space
        s.extra_attrs = {}
        s.label = "declarative-semantic"


class FakeEngine:
    _running = False

    def __init__(s, topk=None):
        s.topk = topk if topk is not None else [FN("Minecraft", 1.5)]
        s.marked = []

    def get_topk(s, k=15):
        return s.topk[:k], []

    def mark_active(s, ids):
        s.marked = list(ids)


def build():
    base = os.path.join(tempfile.gettempdir(), "fas_bcomp")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    kg = KnowledgeGraph()
    for nid in ("用户", "Minecraft", "钻石", "CuriosityDrive"):
        kg.add_node(Node(id=nid))
    ds = DispositionStore(kg, dict(C.DEFAULT_CONFIG))   # 种入词表 + 先验边 + 竞争通路
    eng = FakeEngine()
    return kg, ds, eng, base


def decide(kg, eng, ds, da, text, exp="medium", tend=None, curiosity=False,
           neg=False, gap=3600.0, act=False, exploration=None, hormone=None,
           inhibit=False):
    if not inhibit:
        dd_mod._inhibit_until = 0
    ctx = dd_mod.context_for_dialogue_act(da)
    tendencies = tend if tend is not None else ds.tendencies_for([ctx])
    return dialogue_decide(
        {"dialogue_act": da, "illocutionary_act": "assertive",
         "response_expectation": exp},
        text, kg, eng, tendencies,
        curiosity, neg, gap, act, exploration=exploration, hormone=hormone)


def cand_of(r, name):
    for c in r["candidates"]:
        if c["behavior"] == name:
            return c
    return {}


# ═══ 1. 普通用户分享 → 回应胜出（先验+学习来源可见）═══
def test_normal_sharing():
    print("\n── 1. 普通分享 → respond 胜出 ──")
    kg, ds, eng, base = build()
    r = decide(kg, eng, ds, "sharing", "我最近在玩Minecraft")
    check("回应胜出", r["decision"] in ("respond", "minimal"), r["decision"])
    top = r["candidates"][0]
    check("胜者是 respond 且激活来源含先验分量",
          top["behavior"] == "respond" and top["prior"] > 0, str(top))
    check("silence 是正式候选（不是阈值外的缺省）",
          cand_of(r, "silence") != {}, str([c["behavior"] for c in r["candidates"]]))
    check("探索候选未参与时 explore 不在候选席", cand_of(r, "explore_ask") == {})
    shutil.rmtree(base, ignore_errors=True)


# ═══ 2. 极短消息 → 沉默/极简（silence 作为行为结果）═══
def test_short_message():
    print("\n── 2. 极短消息 → silence 候选可以赢 ──")
    kg, ds, eng, base = build()
    r = decide(kg, eng, ds, "backchannel", "嗯")
    check("极短回执 → minimal 或 silence", r["decision"] in ("minimal", "silence"),
          r["decision"])
    if r["decision"] == "silence":
        check("silence 以候选身份胜出（激活高于表达类）",
              r["winner_behavior"] == "silence", r["winner_behavior"])
    check("表达类候选被极短输入压制（调制留痕）",
          r["factors"].get("投入度") == "极短")
    shutil.rmtree(base, ignore_errors=True)


# ═══ 3. 用户提问 → 回应胜出（先验 0.85）═══
def test_question():
    print("\n── 3. 用户提问 → respond ──")
    kg, ds, eng, base = build()
    r = decide(kg, eng, ds, "question", "Minecraft里村民怎么交易？")
    check("提问 → 回应胜出", r["decision"] == "respond", r["decision"])
    check("respond 激活主要来自先验（0.85）", cand_of(r, "respond")["prior"] >= 0.85)
    shutil.rmtree(base, ignore_errors=True)


# ═══ 4. 好奇心与正事冲突 ═══
def test_curiosity_vs_business():
    print("\n── 4. 好奇 vs 正事（工具结果待汇报）──")
    kg, ds, eng, base = build()
    strong = [{"action": "explore_ask", "score": 0.90, "win_threshold": 0.55,
               "margin": 0.05, "target": "SAO"}]
    r = decide(kg, eng, ds, "sharing", "随便一句正常长度的话",
               act=True, exploration={"candidates": strong})
    check("正事优先：探索即使激活高也让位", r["decision"] == "respond", r["decision"])
    check("探索落选留痕（可解释）",
          "探索落选" in r["factors"] or "强制回应" in r["factors"], str(r["factors"].keys()))
    # 无正事时，同候选凭激活胜出
    r2 = decide(kg, eng, ds, "information_statement", "随便一句正常长度的话",
                exploration={"candidates": strong})
    check("无正事时探索凭激活胜出（同一竞争场）",
          r2["decision"] == "explore_ask" and r2["factors"].get("探索胜出"),
          r2["decision"])
    shutil.rmtree(base, ignore_errors=True)


# ═══ 5. 沉默胜出（学到的沉默倾向）═══
def test_silence_wins():
    print("\n── 5. silence 作为候选胜出 ──")
    kg, ds, eng, base = build()
    tend = [{"behavior": "silence", "strength": 0.8, "status": "stable",
             "activation": 0.0}]
    r = decide(kg, eng, ds, "sharing", "随便一句正常长度的话", tend=tend)
    check("学到的沉默倾向让 silence 赢得竞争", r["winner_behavior"] == "silence"
          and r["decision"] == "silence", str(r["winner_behavior"]))
    check("表达 urge 被沉默倾向抑制（兼容字段下降）",
          r["desire"] < 0.55, str(r["desire"]))
    shutil.rmtree(base, ignore_errors=True)


# ═══ 6. 主动表达共享同一行为图 ═══
def test_proactive_shared_graph():
    print("\n── 6. 主动侧共享行为图（情境:主动发起）──")
    kg, ds, eng, base = build()
    before = ds.tendencies_for(["情境:主动发起"])
    # 自主/主动侧的分享经历（经 apply_experience 写激活边）
    for _ in range(6):
        ds.apply_experience("share", "情境:主动发起", self_outcome="goal_success",
                            allow_create=True)
    after = ds.tendencies_for(["情境:主动发起"])
    check("主动侧经历写入同一行为图",
          any(x["behavior"] == "share" for x in after), str(after))
    check("学习后 share 倾向强于学习前",
          (next((x["strength"] for x in after if x["behavior"] == "share"), 0)
           > next((x["strength"] for x in before if x["behavior"] == "share"), 0)))
    shutil.rmtree(base, ignore_errors=True)


# ═══ P1. 改图中先验边 → 决策改变（"读的是图"的铁证）═══
def test_prior_edge_is_read():
    print("\n── P1 图谱先验边真的被读取 ──")
    kg, ds, eng, base = build()
    r0 = decide(kg, eng, ds, "question", "Minecraft里村民怎么交易？")
    check("基线：提问 → respond 胜出", r0["winner_behavior"] == "respond",
          r0["winner_behavior"])
    # 经验修正先验（§四允许）：把 情境:用户提问→行为:回应 的先验削弱
    with kg._lock:
        e = kg.get_edge("情境:用户提问", "行为:回应", "先验")
        e.weight = 0.05
    r1 = decide(kg, eng, ds, "question", "Minecraft里村民怎么交易？")
    check("削先验后胜者改变（读图而非读常量）",
          r1["winner_behavior"] != "respond", r1["winner_behavior"])
    check("respond 候选激活随先验下降",
          cand_of(r1, "respond")["activation"] < cand_of(r0, "respond")["activation"])
    shutil.rmtree(base, ignore_errors=True)


# ═══ P2. 人格学习 → 竞争胜者改变 ═══
def test_learning_flips_winner():
    print("\n── P2 学习闭环改变未来竞争 ──")
    kg, ds, eng, base = build()
    r0 = decide(kg, eng, ds, "sharing", "我今天想到了一个东西")
    check("学习前 respond 领先", r0["winner_behavior"] == "respond",
          r0["winner_behavior"])
    for _ in range(6):   # 她"追问"屡屡带来自我收获（discovery）
        ds.apply_experience("ask", "情境:用户分享", self_outcome="discovery",
                            allow_create=True)
    r1 = decide(kg, eng, ds, "sharing", "我今天想到了一个东西")
    check("学习后 ask 从先验劣势反超 respond（胜者改变）",
          r1["winner_behavior"] == "ask", r1["winner_behavior"])
    check("ask 候选激活上升且来源为学习通道",
          cand_of(r1, "ask")["learned"] > cand_of(r0, "ask")["learned"])
    shutil.rmtree(base, ignore_errors=True)


# ═══ P3. 激素是调制器不是第二决策器 ═══
def test_hormone_modulation_only():
    print("\n── P3 激素调制增益，不创造行为 ──")
    kg, ds, eng, base = build()
    # 先有一段经历（激素调制的是"经验此刻的表达强度"，无经历则无可调制）
    for _ in range(3):
        ds.apply_experience("respond", "情境:用户分享", self_outcome="goal_success",
                            allow_create=True)
    hi = decide(kg, eng, ds, "sharing", "我今天想到了一个东西",
                hormone={"factor": 1.8})
    lo = decide(kg, eng, ds, "sharing", "我今天想到了一个东西",
                hormone={"factor": 0.4})
    check("高奖赏态表达激活更高（经历分量被放大/缩小）",
          cand_of(hi, "respond")["activation"] > cand_of(lo, "respond")["activation"],
          f"{cand_of(lo, 'respond')['activation']} → {cand_of(hi, 'respond')['activation']}")
    check("silence 不受激素调制（激素≠人格）",
          abs(cand_of(hi, "silence")["activation"]
              - cand_of(lo, "silence")["activation"]) < 1e-9)
    check("胜者行为不因激素翻转（只调增益）",
          hi["winner_behavior"] == lo["winner_behavior"])
    shutil.rmtree(base, ignore_errors=True)


# ═══ 探索经图通路（CuriosityDrive -驱动-> 行为:探索）═══
def test_curiosity_graph_path():
    print("\n── 探索候选混入行为:探索 实时激活 ──")
    kg, ds, eng, base = build()
    check("竞争通路边存在：CuriosityDrive -[驱动]-> 行为:探索",
          kg.get_edge("CuriosityDrive", "行为:探索", "驱动") is not None)
    strong = [{"action": "explore_ask", "score": 0.70, "win_threshold": 0.55,
               "margin": 0.05, "target": "SAO"}]
    kg.nodes["行为:探索"].activation = 2.5    # 模拟扩散点亮
    r = decide(kg, eng, ds, "information_statement", "随便一句正常长度的话",
               exploration={"candidates": strong})
    live_term = cand_of(r, "explore_ask").get("live", 0)
    check("探索候选激活含 行为:探索 实时分量", live_term > 0.2, str(live_term))
    shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    test_normal_sharing()
    test_short_message()
    test_question()
    test_curiosity_vs_business()
    test_silence_wins()
    test_proactive_shared_graph()
    test_prior_edge_is_read()
    test_learning_flips_winner()
    test_hormone_modulation_only()
    test_curiosity_graph_path()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAIL: {len(FAILURES)} 项未通过")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS: 行为竞争器全部通过")
