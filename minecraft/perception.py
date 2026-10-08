# minecraft.perception.py — Minecraft 原子感知（Phase B 规范四）
# ============================================================================
# 取代 Phase A 的单 blob 节点：把游戏状态拆成固定身份的动态状态节点
# （in-place 更新，可独立激活/参与扩散，不产生垃圾节点）。
#
# 结构（架构对齐 2026-09-19：Haru 是主体不是 Hub）：
#
#   Haru -[当前状态]-> 当前Minecraft状态 -[状态项]-> 7 个固定槽位
#
#   槽位（cognitive 空间，type=dynamic_state，值存 extra_attrs["value"]，
#   in-place 覆盖，不产生历史垃圾）：
#     Haru的位置 / Haru的血量 / Haru的饥饿 / Haru的手持物 /
#     附近的玩家 / 附近的方块 / 附近的生物
#
# 世界实体（玩家/方块/生物）不再与 Haru 直接建边——"看见"不是"关于自我的
# 事实"。它们的认知入口是：
#   1. Unknown* 节点本体（type=unknown，好奇系统按 id 前缀/type 消费）
#   2. 在视注意力地板（每 tick 抬升激活 + mark_active）——看不见就自然衰减
#   3. 槽位值字符串（附近的方块/生物/玩家的 value）
# 旧版 `Unknown* -[靠近]-> Haru` 边已废弃：它把空间邻近错标成 social_relation，
# 并让 Haru 随方块种类无限增边、成为全图扩散高速公路。
# ============================================================================

import logging

from graph_model import Node, Edge, now_str, is_live_unknown

logger = logging.getLogger(__name__)

DYNAMIC_NODES = ["Haru的位置", "Haru的血量", "Haru的饥饿", "Haru的手持物", "附近的玩家", "附近的方块", "附近的生物"]

# 状态中间节点：Haru 的具身上下文（当前状态），世界信息经由它进入认知。
STATE_HUB_ID = "当前Minecraft状态"


def _ensure_state_hub(kg):
    """确保 当前Minecraft状态 中间节点存在，并锚定 Haru-[当前状态]->它。

    锚定边幂等。Haru 的直接出度因此恒定（1 条状态边），世界槽位的增减
    只影响中间节点的出度——主体节点不被世界信息的增长污染。
    """
    hub = kg.nodes.get(STATE_HUB_ID)
    if hub is None:
        kg.add_node(Node(
            id=STATE_HUB_ID, weight=0.6, label="declarative-semantic",
            graph_space="self",
            extra_attrs={"type": "embodiment_state", "canon": "state",
                         "desc": "Haru 在 Minecraft 中的当前具身状态"}))
        hub = kg.nodes[STATE_HUB_ID]
    if "Haru" in kg.nodes and not kg.get_edge("Haru", STATE_HUB_ID, "当前状态"):
        kg.add_edge(Edge(src="Haru", dst=STATE_HUB_ID, relation="当前状态",
                         weight=0.8, relation_category="cognitive_relation"))
    return hub


def _ensure(kg, nid, value, extra=None):
    """固定身份动态状态节点：存在则 in-place 更新，不存在则建。

    建节点时接 `当前Minecraft状态 -[状态项]-> 槽位` 边（幂等）：没有这条边时
    状态节点是孤岛——扩散到不了它、语言层里也没有"这是 Haru 的状态"这个锚点，
    于是被问"你在哪里"只能靠猜（实测她答成"我在Haru这里，和你一起"）。
    锚经由状态中间节点而不是直连 Haru：主体不直接承担世界状态关系。
    """
    n = kg.nodes.get(nid)
    if n is None:
        kg.add_node(Node(
            id=nid, weight=0.5, label="declarative-semantic", graph_space="cognitive",
            extra_attrs={"type": "dynamic_state", "canon": "state", "value": value,
                         **(extra or {})}),
            source="minecraft.perception")
        n = kg.nodes[nid]
        _ensure_state_hub(kg)
        if not kg.get_edge(STATE_HUB_ID, nid, "状态项"):
            kg.add_edge(Edge(src=STATE_HUB_ID, dst=nid, relation="状态项",
                             weight=0.6, relation_category="cognitive_relation"))
    else:
        n.extra_attrs["value"] = value
        if extra:
            n.extra_attrs.update(extra)
        n.touch()
    return n


def update_perception(kg, engine, state: dict,
                      hostile_names=None) -> dict:
    """游戏状态 → 原子感知节点。返回 {updated:[...], unknowns:[...]}。

    能力图谱扩展（2026-09-20）：附近生物槽位携带 dist 与敌对标注
    （extra_attrs["hostile_dist"]，供 RETREAT gates 读）；背包经
    state["inventory_items"] 低频上图（Haru-[拥有]->物品:x，count
    in-place）——requires_state 的图端点。敌对名单由调用方注入
    （config 唯一真相源，感知层不自带名单）。
    """
    if not state or not state.get("connected"):
        return {"updated": [], "unknowns": []}
    updated = []
    hostile_set = {str(h).lower() for h in (hostile_names or [])}
    with kg._lock:
        # 观测：先快照值槽位（函数末尾只对真变化的槽发事件，§14 只在变化时
        # 发；纯读取不改任何感知写入，失败则整段跳过）
        try:
            _prev_slot = {}
            for _i in DYNAMIC_NODES:
                _n0 = kg.nodes.get(_i)
                _prev_slot[_i] = ((_n0.extra_attrs or {}).get("value")
                                  if _n0 is not None else None)
        except Exception:
            _prev_slot = None
        _ensure_state_hub(kg)
        p = state.get("position") or {}
        _ensure(kg, "Haru的位置", f"({p.get('x')},{p.get('y')},{p.get('z')})",
                {"pos": p})
        _ensure(kg, "Haru的血量", state.get("health", 0))
        _ensure(kg, "Haru的饥饿", state.get("food", 0))
        held = state.get("heldItem") or "空手"
        _ensure(kg, "Haru的手持物", held)
        players = [pl.get("name") for pl in state.get("playersNearby", [])]
        _ensure(kg, "附近的玩家", ", ".join(players) if players else "无",
                {"players": players})

        # 未知检测：图外玩家/方块/生物 → Unknown 节点（认知 discrepancy 信号）
        # 语义（好奇心机制重构 2026-09）：Unknown* 节点本身是"新颖对象"标记；
        # 新发现时给 未知信息 节点注入一次弱信号——它只是探索驱动力（
        # CuriosityDrive）的输入之一，不直接保证产生任何探索行动。
        # 架构对齐 2026-09-19：不再建 Unknown*→Haru 边——"未知对象在附近"
        # 是感知上下文不是自我事实；Unknown* 的认知存在感由在视注意力地板
        # 维持，好奇心由 未知信息 信号节点驱动，两条通路都不依赖那条边。
        unknowns = []
        def _unknown(uid, kind, label):
            if uid in kg.nodes:
                return
            kg.add_node(Node(
                id=uid, weight=0.5, label="declarative-semantic",
                graph_space="semantic",
                extra_attrs={"type": "unknown", "kind": kind, "name": label,
                             "observed_at": state.get("connectedAt", "")}),
                source="minecraft.perception")
            unknowns.append(uid)
            # 探索信号：只在新发现时注入一次（节点已存在则不重复注入）
            sig = kg.get_node("未知信息")
            if sig is not None and engine is not None:
                sig.activation = min(5.0, sig.activation + 0.3)
                sig.touch()
                engine.mark_active([sig.id])
                engine.register_activation_source([sig.id], "perception")
        for name in players:
            if name not in kg.nodes:
                _unknown(f"UnknownPlayer_{name}", "player", name)
        for b in state.get("nearbyBlocks") or []:
            bn = b.get("name", "")
            # 已知判定要跨两个命名空间：mc_knowledge 种子建裸名物种节点，
            # 配方闭包建 物品: 节点。只查裸名会把合成台这类“已知物品名方块”
            # 误报成 UnknownBlock（离线验收 A2/A3 定位）。
            if bn and (bn not in kg.nodes) and (f"物品:{bn}" not in kg.nodes):
                _unknown(f"UnknownBlock_{bn}", "block", bn)
        for en in state.get("nearbyEntities") or []:
            en = en.get("name", "")
            if en and en not in kg.nodes:
                _unknown(f"UnknownEntity_{en}", "entity", en)

        # 方块/生物动态状态节点值
        blocks_v = ", ".join(b.get("name", "") for b in (state.get("nearbyBlocks") or [])[:4])
        ents_v = ", ".join(e.get("name", "") for e in (state.get("nearbyEntities") or [])[:4])
        _ensure(kg, "附近的方块", blocks_v or "无")
        # 附近生物槽位附距离与敌对标注（能力 gates/抑制的图端点；
        # 名单由调用方注入=config 唯一真相源，感知层不私藏名单）
        hostile_dist = {}
        ent_dist = {}
        for e in state.get("nearbyEntities") or []:
            en = str(e.get("name", "")).lower()
            try:
                d = float(e.get("dist", 99))
            except (TypeError, ValueError):
                d = 99.0
            if en:
                ent_dist[en] = min(ent_dist.get(en, d), d)
                if en in hostile_set:
                    hostile_dist[en] = min(hostile_dist.get(en, d), d)
        _ensure(kg, "附近的生物", ents_v or "无",
                {"entities": ent_dist, "hostile": sorted(hostile_dist),
                 "hostile_dist": hostile_dist})
        # 背包低频上图（能力图谱 2026-09-20）：Haru-[拥有]->物品:x，
        # count in-place；本次快照未出现的旧物品 count 置 0（拥有=此刻，
        # 不是终身事实）。2026-09-26 修：先按名聚合所有堆叠再上图——
        # 旧版逐堆覆盖（同物两堆只剩最后一堆的数）且 [:24] 截断（第 25
        # 堆起背包里有物却被置 0，规划器据此判"没有"）。
        inv_items = state.get("inventory_items")
        if isinstance(inv_items, list):
            totals: dict = {}
            for it in inv_items:
                name = str((it or {}).get("name") or "").strip()
                if not name:
                    continue
                try:
                    cnt = float(it.get("count", 1) or 0)
                except (TypeError, ValueError):
                    cnt = 0.0
                if cnt > 0:
                    totals[name] = totals.get(name, 0.0) + cnt
            seen = set()
            for name, cnt in totals.items():
                iid = f"物品:{name}"
                seen.add(iid)
                node = kg.get_node(iid)
                if node is None:
                    kg.add_node(Node(
                        id=iid, weight=0.4, label="declarative-semantic",
                        graph_space="semantic",
                        extra_attrs={"type": "inventory_item",
                                     "name": name, "count": cnt}),
                        source="minecraft.perception")
                    # P3/§5：获得新资源→用途未知→探索缺口（先验层）
                    try:
                        import prior_knowledge
                        prior_knowledge.consider_recognition(
                            kg, engine, name, None)
                    except Exception as e:
                        logger.warning(f"[perception] 缺口登记失败 "
                                       f"{name}: {e}")
                else:
                    ea = dict(node.extra_attrs or {})
                    ea["count"] = cnt
                    node.extra_attrs = ea
                    node.touch()
                    # 真机断点②修复（2026-09-23）：缺口只开在物品节点首建时
                    # ——存量未知物（缺口曾被放弃）永远排不上实验。在手 ∧
                    # 缺口以 abandoned 关闭 ∧ 隔满生命周期 → 有界重开。
                    if cnt > 0:
                        try:
                            import prior_knowledge
                            prior_knowledge.resurface_gap(kg, engine, name)
                        except Exception as e:
                            logger.warning(f"[perception] 缺口重开失败 "
                                           f"{name}: {e}")
                if kg.get_edge("Haru", iid, "拥有") is None \
                        and "Haru" in kg.nodes:
                    kg.add_edge(Edge(src="Haru", dst=iid, relation="拥有",
                                     weight=0.6))
                updated.append(iid)
            for nid, node in kg.nodes.items():
                if str(nid).startswith("物品:") and nid not in seen:
                    ea = dict(node.extra_attrs or {})
                    if float(ea.get("count", 0) or 0) > 0:
                        ea["count"] = 0
                        node.extra_attrs = ea

        # 兼容旧槽位：历史会话留下过 `Fascinator -[位于]-> 当前所在位置`（空值），
        # 它会在位置类问题里抢话题却没有真值。存在就写入真实坐标，保持全图一致。
        if "当前所在位置" in kg.nodes:
            kg.nodes["当前所在位置"].extra_attrs["value"] =                 f"({p.get('x')},{p.get('y')},{p.get('z')})"
            kg.nodes["当前所在位置"].touch()

    ids = DYNAMIC_NODES + [STATE_HUB_ID] + unknowns
    known = [i for i in ids if i in kg.nodes]
    for i in known:
        kg.nodes[i].touch()
        # 在视注意力地板（Bug 修复 2026-09-19）：当前看得见的状态/未知实体
        # 保持最低激活——否则创建瞬间的激活被衰减吃光，自主行动的 attention
        # 分量恒为 0（实测 observe/approach 候选永远低于阈值，她站着不动）。
        # "看得见 = 正在注意" 是诚实的图信号，不是为了让分数好看。
        # B3：本轮新建的未知=满地板；图上已存在者退役（recognized）后回落
        # 0.6——认出来的东西还在视野里也只是"看见"，不再是新颖性信号。
        floor = 1.0 if i in unknowns or (i.startswith("Unknown")
                                          and is_live_unknown(kg.nodes[i])) else 0.6
        if kg.nodes[i].activation < floor:
            kg.nodes[i].activation = floor
    # 观测：只对真变化的槽位发一条（感知=状态变化时记，不逐拍刷屏，§14）；
    # 纯旁路读取，异常吞掉，绝不影响感知返回值。
    try:
        if _prev_slot:
            import fas_log
            _changed = {}
            for _i in DYNAMIC_NODES:
                _n1 = kg.nodes.get(_i)
                if _n1 is None:
                    continue
                _nv = (_n1.extra_attrs or {}).get("value")
                if _nv != _prev_slot[_i]:
                    _changed[_i] = {"old": _prev_slot[_i], "new": _nv}
            if _changed:
                fas_log.get_logger(fas_log.PERCEPTION).info(
                    "mc_perception_updated", "MC 状态槽变化",
                    changed=_changed)
    except Exception:
        pass
    engine.mark_active(known)
    return {"updated": known, "unknowns": unknowns}

def world_state_snapshot(kg) -> dict:
    """从图里读她的世界状态槽位 → {槽位名: 值}。

    语言层要的是"她此刻的真实状态"，来源必须是图（感知写进去的），
    不是再去问一次桥——图谱是唯一真源，这样离线/断连时也不会造出假状态。
    只返回**有值**的槽位。
    """
    out = {}
    with kg._lock:
        for nid in DYNAMIC_NODES:
            n = kg.nodes.get(nid)
            if n is None:
                continue
            v = (n.extra_attrs or {}).get("value")
            if v not in (None, ""):
                out[nid] = v
    return out


def promote_species(kg, engine, name: str, detail: dict = None,
                    config: dict = None) -> dict:
    """经验识别（B3/§十五）：真实观察回执 → 未知物沉淀为物种知识。

    inspect_entity 成功（带真实 dist/rel 回执）后调用。做四件事，零 LLM：
      1. raw-id 物种节点（id 即 "cow"，type=mc_species，对齐 mc_knowledge
         查询层；source=experience——这是她自己看出来的，不是种子给的）；
      2. 原 UnknownEntity_cow **不删**，置 retired="recognized"（历史保留，
         但不再是新颖性信号；消费点统一经 graph_model.is_live_unknown）；
      3. note_interest(resolved)：兴趣 ×0.4 **永不清零**（curiosity_engine
         合同），resolved_count++ 自动喂 satiation 通道；
      4. fas_log CURIOSITY "curiosity_satiated"（level 前后对照，可审计）。
    再遇同名：_unknown() 见 uid 已在图 → 不再建新节点、不再注入 未知信息
    信号——饱食链靠的就是这个既有幂等，无需额外规则。
    """
    name = str(name or "").strip()
    if kg is None or not name or name.lower() in ("none", "unknown"):
        return {"promoted": False, "reason": "bad_name"}
    # 物种 id 与 mc_knowledge 查询层同一归一（normalize_species：小写、去修饰）
    try:
        from mc_knowledge import normalize_species
        sp = normalize_species(name)
    except Exception:
        sp = name.lower()
    if not sp:
        return {"promoted": False, "reason": "bad_name"}
    # Unknown 节点 id 由感知层按 bot 原名建（UnknownEntity_cow）；两种写法都找
    uid = next((u for u in (f"UnknownEntity_{name}", f"UnknownEntity_{sp}",
                            f"UnknownBlock_{name}", f"UnknownBlock_{sp}")
                if u in kg.nodes), None)
    unknown = kg.nodes.get(uid) if uid else None
    if unknown is None:
        return {"promoted": False, "reason": "no_unknown_node"}   # 不是未知物（已是已知），不重复沉淀
    ea_u = unknown.extra_attrs or {}
    first_time = not ea_u.get("retired")

    detail = detail or {}
    try:
        dist = float(detail.get("dist")) if detail.get("dist") is not None else None
    except (TypeError, ValueError):
        dist = None

    # 1) 物种节点（幂等；已存在则只刷新观察计数，绝不覆盖已有语义）
    created = False
    spn = kg.nodes.get(sp)
    if spn is None:
        kg.add_node(Node(
            id=sp, weight=0.5, label="declarative-semantic",
            graph_space="semantic",
            extra_attrs={"type": "mc_species", "source": "experience",
                         "observed_at": now_str(),
                         "observed_count": 1,
                         "first_dist": dist}),
            source="experience")
        created = True
    else:
        ea = dict(spn.extra_attrs or {})
        ea["observed_count"] = int(ea.get("observed_count", 0) or 0) + 1
        ea["last_observed_at"] = now_str()
        spn.extra_attrs = ea
        spn.touch()
    # 2) 证据边：当前Minecraft状态 -[出现过]-> 物种（"她在世界里见过它"的
    #    图端点；直连 当前Minecraft状态 而不是 Haru——看见不是自我事实）
    _ensure_state_hub(kg)
    if kg.get_edge(STATE_HUB_ID, sp, "出现过") is None:
        kg.add_edge(Edge(src=STATE_HUB_ID, dst=sp, relation="出现过",
                         weight=0.6, relation_category="cognitive_relation"))
    # 3) Unknown 退役（标记，不删）+ 兴趣 resolved（×0.4 不清零）
    ea_u = dict(unknown.extra_attrs or {})
    ea_u["retired"] = "recognized"
    ea_u["recognized_as"] = sp
    unknown.extra_attrs = ea_u
    unknown.touch()
    level_before = level_after = 0.0
    try:
        import curiosity_engine as _ce
        cfg = config or {}
        level_before = float((_ce.get_interest(kg, uid) or {}).get(
            "level", 0.0) or 0.0)
        it = _ce.note_interest(kg, uid, "resolved", cfg)
        level_after = float((it or {}).get("level", level_before))
    except Exception as e:
        logger.debug(f"[Perception] 兴趣回写跳过: {e}")
    # 4) 观测面：饱食发生在这里（前后水平可审计；只在首次退役时发）
    if first_time:
        try:
            import fas_log
            fas_log.emit(fas_log.CURIOSITY, "INFO", "curiosity_satiated",
                         f"认识了 {sp}",
                         entity=sp, unknown=uid, created=created,
                         level_before=round(level_before, 3),
                         level_after=round(level_after, 3),
                         dist=dist)
        except Exception:
            pass
    # P3/§5 探索缺口：认识了 ≠ 懂了。经验识别出的物种若没有任何用途
    # 知识（无 产生/需要工具/服务于/分类归属 边、未被配方引用），
    # 就是一条"已知对象 ∧ 未知用途"——开探索缺口喂 CuriosityDrive/候选。
    try:
        import prior_knowledge
        prior_knowledge.consider_recognition(kg, engine, sp, config)
    except Exception as e:
        logger.debug(f"[Perception] 先验层缺口检查跳过: {e}")
    return {"promoted": True, "species": sp, "created": created,
            "level_before": level_before, "level_after": level_after}
