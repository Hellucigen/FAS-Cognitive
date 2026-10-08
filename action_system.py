# action_system.py — Action 节点系统（认知状态 → 具身出口的唯一闸门）
# ============================================================================
# 定位（具身改造规范 §五/§八/§九/§十五）：
#
#   认知状态 → 激活 → 动机/需求/奖赏/情绪 → 行为倾向 → 候选 Action
#       → [本模块] ActionNode 竞争 → Skill Layer → Mineflayer
#       → 新环境状态 → 感知事件 → 重新进入认知系统
#
# ActionNode 不是"执行某个工具调用"，它是 FAS 当前认知状态决定
# "我要做什么"的明确出口。每个 Action 携带完整的可解释字段：
#   action_id / action_type / target / priority / motivation /
#   expected_effect / urgency / timeout / interruptible /
#   prerequisite / success_condition / failure_condition / reason
#
# 三个硬机制（§八/§九）：
#   1. Current Action 承诺：动作没结束/失败/被打断之前，不重新选择
#      ——这是连续行为（不再"走两步又重新选"）的架构保证。
#   2. Priority / Interrupt：动作之间竞争。用户指令、生存紧急事件可以
#      按 priority 规则打断当前动作；interruptible=false 的承诺不被打断。
#   3. Timeout：每个动作带时长上限，超时按失败结算（原因=timeout）。
#
# 因果学习接口（§十五）：每个动作在**开始**时写 ACTION 事件（带 reason、
# 当时内部状态、预期效果），**结算**时写结果事件（实际结果 + 奖赏变化）。
# 经验时间轴/CausalLearner 据此学习"我以前为什么做成/没做成"。
#
# 零 LLM：本模块全程确定性。
# ============================================================================

import logging
import threading
import time
import uuid

from graph_model import Node, Edge, now_str
from activity_tracker import ActivityTracker

logger = logging.getLogger(__name__)

# 动作来源（决定中断规则与记录归属）
SRC_USER = "user"          # 用户指令（解析自对话）
SRC_GOAL = "goal"          # 用户复杂指令的后续步骤（目标队列）
SRC_AUTONOMY = "autonomy"  # 自主认知产生
SRC_SURVIVAL = "survival"  # 生存紧急（不可被普通动作打断）

# 生存紧急动作类型：一旦执行，只有更高级的生存事件能打断
# （2026-09-21 具身图谱化）SURVIVAL_TYPES 行为类别已删除——生存行动的
# 调度优势来自图上概念属性（preempts/priority/urgency 数据）与
# EmbodiedStateMapper 的生存压力激活，不再是代码类别。

# 优先级基线（动作类型的先验紧急度；候选评分可在其上叠加）
BASE_PRIORITY = {
    # 执行安全兜底优先级（不是能力语义——概念/能力的默认优先级在图节点
    # 属性 priority 上，normalize_action 优先读图；此表退为最后兜底）。
    # 幽灵键清理（能力图谱 2026-09-20）：come_here/build_shelter/wait
    # 无对应技能/候选且零消费方，删除。
    "seek_safety": 0.95, "retreat": 0.95, "escape_water": 0.9,
    "avoid_lava": 0.9, "eat_food": 0.85, "recover_health": 0.8,
    "stop": 0.9, "follow_entity": 0.75,
    "sleep": 0.7, "gather_resource": 0.5, "collect_food": 0.6,
    "hunt_animal": 0.5, "attack_entity": 0.55, "craft_item": 0.45,
    "smelt_item": 0.45, "equip_item": 0.4, "explore_area": 0.35,
    "explore_direction": 0.35, "explore_unknown_region": 0.35,
    "investigate_location": 0.4, "return_to_location": 0.5,
    "navigate_home": 0.5, "observe_target": 0.4, "inspect_area": 0.3,
    "inspect_block": 0.3, "look_at": 0.3, "walk_to": 0.4,
    "collect_dropped_item": 0.45, "place_block": 0.4,
    "place_torch": 0.4, "remember_location": 0.3, "rest": 0.3,
    "communicate": 0.5, "chop_tree": 0.5, "mark_location": 0.3,
}

# 旧语义意图 → 技能名（兼容 MinecraftEmbodiment 的 8 个旧意图；冻结）：
# 只在 spec 缺 action_type 时兜底翻译，不是对 intent 的白名单裁决——
# 未命中的 intent 词会原样透传（由能力面在出口如实报告能不能做）。
LEGACY_INTENT_MAP = {
    "observe": "inspect_area", "approach": "navigate_to_entity",
    "follow": "follow_entity", "explore": "explore_direction",
    "collect": "gather_resource", "rest": "stop", "withdraw": "retreat",
    "communicate": "communicate",
}

DEFAULT_TIMEOUT = 120.0
# B8 真机实锤（"一起走"→follow_entity 被管理器 120s 看门狗杀成失败）：
# 持续型技能（跟随/警戒——技能自声明 sustained=True）由技能自己判终，
# 管理器上限只是兜底，放 660s = 技能内部 600s + 60s 余量，
# 让"时间到，停下了"（成功语义）先于"timeout"（失败语义）发生。
SUSTAINED_TIMEOUT = 660.0


def _concept_of(action_type: str) -> str:
    """action_type → 图谱行动概念 id 反查（软依赖：概念层缺席/新增
    动作不在表里时返回 ""，执行与学习链路都不受影响）。"""
    try:
        from action_concepts import EXECUTOR_TO_CONCEPT
        return EXECUTOR_TO_CONCEPT.get(action_type, "")
    except Exception:
        return ""


def normalize_action(spec: dict, source: str = SRC_AUTONOMY,
                     kg=None, sustained_names=None) -> dict:
    """把任意来源的动作描述补全为标准 ActionNode（缺省字段显式赋值）。

    priority 取值链（能力图谱 2026-09-20）：显式 spec.priority
    > 行动概念节点属性 priority（图上数据）> BASE_PRIORITY 兜底。
    """
    action_type = str(spec.get("action_type") or spec.get("type")
                      or LEGACY_INTENT_MAP.get(spec.get("intent"), "")
                      or spec.get("intent") or "").strip()
    if not action_type:
        # 不伪造 action（§23）：缺 action_type 的 spec 交由 propose 拒绝，
        # 而不是被猜测兜底成 inspect_area
        action_type = ""
    concept = str(spec.get("concept") or "") or _concept_of(action_type)
    p = spec.get("priority")
    if p is None and concept and kg is not None:
        try:
            cnode = kg.nodes.get(concept)
            cp = (cnode.extra_attrs or {}).get("priority") \
                if cnode is not None else None
            if cp is not None:
                p = cp
        except Exception:
            pass
    if p is None:
        p = BASE_PRIORITY.get(action_type, 0.4)
    urgency = spec.get("urgency")
    if urgency is None:
        urgency = p
    return {
        "action_id": spec.get("action_id") or f"act_{uuid.uuid4().hex[:8]}",
        "action_type": action_type,
        # 行动概念归属（2026-09-20 行动重构）：显式 concept 优先，
        # 否则经 executor 反查图谱概念；查不到=不归属（学习走旧映射兜底）
        "concept": concept,
        "target": spec.get("target"),
        "params": dict(spec.get("params") or {}),
        "priority": max(0.0, min(1.0, float(p))),
        "motivation": str(spec.get("motivation") or "cognitive_state"),
        "expected_effect": str(spec.get("expected_effect") or ""),
        "urgency": max(0.0, min(1.0, float(urgency))),
        "timeout_s": float(spec.get("timeout_s")
                           or (SUSTAINED_TIMEOUT
                               if str(action_type) in (sustained_names or ())
                               else DEFAULT_TIMEOUT)),
        "interruptible": bool(spec.get("interruptible", True)),
        "prerequisite": str(spec.get("prerequisite") or ""),
        "success_condition": str(spec.get("success_condition") or ""),
        "failure_condition": str(spec.get("failure_condition") or ""),
        "reason": list(spec.get("reason") or []),
        "source": source,
        "score": spec.get("score"),
        "explain": spec.get("explain") or "",
        "created": now_str(),
    }


class ActionManager:
    """当前动作生命周期 + 优先级中断 + 目标队列 + 因果记录。

    所有具身动作（用户指令的、自主产生的、生存紧急的）都从这里过：
    propose() 决定"能不能开始/要不要打断"，tick() 推进与结算。
    具体执行交给注册的具身环境（MinecraftEmbodiment → Skill Registry）。
    """

    def __init__(self, embodiment=None, kg=None, engine=None, config=None,
                 internal_state=None, reward_system=None, disposition_store=None,
                 persona=None, timeline=None, causal=None, llm_budget=None,
                 save_graph_fn=None, note_outcome_fn=None, data_dir="data"):
        self.embodiment = embodiment
        self.kg = kg
        self.engine = engine
        self.config = config or {}
        self.internal_state = internal_state
        self.reward_system = reward_system
        self.disposition_store = disposition_store
        self.persona = persona
        self.timeline = timeline
        self.causal = causal
        self.llm_budget = llm_budget
        self._save_graph_fn = save_graph_fn
        self._note_outcome_fn = note_outcome_fn

        cfg = self.config.setdefault("action_system", {})
        cfg.setdefault("interrupt_margin", 0.12)     # 打断所需优先级差
        cfg.setdefault("emergency_health", 8)        # 血量低于此 = 生存紧急
        cfg.setdefault("danger_distance", 5.0)       # 敌对生物此距离内 = 紧急
        cfg.setdefault("max_goal_queue", 12)
        self.cfg = cfg

        # 行动侧事件框架（C21，2026-09-28）：config.py 同名单小节，默认值
        # 兜底注入（同 action_system 小节先例）。enabled=False=零落图零点火。
        _evf_defaults = {
            "enabled": False, "success_side": True,
            "failure_polarity": -0.8, "success_polarity": 0.5,
            "result_polarity": 0.9, "reign_amt": 0.8, "activation_cap": 3.0,
            "ignition": True, "retire_unknown": False,
            "suppress_gap_anchor": False, "flip_cooldown_s": 0,
        }
        self.evf = self.config.setdefault(
            "experience_eventframe", dict(_evf_defaults))
        for _k, _v in _evf_defaults.items():
            self.evf.setdefault(_k, _v)

        self._lock = threading.RLock()
        self._event_frame_nid = None   # C21：最近一次结算的事件框架节点（引擎点火用）
        self.current = None          # 当前 ActionNode（承诺期）
        self._started_at = 0.0
        self._goal_queue = []        # 用户复杂指令的后续步骤
        self._goal_ctx = None        # 当前任务在自我图谱的目标节点 id（2026-09-21）
        self._recent = []            # 最近结算的 ActionNode（环）
        self._stats = {"started": 0, "success": 0, "failed": 0, "cancelled": 0,
                       "interrupted": 0}
        self._last_percept_state = {}   # 生存差分用（血量/威胁）
        self._last_action_nid = None    # 最近一条行动留痕（时间顺序链用）
        self.on_settled = None          # 回调 (action, result, success)：自主层用它回写 recency/失败计数
        self.revalidate = None          # 回调 (action)->action|None：队列候选出队执行前的世界状态重验（P1b）
        # 当前活动（CurrentActivity）认知结构：action 是执行层事件，
        # activity 是认知层"我正在做什么"——见 activity_tracker.py
        self.activity = ActivityTracker(kg, engine) if kg is not None else None

    # ── 状态查询 ──────────────────────────────────────────

    def busy(self) -> bool:
        return self.current is not None

    def status(self) -> dict:
        with self._lock:
            return {
                "current": dict(self.current) if self.current else None,
                "current_elapsed_s": (round(time.time() - self._started_at, 1)
                                      if self.current else 0),
                "goal_queue": list(self._goal_queue),
                "recent": list(self._recent[-8:]),
                "stats": dict(self._stats),
            }

    # ── 提交动作（所有来源的唯一入口）─────────────────────

    def propose(self, spec: dict, source: str = SRC_AUTONOMY,
                now: float = None) -> dict:
        """提交一个 ActionNode。

        返回 {"started": bool, "queued": bool, "interrupted": bool,
              "reason": str, "result": 执行结果(若立即启动)}
        决策规则（可解释）：
          - 空闲 → 立即开始
          - 忙：新动作是用户/目标来源且 priority ≥ 当前 + margin
                或当前可打断且新动作 priority 更高 → 打断并开始
          - 忙：新动作是目标 → 进队列
          - 否则 → 拒绝（当前动作承诺期内）
        """
        now = time.time() if now is None else now
        if getattr(self, "_sustained_names", None) is None:
            try:
                self._sustained_names = {
                    str(s.get("name")) for s in self.embodiment.skill_catalog()
                    if s.get("sustained")}
            except Exception:
                self._sustained_names = set()
        action = normalize_action(spec, source=source, kg=self.kg,
                                  sustained_names=self._sustained_names)
        # 观测：动作候选诞生（action_id 贯穿 候选→选中→开始→结算）
        import fas_log
        fas_log.get_logger(fas_log.ACTION).info(
            "action_proposed", f"动作候选 {action['action_type']}",
            action_id=action["action_id"], action_type=action["action_type"],
            target=action.get("target"), priority=action.get("priority"),
            source=source, motivation=str(action.get("motivation") or "")[:80])
        # Safety Kernel 前置守卫（2026-09-21 具身图谱化）：只检查不可逾越
        # 的执行安全不变量（资产授权/攻击对象核验/防抖），不裁决行为价值。
        kernel = getattr(self, "kernel", None)
        if kernel is not None:
            try:
                ok_g, why_g, granted = kernel.guard(action, source, now=now)
            except Exception as e:
                # fail-open 保持（守卫自身故障不该卡死所有行为），但必须留痕：
                # 静默放行=安全守卫事实上消失（§14，2026-09-26 离线验收）
                logger.warning(f"[Action] kernel guard 异常，本次放行: {e!r}")
                ok_g, why_g, granted = True, "", False
            if not ok_g:
                logger.info(f"[Action] kernel 拒绝 {action['action_type']}"
                            f"@{action.get('target')}: {why_g}")
                import fas_log
                fas_log.get_logger(fas_log.ACTION).info(
                    "action_rejected", "Safety Kernel 拒绝",
                    action_id=action["action_id"], reason=str(why_g)[:120],
                    stage="kernel")
                return {"started": False, "queued": False, "interrupted": False,
                        "reason": why_g, "result": None}
            if granted:
                action.setdefault("params", {})["_kernel_granted"] = True
        if not action["action_type"]:
            return {"started": False, "queued": False, "interrupted": False,
                    "reason": "missing_action_type", "result": None}
        with self._lock:
            cur = self.current
            if cur is None:
                out = self._start(action, now)
                return {"started": True, "queued": False, "interrupted": False,
                        "action_id": action["action_id"], **out}
            margin = float(self.cfg["interrupt_margin"])
            new_p = action["priority"]
            cur_p = float(cur.get("priority", 0.5))
            user_boost = source in (SRC_USER, SRC_SURVIVAL)
            # 2026-09-21：SURVIVAL_TYPES 类别抢占拆除——"谁能压过谁"
            # 是认知产出的数值（概念 priority/urgency 数据 + 图压力驱动
            # 的评分），调度器只看优先级差；图上标了 preempts 的概念
            # （寻安全/撤离）享有 −margin 宽减（数据读图，不是代码名单）。
            preempts = self._concept_preempts(action)
            should_interrupt = (
                cur.get("interruptible", True)
                and ((user_boost or preempts) and new_p >= cur_p - margin
                     or (new_p >= cur_p + margin)))
            if should_interrupt:
                cancel_info = self._cancel_current(
                    f"被更高优先级动作打断: {action['action_type']}", now,
                    interrupted=True)
                out = self._start(action, now)
                return {"started": True, "queued": False, "interrupted": True,
                        "cancel": cancel_info, "action_id": action["action_id"], **out}
            if source in (SRC_GOAL, SRC_USER):
                if len(self._goal_queue) < int(self.cfg["max_goal_queue"]):
                    self._goal_queue.append(action)
                    import fas_log
                    fas_log.get_logger(fas_log.ACTION).info(
                        "action_queued", "排队等待当前动作完成",
                        action_id=action["action_id"],
                        queue_len=len(self._goal_queue),
                        blocked_by=str(cur.get("action_type")))
                    return {"started": False, "queued": True,
                            "interrupted": False,
                            "reason": "当前动作完成后执行",
                            "action_id": action["action_id"]}
            _rej = (f"当前动作 {cur['action_type']} 承诺期内"
                    f"（priority {cur_p:.2f} vs {new_p:.2f}）")
            import fas_log
            fas_log.get_logger(fas_log.ACTION).info(
                "action_rejected", _rej, action_id=action["action_id"],
                stage="scheduler", reason=_rej,
                blocked_by=str(cur.get("action_type")))
            return {"started": False, "queued": False, "interrupted": False,
                    "reason": _rej,
                    "action_id": action["action_id"]}

    # ── 主循环 tick（由 CC 认知循环驱动；零 LLM、不阻塞）──

    def tick(self, now: float = None) -> dict:
        now = time.time() if now is None else now
        try:
            return self._tick(now)
        except Exception as e:
            logger.warning(f"[Action] tick 异常: {e}")
            return {"acted": False, "error": str(e)}

    def _tick(self, now: float) -> dict:
        if self.current is None:
            # 空闲：目标队列里有用户的后续意图 → 依次执行
            nxt = None
            with self._lock:
                if self._goal_queue:
                    nxt = self._goal_queue.pop(0)
            if nxt is not None:
                # §P1b（2026-09-27 收敛修复）：排队等待 ≠ 世界冻结。pop 到
                # 执行之间可能隔了多个决策拍，执行前重验一次候选依赖的关键
                # 世界状态；重验器（autonomy 挂的钩子）返回 None = 丢弃该
                # 候选（条件已不成立的步骤不执行）；异常/未挂 = fail-open。
                revalidate = getattr(self, "revalidate", None)
                if callable(revalidate):
                    try:
                        nxt = revalidate(nxt)
                    except Exception as e:
                        logger.warning(f"[Action] 重验异常，按原单执行: {e!r}")
                if nxt is None:
                    return {"acted": False, "reason": "revalidated_dropped"}
                self._start(nxt, now, source=nxt.get("source", SRC_GOAL))
                return {"acted": True, "started_goal": nxt["action_type"],
                        "target": nxt.get("target")}
            if self._goal_ctx:
                # 队列排空且无在飞动作 = 任务全部步骤跑完 → 摘当前目标
                self._end_goal_context(achieved=True)
            return {"acted": False, "reason": "idle"}

        # 1) 濒死停手（2026-09-21 Safety Kernel K4，唯一数值兜底）：
        #    HP 跌破不可逾越线 → 无条件停止占用动作。不指定去哪——
        #    逃/吃/回血由认知在生存压力下选择（旧的"HP≤8 强制 seek_safety、
        #    贴脸名单强制 retreat"是代码替认知做决定，已拆除）。
        kernel = getattr(self, "kernel", None)
        if kernel is not None:
            try:
                if kernel.death_floor_check(self.embodiment.raw_state(),
                                            self.current):
                    logger.info("[Action] 濒死停手（kernel K4）")
                    self._cancel_current("濒死停手（kernel）", now,
                                         interrupted=True)
                    return {"acted": True,
                            "interrupted_by": "death_floor_stop"}
            except Exception as e:
                # K4 是保命闸：检查本身抛错≠安全，静默=没有濒死停手（§14）
                logger.warning(f"[Action] death_floor_check 异常，本轮无兜底: {e!r}")

        # 2) 超时检查（收尾修复：旧版 _cancel_current 结算一遍 + 行尾再
        #    _settle 一遍 = 同一动作双留痕双回调，autonomy 失败计数翻误增）
        if now - self._started_at > float(self.current.get("timeout_s", DEFAULT_TIMEOUT)):
            self._cancel_current("动作超时", now, interrupted=False,
                                 settle_result={"success": False,
                                                "reason": "timeout",
                                                "cancelled": False})
            return {"acted": True, "settled": "timeout"}

        # 3) 轮询在飞动作的真实结果
        if self.embodiment is None:
            return {"acted": False, "reason": "no_embodiment"}
        try:
            status = self.embodiment.poll_action() or {}
        except Exception as e:
            status = {"status": "failed", "reason": f"poll_error: {e}"}
        st = str(status.get("status", "")).lower()
        if st in ("running", "pending", ""):
            return {"acted": False, "reason": "acting",
                    "action": self.current.get("action_type")}
        action = self.current
        ok = st in ("done", "partial")
        result = {"success": ok, "action": action["action_type"], "status": st,
                  "result": status.get("detail", status),
                  "describe": status.get("describe", ""),
                  "reason": "" if ok else status.get("reason") or st}
        if st == "cancelled":
            result["cancelled"] = True
        with self._lock:
            self.current = None
        self._settle(action, result, self._started_at, now)
        return {"acted": True, "settled": "done" if ok else "failed",
                "action": action["action_type"]}

    # ── 调度元数据读图（2026-09-21）────────────────────────

    def _concept_preempts(self, action: dict) -> bool:
        """该动作所属行动概念是否在图上标注 preempts（撤离/寻安全等）。
        数据来自 capability_graph 概念节点属性，不是代码行为名单——
        调度器只消费元数据，不裁决行为价值。"""
        concept = str(action.get("concept") or "")
        if not concept or self.kg is None:
            return False
        try:
            with self.kg._lock:
                node = self.kg.nodes.get(concept)
            return bool((node.extra_attrs or {}).get("preempts")) \
                if node is not None else False
        except Exception:
            return False

    # ── 启动/取消/结算 ────────────────────────────────────

    def _start(self, action: dict, now: float, source: str = None) -> dict:
        if source:
            action["source"] = source
        if self.embodiment is None:
            result = {"success": False, "action": action["action_type"],
                      "reason": "no_embodiment"}
            self._settle(action, result, now, now)
            return {"success": False, "reason": "no_embodiment"}
        # ACTION 事件先入轴（§三：动作开始 = 事件；后续观察才有资格成为结果）
        self._emit_action_event(action)
        # 当前活动挂接：activity ≠ action——同族动作归入同一活动节点
        self._track_activity_start(action)
        # started 在"动作真的开始执行"处计一次（同步/挂起都算）。旧版只在
        # pending 分支加计数：同步成功的 craft/place 结算 success 却不进
        # started，账目恒等式 success+failed+cancelled+interrupted==started
        # 被打破（2026-09-26 离线验收 E5 抓到）。
        with self._lock:
            self._stats["started"] += 1
        try:
            result = self.embodiment.execute(action) or {}
        except Exception as e:
            result = {"success": False, "action": action["action_type"],
                      "reason": f"execution_error: {e}"}
        if result.get("pending"):
            with self._lock:
                self.current = action
                self._started_at = now
            logger.info(f"[Action] 开始 {action['action_type']}@{action.get('target')} "
                        f"src={action['source']} p={action['priority']:.2f} "
                        f"motivation={action['motivation']}")
            import fas_log
            fas_log.get_logger(fas_log.ACTION).info(
                "action_started", f"开始 {action['action_type']}",
                action_id=action["action_id"], action_type=action["action_type"],
                target=action.get("target"), source=action.get("source"),
                priority=action.get("priority"),
                describe=str(result.get("describe", ""))[:120])
            return {"success": True, "pending": True,
                    "describe": result.get("describe", ""),
                    "reason": result.get("reason", "")}
        # 同步完成/失败
        self._settle(action, result, now, now)
        return {"success": bool(result.get("success")),
                "describe": result.get("describe", ""),
                "reason": result.get("reason", "")}

    def _cancel_current(self, reason: str, now: float, interrupted: bool,
                        settle_result: dict = None) -> dict:
        """停止在飞动作并**恰好结算一次**。

        settle_result=None → 常规取消（不算人格证据，不记成败）。
        传入自定义 result → 用该结果结算（超时路径：cancelled=False，
        走一次正常的失败结算——统计/留痕/回调都只一遍）。
        """
        action = self.current
        if action is None:
            return {"ok": False}
        try:
            if self.embodiment is not None:
                self.embodiment.cancel()
        except Exception as e:
            logger.warning(f"[Action] 取消执行失败: {e}")
        with self._lock:
            self.current = None
        if settle_result is None:
            result = {"success": False, "action": action["action_type"],
                      "reason": f"cancelled: {reason}", "cancelled": True,
                      "interrupted": bool(interrupted)}
        else:
            result = dict(settle_result)
            result.setdefault("action", action["action_type"])
        self._settle(action, result, self._started_at, now,
                     skip_disposition=(settle_result is None))
        logger.info(f"[Action] 取消 {action['action_type']}: {reason}")
        return {"ok": True, "action": action["action_type"], "reason": reason}

    def cancel(self, reason: str = "外部取消") -> dict:
        with self._lock:
            return self._cancel_current(reason, time.time(), interrupted=False)

    def _settle(self, action: dict, result: dict, started: float, now: float,
                skip_disposition: bool = False) -> dict:
        """动作结算：结果事件 + 奖赏 + 倾向证据 + 图谱留痕。"""
        success = bool(result.get("success"))
        duration = round(now - started, 2)
        result = dict(result)
        result["duration_s"] = duration
        with self._lock:
            # 统计单点归属（2026-09-22 收尾修复）：_cancel_current 不再自行
            # 加计数，否则取消=双计、超时=三计（stats/_recent/留痕全翻倍）。
            if result.get("cancelled"):
                self._stats["interrupted" if result.get("interrupted")
                            else "cancelled"] += 1
            else:
                self._stats["success" if success else "failed"] += 1
            self._recent.append({
                "action": action["action_type"], "target": action.get("target"),
                "success": success, "reason": result.get("reason"),
                "duration_s": duration, "source": action.get("source"),
                "ts": now_str()})
            if len(self._recent) > 40:
                del self._recent[:-40]
        self._emit_result_event(action, result, success)
        self._write_action_memory(action, result, success)
        self._track_activity_settle(result, success)
        if not skip_disposition:
            self._apply_reward(action, result, success)
        if self.on_settled is not None:
            try:
                try:
                    self.on_settled(action, result, success, now)
                except TypeError:
                    self.on_settled(action, result, success)   # 旧三参回调兼容
            except Exception as e:
                logger.debug(f"[Action] on_settled 回调失败: {e}")
        # 具身世界事件协议钩子（B2）：执行器把本次动作的真实回执翻译成
        # 环境事实（如 dig/place → 含坐标的方块变化事件）。通用协议、
        # 非环境专用 if——具身没实现就什么也不发生；绝不在此写 MC 语义。
        _emb_hook = getattr(self.embodiment, "on_settled", None)
        if callable(_emb_hook):
            try:
                _emb_hook(action, result, success)
            except Exception as e:
                logger.debug(f"[Action] 具身世界事件钩子跳过: {e}")
        # 认知投递回执（B5/§5）：注入了 self.cc 时，让认知层自己认领
        # 与 action_id 相关的表达回执（delivered/say_failed）。通用协议
        # 钩子——cc 没有匹配的 CI 就静默返回，不在此做任何语义判断。
        _cc = getattr(self, "cc", None)
        _note = getattr(_cc, "note_delivery", None) if _cc is not None else None
        if callable(_note):
            try:
                _note(action.get("action_id"), result, success)
            except Exception as e:
                logger.debug(f"[Action] 投递回执回调跳过: {e}")
        logger.info(f"[Action] 结算 {action['action_type']}@{action.get('target')} "
                    f"{'成功' if success else '失败'}"
                    f"{'（' + str(result.get('reason')) + '）' if not success else ''} {duration}s")
        import fas_log
        # 技能证据在两条结算路径下落点不同：embodiment.execute 直返放
        # result["detail"]，看门狗/轮询路径（L390）把回执 detail 嵌在
        # result["result"]。两处都取，B8 真机 stuck 样本实证过只读前者会恒 None。
        _det = result.get("detail")
        if not isinstance(_det, dict):
            _det = result.get("result") if isinstance(result.get("result"), dict) else None
        fas_log.get_logger(fas_log.ACTION).info(
            "action_cancelled" if result.get("cancelled") else "action_settled",
            f"{action['action_type']} "
            + ("取消" if result.get("cancelled") else ("成功" if success else "失败")),
            action_id=action.get("action_id"), action_type=action["action_type"],
            target=action.get("target"), success=success,
            cancelled=bool(result.get("cancelled")),
            reason=str(result.get("reason") or "")[:160],
            # B8：结算全文带技能回执证据（3D/坐标），供 §24 trace 事后取证
            detail={k: v for k, v in _det.items()
                    if k in ("dist3d", "dy", "position", "entity",
                             "target_last", "last_target", "target")}
            if isinstance(_det, dict) else None,
            duration_s=duration, source=action.get("source"))
        return result

    # ── 当前活动挂接（activity_tracker）──────────────────────

    def _track_activity_start(self, action: dict):
        if self.activity is None:
            return
        try:
            reason = action.get("motivation") or action.get("explain") \
                or " ".join(str(r) for r in (action.get("reason") or [])[:3])
            self.activity.start(
                action["action_type"],
                target=action.get("target"),
                reason=str(reason or "")[:120],
                source=str(action.get("source") or ""),
            )
        except Exception as e:
            logger.debug(f"[Activity] 活动挂接失败（不影响行动）: {e}")

    def _track_activity_settle(self, result: dict, success: bool):
        if self.activity is None:
            return
        try:
            if result.get("cancelled"):
                # 打断/取消不由本次结算定性——后续活动切换时收尾
                return
            self.activity.settle(success)
        except Exception as e:
            logger.debug(f"[Activity] 活动结算失败: {e}")

    # ── 因果记录（§十五：为什么做 / 预期 / 实际 / 奖赏变化）──

    def _internal_state_snapshot(self) -> dict:
        snap = {}
        if self.internal_state is not None:
            try:
                st = self.internal_state.state()
                snap = {"health_mods": {k: v.get("level") for k, v in
                                        (st.get("modulators") or {}).items()},
                        "needs": {k: v.get("level") for k, v in
                                  (st.get("needs") or {}).items()}}
            except Exception:
                pass
        try:
            if self.embodiment is not None:
                raw = self.embodiment.raw_state() or {}
                snap["game"] = {"health": raw.get("health"), "food": raw.get("food"),
                                "pos": raw.get("position")}
        except Exception:
            pass
        return snap

    def _emit_action_event(self, action: dict):
        if self.timeline is None:
            return
        try:
            from experience import EVENT_ACTION, make_event
            evt = make_event(
                EVENT_ACTION, actor="self", subject=str(action["action_type"]),
                content={
                    "target": action.get("target"),
                    "params": action.get("params", {}),
                    "reason": action.get("reason", []),
                    "motivation": action.get("motivation"),
                    "expected_effect": action.get("expected_effect"),
                    "urgency": action.get("urgency"),
                    "priority": action.get("priority"),
                    "source": action.get("source"),
                    "internal_state": self._internal_state_snapshot(),
                    "explain": action.get("explain", ""),
                },
                source="action_system")
            self.timeline.append(evt)
            if self.causal is not None:
                self.causal.record_action(evt)
        except Exception as e:
            logger.debug(f"[Action] ACTION 事件入轴跳过: {e}")

    def _emit_result_event(self, action: dict, result: dict, success: bool):
        if self.timeline is None:
            return
        try:
            from experience import EVENT_SELF_STATE, make_event
            from reward import classify_self_outcome, SELF_VALENCE
            self_outcome = classify_self_outcome(
                action["action_type"], success, result=result,
                target_is_unknown=self._target_is_unknown(action))
            valence = SELF_VALENCE.get(self_outcome, 0.0)
            reason = str(result.get("reason") or "")
            change = "succeeded" if success else f"failed:{reason}" if reason else "failed"
            evt = make_event(
                EVENT_SELF_STATE, actor="self", subject=str(action["action_type"]),
                content={"change": change,
                         "reason": reason,
                         "expected_effect": action.get("expected_effect"),
                         "outcome": self_outcome,
                         "reward_change": round(valence, 3)},
                source="action_system")
            self.timeline.append(evt)
        except Exception as e:
            logger.debug(f"[Action] 结果事件入轴跳过: {e}")

    @staticmethod
    def _target_is_unknown(action: dict) -> bool:
        for b in (action.get("reason") or []):
            if str(b).startswith(("UnknownEntity_", "UnknownPlayer_",
                                  "UnknownBlock_", "UnknownConcept_")):
                return True
        return False

    # ── 奖赏/倾向（复用既有链路；与 autonomy 旧路径语义一致）──

    _DISPOSITION_INTENT_MAP = {
        "observe": "explore", "inspect_area": "explore",
        "inspect_block": "explore", "observe_target": "explore",
        "navigate_to_entity": "explore", "explore_area": "explore",
        "explore_direction": "explore", "explore_unknown_region": "explore",
        "gather_resource": "explore", "chop_tree": "explore",
        "follow_entity": "share", "communicate": "share",
    }

    # 性格慢学习映射（Phase 5 补账）：行动结果 → trait 证据。
    # (action 族, self_outcome) → {trait: 证据方向}；α=0.005/次，band±0.15——
    # 一次经历动不了性格，长期模式才动。族按动作类型前缀粗分（数据表非 if 链）。
    _TRAIT_FAMILY = {
        "explore": {"observe", "inspect_area", "inspect_block", "inspect_entity",
                    "approach", "navigate_to_entity", "look_at"},
        "novelty": {"explore_area", "explore_direction", "explore_unknown_region",
                    "investigate_location", "revisit_location"},
        "resource": {"gather_resource", "chop_tree", "gather_wood", "gather_stone",
                     "collect_food", "collect_dropped_item", "craft_item", "smelt_item",
                     "till_soil", "plant_seed", "harvest_crop", "hunt_animal"},
        # B6/§14：follow_entity 是 executor 实名——"跟着人走"是社交行为的
        # 真实输入；旧版挂在 explore 家族，社交倾向慢学习永远收不到它的证据。
        "social": {"follow", "follow_entity", "communicate"},
        "safety": {"retreat", "seek_safety", "escape_water", "avoid_lava"},
    }
    _TRAIT_RULES = {
        # (family, outcome) → [(trait, value)]
        ("novelty", "discovery"):      [("exploration_bias", 1.0), ("novelty_preference", 1.0)],
        ("explore", "goal_success"):   [("exploration_bias", 0.6)],
        ("novelty", "goal_success"):   [("exploration_bias", 0.6), ("novelty_preference", 0.3)],
        ("resource", "goal_success"):  [("resource_thrift", 0.5)],
        ("resource", "goal_failure"):  [("persistence", -0.5)],
        ("social", "goal_success"):    [("social_openness", 0.6)],
        ("social", "goal_failure"):    [("social_openness", -0.4)],
        ("safety", "goal_success"):    [("caution", 0.3)],
        ("*", "blocked"):              [("persistence", 0.2)],   # 被环境挡住仍试过=微弱坚持证据
    }

    def _family_of(self, action_type: str) -> str:
        for fam, members in self._TRAIT_FAMILY.items():
            if action_type in members:
                return fam
        return "*"

    def _learn_traits(self, action: dict, self_outcome: str, success: bool):
        """行动结果 → trait 证据（走 internal_state 唯一写入口）。"""
        ist = getattr(self, "internal_state", None)
        if ist is None or self_outcome in ("cancelled", None):
            return
        try:
            fam = self._family_of(str(action.get("action_type") or ""))
            rules = []
            key = (fam, self_outcome)
            if key in self._TRAIT_RULES:
                rules += self._TRAIT_RULES[key]
            if ("*", self_outcome) in self._TRAIT_RULES:
                rules += self._TRAIT_RULES[("*", self_outcome)]
            ref = f"{action.get('action_type')}@{action.get('target')}"
            for trait, val in rules:
                if val == 0:
                    continue
                ist.record_trait_evidence(trait, val, source="action_outcome",
                                         reason=f"{self_outcome} {ref}")
        except Exception as e:
            logger.debug(f"[Action] trait 慢学习跳过: {e}")

    def _apply_reward(self, action: dict, result: dict, success: bool):
        try:
            if self.persona is not None:
                self.persona.mood_event("positive" if success else "negative")
        except Exception as e:
            logger.debug(f"[Action] mood_event 失败: {e}")
        try:
            if self._note_outcome_fn:
                self._note_outcome_fn(not success)
        except Exception as e:
            logger.debug(f"[Action] note_outcome 失败: {e}")
        # 行动失败 → 反思压力信号（统一认知循环 2026-09-20）：失败不只是
        # 退避计数，它持续累积"为什么没成"的疑问，够重时驱动离线自问
        cc = getattr(self, "cc", None)
        if cc is not None and not success:
            try:
                cc.note_pressure("action_failure")
            except Exception:
                pass
        # 先做 trait 慢学习（与 disposition 同源证据，独立通道：
        # disposition 记的是"行为:情境"倾向，trait 记的是跨情境慢变量）
        try:
            from reward import classify_self_outcome as _cso
            _outcome = _cso(str(action.get("action_type") or ""), success,
                            result=result,
                            target_is_unknown=self._target_is_unknown(action))
            self._learn_traits(action, _outcome, success)
            # R2 P7：行动结果同时是一次调制事件。cancelled 不进人格证据
            # （没完成 ≠ 做错了），但"意图被打断"确实要切换注意——原先它在
            # 激素层完全隐形（场景 14）；blocked 则走急性应急而非挫败。
            rs = self.reward_system
            if rs is not None:
                eng = rs.mod_engine()
                if eng is not None:
                    if _outcome == "cancelled":
                        eng.emit("interrupted", source="action", intensity=1.0,
                                 uncertainty=0.6, goal_relevance=1.0,
                                 ref=str(action.get("action_type") or ""))
                    elif _outcome == "blocked":
                        eng.emit("obstacle_hit", source="action", intensity=0.6,
                                 valence=-0.2, goal_relevance=1.0,
                                 ref=str(action.get("action_type") or ""))
        except Exception as _te:
            logger.debug(f"[Action] trait/调制事件通道跳过: {_te}")
        self._record_disposition_outcome(action, result, success)

    def _record_disposition_outcome(self, action: dict, result: dict,
                                    success: bool):
        """行动结果 → disposition 自我来源人格证据（具名通道：可测、可复用）。

        cancelled 不算证据（没完成 ≠ 做错了）；映射表外的动作不落倾向。
        行动重构（2026-09-20）双粒度学习：粗粒度 explore/share 通道
        原样保留（兼容既有 9 天数据与测试），同时若该动作能反查到
        图谱行动概念（concept 字段），向 `倾向:<concept>@主动发起` 再记
        一条**概念级**证据——"探索"不再吞掉挖矿/观察/采集的差别；
        概念对人格的影响随经验在 情境-[激活]->概念 边上长出来。
        """
        if self.disposition_store is None:
            return
        try:
            from reward import classify_self_outcome
            intent = str(action.get("action_type") or "")
            behavior = self._DISPOSITION_INTENT_MAP.get(intent)
            concept = str(action.get("concept") or "")
            if not behavior and not concept:
                return
            self_outcome = classify_self_outcome(
                intent, success, result=result,
                target_is_unknown=self._target_is_unknown(action))
            if self_outcome == "cancelled":
                return
            ref = f"{intent}@{action.get('target')} {result.get('reason') or result.get('describe') or ''}"
            hormone = None
            rs = self.reward_system
            if rs is not None:
                try:
                    ev = rs.evaluate(behavior=behavior or concept,
                                     context="情境:主动发起",
                                     social_outcome=None, self_outcome=self_outcome,
                                     ref=ref)
                    rel = rs.release(ev)
                    hormone = rs.modulation(
                        key=f"{behavior or concept}@情境:主动发起",
                        surprise=rel.get("surprises", {}).get(f"{behavior or concept}@情境:主动发起"))
                except Exception as e:
                    logger.debug(f"[Action] 奖赏释放跳过: {e}")
            trace = None
            for _bkey in dict.fromkeys([k for k in (behavior, concept) if k]):
                trace = self.disposition_store.apply_experience(
                    _bkey, "情境:主动发起", social_outcome=None,
                    self_outcome=self_outcome, hormone=hormone,
                    evidence_ref=ref, allow_create=True)
            logger.info(f"[Action] 自我奖赏→disposition: {behavior or '-'}"
                        + (f"+{concept}" if concept else "") +
                        f" {self_outcome} "
                        f"sig={trace.get('learning_signal') if trace else '-'}")
        except Exception as e:
            logger.debug(f"[Action] disposition 证据回写失败: {e}")

    # ── 图谱留痕（行动节点进 episodic 空间，参与扩散/反思）──

    def _write_action_memory(self, action: dict, result: dict, success: bool):
        if self.kg is None:
            return
        try:
            _base = f"行动_{int(time.time() * 1000) % 10**9}"
            with self.kg._lock:
                nid = _base
                _i = 1
                while nid in self.kg.nodes:      # 同毫秒双留痕不互踩（撞 id 会静默合并）
                    nid = f"{_base}#{_i}"
                    _i += 1
                self.kg.add_node(Node(
                    id=nid, weight=0.5, label="declarative-episodic",
                    graph_space="episodic",
                    extra_attrs={
                        "type": "embodied_action",
                        "action_type": action["action_type"],
                        "target": action.get("target"),
                        "params": action.get("params", {}),
                        "motivation": action.get("motivation"),
                        "expected_effect": action.get("expected_effect"),
                        "success": success,
                        "reason": str(result.get("reason") or "")[:200],
                        "describe": str(result.get("describe") or "")[:200],
                        "duration_s": result.get("duration_s"),
                        "basis": action.get("reason", []),
                        "source": action.get("source"),
                        "created": now_str(),
                    }))
                for b in (action.get("reason") or [])[:3]:
                    if b in self.kg.nodes:
                        self.kg.add_edge(Edge(src=nid, dst=b, relation="涉及",
                                              weight=0.4,
                                              relation_category="semantic_relation"))
                # 事件→对象边（2026-09-23 记忆审计修复）：此前动作痕迹只连
                # "她在想什么"（依据），不连"她对谁做了什么"——对象节点反
                # 扩散回不到自己的历史经历。与对话侧 事件-[涉及]->实体 同构；
                # 只连已存在节点，不为缺失对象造新节点（防噪声）。
                _prm = action.get("params") or {}
                for _tgt in {str(action.get("target") or "").strip(),
                             str(_prm.get("item") or "").strip(),
                             str(_prm.get("block") or "").strip()}:
                    if not _tgt:
                        continue
                    for _c in (_tgt, _tgt.lower(),
                               f"物品:{_tgt.lower()}", f"物品:{_tgt}"):
                        if _c in self.kg.nodes:
                            # 类别按关系本体（"涉及"=cognitive_relation，
                            # 扩散方向覆盖为双向：事件↔对象）
                            self.kg.add_edge(
                                Edge(src=nid, dst=_c, relation="涉及",
                                     weight=0.5,
                                     relation_category="cognitive_relation"))
                            break
                # 时间链：连续两次具身留痕接 时间顺序（既有时序扩散规则），
                # "我刚才做了什么→接着做了什么"成为可沿走的序列；重启后从头
                # 接（链头不落盘，跨进程断点无害，节点 ts 仍可排序还原）。
                if (self._last_action_nid
                        and self._last_action_nid != nid
                        and self._last_action_nid in self.kg.nodes):
                    self.kg.add_edge(
                        Edge(src=self._last_action_nid, dst=nid,
                             relation="时间顺序", weight=0.8,
                             relation_category="temporal_relation"))
                self._last_action_nid = nid
                # C21 事件框架（签名级 + 极性槽位边，2026-09-28）：同一锁帧内
                # 落图，引擎免扣点火在锁外段。故障绝不破坏实例留痕。
                try:
                    self._event_frame_nid = self._write_event_frame(
                        action, result, success)
                except Exception as e:
                    self._event_frame_nid = None
                    logger.debug(f"[Action] 事件框架落图跳过: {e}")
                # 行动概念溯源边（2026-09-20 行动重构）：事件痕迹 → 概念
                # （行动_*-[实施]->概念，episodic 前向；供回溯与
                # "该概念被做过"的生命周期/证据观察，不改概念定义）
                _cnode = str(action.get("concept") or "")
                if _cnode and _cnode in self.kg.nodes:
                    self.kg.add_edge(Edge(
                        src=nid, dst=_cnode, relation="实施", weight=0.4,
                        relation_category="episodic_relation"))
            # 能力溯源（2026-09-20 能力图谱）：概念→能力的 learned_from
            # 挂本次行动痕迹节点（"这个能力是怎么长出来的"可回答）。
            # B6/§12：附带结算时刻的条件签名（与 causal context 同源，
            # 从内部状态派生，零 MC 语义）——能力经验按条件记账。
            _capidx = getattr(self, "cap_index", None)
            if _capidx is not None and _cnode:
                _ctx_sig = None
                try:
                    from experience import context_signature
                    _ctx_sig = context_signature({"content": {
                        "internal_state": self._internal_state_snapshot()}})
                except Exception:
                    _ctx_sig = None
                try:
                    _capidx.record_execution(_cnode, str(
                        action.get("action_type") or ""), nid, success,
                        context=_ctx_sig)
                except Exception as e:
                    # 能力经验记账是学习写入，失败必须留痕（§14）
                    logger.warning(f"[Action] 能力溯源记账失败: {e!r}")
            if self.engine is not None:
                node = self.kg.nodes.get(nid)
                if node is not None:
                    with self.engine._lock:
                        self.engine.name_to_node[nid] = node
                    self.engine.mark_active([nid])
                    try:
                        self.engine.activate_from_inputs([nid], [],
                                                         source_type="memory_recall")
                    except Exception as e:
                        logger.debug(f"[Action] 行动留痕激活失败: {e}")
            # C21 事件框架点火（2026-09-28）：结算落图后给事件节点注入激活，
            # 让抑制在下一拍扩散中发行。口径=continuous_cognition._reactivate_field
            # 两件套：手动 activation 赋值（cap=episodic 3.0）+ touch + mark_active
            # + register_activation_source（免扣语义）。**不用 activate_from_inputs**
            # （cap 全局 5.0 会越 episodic 上限且与残值双计）。
            _efid = getattr(self, "_event_frame_nid", None)
            if _efid and self.engine is not None \
                    and self.evf.get("ignition", True):
                _enode = self.kg.nodes.get(_efid)
                if _enode is not None:
                    try:
                        _cap = float(self.evf.get("activation_cap") or 3.0)
                        _reign = float(self.evf.get("reign_amt") or 0.8)
                        _enode.activation = min(
                            _cap, float(_enode.activation or 0.0) + _reign)
                        _enode.touch()
                        with self.engine._lock:
                            self.engine.name_to_node[_efid] = _enode
                        self.engine.mark_active([_efid])
                        self.engine.register_activation_source(
                            [_efid], "memory_recall")
                    except Exception as e:
                        logger.debug(f"[Action] 事件框架点火失败: {e}")
            if self._save_graph_fn:
                self._save_graph_fn()
        except Exception as e:
            logger.warning(f"[Action] 行动留痕失败: {e}")

    # ── 目标队列（复杂指令的后续步骤）─────────────────────

    def _write_event_frame(self, action: dict, result: dict,
                           success: bool) -> str | None:
        """C21（2026-09-28）：失败/成功结算 → 签名级事件框架节点 + 极性槽位边。

        论文愿景 §552-562"事件帧调制认知"的生产落地（沙箱实证 C20e 5/5
        行为翻转、C20g 真实失败回执内源落图独力承担 17→15 差分）。形制：
          `行动经验:{atype}({sig})`（episodic 事件节点，签名级归并重复经历）
             -[涉及 w=成败极性]-> 实体节点（负权=抑制实体，认知关系）
             -[结果 +result_polarity]-> `行动结果:{...}:{succeeded|failed}`
                 （semantic 叶子，恒正撑事件节点 total_w>0 使抑制被发行——
                  diffusion_engine.py:1023 的条件，本方法不碰扩散机制）
        极性翻转靠 graph_model.add_edge(force_weight=True)（覆盖异号）。
        失败**不按暂态集合豁免**（C20g 关键证据：timeout 暂态时统计/冷却/
        因果通道全静默，图侧是唯一留痕通道）。cancelled 不落（取消不是
        关于对象的证据，同 _track_activity_settle 不定性语义）。

        调用方约定：必须在 kg._lock 内调用（_write_action_memory 同帧）。
        本方法独立 try/except 由调用方包裹——事件框架故障绝不破坏实例留痕。
        """
        evf = self.evf
        if not evf or not evf.get("enabled"):
            return None
        if result.get("cancelled"):
            return None
        _prm = action.get("params") or {}
        sig = str(action.get("target") or _prm.get("item")
                  or _prm.get("block") or "").strip()
        atype = str(action.get("action_type") or "action")
        eid = f"行动经验:{atype}({sig})" if sig else f"行动经验:{atype}"
        polarity = float(evf.get("success_polarity" if success
                                 else "failure_polarity") or 0.5)
        node = self.kg.nodes.get(eid)
        _ts = now_str()
        if node is None:
            self.kg.add_node(Node(
                id=eid, weight=0.5, label="declarative-episodic",
                graph_space="episodic",
                extra_attrs={
                    "type": "action_experience",
                    "action_type": atype, "target": sig or None,
                    "success": success,
                    "success_count": 1 if success else 0,
                    "fail_count": 0 if success else 1,
                    "last_success": _ts if success else None,
                    "last_reason": str(result.get("reason") or ""),
                    "created": _ts, "updated": _ts,
                    "source": "action_settle",
                }))
            node = self.kg.nodes.get(eid)   # add_node 不返回节点，重新取回
        else:
            # 签名级归并：同一 (atype,target) 的重复经历收敛到同一节点，
            # 原地更新计数器/最近一次（历史流水在 timeline/_recent，不复制）。
            ea = node.extra_attrs if isinstance(node.extra_attrs, dict) else {}
            ea["success"] = success
            ea["success_count"] = int(ea.get("success_count") or 0) \
                + (1 if success else 0)
            ea["fail_count"] = int(ea.get("fail_count") or 0) \
                + (0 if success else 1)
            ea["last_success"] = _ts if success else ea.get("last_success")
            ea["last_reason"] = str(result.get("reason") or "")
            ea["updated"] = _ts
        # 极性翻转冷却（默认 0 关；v1 语义=最新一次结算定权）
        _lt = float(node.extra_attrs.get("last_polarity_ts") or 0.0)
        _cd = float(evf.get("flip_cooldown_s") or 0.0)
        _last_pol = node.extra_attrs.get("last_polarity")
        now = time.time()
        if _last_pol is not None and _cd > 0 \
                and float(_last_pol) != polarity and now - _lt < _cd:
            node.extra_attrs.setdefault("skipped_polarities", []).append(
                f"{_ts}:{polarity}")
            return eid
        # 涉及 槽位边：只连已存在的实体节点（绝不新建——那是感知层的职责）
        n_written = 0
        written_dst = None
        for _cands, _force in (
                (tuple(dict.fromkeys((sig, sig.lower(),
                                      f"物品:{sig.lower()}", f"物品:{sig}")))
                 if sig else (), True),
                (tuple(dict.fromkeys((f"缺口:用途({sig})",
                                      f"缺口:用途({sig.lower()})")))
                 if evf.get("suppress_gap_anchor") and sig else (), True),
        ):
            for _c in _cands:
                if _c and _c in self.kg.nodes and _c != eid:
                    _old = self.kg.get_edge(eid, _c, "涉及")
                    if _old is None or float(_old.weight) != polarity:
                        self.kg.add_edge(
                            Edge(src=eid, dst=_c, relation="涉及",
                                 weight=polarity,
                                 relation_category="cognitive_relation"),
                            force_weight=_force)
                    n_written += 1
                    if written_dst is None:
                        written_dst = _c
                    break
        # 结果 槽位边：semantic 叶子 + 恒正权（撑 total_w>0 搭发射循环）
        leaf = (f"行动结果:{sig}:{'succeeded' if success else 'failed'}"
                if sig else f"行动结果:{atype}:"
                            f"{'succeeded' if success else 'failed'}")
        rp = float(evf.get("result_polarity") or 0.9)
        if leaf not in self.kg.nodes:
            self.kg.add_node(Node(
                id=leaf, weight=0.5, label="declarative-semantic",
                graph_space="semantic",
                extra_attrs={"type": "outcome", "success": success,
                             "host": eid, "source": "action_settle",
                             "created": _ts}))
        _old_r = self.kg.get_edge(eid, leaf, "结果")
        if _old_r is None or float(_old_r.weight) != rp:
            self.kg.add_edge(
                Edge(src=eid, dst=leaf, relation="结果", weight=rp,
                     relation_category="cognitive_relation"),
                force_weight=True)
        node.extra_attrs["last_polarity"] = polarity
        node.extra_attrs["last_polarity_ts"] = now
        # C20c 附件（retire_unknown，默认关）：失败=充分接触 → novelty 熄灭
        if not success and evf.get("retire_unknown") and sig:
            for _u in (f"UnknownBlock_{sig}", f"UnknownBlock_{sig.lower()}"):
                _un = self.kg.nodes.get(_u)
                if _un is not None:
                    if not isinstance(_un.extra_attrs, dict):
                        _un.extra_attrs = {}
                    _un.extra_attrs["retired"] = f"action_failed:{atype}"
                    break
        import fas_log
        try:
            fas_log.get_logger(fas_log.ACTION).info(
                "eventframe_landed",
                f"事件框架 {'成功' if success else '失败'} {eid}",
                event_node=eid, action_type=atype, target=sig or None,
                success=success, polarity=polarity, edge_target=written_dst,
                edge_count=n_written, result_leaf=leaf,
                reason=str(result.get("reason") or "")[:160])
        except Exception:
            pass
        return eid

    def set_goal_context(self, goal_desc: str, source: str = "user"):
        """复杂指令开跑前把任务挂上自我图谱：Self-[当前目标]->目标节点。

        这是"为什么做这一串"的任务级指针（区别于 CurrentActivity 的
        "此刻在做什么"）。检测器/触发器与认知上下文都可读它；无 kg 静默跳过。
        """
        if self.kg is None or not str(goal_desc or "").strip():
            return
        try:
            from self_graph import set_current_goal
            self._goal_ctx = set_current_goal(self.kg, goal_desc,
                                              source=source) or None
            # 前沿不变量：新增激活直写点必须走 mark_active（当前目标是
            # "我为什么在做"的进行态，认知应能在前沿看到它）
            if self._goal_ctx and self.engine is not None:
                try:
                    self.engine.mark_active([self._goal_ctx])
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"[Action] 当前目标挂接失败: {e}")

    def _end_goal_context(self, achieved: bool = True):
        if self.kg is None or not self._goal_ctx:
            self._goal_ctx = None
            return
        try:
            from self_graph import clear_current_goal
            clear_current_goal(self.kg, achieved=achieved)
        except Exception as e:
            logger.debug(f"[Action] 当前目标摘除失败: {e}")
        self._goal_ctx = None

    def queue_goal(self, spec: dict) -> dict:
        action = normalize_action(spec, source=SRC_GOAL, kg=self.kg)
        with self._lock:
            if len(self._goal_queue) >= int(self.cfg["max_goal_queue"]):
                return {"ok": False, "reason": "goal_queue_full"}
            self._goal_queue.append(action)
            return {"ok": True, "queued": action["action_type"],
                    "queue_len": len(self._goal_queue)}

    def clear_goals(self) -> int:
        with self._lock:
            n = len(self._goal_queue)
            self._goal_queue = []
        if n:
            # 用户/系统取消了后续步骤 = 任务被放弃（不是完成）
            self._end_goal_context(achieved=False)
        return n
