# embodied_mapper.py — 具身状态 → 图谱激活（2026-09-21 具身图谱化）
# ============================================================================
# 职责边界（五层分权）：本模块是"感知事实 → 认知状态"的唯一桥。
#   输入：归一化 percept（世界读数）+ 事件流（时间性事实）
#   输出：图上激活（物种/生存需求/机会节点，带 source 与强度推导依据）
#   它不做：需求 delta（走 internal_state provider 单源）、行动裁决
#   （候选/评分/竞争在 capability_graph/autonomy）、任何 if→action 映射。
#
# 激活强度是**连续推导**（距离/血量/物种威胁权重来自图上的 威胁/造成
# 因果边），不是 if 名单；历史经验可经 causal 传入做放大（默认关）。
# MC 昼夜与现实时段隔离：游戏夜晚写 "MC夜"（source=minecraft），
# 经 属于->夜间 汇入共享昼夜抽象——绝不写现实时段节点。
# ============================================================================

import logging
import time

from graph_model import Node, Edge

logger = logging.getLogger(__name__)


def _cfg(config):
    return (config or {}).get("embodied_mapper") or {}


def _clamp01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))


def _hostile_names(config):
    return {str(x).lower() for x in
            (config or {}).get("hostile_entities") or []}


class EmbodiedStateMapper:
    def __init__(self, kg, engine=None, config=None, causal=None):
        self.kg = kg
        self.engine = engine
        self.config = config or {}
        self.causal = causal          # 可选：CausalLearner（历史放大，默认弱）

    # ── 物种威胁权重：读图上的 威胁/造成 因果知识，缺省保守 ──

    def _threat_weight(self, species_id):
        try:
            with self.kg._lock:
                node = self.kg.get_node(species_id)
                if node is None:
                    return 0.8
                w = 0.0
                for e in self.kg.get_out_edges(species_id):
                    if e.relation in ("造成", "敌对", "威胁"):
                        w = max(w, float(e.weight or 0))
                return max(0.6, min(1.2, w if w > 0 else 0.8))
        except Exception:
            return 0.8

    # ── 主入口：percept(+events) → 图激活。返回 survival_pressure ──

    def update(self, percept: dict, events: list = None,
               now: float = None) -> float:
        if self.kg is None or not percept or not percept.get("connected",
                                                             True):
            return 0.0
        mc = _cfg(self.config)
        now = time.time() if now is None else float(now)
        surv_node = str(mc.get("survival_node") or "生存需求")
        radius = float(mc.get("threat_radius", 16.0))
        gains = mc.get("gain") or {}
        pr = mc.get("pressure") or {}

        try:
            hp = float(percept.get("health"))
        except (TypeError, ValueError):
            hp = None
        try:
            food = float(percept.get("food"))
        except (TypeError, ValueError):
            food = None

        # 1) 敌对在视：物种节点激活 + 威胁压力（距离×物种威胁×(1+血量修正)）
        hostile_p = 0.0
        lit = []
        hostiles = _hostile_names(self.config)
        ents = percept.get("entities") or []
        with self.kg._lock:
            for e in ents:
                name = str(e.get("name") or "").lower()
                if not name or name not in hostiles:
                    continue
                try:
                    d = float(e.get("dist", radius))
                except (TypeError, ValueError):
                    d = radius
                prox = max(0.0, 1.0 - d / radius)
                tw = self._threat_weight(name)
                hp_mod = 1.0 + (0.6 if hp is not None and
                                hp <= float(pr.get("health_worried", 12))
                                else 0.0)
                hostile_p = max(hostile_p,
                                prox * tw * hp_mod *
                                float(gains.get("hostile", 1.6)))
                node = self.kg.get_node(name)
                if node is not None:
                    need = max(0.5, min(5.0, prox * 2.5))
                    if float(node.activation or 0.0) < need:
                        node.activation = need
                        node.touch()
                        lit.append(name)
            # 2) 血量/饥饿/熔岩/溺水压力（连续曲线，不是阶梯裁判）
            health_p = food_p = 0.0
            if hp is not None:
                crit = float(pr.get("health_critical", 6))
                worried = float(pr.get("health_worried", 12))
                health_p = _clamp01((worried - hp) / max(worried, 1e-6)) \
                    * float(gains.get("health", 2.0))
            if food is not None:
                fw = float(pr.get("food_worried", 8))
                fc = float(pr.get("food_critical", 4))
                food_p = _clamp01((fw - food) / max(fw - fc, 1e-6)) \
                    * float(gains.get("food", 1.2))
            lava_p = 0.0
            for b in (percept.get("blocks") or []):
                if str(b.get("name") or "").lower() == "lava":
                    lava_p = float(gains.get("lava", 2.2))
                    break
            survival = max(hostile_p, health_p, lava_p, food_p * 0.6)
            # 3) 资源在场：可采物种轻激活（机会由物种节点扩散承载）
            for b in (percept.get("blocks") or []):
                bn = str(b.get("name") or "").lower()
                node = self.kg.get_node(bn) if bn else None
                if node is None:
                    continue
                gatherable = any(
                    x.dst == "可采资源" and x.relation == "属于"
                    for x in self.kg.get_out_edges(bn))
                if gatherable:
                    if float(node.activation or 0) < 0.4:
                        node.activation = 0.4
                        node.touch()
                        lit.append(bn)
                    # 类节点聚合：有可采资源在场（"可采资源"激活→MINE 诱发）
                    cls = self.kg.get_node("可采资源")
                    if cls is not None and float(cls.activation or 0) < 0.4:
                        cls.activation = 0.4
                        cls.touch()
                        lit.append("可采资源")
            # 4) 生存需求节点：写入压力（max 合并，不压制更高来源）
            sn = self.kg.get_node(surv_node)
            if sn is None:
                sn = Node(id=surv_node, weight=0.5,
                          label="declarative-semantic", graph_space="semantic",
                          extra_attrs={"type": "mc_state",
                                       "source": "embodied_mapper"})
                self.kg.add_node(sn)
            if survival > float(sn.activation or 0.0):
                sn.activation = min(5.0, survival)
                sn.touch()
            ea = dict(sn.extra_attrs or {})
            ea.update({"pressure": round(survival, 3),
                       "components": {"hostile": round(hostile_p, 3),
                                      "health": round(health_p, 3),
                                      "food": round(food_p, 3),
                                      "lava": lava_p},
                       "hp": hp, "food_level": food,
                       "updated": int(now)})
            sn.extra_attrs = ea
            lit.append(surv_node)
        # 5) 时间性事件（update_events 处理）+ 统一注入激活前沿
        self.update_events(events or [], now=now)
        ids = list(dict.fromkeys(lit))
        if self.engine is not None and ids:
            try:
                self.engine.mark_active(ids)
                self.engine.register_activation_source(ids, "perception")
            except Exception:
                pass
        return survival

    # ── 事件→图激活（时间性事实；不做需求 delta、不产候选）──────

    def update_events(self, events: list, now: float = None):
        if self.kg is None or not events:
            return
        now = time.time() if now is None else float(now)
        mc = _cfg(self.config)
        gains = mc.get("gain") or {}
        lit = []
        with self.kg._lock:
            for ev in events:
                et = str(ev.get("type") or "")
                if et == "resource_detected":
                    sp = str(ev.get("resource") or "").lower()
                    pulse = float(gains.get("resource", 0.8))
                    for pid in (sp, "可采资源"):
                        node = self.kg.get_node(pid)
                        if node is not None:
                            node.activation = min(5.0, float(
                                node.activation or 0.0) + pulse)
                            node.touch()
                            lit.append(pid)
                elif et == "item_on_ground":
                    node = self.kg.get_node("地面物品")
                    if node is None:
                        node = Node(id="地面物品", weight=0.4,
                                    label="declarative-semantic",
                                    graph_space="semantic",
                                    extra_attrs={"type": "mc_state",
                                                 "source": "embodied_mapper"})
                        self.kg.add_node(node)
                    node.activation = min(5.0, float(
                        node.activation or 0.0) + 0.8)
                    node.touch()
                    lit.append("地面物品")
                elif et == "structure_detected":
                    node = self.kg.get_node("建筑结构")
                    if node is None:
                        node = Node(id="建筑结构", weight=0.4,
                                    label="declarative-semantic",
                                    graph_space="semantic",
                                    extra_attrs={
                                        "type": "mc_state",
                                        "source": "embodied_mapper"})
                        self.kg.add_node(node)
                    node.activation = min(5.0, float(
                        node.activation or 0.0) + 0.8)
                    node.touch()
                    lit.append("建筑结构")
                elif et == "time_changed":
                    # MC 昼夜：写 "MC夜"/"MC白天"（source=minecraft），
                    # 属于->夜间/白天 汇入共享抽象；绝不写现实时段节点
                    ph = str(ev.get("time") or ev.get("phase") or "")
                    is_night = ("夜" in ph) or str(ph).lower() in (
                        "night", "midnight", "dusk")
                    anchor = "MC夜" if is_night else "MC白天"
                    abs_node = "夜间" if is_night else "白天"
                    node = self.kg.get_node(anchor)
                    if node is None:
                        node = Node(id=anchor, weight=0.4,
                                    label="declarative-semantic",
                                    graph_space="semantic",
                                    extra_attrs={
                                        "type": "day_phase",
                                        "source": "minecraft"})
                        self.kg.add_node(node)
                    node.activation = min(5.0, float(
                        node.activation or 0.0) + 0.6)
                    node.touch()
                    lit.append(anchor)
                    tgt = self.kg.get_node(abs_node)
                    if tgt is not None and self.kg.get_edge(
                            anchor, abs_node, "属于") is None:
                        self.kg.add_edge(Edge(src=anchor, dst=abs_node,
                                              relation="属于", weight=0.7))
                    if is_night:
                        sn = self.kg.get_node(
                            str(mc.get("survival_node") or "生存需求"))
                        if sn is not None:
                            sn.activation = min(5.0, float(
                                sn.activation or 0.0)
                                + float(gains.get("night", 0.5)))
                            sn.touch()
                            # 收尾修复 2026-09-22：直写点必须入 lit，
                            # 否则不进 mark_active/前沿（不变量违例）
                            lit.append(sn.id)
        if self.engine is not None and lit:
            try:
                ids = list(dict.fromkeys(lit))
                self.engine.mark_active(ids)
                self.engine.register_activation_source(ids, "perception")
            except Exception:
                pass
