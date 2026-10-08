# mc_knowledge.py — MC 世界语义入图（具身图谱化重构 2026-09-21）
# ============================================================================
# 原则：世界知识（这是什么、它能产生什么、它威胁什么、它受谁保护）属于
# 知识图谱，不属于 Python 特判。本模块只做两件事：
#   1. ensure_mc_world(kg)：把 config["mc_world"] 种子表幂等种进主图谱
#      （物种节点 + 属于/需要工具/产生/造成/威胁/服务于/受保护/敌对/不兼容 边）
#   2. 查询助手：kernel 与认知层"读图判断"，不读表。
# 种子之外，经验晋升（CausalLearner）与用户教学可以继续在图上生长这些关系
# ——图是活的，表只是起点。
# ============================================================================

import logging
import time

from graph_model import Node, Edge

logger = logging.getLogger(__name__)

SPECIES_PREFIX = ""          # 物种节点直接用世界名（chest/creeper…）


def _cfg(config):
    return (config or {}).get("mc_world") or {}


def ensure_mc_world(kg, config) -> dict:
    """幂等播种 MC 世界语义。返回统计。"""
    w = _cfg(config)
    if not w:
        return {"skipped": True}
    stats = {"species": 0, "edges": 0}

    def _spc(name, extra=None):
        """物种/概念节点（semantic 空间 declarative-semantic，可扩散）。"""
        if not name:
            return None
        with kg._lock:
            node = kg.get_node(name)
            if node is None:
                node = Node(id=name, weight=0.5, label="declarative-semantic",
                            graph_space="semantic",
                            extra_attrs={"type": "mc_species",
                                         "source": "mc_world_seed"})
                kg.add_node(node)
                stats["species"] += 1
            else:
                ea = dict(node.extra_attrs or {})
                ea.setdefault("source", "mc_world_seed")
                node.extra_attrs = ea
            if extra:
                ea = dict(node.extra_attrs or {})
                ea.update(extra)
                node.extra_attrs = ea
            return node

    def _edge(src, rel, dst, wt=0.7, cat="semantic_relation"):
        if not src or not dst:
            return
        with kg._lock:
            ok_nodes = src in kg.nodes and dst in kg.nodes
            exists = kg.get_edge(src, dst, rel) is not None if ok_nodes else False
            if not ok_nodes or exists:
                return
        # 走 kg.add_edge（add_edge 自身取锁，避免 kg._lock 可重入下的
        # 裸列表改动与索引不一致）
        kg.add_edge(Edge(src=src, dst=dst, relation=rel, weight=wt,
                         relation_category=cat))
        stats["edges"] += 1

    prot_classes = set(w.get("protected_classes") or [])
    protection = str(w.get("protection_node") or "用户资产保护")
    _spc(protection, {"type": "mc_constraint"})   # 保护约束概念本身先存在
    # 1) 分类树：物种-属于->类别；受保护类别-受保护->资产保护概念
    for sp, classes in (w.get("taxonomy") or {}).items():
        _spc(sp)
        for cls in classes:
            _spc(cls, {"type": "mc_class"})
            _edge(sp, "属于", cls, 0.8)
            if cls in prot_classes:
                _edge(sp, "受保护", protection, 0.8, "social_relation")
    # 2) 可采集资源分类（SAFE_GATHER_BLOCKS 的图镜像）
    _spc("可采资源", {"type": "mc_class"})
    for sp in (w.get("gatherable") or []):
        _spc(sp)
        _edge(sp, "属于", "可采资源", 0.8)
    # 3) 工具需求（挖掘机制知识：铁矿-需要工具->石镐）
    for sp, tool in (w.get("tool_required") or {}).items():
        _spc(sp)
        _spc(tool, {"type": "mc_item"})
        _edge(sp, "需要工具", tool, 0.8, "procedural_relation")
    # 4) 产出（石头-产生->圆石）
    for sp, prod in (w.get("produces") or {}).items():
        _spc(sp)
        _spc(prod, {"type": "mc_item"})
        _edge(sp, "产生", prod, 0.7, "causal_relation")
    # 5) 危险因果：creeper-造成->爆炸伤害-威胁->健康；敌对物种-敌对->玩家
    _spc("健康", {"type": "mc_state"})
    _spc("玩家", {"type": "mc_species"})
    for sp, harm in (w.get("hazards") or {}).items():
        _spc(sp)
        _spc(harm, {"type": "mc_effect"})
        _edge(sp, "造成", harm, 0.8, "causal_relation")
        _edge(harm, "威胁", "健康", 0.8, "causal_relation")
    _NON_CREATURE = {"lava", "fall", "drowning"}
    for prey in (w.get("hostile_preys") or []):
        for sp in (w.get("hazards") or {}):
            if str(sp).lower() in _NON_CREATURE:
                continue
            _edge(str(sp).lower(), "敌对", "玩家", 0.8)
    # 6) 行动的动机归属与互斥（服务/不兼容）
    _spc("生存需求", {"type": "mc_state"})
    for cid in (w.get("serves_survival") or []):
        if kg.get_node(cid) is None:
            continue   # 概念由 capability_graph 播种；不在则不越俎代庖
        _edge(cid, "服务于", "生存需求", 0.7, "cognitive_relation")
    for act, others in (w.get("incompatible") or {}).items():
        for other in others:
            _edge(act, "不兼容", other, 0.6)
    logger.info(f"[MCKnowledge] 世界语义入图: {stats}")
    return stats


# ── 查询助手（kernel/认知层的读图接口）─────────────────────────

def normalize_species(name: str) -> str:
    """世界名归一（去中文修饰、转小写、剥数量词）。"""
    s = str(name or "").strip().lower()
    for strip in ("方块", "矿", "了", "掉", "一些", "个", "的"):
        s = s.replace(strip, "") if len(s) > 3 else s
    return s.strip()


def species_node(kg, name: str):
    sp = normalize_species(name)
    if not sp:
        return None
    with kg._lock:
        node = kg.get_node(sp)
    if node is not None and (node.extra_attrs or {}).get("type") == "mc_species":
        return node
    # 中文名兜底（箱子→chest 由调用方 normalize_resource 处理，这里只查图）
    return None


def is_protected(kg, name: str) -> bool:
    """该物种是否受资产保护（图查询，不是名单）。"""
    node = species_node(kg, name)
    if node is None:
        return False
    prot = str(_CFG_PROTECTION)
    with kg._lock:
        for e in kg.edges:
            if (e.src == node.id and e.relation == "受保护"
                    and e.dst == prot):
                return True
    return False


_CFG_PROTECTION = "用户资产保护"   # 由 init_protection_node() 按 config 覆盖


def init_protection_node(config):
    global _CFG_PROTECTION
    _CFG_PROTECTION = str(_cfg(config).get("protection_node")
                          or _CFG_PROTECTION)


def is_gatherable(kg, name: str) -> bool:
    node = species_node(kg, name)
    if node is None:
        return False
    with kg._lock:
        for e in kg.edges:
            if (e.src == node.id and e.relation == "属于"
                    and e.dst == "可采资源"):
                return True
    return False


def is_hostile_species(kg, name: str, config) -> bool:
    """敌对判定：config 主名单 ∨ 物种带"造成"边（危险因果知识）。"""
    sp = normalize_species(name)
    if sp in {str(x).lower() for x in
              (config or {}).get("hostile_entities") or []}:
        return True
    node = species_node(kg, name)
    if node is None:
        return False
    with kg._lock:
        for e in kg.edges:
            if e.src == node.id and e.relation in ("造成", "敌对"):
                return True
    return False


def grant_id(action_key: str, species: str) -> str:
    return f"授权:{action_key}:{normalize_species(species)}"


def record_grant(kg, action_key: str, species: str, ttl_s: float = 600.0,
                 source: str = "user_command", now: float = None) -> str:
    """用户明确命令 → 图上授权证据（对物种级保护解除，带 TTL）。"""
    gid = grant_id(action_key, species)
    now = time.time() if now is None else float(now)
    with kg._lock:
        node = kg.get_node(gid)
        if node is None:
            node = Node(id=gid, weight=0.4, label="declarative-semantic",
                        graph_space="self",
                        extra_attrs={"type": "authorization",
                                     "action": str(action_key),
                                     "target_species": normalize_species(species),
                                     "granted_at": now, "ttl_s": float(ttl_s),
                                     "source": str(source)})
            kg.add_node(node)
        else:
            ea = dict(node.extra_attrs or {})
            ea["granted_at"] = now
            ea["ttl_s"] = float(ttl_s)
            ea["times"] = int(ea.get("times", 0)) + 1
            node.extra_attrs = ea
        if kg.get_edge(gid, normalize_species(species), "针对") is None \
                and normalize_species(species) in kg.nodes:
            kg.add_edge(Edge(src=gid, dst=normalize_species(species),
                             relation="针对", weight=0.7))
    return gid


def valid_grant(kg, action_key: str, species: str, now: float = None):
    """图上是否仍有对该物种有效的授权（TTL 内）。返回 (bool, 剩余秒)。"""
    now = now if now is not None else time.time()
    gid = grant_id(action_key, species)
    with kg._lock:
        node = kg.get_node(gid)
        if node is None:
            # 泛授权：任何 action 对该物种的授权也算
            for n in kg.nodes.values():
                ea = n.extra_attrs or {}
                if ea.get("type") == "authorization" \
                        and ea.get("target_species") == normalize_species(species):
                    left = float(ea.get("ttl_s", 600)) - \
                        (now - float(ea.get("granted_at", 0)))
                    if left > 0:
                        return True, left
            return False, 0.0
        ea = node.extra_attrs or {}
        left = float(ea.get("ttl_s", 600)) - \
            (now - float(ea.get("granted_at", 0)))
        return (left > 0), max(0.0, left)
