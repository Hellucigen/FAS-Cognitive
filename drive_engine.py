# drive_engine.py — Drive System 兼容壳：CognitiveField 的图谱端点
# ============================================================================
# 2026-09-20 Drive 重构：Drive 不再是"某类需求的分数"，而是
#   Tension（既有系统读数的持续张力场）
#     → Drive（张力亲和度聚合，多驱并存、软竞争、时间惯性）
#       → Network（CEN/DMN 网络调制层）
#         → Modulation（各系统参数的闭环调制）
# 的核心中间层。动力学在 cognitive_field.py / tension_field.py /
# drive_field.py / cognitive_networks.py / modulation.py；
# 本模块只负责三件事：
#   1. 采样接线：provider 闭包 + 图谱扫描 → TensionField samplers
#   2. 图谱写回：Drive level → Drive 节点激活（mark_active +
#      register_activation_source("internal_drive")，参与扩散与行为竞争）
#   3. 兼容视图：旧 DriveEvaluator API / evaluate() 返回结构原样保留
#      （drives/dominant 为派生兼容字段，不再是控制逻辑）
#
# 语义不变量（设计原则）：
#   * Drive 不执行行为——只改变认知场与行为竞争
#   * 不建平行状态库——所有读数来自图谱/需求/激素/失败账/预测基线
#   * 激素是调制器（上升/消退速率、亲和度增益），不是 Drive 加数
# ============================================================================

import logging
import time
from typing import Optional

from graph_model import KnowledgeGraph, Node, Edge, now_str, is_live_unknown
from cognitive_field import CognitiveField

logger = logging.getLogger(__name__)

# ── Drive 节点常量（兼容契约：节点名不变）───────────────────

DRIVE_NODE_IDS = {
    "curiosity": "CuriosityDrive",
    "social": "SocialDrive",
    "learning": "LearningDrive",
    "consistency": "ConsistencyDrive",
}

# discrepancy 信号节点（来自 curiosity_engine 检测层）
CURIOSITY_SIGNAL_NODES = [
    "未知概念", "未知关系", "未知属性", "未知事件", "未知信息",
    "好奇",
]

# 兼容保留：旧信号通路节点（uncertainty 张力的采样落点）
_LEGACY_CHAIN_NODES = ["好奇", "生成好奇问题"]

# 图谱 Unknown* 目标前缀（具身感知/探索落图的新颖对象）
_UNKNOWN_PREFIXES = ("UnknownObject", "UnknownEntity", "UnknownPlayer",
                     "UnknownBlock", "UnknownConcept")

# 激素类 provider：进调制器通道（register_state），不进张力加数。
# 兜底集合（config 缺 modulator_system 时用，如离线测试）；生产环境的判据是
# **规格里有没有这个名字**（R2：消除闭合名单式词汇，见审计 Q9.8）。
_HORMONE_NAMES = {"dopamine", "oxytocin", "serotonin", "cortisol"}

# 因子标签（可解释性展示，纯输出层；张力名 → fn(raws, contrib) → str|None）
def _label_discrepancy(raws, contrib):
    note = (raws.get("graph.discrepancy") or {}).get("note") or {}
    n = int(note.get("count", 0) or 0)
    return f"{n} 个 discrepancy 信号活跃" if n else None


def _label_novelty(raws, contrib):
    n_obj = int((raws.get("novel_objects") or {}).get("raw", 0) or 0)
    note = (raws.get("graph.unknown_objects") or {}).get("note") or {}
    n = n_obj + int(note.get("count", 0) or 0)
    return f"{n} 个未知/新颖对象" if n else None


def _label_interest(raws, contrib):
    note = (raws.get("graph.interest") or {}).get("note") or {}
    n = int(note.get("count", 0) or 0)
    if not n:
        return None
    return f"{n} 个目标带兴趣水位（均值 {float(note.get('mean', 0)):.2f}）"


def _label_satiation(raws, contrib):
    return f"满足感抑制 {contrib:.2f}"


def _label_prediction_error(raws, contrib):
    raw = float((raws.get("prediction_surprise") or {}).get("raw", 0) or 0)
    return f"世界演化偏离预期（surprise {raw:.2f}）" if raw > 0.05 else None


def _label_unfinished(raws, contrib):
    raw = int(float((raws.get("recent_goal_failures") or {}).get("raw", 0) or 0))
    return f"最近自身目标失败 x{raw}" if raw else None


def _label_blocked(raws, contrib):
    raw = int(float((raws.get("causal_blockers") or {}).get("raw", 0) or 0))
    return f"已知阻碍（如缺工具）x{raw}" if raw else None


def _label_self_contradiction(raws, contrib):
    raw = int(float((raws.get("pending_reflection_candidates") or {})
                    .get("raw", 0) or 0))
    return f"{raw} 个待处理反思候选" if raw else None


def _label_negative(raws, contrib):
    raw = float((raws.get("recent_negative_reward_ratio") or {})
                .get("raw", 0) or 0)
    return f"近期负性经历占 {raw:.0%}" if raw > 0.2 else None


def _label_mood_low(raws, contrib):
    note = (raws.get("state.mood_deficit") or {}).get("note") or {}
    val = note.get("valence")
    return f"心情偏低（{float(val):.2f}）" if isinstance(
        val, (int, float)) and val < -0.1 else None


def _label_social_absence(raws, contrib):
    raw = float((raws.get("social_idle_minutes") or {}).get("raw", 0) or 0)
    return f"{raw:.0f} 分钟没互动" if raw > 0 else None


def _label_belonging(raws, contrib):
    raw = float((raws.get("social_need") or {}).get("raw", 0) or 0)
    return f"社交需求水位 {raw:.2f}" if raw > 0.3 else None


def _label_presence(raws, contrib):
    raw = int(float((raws.get("players_present") or {}).get("raw", 0) or 0))
    return f"同伴在场 x{raw}" if raw > 0 else None


FACTOR_LABELS = {
    "discrepancy": _label_discrepancy,
    "novelty": _label_novelty,
    "unresolved_interest": _label_interest,
    "satiation": _label_satiation,
    "prediction_error": _label_prediction_error,
    "unfinished_goal": _label_unfinished,
    "blocked_action": _label_blocked,
    "self_contradiction": _label_self_contradiction,
    "negative_experience": _label_negative,
    "mood_low": _label_mood_low,
    "social_absence": _label_social_absence,
    "belonging_need": _label_belonging,
    "social_presence": _label_presence,
}


class DriveEvaluator:
    """驱动力评估器（兼容壳）。

    对外 API 与旧版一致：evaluate / get_drive_state / get_all_drives /
    get_dominant_drive / set_signal_provider / bootstrap_drives。
    内部换成 CognitiveField：每拍 step 推进
    Tension→Drive→Network→Modulation 四层动力学，
    Drive 激活写回图谱节点参与扩散（drive 不直接执行行为）。
    """

    def __init__(self, kg: KnowledgeGraph, config: dict):
        self.kg = kg
        self.config = config or {}
        self.engine = None      # 扩散引擎（app 装配注入；直写激活须 mark_active）
        self._last_eval_time = 0.0
        self._eval_cache = {}
        self._providers = {}
        self._dom_logged = None   # fas_log DRIVE 观测：上次记录的主导驱（仅变化时发）
        # 调制器名字集合 = config 规格（缺段时回退历史 4 个名字，行为不变）
        self._modulator_names = set(
            ((self.config.get("modulator_system") or {}).get("specs") or {}).keys()
        ) or set(_HORMONE_NAMES)
        self.field = CognitiveField(self.config)
        self._register_graph_samplers()
        # 即时语境/激素读数的兼容转接口（app 用 set_signal_provider 统一接线）
        self.field.register_sampler(
            "state.mood_deficit",
            lambda: self._mood_deficit())

    # ── 信号接线 ──────────────────────────────────────────

    def set_signal_provider(self, name: str, fn):
        """注册一个既有系统读数。语义与旧版一致：
        普通信号 → 张力采样器；调制器名 → 调制器通道（速率/增益），不做加数。

        "是不是调制器"的判据来自 config 的调制器规格（R2），不再是一串硬编码名字。
        """
        self._providers[name] = fn
        if name in self._modulator_names:
            self.field.register_state(name, fn)
        else:
            self.field.register_sampler(name, fn)

    def set_context_provider(self, name: str, fn):
        """网络/调制的即时语境读数（task_engaged / time_since_task /
        player_near / idle_minutes / arousal / stress…），0~1 或秒数。"""
        self.field.register_context(name, fn)

    def set_context_value(self, name: str, value: float):
        """CC 每拍直接喂语境值（arousal/stress），并即时刷新调制参数。"""
        self.field.set_context_value(name, value)

    def register_need(self, name: str, fn):
        """需求水位 → `need.<name>` 信号（R2 P9，审计 D-4 的收口）。"""
        self.field.register_need(name, fn)

    def set_graph_bias_source(self, fn):
        """图上的调制器→网络/驱动 偏置取值函数（modulator_subgraph.graph_biases）。
        在 `evaluate()` 的每次真实 step 里生效（不在 5s 缓存命中时重复取）。"""
        self.field.set_bias_source(fn)

    def refresh_modulation(self):
        self.field.refresh_modulation()

    def _sig(self, name, default=0.0):
        fn = self._providers.get(name)
        if fn is None:
            return default
        try:
            return fn()
        except Exception:
            return default

    def _mood_deficit(self):
        val = self._sig("mood_valence", 0.0)
        try:
            val = float(val)
        except (TypeError, ValueError):
            return 0.0
        return (max(0.0, -val), {"valence": round(val, 3)})

    def _register_graph_samplers(self):
        """图谱读数采样器：张力尽量长在图上（activation 场即状态源）。"""
        kg = self.kg

        def s_discrepancy():
            count, total = 0, 0.0
            signals = {}
            with kg._lock:
                for nid in CURIOSITY_SIGNAL_NODES:
                    node = kg.get_node(nid)
                    if node and node.activation > 0.01:
                        count += 1
                        total += node.activation
                        signals[nid] = round(node.activation, 4)
            mean = total / count if count else 0.0
            intensity = min(1.0, 0.22 * count + 0.45 * mean)
            return intensity, {"count": count, "mean": round(mean, 3),
                               "signals": signals}

        def s_unknown_objects():
            # B3 饱食链：已被经验"认出来"的（retired）不再计数——否则
            # 认识了 cow 好奇心照样钉满（好奇永不饱食的驱动端病灶）。
            with kg._lock:
                count = sum(
                    1 for nid, node in kg.nodes.items()
                    if nid.startswith(_UNKNOWN_PREFIXES)
                    and node.activation > 0.01
                    and is_live_unknown(node))
            return min(1.0, 0.25 * count), {"count": count}

        def s_interest():
            levels = []
            with kg._lock:
                for node in kg.nodes.values():
                    it = (node.extra_attrs or {}).get("interest")
                    if isinstance(it, dict) and it:
                        lv = float(it.get("level", 0.0) or 0.0)
                        if lv > 0.01:
                            levels.append(lv)
            if not levels:
                return 0.0, {"count": 0, "mean": 0.0}
            mean = sum(levels) / len(levels)
            intensity = min(1.0, mean * (1.0 + 0.3 * (len(levels) - 1)))
            return intensity, {"count": len(levels), "mean": round(mean, 3)}

        def s_satiation():
            totals = []
            with kg._lock:
                for node in kg.nodes.values():
                    it = (node.extra_attrs or {}).get("interest")
                    if isinstance(it, dict) and it:
                        totals.append(min(1.0, float(
                            it.get("resolved_count", 0) or 0) * 0.4))
            if not totals:
                return 0.0, {}
            return min(1.0, sum(totals) / len(totals)), {"mean": round(
                sum(totals) / len(totals), 3)}

        def s_pending_inquiry():
            try:
                import curiosity_engine as _ce
                return 1.0 if _ce.is_curiosity_active(kg) else 0.0
            except Exception:
                return 0.0

        def s_uncertainty():
            acts = []
            with kg._lock:
                for nid in _LEGACY_CHAIN_NODES:
                    node = kg.get_node(nid)
                    if node and node.activation > 0.01:
                        acts.append(node.activation)
            return (min(1.0, sum(acts) / len(acts)) if acts else 0.0), \
                {"mean": round(sum(acts) / len(acts), 3) if acts else 0.0}

        def s_capability_gap():
            """能力缺口信号（能力图谱 2026-09-20）：概念存在但现实
            接口/资源缺失的条目数与节点激活——'理解但做不到'进入学习张力。"""
            with kg._lock:
                node = kg.get_node("能力缺口")
                if node is None:
                    return 0.0, {}
                gaps = (node.extra_attrs or {}).get("gaps") or {}
                return min(1.0, float(node.activation or 0.0) / 5.0), \
                    {"count": len(gaps)}

        def s_exploration_gap():
            """探索缺口信号（P3/§5）：活缺口的代表性激活与缺口数——
            '已知对象但用途未知'进入好奇张力。量程与 s_capability_gap 同
            （activation 0~5 → 0~1；hub 封顶 2.5 → 常态最多 0.5）。"""
            with kg._lock:
                node = kg.get_node("探索缺口")
                if node is None:
                    return 0.0, {}
                gaps = (node.extra_attrs or {}).get("gaps") or {}
                # 只读 hub 会饿死张力：hub 在维护拍之间自然衰减，而缺口节点
                # 被 gap_attention_floor 托在 0.9（2026-09-25 实测 3 个活缺口
                # 只读出 0.09）。取 hub 与各活缺口的最大激活。
                best = float(node.activation or 0.0)
                for g in gaps.values():
                    gn = kg.nodes.get((g or {}).get("gap"))
                    if gn is not None and not (gn.extra_attrs or {}).get("closed"):
                        best = max(best, float(gn.activation or 0.0))
                return min(1.0, best / 5.0), {"count": len(gaps)}

        def s_cognitive_events():
            """**意外**的图上一手证据：`事件类型:prediction_violation` 节点的激活。
            （审计 D-11：`unexpected_event` 张力的 component 从 2026-09-20 就声明要
            这个源，但采样器从来没注册过 ⇒ 这一路张力恒 0，是个"假旋钮"。）
            为什么只读这一个节点：`cognitive_field` 里 `unexpected_event` 的语义是
            "预期被打破"，R2 事件词汇表里对应的就是 `prediction_violation`
            （config.modulator_system.event_rules）；受阻/被打断各有自己的调制边，
            不在这里合并——合并就把三件事说成一件事了。量程换算与其余 graph.* 采样器
            一致（activation 0~5 → 0~1）。"""
            with kg._lock:
                node = kg.get_node("事件类型:prediction_violation")
                if node is None:
                    return 0.0, {}
                return min(1.0, float(node.activation or 0.0) / 5.0), \
                    {"activation": round(float(node.activation or 0.0), 3)}

        for name, fn in (("graph.discrepancy", s_discrepancy),
                          ("graph.unknown_objects", s_unknown_objects),
                          ("graph.interest", s_interest),
                          ("graph.satiation", s_satiation),
                          ("graph.pending_inquiry", s_pending_inquiry),
                          ("graph.uncertainty", s_uncertainty),
                          ("graph.capability_gap", s_capability_gap),
                          ("graph.exploration_gap", s_exploration_gap),
                          ("graph.cognitive_events", s_cognitive_events)):
            self.field.register_sampler(name, fn)

    # ── 核心评估 ──────────────────────────────────────────

    def evaluate(self, force: bool = False) -> dict:
        """推进全场一次并派生旧版结构。

        force=True（回合内需要新鲜读数）→ settle：各场一步到位；
        周期调用（CC）→ 5s 缓存 + 动力学推进（张力/驱动/网络的持续性与
        迟滞存在于非强制路径）。
        """
        now = time.time()
        if not force and now - self._last_eval_time < 5.0:
            return self._eval_cache
        try:
            self.field.step(settle=force)
        except Exception as e:
            logger.warning(f"[Drive] CognitiveField.step 失败（缓存旧读数）: {e}")
        result = self._build_result()
        self._eval_cache = result
        self._last_eval_time = now
        return result

    def _build_result(self) -> dict:
        field = self.field
        raws = field.tensions.raws()
        drives = []
        for name in field.drives.spec_names():
            snap = field.drives.snapshot().get(name) or {}
            activation = float(snap.get("activation", 0.0))
            node_id = snap.get("node") or field.drives.node_id(name) or \
                DRIVE_NODE_IDS.get(name, name)
            self._apply_drive_activation(node_id, activation)
            factors = []
            for tname, contrib in (snap.get("contributions") or {}).items():
                label_fn = FACTOR_LABELS.get(tname)
                if label_fn is None:
                    if abs(contrib) > 0.05:
                        factors.append(f"{tname} {contrib:.2f}")
                    continue
                line = label_fn(raws.get(tname, {}), contrib)
                if line:
                    factors.append(line)
            entry = {
                "drive": name,
                "drive_id": node_id,
                "activation": round(activation, 4),
                "factors": factors,
                "description": snap.get("description", ""),
                "components": snap.get("contributions") or {},
                "level": snap.get("level", 0.0),
            }
            if name == "curiosity":
                entry["signals"] = ((raws.get("discrepancy") or {})
                                    .get("note") or {}).get("signals") or {}
            drives.append(entry)
        drives.sort(key=lambda d: d["activation"], reverse=True)
        try:
            self.step_network_markers()   # 网络 level → marker 节点（观测）
        except Exception:
            pass
        result = {
            "drives": drives,
            "dominant": drives[0] if drives else None,
            "timestamp": now_str(),
            "networks": field.networks.levels(),
            "tensions": field.tensions.levels(),
            "action_tendencies": field.action_tendencies(),
        }
        # 观测（2026-09-22 收尾补）：DRIVE 段此前零 emit。仅在**主导驱变化**
        # 时发一条（首拍也发，作为基线），避免 5s 缓存节拍刷屏。
        try:
            dom = result["dominant"] or {}
            name = dom.get("drive")
            if name != self._dom_logged:
                import fas_log
                fas_log.get_logger(fas_log.DRIVE).info(
                    "dominant_shift",
                    f"主导驱 {self._dom_logged or '-'} → {name}",
                    from_=self._dom_logged, to=name,
                    activation=dom.get("activation"),
                    top=[(d["drive"], d["activation"]) for d in drives[:4]])
                self._dom_logged = name
        except Exception:
            pass
        return result

    # ── Drive 节点管理（图谱写回 = Drive 的唯一输出）──────

    def _apply_drive_activation(self, drive_id: str, activation: float):
        """将驱动场 level 换算的激活写回 Drive 节点（参与扩散）。"""
        node = self.kg.get_node(drive_id)
        if node is None:
            node = Node(
                id=drive_id, weight=0.5, label="declarative-semantic",
                graph_space="cognitive",
                extra_attrs={
                    "type": "drive",
                    "drive_type": drive_id.lower().replace("drive", ""),
                    "status": "active",
                    "description": "认知张力聚合的持续驱动力",
                }
            )
            self.kg.add_node(node)
            self.kg.add_edge(Edge(
                src="Self", dst=drive_id, relation="驱动力",
                weight=0.5, relation_category="cognitive_relation"
            ))
        node.activation = min(5.0, max(0.0, activation))
        node.touch()
        if self.engine is not None:
            self.engine.mark_active([node.id])
            # 驱动力是本轮的内源激活（internal_drive）：发射免扣
            self.engine.register_activation_source([node.id], "internal_drive")

    def bootstrap_drives(self):
        """创建 Drive / 网络 marker 节点（幂等；旧存档就地修正）。"""
        for drive_type, node_id in DRIVE_NODE_IDS.items():
            node = self.kg.get_node(node_id)
            if node is None:
                node = Node(
                    id=node_id, weight=0.5, label="declarative-semantic",
                    graph_space="cognitive",
                    extra_attrs={
                        "type": "drive", "drive_type": drive_type,
                        "status": "active",
                    }
                )
                self.kg.add_node(node)
                self.kg.add_edge(Edge(
                    src="Self", dst=node_id, relation="驱动力",
                    weight=0.5, relation_category="cognitive_relation"
                ))
                logger.info(f"[Drive] + Drive 节点: {node_id}")
            else:
                # 旧存档迁移：planned→active、semantic→cognitive（幂等）
                ea = node.extra_attrs or {}
                if ea.get("status") != "active":
                    ea["status"] = "active"
                ea.setdefault("drive_type", drive_type)
                ea.setdefault("type", "drive")
                node.extra_attrs = ea
                if getattr(node, "graph_space", None) != "cognitive":
                    node.graph_space = "cognitive"

        # 认知网络 marker 节点（可观测；网络的效力走 ModulationLayer，
        # 不经这些节点的扩散——刻意不建扩散边）
        for net_name, desc in (
                ("CENetwork", "中央执行网络 — 任务专注的组织态"),
                ("DMNetwork", "默认模式网络 — 内部联想/回忆的组织态")):
            if self.kg.get_node(net_name) is None:
                self.kg.add_node(Node(
                    id=net_name, weight=0.3, label="declarative-semantic",
                    graph_space="cognitive",
                    extra_attrs={"type": "cognitive_network",
                                 "status": "active", "description": desc}))
                self.kg.add_edge(Edge(
                    src="Self", dst=net_name, relation="网络",
                    weight=0.3, relation_category="cognitive_relation"))

        logger.info("[Drive] Drive/CognitiveField 基础设施已就位")

        # 决策通路边（幂等补建，bootstrap 顺序无关）
        try:
            import curiosity_engine as _ce
            _ce.ensure_drive_signal_edges(self.kg)
        except Exception as _e:
            logger.warning(f"[Drive] 决策通路边补建失败: {_e}")
        try:
            from disposition_store import ensure_competition_edges
            ensure_competition_edges(self.kg)
        except Exception as _e:
            logger.warning(f"[Drive] 行为竞争通路补建失败: {_e}")

    def step_network_markers(self):
        """把网络 level 写入 marker 节点（观测/调试用；不参与扩散边）。"""
        marker_of = {"CENetwork": "CEN", "DMNetwork": "DMN"}
        for net_name, key in marker_of.items():
            node = self.kg.get_node(net_name)
            if node is not None:
                node.activation = round(
                    self.field.networks.level(key) * 5.0, 4)
                node.touch()
                # 收尾修复 2026-09-22：镜像激活直写进前沿受衰减管理
                # （不变量；标记节点无出边，不参与发射，只是别让残值
                # 在 evaluate 停跑后永久滞留 runtime_graph）
                try:
                    self.engine.mark_active([node.id])
                except Exception:
                    pass

    # ── 查询接口 ──────────────────────────────────────────

    def get_dominant_drive(self) -> dict:
        """兼容字段：派生视图，不是控制逻辑（新代码禁止消费）。"""
        result = self.evaluate()
        dominant = result.get("dominant")
        if dominant is None:
            return {"drive": None, "activation": 0.0,
                    "description": "当前无显著内部驱动力"}
        return dominant

    def get_all_drives(self) -> list:
        return self.evaluate().get("drives", [])

    def get_drive_state(self) -> dict:
        result = self.evaluate()
        return {
            # 兼容视图
            "drives": result["drives"],
            "dominant": result["dominant"],
            "timestamp": result["timestamp"],
            "description": self._generate_natural_description(result),
            # 升级结构
            "tensions": result.get("tensions"),
            "networks": result.get("networks"),
            "action_tendencies": result.get("action_tendencies"),
            "modulation": self.field.modulation.snapshot(),
        }

    def modulation(self):
        """调制层引用（扩散/预算/行为竞争等消费者接线入口）。"""
        return self.field.modulation

    def _generate_natural_description(self, result: dict) -> str:
        dominant = result.get("dominant")
        nets = result.get("networks") or {}
        tail = ""
        if nets:
            cen, dmn = nets.get("CEN", 0.0), nets.get("DMN", 0.0)
            if cen > 0.55 and cen > dmn:
                tail = "（注意力收拢在当下任务上）"
            elif dmn > 0.55 and dmn > cen:
                tail = "（神思有些发散，偏向回忆和联想）"
            elif cen > 0.4 and dmn > 0.4:
                tail = "（专注和联想同时活跃着）"
        if dominant is None:
            return f"当前没有特别想做的事情。{tail}"
        drive = dominant["drive"]
        factors = dominant.get("factors", [])
        activation = dominant["activation"]
        if drive == "curiosity":
            if activation < 0.5:
                return f"当前好奇心水平较低，对已有知识感到基本满足。{tail}"
            elif activation < 1.5:
                return f"稍微有点好奇——{'; '.join(factors)}。{tail}"
            elif activation < 3.0:
                return f"有较强的求知欲——{'; '.join(factors)}。想了解更多。{tail}"
            else:
                return f"好奇心非常强烈——{'; '.join(factors)}。迫切想搞清楚这些问题。{tail}"
        return f"驱动力: {drive} (激活度: {activation:.2f}){tail}"
