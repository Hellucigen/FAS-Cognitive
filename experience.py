# experience.py — 通用经验时间轴 + 保守因果发现（核心机制）
# ============================================================================
# 定位（Experience Timeline 改造 2026-09）：
#
#   PERCEPTION（任何来源）→ ExperienceEvent → ExperienceTimeline
#                                            → CausalLearner
#                                            → candidate association
#                                            → 重复聚合 → Causal Hypothesis
#                                            → （足够支撑才）Knowledge Graph
#
#   Timeline = experience 层（ episodic 原始经历）
#   Aggregation = learned regularity（重复统计）
#   KG = generalized knowledge（泛化知识）
#   三层不混。原始事件**不**进 KG；只有反复验证的假设才以
#   `操作 -[导致]-> 变化` 的形式晋升，且带完整 provenance。
#
# 通用性红线：
#   1. 本模块**不含任何具体环境的专有字段与名称**——事件由来源侧翻译，这里
#      只认 event_type/actor/subject/content（有回归测试锁住这一条）。
#   2. **零 LLM**：整个因果发现是结构化事件 + 时间关系 + 重复统计，
#      确定性、可复算、可测试。
#   3. 不做"行动后所有变化=行动后果"：候选结果必须通过**主体相关性门槛**
#      （§七），时间/领域只调节不资历。
#   4. 第一版无反事实/主动实验（§九）：observe → associate → repeat → hypothesize。
#   5. 事件级而非采样级（§二）：同类同主体的高频 observation 在合并窗内
#      去重合并，Timeline 是"她经历过什么"，不是 debug log。
#
# 持久化（data/experience_timeline.json，原子写）：
#   raw          = 原始事件环（有界，可压缩归档）
#   causal       = CausalLearner 的统计结构 {aggregations, hypotheses, promoted}
#   （旧版顶层 aggregations/hypotheses/promoted 会在载入时一次性迁移进 causal。）
# 单一写者：整文档只有 ExperienceTimeline.flush/put_section 一个落盘点，
# 两侧共享同一份内存态——修掉"raw 与 causal 互相覆写"的事故（2026-09-22：
# 磁盘上 383 事件、causal 三节被对方 flush 抹光）。
# 这是**经验/统计层**存储，与人格无关（人格仍只在 self 图）。
# ============================================================================

import logging
import threading
import time
import uuid

from json_store import load_json, atomic_write_json

try:                      # §21 实验观测钩子（未开实验时恒静默；xlog 绝不抛）
    import experiment_mode as xm
except Exception:
    xm = None

logger = logging.getLogger(__name__)

# ── 事件类型（统一词表；来源侧翻译，本模块不认识具体环境）──
EVENT_ACTION = "ACTION"                    # FAS 自己主动执行的行为
EVENT_OBSERVATION = "OBSERVATION"          # 从环境/用户/工具获得的观察
EVENT_SELF_STATE = "SELF_STATE_CHANGE"     # 自身状态变化（健康/库存/奖赏/激素…）
EVENT_COGNITIVE = "COGNITIVE_EVENT"        # 重要认知事件（好奇触发/焦点转移…）

# 动作语义类别 → 该类动作的**预期作用域**（通用世界词汇，非具体环境）。
# 只有落进预期作用域、或主体与动作目标直接相关的观察，才有资格成为
# candidate outcome（§七：时间接近本身不构成归因资格）。
EXPECTED_EFFECT_DOMAINS = {
    "dig":       {"block", "inventory", "object"},
    "collect":   {"block", "inventory", "object"},
    "attack":    {"entity", "health"},
    "move":      {"position"},
    "say":       {"chat", "utterance", "user_response"},
    "ask":       {"chat", "utterance", "user_response"},
    "inspect":   {"object", "block", "entity"},
    "look":      {"object", "entity"},
    "search":    {"knowledge"},
}

ASSOC_WINDOW_S = 8.0        # 动作后多久内的观察有资格候选
# 动作 → 归因窗（秒）数据表：简单物理/ reflex 结果在秒级；言语类动作的
# "结果"是**对方回应**，天然迟到几十秒——8 秒窗对它们必然空手而归。
# 获取/制作类同理：其"结果"是**库存观察**，库存观测随感知节拍刷新（几十秒
# 一拍）——MIN_SCORE=0.60 要求 dt≤⅔窗，8 秒窗会把 §8 核心闭环
# （动作→世界增量→结果）系统性筛空。纯数据行，随动作库扩充。
ACTION_WINDOW_S = {"communicate": 90.0, "say": 90.0, "ask": 90.0,
                   "gather_resource": 90.0, "chop_tree": 90.0,
                   "break_block": 90.0, "harvest_crop": 90.0,
                   "collect_dropped_item": 90.0, "pickup_item": 90.0,
                   "find_item": 90.0, "collect_drop": 90.0,
                   "gather_stone": 90.0, "gather_wood": 90.0,
                   "collect_food": 90.0, "collect_plant": 90.0,
                   "craft_item": 90.0, "place_block": 90.0,
                   "equip_item": 90.0, "chest_store": 90.0,
                   "smelt_item": 120.0, "furnace_take": 120.0,
                   "use_furnace": 120.0, "chest_take": 120.0}
ACTION_WINDOW_KIND_S = {"dialogue_answer": 90.0}
# 动作执行器名 → 语义类别作用域别名（纯数据行，随动作库扩充）。
# 目的同 EXPECTED_EFFECT_DOMAINS：让真实执行器名（navigate_to_entity、
# gather_resource…）的候选结果有归因资格，而不必等调用方各自改写。
for _dom, _names in (
    ({"block", "inventory", "object"},
     ("break_block", "chop_tree", "harvest_crop", "till_soil",
      "gather_resource", "gather_stone", "gather_wood", "collect_drop",
      "collect_dropped_item", "collect_food", "collect_plant",
      "pickup_item", "chest_take", "furnace_take", "find_item")),
    ({"position"},
     ("walk_to", "run_to", "go_direction", "navigate_to_entity", "navigate_home",
      "approach_animal", "follow_entity", "approach", "maintain_distance",
      "retreat", "retreat_from_entity", "seek_safety", "return_to_location",
      "return_to_known_location", "revisit_location", "descend", "swim",
      "escape_water", "avoid_lava", "withdraw", "jump", "follow")),
    ({"entity", "health"},
     ("attack_animal", "attack_entity", "hunt_animal")),
    ({"object", "block", "entity"},
     ("observe", "observe_target", "inspect_area", "inspect_block",
      "inspect_entity", "inspect_inventory", "look_at", "look_at_block",
      "detect_hostile", "detect_nearby_resource", "detect_food", "detect_cave",
      "detect_environment", "detect_inventory_full", "detect_structure",
      "investigate_location", "explore", "explore_area", "explore_direction",
      "explore_unknown_region", "mark_interesting_location", "find_animal")),
    ({"chat", "utterance", "user_response"}, ("communicate",)),
    ({"block", "inventory"},
     ("place_block", "place_named_block", "place_torch", "build_floor",
      "build_wall", "build_simple_shelter", "plant_seed", "replant_crop",
      "chest_store")),
    ({"inventory"},
     ("craft_item", "craft_batch", "smelt_item", "equip_item", "unequip_item",
      "equip_weapon", "equip_shield", "use_furnace", "open_crafting_table",
      "sort_inventory", "count_item", "drop_item", "choose_best_tool",
      "choose_weapon", "interact_with_block", "interact_with_entity",
      "breed_animal", "feed_animal", "turn_to", "sneak", "stop")),
    ({"inventory", "health"}, ("eat_food", "choose_food", "recover_health",
                               "recover_after_combat")),
    ({"health", "position"}, ("sleep", "recover_stuck")),
):
    for _n in _names:
        EXPECTED_EFFECT_DOMAINS.setdefault(_n, _dom)
MERGE_WINDOW_S = 2.0        # 同类同主体 observation 的去重合并窗
MIN_SCORE = 0.60            # 候选资格分（§七筛选）
MIN_SUPPORT = 3             # 聚合 → 假设 的最少支撑次数
HYP_CONFIDENCE = 0.60       # 假设门槛
KG_SUPPORT = 5              # 假设 → KG 晋升 的最少支撑
KG_CONFIDENCE = 0.80        # KG 晋升门槛
CONF_SMOOTHING = 2.0        # Laplace 平滑：confidence 永远到不了 1.0
EXPECTED_RATE = 0.60        # 反例判定：历史支撑率高于此而本次缺席 → contradiction


def make_event(event_type: str, actor: str, subject: str,
               content: dict = None, source: str = "",
               confidence: float = 1.0, meta: dict = None) -> dict:
    """统一 ExperienceEvent 工厂。字段刻意少（§三：不为完整而堆字段）。"""
    now = time.time()
    return {
        "id": "ev_" + uuid.uuid4().hex[:10],
        "ts": now,
        "ts_str": time.strftime("%H:%M:%S"),
        "event_type": str(event_type),
        "actor": str(actor),            # self / user / 来源环境名
        "subject": str(subject or ""),  # 关于什么（oak_log / inventory / utterance…）
        "content": dict(content or {}),
        "source": str(source),          # 来源侧名称（embodiment / nlp / reward / autonomy…）
        "confidence": float(confidence),
        "meta": dict(meta or {}),
    }


def action_signature(event: dict) -> str:
    """ACTION 事件的签名：intent(target)。用于聚合与假设键。"""
    intent = str(event.get("subject") or "act")
    target = str((event.get("content") or {}).get("target") or "").strip()
    return f"{intent}({target})" if target else intent


def outcome_signature(event: dict) -> str:
    """候选结果事件的签名：type:actor:subject:change。"""
    kind = {"OBSERVATION": "obs", "SELF_STATE_CHANGE": "self"}.get(
        event.get("event_type"), "ev")
    actor = str(event.get("actor") or "?")
    subj = str(event.get("subject") or "?")
    change = str((event.get("content") or {}).get("change") or "observed")
    return f"{kind}:{actor}:{subj}:{change}"


# ── 条件签名（§十/§十一：经验带"当时条件"，失败链不留"夜→死"式断言）──
_CTX_SKIP = {"pos", "x", "y", "z", "ts", "text", "explain", "reason",
             "params", "basis", "target", "id", "seq", "items", "meta"}


def _ctx_bucket(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        if abs(v) <= 1.5:            # 归一化量（心情/需求/调制 level）→ 低/中/高
            return str(max(0, min(2, int(abs(float(v)) * 3))))
        return str(int(round(float(v) / 5.0) * 5))   # 大量程（血量/时间）→ 粗粒度桶
    return str(v)[:24]


def _ctx_walk(d: dict, prefix: str, out: dict, depth: int = 0):
    if depth > 2 or len(out) >= 12:
        return
    for k in sorted(d):
        if str(k) in _CTX_SKIP or k.startswith("_"):
            continue
        v = d[k]
        if isinstance(v, dict):
            _ctx_walk(v, f"{prefix}{k}.", out, depth + 1)
        elif isinstance(v, (str, int, float, bool)):
            out[f"{prefix}{k}"] = _ctx_bucket(v)


def context_signature(evt: dict) -> str:
    """事件发生时的**条件**签名：优先 content.context（来源侧可直接给分桶后
    的字符串），否则从 content.internal_state 通用提取。相同条件 → 同一桶，
    聚合里的 support/contra 按条件记账。"""
    content = evt.get("content") or {}
    ctx = content.get("context")
    if isinstance(ctx, str) and ctx.strip():
        return ctx.strip()[:160]
    out = {}
    if isinstance(ctx, dict):
        _ctx_walk(ctx, "", out)
    ist = content.get("internal_state")
    if isinstance(ist, dict):
        _ctx_walk(ist, "", out)
    if not out:
        return "default"
    return "|".join(f"{k}={v}" for k, v in sorted(out.items()))[:160]


class ExperienceTimeline:
    """事件级经验时间轴：append（带去重合并）+ 查询 + 有界持久化。

    同时是**整份文档的单一写者**：raw 与各统计节（put_section）都从这里的
    flush 原子落盘；新事件通过 observe() 回调广播（因果归因的窗口匹配靠它，
    修"动作开始时扫窗必然扫不到结果"的诞生时空扫）。
    """

    def __init__(self, path: str = "data/experience_timeline.json",
                 config: dict = None, max_raw: int = 4000):
        self.path = path
        self.config = config or {}
        self.max_raw = int(max_raw)
        self._lock = threading.RLock()
        self._raw = []            # 时间升序事件环
        self._sections = {}       # 统计节（"causal" → CausalLearner 三件套）
        self._observers = []      # 新事件回调 fn(event)
        self._dirty = False
        self._last_flush = 0.0
        self._load()

    # ── 持久化（单一写者：raw + 全部统计节在同一份内存态上合并写）──

    def _load(self):
        data = load_json(self.path, default=None) or {}
        raw = data.get("raw") or []
        sections = {k: v for k, v in data.items() if k != "raw"}
        # 旧形状迁移：顶层 aggregations/hypotheses/promoted → causal 节
        if "causal" not in sections and any(
                k in sections for k in ("aggregations", "hypotheses", "promoted")):
            sections["causal"] = {
                k: sections.pop(k)
                for k in ("aggregations", "hypotheses", "promoted")
                if k in sections}
            self._dirty = True    # 下次落盘即完成形状迁移
        with self._lock:
            self._raw = [e for e in raw if isinstance(e, dict)][-self.max_raw:]
            self._sections = sections
        logger.info("[Experience] 时间轴载入 %d 条原始事件（节: %s）",
                    len(self._raw), sorted(self._sections) or "无")

    def flush(self, force: bool = False):
        with self._lock:
            if not self._dirty:
                return
            if not force and time.time() - self._last_flush < 2.0:
                return
            doc = {"raw": self._raw[-self.max_raw:]}
            doc.update(self._sections)
            ok = atomic_write_json(self.path, doc)
            self._dirty = not ok
            self._last_flush = time.time()

    def get_section(self, name: str, default=None):
        with self._lock:
            return self._sections.get(name, default)

    def put_section(self, name: str, data):
        """统计节写入的唯一通道：更新内存态并强制落盘整文档。"""
        with self._lock:
            self._sections[name] = data
            self._dirty = True
            self.flush(force=True)

    def observe(self, fn):
        """注册新事件回调（幂等：同一函数不重复挂）。"""
        with self._lock:
            if fn not in self._observers:
                self._observers.append(fn)

    # ── 写入（事件级 + 去重合并，§二）──────────────────────

    def append(self, event: dict, merge_window_s: float = MERGE_WINDOW_S) -> dict:
        """追加事件。同类同主体且合并窗内 → 原位合并（计数+末次时间），
        不新增条目——Timeline 记"发生过什么"，不是采样日志。
        新事件（含合并刷新）广播给观察者（因果窗口匹配）。"""
        with self._lock:
            last = self._raw[-1] if self._raw else None
            if (last is not None
                    and last.get("event_type") == event.get("event_type")
                    and last.get("actor") == event.get("actor")
                    and last.get("subject") == event.get("subject")
                    and event.get("ts", 0) - last.get("ts", 0) <= merge_window_s
                    and (event.get("content") or {}).get("change")
                        == (last.get("content") or {}).get("change")):
                # 合并：刷新末次时间与重复计数，保留首次
                last.setdefault("meta", {})
                last["meta"]["repeat"] = int(last["meta"].get("repeat", 0)) + 1
                last["meta"]["last_ts"] = event.get("ts")
                self._dirty = True
                self.flush()
                stored = last
            else:
                self._raw.append(event)
                if len(self._raw) > self.max_raw:
                    del self._raw[:len(self._raw) - self.max_raw]
                self._dirty = True
                self.flush()
                stored = event
        for obs in list(self._observers):
            try:
                obs(stored)
            except Exception as e:
                logger.debug("[Experience] 观察者异常（忽略）: %s", e)
        return stored

    # ── 查询 ──────────────────────────────────────────────

    def recent(self, n: int = 30, event_type: str = None, actor: str = None,
               since: float = None) -> list:
        out = []
        with self._lock:
            for e in reversed(self._raw):
                if len(out) >= n:
                    break
                if event_type and e.get("event_type") != event_type:
                    continue
                if actor and e.get("actor") != actor:
                    continue
                if since and e.get("ts", 0) < since:
                    continue
                out.append(e)
        return out

    def events_since(self, ts: float, event_types: tuple = None) -> list:
        with self._lock:
            return [e for e in self._raw
                    if e.get("ts", 0) >= ts
                    and (not event_types or e.get("event_type") in event_types)]

    def __len__(self) -> int:
        with self._lock:
            return len(self._raw)


class CausalLearner:
    """Action → Candidate Outcome → 重复聚合 → Causal Hypothesis →（够稳才）KG。

    第一版（§九）：只做 observe/associate/repeat/hypothesize。
    无反事实、无主动干预、无结构因果模型。
    """

    def __init__(self, timeline: ExperienceTimeline, config: dict = None,
                 kg=None, engine=None):
        self.timeline = timeline
        self.config = config or {}
        self.kg = kg
        self.engine = engine
        self._lock = threading.RLock()
        self._aggregations = {}   # action_sig → {"obs":n, "outcomes":{osig:{...}}}
        self._hypotheses = {}     # "action_sig|osig" → hypothesis dict
        self._promoted = set()    # 已晋升 KG 的键（幂等）
        self._candidates_recent = []  # 运行期候选环（debug 用）
        self._pending = []        # 开着的归因窗（动作→迟到结果，pending-window）
        self._load()
        # 挂到时间轴：动作之后**迟到**的观察/结果事件才有机会被归因
        # （修致命病灶：record_action 在动作开始瞬间扫窗，窗口里必然什么都没有）
        self.timeline.observe(self._on_timeline_event)

    # ── 持久化（聚合与假设是长期统计结构，§十三；单一写者=时间轴）──

    def _load(self):
        data = self.timeline.get_section("causal") or {}
        with self._lock:
            self._aggregations = data.get("aggregations") or {}
            self._hypotheses = data.get("hypotheses") or {}
            self._promoted = set(data.get("promoted") or [])
        if self._aggregations or self._hypotheses:
            logger.info("[Causal] 载入聚合 %d 组 / 假设 %d 条",
                        len(self._aggregations), len(self._hypotheses))

    def _persist(self):
        import copy
        with self._lock:
            payload = {"aggregations": copy.deepcopy(self._aggregations),
                       "hypotheses": copy.deepcopy(self._hypotheses),
                       "promoted": sorted(self._promoted)}
        self.timeline.put_section("causal", payload)

    # ── 归因窗 ────────────────────────────────────────────

    @staticmethod
    def window_for(action_event: dict) -> float:
        subj = str(action_event.get("subject") or "").lower()
        kind = str((action_event.get("content") or {}).get("kind") or "").lower()
        return float(ACTION_WINDOW_S.get(subj)
                     or ACTION_WINDOW_KIND_S.get(kind)
                     or ASSOC_WINDOW_S)

    # ── 主体相关性门槛（§七：防"行动后所有变化=行动后果"）──

    @staticmethod
    def _subject_relevant(action_event: dict, ev: dict) -> bool:
        subj = str(ev.get("subject") or "").lower()
        content = action_event.get("content") or {}
        target = str(content.get("target") or "").lower()
        intent = str(action_event.get("subject") or "").lower()
        if not subj:
            return False
        # 0) 动作**自身**的状态结果事件（change=succeeded/failed:reason）：
        #    subject 就是动作名，天然关于这次动作——成功率/阻碍统计的地基
        if (ev.get("event_type") == EVENT_SELF_STATE and subj == intent
                and (ev.get("content") or {}).get("change")):
            return True
        # 1) 主体与动作目标直接相关
        if target and (target in subj or subj in target):
            return True
        # 2) 落在该类动作的预期作用域（通用动作语义，非具体环境）
        domains = EXPECTED_EFFECT_DOMAINS.get(intent, set())
        base = subj.split(":")[0]
        if base in domains:
            return True
        return False

    @staticmethod
    def _score_candidate(action_event: dict, ev: dict,
                         window_s: float = None) -> float:
        """资格通过后的排序分：时间接近（**按各自归因窗归一**——物理结果
        8s 与言语回应 90s 不能用同一把秒尺）+ 主体精确匹配加成。"""
        dt = ev.get("ts", 0) - action_event.get("ts", 0)
        win = max(1e-6, float(window_s if window_s is not None
                               else ASSOC_WINDOW_S))
        frac = max(0.0, 1.0 - min(1.0, dt / win))
        score = 0.5 + 0.30 * frac
        target = str((action_event.get("content") or {}).get("target") or "").lower()
        subj = str(ev.get("subject") or "").lower()
        if target and (target in subj or subj in target):
            score += 0.20
        return round(min(1.0, score), 3)

    # ── P2b：窗口内有无"世界真值"确认效果发生 ─────────────
    # 判定：候选里存在观察类结果（obs:*），其 change 是**正向**（增加/
    # 出现/找到…，方向不明的 "observed" 不算），且该观察已通过主体相关
    # 门（候选资格本身就是"与动作相关"）。"世界确认了效果"使同窗的
    # failed:* 成为仪器噪声；感知最后否决（§P2：perception final veto）。
    _WORLD_POSITIVE = {"count_increased", "appeared", "found", "arrived",
                       "placed", "gained", "collected", "detected"}
    _WORLD_NEGATIVE = {"count_decreased", "disappeared", "damaged", "lost",
                       "vanished", "removed"}

    @classmethod
    def _window_confirms_effect(cls, action_event: dict, candidates: list) -> bool:
        """没有失败就不问（调用方已保证有 failed sig）；这里只看候选里
        有没有世界确认。fail-open：候选空/解析失败都判 False（保守——
        宁可把账记成"疑似失败"，不把噪声当成功）。"""
        for c in candidates:
            o = str(c.get("outcome") or "")
            if not o.startswith("obs:"):
                continue
            parts = o.split(":")
            if len(parts) < 4:
                continue
            change = parts[-1]
            if change == "observed":     # 方向不明的观察不构成确认
                continue
            if change in cls._WORLD_NEGATIVE:
                continue
            if change in cls._WORLD_POSITIVE:
                return True
        return False

    # ── 主入口：ACTION 开窗 → 迟到结果入窗 → 关窗记账 →（达标）假设 ──

    def record_action(self, action_event: dict, window_s: float = None) -> list:
        """登记一次归因窗（pending）。返回**当下**已能看到的候选（可能为空，
        如实——结果通常迟到）。窗口由时间轴新事件驱动推进与关闭；
        登记时窗口已过的（测试/补录历史事件）当场记账，语义与旧版一致。"""
        if action_event.get("event_type") != EVENT_ACTION:
            return []
        a_sig = action_signature(action_event)
        now = action_event.get("ts", time.time())
        win = float(window_s) if window_s is not None else self.window_for(action_event)
        deadline = now + win
        # 出生即扫一遍已入轴的事件（向后兼容：调用方在 append 之后登记）
        existing = [e for e in self.timeline.events_since(
            now - 0.001, event_types=(EVENT_OBSERVATION, EVENT_SELF_STATE))
            if e.get("ts", 0) >= now - 1e-6 and e.get("id") != action_event.get("id")
            and e.get("ts", 0) <= deadline]
        cands, sigs, ids = self._match(action_event, existing, win)
        pending = {"action": action_event, "a_sig": a_sig, "deadline": deadline,
                   "sigs": sigs, "ids": ids, "cands": cands}
        if time.time() >= deadline:
            # 事件时间已是过去（补录/测试）：当场关窗记账
            self._finalize(pending)
            return cands
        with self._lock:
            self._pending.append(pending)
        return cands

    def _match(self, action_event: dict, evs: list, window_s: float = None):
        """§七筛选：过主体相关门 + 资格分 ≥ MIN_SCORE。
        返回 (候选列表, 合格 outcome 签名集, 已消费事件 id 集)。"""
        candidates, sigs, ids = [], set(), set()
        for ev in evs:
            if ev.get("event_type") not in (EVENT_OBSERVATION, EVENT_SELF_STATE):
                continue
            if not self._subject_relevant(action_event, ev):
                continue                      # §七：无主体相关 → 永不归因
            score = self._score_candidate(action_event, ev, window_s)
            if score < MIN_SCORE:
                continue
            o_sig = outcome_signature(ev)
            sigs.add(o_sig)
            ids.add(ev.get("id"))
            candidates.append({"action": action_signature(action_event),
                               "outcome": o_sig, "score": score,
                               "ts": ev.get("ts"), "event_id": ev.get("id"),
                               "ref": ev.get("source")})
        candidates.sort(key=lambda c: -c["score"])
        return candidates, sigs, ids

    def _on_timeline_event(self, e: dict):
        """时间轴观察者：新事件先推进/关闭过期窗，再喂给还开着的窗。"""
        ts = float(e.get("ts") or time.time())
        wall = time.time()
        ready = []
        with self._lock:
            keep = []
            for p in self._pending:
                if ts >= p["deadline"] or wall >= p["deadline"]:
                    ready.append(p)
                else:
                    keep.append(p)
            self._pending = keep
            if e.get("event_type") in (EVENT_OBSERVATION, EVENT_SELF_STATE):
                for p in self._pending:
                    if (e.get("id") in p["ids"]
                            or e.get("id") == p["action"].get("id")
                            or not (p["action"].get("ts", 0) - 1e-6
                                    <= ts <= p["deadline"])):
                        continue
                    cands, sigs, ids = self._match(
                        p["action"], [e],
                        p["deadline"] - p["action"].get("ts", 0))
                    if cands:
                        p["sigs"] |= sigs
                        p["ids"] |= ids
                        p["cands"] = (p["cands"] + cands)
                        p["cands"].sort(key=lambda c: -c["score"])
        for p in ready:
            self._finalize(p)

    def sweep(self, now: float = None):
        """显式关窗（壁钟或给定的事件时间）。时间轴安静时的兜底；
        也是 debug/测试的确定性把手。"""
        now = time.time() if now is None else float(now)
        with self._lock:
            ready = [p for p in self._pending if now >= p["deadline"]]
            self._pending = [p for p in self._pending if now < p["deadline"]]
        for p in ready:
            self._finalize(p)
        return len(ready)

    def _finalize(self, pending: dict):
        """关窗记账（原 record_action 的账本部分，一字不改语义）：
        obs+1、合格 outcome 记 support（含条件签名）、缺席的预期结果记
        contradiction、然后假设评估（含晋升触发）。"""
        action_event = pending["action"]
        a_sig = pending["a_sig"]
        qualified_sigs = set(pending["sigs"])
        candidates = pending["cands"]
        # §P2b（2026-09-27 收敛修复）：感知最后否决——窗口内**世界**观察
        # 确认了效果已发生（库存 +1 / 方块出现…，方向为正向），则同窗口
        # 的 failed:* 自身状态结果（桥超时/回执丢失/轮询异常的合称 transport
        # 族）是仪器噪声：不计失败支撑、不进入 success_rate/blocker 账。
        # 世界真值压过中介观测；"动作没成功"只记在没有世界确认的窗口。
        failure_sigs = {s for s in qualified_sigs
                        if s.startswith("self:") and ":failed:" in s}
        late_confirmed = bool(failure_sigs and
                              self._window_confirms_effect(action_event,
                                                           candidates))
        if late_confirmed:
            qualified_sigs = qualified_sigs - failure_sigs
            import fas_log
            fas_log.emit(fas_log.MEMORY, "INFO", "causal_late_confirmed",
                         f"世界确认覆盖迟到失败: {a_sig}",
                         action_sig=a_sig, superseded=sorted(failure_sigs)[:4])
            try:
                if xm is not None and xm.enabled():
                    xm.xlog("LEARNING",
                            f"迟到世界确认: {a_sig} 覆盖 "
                            f"{sorted(failure_sigs)[:2]}",
                            覆盖=len(failure_sigs))
            except Exception:
                pass
        csig = context_signature(action_event)
        with self._lock:
            self._candidates_recent.extend(candidates)
            del self._candidates_recent[:-40]
            agg = self._aggregations.setdefault(
                a_sig, {"obs": 0, "outcomes": {}})
            if late_confirmed:   # 记账留痕：§18 可验证"已覆盖 n 次"
                agg["late_confirmed"] = int(agg.get("late_confirmed", 0)) + 1
                agg["late_confirmed_last"] = (agg.get("last_seen")
                                              or agg.get("first_seen")
                                              or time.strftime("%Y/%m/%d %H:%M:%S"))
            # P2b 续：世界确认 = 一次由世界背书的成功。记一条合成结果
            # （succeeded:late_confirmed）——action_prior 的 success_rate
            # 只认 :succeeded；不记的话"桥掉回执但世界办成了"会以
            # success=0 的形态压低成功率，测试 C6 的断言即由此而来。
            if late_confirmed:
                _lks = outcome_signature(make_event(
                    EVENT_SELF_STATE,
                    str(action_event.get("actor") or "self"),
                    str(action_event.get("subject") or ""),
                    {"change": "succeeded:late_confirmed"}))
                _o = agg["outcomes"].setdefault(
                    _lks, {"support": 0, "contra": 0,
                           "first": (agg.get("last_seen")
                                     or time.strftime("%Y/%m/%d %H:%M:%S")),
                           "last": None})
                _o["support"] = int(_o.get("support", 0)) + 1
                _o["last"] = (agg.get("last_seen")
                              or time.strftime("%Y/%m/%d %H:%M:%S"))
                _c = _o.setdefault("contexts", {}).setdefault(
                    csig, {"support": 0, "contra": 0})
                _c["support"] = int(_c.get("support", 0)) + 1
            n_before = int(agg.get("obs", 0))
            agg["obs"] = n_before + 1
            agg["last_seen"] = time.strftime("%Y/%m/%d %H:%M:%S")
            if not agg.get("first_seen"):
                agg["first_seen"] = agg["last_seen"]
            for o_sig in qualified_sigs:
                o = agg["outcomes"].setdefault(
                    o_sig, {"support": 0, "contra": 0,
                            "first": agg["last_seen"], "last": None})
                o["support"] += 1
                o["last"] = agg["last_seen"]
                c = o.setdefault("contexts", {}).setdefault(
                    csig, {"support": 0, "contra": 0})
                c["support"] += 1
            # 反例记账：历史上高频伴随的结果这次缺席 → contradiction
            for o_sig, o in agg["outcomes"].items():
                if o_sig in qualified_sigs:
                    continue
                rate = o["support"] / max(1, n_before)
                if n_before >= 2 and rate >= EXPECTED_RATE:
                    o["contra"] = int(o.get("contra", 0)) + 1
                    cc = o.setdefault("contexts", {}).setdefault(
                        csig, {"support": 0, "contra": 0})
                    cc["contra"] += 1
                    logger.info(
                        "[Causal] 反例: %s 未伴随 %s（历史支撑率 %.0f%%，"
                        "contra=%d）", a_sig, o_sig, rate * 100, o["contra"])
        if candidates:
            logger.info(
                "[Causal] 候选结果: %s → %s",
                a_sig, [(c["outcome"], c["score"]) for c in candidates[:3]])
        # B7/§24 trace 标记：settled → attributed → promoted 贯通一条动作链
        # （action_sig 可与 action_settled 的 sig 对照；晋升在 causal_promoted）
        if qualified_sigs or candidates:
            import fas_log
            fas_log.emit(fas_log.MEMORY, "INFO", "causal_attributed",
                         f"归因关窗: {a_sig}",
                         action_sig=a_sig, attributed=len(qualified_sigs),
                         candidates=len(candidates), context=csig[:80])
        try:
            if xm is not None and xm.enabled():
                xm.xlog("LEARNING",
                        f"归因 {a_sig} → "
                        + ("; ".join(sorted(qualified_sigs))[:140]
                           or "窗口内无可归因变化"),
                        候选=len(candidates))
        except Exception:
            pass
        self._hypothesize(a_sig)
        self._persist()

    # ── 置信度（平滑 + 反例惩罚；永远 <1.0）────────────────

    def confidence(self, action_sig: str, o_sig: str) -> float:
        with self._lock:
            o = ((self._aggregations.get(action_sig) or {}).get("outcomes") or {}).get(o_sig)
            if not o:
                return 0.0
            s = int(o.get("support", 0))
            c = int(o.get("contra", 0))
            return round(s / (s + c + CONF_SMOOTHING), 3)

    def _hypothesize(self, action_sig: str):
        """重复聚合 → 假设（support≥MIN_SUPPORT 且 confidence≥门槛）。
        越过晋升线的当场触发 promote_to_kg——这是晋升链的**唯一生产调用点**
        （此前 promote 零调用者，§十二的"够稳才进图"整条链断在末端）。"""
        need_promote = False
        with self._lock:
            agg = self._aggregations.get(action_sig) or {}
            for o_sig, o in (agg.get("outcomes") or {}).items():
                support = int(o.get("support", 0))
                conf = self.confidence(action_sig, o_sig)
                key = f"{action_sig}|{o_sig}"
                if support >= MIN_SUPPORT and conf >= HYP_CONFIDENCE:
                    h = self._hypotheses.get(key) or {}
                    is_new = not h
                    h.update({
                        "action": action_sig, "outcome": o_sig,
                        "observations": int(agg.get("obs", 0)),
                        "support": support,
                        "contradictions": int(o.get("contra", 0)),
                        "confidence": conf,
                        "first_observed": o.get("first"),
                        "last_observed": o.get("last"),
                        "status": "hypothesis",   # hypothesis ≠ 事实
                    })
                    self._hypotheses[key] = h
                    if is_new:
                        logger.info(
                            "[Causal] ✦ 新因果假设: %s → %s "
                            "(observations=%d support=%d contra=%d confidence=%.2f)",
                            action_sig, o_sig, h["observations"], support,
                            h["contradictions"], conf)
                        try:
                            if xm is not None and xm.enabled():
                                xm.xlog("LEARNING",
                                        f"假设成立: {action_sig} → {o_sig}",
                                        support=support, conf=round(conf, 3))
                        except Exception:
                            pass
                    try:
                        if xm is not None and xm.enabled():
                            xm.xlog("CONFIDENCE", f"{action_sig} → {o_sig}",
                                    support=support,
                                    contra=int(o.get("contra", 0)),
                                    conf=round(conf, 3),
                                    观察=int(agg.get("obs", 0)))
                    except Exception:
                        pass
                    if (self.kg is not None and key not in self._promoted
                            and support >= KG_SUPPORT and conf >= KG_CONFIDENCE):
                        need_promote = True
                elif key in self._hypotheses and conf < HYP_CONFIDENCE:
                    # 反例累积：假设降回观察（不是删除——保留历史）
                    self._hypotheses[key]["status"] = "weakened"
                    self._hypotheses[key]["confidence"] = conf
                    logger.info("[Causal] 假设降级: %s confidence=%.2f", key, conf)
        if need_promote:
            try:
                self.promote_to_kg()
            except Exception as e:
                logger.warning("[Causal] 自动晋升失败（下轮再试）: %s", e)

    # ── KG 晋升：只写足够稳定的泛化关系（§十二）────────────

    def promote_to_kg(self, kg=None, engine=None) -> list:
        kg = kg or self.kg
        engine = engine or self.engine
        if kg is None:
            return []
        promoted = []
        with self._lock:
            for key, h in list(self._hypotheses.items()):
                if (h.get("status") != "hypothesis"
                        or key in self._promoted
                        or int(h.get("support", 0)) < KG_SUPPORT
                        or float(h.get("confidence", 0)) < KG_CONFIDENCE):
                    continue
                a_sig, o_sig = h["action"], h["outcome"]
                # 节点：操作 与 状态变化（泛化知识，semantic 空间）
                a_id = f"操作:{a_sig}"
                o_id = f"变化:{o_sig}"
                from graph_model import Node, Edge, now_str
                if a_id not in kg.nodes:
                    kg.add_node(Node(id=a_id, weight=0.5,
                                     label="declarative-semantic",
                                     graph_space="semantic",
                                     extra_attrs={"type": "action_pattern",
                                                  "source": "causal_hypothesis",
                                                  "created": now_str()}))
                if o_id not in kg.nodes:
                    kg.add_node(Node(id=o_id, weight=0.5,
                                     label="declarative-semantic",
                                     graph_space="semantic",
                                     extra_attrs={"type": "state_change",
                                                  "source": "causal_hypothesis",
                                                  "created": now_str()}))
                e = kg.get_edge(a_id, o_id, "导致")
                if e is None:
                    kg.add_edge(Edge(src=a_id, dst=o_id, relation="导致",
                                     weight=float(h["confidence"]),
                                     relation_category="causal_relation"))
                else:
                    e.weight = float(h["confidence"])
                    e.touch()
                # provenance 全量在边上，可追溯（§十四）
                for n in (kg.nodes[a_id], kg.nodes[o_id]):
                    n.extra_attrs.update({
                        "observations": h["observations"],
                        "support": h["support"],
                        "contradictions": h["contradictions"],
                        "confidence": h["confidence"],
                        "hypothesis_key": key,
                        "last_updated": now_str(),
                    })
                self._promoted.add(key)
                promoted.append(key)
                logger.info(
                    "[Causal] 晋升 KG: %s -[导致 %.2f]-> %s "
                    "(support=%d contra=%d observations=%d)",
                    a_id, h["confidence"], o_id, h["support"],
                    h["contradictions"], h["observations"])
                try:
                    if xm is not None and xm.enabled():
                        xm.xlog("GRAPH",
                                f"因果晋升 {a_id} -[导致 {h['confidence']:.2f}]-> {o_id}",
                                support=int(h["support"]),
                                contra=int(h["contradictions"]))
                except Exception:
                    pass
                try:
                    import fas_log
                    fas_log.emit(fas_log.MEMORY, "INFO", "causal_promoted",
                                 f"{a_id} -[导致 {h['confidence']:.2f}]-> {o_id}",
                                 action=a_sig, outcome=o_sig,
                                 support=int(h["support"]),
                                 contradictions=int(h["contradictions"]),
                                 observations=int(h["observations"]),
                                 confidence=float(h["confidence"]))
                except Exception:
                    pass
        if promoted and engine is not None:
            try:
                engine.mark_active([f"操作:{h['action']}" for h in
                                    (self._hypotheses[k] for k in promoted)])
            except Exception:
                pass
        if promoted:
            self._persist()
        return promoted

    # ── 行动先验（候选评分消费：从自身经历学"这件事我行不行"）──

    def action_prior(self, intent: str, target: str = "") -> dict:
        """查询某类动作的历史成功率与已知阻碍（§十五落地）。

        返回 {"success_rate": float|None, "blockers": [...], "obs": int}。
        blockers 如 ["tool_missing:stone_pickaxe"] —— 自主候选评分用它
        压低"以前没工具就失败过"的动作；认知层也可以据此先去合成工具。
        """
        sig = f"{intent}({target})" if target else intent
        with self._lock:
            agg = self._aggregations.get(sig) or self._aggregations.get(intent) or {}
            outcomes = agg.get("outcomes") or {}
            obs = int(agg.get("obs", 0))
            if not outcomes or obs == 0:
                return {"success_rate": None, "blockers": [], "obs": 0}
            success = 0
            blockers = {}
            for osig, o in outcomes.items():
                support = int(o.get("support", 0))
                if ":succeeded" in osig:
                    success += support
                elif ":failed:" in osig:
                    reason = osig.split(":failed:", 1)[1]
                    blockers[reason] = blockers.get(reason, 0) + support
            ranked = sorted(blockers.items(), key=lambda kv: -kv[1])
            return {"success_rate": round(success / max(1, obs), 3),
                    "blockers": [r for r, _ in ranked[:2]], "obs": obs}

    # ── debug（§十四）────────────────────────────────────

    def debug_report(self, n: int = 12) -> dict:
        with self._lock:
            recent_actions = [e for e in self.timeline.recent(n * 2)
                              if e.get("event_type") == EVENT_ACTION][:n]
            return {
                "pending_windows": len(self._pending),
                "recent_candidates": list(self._candidates_recent)[-n:],
                "recent_actions": [
                    {"ts": e.get("ts_str"), "sig": action_signature(e)}
                    for e in recent_actions],
                "aggregations": {
                    a: {"obs": v.get("obs"),
                        "outcomes": {o: {"support": ov.get("support"),
                                         "contra": ov.get("contra", 0),
                                         "confidence": self.confidence(a, o)}
                                     for o, ov in (v.get("outcomes") or {}).items()}}
                    for a, v in list(self._aggregations.items())[-n:]},
                "hypotheses": dict(list(self._hypotheses.items())[-n:]),
                "promoted": sorted(self._promoted),
            }
