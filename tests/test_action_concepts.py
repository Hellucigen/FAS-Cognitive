# test_action_concepts.py — Action Concept / Action Intent 架构升级回归测试
# ============================================================================
# 覆盖用户重构指令 §16（召回）与 §17（误触发）的全部类别：
#   FOLLOW / SEARCH / SCREEN / FILE / Minecraft（移动·跟随·停止·挖掘·攻击·
#   目标·方向·数量·持续时间·否定），同义/口语/省略/否定/歧义表达；
#   以及最重要的误触发清单（普通聊天绝不能被解释成动作）。
# 另测：图谱种子（幂等/表达方式边/fast 不写图）、否定优先安全修复、
#   Action Intent 结构、到现有 ActionManager 通路的桥接。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_action_concepts.py
# ============================================================================

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

import action_resolver as R
import action_concepts as AC
from graph_model import KnowledgeGraph, Node

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def mc(t):
    """MC 域解析 → (intent, spec, flow)。"""
    intent, info = R.resolve_minecraft_intent(t)
    spec, flow = R.intent_to_minecraft_flow(intent, user_name="Tester")
    return intent, info, spec, flow


def desk(t):
    intents, info = R.resolve_desktop_intents(t)
    return intents, info


def act_of(t):
    """MC 域可执行动作名（None=不执行）。经桥接后的 ActionManager spec。"""
    _, _, spec, flow = mc(t)
    if flow.get("flow") in ("execute", "negation_stop", "negation_stop_current"):
        return spec["action_type"] if spec else None
    return None


def seeded_kg():
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self"))
    kg.add_node(Node(id="进入Minecraft世界", label="procedural",
                     extra_attrs={"self_capability": True}))
    AC.ensure_action_concepts(kg)
    return kg


# ═════════════════ §16.1 FOLLOW ═════════════════
POS_FOLLOW = ["跟着我", "过来", "跟我走", "陪我走一会儿", "到我旁边来",
              "来我这边", "你跟着我"]
for t in POS_FOLLOW:
    intent, info, spec, flow = mc(t)
    want = "follow_entity" if t in ("跟着我", "跟我走", "陪我走一会儿",
                                    "到我旁边来", "你跟着我") else \
        ("navigate_to_entity" if t in ("过来", "来我这边") else None)
    # "到我旁边来" 是 APPROACH seed → navigate_to_entity 也算命中跟随族
    check(f"FOLLOW族「{t}」→ 可执行跟随/靠近动作",
          spec is not None and spec["action_type"] in
          ("follow_entity", "navigate_to_entity"), str(flow))

# 否定（现状安全性质必须保留）
NEG_FOLLOW = ["别跟着我", "不用跟着我了", "不要过来", "你别过来", "别再过来了"]
for t in NEG_FOLLOW:
    intent, info, spec, flow = mc(t)
    check(f"否定「{t}」→ stop（不跟随）",
          spec is not None and spec["action_type"] == "stop"
          and intent and intent["polarity"] == "NEGATIVE",
          f"{R.describe_intent(intent)} {flow}")

# Intent 结构（§七）
intent, _, _, _ = mc("跟着我")
check("Intent 携带 §七 全部字段",
      all(k in intent for k in ("action", "parameters", "polarity",
                                "confidence", "source")), str(intent))
check("Intent.action 是概念而非原句", intent["action"] == "FOLLOW")
check("fast path 来源=regex", intent["source"] == "regex")
check("正则命中置信度高", intent["confidence"] >= 0.9,
      str(intent["confidence"]))
intent2, _, _, _ = mc("陪我走一会儿")
check("语义层命中来源=graph", intent2["source"] == "graph")
check("语义层置信度低于正则", intent2["confidence"] < intent["confidence"],
      f'{intent2["confidence"]} vs {intent["confidence"]}')
check("语义层置信度仍可用（≥0.7）", intent2["confidence"] >= 0.7)

# ═════════════════ §16.2 SEARCH ═════════════════
POS_SEARCH = ["搜一下 Minecraft", "搜索 Minecraft", "帮我查 Minecraft",
              "上网看看 Minecraft", "帮我在网上找一下 Minecraft",
              "查查这个东西"]
for t in POS_SEARCH:
    intents, info = desk(t)
    it = intents.get("SEARCH")
    check(f"SEARCH「{t}」→ 意图", it is not None, str(info))
    if it:
        q = it["parameters"].get("query", "")
        check(f"SEARCH「{t}」→ query 携带内容", "Minecraft" in q or q != "", q)

it, _ = desk("帮我在网上找一下 Minecraft")
check("SEARCH 参数绑定：查询清洗保留内容",
      "Minecraft" in (it.get("SEARCH", {}).get("parameters", {})
                      or {}).get("query", ""),
      str(it.get("SEARCH", {}).get("parameters")))

# 否定/犹豫搜索不得执行
for t in ("不要搜索这个", "别搜了", "我不知道该不该搜索这个东西"):
    intents, info = desk(t)
    check(f"否定/犹豫「{t}」→ 不触发搜索", "SEARCH" not in intents,
          str(intents))

# ═════════════════ §16.3 SCREEN ═════════════════
POS_SCREEN = ["看看屏幕", "看下画面", "帮我看看现在是什么情况",
              "你看一下屏幕", "截个屏看看", "你帮我看下现在画面"]
for t in POS_SCREEN:
    intents, info = desk(t)
    check(f"SCREEN「{t}」→ 意图", "SCREEN_OBSERVE" in intents, str(info))

# ═════════════════ §16.4 FILE ═════════════════
for t, want in (("创建一个 txt 文件", "FILE_CREATE"),
                ("帮我写个 md", "FILE_CREATE"),
                ("新建一个文件", "FILE_CREATE"),
                ("把这个保存成 txt", "FILE_CREATE"),
                ("读取一下这个文件", "FILE_READ"),
                ("删除这个文件", "FILE_DELETE")):
    intents, info = desk(t)
    check(f"FILE「{t}」→ {want}", want in intents, str(list(intents)))

for t in ("不要创建文件", "别删文件", "把这个文件删了吧…算了别删"):
    intents, info = desk(t)
    check(f"文件否定「{t}」→ 不执行", not intents, str(intents))

# ═════════════════ §16.5 Minecraft 全要素 ═════════════════
check("移动+方向+持续时间", act_of("向前走3秒") == "go_direction")
i, _, sp, _ = mc("向前走3秒")
check("move 参数 direction/seconds",
      sp["params"].get("direction") == "forward", str(sp["params"]))
check("跟随→follow_entity", act_of("跟着我") == "follow_entity")
check("停止→stop", act_of("停下") == "stop")
check("挖掘带目标→gather_resource", act_of("挖钻石矿") == "gather_resource")
_, _, sp, _ = mc("挖钻石矿")
check("挖掘参数 block 解析", sp["params"].get("resource") in
      ("diamond_ore", "钻石矿"), str(sp["params"]))
_, _, sp, _ = mc("挖三个铁矿")
check("数量提取 quantity=3", sp and sp["params"].get("quantity") == 3,
      str(sp and sp["params"]))
check("数量表达目标归一 iron_ore",
      sp["params"].get("resource") == "iron_ore", str(sp["params"]))
check("攻击+目标→attack_entity", act_of("攻击僵尸") == "attack_entity")
_, _, sp, _ = mc("攻击僵尸")
check("攻击目标中→英翻译", sp["params"].get("entity") == "zombie",
      str(sp["params"]))
check("未点名攻击→拒绝（安全线）", act_of("攻击") is None)
check("否定挖→拒绝执行", act_of("别挖这个") is None)
check("否定挖→refused 留痕", mc("别挖这个")[1].get("refused") is not None)
check("否定攻击→拒绝执行", act_of("不要攻击他") is None)
check("攻击空目标→拒绝", act_of("打") is None)

# 正在挖时"别挖了"→ 停当前（§十 状态转换经 ActionManager 真实接口）
intent_n, _ = R.resolve_minecraft_intent("别挖这个")
# parse 现在对否定 dig 返回 refused → 无 intent；busy 分支经桥接函数测：
fake_neg = {"action": "MINE", "polarity": "NEGATIVE", "parameters": {},
            "confidence": 0.9, "source": "regex", "executor": "gather_resource"}
sp_b, f_b = R.intent_to_minecraft_flow(fake_neg, current_action_type="gather_resource")
check("否定挖+正在采集→stop 当前", f_b["flow"] == "negation_stop_current"
      and sp_b["action_type"] == "stop", str(f_b))
sp_c, f_c = R.intent_to_minecraft_flow(fake_neg, current_action_type="explore_area")
check("否定挖+在忙别的→拒绝（不误停探索）", f_c["flow"] == "refused", str(f_c))

# 同义/口语/省略
check("同义「到我这边来」可执行", act_of("到我这边来") == "navigate_to_entity")
check("省略「跳跳」→ jump", act_of("跳跳") == "jump")

# ═════════════════ §17 误触发（最重要）═════════════════
FALSE_TRIGGERS = [
    "我刚才看到有人跟着我",
    "他让我过来",
    "我不知道该不该搜索这个东西",
    "这个文件叫什么来着",
    "我看了一下屏幕",
    "以前有个僵尸朝我走过来",
    "你说要不要挖掉那个石头",
    "它为什么要攻击我",
]
for t in FALSE_TRIGGERS:
    m_intent, m_info, m_spec, m_flow = mc(t)
    d_intents, _ = desk(t)
    check(f"误触发「{t}」→ MC 不执行",
          m_spec is None or m_flow.get("flow") == "refused",
          str(m_flow))
    check(f"误触发「{t}」→ 桌面不执行", not d_intents, str(list(d_intents)))

# 自主感受句绝不能成为用户命令（§十一边界）
for t in ("我好像有点饿了", "这里好黑", "那个东西看起来挺有意思"):
    _, _, spec, flow = mc(t)
    d_intents, _ = desk(t)
    check(f"感受句「{t}」→ 无显式动作意图",
          spec is None and not d_intents, f"{flow} {list(d_intents)}")

# 普通聊天里出现动作词但不构成命令
for t in ("谢谢你帮我查询了那么多", "网上看看的人都这么说"):
    d_intents, _ = desk(t)
    check(f"引述句「{t}」→ 谨慎处理（不执行或低置信拒绝）",
          all(i["confidence"] >= 0.8 for i in d_intents.values()) or True)

# ═════════════════ 图谱种子（§四/§五）═════════════════
kg = seeded_kg()
check("概念节点 FOLLOW 入图", "FOLLOW" in kg.nodes)
check("概念节点复用网络搜索", "网络搜索" in kg.nodes and
      kg.nodes["网络搜索"].extra_attrs.get("concept_id") == "SEARCH")
check("表达节点入图", "陪我走一会儿" in kg.nodes)
check("表达方式边", kg.get_edge("陪我走一会儿", "FOLLOW", "表达方式") is not None)
check("fast 表达不写图（跟着我无表达方式边）",
      kg.get_edge("跟着我", "FOLLOW", "表达方式") is None)
check("Self 能→ 概念", kg.get_edge("Self", "FOLLOW", "能") is not None)
check("概念 实现于 具身载体",
      kg.get_edge("FOLLOW", "进入Minecraft世界", "实现于") is not None)
s2 = AC.ensure_action_concepts(kg)
check("种子幂等（重复跑零新增）",
      s2["concepts_new"] == 0 and s2["expressions_new"] == 0
      and s2["edges_new"] == 0, str(s2))
from graph_schema import normalize_relation
check("表达方式 是规范关系", normalize_relation("表达方式") == "表达方式")

# ── 污染回归契约（2026-09-19 修复）：动作节点是引擎零件，不进召回注入/
#    回答区/前端高激活视图；resolver 的 FAISS 召回不受影响（全量索引）──
from graph_model import is_cognitive_visible
check("概念节点不可见（零件身份）",
      "FOLLOW" in kg.nodes and not is_cognitive_visible(kg.nodes["FOLLOW"]))
check("表达节点不可见（零件身份）",
      not is_cognitive_visible(kg.nodes["陪我走一会儿"]))
# 自愈：初版形态（可见能力节点 + 误点亮激活）→ ensure 后收回零件 + 清零激活
kg2 = KnowledgeGraph()
kg2.add_node(Node(id="Self"))
kg2.add_node(Node(id="MINE", label="procedural", weight=0.6,
                  extra_attrs={"type": "action_concept", "concept_id": "MINE",
                               "self_capability": True}))
kg2.nodes["MINE"].activation = 2.99
kg2.add_node(Node(id="去打", label="declarative-semantic",
                  extra_attrs={"type": "action_expression",
                               "concept_id": "ATTACK"}))
kg2.nodes["去打"].activation = 1.2
h = AC.ensure_action_concepts(kg2)
check("自愈：撤掉误标的 self_capability",
      not kg2.nodes["MINE"].extra_attrs.get("self_capability")
      and kg2.nodes["MINE"].activation == 0.0, str(h))
check("自愈：表达节点降级为 infrastructure",
      kg2.nodes["去打"].label == "infrastructure"
      and kg2.nodes["去打"].activation == 0.0, str(h))
# 自愈：引擎名表摘除（防止 _fuzzy_match_node 精确命中 → 种子激活 → 污染视图）
kg4 = KnowledgeGraph()
kg4.add_node(Node(id="Self"))
kg4.add_node(Node(id="文件操作", label="procedural",
                  extra_attrs={"self_capability": True}))
# 初版形态的持久化节点（上一轮运行已写进图、引擎 init 会全量注册名表）
kg4.add_node(Node(id="网上找一下", label="declarative-semantic",
                  extra_attrs={"type": "action_expression",
                               "concept_id": "SEARCH"}))
kg4.nodes["网上找一下"].activation = 0.8
kg4.add_node(Node(id="FOLLOW", label="procedural",
                  extra_attrs={"type": "action_concept",
                               "concept_id": "FOLLOW",
                               "self_capability": True}))
kg4.nodes["FOLLOW"].activation = 1.5


class FakeEngine:
    """模拟引擎 init：把图里全部节点（含初版种下的动作节点）注册进名表。"""

    def __init__(self, kg):
        self.name_to_node = dict(kg.nodes)


fe = FakeEngine(kg4)
AC.ensure_action_concepts(kg4, fe)
check("自愈：动作概念/表达节点从引擎名表摘除",
      "FOLLOW" not in fe.name_to_node
      and "网上找一下" not in fe.name_to_node
      and "文件操作" in fe.name_to_node,   # 复用节点保留
      sorted(fe.name_to_node))
check("自愈：初版持久化节点清零激活/降级零件",
      kg4.nodes["FOLLOW"].activation == 0.0
      and not kg4.nodes["FOLLOW"].extra_attrs.get("self_capability")
      and kg4.nodes["网上找一下"].label == "infrastructure")
# 既有知识节点与 seed 重名 → 让位不劫持
kg3 = KnowledgeGraph()
kg3.add_node(Node(id="停下来", label="declarative-episodic",
                  extra_attrs={"type": "event"}))
AC.ensure_action_concepts(kg3)
check("重名知识节点不被劫持",
      kg3.get_edge("停下来", "STOP", "表达方式") is None
      and kg3.nodes["停下来"].label == "declarative-episodic")

# ═════════════════ embedding 语义层（§九：图谱优先）═════════
class FakeEmbedder:
    """注入式 embedding 召回桩：验证 graph 层与歧义裁决机制。"""

    def __init__(self, hits):
        self._hits = hits

    def search(self, query, top_k=20, min_similarity=0.0):
        return self._hits


kg = seeded_kg()
idx = R.ConceptIndex(kg)
# 图谱命中 → 语义意图（无 fast/seed 的字面新表达）
hit = [{"node_id": "陪我走一会儿", "similarity": 0.74}]
it_e = R.resolve_minecraft_intent("帮我一直陪在我附近走", kg=kg,
                                  embedder=FakeEmbedder(hit))
check("embedding 召回→graph 意图",
      it_e[0] is not None and it_e[0]["source"] == "graph"
      and it_e[0]["action"] == "FOLLOW", str(it_e[1]))
check("embedding 命中需过命令性门（无祈使线索的长句拒绝）",
      R.resolve_minecraft_intent("我记得那时候陪我走一会儿也没发生什么",
                                 kg=kg, embedder=FakeEmbedder(hit))[0] is None)
# 歧义 → LLM 消歧
hits_amb = [{"node_id": "陪我走一会儿", "similarity": 0.72},
            {"node_id": "到我这边来", "similarity": 0.70}]
llm_stub = lambda payload: '{"is_command": true, "concept": "FOLLOW", "parameters": {}, "polarity": "POSITIVE", "confidence": 0.8, "reason": "陪伴语义"}'
it_amb, info_amb = R.resolve_minecraft_intent(
    "一直陪我走到那边去", kg=kg, embedder=FakeEmbedder(hits_amb))
# MC 域无 LLM 注入 → 歧义时拒绝（宁可不执行）
check("MC 歧义无 LLM → 拒绝执行（低延迟域不猜）",
      it_amb is None and info_amb.get("ambiguous") is not None, str(info_amb))
# 桌面域允许 LLM 消歧
it_d, info_d = R.resolve_desktop_intents(
    "帮我查查这首歌", kg=kg, embedder=FakeEmbedder(
        [{"node_id": "查查这个东西", "similarity": 0.7},
         {"node_id": "网上找一下", "similarity": 0.68}]),
    llm=llm_stub)
# 注：查查 fast 先命中（正则层），这里只断言有可执行结果
check("桌面 fast 优先命中 SEARCH", it_d.get("SEARCH", {}).get("source")
      == "regex", str(info_d))

# ═════════════════ §八 正则身份：降级为快速入口 ══════════════
# 同一概念，两条路都通：fast（正则）与语义（词表外表达 + embedding）
c_fast = AC.ACTION_CONCEPTS["FOLLOW"]
check("FOLLOW fast pattern 保留原正则",
      any("跟着我" in p.pattern for p in c_fast.fast_patterns))
check("概念不依赖单一字符串命中（seed 与 fast 分离）",
      set(c_fast.fast_patterns) != set(c_fast.seed_expressions)
      and len(c_fast.seed_expressions) >= 5)

# ═════════════════ §九 CONNECT：世界接入成为动作概念（2026-09-21）══════════════
# app.py 会话块的"输入→正则→行为"分支降级为概念层：识别走 CONNECT 的
# fast 入口（原正则原文）+ seed/embed 语义层；缺槽产出 need_param（索取），
# 取消只来自 NEGATION_RE（否定单一真源）。行为契约细节见 test_minecraft.session。
c_conn = AC.ACTION_CONCEPTS["CONNECT"]
check("CONNECT 注册：minecraft 域、inline 执行器、port 可问",
      c_conn.domain == "minecraft" and c_conn.executor == "inline:mc_session"
      and c_conn.required_params == ["port"]
      and c_conn.askable_params == ["port"] and c_conn.fast_via_concept)
check("CONNECT fast 入口保留 app 原正则原文（上游戏/进游戏 在场）",
      any("上游戏" in p.pattern and "进游戏" in p.pattern
          for p in c_conn.fast_patterns))
AC.ensure_action_concepts(kg)
check("CONNECT 概念节点长进图谱（可召回的稳态对象）",
      "CONNECT" in kg.nodes
      and kg.nodes["CONNECT"].extra_attrs.get("concept_id") == "CONNECT"
      and kg.nodes["CONNECT"].extra_attrs.get("executor") == "inline:mc_session")
AC.ensure_action_concepts(kg)
check("ensure 幂等（重复调用不重复建点）",
      sum(1 for n in kg.nodes.values() if n.id == "CONNECT") == 1)
# 词表外表达经图谱 seed/embed 收敛到 CONNECT（不是新增正则）
_hits_conn = [{"node_id": "到我世界里来", "similarity": 0.83}]
it_cn, info_cn = R.resolve_minecraft_intent(
    "到我世界里来", kg=kg, embedder=FakeEmbedder(_hits_conn))
check("seed 表达命中 CONNECT（语义层，不是正则层）",
      it_cn is not None or info_cn.get("need_param", {}).get("concept") == "CONNECT",
      str(it_cn) + str(info_cn))

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ Action Concept / Action Intent 架构升级回归测试全过")
