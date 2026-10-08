# graph_model.py — 知识图谱数据模型
# ============================================================================
# Fascinator 的核心数据结构层。
#
# 提供三个核心类：
#   Node          — 图谱节点（实体/概念/程序性动作）
#   Edge          — 图谱边（实体间关系/因果关系/层级关系）
#   KnowledgeGraph — 图谱容器（线程安全的增删改查 + JSON 序列化）
#
# 设计原则：
#   1. 统一认知空间：所有节点属于同一图谱，通过 graph_space 区分生命周期与传播动力学
#      - semantic   (语义空间：客观知识，稳定传播)
#      - episodic   (情景空间：个人经历，时间衰减)
#      - cognitive  (认知空间：思维过程，快速注意)
#      - self       (自我空间：长期保持，缓慢变化)
#   2. Node.label 区分记忆类型（与旧系统兼容）：
#      - declarative-semantic  (语义记忆：客观事实)
#      - declarative-episodic  (情景记忆：个人经历)
#      - procedural            (程序性记忆：可执行动作)
#      - self                  (自我节点)
#      - infrastructure        (基础设施节点)
#   3. Edge.relation_category 区分关系类型，支持差异化传播：
#      - semantic_relation / causal_relation / temporal_relation
#      - emotional_relation / social_relation
#      - cognitive_relation / procedural_relation
#   4. 激活值分离：weight 是静态先验重要性，activation 是运行时动态值
#   5. 线程安全：所有修改操作均使用 threading.RLock 保护
#   6. JSON 持久化：图谱可完整导出/导入，支持版本控制和备份
#   7. graph_space 决定生命周期与传播策略，但不限制跨空间连接
# ============================================================================

from datetime import datetime
import itertools
import json
import logging
import threading
import os
import shutil
import tempfile

logger = logging.getLogger(__name__)


def _schema():
    """lazy import graph_schema（它只 import config，无循环）。

    归一漏斗的唯一目的：图里只存规范原子关系。写（add/update）与读
    （get/remove）双向归一——写入方传旧表面形式（如"名字是"），读取方用
    规范词（"名字叫"）也能命中，反之亦然。
    """
    global _graph_schema
    try:
        return _graph_schema
    except NameError:
        import graph_schema as _gs
        _graph_schema = _gs
        return _gs


def _norm_rel(relation) -> str:
    return _schema().normalize_relation(relation)

# 模块级单调序号：节点/边的稳定 tie-break 基准（不序列化）。
# 语义约定：_seq 越小 = 越早入图 = kg.edges/kg.nodes 列表序越靠前；
# rebuild_indexes() 会按当前列表序重排全部 _seq，保持序号与列表序一致。
_SEQ = itertools.count()

def now_str():
    """
    获取当前时间的格式化字符串。
    格式：YYYY/MM/DD HH:MM:SS
    用于节点和边的 created / last_access 时间戳。
    """
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


# 未知对象节点前缀（具身感知的"图外新东西"标记）
UNKNOWN_PREFIXES = ("UnknownEntity_", "UnknownBlock_", "UnknownPlayer_",
                    "UnknownObject_", "UnknownConcept_")


def is_live_unknown(node) -> bool:
    """节点是否仍是"活着的未知对象"（新颖性信号）。

    经验已把它认出来的（extra_attrs["retired"] 有值）**不再算数**：
    节点保留（历史与激活链不删），但它不该再抬好奇心地板、再进
    unknown_objects 计数、再把候选新颖性钉满——那正是"好奇永不饱食、
    每次遇到 cow 都当第一次见"的病灶。消费点统一走这里判定。
    """
    if node is None:
        return False
    nid = str(getattr(node, "id", "") or "")
    ea = getattr(node, "extra_attrs", None) or {}
    if not nid.startswith(UNKNOWN_PREFIXES) and ea.get("type") != "unknown":
        return False
    return not ea.get("retired")


def is_live_unknown_id(kg, nid) -> bool:
    """按节点 id 判定"图上有活着的未知标记"。

    节点**不存在** = 图上没有这个未知标记 = False（保守向：世界里有只
    cow 不等于图认为她没见过 cow——标记只能由感知层建立；
    test_minecraft.embodiment 钉过这条边界）。
    """
    nid = str(nid or "")
    if not nid.startswith(UNKNOWN_PREFIXES):
        return False
    node = kg.nodes.get(nid) if kg is not None else None
    return False if node is None else is_live_unknown(node)


# 有效的节点标签枚举
# procedural: 程序性记忆节点，其 execution 字段包含可执行 Python 代码
# declarative-episodic: 情景记忆节点，关联特定时间/地点的个人经历
# declarative-semantic: 语义记忆节点，脱离时空的客观事实/概念
# self: 自我节点，认知图谱的聚合中心（Everything is Graph 核心）
# infrastructure: 基础设施节点，默认不参与认知扩散，仅由程序调用
# disposition: 行为倾向词表节点（行为:/情境:/倾向:*）——引擎零件，不进回答区
# intention: 交流意图节点（CI_*）——持续认知循环的表达候选
VALID_LABELS = [
    "declarative-episodic",
    "declarative-semantic",
    "procedural",
    "self",
    "infrastructure",
    "disposition",
    "intention",
]

# ── 认知空间类型（Architecture Refactor 0.1） ──
# graph_space 决定节点的生命周期、遗忘策略与扩散策略。
# 不限制是否可以连接——跨空间联想是 FAS 的核心能力。
# semantic:   客观知识，正常传播，稳定衰减
# episodic:   经历/事件，快速衰减，时间敏感
# cognitive:  认知过程/思维，快速形成注意，中等衰减
# self:       自我相关，长期保持，缓慢传播
GRAPH_SPACES = ("semantic", "episodic", "cognitive", "self")
DEFAULT_GRAPH_SPACE = "semantic"


# ─ 引擎零件 label ──
# 这三类节点是引擎自己的零件，不是认知内容：基础设施（LLM回答触发节点）、
# 程序性（按键编译产物 按下W、动作节点）、disposition（行为倾向）。
# 单一真源：is_cognitive_visible 与三个下游模块（continuous_cognition 的认知脉冲、
# dialogue_decision 的回应欲望、cognition_modes 的注意力块）都靠它排除零件。
# 历史上这三处各自抄了一份字面量，改一处要同步三处——统一到这里。
ENGINE_PART_LABELS = ("infrastructure", "procedural", "disposition")


def is_cognitive_visible(node) -> bool:
    """节点是否作为"认知内容"参与语义召回与回答上下文。

    图里同时住着两类东西：
      - 认知内容：知识、经历、自认知能力（"我能做什么"）
      - 引擎零件：LLM回答触发节点、按键编译产物（按下W）、执行留痕（搜索记录_N）
    只有前者该进激活区与回答区——把零件喂给语言层等于让她对着自己的齿轮说话。

    判据取自节点自身属性，不硬编码节点名：
      self_capability=True → 自认知能力，可见（能力节点由 app 启动块标记）
      label ∉ {infrastructure, procedural, disposition} → 知识/情绪节点，可见
      其余 → 引擎零件，不可见

    Note: 能力节点多为 procedural（它们由动作执行器实现），因此标记优先于 label。
    """
    attrs = getattr(node, "extra_attrs", {}) or {}
    if attrs.get("self_capability"):
        return True
    return getattr(node, "label", "") not in ENGINE_PART_LABELS

# ── 关系类别（Architecture Refactor 0.1；episodic_relation 增于架构对齐 2026-09-19）──
# relation_category 决定边的传播方向、衰减倍率与激活增益。
# 与 config.relation_categories 保持同集（此处为运行时唯一校验源）。
RELATION_CATEGORIES = (
    "semantic_relation",    # 语义关系：属于、包含、位于
    "episodic_relation",    # 经历关系：参与、经历、观察（事件论元）
    "causal_relation",      # 因果关系：导致、引发、抑制
    "temporal_relation",    # 时间关系：之前、之后、发生于
    "emotional_relation",   # 情绪关系：喜欢、感受、引发(情绪)
    "social_relation",      # 社交关系：感谢、问候、道别
    "cognitive_relation",   # 认知关系：思考、关联、好奇触发
    "procedural_relation",  # 程序关系：触发、执行、绑定
)
DEFAULT_RELATION_CATEGORY = "semantic_relation"

# ──────────────────────────────────────────────
# Node 节点
# ──────────────────────────────────────────────
class Node:
    def __init__(
        self,
        id: str,
        weight: float = 0.0,
        activation: float = 0.0,
        label: str = "declarative-semantic",
        graph_space: str = "semantic",     # Architecture Refactor 0.1: 认知空间
        execution: str = None,             # 程序性记忆节点的执行代码字符串
        confidence: float = 0.5,           # 知识置信度 0~1（Everything is Graph 核心）
        extra_attrs: dict = None,
        created: str = None,
        last_access: str = None
    ):
        self.id = str(id).strip()
        self.weight = float(max(-2.0, min(2.0, weight)))
        self.activation = float(max(0.0, activation))
        # 严格校验（架构对齐 2026-09-19）：非法 label/space 不再静默改写。
        # 静默改写的代价曾经很实际——"disposition" 被改写成
        # declarative-semantic 后，ENGINE_PART_LABELS 的零件过滤永远打不中，
        # 行为/情境词表节点一直混在回答区里。此处 raise 让接错线在创建点爆炸，
        # 而不是在认知输出端表现为诡异的污染。
        if label not in VALID_LABELS:
            raise ValueError(
                f"非法节点 label={label!r}（id={self.id!r}）。"
                f"合法值: {VALID_LABELS}——如果是新的节点类型，请先在 "
                f"graph_model.VALID_LABELS 注册并说明其记忆功能。")
        self.label = label
        if graph_space not in GRAPH_SPACES:
            raise ValueError(
                f"非法 graph_space={graph_space!r}（id={self.id!r}）。"
                f"合法值: {GRAPH_SPACES}。")
        self.graph_space = graph_space
        self.execution = execution          # None 或 Python 代码字符串
        self.confidence = float(max(0.0, min(1.0, confidence)))
        self.extra_attrs = extra_attrs or {}
        self.created = created or now_str()
        self.last_access = last_access or now_str()

    def touch(self):
        self.last_access = now_str()

    def to_dict(self):
        return {
            "id": self.id,
            "weight": round(self.weight, 6),
            "activation": round(self.activation, 6),
            "label": self.label,
            "graph_space": self.graph_space,
            "execution": self.execution,
            "confidence": round(self.confidence, 4),
            "extra_attrs": self.extra_attrs,
            "created": self.created,
            "last_access": self.last_access
        }

    @classmethod
    def from_dict(cls, d: dict):
        # 持久化数据兼容：磁盘上的旧节点若带非法 label/space（历史静默改写
        # 之前的产物），加载时降级为警告+改写而不是 raise——启动不能被脏数据炸掉。
        try:
            return cls(
                id=d["id"],
                weight=d.get("weight", 0.0),
                activation=d.get("activation", 0.0),
                label=d.get("label", "declarative-semantic"),
                graph_space=d.get("graph_space", "semantic"),
                execution=d.get("execution"),
                confidence=d.get("confidence", 0.5),
                extra_attrs=d.get("extra_attrs", {}),
                created=d.get("created"),
                last_access=d.get("last_access")
            )
        except ValueError as e:
            logger.warning(f"[图谱] 节点 {d.get('id')!r} 元数据非法，已降级处理: {e}")
            node = cls(
                id=d["id"],
                weight=d.get("weight", 0.0),
                activation=d.get("activation", 0.0),
                label="declarative-semantic",
                graph_space="semantic",
                execution=d.get("execution"),
                confidence=d.get("confidence", 0.5),
                extra_attrs=d.get("extra_attrs", {}),
                created=d.get("created"),
                last_access=d.get("last_access")
            )
            return node


# ──────────────────────────────────────────────
# Edge 边
# ──────────────────────────────────────────────
class Edge:
    def __init__(
        self,
        src: str,
        dst: str,
        relation: str,
        weight: float = 0.5,
        activation: float = 0.0,              # 运行时激活度
        relation_category: str = "semantic_relation",  # Architecture Refactor 0.1: 关系类别
        created: str = None,
        last_access: str = None,
        extra_attrs: dict = None
    ):
        self.src = str(src).strip()
        self.dst = str(dst).strip()
        self.relation = str(relation).strip()
        self.weight = float(max(-2.0, min(2.0, weight)))
        self.activation = float(max(0.0, activation))
        self.relation_category = relation_category if relation_category in RELATION_CATEGORIES else DEFAULT_RELATION_CATEGORY
        # 学习实验（2026-09-25）：边也需要 provenance/结构化属性
        # （配方原料数量、知识来源、版本核验标记）——与 Node.extra_attrs
        # 同一约定；老存档缺键 → {}，序列化往返无损。
        self.extra_attrs = extra_attrs or {}
        self.created = created or now_str()
        self.last_access = last_access or now_str()
        self._seq = next(_SEQ)              # 稳定序号（不序列化；rebuild 重排）

    def touch(self):
        self.last_access = now_str()

    def to_dict(self):
        return {
            "src": self.src,
            "dst": self.dst,
            "relation": self.relation,
            "weight": round(self.weight, 6),
            "activation": round(self.activation, 6),
            "relation_category": self.relation_category,
            "created": self.created,
            "last_access": self.last_access,
            "extra_attrs": self.extra_attrs,
        }

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            src=d["src"],
            dst=d["dst"],
            relation=d["relation"],
            weight=d.get("weight", 0.5),
            activation=d.get("activation", 0.0),
            relation_category=d.get("relation_category", "semantic_relation"),
            created=d.get("created"),
            last_access=d.get("last_access"),
            extra_attrs=d.get("extra_attrs") or {},
        )


# ──────────────────────────────────────────────
# KnowledgeGraph 知识图谱
# ──────────────────────────────────────────────
class KnowledgeGraph:
    def __init__(self):
        self.nodes: dict[str, Node] = {}     # id → Node
        self.edges: list[Edge] = []
        self._lock = threading.RLock()

        # ---- 邻接索引（派生缓存，Architecture 性能改造 2026-09）----
        # 不变量：kg.edges 列表是唯一真相；三个索引与列表语义逐条一致
        #（含重复边与 dangling 边）。所有结构变更必须走本类方法；
        # 批量直写（启动 overlay / 迁移脚本 / pack 导入）之后调用
        # rebuild_indexes() 重建。
        self._out_index: dict[str, list] = {}     # src → [Edge]（列表序，允许重复）
        self._in_index: dict[str, list] = {}      # dst → [Edge]
        self._pair_index: dict[tuple, dict] = {}  # (src,dst) → {relation: Edge}（first-wins = 列表序首条）
        self._node_order: dict[str, int] = {}     # 节点稳定序（插入序），top-k/队列 tie-break 用

        # ---- 结构变更回调（默认空 = 零开销；diffusion_engine 订阅）----
        self._cb_node_renamed: list = []   # fn(old_id, new_id)
        self._cb_edge_removed: list = []   # fn(edge)
        self._cb_node_removed: list = []   # fn(node_id)
        self._cb_node_added: list = []     # fn(node_id)：新节点诞生（索引跟随用）

    # ---- 派生索引维护 ----

    def _index_edge(self, edge: Edge):
        """把一条边挂入三个邻接索引（first-wins 匹配 get_edge 列表序首条语义）。"""
        self._out_index.setdefault(edge.src, []).append(edge)
        self._in_index.setdefault(edge.dst, []).append(edge)
        inner = self._pair_index.setdefault((edge.src, edge.dst), {})
        if edge.relation not in inner:
            inner[edge.relation] = edge

    def _drop_edge_from_indexes(self, edge: Edge):
        """按对象身份把一条边从三个索引摘除（Edge 无 __eq__，== 即身份）。"""
        bucket = self._out_index.get(edge.src)
        if bucket and edge in bucket:
            bucket.remove(edge)
            if not bucket:
                del self._out_index[edge.src]
        bucket = self._in_index.get(edge.dst)
        if bucket and edge in bucket:
            bucket.remove(edge)
            if not bucket:
                del self._in_index[edge.dst]
        inner = self._pair_index.get((edge.src, edge.dst))
        if inner and inner.get(edge.relation) is edge:
            del inner[edge.relation]
            if not inner:
                del self._pair_index[(edge.src, edge.dst)]

    def rebuild_indexes(self):
        """全量重建派生索引（启动 overlay / 迁移 / pack 导入等批量直写之后调用）。

        同时按当前列表序重排 _seq 与 _node_order，恢复"序号序 == 列表序"不变量。
        """
        with self._lock:
            self._out_index = {}
            self._in_index = {}
            self._pair_index = {}
            self._node_order = {}
            for order, nid in enumerate(self.nodes):
                self._node_order[nid] = order
            for seq, e in enumerate(self.edges):
                e._seq = seq
                self._index_edge(e)

    # ---- 节点操作 ----

    def add_node(self, node: Node, source: str = None) -> bool:
        """source 仅用于观测/演化留痕（"这个节点为什么在图里"），
        不写入节点内容，不参与任何认知计算。"""
        with self._lock:
            if node.id in self.nodes:
                return False
            self.nodes[node.id] = node
            self._node_order[node.id] = next(_SEQ)
            self._note_evo("node_added", "node", node.id,
                           {"label": node.label, "weight": node.weight,
                            "source": source
                            or (node.extra_attrs or {}).get("source")})
            for fn in self._cb_node_added:
                try:
                    fn(node.id)
                except Exception:
                    pass
            return True

    def upsert_node(self, node: Node, source: str = None):
        """插入或覆盖节点（不更新激活度）。source 仅观测留痕。"""
        _src = source or (node.extra_attrs or {}).get("source")
        with self._lock:
            if node.id in self.nodes:
                existing = self.nodes[node.id]
                existing.weight = node.weight
                existing.label = node.label
                existing.graph_space = getattr(node, 'graph_space', existing.graph_space)
                if node.execution is not None:
                    existing.execution = node.execution
                existing.confidence = getattr(node, 'confidence', existing.confidence)
                existing.extra_attrs.update(node.extra_attrs)
                existing.touch()
                self._note_evo("node_updated", "node", node.id,
                               {"label": node.label, "weight": node.weight,
                                "source": _src})
            else:
                self.nodes[node.id] = node
                self._node_order[node.id] = next(_SEQ)
                self._note_evo("node_added", "node", node.id,
                               {"label": node.label, "weight": node.weight,
                                "source": _src})
                for fn in self._cb_node_added:
                    try:
                        fn(node.id)
                    except Exception:
                        pass

    def remove_node(self, node_id: str) -> bool:
        with self._lock:
            if node_id not in self.nodes:
                return False
            # 先按索引精准清边（O(deg)），再摘节点
            incident = (self._out_index.get(node_id, [])
                        + self._in_index.get(node_id, []))
            for e in incident:
                self._drop_edge_from_indexes(e)
            del self.nodes[node_id]
            self._out_index.pop(node_id, None)
            self._in_index.pop(node_id, None)
            self._node_order.pop(node_id, None)
            self.edges = [e for e in self.edges
                          if e.src != node_id and e.dst != node_id]
            for fn in self._cb_node_removed:
                try:
                    fn(node_id)
                except Exception:
                    pass
            self._note_evo("node_removed", "node", node_id)
            return True

    def get_node(self, node_id: str):
        return self.nodes.get(node_id)

    # ---- 边操作 ----

    def add_edge(self, edge: Edge, force_weight: bool = False) -> bool:
        with self._lock:
            if edge.src not in self.nodes or edge.dst not in self.nodes:
                # PIN-11 留痕:主写图路径(202 处调用)大多不检查返回值,
                # 端点缺失本来静默丢边;warning 化让摄入侧漏斗的错误可见
                # (调用方已自查返回值的 graph_expansion.py 会在收到 False 时
                # 再计一次 rejected——那是有意重复,双留痕可接受)。
                logger.warning(
                    f"[图谱] add_edge 被丢弃(端点缺失): {edge.src} "
                    f"-[{edge.relation}]-> {edge.dst}")
                return False
            # ── 关系归一漏斗（架构对齐 2026-09-19）──
            # 只允许规范原子关系入图；同义表面形式就地归一（mutate 传入对象，
            # 调用方后续引用同一对象时看到的也是规范词）。
            edge.relation = _norm_rel(edge.relation)
            # 类别补全：调用方未显式指定（留默认）时，按规范词表的类别落。
            if edge.relation_category == DEFAULT_RELATION_CATEGORY:
                edge.relation_category = _schema().category_for(edge.relation)
            # 主体守卫：Haru/Self/用户 的直连边必须在白名单内（warning 不阻断）
            _schema().check_hub_edge(edge.src, edge.dst, edge.relation)
            # 规范化后可能撞上已有三元组（如同义变体归一后重合）：
            # 合并权重而不是追加重复边，保持"同 (src,rel,dst) 唯一"的收敛性。
            # force_weight=True（C21 事件框架极性槽位）：权重以本次值为准
            # （覆盖异号）。默认 False 保持既有 max 语义零变化——max 对
            # 异号极性不对称（负权 +0.5 后 −0.8 永远进不来），事件框架的
            # 失败↔成功极性翻转依赖此参数。
            existing = self.get_edge(edge.src, edge.dst, edge.relation)
            if existing is not None:
                if force_weight:
                    existing.weight = float(edge.weight)
                else:
                    existing.weight = max(existing.weight, edge.weight)
                existing.touch()
                self._note_evo("edge_updated", "edge", f"{edge.src}->{edge.dst}",
                               {"src": edge.src, "dst": edge.dst,
                                "relation": edge.relation, "weight": existing.weight})
                return True
            self.edges.append(edge)
            self._index_edge(edge)
            self._note_evo("edge_added", "edge", f"{edge.src}->{edge.dst}",
                           {"src": edge.src, "dst": edge.dst,
                            "relation": edge.relation, "weight": edge.weight})
            return True

    def remove_edge(self, src: str, dst: str, relation: str) -> bool:
        relation = _norm_rel(relation)
        with self._lock:
            kept = []
            removed = False
            for e in self.edges:
                if e.src == src and e.dst == dst and e.relation == relation:
                    self._drop_edge_from_indexes(e)
                    for fn in self._cb_edge_removed:
                        try:
                            fn(e)
                        except Exception:
                            pass
                    removed = True
                else:
                    kept.append(e)
            self.edges = kept
            if removed:
                self._note_evo("edge_removed", "edge", f"{src}->{dst}",
                               {"src": src, "dst": dst, "relation": relation})
            return removed

    def remove_edges(self, src: str = None, dst: str = None, relation: str = None) -> int:
        """批量删边：任一维度传 None 表示通配（如焦点指针按 (src, relation) 清全部旧边）。

        返回删除条数。与 remove_edge 一样清全部匹配项（含重复三元组）。
        """
        relation = _norm_rel(relation) if relation is not None else None
        with self._lock:
            kept = []
            removed = 0
            for e in self.edges:
                if ((src is None or e.src == src)
                        and (dst is None or e.dst == dst)
                        and (relation is None or e.relation == relation)):
                    self._drop_edge_from_indexes(e)
                    for fn in self._cb_edge_removed:
                        try:
                            fn(e)
                        except Exception:
                            pass
                    removed += 1
                else:
                    kept.append(e)
            self.edges = kept
            if removed:
                self._note_evo("edge_removed", "edge",
                               f"{src or '*'}->{dst or '*'}[{relation or '*'}]",
                               {"src": src, "dst": dst, "relation": relation,
                                "count": removed})
            return removed

    def get_edge(self, src: str, dst: str, relation: str = None):
        with self._lock:
            inner = self._pair_index.get((src, dst))
            if not inner:
                return None
            if relation is None:
                # 现状语义：跨 relation 返回列表序第一条 = _seq 最小者
                return min(inner.values(), key=lambda e: e._seq)
            return inner.get(_norm_rel(relation))

    def update_edge(self, src: str, dst: str, relation: str, **kwargs) -> bool:
        with self._lock:
            relation = _norm_rel(relation)
            edge = self.get_edge(src, dst, relation)
            if not edge:
                return False
            if "weight" in kwargs:
                edge.weight = float(max(-2.0, min(2.0, kwargs["weight"])))
            if "new_relation" in kwargs and kwargs["new_relation"] != relation:
                new_relation = _norm_rel(kwargs["new_relation"])
                if new_relation == relation:
                    edge.touch()
                    return True
                # 重挂 pair_index：旧 relation 槽位摘除（仅当指向本边），
                # 新 relation 槽位冲突时保留 _seq 小者（= 列表序在前者）
                inner = self._pair_index.setdefault((src, dst), {})
                if inner.get(relation) is edge:
                    del inner[relation]
                existing = inner.get(new_relation)
                if existing is None or edge._seq < existing._seq:
                    inner[new_relation] = edge
                edge.relation = new_relation
            edge.touch()
            return True

    def get_out_edges(self, node_id: str) -> list:
        with self._lock:
            return list(self._out_index.get(node_id, ()))

    def get_in_edges(self, node_id: str) -> list:
        with self._lock:
            return list(self._in_index.get(node_id, ()))

    def rename_node(self, old_id: str, new_id: str) -> bool:
        """运行时节点改名：dict 键、node.id、关联边端点与全部索引一并迁移。

        唯一安全序列（自环边依赖 out→in 的固定两步，各自只改一端）：
          1) nodes 键迁移 + node.id 改写（pop+重插 = 节点排到插入序末尾）
          2) out_index 桶重挂 → 逐条 e.src = new
          3) in_index 桶重挂 → 逐条 e.dst = new
          4) pair_index 全键重组（冲突取 _seq 小者 = 列表序在前者）
        目标 id 已存在时拒绝（返回 False），不再像旧直写那样静默覆盖。
        """
        with self._lock:
            node = self.nodes.get(old_id)
            if node is None or old_id == new_id or new_id in self.nodes:
                return False
            # 1) 节点
            del self.nodes[old_id]
            node.id = new_id
            self.nodes[new_id] = node
            self._node_order.pop(old_id, None)
            self._node_order[new_id] = next(_SEQ)
            # 2) 出边桶（无出边则不建键，保持"无桶 = 无边"与 rebuild 语义一致）
            bucket = self._out_index.pop(old_id, None)
            if bucket:
                self._out_index[new_id] = bucket
                for e in bucket:
                    if e.src == old_id:
                        e.src = new_id
            # 3) 入边桶
            bucket = self._in_index.pop(old_id, None)
            if bucket:
                self._in_index[new_id] = bucket
                for e in bucket:
                    if e.dst == old_id:
                        e.dst = new_id
            # 4) pair_index 重组（dangling 边同样迁移）
            new_pair = {}
            for (s, d), inner in self._pair_index.items():
                ns = new_id if s == old_id else s
                nd = new_id if d == old_id else d
                slot = new_pair.setdefault((ns, nd), {})
                for rel, e in inner.items():
                    cur = slot.get(rel)
                    if cur is None or e._seq < cur._seq:
                        slot[rel] = e
            self._pair_index = new_pair
            for fn in self._cb_node_renamed:
                try:
                    fn(old_id, new_id)
                except Exception:
                    pass
            return True

    # ---- Evolution Log 集成 ----

    def _note_evo(self, change_type: str, target_type: str,
                  target_id: str, details: dict = None):
        """Lazy import to avoid circular dependency."""
        try:
            from graph_evolution_log import get_evolution_log, ChangeType
            evo = get_evolution_log()
            ct_map = {
                "node_added": ChangeType.NODE_ADDED,
                "node_removed": ChangeType.NODE_REMOVED,
                "node_updated": ChangeType.NODE_UPDATED,
                "edge_added": ChangeType.EDGE_ADDED,
                "edge_removed": ChangeType.EDGE_REMOVED,
                "weight_changed": ChangeType.WEIGHT_CHANGED,
            }
            ct = ct_map.get(change_type, ChangeType.NODE_ADDED)
            evo.record(ct, target_type, target_id, details)
        except Exception:
            pass  # Evolution log is optional
        # ── 观测：图谱结构变更桥接到 fas_log（"这个节点为什么在图里"）──
        # 注意：本函数常在 self._lock 内被调用，绝不回查 kg（避免自锁）；
        # 只用调用方传进来的 details。日志失败静默——不影响图操作。
        try:
            import fas_log
            if fas_log.enabled_for_cognition() and fas_log.is_enabled(fas_log.GRAPH, "INFO"):
                _d = details or {}
                fas_log.emit(
                    fas_log.GRAPH,
                    "DEBUG" if change_type == "node_updated" else "INFO",
                    change_type, f"{target_type} {target_id} {change_type}",
                    target=target_id,
                    source=_d.get("source") or _d.get("reason") or None,
                    label=_d.get("label"), relation=_d.get("relation"),
                    weight=round(float(_d.get("weight", 0) or 0), 3))
        except Exception:
            pass

    # ---- 序列化 ----

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "nodes": [n.to_dict() for n in self.nodes.values()],
                "edges": [e.to_dict() for e in self.edges]
            }

    def save(self, path: str):
        # PERS-01 三重守卫(2/3)：load 失败(文件损坏 JSONDecodeError)的
        # 实例拒绝保存——否则缩小平读不出旧文件(save 侧 json.load 同样抛错)
        # 会跳过备份分支，损坏文件被空图原子覆写且无副本可恢复。
        if getattr(self, "_load_failed", False):
            logger.warning(
                f"[图谱] save 被拒绝:本实例由损坏文件 load 而来(_load_failed)，"
                f"拒绝以空图覆写 {os.path.basename(path)}——请先人工修复源文件。")
            return False
        data = self.to_dict()
        # ── 意外缩小保险（这行不是装饰，是事故教训）──
        # 2026-09-13 我用 `kg = KnowledgeGraph(); kg.load(p)` 写了个迁移脚本——
        # 而 load 是 **classmethod**（返回新实例），那句等于没加载；随后 save()
        # 把内存里的空图原子替换进了运行时图谱。实测损失 894 节点 / 2136 边
        # → 162 节点（那一周的对话记忆全没了，只能从 9/6 备份恢复到 541 节点）。
        # 原子写只保证"不写坏"，不保证"不写空"。所以覆写一个明显更大的旧文件前，
        # 先把它留一份带时间戳的副本（不阻断保存，但损失可回收）。
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    old = json.load(f)
                old_n = len(old.get("nodes", []) or [])
                new_n = len(data.get("nodes", []) or [])
                if old_n >= 50 and new_n < old_n * 0.5:
                    backup = f"{path}.bak.shrink_{now_str().replace(':', '').replace(' ', '_').replace('/', '')}"
                    shutil.copy2(path, backup)
                    logger.warning(
                        f"[图谱] 本次保存节点数 {new_n} 远小于原文件 {old_n}——"
                        f"已留副本 {os.path.basename(backup)}（若这是误操作，可从该副本恢复）")
        except Exception as e:
            # PERS-01 三重守卫(3/3)：旧文件读不出(损坏/半写/被并发改写)——
            # 缩小平拿不到旧基数时不能只 debug 跳过；源文件损坏时必须拒绝保存
            # (它是唯一旧数据，空图覆写=事故)，先留副本再退出。
            try:
                if os.path.exists(path):
                    bak = f"{path}.bak.unreadable_{now_str().replace(':', '').replace(' ', '_').replace('/', '')}"
                    shutil.copy2(path, bak)
                    logger.warning(
                        f"[图谱] 缩小平读不出旧文件({e})，已留副本 "
                        f"{os.path.basename(bak)}；**本次保存已拒绝**——"
                        f"源文件损坏时绝不允许空图/半图覆写，请人工处理。")
                    return False
            except OSError:
                pass
            logger.warning(f"[图谱] 缩小平检查跳过: {e}")
        # 原子替换：temp 文件与目标同目录（Windows 上同卷 os.replace 才是
        # 原子操作），flush+fsync 后 rename——写一半崩溃不会留下损坏 JSON
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix="." + os.path.basename(path) + ".tmp", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp is not None and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    @classmethod
    def load(cls, path: str) -> "KnowledgeGraph":
        kg = cls()
        # 如果文件不存在，直接返回空实例（这是正常的首次运行逻辑）
        if not os.path.exists(path):
            return kg

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for nd in data.get("nodes", []):
                kg.nodes[nd["id"]] = Node.from_dict(nd)
            for ed in data.get("edges", []):
                kg.edges.append(Edge.from_dict(ed))
            kg.rebuild_indexes()
        except json.JSONDecodeError as e:
            # 💡 关键修改：如果文件存在但格式错误，打印警告并返回空实例
            # 这样至少不会覆盖掉旧文件，或者让用户知道文件坏了
            import logging; _gm_logger = logging.getLogger(__name__)
            _gm_logger.warning(f"知识图谱文件损坏 ({e})，将创建新的空图谱。")
            # PERS-01 三重守卫(1/3)：load 失败必须留下显式痕迹——
            # 调用方后续 save() 靠这个标记拒绝保存，防止空图/损坏态覆写
            # 唯一旧数据（2026-09-13 事故同类路径：空图覆写 894→162 节点）。
            kg._load_failed = True
            # 这里可以选择 return kg (创建新图谱) 或者 raise (中断程序)
            # 为了程序健壮性，通常选择创建新图谱，但绝不能静默掩盖错误
        return kg
