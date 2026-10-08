# capability_graph.py — Capability Graph: 能力·行动·执行器的图谱层
# ============================================================================
# Capability–Action–Executor 重构（2026-09-20）。
#
# 三权分立（§18）：
#   Action Concept（想做什么）＝图谱节点，被 Drive/状态/情绪经现有
#     diffuse_step 点亮（驱动/诱发/倾向 边）。
#   Capability（具备什么能力）＝图谱节点，概念的 需要 边指向它；
#     它把 ~105 个 executor 聚合成 ~20 个语义单元；条件节点经 抑制 边
#     使其路径暂时不可达；它本身**没有 available 布尔**——可达性是
#     "图结构 × 当前状态"的联合读数（§七）。
#   Executor（现实接口）＝ skills REGISTRY，唯一保留的代码注册表，
#     只回答"这个名字的实现是哪个函数"；能力→执行器的映射权威在
#     **能力节点的 executors 属性**（图是语义真相源，registry 是实现索引）。
#
# 链路：状态/Drive 点亮概念 → resolve 沿 需要→能力→executors∩caps →
#   gates（读图状态槽位的数值条件，数据求值非行为分支）→ 抑制检查 →
#   产出 ActionManager spec 草案。失败若因"没有实现接口"→ 能力缺口信号
#   上图（理解但做不到 §八；喂 capability_gap 张力 → LearningDrive）。
#
# 诚实边界（docs §8 否决项避让）：
#   * 资源前置的 需要→物品:x 边**不伪造**——由因果晋升/用户教学自然生长，
#     本模块只负责"有边就检查、无边就不武断拒绝"。
#   * 启动种子边是**可发现性**（与 先验/驱动 同类），不是固定触发管道；
#     真正的门槛在 gates 数值条件与执行安全层（留在 action_system）。
# ============================================================================

import logging
import time

from graph_model import Node, Edge, is_live_unknown

logger = logging.getLogger(__name__)

CAPABILITY_NODE_TYPES = ("capability",)
EMBODIED_CONCEPT_RELATIONS = ("诱发", "驱动")


def _cfg(config):
    return (config or {}).get("capability_graph") or {}


class CapabilityIndex:
    """能力图谱：种子写入 + 路径索引 + 候选发现（概念→能力→执行器）。

    无跨拍缓存：索引每次从图重建（~1600 边，决策节拍 ~10s，成本 ms 级），
    图是唯一真相源，不存在"缓存与图不一致"这类幽灵 bug。
    """

    def __init__(self, kg, engine=None, config=None):
        self.kg = kg
        self.engine = engine
        self.config = config or {}
        self._alias_remember = {}

    @property
    def enabled(self) -> bool:
        return _cfg(self.config).get("enabled", True) is not False

    # ── 启动种子（幂等；数据表在 config["capability_graph"]）────────

    def ensure_graph(self) -> dict:
        cfg = _cfg(self.config)
        if not self.enabled:
            return {"skipped": True}
        stats = {"capabilities": 0, "concepts": 0, "requires": 0,
                 "affordances": 0, "inhibitions": 0, "drive_edges": 0}
        # 1) 能力节点 + 实现于 载体边
        for cap_id, spec in (cfg.get("capabilities") or {}).items():
            node = self.kg.get_node(cap_id)
            if node is None:
                node = Node(
                    id=cap_id, weight=0.5, label="procedural",
                    graph_space="cognitive",
                    extra_attrs={"type": "capability",
                                 "channel": "embodied",
                                 "executors": list(spec.get("executors") or []),
                                 "description": spec.get("desc", ""),
                                 "dangerous": bool(spec.get("dangerous")),
                                 "created": time.strftime(
                                     "%Y-%m-%d %H:%M:%S")})
                self.kg.add_node(node)
                stats["capabilities"] += 1
            else:
                ea = dict(node.extra_attrs or {})
                ea.setdefault("type", "capability")
                ea.setdefault("executors",
                              list(spec.get("executors") or []))
                node.extra_attrs = ea
            carrier = spec.get("carrier")
            if (carrier and self.kg.get_node(carrier) is not None
                    and self.kg.get_edge(cap_id, carrier, "实现于") is None):
                self.kg.add_edge(Edge(src=cap_id, dst=carrier,
                                      relation="实现于", weight=0.7,
                                      relation_category="procedural_relation"))
        # 2) 新概念节点（procedural/semantic、认知不可见——与 action_concepts
        #    同契约；点亮走 诱发/驱动 边，不经 FAISS 召回）
        for cid, spec in (cfg.get("concepts") or {}).items():
            node = self.kg.get_node(cid)
            if node is None:
                node = Node(
                    id=cid, weight=0.5, label="procedural",
                    graph_space="semantic",
                    extra_attrs={"type": "action_concept",
                                 "action_key": cid,
                                 "channel": "embodied",
                                 "concept_id": cid,
                                 "name_zh": spec.get("name_zh", cid),
                                 "description":
                                     f"具身行动概念 {spec.get('name_zh', cid)}",
                                 "lifecycle": "established",
                                 "expressive": False,
                                 "gates": spec.get("gates") or [],
                                 "hints": spec.get("hints") or [],
                                 "priority": spec.get("priority"),
                                 "urgency": spec.get("urgency"),
                                 "motivation": spec.get("motivation",
                                                        "cognitive_state"),
                                 "preempts": bool(spec.get("preempts")),
                                 "provenance": "capability_graph_seed"})
                self.kg.add_node(node)
                stats["concepts"] += 1
            else:
                ea = dict(node.extra_attrs or {})
                for fld in ("gates", "hints", "priority", "urgency",
                            "motivation", "name_zh", "preempts"):
                    if spec.get(fld) is not None:
                        ea[fld] = spec[fld]
                ea.setdefault("type", "action_concept")
                ea.setdefault("channel", "embodied")
                ea.setdefault("action_key", cid)
                node.extra_attrs = ea
        # 3) 概念-[需要]->能力 边（旧 12 概念 + 新概念统一挂）
        req_map = dict(cfg.get("concept_requires") or {})
        for cid, spec in (cfg.get("concepts") or {}).items():
            req_map.setdefault(cid, spec.get("requires") or [])
        for cid, caps in req_map.items():
            if self.kg.get_node(cid) is None:
                continue
            for cap_id in caps:
                if self.kg.get_node(cap_id) is None:
                    continue
                if self.kg.get_edge(cid, cap_id, "需要") is None:
                    self.kg.add_edge(Edge(
                        src=cid, dst=cap_id, relation="需要", weight=0.6,
                        relation_category="procedural_relation"))
                    stats["requires"] += 1
        # 4) 状态-[诱发]->概念；Drive-[驱动]->概念
        for src, rows in (cfg.get("affordances") or {}).items():
            for cid, w in rows:
                if (self.kg.get_node(src) is not None
                        and self.kg.get_node(cid) is not None
                        and self.kg.get_edge(src, cid, "诱发") is None):
                    self.kg.add_edge(Edge(src=src, dst=cid, relation="诱发",
                                          weight=float(w),
                                          relation_category=
                                          "cognitive_relation"))
                    stats["affordances"] += 1
        for src, rows in (cfg.get("drive_concept_edges") or {}).items():
            for cid, w in rows:
                if (self.kg.get_node(src) is not None
                        and self.kg.get_node(cid) is not None
                        and self.kg.get_edge(src, cid, "驱动") is None):
                    self.kg.add_edge(Edge(src=src, dst=cid, relation="驱动",
                                          weight=float(w),
                                          relation_category=
                                          "cognitive_relation"))
                    stats["drive_edges"] += 1
        # 5) 条件-[抑制]->能力
        for src, cap_id, w in (cfg.get("inhibitions") or []):
            if (self.kg.get_node(src) is not None
                    and self.kg.get_node(cap_id) is not None
                    and self.kg.get_edge(src, cap_id, "抑制") is None):
                self.kg.add_edge(Edge(src=src, dst=cap_id, relation="抑制",
                                      weight=float(w),
                                      relation_category="causal_relation"))
                stats["inhibitions"] += 1
        logger.info(f"[CapabilityGraph] 种子就位: {stats}")
        return stats

    def sync_affordances(self) -> int:
        """状态槽位节点可能晚于启动出现（首次连 MC 才建）——定向补种
        缺失的 诱发/驱动 边（只查种子表的源存在性，成本可忽略）。"""
        if not self.enabled:
            return 0
        cfg = _cfg(self.config)
        created = 0
        for table, rel in (("affordances", "诱发"),
                           ("drive_concept_edges", "驱动")):
            for src_id, rows in (cfg.get(table) or {}).items():
                for cid, w in rows:
                    if self.kg.get_node(src_id) is None                             or self.kg.get_node(cid) is None:
                        continue
                    if self.kg.get_edge(src_id, cid, rel) is None:
                        self.kg.add_edge(Edge(src=src_id, dst=cid,
                                              relation=rel,
                                              weight=float(w),
                                              relation_category=
                                              "cognitive_relation"))
                        created += 1
        return created

    # ── 路径索引（每决策拍从图重建；图=唯一真相源）────────────────

    def build_index(self) -> dict:
        idx = {"concept_requires": {}, "cap_executors": {},
               "cap_inhibitors": {}, "affordances": {},
               "cap_requires_items": {}}
        with self.kg._lock:
            nodes, edges = self.kg.nodes, self.kg.edges
            for nid, node in nodes.items():
                ea = node.extra_attrs or {}
                if ea.get("type") == "capability":
                    idx["cap_executors"][nid] = list(ea.get("executors")
                                                     or [])
            for e in edges:
                if e.relation == "需要":
                    dst = nodes.get(e.dst)
                    dta = (dst.extra_attrs or {}) if dst else {}
                    if dta.get("type") == "capability":
                        idx["concept_requires"].setdefault(
                            e.src, []).append(e.dst)
                    elif str(e.dst).startswith("物品:"):
                        idx["cap_requires_items"].setdefault(
                            e.src, []).append((e.dst, float(e.weight)))
                elif e.relation == "抑制":
                    idx["cap_inhibitors"].setdefault(e.dst, []).append(
                        (e.src, float(e.weight)))
                elif e.relation in EMBODIED_CONCEPT_RELATIONS:
                    idx["affordances"].setdefault(e.dst, []).append(
                        (e.src, e.relation, float(e.weight)))
        return idx

    # ── 候选发现：被点亮的具身行动概念 → ActionManager spec 草案 ────

    def _caps_available(self):
        """embodiment 在位时其 capabilities() 为准（连接态真相：空集=
        离线，调用方按离线处理，不记缺口）；无 embodiment 才回退
        skills REGISTRY（离线单测/无具身部署）。"""
        emb = getattr(self, "embodiment", None)
        if emb is not None and callable(getattr(emb, "capabilities", None)):
            try:
                return set(emb.capabilities() or set()), True
            except Exception:
                return set(), False
        try:
            from skills import base as _sb
            return {s["name"] for s in _sb.all_skills()}, False
        except Exception:
            return set(), False

    def discover_candidates(self, min_src_activation: float = 0.01,
                            active_concepts: dict = None,
                            live: dict = None) -> list:
        """返回 spec 草案列表 [{action_type, concept, motivation, priority,
        urgency, reason[nodes], _salience_src}]。

        概念进入 = （诱发/驱动 源节点当前激活 ≥ 阈值）∧ gates 通过
        ∧ 能力路径可达（executors∩caps 非空 ∧ 无活跃抑制 ∧ 显式 需要→物品
        满足）。不可达原因分两类：执行接口缺失→能力缺口信号（做不到但
        理解）；状态抑制→静默（此刻不该做，不上升为"缺陷"）。

        live（可选）：实时感知缓存 dict{health, food, entities, players,
        unknown_entities...}。图槽位是真相源优先读；图节点缺失/无值时
        回退 live（离线单测与"感知尚未写图"的启动窗口）——回退只影响
        数值来源，不改变"概念是否可行由 gates+可达性决定"的结构。
        """
        if not self.enabled:
            return []
        try:
            self.sync_affordances()
        except Exception:
            pass
        idx = self.build_index()
        caps, from_embodiment = self._caps_available()
        if from_embodiment and not caps:
            return []          # 具身离线：不产候选也不报缺口（真相是"没连上"）
        out = []
        cfg = _cfg(self.config)
        with self.kg._lock:
            nodes = self.kg.nodes
            for cid, affs in idx["affordances"].items():
                node = nodes.get(cid)
                if node is None:
                    continue
                ea = node.extra_attrs or {}
                if ea.get("type") != "action_concept":
                    continue
                src_in_graph = [s for s, _r, _w in affs if s in nodes]
                src_act = max((float(nodes[s].activation or 0.0)
                               for s in src_in_graph), default=0.0)
                self_act = float(node.activation or 0.0)
                if src_in_graph:
                    # 图上有该状态槽：激活是真相源（无脉动=此刻无诱因）
                    if src_act < min_src_activation and self_act < 0.05:
                        continue
                #  affordance 源节点全不在图（离线单测/感知未写图窗口）：
                #  以 gates 数值条件为门（live 回退），不静默失效
                if not self._gates_ok(ea.get("gates") or [], live):
                    continue
                spec = self._resolve_concept(cid, ea, idx, caps, cfg)
                if spec:
                    spec["reason"] = list({*[s for s, _r, _w in affs],
                                           cid, *spec.get("reason", [])})
                    out.append(spec)
        return out

    def _resolve_concept(self, cid, ea, idx, caps, cfg):
        caps_needed = idx["concept_requires"].get(cid) or []
        execs = []
        if not caps_needed:
            # 未挂能力的概念（旧 12 概念的 executor 字段）：走 executor 属性
            ex = str(ea.get("executor") or "")
            execs = ([ex] if ex and not ex.startswith("inline:")
                     else list(ea.get("hints") or []))
        for cap in caps_needed:
            if self._cap_inhibited(cap, idx):
                return None          # 状态抑制：概念仍在，路径此刻关闭（§六）
            for x in idx["cap_executors"].get(cap, []):
                if x not in execs:
                    execs.append(x)
            for item, _w in idx["cap_requires_items"].get(cap, []):
                if not self._owns(item):
                    self.note_gap(cid, f"missing:{item}")
                    return None
        hints = [h for h in (ea.get("hints") or []) if h in execs] or \
            [h for h in (ea.get("hints") or [])]
        chosen = next((h for h in hints if h in caps), None) \
            or next((x for x in execs if x in caps), None)
        if chosen is None:
            # 能力路径有定义但现实接口缺失 → Capability Gap（不静默吞掉；
            # executors 为空的能力同样报——"没有会做这件事的接口"）
            self.note_gap(cid, f"no_executor:{'|'.join(execs[:4]) or '∅'}")
            return None
        if ea.get("dangerous"):
            # 2026-09-21 开放：危险概念可以成为认知候选（反制/冒险是
            # 可被想到的行动），但 spec 带 requires_kernel_check——
            # Safety Kernel 在执行前核验（目标是图上确认的危险因果、
            # 自身状态、授权证据）。"不能想到"从来不该是安全机制。
            spec_extra = {"requires_kernel_check": True}
        else:
            spec_extra = {}
        return {
            "action_type": chosen,
            "concept": cid,
            "motivation": ea.get("motivation") or "cognitive_state",
            "priority": ea.get("priority"),
            "urgency": ea.get("urgency"),
            "reason": [],
            **spec_extra,
        }

    def _cap_inhibited(self, cap, idx):
        thr = float(_cfg(self.config).get("suppression_min_activation", 0.3))
        with self.kg._lock:
            for src, _w in idx["cap_inhibitors"].get(cap, []):
                node = self.kg.nodes.get(src)
                if node is not None and float(node.activation or 0.0) >= thr:
                    return True
        return False

    def _owns(self, item_node_id):
        """背包图端点：物品:x 节点 value 数量 >0（缺背包节点=信息不足，
        不武断拒绝——交给技能层运行时校验与因果学习）。"""
        with self.kg._lock:
            node = self.kg.get_node(item_node_id)
            if node is None:
                return True
            try:
                return float((node.extra_attrs or {}).get("count",
                                                          1)) > 0
            except (TypeError, ValueError):
                return True

    # ── gates：数值条件求值（图槽位优先，live 感知缓存回退；
    #    ops 是小求值表——数据条件，不是行为 if 链）──────────────

    _LIVE_FIELD = {"Haru的血量": "health", "Haru的饥饿": "food"}

    def _gates_ok(self, gates, live: dict = None) -> bool:
        live = live or {}
        with self.kg._lock:
            nodes = self.kg.nodes
            for g in gates:
                op = g.get("op")
                try:
                    if op in ("<=", ">="):
                        node = nodes.get(g.get("node") or "")
                        v = (node.extra_attrs or {}).get("value") \
                            if node is not None else None
                        if v in (None, ""):
                            v = live.get(self._LIVE_FIELD.get(
                                str(g.get("node") or "")))
                        if v is None:
                            return False
                        v = float(v)
                        ok = v <= float(g["v"]) if op == "<=" \
                            else v >= float(g["v"])
                    elif op == "!=":
                        node = nodes.get(g.get("node") or "")
                        v = (node.extra_attrs or {}).get("value")                             if node is not None else None
                        ok = v is not None and str(v) != str(g.get("v"))
                    elif op == "health_above":
                        node = nodes.get("Haru的血量")
                        v = (node.extra_attrs or {}).get("value")                             if node is not None else None
                        if v is None and live:
                            v = live.get("health")
                        ok = v is not None and float(v) >= float(
                            g.get("v", 14))
                    elif op == "node_active":
                        node = nodes.get(g.get("node") or "")
                        ok = node is not None and float(
                            node.activation or 0) >= float(g.get("v", 0.05))
                    elif op == "unknown_present":
                        ok = any(
                            str(nid).startswith(("UnknownEntity_",
                                                 "UnknownBlock_",
                                                 "UnknownPlayer_"))
                            and float(nd.activation or 0.0) >= 0.5
                            and is_live_unknown(nd)
                            for nid, nd in nodes.items()) \
                            or bool(live.get("unknown_entities")
                                    or live.get("unknown_blocks"))
                    elif op == "hostile_within":
                        ok = False
                        slot = nodes.get("附近的生物")
                        dists = ((slot.extra_attrs or {}).get("hostile_dist")
                                 if slot else None) or {}
                        if dists:
                            ok = any(d <= float(g.get("v", 15))
                                     for d in dists.values())
                        else:
                            # live 回退：直接按窗口参数判最近敌对距离
                            # （与图槽位 hostile_dist 同一语义，不是 danger_visible
                            #  的贴脸窗——v 是这条 gate 自己的数据）
                            hostiles = {str(x).lower() for x in
                                        (self.config.get(
                                            "hostile_entities") or [])}
                            ok = any(
                                str(e.get("name") or "").lower() in hostiles
                                and float(e.get("dist", 99))
                                <= float(g.get("v", 15))
                                for e in (live.get("entities") or [])
                                if isinstance(e, dict))
                    elif op == "players_present":
                        slot = nodes.get("附近的玩家")
                        ok = bool((slot.extra_attrs or {}).get("value")
                                  not in (None, "", "无")) if slot \
                            else bool(live.get("players"))
                    else:
                        ok = True
                except Exception:
                    ok = False
                if not ok:
                    return False
        return True

    # ── 能力缺口（§八：理解但做不到 → 认知信号，可被学习利用）──────

    def note_gap(self, concept: str, reason: str):
        if not self.enabled:
            return
        cfg = _cfg(self.config)
        gid = str(cfg.get("gap_node") or "能力缺口")
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self.kg._lock:
                node = self.kg.get_node(gid)
                if node is None:
                    node = Node(
                        id=gid, weight=0.4, label="declarative-semantic",
                        graph_space="cognitive",
                        extra_attrs={"type": "capability_gap",
                                     "description": "能力缺口信号：某些"
                                                    "行动概念缺少现实接口"
                                                    "或所需资源",
                                     "gaps": {}})
                    self.kg.add_node(node)
                gaps = dict(node.extra_attrs.get("gaps") or {})
                gaps[concept] = {"reason": str(reason)[:80], "at": now}
                node.extra_attrs["gaps"] = gaps
                node.extra_attrs["last_gap_at"] = now
                node.activation = min(5.0, float(node.activation or 0.0)
                                      + float(cfg.get("gap_activation", 0.5)))
                node.touch()
            if self.engine is not None:
                self.engine.mark_active([gid])
                try:
                    self.engine.register_activation_source(
                        [gid], "internal_drive")
                except Exception:
                    pass
            logger.debug(f"[CapabilityGraph] gap: {concept} ← {reason}")
        except Exception as e:
            logger.debug(f"[CapabilityGraph] gap 信号失败: {e}")

    def gap_count(self) -> int:
        cfg = _cfg(self.config)
        with self.kg._lock:
            node = self.kg.get_node(str(cfg.get("gap_node") or "能力缺口"))
            return len((node.extra_attrs or {}).get("gaps") or {}) \
                if node else 0

    # ── executor 归属学习（溯源：能力"怎么学会的"§8 诚实要求）────────

    def record_execution(self, concept: str, executor: str,
                         action_node_id: str, success: bool,
                         context: str = None):
        """结算回写：能力节点 learned_from 溯源 + 概念 touch。
        成功率统计**不新建账本**——causal action_prior 已有，这里只挂
        可追溯的节点 id 环（≤10）。

        B6/§12：context 是结算时的**条件签名**（B0 context_signature 同源，
        由调用方算好传入）。按条件计 ok/fail 次数（≤16 桶）——"会做"变成
        "什么条件下做过几次"，供 experience_summary 只读派生；不改评分。"""
        with self.kg._lock:
            caps = ([e.dst for e in self.kg.edges
                     if e.src == concept and e.relation == "需要"]
                    if self.kg.get_node(concept) is not None else [])
            for cap in caps:
                node = self.kg.get_node(cap)
                if node is None:
                    continue
                ea = dict(node.extra_attrs or {})
                lf = list(ea.get("learned_from") or [])
                if action_node_id and action_node_id not in lf:
                    lf.append(action_node_id)
                    ea["learned_from"] = lf[-10:]
                ea["last_executed"] = time.strftime("%Y-%m-%d %H:%M:%S")
                if context:
                    ctxs = dict(ea.get("exec_contexts") or {})
                    ent = dict(ctxs.get(context) or {"ok": 0, "fail": 0})
                    ent["ok" if success else "fail"] = \
                        int(ent.get("ok" if success else "fail", 0)) + 1
                    ctxs[context] = ent
                    if len(ctxs) > 16:   # 有界：留累计量最大的条件桶
                        ctxs = dict(sorted(
                            ctxs.items(),
                            key=lambda kv: -(int(kv[1].get("ok", 0))
                                             + int(kv[1].get("fail", 0))))[:16])
                    ea["exec_contexts"] = ctxs
                node.extra_attrs = ea

    def experience_summary(self, node_id: str, causal=None) -> dict:
        """B6/§14 能力经验读端点：纯派生视图，不写任何东西。

        图上挂着什么就读什么（learned_from 环 / last_executed / 条件计数），
        再可选合并 causal 的成功率账本（传入 causal 才合，不自己找）。
        """
        with self.kg._lock:
            n = self.kg.get_node(node_id)
            if n is None:
                return {"node": node_id, "found": False}
            ea = n.extra_attrs or {}
            caps = ([e.dst for e in self.kg.edges
                     if e.src == node_id and e.relation == "需要"])
            out = {"node": node_id, "found": True, "type": ea.get("type"),
                   "learned_from": list(ea.get("learned_from") or []),
                   "last_executed": ea.get("last_executed"),
                   "exec_contexts": dict(ea.get("exec_contexts") or {}),
                   "capabilities": caps}
        if causal is not None:
            try:
                out["prior"] = causal.action_prior(node_id)
            except Exception:
                pass
        return out
