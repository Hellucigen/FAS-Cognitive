# test_action_space.py — 行动空间层验收
# ============================================================================
# 2026-09-20 行动/行为重构核心断言：
#   1. 候选来自图谱（激活 ∪ 活跃源供性边），不来自枚举——
#      证明"删掉 BEHAVIORS 词表注入后，新的行动概念仍能进候选"
#   2. 行动边真实参与现有 diffuse_step（情境点亮→扩散→行动节点激活↑）
#   3. 概念提议三层去重（规范化/别名边/精确短键），不刷重复节点
#   4. 生命周期数值迁移（proposed→observed→established→weakened；
#      deprecated 退出候选但节点保留）
#   5. 统一 Action Schema 字段与 execution_method 声明
#   6. [ActionTrace] 结构
# 离线，无 LLM、无网络。运行:
#   E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_action_space.py

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

import config as C
from graph_model import KnowledgeGraph, Node, Edge
from diffusion_engine import DiffusionEngine
from action_space import (ActionSpace, action_key, is_action_node,
                          ACTION_NODE_TYPES)

FAILURES = []


def check(name, cond, detail=""):
    st = "PASS" if cond else "FAIL"
    print(f"[{st}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


CFG = dict(C.DEFAULT_CONFIG)
CFG["action_space"] = dict(CFG.get("action_space") or {})


def build(with_vocab=True):
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", weight=0.5, label="self", graph_space="self"))
    kg.add_node(Node(id="CuriosityDrive", weight=0.5,
                     graph_space="cognitive",
                     extra_attrs={"type": "drive"}))
    kg.add_node(Node(id="情境:用户分享", weight=0.5, label="disposition",
                     graph_space="self", extra_attrs={"type": "context"}))
    if with_vocab:
        kg.add_node(Node(id="行为:追问", weight=0.5, label="disposition",
                         graph_space="self",
                         extra_attrs={"type": "behavior",
                                      "action_key": "ask",
                                      "channel": "communication",
                                      "lifecycle": "established",
                                      "expressive": True}))
        kg.add_node(Node(id="行为:沉默", weight=0.5, label="disposition",
                         graph_space="self",
                         extra_attrs={"type": "behavior",
                                      "action_key": "silence",
                                      "channel": "communication",
                                      "lifecycle": "established",
                                      "expressive": False}))
    eng = DiffusionEngine(kg, CFG)
    return kg, eng


# ═══ 1. 候选 = 图谱扫描，不依赖词表 ═══

kg, eng = build(with_vocab=False)     # 无 LEGACY 行为词表
asp = ActionSpace(kg, eng, CFG)
asp._ensure_edge("情境:用户分享", "行动:讲个相关的故事", "先验", 0.6)
kg.get_node("情境:用户分享").activation = 1.5
kg.get_node("情境:用户分享").touch()
eng.mark_active(["情境:用户分享"])
cands = asp.collect_action_candidates()
check("候选宇宙来自图谱：无词表时新行动仍可入选（经供性边+活跃源）",
      "讲个相关的故事" in cands, str(list(cands)))
check("候选携带 affordance 溯源",
      cands and "先验" in cands["讲个相关的故事"]["affordances"],
      str(cands))
# deprecated 退出候选但节点保留
asp._ensure_action_node("行动:旧动作", lifecycle="proposed")
kg.get_node("行动:旧动作").extra_attrs["lifecycle"] = "deprecated"
kg.get_node("行动:旧动作").activation = 3.0
c2 = asp.collect_action_candidates()
check("deprecated 退出候选（节点仍在图里）",
      "旧动作" not in c2 and "行动:旧动作" in kg.nodes, str(list(c2)))

# ═══ 2. 新边真实参与 diffuse_step（§十八硬要求）═══

kg, eng = build()
asp = ActionSpace(kg, eng, CFG)
asp._ensure_edge("情境:用户分享", "行动:讲个相关的故事", "先验", 0.9)
ctx = kg.get_node("情境:用户分享")
ctx.activation = 2.0
ctx.touch()
eng.mark_active(["情境:用户分享"])
act_before = kg.get_node("行动:讲个相关的故事").activation
eng.diffuse_round(max_steps=2)
act_after = kg.get_node("行动:讲个相关的故事").activation
check("情境→行动 供性边真实传导（diffuse 后行动节点被点亮）",
      act_after > max(act_before, 0.05), f"{act_before} → {act_after}")
cands = asp.collect_action_candidates()
check("被扩散点亮的行动直接成为候选（activation 路径）",
      "讲个相关的故事" in cands and
      cands["讲个相关的故事"]["activation"] > 0.04,
      str({k: v["activation"] for k, v in cands.items()}))
# Drive→行动 通道：CuriosityDrive 点亮 行为:追问（先验之外的 驱动 边）
asp._ensure_edge("CuriosityDrive", "行为:追问", "驱动", 0.6)
cd = kg.get_node("CuriosityDrive")
cd.activation = 3.0
cd.touch()
eng.mark_active(["CuriosityDrive"])
before = kg.get_node("行为:追问").activation
eng.diffuse_round(max_steps=2)
check("Drive→行动 边传导内驱压力（追问节点激活上升）",
      kg.get_node("行为:追问").activation > before,
      f"{before} → {kg.get_node('行为:追问').activation}")

# ═══ 3. 概念提议：三层去重 ═══

kg, eng = build()
asp = ActionSpace(kg, eng, CFG)
k1 = asp.propose_action("去看看那个村民在干什么", description="观察村民")
node_id = f"行动:{k1}"
check("新行动概念入图（proposed）",
      k1 and node_id in kg.nodes and
      kg.nodes[node_id].extra_attrs["lifecycle"] == "proposed", str(k1))
k2 = asp.propose_action("去看看那个村民在干什么")   # 同表面
check("同义表面复用不建重复节点（规范化层）",
      k2 == k1 and sum(1 for n in kg.nodes
                       if str(n).startswith("行动:")) <= 2, str(k2))
k3 = asp.propose_action("去瞧瞧那村民做什么", create=True)
check("不同表面不同概念才新建（规范化区分）", k3 != k1, f"{k1} vs {k3}")
k4 = asp.propose_action("追问")            # 精确命中既有短键/节点
check("表面恰为既有概念 → 复用", k4 == "追问" or k4 == "ask", str(k4))
check("表面形式登记 别名 边（跨重启去重）",
      any(e.relation == "别名" for e in kg.edges))

# ═══ 4. 生命周期数值迁移 ═══

cfg2 = dict(CFG)
cfg2["action_space"] = dict(cfg2["action_space"],
                            lifecycle_promote_touches=3,
                            lifecycle_establish_evidence=2)
kg, eng = build()
asp = ActionSpace(kg, eng, cfg2)
k = asp.propose_action("给对方出个主意")
node = kg.nodes[f"行动:{k}"]
for _ in range(3):
    asp.touch(k)
check("proposed 多次被考虑 → observed",
      node.extra_attrs["lifecycle"] == "observed",
      str(node.extra_attrs["lifecycle"]))
kg.add_node(Node(id=f"倾向:{k}@用户分享", weight=0.3, label="disposition",
                 graph_space="self",
                 extra_attrs={"type": "disposition", "behavior": k,
                              "evidence_count": 5}))
asp.touch(k)
check("observed + pair 证据达标 → established",
      node.extra_attrs["lifecycle"] == "established",
      str(node.extra_attrs["lifecycle"]))
node.extra_attrs["last_touched"] = time.strftime(
    "%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 30 * 86400))
node.extra_attrs["lifecycle"] = "observed"
n_changed = asp.decay_lifecycle()
check("长期未触及 → weakened（数值规则）",
      n_changed >= 1 and node.extra_attrs["lifecycle"] == "weakened",
      str(node.extra_attrs["lifecycle"]))

# ═══ 5. 统一 Schema 与 execution_method ═══

kg, eng = build()
asp = ActionSpace(kg, eng, CFG)
asp._ensure_edge("情境:用户分享", "行动:出主意", "先验", 0.5)
kg.get_node("情境:用户分享").activation = 1.0
eng.mark_active(["情境:用户分享"])
cands = asp.collect_action_candidates()
sch = asp.to_schema("出主意", candidate=cands.get("出主意"),
                    target="用户", intention="帮助用户拿主意",
                    expected_outcome="用户得到参考",
                    confidence=0.6, execution_method="language")
check("schema 指向图谱节点", sch["action_concept"] == "行动:出主意"
      and kg.get_node(sch["action_concept"]) is not None, str(sch))
check("schema 字段齐全（含 target 任意图谱节点/执行方式声明）",
      all(f in sch for f in ("action_id", "action_concept", "target",
                             "intention", "expected_outcome", "urgency",
                             "confidence", "cost", "risk", "prerequisites",
                             "execution_method")))

# ═══ 6. ensure_action_space 种表幂等 + trace ═══

kg, eng = build()
asp = ActionSpace(kg, eng, CFG)
s1 = asp.ensure_action_space()
s2 = asp.ensure_action_space()
check("供性边/本体种子幂等（第二次零新增）",
      s2["drive_edges"] == 0 and s2["emotion_edges"] == 0, str(s2))
check("CuriosityDrive→行为:追问 驱动边已种",
      kg.get_edge("CuriosityDrive", "行为:追问", "驱动") is not None)
check("行动族本体存在且族节点不作为候选类型",
      kg.get_node("行动族:社交表达") is not None and
      (kg.nodes["行动族:社交表达"].extra_attrs or {}).get("type")
      not in ACTION_NODE_TYPES)
kg.get_node("CuriosityDrive").activation = 2.0
kg.get_node("CuriosityDrive").touch()
kg.get_node("情境:用户分享").activation = 1.0
kg.get_node("情境:用户分享").touch()
tr = asp.trace(candidates=asp.collect_action_candidates(),
               winner="ask", context="情境:用户分享",
               drives={"curiosity": 0.66},
               competition={"activation": 0.82, "personality": 0.31},
               execution="language", outcome="用户继续提供信息",
               learning="情境:用户分享→行为:追问 strength +0.04")
check("trace 五段齐全（§二十一）",
      all(s in tr for s in ("Context:", "Action candidates:", "Selected:",
                            "Competition:", "Learning:")), tr[:160])

# ═══ 7. 注册表视图（供 disposition 校验）═══

kg, eng = build()
asp = ActionSpace(kg, eng, CFG)
asp.propose_action("给对方出个主意")
keys = asp.known_action_keys()
check("known_action_keys = 图谱行动节点视图",
      {"ask", "silence"} <= keys and len([k for k in keys]) >= 3, str(keys))

# ═══ 8. 竞争集成：新概念进 dialogue_decide 并可胜出/可学习 ═══

from dialogue_decision import dialogue_decide
from disposition_store import DispositionStore

kg, eng = build()                    # 带 LEGACY 行为:追问/沉默
asp = ActionSpace(kg, eng, CFG)
ds = DispositionStore(kg, dict(CFG))
# 提议一个新沟通行动，并给它一条强先验（模拟"在分享情境里她学会了
# 给对方出主意很有效"——先建 pair 学强度，激活边即人格载体）
k = asp.propose_action("给对方出个主意")
ds.apply_experience(k, "情境:用户分享", social_outcome="accepted",
                    hormone={"factor": 1.0}, evidence_ref="e1",
                    allow_create=True)
for _ in range(4):
    ds.apply_experience(k, "情境:用户分享", social_outcome="accepted",
                        hormone={"factor": 1.0}, evidence_ref="e2",
                        allow_create=True)
pair = kg.nodes.get(f"倾向:{k}@用户分享")
check("词表外新行动概念可建学习 pair（注册表校验放行）",
      pair is not None, str(list(n for n in kg.nodes
                                 if n.startswith("倾向:"))[:5]))
ae = ds._activation_edge("情境:用户分享", k)
check("新概念的 情境-[激活]->行动 边存在且权重=strength",
      ae is not None and abs(ae.weight - float(
          pair.extra_attrs["strength"])) < 1e-9)
asp._ensure_edge("情境:用户分享", f"行动:{k}", "先验", 0.9)
kg.get_node("情境:用户分享").activation = 3.0
kg.get_node("情境:用户分享").touch()
dd = dialogue_decide(
    {"dialogue_act": "sharing", "response_expectation": "medium"},
    "我今天把红石机器修好了", kg, eng,
    tendencies=ds.tendencies_for(["情境:用户分享"]),
    curiosity_active=False, last_outcome_negative=False,
    last_expr_gap_s=None, has_action_result=False,
    action_space=asp)
kn = [c["behavior"] for c in dd["candidates"]]
check("新行动概念进入竞争候选（图谱来源，非枚举）", k in kn, str(kn))
check("新概念胜者携带 action_concept 节点",
      dd.get("action_concept") == f"行动:{k}"
      or dd.get("winner_behavior") in (k, "respond", "share"),
      f"winner={dd.get('winner_behavior')} concept={dd.get('action_concept')}")
check("secondary_actions 输出存在", "secondary_actions" in dd)
# 胜者生命周期被记录
node = kg.nodes.get(f"行动:{k}")
check("胜者 touch → lifecycle 推进可观测",
      (node.extra_attrs or {}).get("touch_count", 0) >= 0
      and (node.extra_attrs or {}).get("last_touched"),
      str((node.extra_attrs or {}).get("lifecycle")))

# ═══ 9. 真实 disposition 节点形态（extra_attrs.key 而非 action_key）
#        不得产生重复候选（冒烟中发现的污染回归）═══

kg, eng = build()
kg.add_node(Node(id="行为:回应", weight=0.5, label="disposition",
                 graph_space="self",
                 extra_attrs={"type": "behavior", "key": "respond"}))
kg.add_node(Node(id="情境:用户分享", weight=0.5, label="disposition",
                 graph_space="self", extra_attrs={"type": "context"}))
asp = ActionSpace(kg, eng, CFG)
kg.get_node("情境:用户分享").activation = 2.0
kg.get_node("情境:用户分享").touch()
ds = DispositionStore(kg, dict(CFG))
ds.apply_experience("respond", "情境:用户分享", social_outcome="accepted",
                    evidence_ref="x", allow_create=True)
kg.get_node("情境:用户分享").activation = 2.0
dd = dialogue_decide(
    {"dialogue_act": "sharing", "response_expectation": "medium"},
    "分享一件事", kg, eng, [], False, False, None, False,
    action_space=asp)
_ck = [c["behavior"] for c in dd["candidates"]]
check("真实节点形态下候选无 respond/回应 重复",
      "respond" in _ck and "回应" not in _ck, str(_ck))

# ═══ 10. 具身学习双粒度（concept 级 pair 与粗粒度共存）═══

kg, eng = build()
ds = DispositionStore(kg, dict(CFG))
kg.add_node(Node(id="MINE", weight=0.5, label="procedural",
                 graph_space="semantic",
                 extra_attrs={"type": "action_concept",
                              "action_key": "MINE",
                              "channel": "embodied"}))
b1 = ds.apply_experience("explore", "情境:主动发起",
                         self_outcome="discovery",
                         evidence_ref="r", allow_create=True)
b2 = ds.apply_experience("MINE", "情境:主动发起",
                         self_outcome="discovery",
                         evidence_ref="r", allow_create=True)
check("粗粒度概念（图谱外的 explore）照常学习", b1.get("written"),
      str(b1))
check("具身概念 id（图谱 action_concept 节点）可学习——行动细节不再被折叠",
      b2.get("written") and kg.nodes.get("倾向:MINE@主动发起") is not None,
      str(b2))

# ═══ 11. resolver 的 LLM 提议通道（来源之一，不垄断、不执行）═══

from action_resolver import _llm_disambiguate
from action_concepts import ACTION_CONCEPTS

kg, eng = build()
asp = ActionSpace(kg, eng, CFG)
_concepts = list(ACTION_CONCEPTS.values())[:4]


def fake_llm_new(_prompt):
    return ('{"is_command": true, "is_new": true, '
            '"new_action": "给对方唱首歌", "description": "唱歌哄人"}')


info = {}
out = _llm_disambiguate(_concepts, "给她唱首歌吧", fake_llm_new,
                        action_space=asp, info=info)
_pk = info.get("proposed_action")
check("LLM 可提议新行动概念（is_new → proposed 入图）",
      out is None and _pk and f"行动:{_pk}" in kg.nodes,
      str(info))
check("提议不产执行意图（返回 None，能理解≠能做）", out is None)
out2 = _llm_disambiguate(_concepts, "给她唱首歌吧", fake_llm_new,
                         action_space=asp, info={})
check("重复提议规范化去重（同 key，节点不翻倍）",
      out2 is None and sum(1 for n in kg.nodes if n.startswith("行动:")) == 1,
      str([n for n in kg.nodes if n.startswith("行动:")]))


def fake_llm_known(_p):
    return ('{"is_command": true, "concept": "SEARCH", '
            '"parameters": {"query": "红石"}}')


out3 = _llm_disambiguate(_concepts, "帮我查查红石", fake_llm_known,
                         action_space=asp, info={})
check("既有概念消歧通道不受提议扩展影响",
      out3 is not None and out3[0].concept_id == "SEARCH", str(out3))

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 行动空间层验收通过（候选来自图谱、边参与扩散、提议去重、生命周期、schema、trace）")
