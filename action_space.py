# action_space.py — Action Space: 行动概念的图谱统一层
# ============================================================================
# 行为/行动决策重构（2026-09-20）：Action Concept 成为知识图谱一等公民。
#
# 原则：
#   * 动力来源开放、行动概念开放——候选宇宙来自图谱扫描（激活 + 供性边），
#     不来自枚举。LEGACY 10 行为只是**启动期先验边种子的作者**；
#     运行时的控制逻辑里不存在"遍历行为键"。
#   * 不建第二套扩散、不建第二套权重系统：行动节点靠现有
#     先验(情境→)/激活(学习后情境→)/驱动(Drive→)/倾向(情绪→)/
#     包含·可实现为(本体) 边被现有 diffuse_step 点亮；
#     人格学习继续写 情境-[激活 w=strength]->行动 边（disposition 通道），
#     新行动概念的 pair 自动获得同一学习载体。
#   * 概念可被提议：新表达 → 规范化 → 别名边 → 嵌入相似 → 复用 ∨ 创建
#     proposed 节点。executor 缺失 = "能理解但暂做不到"（沿用 FILE_READ
#     既有语义），执行不了不污染执行面。
#   * 执行方式统一声明（language/minecraft/tool/perception/memory/internal），
#     但两个竞技场保留：语言行动在 dialogue_decide 竞争（decision 字符串是
#     对外契约），具身行动在 ActionManager 闸门执行——统一发生在
#     registry / schema / trace / 学习 层，不强行合并裁判。
#
# 节点约定：
#   communication 行动 = label "disposition", graph_space "self"
#     （继承 λ=0.01 常驻、软遗忘豁免、tendencies/竞争全套既有机制）
#   embodied 概念沿用 action_concepts 既有节点（procedural/semantic，
#     刻意不进 name_to_node/行动队列——该契约不动）
#   extra_attrs: {"type": "behavior"|"action_concept"|"action_proposed",
#                 "action_key", "channel", "lifecycle", "expressive",
#                 "name_zh", ...}
#   lifecycle: proposed→observed→established→weakened→deprecated
#     （数值规则全在 config["action_space"]；deprecated 退出候选但节点保留）
# ============================================================================

import logging
import time

from graph_model import Node, Edge

logger = logging.getLogger(__name__)

ACTION_NODE_TYPES = ("behavior", "action_concept", "action_proposed")
LIFECYCLE_STATES = ("proposed", "observed", "established",
                    "weakened", "deprecated")

# 供性关系（源 → 行动）：语义与学习角色。都是认知关系，参与正常扩散；
# 学习写权只发生在 激活（disposition apply_experience 既有通道）。
# 诱发（能力图谱 2026-09-20）：世界状态节点→行动概念的可发现性边。
AFFORDANCE_RELATIONS = ("先验", "激活", "驱动", "倾向", "诱发")

_STOP_CHARS = "的了呢吗吧呀啊哦哈吧请帮我你他她它一下那个这个就都还也都很"


def action_key(node_id: str, node=None) -> str:
    """学习/配对用的稳定短键。优先 extra_attrs.action_key（兼容旧
    disposition 节点的 "key" 字段——行为:respond 节点存的是 key:respond）；
    否则去族前缀（行为:追问→追问、行动:X→X、FOLLOW→FOLLOW）。
    与既有 pair 命名 `倾向:<key>@<ctx>` 完全兼容。"""
    ea = (getattr(node, "extra_attrs", None) or {}) if node is not None else {}
    k = ea.get("action_key") or ea.get("key")
    if k:
        return str(k)
    for pre in ("行为:", "行动:", "行动族:"):
        if node_id.startswith(pre):
            return node_id[len(pre):]
    return node_id


def is_action_node(node) -> bool:
    ea = getattr(node, "extra_attrs", None) or {}
    return ea.get("type") in ACTION_NODE_TYPES


def _cfg(config: dict) -> dict:
    return (config or {}).get("action_space") or {}


class ActionSpace:
    """行动空间：视图 + 薄写入方。真状态全在图谱（节点/边/激活）与
    disposition pair（学习），这里不存第二份权重。"""

    def __init__(self, kg, engine=None, config: dict = None,
                 embedder=None):
        self.kg = kg
        self.engine = engine
        self.config = config or {}
        self.embedder = embedder       # 可选：嵌入检索（概念去重）
        self._alias_cache = {}          # 归一表面 → action_key（进程级）

    @property
    def enabled(self) -> bool:
        return _cfg(self.config).get("enabled", True) is not False

    # ── 启动：供性边与最小本体（表在 config，代码零清单）──────────

    def ensure_action_space(self) -> dict:
        """幂等种入 Drive/情绪→行动 供性边与行动族本体。

        权重是**先验种子**不是控制常数——运行时学习（激活边）与扩散会盖过
        它们；新增行动概念不需要回到这里。
        """
        cfg = _cfg(self.config)
        if not self.enabled:
            return {"skipped": True}
        stats = {"drive_edges": 0, "emotion_edges": 0, "hierarchy": 0}
        for src, targets in (cfg.get("drive_edges") or {}).items():
            for tnode, w in (targets or []):
                if self._ensure_edge(src, tnode, "驱动", float(w)):
                    stats["drive_edges"] += 1
        for src, targets in (cfg.get("emotion_edges") or {}).items():
            for tnode, w in (targets or []):
                if self._ensure_edge(src, tnode, "倾向", float(w)):
                    stats["emotion_edges"] += 1
        hw = float(cfg.get("hierarchy_weight", 0.3))
        for row in (cfg.get("hierarchy") or []):
            parent, rel, kids = row[0], row[1], row[2]
            # 族节点是本体分组（action_family 不在候选类型里——
            # 分组可被追溯、可被扩散经过，但不作为行动参与竞争）
            self._ensure_action_node(
                parent, lifecycle="established",
                name_zh=action_key(parent), expressive=False,
                node_type="action_family")
            for kid in kids:
                if self._ensure_edge(parent, kid, rel, hw,
                                     category="semantic_relation"):
                    stats["hierarchy"] += 1
        logger.info(f"[ActionSpace] 供性边/本体就位: {stats}")
        return stats

    def _ensure_action_node(self, node_id: str, channel: str = "communication",
                            lifecycle: str = "observed", name_zh: str = "",
                            expressive: bool = True, node_type: str = None):
        """行动节点 upsert。既有节点只补新字段，不改写既有语义。"""
        node = self.kg.get_node(node_id)
        if node is None:
            if node_type is None:
                node_type = ("behavior" if node_id.startswith("行为:")
                             else "action_proposed"
                             if channel == "communication"
                             else "action_concept")
            node = Node(
                id=node_id, weight=0.5,
                label="disposition" if channel == "communication"
                else "procedural",
                graph_space="self" if channel == "communication" else "semantic",
                extra_attrs={"type": node_type,
                             "action_key": action_key(node_id),
                             "channel": channel,
                             "lifecycle": lifecycle,
                             "expressive": expressive,
                             "name_zh": name_zh or action_key(node_id)})
            self.kg.add_node(node)
            if self.engine is not None and channel == "communication":
                try:
                    self.engine.name_to_node[node_id] = node_id
                except Exception:
                    pass
        else:
            ea = dict(node.extra_attrs or {})
            ea.setdefault("action_key", action_key(node_id, node))
            ea.setdefault("channel", channel)
            ea.setdefault("lifecycle", lifecycle)
            ea.setdefault("expressive",
                          node_id.startswith("行为:")
                          and node_id not in ("行为:沉默", "行为:探索"))
            node.extra_attrs = ea
        return node

    def _ensure_edge(self, src: str, dst: str, relation: str, w: float,
                     category: str = "cognitive_relation") -> bool:
        """建供性边。源必须已存在（不造平行状态——没有源节点的边没有
        意义，静默跳过）；目标行动节点缺失则按行动约定补建。"""
        if self.kg.get_node(src) is None:
            return False
        if self.kg.get_node(dst) is None:
            self._ensure_action_node(dst)
        if self.kg.get_edge(src, dst, relation) is not None:
            return False
        self.kg.add_edge(Edge(src=src, dst=dst, relation=relation,
                              weight=w, relation_category=category))
        return True

    # ── 候选收集：图谱激活，非枚举 ──────────────────────────────

    def collect_action_candidates(self, source_nodes: list = None,
                                  top_k: int = None,
                                  affordance_gain: float = 1.0) -> dict:
        """返回 {action_key: candidate}。

        candidate = {node_id, action_key, channel, lifecycle, expressive,
                     activation, affordances: {relation: (src, w, src_act)}}

        入选（任一）：
          1. 节点 activation ≥ candidate_min_activation（被任何现有传播
             路径点亮：Drive/情境/情绪/记忆/目标/未知* 扩散都算——这就是
             "行动通过正常图谱激活机制进入认知"的落点）
          2. 有来自**当前活跃源**（source_nodes 给了就只认这些 + 一切
             activation≥0.01 的供性边源）的 先验/激活/驱动/倾向 入边
        affordance_gain>1：CEN 聚焦（已有倾向边更确定）；<1：DMN 发散。
        deprecated 节点退出候选（节点保留，图谱不失忆）。
        top_k 是计算限制，不是语义封闭。
        """
        cfg = _cfg(self.config)
        if not self.enabled:
            return {}
        thr = float(cfg.get("candidate_min_activation", 0.05))
        cand = {}
        with self.kg._lock:
            nodes = self.kg.nodes
            for e in self.kg.edges:
                if e.relation not in AFFORDANCE_RELATIONS:
                    continue
                dst = nodes.get(e.dst)
                if dst is None or not is_action_node(dst):
                    continue
                ea = dst.extra_attrs or {}
                if ea.get("lifecycle") == "deprecated":
                    continue
                src = nodes.get(e.src)
                src_act = float(getattr(src, "activation", 0.0) or 0.0) \
                    if src is not None else 0.0
                if src_act < 0.01:
                    continue
                key = action_key(e.dst, dst)
                c = cand.setdefault(key, self._new_candidate(e.dst, dst))
                w = float(e.weight or 0.0)
                if e.relation == "激活":
                    w *= max(0.0, float(affordance_gain or 1.0))
                prev = c["affordances"].get(e.relation)
                if prev is None or w > prev[1]:
                    c["affordances"][e.relation] = (e.src, round(w, 4),
                                                    round(src_act, 4))
            for nid, node in nodes.items():
                if not is_action_node(node):
                    continue
                if float(node.activation or 0.0) >= thr:
                    ea = node.extra_attrs or {}
                    if ea.get("lifecycle") == "deprecated":
                        continue
                    key = action_key(nid, node)
                    c = cand.setdefault(key, self._new_candidate(nid, node))
                    c["activation"] = round(float(node.activation), 3)
        # 强度 = max(自身激活归一, 供性边 源激活×边权)；Top-K 计算截断
        scored = []
        for key, c in cand.items():
            strength = c["activation"] / 5.0
            for _rel, (src, w, sact) in c["affordances"].items():
                strength = max(strength, min(1.0, w * (0.2 + 0.8 * sact / 5.0)))
            if strength >= 0.02:
                scored.append((strength, key, c))
        scored.sort(key=lambda x: -x[0])
        k = int(top_k if top_k is not None
                else cfg.get("candidate_top_k", 12))
        out = {}
        for _s, key, c in scored[:max(3, k)]:
            c["strength"] = round(_s, 3)
            out[key] = c
        return out

    def _new_candidate(self, node_id, node):
        ea = node.extra_attrs or {}
        return {
            "node_id": node_id,
            "action_key": ea.get("action_key") or action_key(node_id, node),
            "channel": ea.get("channel",
                              "embodied" if not str(node_id).startswith(
                                  ("行为:", "行动:")) else "communication"),
            "lifecycle": ea.get("lifecycle", "observed"),
            "expressive": bool(ea.get("expressive",
                                      str(node_id).startswith("行为:")
                                      and node_id not in
                                      ("行为:沉默", "行为:探索"))),
            "activation": round(float(node.activation or 0.0), 3),
            "affordances": {},
        }

    # ── 概念提议与规范化（LLM 可以是来源之一，不是唯一）───────────

    def propose_action(self, surface: str, channel: str = "communication",
                       description: str = "", evidence: str = "",
                       create: bool = True) -> str:
        """规范化 → 图内别名/表达方式边 → 嵌入相似 → 复用 ∨ 创建 proposed。
        返回 action_key（""=放弃）。去重三层，防每轮刷重复节点。"""
        raw = str(surface or "").strip()
        cfg = _cfg(self.config)
        if not raw or not self.enabled:
            return ""
        norm = self._normalize(raw)
        if not norm:
            return ""
        # 1. 进程别名缓存
        if norm in self._alias_cache:
            return self._alias_cache[norm]
        # 2. 图内别名/表达方式边（跨重启去重）
        hit = self._graph_alias_lookup(norm)
        if hit:
            self._alias_cache[norm] = hit
            return hit
        # 2.5 短键精确匹配（FOLLOW / 追问 等直接命中已有概念）
        for nid in (norm, f"行为:{norm}", f"行动:{norm}"):
            node = self.kg.get_node(nid)
            if node is not None and is_action_node(node):
                key = action_key(nid, node)
                self._alias_cache[norm] = key
                return key
        # 3. 嵌入相似复用（若注入 embedder）
        similar = self._embed_lookup(raw, float(
            cfg.get("new_concept_similarity", 0.82)))
        if similar:
            self._alias_cache[norm] = similar
            self._register_surface(raw, similar)
            return similar
        # 4. 创建 proposed 概念
        if not create:
            return ""
        key = norm[:12]
        node_id = self._node_id_for(key, channel)
        node = self._ensure_action_node(
            node_id, channel=channel, lifecycle="proposed", name_zh=key,
            expressive=(channel == "communication"))
        ea = dict(node.extra_attrs or {})
        ea["description"] = str(description or raw)[:120]
        ea["proposed_from"] = str(evidence or raw)[:120]
        ea["proposed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        node.extra_attrs = ea
        self._register_surface(raw, key)
        self._alias_cache[norm] = key
        if self.engine is not None:
            try:
                self.engine.mark_active([node_id])
            except Exception:
                pass
        logger.info(f"[ActionSpace] +proposed 行动概念 {node_id}"
                    f"（表面「{raw[:24]}」）")
        return key

    def _node_id_for(self, key: str, channel: str) -> str:
        if channel != "communication":
            return key
        for pre in ("行为:", "行动:", "行动族:"):
            if key.startswith(pre):
                return key
        return f"行动:{key}"

    def _normalize(self, text: str) -> str:
        """保守规范化：去空白/标点/高频虚词，保留实义字序。
        完整语义规范化交给嵌入层，这里只是廉价第一层去重。"""
        out = []
        for ch in str(text):
            if ch.isalnum() and ch not in _STOP_CHARS:
                out.append(ch)
        return "".join(out)

    def _graph_alias_lookup(self, norm: str) -> str:
        with self.kg._lock:
            for e in self.kg.edges:
                if e.relation not in ("别名", "表达方式"):
                    continue
                if self._normalize(e.src) == norm:
                    dst = self.kg.get_node(e.dst)
                    if dst is not None and is_action_node(dst):
                        return action_key(e.dst, dst)
        return ""

    def _embed_lookup(self, raw: str, threshold: float) -> str:
        if self.embedder is None:
            return ""
        try:
            hits = self.embedder.search(raw, top_k=6,
                                        min_similarity=threshold)
        except Exception:
            return ""
        for h in (hits or []):
            nid = h.get("hit") if isinstance(h, dict) else str(h)
            with self.kg._lock:
                node = self.kg.get_node(nid)
            if node is not None and is_action_node(node):
                return action_key(nid, node)
        return ""

    def _register_surface(self, surface: str, key: str):
        """表面形式 → 行动概念 的 别名 边（infrastructure 表达节点，
        进 FAISS 全量索引供下次相似去重；刻意不进 name_to_node）。"""
        norm = self._normalize(surface)
        if not norm:
            return
        node_id = None
        for cand_id in (f"行为:{key}", f"行动:{key}", key):
            if self.kg.get_node(cand_id) is not None:
                node_id = cand_id
                break
        if node_id is None:
            return
        src_id = f"表达:{norm[:16]}"
        if self.kg.get_node(src_id) is None:
            self.kg.add_node(Node(
                id=src_id, weight=0.3, label="infrastructure",
                graph_space="self",
                extra_attrs={"type": "action_expression",
                             "surface": str(surface)[:60]}))
        if self.kg.get_edge(src_id, node_id, "别名") is None:
            self.kg.add_edge(Edge(src=src_id, dst=node_id, relation="别名",
                                  weight=0.85,
                                  relation_category="semantic_relation"))

    # ── 生命周期（数值规则在 config，不是硬编码流程）────────────

    def touch(self, key: str):
        """候选被竞争考虑/执行时记录，驱动 proposed→observed→established。"""
        cfg = _cfg(self.config)
        node = self._node_for(key)
        if node is None:
            return
        ea = dict(node.extra_attrs or {})
        ea["last_touched"] = time.strftime("%Y-%m-%d %H:%M:%S")
        count = int(ea.get("touch_count", 0)) + 1
        ea["touch_count"] = count
        life = ea.get("lifecycle", "proposed")
        if life == "proposed" and count >= int(
                cfg.get("lifecycle_promote_touches", 5)):
            life = "observed"
        if life == "observed" and self._pair_evidence(
                key) >= int(cfg.get("lifecycle_establish_evidence", 4)):
            life = "established"
        ea["lifecycle"] = life
        node.extra_attrs = ea

    def decay_lifecycle(self) -> int:
        """低频扫描（挂 disposition decay_all 节拍）：长期未触及 →
        weakened（established 只回落到 observed，本体化过的不轻易掉级）。"""
        cfg = _cfg(self.config)
        idle_days = float(cfg.get("lifecycle_weaken_days", 14))
        now = time.time()
        changed = 0
        with self.kg._lock:
            for nid, node in self.kg.nodes.items():
                if not is_action_node(node):
                    continue
                ea = dict(node.extra_attrs or {})
                life = ea.get("lifecycle")
                if life not in ("proposed", "observed", "established"):
                    continue
                ts = str(ea.get("last_touched") or ea.get("proposed_at")
                         or "")
                try:
                    t = time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
                except (ValueError, OverflowError):
                    continue
                if (now - t) / 86400.0 <= idle_days:
                    continue
                new = {"proposed": "weakened", "observed": "weakened",
                       "established": "observed"}[life]
                ea["lifecycle"] = new
                node.extra_attrs = ea
                changed += 1
        return changed

    def _pair_evidence(self, key: str) -> int:
        with self.kg._lock:
            for nid, node in self.kg.nodes.items():
                if nid.startswith(f"倾向:{key}@"):
                    return int((node.extra_attrs or {}).get(
                        "evidence_count", 0) or 0)
        return 0

    def _node_for(self, key: str):
        with self.kg._lock:
            for nid in (f"行为:{key}", f"行动:{key}", key):
                node = self.kg.get_node(nid)
                if node is not None and is_action_node(node):
                    return node
        return None

    def known_action_keys(self) -> set:
        """注册表视图：图谱里所有行动概念的 action_key（pair 校验用）。"""
        out = set()
        with self.kg._lock:
            for nid, node in self.kg.nodes.items():
                if is_action_node(node):
                    out.add(action_key(nid, node))
        return out

    # ── 统一 Action Schema（候选 → 可调度描述）─────────────────

    def to_schema(self, key: str, candidate: dict = None, target: str = "",
                  intention: str = "", expected_outcome: str = "",
                  urgency: float = 0.5, confidence: float = 0.5,
                  cost: float = 0.0, risk: float = 0.0,
                  prerequisites: list = None,
                  execution_method: str = "language") -> dict:
        """action_concept 字段指向图谱节点；target 允许指向任意图谱节点。
        execution_method ∈ {language, minecraft, tool, perception,
        memory, internal, system}。"""
        node = self._node_for(key)
        nid = node.id if node is not None else self._node_id_for(key, "communication")
        ea = (node.extra_attrs or {}) if node is not None else {}
        c = candidate or {}
        return {
            "action_id": f"actc_{key}"[:64],
            "action_concept": nid,
            "action_key": key,
            "channel": ea.get("channel", c.get("channel", "communication")),
            "lifecycle": ea.get("lifecycle", c.get("lifecycle", "observed")),
            "target": target,
            "intention": intention,
            "context": (c.get("affordances", {}) or {}).get(
                "先验", ("",))[0] or c.get("lit_by", ""),
            "expected_outcome": expected_outcome,
            "urgency": round(float(urgency), 3),
            "confidence": round(float(confidence), 3),
            "cost": float(cost),
            "risk": float(risk),
            "prerequisites": list(prerequisites or []),
            "execution_method": execution_method,
        }

    # ── 可观测性：一次行动怎么产生的 ────────────────────────────

    def trace(self, candidates: dict, winner: str = "", context: str = "",
              drives: dict = None, competition: dict = None,
              execution: str = "", outcome: str = "",
              learning: str = "") -> str:
        lines = ["[ActionTrace]", f"Context: {context or '?'}"]
        if drives:
            top = sorted(((v, k) for k, v in drives.items() if v > 0.01),
                         reverse=True)[:4]
            if top:
                lines.append("Activated drives: " + ", ".join(
                    f"{k}={v:.2f}" for v, k in top))
        if candidates:
            parts = []
            for k, c in sorted(candidates.items(),
                               key=lambda kv: -kv[1].get("strength", 0))[:6]:
                aff = ",".join(f"{r}←{s.split(':')[-1]}:{w:.2f}" for r,
                               (s, w, _a) in list(
                                   c["affordances"].items())[:2])
                parts.append(f"{k}={c.get('strength', 0):.2f}"
                             f"[{c['lifecycle'][0]}"
                             + (f";{aff}" if aff else "") + "]")
            lines.append("Action candidates: " + (" | ".join(parts) or "none"))
        if winner:
            lines.append(f"Selected: {winner}")
        if competition:
            lines.append("Competition: " + ", ".join(
                f"{k}={v}" for k, v in competition.items()))
        if execution:
            lines.append(f"Execution: {execution}")
        if outcome:
            lines.append(f"Outcome: {outcome}")
        if learning:
            lines.append(f"Learning: {learning}")
        return "\n".join(lines)

    def trace_compact(self, candidates: dict, winner: str = "",
                      context: str = "") -> str:
        """单行版（回合日志）。"""
        parts = []
        for k, c in sorted(candidates.items(),
                           key=lambda kv: -kv[1].get("strength", 0))[:5]:
            parts.append(f"{k}:{c.get('strength', 0):.2f}")
        return (f"ctx={context.split(':')[-1] if context else '?'} "
                f"cands=[{','.join(parts) or '-'}] → {winner or '-'}")
