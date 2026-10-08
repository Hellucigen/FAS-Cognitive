# diffusion_engine.py - 激活扩散引擎
# ============================================================================
# Fascinator 的核心认知驱动模块。
#
# 理论定位（理论—实现重新对齐 2026-09-19b）：
#
#   Fascinator 的激活扩散是【有限注意资源约束下的认知激活传播机制】，
#   不是图上的物理能量守恒模拟。
#
# 概念层级：
#   activation         节点当前的认知激活程度 / 注意相关程度（认知状态，非守恒量）
#   emission budget    节点本轮可用于传播的注意资源 = activation × 配比；
#                      配比可被认知上下文调制（当前活动/情绪/来源类型，逐步接入）
#   emission transfer  注意资源不无成本复制：向外传播的量从自身资源中扣除——
#                      这是运行时约束，不是对"认知"的本体论定义
#   activation source  本轮激活的来源（external_input/perception/internal_drive/
#                      memory_recall/goal/emotion）。来源的初始激活不属于上一轮
#                      图内资源，因此发射免扣（clear_anchors 在回合边界清空）
#   fire-once          一次 propagation cycle 内至多一次 outward emission——
#                      防止图环重复自激。注意：激活可以持续累积，
#                      受限的只是"向外发射"这个动作
#   relation weight    w̃ = w + bounded(r)：知识关系对传播的调制；运行时增量
#                      有界、且永不回写本体中的静态权重 w
#   inhibition         负权重 = 竞争性抑制（激发与抑制在同一更新规则内竞争）
#   decay              每个传播步的激活消退（step-level，深度因此才有意义）
#
# 实现论文 5.1 节的激活扩散公式：
#   w̃_uv(t) = w_uv + bounded r(u,v,t)
#   Δa(u→v)(t) = a(u,t) · w̃_uv(t) · β_space · gain_relation / Σw̃₊
#   a(v, t+1) = (1-λ) · a(v,t) + Σ Δa(u→v)(t) - inhibition
#
# 核心功能：
#   activate_from_inputs() - 注入激活（external_input 来源，可指定 source_type）
#   register_activation_source() - 登记本轮激活来源（其他注入模块调用）
#   diffuse_step()          - 单步传播（空间/关系感知 + 注意资源约束）
#   diffuse_round()         - 完整传播轮（每步衰减，至收敛或 max_depth）
#   decay_step()            - 空间感知衰减
#   get_topk()              - Top-K 认知焦点
#   execute_action()        - 程序性节点执行（受限沙箱）
#   start_auto()/stop_auto()- 自动扩散线程
# ============================================================================

import threading
import time
import logging
import traceback
import heapq

from graph_model import KnowledgeGraph, Node, Edge, now_str

logger = logging.getLogger(__name__)

# ── 动作代码沙箱：可导入模块白名单 ──
# 为什么需要它：execute_action() 回退路径用 exec() 执行节点 execution 字符串，
# 白名单里若直接放裸 __import__，则一句 __import__("os").system(...) 就能逃逸
# ——内建白名单只挡住了没有 import 能力的路径，挡不住 import 本身。
# 现有动作节点的实际需求只有两个模块（按下W/长按A 等 12 个节点用 ctypes 发
# 扫描码、time 做长按间隔），所以白名单按实需给，不多不少。
# 需要新模块时在这里加，并在 tests/test_action_sandbox.py 里补一条断言。
ALLOWED_ACTION_IMPORTS = frozenset({
    "ctypes",      # 键盘/鼠标扫描码投递（按下W、长按A 等）
    "time",        # 长按间隔、sleep
    "math", "random", "json", "re", "datetime", "collections", "itertools", "statistics",
    "unicodedata", "string", "textwrap", "functools", "operator",
})


def _action_safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    """受控 __import__：只放行 ALLOWED_ACTION_IMPORTS 里的顶层模块。

    其余一律 ImportError——os / subprocess / socket / shutil / importlib /
    pathlib 等能触达文件系统、进程与网络的模块都不在表内。
    """
    top = str(name).split(".")[0]
    if top not in ALLOWED_ACTION_IMPORTS:
        raise ImportError(
            f"模块 '{top}' 不在动作沙箱白名单内（见 ALLOWED_ACTION_IMPORTS）"
        )
    return __import__(name, globals, locals, fromlist, level)


class DiffusionEngine:

    def __init__(self, kg: KnowledgeGraph, config: dict):

        logger.info("=" * 60)
        logger.info("[DiffusionEngine] 初始化开始")
        logger.info("=" * 60)

        self.kg = kg
        self.config = config

        self._lock = threading.RLock()

        self._running = False
        self._auto_thread = None

        # 最近点亮序：节点 id → 单调递增序号（越大越近）。
        # 注意力并列（都撞到 cap）时，本轮刚点亮的节点比图谱早年的节点更相关：
        # 旧并列键 (activation, -入图序) 在 cap 并列时让"最早的节点"胜出，
        # 于是饱和场上回答区被最老的节点占满（学校/班级那类旧簇）。
        self._last_lit: dict[str, int] = {}
        self._lit_seq = 0

        # ── Activation Source（本轮激活来源登记）──
        # node_id → source_type。语义（理论对齐 2026-09-19b）：
        # 来源节点是"本轮 activation 的注入点"，其初始激活不属于上一轮图内
        # 资源，因此发射免扣（否则等于让外部输入替图内的旧资源买单）。
        # 这不是"特殊节点"豁免，而是来源与普通图节点的区分：
        #   external_input  用户输入 / 对话通道
        #   perception      具身感知 / 环境事件
        #   internal_drive  驱动力 / 好奇信号 / 情境偏置
        #   memory_recall   记忆再点火 / 经验写入
        #   goal / emotion  目标与情绪注入（预留，当前未接线）
        # 生命周期：回合边界清空（clear_anchors）。空闲期由 CC 循环每 tick
        # 清空——每个 tick 是自包含的传播周期，注入只在当期免扣。
        self._sources: dict[str, str] = {}

        # CurrentActivity 语境缓存：当前活动节点的局部邻域（活动本体/类型/
        # 状态/目标 + 具身状态槽位）。这些节点分到更多可发射注意资源
        # （_emission_ratio_for 的语境调制项）——是 context modulation，
        # 不是激活注入，更不是全图广播。
        self._activity_context: set = set()

        # ── 认知锁门控（cognitive_locks.LockRegistry 或 None） ──
        # 语义（详见 cognitive_locks.py 顶部）：
        #   blocking     → 对象不参与扩散：不被种子激活、不发射、不接收
        #   non_blocking → 照常参与扩散（传播激活值），但不进入扩散**输出**
        #                  （Top-K / 活跃快照 / 回答区）
        # 引擎只读注册表暴露的四组 set（O(1) 查找），不反向依赖该模块，
        # 避免 diffusion_engine ↔ cognitive_locks 循环导入。
        self.lock_registry = None

        # 一轮扩散中已发射过的节点集合（fire-once 语义）
        # 一轮 = 输入点火到回合结束衰减之间。每个节点只向外发射一次信号，
        # 防止环路（如好奇心链路、双向语义边）把激活值反复泵送直至饱和。
        self._fired_round = set()

        # -----------------------------------
        # 活跃前沿（稀疏遍历域）
        # 衰减把激活压向 0，任意时刻活跃前沿 F ≪ N。扩散/衰减/top-k 只遍历
        # 前沿，复杂度 O(前沿×平均出度)，与图谱规模 N 解耦。
        #   _active_nodes: dict-as-ordered-set（保持入集顺序，供稳定遍历）
        #   _active_edges: 边对象 identity 集合（只有被激活过的边参与衰减）
        # 激活值 < activation_epsilon 时吸附为 0 并出集——这些值本就不能
        # 扩散、低于全库一切决策阈值（最低 0.01），吸附不可观察。
        # -----------------------------------
        self._active_nodes: dict = {}
        self._active_edges: set = set()
        # Hebbian 首见基线（运行时，不落盘）与参数调制增益（默认中性）
        self._hebb_w0: dict = {}
        self._param_gain: float = 1.0
        # 认知调制层（可选，app 装配注入 ModulationLayer）：
        # 网络态/张力/激素 → 扩散参数的闭环读点（无则全部回退 config，
        # 单测/离线行为与旧版逐位一致）。
        self.modulation = None
        self._action_dirty: set = set()
        self._queue_map: dict = {}          # nid → activation（增量行动队列）

        # 订阅图结构变更回调：rename/删除时同步前沿，防止悬空 id。
        # 回调必须无锁（不取 engine._lock）——kg 在调用方持有 kg._lock 时
        # 也可能触发回调，取锁会造出 engine→kg / kg→engine 锁序环。
        # 个别竞态留下的陈旧 id 由遍历自愈（kg.nodes.get 为 None 时弹除）。
        kg._cb_node_renamed.append(self._on_graph_node_renamed)
        kg._cb_edge_removed.append(self._on_graph_edge_removed)
        kg._cb_node_removed.append(self._on_graph_node_removed)

        # 初始同步：已激活节点/边纳入前沿（正常启动时激活全为 0，集合为空）
        for _nid, _n in self.kg.nodes.items():
            if _n.activation > 0:
                self._active_nodes[_nid] = None
        for _e in self.kg.edges:
            if _e.activation > 0:
                self._active_edges.add(_e)

        self.action_queue = []
        self.execution_log = []
        self._last_reflection_time = 0.0
        self._last_thought_time = 0.0
        self._last_preference_decay_time = 0.0
        self._nlp_ref = None

        # -----------------------------------
        # 建立名称反向索引
        # -----------------------------------

        start = time.time()

        logger.info(
            f"[索引构建] 开始扫描节点..."
        )

        self.name_to_node = {}
        count = 0
        for nid, node in self.kg.nodes.items():
            # 🔍 BUG 1 修复：去除原 getattr(node, 'name') 的无效回退逻辑，直接绑定真实的实体概念 ID
            node_name = str(node.id).strip()
            if node_name:
                self.name_to_node[node_name] = node
            count += 1

            if count % 50000 == 0:
                logger.info(
                    f"[索引构建] 已处理 {count} 节点..."
                )

        logger.info(
            f"[索引构建] 完成 "
            f"(总节点: {count}, "
            f"耗时: {time.time() - start:.2f}s)"
        )

        logger.info("=" * 60)
        logger.info("[DiffusionEngine] 初始化完成")
        logger.info("=" * 60)

    # ── 活跃前沿管理 ──

    def _mark_lit(self, node_id: str):
        """记录节点在本轮被点亮（种子激活 / 接收传播）。"""
        self._lit_seq += 1
        self._last_lit[node_id] = self._lit_seq

    # ── 认知锁门控 ────────────────────────────────────────

    def set_lock_registry(self, registry):
        """绑定认知锁注册表（app 启动时接线；None = 无锁，零开销）。"""
        self.lock_registry = registry
        if registry is not None:
            logger.info("[DiffusionEngine] 认知锁门控已绑定")
        return registry

    def _locks(self):
        """当前锁注册表（可能为 None）。"""
        return self.lock_registry

    @staticmethod
    def _edge_key(edge) -> tuple:
        return (edge.src, edge.relation, edge.dst)

    def _node_is_blocked(self, node_id: str) -> bool:
        reg = self.lock_registry
        return bool(reg) and reg.is_node_blocked(node_id)

    def _edge_is_blocked(self, edge) -> bool:
        reg = self.lock_registry
        return bool(reg) and reg.is_edge_blocked(edge.src, edge.relation, edge.dst)

    def _node_is_hidden(self, node_id: str, scope: str = "topk") -> bool:
        """non_blocking 或输出型过程的锁：参与内部计算但不出现在输出。"""
        reg = self.lock_registry
        return bool(reg) and reg.hides_node(node_id, scope)

    def _edge_is_hidden(self, edge) -> bool:
        reg = self.lock_registry
        return bool(reg) and reg.is_edge_hidden(edge.src, edge.relation, edge.dst)

    def _eps(self) -> float:
        """激活吸附阈值（config 可热更新，每次现读）。"""
        return float(self.config.get("activation_epsilon", 1e-4))

    def _edge_r_cap(self) -> float:
        """边运行时激活 r 的上限（论文 w̃ = w + r 的 r 值域）。"""
        return float(self.config.get("edge_runtime_activation_cap", 1.0))

    # ── 认知动力学调制（Hebbian / 软遗忘 / 参数增益，2026-09-20 补账）──

    def _hebbian(self, edge, src_node):
        """共激活边强化（论文 V2 w += ε·a(u)·a(v) 的工程安全版）。

        护栏：config.hebbian.enabled；类别白名单（默认 semantic/emotional——
        disposition 的先验/激活/pair 边与认知关系类**永不被运行时改写**，
        人格学习只走 disposition 通道）；单边相对首见基线增幅封顶 max_gain
        （可逆不爆炸）；ε 极小。基线以运行时首见值记录，重启后以当前持久
        权重为新基线，限幅跨会话稳定。
        """
        hb = self.config.get("hebbian") or {}
        if not hb.get("enabled", True):
            return
        cat = str(getattr(edge, "relation_category", ""))
        if cat not in set(hb.get("categories", ["semantic_relation",
                                                "emotional_relation"])):
            return
        eps = float(hb.get("epsilon", 0.0015))
        max_gain = float(hb.get("max_gain", 0.5))
        key = (edge.src, edge.dst, edge.relation)
        w0 = self._hebb_w0.get(key)
        if w0 is None:
            self._hebb_w0[key] = w0 = abs(edge.weight) or 1e-6
        a_u = min(1.0, max(0.0,
                float(getattr(src_node, "activation", 0) or 0) / 5.0))
        cap = abs(w0) * (1.0 + max_gain)
        step = eps * a_u
        if edge.weight >= 0:
            edge.weight = min(cap, edge.weight + step)
        else:   # 抑制边同向强化（更抑制），|w| 同封顶
            edge.weight = max(-cap, edge.weight - step)

    def _forget_factor(self, node):
        """last_access 驱动的可达性衰减（软遗忘：数据保留、唤醒变难）。"""
        sf = self.config.get("soft_forgetting") or {}
        if not sf.get("enabled", True):
            return 1.0
        try:
            if getattr(node, "graph_space", "") in set(
                    sf.get("exempt_spaces", ["self"])):
                return 1.0
            if str(getattr(node, "label", "")) in set(sf.get(
                    "exempt_labels", ["infrastructure", "procedural",
                                      "disposition", "intention"])):
                return 1.0
            if str(node.id) in set(sf.get("exempt_ids",
                                          ["用户", "Haru", "Self", "Fascinator"])):
                return 1.0
            from datetime import datetime as _dt
            la = str(getattr(node, "last_access", "") or "")
            t = _dt.strptime(la, "%Y/%m/%d %H:%M:%S").timestamp()
            idle_days = max(0.0, (time.time() - t) / 86400.0)
        except Exception:
            return 1.0
        per_day = float(sf.get("decay_per_day", 0.06))
        floor = float(sf.get("floor", 0.25))
        grace = float(sf.get("grace_days", 2.0))
        return max(floor, 1.0 - per_day * max(0.0, idle_days - grace))

    def _mod(self, name: str, default):
        """调制层读点：有 modulation 用它，否则回退 default（config 值）。"""
        m = getattr(self, "modulation", None)
        if m is None:
            return default
        try:
            v = m.get(name)
            return default if v is None else v
        except Exception:
            return default

    def update_param_modulation(self, arousal=0.0, stress=0.0):
        """把当前情绪唤醒/压力换算成传播增益（参数节点化的最小接线）。

        高唤醒 → 联想更发散（beta 上限 1.6）；高压力 → 小幅收敛。
        arousal/stress ∈ [0,1]。不触碰任何边语义/锁/白名单。
        调制层在位时（2026-09-20 Drive 重构收编）：唤醒/压力已由
        CognitiveField 以 context.arousal/stress 并入 diffusion.param_gain
        （网络态/激素一并生效），这里只读调制结果；无调制层=旧路径。
        """
        if getattr(self, "modulation", None) is not None:
            self._param_gain = float(self._mod(
                "diffusion.param_gain", self._param_gain))
            return self._param_gain
        pm = self.config.get("param_modulation") or {}
        if not pm.get("enabled", True):
            self._param_gain = 1.0
            return self._param_gain
        a = min(1.0, max(0.0, float(arousal or 0)))
        st = min(1.0, max(0.0, float(stress or 0)))
        self._param_gain = 1.0 + float(pm.get("arousal_gain", 0.3)) * a             - float(pm.get("stress_damp", 0.15)) * st
        self._param_gain = min(1.6, max(0.7, self._param_gain))
        return self._param_gain

    def _edge_w_eff(self, edge) -> float:
        """边的有效传播权重 w̃ = w + r（论文加性语义，r 夹到上限）。

        架构对齐（2026-09-19）：旧实现是乘性 boost w×(1+r×0.6)——常用边
        越走越宽（r 可到 3.0 → 2.8×），形成永久"高速公路"，是扩散发散的
        放大器。加性 + 上限后，使用频率只把有效权重最多抬升 r_cap（默认 1.0）。
        负权重保留：w<0 的边产生抑制性贡献（论文的统一抑制机制）。
        """
        return edge.weight + min(max(0.0, edge.activation), self._edge_r_cap())

    def _edge_direction(self, edge) -> str:
        """边的传播方向：全局语义覆盖 > 实例 config 类别规则 > 全局类别默认。

        优先级说明：关于/涉及/参与 这类对称语义是本体约定（graph_schema
        全局覆盖表），不允许被一份旧实例 config 改回单向；实例 config 的
        relation_propagation 仍可热调整各类别的默认方向。
        """
        import graph_schema as _gs
        rel = edge.relation
        ov = _gs.global_direction_override(rel)
        if ov:
            return ov
        cat = getattr(edge, "relation_category", "semantic_relation")
        rules = self.config.get("relation_propagation", {}).get(cat)
        if rules and rules.get("direction"):
            return rules["direction"]
        return _gs.direction_for(rel, cat)

    def mark_active(self, node_ids):
        """外部直写 node.activation 之后必须调用：把节点同步进活跃前沿。

        绕过 activate_from_inputs 的激活写入（好奇链路、LLM 回答、网络搜索、
        drive/vision 注入等）不经此同步就不会进入扩散/衰减/top-k 遍历域，
        好奇链路会整体停摆。activation 已低于 EPS 的节点会被移出集合
        （清零类直写也走这里兜底）。同时标记行动队列脏（激活变化影响
        procedural 节点的入队资格）。
        """
        if isinstance(node_ids, str):
            node_ids = [node_ids]
        eps = self._eps()
        with self._lock:
            for nid in node_ids:
                node = self.kg.nodes.get(nid)
                if node is None:
                    continue
                if node.activation >= eps:
                    self._active_nodes[node.id] = None
                    # 直写激活也是"此刻被点亮"——并列破局需要这个时间序
                    self._mark_lit(node.id)
                else:
                    self._active_nodes.pop(node.id, None)
            self._action_dirty.update(n.id for n in
                                      (self.kg.nodes.get(nid) for nid in node_ids)
                                      if n is not None)

    def mark_edges_active(self, edges):
        """外部直写 edge.activation 之后必须调用（mark_active 的边版本）。"""
        with self._lock:
            for e in edges:
                if e is not None and e.activation > 0:
                    self._active_edges.add(e)

    def note_action_dirty(self, node_ids):
        """label 等影响行动队列资格的属性变更后调用：下次队列刷新重估。"""
        if isinstance(node_ids, str):
            node_ids = [node_ids]
        with self._lock:
            self._action_dirty.update(node_ids)

    def active_snapshot(self):
        """活跃前沿快照：(nodes, edges)，供响应展示等消费。

        语义与 process_nlp 响应展示的既有过滤逐位一致：
          - 节点：activation > 0 且 label 不属于 infrastructure/procedural
          - 边：两端都在上述活跃节点集内（与边自身 activation 无关），
            按 kg.edges 列表序（_seq）输出
        锁序：engine → kg（与 activate_from_inputs 一致，防死锁环）。
        """
        with self._lock:
            nodes = []
            node_ids = set()
            for nid in self._active_nodes:
                n = self.kg.nodes.get(nid)
                if n is None or n.activation <= 0:
                    continue
                if n.label in ("infrastructure", "procedural"):
                    continue
                if self._node_is_hidden(nid):      # non_blocking 锁：不进输出
                    continue
                nodes.append(n)
                node_ids.add(n.id)
            seen = set()
            cand = []
            for nid in self._active_nodes:
                if nid not in node_ids:
                    continue
                for e in self.kg.get_out_edges(nid):
                    if e.dst in node_ids and id(e) not in seen:
                        seen.add(id(e))
                        if not self._edge_is_hidden(e):
                            cand.append(e)
                for e in self.kg.get_in_edges(nid):
                    if e.src in node_ids and id(e) not in seen:
                        seen.add(id(e))
                        if not self._edge_is_hidden(e):
                            cand.append(e)
            cand.sort(key=lambda e: e._seq)
            return nodes, cand

    def reattach_graph(self, kg: KnowledgeGraph):
        """pack 启停/删除后重绑图谱：重建前沿、重订阅回调、全量刷新队列。

        name_to_node 由调用方重建（与现状一致）。
        """
        self.kg = kg
        eps = self._eps()
        with self._lock:
            self._active_nodes = {nid: None for nid, n in kg.nodes.items()
                                  if n.activation > eps}
            self._active_edges = {e for e in kg.edges if e.activation > 0}
            self._fired_round.clear()
            self._action_dirty = set()
            self._queue_map = {}
        kg._cb_node_renamed.append(self._on_graph_node_renamed)
        kg._cb_edge_removed.append(self._on_graph_edge_removed)
        kg._cb_node_removed.append(self._on_graph_node_removed)
        self._refresh_action_queue()

    # ── 图结构变更回调（无锁；见 __init__ 注释）──

    def _on_graph_node_renamed(self, old_id: str, new_id: str):
        if old_id in self._active_nodes:
            self._active_nodes.pop(old_id, None)
            self._active_nodes[new_id] = None
        if old_id in self._fired_round:
            self._fired_round.discard(old_id)
            self._fired_round.add(new_id)
        self._action_dirty.add(old_id)
        self._action_dirty.add(new_id)

    def _on_graph_edge_removed(self, edge):
        self._active_edges.discard(edge)

    def _on_graph_node_removed(self, node_id: str):
        self._active_nodes.pop(node_id, None)
        self._action_dirty.add(node_id)

    # ── Architecture Refactor 0.1: 类型感知传播辅助方法 ──

    def _space_rules(self, graph_space: str) -> dict:
        """获取指定认知空间的传播规则，未配置则回退到全局默认值。"""
        rules = self.config.get("propagation_rules", {}).get(graph_space, {})
        return {
            "lambda_decay": rules.get("lambda_decay", self.config.get("lambda_decay", 0.05)),
            "beta_spread": rules.get("beta_spread", self.config.get("beta_spread", 1.0)),
            "allow_bidirectional": rules.get("allow_bidirectional", True),
            "activation_cap": rules.get("activation_cap", self.config.get("activation_max", 5.0)),
        }

    def _rel_rules(self, relation_category: str) -> dict:
        """获取指定关系类别的传播规则，未配置则回退到默认（双向、标准倍率）。"""
        return self.config.get("relation_propagation", {}).get(
            relation_category,
            {"direction": "bidirectional", "decay": 1.0, "gain": 1.0}
        )

    def get_topk(self, k=20):
        """当前最活跃节点 + 相关边（给前端展示用）。

        只在活跃前沿内取——前沿外节点激活已吸附为 0，旧版用零激活节点
        填充 top-k 的行为随 EPS 吸附一并消失（零激活节点无认知意义）。
        tie-break 键 (activation, 点亮序, -入图序)：激活降序；并列时本轮
        更近点亮的节点优先（饱和场上 cap 并列很常见，不能让"最老的节点"
        靠入图序取胜，否则回答区被早年旧簇占满）；点亮序也并列才回落到
        入图序（保持旧版"等值输入序"的确定性）。
        """
        with self._lock:
            node_order = self.kg._node_order
            last_lit = self._last_lit
            # ── 锁介入点（输出侧）：non_blocking 锁 ──
            # 对象照常参与扩散（激活值真实存在），但不进入 Top-K 输出。
            # 这是 blocking 与 non_blocking 的可测试分界：前者激活值恒为 0，
            # 后者激活值正常但被输出过滤掉。
            pool = [self.kg.nodes[nid] for nid in self._active_nodes
                    if nid in self.kg.nodes and not self._node_is_hidden(nid)]
            top_nodes = heapq.nlargest(
                k,
                pool,
                key=lambda n: (n.activation,
                               last_lit.get(n.id, 0),
                               -node_order.get(n.id, 1 << 60))
            )

            node_ids = {n.id for n in top_nodes}

            # 关联边 = top 节点的出入边（去重），按 _seq（= kg.edges 列表序）输出
            seen = set()
            cand = []
            for nid in node_ids:
                for e in self.kg.get_out_edges(nid):
                    if id(e) not in seen:
                        seen.add(id(e))
                        if not self._edge_is_hidden(e):
                            cand.append(e)
                for e in self.kg.get_in_edges(nid):
                    if id(e) not in seen:
                        seen.add(id(e))
                        if not self._edge_is_hidden(e):
                            cand.append(e)
            cand.sort(key=lambda e: e._seq)

            return top_nodes, cand

    # =========================================================
    # Action Queue
    # =========================================================

    def _refresh_action_queue(self, changed_ids=None):
        """行动队列刷新。

        changed_ids=None → 无条件全量重建（/api/actions 轮询、执行端点等既有
        调用点依赖"返回即全图现状"，脏集合被全量扫描一并吸收）。
        changed_ids=列表 → 增量：只重估激活/资格变化的节点（现场读 label，
        不缓存 procedural 集合）；note_action_dirty / mark_active / 图回调
        累积的脏节点并入本次重估。无脏且无 changed → 跳过重建与日志。
        两种模式共用排序键 (-activation, 入图序)，保证 /api/actions/execute
        取队首的语义与全量重建逐位一致。
        """
        start = time.time()

        theta_act = self.config.get(
            "theta_action",
            0.5
        )

        try:

            with self._lock:

                if changed_ids is None:
                    # 全量重建（无条件——脏集合必然被全量扫描覆盖）
                    procedural_count = 0
                    candidates_map = {}
                    for n in self.kg.nodes.values():
                        if n.label == "procedural":
                            procedural_count += 1
                            # 动作概念/表达节点（Action Concept 2026-09-19）
                            # 是解析器的语义结构，不是"被激活即入队"的旧式
                            # procedural 动作——执行必须经 Action Intent →
                            # ActionManager，绝不允许经此遗留队列。
                            if (n.extra_attrs or {}).get("type") in (
                                    "action_concept", "action_expression"):
                                continue
                            # 锁介入点（动作过程）：scope=action 的锁把该节点
                            # 排除在可执行队列之外；队列是动作的唯一通路，
                            # 因此"不在队列"= 不会被自动执行。
                            if self._node_is_hidden(n.id, scope="action"):
                                continue
                            if n.activation >= theta_act:
                                candidates_map[n.id] = n.activation
                    self._queue_map = candidates_map
                    self._action_dirty = set()
                else:
                    # 增量重估：脏集合（回调/直写标记）∪ 本次显式 changed
                    dirty = self._action_dirty | set(changed_ids)
                    if not dirty:
                        return  # 无变化：跳过重建与日志
                    for nid in dirty:
                        n = self.kg.nodes.get(nid)
                        if (n is None or n.label != "procedural"
                                or (n.extra_attrs or {}).get("type") in (
                                    "action_concept", "action_expression")
                                or self._node_is_hidden(nid, scope="action")):
                            self._queue_map.pop(nid, None)
                        elif n.activation >= theta_act:
                            self._queue_map[nid] = n.activation
                        else:
                            self._queue_map.pop(nid, None)
                    self._action_dirty = set()
                    procedural_count = None

                node_order = self.kg._node_order
                candidates = [(act, nid) for nid, act in self._queue_map.items()]
                candidates.sort(
                    key=lambda x: (-x[0], node_order.get(x[1], 1 << 60))
                )

                self.action_queue = candidates

                if procedural_count is not None:
                    logger.info(
                        f"[ActionQueue] "
                        f"procedural节点: {procedural_count}, "
                        f"可执行动作: {len(candidates)}"
                    )
                    # 打印前5个动作
                    for act, nid in candidates[:5]:
                        logger.info(
                            f"  ACTION -> "
                            f"{nid} "
                            f"(act={act:.4f})"
                        )
                else:
                    logger.info(
                        f"[ActionQueue] 增量刷新, 可执行动作: {len(candidates)}"
                    )

        except Exception as e:

            logger.error(
                f"[ActionQueue异常] {e}"
            )

            traceback.print_exc()

        logger.info(
            f"[ActionQueue] 完成 "
            f"(耗时: {time.time() - start:.3f}s)"
        )

    # =========================================================
    # 输入激活
    # =========================================================

    def activate_from_inputs(
            self,
            node_ids: list,
            edge_specs: list,
            similarity_map: dict = None,
            source_type: str = "external_input"
    ):

        logger.info("=" * 60)
        logger.info("[输入激活] 开始")
        logger.info("=" * 60)

        total_start = time.time()
        a_max = self.config.get("activation_max", 5.0)
        sim_map = similarity_map or {}

        try:

            with self._lock:

                # -----------------------------------
                # 节点激活（cap 到 a_max）
                #    如果有 semantic_similarity，则 activation += similarity
                #    否则 activation += 1.0 (默认)
                # -----------------------------------

                node_hit = 0
                node_miss = 0
                activated_node_ids = set()

                start = time.time()

                logger.info(
                    f"[节点激活] 输入节点数: {len(node_ids)}"
                )

                for nid in node_ids:

                    node_name = str(nid).strip()

                    if not node_name:
                        continue

                    if node_name in self.name_to_node:

                        node = self.name_to_node[node_name]

                        # ── 锁介入点 1/5：节点激活前（blocking 锁）──
                        # 被阻塞的对象不参与扩散，因此连种子激活都不给：
                        # 激活值保持 0，后续发射/接收自然无从发生。放在这里
                        # 而不是"激活后再清零"，是为了让活跃前沿与 top-k
                        # 的数学语义与"该对象不在本轮认知中"完全一致。
                        if self._node_is_blocked(node.id):
                            node_miss += 1
                            logger.debug(f"  locked(blocked): {node_name}")
                            continue

                        old_activation = node.activation

                        # 种子强度 = 相似度在 [相似度地板, 1] 上的线性映射 × 量程。
                        # 相似度是 [0,1] 的意义强度，激活值域是 [0, a_max]：
                        #   sim = 1.0            → 满量程（这个节点就是输入本身）
                        #   sim = floor(0.5)     → 0（低于地板不算焦点：0.5 左右的
                        #                          匹配多半是高频词/枢纽节点的人人
                        #                          有份式命中，例如一句"Fascinator"
                        #                          能召回一串 CI 节点——这些噪声种子
                        #                          会把整片邻域泵到饱和，把真正相关的
                        #                          节点挤出回答区）
                        # 无相似度时（LLM 抽取出的实体）用固定档位 default × 量程。
                        _floor = float(self.config.get("input_similarity_floor", 0.5))
                        _default = float(self.config.get("input_default_bonus", 0.5))
                        _sim = sim_map.get(node_name)
                        if _sim is None:
                            bonus = _default * a_max
                        else:
                            _span = max(1e-6, 1.0 - _floor)
                            bonus = max(0.0, (_sim - _floor) / _span) * a_max
                        # Soft forgetting（V2：数据保留、可达性衰减）：久未
                        # 访问的节点种子强度按 forget_factor 衰减——不删数据、
                        # 不改权重，只降低"被唤起"的强度；触碰即恢复。
                        bonus *= self._forget_factor(node)
                        node.activation = min(
                            a_max,
                            node.activation + bonus
                        )

                        node.touch()
                        activated_node_ids.add(node.id)
                        self._active_nodes[node.id] = None
                        self._mark_lit(node.id)
                        # 登记为本轮 activation source（external_input 等，
                        # 见 register_activation_source 的语义说明）
                        self._sources[node.id] = source_type
                        node_hit += 1

                        logger.debug(
                            f"  node: "
                            f"{node_name} "
                            f"{old_activation:.3f}"
                            f" + {bonus:.3f}"
                            f" -> "
                            f"{node.activation:.3f}"
                        )

                    else:

                        node_miss += 1

                        logger.debug(
                            f"  unknown: {node_name}"
                        )

                # -----------------------------------
                # 激活被激活节点的所有出入边
                #    边激活值 = 0.5 * semantic_similarity (有则用, 否则 0.5)
                #    不超过 3.0
                # -----------------------------------

                incident_edge_count = 0

                # 邻接索引桶遍历（O(Σdeg(激活节点))），替代全边表扫描；
                # 双端都激活的边只加成一次（与旧版单次列表扫描语义一致）
                seen_edges = set()
                for nid in activated_node_ids:
                    for edge in self.kg.get_out_edges(nid):
                        if id(edge) in seen_edges:
                            continue
                        seen_edges.add(id(edge))
                        # ── 锁介入点 2/5：边参与扩散前（blocking 锁）──
                        # 被锁的边不进入被加成集合，后续 diffuse_step 也不会
                        # 从它的激活值里受益（它本来就不参与传播）。
                        if self._edge_is_blocked(edge):
                            continue
                        bonus = max(sim_map.get(edge.src, 0.5), sim_map.get(edge.dst, 0.5))
                        edge_bonus = max(0.1, 0.5 * bonus)
                        edge.activation = min(self._edge_r_cap(),
                                              edge.activation + edge_bonus)
                        edge.touch()
                        self._active_edges.add(edge)
                        incident_edge_count += 1
                    for edge in self.kg.get_in_edges(nid):
                        if id(edge) in seen_edges:
                            continue
                        seen_edges.add(id(edge))
                        if self._edge_is_blocked(edge):
                            continue
                        bonus = max(sim_map.get(edge.src, 0.5), sim_map.get(edge.dst, 0.5))
                        edge_bonus = max(0.1, 0.5 * bonus)
                        edge.activation = min(self._edge_r_cap(),
                                              edge.activation + edge_bonus)
                        edge.touch()
                        self._active_edges.add(edge)
                        incident_edge_count += 1

                logger.info(
                    f"[节点激活] 完成 "
                    f"(命中: {node_hit}, "
                    f"丢失: {node_miss}, "
                    f"激活边: {incident_edge_count}, "
                    f"耗时: {time.time() - start:.3f}s)"
                )

                # -----------------------------------
                # 边激活（LLM 解析出的边——精确匹配图谱已有边）
                # -----------------------------------

                edge_hit = 0
                edge_miss = 0

                start = time.time()

                logger.info(
                    f"[边激活] 输入边数: {len(edge_specs)}"
                )

                for espec in edge_specs:

                    src = str(espec.get("src", "")).strip()
                    dst = str(espec.get("dst", "")).strip()
                    relation = str(espec.get("type", "")).strip()

                    if not src or not dst or not relation:
                        continue

                    if (
                            src in self.name_to_node
                            and dst in self.name_to_node
                    ):

                        src_node = self.name_to_node[src]
                        dst_node = self.name_to_node[dst]

                        existing_edge = self.kg.get_edge(
                            src_node.id,
                            dst_node.id,
                            relation
                        )

                        if existing_edge:

                            old_ea = existing_edge.activation

                            existing_edge.activation = min(self._edge_r_cap(),
                                                           existing_edge.activation + 0.5)

                            existing_edge.touch()
                            self._active_edges.add(existing_edge)

                            edge_hit += 1

                            logger.debug(
                                f"  edge: "
                                f"{src}"
                                f" -[{relation}]-> "
                                f"{dst} "
                                f"{old_ea:.3f}"
                                f" -> "
                                f"{existing_edge.activation:.3f}"
                            )

                        else:

                            edge_miss += 1

                logger.info(
                    f"[边激活] 完成 "
                    f"(命中: {edge_hit}, "
                    f"丢失: {edge_miss})"
                )

                # -----------------------------------
                # action queue（增量：激活变化的节点重新估资格）
                # -----------------------------------

                self._action_dirty.update(activated_node_ids)
                self._refresh_action_queue(changed_ids=list(activated_node_ids))

        except Exception as e:

            logger.error(
                f"[输入激活异常] {e}"
            )

            traceback.print_exc()

        logger.info(
            f"[输入激活] 总耗时: "
            f"{time.time() - total_start:.3f}s"
        )

        logger.info("=" * 60)

    # =========================================================
    # 扩散
    # =========================================================

    def diffuse_step(self):
        """单步传播（空间/关系感知 + 有限注意资源约束）。

        节点层：graph_space 决定 β/λ/cap；边层：relation_category（及
        per-relation 覆盖）决定方向/增益/边激活衰减；资源层：发射预算
        （可被 CurrentActivity 语境调制）+ 发射即转移 + fire-once。
        config["emission_trace"]=true 时按 [EMISSION]/[PROPAGATION] 块
        输出本步的来源、预算、发射、抑制明细（调试/研究用）。
        """

        # 全局回退参数
        fallback_beta = self.config.get("beta_spread", 1.0)
        fallback_cap = self.config.get("activation_max", 5.0)
        min_spread = float(self._mod(
            "diffusion.min_spread",
            self.config.get("min_spread_threshold", 0.01)))

        max_delta = 0.0
        active_nodes_count = 0
        propagated_edges = 0
        bidirectional_flow_count = 0
        emitted_resource = 0.0   # 本步因发射转移而消耗的注意资源总量（观测用）
        inhibited_energy = 0.0   # 本步抑制性贡献绝对值合计（观测用）

        with self._lock:
            delta = {}         # target_id → accumulated incoming activation

            # CurrentActivity 语境刷新（§12：局部 context modulation）
            self._refresh_activity_context()
            _trace = [] if self.config.get("emission_trace") else None

            # 只遍历活跃前沿（稀疏遍历域）；集合在遍历中只增不改序，
            # list() 快照保证接收阶段新增节点不参与本轮发射
            for nid in list(self._active_nodes.keys()):
                node = self.kg.nodes.get(nid)
                if node is None:
                    # 节点已被删除（回调无锁，此处自愈弹除）
                    self._active_nodes.pop(nid, None)
                    continue
                if node.activation < min_spread:
                    continue

                # ── Fire-once：一次 propagation cycle 内至多一次 outward emission ──
                # 注意区分两件事：激活的**累积**（可以持续接收多条传入并累加）
                # 与**向外发射**（受限动作）。图环 A→B→A 中若无此约束，
                # 激活会被反复泵送直至饱和（如 好奇→等待回答→…→好奇 链路）。
                # 只发射一次后，一轮的传播总量由种子及其语境决定，
                # Top-K 由种子及其邻域主导。
                if nid in self._fired_round:
                    continue

                # ── 锁介入点 3/5：节点发射前（blocking 锁）──
                # 被阻塞的对象不参与扩散：既不发射也不接收（接收侧见 5/5）。
                if self._node_is_blocked(nid):
                    continue

                active_nodes_count += 1
                out_edges = self.kg.get_out_edges(nid)
                # 反向传播沿"入边回溯"（dst → src，即我作为接收方把信号还回去）。
                # 方向判定（架构对齐 2026-09-19）：per-relation 覆盖 > 类别规则。
                # 默认已从"双向"改为 forward——不知道方向的关系不双向传播，
                # 这是旧版扩散大面积铺开的两大原因之一（另一半是步间无衰减）。
                in_edges = [
                    e for e in self.kg.get_in_edges(nid)
                    if self._edge_direction(e) == "bidirectional"
                ]
                if not out_edges and not in_edges:
                    continue

                # ── 空间感知参数 ──
                node_space = getattr(node, "graph_space", "semantic")
                srules = self._space_rules(node_space)
                beta = srules["beta_spread"]

                # ── 发射预算钳制：一次发射总量 ≤ 注意资源预算 ──
                # 归一化分母只计正贡献（激发）权重；负权重边不进分母但仍
                # 发射与其权重成比例的抑制量——同一动力学框架内表达促进
                # 与抑制（论文 §5.1），而不是把负权重钳死成 0。
                node_delta = {}
                emitted_sum = 0.0      # 正贡献合计（消耗的注意资源）
                inhibited_sum = 0.0    # 负贡献绝对值合计（观测用）

                # 正向传播（src → dst）
                total_w = sum(w for w in (self._edge_w_eff(e) for e in out_edges
                                          if not self._edge_is_blocked(e))
                              if w > 0)
                # ── 抑制分母修复（L1-DFU-01，见 PATCH_LOG）──
                # 旧实现：`if total_w > 0` 守卫整个边循环——纯抑制节点（出
                # 边全负）total_w==0 → 负贡献分支随循环一起被跳过，抑制量
                # 永远不发射（目标只吃自然衰减）。混合节点（正负边共存）
                # 的负贡献分母是 total_w。新分母：有正边时保持 total_w
                #（混合语义逐位不变），纯负时按 Σ|w| 归一向各目标分配。
                total_abs_neg = sum(-w for w in
                                    (self._edge_w_eff(e) for e in out_edges
                                     if not self._edge_is_blocked(e)) if w < 0)
                denom = total_w if total_w > 0 else total_abs_neg
                if denom > 0:
                    for edge in out_edges:
                        if self._edge_is_blocked(edge):
                            continue
                        w_eff = self._edge_w_eff(edge)
                        # ── 关系类别感知增益 ──
                        edge_cat = getattr(edge, "relation_category", "semantic_relation")
                        gain = self._rel_rules(edge_cat).get("gain", 1.0)

                        contribution = node.activation * w_eff * beta * gain / denom
                        if contribution > 1e-8:
                            node_delta[edge.dst] = node_delta.get(edge.dst, 0.0) + contribution
                            emitted_sum += contribution
                            propagated_edges += 1
                            edge.activation = min(self._edge_r_cap(),
                                                  edge.activation + 0.02)
                            self._active_edges.add(edge)   # 被加成的边进入衰减域
                            self._hebbian(edge, node)      # 共激活边强化
                        elif contribution < -1e-8:
                            # 抑制性贡献：降低目标激活，不消耗发射预算
                            node_delta[edge.dst] = node_delta.get(edge.dst, 0.0) + contribution
                            inhibited_sum += -contribution

                inhibited_energy += inhibited_sum

                # 反向传播（dst → src）：回溯入边，强度为正向的一半
                total_w_in = sum(w for w in (self._edge_w_eff(e) for e in in_edges
                                             if not self._edge_is_blocked(e))
                                 if w > 0)
                # L1-DFU-01 同修（反向）：纯抑制入边集也按 Σ|w| 归一发射
                total_abs_neg_in = sum(-w for w in
                                       (self._edge_w_eff(e) for e in in_edges
                                        if not self._edge_is_blocked(e)) if w < 0)
                denom_in = total_w_in if total_w_in > 0 else total_abs_neg_in
                if denom_in > 0:
                    for edge in in_edges:
                        if self._edge_is_blocked(edge):
                            continue
                        w_eff = self._edge_w_eff(edge)
                        gain = self._rel_rules(
                            getattr(edge, "relation_category", "semantic_relation")
                        ).get("gain", 1.0)
                        reverse_contrib = (node.activation * w_eff * beta * gain * 0.5
                                           / denom_in)
                        if reverse_contrib > 1e-8:
                            node_delta[edge.src] = node_delta.get(edge.src, 0.0) + reverse_contrib
                            emitted_sum += reverse_contrib
                            bidirectional_flow_count += 1
                            edge.activation = min(self._edge_r_cap(),
                                                  edge.activation + 0.01)
                            self._active_edges.add(edge)
                            self._hebbian(edge, node)
                        elif reverse_contrib < -1e-8:
                            node_delta[edge.src] = node_delta.get(edge.src, 0.0) + reverse_contrib
                            inhibited_sum += -reverse_contrib

                # 总发射能量封顶 = 源节点自身激活值 × 发射比例
                # 发射比例 < 1 是必要的：否则"只连一个邻居"的节点会在一步里把
                # 自己的激活值全部交出去（转移语义下等于自我清零），两跳以外的
                # 节点就再也留不住任何东西。默认 0.5：每步最多发出一半，
                # 剩下的留在自己身上参与累积与排序。
                _budget = self._emission_budget(node)   # 语境调制在此生效
                if emitted_sum > _budget > 0:
                    scale = _budget / emitted_sum
                    for _d in node_delta:
                        node_delta[_d] *= scale
                    # 关键：emitted_sum 必须跟着缩放更新——否则下面按"未缩放量"
                    # 做转移扣除，会扣掉实际发出的两倍（实测把两跳节点清零）
                    emitted_sum = _budget

                for _dst, _c in node_delta.items():
                    delta[_dst] = delta.get(_dst, 0.0) + _c

                # ── 发射即转移（注意资源约束，非能量守恒公理）──
                # 旧语义是"复制"：源节点把激活值发给邻居后自己一点不掉，
                # activation 在图上无成本复制、每步总量翻倍，唯一平衡点是
                # 所有节点撞 cap（实测：空闲 40 tick 内 733 节点全亮、374 个
                # 钉在 5.0——那是固定点不是认知）。
                # 运行时约束：节点向外传播要消耗本轮的一部分有限注意资源，
                # 发出的量从自身激活中扣除——
                # 1.0 = 发多少扣多少（图内资源只减不增，衰减得以真正排水）；
                # 0.0 = 旧的复制语义（仅为对照实验保留）。
                # activation source（本轮注入点）免扣：它们的初始激活不属于
                # 上一轮图内资源，是本轮的外来输入。
                _transfer = float(self.config.get("emission_transfer", 1.0))
                if nid in self._sources:
                    _transfer = 0.0        # 来源节点：本轮注入点，发射免扣
                if _transfer > 0.0 and emitted_sum > 0.0:
                    _lost = emitted_sum * min(1.0, _transfer)
                    delta[nid] = delta.get(nid, 0.0) - _lost
                    emitted_resource += _lost

                # 发射完成：本节点本轮不再向外传播（但可以继续接收）
                self._fired_round.add(nid)

                if _trace is not None:
                    _remaining = node.activation - (emitted_sum if nid not in self._sources else 0.0)
                    _trace.append({
                        "id": nid, "src": self._source_type(nid),
                        "act": round(node.activation, 3),
                        "budget": round(_budget, 3),
                        "emitted": round(emitted_sum, 3),
                        "remaining": round(max(0.0, _remaining), 3),
                        "inh": round(inhibited_sum, 3),
                    })

            # 目标节点接收（按目标节点的空间 cap）
            for dst, d in delta.items():
                dst_node = self.kg.nodes.get(dst)
                if dst_node is not None and self._node_is_blocked(dst):
                    # ── 锁介入点 5/5：目标接收前（blocking 锁）──
                    # 被阻塞的对象激活值保持 0，不因他人传播而获得注意力
                    continue
                if dst_node is not None:
                    dst_space = getattr(dst_node, "graph_space", "semantic")
                    dst_rules = self._space_rules(dst_space)
                    cap = dst_rules["activation_cap"]
                    old = dst_node.activation
                    # 扣掉发射损失后 d 可能为负：夹到 [0, cap]，激活不可是负数
                    new_val = max(0.0, min(cap, old + d))
                    dst_node.activation = new_val
                    if new_val > old:
                        self._mark_lit(dst)
                    diff = abs(new_val - old)
                    if diff > max_delta:
                        max_delta = diff
                    if new_val >= self._eps() and dst not in self._active_nodes:
                        self._active_nodes[dst] = None

            # top-5（前沿内；稳定 tie-break 与 get_topk 一致：
            # nlargest 键 = (activation, -入图序)）
            node_order = self.kg._node_order
            top_nodes = heapq.nlargest(
                5,
                [self.kg.nodes[nid] for nid in self._active_nodes
                 if nid in self.kg.nodes],
                key=lambda n: (n.activation, -node_order.get(n.id, 1 << 60))
            )

            logger.info(
                "[扩散] active=%d propagated=%d bidirectional=%d delta=%d "
                "max_delta=%.6f transferred=%.3f inhibited=%.3f",
                active_nodes_count, propagated_edges, bidirectional_flow_count,
                len(delta), max_delta, emitted_resource, inhibited_energy
            )

            for n in top_nodes:
                logger.info(f"  {n.id} (act={n.activation:.4f}, space={getattr(n, 'graph_space', 'semantic')})")

            if _trace:
                _lines = ["[EMISSION] 本步发射明细（top8 by emitted）:"]
                for r in sorted(_trace, key=lambda x: -x["emitted"])[:8]:
                    _lines.append(
                        f"  {r['id']}({r['src']}) act={r['act']} budget={r['budget']} "
                        f"emitted={r['emitted']} remaining={r['remaining']} inh={r['inh']}")
                _inflows = sorted(delta.items(), key=lambda x: -abs(x[1]))[:10]
                _lines.append("[PROPAGATION] 本步净流入 top10（+接收激发 / -发射转出或抑制）:")
                for _d, _v in _inflows:
                    _lines.append(f"  {_d} {'+' if _v >= 0 else ''}{_v:.3f}")
                logger.debug("; ".join(_lines))

            # 行动队列增量刷新（激活变化的是接收方；发射方自身激活不变）
            self._refresh_action_queue(changed_ids=list(delta.keys()))

        return max_delta

    def diffuse_from(self, node_ids, steps: int = 1):
        """只从指定节点发射的传播步（"补激活后的补传播"）。

        场景：本轮扩散收敛之后才拿到激活的节点（如记忆抽取新实体），
        需要把它们的信号发出去，让邻域（关联能力、时间桶、相关实体）
        一起进回答区。若直接再调 diffuse_step()，回合末衰减已清空
        fire-once 记录 → 整片前沿会重新发射一轮，把邻域整体泵到饱和，
        回答区退化成一堆 cap 并列。所以先把其余活性节点标记为本轮已发射，
        只留给定节点发射。
        """
        seeds = {str(n) for n in (node_ids or [])}
        if not seeds:
            return 0.0
        max_d = 0.0
        with self._lock:
            self._fired_round.update(
                nid for nid in self._active_nodes if nid not in seeds)
        for _ in range(max(1, int(steps))):
            max_d = self.diffuse_step()
        return max_d

    def clear_anchors(self):
        """清空本轮激活来源（回合边界调用：新输入会重新登记来源）。

        来源清空后，图内所有节点在传播中统一受注意资源约束：
        向外发射的量从自身资源中扣除，场随衰减自然排空。
        """
        with self._lock:
            n = len(self._sources)
            self._sources = {}
        return n

    # ── Activation Source（本轮激活来源）──────────────────────

    def register_activation_source(self, node_ids, source_type: str = "external_input"):
        """登记本轮激活来源（不注入激活，只登记身份）。

        谁该调用：绕过 activate_from_inputs 的激活注入方——驱动力刷新
        （internal_drive）、好奇信号（internal_drive）、记忆再点火
        （memory_recall）、环境事件（perception）等。它们注入的激活是
        本轮的外来输入，不属于上一轮图内资源，发射免扣。
        只做激活维持（如感知在视地板）的调用方**不要**登记——维持不是注入。
        """
        if isinstance(node_ids, str):
            node_ids = [node_ids]
        with self._lock:
            for nid in node_ids:
                if nid:
                    self._sources[str(nid)] = str(source_type)

    def _source_type(self, node_id: str) -> str:
        """节点在本轮的来源身份；普通图节点返回 'graph'。"""
        return self._sources.get(node_id, "graph")

    # ── Emission Budget（本轮可传播的注意资源）────────────────

    def _emission_ratio_for(self, node) -> float:
        """该节点本轮的发射配比（注意资源占自身激活的比例）。

        当前 = base(emission_ratio) + CurrentActivity 语境调制。
        预留的调制入口（按论文参数节点化路线逐步接入，勿一次性硬接）：
          emotion_gain   情绪节点激活调制（高多巴胺 → 联想更放得开）
          context_gain   认知焦点/任务语境调制
          source_gain    来源类型调制（内驱 > 外部 > 记忆）
          activity_gain  当前活动的进一步细分（疲劳/暂停 → 收缩）
        """
        ratio = float(self._mod(
            "diffusion.emission_ratio",
            self.config.get("emission_ratio", 0.5)))
        if node.id in self._activity_context:
            ratio += float(self.config.get("activity_emission_bonus", 0.15))
        # 预留：emotion/context/source 调制项在此累加
        return max(0.0, min(1.0, ratio))

    def _emission_budget(self, node) -> float:
        """节点本轮可向外传播的注意资源上限。

        语义：budget = activation × ratio —— 发出去的部分从自身激活中
        扣除（emission_transfer），剩余部分留在身上参与累积与排序。
        这是运行时注意资源约束，不是能量守恒公理。
        """
        # 参数节点化的兑现点：情绪唤醒/压力经 update_param_modulation 调整
        # 发射预算（高唤醒=更多注意资源允许外发→联想更发散；压力=收敛）。
        # 放在这里而非 β：预算钳制语义下 β 乘子会被归一化抵消，资源才是真杠杆。
        return max(0.0, getattr(node, "activation", 0.0))             * self._emission_ratio_for(node) * self._param_gain

    def _refresh_activity_context(self):
        """读取 Haru-[当前活动]-> 的局部语境（§12：影响注意力，不做广播）。

        语境集合 = 当前活动节点 + 它的类型/状态/目标 + 具身状态（当前
        Minecraft状态及其槽位）。语境内的节点发射配比获得 activity_emission_bonus
        ——"正在挖铁时，铁相关的知识更容易被联想展开"，作用范围是活动的
        1-hop 局部，不是整个知识图。
        """
        ctx = set()
        try:
            for e in self.kg.get_out_edges("Haru"):
                if e.relation == "当前活动" and e.dst in self.kg.nodes:
                    ctx.add(e.dst)
                    for e2 in self.kg.get_out_edges(e.dst):
                        ctx.add(e2.dst)          # 类型/状态/目标
                    break
            for e in self.kg.get_out_edges("Haru"):
                if e.relation == "当前状态" and e.dst in self.kg.nodes:
                    ctx.add(e.dst)               # 当前Minecraft状态
                    for e2 in self.kg.get_out_edges(e.dst):
                        if e2.relation == "状态项":
                            ctx.add(e2.dst)      # 具身状态槽位
        except Exception:
            pass
        self._activity_context = ctx

    def diffuse_round(self, max_steps: int = None):
        """运行一轮扩散（多步 + 回合末统一衰减）。

        Args:
          max_steps: 本轮的最大扩散步数。缺省时取 config["max_depth"]。
            用于显式的多阶段编排（如好奇心检测阶段 3 步 → 主扩散阶段
            剩余步数），替代临时改写 config 的做法（P0-4）。
            不修改任何配置——参数只作用于这一轮。
        """
        theta = self.config.get(
            "theta_threshold",
            0.01
        )

        max_depth = int(round(self._mod(
            "diffusion.max_depth",
            self.config.get("max_depth", 3))))
        if max_steps is not None:
            max_depth = int(max_steps)

        max_delta = 0.0
        steps = 0

        # 新一轮开始：清空 fire-once 记录
        self._fired_round.clear()

        # 扩散阶段：每步传播后立即衰减（架构对齐 2026-09-19，论文 §5.1：
        # a(v,t+1)=(1-λ)a(v,t)+ΣΔa 的衰减项在每步生效）。旧行为是 6 步
        # 纯传播、回合末才衰减一次——深度控制名存实亡，能量走到第 5、6 跳
        # 仍有传播意义。衰减不清 fire-once（clear_fired=False），fire-once
        # 仍以"一轮"为单位；回合末的最终衰减负责清空。
        for _ in range(max_depth):
            max_delta = self.diffuse_step()
            steps += 1
            self.decay_step(clear_fired=False)
            if max_delta < theta:
                break

        # 回合结束：最终衰减（含 fire-once 清空，作为回合边界）
        self.decay_step()

        return {
            "steps": steps,
            "max_delta": max_delta
        }

    def start_auto(self):
        with self._lock:
            if self._running:
                return False
            self._running = True

            interval = float(
                self.config.get(
                    "auto_interval",
                    1.5
                )
            )

            def _loop():
                while True:
                    with self._lock:
                        if not self._running:
                            break
                    try:
                        self.decay_step()
                        self.diffuse_step()
                        if self._nlp_ref and self.config.get('thought_threshold', 0.2) > 0:
                            try:
                                self.generate_thought()
                            except Exception as _de:
                                logger.warning(f"[Auto] generate_thought failed: {_de}")
                        # Phase 2: 反思由 ReflectionEngine 的 trigger 系统管理
                        # 不再在 auto-diffusion 线程中定期调用旧版 run_reflection_cycle
                        if time.time() - self._last_preference_decay_time > 86400:
                            try:
                                self.decay_preferences_cycle()
                            except Exception as _de:
                                logger.warning(f"[Auto] preference decay failed: {_de}")

                    except Exception:
                        logger.exception("[Auto] 扩散异常")
                    time.sleep(interval)

            t = threading.Thread(
                target=_loop,
                daemon=True
            )
            self._auto_thread = t
            t.start()
            return True

    def stop_auto(self):
        with self._lock:
            if not self._running:
                return False
            self._running = False
            t = self._auto_thread

        if t:
            t.join(timeout=2.0)

        with self._lock:
            if self._auto_thread is t:
                self._auto_thread = None
        return True

    # =========================================================
    # Everything is Graph: Thought, Reflection, Preference Decay
    # =========================================================

    def generate_thought(self, nlp_processor=None, k: int = 10):
        """Generate internal thought node from Self diffusion."""
        from self_graph import generate_thought as sg_thought
        nlp = nlp_processor or self._nlp_ref
        if nlp is None:
            return None
        thought_id = sg_thought(self.kg, self, nlp, k=k,
                                threshold=self.config.get("thought_threshold", 0.2))
        self._last_thought_time = time.time()
        return thought_id


    def decay_preferences_cycle(self):
        """Run preference decay cycle."""
        from self_graph import decay_preferences as sg_decay
        sg_decay(self.kg)
        self._last_preference_decay_time = time.time()

    def execute_action(self, node_id: str):
        node_id = str(node_id).strip()
        if not node_id:
            return {
                "success": False,
                "error": "node_id 不能为空"
            }

        with self._lock:
            node = self.kg.nodes.get(node_id)

        if not node:
            return {
                "success": False,
                "error": f"节点不存在: {node_id}"
            }

        if getattr(node, "label", "") != "procedural":
            return {
                "success": False,
                "error": f"节点不是 procedural: {node_id}"
            }

        # ── 获取节点 execution 代码（回退用）──
        code = getattr(node, "execution", None)

        # ── 优先使用 Action 注册表 ──
        try:
            from Action import get_action_func
            func = get_action_func(node_id)
            if func is not None:
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                try:
                    with redirect_stdout(buf):
                        func_result = func()
                    out = buf.getvalue().rstrip()
                    if func_result:
                        out = out + ("\n" + str(func_result) if out else str(func_result))
                    if not out:
                        out = "(无输出)"

                    item = {
                        "time": now_str(),
                        "node_id": node_id,
                        "success": True,
                        "output": out,
                        "via": "Action注册表",
                    }
                    with self._lock:
                        self.execution_log.append(item)
                    return {
                        "success": True,
                        "output": out
                    }
                except Exception as e:
                    item = {
                        "time": now_str(),
                        "node_id": node_id,
                        "success": False,
                        "error": str(e),
                        "via": "Action注册表",
                    }
                    with self._lock:
                        self.execution_log.append(item)
                    return {
                        "success": False,
                        "error": str(e)
                    }
        except ImportError:
            pass

        # ── 回退：直接 exec 节点 execution 代码 ──

        if not code or not str(code).strip():
            return {
                "success": False,
                "error": f"节点 execution 为空且未注册 Action: {node_id}"
            }

        import io
        from contextlib import redirect_stdout

        safe_builtins = {
            # 受控 import（见模块顶部 ALLOWED_ACTION_IMPORTS）：
            # 裸 __import__ 会让 exec 沙箱形同虚设（__import__("os").system(...)）。
            "__import__": _action_safe_import,
            "print": print,
            "len": len,
            "range": range,
            "min": min,
            "max": max,
            "sum": sum,
            "sorted": sorted,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "dict": dict,
            "list": list,
            "set": set,
            "tuple": tuple,
            "enumerate": enumerate,
            "zip": zip,
        }

        buf = io.StringIO()
        g = {"__builtins__": safe_builtins}
        l = {"result": {}}

        try:
            with redirect_stdout(buf):
                exec(code, g, l)
            out = buf.getvalue().rstrip()
            if l.get("result", None) is not None:
                if out:
                    out = out + "\n" + str(l["result"])
                else:
                    out = str(l["result"])
            if not out:
                out = "(无输出)"

            item = {
                "time": now_str(),
                "node_id": node_id,
                "success": True,
                "output": out
            }
            with self._lock:
                self.execution_log.append(item)
            return {
                "success": True,
                "output": out
            }
        except Exception as e:
            item = {
                "time": now_str(),
                "node_id": node_id,
                "success": False,
                "error": str(e)
            }
            with self._lock:
                self.execution_log.append(item)
            return {
                "success": False,
                "error": str(e)
            }

    # =========================================================
    # 衰减
    # =========================================================

    def decay_step(self, clear_fired: bool = True):
        """空间感知衰减（Architecture Refactor 0.1: 类型感知；性能改造: 只衰减活跃前沿）。

        节点衰减率按 graph_space 差异化。
        边衰减率 = 源节点 space_lambda * relation_category decay_mult
        （源节点缺失——dangling 边——回退 "semantic"，与旧版 node_space_map
        缺省语义一致）。激活值低于 activation_epsilon 吸附为 0 并出前沿。

        clear_fired=False 用于 diffuse_round 内的步间衰减：衰减但不重置
        fire-once 记录（发射记号属于整个回合，不属于单步）。
        """

        logger.info("[衰减] 开始 (空间感知)")

        start = time.time()

        try:
            with self._lock:

                # 回合边界：清空 fire-once 记录（auto 模式下新一轮从衰减后开始）
                if clear_fired:
                    self._fired_round.clear()

                eps = self._eps()
                node_count = 0
                edge_count = 0
                space_decay_counts = {"semantic": 0, "episodic": 0, "cognitive": 0, "self": 0}

                for nid in list(self._active_nodes.keys()):
                    node = self.kg.nodes.get(nid)
                    if node is None:
                        self._active_nodes.pop(nid, None)
                        continue
                    if node.activation <= 0:
                        self._active_nodes.pop(nid, None)
                        continue
                    space = getattr(node, "graph_space", "semantic")
                    srules = self._space_rules(space)
                    space_lambda = srules["lambda_decay"]
                    node.activation *= (1.0 - space_lambda)
                    node_count += 1
                    space_decay_counts[space] = space_decay_counts.get(space, 0) + 1
                    if node.activation < eps:
                        node.activation = 0.0
                        self._active_nodes.pop(nid, None)

                for edge in list(self._active_edges):
                    if edge.activation <= 0:
                        self._active_edges.discard(edge)
                        continue
                    # 边衰减 = 源节点空间衰减 × 关系类别衰减倍率
                    src_node = self.kg.nodes.get(edge.src)
                    src_space = (getattr(src_node, "graph_space", "semantic")
                                 if src_node is not None else "semantic")
                    base_lambda = self._space_rules(src_space)["lambda_decay"]

                    edge_cat = getattr(edge, "relation_category", "semantic_relation")
                    crules = self._rel_rules(edge_cat)
                    decay_mult = crules.get("decay", 1.0)

                    edge_lambda = base_lambda * decay_mult
                    edge.activation *= (1.0 - edge_lambda)
                    edge_count += 1
                    if edge.activation < eps:
                        edge.activation = 0.0
                        self._active_edges.discard(edge)

                logger.info(
                    f"[衰减] 节点: {node_count} (各空间: {dict(space_decay_counts)}), "
                    f"边: {edge_count}"
                )

        except Exception as e:
            logger.error(f"[衰减异常] {e}")
            traceback.print_exc()

        logger.info(
            f"[衰减] 完成 "
            f"(耗时: {time.time() - start:.3f}s)"
        )

    def apply_inter_round_decay(self, factor: float, floor: float):
        """回合间衰减（app.py 聊天主路径调用）：前沿内节点/边统一乘 (1-factor)。

        数学语义与旧版全图循环逐位一致：前沿外节点/边激活恒为 0（EPS 吸附
        保证），旧版的 `> 0.001` 守卫对它们本就是空操作。低于 floor 的值
        归零并出前沿。不改行动队列——与旧行为一致（衰减后的资格重估留给
        后续 activate_from_inputs 的增量刷新）。
        """
        keep = 1.0 - factor
        with self._lock:
            for nid in list(self._active_nodes.keys()):
                node = self.kg.nodes.get(nid)
                if node is None:
                    self._active_nodes.pop(nid, None)
                    continue
                if node.activation > 0.001:
                    node.activation *= keep
                    if node.activation < floor:
                        node.activation = 0.0
                        self._active_nodes.pop(nid, None)
            for edge in list(self._active_edges):
                if edge.activation > 0.001:
                    edge.activation *= keep
                    if edge.activation < floor:
                        edge.activation = 0.0
                        self._active_edges.discard(edge)
