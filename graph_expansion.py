# graph_expansion.py — 图谱知识扩展 + 连接致密化（2026-09-22 一次性任务）
# ============================================================================
# 目标（用户任务书）：用 LLM 公共知识把可外部扩展的节点扩出主题簇，并发现
# 已有节点之间合理但未建的语义边；个人经历/当前状态/episodic/引擎零件**不得**
# 凭空扩展（宁可少加，不要幻觉）。
#
# 本模块只碰三样东西：graph_model（读写）、graph_schema（关系归一/hub 守卫）、
# llm_provider（批量问答）。**不改扩散、不改认知回路、不新建运行时依赖**——
# 这是一条离线索引维护管线，产物是 runtime_graph.json 的增量 + 台账。
#
# 溯源与去重的落地决策（最小侵入，写在这里以免被当成遗漏）：
#   - 节点 provenance → extra_attrs["expansion"]（Node 有这个口袋）
#   - 边 provenance   → Edge schema 没有 extra 字段；加字段要动 graph_model
#     序列化（侵入运行时）。改为旁路台账 data/graph_expansion/edge_ledger.jsonl，
#     每行一条边事实 {src,rel,dst,source,confidence,reason,batch,ts}。
#     confidence 同时折算进 edge.weight。
#   - 去重：不删任何节点。复用现有 aliases 机制：提案名先过
#     canonicalize()（精确/大小写/别名表），命中 → 连到既有节点上。
# ============================================================================

import json
import logging
import os
import re
import time

from graph_model import Edge, KnowledgeGraph, Node, now_str
from graph_schema import (
    COMPOUND_NODE_RE, DATE_NODE_RE, category_for, check_hub_edge,
    normalize_relation,
)

logger = logging.getLogger(__name__)

STATE_VERSION = 1

# ── 可扩展性分类（任务书 §5：不依赖硬编码字符串表，多信号综合）──────────
CLASS_PUBLIC = "public_knowledge"
CLASS_EPISODIC = "episodic_memory"
CLASS_PERSONAL = "personal_fact"
CLASS_STATE = "current_state"
CLASS_INFRA = "infrastructure"
CLASS_PROCEDURAL = "procedural"
CLASS_SELF = "self_node"
CLASS_UNKNOWN = "unknown"

# 引擎/自我/社会图谱锚点：绝不外扩、绝不与其建边
PROTECTED_IDS = {
    "用户", "Self", "Haru", "FAS", "Minecraft会话", "用户关系",
    "Hellucigen",  # 用户本人账号名（2026-09-22 核查：邻居全是反思/传送私人情节）
    "CuriosityDrive", "SocialDrive", "LearningDrive", "ConsistencyDrive",
}
# 时间指示词：语义随"此刻"漂移，外扩只会造出以当下为中心的虚构
DEICTIC_NAMES = {"今天", "明天", "后天", "昨天", "前天", "现在", "刚才"}
# 图上这些 type 属于系统内部概念（自己的动作/表达/能力/时段），不是公共知识
INTERNAL_TYPES = {
    "action_concept", "action_expression", "speech_act_concept",
    "capability", "time_of_day", "day_phase", "mc_session",
    "user_relationship", "input_device", "drive", "signal",
}
# mc_* 是游戏内公共设定（生物/物品/机制）——可外扩
PUBLIC_TYPES = {"mc_species", "mc_item", "mc_class", "mc_effect", "mc_state"}
# 内部来源产生的节点（系统自己写的状态/情绪/时间注入）
INTERNAL_SOURCES = {
    "autonomy", "curiosity_auto", "emotion_injection", "temporal_awareness",
    "narrative", "episodic_migration", "atomic_repair", "atomization_repair",
    "mc_world_seed",
}
SELF_SPACED_PREFIXES = ("能力_", "行为:", "情境:", "状态:", "思考_", "反思_")


def classify_node(node) -> str:
    """综合 label / graph_space / type / source / 命名形态判定可扩展类别。

    顺序即优先级——先排除"不能扩的"，剩下的语义空间公共概念才进扩展队列。
    """
    nid = node.id
    ea = node.extra_attrs or {}
    ntype = str(ea.get("type") or "").strip()
    source = str(ea.get("source") or "").strip()

    if nid in PROTECTED_IDS or node.graph_space == "self" or node.label == "self":
        return CLASS_SELF
    if node.graph_space == "episodic" or node.label == "declarative-episodic":
        return CLASS_EPISODIC
    if COMPOUND_NODE_RE.match(nid) or DATE_NODE_RE.match(nid):
        return CLASS_EPISODIC          # 事件命名式 {活动}—{方面}—{实体}
    if nid in DEICTIC_NAMES or str(ea.get("attr_name") or ""):
        return CLASS_STATE             # 时间指示词/属性值节点：语义随情境漂移
    if ntype == "user_relationship" or "用户关系" == ntype:
        return CLASS_PERSONAL          # 朋友/家人等——个人社会图谱，不可外推
    if node.label in ("infrastructure", "disposition", "intention", "procedural"):
        return CLASS_PROCEDURAL if node.label == "procedural" else CLASS_INFRA
    if node.graph_space == "cognitive":
        return CLASS_INFRA
    if ntype in INTERNAL_TYPES or source in INTERNAL_SOURCES:
        return CLASS_INFRA
    if nid.startswith(SELF_SPACED_PREFIXES):
        return CLASS_INFRA
    if node.label == "declarative-semantic" and node.graph_space == "semantic":
        if ntype in PUBLIC_TYPES:
            return CLASS_PUBLIC
        # taught_by_user 等来源不影响类别：奶茶/红石是公共知识，
        # "用户教我认识X"这一事实本身是个人经历（那是 episodic 边的事，不是这里）。
        if ntype in ("", "unknown", None) and _looks_like_noise(nid):
            return CLASS_UNKNOWN
        return CLASS_PUBLIC
    return CLASS_UNKNOWN


def _looks_like_noise(nid: str) -> bool:
    """过短/纯符号/疑似临时节点的语义空间名——不扩。"""
    s = str(nid or "").strip()
    if len(s) <= 1:
        return True
    if re.fullmatch(r"[\W_]+", s):
        return True
    if s.startswith(("eye_text_", "ear_text_", "vision_", "temp_", "UnknownObject")):
        return True
    return False


# ── 去重：canonical 索引（复用 aliases 机制，不删节点）───────────────────

def _norm_name(name: str) -> str:
    s = str(name or "").strip().lower()
    s = re.sub(r"[（）()【】\[\]「」『』\"'’‘]", "", s)
    return re.sub(r"\s+", "", s)


def build_canon_index(kg) -> dict:
    """normalized_name → canonical node id（含自身与既有 aliases）。"""
    idx = {}
    for nid, node in kg.nodes.items():
        idx.setdefault(_norm_name(nid), nid)
        for al in (node.extra_attrs or {}).get("aliases") or []:
            idx.setdefault(_norm_name(al), nid)
    return idx


def canonicalize(name: str, canon_index: dict, kg) -> str:
    """把提案名解析为图上既有规范节点 id；无匹配返回 ""。"""
    hit = canon_index.get(_norm_name(name))
    if hit:
        return hit
    s = str(name or "").strip()
    return s if s in kg.nodes else ""


# ── 选种（任务书 §6：Tier 1/2/3；个人/episodic 不进队列）─────────────────

def node_degree(kg) -> dict:
    deg = {nid: 0 for nid in kg.nodes}
    for e in kg.edges:
        if e.src in deg:
            deg[e.src] += 1
        if e.dst in deg:
            deg[e.dst] += 1
    return deg


def classify_all(kg) -> dict:
    return {nid: classify_node(n) for nid, n in kg.nodes.items()}


def select_seeds(kg, classes: dict, degree: dict,
                 tier1_degree: int = 5,
                 tier1_max: int = 40, tier2_max: int = 70,
                 tier3_max: int = 50) -> list:
    """返回 [(seed_id, tier)]，按 (tier, -degree) 排序。"""
    public = [nid for nid, c in classes.items() if c == CLASS_PUBLIC]
    pset = set(public)
    t1 = sorted((n for n in public if degree.get(n, 0) >= tier1_degree),
                key=lambda n: -degree.get(n, 0))[:tier1_max]
    t1set = set(t1)
    # Tier2：T1 的公共邻居
    nbr = set()
    for e in kg.edges:
        if e.src in t1set and e.dst in pset:
            nbr.add(e.dst)
        if e.dst in t1set and e.src in pset:
            nbr.add(e.src)
    t2 = sorted(nbr - t1set, key=lambda n: -degree.get(n, 0))[:tier2_max]
    t2set = set(t2)
    # Tier3：孤立/低度公共节点（连接致密化的重点对象）
    t3 = sorted((n for n in public
                 if n not in t1set and n not in t2set and degree.get(n, 3) <= 1),
                key=lambda n: degree.get(n, 0))[:tier3_max]
    return ([(n, 1) for n in t1] + [(n, 2) for n in t2] + [(n, 3) for n in t3])


# ── LLM：批量提示词（任务书 §7 的十类关系、§10 批处理、§15 上限）──────────

# 从 101 条规范词表里挑公共知识适用的子集（含方向示例，防 LLM 造词）
PUBLIC_RELATION_VOCAB = (
    "属于(下位→上位，如 Minecraft→沙盒游戏)、包含(整体→部分)、是(同一/定义)、"
    "具有(实体→属性)、位于、导致/造成(因果)、影响、使用、作用于、服务于、"
    "基于、可实现为/实现于、细化、需要、产生、增强、抑制、驱动、"
    "相关(弱关联，最后手段)、关联(兜底)"
)

_EXPAND_SYSTEM = (
    "你是知识图谱扩建器。给你若干【已有节点】（都属公共知识），请为每个节点"
    "提出值得入图的高质量邻居与链路。规则：\n"
    "1. 只输出严格 JSON（不要 markdown、不要解释）。\n"
    f"2. 关系词只能从这张表里选（方向按括号说明）：{PUBLIC_RELATION_VOCAB}。\n"
    "3. 每个种子节点：新节点 ≤6 个、新边 ≤12 条。宁可少而准，不要多而泛。\n"
    "4. 优先构建可被激活传播的语义链（上位/下位/组成/机制/因果/应用领域），"
    "并尽量让不同种子的输出互相复用概念、形成汇合的簇——"
    "【已给节点名单】里的名字（含其它种子的输出）优先复用而不是另造同义词。\n"
    "5. 禁止：时效性事实（当前版本/最新新闻/现任职位）；主观评价；"
    "与任何用户个人经历相关的推断；对明显专名不确定的实体直接跳过。\n"
    "6. 输出 schema：{\"items\": [{\"seed\":\"种子名\","
    "\"nodes\": [\"新节点名\", ...], "
    "\"edges\": [{\"src\":\"A\",\"dst\":\"B\",\"type\":\"属于\","
    "\"weight\":0.7,\"confidence\":0.8,\"reason\":\"≤25字\"}]}]}\n"
    "   edges 的 src/dst 允许是【已给节点名单】中的名字（补既有节点间的缺边），"
    "或本批新节点，不允许发明名单外且不在 nodes 里的名字。\n"
    "7. weight=这条边对理解该概念的重要度(0~1)，confidence=你对这条关系的把握(0~1)。"
)


def build_expand_prompt(seeds: list, existing_pool: list) -> str:
    """seeds: [{"id","desc"}]；existing_pool: 可复用的既有节点名（消歧+促汇合）。"""
    pool = "、".join(existing_pool[:150])
    lines = []
    for s in seeds:
        d = f"（{s['desc']}）" if s.get("desc") else ""
        lines.append(f"- {s['id']}{d}")
    return (
        "【已给节点名单】\n" + pool + "\n\n"
        "【本批种子（每个都要评估，不值得扩的 seed 输出空数组即可）】\n"
        + "\n".join(lines)
    )


_LINK_SYSTEM = (
    "你是图谱连边审判员。给你若干部【已有节点】对（同一图谱中的公共知识实体）。"
    "判断每对之间是否存在语义上成立、且值得进入知识图谱的**直接**关系。规则：\n"
    f"1. 关系词只能选：{PUBLIC_RELATION_VOCAB}；方向要正确（src→dst）。\n"
    "2. 只判断给定的对，不得新增节点、不得改写名字。\n"
    "3. 没有可靠直接关系就跳过该对——宁缺毋滥。弱相关不算。\n"
    "4. 只输出严格 JSON：{\"links\": [{\"pair\": i, \"src\":\"A\",\"dst\":\"B\","
    "\"type\":\"属于\",\"weight\":0.6,\"confidence\":0.8,\"reason\":\"≤25字\"}]}，"
    "pair 为对子的序号（从 0 起）。"
)


def build_link_prompt(pairs: list) -> str:
    lines = [f"{i}. {a} ？ {b}" for i, (a, b, _basis) in enumerate(pairs)]
    return "【候选对子】\n" + "\n".join(lines)


_BRIDGE_SYSTEM = (
    "你是知识图谱桥接员。给你图谱中两个互不连通的主题簇（各列代表节点）。"
    "找出能合理连接两簇的桥：既有的共同上位概念、交叉学科概念、真实的应用/历史"
    "联系。规则：1. 只输出严格 JSON；2. 桥必须语义成立且有公共知识依据，"
    "宁缺毋滥，找不到就输出空数组；3. 至多 3 条桥；4. 优先复用两侧已有节点名，"
    "确有必要才可提出 1 个新桥节点。\n"
    "schema: {\"bridges\": [{\"src\":\"A\",\"dst\":\"B\",\"type\":\"相关\","
    "\"weight\":0.6,\"confidence\":0.8,\"new\":false,\"reason\":\"≤30字\"}]}"
)


def build_bridge_prompt(cl_a: list, cl_b: list, existing_pool: list) -> str:
    return ("【簇A 代表节点】" + "、".join(cl_a) +
            "\n【簇B 代表节点】" + "、".join(cl_b) +
            "\n【图中已有节点可复用】" + "、".join(existing_pool[:120]))


# ── 候选生成：已有节点配对（任务书 §9：绝不 O(N²) 问 LLM）─────────────────

def gen_link_candidates(kg, classes: dict, degree: dict,
                        max_pairs: int = 120) -> list:
    """三路候选：共邻（A-→X←-B）、同型（同 type）、低度孤立点+嵌入相似（可选）。

    返回 [(a, b, basis)]，已去自反/去重/滤掉非公共与受保护节点。
    """
    public = {n for n, c in classes.items() if c == CLASS_PUBLIC}
    undirected = {}
    for e in kg.edges:
        if e.src in public and e.dst in public and e.src != e.dst:
            undirected.setdefault(e.src, set()).add(e.dst)
            undirected.setdefault(e.dst, set()).add(e.src)
    linked = {(min(e.src, e.dst), max(e.src, e.dst))
              for e in kg.edges if e.src != e.dst}

    out, seen = [], set()

    def push(a, b, basis):
        key = (min(a, b), max(a, b))
        if a == b or key in linked or key in seen:
            return
        seen.add(key)
        out.append((a, b, basis))

    # 1) 共享邻居：A 和 B 都连向同一个 X → 潜在关联
    #    限 X 度 ≤ 30，避免在万能枢纽下配对（那种"相关"多数无意义）
    by_hub = {}
    for e in kg.edges:
        if e.src in public and e.dst in public:
            by_hub.setdefault(e.src, set()).add(e.dst)
            by_hub.setdefault(e.dst, set()).add(e.src)
    for hub, nbs in by_hub.items():
        if len(nbs) > 30:
            continue
        nbs = sorted(nbs)
        for i in range(len(nbs)):
            for j in range(i + 1, min(i + 6, len(nbs))):
                push(nbs[i], nbs[j], f"shared:{hub}")

    # 2) 同 type 配对：mc_species 里"苦力怕/骷髅"等同类实体互查
    by_type = {}
    for nid in public:
        t = str((kg.nodes[nid].extra_attrs or {}).get("type") or "")
        if t:
            by_type.setdefault(t, []).append(nid)
    for t, group in by_type.items():
        group = sorted(group, key=lambda n: -degree.get(n, 0))[:20]
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if len(out) >= max_pairs:
                    break
                push(group[i], group[j], f"type:{t}")

    # 3) 嵌入相似：对低度公共节点跑 faiss 近邻（不可用则静默跳过）
    if len(out) < max_pairs:
        try:
            from embedding_manager import EmbeddingManager
            em = EmbeddingManager()
            em.refresh(kg)
            targets = sorted((n for n in public if degree.get(n, 0) <= 2),
                             key=lambda n: degree.get(n, 0))[:80]
            for nid in targets:
                if len(out) >= max_pairs:
                    break
                for hit in em.search(nid, top_k=6, min_similarity=0.55):
                    hid = hit.get("id") if isinstance(hit, dict) else None
                    if hid and hid in public:
                        push(nid, hid, "embedding")
        except Exception as e:
            logger.info(f"[Expansion] 嵌入候选跳过（不可用）: {e}")

    return out[:max_pairs]


def public_components(kg, classes: dict, min_size: int = 6) -> list:
    """公共知识子图的连通分量（桥接发现的对象），按大小降序。"""
    public = {n for n, c in classes.items() if c == CLASS_PUBLIC}
    adj = {n: set() for n in public}
    for e in kg.edges:
        if e.src in adj and e.dst in adj:
            adj[e.src].add(e.dst)
            adj[e.dst].add(e.src)
    comps, seen = [], set()
    for n in public:
        if n in seen:
            continue
        comp, stack = set(), [n]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            stack.extend(adj[x] - comp)
        seen |= comp
        if len(comp) >= min_size:
            comps.append(comp)
    return sorted(comps, key=len, reverse=True)


# ── 落图（守卫 + 溯源 + 台账 + 上限）─────────────────────────────────────

GUARD_REJECTED = {"protected_endpoint": 0, "self_loop": 0, "unknown_name": 0,
                  "dup": 0, "over_cap": 0, "low_conf": 0}


def apply_items(kg, items: list, canon_index: dict, state: dict,
                batch_id: str, guard, ledger_lines: list) -> dict:
    """一批 expand/link/bridge 结果落图。

    items: [{"seed", "nodes":[...], "edges":[{src,dst,type,weight,confidence,reason}]}]
    守卫：两端必须是图上已有节点（提案名先经 canonicalize）；受保护端点拒；
    置信 < guard["min_confidence"] 拒；每 seed 新边 ≤ guard["max_edges_per_seed"]。
    """
    stats = {"new_nodes": 0, "new_edges": 0, "merged_edges": 0,
             "rejected": {k: 0 for k in GUARD_REJECTED}}
    min_conf = float(guard.get("min_confidence", 0.55))
    max_e = int(guard.get("max_edges_per_seed", 12))
    max_n = int(guard.get("max_new_nodes_per_seed", 6))
    # 全局熔断：本次运行新增总量（跨 batch 由 guard 字典递减持有）
    node_budget = guard.setdefault("new_node_budget", 600)
    edge_budget = guard.setdefault("new_edge_budget", 1500)
    total_new_nodes = state.setdefault("_run", {}).get("new_nodes", 0)
    total_new_edges = state["_run"].get("new_edges", 0)

    def resolve(name):
        return canonicalize(name, canon_index, kg)

    for item in items:
        seed = resolve(str(item.get("seed") or ""))
        if not seed:
            continue
        # 先建本批新节点（受每 seed 与全局双上限约束）
        made = 0
        for nm in item.get("nodes") or []:
            if made >= max_n or total_new_nodes >= node_budget:
                break
            nid = resolve(str(nm))
            if nid:
                continue                      # 同义命中既有 → 直接复用，不建
            nm_s = str(nm or "").strip()
            if (not nm_s or len(nm_s) > 30 or _looks_like_noise(nm_s)
                    or nm_s in PROTECTED_IDS):
                continue
            node = Node(id=nm_s, weight=0.5, label="declarative-semantic",
                        graph_space="semantic",
                        extra_attrs={
                            "type": "expanded_knowledge",
                            "expansion": {"source": "llm_knowledge",
                                          "batch": batch_id, "ts": now_str(),
                                          "via_seed": seed,
                                          "version": STATE_VERSION}})
            try:
                kg.add_node(node)
            except Exception as e:
                logger.warning(f"[Expansion] 节点写入失败(跳过 {nm_s}): {e!r}")
                continue
            canon_index.setdefault(_norm_name(nm_s), nm_s)
            made += 1
            total_new_nodes += 1
            stats["new_nodes"] += 1
            state["expanded"].setdefault(seed, {}).setdefault(
                "new_nodes", []).append(nm_s)

        added = 0
        for e in item.get("edges") or []:
            if added >= max_e or total_new_edges >= edge_budget:
                stats["rejected"]["over_cap"] += 1
                continue
            src = resolve(str(e.get("src") or ""))
            dst = resolve(str(e.get("dst") or ""))
            rel = normalize_relation(str(e.get("type") or e.get("relation") or ""))
            try:
                conf = float(e.get("confidence", 0.6))
                w = float(e.get("weight", 0.5))
            except (TypeError, ValueError):
                conf, w = 0.6, 0.5
            if not src or not dst:
                stats["rejected"]["unknown_name"] += 1
                continue
            if src == dst:
                stats["rejected"]["self_loop"] += 1
                continue
            # 端点保护：受保护 hub、episodic/自我/引擎零件一律不建边。
            # 本次新建的 expanded_knowledge 节点不在分类快照里
            # （get 默认 CLASS_UNKNOWN）→ 放行，它们按定义就是公共知识。
            cls_map = classes_of(state)
            ca = cls_map.get(src, CLASS_UNKNOWN)
            cb = cls_map.get(dst, CLASS_UNKNOWN)
            if (src in PROTECTED_IDS or dst in PROTECTED_IDS
                    or ca in (CLASS_SELF, CLASS_EPISODIC, CLASS_PERSONAL,
                              CLASS_INFRA, CLASS_PROCEDURAL)
                    or cb in (CLASS_SELF, CLASS_EPISODIC, CLASS_PERSONAL,
                              CLASS_INFRA, CLASS_PROCEDURAL)):
                stats["rejected"]["protected_endpoint"] += 1
                continue
            if conf < min_conf:
                stats["rejected"]["low_conf"] += 1
                continue
            if kg.get_edge(src, dst, rel) is not None:
                stats["merged_edges"] += 1
                stats["rejected"]["dup"] += 1
                continue
            w = max(0.1, min(0.9, 0.5 * w + 0.5 * conf))
            edge = Edge(src=src, dst=dst, relation=rel, weight=w,
                        relation_category=category_for(rel))
            # hub 直连守卫（现机制是 warning-only，这里沿用其计数语义）
            check_hub_edge(src, dst, edge.relation)
            if not kg.add_edge(edge):
                stats["rejected"]["unknown_name"] += 1
                continue
            added += 1
            total_new_edges += 1
            stats["new_edges"] += 1
            ledger_lines.append(json.dumps({
                "src": src, "dst": dst, "rel": edge.relation,
                "category": edge.relation_category, "weight": w,
                "source": "llm_knowledge", "confidence": conf,
                "reason": str(e.get("reason") or "")[:60],
                "batch": batch_id, "ts": now_str()}, ensure_ascii=False))
            state["expanded"].setdefault(seed, {})
            state["expanded"][seed]["edges_committed"] = \
                state["expanded"][seed].get("edges_committed", 0) + 1
    state["_run"]["new_nodes"] = total_new_nodes
    state["_run"]["new_edges"] = total_new_edges
    return stats


def classes_of(state: dict) -> dict:
    """分类快照缓存在 state 里（同一次运行内稳定；跨运行由 --refresh 重建）。"""
    return state.get("_classes", {})


# ── LLM 调用与解析 ──────────────────────────────────────────────────────

def parse_json_output(text: str):
    """三级容错：直接 loads → 剥 ```json 围栏 → 截取首个 {..最后一个 }。"""
    s = str(text or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(.+?)```", s, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            s2 = m.group(1)
    else:
        s2 = s
    a, b = s2.find("{"), s2.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(s2[a:b + 1])
        except Exception:
            return None
    return None


def llm_backend(api_key=None, base_url=None, model=None, max_tokens=6000):
    """构建一次性 MiMo 后端（独立于 app 的运行时后端，带 HTTP 超时）。"""
    import openai
    from llm_provider import create_backend
    if not api_key or not base_url:
        cfg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        if not api_key:
            try:
                with open(os.path.join(cfg_dir, "llm_secret.json"),
                          encoding="utf-8") as f:
                    api_key = str(json.load(f).get("api_key") or "").strip()
            except Exception:
                api_key = ""
            api_key = api_key or os.environ.get("MIMO_API_KEY", "")
        if not base_url:
            try:
                with open(os.path.join(cfg_dir, "llm_config.json"),
                          encoding="utf-8") as f:
                    cfg = json.load(f)
                base_url = str(cfg.get("base_url") or "").strip()
                model = model or str(cfg.get("model") or "").strip()
            except Exception:
                pass
    backend = create_backend("mimo", api_key=api_key,
                             base_url=base_url or None,
                             model=model or None,
                             temperature=0.3, max_tokens=max_tokens,
                             json_mode=True)
    # 单请求超时——任务书 §22 之 Timeout：挂住的 batch 不能卡死整条管线
    try:
        import httpx
        backend._raw_client = openai.OpenAI(
            api_key=api_key, base_url=base_url or "https://api.xiaomimimo.com/v1",
            timeout=httpx.Timeout(180.0, connect=15.0))
    except Exception as e:
        logger.warning(f"[Expansion] 超时参数设置失败（用默认）: {e}")
    return backend


def call_batch(backend, system: str, user: str, retries: int = 2):
    """system+user → 严格 JSON dict；失败重试；全失败返回 None。"""
    last_err = ""
    for attempt in range(retries + 1):
        try:
            raw = backend.invoke([{"role": "system", "content": system},
                                  {"role": "user", "content": user}])
            data = parse_json_output(raw if isinstance(raw, str) else str(raw))
            if data is not None:
                return data, None
            last_err = "unparsable_json"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(min(20, 3 * (attempt + 1)))
    return None, last_err


# ── 台账/状态 ──────────────────────────────────────────────────────────

def load_state(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                st = json.load(f)
            if st.get("version") == STATE_VERSION:
                return st
        except Exception:
            pass
    return {"version": STATE_VERSION, "expanded": {}, "batches": [],
            "errors": []}


def save_state(path: str, state: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    st = {k: v for k, v in state.items() if not k.startswith("_")}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ── 统计报告（任务书 §25）─────────────────────────────────────────────

def graph_stats(kg, classes: dict, degree: dict) -> dict:
    comps_all = _components(kg, set(kg.nodes))
    degs = list(degree.values())
    return {
        "nodes": len(kg.nodes), "edges": len(kg.edges),
        "avg_degree": round(sum(degs) / max(1, len(degs)), 3),
        "isolated": sum(1 for d in degs if d == 0),
        "largest_component": max((len(c) for c in comps_all), default=0),
        "components": len(comps_all),
        "class_counts": {c: sum(1 for v in classes.values() if v == c)
                         for c in (CLASS_PUBLIC, CLASS_EPISODIC, CLASS_PERSONAL,
                                   CLASS_STATE, CLASS_INFRA, CLASS_PROCEDURAL,
                                   CLASS_SELF, CLASS_UNKNOWN)},
    }


def _components(kg, node_set: set) -> list:
    adj = {n: set() for n in node_set}
    for e in kg.edges:
        if e.src in adj and e.dst in adj:
            adj[e.src].add(e.dst)
            adj[e.dst].add(e.src)
    seen, comps = set(), []
    for n in node_set:
        if n in seen:
            continue
        comp, stack = set(), [n]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            stack.extend(adj[x] - comp)
        seen |= comp
        comps.append(comp)
    return comps


def top_nodes(kg, degree: dict, n=20):
    return sorted(degree.items(), key=lambda x: -x[1])[:n]
