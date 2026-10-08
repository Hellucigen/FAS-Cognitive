# modulation_events.py — R2 P7：结构化调制事件 + 图上扇出（设计决定 D4）
# ============================================================================
# 解决的具体问题：`reward.release()` 原来用逐激素 if/else 决定"哪个事件动哪个激素、
# 动多少"（.25/.06/.05/.04/−.05/.02 六个常数写在函数体里）。禁令 1 要求这件事是**数据**。
#
# 结构（与 P6 的 `modulator_subgraph` 同构，不发明第二套机制）：
#
#   ModulationEvent      纯数据事件（§10 的十个字段），不改任何东西
#   ensure_event_types() 幂等 bootstrap：`事件类型:<名>` 节点 + `-[影响 w]-> 调制器镜像` 边
#   ModulatorEngine      通用应用器：扇出**读图上的边**，门控读事件类型节点的
#                        `extra_attrs`（Edge 没有 extra_attrs，实测——所以每条边的
#                        语义细节存在事件类型节点上，按调制器名索引）
#
# 三条刻意的设计边界：
#   1) **数值写入仍只走 internal_state**（I1 单一写者）：本模块只决定"哪个调制器、
#      多大幅度、走 tonic 还是 phasic"，写由 `apply_delta` / `pulse` 完成——
#      于是 P3 的夹速/习惯化/不应期/受体曲线与 P6 的 phasic→图激活自动生效。
#   2) **调制器不决定内容**（禁令 4）：这里没有任何 LLM 调用、没有回答文本、没有
#      动作执行；出口只有"写一个调制器的数"。
#   3) **无图则退回出厂表**（与 internal_state 的 MODULATOR_SPEC 兜底同一策略）：
#      `InternalState(kg=None)` 的离线状态对象仍然存在，此时按 config.event_rules
#      原样应用；有图时图是权威（改边权=改效力，测试 C 组锁这条）。
#
# `交互` 边（P5 注册的第五个词）在这里有真实消费者：一个调制器的**当前偏差**改变
# 另一个调制器接收事件时的增益（`gain = Π(1 + w × dev(源))`，逐边夹到 [0.4,2.0]、
# 总增益夹 [0.25,3.0]）。这是"endorphin 缓冲压力"（场景 10）的实现方式——
# 不是 `if endorphin>0.6`，而是图上一条带符号权重的边。
# ============================================================================
"""结构化调制事件与图驱动扇出（R2 P7）。"""

import logging
import math
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MS = "modulator_system"
EVENT_PREFIX = "事件类型:"
EVENT_RELATION = "影响"                 # P5 已在册（causal_relation）
EDGE_RELATION_CATEGORY = "causal_relation"
# 事件字段 → 幅度。`const` 让"固定增量"型规则（情绪共振表）也能进同一张表：
# 权重本身就是 delta。`intensity` 缺省回落到 |valence|，所以调用方可以只给效价。
SIGNALS = ("const", "valence", "intensity", "rpe", "abs_rpe", "novelty",
           "uncertainty", "social_relevance", "goal_relevance", "success")
CHANNELS = ("tonic", "phasic")
GAIN_EDGE_MIN, GAIN_EDGE_MAX = 0.4, 2.0
GAIN_TOTAL_MIN, GAIN_TOTAL_MAX = 0.25, 3.0


@dataclass
class ModulationEvent:
    """一次调制事件（§10）。纯数据：构造它不改变任何状态。

    字段的**语义来源**由产生它的模块决定（奖赏、意外、新奇、时段…），
    哪个字段被拿去算幅度由**图上的规则**决定（见 `signal`）。
    """
    event_type: str
    source: str = "system"              # reward | emotion | prediction | action | circadian
    valence: float = 0.0
    intensity: float = 0.0              # 0 → 取 |valence|
    rpe: float = 0.0                    # 奖励预测误差（有符号）
    novelty: float = 0.0
    uncertainty: float = 0.0
    social_relevance: float = 0.0
    goal_relevance: float = 0.0
    success: bool = False
    cycle_id: str = None
    ref: str = ""
    ts: float = field(default_factory=time.time)

    def value_of(self, signal: str) -> float:
        s = str(signal or "valence")
        if s == "const":
            return 1.0
        if s == "intensity":
            return abs(float(self.intensity or 0.0)) or abs(float(self.valence or 0.0))
        if s == "abs_rpe":
            return abs(float(self.rpe or 0.0))
        if s == "success":
            return 1.0 if self.success else 0.0
        if s in ("valence", "rpe"):
            return float(getattr(self, s) or 0.0)
        return float(getattr(self, s, 0.0) or 0.0)

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["intensity"] = self.value_of("intensity")
        return d


# ── 出厂事件表 ─────────────────────────────────────────────

def _blk(config) -> dict:
    return ((config or {}).get(MS) or {})


def emotion_rules(config) -> list:
    """把 `config.emotion_hormone_modulation`（情绪词→激素增量）翻成事件规则。

    D4 要求"消除重复表"：这张表不再是**第二条事件通道**，只是同一张事件表的
    另一批出厂种子——app 的情绪共振从此只发一个事件，扇出由图决定。
    signal=const：权重本身就是原来的 delta（情绪共振不按幅度缩放）。
    """
    out = []
    for word, mods in ((config or {}).get("emotion_hormone_modulation") or {}).items():
        for name, delta in (mods or {}).items():
            out.append({"type": f"emotion:{word}", "mod": name, "w": float(delta),
                        "signal": "const", "channel": "tonic", "gate": {},
                        "clamp": None, "why": f"情绪共振出厂表迁移：{word}→{name}"})
    return out


def seed_rules(config) -> list:
    """config.event_rules + 情绪表 → 归一化的规则行。"""
    blk = _blk(config)
    dflt = blk.get("event_default") or {}
    rows = []
    for row in (blk.get("event_rules") or []) + emotion_rules(config):
        if not isinstance(row, dict):
            continue
        rows.append({
            "type": str(row.get("type") or ""),
            "mod": str(row.get("mod") or ""),
            "w": float(row.get("w", 0.0)),
            "signal": str(row.get("signal") or dflt.get("signal") or "valence"),
            "channel": str(row.get("channel") or dflt.get("channel") or "tonic"),
            "gate": dict(row.get("gate") or {}),
            "clamp": (dflt.get("clamp") if row.get("clamp") is None
                      else float(row.get("clamp"))),
            "why": str(row.get("why") or ""),
        })
    return [r for r in rows if r["type"] and r["mod"]]


def rules_index(config) -> dict:
    """{event_type: [rule…]}，同 (type, mod) 重复时报冲突（kg.add_edge 会 max 合并）。"""
    idx, seen, conflicts = {}, set(), []
    for r in seed_rules(config):
        key = (r["type"], r["mod"])
        if key in seen:
            conflicts.append({"event_type": r["type"], "mod": r["mod"],
                              "why": "同一事件类型对同一调制器有两条规则"
                                     "（add_edge 会按 max(weight) 合并，第二条无效）"})
            continue
        seen.add(key)
        idx.setdefault(r["type"], []).append(r)
    return {"by_type": idx, "conflicts": conflicts}


# ── 1) bootstrap ───────────────────────────────────────────

def _reverse_mirror(st) -> dict:
    return {nid: name for name, nid in (st.mirror_modulator or {}).items()}


def ensure_event_types(kg, st, config) -> dict:
    """幂等种入事件类型节点与 `影响` 边。**种完后图是权威**。

    端点缺失（调制器镜像还没建）时**整类跳过、连节点都不建**——只建节点不建边
    会造出"半接线"状态：`graph_rules` 看见节点就认为图已接管，返回空规则，
    于是出厂表兜底也不走了，调制静默归零（`test_reward_disposition` 抓到过一次）。
    已经存在的节点不动它自己的边（那是"故意消音"，见 `rules_for` 的注释）。
    """
    blk = _blk(config)
    if not blk.get("events", True) or kg is None:
        return {"skipped": "events 关闭或无图谱", "nodes": 0, "edges": 0,
                "edges_skipped": 0, "conflicts": [], "types": 0, "types_unwired": 0}
    from graph_model import Edge, Node

    mirror = st.mirror_modulator
    rx = _reverse_mirror(st)
    ri = rules_index(config)
    created_nodes, created_edges, skipped, unwired = 0, 0, [], 0
    known = set(mirror.values())
    for etype, rules in sorted(ri["by_type"].items()):
        nid = f"{EVENT_PREFIX}{etype}"
        node = kg.get_node(nid)
        targets = []
        for r in rules:
            dst = mirror.get(r["mod"]) or (r["mod"] if r["mod"] in known else None)
            if dst is None or kg.get_node(dst) is None:
                skipped.append({"edge": f"{etype}→{r['mod']}", "why": "调制器镜像不存在"
                                "（先跑 internal_state.sync_graph）"})
                continue
            targets.append((r, dst))
        if not targets:
            if node is None:
                unwired += 1            # 一类都接不上 → 留未接线状态，走兜底表
            continue
        if node is None:
            ea = {"type": "modulation_event_type", "canon": "modulation_events",
                  "signal": blk.get("event_default", {}).get("signal", "valence"),
                  "channel": blk.get("event_default", {}).get("channel", "tonic"),
                  "clamp": blk.get("event_default", {}).get("clamp"),
                  "edges": {},
                  "description": f"调制事件类型 {etype}：扇出与门控见 edges 属性与出边权重"}
            kg.add_node(Node(id=nid, weight=0.4, label="infrastructure",
                             graph_space="cognitive", extra_attrs=ea))
            created_nodes += 1
            node = kg.get_node(nid)
        ea = node.extra_attrs if node.extra_attrs is not None else {}
        meta = ea.get("edges") if isinstance(ea.get("edges"), dict) else {}
        for r, dst in targets:
            # 每条边的语义细节挂在事件类型节点上（Edge 无 extra_attrs，实测）
            want = {"signal": r["signal"], "channel": r["channel"],
                    "gate": r["gate"], "clamp": r["clamp"], "why": r["why"]}
            if meta.get(rx[dst]) != want:
                meta[rx[dst]] = want
                ea["edges"] = meta
                node.extra_attrs = ea
            if kg.get_edge(nid, dst, EVENT_RELATION) is None:
                kg.add_edge(Edge(src=nid, dst=dst, relation=EVENT_RELATION,
                                 weight=float(r["w"]),
                                 relation_category=EDGE_RELATION_CATEGORY))
                created_edges += 1
    out = {"nodes": created_nodes, "edges": created_edges,
           "edges_skipped": len(skipped), "skipped": skipped[:8],
           "types": len(ri["by_type"]), "types_unwired": unwired,
           "conflicts": ri["conflicts"]}
    if created_nodes or created_edges:
        logger.info(f"[Modulation] 事件表种入：事件类型 +{created_nodes}（共 {out['types']} "
                    f"类），影响边 +{created_edges}，跳过 {len(skipped)}"
                    + (f"，未接线 {unwired} 类" if unwired else ""))
    if ri["conflicts"]:
        logger.warning(f"[Modulation] 事件表冲突 ×{len(ri['conflicts'])}："
                       f"同一 (事件类型, 调制器) 写了两条规则")
    return out


# ── 2) 读取扇出（图优先，出厂表兜底）─────────────────────

def graph_rules(kg, st, event_type: str) -> list:
    """图上的规则；事件类型节点不存在时返回 None（= 该图尚未接入事件表）。"""
    nid = f"{EVENT_PREFIX}{event_type}"
    node = kg.get_node(nid) if kg is not None else None
    if node is None:
        return None
    ea = node.extra_attrs or {}
    meta = ea.get("edges") if isinstance(ea.get("edges"), dict) else {}
    rx = _reverse_mirror(st)
    out = []
    for e in kg.get_out_edges(nid):
        if e.relation != EVENT_RELATION:
            continue
        dst = kg.get_node(e.dst)
        if dst is None or (dst.extra_attrs or {}).get("type") != "modulator":
            continue                        # 不是调制器镜像的边不参与扇出
        name = rx.get(e.dst)
        if name is None:
            continue
        m = meta.get(name) or {}
        out.append({"mod": name, "w": float(e.weight),
                    "signal": str(m.get("signal") or ea.get("signal") or "valence"),
                    "channel": str(m.get("channel") or ea.get("channel") or "tonic"),
                    "gate": dict(m.get("gate") or {}),
                    "clamp": m.get("clamp", ea.get("clamp")),
                    "why": str(m.get("why") or ""), "from": "graph"})
    return out


def config_rules(st, config, event_type: str) -> list:
    """出厂表兜底（无图谱时用），并按调制器名过滤掉状态里没有的调制器。"""
    rows = rules_index(config)["by_type"].get(event_type) or []
    have = set(st.modulator_names())
    return [dict(r, mod=r["mod"], from_="config") for r in rows if r["mod"] in have]


# ── 3) 门控与 `交互` 增益 ─────────────────────────────────

def gate_ok(ev: ModulationEvent, gate: dict):
    """通用门控：`<字段>_min` / `<字段>_max` / `valence_sign`。

    字段名与 ModulationEvent 同域，所以加一个新门控不需要改代码——这正是把
    `if ev["source"]=="self"` 换成 `gate:{"goal_relevance_min":0.5}` 的意思。
    """
    for key, want in (gate or {}).items():
        if key == "valence_sign":
            v = float(ev.valence or 0.0)
            if want == "pos" and not v > 0:
                return False, key
            if want == "neg" and not v < 0:
                return False, key
            continue
        if key.endswith("_min") or key.endswith("_max"):
            field_name, is_min = key[:-4], key.endswith("_min")
            val = ev.value_of(field_name)
            if is_min and val < float(want):
                return False, key
            if not is_min and val > float(want):
                return False, key
    return True, ""


def interaction_gains(kg, st) -> dict:
    """`交互` 边 → 每个调制器的事件写入增益（纯计算，不写任何东西）。

    `交互` 是对称耦合，所以任一方向的边都计入被调调制器的增益。
    源调制器处于基线（dev=0）时 gain 恒为 1 —— 出厂拓扑下无人被改变。
    """
    gains = {}
    if kg is None:
        return gains
    rx = _reverse_mirror(st)
    for name in st.modulator_names():
        g, terms = 1.0, []
        nid = (st.mirror_modulator or {}).get(name)
        for e in (kg.get_out_edges(nid) + kg.get_in_edges(nid) if nid else []):
            if e.relation != "交互":
                continue
            other = e.dst if e.src == nid else e.src
            src_name = rx.get(other)
            if src_name is None or src_name == name:
                continue
            dev = float(st.modulator_dev(src_name) or 0.0)
            f = 1.0 + float(e.weight) * dev
            f = max(GAIN_EDGE_MIN, min(GAIN_EDGE_MAX, f))
            g *= f
            terms.append({"with": src_name, "w": round(float(e.weight), 3),
                          "dev": round(dev, 3), "factor": round(f, 3)})
        gains[name] = {"gain": round(max(GAIN_TOTAL_MIN, min(GAIN_TOTAL_MAX, g)), 4),
                       "terms": terms}
    return gains


# ── 4) 应用器 ─────────────────────────────────────────────

class ModulatorEngine:
    """事件 → 调制器的通用应用器（§7 的 `ModulatorEngine.apply_event`）。

    它**不**持有状态：数值住在 internal_state，拓扑住在图，出厂表在 config。
    """

    def __init__(self, kg=None, st=None, config=None, ensure=True):
        self.kg = kg
        self.st = st
        self.config = config or {}
        self._warned_no_graph = False
        if ensure:
            self.ensure()

    def attach(self, kg=None, st=None):
        if kg is not None:
            self.kg = kg
        if st is not None:
            self.st = st
        return self

    def ensure(self) -> dict:
        if self.kg is None or self.st is None:
            return {"skipped": "no_graph", "nodes": 0, "edges": 0}
        return ensure_event_types(self.kg, self.st, self.config)

    # ── 核心 ──

    def rules_for(self, event_type: str) -> dict:
        """图优先；**未接线**（该类节点不存在）时才退回出厂表，并标明来源。

        三种状态语义不同，别混：
          节点不存在      → 图没接管这类事件 → 兜底表（`source="config"`）
          节点存在、无边  → 图接管后被清空 = **故意消音**（`no_rules`，不兜底）
          节点存在、有边  → 扇出完全由边决定（改效力改图，不改 config）
        """
        if self.kg is not None and self.st is not None:
            gr = graph_rules(self.kg, self.st, event_type)
            if gr is not None:
                return {"rules": gr, "source": "graph"}
        return {"rules": config_rules(self.st, self.config, event_type),
                "source": "config"}

    def apply_event(self, ev: ModulationEvent, modulation=None) -> dict:
        """按图上的边扇出。返回可读的 fan-out 明细（也是 trace 的数据源）。"""
        try:
            import experiment_mode as _xm
            if _xm.shield("modulation_events"):
                return {"ok": True, "event_type": ev.event_type,
                        "applied": [], "skipped": [], "reason": "shielded",
                        "interaction": {}}
        except Exception:
            pass
        if self.st is None:
            return {"ok": False, "reason": "no_internal_state", "applied": [],
                    "event_type": ev.event_type}
        packed = self.rules_for(ev.event_type)
        rules, src = packed["rules"], packed["source"]
        if src == "config" and self.kg is None and not self._warned_no_graph:
            self._warned_no_graph = True
            logger.debug("[Modulation] 无图谱：事件扇出退回出厂 event_rules"
                         "（与 MODULATOR_SPEC 兜底同一策略）")
        if not rules:
            return {"ok": True, "event_type": ev.event_type, "source": src,
                    "applied": [], "skipped": [], "reason": "no_rules",
                    "interaction": {}}
        gains = interaction_gains(self.kg, self.st) if src == "graph" else {}
        applied, gated, parts = [], [], []
        for r in rules:
            # `circadian_driven` 的消费者（§5：字段不许只声明不使用；R2 P10）。
            # 语义：**这一类调制器只吃 circadian 语境**。褪黑素样的水位由时段事实
            # 决定，奖赏/情绪/意外都不该动它（"吃了甜点→褪黑素上升"是荒谬的）。
            # 出厂只有 melatonin 打了这个标记；跳过要记一笔（看得见），不是静默丢。
            if str(ev.source or "") != "circadian" and self.st.modulator_field(
                    r["mod"], "circadian_driven", False):
                gated.append({"mod": r["mod"],
                              "gate": "circadian_driven：只接受 circadian 语境"})
                continue
            ok, why = gate_ok(ev, r.get("gate") or {})
            if not ok:
                gated.append({"mod": r["mod"], "gate": why})
                continue
            val = ev.value_of(r["signal"])
            if not val:
                gated.append({"mod": r["mod"], "gate": f"signal={r['signal']}=0"})
                continue
            gain = (gains.get(r["mod"]) or {}).get("gain", 1.0)
            delta = float(r["w"]) * float(val) * float(gain)
            clamp = r.get("clamp")
            if clamp:
                c = abs(float(clamp))
                delta = max(-c, min(c, delta))
            if not math.isfinite(delta) or abs(delta) < 1e-9:
                gated.append({"mod": r["mod"], "gate": "delta≈0"})
                continue
            reason = f"{ev.event_type}:{ev.ref[:30]}" if ev.ref else ev.event_type
            channel = str(r.get("channel") or "tonic")
            if channel == "phasic":
                res = self.st.pulse(r["mod"], delta, cycle_id=ev.cycle_id,
                                    reason=reason, source=ev.source)
            else:
                res = self.st.apply_delta("modulator", r["mod"], delta, reason=reason,
                                          cycle_id=ev.cycle_id, source=ev.source)
            applied.append({"mod": r["mod"], "requested": round(delta, 4),
                            "weight": round(float(r["w"]), 4),
                            "signal": r["signal"], "value": round(float(val), 4),
                            "gain": round(float(gain), 3), "channel": channel,
                            "ok": bool(res.get("ok")),
                            "actual": round(float(res.get("delta") or 0.0), 4)})
            parts.append(f"{r['mod']}:{delta:+.3f}({channel[:1]})")
        logger.info(f"[MODULATION] event={ev.event_type} source={ev.source} "
                    f"valence={ev.valence:+.2f} 扇出 {' '.join(parts) or '—'}"
                    + (f" 门控跳过 {len(gated)}" if gated else "")
                    + ("" if src == "graph" else " [出厂表]"))
        return {"ok": True, "event_type": ev.event_type, "source": src,
                "applied": applied, "gated": gated,
                "interaction": {k: v["gain"] for k, v in gains.items()
                                if abs(v["gain"] - 1.0) > 1e-6}}

    def emit(self, event_type: str, **fields) -> dict:
        """构造 + 应用（测试与生产走同一个入口，§26）。"""
        ev = ModulationEvent(event_type=event_type, **fields)
        out = self.apply_event(ev)
        out["event"] = ev.as_dict()
        return out

    # ── 观测面（P12 的调试 API 复用它）──

    def explain(self) -> dict:
        """每个事件类型的出边、权重、门控、当前增益——人读，给调试台用。"""
        types = {}
        blk = _blk(self.config)
        names = set(rules_index(self.config)["by_type"])
        if self.kg is not None:
            names |= {n[len(EVENT_PREFIX):] for n in self.kg.nodes
                      if n.startswith(EVENT_PREFIX)}
        for t in sorted(names):
            got = self.rules_for(t)
            types[t] = {"source": got["source"],
                        "fanout": [{"mod": r["mod"], "w": round(float(r["w"]), 4),
                                    "signal": r["signal"], "channel": r["channel"],
                                    "gate": r["gate"]} for r in got["rules"]]}
        return {"event_types": types,
                "interaction": ({k: v for k, v in interaction_gains(
                    self.kg, self.st).items()
                    if abs(v["gain"] - 1.0) > 1e-6} if self.st else {}),
                "enabled": bool(blk.get("events", True))}
