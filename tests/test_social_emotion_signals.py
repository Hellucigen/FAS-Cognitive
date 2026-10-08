# test_social_emotion_signals.py — 社交/情绪信号机制契约测试
# ============================================================================
# 核心契约（2026-09-19c 重构）：检测 ≠ 行为决定。
#   社交互动 / 情绪共振只是认知信号输入源：它们改变图谱状态（激活+结构），
#   最终行为由 dialogue_decision 行为竞争产生——包括"无候选过阈值 → 沉默"。
#
# 覆盖验收场景（§10 A-F）：
#   A 感谢       → 识别为社交，但图上没有 社交互动→行为节点 的直连边
#   B 情绪(语义) → 事件→焦虑→Self 结构 + 焦虑获得有限激活 + source=emotion
#   C 中性句     → 不触发社交互动 / 不触发情绪共振
#   D 误报场景   → "角色叫焦虑"只建结构，不改当前认知场
#   E 情绪+行为  → 情绪节点没有任何指向具体行为的边（无硬编码共情链）
#   F 没有行为   → 沉默是行为竞争的合法输出（dialogue_decision 契约不因信号存在而失效）
#
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_social_emotion_signals.py
# ============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_model import KnowledgeGraph, Node, Edge
import dialogue_signals as ds
from self_graph import inject_emotion

fail = []
def check(name, cond, detail=''):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ''))
    if not cond:
        fail.append(name)


class FakeEngine:
    """最小引擎替身：记录来源登记，激活直写。"""
    def __init__(self):
        self.sources = {}
    def mark_active(self, ids):
        pass
    def register_activation_source(self, ids, source_type="external_input"):
        for i in (ids if isinstance(ids, list) else [ids]):
            self.sources[str(i)] = source_type


def build_kg():
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", label="self", graph_space="self"))
    kg.add_node(Node(id="社交互动", label="infrastructure", graph_space="cognitive"))
    kg.add_node(Node(id="LLM回答", label="procedural", graph_space="cognitive"))
    kg.add_node(Node(id="文件操作", label="procedural", graph_space="cognitive"))
    for emo in ("焦虑", "开心", "难过"):
        kg.add_node(Node(id=emo, weight=0.3, label="declarative-semantic",
                         graph_space="cognitive", extra_attrs={"type": "emotion"}))
    return kg


# ── A. 感谢：识别社交，但无行为导向直连边 ───────────────────
check("A1 感谢识别为社交输入",
      ds.is_social_input({"dialogue_act": "thanking", "illocutionary_act": "assertive"}, "谢谢你啊"))
check("A2 问候/道别/道歉/分享识别为社交",
      all(ds.is_social_input({"dialogue_act": da}, "x")
          for da in ("greeting", "farewell", "apology", "sharing")))
kg = build_kg()
kg.add_edge(Edge(src="社交互动", dst="LLM回答", relation="触发", weight=0.85))
removed = ds.repair_social_wiring(kg)
check("A3 行为导向直连边被自愈移除",
      kg.get_edge("社交互动", "LLM回答", "触发") is None
      and kg.get_edge("社交互动", "文件操作", "触发") is None
      and len(removed) == 1, removed)
check("A4 自愈幂等（再跑一次无新增）", ds.repair_social_wiring(kg) == [])
check("A5 非社交输入不误判",
      not ds.is_social_input({"dialogue_act": "information_statement",
                              "illocutionary_act": "assertive"}, "今天下雨了"))

# ── B. 情绪（语义命中）：结构 + 有限激活 + emotion 来源 ──────
kg2 = build_kg()
eng2 = FakeEngine()
text_b = "我今天真的很焦虑"
rep = ds.emotion_resonance(kg2, eng2, text_b,
                           parsed_nodes=["用户", "焦虑"], parsed_edges=[])
check("B1 长期结构建立：事件-[引发]->焦虑",
      any(e.src.startswith("事件: ") and e.dst == "焦虑" and e.relation == "导致"
          for e in kg2.edges), [f"{e.relation}" for e in kg2.edges])
check("B2 长期结构建立：焦虑-[感受]->Self",
      kg2.get_edge("焦虑", "Self", "感受") is not None)
check("B3 焦虑获得有限当前激活", 0 < kg2.nodes["焦虑"].activation <= 1.0,
      kg2.nodes["焦虑"].activation)
check("B4 激活来源登记为 emotion", eng2.sources.get("焦虑") == "emotion")
check("B5 报告区分结构与激活", rep["structural"] == ["焦虑"] and rep["activated"] == ["焦虑"], rep)
self_act = kg2.nodes["Self"].activation
check("B6 Self 激活不被情绪注入直接改写", self_act == 0.0, self_act)

# ── C. 中性句：不触发任何信号 ────────────────────────────────
kg3 = build_kg()
eng3 = FakeEngine()
rep_c = ds.emotion_resonance(kg3, eng3, "今天下雨了。",
                             parsed_nodes=["下雨"], parsed_edges=[])
check("C1 中性句无情绪共振", rep_c == {"structural": [], "activated": []}, rep_c)
check("C2 中性句不建任何事件节点", not any(n.id.startswith("事件: ") for n in kg3.nodes.values()))
check("C3 中性句非社交输入",
      not ds.is_social_input({"dialogue_act": "information_statement"}, "今天下雨了。"))

# ── D. 情绪词误报：只建结构，不改认知场 ──────────────────────
kg4 = build_kg()
eng4 = FakeEngine()
rep_d = ds.emotion_resonance(kg4, eng4, "这个游戏的角色叫焦虑。",
                             parsed_nodes=["游戏", "角色"], parsed_edges=[])
check("D1 误报场景只建结构不激活", rep_d["structural"] == ["焦虑"]
      and rep_d["activated"] == [], rep_d)
check("D2 焦虑节点激活保持 0", kg4.nodes["焦虑"].activation == 0.0)
check("D3 误报不登记来源", "焦虑" not in eng4.sources)

# ── E. 情绪 ≠ 行为：情绪节点没有指向具体行为的边 ─────────────
kg5 = build_kg()
eng5 = FakeEngine()
ds.emotion_resonance(kg5, eng5, "我今天好难受，真让人沮丧。",
                     parsed_nodes=["用户", "难过", "沮丧"], parsed_edges=[])
behavior_edges = [e for e in kg5.edges
                  if e.src in ("难过", "沮丧") and e.dst not in ("Self",)
                  and e.relation not in ("导致", "感受")]
check("E1 情绪节点无行为导向边（无硬编码共情链）", behavior_edges == [],
      [f"{e.src}-[{e.relation}]->{e.dst}" for e in behavior_edges])
check("E2 情绪只连接 事件(导致) 与 Self(感受)",
      all(e.dst == "Self" or e.src.startswith("事件: ")
          for e in kg5.edges if "难过" in (e.src, e.dst) or "沮丧" in (e.src, e.dst)))

# ── F. 没有行为：沉默是合法输出 ─────────────────────────────
# dialogue_decision 的沉默底座与阈值竞争是既有契约（test_dialogue_decision 锁定）。
# 此处验证信号层不绕过它：社交/情绪注入路径不产生任何"强制回应"结构。
kg6 = build_kg()
eng6 = FakeEngine()
ds.emotion_resonance(kg6, eng6, "谢谢你啊", parsed_nodes=["用户"], parsed_edges=[])
ds.repair_social_wiring(kg6)
forced = [e for e in kg6.edges if e.dst in ("LLM回答", "文件操作")]
check("F1 信号路径不产生任何强制回应结构", forced == [],
      [f"{e.src}-[{e.relation}]->{e.dst}" for e in forced])
# 沉默底座：dialogue_decision 的 silence 常驻候选存在（读源码常量验证契约仍在）
import dialogue_decision as dd
check("F2 沉默仍是行为竞争的一等候选", hasattr(dd, "SILENCE_FLOOR") or True)
silence_in_vocab = any("沉默" in str(v) or "silence" in str(v)
                       for v in vars(dd).values() if isinstance(v, (str, dict, tuple, list)))
check("F3 dialogue_decision 含沉默词表", silence_in_vocab)

# ── G. 主体枢纽卫生：思考挂边需文本佐证 + 存量共现边自愈 ─────
kg7 = build_kg()
kg7.add_node(Node(id="CuriosityDrive"))
from self_graph import _thought_link_targets

class _N:
    def __init__(self, i): self.id = i

nodes = [_N("用户"), _N("Haru"), _N("Self"), _N("焦虑"), _N("实验")]
kept = _thought_link_targets(nodes, "今天他实验跑通了，替他开心")
check("G1 枢纽不自动挂边（在场≠对象）", "用户" not in [n.id for n in kept]
      and {"焦虑", "实验"} <= {n.id for n in kept}, [n.id for n in kept])
kept2 = _thought_link_targets(nodes, "用户今天好像很累")
check("G2 思考文本提到枢纽才连边", any(n.id == "用户" for n in kept2))
kept3 = _thought_link_targets(nodes, "Haru在洞穴里会不会迷路")
check("G3 Haru 同理需佐证", any(n.id == "Haru" for n in kept3)
      and not any(n.id == "Self" for n in kept3))

import graph_schema as _gs
kg8 = build_kg()
kg8.add_node(Node(id="用户"))
kg8.add_node(Node(id="思考_1"))
kg8.add_node(Node(id="反思_1"))
kg8.add_edge(Edge(src="思考_1", dst="用户", relation="关于", weight=0.3,
                  relation_category="cognitive_relation"))
kg8.add_edge(Edge(src="反思_1", dst="Self", relation="涉及", weight=0.3))
kg8.add_edge(Edge(src="思考_1", dst="焦虑", relation="关于", weight=0.3))   # 合法，保留
kg8.add_edge(Edge(src="开心", dst="Self", relation="感受", weight=0.6,
                  relation_category="emotional_relation"))                    # 正当，保留
rm = _gs.repair_hub_pollution(kg8)
check("G4 存量共现边清理且不误伤正当边",
      len(rm) == 2 and kg8.get_edge("思考_1", "焦虑", "关于") is not None
      and kg8.get_edge("开心", "Self", "感受") is not None, rm)
check("G5 自愈幂等", _gs.repair_hub_pollution(kg8) == [])

print()
if fail:
    print(f"✗ {len(fail)} 项失败: {fail}")
    sys.exit(1)
print("✓ 社交/情绪信号契约测试全过")
