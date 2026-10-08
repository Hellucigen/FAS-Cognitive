"""fas.contracts — Cognitive Core 与 Companion Layer 之间唯一的跨界数据结构。

规则（docs/ARCHITECTURE_PLAN.md §2）：
  - 跨 core↔companion 边界只允许这里定义的数据结构（或其 JSON 兼容 dict 形式）。
  - 字段命名对齐现有代码事实（dialogue_decide 返回、compile_for_language 产物、
    engine.active_snapshot 输出），不另造词汇。
  - 本模块必须保持纯 stdlib：任何一侧的实现都不从这里获得行为，只获得形状。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any


# ── Companion → Cognitive ─────────────────────────────────────────

#: CognitiveEvent.kind 取值（对齐 app.py 回合内实际存在的外部输入形态）
EVT_USER_MESSAGE = "user_message"      # 用户话语（web/Minecraft/Charon 通道）
EVT_PERCEPTION = "perception"          # 感知注入（ear/eye/vision/minecraft 观测）
EVT_ACTION_RESULT = "action_result"    # 动作执行回执（ActionManager 结算）
EVT_ENVIRONMENT = "environment"        # 环境/世界状态（world_prior、Charon summary）
EVT_TICK = "tick"                      # 时间推进（后台循环节拍）


@dataclass
class CognitiveEvent:
    """进入认知层的外部事件。parsed 结构放在 payload（nodes/edges 等），
    由陪伴侧的解析步骤填充——认知核心自己不调用语言后端做解析。"""
    kind: str
    source: str = "unknown"            # web / minecraft / ear / eye / charon / internal …
    text: str = ""
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "CognitiveEvent":
        return CognitiveEvent(kind=str(d.get("kind") or EVT_USER_MESSAGE),
                              source=str(d.get("source") or "unknown"),
                              text=str(d.get("text") or ""),
                              payload=dict(d.get("payload") or {}),
                              ts=float(d.get("ts") or time.time()))


@dataclass
class MemoryEvent:
    """记录进图谱的最小关系事实。陪伴侧记录记忆的唯一合法通道
    （经 CognitiveCore.record_memory，不再直写 kg）。"""
    subject: str
    predicate: str                     # 关系词，进 graph_schema 归一漏斗
    object: str
    provenance: str = "companion"      # 来源留痕（谁说的/哪个通道）
    weight: float = 0.5
    extra: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


# ── Cognitive → Companion ─────────────────────────────────────────

@dataclass
class ActivationReport:
    """perceive() 的结果：本轮激活与扩散后的可见状态。"""
    activated: list = field(default_factory=list)   # 注入激活的节点 id
    topk: list = field(default_factory=list)        # engine.get_topk 原样
    diffuse_steps: int = 0


@dataclass
class TurnDecision:
    """dialogue_decision.dialogue_decide 返回字典的包装（零 LLM 决策头）。
    raw 保留全部原字段，properties 只是便捷读取——不改变语义。"""
    raw: dict = field(default_factory=dict)

    @property
    def decision(self):
        return self.raw.get("decision")

    @property
    def should_respond(self) -> bool:
        return self.decision in ("respond", "minimal", "explore_ask", "explore_search")

    @property
    def desire(self):
        return self.raw.get("desire")

    @property
    def constraints(self):
        return self.raw.get("constraints") or []

    @property
    def factors(self):
        return self.raw.get("factors") or {}


@dataclass
class CognitiveStateSnapshot:
    """认知状态的可导出切片（内省展示/语言上下文/日志用，全部只读拷贝）。"""
    topk_nodes: list = field(default_factory=list)
    topk_edges: list = field(default_factory=list)
    attention: dict = field(default_factory=dict)
    mood: Any = None
    mode: Any = None
    drives: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


@dataclass
class LanguageRequest:
    """Core→Companion 的语言实现请求。
    payload = cognitive_context.compile_for_language 的编译产物（键零改动，
    L1/L2 向后兼容既有 nlp_processor 消费面）；user_text/topk/evidence 是
    现有 app 调用点需要的随行参数位。"""
    path: str = "L2"                   # "L1"（短确认）| "L2"（全量回答）
    user_text: str = ""
    payload: dict = field(default_factory=dict)
    topk_nodes: list = field(default_factory=list)
    topk_edges: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    channel: str = "web"
    decision: dict = field(default_factory=dict)


@dataclass
class ActionProposal:
    """认知层提出的行动意向（交给 ActionManager/GameBackend 执行）。
    字段对齐 action_system.ActionManager.propose 的入参面。"""
    action: str
    params: dict = field(default_factory=dict)
    confidence: float = 0.0
    source: str = "core"               # autonomy | dialogue | reflex …


@dataclass
class Utterance:
    """陪伴出口的话语（回复/主动消息/提问）。"""
    text: str
    origin: str = "reactive"           # reactive | proactive | question
    refs: dict = field(default_factory=dict)   # 关联事件/决策（留痕，可追溯）
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)
