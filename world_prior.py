# world_prior.py — 目标驱动的**最小先验闭环**（学习实验 2026-09-25，§5/§6/§10）
# ============================================================================
# 职责（只此两件，不是规划器、不是行为链）：
#   1. build_recipe_closure(kg, engine, target, bridge)
#      从目标物品出发，沿**运行时 minecraft-data**（bot 的 /recipe_for、
#      /block_meta，版本=服务器协商版本）反向补齐"配方→原料→可挖方块→
#      工具"链条，写进既有图词汇：配方:x -需要-> 物品:mat / -产生-> 物品:result、
#      方块 -掉落-> 物品:x、方块 -需要工具-> 物品:tool。每条边带 provenance
#      （knowledge_type/mc_version/version_verified/confidence）。运行时查
#      得到的才是 prior；查不到就如实不写（§5：不把攻略当事实、不跨版本混用）。
#      另对种子图（mc_world）已知的"方块-产生->物品"做**运行时核验**：
#      补版本正确的 掉落/需要工具 边。
#   2. next_step(kg, target, inventory)
#      纯读图的"下一步可达性提示"：done / craft / smelt / gather / explore。
#      **只产提示，不产执行**——候选生成、评分、可行性、承诺全部仍归
#      autonomy/Action System（§12 薄层约束）。零 LLM。
# ============================================================================

import logging
import math
import time

logger = logging.getLogger(__name__)

MAX_DEPTH = 8               # 闭包 BFS 跳数上限（防环；查得越深写图越多）。
# 爬链上限**不等于**闭包上限（26.1 实测）：空背包挖铁矿的真实链条是
# iron_ingot→raw_iron→(需要工具)铜镐→铜锭→铜矿→(需要工具)石镐→圆石→
# (石头需要)木镐→木板→橡木原木 = 10 跳，用 6 会半路断成"定向探索"。
# seen 集合负责防环，这里只放宽可达深度。
CHAIN_MAX_DEPTH = 14
_QUERY_TTL_S = 3600.0       # 配方表不随时间变：同一目标查一次就够
_QUERY_FAIL_TTL_S = 60.0    # 失败缓存短 TTL：桥竞态/瞬断不等于"没有这条
                            # 知识"，1h 负缓存会截断配方闭包（2026-09-26 修）
_RUNTIME = {"mc_version": "", "mcd_version": "", "queried": {}, "stats": {}}
_CFGREF = {}                # bind_config 注入（读 experiment.prior_extra）


def bind_config(config) -> None:
    """装配期注入：人工先验表（experiment.prior_extra）从这里读。"""
    _CFGREF["config"] = config or {}


def runtime_info() -> dict:
    return dict(_RUNTIME)


# ── 公共知识：燃料与熔炼 ──────────────────────────────────────────────
# 燃料是世界的**机制属性**，不是技能层的枚举清单（用户 2026-09-27 定：
# "木头/煤炭可以当燃料是公共知识，一定要通用"）。所以知识写成数据：
# 精确名列出"燃料本尊"，木质**家族**用后缀规则表达——世界里任何新木种
# （云杉/金合欢/红树…）和它们的木板/台阶/楼梯自动进知识库，零改码。
# 燃烧秒数是原版机制常识，不来自运行时数据，按 prior_extra 同一规范
# 对待（使用侧 provenance 标 version_verified=False）。
SMELT_BURN_S = 10.0          # 熔炼一件所需燃料秒数
_FUEL_MARGIN_S = 2.0         # 开盖/放料等操作耗时余量
FUEL_BURN_S = {              # 单件燃烧秒数（精确名）
    "coal": 80.0, "charcoal": 80.0, "coal_block": 800.0,
    "stick": 5.0, "blaze_rod": 120.0, "lava_bucket": 1000.0,
}
FUEL_SUFFIX_BURN_S = {       # 家族规则：后缀匹配，取最长命中
    "_log": 15.0, "_wood": 15.0, "_planks": 15.0,
    "_slab": 7.5, "_stairs": 7.5,
}


def fuel_burn_s(name) -> float:
    """该物品单件燃烧秒数；0 = 不是燃料。coal_ore 也是 0——矿石是
    燃料的**候选**（烧它得先有火），不是燃料本身。"""
    n = str(name or "").strip().lower()
    if not n:
        return 0.0
    if n in FUEL_BURN_S:
        return float(FUEL_BURN_S[n])
    hit = ""
    for suf in FUEL_SUFFIX_BURN_S:
        if n.endswith(suf) and len(suf) > len(hit):
            hit = suf
    return float(FUEL_SUFFIX_BURN_S.get(hit, 0.0))


def fuel_pieces(name, need_items: int = 1) -> int:
    """烧够 need_items 件要放几根该燃料（向上取整+操作余量）；0=不可燃。"""
    burn = fuel_burn_s(name)
    if burn <= 0:
        return 0
    need_s = SMELT_BURN_S * max(1, int(need_items)) + _FUEL_MARGIN_S
    return int(math.ceil(need_s / burn))


def best_fuel(have_counts: dict, need_items: int = 1) -> tuple:
    """从 {物品名: 背包数量} 里选燃料，返回 (名称, 放入根数)。
    偏好：**件数少者优先**（烧 coal×1 好过烧 stick×3——省物品），并列时
    总燃烧秒数小者（能不拿 1000 秒的熔岩桶烧一件就不拿）。凑不齐一份
    如实返回 ("", 0)——让认知层去学"要找燃料"，而不是半炉火糊弄过去。"""
    ranked = []
    for name, cnt in (have_counts or {}).items():
        burn = fuel_burn_s(name)
        cnt = int(cnt or 0)
        if burn <= 0 or cnt <= 0:
            continue
        pieces = fuel_pieces(name, need_items)
        if pieces <= cnt:
            ranked.append((pieces, burn * pieces, str(name)))
    if not ranked:
        return "", 0
    ranked.sort()
    pieces, _, name = ranked[0]
    return name, pieces


def _prov(knowledge_type="prior", source="minecraft-data:runtime",
          version_verified=True, confidence=0.9) -> dict:
    return {"knowledge_type": knowledge_type, "source": source,
            "mc_version": _RUNTIME.get("mc_version") or "",
            "version_verified": bool(version_verified),
            "confidence": float(confidence)}


def _item(name) -> str:
    return f"物品:{str(name or '').strip().lower()}"


def _ensure(kg, node_id, *, label="declarative-semantic", space="semantic",
            attrs=None) -> bool:
    """幂等建节点（引用即建——add_edge 会静默丢端点缺失的边，教训在册）。
    返回是否**新建**。"""
    with kg._lock:
        n = kg.get_node(node_id)
        if n is None:
            from graph_model import Node
            kg.add_node(Node(id=node_id, weight=0.45, label=label,
                             graph_space=space, extra_attrs=dict(attrs or {})))
            return True
        ea = dict(n.extra_attrs or {})
        changed = False
        for k, v in (attrs or {}).items():
            if k not in ea:
                ea[k] = v
                changed = True
        if changed:
            n.extra_attrs = ea
        return False


def _edge(kg, src, rel, dst, weight=0.6, cat="procedural_relation",
          provenance=None, extra=None) -> bool:
    """幂等建边（带 provenance / 数量）。返回是否新建。"""
    from graph_model import Edge
    if kg.get_edge(src, dst, rel) is not None:
        return False
    attrs = {"provenance": provenance or _prov()}
    attrs.update(extra or {})
    kg.add_edge(Edge(src=src, dst=dst, relation=rel, weight=weight,
                     relation_category=cat, extra_attrs=attrs))
    return True


def _mark(kg, engine, node_ids) -> None:
    """新写入节点进活跃前沿（前沿不变量，memory/active-frontier-invariant）。"""
    if engine is None:
        return
    try:
        engine.mark_active(list(node_ids))
    except Exception:
        pass


# ── 运行时查询（经桥；失败=如实"没有这条知识"）────────────────


def _q(bridge, fname, arg, key):
    hit = _RUNTIME["queried"].get(key)
    if hit:
        # 成功缓存走长 TTL；失败（None）只用短 TTL——一次桥竞态不该把
        # "未知配方/未知方块"钉一小时（recipe 截断→计划错步）。
        ttl = _QUERY_TTL_S if hit[1] is not None else _QUERY_FAIL_TTL_S
        if time.time() - hit[0] < ttl:
            return hit[1]
    try:
        r = getattr(bridge, fname)(arg) if bridge is not None else {}
    except Exception:
        r = {}
    out = r if isinstance(r, dict) and r.get("ok") else None
    _RUNTIME["queried"][key] = (time.time(), out)
    if isinstance(r, dict):
        _RUNTIME["mc_version"] = str(r.get("mc_version") or
                                     _RUNTIME.get("mc_version") or "")
        _RUNTIME["mcd_version"] = str(r.get("mcd_version") or
                                      _RUNTIME.get("mcd_version") or "")
    return out


def _recipe_for(bridge, item):
    return _q(bridge, "recipe_for", str(item), f"recipe:{item}")


def _block_meta(bridge, block):
    return _q(bridge, "block_meta", str(block), f"block:{block}")


def _extra_priors():
    return ((_CFGREF.get("config") or {}).get("experiment") or {}).get(
        "prior_extra") or []


# ── 1. 先验闭包 ────────────────────────────────────────────


def build_recipe_closure(kg, engine, target: str, bridge=None) -> dict:
    """目标物品 ← 配方 ← 原料 ← …（BFS 到"可挖方块+其工具"层）。
    链条形状来自运行时数据，本函数只是搬运工。返回统计。"""
    if kg is None:
        return {"skipped": True}
    stats = {"nodes": 0, "edges": 0, "recipes": 0, "blocks": 0,
             "manual": 0, "mc_version": "", "verified": 0}
    t = str(target or "").lower()
    if not t:
        return stats
    seen, frontier, touched = {t}, [t], []
    depth = 0
    while frontier and depth < MAX_DEPTH:
        nxt = []
        for item in frontier:
            _expand_item(kg, engine, item, bridge, stats, nxt, touched)
        frontier = [x for x in nxt if x not in seen]
        seen |= set(frontier)
        depth += 1
    stats["mc_version"] = _RUNTIME.get("mc_version") or ""
    stats["depth"] = depth
    _RUNTIME["stats"] = dict(stats, target=t, at=time.time())
    _mark(kg, engine, touched[:64])
    try:
        import experiment_mode as xm
        xm.xlog("GRAPH", f"先验闭包 target={t} 边={stats['edges']} "
                  f"节点={stats['nodes']} 配方={stats['recipes']} "
                  f"方块={stats['blocks']} 运行时核验={stats['verified']} "
                  f"人工先验={stats['manual']} "
                  f"版本={stats['mc_version'] or '未取到'}", **stats)
    except Exception:
        pass
    return stats


def _expand_item(kg, engine, item, bridge, stats, nxt, touched) -> None:
    """一个物品的产出方式入图；未展开的原料/工具进下一层。"""
    prod = _item(item)
    if _ensure(kg, prod, label="declarative-episodic", space="episodic",
               attrs={"type": "mc_item", "provenance": _prov()}):
        stats["nodes"] += 1
        touched.append(prod)
    # ① 运行时合成配方（minecraft-data 里真有的才算 prior）。
    # 同一产物可能有多条配方（八种木板各能造工作台）——**逐条建节点**，
    #  producer 语义天然是"任一条满足即可"（合并 rid 会把它错成"全部都要"）。
    rec = _recipe_for(bridge, item)
    for i, r in enumerate(((rec or {}).get("recipes") or [])):
        res = str(r.get("result") or item).lower()
        rid = f"配方:{res}:{'table' if r.get('needs_table') else 'hand'}:{i}"
        if _ensure(kg, rid, attrs={"type": "recipe", "kind": "craft",
                                   "needs_table": bool(r.get("needs_table")),
                                   "yield": r.get("yield", 1)}):
            stats["nodes"] += 1
            touched.append(rid)
        if _edge(kg, rid, "产生", _item(res), provenance=_prov()):
            stats["edges"] += 1
        stats["recipes"] += 1
        for mat, cnt in (r.get("ingredients") or {}).items():
            m = str(mat).lower()
            if _ensure(kg, _item(m), label="declarative-episodic",
                       space="episodic",
                       attrs={"type": "mc_item", "provenance": _prov()}):
                stats["nodes"] += 1
                touched.append(_item(m))
            if _edge(kg, rid, "需要", _item(m), provenance=_prov(),
                     extra={"count": int(cnt or 1)}):
                stats["edges"] += 1
            nxt.append(m)
        if r.get("needs_table"):
            # 工作台是**放置型元工具**：它的配方也进闭包（没有它这条
            # 配方永远做不出来——链条完整性，不是攻略）
            nxt.append("crafting_table")
    # ② 运行时方块数据：物品同名方块 / 掉落它的已知方块（核验种子边；
    # 种子图用裸名节点 ID（"raw_iron"），运行时层用 物品:x——两边都认）
    cand = [item]
    for e in list(kg.edges):
        if e.relation == "产生" and str(e.dst) in (prod, item):
            n = kg.get_node(str(e.src))
            if n is not None and (n.extra_attrs or {}).get("type") == \
                    "mc_species":
                cand.append(str(e.src))
    for blk in cand:
        meta = _block_meta(bridge, blk)
        if not meta:
            continue
        stats["blocks"] += 1
        if blk != item:
            stats["verified"] += 1
        bn = str(meta.get("block") or blk).lower()
        if _ensure(kg, bn, attrs={"type": "mc_species", "source": "block_meta",
                                  "provenance": _prov()}):
            stats["nodes"] += 1
            touched.append(bn)
        for d in (meta.get("drops") or []):
            dn = str(d).lower()
            _ensure(kg, _item(dn), label="declarative-episodic",
                    space="episodic",
                    attrs={"type": "mc_item", "provenance": _prov()})
            if _edge(kg, bn, "掉落", _item(dn), provenance=_prov()):
                stats["edges"] += 1
            nxt.append(dn)
        for tool in (meta.get("harvest_tools") or []):
            tn = str(tool).lower()
            _ensure(kg, _item(tn), label="declarative-episodic",
                    space="episodic",
                    attrs={"type": "mc_item", "provenance": _prov()})
            if _edge(kg, bn, "需要工具", _item(tn), provenance=_prov()):
                stats["edges"] += 1
            nxt.append(tn)     # 工具的配方链也进闭包（石镐←圆石+木棍←木板）
    # ③ 人工先验（只补运行时缺口；provenance 明写 version_verified=False）
    for ex in _extra_priors():
        if str(ex.get("result") or "").lower() != item:
            continue
        ep = ex.get("provenance") or {}
        prov = _prov(knowledge_type=str(ep.get("knowledge_type") or "prior"),
                     source=str(ep.get("source") or "manual"),
                     version_verified=bool(ep.get("version_verified", False)),
                     confidence=float(ep.get("confidence", 0.4)))
        rid = f"配方:{item}:{ex.get('kind') or 'extra'}"
        if _ensure(kg, rid, attrs={"type": "recipe",
                                   "kind": str(ex.get("kind") or "extra"),
                                   "tool": str(ex.get("tool") or ""),
                                   "fuel": bool(ex.get("fuel")),
                                   "provenance": prov}):
            stats["nodes"] += 1
            touched.append(rid)
        if _edge(kg, rid, "产生", prod, provenance=prov):
            stats["edges"] += 1
        for mat, cnt in (ex.get("inputs") or {}).items():
            m = str(mat).lower()
            _ensure(kg, _item(m), label="declarative-episodic",
                    space="episodic",
                    attrs={"type": "mc_item", "provenance": prov})
            if _edge(kg, rid, "需要", _item(m), provenance=prov,
                     extra={"count": int(cnt or 1)}):
                stats["edges"] += 1
            nxt.append(m)
        _tool = str(ex.get("tool") or "").lower()
        if _tool:
            # 熔炉这类"前置设备"不建 需要 边——建了就被 _inputs_ok 当原料算，
            # "炉子已摆在地上"永远判不齐；只把它的配方拉进闭包，让
            # next_step(炉子) 有路可爬。"设备在不在场"的裁决归 autonomy 的
            # smelt 分支（seen_blocks ∨ inv）。
            nxt.append(_tool)
        stats["manual"] += 1


# ── 2. 图查询（读）─────────────────────────────────────────


def producers_of(kg, item: str) -> list:
    """[{"recipe", "kind", "needs_table", "tool", "inputs":{mat:count}}]"""
    dst = _item(item)
    out = []
    try:
        edges = list(kg.edges)
        for e in edges:
            if e.relation != "产生" or str(e.dst) != dst:
                continue
            rid = str(e.src)
            node = kg.get_node(rid)
            ea = dict((node.extra_attrs or {}) if node else {})
            if ea.get("type") != "recipe":
                continue
            inputs = {}
            for e2 in edges:
                if e2.relation == "需要" and str(e2.src) == rid:
                    m = str(e2.dst)
                    if m.startswith("物品:"):
                        cnt = int((e2.extra_attrs or {}).get("count") or 1)
                        inputs[m[3:]] = max(1, cnt)
            out.append({"recipe": rid,
                        "kind": str(ea.get("kind") or "craft"),
                        "needs_table": bool(ea.get("needs_table")),
                        "tool": str(ea.get("tool") or ""),
                        "inputs": inputs})
    except Exception as exc:
        logger.debug(f"[WorldPrior] producers_of({item}) 失败: {exc}")
    return out


def tool_required(kg, block: str) -> list:
    """该方块在图上的工具需求（运行时 需要工具 ∨ 种子）。
    工具节点两边命名都认（"物品:x" 与种子裸名 "x"）。"""
    out = []
    try:
        for e in list(kg.edges):
            if (e.relation == "需要工具"
                    and str(e.src) == str(block or "").lower()):
                d = str(e.dst)
                if d.startswith("物品:"):
                    d = d[3:]
                if d and d not in out:
                    out.append(d)
    except Exception:
        pass
    return out


def drop_sources(kg, item: str) -> list:
    """哪些方块产出/掉落该物品（运行时 掉落 边 ∨ mc_world 种子 产生 边，
    且端点须是方块物种节点——排除"配方-产生->物品"。种子节点用裸名，
    运行时层用 物品: 前缀，两种 dst 都认）。"""
    dst = _item(item)
    bare = str(item or "").lower()
    out = []
    try:
        for e in list(kg.edges):
            if e.relation in ("掉落", "产生") and str(e.dst) in (dst, bare):
                src = str(e.src)
                if src in (dst, bare) and e.relation != "掉落":
                    # 种子自环（oak_log-产生->oak_log）不算"来源"；但运行时
                    # block_meta 核验过的自掉落（挖橡木原木得橡木原木）是真实
                    # 来源——否则"原木"这类方块永远进不了 gather 跳链。
                    continue
                node = kg.get_node(src)
                t = (node.extra_attrs or {}).get("type") if node else None
                # 方块身份判据：物种节点 ∨ 运行时 block_meta 核验过的掉落边。
                # 后者不是放水：mc_world 的 produces 自掉落行（oak_log→oak_log、
                # cobblestone→cobblestone）会把方块节点的 type 覆写成 mc_item，
                # 于是"挖原木得原木"这条真实来源在标签上消失了——信物证
                # （block_meta 只对真方块应答）比信被覆写的标签可靠。
                if t == "mc_species" or e.relation == "掉落":
                    out.append(src)
    except Exception:
        pass
    return out


def _inputs_ok(inputs: dict, inv: dict) -> bool:
    return all(int(inv.get(m, 0) or 0) >= (c or 1)
               for m, c in (inputs or {}).items())


def _missing(inputs: dict, inv: dict) -> list:
    return [m for m, c in (inputs or {}).items()
            if int(inv.get(m, 0) or 0) < (c or 1)]


def _gatherable_block(kg, block) -> bool:
    """这个方块在不在图上的 `属于→可采资源` 分类里。该分类是执行层物理白名单
    （skills/gathering.SAFE_GATHER_BLOCKS）的图镜像（mc_world.gatherable 播种），
    分类外的方块提交上去必被 `requires_user_permission` 拒绝。26.1 实测正是如
    此：block_meta 说 cherry_planks 掉 cherry_planks，于是爬链停在"去挖一块木
    板"，连着三次被拒——而同一张图上"cherry_log 属于可采资源"早就写着。读图能
    避免的失败，不该靠重试去碰。"""
    try:
        return kg.get_edge(str(block or "").split(":", 1)[-1],
                           "可采资源", "属于") is not None
    except Exception:
        return True         # 查询异常时按"可采"处理（保守性由执行层兜底）


def _gather_rank(kg, item: str):
    """item 有没有"执行层真能采"的方块来源：

      0 = 至少一个来源方块在可采资源分类里（铁矿、石头、原木…）
      2 = 来源方块都不在这个分类里（木板、铁块这类玩家制品）
      None = 图上没有任何方块来源
    """
    srcs = drop_sources(kg, item)
    if not srcs:
        return None
    return 0 if any(_gatherable_block(kg, s) for s in srcs) else 2


def _leaf_siblings(kg, chain, bare) -> list:
    """叶子的**并列等价来源**（26.1 真机修正）。爬链在配方层就选定了物种：
    stick 有八条 *_planks 配方、每条 planks 又各有一条 *_log——叶子只看到
    自己选中那条，于是死盯 cherry_log 连吃 20 次 not_found_in_radius，而
    24 格外就有真的 oak 和 acacia（它们与 cherry_log 在图上是并列的、没有任何
    判据说"要樱花不要橡木"）。修法不是换一套排序，而是把"到底采哪种"交回
    世界：这里把 parent 的各条配方能采到的来源全列出来，交给 gather 的
    variants（执行层逐成员 find_blocks 现查、取最近者，机制在
    skills/gathering._phase_find 里早已存在且只当命名学处理）。哪个物种在场
    由探测决定，代码里依旧没有一个木材/矿石的名字。

    parent 不取固定的 chain[-2]：真机链条（2026-09-25 实测）结尾是
    …→stick→cherry_planks→cherry_log(→cherry_log 重复)，倒数第二个就是叶子
    自己，展开恒为空。改为从近到远找**第一个其展开集包含 bare 的祖先**——
    那正是 bare 所在的那个并列层（stick 层：八条 planks 配方的来源）。
    更高层（如 iron_ingot）的展开不含 bare，不会把不相干的矿石混进来。

    展开也不止一层：真机闭包只有部分物种建了 planks 配方（26.1 图实测
    acacia/dark_oak/mangrove/cherry 有、oak/spruce/birch/jungle 缺），
    drop_sources(oak_planks)=[] 时单层展开会把它们整体漏掉。缺料本身不可采
    时沿它的 producer 再跳一层（限深，同样只读图）。"""
    def _srcs(item, depth):
        s = []
        for src in (drop_sources(kg, item) or []):
            sb = str(src).split(":", 1)[-1]
            if _gatherable_block(kg, sb) and sb not in s:
                s.append(sb)
        if s or depth <= 0:
            return s
        for p in producers_of(kg, item):
            for m in (p.get("inputs") or {}):
                for r in _srcs(str(m).split(":", 1)[-1], depth - 1):
                    if r not in s:
                        s.append(r)
        return s

    out = [str(bare)]
    if len(chain) < 2:
        return out
    uniq = []
    for x in chain:
        b = str(x).split(":", 1)[-1]
        if b not in uniq:
            uniq.append(b)
    try:
        for parent in reversed(uniq[:-1]):
            ext = []
            for p in producers_of(kg, parent):
                for m in (p.get("inputs") or {}):
                    for sb in _srcs(str(m).split(":", 1)[-1], 2):
                        if sb not in ext:
                            ext.append(sb)
            if str(bare) in ext and len(ext) > 1:
                out.extend([e for e in ext if e not in out])
                return out
    except Exception:
        pass                    # 展开失败不拦路：[bare] 单候选照常可执行
    return out


def _evidence_rank(kg, item: str, inv: dict) -> int:
    """这件原料在图上的"证据强度"（爬链选路用；越小越可信）。

    0 = 背包里就有；或有硬的方块来源（见 _gather_rank）
    1 = 图上有它的配方，且每条配方的缺料都已有方块来源
    2 = 图上有它的配方（料还远），或只能靠挖人工制品方块
    3 = 图上没有任何来源（走不通）

    排序依据是**证据种类**，不是攻略顺序：26.1 运行时给 iron_ingot 两条合料
    配方（9 铁粒 / 1 铁块），铁块那条的来源只是"自掉落的人工制品"（等级 2），
    而人工先验那条（raw_iron←铁矿）是硬来源（等级 0）——熔炼分支自然胜出。
    代码里没有一处 "if iron"。
    """
    if int(inv.get(str(item or "").split(":", 1)[-1], 0) or 0) > 0:
        # 手上就有的东西是最硬的证据（2026-09-25 真机教训：并列配方层对
        # 背包视而不见，acacia_log 攒到 15 个也压不过激活值更高的樱桃木）。
        return 0
    g = _gather_rank(kg, item)
    if g == 0:
        return 0
    prods = producers_of(kg, item)
    if not prods:
        return 2 if g is not None else 3
    for p in prods:
        miss = _missing(p["inputs"], inv)
        if all(_gather_rank(kg, m) == 0 for m in miss):
            return 1
    return 2


# _chain_cost 的"走不通/成环"罚值：必须远大于任何**可行**链的累加——
# 代价按配方份数累乘（3 圆石 ×30 这类），真实可行链可以到几百；罚值太小
# 会把"可行但深"的分支顶过"死链"，min() 就按边序捡回真正走不通的那条
# （2026-09-25 离线实测：99 被石镐链 110 越过，铜镐死而复选）。
_CHAIN_DEAD = 1e9
# 递归深度上限：真实链条（26.1 空背包挖铁矿：原木→木板→木棍→木镐→圆石
# →石镐→铁矿→粗铁→铁锭）在成本函数里要下探 12-13 层才见底（实测 6/10 层
# 会把钻石镐下面的铁镐支链饿死成死值）。memo 键只有 (物品, 深度)，状态数
# 有界，深度放宽不带来组合爆炸。
_CHAIN_DEPTH = 13


def _chain_cost(kg, item: str, inv: dict, seen=None, depth=_CHAIN_DEPTH,
                memo=None) -> float:
    """把 item 弄到手的**全链代价**（越小越先选；纯读图，零物品名分支）。

      0   = 背包里就有
      1   = 现场一跳可采（可采方块、无需工具）
      n   = 每往下一跳（合成一跳、换工具一跳）+1，按配方份数累加
      罚值 = 走不通 / 会爬回 next_step 爬链已有的节点（成环）

    为什么不用旧的单层 rank×10：26.1 真机（2026-09-25）它把钻石镐排在
    木镐前面——钻石是 rank-0 硬来源而木板是 rank-1，单层视角看不见"取这
    颗钻石还得先有一把铁镐、而铁镐绕回铁锭（目标本体，环）"；背包感知
    同样缺位，acacia_log 攒了 15 个，并列的 8 条木棍配方里 acacia_planks
    那条仍不占优，于是永远在野外采"激活值更高"的那种木头。

    seen 是 next_step 爬链至今的节点集，**扁平使用**（命中即罚，不再逐层
    累积进 memo 键）：memo 键 = (物品, 深度)，状态数有界；递归自身的环
    （木板→木棍→木板）由"先按罚值占位、算完覆写"的守卫处理。同一份
    memo 只在单次 next_step 调用内活着，seen 在调用内恒定，不串味。"""
    bn = str(item or "").split(":", 1)[-1].strip().lower()
    if not bn:
        return _CHAIN_DEAD
    if int(inv.get(bn, 0) or 0) > 0:
        return 0.0
    if depth <= 0:
        return _CHAIN_DEAD
    if bn in (seen or ()):
        return _CHAIN_DEAD
    if memo is None:
        memo = {}
    # 每次顶层调用共享一张查询缓存：递归会反复问同一批物品的
    # 配方/来源/工具（stick/planks 在并列层被问十几次），producers_of 一类
    # 读函数是全边扫描，不缓存的话 autonomy 每拍都白付一遍 O(边数)。
    qcache = memo.get("__q__")
    if qcache is None:
        qcache = memo["__q__"] = {}
    key = (bn, depth)
    if key in memo:
        return memo[key]
    memo[key] = _CHAIN_DEAD          # 先按最坏占位：递归里遇到自己即环
    try:
        best = None
        # ① 现场可采的来源方块（工具链递归计入——"能采"的前提是镐在手）
        if ("srcs", bn) not in qcache:
            qcache[("srcs", bn)] = [
                str(b).split(":", 1)[-1]
                for b in (drop_sources(kg, bn) or [])
                if _gatherable_block(kg, str(b).split(":", 1)[-1])]
        srcs = qcache[("srcs", bn)]
        for b in srcs:
            cost = 1.0
            if ("tools", b) not in qcache:
                qcache[("tools", b)] = tool_required(kg, b)
            tools = qcache[("tools", b)]
            if tools:
                tcost = min(_chain_cost(kg, t, inv, seen, depth - 1, memo)
                            for t in tools)
                if tcost >= _CHAIN_DEAD:
                    continue         # 工具链全是环/走不通：这条来源作废
                cost += tcost
            if best is None or cost < best:
                best = cost
        # ② 配方（含熔炼：tool 也算一份输入；needs_table 时工作台是隐输入）
        if ("prods", bn) not in qcache:
            qcache[("prods", bn)] = producers_of(kg, bn)
        for p in (qcache[("prods", bn)] or []):
            inputs = dict(p.get("inputs") or {})
            tool = str(p.get("tool") or "")
            if tool:
                inputs[tool] = max(1, int(inputs.get(tool, 0) or 0))
            if p.get("needs_table") and int(inv.get("crafting_table", 0)
                                            or 0) <= 0:
                inputs.setdefault("crafting_table", 1)
            sub = 1.0
            ok = True
            for m, c in inputs.items():
                ccost = _chain_cost(kg, m, inv, seen, depth - 1, memo)
                if ccost >= _CHAIN_DEAD:
                    ok = False
                    break
                sub += ccost * max(1, int(c or 1))
            if ok and (best is None or sub < best):
                best = sub
        memo[key] = _CHAIN_DEAD if best is None else best
        return memo[key]
    except Exception:
        return _CHAIN_DEAD


def _step(item, pr, chain, note="") -> dict:
    kind = str(pr.get("kind") or "craft")
    act = {"smelt": "smelt", "furnace": "smelt"}.get(kind, "craft")
    return {"done": False, "action": act, "item": item, "block": "",
            "recipe": str(pr.get("recipe") or ""),
            "tool": str(pr.get("tool") or ""),
            "needs_table": bool(pr.get("needs_table")),
            "inputs": dict(pr.get("inputs") or {}),
            "why": note or f"{item} 可由 {pr.get('recipe')} 产出（原料已齐）",
            "chain": chain}


def next_step(kg, target: str, inventory: dict, exclude=None) -> dict:
    """沿图跳链给出**下一步提示**（≤CHAIN_MAX_DEPTH 跳，seen 防环）。
    返回 {done, action: done|craft|smelt|gather|explore, item, block,
    recipe, why, chain}。只是提示：可执行性与评分仍由 autonomy 判定。

    exclude：近期反复"半径内找不到"的方块名（autonomy._locally_absent 的
    证据）。**只降级、不禁用**——若来源全被排除则忽略排除集照常返回，
    免得把链条做成死路（暂态证据 permanent 化正是 2026-09-22 定性的坑）。"""
    _ex = {str(x).split(":", 1)[-1] for x in (exclude or set())}

    _abs_memo: dict = {}

    def _abs(name, _depth=0, _trail=None) -> int:
        """0=可以用，1=此地反复"半径内找不到"（判据是 autonomy 的失败回执）。

        26.1 没有泛型 planks —— 每种原木一条 *_planks、每条 *_planks 一条
        stick，所以降级必须**顺着图往上传**：原木采不到 → 用它做的木板也不
        可得 → 用那块木板的配方换物种。判据全在图上的 掉落/需要 边里，代码
        没有一个物种名。三层封顶（原木→木板→木棍够了），成环即停（自掉落
        边 掉落:x 会把方块算成自己的来源，必须先剔掉）。
        只降级、不禁止：所有候选都缺席时照样返回最好的那个，不做死路。"""
        bn = str(name or "").split(":", 1)[-1]
        if not _ex:
            return 0
        hit = _abs_memo.get((bn, _depth))
        if hit is not None:
            return hit
        if bn in _ex:
            _abs_memo[(bn, _depth)] = 1
            return 1
        if _depth >= 3:
            return 0
        _trail = _trail or set()
        if bn in _trail:
            return 0
        _trail = _trail | {bn}
        out = 0
        try:
            srcs = [str(s).split(":", 1)[-1]
                    for s in (drop_sources(kg, bn) or [])
                    if str(s).split(":", 1)[-1] != bn]
            if srcs and all((str(x) in _ex) for x in srcs):
                out = 1
            else:
                prods = producers_of(kg, bn)
                dead = []
                for p in prods:
                    miss = _missing(p["inputs"], inv)
                    if miss and all(_abs(m, _depth + 1, _trail) for m in miss):
                        dead.append(True)
                    else:
                        dead.append(False)
                if prods and all(dead):
                    out = 1
        except Exception:
            out = 0
        _abs_memo[(bn, _depth)] = out
        return out

    def _vis(name) -> float:
        """这个原料在图上的"被她感知过"程度（节点激活值，越大越近）。
        并列配方的第三判据：同样可得时，先要眼前有的那种。"""
        bn = str(name or "").split(":", 1)[-1]
        best = 0.0
        try:
            n = kg.get_node(bn) or kg.get_node(_item(bn))
            best = max(best, float(getattr(n, "activation", 0.0) or 0.0)
                       if n else 0.0)
            for s in (drop_sources(kg, bn) or []):
                sb = str(s).split(":", 1)[-1]
                sn = kg.get_node(sb)
                best = max(best, float(getattr(sn, "activation", 0.0) or 0.0)
                           if sn else 0.0)
        except Exception:
            return 0.0
        return best
    inv = {str(k).lower(): int(v or 0) for k, v in (inventory or {}).items()}
    t = str(target or "").lower()
    if not t:
        return {"done": True, "action": "done", "item": "", "block": "",
                "recipe": "", "why": "无目标", "chain": []}
    if inv.get(t, 0) > 0:
        return {"done": True, "action": "done", "item": t, "block": "",
                "recipe": "", "why": f"背包已有 {t} x{inv[t]}",
                "chain": [t]}
    cur, chain, seen = t, [t], {t}
    for _hop in range(CHAIN_MAX_DEPTH):
        prods = producers_of(kg, cur)
        ready = [p for p in prods if _inputs_ok(p["inputs"], inv)]
        if ready:
            # 并列的可行配方：先避开缺料里"此地找不到"的，再按原料证据强度、
            # 再按原料种类数定序（读图，不查表）
            ready.sort(key=lambda p: (sum(_abs(m) for m in p["inputs"]),
                                      sum(_evidence_rank(kg, m, inv)
                                          for m in p["inputs"]),
                                      len(p["inputs"])))
            return _step(cur, ready[0], chain)
        srcs = drop_sources(kg, cur)
        # 只有"执行层真能采"的来源才当采集目标（_gather_rank：边→可采资源分类）
        if srcs and _gather_rank(kg, cur) == 0:
            # 多个来源方块并列（iron_ore / deepslate_iron_ore）：先避开"此地
            # 反复找不到"的（证据降级，不是禁令），再要分类内的，再优先手上
            # 工具就能采的，最后**优先她最近看到过的那种**——第四判据读的是
            # 物种节点的激活值（感知层每拍点亮她眼前的方块），于是"眼前有
            # 金合欢原木"会自然压过"图上并列但没见过的樱花原木"。
            # 四个判据全部读图/读账，代码里没有一种木材的名字。
            def _blk_key(b):
                bn = str(b).split(":", 1)[-1]
                gatherable = 0 if _gatherable_block(kg, bn) else 1
                tl = tool_required(kg, bn)
                held = 0 if (not tl or any(int(inv.get(x, 0) or 0) > 0
                                           for x in tl)) else 1
                try:
                    _n = kg.get_node(bn)
                except Exception:
                    _n = None
                seen = -float(getattr(_n, "activation", 0.0) or 0.0)
                return (_abs(bn), gatherable, held, seen)
            blk = min(srcs, key=_blk_key)
            bare = str(blk).split(":", 1)[-1]
            tools = tool_required(kg, bare)
            held = [x for x in tools if int(inv.get(x, 0) or 0) > 0]
            if not tools or held:
                return {"done": False, "action": "gather", "item": cur,
                        "block": bare, "recipe": "",
                        "blocks": _leaf_siblings(kg, chain, bare),
                        "why": f"{cur} 可由方块 {bare} 产出"
                               f"{'（工具已备）' if held else '（无需工具）'}",
                        "chain": chain + [bare]}
            # 工具不在手：先造/先取工具（工具自身也是图上的物品，继续跳链）。
            # harvest_tools 是"任一即可"的集合（木镐能挖深slate圆石，石镐也能），
            # 所以挑证据最便宜的那件；已经在链上的跳过——不是成环，是换一件。
            # （26.1 实测：cobbled_deepslate 的工具集含 stone_pickaxe，而它正是
            #  我们爬下来的原因，直接选中就自锁，链条当场退回"定向探索"。）
            fresh = [x for x in tools if x not in seen]
            if not fresh:
                return {"done": False, "action": "explore", "item": tools[0],
                        "block": "", "recipe": "",
                        "why": f"采 {bare} 需 {tools[0]}，链条成环",
                        "chain": chain + list(tools[:1])}
            need = min(fresh, key=lambda x: _chain_cost(kg, x, inv,
                                                        seen=seen))
            if need in seen:
                return {"done": False, "action": "explore", "item": need,
                        "block": "", "recipe": "",
                        "why": f"采 {bare} 需 {need}，链条成环",
                        "chain": chain + [need]}
            seen.add(need)
            chain.append(need)
            cur = need
            continue
        if prods:
            # 有配方但缺料：不固定取 prods[0]——按"这条配方的缺口整体有多可
            # 信"排序后取最优，再在其中挑证据最强的缺口继续爬。26.1 运行时给
            # iron_ingot 两条合料配方（9 铁粒 / 1 铁块），图上都没有来源；人工
            # 先验那条（raw_iron←铁矿）有——于是自然走熔炼支路，全程零物品名。
            def _recipe_cost(p):
                miss = _missing(p["inputs"], inv)
                # 首位是"这条配方的缺料里有几种是此地反复找不到的"：并列配方
                # （26.1 实测 stick 每种 *_planks 各一条、planks 每条 *_log
                #  各一条）于是自然从摘不到的那种换到别的种——原来只按路径
                # 代价排，代价相等时按图上边序取第一个，真机因此死盯
                # cherry_log 一小时（20 次 not_found_in_radius 也不换种）。
                # 第二位读**全链代价**（背包感知 + 成环罚）：手上有原木时，
                # 那条木板的链价是 1，去野外采另一种原木的链价更高——
                # "先用上手里的"由代价差自然涌现，不写物种名。
                # 第三位读感知激活：同价时先要她眼前出现过的物种。
                return (sum(_abs(m) for m in miss),
                        sum(_chain_cost(kg, m, inv, seen=seen)
                            for m in miss),
                        -sum(_vis(m) for m in miss), len(miss))
            best = min(prods, key=_recipe_cost)
            miss = _missing(best["inputs"], inv)
            # 先在本条配方的缺料里挑没走过、且图上还有来源的；都不满足时换一条
            # 配方的缺料再看——"这条配方走不通"不等于"目标不可达"，直接判环会
            # 让爬链停死在第一个死结上（真机实测踩过：cobbled_deepslate 的工具
            # 集含刚爬过来的 stone_pickaxe，选中即自锁退回探索）。
            fresh = [m for m in miss if m not in seen
                     and _evidence_rank(kg, m, inv) < 3]
            if not fresh:
                alt = [p for p in prods if p is not best]
                cand = []
                for p in alt:
                    cand += [m for m in _missing(p["inputs"], inv)
                             if m not in seen and _evidence_rank(kg, m, inv) < 3]
                fresh = sorted(cand, key=lambda m: (
                    _abs(m), _chain_cost(kg, m, inv, seen=seen)))
            if not fresh:
                sub = miss[0] if miss else cur
                return {"done": False, "action": "explore", "item": sub,
                        "block": "", "recipe": "",
                        "why": f"缺 {sub}，图上无可走的下一步：定向探索",
                        "chain": chain + ([sub] if sub != cur else [])}
            sub = min(fresh, key=lambda m: (
                _abs(m), _chain_cost(kg, m, inv, seen=seen)))
            seen.add(sub)
            chain.append(sub)
            cur = sub
            continue
        return {"done": False, "action": "explore", "item": cur, "block": "",
                "recipe": "", "why": f"{cur} 图上无产出路径：定向探索",
                "chain": chain}
    return {"done": False, "action": "explore", "item": cur, "block": "",
            "recipe": "", "why": "跳链超限：定向探索", "chain": chain}
