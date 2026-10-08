# minecraft.embodiment.py — Minecraft 具身环境（认知 → Action → Skill 的出口）
# ============================================================================
# 本模块是 Minecraft 这一侧的适配器：
#   上游（ActionManager / 用户指令 / 自主循环）产生 **ActionNode**
#       （带 motivation / reason / expected_effect 的结构化动作）
#   本模块把 action_type 翻译成 Skill Library 里的具身技能并执行，
#   把**真实**结果回传（失败必须语义化真实返回，绝不假装成功）。
#
# 技能是 FAS 的具身能力（skills/ 包）：认知系统决定"做不做"，
# 这里 + Skill 层决定"怎么做"。旧 8 个语义意图（observe/approach/follow/
# explore/collect/rest/communicate/withdraw）**只是兼容词**，映射到对应技能
# 执行（2026-09-21 降级：它们不再是行为类别，能力面以技能名为准）。
#
# 感知进图：tick 级感知写统一知识图谱（沉默期世界状态不再冻结）；
# detect_events() 把世界差分翻成结构化事件（资源/威胁/生存/时段/社交），
# 供自主认知走 事件 → 图激活 → 需求/动机 → 候选 Action 的通路。
#
# 危险动作边界：发现未知生物**不会自动攻击**（观察/靠近/撤离）；攻击类
# 动作只能由认知层明确产生 ActionNode（如用户指令"杀掉僵尸"）。
# ============================================================================

import logging
import time

import minecraft.bridge as bridge
from graph_model import is_live_unknown_id
from minecraft.actions import BLOCK_MAP
import skills
from skills.base import SkillContext, LocationMemory, get as get_skill, poll as poll_skill, cancel as cancel_skill
from skills.observation import detect_environment_events
from skills.gathering import SAFE_GATHER_BLOCKS as SAFE_DIG_BLOCKS  # 兼容旧导入

logger = logging.getLogger(__name__)

try:                      # §21 实验观测钩子（未开实验时恒静默）
    import experiment_mode as xm
except Exception:
    xm = None

# 学习实验 §8：获取/制作类动作成功后背包观察节拍立即到点（默认每 8 拍
# ≈40s 才刷一次，归因窗等不起）。纯节流调整，不改变观测内容。
_ACQUISITION_ACTIONS = frozenset({
    "gather_resource", "chop_tree", "break_block", "harvest_crop",
    "collect_dropped_item", "pickup_item", "craft_item", "smelt_item",
    "furnace_take", "place_block", "chest_take", "collect_food"})

# 旧语义意图 → 技能名（兼容层，冻结于 2026-09-21）：仅供旧自主层/测试的
# 接口兼容。它不是行为类别清单——新的能力表达走技能名本身与行动概念图谱，
# 依赖只允许收敛到本表，不得在新代码路径里再引用旧 8 词。
LEGACY_SKILL_MAP = {
    "observe": "inspect_area",
    "approach": "navigate_to_entity",
    "follow": "follow_entity",
    "explore": "explore_direction",
    "collect": "gather_resource",
    "rest": "stop",
    "withdraw": "retreat",
}

# 对外暴露的能力面 = 技能名全集 + 旧语义意图（兼容检查用；旧词冻结，勿扩展）
CAPABILITIES = {"observe", "approach", "follow", "explore", "collect",
                "rest", "communicate", "withdraw"}


class MinecraftEmbodiment:
    """Minecraft 具身环境：感知 + Action→Skill 执行 + 真实结果回执。"""

    name = "minecraft"

    def __init__(self, kg=None, engine=None, companion_names=None,
                 perceive_into_graph: bool = True, config=None):
        self.kg = kg
        self.engine = engine
        self.companion_names = list(companion_names or [])
        self.perceive_into_graph = perceive_into_graph
        # 能力图谱接线（2026-09-20）：敌对名单唯一真相源=config；
        # CapabilityIndex 由 app 注入后，capabilities() 以
        # 技能全集 ∪ 能力节点 executors 为面（8 旧语义词退役为兼容别名）
        self.config = config or {}
        self.cap_index = None
        # 背包刷新：时间门（_inv_last_pull，见 perceive；2026-09-26 由
        # "每 8 拍"计数改造，旧 _inv_every/_inv_count 计数字段已退役删除）
        self._last_inv = None          # 最近一次成功拉到的背包（给自主层 percept）
        self._last_state = None
        self._last_sig = ""
        self._followed = None
        self.timeline = None
        # B5/§5：主动言语投递出口（app 注入 ChannelHub）。有通道就走通道
        # （画像清洗+单点节流+诚实回执），没有才退回 bridge.say 直发。
        self.channel_hub = None
        self._tl_prev = None
        self._inv_prev = None          # 上一次背包快照（B2 计数差分基准）
        # 技能上下文（世界接口：桥 + 缓存 + 位置记忆 + 当前技能会话）
        self.ctx = SkillContext(bridge=bridge, config=self.config,
                                locations=LocationMemory())
        self._skill_events_prev = None   # 事件检测差分用

    # ── 可用性 / 能力 ─────────────────────────────────────

    def available(self) -> bool:
        state = bridge.get_state()
        return bool(state and state.get("connected"))

    def capabilities(self):
        """此刻现实可执行的 executor 名集合（连接态真相）。

        能力真相源=图上能力节点的 executors；本方法只是
        "技能注册表 ∪ 能力节点声明" 的读取器——不再维护独立名单。
        8 个旧语义词在 cap_index 未注入时保留（兼容测试/旧 goal 名），
        注入后退役为经 LEGACY_SKILL_MAP 的输入别名。
        """
        if not self.available():
            return set()
        caps = {s["name"] for s in skills.all_skills()}
        caps.add("communicate")       # 具身发言口（非技能，embodiment 短路）
        if self.cap_index is None:
            caps |= set(CAPABILITIES)
        return caps

    def realize_goal(self, action_type: str) -> str:
        """目标词 → 此刻的具身实现名（B4/§4）。

        目标词表（follow/approach/…）是 goal API 的稳定契约面；执行词是
        技能名。旧透传直接把契约词喂给 _feasible_action，在 cap_index
        注入后（旧词退役）死于"具身环境不支持 follow"——承诺明明在场却
        永远不兑现。映射单一真相源=LEGACY_SKILL_MAP，不在此表的原样返回
        （契约词已实现名时幂等）。
        """
        t = str(action_type or "")
        return LEGACY_SKILL_MAP.get(t, t)

    def skill_catalog(self) -> list:
        return skills.all_skills()

    # ── 原始世界状态（ActionManager 生存紧急检查用；轻量）──

    def raw_state(self) -> dict:
        return bridge.get_state() or {}

    # ── 感知（进图 + 归一化给自主层） ─────────────────────

    def perceive(self) -> dict:
        state = bridge.get_state()
        if not state or not state.get("connected"):
            return {"connected": False, "players": [], "entities": [], "blocks": [],
                    "unknown_entities": [], "unknown_blocks": []}

        if self.perceive_into_graph and self.kg is not None:
            sig = self._signature(state)
            # 背包刷新按**时间**（2026-09-26 真机）：旧的"每 8 拍"节流在
            # busy 路径下（perceive 被 20s 心跳门限流）首拍要 2 分半才到，
            # 启动窗口里规划器看到空背包→整条目标链爬错方向（craft 明明
            # 原料已齐却去收集原木）。时间门：首拍立即拉，之后 20s 一次，
            # 失败静默（下拍再试），_last_inv 给每一拍兜底。
            _now = time.time()
            inv_items = None
            if _now - float(getattr(self, "_inv_last_pull", 0.0) or 0.0) >= 20.0:
                self._inv_last_pull = _now
                try:
                    inv = bridge.inventory()
                    if isinstance(inv, dict) and inv.get("ok", True):
                        inv_items = inv.get("items") or []
                        self._last_inv = inv_items
                except Exception:
                    inv_items = None
            if sig != self._last_sig or inv_items is not None:
                try:
                    from minecraft.perception import update_perception
                    if inv_items is not None:
                        state["inventory_items"] = inv_items
                    elif self._last_inv:
                        # 非刷新拍也要带背包（2026-09-26 深夜真机）：刷新
                        # 节流只该管"拉取/写图频率"，不该让自主层在非刷新拍
                        # 里看到空背包——规划器因此时而有木棍、时而没有，链条
                        # 在采集/合成之间精神分裂。
                        state["inventory_items"] = list(self._last_inv)
                    update_perception(
                        self.kg, self.engine, state,
                        hostile_names=(self.config.get(
                            "hostile_entities") or []))
                    self._last_sig = sig
                except Exception as e:
                    logger.warning(f"[MCEmbodiment] 感知入图失败: {e}")
            # P3/§8 配方线索（环境事实→图）：背包刷新后同拍向 bot 要
            # "此刻能做出什么"——写 可制作物品 hub + 配方:边，并关闭
            # 线索涉及的探索缺口（知道配方=知道一个用途）。bot 侧 30s
            # 缓存；桥断了静默降级（线索不是生命线，绝不阻塞感知）。
            if inv_items is not None and self.kg is not None:
                try:
                    rec = bridge.recipes()
                    if isinstance(rec, dict) and rec.get("ok"):
                        import prior_knowledge as pk
                        pk.note_craftable(self.kg, self.engine,
                                          rec.get("craftable") or [],
                                          self.config)
                except Exception as e:
                    logger.debug(f"[MCEmbodiment] 配方线索刷新跳过: {e}")
            # 在视注意力地板（Bug 修复 2026-09-19）：**每次感知都维持**，
            # 不受签名门控影响——她站着不动、世界无实质变化时 update_perception
            # 会被跳过，看得见的未知实体激活照样衰减到 0。
            try:
                from minecraft.perception import DYNAMIC_NODES
                visible = list(DYNAMIC_NODES)
                for e in state.get("nearbyEntities") or []:
                    nm = e.get("name") or e.get("displayName")
                    if nm:
                        visible.append(f"UnknownEntity_{nm}")
                for b in state.get("nearbyBlocks") or []:
                    nm = b.get("name")
                    if nm:
                        visible.append(f"UnknownBlock_{nm}")
                with self.kg._lock:
                    active_ids = [v for v in visible if v in self.kg.nodes]
                    for nid in active_ids:
                        n = self.kg.nodes[nid]
                        # B3 饱食链：退役（已认识）的 Unknown 只按"在视"给
                        # 普通地板，不再享受未知物满地板——每拍托起 9 个
                        # 永新 Unknown 正是"呆呆地反复好奇"的病灶。
                        floor = (1.0 if is_live_unknown_id(self.kg, nid) else 0.6)
                        if n.activation < floor:
                            n.activation = floor
                if active_ids and self.engine is not None:
                    self.engine.mark_active(active_ids)
            except Exception as e:
                logger.debug(f"[MCEmbodiment] 注意力地板跳过: {e}")
        self._last_state = state
        if self.timeline is not None:
            try:
                self._emit_timeline_events(state)
            except Exception as e:
                logger.debug(f"[MCEmbodiment] 时间轴事件发射跳过: {e}")

        entities = []
        unknown_entities = []
        for e in state.get("nearbyEntities", []) or []:
            name = e.get("name") or e.get("displayName")
            item = {"name": name, "displayName": e.get("displayName") or name,
                    "dist": e.get("dist"), "rel": e.get("rel") or {}}
            entities.append(item)
            if self.kg is not None and is_live_unknown_id(
                    self.kg, f"UnknownEntity_{name}"):
                unknown_entities.append(item)

        blocks = []
        unknown_blocks = []
        for b in state.get("nearbyBlocks", []) or []:
            name = b.get("name")
            blocks.append({"name": name, "count": b.get("count")})
            if self.kg is not None and is_live_unknown_id(
                    self.kg, f"UnknownBlock_{name}"):
                unknown_blocks.append({"name": name, "count": b.get("count")})

        players = []
        for pl in state.get("playersNearby", []) or []:
            players.append({"name": pl.get("name"), "dist": pl.get("dist"),
                            "rel": pl.get("rel") or {}})

        # 危险信号生产者（2026-09-25）：danger_visible 此前只有消费者没有
        # 生产者——全库 grep 证实。risk 评分分量/绑定器守卫因此恒 0/恒 False
        # （敌对生物站旁边她也当没有）。这里按配置表 + 危险距离补上。
        _hn = {str(n or "").lower() for n in (self.config.get("hostile_entities") or [])}
        _dd = float((self.config.get("autonomy") or {}).get("danger_distance", 6.0) or 6.0)
        danger_visible = any(
            str(e.get("name") or "").lower() in _hn
            and isinstance(e.get("dist"), (int, float))
            and float(e["dist"]) <= _dd
            for e in entities)
        return {
            "connected": True,
            "position": state.get("position"),
            "health": state.get("health"),
            "food": state.get("food"),
            "held": state.get("heldItem"),
            "players": players,
            "entities": entities,
            "blocks": blocks,
            "danger_visible": danger_visible,
            "unknown_entities": unknown_entities,
            "unknown_blocks": unknown_blocks,
            "chat": state.get("chat") or [],
            # 2026-09-23 真机断点修复：缺口→试做候选看的是自主层 percept 的
            # inventory_items，此前背包只进"写图"那条路、从不下发——试做通道
            # 在真机上永远看不见物品。低频缓存（_last_inv），宁旧勿缺。
            "inventory_items": list(self._last_inv or []),
        }

    @staticmethod
    def _signature(state: dict) -> str:
        """状态指纹：只有材料性变化才重新写图（避免每 tick 无意义地刷节点）。

        §P1（2026-09-27 收敛修复）：nearbyBlocks/nearbyEntities 是类型计数
        表——名单不变但数量变了（5 块石头被挖剩 2 块）此前不触发隐私写图，
        世界→认知存在盲窗。指纹升级为 name×count。
        """
        p = state.get("position") or {}
        def _nxc(rows):
            return ",".join(sorted(
                f"{r.get('name')}x{r.get('count')}"
                for r in (rows or []) if r.get("name")))
        return "|".join([
            str(p.get("x")), str(p.get("y")), str(p.get("z")),
            str(state.get("health")), str(state.get("food")),
            str(state.get("heldItem")),
            _nxc(state.get("nearbyEntities", [])),
            _nxc(state.get("nearbyBlocks", [])),
            ",".join(sorted(str(pl.get("name")) for pl in state.get("playersNearby", []) or [])),
        ])

    # ── 世界事件（自主认知的事件源；零 LLM）───────────────

    def detect_events(self) -> list:
        """世界快照 → 结构化事件（资源/威胁/生存/时段/社交/结构/掉落物）。"""
        state = bridge.get_state()
        if not state or not state.get("connected"):
            self._skill_events_prev = None
            return []
        class _Shim:
            """detect_environment_events 需要 ctx.state() 接口，这里直接喂快照。"""
            def __init__(self, s):
                self._s = s
            def state(self, refresh=False):
                return self._s
        events = detect_environment_events(_Shim(state), self._skill_events_prev)
        self._skill_events_prev = dict(state)
        return events

    # ── 经验时间轴：轮询差分 → 事件级 observation（§二/§五）──

    def _emit_timeline_events(self, state: dict):
        from experience import (make_event, EVENT_OBSERVATION, EVENT_SELF_STATE)
        tl = self.timeline
        prev = self._tl_prev or {}
        now_src = "minecraft"

        def emit(etype, actor, subject, content):
            tl.append(make_event(etype, actor=actor, subject=subject,
                                 content=content, source=now_src))
            # 观测面（§24）：世界事件聚合行；默认关，防刷屏（debug_world_events）
            if self.config.get("debug_world_events"):
                try:
                    import fas_log
                    fas_log.emit(fas_log.PERCEPTION, "DEBUG", "world_event",
                                 f"{subject} {content.get('change')}",
                                 subject=subject,
                                 change=str(content.get("change") or ""))
                except Exception:
                    pass

        for slot in ("health", "food"):
            a, b = prev.get(slot), state.get(slot)
            if a is not None and b is not None and a != b:
                emit(EVENT_SELF_STATE, "self", slot,
                     {"change": "changed", "target": slot,
                      "before": a, "after": b})
        held_a, held_b = prev.get("heldItem"), state.get("heldItem")
        if held_a != held_b and (held_a or held_b):
            emit(EVENT_SELF_STATE, "self", "held_item",
                 {"change": "changed", "target": "held_item",
                  "before": held_a, "after": held_b})

        prev_e = {str(e.get("name")) for e in (prev.get("nearbyEntities") or [])}
        cur_e = {str(e.get("name")) for e in (state.get("nearbyEntities") or [])}
        for name in sorted(cur_e - prev_e):
            emit(EVENT_OBSERVATION, "minecraft", f"entity:{name}",
                 {"change": "appeared", "target": name})
        for name in sorted(prev_e - cur_e):
            emit(EVENT_OBSERVATION, "minecraft", f"entity:{name}",
                 {"change": "disappeared", "target": name})

        prev_b = {str(b.get("name")) for b in (prev.get("nearbyBlocks") or [])}
        cur_b = {str(b.get("name")) for b in (state.get("nearbyBlocks") or [])}
        for name in sorted(cur_b - prev_b):
            emit(EVENT_OBSERVATION, "minecraft", f"block:{name}",
                 {"change": "appeared", "target": name})
        for name in sorted(prev_b - cur_b):
            emit(EVENT_OBSERVATION, "minecraft", f"block:{name}",
                 {"change": "disappeared", "target": name})

        prev_p = {str(p.get("name")) for p in (prev.get("playersNearby") or [])}
        cur_p = {str(p.get("name")) for p in (state.get("playersNearby") or [])}
        for name in sorted(cur_p - prev_p):
            emit(EVENT_OBSERVATION, "minecraft", f"player:{name}",
                 {"change": "appeared", "target": name})
        for name in sorted(prev_p - cur_p):
            emit(EVENT_OBSERVATION, "minecraft", f"player:{name}",
                 {"change": "disappeared", "target": name})

        # 背包计数差分（B2）："挖到了什么/吃了什么"进因果窗口。
        # 只在两侧快照都齐时对比——背包是低频刷拍，缺拍≠没变化，不误报。
        # change 带方向（increased/decreased）：同值会被时间轴合并语义吞掉
        # 一次方向变化，且因果本就该把"变多/变少"当不同结果。
        inv_cur = state.get("inventory_items")
        if inv_cur is not None:
            if self._inv_prev is not None:
                _pc = {str(i.get("name")): int(i.get("count", 0))
                       for i in self._inv_prev}
                _cc = {str(i.get("name")): int(i.get("count", 0)) for i in inv_cur}
                for name in sorted(set(_pc) | set(_cc)):
                    _b, _a = _pc.get(name, 0), _cc.get(name, 0)
                    if _b != _a:
                        try:
                            if xm is not None and xm.enabled():
                                xm.xlog("OBSERVATION",
                                        f"inventory:{name} {_b}→{_a}")
                        except Exception:
                            pass
                        emit(EVENT_OBSERVATION, "self", f"inventory:{name}",
                             {"change": "count_increased" if _a > _b
                              else "count_decreased",
                              "target": name, "before": _b, "after": _a})
            self._inv_prev = list(inv_cur)

        seen_seq = int((self._tl_prev or {}).get("_chat_seq") or 0)
        max_seq = seen_seq
        for c in sorted(state.get("chat") or [], key=lambda x: x.get("seq", 0)):
            if int(c.get("seq", 0)) <= seen_seq:
                continue
            max_seq = max(max_seq, int(c.get("seq", 0)))
            emit(EVENT_OBSERVATION, str(c.get("sender") or "unknown"),
                 "utterance",
                 {"change": "observed", "text": str(c.get("text", ""))[:160]})
        self._tl_prev = dict(state)
        self._tl_prev["_chat_seq"] = max_seq

    def on_settled(self, action: dict, result: dict, success: bool):
        """动作结算后的世界事件钩子（B2，由 ActionManager 通用协议调用）。

        把执行回执里"哪个坐标的哪个方块没了/多了"写成环境事实事件——
        快照差分覆盖不了它：nearbyBlocks 是类型计数表，五块石头挖掉一块
        集合不变，只有回执能证明这次变化。零 LLM、不写图（归因归
        CausalLearner）；回执里没有这些键（别的具身/别的动作）就静默。
        一次结算发**一条**事件（含全部坐标）：同 subject 同 change 的
        连续多条会被时间轴合并语义收成一条，逐点发反而丢信息。
        """
        if not success:
            return
        detail = result.get("result")
        if not isinstance(detail, dict):
            return
        from experience import make_event, EVENT_OBSERVATION
        atype = str(result.get("action") or action.get("action_type") or "")

        # 学习实验 §8：获取类动作成功 → 背包刷拍立即到点，下一次 perceive
        # 就观测增量（8s 归因窗 vs 40s 节拍的老错配，从观测侧解掉）。
        # 2026-09-26：刷新改按时间门后，"到点"=把上次拉取时间清零。
        if atype in _ACQUISITION_ACTIONS:
            self._inv_last_pull = 0.0

        # B3/§十五 物种学习：真实观察回执（带距离/方位，不是 found:0 空手
        # 回执）→ 经验识别沉淀物种节点并给好奇部分饱食。零 LLM；不删节点。
        if self.kg is not None:
            _sp = None
            if (atype == "inspect_entity" and detail.get("entity")
                    and (detail.get("dist") is not None
                         or detail.get("rel"))):
                _sp = (str(detail["entity"]), detail)
            elif (atype == "inspect_block" and detail.get("block")
                    and int(detail.get("found") or 0) > 0):
                _sp = (str(detail["block"]), {"found": detail.get("found")})
            if _sp:
                try:
                    from minecraft.perception import promote_species
                    promote_species(self.kg, self.engine, _sp[0], _sp[1],
                                    config=self.config)
                except Exception as e:
                    logger.debug(f"[MCEmbodiment] 物种沉淀跳过: {e}")

        if self.timeline is None:
            return

        def _emit(block, change, positions, n):
            if not block:
                return
            try:
                if xm is not None and xm.enabled():
                    xm.xlog("DELTA", f"{atype}: block:{block} {change} ×{n}",
                            pos=positions[0] if positions else None)
            except Exception:
                pass
            pos = positions[0] if positions else None
            self.timeline.append(make_event(
                EVENT_OBSERVATION, actor="self", subject=f"block:{block}",
                content={"change": change, "target": str(block),
                         "action": atype, "result": "success",
                         "before": n if change == "disappeared" else 0,
                         "after": 0 if change == "disappeared" else n,
                         "pos": pos if isinstance(pos, dict) else None,
                         "positions": positions[:16]},
                source="minecraft"))

        if atype == "gather_resource":
            dug = [p for p in (detail.get("dug_positions") or [])
                   if isinstance(p, dict)]
            _emit(detail.get("resource"), "disappeared", dug,
                  int(detail.get("collected") or len(dug) or 1))
        elif atype in ("break_block", "chop_tree") and detail.get("block"):
            p = detail.get("position")
            _emit(detail.get("block"), "disappeared",
                  [p] if isinstance(p, dict) else [], 1)
        elif atype == "place_block" and detail.get("block"):
            p = detail.get("position")
            _emit(detail.get("block"), "appeared",
                  [p] if isinstance(p, dict) else [], 1)

    # ── 执行（ActionNode → Skill）─────────────────────────

    def execute(self, action: dict) -> dict:
        """执行一个动作。接受 ActionNode（action_type）或旧意图（type）。"""
        params = dict(action.get("params") or {})
        action_type = str(action.get("action_type")
                          or action.get("type") or "").strip()
        if not action_type:
            return {"success": False, "action": "?", "reason": "no_action_type"}
        if not self.available():
            return {"success": False, "action": action_type,
                    "reason": "not_connected"}

        # 旧语义意图 → 技能名
        skill_name = LEGACY_SKILL_MAP.get(action_type, action_type)

        # observe 带实体目标 = 把视角转向它（保留旧语义）；无目标 = 环顾四周
        if action_type == "observe":
            ent_name = params.get("entity") or action.get("target")
            if ent_name:
                skill_name = "look_at"
                params.setdefault("entity", ent_name)

        # communicate 是语言动作：只有确实有内容才说（不编台词）
        if action_type == "communicate":
            return self._communicate(action, params)

        # gather 的旧参数（block=）映射到 resource=
        if skill_name in ("gather_resource", "chop_tree", "gather_wood",
                          "gather_stone"):
            params.setdefault("resource",
                              params.pop("block", None)
                              or action.get("target") or params.get("target"))
            # 旧路径 target 是方块名（如 oak_log）
            if not params.get("resource"):
                params["resource"] = action.get("target")

        skill = get_skill(skill_name)
        if skill is None:
            return {"success": False, "action": action_type,
                    "reason": f"unknown_skill:{skill_name}"}

        # 目标实体/玩家的约定参数（ActionNode.target → 技能参数）
        if skill_name in ("navigate_to_entity", "follow_entity",
                          "inspect_entity") and action.get("target"):
            params.setdefault("entity", action.get("target"))
            params.setdefault("player", action.get("target"))
        if skill_name == "retreat" and action.get("target"):
            params.setdefault("from", action.get("target"))
        if skill_name == "explore_direction" and action.get("target") \
                and action.get("target") in ("north", "south", "east", "west"):
            params.setdefault("direction", action.get("target"))

        out = skills.run(skill_name, self.ctx, params)
        if out.get("status") == "pending":
            return {"success": True, "action": skill_name, "pending": True,
                    "describe": out.get("describe", ""),
                    "result": out.get("detail", {})}
        if out.get("ok"):
            return {"success": True, "action": skill_name,
                    "status": out.get("status", "done"),
                    "describe": out.get("describe", ""),
                    "result": out.get("detail", {})}
        return {"success": False, "action": skill_name,
                "reason": out.get("reason") or "skill_failed",
                "describe": out.get("describe", ""),
                "result": out.get("detail", {})}

    def _communicate(self, action, params):
        text = str(params.get("text") or action.get("params", {}).get("text") or "").strip()
        if not text:
            return {"success": False, "action": "communicate", "reason": "no_text"}
        # B5/§5：投递走通道层统一出口（不绕过 ChannelHub）。节流/失败如实
        # 返回 reason="say_failed"+detail——没送出去绝不报"已送达"；只有
        # 通道本身不存在/被停用时才兜底 bridge.say（兜底同样如实）。
        hub = getattr(self, "channel_hub", None)
        if hub is not None:
            try:
                r = hub.send_text("minecraft_chat", text) or {}
            except Exception as e:
                r = {"ok": False, "reason": "channel_error",
                     "detail": str(e)[:100]}
            reason = str(r.get("reason") or "")
            if r.get("ok"):
                return {"success": True, "action": "communicate",
                        "result": f"said:{text[:40]}",
                        "via": "channel_hub",
                        "delivery_detail": str(r.get("detail") or "")[:120]}
            if reason not in ("channel_unavailable", "channel_disabled",
                               "channel_error"):
                return {"success": False, "action": "communicate",
                        "reason": "say_failed",
                        "delivery_detail": str(r.get("detail") or reason)[:120]}
        if not bridge.say(text):
            return {"success": False, "action": "communicate", "reason": "say_failed"}
        return {"success": True, "action": "communicate",
                "result": f"said:{text[:40]}", "via": "bridge_direct"}

    # ── 动作回执（真实结果） ──────────────────────────────

    def poll_action(self) -> dict:
        """取当前技能的真实回执：running / done / failed + 明细。"""
        if not self.ctx.session.get("skill"):
            return {"status": "done"}
        out = poll_skill(self.ctx)
        st = out.get("status")
        if st == "pending":
            return {"status": "running", "detail": out.get("detail", {}),
                    "describe": out.get("describe", "")}
        if st in ("done", "partial"):
            return {"status": st, "detail": out.get("detail", {}),
                    "describe": out.get("describe", "")}
        if st == "failed":
            return {"status": "failed",
                    "reason": out.get("reason") or "skill_failed",
                    "detail": out.get("detail", {}),
                    "describe": out.get("describe", "")}
        return {"status": "running", "detail": out.get("detail", {})}

    def cancel(self) -> dict:
        """取消当前动作：停技能 + 停控制状态 + 取消寻路 + 解除跟随。"""
        ok = True
        try:
            cancel_skill(self.ctx)
        except Exception as e:
            logger.warning(f"[MCEmbodiment] 技能取消异常: {e}")
        try:
            ok = bridge.stop() and ok
            bridge.stop_goto()
            bridge.stop_combat()
            if self._followed:
                bridge.stopfollow()
                self._followed = None
        except Exception as e:
            logger.warning(f"[MCEmbodiment] cancel 异常: {e}")
            ok = False
        return {"ok": ok}
