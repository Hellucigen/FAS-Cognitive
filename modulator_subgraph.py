# modulator_subgraph.py — R2 P6：把调制器接成图谱公民（边是系数的真源）
# ============================================================================
# 四件事（P6 建 1)2)3)，P9 加 2b)），都不碰受保护核心
# （KnowledgeGraph / DiffusionEngine / 扩散数学）：
#
#   1) ensure_modulator_subgraph()  幂等 bootstrap：12 个调制器镜像点（P4 已建）
#      + 14 个 `调制目标:*` 基础设施点 + config.modulator_system.edges 的边 +
#      （开关）Self-[处于]->调制器 锚定边。**表在 config，种入后图是权威**
#      ——改边=改效力（与 capability_graph / ensure_drive_signal_edges 同构）。
#
#   2) project_coefficients()  投影器：把 `调制器 -[调制/增强/抑制]-> 调制目标:X`
#      的边编译成 ModulationLayer 的 effects 行 `(hormone.<名> → coef)`。
#      coef = sign(权重) × |权重| × 调制器 sensitivity。符号一律以**权重**为准
#      （扩散用的就是它），关系词与权重符号不一致时报告为 sign_mismatch。
#      没有 if 激素>0.6，没有 per-hormone 代码：闭合的只有关系词表 4 个词，
#      拓扑与强度都是图上的数据（禁令 2）。删边 ⇒ 系数被回收（不是留在层里）。
#
#   2b) graph_biases() / apply_need_drift()（R2 P9）  同一批边里**端点不是参数
#      靶点**的那些：`调制器→网络/驱动/需求` 编译成连续偏置（Σ w×dev，钳 ±0.5），
#      `需求→调制器` 编译成慢漂移（经唯一写入口）。仍然没有 per-hormone 分支：
#      新增一条耦合 = config 的 edges 加一行。
#
#   3) 图通道（phasic→激活）在 internal_state.pulse() 里，因为写者必须仍是
#      InternalState（I1 单一写者）；本模块只提供量纲常量与说明。
#      **脉冲是稀疏事件**：每拍补激活＝外置能量泵，正是 09-13 饱和事故
#      （docs 记忆 diffusion-energy-conservation）的形状。
#
# 语义边界：本模块只写"系数与边"。调制器**不**决定回答文本、不调 LLM、
# 不执行动作（禁令 4）；那些通道读的是被投影之后的认知场参数。
# ============================================================================
"""神经调制子图的 bootstrap 与投影（R2 P6）。"""

import logging

logger = logging.getLogger(__name__)

MS = "modulator_system"
TARGET_PREFIX = "调制目标:"
# 投影只认这三条有向关系；`交互` 是调制器之间的对称耦合，只走图通道，
# 不产生参数系数（系数需要明确的"目标参数"）。
PROJECTED_RELATIONS = ("调制", "增强", "抑制")
EDGE_RELATION_CATEGORY = "causal_relation"


def _cfg_block(config) -> dict:
    return ((config or {}).get(MS) or {})


def _resolve_node(name: str, mirror: dict) -> str:
    """规格名（dopamine）→ 图节点 id（多巴胺样）；已经是节点 id 的原样返回。"""
    return mirror.get(name, name)


# 偏置量程帽（R2 P9）：Σ w×dev 的**稳态位移**上限（绝对值）。
# 为什么要有：网络/驱动/需求的字段值域是 [0,1]，加性偏置理论上能被多条边
# 累加推过头（拓扑被人改宽后尤其）；封顶后"调制器钉在高位"最多把落点推
# 半个量程，永远不会独占它。P9 闸门测的就是这个界。
# （出厂值声明在 config.modulator_system.bias_cap，这里是**兜底**：没接线时也要有界。）
BIAS_CAP = 0.5
# 心情包络的幅度帽（R2 P11）：`Σ(边权 × dev)` 的绝对值上限。与 `bias_cap` 是两个
# 不同的界（一个是场稳态位移、一个是 0~1 情绪量表上的一段），所以不共用一个数：
# 0.35 的理由（"最多相当于两次强交互事件"）写在 config.modulator_system.mood 的
# 注释里，那里是出厂真源，此处只是没接线时的兜底。
MOOD_CAP = 0.35
# 哪些图节点可以充当"状态→调制器"这一侧的显著性来源（按 extra_attrs.type 判定，
# 见 §4 的 SALIENCE_READERS——类型与读法一一对应，新增一类只在那里加条目）：
#   need         = InternalState 镜像（安全/探索/掌控/社交需求）
#   mc_state     = 具身状态节点（`生存需求`：embodied_mapper 由 hp/饥饿/威胁写出）
#   time_of_day  = 时段桶（凌晨…深夜，R2 P10）
#   day_phase    = 昼夜相位（白天/夜间，R2 P10）
NEED_SOURCE_TYPES = ("need", "mc_state")
CIRCADIAN_SOURCE_TYPES = ("time_of_day", "day_phase")
# 需求→调制器慢写入的默认速率（每模拟分钟）：出厂值下 w=0.2、显著性=1
# 的需求满缺口约 +0.02×60×0.2 = **0.24/小时**。选这个尺度的理由：调制器自身
# 每分钟都在向基线回归（dopamine `decay_per_min=0.02`），漂移必须比它慢，
# 否则"一次没做成的事"就能把通道钉死——**缺口要持续**才留得住痕迹，
# 瞬时事件该走 pulse()（快通道），而不是这里。
# （出厂值声明在 config.modulator_system.need_drift_rate_per_min，此处兜底。）
NEED_DRIFT_RATE_PER_MIN = 0.02
# 时段/昼夜→调制器慢写入的默认速率（R2 P10）。比需求漂移快一档是故意的：
# 昼夜是**小时级**持续事实，要在一个夜间窗口里真的把褪黑素样抬到能改变
# 参数曲线的位置（它的 decay 是 0.035/min，抬幅 = rate×w×强度/decay）；
# 而需求缺口是分钟级波动的东西，抬太快就变成"一次没做成的事钉死通道"。
# 两个数不同不是不一致，是两个时间尺度（§27）。
# （出厂值声明在 config.modulator_system.circadian_drift_rate_per_min，此处兜底。）
CIRCADIAN_DRIFT_RATE_PER_MIN = 0.06


# ── 1) bootstrap ───────────────────────────────────────────

def ensure_modulator_subgraph(kg, st, config) -> dict:
    """幂等种入调制子图。返回 {targets, edges, edges_skipped, anchors, sign_mismatch}。

    端点缺失的边**跳过**（不为此造点：那会把语义空洞带进图里）。
    """
    blk = _cfg_block(config)
    if not blk.get("subgraph", True) or kg is None:
        return {"skipped": "subgraph 关闭或无图谱", "targets": 0, "edges": 0,
                "edges_skipped": 0, "anchors": 0, "sign_mismatch": []}
    from graph_model import Edge, Node

    mirror = st.mirror_modulator
    targets = blk.get("targets") or {}
    created_targets = 0
    for short, param in targets.items():
        nid = f"{TARGET_PREFIX}{short}"
        if kg.get_node(nid) is None:
            kg.add_node(Node(
                id=nid, weight=0.4, label="infrastructure", graph_space="cognitive",
                extra_attrs={"type": "modulator_target", "param": param,
                             "canon": "modulator_subgraph",
                             "description": f"认知场参数 {param} 的调制靶点（系数来自图上的调制边）"}))
            created_targets += 1
        else:
            # 就地对齐 param（参数键改名后投影仍跟得上），不重建节点
            node = kg.get_node(nid)
            ea = node.extra_attrs if node.extra_attrs is not None else {}
            if ea.get("param") != param:
                ea["param"] = param
                node.extra_attrs = ea

    created, skipped, mismatch = 0, [], []
    mood_seeded = 0
    mood_nid = str((blk.get("mood") or {}).get("node") or "").strip()
    if mood_nid and kg.get_node(mood_nid) is None:
        # 心情的**数值不在图上**（在 PersonalityBaseline 里，混合原则）：这个节点
        # 存在的理由是①`调制器→心情` 那些边要有端点、②"心情"因此在 self 图上可达
        # （调制器脉冲沿边点亮它，与 P6 给 12 个调制器用的同一条证据）。
        # 刻意**不建出边**：出边为 0 就是写在图上的单向性（P11 闸门断言它）。
        kg.add_node(Node(id=mood_nid, weight=0.4, label="declarative-semantic",
                         graph_space="self",
                         extra_attrs={"type": "mood_state", "canon": "modulator_subgraph",
                                      "numeric_owner": "personality_baseline",
                                      "description": "心情底色（派生读数：事件余韵 + 调制器包络；"
                                                     "数值在 PersonalityBaseline，边只定义权重）"}))
        mood_seeded = 1
    for row in blk.get("edges") or []:
        try:
            src, rel, dst, weight = row[0], row[1], row[2], float(row[3])
        except (ValueError, IndexError, TypeError):
            skipped.append({"row": str(row)[:80], "why": "行格式不合（需 [src,rel,dst,w]"
                                                                      "[$,why]）"})
            continue
        s, d = _resolve_node(src, mirror), _resolve_node(dst, mirror)
        if kg.get_node(s) is None or kg.get_node(d) is None:
            skipped.append({"edge": f"{s}-{rel}->{d}", "why": "端点不存在"})
            continue
        # 符号约定：增强=正、抑制=负；调制=符号写在权重上。不一致直接报告，
        # 因为扩散看的是权重，投影也以权重为准——关系词写错会误导读者。
        if rel == "增强" and weight <= 0 or rel == "抑制" and weight >= 0:
            mismatch.append({"edge": f"{s}-{rel}->{d}", "weight": weight,
                             "why": "关系词与权重符号矛盾（以权重为准）"})
        if kg.get_edge(s, d, rel) is None:
            kg.add_edge(Edge(src=s, dst=d, relation=rel, weight=weight,
                             relation_category=EDGE_RELATION_CATEGORY))
            created += 1
        else:
            # 已存在：不改既有权重（图是权威，可能已被人/实验改过），只对齐符号报告
            pass

    anchors = 0
    if blk.get("link_to_self", False):
        rel = str(blk.get("link_relation") or "处于")
        w = float(blk.get("link_weight") or 0.2)
        for nid in mirror.values():
            if kg.get_node(nid) is None:
                continue
            if kg.get_edge("Self", nid, rel) is None:
                kg.add_edge(Edge(src="Self", dst=nid, relation=rel, weight=w,
                                 relation_category="cognitive_relation"))
                anchors += 1

    out = {"targets": created_targets, "edges": created,
           "edges_skipped": len(skipped), "skipped": skipped[:8],
           "anchors": anchors, "mood_node_seeded": mood_seeded,
           "sign_mismatch": mismatch}
    if created or created_targets or anchors:
        logger.info(f"[Modulation] 子图种入：目标节点 +{created_targets}，"
                    f"边 +{created}，Self 锚定 +{anchors}，跳过 {len(skipped)}")
    if mismatch:
        logger.warning(f"[Modulation] 符号约定告警 ×{len(mismatch)}（关系词与权重"
                       f"不一致，投影以权重为准）")
    return out


# ── 2) 投影器 ──────────────────────────────────────────────

def projection(kg, st, config) -> dict:
    """从图上的调制边算出 `param → {signal_key: coef}`（纯计算，不写任何东西）。

    返回 {"rows": {param: {"hormone.dopamine": 0.80, ...}},
          "edges_read": n, "orphans": [...], "conflicts": [...],
          "no_positive_out": [...]}
    `no_positive_out` 是给用户裁定拓扑用的：纯负出边的节点在扩散里永不发射
    （实测 `if total_w > 0`），等于图上不存在——它的抑制边也是死边。
    """
    blk = _cfg_block(config)
    mirror = st.mirror_modulator
    ids = set(mirror.values())
    rows: dict = {}
    per_node_pos: dict = {i: 0.0 for i in ids}
    edges_read = 0
    orphans = []
    conflicts = []
    for nid in sorted(ids):
        for e in kg.get_out_edges(nid):
            dst = kg.get_node(e.dst)
            if dst is None:
                continue
            if e.weight > 0:
                per_node_pos[nid] += e.weight
            da = dst.extra_attrs or {}
            if da.get("type") != "modulator_target":
                continue                      # 图通道边（CEN/行为/Drive/需求），不产系数
            if e.relation not in PROJECTED_RELATIONS:
                continue                        # 交互等对称耦合：只走图通道
            param = da.get("param")
            if not param:
                orphans.append({"edge": f"{nid}-{e.relation}->{e.dst}",
                                "why": "靶点节点没有 param 属性"})
                continue
            name = next((k for k, v in mirror.items() if v == nid), None)
            if name is None:
                continue
            sens = float(st.modulator_field(name, "sensitivity", 1.0) or 1.0)
            coef = float(e.weight) * sens       # 符号以权重为准
            key = f"hormone.{name}"
            prev = rows.setdefault(param, {}).get(key)
            if prev is not None:
                # 同一调制器→同一参数有两条边：effects 表按信号键索引，装不下两行。
                # 静默覆盖会让"哪条边生效"取决于遍历顺序——保留更强的那条并报告，
                # 让拓扑错误在测试/日志里现形（而不是变成一个说不清的系数）。
                conflicts.append({"param": param, "signal": key,
                                  "kept": max(prev, coef, key=abs),
                                  "dropped": min(prev, coef, key=abs),
                                  "why": "同一调制器对同一参数有两条边（保留 |系数| 大的）"})
                if abs(coef) <= abs(prev):
                    continue
            rows[param][key] = round(coef, 6)
            edges_read += 1
    no_pos = sorted(n for n, s in per_node_pos.items() if s <= 0.0)
    return {"rows": rows, "edges_read": edges_read, "orphans": orphans,
            "conflicts": conflicts,
            "no_positive_out": no_pos, "enabled": bool(blk.get("subgraph", True))}


def project_coefficients(kg, st, modulation, config) -> dict:
    """把 projection() 的结果写进 ModulationLayer，并回收图上已不存在的激素系数。

    回收这一步是"图是真源"的硬要求：删掉一条边，效力必须跟着消失，
    否则层里的旧系数会变成看不见的第二真源。
    """
    proj = projection(kg, st, config)
    if not proj["enabled"] or modulation is None:
        return {"applied": 0, "removed": 0, "projection": proj, "skipped": True}
    rows = proj["rows"]
    applied = 0
    # ① 先删掉所有"激素来源"的旧行（含 config 出厂那 7 行），再按图重建
    removed = 0
    for param in list(rows.keys()) + _hormone_params(modulation):
        for key in list(modulation.effects(param).keys()):
            if not key.startswith("hormone."):
                continue
            if param in rows and key in rows[param]:
                continue                        # 下面覆盖它
            if modulation.drop_effect(param, key):
                removed += 1
    # ② 写图上的系数
    for param, effs in rows.items():
        if not modulation.param_defined(param):
            continue                            # 参数不存在于该 config：不硬造
        for key, coef in effs.items():
            if modulation.set_effect(param, key, coef):
                applied += 1
    out = {"applied": applied, "removed": removed, "projection": proj,
           "skipped": False}
    logger.debug(f"[Modulation] 投影：{proj['edges_read']} 条边 → {applied} 行系数"
                 f"（回收 {removed} 行）")
    return out


def _hormone_params(modulation) -> list:
    """当前带 hormone.* 行的所有参数（回收时要覆盖到图上已删的边）。"""
    found = []
    for name in list(getattr(modulation, "_specs", {}) or {}):
        if any(k.startswith("hormone.") for k in modulation.effects(name)):
            found.append(name)
    return found


# ── 4) 图上的其余耦合：调制器→动力学场 的连续偏置 + 状态→调制器 的慢漂移 ──
#
# R2 P9 起步、P10 补全。投影器（§2）只管 `调制目标:*` 那 30 条**参数**边；同一批
# edges 表里还有三类端点不是参数靶点的边：
#
#   调制器 -[w]-> CENetwork / DMNetwork / *Drive   →  场稳态的加性偏置
#   需求   -[w]-> 调制器                            →  通道张力性浓度的慢漂移
#   时段/昼夜 -[w]-> 调制器                          →  同一条漂移通道（P10）
#   调制器 -[w]-> 需求                              →  需求靶值的连续偏置（稳态回路）
#
# 三件事共用一个算法（Σ 权重 × dev(源) 或 Σ 权重 × 显著性(源)），因此
# **没有按调制器名分支**（禁令 2）：新增一条耦合 = 在 config 的 edges 里加一行。

def _need_name_map(st) -> dict:
    """需求镜像节点 id → internal_state 需求名。"""
    return {v: k for k, v in (getattr(st, "MIRROR_NEED", {}) or {}).items()}


def need_salience_of(kg, st, node_id: str, cache: dict = None) -> float:
    """一个**需求节点**此刻的显著性（0~1），图上边权的乘数。

    两条来源，按可靠性排序（都是读既有事实，不新建状态）：
      ① internal_state 的需求镜像（安全/探索/掌控/社交需求）→ `need_salience()`；
      ② 具身状态节点（如 `生存需求`：embodied_mapper 写 hp/饥饿/威胁算出的
         压力，量程 0~5）→ `pressure/5`，缺 pressure 时退 `activation/5`。
    ②存在是因为有些需求只有世界侧的读数、没有内部需求维度；不为此在
    internal_state 里造平行需求（禁令 11）。两者都没有 ⇒ 0（没有信号就没有观点）。
    """
    if cache is not None and node_id in cache:
        return cache[node_id]
    v = 0.0
    name = _need_name_map(st).get(node_id)
    if name is not None:
        try:
            v = float(st.need_salience(name) or 0.0)
        except Exception:
            v = 0.0
    else:
        node = kg.get_node(node_id) if kg is not None else None
        if node is not None:
            ea = node.extra_attrs or {}
            p = ea.get("pressure")
            raw = float(p) if isinstance(p, (int, float)) else float(
                node.activation or 0.0)
            v = max(0.0, min(1.0, raw / 5.0))
    v = round(v, 4)
    if cache is not None:
        cache[node_id] = v
    return v


def _circadian_salience(kg, st, node_id: str, cache: dict = None) -> float:
    """一个**时段/昼夜节点**此刻的显著性（0~1）= `circadian_strength`。

    R2 P10 的关键取舍：这里**不读 activation**。activation 是扩散用的货币
    （而且会因对话/空闲起伏），拿它当调制驱动 = ① 借了别的系统的钱（P6 的
    同一纪律）② 让"昼夜曲线"变成"今天聊了多少次夜晚"。strength 只有一个作者：
    `temporal_awareness.update_clock_state`，它按时段写、别的都不写 ⇒
    同一 bucket 序列必然给出同一曲线（P10 闸门就是这条）。
    键不在位（时钟还没跑过 / 旧图没播种）⇒ 0：没有事实就没有驱动，
    不拿默认值冒充昼夜。
    """
    if cache is not None and node_id in cache:
        return cache[node_id]
    v = 0.0
    node = kg.get_node(node_id) if kg is not None else None
    if node is not None:
        raw = (node.extra_attrs or {}).get("circadian_strength")
        if isinstance(raw, (int, float)):
            v = max(0.0, min(1.0, float(raw)))
    v = round(v, 4)
    if cache is not None:
        cache[node_id] = v
    return v


# 状态源类型（节点 `extra_attrs.type`）→ 显著性读法。**这一张表就是"什么算一个
# 驱动性状态事实"的完整定义**：新增一类（比如 MC 的昼夜、饥饿读数）= 加一个类型
# 条目 + config 里加边，漂移的钳位/留痕/回滚一律复用。
SALIENCE_READERS = {
    "need": need_salience_of,
    "mc_state": need_salience_of,
    "time_of_day": _circadian_salience,
    "day_phase": _circadian_salience,
}


def graph_biases(kg, st, config, node_names: dict = None) -> dict:
    """把 `调制器 -[w]-> 网络/驱动` 与 `调制器 -[w]-> 需求` 的边编译成偏置。

    `node_names`：调用方（CognitiveField）给出的 `{"network": {图节点id: 字段名},
    "drive": {...}}`——落点名册由**拥有这些字段的对象**提供，本模块不另立一份
    （否则 config 改网络名就会有两处要同步）。需求一侧的名册来自 InternalState
    自己的 MIRROR_NEED，不需要调用方给。

    返回 {"network": {...}, "drive": {...}, "need": {...},
          "edges_used": n, "capped": [...], "skipped": bool}
    偏置 = Σ(权重 × dev(调制器))，钳在 ±`bias_cap`（config 声明，`BIAS_CAP` 兜底）。
    dev 已经过受体曲线（`modulator_dev`），所以**过载反转在这条通道上同样生效**：
    浓度钉在 max 附近时偏置会往回拉，而不是继续推高。
    """
    blk = _cfg_block(config)
    cap = float(blk.get("bias_cap", BIAS_CAP) or BIAS_CAP)
    out = {"network": {}, "drive": {}, "need": {}, "edges_used": 0,
           "capped": [], "skipped": False}
    if kg is None or st is None or not blk.get("subgraph", True):
        out["skipped"] = True
        return out
    idx = {}
    for kind, m in (node_names or {}).items():
        for nid, fname in (m or {}).items():
            idx[nid] = (kind, fname)
    for nid, name in _need_name_map(st).items():
        idx.setdefault(nid, ("need", name))
    mirror = st.mirror_modulator
    spec_of_node = {v: k for k, v in mirror.items()}
    for nid in sorted(spec_of_node):
        spec = spec_of_node[nid]
        dev = float(st.modulator_dev(spec) or 0.0)
        if abs(dev) < 1e-9:
            continue                       # 基线上 = 无调制，不产偏置
        for e in kg.get_out_edges(nid):
            tgt = idx.get(e.dst)
            if tgt is None or e.relation not in PROJECTED_RELATIONS:
                continue                   # 参数靶点（投影器管）/ 行为概念（图通道）
            kind, fname = tgt
            # 三种落点用的是同一个乘数 dev(调制器)：需求一侧的显著性在这里是
            # **输出**（偏置改掉靶值，靶值再改掉需求水位），不是输入。
            contrib = float(e.weight) * dev
            cur = out[kind].get(fname, 0.0) + contrib
            if cur > cap or cur < -cap:
                out["capped"].append({"kind": kind, "name": fname,
                                      "raw": round(cur, 4),
                                      "kept": round(max(-cap, min(
                                          cap, cur)), 4)})
                cur = max(-cap, min(cap, cur))
            out[kind][fname] = round(cur, 4)
            out["edges_used"] += 1
    out["network"] = {k: v for k, v in out["network"].items() if abs(v) > 1e-9}
    out["drive"] = {k: v for k, v in out["drive"].items() if abs(v) > 1e-9}
    out["need"] = {k: v for k, v in out["need"].items() if abs(v) > 1e-9}
    return out


def mood_projection(kg, st, config) -> dict:
    """把 `调制器 -[w]-> 心情` 的边编译成心情的**包络项**（只读，不写任何东西）。

     term = clamp( Σ(边权 × dev(调制器)), ±mood.cap )，dev 与参数通道、稳态偏置
    用的是**同一个受体响应量**（`st.modulator_dev`），所以：
      · 过载反转自动生效（皮质醇钉在 max 时对心情的下拉往回推，不再加深）；
      · 用的是 tonic 侧的浓度响应，**phasic 脉冲不进包络**（快信号该改变的是
        "刚刚发生了什么"，那是事件余韵的活，P11 闸门按这条断言）；
      · 没有 `if 名字 == "serotonin"`（禁令 2）：新增一条耦合 = config 里加一行边。

    返回 {"term", "raw", "cap", "edges_used", "rows", "skipped", "no_node"}。
    心情的**数值真源在 PersonalityBaseline**（这里只给"包络"这一项）；本函数
    不写图、不写状态，因此可以被任意次重放而不改变任何指纹（单向性的结构证明）。
    """
    blk = _cfg_block(config)
    mcfg = blk.get("mood") or {}
    nid = str(mcfg.get("node") or "").strip()
    cap = float(mcfg.get("cap", MOOD_CAP) or MOOD_CAP)
    out = {"term": 0.0, "raw": 0.0, "cap": cap, "edges_used": 0, "rows": [],
           "skipped": False, "no_node": False}
    if kg is None or st is None or not nid or not blk.get("subgraph", True):
        out["skipped"] = True
        return out
    if kg.get_node(nid) is None:
        out["no_node"] = True
        return out
    mirror = st.mirror_modulator
    spec_of_node = {v: k for k, v in mirror.items()}
    total = 0.0
    for src in sorted(spec_of_node):
        spec = spec_of_node[src]
        dev = float(st.modulator_dev(spec) or 0.0)
        if abs(dev) < 1e-9:
            continue                       # 在基线上 = 不贡献包络
        for e in kg.get_in_edges(nid):
            if e.src != src or e.relation not in PROJECTED_RELATIONS:
                continue
            contrib = float(e.weight) * dev
            total += contrib
            out["rows"].append({"src": spec, "edge": f"{src}-{e.relation}->{nid}",
                                "weight": round(float(e.weight), 3),
                                "dev": round(dev, 4),
                                "contrib": round(contrib, 4)})
            out["edges_used"] += 1
    out["raw"] = round(total, 4)
    clamped = max(-cap, min(cap, total))
    # `capped` 用**未取整**的值判：先 round 再比会把 1e-5 的取整差当成"钳过"。
    out["capped"] = abs(clamped - total) > 1e-9
    out["term"] = round(clamped, 4)
    return out


def _apply_state_drift(kg, st, config, source_types, rate: float,
                       dt_min: float = None, cycle_id: str = None,
                       reason_prefix: str = "状态漂移",
                       source: str = "state") -> dict:
    """**唯一**的"状态事实→调制器慢漂移"实现：把图上 `源 -[w]-> 调制器` 的边写成
    每拍一步的 tonic 变化。需求侧（P9）与昼夜侧（P10）都走这里，区别只是
    `source_types`（哪些节点类型算这一类的源）与速率——不是两段代码。

    `delta = rate_per_min × dt_min × w × 显著性(源)`，经 InternalState 唯一写入口
    （`apply_delta` ⇒ 受上升夹速、进历史、动的是 **tonic**）。负权边自然是下降。
    遍历方向 = **调制器节点的入边**，判据是节点的 `extra_attrs.type`（查
    `SALIENCE_READERS`），不是"名字以需求结尾"那种字符串猜测——后者才是闭合词表。
    只有**确实连进调制器**的源节点才会被乘进漂移，所以放宽类型集合
    不会让无关节点渗进通道。

    返回 {"written": n, "skipped": [...], "rate_per_min": r, "dt_min": dt}。
    没有 kg / 子图关闭 / 速率 0 ⇒ 什么都不做（不新建平行状态，也不静默造默认边）。
    """
    blk = _cfg_block(config)
    dt = 0.0 if dt_min is None else max(0.0, min(5.0, float(dt_min)))
    res = {"written": 0, "skipped": [], "rate_per_min": rate, "dt_min": dt}
    if kg is None or st is None or not blk.get("subgraph", True) or rate <= 0:
        return res
    mirror = st.mirror_modulator
    sal: dict = {}
    for spec, nid in mirror.items():
        for e in kg.get_in_edges(nid):
            if e.relation not in PROJECTED_RELATIONS:
                continue
            src = kg.get_node(e.src)
            if src is None:
                continue
            stype = str((src.extra_attrs or {}).get("type") or "")
            if stype not in source_types:
                continue
            reader = SALIENCE_READERS.get(stype)
            if reader is None:            # 类型在集合里但没注册读法 = 配置错，如实报
                res["skipped"].append({"edge": f"{e.src}->{nid}",
                                       "why": f"type={stype} 无显著性读法"})
                continue
            s = reader(kg, st, e.src, cache=sal)
            delta = rate * dt * float(e.weight) * s
            if abs(delta) < 1e-6:
                continue
            r = st.apply_delta(
                "modulator", spec, delta,
                reason=(f"{reason_prefix}: {e.src} 显著性 {s:.2f} ×"
                        f"{float(e.weight):+.2f}（{e.relation}）"),
                cycle_id=cycle_id, source=source)
            if r.get("ok"):
                res["written"] += 1
            else:
                res["skipped"].append({"edge": f"{e.src}->{nid}",
                                       "why": r.get("error", "?")})
    return res


def apply_need_drift(kg, st, config, dt_min: float = None,
                     cycle_id: str = None) -> dict:
    """需求→调制器（R2 P9）：缺口按边权慢慢抬/压通道。见 `_apply_state_drift`。"""
    blk = _cfg_block(config)
    rate = float(blk.get("need_drift_rate_per_min", NEED_DRIFT_RATE_PER_MIN))
    return _apply_state_drift(kg, st, config, NEED_SOURCE_TYPES, rate,
                              dt_min=dt_min, cycle_id=cycle_id,
                              reason_prefix="需求漂移", source="needs")


def apply_circadian_drift(kg, st, config, dt_min: float = None,
                          cycle_id: str = None) -> dict:
    """时段/昼夜→调制器（R2 P10）：褪黑素样/组胺样的**日内水位**由图上事实驱动。

    与需求漂移同一条实现，只有三点不同：源类型是 `time_of_day/day_phase`、
    显著性读的是 `circadian_strength`（时钟写的"此刻在势"，不借 activation），
    速率单列一档（`circadian_drift_rate_per_min`）。
    为什么单列速率：昼夜是**小时级**的慢变量，出厂幅度要让褪黑素样在夜里
    抬到饱和拐点附近、白天压回量程低段（不是把量程钉满——钉满就没有信息了），
    这个尺度与"需求缺口"不是一回事，写成一个数就会两头都不对。
    """
    blk = _cfg_block(config)
    rate = float(blk.get("circadian_drift_rate_per_min",
                         CIRCADIAN_DRIFT_RATE_PER_MIN))
    return _apply_state_drift(kg, st, config, CIRCADIAN_SOURCE_TYPES, rate,
                              dt_min=dt_min, cycle_id=cycle_id,
                              reason_prefix="昼夜漂移", source="circadian")


# ── 5) 观测 ────────────────────────────────────────────────

def mood_facts(kg, st, config) -> dict:
    """调试/实验视图（§26）：心情这一格现在由哪些边、乘哪些 dev 得到什么值。
    纯读（内部只调 `mood_projection`），不写图也不写状态。"""
    blk = _cfg_block(config)
    nid = str(((blk.get("mood") or {}).get("node") or "")).strip()
    node = kg.get_node(nid) if (kg is not None and nid) else None
    proj = mood_projection(kg, st, config)
    return {"node": nid, "exists": node is not None,
            "out_edges": len(kg.get_out_edges(nid)) if node is not None else None,
            "in_edges": len(kg.get_in_edges(nid)) if node is not None else None,
            "projection": proj}


def explain_subgraph(kg, st, config) -> dict:
    """人读版拓扑：每个调制器的出边、系数、是否纯负边（永不发射）。"""
    mirror = st.mirror_modulator
    out = {}
    for name, nid in mirror.items():
        edges = []
        for e in sorted(kg.get_out_edges(nid), key=lambda x: (-abs(x.weight), x.dst)):
            edges.append({"dst": e.dst, "relation": e.relation,
                          "weight": round(float(e.weight), 4),
                          "coef": round(float(e.weight) *
                                         float(st.modulator_field(
                                             name, "sensitivity", 1.0) or 1.0), 4),
                          "is_param_target": (
                              (kg.get_node(e.dst).extra_attrs or {}).get("type")
                              == "modulator_target" if kg.get_node(e.dst) else False)})
        out[name] = {"node": nid, "out_edges": len(edges),
                     "positive_out": sum(1 for x in edges if x["weight"] > 0),
                     "in_edges": len(kg.get_in_edges(nid)),
                     "level": round(st.modulator_level(name), 4),
                     "conc": round(st.modulator_conc(name), 4),
                     "edges": edges}
    return out
