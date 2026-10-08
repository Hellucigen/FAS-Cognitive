# action_concepts.py — 动作概念层（Action Concept：稳定的认知对象）
# ============================================================================
# 架构升级（2026-09-19，用户指令）：把"正则匹配动作"降级为"动作语义的快速
# 入口"。正则仍然可以让 FAS 很快地行动，但正则不再定义 FAS 能理解什么动作。
#
# 设计（与 compound phrase / relation frame 同一思想，§十九）：
#   自然语言动作表达（"陪我走一会儿"）
#       ↓ [表达方式]                      ← 图谱：action_expression → action_concept
#   动作概念（FOLLOW）                    ← 稳定认知对象，住图里（可召回、可扩散）
#       ↓ Action Frame（参数槽位绑定）
#   Action Intent（结构化裁决，见 action_resolver.py）
#       ↓
#   ActionManager / 内联执行器（现有通路不变）
#
# 本模块三件事：
#   1. 概念注册表 ACTION_CONCEPTS：每个概念绑定 fast patterns（高置信快捷
#      入口，来自现有正则，不重写语义）、seed 表面表达（先验，长到图上）、
#      参数槽、否定语义（stop/refuse）、执行器绑定。
#   2. ensure_action_concepts(kg)：幂等地把概念节点 + 表达方式边种进图谱。
#      图谱的作用是提供语义概念、关系与候选激活——**不是**动作关键词词典：
#      任何通路都必须经 action_resolver 形成 Action Intent 才允许执行。
#   3. MC 反射（minecraft.reflex）保持为 FOLLOW/APPROACH/... 的 fast pattern
#      层与参数绑定来源；概念层在其上扩展覆盖，不替换它（§十五/§八）。
#
# 边界（§十一）：这里解析的是**用户显式动作指令**（SRC_USER）。自主行为
# （图谱激活→行为候选→竞争→ActionManager）是另一条通路（autonomy/
# dialogue_decision），绝不允许用本层的意图产物直接当用户命令执行。
#
# 零 LLM：本模块确定性；LLM 只在 resolver 的歧义消解层被按需调用。
# ============================================================================

import logging
import re
from dataclasses import dataclass, field

from graph_model import Node, Edge, now_str

logger = logging.getLogger(__name__)

# MC fast patterns 的唯一真源仍是 minecraft.reflex（否定优先/具名参数组/
# 安全拒绝等已锁定测试），概念层引用它而不是复制一份正则。
import minecraft.reflex as _reflex

# ── 最小数据结构：Action Concept ──────────────────────────────

@dataclass
class ActionConcept:
    """一个稳定的动作语义概念（不是用户说出的字符串）。

    字段即 §三 要求的最小描述：概念 / 表面表达 / fast pattern / 参数 /
    否定语义 / 执行器 / 确认要求 / 执行约束。
    """
    concept_id: str            # 稳定概念名（FOLLOW / SEARCH / SCREEN_OBSERVE …）
    name_zh: str               # 中文规范名（进图谱 description/tags）
    domain: str                # minecraft | web | file | screen
    executor: str              # 执行器绑定：ActionManager action_type，
                               # "inline:<name>"=app 管线内联执行（现状保留），
                               # ""=能力缺失（能理解、暂不支持执行，如实拒绝）
    description: str = ""      # 概念语义描述（参与 embedding 召回）
    node_id: str = ""          # 图谱概念节点 id（空=用 concept_id；复用现有节点时指定）
    fast_patterns: list = field(default_factory=list)   # [re.Pattern] 高置信快捷入口
    seed_expressions: list = field(default_factory=list)  # 先验表面形式（长到图上）
    required_params: list = field(default_factory=list)
    optional_params: list = field(default_factory=list)
    # askable_params：缺这些参数时**向用户索取**（resolver 产出结构化的
    # need_param 结果，进入对话证据），而不是猜、也不是硬拒绝。
    # 前提仍是铁律"缺参数不猜目标"——索取是把不知道的事实交还给知道的人。
    askable_params: list = field(default_factory=list)
    negation: str = "refuse"   # 否定语义：stop（否定=停当前）| refuse（否定=不做）
                               # | none（本身就是停止类）
    dangerous: bool = False    # 危险动作：目标必须点名，绝不就近挑选（现状安全线）
    confirm_required: bool = False  # 需用户确认才执行（删除类；v1 全部未接入）
    min_confidence: float = 0.60    # 意图置信度下限（低于 → 不执行/进消歧）
    allow_semantic: bool = True     # 是否允许语义层召回（false=只认 fast pattern）
    fast_via_concept: bool = False  # True=由 resolver 直接执行本概念的
                                    # fast_patterns（反射通道之外的概念，如 CONNECT）

    def matches_fast(self, text: str):
        """fast path：命中任一高置信快捷正则 → 返回 Match（None=未命中）。"""
        for pat in self.fast_patterns:
            m = pat.search(text)
            if m:
                return m
        return None


def _mc(name: str) -> list:
    """取 minecraft.reflex.ACTION_PATTERNS 里某动作名的全部已编译正则。"""
    return [pat for act, pat in _reflex.ACTION_PATTERNS if act == name]


# ── 概念注册表 ────────────────────────────────────────────────
# fast_patterns 全部来自现有代码（app.py 内联正则 + minecraft.reflex），
# 原文保留、只降级为"快速入口"身份；seed_expressions 是先验种子
# （§十三：新增口语表达优先靠语义泛化，词表只是高置信锚点）。

SEARCH_FAST_RE = re.compile(
    # 原 app.py:2716 触发词原文（保留），外加已证实的高频口语变体：
    # "帮我在网上找找"（app.py:16 测试清单要求命中）
    r"搜一下|搜搜|搜索|上网搜|网上搜|网上查|查一下|查查|帮我查|search"
    r"|在网上找|网上找|上网找|上网看看|网上看看|帮我搜|查资料|百度一下",
    re.I)

FILE_CREATE_FAST_RE = re.compile(
    # 原 app.py:2763 触发正则原文（保留）+ "保存成/存为 txt" 类口语
    r"(创建|新建|写|生成|建).{0,12}(文件|txt|md)|"
    r"(保存|存)(成|为).{0,10}(文件|txt|md|文档)", re.I)

FILE_READ_FAST_RE = re.compile(
    r"(读取|读一下|读下|打开).{0,10}(文件|txt|md)|文件.{0,6}(内容|读一下)", re.I)

FILE_DELETE_FAST_RE = re.compile(
    r"(删除|删掉).{0,10}(文件|txt|md)|把.{0,10}文件删", re.I)

SCREEN_FAST_RE = re.compile(
    # 原 app.py:2799 双条件正则原文（动词+屏幕物项都必须在场），补"看下/截个屏"
    r"(?=(?:.|\n)*(看看|看一下|瞧一?下|看下|瞧瞧|识别|读一下|截屏|截个屏))"
    r"(?=(?:.|\n)*(屏幕|显示屏|画面))", re.I)

CAMERA_FAST_RE = re.compile(
    # 与 SCREEN 同构的双条件（动词+摄像物项都必须在场）——"看一下屏幕"
    # 不会误中，"看下摄像头/镜头里有什么"才进本概念
    r"(?=(?:.|\n)*(看看|看一下|看一眼|瞧一?下|瞧一眼|识别|读一下))"
    r"(?=(?:.|\n)*(摄像头|相机|镜头))", re.I)

CONNECT_FAST_RE = re.compile(
    # 原 app.py 世界接入会话块正则原文，2026-09-21 降级为概念快速入口：
    # 识别"进入世界"这一动作语义，参数裁决（端口）在 action_resolver，
    # 状态迁移在 minecraft.session —— app 不再持有这个字符串表。
    r"进入.{0,4}(minecraft|我的世界|世界)|进我的世界"
    r"|进来.{0,4}(minecraft|我的世界|世界)"
    r"|(连|联|接).{0,3}(minecraft|我的世界)"
    r"|(玩|来玩|陪我玩).{0,3}(minecraft|我的世界|mc)"
    r"|上游戏|进游戏", re.I)

ACTION_CONCEPTS: dict[str, ActionConcept] = {}

_CONCEPT_LIST = [
    # ── Minecraft 具身域（执行器=ActionManager action_type）──
    ActionConcept(
        "FOLLOW", "跟随", "minecraft", "follow_entity",
        description="跟着某人移动，保持在对方身边；陪伴行走、随行、到我身边跟着走。"
                    "动作概念 跟随 FOLLOW",
        fast_patterns=_mc("follow"),
        seed_expressions=["陪我走一会儿", "陪我走", "你陪我一会儿", "跟紧我",
                          "一直跟着我", "在我附近待着", "我走哪你跟哪",
                          "一直在我身边"],
        required_params=["target"], optional_params=["duration"],
        negation="stop"),
    ActionConcept(
        "APPROACH", "靠近", "minecraft", "navigate_to_entity",
        description="朝用户当前位置走过来、靠近用户、到用户这边。"
                    "动作概念 靠近 APPROACH",
        fast_patterns=_mc("approach"),
        seed_expressions=["到我旁边来", "来我这边", "到我这边来", "到我这里来",
                          "走过来", "到我身边来", "往我这边靠", "到我跟前来"],
        required_params=["target"], negation="stop"),
    ActionConcept(
        "STOP", "停止", "minecraft", "stop",
        description="停止当前动作、原地待命、不再跟随、取消正在做的事。"
                    "动作概念 停止 STOP",
        fast_patterns=_mc("stop"),
        # 注意：seed 表达本身不能含否定词（"别再动了"会被否定优先路径拦下，
        # 永远走不到语义层）；停止语义用不含否定词的说法做种子。
        seed_expressions=["停下来", "原地不动", "先停", "算了停下",
                          "到此为止", "就待在这"],
        negation="none"),
    ActionConcept(
        "JUMP", "跳跃", "minecraft", "jump",
        description="跳一下、蹦一下。动作概念 跳跃 JUMP",
        fast_patterns=_mc("jump"),
        seed_expressions=["跳", "跳起来", "蹦一下"],
        negation="stop"),
    ActionConcept(
        "MOVE", "移动", "minecraft", "go_direction",
        description="朝一个方向移动：向前/向后/向左/向右走若干秒。"
                    "动作概念 移动 MOVE",
        fast_patterns=_mc("move"),
        seed_expressions=["往前走", "向后走", "往左走", "往右走", "向前走几步"],
        required_params=["direction"], optional_params=["duration"],
        negation="stop"),
    ActionConcept(
        "MINE", "挖掘", "minecraft", "gather_resource",
        description="挖某个方块/矿石：挖掘、采集、收集资源。"
                    "动作概念 挖掘 MINE",
        fast_patterns=_mc("dig"),
        seed_expressions=["挖掉", "帮我挖", "采集一些", "收一些"],
        required_params=["target"], optional_params=["quantity"],
        negation="refuse"),
    ActionConcept(
        "ATTACK", "攻击", "minecraft", "attack_entity",
        description="攻击用户点名的生物：打/杀/干掉某个目标。"
                    "动作概念 攻击 ATTACK",
        fast_patterns=_mc("attack"),
        seed_expressions=["帮我打", "去打"],
        required_params=["target"],
        negation="refuse", dangerous=True),
    ActionConcept(
        "CONNECT", "进入世界", "minecraft", "inline:mc_session",
        description="连接并进入用户的 Minecraft 世界：用户在局域网开放后给出"
                    "端口号，或复用上次的端口；缺端口时向用户索取而不是猜"
                    "（槽位待填的事实记在图谱节点 Minecraft会话 上）。"
                    "动作概念 进入世界 CONNECT",
        fast_patterns=[CONNECT_FAST_RE],
        seed_expressions=["来玩我的世界", "一起玩mc", "到我世界里来", "连一下服务器",
                          "进我的世界", "上游戏", "进游戏", "接入那个存档",
                          "陪我玩我的世界"],
        required_params=["port"], askable_params=["port"],
        negation="refuse", fast_via_concept=True),

    # ── 网络搜索域（inline 执行器：现状在 app 管线内）──
    ActionConcept(
        "SEARCH", "网络搜索", "web", "inline:web_search",
        description="联网搜索图谱未覆盖的外部信息，查资料、上网查、"
                    "帮我在网上找找看。动作概念 网络搜索 SEARCH",
        node_id="网络搜索",            # 复用现有 procedural 能力节点（§三：不强行重命名）
        fast_patterns=[SEARCH_FAST_RE],
        seed_expressions=["帮我查一下", "查查这个东西", "上网看看", "帮我在网上找找",
                          "查一下资料", "网上找一下"],
        required_params=["query"], negation="refuse"),

    # ── 文件操作域 ──
    ActionConcept(
        "FILE_CREATE", "创建文件", "file", "inline:file_create",
        description="在本地创建并写入文本文件，保存内容到 txt/md。"
                    "动作概念 创建文件 FILE_CREATE",
        node_id="文件操作",            # 复用现有 procedural 能力节点
        fast_patterns=[FILE_CREATE_FAST_RE],
        seed_expressions=["新建一个文件", "帮我写个文件", "创建一个txt文件",
                          "把这个保存成txt", "帮我建个md"],
        optional_params=["filename", "content", "dir"], negation="refuse"),
    ActionConcept(
        "FILE_READ", "读取文件", "file", "",   # 能理解，暂不支持执行（如实拒绝）
        description="读取本地文件的内容。动作概念 读取文件 FILE_READ",
        fast_patterns=[FILE_READ_FAST_RE],
        seed_expressions=["读一下这个文件", "把文件打开看看", "看看文件内容"],
        optional_params=["filename"], negation="refuse"),
    ActionConcept(
        "FILE_DELETE", "删除文件", "file", "",  # 危险 + 暂不支持
        description="删除本地文件。动作概念 删除文件 FILE_DELETE",
        fast_patterns=[FILE_DELETE_FAST_RE],
        seed_expressions=["把这个文件删了", "删除那个txt"],
        optional_params=["filename"], negation="refuse",
        dangerous=True, confirm_required=True),

    # ── 屏幕感知域 ──
    ActionConcept(
        "SCREEN_OBSERVE", "看屏幕", "screen", "screen_observe",
        # 2026-09-22：原 "inline:screen_observe"（只有对话管线的内联执行块，
        # capability_graph 排除 inline → 自主永远到不了）。executor 名 =
        # action_type，由 eye/observer.ScreenObserver 实现，经
        # EmbodimentRouter 挂在具身接口上——对话与自主共用同一执行链。
        description="截屏识别屏幕上的文字与方位，看看画面/屏幕上有什么。"
                    "动作概念 看屏幕 SCREEN_OBSERVE",
        node_id="看屏幕",              # 复用现有 procedural 能力节点
        fast_patterns=[SCREEN_FAST_RE],
        seed_expressions=["看下屏幕", "看下画面", "你看一下屏幕", "帮我看看屏幕",
                          "看看现在是什么情况", "看下现在画面", "屏幕现在显示什么",
                          "截个屏看看", "你帮我看下现在画面"],
        optional_params=["target"], negation="refuse"),

    # ── 摄像头感知域（2026-10-01，移植 The Institute Eyes）──
    ActionConcept(
        "CAMERA_OBSERVE", "看摄像头", "camera", "camera_observe",
        # executor 名 = action_type，由 eye/observer.CameraObserver 实现，
        # 经 EmbodimentRouter 挂在具身接口上——对话与自主共用同一执行链
        # （与 SCREEN_OBSERVE 同构）。
        description="看一眼电脑摄像头，识别画面里有几个人、正在做什么"
                    "（姿态/行为判定：挥拳、摔倒、斗殴等）。"
                    "动作概念 看摄像头 CAMERA_OBSERVE",
        node_id="看摄像头",            # 新概念节点（ensure_action_concepts 建）
        fast_patterns=[CAMERA_FAST_RE],
        seed_expressions=["看下摄像头", "打开摄像头看看", "你看一下摄像头",
                          "帮我看看摄像头", "摄像头里有什么", "看看镜头",
                          "摄像头现在拍到什么", "用摄像头看看现在的情况"],
        optional_params=["camera_index"], negation="refuse"),
]

for _c in _CONCEPT_LIST:
    ACTION_CONCEPTS[_c.concept_id] = _c

# MC 反射动作名 → 概念 id（minecraft.reflex 输出进概念层的桥）
REFLEX_ACTION_TO_CONCEPT = {
    "follow": "FOLLOW", "approach": "APPROACH", "stop": "STOP",
    "jump": "JUMP", "move": "MOVE", "dig": "MINE", "attack": "ATTACK",
}
CONCEPT_TO_REFLEX_ACTION = {v: k for k, v in REFLEX_ACTION_TO_CONCEPT.items()}

MC_DOMAIN_CONCEPTS = [c.concept_id for c in _CONCEPT_LIST
                      if c.domain == "minecraft"]
DESKTOP_DOMAIN_CONCEPTS = [c.concept_id for c in _CONCEPT_LIST
                           if c.domain != "minecraft"]

# ActionManager action_type（=概念 executor 名）→ 概念 id 反查
# （行动重构 2026-09-20）：spec 借此补 concept 字段，具身行动的人格
# 学习从"12 项粗折叠表"升级到概念级；executor 为 inline:/空（能力缺口）
# 的概念不在表中，反查不到即自然回落旧映射兜底。
EXECUTOR_TO_CONCEPT = {
    c.executor: c.concept_id for c in _CONCEPT_LIST
    if c.executor and not c.executor.startswith("inline:")}

# 否定语义常量
POLARITY_POSITIVE = "POSITIVE"
POLARITY_NEGATIVE = "NEGATIVE"


def concept_node_id(c: ActionConcept) -> str:
    """概念对应的图谱节点 id（复用现有中文节点时返回其 id）。"""
    return c.node_id or c.concept_id


def concepts_for_domain(domain: str) -> list:
    """domain: minecraft | desktop（web+file+screen）| 具体域名单值。"""
    if domain == "desktop":
        ids = DESKTOP_DOMAIN_CONCEPTS
    elif domain == "minecraft":
        ids = MC_DOMAIN_CONCEPTS
    else:
        ids = [c.concept_id for c in _CONCEPT_LIST if c.domain == domain]
    return [ACTION_CONCEPTS[i] for i in ids]


# ── 图谱种子（幂等）──────────────────────────────────────────

def ensure_action_concepts(kg, engine=None) -> dict:
    """把 Action Concept 与其表达方式边种进图谱（幂等，启动时调用）。

    可见性（回归修复 2026-09-19，重要）：概念节点与表达节点是
    **引擎内部认知结构**，不是要出现在激活区/回答区/前端图里的知识——
    label=procedural/infrastructure 且**不带 self_capability**，因此
    is_cognitive_visible=False：FAISS 仍然索引它们（build 全量编码），
    action_resolver 的语义召回照常命中；但普通对话的召回注入、扩散、
    前端"高激活节点"视图都不再被这些短口语节点污染。
    （初版误把新概念按能力节点 self_capability=True 建图，导致 MINE/ATTACK
    等随普通聊天被点亮——本函数的自愈分支会把旧状态修回来。）

    复用节点（网络搜索/文件操作/看屏幕）：只补 type=action_concept、
    concept_id，不动其原有可见性（它们是改造前就存在的合法能力节点）。
    """
    stats = {"concepts_new": 0, "concepts_updated": 0,
             "expressions_new": 0, "edges_new": 0, "healed": 0}
    with kg._lock:
        for c in _CONCEPT_LIST:
            nid = concept_node_id(c)
            node = kg.nodes.get(nid)
            concept_meta = {
                "type": "action_concept",
                "concept_id": c.concept_id,
                "domain": c.domain,
                "executor": c.executor,
                "negation": c.negation,
                "dangerous": c.dangerous,
            }
            is_reuse = bool(c.node_id)
            if node is None:
                node = Node(id=nid, weight=0.5, label="procedural",
                            graph_space="semantic",
                            extra_attrs={
                                "category": "action_concept",
                                "name_zh": c.name_zh,
                                "description": c.description,
                                "tags": [c.name_zh],
                                **concept_meta})
                kg.add_node(node)
                stats["concepts_new"] += 1
                logger.info(f"[ActionConcept] + 概念节点: {nid}")
            else:
                node.extra_attrs.update(concept_meta)
                if not is_reuse:
                    # 自愈：初版把新概念建成可见能力节点 → 收回零件身份
                    if node.extra_attrs.pop("self_capability", None) is not None:
                        node.activation = 0.0
                        stats["healed"] += 1
                    elif node.extra_attrs.get("type") == "action_concept" \
                            and float(getattr(node, "activation", 0) or 0) > 0:
                        node.activation = 0.0   # 清除误点亮残留
                stats["concepts_updated"] += 1
            # Self 能 → 概念（复用节点可能已接线；幂等）
            if "Self" in kg.nodes and not kg.get_edge("Self", nid, "能"):
                kg.add_edge(Edge(src="Self", dst=nid, relation="能", weight=0.9,
                                 relation_category="procedural_relation"))
                stats["edges_new"] += 1
            # 概念 实现于 → 具身/执行载体
            impl = None
            if c.domain == "minecraft" and "进入Minecraft世界" in kg.nodes:
                impl = "进入Minecraft世界"
            elif c.concept_id in ("FILE_READ", "FILE_DELETE") \
                    and "文件操作" in kg.nodes:
                impl = "文件操作"
            if impl and not kg.get_edge(nid, impl, "实现于"):
                kg.add_edge(Edge(src=nid, dst=impl, relation="实现于",
                                 weight=0.8,
                                 relation_category="procedural_relation"))
                stats["edges_new"] += 1

            # 表达节点 + 表达方式边
            # label=infrastructure：引擎零件，不参与认知召回注入/回答区/
            # 前端高激活视图（resolver 经 FAISS+ConceptIndex 照常可用）
            for expr in c.seed_expressions:
                en = kg.nodes.get(expr)
                if en is None:
                    kg.add_node(Node(
                        id=expr, weight=0.3, label="infrastructure",
                        extra_attrs={"type": "action_expression",
                                     "concept_id": c.concept_id,
                                     "created": now_str()}))
                    stats["expressions_new"] += 1
                else:
                    _t = (en.extra_attrs or {}).get("type")
                    if _t == "action_expression":
                        # 自愈：初版把表达节点建成了可见知识节点
                        if en.label != "infrastructure":
                            en.label = "infrastructure"
                            en.activation = 0.0
                            stats["healed"] += 1
                    else:
                        # 与既有知识/概念节点重名：让位，不建边、不劫持语义
                        continue
                if not kg.get_edge(expr, nid, "表达方式"):
                    kg.add_edge(Edge(src=expr, dst=nid, relation="表达方式",
                                     weight=0.85,
                                     relation_category="semantic_relation"))
                    stats["edges_new"] += 1

    if engine is not None:
        # 不注册进引擎名表（回归修复 2026-09-19）：概念/表达节点是解析器
        # 内部结构，若进 name_to_node，普通聊天里的同名短语会被
        # _fuzzy_match_node 精确命中 → 种子激活 → 出现在前端激活视图。
        # resolver 的召回走 FAISS 全量索引 + ConceptIndex，不依赖名表。
        # 自愈：初版注册过 → 从名表摘除（复用能力节点 网络搜索/文件操作/
        # 看屏幕 是原有名表成员，不摘）。
        for c in _CONCEPT_LIST:
            if not c.node_id:   # 新建概念节点（复用节点不摘）
                node = kg.nodes.get(c.concept_id)
                if (node is not None
                        and engine.name_to_node.get(c.concept_id) is node):
                    del engine.name_to_node[c.concept_id]
            # 表达节点一律摘除（不论所属概念是否复用）
            for expr in c.seed_expressions:
                en = kg.nodes.get(expr)
                if (en is not None
                        and (en.extra_attrs or {}).get("type") == "action_expression"
                        and engine.name_to_node.get(expr) is en):
                    del engine.name_to_node[expr]

    if (stats["concepts_new"] or stats["expressions_new"]
            or stats["edges_new"] or stats["healed"]):
        logger.info(f"[ActionConcept] 图谱种子: {stats}")
    return stats

# 收尾清理 2026-09-22：删除零引用的 concept_for_node()（全仓无任何调用；
# 概念↔节点映射由 ensure_action_concepts 的种子里程序与 capability_graph 承担）。
