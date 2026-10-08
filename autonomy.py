# autonomy.py — 自主认知与行动闭环（事件 → 内部状态 → 候选 Action）
# ============================================================================
# 让 FAS 在没有用户指令时，从**自身认知状态**产生行动。核心改造（2026-09）：
# 彻底废除"自主模式 = 随机行为列表/定时乱逛"，改为回答
# "我现在为什么要行动"：
#
#   世界状态（embodiment.perceive + detect_environment_events）
#        ↓ 事件（资源/威胁/生存/时段/社交/结构/掉落物）
#   图谱激活 + 内部状态（需求/驱动力）变化
#        ↓
#   候选 Action 生成（每个候选都带 motivation/reason/expected_effect——
#       没有可解释依据就不产生候选，不编造想法）
#        ↓
#   目标竞争（确定性评分：动机匹配 × 图激活 × 新颖性 × 倾向
#       − 风险 − 刚做过 − 刚失败 − **因果先验**[以前没工具就失败过的会先压低]）
#        ↓
#   ActionManager.propose（当前动作承诺期 / 优先级中断 / 目标队列）
#        ↓
#   执行（embodiment → Skill Library）
#        ↓
#   真实结果反馈（奖赏链 + 倾向证据 + 经验时间轴 + 图谱留痕）
#        ↓
#   下一轮（当前动作结束前**不**重新选择——连续行为的架构保证）
#
# 三条硬约束（沿用）：
#   1. 不让 LLM 当循环的决策器：tick 全程确定性，零 LLM 调用。
#   2. 不伪造自主性：没有可解释的依据（basis 全空）就不产生意图。
#   3. 不绕过现有机制：锁（regulation.locks）拦住的动作直接放弃；
#      行动结果走既有奖赏链（note_outcome / mood_event / disposition）。
#
# 兼容性：未注入 ActionManager 时走旧执行路径（in-flight 轮询），
# 语义不变——旧测试与旧装配继续工作。
# ============================================================================

import logging
import os
import threading
import math
import time
from collections import deque

from graph_model import Node, Edge, now_str, is_live_unknown
from json_store import load_json, atomic_write_json

logger = logging.getLogger(__name__)

# 学习实验（2026-09-25）：调制屏蔽开关 + 结构化实验日志 + 目标→手段先验层。
# 两模块都只依赖标准库/既有图，无环；xm=None 时一切按正常模式走。
try:
    import experiment_mode as xm
except Exception:
    xm = None

# 兼容词表（2026-09-21 降级）：旧 8 个意图名是自主层历史目标词汇，经两张
# 冻结兼容表（LEGACY_INTENT_MAP/LEGACY_SKILL_MAP）进入执行管路。它们不再是
# 语义意图的完整类别——新的目标表达优先使用技能名/行动概念 id（图谱长法）。
INTENT_TYPES = ("observe", "approach", "follow", "explore", "collect",
                "rest", "communicate", "withdraw")
MODES = ("off", "on")
ST_OFF = "off"
ST_PAUSED = "paused"
ST_UNAVAILABLE = "unavailable"     # 具身环境不可用（如 bot 未连接）
ST_IDLE = "idle"                   # 没有值得做的事，保持观察
ST_ACTING = "acting"
ST_COOLDOWN = "cooldown"
ST_BLOCKED = "blocked"             # 被用户活动/锁/上限挡住

LOG_LIMIT = 100
HISTORY_LIMIT = 60

DEFAULT_WEIGHTS = {
    "drive": 0.30,      # 动机匹配（需求/驱动力与候选动机的契合度）
    "attention": 0.25,  # basis 节点在图中的激活强度
    "novelty": 0.20,    # 新颖性（未知对象/首次接触）
    "tendency": 0.10,   # 行为倾向（情境:主动发起 的历史倾向）
    "intention": 0.12,  # 认知意图支持（统一认知循环：意图→行动共享状态）
    "risk": 0.35,       # 风险惩罚（危险目标/低血量）
    "recency": 0.25,    # 刚做过同类动作的惩罚
    "failure": 0.30,    # 最近失败过的惩罚
    "causal": 0.30,     # 因果先验：经历里"这么做失败过"的惩罚
    # P3/§6 重复边际递减：同一(动作,目标)刚成功过的额外减扣（窗口内线性
    # 衰减，封顶 1.0）。与 recency 的分工：recency 180s 全恢复（"刚做过
    # 手感热"），habituation 600s（"已经有一个了，再做一个没那么值"）。
    "habituation": 0.18,
}

# 候选动作类型 → 驱动族（决定 drive 分量从哪来）
_CURIOSITY_FAMILY = {"inspect_area", "inspect_block", "inspect_entity",
                     "observe", "approach", "navigate_to_entity",
                     "explore_area", "explore_direction",
                     "explore_unknown_region", "investigate_location",
                     "detect_cave", "detect_structure", "remember_location",
                     "mark_location", "look_at"}
_RESOURCE_FAMILY = {"gather_resource", "chop_tree", "gather_wood",
                    "gather_stone", "collect_food", "collect_plant",
                    "collect_dropped_item", "hunt_animal", "attack_animal"}
_SOCIAL_FAMILY = {"follow_entity", "follow", "communicate"}

# AUTONOMY.md §5"失败必须真实返回"列出的那类原因是"世界此刻没准备好"
# （失联/不可见/没坐标），不是 Haru 能力不行。causal 先验只对结构性阻碍
# （如 tool_missing）判满额惩罚；暂态理由也当阻碍 = 变相硬禁令，一次
# 连入即失败会把"去找用户"永久软封禁（2026-09-22"呆呆站着"事故）。
_TRANSIENT_WORLD_REASONS = {"not_connected", "entity_not_visible",
                            "player_not_found", "no_target_coords",
                            "no_position", "target_lost", "no_path",
                            "timeout", "missing_entity", "missing_coords",
                            # 2026-09-22 收尾：因果归因接上后（B0），这些同类
                            # 暂态失败若还留在集合外，一次卡住/绕不过去/一次
                            # 投递失败就会被 action_prior 当结构性阻碍永久扣分
                            "stuck", "unreachable", "say_failed",
                            # 2026-09-25：探测接口本身失败（unknown_block /
                            # chunks_unloaded）——"我没查成"不等于"此地没有"，
                            # 更不能进 absent 降级账或 causal 先验。
                            "find_query_failed",
                            # 2026-09-24：探索在出发拍被世界拦下（night/danger）
                            # ——preempted:night 前缀=暂态，天一亮自解；若当
                            # 结构性阻碍，一个夜晚的尝试史会永久压低探索族
                            "preempted",
                            # 2026-09-24 真机 18:38：pathfinder 回执 ok 但整段
                            # 零位移（bot 侧腿病，重启即愈）——机器状态暂态，
                            # 不是"探索这件事做不到"
                            "path_stall",
                            # 2026-09-25 真机：rawGait 12s 零位移接管失败
                            # （她卡在没有可走出口的坑里撞墙）——地形物理阻挡
                            # 与 path_stall 同类，等环境变化自解，不进 causal
                            # 先验/退避账（"暂态失败不进因果先验"同款教训）
                            "raw_gait_stalled",
                            # 2026-09-25 §四D：方块挖开了但背包没多出掉落物
                            # （徒手挖 deepslate 服务器不掉东西）。这是"环境
                            # 这次没给货"，不是"采集这种行为做不到"——当结构性
                            # 阻碍会把她对整类资源的采集永久压死。
                            "dug_no_item",
                            # 2026-09-27 闭环一致性 §P2/P11：transport 类——
                            # 桥超时/回执丢失/轮询异常/执行器异常 = "没拿到
                            # 可靠回执"，不是能力问题也不是世界结构阻碍。不进
                            # recency/退避账、不进 causal 先验（与 reward.py
                            # blockedish 同口径）；世界真实结果由感知迟到确认。
                            "bridge_error", "poll_error", "execution_error"}

# 资源事件价值 → 候选优先级加成（资源机会的内在吸引力）
# （2026-09-21 已退役）资源价值不再用代码表裁决：
# 物种节点激活（mapper）+ 事件 salience + 因果成功率共同决定。

# 探索目标词表（认知层可读的探索意图；方向由足迹与偏好决定）
GOAL_ALIASES = {"村庄": "village", "铁": "iron", "铁矿": "iron", "煤": "coal",
                "煤矿": "coal", "洞穴": "cave", "水": "water", "树": "tree"}


class AutonomousLoop:
    """自主认知循环（无独立线程：由既有认知循环按节拍驱动 tick）。"""

    def __init__(self, kg, engine=None, config=None, regulation=None,
                 llm_budget=None, disposition_store=None, persona=None,
                 data_dir: str = "data", save_graph_fn=None,
                 note_outcome_fn=None, action_manager=None):
        self.kg = kg
        self.engine = engine
        self.config = config or {}
        self.regulation = regulation
        self.llm_budget = llm_budget
        # 能力图谱索引（app 注入；None/关闭=kill switch：只剩承诺目标与兴趣追问）
        self.cap_index = None
        self.disposition_store = disposition_store
        self.persona = persona
        self._save_graph_fn = save_graph_fn
        self._note_outcome_fn = note_outcome_fn
        self.actions = action_manager          # ActionManager（可选注入）
        self.path = f"{data_dir}/autonomy.json"
        self.data_dir = data_dir

        cfg = self.config.setdefault("autonomy", {})
        cfg.setdefault("mode_default", "on")        # 启动默认模式（on=随服务启动即自主；off=需手动开启）
        cfg.setdefault("min_interval_s", 12)        # 两次自主行动的最小间隔
        # busy（长动作在飞）期间的感知心跳：只做世界状态上图，不做决策
        cfg.setdefault("busy_perceive_every_s", 20.0)
        # 空闲踱步（2026-09-25，用户："真人会因为没到阈值就站游戏里不动吗"）
        cfg.setdefault("amble_every_s", 60)
        cfg.setdefault("idle_explore_every_s", 180)   # 有目标的空闲：短探索节流
        cfg.setdefault("idle_explore_max_time_s", 45)
        cfg.setdefault("tick_every_ticks", 4)       # CC 循环里每 N tick 决策一次
        cfg.setdefault("score_threshold", 0.30)
        cfg.setdefault("danger_distance", 6.0)
        cfg.setdefault("approach_distance", 3.0)
        cfg.setdefault("max_attempts", 3)           # 同一(动作,目标)最多尝试次数
        cfg.setdefault("retry_backoff_s", 45)
        # P3/§6/§7：重复成功的习惯化窗口 / 缺口目标的生命周期 TTL
        # （超时=没抓住机会，放弃并关缺口，不占坑到永远）。
        cfg.setdefault("habituation_window_s", 600)
        cfg.setdefault("goal_ttl_s", 1800)
        cfg.setdefault("recency_window_s", 180)
        cfg.setdefault("explore_interval_s", 240)
        # §4（2026-09-25 学习实验）：某方块刚报过"半径内找不到"后的定向探索
        # 窗口——窗口内改提 explore_area(goal=该方块)，窗口过后允许再试 gather。
        cfg.setdefault("explore_unlocatable_s", 300)
        # 世界拦截（夜里/危险）的重试节拍：首停顿 45s，同类拦截每次翻倍，
        # 封顶 300s；成功即清零。只作用于"下一拍还提不提"，不进因果/退避账。
        cfg.setdefault("preempt_pause_s", 45)
        cfg.setdefault("preempt_pause_max_s", 300)
        cfg.setdefault("max_health_for_rest", 6)
        cfg.setdefault("max_food_for_rest", 6)
        cfg.setdefault("collectible_blocks",
                       ["oak_log", "spruce_log", "birch_log", "jungle_log",
                        "acacia_log", "dark_oak_log", "log", "wood",
                        "stone", "cobblestone", "sand", "dirt", "gravel",
                        "coal_ore", "iron_ore"])
        cfg.setdefault("hostile_entities",
                       (config or {}).get("hostile_entities")
                       or ["zombie", "creeper", "skeleton", "spider",
                           "enderman", "witch", "slime", "pillager",
                           "husk", "drowned"])
        cfg.setdefault("companion_names", [])
        cfg.setdefault("llm_mode", 0)
        cfg.setdefault("weights", dict(DEFAULT_WEIGHTS))
        cfg.setdefault("ask_min_drive", 1.5)
        cfg.setdefault("ask_interest_min", 0.3)
        # 动机类别 → 需求维度（R2 P9）。`coef` 把需求显著性换算进动机分，
        # `bias` 保留旧语义里的常数抬升（survival 原来就是 safety+0.3）。
        # 想加一维（比如让 social 也吃 competence 的缺口）= 在这里加一行，
        # `_motivation_value()` 不改。
        cfg.setdefault("motivation_needs", {
            "survival": [{"need": "safety", "coef": 1.0, "bias": 0.30}],
            "resource_opportunity": [{"need": "competence", "coef": 1.0}],
            "curiosity": [{"need": "exploration", "coef": 1.0}],
            "social": [{"need": "social", "coef": 1.0}],
        })
        self.cfg = cfg

        self._lock = threading.RLock()
        # 启动默认模式（2026-09-19 应要求改为 on）。硬约束仍然全部生效：
        # bot 未连接 → ST_UNAVAILABLE；用户说话 → 安静期让位 + 打断在飞
        # 自主动作；无认知依据 → idle；不自动持久化——set_mode("off") 随时
        # 可关，且关闭状态只影响本次运行（重启回到 mode_default）。
        _mode0 = str(self.cfg.get("mode_default", "on")).strip().lower()
        self.mode = _mode0 if _mode0 in MODES else "off"
        self._paused = False
        self._embodiment = None

        if self.mode == "on":
            self.phase = ST_IDLE
            self._state_reason = "自主模式已开启（默认），等待下一轮决策"
        else:
            self.phase = ST_OFF
            self._state_reason = "自主模式未开启"
        self._current = None
        self._in_flight = None
        self._last_result = None
        self._last_action_ts = 0.0
        # 刺激剥夺时钟（2026-09-24，用户："站几个小时不动叫什么玩游戏"）：
        # 最近一次"好奇心动机成功结算"的时刻——social_idle_minutes 的同类
        # 机制，喂 stimulation_hunger 张力（发呆越久→越坐不住→自发起动）。
        # 初值锚定进程启动；_load() 会用持久化的上次值覆盖（跨重启继承，
        # 2026-09-25 用户指令：驱动能量是持续变量）。
        self._last_novel_ts = time.time()
        self._last_amble_ts = 0.0    # 空闲节流（踱步/短探索共用，见 _maybe_amble）
        self._last_idle_explore_ts = 0.0
        self._user_recent_ts = 0.0   # 最近用户交互时刻（调制信号源）
        self._attempts = {}
        self._preempt = {}           # 世界拦截的纯节拍闸门（不进因果账，见 _on_action_settled）
        self._preempt_gap_logged = {}  # [GAP] 去重：同一轮停顿只记一条
        self._recent_by_key = {}
        self._success_by_key = {}    # P3/§6 habituation 用：键最近一次成功时刻
        # 学习实验（2026-09-25）：obtain 目标的闭包重建节流与 PATH 签名去重
        self._closure_ts = {}
        self._goal_step_sigs = {}
        self._log = deque(maxlen=LOG_LIMIT)
        self._history = deque(maxlen=HISTORY_LIMIT)
        self._goals = []
        self._explore_dirs = {"north": 0, "east": 0, "south": 0, "west": 0}
        self._escalated = {}
        self._stats = {"actions": 0, "success": 0, "failed": 0, "cancelled": 0}
        self._last_events = []          # 最近一轮检测到的世界事件（前端可查）
        # 注意：上方 self.actions = action_manager 已经过 property setter
        # （注册结算回调），这里不能再把 _actions_ref 重置为 None。

        self._load()

    # ── ActionManager 注入：注册结算回调（recency/失败计数同步）──

    @property
    def actions(self):
        return self._actions_ref

    @actions.setter
    def actions(self, am):
        if am is None:
            # 清理（2026-09-20）：旧的"自主自带执行支路"（_candidates/
            # _score/_execute/_settle 一整套与 ActionManager 并行的机制）
            # 已删除——未显式注入时自动建内部 ActionManager，全系统只有
            # 一条行动路径。
            from action_system import ActionManager
            # setter 可能在 __init__ 早期被调用：全部用 getattr 容错，
            # embodiment 后由 register_embodiment 补挂。
            am = ActionManager(
                embodiment=getattr(self, "_embodiment", None),
                kg=getattr(self, "kg", None),
                engine=getattr(self, "engine", None),
                config=getattr(self, "config", None) or {},
                data_dir=getattr(self, "data_dir", "data"),
                persona=getattr(self, "persona", None),
                disposition_store=getattr(self, "disposition_store", None),
                internal_state=getattr(self, "internal_state", None),
                note_outcome_fn=getattr(self, "_note_outcome_fn", None),
                save_graph_fn=getattr(self, "_save_graph_fn", None))
        self._actions_ref = am
        am.on_settled = self._on_action_settled
        am.revalidate = self._revalidate_queued_action

    # ── timeline/causal 转发：app 在构造后赋值，落到行动执行器上 ──
    @property
    def timeline(self):
        return getattr(self._actions_ref, "timeline", None)

    @timeline.setter
    def timeline(self, tl):
        if self._actions_ref is not None:
            self._actions_ref.timeline = tl

    @property
    def reward_system(self):
        return getattr(self._actions_ref, "reward_system", None)             or getattr(self, "_reward_local", None)

    @reward_system.setter
    def reward_system(self, rs):
        self._reward_local = rs
        if self._actions_ref is not None:
            self._actions_ref.reward_system = rs

    @property
    def causal(self):
        return getattr(self._actions_ref, "causal", None)

    @causal.setter
    def causal(self, cl):
        if self._actions_ref is not None:
            self._actions_ref.causal = cl

    def _maybe_amble(self, percept: dict, now: float) -> dict:
        """空闲的身体（2026-09-25，用户："真人会因为没到阈值就站游戏里不动吗"
        → "没个目标就在那散步？不得探索一下吗"）。

        两档：优先**短探索**（少走的方向 + 真实观察产出，45s 预算）；探索刚
        做过/夜里被拦时退为**踱步**（原地附近走走看看）。都有界、节流、危险
        不逛，经 ActionManager 提交——kernel/调度照常裁决，结算走正常账
        （各自 key，不冒充高价值行动）。"""
        if percept.get("danger_visible"):
            return {"acted": False, "reason": "amble_danger"}
        idle_gap = now - float(self._last_amble_ts or 0.0)
        if idle_gap < float(self.cfg.get("amble_every_s", 60)):
            return {"acted": False, "reason": "amble_pacing"}
        # 第一档：有目标的空闲——去少走的方向探索一阵（真观察，非瞎晃）
        if now - float(getattr(self, "_last_idle_explore_ts", 0.0)) >= float(
                self.cfg.get("idle_explore_every_s", 180)):
            direction = min(self._explore_dirs,
                            key=lambda k: self._explore_dirs[k])
            self._explore_dirs[direction] += 1
            cand = self._act("explore_direction", direction,
                             {"direction": direction,
                              "max_time": float(self.cfg.get(
                                  "idle_explore_max_time_s", 45)),
                              **self._night_params()},
                             "curiosity", f"往{direction}走走看，了解周边",
                             ["CuriosityDrive", "Haru的位置"],
                             priority=0.35, explain="空闲：去少走的方向探索一阵")
            res = self.actions.propose(cand, source="autonomy", now=now)
            if res.get("started") or res.get("queued"):
                self._last_idle_explore_ts = now
                self._last_amble_ts = now
                self._record("idle_explore", f"空闲探索（{direction}，45s 预算）")
                return {"acted": True, "reason": "idle_explore",
                        "direction": direction}
        # 第二档：纯踱步张望
        cand = self._act("amble", None, {}, "cognitive_state",
                         "换一批感知样本，看看附近有什么", [],
                         priority=0.3, explain="刚探索过，在附近走走张望")
        # 注入时钟必须一路传到 propose：否则 ActionManager 用 wall-clock 结算，
        # _last_action_ts 被写成 1.7e9，虚拟时钟下 min_interval 永久卡死
        # （2026-09-25 test_cognitive_loop S12 转红的根因）
        res = self.actions.propose(cand, source="autonomy", now=now)
        if res.get("started") or res.get("queued"):
            self._last_amble_ts = now
            self._record("amble", "空闲踱步张望")
            return {"acted": True, "reason": "amble"}
        return {"acted": False, "reason": "amble_rejected"}

    def _revalidate_queued_action(self, action: dict):
        """§P1b（2026-09-27 收敛修复）：队列候选 → 执行前的世界状态重验。

        排队等到执行之间隔了多个决策拍，世界在动。两类关键依赖变化直接
        丢弃（让决策循环从新感知再生候选，不硬启动注定失败的步骤）：
          1. 重试冷却过期前到期的键（propose 时还没到冷却，现在到了）；
          2. 目标在场依赖（follow/approach/communicate/navigate/inspect/
             retreat 的对象）在**当前**感知里已不在场——执行注定拿不到
             receipt，不如丢弃。
        其余 fail-open 放行：重验宁可执行也不误杀活候选；感知失败/缺
        字段放行（看不见≠不存在，P3 原则）。
        """
        if action is None:
            return None
        now = time.time()
        try:
            key = self._attempt_key(str(action.get("action_type") or ""),
                                    action.get("target"))
            # 1) 重试冷却：_feasible_action 在 propose 时看过；执行前再看
            #    一次。撞冷却的候选不发（执行器会当场撤回，白走一圈）。
            att = self._attempts.get(key) or {}
            if now < float(att.get("next_ts", 0) or 0):
                logger.info(f"[Autonomy] 重验：{key} 仍在重试冷却，丢弃队列候选")
                return None
            pp = self._preempt.get(key) or {}
            if now < float(pp.get("next_ts", 0) or 0):
                logger.info(f"[Autonomy] 重验：{key} 仍在拦截闸门期，丢弃队列候选")
                return None
            # 2) 目标在场依赖（与 _feasible_action 同一判据，但看的是此刻
            #    的感知，不是 propose 那一刻的）。
            atype = str(action.get("action_type") or "")
            target = action.get("target")
            if not target:
                return action
            percept = self._perceive()
            names = [p.get("name") for p in percept.get("players", [])]
            if atype in ("follow_entity", "approach") or (
                    atype == "communicate"
                    and (action.get("params") or {}).get("player")):
                if target not in names:
                    logger.info(f"[Autonomy] 重验：{atype} 目标 "
                                f"{target} 已不在场，丢弃队列候选")
                    return None
            ents = [e.get("name") for e in percept.get("entities", [])] + names
            if atype in ("navigate_to_entity", "inspect_entity"):
                if target not in ents:
                    logger.info(f"[Autonomy] 重验：{atype} 目标 "
                                f"{target} 已不在场，丢弃队列候选")
                    return None
            if atype == "retreat" and target not in ents:
                logger.info(f"[Autonomy] 重验：威胁 {target} 已离开，丢弃队列候选")
                return None
        except Exception as e:
            logger.warning(f"[Autonomy] 重验异常，放行按原单执行: {e!r}")
        return action

    def _on_action_settled(self, action: dict, result: dict, success: bool,
                           now: float = None):
        """ActionManager 结算 → 自主层的 recency/失败计数同步。

        没有这一步，"刚做过/刚失败过"的降权永远不更新，她会反复做同一件事。
        """
        # 结算是真实的状态改变（outcome 进认知）：撤销变化门，下一拍
        # 重新决定（统一认知循环 §十一）
        self._last_decision_sig = None
        atype = str(action.get("action_type") or "")
        key = self._attempt_key(atype, action.get("target"))
        # 时间戳用结算时的注入时钟（虚拟时钟测试里 recency/退避才能到期；
        # 生产路径本来就是真实时间）
        now = time.time() if now is None else now
        # 暂态失败（世界没准备好：夜里没迈步/实体不可见/没连着）≠"刚试过"——
        # 不记 recency、不进退避账。否则一次夜间拦截会把清晨的探索拖住几分钟
        # （2026-09-25 实测：preempted:night 0 秒结算 → rec/fail 双压住白天
        # 候选，天亮了她反而更动不了）。causal 先验与 reward 分类早已同口径
        # 豁免，这里是最后一条账。
        _r0 = (str(result.get("reason") or "").split(":", 1)[0]
               if (not success and not result.get("cancelled")) else "")
        _transient_fail = _r0 in _TRANSIENT_WORLD_REASONS
        with self._lock:
            if not _transient_fail:
                self._recent_by_key[key] = now
            self._last_action_ts = now
            self._stats["actions"] += 1
            if success:
                self._stats["success"] += 1
                self._attempts.pop(key, None)
                self._preempt.pop(key, None)   # 世界放行 → 节拍闸门清零
                self._success_by_key[key] = now   # P3/§6 habituation 记账
                if str(action.get("motivation") or "") == "curiosity" \
                        and self._settlement_gained_knowledge(action, result):
                    self._last_novel_ts = now     # 有真知识增量才归零剥夺钟
                # 探索/寻访成功且指向方块 → 清掉该方块的 gather"此地没有"账：
                # 定向探索的职责是把"看不见"变成"看见了"，看见了就该回去采
                # （2026-09-26 场景实测：explore 0 秒发现 oak_log 成功循环，
                #  absent 账不清，gather 永远不回头）。investigate_location
                # 的 target 就是方块名（_mk 第二参）。
                _blk = str((result.get("detail") or {}).get("block")
                           or (result.get("detail") or {}).get("found") or "")
                if atype == "explore_area" and _blk:
                    self._attempts.pop(
                        self._attempt_key("gather_resource", _blk), None)
                    self._preempt.pop(
                        self._attempt_key("gather_resource", _blk), None)
                elif atype in ("investigate_location", "walk_to") and \
                        str(action.get("target") or ""):
                    self._attempts.pop(
                        self._attempt_key("gather_resource",
                                          str(action.get("target"))), None)
                    self._preempt.pop(
                        self._attempt_key("gather_resource",
                                          str(action.get("target"))), None)
                # 放置成功 → 台子/设备的位置进记忆（seen:<方块>）：needs_table
                # 的合成据此知道"台子已放"，直接去台旁合成，不再绕回去找原木
                if atype == "place_block":
                    _pdet = result.get("detail") or {}
                    # 方块名从 detail 取，但 2026-09-27 真机教训：这次结算的
                    # detail 只剩 position（_place_item 在途丢），seen:furnace
                    # 静默没记上 → 回头找不着炉。action 的 target 本来就是
                    # 放置物的物品名，如实兜底。
                    _pblk = str(_pdet.get("block") or action.get("target") or "")
                    if _pblk:
                        try:
                            from skills.base import LocationMemory
                            locmem = getattr(self, "_locmem", None)
                            if locmem is None:
                                locmem = self._locmem = LocationMemory(
                                    path=os.path.join(
                                        self.data_dir, "mc_locations.json")
                                    if getattr(self, "data_dir", None)
                                    else None)
                            locmem.remember("seen:" + _pblk,
                                            _pdet.get("position") or {},
                                            kind="seen")
                        except Exception as e:
                            # §14：位置记忆写失败必须留痕（静默丢点会让
                            # gather 永远回不去上次见过的坐标）
                            logger.warning(f"[Autonomy] seen 位置记忆写入失败 "
                                           f"{_pblk}: {e}")
            elif result.get("cancelled"):
                self._stats["cancelled"] += 1
            else:
                self._stats["failed"] += 1
                # 带着 goal 的寻访失败且原因是"到场未发现目标"：那条
                # seen 记忆是废墟坐标，作废以免下次又回访（2026-09-27
                # 真机：熔炉从世界消失，investigate 还在原址转圈）。
                if atype == "investigate_location" and \
                        str(result.get("reason") or "") == "goal_not_found":
                    _tg = str(action.get("target") or "")
                    if _tg:
                        try:
                            from skills.base import LocationMemory
                            locmem = getattr(self, "_locmem", None)
                            if locmem is None:
                                locmem = self._locmem = LocationMemory(
                                    path=os.path.join(
                                        self.data_dir, "mc_locations.json")
                                    if getattr(self, "data_dir", None)
                                    else None)
                            locmem.forget("seen:" + _tg)
                            logger.info(f"[Autonomy] seen:{_tg} 记忆作废"
                                        f"（寻访到场未发现目标）")
                        except Exception as e:
                            logger.warning(f"[Autonomy] seen:{_tg} 遗忘失败: {e}")
                att = self._attempts.get(key) or {"count": 0}
                if not _transient_fail:
                    att["count"] = int(att.get("count", 0)) + 1
                    att["last_ts"] = now
                    # P3/§6 软退避（消费写了一年没人读的 next_ts）：max_attempts
                    # 之后同类失败的退避时间倍增（封顶 8×）。与 2026-09-21 拆除的
                    # "永久放弃"不同——有限窗口随时间到期，一次成功即清零；
                    # _feasible_action 消费它（见"重试冷却"）。
                    _maxn = max(1, int(self.cfg.get("max_attempts", 3)))
                    _mult = min(8.0, 2.0 ** max(0, int(att["count"]) - _maxn))
                    att["next_ts"] = now + float(self.cfg["retry_backoff_s"]) * _mult
                    att["last_reason"] = result.get("reason")
                    self._attempts[key] = att
                if _r0 == "preempted":
                    # 世界说"此刻不行"（夜里/危险）。这是暂态：不进 _attempts
                    # （那会污染因果先验，2026-09-25 早已定性的坑），但也不能
                    # 每 12 秒撞一次墙——实测 2026-09-25 13:32 真机：
                    # explore_area@cherry_log 提议→preempted:night→再提议，
                    # 每一拍还向因果学习器多投一条失败观察（刷出噪声因果）。
                    # 所以这里只有一条**纯节拍闸门**：同类拦截越多次，间隔越长
                    # （45s 起，×2，封顶 300s）；成功即清零，天亮下一拍就能动。
                    pp = self._preempt.get(key) or {"count": 0}
                    pp["count"] = int(pp.get("count", 0)) + 1
                    base = float(self.cfg.get("preempt_pause_s", 45))
                    pp["next_ts"] = now + min(
                        float(self.cfg.get("preempt_pause_max_s", 300)),
                        base * (2.0 ** max(0, int(pp["count"]) - 1)))
                    pp["reason"] = result.get("reason")
                    self._preempt[key] = pp
            self._last_result = {"success": success, "action": atype,
                                 "target": action.get("target"),
                                 "reason": result.get("reason"),
                                 "duration_s": result.get("duration_s"),
                                 "ts": now_str()}
            # B4/§4 承诺兑现即销账：目标的实现名与本次结算匹配（目标名
            # 也匹配或任一缺目标名）→ 移除。邀请不该在兑现之后继续统治
            # 每一个决策拍。（实验 obtain 目标不在此列——它的销账条件是
            # "背包里真有"，见 _obtain_candidates。）
            _drop = []
            # 无条件绑定：成败两路都会走到下方的 if _pend or _progressed，
            # 只绑在 if success: 内会让失败结算 UnboundLocalError（2026-09-27 修）
            _progressed = False
            if success:
                _ap = action.get("params") or {}
                _atgt = (action.get("target") or _ap.get("player")
                         or _ap.get("entity"))
                for g in self._goals:
                    gt = str(g.get("type") or "")
                    if (not gt or self._realize_goal(gt) != atype
                            or str(g.get("source") or "")
                            == self.OBTAIN_GOAL_SOURCE):
                        continue
                    _gp = g.get("params") or {}
                    _gtgt = (g.get("target") or _gp.get("player")
                             or _gp.get("entity"))
                    if not _gtgt or not _atgt or str(_gtgt) == str(_atgt):
                        _drop.append(g)
                        # §P5：目标匹配的成功结算 = 目标进展证据（生命周期
                        # 评估的"最近成功"锚点；同时解除 demoted 状态）
                        g["last_success_at"] = now
                        g.pop("demote_since", None)
                # §P5：obtain 链上的中间步骤成功同样 = 目标进展（候选的
                # reason 首项就是目标物品节点 物品:{target}；结算 action 带
                # 同一 reason 列表）——不记的话，挖矿→合炉→放置等多步链
                # 会在 10 分钟无直接销账胜利后被"降权/搁置"误杀。
                _reasons = action.get("reason") or []
                for g in self._goals:
                    if not (str(g.get("source") or "")
                            == self.OBTAIN_GOAL_SOURCE and success):
                        continue
                    _gtgt = str(g.get("target") or "").lower()
                    if _gtgt and f"物品:{_gtgt}" in _reasons:
                        g["last_success_at"] = now
                        g.pop("demote_since", None)
                        _progressed = True
                if _drop:
                    self._goals = [g for g in self._goals
                                   if not any(g is d for d in _drop)]
            if self.phase == ST_ACTING:
                self.phase = ST_IDLE
                self._state_reason = ("动作完成" if success
                                      else f"动作失败: {result.get('reason') or '未知原因'}")
                self._current = None
            # 熔炼→取货记账（仅实验 obtain 链）：smelt_item 成功≠拿到产物——
            # 东西在炉子里，要 furnace_take 取出。furnace_take **成功才销账**；
            # 失败（还在炼/炉子空/取失败）留账，300s 窗口内自动重试，过期
            # 不再提出（2026-09-26 修：原本失败也清账，而 FurnaceTake 空炉
            # 返回假成功——产物留在炉里，账面却已了结）。
            _pend = False
            if (atype in ("smelt_item", "furnace_take")
                    and self._obtain_goals()):
                for g in self._goals:
                    if str(g.get("source") or "") != self.OBTAIN_GOAL_SOURCE:
                        continue
                    if atype == "smelt_item" and success:
                        g["smelt_pending"] = {
                            # 记的是"炉子里有东西要取"，名字取本次动作的原料名；
                            # 兜底用目标名（不是某个具体物品名——实验里
                            # 曾经在这里写死 "raw_iron"，那是攻略残留）
                            "item": str((action.get("params") or {})
                                        .get("item") or g.get("target")
                                        or ""),
                            "ts": now}
                        # §P5：链上成功（进炉/取货）都是目标进展证据
                        g["last_success_at"] = now
                        g.pop("demote_since", None)
                    elif atype == "furnace_take" and not success:
                        pass
                    else:
                        g.pop("smelt_pending", None)
                        if success:
                            g["last_success_at"] = now
                            g.pop("demote_since", None)
                    _pend = True
                    break
        if _pend or _progressed:
            self._save()
        if _drop:
            self._save()
            self._record("goal", f"兑现销账 {len(_drop)} 条："
                           + ", ".join(f"{d.get('type')}@{d.get('target')}"
                                       for d in _drop))
        # P3/§5 探索缺口：真实结果回流先验层（零 LLM；异常静默不打断结算）
        try:
            self._gap_outcome(action, success, now, result=result)
        except Exception as e:
            logger.warning(f"[Autonomy] gap outcome 回流失败: {e}")
        # 旧路径收尾职责并入单一路径：llm_mode=2 升级标记 + 状态检测器发布
        try:
            _cand = {"type": atype, "target": action.get("target")}
            if not success and not result.get("cancelled"):
                self._escalate(_cand, result)
            self._publish_state(_cand, result, success)
        except Exception as e:
            # §14：失败升级与状态发布属于状态同步——静默丢会让
            # "连续失败"对上层完全不可见（检测器以为一切正常）
            logger.warning(f"[Autonomy] 升级/状态发布失败 {atype}: {e}")
        # §21 [RESULT]：每个真实动作结算一行结构化日志（仅实验模式）
        try:
            if xm is not None and xm.enabled():
                xm.xlog("RESULT",
                        f"{atype}@{action.get('target') or ''} "
                        f"{'ok' if success else ('cancelled' if result.get('cancelled') else 'fail')}",
                        reason=str(result.get("reason") or "")[:80],
                        dur=result.get("duration_s"))
        except Exception:
            pass

    def _settlement_gained_knowledge(self, action: dict, result: dict) -> bool:
        """本次结算是否带**真知识增量**（§P6：执行成功 ≠ 知识进展）。

        通用判据（数据驱动，零逐物分支）：
          1. 行动对象是活跃 Unknown 且成功——凑近看清了不认识的 X；
          2. 回执带直接观察证据（found>0 / collected>0 / entity / block /
             dist / rel）——真的看见了或拿到了什么；
          3. 探索回执的 observed 列表非空——这趟确实看了点什么新样本。
        三条都不满足 = 走了一趟什么都不了解：不重置刺激剥夺钟，不然
        "空手漫步"会持续喂满好奇心时钟，漫游成为 reward loophole
        （2026-09-27 收敛修复 §P6，与 reward.discovery 证据要求同口径）。
        """
        if not result:
            return False
        try:
            for b in (action.get("reason") or []):
                nid = str(b)
                node = self.kg.nodes.get(nid) if self.kg is not None else None
                if (node is not None and is_live_unknown(node)) or \
                        nid.startswith(("UnknownEntity_", "UnknownBlock_",
                                        "UnknownPlayer_")):
                    return True
        except Exception:
            pass
        d = result.get("detail")
        if not isinstance(d, dict):
            d = result.get("result")
            if not isinstance(d, dict):
                return False
        if int(d.get("found") or 0) > 0 or int(d.get("collected") or 0) > 0:
            return True
        for _k in ("entity", "block", "dist", "rel"):
            if d.get(_k) not in (None, ""):
                return True
        obs = d.get("observed")
        if isinstance(obs, (list, tuple, set, dict)) and len(obs) > 0:
            return True
        return False

    # ── 持久化 ────────────────────────────────────────────

    def _load(self):
        data = load_json(self.path, default=None) or {}
        with self._lock:
            self._goals = list(data.get("goals", []) or [])
            self._explore_dirs = dict(data.get("explore_dirs", self._explore_dirs))
            # 刺激剥夺钟跨重启继承（2026-09-25 用户指令：驱动能量是持续变量）。
            # 之前构造器里 time.time() 重置 → 每次开机饥饿归零、驱动源头断供
            # 十分钟。继承上次的"上次新鲜体验"时刻：关机久了饥饿本就该满。
            try:
                _lnt = float(data.get("last_novel_ts") or 0)
                if _lnt > 0:
                    self._last_novel_ts = min(_lnt, time.time())
            except (TypeError, ValueError):
                pass
            # 模式不持久化（原设计：避免无法停止的后台行为）；重启回到
            # mode_default。运行中 set_mode("off") 仍随时可停。
            _mode0 = str(self.cfg.get("mode_default", "on")).strip().lower()
            self.mode = _mode0 if _mode0 in MODES else "off"

    def _save(self):
        with self._lock:
            payload = {"version": 1,
                       "goals": self._goals[-20:],
                       "explore_dirs": self._explore_dirs,
                       # 剥夺钟一并落盘（跨重启继承，见 _load）
                       "last_novel_ts": float(self._last_novel_ts or 0.0),
                       "updated_at": now_str()}
        atomic_write_json(self.path, payload)

    # ── 模式控制 ──────────────────────────────────────────

    def set_mode(self, mode: str) -> dict:
        mode = str(mode or "").strip().lower()
        if mode not in MODES:
            return {"ok": False, "error": f"mode 必须是 {MODES}"}
        with self._lock:
            self.mode = mode
            if mode == "off":
                self._paused = False
                if self.actions is not None:
                    self.actions.cancel("自主模式已关闭")
                self._intent_cancelled("按下了停止")
                self.phase = ST_OFF
                self._state_reason = "自主模式已关闭"
            else:
                self._paused = False
                self.phase = ST_IDLE
                self._state_reason = "自主模式已开启，等待下一轮决策"
        self._record("mode", f"mode={mode}")
        logger.info(f"[Autonomy] 模式 → {mode}")
        return {"ok": True, "mode": self.mode, "state": self.phase}

    def pause(self, reason: str = "手动暂停") -> dict:
        with self._lock:
            self._paused = True
            self.phase = ST_PAUSED
            self._state_reason = reason
            cancelled = self._intent_cancelled(reason)
        # 2026-09-26 实机：amble 的 goto 已按回执结算"成功"（27.15s），但桥侧
        # 原始步态/残留 pathfinder  goal 还在走——pause 只置旗语时，Haru 事后
        # 无指令漂行 136 格。"暂停自主"必须连腿一起停：embodiment.cancel()
        # 是桥侧停腿总入口（/stop + /stop_goto + /stop_combat + 解跟随），
        # 没有在飞动作时也 best-effort 调一次（内部全 try 包裹，离线安全）。
        if self._embodiment is not None:
            try:
                self._embodiment.cancel()
            except Exception as e:
                logger.warning(f"[Autonomy] pause 停腿失败: {e}")
        self._record("paused", reason)
        return {"ok": True, "paused": True, "cancelled": cancelled}

    def resume(self) -> dict:
        with self._lock:
            self._paused = False
            self.phase = ST_IDLE if self.mode == "on" else ST_OFF
            self._state_reason = "已恢复"
        self._record("resumed", "")
        return {"ok": True, "mode": self.mode, "state": self.phase}

    def notify_user_activity(self, quiet_s: float = None,
                             now: float = None):
        """用户交互（2026-09-21 去硬编码安静期）：
        - 取消在飞的**自主/生存**动作（用户优先=执行调度礼仪，保留）；
        - 把"刚与用户交互"作为 context.user_recent 喂调制层——
          action.score_threshold 连续抬高（不是 20s 一刀切封门），
          足够强的社交/好奇驱动仍可立即行动。
        """
        now = time.time() if now is None else now
        self._user_recent_ts = now
        # 调制信号：240s 半衰的"最近交互强度"
        try:
            mod = getattr(self, "modulation", None)
            if mod is not None and hasattr(mod, "define"):
                pass   # 值由 drive_engine 侧 context provider 读取
        except Exception:
            pass
        self._intent_cancelled("用户正在交互")
        try:
            cur = self.actions.current if self.actions.busy() else None
        except Exception:
            cur = None
        if cur and str(cur.get("source")) in ("autonomy", "survival"):
            self.actions.cancel(str(cur.get("action_id") or ""),
                                reason="用户交互让位")

    def _intent_cancelled(self, reason: str) -> bool:
        """取消在飞的自主动作（旧路径；调用方持锁）。"""
        if self._in_flight is None:
            return False
        in_flight = self._in_flight
        self._in_flight = None
        if self._embodiment is not None:
            try:
                self._embodiment.cancel()
            except Exception as e:
                logger.warning(f"[Autonomy] 取消动作失败: {e}")
        self._stats["cancelled"] += 1
        self._last_result = {"success": False, "action": in_flight.get("type"),
                             "reason": f"cancelled: {reason}", "cancelled": True}
        self._record("cancelled", f"{in_flight.get('type')}@{in_flight.get('target')} ← {reason}")
        return True

    def register_embodiment(self, embodiment):
        self._embodiment = embodiment
        if self._actions_ref is not None and self._actions_ref.embodiment is None:
            self._actions_ref.embodiment = embodiment
        logger.info(f"[Autonomy] 具身环境已注册: {getattr(embodiment, 'name', '?')}")
        return embodiment

    # ── 主循环 ────────────────────────────────────────────

    def tick(self, now: float = None) -> dict:
        now = time.time() if now is None else now
        try:
            return self._tick(now)
        except Exception as e:
            logger.warning(f"[Autonomy] tick 异常: {e}")
            self.phase = ST_IDLE
            self._state_reason = f"内部异常: {e}"
            return {"acted": False, "reason": "exception", "error": str(e)}

    # ── 当前活动挂钩 ──────────────────────────────────────

    def _notify_activity_idle(self, reason: str):
        """空闲期写入 CurrentActivity："我此刻在观察环境"。

        活动是认知层状态（图节点，Haru-[当前活动]->），与 ST_IDLE 这个
        内存状态机不同步——它让持续认知循环/语言层能读到"我正在做什么"。
        """
        try:
            tracker = getattr(self.actions, "activity", None)
            if tracker is not None:
                tracker.idle(reason=reason)
        except Exception:
            pass

    def _tick(self, now: float) -> dict:
        """新路径：候选 = 完整 ActionNode；执行/承诺/中断交给 ActionManager。"""
        if self.mode != "on":
            self.phase = ST_OFF
            self._state_reason = "自主模式未开启"
            return {"acted": False, "reason": "mode_off"}
        if self._paused:
            self.phase = ST_PAUSED
            return {"acted": False, "reason": "paused"}
        # （2026-09-21）安静期封门已拆除：用户刚交互过的事实以
        # context.user_recent 进调制层抬高行动阈值（连续、可被强驱动
        # 覆盖），不再是"20 秒内禁止一切决策"的硬规则。

        emb = self._embodiment
        if emb is None:
            self.phase = ST_UNAVAILABLE
            self._state_reason = "未注册具身环境"
            return {"acted": False, "reason": "no_embodiment"}
        try:
            available = bool(emb.available())
        except Exception:
            available = False
        if not available:
            self.phase = ST_UNAVAILABLE
            self._state_reason = "具身环境不可用（bot 未连接）"
            return {"acted": False, "reason": "embodiment_unavailable"}

        # 当前动作承诺期：只推进（含中断检查/结算），不重新选择
        if self.actions.busy():
            # 2026-09-23 P3 验收：世界状态上图不许陪 busy 门饿死——背包刷新/
            # 配方线索/缺口复活(resurface_gap)/在视注意力地板都寄生在 perceive
            # 里，perceive 停=缺口生命周期停（长 follow=盲跟，"零星行为"的
            # accidental 半边）。决策照旧不重决（§8 承诺稳定），只给慢心跳。
            try:
                if now - float(getattr(self, "_last_busy_perceive", 0.0)) \
                        >= float(self.cfg.get("busy_perceive_every_s", 20.0)):
                    self._last_busy_perceive = now
                    self._perceive()
            except Exception:
                pass
            out = self.actions.tick(now)
            self.phase = ST_ACTING
            self._state_reason = (f"正在执行: {self.actions.current.get('action_type')}"
                                  if self.actions.current else "动作刚结束")
            return {"acted": bool(out.get("acted")), "reason": "action_progress",
                    "detail": out}

        # 最小间隔
        gap = now - self._last_action_ts
        if gap < float(self.cfg["min_interval_s"]):
            self.phase = ST_COOLDOWN
            self._state_reason = f"行动间隔（剩 {float(self.cfg['min_interval_s']) - gap:.0f}s）"
            return {"acted": False, "reason": "min_interval"}

        # 1) 感知 → 世界事件 → 图激活 + 内部状态（事件先影响认知，不直接变行动）
        percept = self._perceive()
        events = self._detect_events()
        # 具身状态映射（2026-09-21）：观测事实→图谱激活/生存压力，
        # 认知与调度从图上自然感知到危险，而非 if→action 特判
        mapper = getattr(self, "mapper", None)
        if mapper is not None:
            try:
                self._last_survival_pressure = mapper.update(percept, events,
                                                             now=now)
            except Exception as _me:
                logger.debug(f"[Autonomy] 具身状态映射跳过: {_me}")
        self._apply_event_effects(events)
        self._last_events = events

        # 变化门（统一认知循环 §十一）：决策由状态改变驱动，不是固定闹钟
        # 重扫。事件效应照常注入认知（上面已做），但"要不要重新选择行动"
        # 取决于世界/意图签名是否变化；120s 兜底强制刷新防摘要巧合僵死。
        try:
            sig = self._decision_signature(percept, events)
            if (sig == getattr(self, "_last_decision_sig", None)
                    and now - getattr(self, "_last_scan_ts", 0.0) < 120.0):
                self.phase = ST_IDLE
                self._state_reason = "世界与内部状态无变化，保持观察"
                return {"acted": False, "reason": "no_change"}
            self._last_decision_sig = sig
            self._last_scan_ts = now
        except Exception:
            pass

        # 2) 候选生成（每个都带 motivation/reason/expected_effect）
        candidates = self._action_candidates(percept, events, now)
        scored = []
        for cand in candidates:
            score, explain = self._score_action(cand, percept, now)
            if score > 0:
                cand["score"] = round(score, 4)
                cand["explain"] = explain
                scored.append(cand)
        scored.sort(key=lambda c: -c["score"])
        # （2026-09-21 §13）"生存硬置顶"已删除：生存候选的领先来自
        # safety 需求/生存压力抬高的 drive 分量与概念 urgency/priority
        # 数据——排序自然反映，调度器不再按行为类别强制插队。
        with self._lock:
            self._history.append({"ts": now_str(), "events": [e.get("type") for e in events[:6]],
                                  "candidates": [{"type": c["action_type"],
                                                  "target": c.get("target"),
                                                  "score": c.get("score"),
                                                  "motivation": c.get("motivation"),
                                                  "explain": c.get("explain")}
                                                 for c in scored[:5]]})
        if not scored:
            self.phase = ST_IDLE
            self._state_reason = "没有任何可解释的意图依据（不编造想法）"
            self._record("idle", self._state_reason)
            self._notify_activity_idle("自主空闲，保持观察")
            _am = self._maybe_goal_fallback(percept, now) or \
                self._maybe_amble(percept, now)
            if _am.get("acted"):
                return _am
            return {"acted": False, "reason": "no_candidates"}

        best = scored[0]
        if best["score"] < self._threshold():
            self.phase = ST_IDLE
            self._state_reason = (f"最强候选 {best['action_type']} 得分 {best['score']} "
                                  f"低于阈值 {self._threshold():.2f}，保持观察")
            self._record("idle", self._state_reason)
            self._notify_activity_idle(f"自主空闲，保持观察（{self._state_reason[:60]}）")
            _am = self._maybe_goal_fallback(percept, now) or \
                self._maybe_amble(percept, now)
            if _am.get("acted"):
                return _am
            return {"acted": False, "reason": "below_threshold",
                    "best": {"type": best["action_type"], "score": best["score"]}}

        # 3) 可行性 → 提交 ActionManager（优先级/中断/承诺在那里裁决）
        rejected = []
        for cand in scored:
            if cand["score"] < self._threshold():
                break
            ok, reason = self._feasible_action(cand, percept, now)
            if ok:
                self.phase = ST_ACTING
                self._state_reason = cand.get("explain", "")
                self._record("intent",
                             f"{cand['action_type']}@{cand.get('target')} — {cand.get('explain')}")
                logger.info(f"[Autonomy] 提交动作 {cand['action_type']}@{cand.get('target')} "
                            f"({cand.get('score')}) motivation={cand.get('motivation')}")
                prop = self.actions.propose(cand, source="autonomy", now=now)
                if prop.get("started"):
                    # §21 [ACTION]：每个被真实提交的自主动作一行
                    try:
                        if xm is not None and xm.enabled():
                            xm.xlog("ACTION",
                                    f"{cand['action_type']}"
                                    f"@{cand.get('target') or ''}",
                                    score=cand.get("score"),
                                    motivation=cand.get("motivation"))
                    except Exception:
                        pass
                    self._last_action_ts = now
                    # 意图→行动闭环：被实现的意图被消耗（不然它会永远
                    # 支持同一候选，驱动重复行动）
                    self._consume_intention(cand)
                    out = {"acted": True, "intent": cand["action_type"],
                           "target": cand.get("target"),
                           "explain": cand.get("explain"),
                           "interrupted": prop.get("interrupted", False),
                           "pending": bool(prop.get("pending")),
                           "rejected": rejected or None}
                    # 同步完成的动作把真实结果带回（旧路径 _settle 的返回契约）
                    if not prop.get("pending"):
                        out["success"] = bool(prop.get("success"))
                        out["reason"] = prop.get("reason") or ""
                    return out
                self._record("blocked",
                             f"{cand['action_type']} 提交失败: {prop.get('reason')}")
                rejected.append({"type": cand["action_type"],
                                 "reason": prop.get("reason")})
                continue
            rejected.append({"type": cand["action_type"],
                             "target": cand.get("target"),
                             "score": cand["score"], "reason": reason})
            self._record("blocked",
                         f"{cand['action_type']}@{cand.get('target')} 不可行: {reason}")

        self.phase = ST_BLOCKED
        if rejected:
            top = rejected[0]
            self._state_reason = f"最强候选 {top.get('type')} 不可行: {top.get('reason')}"
            return {"acted": False, "reason": "infeasible", "detail": top.get("reason"),
                    "rejected": rejected}
        return {"acted": False, "reason": "no_action"}

    # ── 事件检测与事件效应（§七：事件先影响内部状态）──────

    def _detect_events(self) -> list:
        """世界事件检测（委托具身层；具身层不支持时返回空）。"""
        fn = getattr(self._embodiment, "detect_events", None)
        if fn is None:
            return []
        try:
            return list(fn() or [])
        except Exception as e:
            logger.debug(f"[Autonomy] 事件检测跳过: {e}")
            return []

    def _apply_event_effects(self, events: list):
        """事件 → 图节点激活 + 需求变化（事件不直接变成行动）。"""
        if not events:
            return
        activate = []
        need_deltas = []
        for ev in events:
            etype = ev.get("type")
            if etype == "resource_detected":
                res = str(ev.get("resource") or "")
                nid = res if (self.kg and res in self.kg.nodes) \
                    else self._unknown_node_id("block", res)
                activate.append(nid)
                # 资源机会 → 掌控/能力需求上升（想要去获取）
                need_deltas.append(("competence", 0.03))
            elif etype == "hostile_detected":
                nm = str(ev.get("entity") or "")
                activate.append(self._unknown_node_id("entity", nm))
                d = float(ev.get("dist") or 16)
                need_deltas.append(("safety", max(0.02, min(0.10, (12.0 - d) / 100.0))))
            elif etype == "health_low":
                activate.append("Haru的血量")
                need_deltas.append(("safety", 0.08))
            elif etype == "hunger_low":
                activate.append("Haru的饥饿")
                need_deltas.append(("safety", 0.04))
                need_deltas.append(("competence", 0.02))
            elif etype == "item_on_ground":
                need_deltas.append(("competence", 0.02))
            elif etype == "player_arrived":
                activate.append("用户")
                need_deltas.append(("social", 0.05))
            elif etype == "player_left":
                need_deltas.append(("social", -0.03))
            elif etype == "time_changed":
                if ev.get("to") == "night":
                    need_deltas.append(("safety", 0.05))
            elif etype == "structure_detected":
                for s in (ev.get("structures") or []):
                    activate.append(self._unknown_node_id("entity", s))
                need_deltas.append(("exploration", 0.03))
        # 图激活：具身图谱化 2026-09-21——事件→激活的通路移交
        # EmbodiedStateMapper（update 里统一处理，含 MC 昼夜的 source 隔离）；
        # 未注入 mapper 时保留原激活（离线旧桩兼容），需求 delta 单源不变
        mapper = getattr(self, "mapper", None)
        if mapper is None and activate and self.kg is not None \
                and self.engine is not None:
            try:
                with self.kg._lock:
                    hit = []
                    for nid in activate:
                        n = self.kg.nodes.get(nid)
                        if n is not None:
                            n.activation = min(5.0, n.activation + 0.8)
                            n.touch()
                            hit.append(nid)
                if hit:
                    self.engine.mark_active(hit)
                    # 环境事件是本轮的外源感知激活（perception）：发射免扣
                    self.engine.register_activation_source(hit, "perception")
            except Exception as e:
                logger.debug(f"[Autonomy] 事件激活失败: {e}")
        # 需求变化（经 InternalState 唯一写入口）。§16 实验屏蔽：事件仍上图、
        # 仍进认知，但不再推拉需求——动机分量钉在装配时刻，排除情绪干扰。
        if (need_deltas and getattr(self, "internal_state", None) is not None
                and not (xm is not None and xm.shield("world_events_to_needs"))):
            try:
                for name, delta in need_deltas:
                    self.internal_state.apply_delta("need", name, delta,
                                                    reason=f"event:{events[0].get('type')}")
            except Exception as e:
                logger.debug(f"[Autonomy] 需求更新跳过: {e}")

    # ── 目标生命周期（§P5 收敛修复 2026-09-27）─────────────────
    # 目标不只因"没失败"就无限持续：relevance/progress/时间/用户语境
    # 数据驱动地重新评估。状态 active → demoted（候选降权 0.5，仍生成
    # ＝让位给有证据的行动）→ stale（不再自动生成候选，保留记录，用户
    # 再提/重新登记即复活）。没有 `if 某目标 > N 秒: stop()`——判据只读
    # 目标自有字段与感知，任何目标类型同构。不可见≠不存在：降权/搁置是
    # 软状态（可被任何成功结算解除），不是禁令。

    GOAL_DEMOTE_AFTER_S = 600.0     # 距最近一次成功这么久 → 降权
    GOAL_STALE_S = 1800.0           # 降权持续这么久 → 搁置
    _GOAL_USER_GRACE_S = 900.0      # 最近与用户交互 → 用户目标免降权

    def _reevaluate_goal(self, g: dict, percept: dict,
                         now: float) -> str:
        """返回当前生命周期状态（含状态转移簿记：首次转 stale 留记录）。

        判据：
          - 探索缺口目标：不在此裁决（_gap_candidates 的 TTL/fails/试用账
            是更强的独立生命周期，这里不重复判死）
          - 用户语境：最近与用户交互（自己人正在附近/正在对话）→ 用户
            来源目标保持 active——"环境中暂时没兑现"不误伤用户承诺
          - 有成功锚点（last_success_at）：距它 < DEMOTE 窗口 → active；
            超过 → demoted（降权）；降权满 STALE 窗口 → stale
          - 无成功锚点：自创建 < STALE 窗口（尝试机会期）→ active；
            到期 → stale（从来没能兑现的承诺不占坑到永远）
          - 复活条件：软状态不是终态——目标重新在场（邀请类）或成功结算
            （_on_action_settled 解 demote_since）随时解除降权/搁置
        """
        src = str(g.get("source") or "")
        if src == "exploration_gap":
            return "active"
        _gtype = str(g.get("type") or "")
        # 在场证据（2026-09-27 收敛修复）：对"目标是活的在场者"的目标
        # （跟随/靠近）——出现即清零缺席计时；缺席连续累积（感知截断的
        # 偶发缺拍不会累积）满 DEMOTE 窗口 → 降权，满 STALE → 搁置。
        # 物品目标的在场性由感知的图状态承担，不在这里判。
        _present = None
        _tgt = str(g.get("target") or "")
        if _gtype in ("follow", "approach") and _tgt:
            _names = ({p.get("name")
                       for p in (percept or {}).get("players", [])}
                      | {e.get("name")
                         for e in (percept or {}).get("entities", [])})
            _present = _tgt in _names
            if _present:
                g.pop("absent_since", None)
            else:
                g.setdefault("absent_since", now)
        if g.get("status") == "stale":
            # 目标重新在场 = relevance 回来了——但只对**邀请型**承诺成立
            # （"跟着我"是持续性承诺，重新见面即恢复）；一次性 add_goal
            # （debug 面板/单次请求）的搁置是终态，复活=用户再提一次（
            # 新时间锚，F3）。2026-09-27：不可见≠不存在，但感知正面确认
            # 在场时，"搁置"判断的邀请目标已失效——复活。
            if (_present and _gtype in ("follow", "approach")
                    and str(g.get("source") or "") == "user_invitation"):
                g.pop("status", None)
                g.pop("demote_since", None)
                g.pop("absent_since", None)
                self._record("goal", f"目标复活（{_gtype} {_tgt} 重新在场）")
                self._save()
                return "active"
            return "stale"
        # 邀请型跟随承诺（用户亲口交办的"跟着我/过来"）：人在场即保活。
        # 生命周期由"人在不在"驱动——她还在身边，"跟着她"没有过期一说，
        # 不按"多久没成功"判死（那是无进展账，会经评分失败惩罚降权）；
        # 人不在则落进下方 absent_since 通道降权/搁置。一次性 add_goal
        # （调试面板/单次请求）不走此门，仍按无进展时长让位（Test F）。
        if (_gtype in ("follow", "approach")
                and str(g.get("source") or "") == "user_invitation"
                and _present):
            return "active"
        is_user = src in ("user_invitation", "user_goal",
                          self.OBTAIN_GOAL_SOURCE) or not src
        if is_user and (now - float(getattr(self, "_user_recent_ts", 0.0))) \
                < self._GOAL_USER_GRACE_S:
            return "active"
        _absent = float(g.get("absent_since") or 0.0)
        if _absent and now - _absent >= self.GOAL_DEMOTE_AFTER_S:
            g["demote_since"] = g.get(
                "demote_since", g.get("absent_since", now))
        last_ok = float(g.get("last_success_at") or 0.0) or 0.0
        created = float(g.get("created_ts") or 0.0) or 0.0
        if created <= 0:
            return "active"          # 无时间锚的旧目标（历史数据）不判死
        span = now - (last_ok if last_ok else created)
        if last_ok and span < self.GOAL_DEMOTE_AFTER_S:
            return "active"
        if not last_ok and span < self.GOAL_DEMOTE_AFTER_S:
            return "active"          # 尝试机会期（DEMOTE 窗口）内不判死；
                                     # 过期即进让位通道，不再挂到 STALE
        if g.get("demote_since"):
            if now - float(g["demote_since"]) >= self.GOAL_STALE_S:
                g["status"] = "stale"
                self._record("goal", f"目标搁置（长时间无进展）: "
                              f"{g.get('type')} {g.get('target') or ''}")
                self._save()
                return "stale"
            return "demoted"
        g["demote_since"] = now
        return "demoted"

    # ── 候选生成（§六：先回答"为什么"）────────────────────

    # ── 候选发现（能力图谱重构 2026-09-20）──────────────────
    # 目标形态：Autonomy 是裁判不是编剧——"什么状态值得行动"是图上的
    # 诱发/驱动边 + 概念 gates + 能力可达性（CapabilityIndex）；本函数
    # 只做发现、参数绑定、去重。2026-09-21 起世界状态 if 链已全部拆除
    # （资源/掉落/结构/生存由 EmbodiedStateMapper 上图 + capability gates
    # 接管）；capability_graph.enabled=false 是 kill switch 而非回滚。

    _GRAPH_BINDERS = {
        "RECOVER": "_bind_recover", "EAT": "_bind_eat",
        "FORAGE": "_bind_forage", "RETREAT": "_bind_retreat",
        "OBSERVE": "_bind_observe", "APPROACH": "_bind_approach",
        "FOLLOW": "_bind_follow", "CONVERSE": "_bind_converse",
        "EXPLORE": "_bind_explore",
        "SEEK_SAFETY": "_bind_seek_safety", "PICKUP": "_bind_pickup",
        "MINE": "_bind_mine", "ATTACK": "_bind_attack",
        "CRAFT": "_bind_craft",
    }

    def _action_candidates(self, percept: dict, events: list,
                           now: float) -> list:
        """候选发现（2026-09-21 完全图谱化，§27：不存在新旧双系统）。

        来源只有三类：
          1. 图谱点亮的行动概念（诱发/驱动/gates/可达性）+ binder 参数绑定
          2. 用户承诺目标透传（(c) 承诺管理，不是世界状态枚举）
          3. 未了结兴趣追问（读 Drive/兴趣图状态）
        世界状态检测（血量/饥饿/威胁/资源/掉落物/结构）已全部经
        EmbodiedStateMapper 上图，由 capability_graph 的候选发现接管。
        capability_graph.enabled=false 是 kill switch（只剩承诺与追问），
        不再是 if 链回滚。"""
        out = []
        ci = getattr(self, "cap_index", None)
        if ci is not None and getattr(ci, "enabled", False):
            out = self._graph_action_candidates(ci, percept, now, events)
        # 用户承诺：交代过的事透传（目标词表是 goal API 契约面）。
        # 缺口目标（source=exploration_gap）不参与——它们是自发目标，
        # 不该挤占用户承诺的透传位（P3/§7；缺口自己下面有专属通道）。
        for g in [x for x in self._goals
                  if x.get("source") != "exploration_gap"][:3]:
            # §P5：目标生命周期评估先行——stale 不自动透传（保留记录，
            # 用户再提即复活）；demoted 降权 0.5（候选照生成，"让位"
            # 由评分实现，不是硬移除）。
            gstate = self._reevaluate_goal(g, percept, now)
            if gstate == "stale":
                continue
            g["last_considered"] = now
            gtype = g.get("type")
            if gtype in INTENT_TYPES:
                spec = dict(g.get("params") or {})
                spec["player"] = (g.get("target") if gtype == "follow"
                                  else spec.get("player"))
                # B4/§4：契约词先实现化再进候选（legacy "follow" 经
                # LEGACY_SKILL_MAP 变成 follow_entity；旧透传直接把契约词
                # 喂给 _feasible_action，在旧词退役后永远死于"不支持"）
                realized = self._realize_goal(gtype)
                if gtype in ("follow", "approach") and g.get("target"):
                    spec.setdefault("entity", g.get("target"))
                cand = self._act(realized, g.get("target"), spec,
                                 motivation="user_goal",
                                 expected="完成用户交代的事",
                                 reason=["用户"],
                                 priority=0.65, urgency=0.6,
                                 explain=f"用户交代过：{gtype} "
                                         f"{g.get('target') or ''}".strip())
                if gstate == "demoted":
                    cand["score_mul"] = 0.5
                out.append(cand)
        # P3/§5 探索缺口 → 观察候选（缺口目标是内部登记，不走上面的
        # 契约词透传；visibility/fails/TTL 都在 _gap_candidates 里裁决）
        try:
            out += self._gap_candidates(percept, now)
        except Exception as e:
            self._cand_warn("gap", e, now)
        # 学习实验 §11：obtain 目标 → 图先验可达性产出下一步候选。
        # 不是第二规划器——链条来自 world_prior 读的运行时 minecraft-data，
        # 这里只把"图上下一步"翻译成普通候选，评分/可行性/承诺走正常管道。
        try:
            out += self._obtain_candidates(percept, now)
        except Exception as e:
            self._cand_warn("obtain", e, now)
        # 未了结的兴趣 → 追问（图读数机制，保留）
        health, food = self._health_food(percept)
        weak = health is not None and health <= 10
        hungry = food is not None and food <= 10
        danger = bool(percept.get("danger_visible"))
        self._interest_ask_candidates(out, percept, self.cfg, weak, hungry,
                                      danger)
        # 学习实验 shield(social_drive)：社交动机候选在实验期不进决策。
        # 不删图、不停驱动节点——SocialDrive 与社交能力照常存在，只是
        # 认知层这一路暂不受理（§16：屏蔽而非切除，退出实验即恢复）。
        if xm is not None and xm.shield("social_drive"):
            out = [c for c in out
                   if str(c.get("motivation") or "") != "social"]
        # dedupe（B4/§4）：同 (action_type,target) 保 **动机地板高** 者，
        # 不再保首条——图谱先产 social(0.3) 的跟随候选时，用户承诺
        # user_goal(0.8) 被压掉是"承诺说了不算"的第二条通路。
        idx, floors, merged = {}, [], []
        for c in out:
            k = (c.get("action_type"), c.get("target"))
            fl = float(self.BASE_MOTIVATION.get(
                str(c.get("motivation") or ""), 0.0))
            if k in idx:
                j = idx[k]
                if fl > floors[j]:
                    merged[j], floors[j] = c, fl
                continue
            idx[k] = len(merged)
            merged.append(c)
            floors.append(fl)
        return merged

    def _realize_goal(self, gtype):
        """目标契约词 → 具身实现词（单一真相源=具身的 realize_goal）。"""
        f = getattr(self._embodiment, "realize_goal", None)
        if callable(f):
            try:
                r = f(gtype)
                if r:
                    return str(r)
            except Exception:
                pass
        return str(gtype)

    # ── 探索缺口 → 目标 → 候选（P3/§5/§6/§7/§12；零 LLM）──────
    # 缺口 = "已知对象 ∧ 未知用途"（prior_knowledge 上图）。本节把它变成
    # 行为：目标登记（_goals 持久化，跨重启存活）→ 在视时转化为观察候选
    # → 真实结果回流（进度/失败计数/放弃）。**没有任何 MC 攻略分支**：
    # 探索什么由图上缺口决定，值不值得由 _score_action 决定。

    # "使用/创造"类成功 = 懂了这个对象的用途的一手证据 → 关缺口。
    # 观察（inspect）与采集（gather）**不关**：描述≠用途，到手≠理解。
    _GAP_CLOSING_ACTIONS = ("craft_item", "craft_batch", "smelt_item",
                            "use_furnace", "furnace_take", "place_block",
                            "place_named_block", "eat_food", "build_wall",
                            "build_floor", "build_simple_shelter",
                            "place_torch", "interact_with_block",
                            "interact_with_entity", "plant_seed",
                            "harvest_crop", "till_soil")

    def _gap_outcome(self, action: dict, success: bool, now: float,
                     result: dict = None):
        """结算 → 缺口目标的 progress/fails/完成/放弃 + 用途类关缺口 + 试做账。"""
        import prior_knowledge as pk
        atype = str(action.get("action_type") or "")
        result = result or {}
        params = action.get("params") or {}
        obj = str(action.get("target") or params.get("item")
                  or params.get("block") or params.get("food")
                  or params.get("entity") or "").strip().lower()
        if not obj or self.kg is None:
            return
        if success and atype in self._GAP_CLOSING_ACTIONS:
            if pk.close_gap(self.kg, self.engine, obj,
                            reason=f"used:{atype}", config=self.config):
                self._record("gap", f"用途已证 {obj}（{atype} 成功）")
        changed = False
        trial_key = None
        for t in ((self.config.get("prior") or {}).get("gap_trials") or []):
            if str(t.get("skill") or "") == atype:
                trial_key = str(t.get("trial") or atype)
                break
        for g in list(self._goals):
            if g.get("source") != "exploration_gap" or \
                    str(g.get("gap_obj") or "") != obj:
                continue
            if trial_key:
                # 试做账（§16 可解释进展）：谁真的试过几次、最近为何失败。
                # 冷却与降权在候选侧消费；这里只记账，不做永久禁令。
                ledger = g.setdefault("trials", {})
                rec = ledger.setdefault(trial_key, {"n": 0})
                rec["n"] = int(rec.get("n", 0)) + 1
                rec["last_at"] = now
                if not success:
                    rec["last_failed"] = now
                    rec["reason"] = str(result.get("reason") or "")[:80]
                changed = True
            if success:
                g["progress"] = int(g.get("progress", 0)) + 1
                changed = True
            else:
                g["fails"] = int(g.get("fails", 0)) + 1
                if int(g["fails"]) >= int(self.cfg.get("max_attempts", 3)):
                    # §7 放弃：反复失败 → 撤目标 + 关缺口（reason=abandoned）。
                    # 同一缺口对象短期内不再复活——open_gap 幂等重建虽然
                    # 可能，但 has_known_use 若因别的证据变化会重开，那是新
                    # 证据驱动，不是循环（§5 无无限重复）。
                    self._goals = [x for x in self._goals if x is not g]
                    pk.close_gap(self.kg, self.engine, obj,
                                 reason="abandoned", config=self.config)
                    self._record("gap", f"放弃探索 {obj}（失败 {g['fails']} 次）")
                changed = True
        if changed:
            self._save()

    def _gap_candidates(self, percept: dict, now: float) -> list:
        """活缺口 → 持久目标（登记/清理）；目标对象在视野内 → 观察候选。"""
        import prior_knowledge as pk
        if self.kg is None or not (self.config.get("prior") or {}).get(
                "enabled", True):
            return []
        gaps = pk.open_gaps(self.kg)
        # gap_obj 一律用**裸对象名**（与结算回流的 target、感知快照的
        # entity/block 名同键）；gid 只在需要图上节点时现算 gap_node_id()。
        gap_objs = {str(x.get("object") or "") for x in gaps} - {""}
        ttl = float(self.cfg.get("goal_ttl_s", 1800))
        maxn = int(self.cfg.get("max_attempts", 3))
        changed = False
        # 清理：缺口已不在图上（被关闭）→ 目标销账；TTL 超时（一直没抓住
        # 机会/对象不再出现）→ 目标放弃 + 关缺口，防止占坑复活循环。
        for g in list(self._goals):
            if g.get("source") != "exploration_gap":
                continue
            obj = str(g.get("gap_obj") or g.get("target") or "")
            if obj not in gap_objs:
                self._goals = [x for x in self._goals if x is not g]
                changed = True
            elif (now - float(g.get("gap_since", now))) > ttl:
                # §16 精细化（真机 2026-09-23）：试都没试过就被 ttl 掐掉，和
                # "试过仍无进展"不是同一种放弃——前者记 ttl_untried，复活线
                # 对它不计额度（她只是没赶上，不是失败了）。
                tried = bool(g.get("trials")) or int(g.get("fails", 0) or 0) > 0 \
                    or int(g.get("progress", 0) or 0) > 0
                pk.close_gap(self.kg, self.engine, obj,
                             reason="abandoned:ttl" if tried
                             else "abandoned:ttl_untried",
                             config=self.config)
                self._goals = [x for x in self._goals if x is not g]
                self._record("gap", f"缺口目标超时放弃 {obj}"
                               + ("" if tried else "（未获尝试机会）"))
                # gap_objs 是本拍开头算的：不丢弃，下面"新缺口→目标"会
                # 拿陈旧集合把刚放弃的缺口原地复活（占坑循环）。
                gap_objs.discard(obj)
                changed = True
            else:
                # §9 目标反向驱动注意（真机 2026-09-23 的"零星行为"根因）：
                # 缺口目标活着=她正在想着这个对象——把缺口节点激活托在注意力
                # 地板上并进活跃前沿。"指向 物品:x"的扩散抬升对象激活，试做
                # 候选的 att 分量由**既有评分体系**自然算出更高分数——不开
                # priority 后门，不碰评分公式。目标死亡（完成/放弃）即静默。
                gid = pk.gap_node_id(obj)
                n = self.kg.get_node(gid)
                if n is not None:
                    floor = float((self.config.get("prior") or {}).get(
                        "gap_attention_floor", 0.9))
                    if float(n.activation or 0.0) < floor:
                        n.activation = floor
                        n.touch()
                    if self.engine is not None:
                        try:
                            self.engine.mark_active([gid])
                        except Exception:
                            pass
        # 新缺口 → 目标登记（同时最多 3 条，不挤占用户承诺的位置）。
        have = {str(g.get("gap_obj")) for g in self._goals
                if g.get("source") == "exploration_gap"}
        for obj in sorted(gap_objs - have):
            if sum(1 for g in self._goals
                   if g.get("source") == "exploration_gap") >= 3:
                break
            self._goals.append({"type": "explore_object", "target": obj,
                                "source": "exploration_gap", "params": {},
                                "text": f"弄清 {obj} 有什么用",
                                "gap_obj": obj, "fails": 0, "progress": 0,
                                "gap_since": now, "created": now_str()})
            self._record("gap", f"+探索目标 {obj}（用途未知）")
            changed = True
        if changed:
            self._goals = self._goals[-20:]
            self._save()
        # 候选：只在对象**真的在场**时生成（避免注定失败的刷屏）；
        # 生物→inspect_entity，方块→inspect_block；
        # 背包里的对象走**试做实验通道**（2026-09-23 §3/§5）：通用先验
        # "任何已知物品可试着放置/持握"（config.prior.gap_trials 数据表，
        # 无逐物答案）→ 绑定真实 capability → 世界如实回执。
        out = []
        ents = {str(e.get("name") or "").lower()
                for e in (percept.get("entities") or [])}
        blks = {str(b.get("name") or "").lower()
                for b in (percept.get("blocks") or [])}
        inv = {str((i or {}).get("name") if isinstance(i, dict) else i or "").lower()
               for i in (percept.get("inventory_items") or [])}
        trials = ((self.config.get("prior") or {}).get("gap_trials")) or []
        for g in self._goals:
            if g.get("source") != "exploration_gap":
                continue
            if int(g.get("fails", 0)) >= maxn:
                continue
            obj = str(g.get("gap_obj") or "")
            if obj in ents:
                skill, spec = "inspect_entity", {"entity": obj}
            elif obj in blks:
                skill, spec = "inspect_block", {"block": obj}
            elif obj in inv:
                # 试做候选：每次尝试的**信息边际**递减（§4/§15），
                # 近期失败冷却降权（§6 条件性负证据，不永久禁止）。
                ledger = g.get("trials") or {}
                for t in trials:
                    rec = ledger.get(t.get("trial") or t.get("skill")) or {}
                    n = int(rec.get("n", 0))
                    if n >= 3:
                        continue
                    p = 0.42 * (0.5 ** n)
                    if now - float(rec.get("last_failed", 0.0)) < 600:
                        p *= 0.3
                    spec = {k: str(v).replace("{obj}", obj)
                            for k, v in (t.get("params") or {}).items()}
                    out.append(self._act(
                        t.get("skill"), obj, spec,
                        motivation="curiosity",
                        expected=str(t.get("why") or "试一下看会发生什么"),
                        reason=[pk.gap_node_id(obj), "探索缺口", "试做实验"],
                        priority=round(p, 3),
                        explain=f"{obj} 在背包里但用途未知，"
                                f"先验说{t.get('why') or '物品可以被试着操作'}"))
                continue
            else:
                continue
            out.append(self._act(skill, obj, spec,
                                 motivation="curiosity",
                                 expected=f"弄清 {obj} 的用途",
                                 reason=[pk.gap_node_id(obj), "探索缺口",
                                         "CuriosityDrive"],
                                 priority=0.45,
                                 explain=f"{obj} 已知存在但用途未知"
                                         f"（探索缺口），想凑近看清楚"))
        return out

    # ── 学习实验 §11/§12：obtain 目标 → 图先验可达性的下一步候选 ──
    # 目标只有一个字段："获得物品 X"。链条**不写在代码里**——
    # world_prior 从运行时 minecraft-data（bot 的 /recipe_for、/block_meta，
    # 版本=服务器协商版本）建配方闭包入图，next_step 纯读图给下一步；
    # 这里把"下一步"翻译成带 motivation=user_goal 的普通候选，与图谱
    # 候选同台评分、同走可行性与承诺管道。**没有采木/挖铁行为树**：
    # 动作名是图边形状（配方-产生/方块-掉落/需要工具）的函数。
    # 实验未开启（无 obtain 目标）时本通道恒空，正常模式零影响。

    OBTAIN_GOAL_SOURCE = "experiment_obtain"

    def _obtain_goals(self) -> list:
        out = [g for g in self._goals
               if str(g.get("source") or "") == self.OBTAIN_GOAL_SOURCE
               and str(g.get("type") or "") == "obtain"
               and str(g.get("target") or "")]
        # P1-F 统一 state consumer（架构诊断修复；config
        # self_model.goal_attention 默认 False=生产行为零变化）:
        # self-graph 的"当前目标"指针若引用已知可获取物品,视为等效
        # obtain 目标——任何写入当前目标的状态自动获得同一通道,
        # 无 goal 专用分支。registry 已有同目标时不重复派生。
        try:
            if (self.config.get("self_model") or {}).get("goal_attention"):
                from self_graph import current_goal
                cg = current_goal(self.kg) or {}
                desc = str(cg.get("desc") or "")
                if desc:
                    have = {str(g.get("target") or "").lower() for g in out}
                    for nid in list(self.kg.nodes):
                        if not str(nid).startswith("物品:"):
                            continue
                        tgt = str(nid)[3:]
                        if tgt and tgt in desc and tgt.lower() not in have:
                            out = out + [{
                                "type": "obtain", "target": tgt,
                                "source": self.OBTAIN_GOAL_SOURCE,
                                "text": f"[self-graph] {desc}",
                                "created": now_str(),
                                "created_ts": time.time()}]
                            break
        except Exception as e:
            logger.debug(f"[Autonomy] self-goal consumer 跳过: {e}")
        return out

    def _inv_counts(self, percept: dict) -> dict:
        inv = {}
        for i in (percept.get("inventory_items") or []):
            if isinstance(i, dict):
                nm = str(i.get("name") or "").lower()
                if not nm:
                    continue
                # 同物多堆求和；显式 count=0 就是 0（旧版 `or 1` 把空堆
                # 当有——2026-09-26 离线验收修），缺字段按 1 保守处理。
                raw = i.get("count", 1)
                try:
                    c = int(float(1 if raw is None else raw))
                except (TypeError, ValueError):
                    c = 0
                inv[nm] = inv.get(nm, 0) + max(c, 0)
        return inv

    def _cand_warn(self, key: str, e: Exception, now: float) -> None:
        """候选生成异常：planner 失败必须可见（§14），但 10s 一拍的持续
        异常不能刷屏——同一通道 300s 最多记一条 warning。"""
        try:
            ts = getattr(self, "_cand_warn_ts", None)
            if ts is None:
                ts = self._cand_warn_ts = {}
            if now - float(ts.get(key, 0.0)) > 300.0:
                ts[key] = now
                logger.warning(f"[Autonomy] {key} 候选生成异常"
                               f"(300s 内仅记一次): {e!r}")
        except Exception:
            pass

    def _obtain_candidates(self, percept: dict, now: float) -> list:
        goals = self._obtain_goals()
        if not goals or self.kg is None:
            return []
        import world_prior as wp
        out = []
        inv = self._inv_counts(percept)
        seen_blocks = {str(b.get("name") or "").lower()
                       for b in (percept.get("blocks") or [])}
        for g in goals:
            target = str(g.get("target") or "").lower()
            # §P5：obtain 目标同样走生命周期——stale 不再自动产出下一步
            # （记录保留，用户再提即复活）；demoted 降权由评分实现。
            gstate = self._reevaluate_goal(g, percept, now)
            if gstate == "stale":
                continue
            g["last_considered"] = now
            # 闭包重建（10 分钟/次，桥断时自然跳过；world_prior 内部
            # 查询有 TTL，重复调用近乎零成本）
            if now - float(self._closure_ts.get(target, 0.0)) > 600.0:
                self._closure_ts[target] = now
                try:
                    import minecraft.bridge as mb
                    st = wp.build_recipe_closure(self.kg, self.engine,
                                                 target, mb)
                    if xm is not None and xm.enabled():
                        xm.xlog("PATH", f"先验闭包 {target}: "
                                  f"边={st.get('edges')} "
                                  f"版本={st.get('mc_version') or '?'}")
                except Exception as e:
                    logger.debug(f"[Autonomy] 闭包重建跳过: {e}")
            # 熔炼在途：先把炉子里的东西取出来（smelt_item 成功≠拿到产物）
            pend = g.get("smelt_pending") or {}
            if pend.get("item") and now - float(pend.get("ts", 0)) < 300:
                # 取要走到炉子边上取，不是在半路取（2026-09-27 真机：
                # 放完炉她又漂去探索，furnace_take 的 16 格搜索连三次
                # furnace_not_found）。炉不在视野而位置已知：远先回炉旁；
                # **近（≤6 格）或无线索就直接取**——nearbyBlocks 列表常
                # 常截断/漏掉炉子（2026-09-27 真机：人就站在炉上，视野里
                # 只有 grass/cobble），"看不见"不能卡死取货；furnace_take
                # 自己有 16 格搜索兜底。
                if not any(t in seen_blocks for t in
                           ("furnace", "blast_furnace", "smoker")):
                    _fsp = self._seen_spot("furnace",
                                           ["blast_furnace", "smoker"])
                    if _fsp is not None:
                        _pos = (percept or {}).get("position") or {}
                        _fd = math.hypot(float(_fsp.get("x", 0)) -
                                         float(_pos.get("x", 0)),
                                         float(_fsp.get("z", 0)) -
                                         float(_pos.get("z", 0)))
                        if _fd > 6:
                            out.append(self._act(
                                "investigate_location", "furnace",
                                {"position": _fsp, "goal": "furnace"},
                                motivation="user_goal",
                                expected=f"回到炉旁取炼好的 {target}",
                                reason=[f"物品:{target}", "熔炼在途", "炉不在视野"],
                                priority=0.7, urgency=0.6,
                                explain="炉子不在视野，先回到放它的地方再取出炼好的",
                                timeout_s=600.0, interruptible=False))
                            continue
                out.append(self._act(
                    "furnace_take", target, {},
                    motivation="user_goal",
                    expected=f"取出炼好的 {target}",
                    reason=[f"物品:{target}", "熔炼在途"],
                    priority=0.7, urgency=0.6,
                    explain=f"{target} 在炉子里炼着，去取出来"))
                continue
            # P3 收敛修复（2026-09-27）：眼前正看见的方块不可能是"本地
            # 没有"——absent 证据只该来自"找过没找到"。把视野里的 seen
            # 方块从 _locally_absent 剔除，防止旧"找不到"压掉"它就在这"。
            _absent = self._locally_absent(now)
            if seen_blocks:
                _absent = _absent - seen_blocks
            step = wp.next_step(self.kg, target, inv, exclude=_absent)
            # 目标达成：销账 + [RESULT]
            if step.get("done"):
                self._goals = [x for x in self._goals if x is not g]
                self._save()
                self._record("goal", f"目标达成：{target}（{step.get('why')}）")
                if xm is not None:
                    xm.xlog("RESULT", f"目标达成 {target}",
                            背包数=inv.get(target, 0))
                continue
            # §9 目标反向驱动注意：物品节点托在注意力地板并进活跃前沿，
            # 扩散自然抬升链条节点激活，评分 attention 分量读到它。
            pid = f"物品:{target}"
            node = self.kg.get_node(pid)
            if node is not None:
                floor = (xm.attention_floor()
                         if xm is not None else 0.9)
                if float(node.activation or 0.0) < floor:
                    node.activation = floor
                    node.touch()
            act = str(step.get("action") or "")
            cands = self._goal_step_candidates(g, target, step, inv,
                                               seen_blocks, now,
                                               exclude=_absent,
                                               percept=percept)
            if self.engine is not None:
                ids = [pid] + [f"物品:{c}" for c in
                               (step.get("chain") or [])[:4]]
                try:
                    live = [i for i in ids if i in self.kg.nodes]
                    if live:
                        self.engine.mark_active(live)
                except Exception:
                    pass
            for c in cands:
                c["reason"] = [r for r in ([pid] + list(c.get("reason") or []))
                               if r][:8]
                if gstate == "demoted":      # §P5：让位信号
                    c["score_mul"] = 0.5
            out += cands
            sig = (act, str(step.get("item")), str(step.get("block")))
            if sig != self._goal_step_sigs.get(target):
                self._goal_step_sigs[target] = sig
                if xm is not None and xm.enabled():
                    xm.xlog("TARGET", f"下一步 {act} {step.get('item') or ''}"
                              + (f"@{step['block']}" if step.get("block")
                                 else ""),
                              why=step.get("why"),
                              链="→".join(str(x) for x in
                                          (step.get("chain") or [])[:6]))
        return out

    def _seen_spot(self, blk: str, variants: list = None) -> dict | None:
        """位置记忆里这个方块（或并列种）上次被看见的坐标。

        记忆由技能层写（探索发现/gather 探测命中 → seen:<名>，文件直写），
        这里只读。没有记忆返回 None，行为退回随机方向探索。"""
        try:
            from skills.base import LocationMemory
            locmem = getattr(self, "_locmem", None)
            if locmem is None:
                locmem = self._locmem = LocationMemory(
                    path=os.path.join(self.data_dir, "mc_locations.json")
                    if getattr(self, "data_dir", None) else None)
            for name in [str(blk)] + [str(v) for v in (variants or [])]:
                e = locmem.get("seen:" + name) or {}
                if e.get("x") is not None:
                    return {"x": e["x"], "y": e.get("y", 64), "z": e["z"]}
        except Exception as e:
            logger.warning(f"[Autonomy] _seen_spot 异常: {e!r}")
        return None

    def _goal_step_candidates(self, g: dict, target: str, step: dict,
                              inv: dict, seen_blocks: set,
                              now: float, exclude: set = None,
                              percept: dict = None) -> list:
        """图上下一步 → 具体技能候选（含熔炉/工作台"持有→放置→使用"的
        现实裁决——放置型工具在场没有、背包有就先放下来）。
        exclude 是 _locally_absent 的降级证据，子目标跳链同样带上，
        否则主链避开的"此地没有"会在子链里被重新选中。"""
        act = str(step.get("action") or "")
        item = str(step.get("item") or "")
        why = str(step.get("why") or "")

        def _mk(atype, tgt, params, exp):
            # 长途步行类动作放宽外层超时（回记忆点/跨场景 300 格级步行，
            # 默认 120s 会在半路把整次采集砍死——2026-09-26 真机实测）；
            # 且**不可中断**：采集在途时被同优先级的探索打断，探索又往外
            # 漂，新的采集再从新位置出发——收敛永远完不成（"瞎跑"主因）。
            # 不可中断 + 自带 600s 超时，走完/挖完才轮到别人。
            return self._act(atype, tgt, params, motivation="user_goal",
                             expected=exp,
                             reason=["实验目标", f"目标:获得{target}"],
                             priority=0.7, urgency=0.6,
                             explain=f"目标 {target}：{why}",
                             timeout_s=600.0 if atype in (
                                 "gather_resource",
                                 "investigate_location") else None,
                             interruptible=atype not in (
                                 "gather_resource",
                                 "investigate_location"))
        if act == "gather":
            blk = str(step.get("block") or item)
            if self._gather_unlocatable(blk, now, seen_blocks):
                # §4 定向探索：这个方块刚交 gather 报回"半径内找不到"，
                # 再交一次只是 0 秒空转（实测 2026-09-25 真机连续同因失败）。
                # 目标已知而找不到 = 去找它，不是站在原地重试。
                # 先回"见过的地方"：探索/采集发现方块时写过位置记忆
                # （seen:<种>），有记忆就直接回去——2026-09-26 场景实测，
                # 橡木明明在出生点，随机方向探索能漂出 260 格。
                _spot = self._seen_spot(
                    blk, [str(b) for b in (step.get("blocks") or [])])
                if _spot is not None:
                    return [_mk("investigate_location", blk,
                                {"position": _spot, "goal": blk},
                                f"回到上次见到 {blk} 的地方")]
                return self._explore_candidates(
                    _mk, blk, f"定向找到 {blk}",
                    blocks=[str(b) for b in (step.get("blocks") or [])])
            params = {"resource": blk, "quantity": 3, "search_radius": 24,
                      "timeout": 600.0}
            # 图上的并列等价来源（world_prior._leaf_siblings）一并交给技能：
            # 执行层逐种现查取最近者，"哪种树真在场"由世界回答（26.1 真机
            # 死盯 cherry_log 一小时，而 24 格内就有橡木）。单来源时不传，
            # 行为与旧路径完全一致。
            _alts = [str(b) for b in (step.get("blocks") or []) if str(b)]
            if len(_alts) > 1:
                params["variants"] = _alts
            return [_mk("gather_resource", blk, params, f"挖到 {item}")]
        if act == "craft":
            if step.get("needs_table") and \
                    "crafting_table" not in seen_blocks:
                if "crafting_table" in inv:
                    return [_mk("place_block", "crafting_table",
                                {"item": "crafting_table"},
                                "放置工作台以便合成")]
                # 台子已放置（位置记忆 seen:crafting_table）→ 不要绕回去
                # 找原木再造一张：近就去台旁合成，远了先寻访过去
                # （2026-09-26 真机：台子放在世界里，规划器却回头合成第二张）。
                _tspot = self._seen_spot("crafting_table")
                logger.info(f"[Autonomy] craft 需要工作台: seen_spot={_tspot}")
                if _tspot is not None:
                    # percept 是只读快照（position 来自感知）；缺失按原点算，
                    # 宁可多走一趟 investigate_location，不抛 NameError 断整条
                    # obtain 候选链（2026-09-26 离线验收定级的 P0）。
                    _pos = (percept or {}).get("position") or {}
                    _td = math.hypot(float(_tspot.get("x", 0)) -
                                     float(_pos.get("x", 0)),
                                     float(_tspot.get("z", 0)) -
                                     float(_pos.get("z", 0)))
                    if _td > 6:
                        return [_mk("investigate_location", "crafting_table",
                                    {"position": _tspot,
                                     "goal": "crafting_table"},
                                    "回到工作台旁")]
                    return [_mk("craft_item", item,
                                {"item": item, "count": 1}, f"合成 {item}")]
                import world_prior as wp
                sub = wp.next_step(self.kg, "crafting_table", inv,
                                   exclude=exclude)
                if not sub.get("done"):
                    return self._goal_step_candidates(
                        g, target,
                        dict(sub, why=f"需要工作台：{sub.get('why') or ''}"),
                        inv, seen_blocks, now, exclude=exclude,
                        percept=percept)
            return [_mk("craft_item", item,
                        {"item": item, "count": 1}, f"合成 {item}")]
        if act == "smelt":
            tool = str(step.get("tool") or "")
            if tool and tool not in seen_blocks:
                # 炉不在视野时的三岔裁决（2026-09-27 真机：她放着一台炉
                # 又在别处盲熔/差点放第二台——seen:furnace 因结算字段缺陷
                # 根本没记上，见 _on_action_settled 兜底修复）。有位置记忆
                # → 远则先回去、近则落到下面让技能就地找；没记忆而背包有
                # → 放下；两样都没有 → 把取得工具本身当子目标。
                _fsp = self._seen_spot(tool)
                if _fsp is not None:
                    _pos = (percept or {}).get("position") or {}
                    _fd = math.hypot(float(_fsp.get("x", 0)) -
                                     float(_pos.get("x", 0)),
                                     float(_fsp.get("z", 0)) -
                                     float(_pos.get("z", 0)))
                    if _fd > 6:
                        return [_mk("investigate_location", tool,
                                    {"position": _fsp, "goal": tool},
                                    f"回到已放置的 {tool} 旁")]
                elif tool in inv:
                    return [_mk("place_block", tool, {"item": tool},
                                f"放置 {tool} 以便熔炼")]
                else:
                    # 工具（熔炉）既没在场、没有位置记忆也不在背包：
                    # 先把工具本身当子目标
                    import world_prior as wp
                    sub = wp.next_step(self.kg, tool, inv, exclude=exclude)
                    if not sub.get("done"):
                        return self._goal_step_candidates(
                            g, target,
                            dict(sub, why=f"熔炼需要 {tool}：{sub.get('why') or ''}"),
                            inv, seen_blocks, now, exclude=exclude,
                            percept=percept)
            inputs = step.get("inputs") or {}
            smelt_in = str(g.get("smelt_input") or "")
            if not smelt_in or int(inv.get(smelt_in, 0) or 0) <= 0:
                smelt_in = next(iter(inputs), item)
            return [_mk("smelt_item", item,
                        {"item": smelt_in, "count": 1},
                        f"熔炼 {smelt_in} 得 {item}")]
        if act == "explore":
            goal_word = str(item or target)
            # 目标位置已知（见过）→ 直接寻访，不随机漂（2026-09-26 真机：
            # 探索连轴转能把 bot 漂出几百格，而橡木一直在出生点）。
            _spot = self._seen_spot(goal_word,
                                    [str(b) for b in
                                     (step.get("blocks") or [])])
            if _spot is not None:
                return [_mk("investigate_location", goal_word,
                            {"position": _spot, "goal": goal_word},
                            f"回到上次见到 {goal_word} 的地方")]
            try:
                import world_prior as _wp
                blocks = [str(b) for b in _wp._leaf_siblings(
                    self.kg, list(step.get("chain") or []), goal_word)]
            except Exception:
                blocks = [goal_word]
            return self._explore_candidates(_mk, goal_word,
                                            f"定向找到 {item}",
                                            blocks=blocks)
        return []

    def _gather_unlocatable(self, blk: str, now: float,
                            seen_blocks=None) -> bool:
        """这个方块近期的 gather 是否刚因"找不到"失败过。

        判据用失败回执，不用 nearbyBlocks 判"在不在视野里"：bot 侧那份是
        身体周围 5×4×5 采样再按计数取前 6（minecraft_bot/bot.js:294-305），
        半径 24 的 gather 完全走得到的方块基本都不在里面 —— 拿它当条件
        等于几乎永不 gather。窗口期内改做定向探索，窗口过后允许再试一次
        gather（世界会变：走动、区块加载、别人挖开遮挡）。

        反向也成立：目标出现在这份近身采样里 = 就在脚边，探索的目的已达到，
        立刻回到 gather，不必等窗口过期（探索成功本身不清失败账，因为
        "逛了一圈"不等于"看见了铁矿"）。"""
        if seen_blocks and blk in seen_blocks:
            return False
        att = self._attempts.get(self._attempt_key("gather_resource", blk)) or {}
        if not att:
            return False
        window = float(self.cfg.get("explore_unlocatable_s", 300))
        return (now - float(att.get("last_ts") or 0.0)) <= window

    def _locally_absent(self, now: float) -> set:
        """近期反复"半径内找不到"的方块名集合 —— 交给 world_prior 当**排序
        降级证据**（不是禁令：备选全被排除时照旧返回，窗口到期自动失效）。

        为什么需要：真机 2026-09-25 14:24–14:43 实测，闭包给出的采集目标
        一直是 cherry_log，而这片原子上没有樱花树 —— gather 报
        not_found_in_radius（20 次，conf 0.909）→ §4 改派定向探索 →
        探索正常结束（ok，走了 100+ 秒）也没看见 → 下一拍又 gather
        cherry_log。目标本身不可达时，"已知而看不见"的循环节省不了任何
        一步。降级读的是失败回执（图/账本里的证据），代码里没有一个
        木材或矿石的名字；同一条机制对铁矿、对任何物种一视同仁。"""
        out = set()
        window = float(self.cfg.get("absent_window_s", 1800))
        need = max(2, int(self.cfg.get("absent_min_fails", 3)))
        for key, att in list(self._attempts.items()):
            if not key.startswith("gather_resource@"):
                continue
            if int(att.get("count", 0) or 0) < need:
                continue
            if (now - float(att.get("last_ts") or 0.0)) > window:
                continue
            if str(att.get("last_reason") or "").split(":", 1)[0] != \
                    "not_found_in_radius":
                continue
            out.add(key.split("@", 1)[1])
        return out

    def _explore_candidates(self, mk, goal_word: str, expected: str,
                            blocks: list = None) -> list:
        """定向探索候选：方向按"最少走过"轮换（同一目标反复朝同一方向探是
        原地打转，不是探索）。mk 是 _goal_step_candidates 里的候选工厂。
        blocks 是图上的并列等价来源（gather 的 variants 同源）：探索的到
        达检查按它逐个扫——2026-09-26 场景实测，目标词 cherry_log 锁死单
        物种，出生点旁真立着 oak_log 也"看不见"，于是一路向外跑丢。"""
        direction = min(self._explore_dirs,
                        key=lambda k: self._explore_dirs[k])
        self._explore_dirs[direction] += 1
        params = {"goal": goal_word, "direction": direction,
                  "max_time": float(self.cfg.get(
                      "explore_interval_s", 240)),
                  # 缰绳（2026-09-26 深夜）：目标导向探索的绳长减半——
                  # bot 走出服务器加载区块（玩家视距外 ~150 格）后方块数据
                  # 全空，实体移动不再被处理，冻结在无人区。
                  "max_distance": float(self.cfg.get(
                      "explore_max_distance", 64)) * 0.5,
                  **self._night_params()}
        _blocks = [str(b) for b in (blocks or []) if str(b)]
        if len(_blocks) > 1:
            params["goal_blocks"] = _blocks
        return [mk("explore_area", goal_word, params, expected)]

    def _night_params(self) -> dict:
        """实验模式的豁免覆盖到**所有**探索族候选（用户 2026-09-27 定：
        "实验模式全部忽略这些和实验无关的因素，包括夜拦"）。此前这条只
        接在定向搜索（_explore_candidates）上，好奇探索/空闲踱步没带参数
        → 吃技能默认 night_stop=True → 实验开着夜里照样全线罚站，只能守
        在用户旁边。这不是默认行为变更、也不进配置层：shield 未开回到
        night_stop=True，技能侧既有安全语义一字未动；danger（敌对 8 格内）
        与此无关照停。"""
        return {"night_stop": not (xm is not None
                                   and xm.shield("night_preempt"))}

    def _preempt_pause(self, cand: dict, now: float) -> float:
        """该候选是否正处在"世界拦截"的停顿期。返回还需等的秒数（0=可提）。

        与 _attempts 的退避账**故意分开**：那条记的是"这个动作我对这个目标
        做不到"（进因果先验、参与评分），这条记的是"世界此刻不让"（夜里、
        危险）——2026-09-25 已定性：暂态失败绝不能进因果账，否则一个夜晚的
        拦截史会永久压低探索族（"呆呆站着"事故的同一条教训）。所以这里只是
        节拍，成功一次即清零。"""
        key = self._attempt_key(str(cand.get("action_type") or ""),
                                cand.get("target"))
        pp = self._preempt.get(key) or {}
        return max(0.0, float(pp.get("next_ts") or 0.0) - now)

    def _xlog_preempt_gap(self, cand: dict, now: float):
        """停顿期只在"进入新一轮停顿"时记一条 [GAP]，不每拍刷屏。"""
        if xm is None or not xm.enabled():
            return
        key = self._attempt_key(str(cand.get("action_type") or ""),
                                cand.get("target"))
        pp = self._preempt.get(key) or {}
        nxt = float(pp.get("next_ts") or 0.0)
        if self._preempt_gap_logged.get(key) == nxt:
            return
        self._preempt_gap_logged[key] = nxt
        xm.xlog("GAP", f"世界拦截 {cand.get('action_type')}"
                f"@{cand.get('target') or ''}：{pp.get('reason') or 'preempted'}"
                f"，停顿至节拍到期", 已撞次数=pp.get("count"),
                剩余秒=round(max(0.0, nxt - now)))

    def _maybe_goal_fallback(self, percept: dict, now: float) -> dict:
        """§2 随机漫步降级后的第一优先：有 obtain 目标而主决策路径无行动
        （无候选/低于阈值）时，直接按图上的"下一步"提议——随机闲逛排在
        目标之后。照走 _feasible_action（锁/冷却/能力一样不绕），只是不
        再要求评分过阈值；目标也不可行时才返回 {} 交给 _maybe_amble。"""
        if not self._obtain_goals():
            return {}
        try:
            cands = self._obtain_candidates(percept, now)
        except Exception as e:
            logger.debug(f"[Autonomy] goal fallback 候选跳过: {e}")
            return {}
        for cand in cands:
            w = self._preempt_pause(cand, now)
            if w > 0:
                # 世界此刻不让（夜里/危险）：站着等，不是每 12 秒撞一次墙。
                # 撞墙的账已经交给 _preempt 的指数停顿，这里只负责不再重提。
                self._xlog_preempt_gap(cand, now)
                continue
            try:
                ok, _r = self._feasible_action(cand, percept, now)
            except Exception:
                ok = False
            if not ok:
                continue
            self.phase = ST_ACTING
            self._state_reason = cand.get("explain", "")
            self._record("intent", f"(目标导向) {cand['action_type']}"
                         f"@{cand.get('target')}")
            prop = self.actions.propose(cand, source="autonomy", now=now)
            if prop.get("started"):
                self._last_action_ts = now
                self._consume_intention(cand)
                if xm is not None and xm.enabled():
                    xm.xlog("ACTION", f"{cand['action_type']}"
                            f"@{cand.get('target') or ''}", 来源="goal-directed",
                            score=cand.get("score"))
                return {"acted": True, "reason": "goal_fallback",
                        "intent": cand["action_type"]}
        return {}

    def _graph_action_candidates(self, ci, percept: dict,
                                 now: float, events: list) -> list:
        """被图谱点亮的具身行动概念 → 带参数的候选（参数绑定层）。"""
        try:
            drafts = ci.discover_candidates(live=percept)
        except Exception as e:
            logger.debug(f"[Autonomy] 图谱候选发现失败: {e}")
            return []
        out = []
        for d in drafts:
            binder_name = self._GRAPH_BINDERS.get(str(d.get("concept") or ""))
            binder = getattr(self, binder_name, None) if binder_name else None
            if binder is None:
                # 无专属 binder 的新概念：默认绑定照样成候选
                # （验收：注册 executor + 接边即入链，autonomy 零改动）
                out.append(self._act(
                    d.get("action_type"), None, {},
                    motivation=d.get("motivation") or "cognitive_state",
                    expected="", reason=d.get("reason"),
                    priority=d.get("priority"), urgency=d.get("urgency"),
                    explain=f"行动概念 {d.get('concept')} 被认知场点亮"))
                continue
            try:
                out += binder(d, percept, now, events) or []
            except Exception as e:
                logger.debug(f"[Autonomy] binder {d.get('concept')} 失败: {e}")
        return out

    def _health_food(self, percept: dict):
        """血量/饥饿：图槽位真相源优先，percept 实时缓存回退。"""
        health = food = None
        try:
            with self.kg._lock:
                hn = self.kg.nodes.get("Haru的血量")
                fn = self.kg.nodes.get("Haru的饥饿")
                hv = (hn.extra_attrs or {}).get("value") if hn else None
                fv = (fn.extra_attrs or {}).get("value") if fn else None
            health = float(hv) if hv not in (None, "") else None
            food = float(fv) if fv not in (None, "") else None
        except Exception:
            pass
        if health is None:
            h = percept.get("health")
            health = float(h) if h is not None else None
        if food is None:
            f = percept.get("food")
            food = float(f) if f is not None else None
        return health, food

    def _nearest_threat(self, percept: dict):
        """最近敌对（(b) 安全窗策略：danger_distance×2.5 撤离窗保留）。"""
        cfg = self.cfg
        threats = [e for e in percept.get("entities", [])
                   if str(e.get("name", "")).lower()
                   in cfg["hostile_entities"]
                   and float(e.get("dist", 99))
                   <= float(cfg["danger_distance"]) * 2.5]
        if not threats:
            return None
        return min(threats, key=lambda e: float(e.get("dist") or 99))

    # ── 各概念的参数绑定（不是"要不要行动"的判断——那是图上 gates
    #    与可达性决定的；这里只把概念落到一次具体执行的参数上）──

    def _bind_recover(self, d, percept, now, events=None):
        health, _ = self._health_food(percept)
        return [self._act(d["action_type"], "self",
                          {"target_health": 16},
                          motivation="survival", expected="血量恢复",
                          reason=["Haru的血量", f"health:{health}"],
                          priority=0.85, urgency=0.9,
                          explain=f"血量只有 {health}，先恢复")]

    def _bind_eat(self, d, percept, now, events=None):
        _, food = self._health_food(percept)
        sev = min(1.0, 0.55 + (1.0 - float(food or 0) / 20.0) * 0.45)
        return [self._act(d["action_type"], "self", {},
                          motivation="survival", expected="饥饿缓解",
                          reason=["Haru的饥饿", f"food:{food}"],
                          priority=0.8, urgency=sev,
                          explain=f"饥饿 {food}，先吃点东西")]

    def _bind_forage(self, d, percept, now, events=None):
        _, food = self._health_food(percept)
        sev = min(1.0, 0.55 + (1.0 - float(food or 0) / 20.0) * 0.45)
        return [self._act(d["action_type"], None, {"timeout": 120},
                          motivation="survival", expected="获得食物",
                          reason=["Haru的饥饿", f"food:{food}"],
                          priority=0.7, urgency=max(0.6, sev),
                          explain=f"饥饿 {food} 且背包可能没吃的，去找食物")]

    def _bind_retreat(self, d, percept, now, events=None):
        nearest = self._nearest_threat(percept)
        if nearest is None:
            return []
        name = nearest.get("name")
        dist = float(nearest.get("dist") or 99)
        urg = 0.9 if dist <= float(self.cfg["danger_distance"]) else 0.8
        return [self._act(d["action_type"], name,
                          {"from": name, "distance": 10},
                          motivation="survival", expected="拉开安全距离",
                          reason=[self._unknown_node_id("entity", name)],
                          priority=0.85, urgency=urg,
                          explain=f"{name} 在 {dist} 格，先拉开距离")]

    def _bind_observe(self, d, percept, now, events=None):
        out = []
        for ent in percept.get("unknown_entities", [])[:3]:
            name = ent.get("name")
            uid = self._unknown_node_id("entity", name)
            out.append(self._act(d["action_type"], name,
                                 {"entity": name},
                                 motivation="curiosity",
                                 expected="看清它是什么",
                                 reason=[uid, "CuriosityDrive"],
                                 explain=f"刚注意到不认识的生物 "
                                         f"{ent.get('displayName') or name}"
                                         f"（距离 {ent.get('dist')}），想先看清它"))
        return out

    def _bind_approach(self, d, percept, now, events=None):
        health, food = self._health_food(percept)
        if (health is not None and health <= 10) or \
                (food is not None and food <= 10):
            return []          # 生存优先时不靠近（安全策略，(b) 类）
        out = []
        for ent in percept.get("unknown_entities", [])[:3]:
            name = ent.get("name")
            uid = self._unknown_node_id("entity", name)
            out.append(self._act(d["action_type"], name,
                                 {"entity": name,
                                  "keep_distance":
                                      float(self.cfg["approach_distance"])},
                                 motivation="curiosity",
                                 expected="近距离观察",
                                 reason=[uid, "CuriosityDrive"],
                                 explain=f"想靠近 {ent.get('displayName') or name}"
                                         f" 看个清楚"))
        return out

    def _bind_follow(self, d, percept, now, events=None):
        cfg = self.cfg
        if percept.get("danger_visible"):
            return []
        companions = percept.get("players", [])
        named = [p for p in companions if not cfg["companion_names"]
                 or p.get("name") in cfg["companion_names"]]
        if not named:
            return []
        target = named[0]
        # 2026-09-24 "跟随占大头"修复：已经贴在一起时不再提出跟随——跟一个
        # 挂机的同伴会把决策拍整块吃掉（follow 一跑就是十分钟，其余行为全
        # 被挤在后面）。陪伴的真相源仍在 SocialDrive/孤独信号里：人走远
        # （>follow_skip_dist）下一拍自然重新提出跟随，没有名单也没有禁令。
        try:
            _d = float(target.get("dist") or 99.0)
        except (TypeError, ValueError):
            _d = 99.0
        if _d <= float(cfg.get("follow_skip_dist", 4.0)):
            return []
        # reason 是证据基底不只是文案：附近的玩家/SocialDrive 进基底后，
        # attention 分量读到真实图激活（B4/§4——旧版只剩"用户"两个字，
        # 图上证据全丢）。2026-09-24：自主跟随带 release_when_close——
        # 追到身边就了结，不是挂 10 分钟皮套寸步不离（承诺透传不带此参数，
        # 陪走照旧陪到超时）。
        return [self._act(d["action_type"], target.get("name"),
                          {"entity": target.get("name"),
                           "release_when_close": True},
                          motivation="social", expected="在一起",
                          reason=["用户", "附近的玩家", "SocialDrive"],
                          explain=f"{target.get('name')} 在附近"
                                  f"（{target.get('dist')} 格），想跟着一起走")]

    def _bind_converse(self, d, percept, now, events=None):
        """红线：没有准备好的话就不说（communicate 无文本必失败）。"""
        companions = percept.get("players", [])
        if not companions or percept.get("danger_visible"):
            return []
        pending = [g for g in self._goals
                   if g.get("type") == "communicate" and g.get("text")]
        if not pending:
            return []
        name = companions[0].get("name")
        return [self._act(d["action_type"], name,
                          {"player": name,
                           "text": str(pending[0]["text"])[:120]},
                          motivation="social", expected="把话说出口",
                          reason=["用户", "附近的玩家", "SocialDrive"],
                          explain="有一条准备好的话要说给同伴听")]

    def _bind_seek_safety(self, d, percept, now, events=None):
        # 寻安全：动机来自"生存需求"图状态（mapper 写入），执行=撤离到
        # 反方向安全位；这里是参数绑定，触发判断已在 gates/诱发边完成
        return [self._act(d["action_type"], "self", {},
                          motivation="survival",
                          expected="脱离危险环境",
                          reason=["生存需求", "Haru的位置"],
                          priority=0.9, urgency=0.95,
                          explain="生存压力高，先找安全位置")]

    def _bind_pickup(self, d, percept, now, events=None):
        return [self._act(d["action_type"], None, {"radius": 6},
                          motivation="resource_opportunity",
                          expected="捡到东西",
                          reason=["地面物品"],
                          priority=0.5, urgency=0.45,
                          explain="地面有掉落物（图状态），顺手捡起来")]

    def _bind_explore(self, d, percept, now, events=None):
        cfg = self.cfg
        if percept.get("danger_visible"):
            return []
        health, _ = self._health_food(percept)
        if health is not None and health <= 10:
            return []
        last = self._recent_by_key.get("explore@world", 0.0)
        if now - last < float(cfg["explore_interval_s"]):
            return []
        goal = self._pick_explore_goal(events or [])
        direction = min(self._explore_dirs,
                       key=lambda k: self._explore_dirs[k])
        self._explore_dirs[direction] += 1
        if goal and goal != "any":
            return [self._act("explore_area", goal,
                              {"goal": goal, "direction": direction,
                               "max_time": float(cfg["explore_interval_s"]),
                               **self._night_params()},
                              motivation="curiosity",
                              expected=f"找到 {goal}",
                              reason=["CuriosityDrive", "Haru的位置"],
                              priority=0.4, urgency=0.35,
                              explain=f"想去看看能不能找到"
                                      f"{GOAL_ALIASES.get(goal, goal)}"
                                      f"（往 {direction} 方向）")]
        return [self._act("explore_direction", direction,
                          {"direction": direction,
                           "max_time": float(cfg["explore_interval_s"]),
                           **self._night_params()},
                          motivation="curiosity", expected="了解周边",
                          reason=["CuriosityDrive", "Haru的位置"],
                          explain=f"有一段时间没到处看看了（走 {direction} 方向）")]

    def _bind_mine(self, d, percept, now, events=None):
        """MINE 参数绑定：从当前可见方块里选可采集物种（mapper 激活过
        的优先）。"该不该挖/有没有授权"在 kernel 与图上保护关系里。"""
        try:
            import mc_knowledge as mck
        except Exception:
            mck = None
        best, best_act = None, -1.0
        for b in (percept.get("blocks") or []):
            name = str(b.get("name") or "").lower()
            if not name:
                continue
            if mck is not None and self.kg is not None:
                try:
                    if mck.is_protected(self.kg, name):
                        continue
                except Exception:
                    pass
            act = 0.0
            if self.kg is not None:
                nd = self.kg.nodes.get(name)
                act = float(getattr(nd, "activation", 0.0) or 0.0)                     if nd is not None else 0.0
            if act > best_act:
                best, best_act = name, act
        if best is None:
            return []
        reason = [self._unknown_node_id("block", best),
                  best, "CuriosityDrive", "Haru的位置"]
        # P3/§4-5：这个方块本身有"用途未知"缺口 → 缺口节点进基底
        # （采集=取样研究；attention 分量直接读缺口激活）
        try:
            import prior_knowledge as pk
            if self.kg is not None:
                _gn = self.kg.get_node(pk.gap_node_id(best))
                if _gn is not None and not (_gn.extra_attrs or {}).get("closed"):
                    reason.append(pk.gap_node_id(best))
        except Exception:
            pass
        cand = self._act(d["action_type"] or "gather_resource", best,
                         {"resource": best, "quantity": 2,
                          "search_radius": 16},
                         motivation=d.get("motivation")
                         or "resource_opportunity",
                         expected=f"获得 {best}",
                         reason=reason,
                         priority=d.get("priority"),
                         urgency=d.get("urgency"),
                         explain=f"附近有 {best}，值得采集")
        # 事件显著度（机会刚发生：resource_detected 的 urgency 直接进
        # 评分，数据来自感知差分事件流，不是价值表裁决）
        for ev in (events or []):
            if ev.get("type") == "resource_detected"                     and str(ev.get("resource") or "").lower() == best:
                cand["_salience"] = float(ev.get("urgency") or 0.0)
                break
        return [cand]

    def _bind_attack(self, d, percept, now, events=None):
        """ATTACK 参数绑定（触发判断在 gates：敌对近身+自身状态良好；
        执行核验在 kernel：目标必须有危险因果边）。不点名不打。"""
        t = self._nearest_threat(percept)
        if t is None:
            return []
        name = t.get("name")
        return [self._act(d["action_type"] or "attack_entity", name,
                          {"entity": name},
                          motivation=d.get("motivation") or "survival",
                          expected="解除威胁",
                          reason=[self._unknown_node_id("entity", name),
                                  "生存需求"],
                          priority=d.get("priority") or 0.6,
                          urgency=d.get("urgency") or 0.6,
                          explain=f"{name} 贴脸且我状态尚可，考虑反制")]

    def _bind_craft(self, d, percept, now, events=None):
        """CRAFT 参数绑定：候选物品来自"可制作物品"hub 的 hints——hints 由
        配方层从 bot 的**真实配方回包**写入（环境事实，§8），原料可行性由
        执行侧核验（/craft 的 missing_ingredients 是真话）。这里不硬编码
        任何合成策略："能做出什么"来自环境，"值不值得做"归评分。"""
        out = []
        try:
            with self.kg._lock:
                hub = self.kg.get_node("可制作物品") if self.kg else None
                hints = list((hub.extra_attrs or {}).get("hints") or []) \
                    if hub is not None else []
        except Exception:
            return []
        try:
            from skills.crafting import resolve_recipe
        except Exception:
            resolve_recipe = None
        for h in hints[:3]:
            item = str(h.get("item") or "")
            if not item:
                continue
            if resolve_recipe is not None:
                # 注意形状：resolve_recipe 返回 (规范名, 配方|None)——非空
                # 元组恒真，必须取 [1]，否则"做不到"的过滤形同虚设。
                if not (resolve_recipe(item) or (None, None))[1]:
                    continue      # 执行侧解析不到配方 = 做不到
            out.append(self._act(
                d["action_type"] or "craft_item", item,
                {"item": item, "count": 1},
                motivation=d.get("motivation") or "resource_opportunity",
                expected=f"合成 {item}",
                reason=["可制作物品", f"配方:{item}"],
                priority=d.get("priority"), urgency=d.get("urgency"),
                explain=f"背包材料据世界配方能做出 {item}，想试试做出来"))
        return out


    def _pick_explore_goal(self, events: list) -> str:
        """探索目标从当前事件/需求来：最近发现过资源痕迹 → 带目标探索。
        §17 泛化：不枚举"哪些矿石值得追"。resource_detected 的 resource 本身
        就是真实方块名（iron_ore/deepslate_iron_ore/coal_ore…），直接原样传给
        explore_area——命名目标词汇表（EXPLORE_GOALS）命中就走表，未命中由
        _check_goal 的通用兜底按方块名找。两条路都覆盖，无需剥后缀。"""
        for ev in events:
            if ev.get("type") == "resource_detected":
                res = str(ev.get("resource") or "").strip().lower()
                if res:
                    return res
        return "any"

    @staticmethod
    def _act(action_type, target, params, motivation, expected, reason,
             priority=None, urgency=None, explain="", interruptible=True,
             timeout_s=None) -> dict:
        return {
            "action_type": action_type,
            "target": str(target) if target is not None else None,
            "params": params or {},
            "motivation": motivation,
            "expected_effect": expected,
            "reason": [r for r in (reason or []) if r],
            "priority": priority,
            "urgency": urgency,
            "explain": explain,
            "interruptible": interruptible,
            "timeout_s": timeout_s,
        }

    def _unknown_node_id(self, kind: str, name) -> str:
        prefix = {"entity": "UnknownEntity_", "block": "UnknownBlock_",
                  "player": "UnknownPlayer_"}.get(kind, "UnknownEntity_")
        return f"{prefix}{name}"

    def _interest_ask_candidates(self, out: list, percept: dict, cfg: dict,
                                 weak: bool, hungry: bool, danger: bool):
        if danger or weak or hungry:
            return
        if not percept.get("players"):
            return
        drive = self._drive_activation("CuriosityDrive")
        if drive < float(cfg.get("ask_min_drive", 1.5)):
            return
        best, best_level = None, float(cfg.get("ask_interest_min", 0.3))
        if self.kg is not None:
            with self.kg._lock:
                for nid, node in self.kg.nodes.items():
                    if nid.startswith(("UnknownEntity_", "UnknownPlayer_",
                                       "UnknownBlock_")):
                        continue
                    it = (node.extra_attrs or {}).get("interest")
                    if isinstance(it, dict):
                        lv = float(it.get("level", 0.0))
                        if lv > best_level:
                            best, best_level = nid, lv
        if best is None:
            return
        out.append(self._act(
            "communicate", "用户",
            {"text": f"{best}是什么？", "explore_ask": True},
            motivation="curiosity", expected="搞清楚它",
            reason=[best, "CuriosityDrive"],
            explain=f"还想搞清楚 {best}（兴趣 {best_level:.2f}），趁你在场问一句"))

    def _drive_activation(self, drive_id: str) -> float:
        if self.kg is None:
            return 0.0
        node = self.kg.nodes.get(drive_id)
        return float(getattr(node, "activation", 0.0) or 0.0) if node else 0.0

    # ── 动机匹配（需求/驱动力 → 候选的 drive 分量）────────

    # 动机基线地板：需求追踪缺失/低位时，动机本身仍有一个诚实的下限
    # （生存欲望不会因为没记录就归零；资源有用的判断是幸存者的常识）。
    BASE_MOTIVATION = {
        "survival": 0.9,      # 生理/安全需求是硬信号（沿旧语义 withdraw=0.9）
        "resource_opportunity": 0.4,
        "social": 0.3,
        "user_goal": 0.8,
        "user_commitment": 0.75,
        "curiosity": 0.0,       # 好奇完全由图驱动（Drive 节点/未知对象），无地板
    }

    def _motivation_value(self, motivation: str) -> float:
        v = float(self.BASE_MOTIVATION.get(motivation, 0.0))
        # 需求层（internal_state，0~1）。R2 P9：原来的四个 `if motivation == ...`
        # 变成 config/出厂表里的一张**多对多**映射（一个动机可以吃多维需求，
        # 一维需求也可以喂多个动机），加一维不改代码（禁令 2 的同一刀法）。
        # 读的是 `need_salience()`（紧迫度或离靶距离）而不是裸 `level`：
        # 每维需求都有 `ideal_range`，"在理想区间内的波动"不该被当饥饿去
        # 顶高行动动机——这也是 `urgency` 在 R2 里的第一个真消费者（审计 D-8）。
        if getattr(self, "internal_state", None) is not None:
            for row in (self.cfg.get("motivation_needs") or {}).get(
                    motivation) or []:
                try:
                    s = float(self.internal_state.need_salience(
                        str(row.get("need"))))
                    v = max(v, min(1.0, s * float(row.get("coef", 1.0))
                                   + float(row.get("bias", 0.0))))
                except Exception:
                    continue
        # 驱动力层（图上 Drive 节点，0~5 → 0~1）
        curiosity = min(1.0, self._drive_activation("CuriosityDrive") / 5.0)
        if motivation == "curiosity":
            v = max(v, curiosity)
        if motivation == "social":
            # B4/§4：镜像 curiosity 分支——社交动机的真相源是图上的
            # SocialDrive 激活；心情只是小加数。旧版 `max(v, 0.5+mood)`
            # 是 mood 捷径 hack：SocialDrive 从不被读，孤独压满的驱动
            # 抬不动社交动机，心情好的闲人反而逢人就聊。
            soc = min(1.0, self._drive_activation("SocialDrive") / 5.0)
            mood = 0.0
            if self.persona is not None:
                try:
                    mood = float(self.persona.current_mood().get("valence", 0.0))
                except Exception:
                    mood = 0.0
            v = max(v, min(1.0, soc + 0.1 * max(0.0, mood)))
        return min(1.0, v)

    # ── 评分（分量可解释；含因果先验）─────────────────────

    _FAMILY_KEYS = {"explore_direction", "explore_area", "explore_unknown_region",
                    "investigate_location", "revisit_location", "return_to_location"}

    @staticmethod
    def _attempt_key(action_type: str, target) -> str:
        """重试/冷却的记账键：探索族共享（方向轮换≠新目标），其余按动作@目标。"""
        atype = str(action_type or "")
        if atype in AutonomousLoop._FAMILY_KEYS:
            return f"explore@world"
        return f"{atype}@{target}"

    def _threshold(self) -> float:
        """行动决策阈值：受认知网络/激素连续调制（§五：阈值不是静态
        magic number），modulation 缺位回退 cfg 值。"""
        m = getattr(self, "modulation", None)
        if m is not None:
            try:
                v = m.get("action.score_threshold")
                if v is not None:
                    return float(v)
            except Exception:
                pass
        return float(self.cfg["score_threshold"])

    def _consume_intention(self, cand: dict):
        """行动承诺消耗意图（统一认知循环）：意图被行动实现后强度大减——
        没有消耗机制，一个意图会永远支持候选、驱动重复行动。表达通路本来
        就有 expressed 终态，这里是行动通路的对应物。"""
        if self.kg is None:
            return
        reason = set(str(r) for r in (cand.get("reason") or []))
        if cand.get("target"):
            reason.add(str(cand.get("target")))
        try:
            with self.kg._lock:
                for n in self.kg.nodes.values():
                    ea = n.extra_attrs or {}
                    if ea.get("type") not in ("intention",
                                              "communication_intention"):
                        continue
                    if ea.get("status") not in ("forming", "ready"):
                        continue
                    if set(ea.get("basis") or []) & reason:
                        ea["activation"] = round(
                            float(ea.get("activation", 0)) * 0.4, 4)
                        ea["consumed_count"] = \
                            int(ea.get("consumed_count", 0)) + 1
                        ea["consumed_by"] = cand.get("action_type")
        except Exception:
            pass

    def _decision_signature(self, percept: dict, events: list) -> tuple:
        """决策签名：世界关键读数 + 新事件 + 活跃行动意图的图状态。
        三者全没变 → 这一拍没什么可重新决定的。"""
        sig = (percept.get("health"), percept.get("food"),
               percept.get("danger_visible"),
               bool(percept.get("players")),
               tuple(percept.get("unknown_entities") or [])[:3],
               tuple(e.get("type") for e in events[:4]))
        cis = ()
        if self.kg is not None:
            try:
                with self.kg._lock:
                    cis = tuple(sorted(
                        (str(n.extra_attrs.get("kind")),
                         round(float(n.extra_attrs.get("activation") or 0), 2))
                        for n in self.kg.nodes.values()
                        if (n.extra_attrs or {}).get("type") in
                        ("intention", "communication_intention")
                        and (n.extra_attrs or {}).get("status") in
                        ("forming", "ready")))
            except Exception:
                pass
        return (sig, cis)

    def _intention_support(self, cand: dict) -> float:
        """活跃意图对该候选的支持度（图谱共享状态，非第二套评分）。"""
        if self.kg is None:
            return 0.0
        reason = set(str(r) for r in (cand.get("reason") or []))
        if cand.get("target"):
            reason.add(str(cand.get("target")))
        best = 0.0
        try:
            with self.kg._lock:
                for n in self.kg.nodes.values():
                    ea = n.extra_attrs or {}
                    if ea.get("type") not in ("intention",
                                              "communication_intention"):
                        continue
                    if str(ea.get("kind") or "") not in (
                            "action", "exploration", "attention_shift"):
                        continue
                    if ea.get("status") not in ("forming", "ready", "ready_to_express"):
                        continue
                    basis = set(ea.get("basis") or [])
                    if not (basis & reason):
                        continue
                    strength = float(ea.get("activation") or 0.0)
                    overlap = len(basis & reason) / max(len(basis), 1)
                    best = max(best, min(1.0, strength) * (0.5 + 0.5 * overlap))
        except Exception:
            return 0.0
        return round(best, 3)

    def _score_action(self, cand: dict, percept: dict, now: float) -> tuple:
        w = self.cfg["weights"]
        comps = {}
        atype = cand["action_type"]

        comps["drive"] = self._motivation_value(cand.get("motivation", ""))

        act = 0.0
        if self.kg is not None:
            for nid in cand.get("reason", []):
                node = self.kg.nodes.get(nid)
                if node is not None:
                    act = max(act, float(getattr(node, "activation", 0.0) or 0.0))
        comps["attention"] = min(1.0, act / 5.0)

        # novelty：reason 节点在图上被标为 unknown（能力图谱
        # 重构 2026-09-20）优先；节点不在图/无标记时回退前缀判定（兼容，
        # 强化而非替换——任意命名的 unknown 节点也能被认出来了）。
        # B3 饱食链：已 retired（经验认出来的）节点不再抬新颖性。
        def _live_unknown_basis(b):
            nid = str(b)
            node = self.kg.nodes.get(nid) if self.kg is not None else None
            if node is not None:
                return is_live_unknown(node)
            return nid.startswith(("UnknownEntity_", "UnknownBlock_",
                                   "UnknownPlayer_"))
        basis_unknown = any(_live_unknown_basis(b)
                            for b in cand.get("reason", []))
        comps["novelty"] = 1.0 if basis_unknown else (
            0.3 if atype.startswith("explore") else 0.0)

        comps["tendency"] = 0.0
        if self.disposition_store is not None:
            try:
                tend = self.disposition_store.tendencies_for(["情境:主动发起"])
                if tend:
                    comps["tendency"] = min(1.0, float(tend[0].get("strength", 0.0)))
            except Exception:
                comps["tendency"] = 0.0

        # 意图支持（统一认知循环 2026-09-20）：认知脉冲形成的 action/
        # exploration/attention_shift 意图通过**共享图谱状态**支持行动——
        # "我想探索那个东西"这个意图本身抬高对应候选的价值，不是 autonomy
        # 另起一套评分。support = 意图强度 × basis 与候选 reason 的重叠。
        comps["intention"] = self._intention_support(cand)

        risk = 0.0
        if atype in ("explore_area", "explore_direction",
                     "explore_unknown_region", "gather_resource",
                     "chop_tree", "navigate_to_entity", "approach") \
                and percept.get("danger_visible"):
            risk = 1.0
        if (atype in ("explore_area", "explore_direction",
                      "explore_unknown_region", "gather_resource")
                and (percept.get("health") is not None
                     and float(percept.get("health")) <= 6)):
            risk = max(risk, 0.6)
        comps["risk"] = risk

        key = self._attempt_key(atype, cand.get("target"))
        last = self._recent_by_key.get(key, 0)
        window = float(self.cfg["recency_window_s"])
        # recency：连续衰减（刚做过的自然降温，非二值开关）
        comps["recency"] = (math.exp(-(now - last) / max(1.0, window))
                             if last else 0.0)
        att = self._attempts.get(key) or {}
        # failure：近期失败软降权（经验通道=causal 分量；这里只是尝试计数衰减）。
        # 2026-09-24 修正："衰减"原本没人实现——只数次数、等一次成功清零，于是
        # 夜里两次 preempted 就把探索族分数压到阈值下、整个进程余生提不起它，
        # 而提不起 ⇒ 拿不到清零用的成功 = 自锁（run12 天亮后呆立实测）。改为随
        # 最近一次失败的时间线性回落，fail_decay_window_s 后完全释放；刷屏由
        # next_ts 重试冷却防住，这里只管"经验压低 ≠ 永久否决"。
        _fraw = min(1.0, float(att.get("count", 0)) / 4.0)
        _flts = float(att.get("last_ts", 0) or 0)
        _fdw = max(1.0, float(self.cfg.get("fail_decay_window_s", 900)))
        comps["failure"] = (_fraw * max(0.0, 1.0 - (now - _flts) / _fdw)
                            if _flts else _fraw)
        # P3/§6 重复边际递减：刚**成功**做过这件事，同类重复的价值随窗口
        # 线性回落（默认 600s）——"已经合成过工作台"让"再造一个工作台"
        # 没那么值，但**不禁**（§6：边际价值递减而非禁止）。与 recency
        # （180s 全恢复的"刚做过"降温）互补而非重复：这里读的是成功史。
        last_ok = self._success_by_key.get(key, 0)
        _hwin = max(1.0, float(self.cfg.get("habituation_window_s", 600)))
        comps["habituation"] = (max(0.0, 1.0 - (now - last_ok) / _hwin)
                                if last_ok and 0 <= now - last_ok < _hwin
                                else 0.0)

        # 认知网络调制（2026-09-20）：探索类候选的新颖性权重乘
        # behavior.exploration_rate（DMN 高/curiosity 强 → 更愿探索；
        # CEN 高 → 守在任务上）。调制层缺位 = 旧静态权重。
        novelty_w = w["novelty"]
        _modl = getattr(self, "modulation", None)
        if _modl is not None:
            try:
                novelty_w *= float(_modl.get("behavior.exploration_rate", 1.0))
            except Exception:
                pass

        # 因果先验：从自身经历学"这件事我现在做会怎样"
        comps["causal"] = 0.0
        prior = {"success_rate": None, "blockers": []}
        if self.actions is not None and self.actions.causal is not None:
            try:
                prior = self.actions.causal.action_prior(atype, str(cand.get("target") or ""))
            except Exception:
                prior = {"success_rate": None, "blockers": []}
        if prior["blockers"]:
            # 只有结构性阻碍（tool_missing 等"现在做不到"）才满额惩罚；
            # 暂态"世界没准备好"不进 causal——recency/failure 分量已经对
            # 刚失败过的动作降过温了，重复计罪就是把 §13 撤掉的硬禁令
            # 从经验通道复活。
            structural = [b for b in prior["blockers"]
                          if str(b).split(":", 1)[0]
                          not in _TRANSIENT_WORLD_REASONS]
            if structural:
                comps["causal"] = 1.0
        # B6/§12 能力经验回流："越用越会"是经验派生，不是等级。
        # 旧式 0.12×success_rate 有两个不诚实：①以 0 为心——纯失败史
        # （rate=0.2）照样拿正加成；②一次运气就算"会"。改为以 0.5 为中心、
        # 观察数 <3 恒 0（经验需要重复），钳位 ±0.05（先验不能压过当下 drive）。
        prior_bonus = 0.0
        _sr = prior.get("success_rate")
        if _sr is not None and int(prior.get("obs", 0) or 0) >= 3:
            prior_bonus = max(-0.05, min(0.05, 0.10 * (float(_sr) - 0.5)))
        comps["prior_bonus"] = round(prior_bonus, 4)
        # 事件显著度：由刚发生的世界事件直接催生的候选（发现铁矿的那一刻）
        salience = float(cand.get("_salience") or 0.0)
        if salience:
            comps["salience"] = round(salience, 2)
        score = (w["drive"] * comps["drive"]
                 + w["attention"] * comps["attention"]
                 + novelty_w * comps["novelty"]
                 + w["tendency"] * comps["tendency"]
                 + float(w.get("intention", 0.12)) * comps["intention"]
                 + prior_bonus
                 - w["risk"] * comps["risk"]
                 - w["recency"] * comps["recency"]
                 - w["failure"] * comps["failure"]
                 - w["causal"] * comps["causal"]
                 - float(w.get("habituation", 0.18)) * comps["habituation"]
                 + 0.15 * salience)
        # §P5：目标生命周期让位信号（demoted → ×0.5）。这是"让位"不是
        # 封杀——能力/驱动照样可以把它捞回来；0.5 由打分规范化胃口。
        _mul = float(cand.get("score_mul") or 1.0)
        if _mul != 1.0:
            score = score * _mul
        explain = (f"{atype} mot={cand.get('motivation')}"
                   f" drive={comps['drive']:.2f} att={comps['attention']:.2f}"
                   f" nov={comps['novelty']:.2f} tend={comps['tendency']:.2f}"
                   f" int={comps['intention']:.2f}"
                   f" risk={comps['risk']:.2f} rec={comps['recency']:.2f}"
                   f" fail={comps['failure']:.2f}"
                   + (f" hab={comps['habituation']:.2f}"
                      if comps["habituation"] else "")
                   + (f" causal={prior['blockers']}" if prior["blockers"] else "")
                   + (f" pb={prior_bonus:+.2f}" if prior_bonus else "")
                   + (f" sal={salience:.2f}" if salience else "")
                   + (f" mul={_mul:.2f}" if _mul != 1.0 else "")
                   + f" → {score:.3f}")
        return score, explain

    # ── 可行性 ────────────────────────────────────────────

    def _feasible_action(self, cand: dict, percept: dict, now: float) -> tuple:
        atype = cand["action_type"]
        if self._embodiment is None:
            return False, "未注册具身环境"
        try:
            caps = set(self._embodiment.capabilities() or ())
        except Exception:
            caps = set()
        if atype not in caps:
            return False, f"具身环境不支持 {atype}"

        # 锁：被锁住的目标不碰
        if self.regulation is not None:
            for nid in cand.get("reason", []):
                try:
                    if (self.regulation.locks.blocks_node(nid, "action")
                            or self.regulation.locks.hides_node(nid, "action")):
                        return False, f"{nid} 被锁（scope=action）"
                except Exception:
                    pass

        # 目标在场性
        target = cand.get("target")
        if atype in ("follow_entity", "communicate"):
            names = [p.get("name") for p in percept.get("players", [])]
            if (cand.get("params", {}).get("player") or target) not in names:
                return False, "同伴不在视野内"
        if atype in ("navigate_to_entity", "inspect_entity"):
            # 在场性读的是感知快照：玩家也是可导航目标（mineflayer 的
            # entities 含 player，playersNearby 是显式在场证据）——
            # "走过去找用户"的承诺不该死于只查生物表。
            ents = [e.get("name") for e in percept.get("entities", [])]
            ents += [p.get("name") for p in percept.get("players", [])]
            if target not in ents:
                return False, "目标生物已不在视野内"
        if atype == "retreat":
            ents = [e.get("name") for e in percept.get("entities", [])]
            if target not in ents:
                return False, "威胁已离开视野"

        # （2026-09-21）"失败 3 次永久放弃 + 45s 固定退避"拆除——那是
        # 计数器冒充经验学习。失败历史现在两处承接：评分的 failure/causal
        # 分量（soft）与 Safety Kernel 的 5s 防抖（纯技术）。反复失败的动作
        # 会因 success_rate↓/blockers 被认知压低，而不是被代码封禁。
        # P3/§6 重接（语义不同）：next_ts 是**有限窗口的重试冷却**（失败
        # 越多等越久，封顶 8×45s；一次成功即清零），不是永久禁令——
        # 它消费的是长期无人读的软退避字段（预算在别处记账），防的正是
        # "每个决策拍撞同一堵墙"的刷屏，与评分通道的经验压低互补。
        att = self._attempts.get(self._attempt_key(atype, cand.get("target"))) or {}
        _next = float(att.get("next_ts", 0) or 0)
        if now < _next:
            return False, f"重试冷却中（{_next - now:.0f}s 后再试）"
        return True, ""

    # ── 感知 ──────────────────────────────────────────────

    def _perceive(self) -> dict:
        try:
            percept = self._embodiment.perceive() or {}
        except Exception as e:
            logger.warning(f"[Autonomy] 感知失败: {e}")
            percept = {}
        percept.setdefault("players", [])
        percept.setdefault("entities", [])
        percept.setdefault("blocks", [])
        percept.setdefault("unknown_entities", [])
        percept.setdefault("unknown_blocks", [])
        percept.setdefault("health", None)
        percept.setdefault("food", None)
        percept.setdefault("connected", False)
        hostiles = [e for e in percept.get("entities", [])
                    if str(e.get("name", "")).lower() in self.cfg["hostile_entities"]]
        near = [e for e in hostiles
                if float(e.get("dist", 99)) <= float(self.cfg["danger_distance"])]
        percept["danger_visible"] = bool(near)
        percept["nearest_hostile"] = near[0] if near else None
        percept["digest"] = (f"pos={percept.get('position')} hp={percept.get('health')} "
                             f"food={percept.get('food')} ents={len(percept.get('entities', []))} "
                             f"players={len(percept.get('players', []))} "
                             f"danger={percept['danger_visible']}")
        return percept

    # ═══════════════════════════════════════════════════════
    # 旧执行路径（未注入 ActionManager 时；行为与历史版本一致）
    # ═══════════════════════════════════════════════════════


    # （旧候选生成/评分/可行性/执行 — 保持原样供旧路径使用）



    @staticmethod


    @staticmethod
    def _is_weak(percept: dict) -> bool:
        h = percept.get("health")
        f = percept.get("food")
        try:
            return (h is not None and float(h) <= 6) or (f is not None and float(f) <= 6)
        except (TypeError, ValueError):
            return False


    @staticmethod
    def _action_node_for(intent_type: str) -> str:
        return {"observe": "看屏幕", "approach": "进入Minecraft世界",
                "follow": "进入Minecraft世界", "explore": "进入Minecraft世界",
                "collect": "进入Minecraft世界", "communicate": "进入Minecraft世界",
                "withdraw": "进入Minecraft世界", "rest": "进入Minecraft世界"}.get(
                    intent_type, "")








    def _publish_state(self, cand: dict, result: dict, success: bool):
        if self.regulation is None:
            return
        try:
            mon_id = "自主行动"
            if self.regulation.monitors.get(mon_id) is None:
                self.regulation.monitors.create(
                    mon_id, "自主行动状态", "external", "haru_autonomy",
                    "enum", mode="event",
                    schema={"enum": ["success", "failed", "idle", "acting"]},
                    description="自主行动的结果类别（由 AutonomousLoop 提交）")
            self.regulation.submit_state(
                mon_id, "success" if success else "failed", source="tool")
        except Exception as e:
            logger.debug(f"[Autonomy] 状态提交失败: {e}")

    def _execute(self, cand: dict, now: float = None):
        """兼容 shim：把旧式意图直接提交给 ActionManager（测试/外部管线
        复用）。旧自主执行路径已并入单一路径，这里只剩一次字段翻译。"""
        spec = {"action_type": cand.get("type"), "target": cand.get("target"),
                "params": cand.get("params") or {},
                "explain": cand.get("explain", ""),
                "reason": cand.get("basis") or []}
        return self.actions.propose(spec, source="autonomy",
                                    now=now or time.time())

    # ── 目标（Mode 1 入口 / 触发器可写入） ────────────────

    def add_goal(self, goal: dict) -> dict:
        if not isinstance(goal, dict) or not goal.get("type"):
            return {"ok": False, "error": "goal 需要 type 字段"}
        # §P5：目标从创建起带时间锚（生命周期评估的基准，2026-09-27）
        goal = {**goal, "created": now_str(), "created_ts": time.time()}
        with self._lock:
            self._goals.append(goal)
            self._goals = self._goals[-20:]
        self._save()
        self._record("goal", f"+ {goal.get('type')} {goal.get('text', '')[:40]}")
        return {"ok": True, "goals": self.goals()}

    # ── 社交邀请 → 持久承诺（B4/§4）──────────────────────

    _INVITE_ALIAS = {"follow_entity": "follow", "navigate_to_entity": "approach"}

    def register_invitation(self, action_type: str, target: str,
                            text: str = "") -> dict:
        """游戏内邀请落成**持久目标**（不是日志标签）。

        "跟着我/过来"之后，承诺经 透传→候选→评分→阈值→propose 全链参与
        每一次决策——**没有硬编码优先级**（user_goal 地板 0.8 与其它动机
        同台竞争，生存类照样压过它）；兑现（settle 成功）自动销账，
        stop 反射经 cancel_invitation 撤账；落盘 _save 后跨重启存活。
        同类型旧邀请被新邀请替换（用户改主意是常态）。
        """
        t = str(action_type or "").strip()
        alias = self._INVITE_ALIAS.get(t, t)
        if alias not in ("follow", "approach") or not target:
            return {"ok": False, "error": f"不支持的邀请 {action_type}@{target}"}
        goal = {"type": alias, "target": str(target),
                "params": ({"player": str(target)} if alias == "follow"
                           else {"entity": str(target)}),
                "text": str(text or ""), "source": "user_invitation"}
        with self._lock:
            self._goals = [g for g in self._goals
                           if not (g.get("type") == alias
                                   and g.get("source") == "user_invitation")]
            self._goals.append({**goal, "created": now_str(),
                                "created_ts": time.time()})
            self._goals = self._goals[-20:]
        self._save()
        self._record("goal", f"承诺 {alias} @{target}")
        return {"ok": True, "goal": goal}

    def cancel_invitation(self, action_type: str = None) -> dict:
        """撤账（"别跟了"）：只清邀请来源的承诺，调试面板目标不动。"""
        t = self._INVITE_ALIAS.get(str(action_type or "").strip(),
                                   str(action_type or "").strip())
        with self._lock:
            before = len(self._goals)
            self._goals = [g for g in self._goals
                           if not ((not t or g.get("type") == t)
                                   and g.get("source") == "user_invitation")]
            n = before - len(self._goals)
        if n:
            self._save()
            self._record("goal", f"撤销邀请承诺 {n} 条")
        return {"ok": True, "cancelled": n}

    def goals(self) -> list:
        with self._lock:
            return list(self._goals)

    def clear_goals(self) -> dict:
        with self._lock:
            n = len(self._goals)
            self._goals = []
        self._save()
        return {"ok": True, "cleared": n}

    def goal_from_text(self, text: str, llm_fn=None) -> dict:
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "text 不能为空"}
        if llm_fn is None:
            return {"ok": False, "error": "未提供 LLM（Mode 1 需要）"}
        if self.llm_budget is not None:
            try:
                if not self.llm_budget.can_call("reason", estimated_tokens=400):
                    return {"ok": False, "error": "LLM 预算不足（Mode 1 被拒绝）"}
            except Exception:
                pass
        prompt = (
            "把下面这句话转成一个结构化自主目标，只输出 JSON，不要解释：\n"
            '{"type": "observe|approach|follow|explore|collect|rest|communicate",'
            ' "target": "目标对象（可以是名字或 world）",'
            ' "text": "如果是要说的话，填原话，否则空字符串",'
            ' "params": {"block": "采集时填方块名", "player": "跟随时填玩家名"}}\n'
            "规则：只能从这 7 种类型里选；不确定就返回 {\"type\": null}。\n"
            f"用户的句子是：{text}"
        )
        try:
            raw = llm_fn(prompt)
        except Exception as e:
            return {"ok": False, "error": f"LLM 调用失败: {e}"}
        import json as _json
        s = str(raw or "").strip()
        if s.startswith("```"):
            s = "\n".join(s.split("\n")[1:-1])
        try:
            data = _json.loads(s)
        except Exception as e:
            return {"ok": False, "error": f"返回不是合法 JSON: {e}", "raw": s[:200]}
        if not isinstance(data, dict) or data.get("type") not in INTENT_TYPES:
            return {"ok": False, "error": "类型不在允许集合内", "candidate": data}
        if self.llm_budget is not None:
            try:
                self.llm_budget.register("reason", tokens=400)
            except Exception:
                pass
        return {"ok": True, "candidate": data}

    def _escalate(self, cand: dict, result: dict):
        if int(self.cfg["llm_mode"]) < 2:
            return
        key = f"{cand['type']}@{cand.get('target')}"
        now = time.time()
        with self._lock:
            last = self._escalated.get(key, 0)
            if now - last < float(self.cfg["retry_backoff_s"]):
                return
            self._escalated[key] = now
        self._record("escalate",
                     f"{cand['type']}@{cand.get('target')} 失败（{result.get('reason')}），"
                     f"标记待反思（llm_mode=2，交给认知层按需处理）")
        self._last_result = dict(self._last_result or {})
        self._last_result["needs_reflection"] = True

    # ── 查询（前端/API） ──────────────────────────────────

    def state(self) -> dict:
        with self._lock:
            drives = {}
            if self.kg is not None:
                for nid in ("CuriosityDrive", "SocialDrive", "LearningDrive",
                            "ConsistencyDrive"):
                    node = self.kg.nodes.get(nid)
                    if node is not None:
                        drives[nid] = round(float(getattr(node, "activation", 0.0) or 0.0), 3)
            current_action = None
            if self.actions is not None and self.actions.current:
                current_action = {"action_type": self.actions.current.get("action_type"),
                                  "target": self.actions.current.get("target"),
                                  "motivation": self.actions.current.get("motivation"),
                                  "reason": self.actions.current.get("reason", []),
                                  "expected_effect": self.actions.current.get("expected_effect")}
            return {
                "mode": self.mode,
                "paused": self._paused,
                "state": self.phase,
                "state_reason": self._state_reason,
                "embodiment": (getattr(self._embodiment, "name", None)
                               if self._embodiment else None),
                "current_intent": self._current,
                "current_action": current_action,
                "world_events": list(self._last_events[:8]),
                "in_flight": ({"type": self._in_flight.get("type"),
                               "target": self._in_flight.get("target")}
                              if self._in_flight else None),
                "last_result": self._last_result,
                "goals": list(self._goals),
                "stats": dict(self._stats),
                "drives": drives,
                "candidates": (self._history[-1]["candidates"] if self._history else []),
                "cfg": {"min_interval_s": self.cfg["min_interval_s"],
                        "score_threshold": self.cfg["score_threshold"],
                                                "llm_mode": self.cfg["llm_mode"]},
            }

    def recent_log(self, n: int = 20) -> list:
        with self._lock:
            return list(self._log)[-n:]

    def candidate_history(self, n: int = 10) -> list:
        with self._lock:
            return list(self._history)[-n:]

    def _record(self, kind: str, text: str):
        with self._lock:
            self._log.append({"ts": now_str(), "kind": kind, "text": str(text)[:200]})
