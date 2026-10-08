"""fas.core.facade — CognitiveCore：认知层对陪伴层的唯一门面。

原则（docs/ARCHITECTURE_PLAN.md §3.3）：
  - 全部方法是对既有函数的**委托**（diffusion_engine / episodic_buffer /
    dialogue_decision / cognitive_context / graph_model），不复制、不修改任何算法。
    每个委托点都标注现有实现的 文件:行号 证据，对拍时可逐位核对。
  - 陪伴层不得再直接触碰 kg._lock / node.activation / engine.mark_active；
    状态变化只走 perceive / record_memory / propose_action / tick。
  - 认知内部的语言触点通过 attach_language_backend 注入的
    CognitiveLanguageAccess（llm/chat_llm/ask 三成员，见 fas/protocols.py），
    替换语言方案不需要动本文件。
"""
from __future__ import annotations

import logging
from typing import Any

from fas.contracts import (ActionProposal, ActivationReport, CognitiveEvent,
                           CognitiveStateSnapshot, LanguageRequest,
                           MemoryEvent, TurnDecision)

logger = logging.getLogger("fas.core.facade")


class CognitiveCore:
    """组装后的认知核心。构造参数全部是既有对象，由 fas.core.assembly 或
    app.py 装配区注入；本类不构造认知组件本身。"""

    def __init__(self, *, kg, engine, buffer=None, config: dict = None,
                 disposition_store=None, action_space=None, action_manager=None,
                 internal_state=None, persona=None, drive_evaluator=None,
                 cc=None, emb_mgr=None, language=None):
        self.kg = kg
        self.engine = engine
        self.buffer = buffer
        self.config = dict(config or {})
        self.disposition_store = disposition_store
        self.action_space = action_space
        self.action_manager = action_manager
        self.internal_state = internal_state
        self.persona = persona
        self.drive_evaluator = drive_evaluator
        self.cc = cc                        # ContinuousCognition（可为 None：无后台循环）
        self.emb_mgr = emb_mgr              # 语义召回（可为 None）
        self._language = language           # CognitiveLanguageAccess

    # ── 语言后端槽（验收 B：换 LLM 不动认知） ────────────────

    def attach_language_backend(self, access) -> None:
        """把语言访问面注入认知内部的全部触点。
        触点与属性名以现状代码为准：
          diffusion_engine.py:181  self._nlp_ref（generate_thought 用）
          continuous_cognition.py:93 self.nlp（_express / _free_think 用）
          reflection_engine.py:39  self.nlp（_llm_reflection 用）
        """
        self._language = access
        if self.engine is not None:
            self.engine._nlp_ref = access
        if self.cc is not None:
            self.cc.nlp = access
            refl = getattr(self.cc, "reflection", None)
            if refl is not None:
                refl.nlp = access

    # ── Companion → Cognitive ───────────────────────────────

    def perceive(self, event: CognitiveEvent) -> ActivationReport:
        """把一个外部事件注入认知状态。payload 约定（与 app.py 主管道同形）：
          nodes       list[dict{id, initial_activation?}] 或 list[str]  — 解析产物
          edges       list[dict{src_id, dst_id, relation}]             — 待建/待点亮边
          similarity_map dict                                          — 语义相似映射
          source_type str（默认 external_input）
          diffuse_rounds int（默认 1；0 = 只激活不扩散）
        委托序列 = app.py:3750(activate_from_inputs) → 3767/3788(diffuse_round)。
        情景缓冲：episodic_buffer.add_experience（app 主管道同款入参）。
        """
        payload = event.payload or {}
        raw_nodes = payload.get("nodes") or []
        node_ids = [n if isinstance(n, str) else n.get("id") for n in raw_nodes]
        node_ids = [x for x in node_ids if x]
        edge_specs = payload.get("edges") or []

        activated: list = []
        steps = 0
        if node_ids and self.engine is not None:
            self.engine.activate_from_inputs(
                node_ids, edge_specs,
                similarity_map=payload.get("similarity_map"),
                source_type=payload.get("source_type", "external_input"))
            activated = list(node_ids)
            rounds = payload.get("diffuse_rounds", 1)
            for _ in range(max(0, int(rounds))):
                self.engine.diffuse_round()      # diffusion_engine.py:1314
                steps += 1
        elif event.kind == "environment" and edge_specs and self.engine is not None:
            # 纯世界状态更新：只建边点亮，无新输入锚点
            self.engine.activate_from_inputs([], edge_specs,
                                             source_type=payload.get("source_type", "world_state"))
        if self.buffer is not None and event.text:
            self.buffer.add_experience(          # episodic_buffer.py:73
                raw_text=event.text, nodes=raw_nodes, edges=edge_specs,
                assertion_type=payload.get("assertion_type", "episodic"))
        topk = self.engine.get_topk(payload.get("topk", 20)) if self.engine else []
        return ActivationReport(activated=activated, topk=list(topk),
                                diffuse_steps=steps)

    def record_memory(self, m: MemoryEvent) -> bool:
        """记忆写入的唯一合法入口（陪伴侧不再直写图）。
        走 graph_model 公共 API：upsert_node(430)/add_edge(487，关系归一漏斗)。
        写入的是最小三元组事实；更丰富的沉淀（LLM 抽取→组帧）属陪伴侧临时通道，
        未来由 FAS RelationalMemory 研究项在本方法内替换（台账 §4 MemoryWriter）。
        """
        from graph_model import Node, Edge
        subj = self.kg.nodes.get(m.subject)
        if subj is None:
            ok = self.kg.upsert_node(Node(
                id=m.subject, label="declarative-semantic", graph_space="semantic",
                weight=m.weight,
                extra_attrs={"source": f"record_memory:{m.provenance}"}))
        obj = self.kg.nodes.get(m.object)
        if obj is None:
            self.kg.upsert_node(Node(
                id=m.object, label="declarative-semantic", graph_space="semantic",
                weight=0.5, extra_attrs={"source": f"record_memory:{m.provenance}"}))
        return self.kg.add_edge(Edge(src=m.subject, dst=m.object,
                                     relation=m.predicate, weight=m.weight))

    def propose_action(self, p: ActionProposal) -> Any:
        """行动意向交给 ActionManager（action_system.py:239 propose）。
        陪伴/游戏后端执行时回传 CognitiveEvent(kind=action_result)。"""
        if self.action_manager is None:
            return None
        return self.action_manager.propose(
            p.action, source=p.source,
            **({"params": p.params} if p.params else {}))

    # ── Cognitive → Companion ───────────────────────────────

    def snapshot(self) -> CognitiveStateSnapshot:
        """只读认知切片（engine.active_snapshot 429 / get_topk 526）。"""
        nodes_edges = self.engine.active_snapshot() if self.engine else ([], [])
        try:
            ns, es = nodes_edges
        except Exception:
            ns, es = [], []
        snap = CognitiveStateSnapshot(topk_nodes=list(ns), topk_edges=list(es))
        if self.drive_evaluator is not None:
            try:
                snap.drives = dict(self.drive_evaluator.get_dominant_drive())
            except Exception:
                pass
        if self.persona is not None:
            try:
                snap.mood = self.persona.current_mood()
            except Exception:
                pass
        if self.cc is not None and getattr(self.cc, "mode", None):
            snap.mode = self.cc.mode
        return snap

    def decide(self, parsed: dict, text: str, *,
               tendencies: list = None, curiosity_active: bool = False,
               last_outcome_negative: bool = False, last_expr_gap_s: float = 9999.0,
               has_action_result: bool = False, exploration: dict = None,
               hormone: dict = None, modulation=None,
               action_tendencies: dict = None) -> TurnDecision:
        """行为竞争决策头，委托 dialogue_decision.dialogue_decide（233 行，零 LLM）。
        参数默认值即"无副作用缺席"——不改变任何打分路径。"""
        from dialogue_decision import dialogue_decide
        if tendencies is None and self.disposition_store is not None:
            try:
                from disposition_store import context_for_dialogue_act
                tendencies = self.disposition_store.tendencies_for(
                    [context_for_dialogue_act(parsed.get("dialogue_act",
                                                         "information_statement"))])
            except Exception:
                tendencies = []
        raw = dialogue_decide(
            parsed, text, self.kg, self.engine,
            tendencies or [], curiosity_active, last_outcome_negative,
            last_expr_gap_s, has_action_result,
            exploration=exploration, hormone=hormone, modulation=modulation,
            action_tendencies=action_tendencies, action_space=self.action_space)
        return TurnDecision(raw=raw)

    def compile_language_request(self, *, decision: TurnDecision,
                                 parsed: dict, text: str, path: str = "L2",
                                 channel: str = "web", evidence: dict = None,
                                 topk_nodes: list = None, topk_edges: list = None,
                                 **state) -> LanguageRequest:
        """认知状态 → 语言实现请求。
        委托 cognitive_context.build_cognitive_context(134) + compile_for_language(345)，
        与 app.py:4542/4665/4754 的现调用同形（payload 键零改动）。
        """
        from cognitive_context import build_cognitive_context, compile_for_language
        ctx = build_cognitive_context(
            text=text, channel=channel, parsed=parsed,
            decision=decision.raw, evidence=evidence,
            attention_context=state.get("attention_context"),
            mode=state.get("mode"), mood=state.get("mood"),
            drives=state.get("drives"), networks=state.get("networks"),
            action_tendencies=state.get("action_tendencies"),
            hormone=state.get("hormone"), exploration=state.get("exploration"),
            tendencies=state.get("tendencies"),
            recent_dialogue=state.get("recent_dialogue"),
            recalled_nodes=topk_nodes if topk_nodes is not None
            else [getattr(n, "id", n) for n in self.snapshot().topk_nodes],
            faiss_hits=state.get("faiss_hits"),
            world_state=state.get("world_state"),
            cognitive_events=state.get("cognitive_events"),
            action_intent=state.get("action_intent"),
            action_queue_len=state.get("action_queue_len", 0),
            speech_act_landscape=state.get("speech_act_landscape"),
            demand_analysis=state.get("demand_analysis"),
            extra_constraints=state.get("extra_constraints"),
            perception_connected=state.get("perception_connected", False),
            environment=state.get("environment"),
            demand=state.get("demand"),
        )
        compiled = compile_for_language(ctx, path=path)
        snap_nodes = topk_nodes if topk_nodes is not None else self.snapshot().topk_nodes
        snap_edges = topk_edges if topk_edges is not None else self.snapshot().topk_edges
        return LanguageRequest(
            path=path, user_text=text, payload=compiled,
            topk_nodes=list(snap_nodes), topk_edges=list(snap_edges),
            evidence=dict(evidence or {}), channel=channel,
            decision=decision.raw)

    # ── 时间推进（可选：无后台循环的沙箱装配） ───────────────

    def tick(self, n: int = 1) -> None:
        """按拍推进认知。cc 在场则走既有 tick_once（continuous_cognition.py:207，
        包含衰减/调度——不复制其节拍逻辑）；cc 缺席（独立装配/实验）时仅做
        衰减+单步扩散（diffuse_step，diffusion_engine.py:940），与回合内扩散原语一致。"""
        if self.cc is not None:
            for _ in range(max(0, int(n))):
                self.cc.tick_once()
            return
        for _ in range(max(0, int(n))):
            self.engine.diffuse_step()
