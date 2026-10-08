# prior_knowledge.py — 通用基础先验层 + 探索缺口（具身学习闭环 P3，2026-09-23）
# ============================================================================
# 这不是 Minecraft 知识库：它描述的是"智慧生物进入陌生环境时自带的迁移
# 性常识"（物体/资源/材料/工具/制作/缺口/尝试/结果……），全部以
# 节点+关系+可传播激活 的形态种进主图谱（label=declarative-semantic，
# semantic 空间，provenance source="prior_seed"），供扩散与候选发现
# 提供"桥"。行为判断仍然在既有管线（capability_graph 诱发/驱动边 +
# gates + binder + _score_action）——本模块不新增任何选择器。
#
# 探索缺口（exploration gap）与"能力缺口"同骨架：
#   已知对象 ∧ 用途未知 → 缺口:用途(x) 节点（指向 x）+ 探索缺口 聚合节点
#   - 聚合节点经 config affordances 的 诱发 边进入候选发现（零新代码）
#   - drive_engine 采样 graph.exploration_gap → tension → CuriosityDrive
#   - 真实反馈（配方线索/成功使用）→ close_gap，缺口消退激活回落
# 幂等：bootstrap 与桥接可反复执行；缺口有界（max_gaps，最旧按 stale 退役）。
# 零 LLM（tests/test_no_llm_on_fact_path FACT_FILES 钉死）。
# ============================================================================

import logging
import time

from graph_model import Node, Edge

logger = logging.getLogger(__name__)

GAP_HUB = "探索缺口"
GAP_ITEM_PREFIX = "缺口:用途("
# P3/§8 配方线索 hub：bot 端真实配方回包写"此刻背包能做出什么"（note_craftable）
CRAFT_HUB = "可制作物品"


def _cfg(config):
    return (config or {}).get("prior") or {}


# ── 先验概念（小而高连接；命名用中文通用词，非 MC 词表）──────────
PRIOR_CONCEPTS = {
    "物体": 0.6, "自然物": 0.5, "人造物": 0.5, "属性": 0.4, "用途": 0.6,
    "资源": 0.7, "材料": 0.6, "工具": 0.7, "容器": 0.5, "设施": 0.5,
    "食物": 0.6, "燃料": 0.4, "制成品": 0.5,
    "行动": 0.6, "获取": 0.6, "使用": 0.5, "消耗": 0.4, "保存": 0.4,
    "加工": 0.5, "制作": 0.6, "组合": 0.5, "建造": 0.4, "修复": 0.3,
    "破坏": 0.4, "移动": 0.4, "观察": 0.5, "尝试": 0.6, "防御": 0.3,
    "能力": 0.6, "效率": 0.4, "成本": 0.3, "耐久": 0.3,
    "环境": 0.5, "时间": 0.4, "危险": 0.5, "伤害": 0.4, "生存": 0.7,
    "目标": 0.6, "缺口": 0.6, "前置条件": 0.4, "探索": 0.6, "未知": 0.6,
    "结果": 0.5, "成功": 0.5, "失败": 0.4, "经验": 0.6, "学习": 0.6,
    "可能性": 0.5, "机会": 0.4,
}

# (src, relation, dst, weight, category) —— 全部使用 relation_ontology
# 已注册词；类别与既有种子层同风格。
PRIOR_EDGES = [
    # 分类桥：什么是什么
    ("自然物", "属于", "物体", 0.7, "semantic_relation"),
    ("人造物", "属于", "物体", 0.7, "semantic_relation"),
    ("资源", "属于", "物体", 0.6, "semantic_relation"),
    ("材料", "属于", "资源", 0.7, "semantic_relation"),
    ("食物", "属于", "资源", 0.8, "semantic_relation"),
    ("燃料", "属于", "资源", 0.6, "semantic_relation"),
    ("工具", "属于", "人造物", 0.7, "semantic_relation"),
    ("容器", "属于", "人造物", 0.6, "semantic_relation"),
    ("设施", "属于", "人造物", 0.6, "semantic_relation"),
    ("制成品", "属于", "人造物", 0.5, "semantic_relation"),
    ("用途", "属于", "属性", 0.6, "semantic_relation"),
    ("耐久", "属于", "属性", 0.4, "semantic_relation"),
    ("成功", "属于", "结果", 0.7, "semantic_relation"),
    ("失败", "属于", "结果", 0.7, "semantic_relation"),
    ("获取", "属于", "行动", 0.7, "semantic_relation"),
    ("使用", "属于", "行动", 0.7, "semantic_relation"),
    ("观察", "属于", "行动", 0.6, "semantic_relation"),
    ("尝试", "属于", "行动", 0.6, "semantic_relation"),
    ("制作", "属于", "行动", 0.6, "semantic_relation"),
    ("加工", "属于", "行动", 0.5, "semantic_relation"),
    ("破坏", "属于", "行动", 0.5, "semantic_relation"),
    ("移动", "属于", "行动", 0.5, "semantic_relation"),
    ("建造", "属于", "行动", 0.5, "semantic_relation"),
    ("探索", "属于", "行动", 0.6, "semantic_relation"),
    # 资源⇄行动：可获得、可消耗、可加工
    ("资源", "具有", "用途", 0.6, "semantic_relation"),
    ("获取", "产生", "资源", 0.5, "causal_relation"),
    ("消耗", "影响", "资源", 0.5, "causal_relation"),
    ("加工", "需要", "材料", 0.6, "procedural_relation"),
    ("制作", "需要", "材料", 0.7, "procedural_relation"),
    ("制作", "需要", "设施", 0.35, "procedural_relation"),
    ("制作", "产生", "制成品", 0.6, "causal_relation"),
    ("制成品", "属于", "材料", 0.35, "semantic_relation"),   # 新物品可再成材料
    ("组合", "需要", "材料", 0.5, "procedural_relation"),
    ("组合", "产生", "制成品", 0.45, "causal_relation"),
    ("设施", "可实现为", "制作", 0.5, "procedural_relation"),  # 设施打开加工能力
    ("容器", "可实现为", "保存", 0.55, "procedural_relation"),
    ("保存", "属于", "行动", 0.5, "semantic_relation"),
    ("消耗", "属于", "行动", 0.4, "semantic_relation"),
    ("修复", "属于", "行动", 0.4, "semantic_relation"),
    ("防御", "属于", "行动", 0.4, "semantic_relation"),
    # 工具：改变效果/效率，可能需特定工具，本身由资源制成
    ("工具", "具有", "用途", 0.6, "semantic_relation"),
    ("工具", "影响", "效率", 0.6, "causal_relation"),
    ("工具", "需要", "材料", 0.45, "procedural_relation"),
    ("工具", "具有", "耐久", 0.5, "semantic_relation"),
    ("耐久", "影响", "使用", 0.35, "causal_relation"),
    ("破坏", "需要", "工具", 0.4, "procedural_relation"),
    # 生存链
    ("食物", "可实现为", "消耗", 0.5, "procedural_relation"),
    ("消耗", "服务于", "生存", 0.5, "cognitive_relation"),
    ("食物", "服务于", "生存", 0.7, "cognitive_relation"),
    ("危险", "造成", "伤害", 0.7, "causal_relation"),
    ("伤害", "威胁", "生存", 0.7, "causal_relation"),
    ("伤害", "影响", "行动", 0.4, "causal_relation"),
    ("环境", "包含", "危险", 0.45, "semantic_relation"),
    ("环境", "包含", "资源", 0.4, "semantic_relation"),
    ("时间", "影响", "环境", 0.35, "causal_relation"),
    ("行动", "造成", "环境", 0.25, "causal_relation"),   # 行动改变环境
    ("生存", "属于", "目标", 0.5, "semantic_relation"),
    # 缺口→探索→经验（认知主回路：§7 的通用形状）
    ("未知", "指向", "缺口", 0.6, "cognitive_relation"),
    ("缺口", "驱动", "探索", 0.6, "cognitive_relation"),
    ("目标", "需要", "前置条件", 0.5, "procedural_relation"),
    ("前置条件", "指向", "资源", 0.4, "cognitive_relation"),
    ("目标", "诱发", "行动", 0.4, "cognitive_relation"),
    ("缺口", "诱发", "获取", 0.35, "cognitive_relation"),
    ("探索", "服务于", "获取", 0.4, "cognitive_relation"),
    ("可能性", "属于", "属性", 0.4, "semantic_relation"),
    ("机会", "指向", "可能性", 0.5, "cognitive_relation"),
    ("尝试", "产生", "结果", 0.7, "causal_relation"),
    ("尝试", "需要", "成本", 0.4, "procedural_relation"),
    ("结果", "产生", "经验", 0.6, "causal_relation"),
    ("失败", "产生", "经验", 0.5, "causal_relation"),      # 失败也是经验
    ("观察", "产生", "经验", 0.6, "causal_relation"),
    ("经验", "服务于", "学习", 0.6, "cognitive_relation"),
    ("学习", "需要", "经验", 0.5, "procedural_relation"),
    ("学习", "产生", "能力", 0.5, "causal_relation"),
    ("能力", "诱发", "行动", 0.35, "cognitive_relation"),
    ("效率", "服务于", "生存", 0.25, "cognitive_relation"),
    ("成本", "属于", "属性", 0.35, "semantic_relation"),
]

# mc 世界分类 → 先验概念的桥（类节点存在才接；每类一条，不逐物种接）
CLASS_BRIDGE = {
    "可采资源": ("资源", 0.7),
    "材料": ("材料", 0.7),
    "食物": ("食物", 0.7),
    "工具": ("工具", 0.8),
    "武器": ("工具", 0.7),
    "盔甲": ("工具", 0.6),
    "容器": ("容器", 0.8),
    "功能方块": ("设施", 0.75),
    "建筑构件": ("材料", 0.5),
    "能源": ("燃料", 0.7),
    "自然物": ("自然物", 0.8),
}

# 被视为"已知用途"的下游关系（对象身上挂着任何一条 → 无缺口）
_USE_RELATIONS = ("产生", "需要工具", "服务于", "可实现为", "造成",
                  "属于", "威胁", "受保护")
_USE_CLASS_TARGETS = {"可采资源", "材料", "食物", "工具", "武器", "盔甲",
                      "容器", "功能方块", "能源"}


# ── bootstrap ─────────────────────────────────────────────

def ensure_prior(kg, config) -> dict:
    """幂等种入先验概念、骨架边、mc 分类桥与探索缺口聚合节点。"""
    if kg is None or not _cfg(config).get("enabled", True):
        return {"skipped": True}
    stats = {"concepts": 0, "edges": 0, "bridges": 0}
    for name, w in PRIOR_CONCEPTS.items():
        with kg._lock:
            if kg.get_node(name) is None:
                kg.add_node(Node(id=name, weight=float(w),
                                 label="declarative-semantic",
                                 graph_space="semantic",
                                 extra_attrs={"type": "prior_concept",
                                              "source": "prior_seed"}))
                stats["concepts"] += 1
    for src, rel, dst, w, cat in PRIOR_EDGES:
        with kg._lock:
            ok = src in kg.nodes and dst in kg.nodes
            dup = kg.get_edge(src, dst, rel) is not None if ok else True
            if ok and not dup:
                kg.add_edge(Edge(src=src, dst=dst, relation=rel,
                                 weight=float(w), relation_category=cat))
                stats["edges"] += 1
    # 分类桥：mc_class 节点（mc_world 种子建的）→ 先验概念
    with kg._lock:
        cls_nodes = [nid for nid, n in kg.nodes.items()
                     if (n.extra_attrs or {}).get("type") == "mc_class"]
    for cls in cls_nodes:
        pair = CLASS_BRIDGE.get(cls)
        if not pair:
            continue
        prior_cls, w = pair
        with kg._lock:
            ok = cls in kg.nodes and prior_cls in kg.nodes
            dup = kg.get_edge(cls, prior_cls, "属于") is not None \
                if ok else True
            if ok and not dup:
                kg.add_edge(Edge(src=cls, dst=prior_cls, relation="属于",
                                 weight=w,
                                 relation_category="semantic_relation"))
                stats["bridges"] += 1
    # 探索缺口聚合节点（与 能力缺口 同骨架）
    _ensure_hub(kg)
    _ensure_craft_hub(kg)
    if stats["concepts"] or stats["edges"] or stats["bridges"]:
        logger.info(f"[Prior] 先验层入图: {stats}")
    return stats


def _ensure_hub(kg):
    with kg._lock:
        if kg.get_node(GAP_HUB) is None:
            kg.add_node(Node(id=GAP_HUB, weight=0.4,
                             label="declarative-semantic",
                             graph_space="cognitive",
                             extra_attrs={"type": "exploration_gap",
                                          "description":
                                              "已知对象但用途未知——"
                                              "可探索缺口的聚合信号",
                                          "gaps": {}}))


# ── 探索缺口（open/close/查询）────────────────────────────

def gap_node_id(obj: str) -> str:
    return f"{GAP_ITEM_PREFIX}{obj})"


def has_known_use(kg, obj: str) -> bool:
    """图上有没有关于该对象"用途/后果/归属"的任何一手知识。
    （配方线索也算：配方:* 需要 物品:x = 它在合成里有位置。）"""
    obj = str(obj or "").strip().lower()
    if not obj:
        return True    # 无名对象不值得开缺口
    ids = {obj, f"物品:{obj}"}   # 物种节点与背包节点两种落点都算"已知对象"
    with kg._lock:
        present = [i for i in ids if i in kg.nodes]
        if not present:
            return True
        for e in kg.edges:
            if e.src in present and e.relation in _USE_RELATIONS:
                if e.relation != "属于" or str(e.dst) in _USE_CLASS_TARGETS:
                    return True
        for e in kg.edges:   # 被配方引用（材料或产物）
            if (e.dst in present and e.relation in ("需要", "产生")
                    and str(e.src).startswith("配方:")):
                return True
    return False


def open_gap(kg, engine, obj: str, kind: str = "unknown_use",
             config=None) -> bool:
    """为"对象已知、用途未知"开一个有界探索缺口（幂等；返回是否新开）。"""
    obj = str(obj or "").strip().lower()
    if not obj or not _cfg(config).get("enabled", True):
        return False
    _ensure_hub(kg)
    gid = gap_node_id(obj)
    now = time.time()
    step = float(_cfg(config).get("gap_activation_step", 0.6))
    cap = int(_cfg(config).get("max_gaps", 24))
    created = False
    with kg._lock:
        hub = kg.get_node(GAP_HUB)
        gaps = dict((hub.extra_attrs or {}).get("gaps") or {})
        if gid in gaps and not (gaps[gid] or {}).get("closed"):
            return False           # 已有活缺口：不重开不叠账
        if gid not in kg.nodes:
            kg.add_node(Node(id=gid, weight=0.3,
                             label="declarative-semantic",
                             graph_space="cognitive",
                             extra_attrs={"type": "exploration_gap_item",
                                          "object": obj, "kind": str(kind),
                                          "opened_at": now,
                                          "source": "prior_seed"}))
            if kg.get_edge(gid, obj, "指向") is None and obj in kg.nodes:
                kg.add_edge(Edge(src=gid, dst=obj, relation="指向",
                                 weight=0.6,
                                 relation_category="cognitive_relation"))
        else:
            n = kg.get_node(gid)
            ea = dict(n.extra_attrs or {})
            ea.update({"opened_at": now, "closed": None})
            n.extra_attrs = ea
        if kg.get_edge(gid, GAP_HUB, "属于") is None:
            kg.add_edge(Edge(src=gid, dst=GAP_HUB, relation="属于",
                             weight=0.6,
                             relation_category="semantic_relation"))
        gaps[gid] = {"object": obj, "kind": str(kind), "at": now}
        # 有界：超出封顶按最旧退役（不删节点，只从聚合账上关闭）
        items = sorted(gaps.items(), key=lambda kv: kv[1].get("at", 0))
        for old_gid, _meta in items[:max(0, len(gaps) - cap)]:
            gaps.pop(old_gid, None)
            on = kg.get_node(old_gid)
            if on is not None:
                ea = dict(on.extra_attrs or {})
                ea["retired"] = "stale"
                on.extra_attrs = ea
                on.activation = 0.0
        hub.extra_attrs["gaps"] = gaps
        hub.activation = min(2.5, float(hub.activation or 0.0) + step)
        hub.touch()
        gn = kg.get_node(gid)
        if gn is not None:
            gn.activation = 1.5
            gn.touch()
        created = True
    if created and engine is not None:
        try:
            engine.mark_active([GAP_HUB, gid])
            engine.register_activation_source([GAP_HUB], "internal_drive")
        except Exception:
            pass
        try:
            import fas_log
            fas_log.get_logger(fas_log.CURIOSITY).info(
                "exploration_gap", f"缺口开启 {obj}（{kind}）",
                object=obj, kind=str(kind), gap=GAP_HUB,
                open_count=len(gaps))
        except Exception:
            pass
    return created


def resurface_gap(kg, engine, obj: str, config=None,
                  now: float = None) -> bool:
    """§16 blocked→retry：放弃的探索缺口不是死刑——对象仍在手且隔了一个
    生命周期，该有有界次数（gap_max_reopen）的新尝试。因用途已知/证据成功
    而关的缺口（use_known / used:*）永不重开。零 LLM；不含任何逐物规则。
    （2026-09-23 真机断点②：试做通道修好前缺口全被 ttl 误杀，而
    consider_recognition 只在物品节点首建时触发——存量未知物永远排不上
    实验，此函数是实验通道的复活线。）"""
    cfg = _cfg(config)
    if not cfg.get("enabled", True):
        return False
    obj = str(obj or "").strip().lower()
    if not obj:
        return False
    gid = gap_node_id(obj)
    now = now if now is not None else time.time()
    min_s = float(cfg.get("gap_resurface_min_s", 1800))
    max_re = int(cfg.get("gap_max_reopen", 2))
    with kg._lock:
        hub = kg.get_node(GAP_HUB)
        gaps = dict((hub.extra_attrs or {}).get("gaps") or {}) if hub else {}
        if gid in gaps and not (gaps[gid] or {}).get("closed"):
            return False                       # 已有活缺口
        n = kg.get_node(gid)
        if n is None:
            return False                       # 从未开过缺口：首遇通道负责
        ea = dict(n.extra_attrs or {})
        retired = str(ea.get("retired") or "")
        if not retired.startswith("abandoned"):
            return False                       # 已知用途/证毕关闭 → 不重试
        if now - float(ea.get("closed") or 0.0) < min_s:
            return False
        # ttl_untried = 目标从没获得过尝试机会就被超时掐掉（真机 2026-09-23
        # 的评分竞争产物）——不是失败，不占用重开额度；真正试过的才计次。
        untried = retired.endswith("ttl_untried")
        if not untried and int(ea.get("reopen_n", 0)) >= max_re:
            return False
        if not untried:
            ea["reopen_n"] = int(ea.get("reopen_n", 0)) + 1
        n.extra_attrs = ea
    return open_gap(kg, engine, obj, "resurface:retry", config)


def close_gap(kg, engine, obj: str, reason: str = "learned",
              config=None) -> bool:
    """真实反馈证实用途（或线索引用）→ 缺口关闭，聚合激活回落。"""
    obj = str(obj or "").strip().lower()
    gid = gap_node_id(obj)
    decay = float(_cfg(config).get("gap_close_decay", 0.4))
    closed = False
    with kg._lock:
        hub = kg.get_node(GAP_HUB)
        if hub is None:
            return False
        gaps = dict((hub.extra_attrs or {}).get("gaps") or {})
        if gid not in gaps:
            return False
        gaps.pop(gid, None)
        hub.extra_attrs["gaps"] = gaps
        hub.activation = max(0.0, float(hub.activation or 0.0) - decay)
        n = kg.get_node(gid)
        if n is not None:
            ea = dict(n.extra_attrs or {})
            ea["retired"] = str(reason)
            ea["closed"] = time.time()
            n.extra_attrs = ea
            n.activation = 0.0
        closed = True
    if closed and engine is not None:
        try:
            engine.mark_active([GAP_HUB])
        except Exception:
            pass
        try:
            import fas_log
            fas_log.get_logger(fas_log.CURIOSITY).info(
                "exploration_gap", f"缺口关闭 {obj}（{reason}）",
                object=obj, kind="closed", gap=GAP_HUB, reason=str(reason))
        except Exception:
            pass
    return closed


def consider_recognition(kg, engine, name: str, config=None) -> bool:
    """感知端调用点：物种/物品被经验识别（或首次建档）后检查——
    已知对象 ∧ 用途未知 → 探索缺口。零 LLM；异常静默（绝不打断感知）。"""
    try:
        obj = str(name or "").strip().lower()
        if not obj:
            return False
        if has_known_use(kg, obj):
            close_gap(kg, engine, obj, "use_known", config)
            return False
        return open_gap(kg, engine, obj, "unknown_use", config)
    except Exception as e:
        logger.debug(f"[Prior] 缺口检查跳过 {name}: {e}")
        return False


def open_gaps(kg) -> list:
    """当前活缺口清单（debug/测试只读视图）。"""
    with kg._lock:
        hub = kg.get_node(GAP_HUB)
        if hub is None:
            return []
        return [dict(meta, gap=g) for g, meta in
                ((hub.extra_attrs or {}).get("gaps") or {}).items()]


# ── 配方线索（§8：recipe book = 环境事实经图成为行动线索）──────────

def _ensure_craft_hub(kg):
    """可制作物品 hub（诱发→CRAFT 的源；激活由 note_craftable 写）。"""
    with kg._lock:
        if kg.get_node(CRAFT_HUB) is None:
            kg.add_node(Node(id=CRAFT_HUB, weight=0.5,
                             label="declarative-semantic",
                             graph_space="cognitive",
                             extra_attrs={"type": "craftable_hub",
                                          "description":
                                              "环境回报：此刻手上的材料"
                                              "能做出什么（配方=线索非剧本）",
                                          "hints": []}))


def note_craftable(kg, engine, craftable, config=None) -> dict:
    """配方层回写：bot 的真实配方扫描（{result, ingredients} 列表）上图。
      - hub extra.hints → autonomy._bind_craft 的候选来源
      - 配方:x -需要-> 物品:材料 / -产生-> 物品:产物（图上因果结构）
      - 产物与材料关各自探索缺口（知道配方=知道了一个用途）；新入图
        材料若用途未知则开缺口——配方提到它，但"它还能为谁服务"未知。
    零 LLM；幂等；异常静默（线索通道，不是生命线）。"""
    if kg is None or not _cfg(config).get("enabled", True):
        return {"skipped": True}
    items = [c for c in (craftable or [])
             if isinstance(c, dict) and str(c.get("result") or "").strip()]
    _ensure_craft_hub(kg)
    cap = int(_cfg(config).get("recipes_max_hints", 8))
    hints, closed, edges_new = [], 0, 0
    for c in items[:max(1, cap)]:
        res = str(c.get("result") or "").strip().lower()
        ings = {str(k).strip().lower(): int(v or 1)
                for k, v in (c.get("ingredients") or {}).items()}
        hints.append({"item": res, "ingredients": sorted(ings),
                      "needs_table": bool(c.get("needs_table"))})
        rid = f"配方:{res}"
        with kg._lock:
            if kg.get_node(rid) is None:
                kg.add_node(Node(id=rid, weight=0.4,
                                 label="declarative-semantic",
                                 graph_space="semantic",
                                 extra_attrs={"type": "recipe",
                                              "source": "recipes_report",
                                              "needs_table":
                                                  bool(c.get("needs_table"))}))

            def _ensure_item(iid):
                # add_edge 会静默丢弃端点缺失的边——先补 物品: 占位节点，
                # 否则"配方→需要→材料""配方→产生→产物"的因果结构根本上不了图。
                if kg.get_node(iid) is None:
                    kg.add_node(Node(id=iid, weight=0.4,
                                     label="declarative-episodic",
                                     graph_space="episodic",
                                     extra_attrs={"type": "mc_item",
                                                  "source": "recipe_mention"}))
            for mat, _n in ings.items():
                mid = f"物品:{mat}"
                _ensure_item(mid)
                if kg.get_edge(rid, mid, "需要") is None:
                    kg.add_edge(Edge(src=rid, dst=mid, relation="需要",
                                     weight=0.6,
                                     relation_category="procedural_relation"))
                    edges_new += 1
                # 配方引用=该材料的一个用途落地 → 关闭其缺口（若有）
            if kg.get_edge(rid, f"物品:{res}", "产生") is None:
                _ensure_item(f"物品:{res}")
                kg.add_edge(Edge(src=rid, dst=f"物品:{res}", relation="产生",
                                 weight=0.6,
                                 relation_category="procedural_relation"))
                edges_new += 1
        closed += int(bool(close_gap(kg, engine, res, "recipe_known", config)))
        for mat in ings:
            closed += int(bool(close_gap(kg, engine, mat,
                                         "recipe_material", config)))
    with kg._lock:
        hub = kg.get_node(CRAFT_HUB)
        ea = dict(hub.extra_attrs or {})
        ea["hints"] = hints
        hub.extra_attrs = ea
        # 背包变空→hints 清空→激活归零：CRAFT 候选自然熄灭（无 if 撤权）
        hub.activation = 0.0 if not hints else min(2.0, 0.3 + 0.25 * len(hints))
        hub.touch()
    if engine is not None:
        try:
            engine.mark_active([CRAFT_HUB])
            engine.register_activation_source([CRAFT_HUB], "internal_drive")
        except Exception:
            pass
    try:
        import fas_log
        fas_log.get_logger(fas_log.CURIOSITY).info(
            "recipe_clues", f"配方线索刷新：hints={len(hints)} "
            f"新边={edges_new} 关缺口={closed}",
            hints=len(hints), edges=edges_new, closed=closed)
    except Exception:
        pass
    return {"hints": len(hints), "edges": edges_new, "closed": closed}
