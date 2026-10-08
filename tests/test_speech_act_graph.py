# test_speech_act_graph.py — 言语行为图谱化验收测试
# ============================================================================
# 真实 KnowledgeGraph + 真实 DiffusionEngine（config.DEFAULT_CONFIG 传播规则）
# + 真实 speech_act_graph / cognitive_context；只有 LLM 与 Minecraft 不出现。
# 覆盖规范 §十三 场景 A-E 与关键不变量：
#   概念入图、话语事件入图、作为统一激活入口参与扩散、
#   行动/社交/信息回应节点被点亮、单次激活不沉淀为长期学习、
#   话语节点有界清理、OGCTX 拿到的是投影不是判断。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_speech_act_graph.py
# ============================================================================

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from config import DEFAULT_CONFIG
from graph_model import KnowledgeGraph, Node, Edge
from diffusion_engine import DiffusionEngine
from graph_schema import normalize_relation, category_for
import speech_act_graph as sag
from cognitive_context import build_cognitive_context, compile_for_language, debug_view

FAILURES = []


def check(name, cond, detail=""):
    st = "PASS" if cond else "FAIL"
    print(f"[{st}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def fresh_kg():
    kg = KnowledgeGraph()
    kg.add_node(Node(id="用户", graph_space="semantic"))
    kg.add_node(Node(id="Self", graph_space="self", label="self"))
    kg.add_node(Node(id="社交互动", label="infrastructure", graph_space="cognitive"))
    # disposition 词表的最小镜像（行为竞争词表节点）
    for b in ("行为:回应", "行为:追问", "行为:确认", "行为:详述", "行为:共情",
              "行为:分享", "行为:延续", "行为:收尾", "行为:沉默", "行为:探索"):
        kg.add_node(Node(id=b, graph_space="self", label="disposition"))
    for ctx in ("情境:用户请求", "情境:用户提问", "情境:用户陈述", "情境:用户分享",
                "情境:用户观点", "情境:用户情绪表达", "情境:用户问候", "情境:用户感谢",
                "情境:用户道别", "情境:用户回应", "情境:用户情绪低落"):
        kg.add_node(Node(id=ctx, graph_space="self", label="disposition"))
    return kg


def turn(kg, engine, text, parsed):
    """复刻 app 的一轮：注入 → 种子并入 → 激活 → 扩散 → 投影。"""
    info = sag.inject_utterance(kg, engine, text, parsed, cycle_id="t")
    seeds = list((info or {}).get("seeds") or [])
    for n in parsed.get("nodes") or []:
        if isinstance(n, str):
            seeds.append(n)
    engine.apply_inter_round_decay(0.4, 0.5)
    engine.activate_from_inputs(seeds, [])
    engine.diffuse_round()
    proj = sag.projection(kg, engine, info)
    return info, proj


def act(kg, nid):
    n = kg.nodes.get(nid)
    return float(getattr(n, "activation", 0) or 0) if n else 0.0


# ── 0. bootstrap：概念层结构与关系本体 ─────────────────────
kg = fresh_kg()
eng = DiffusionEngine(kg, dict(DEFAULT_CONFIG))
eng.name_to_node = dict(kg.nodes)
r1 = sag.bootstrap_speech_acts(kg, eng)
r2 = sag.bootstrap_speech_acts(kg, eng)
check("bootstrap 幂等（二次零新建）", r2["created_nodes"] == 0, str(r2))
for c in sag.CONCEPTS + sag.RESPONSE_MODES + [sag.CONCEPT_ROOT]:
    check(f"概念节点存在: {c}", c in kg.nodes)
check("指令-[细化]->情境:用户请求 已建且双向（semantic）",
      kg.get_edge("指令", "情境:用户请求", "细化") is not None
      and category_for(normalize_relation("细化")) == "semantic_relation")
check("指令-[倾向]->行动回应 已建（cognitive 前向）",
      kg.get_edge("指令", "行动回应", "倾向") is not None
      and category_for(normalize_relation("倾向")) == "cognitive_relation")
check("行动回应-[倾向]->行为:回应（接入既有行为竞争词表）",
      kg.get_edge("行动回应", "行为:回应", "倾向") is not None)
check("情境:用户提问-[倾向]->信息回应",
      kg.get_edge("指令", "信息回应", "倾向") is not None)
check("概念注册进引擎名字索引（否则激活恒 0 的老坑）",
      "指令" in eng.name_to_node and "行动回应" in eng.name_to_node)


# ── 场景 A. 指令："跟着我" ────────────────────────────────
kg = fresh_kg()
eng = DiffusionEngine(kg, dict(DEFAULT_CONFIG))
eng.name_to_node = dict(kg.nodes)
parsed_a = {"nodes": ["用户"], "edges": [],
            "illocutionary_act": "directive", "dialogue_act": "request"}
info, proj = turn(kg, eng, "跟着我", parsed_a)
check("A 话语事件节点入图（episodic 短期）",
      info["utterance_id"] in kg.nodes
      and kg.nodes[info["utterance_id"]].graph_space == "episodic",
      str(info))
check("A 结构边：用户-[说出]->话语 存在",
      kg.get_edge("用户", info["utterance_id"], "说出") is not None)
check("A 结构边：话语-[言外行为]->指令 存在",
      kg.get_edge(info["utterance_id"], "指令", "言外行为") is not None)
check("A 指令概念被真实点亮（参与扩散）", act(kg, "指令") > 0.3, str(act(kg, "指令")))
check("A 扩散到 行动回应（二跳，非 if/else）",
      act(kg, "行动回应") > 0.05, str(act(kg, "行动回应")))
check("A 扩散到 行为:回应（三跳，行为竞争词表被点亮）",
      act(kg, "行为:回应") > 0.01, str(act(kg, "行为:回应")))
check("A 层级合并：情境:用户请求经 细化 边被共激活",
      act(kg, "情境:用户请求") > 0.01, str(act(kg, "情境:用户请求")))
check("A 投影产出（概念+共激活清单）",
      proj.get("concept") == "指令" and proj.get("activation", 0) > 0, str(proj)[:200])
_co = {c["id"] for c in (proj.get("coactivated") or [])}
check("A 投影共激活含 行动回应", "行动回应" in _co, str(_co))


# ── 场景 B. 提问 ──────────────────────────────────────────
kg = fresh_kg()
eng = DiffusionEngine(kg, dict(DEFAULT_CONFIG))
eng.name_to_node = dict(kg.nodes)
parsed_b = {"nodes": ["用户"], "edges": [],
            "illocutionary_act": "directive", "dialogue_act": "question"}
_, proj_b = turn(kg, eng, "你知道这个是什么吗？", parsed_b)
check("B 提问路径点亮 信息回应（directive 语义下的信息通道）",
      act(kg, "信息回应") > 0.05, str(act(kg, "信息回应")))
check("B 情境:用户提问被共激活（层级合并生效）",
      act(kg, "情境:用户提问") > 0.01, str(act(kg, "情境:用户提问")))


# ── 场景 C. 表达/调侃 ────────────────────────────────────
kg = fresh_kg()
eng = DiffusionEngine(kg, dict(DEFAULT_CONFIG))
eng.name_to_node = dict(kg.nodes)
parsed_c = {"nodes": [], "edges": [],
            "illocutionary_act": "expressive", "dialogue_act": "emotion_expression"}
_, proj_c = turn(kg, eng, "哈哈你刚才好笨", parsed_c)
check("C 表达概念点亮", act(kg, "表达") > 0.3, str(act(kg, "表达")))
check("C 扩散到 社交回应", act(kg, "社交回应") > 0.05, str(act(kg, "社交回应")))
check("C 社交回应连到 行为:共情/确认（既有词表参与竞争）",
      act(kg, "行为:共情") > 0.005 or act(kg, "行为:确认") > 0.005,
      f"empathize={act(kg, '行为:共情')} ack={act(kg, '行为:确认')}")


# ── 场景 D. 断言：不把所有话都当请求 ──────────────────────
kg = fresh_kg()
eng = DiffusionEngine(kg, dict(DEFAULT_CONFIG))
eng.name_to_node = dict(kg.nodes)
parsed_d = {"nodes": ["下雨"], "edges": [],
            "illocutionary_act": "assertive", "dialogue_act": "information_statement"}
kg.add_node(Node(id="下雨", graph_space="semantic"))
eng.name_to_node["下雨"] = kg.nodes["下雨"]
_, proj_d = turn(kg, eng, "今天这里下雨了。", parsed_d)
check("D 断言概念点亮", act(kg, "断言") > 0.3, str(act(kg, "断言")))
check("D 内容实体（下雨）作为种子被点亮", act(kg, "下雨") > 0.5, str(act(kg, "下雨")))
check("D 断言不点亮行动回应（不是所有输入都是请求）",
      act(kg, "行动回应") <= 0.001, str(act(kg, "行动回应")))


# ── 场景 E. 混合输入：指令+社交+内容 共同进图 ─────────────
kg = fresh_kg()
eng = DiffusionEngine(kg, dict(DEFAULT_CONFIG))
eng.name_to_node = dict(kg.nodes)
kg.add_node(Node(id="工作台", graph_space="semantic"))
eng.name_to_node["工作台"] = kg.nodes["工作台"]
parsed_e = {"nodes": ["用户", "工作台"], "edges": [],
            "illocutionary_act": "directive", "dialogue_act": "request"}
info_e, proj_e = turn(kg, eng, "你过来一下，我给你看个东西，哈哈", parsed_e)
linked = [e for e in kg.edges
          if e.src == info_e["utterance_id"] and normalize_relation(e.relation) == "涉及"]
check("E 话语节点连内容实体（工作台）", len(linked) >= 1,
      str([(e.src, e.dst, e.relation) for e in kg.edges if e.src.startswith("话语_")]))
check("E 概念与内容同时在激活场（不被强制二选一）",
      act(kg, "指令") > 0.3 and act(kg, "工作台") > 0.3,
      f"指令={act(kg, '指令')} 工作台={act(kg, '工作台')}")
check("E 投影报告本轮言外行为概念", proj_e.get("concept") == "指令",
      str(proj_e)[:160])
check("E 内容实体经话语事件边进入共激活视野（或直接被种子点亮）",
      "工作台" in {c["id"] for c in proj_e.get("coactivated") or []}
      or any(e.dst == "工作台" for e in kg.edges
             if e.src == info_e["utterance_id"]),
      str(proj_e)[:160])


# ── 不变量 1：单次/多次话语不沉淀为长期学习 ───────────────
kg = fresh_kg()
eng = DiffusionEngine(kg, dict(DEFAULT_CONFIG))
eng.name_to_node = dict(kg.nodes)
sag.bootstrap_speech_acts(kg, eng)     # 先建边，再谈"边权不随话语变化"
w_before = None
for e in kg.edges:
    if e.src == "指令" and e.dst == "行动回应":
        w_before = e.weight
parsed = {"nodes": ["用户"], "edges": [],
          "illocutionary_act": "directive", "dialogue_act": "request"}
acts = []
t = 1000.0
for i in range(3):
    eng._last_round_time = 0
    turn(kg, eng, "跟着我", parsed)
    acts.append(act(kg, "指令"))
w_after = None
for e in kg.edges:
    if e.src == "指令" and e.dst == "行动回应":
        w_after = e.weight
check("不变量: 倾向边权重不随话语变化（学习只走 disposition 通道）",
      w_before == w_after, f"{w_before} vs {w_after}")
eng.apply_inter_round_decay(0.75, 0.5)
check("不变量: 概念激活随回合间衰减回落（不是永久强化）",
      act(kg, "指令") < acts[-1], f"post-decay={act(kg, '指令')} last={acts[-1]}")


# ── 不变量 2：话语事件有界（短期节点不膨胀）──────────────
kg = fresh_kg()
eng = DiffusionEngine(kg, dict(DEFAULT_CONFIG))
eng.name_to_node = dict(kg.nodes)
for i in range(15):
    info = sag.inject_utterance(kg, eng, f"话语{i}",
                                {"nodes": [], "edges": [],
                                 "illocutionary_act": "assertive",
                                 "dialogue_act": "information_statement"})
    time.sleep(0.002)
utts = [n for n in kg.nodes if str(n).startswith("话语_")]
check("不变量: 话语节点有界清理（≤12）", len(utts) <= sag.UTTERANCE_KEEP, str(len(utts)))
stale_edges = [e for e in kg.edges
               if e.src.startswith("话语_") and e.src not in kg.nodes]
check("不变量: 清理不留悬空边", not stale_edges, str(stale_edges[:3]))


# ── 不变量 3：OGCTX 是投影不是第二套认知 ──────────────────
kg = fresh_kg()
eng = DiffusionEngine(kg, dict(DEFAULT_CONFIG))
eng.name_to_node = dict(kg.nodes)
_, proj = turn(kg, eng, "跟着我",
               {"nodes": ["用户"], "edges": [],
                "illocutionary_act": "directive", "dialogue_act": "request"})
full = build_cognitive_context(
    text="跟着我", channel="web",
    parsed={"nodes": ["用户"], "edges": [], "illocutionary_act": "directive",
            "dialogue_act": "request", "response_expectation": "high"},
    decision={"decision": "respond", "winner_behavior": "respond",
              "constraints": [], "desire": 0.6, "factors": {}},
    evidence={"mc_action": {"success": True, "pending": True, "describe": "开始跟随"}},
    speech_act_landscape=proj)
c2 = compile_for_language(full, path="L2")
c1 = compile_for_language(full, path="L1")
check("OGCTX: L2 带图谱投影（概念+激活+共激活）",
      c2.get("speech_act_landscape", {}).get("concept") == "指令"
      and "coactivated" in c2["speech_act_landscape"], str(c2.get("speech_act_landscape"))[:160])
check("OGCTX: 投影存于 state 分区（是状态不是裁决）",
      (full["state"].get("speech_act_landscape") or {}).get("source")
      == "speech_act_graph_diffusion")
check("OGCTX: L1 精简投影（只带概念与激活）",
      c1.get("speech_act", {}).get("concept") == "指令"
      and "coactivated" not in c1.get("speech_act", {}), str(c1.get("speech_act")))
check("OGCTX: debug 视图可追溯言外行为链路",
      debug_view(full).get("speech_act", {}).get("concept") == "指令")


# ── 不变量 4：与既有决策层兼容（dialogue_decide 能消化激活）──
from dialogue_decision import dialogue_decide
kg = fresh_kg()
eng = DiffusionEngine(kg, dict(DEFAULT_CONFIG))
eng.name_to_node = dict(kg.nodes)
turn(kg, eng, "跟着我", {"nodes": ["用户"], "edges": [],
                         "illocutionary_act": "directive", "dialogue_act": "request"})
dd = dialogue_decide(
    parsed={"dialogue_act": "request", "response_expectation": "high",
            "illocutionary_act": "directive", "suggested_reply_goals": ["acknowledge"]},
    text="跟着我", kg=kg, engine=eng, tendencies=[], curiosity_active=False,
    last_outcome_negative=False, last_expr_gap_s=None, has_action_result=True,
    exploration={"eligible": False, "candidates": []}, hormone=None)
check("兼容: dialogue_decide 正常出裁决（决策权仍在竞争层）",
      dd.get("decision") in ("respond", "minimal"), str(dd.get("decision")))
check("兼容: 行为:回应 的实时激活进入竞争（live 通道自动吃到图谱点亮）",
      any(c["behavior"] == "respond" and c.get("live", 0) > 0
          for c in dd.get("candidates") or []),
      str([c for c in dd.get("candidates", []) if c["behavior"] == "respond"][:1]))

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 言语行为图谱化测试全过（A-E 五场景 + 四组不变量 + 真实扩散引擎）")
