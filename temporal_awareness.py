# temporal_awareness.py — Temporal State: 持续的世界时间状态（2026-09-20 重构）
# ============================================================================
# 旧设计：时间认知是"正则触发器"——用户说出 现在/凌晨/熬夜… 才锚定时段。
# 新设计（核心原则：时间只告诉 FAS"现在是什么状态"，不决定"该做什么"）：
#
#   现实世界时钟 ──update_clock_state()──► 图谱（CC 节拍驱动，分钟级）
#       · 当前作息感知 节点：基础事实（hour/minute/bucket/day_phase/date,
#         source=real_world）——不存推理结论
#       · 当前时段桶节点 + 其昼夜节点：持续激活地板（具身"在视状态"同款
#         语义）——参与正常扩散/top-k，语言层与竞争随时知道现在几点
#       · 跨时段 = 时间事件：新桶激活脉冲 +（有 timeline 时）统一经验
#         时间轴 COGNITIVE_EVENT（source=real_world），不另造事件系统
#   时间词正则（TIME_DEICTIC_RE）——降级——► 语言侧相关性线索：命中时给
#       当前时段与睡眠/作息概念额外激活；**没有时间词，时间状态照样在线**。
#
#   23:00–7:00 不再是"睡眠裁判"（sleep_relation 判定文本已删）——
#   降级为图上常识边：[睡眠]-[理想时段]->[理想睡眠时段]-[涵盖]->[夜间]、
#   [熬夜]-[偏离]->[理想睡眠时段]。时段激活沿现有扩散点亮常识子图里的
#   睡眠/休息概念；作息是否异常、要不要关心，由 Drive/情绪/关系/经历在
#   既有机制里综合，或由经验自己长出来——不由本模块裁决。
#
#   统一时间抽象（§十一）：昼夜层（白天/夜间）是共享概念，时段层
#   （凌晨…深夜）带 source=real_world；Minecraft 昼夜若将来上图，用
#   source=minecraft 的同一形态，不共享"凌晨"这类现实时段名——
#   防"现实凌晨 = MC 凌晨"的语义污染。
# ============================================================================

import re
import logging
from datetime import datetime

from graph_model import Node, Edge

logger = logging.getLogger(__name__)

# 时间指示语（封闭语言学类别，仅作**语言相关性线索**，
# 不再是时间认知的开关）
TIME_DEICTIC_RE = re.compile(
    r"现在|今晚|今天晚上|熬夜|还没睡|没睡|半夜|凌晨|通宵|几点|这么晚|很晚|早起|清晨|大半夜")

# 语言线索命中时额外点亮的作息概念（图上既有节点，缺位跳过——不造平行状态）
_TEMPORAL_CONCEPT_NODES = ("睡眠", "熬夜", "理想睡眠时段", "当前作息感知")

# 理想睡眠窗口：**只作为常识节点属性存在**（start/end_hour），
# 不再参与任何判定函数。任何代码不得据此产生行为结论。
IDEAL_SLEEP_START = 23
IDEAL_SLEEP_END = 7

# 时段桶（连续时间的工程离散化；带 source 供统一抽象）
BUCKETS = [
    ("清晨", 5, 8), ("上午", 8, 11), ("中午", 11, 13), ("下午", 13, 17),
    ("傍晚", 17, 19), ("晚上", 19, 23), ("深夜", 23, 24), ("凌晨", 0, 5),
]
BUCKET_START_HOUR = {"清晨": 5, "上午": 8, "中午": 11, "下午": 13,
                     "傍晚": 17, "晚上": 19, "深夜": 23, "凌晨": 0}
# 时段 → 昼夜（day_phase：与现实/MC 可共享的抽象层）
BUCKET_PHASE = {"清晨": "白天", "上午": "白天", "中午": "白天",
                "下午": "白天", "傍晚": "白天", "晚上": "夜间",
                "深夜": "夜间", "凌晨": "夜间"}

# 时段 → **夜间度**（0=正午般明亮，1=深夜般昏暗）。R2 P10：这是全仓唯一
# 把"小时"翻译成"事实"的地方——它是时钟传感器的一部分，不是判据。
# 调制器侧（褪黑素样/组胺样）**只读图上写好的属性**，代码里不出现 hour 比较
# （闸门 tests/test_circadian_modulators.py 用源码扫描钉住这条）。
# 尺度是功能类比（禁令 9：不当作医学模拟）：晚上开始抬、深夜最高、清晨回落。
BUCKET_CIRCADIAN = {"凌晨": 1.00, "深夜": 0.95, "晚上": 0.60, "傍晚": 0.30,
                    "下午": 0.05, "中午": 0.00, "上午": 0.00, "清晨": 0.15}
# 节点属性名（**知识**与**此刻在势**分开存，这是本期重要区分）：
#   circadian_load     = 这个时段本身有多"夜"（静态知识，播种时写）
#   circadian_strength = 此刻这一事实有多成立 = 夜间度 ×(是不是当前)
#                        —— 只有 `update_clock_state` 会抬它。语言线索点亮一个
#                        时段节点不会伪装成"现在真是深夜"（strength=0 ⇒ 不驱动）。
CIRCADIAN_LOAD_KEY = "circadian_load"
CIRCADIAN_STRENGTH_KEY = "circadian_strength"

STATE_NODE_ID = "当前作息感知"
BUCKET_FLOOR = 0.6       # 当前桶的持续激活地板
PHASE_FLOOR = 0.5        # 昼夜节点地板
TRANSITION_PULSE = 1.5   # 跨时段：新桶激活脉冲


def hour_bucket(dt: datetime = None) -> str:
    h = (dt or datetime.now()).hour
    for name, a, b in BUCKETS:
        if a <= h < b:
            return name
    return "深夜"


def ensure_bucket_nodes(kg) -> dict:
    """幂等播种：时段桶、昼夜相位、睡眠常识子图。返回 {bucket: node_id}。"""
    with kg._lock:
        for name, _a, _b in BUCKETS:
            if name not in kg.nodes:
                kg.add_node(Node(
                    id=name, weight=0.5, label="declarative-semantic",
                    graph_space="semantic",
                    extra_attrs={"type": "time_of_day", "canon": "state",
                                 "source": "real_world",
                                 "phase": BUCKET_PHASE[name],
                                 CIRCADIAN_LOAD_KEY: BUCKET_CIRCADIAN[name],
                                 CIRCADIAN_STRENGTH_KEY: 0.0}))
            else:
                ea = dict(kg.nodes[name].extra_attrs or {})
                ea.setdefault("source", "real_world")
                ea.setdefault("phase", BUCKET_PHASE[name])
                ea.setdefault("type", "time_of_day")
                ea.setdefault("canon", "state")
                # 播种不覆盖既有的 strength（那是时钟写的"此刻"），只保证键在位
                ea.setdefault(CIRCADIAN_LOAD_KEY, BUCKET_CIRCADIAN[name])
                ea.setdefault(CIRCADIAN_STRENGTH_KEY, 0.0)
                kg.nodes[name].extra_attrs = ea
        for phase in ("白天", "夜间"):
            if phase not in kg.nodes:
                kg.add_node(Node(
                    id=phase, weight=0.5, label="declarative-semantic",
                    graph_space="semantic",
                    extra_attrs={"type": "day_phase", "canon": "concept",
                                 "source": "real_world",
                                 CIRCADIAN_STRENGTH_KEY: 0.0}))
            else:
                ea = dict(kg.nodes[phase].extra_attrs or {})
                ea.setdefault(CIRCADIAN_STRENGTH_KEY, 0.0)
                kg.nodes[phase].extra_attrs = ea
        # 作息常识（公共知识；23-7 只是节点属性知识，无判定逻辑）
        commons = ["睡眠", "理想睡眠时段", "熬夜", "健康"]
        for nid in commons:
            if nid not in kg.nodes:
                meta = {"source": "common_knowledge"}
                if nid == "理想睡眠时段":
                    meta.update({"start_hour": IDEAL_SLEEP_START,
                                 "end_hour": IDEAL_SLEEP_END,
                                 "canon": "state"})
                kg.add_node(Node(
                    id=nid, weight=0.5, label="declarative-semantic",
                    graph_space="semantic", extra_attrs=meta))

        def _e(s, r, d, w=0.6, cat="semantic_relation"):
            if s in kg.nodes and d in kg.nodes and not any(
                    x.src == s and x.relation == r and x.dst == d
                    for x in kg.edges):
                kg.add_edge(Edge(src=s, dst=d, relation=r, weight=w,
                                 relation_category=cat))
        _e("睡眠", "理想时段", "理想睡眠时段", 0.9)
        _e("理想睡眠时段", "涵盖", "夜间", 0.7)
        for bkt, phase in BUCKET_PHASE.items():
            _e(bkt, "属于", phase, 0.8)
        _e("熬夜", "偏离", "理想睡眠时段", 0.7)
        _e("熬夜", "可能影响", "健康", 0.6)
    return {name: name for name, _a, _b in BUCKETS}


def current_bucket(kg):
    """图上当前时段的读点（真相源=当前作息感知节点；无则 None）。"""
    with kg._lock:
        sn = kg.get_node(STATE_NODE_ID)
        if sn is None:
            return None
        return str((sn.extra_attrs or {}).get("bucket") or "") or None


def update_clock_state(kg, engine=None, now: datetime = None,
                       timeline=None) -> dict:
    """时钟节拍（CC 每分钟级）：把现实时间写为图谱的持续世界状态。

    幂等：状态节点覆盖更新、时段桶不重复建；跨迁移才脉冲/写事件。
    """
    now = now or datetime.now()
    ensure_bucket_nodes(kg)
    bucket = hour_bucket(now)
    phase = BUCKET_PHASE[bucket]
    transition = None
    with kg._lock:
        sn = kg.get_node(STATE_NODE_ID)
        if sn is None:
            sn = Node(id=STATE_NODE_ID, weight=0.5,
                      label="declarative-semantic", graph_space="self",
                      extra_attrs={"type": "temporal_state",
                                   "canon": "cognitive_state"})
            kg.add_node(sn)
        ea = dict(sn.extra_attrs or {})
        for stale in ("sleep_relation", "ideal_window"):   # 裁判字段清除
            ea.pop(stale, None)
        prev_bucket = ea.get("bucket")
        load = float(BUCKET_CIRCADIAN.get(bucket, 0.0))
        prev_load = float(BUCKET_CIRCADIAN.get(str(prev_bucket or ""), load))
        ea.update({
            "source": "real_world",
            # hour/minute 是**观测事实**（给语言层/前端看），不是判据：
            # 调制器侧一律读下面写的 circadian_strength，不做小时比较。
            "time": now.strftime("%H:%M"), "hour": now.hour,
            "minute": now.minute, "date": now.strftime("%Y/%m/%d"),
            "bucket": bucket, "day_phase": phase,
            CIRCADIAN_LOAD_KEY: load,
            "updated": now.strftime("%Y/%m/%d %H:%M:%S"),
        })
        sn.extra_attrs = ea
        sn.touch()
        # ── R2 P10：把"现在是几点"写成**驱动用的事实** ──
        # 唯一作者是本函数（时钟），所以语言线索点亮某个时段节点**不会**伪装成
        # "现在真是深夜"：strength 只按时段决定，与 activation 无关。
        # 也不乘 activation：调制通道不该借用扩散的货币（P6 的同一纪律），
        # 而且不乘才让"同一 bucket 序列 ⇒ 同一曲线"成立（P10 闸门）。
        for name in BUCKET_PHASE:                    # 8 个时段桶
            node = kg.nodes.get(name)
            if node is None:
                continue
            na = dict(node.extra_attrs or {})
            na[CIRCADIAN_LOAD_KEY] = BUCKET_CIRCADIAN[name]
            na[CIRCADIAN_STRENGTH_KEY] = load if name == bucket else 0.0
            node.extra_attrs = na
        for ph, val in (("夜间", load), ("白天", round(1.0 - load, 4))):
            node = kg.nodes.get(ph)
            if node is None:
                continue
            na = dict(node.extra_attrs or {})
            na[CIRCADIAN_STRENGTH_KEY] = val
            node.extra_attrs = na
        # 当前桶与昼夜：地板激活（"现在看得见的状态"语义）
        for nid, floor in ((bucket, BUCKET_FLOOR), (phase, PHASE_FLOOR)):
            node = kg.nodes.get(nid)
            if node is not None and float(node.activation or 0.0) < floor:
                node.activation = floor
                node.touch()
        if prev_bucket and prev_bucket != bucket:
            nb = kg.nodes.get(bucket)
            if nb is not None:
                nb.activation = min(5.0, float(nb.activation or 0.0)
                                    + TRANSITION_PULSE)
                nb.touch()
            transition = {"from": prev_bucket, "to": bucket,
                          "at": ea["time"],
                          # 事件幅度也是**图上事实之差**，不是拍脑袋常数：
                          # 上午→中午（夜间度都没变）⇒ delta=0 ⇒ 不打脉冲。
                          "load_from": prev_load, "load_to": load,
                          "strength_delta": round(abs(load - prev_load), 4)}
    ids = [i for i in (STATE_NODE_ID, bucket, phase) if i in kg.nodes]
    if engine is not None:
        try:
            engine.mark_active(ids)
        except Exception:
            pass
    if transition:
        if timeline is not None:
            try:
                _emit_transition(timeline, transition)
            except Exception as e:
                logger.debug(f"[Time] 迁移事件写入跳过: {e}")
        logger.info(f"[Time] 时段迁移 {transition['from']}→{transition['to']}")
    return {"bucket": bucket, "day_phase": phase,
            "transition": transition, "clock": ea["time"]}


def circadian_facts(kg) -> dict:
    """只读视图：调制器这一拍会看到什么（观测/闸门/回放都拿它当证据）。

    没有任何小时运算——全部从节点属性读。时钟没跑过（strength 键不在）时
    返回 `ready=False`，让调用方**如实承认"还没有这个事实"**，
    而不是拿 0 之外的默认值冒充昼夜。
    """
    out = {"ready": False, "bucket": None, "day_phase": None,
           "load": None, "phase_strength": {}, "bucket_strength": {}}
    if kg is None:
        return out
    with kg._lock:
        sn = kg.get_node(STATE_NODE_ID)
        if sn is not None:
            ea = sn.extra_attrs or {}
            out["bucket"] = ea.get("bucket")
            out["day_phase"] = ea.get("day_phase")
            out["load"] = ea.get(CIRCADIAN_LOAD_KEY)
        for name in list(BUCKET_PHASE) + ["白天", "夜间"]:
            node = kg.nodes.get(name)
            if node is None:
                continue
            v = (node.extra_attrs or {}).get(CIRCADIAN_STRENGTH_KEY)
            if v is None:
                continue
            v = float(v)
            (out["phase_strength"] if name in ("白天", "夜间")
             else out["bucket_strength"])[name] = round(v, 4)
    out["ready"] = out["load"] is not None
    return out


def _emit_transition(timeline, tr: dict):
    """时段迁移 → 统一经验时间轴（COGNITIVE_EVENT，source 标注）。"""
    from experience import make_event, EVENT_COGNITIVE
    timeline.append(make_event(
        EVENT_COGNITIVE, actor="Haru", subject="时间感知",
        content={"change": f"time_bucket:{tr['from']}→{tr['to']}",
                 "source": "real_world", "at": tr["at"]},
        source="temporal_awareness"))


def ground(text: str, kg, engine) -> str | None:
    """语言线索通路（2026-09-20 降级）：话语含时间指示语时，给当前时段与
    睡眠/作息概念额外激活——"提到时间"让时间相关认知更可及，仅此而已。

    时间状态本身由 update_clock_state 持续写入，与本函数无关：
    用户什么都不说，凌晨三点也是图谱的当下。
    返回当前时段桶 id（保持 link_event_to_bucket 的兼容调用面）。
    """
    if not TIME_DEICTIC_RE.search(str(text or "")):
        return current_bucket(kg)
    bucket = current_bucket(kg)
    if bucket is None:
        # 时钟还没跑过（启动窗口）：补一次状态更新（幂等、零判断）
        st = update_clock_state(kg, engine)
        bucket = st["bucket"]
    lit = [bucket]
    with kg._lock:
        node = kg.nodes.get(bucket)
        if node is not None:
            node.activation = min(5.0, float(node.activation or 0.0) + 1.5)
            node.touch()
        for nid in _TEMPORAL_CONCEPT_NODES:
            n = kg.nodes.get(nid)
            if n is not None:
                n.activation = min(5.0, float(n.activation or 0.0) + 0.8)
                n.touch()
                lit.append(nid)
    if engine is not None:
        try:
            engine.mark_active(lit)
        except Exception:
            pass
    logger.info(f"[Time] 语言线索: {bucket}（时间词命中，"
                f"额外点亮 {len(lit) - 1} 个作息概念）")
    return bucket


def link_event_to_bucket(kg, event_id: str, bucket: str):
    """事件框架收尾后：为本次事件补 发生于时段 边（通用槽位）。"""
    if not bucket or not event_id:
        return
    with kg._lock:
        if event_id in kg.nodes and bucket in kg.nodes:
            if not any(e.src == event_id and e.relation == "发生于时段"
                       and e.dst == bucket for e in kg.edges):
                kg.add_edge(Edge(src=event_id, dst=bucket,
                                 relation="发生于时段", weight=0.6,
                                 relation_category="temporal_relation"))
                logger.info(f"[Time] {event_id} -[发生于时段]-> {bucket}")
