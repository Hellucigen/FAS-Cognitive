# reflection_engine.py — Reflection Engine: 认知反思循环
# ============================================================================
# FAS Phase 2: Reflection Engine 核心模块。
#
# 设计原则：
#   1. LLM 不直接生成人格 — 人格来自 Self Model 的长期积累
#   2. 反思产出是结构化候选更新，不是自由文本
#   3. 所有候选必须带 confidence + importance + approval_status
#   4. pending 状态的候选不影响 Self Model 实际取值
#   5. 复用 SelfMemoryUpdater 的门槛判断（不重复造轮子）
#
# 三大触发方式：
#   periodic  — 每 N 轮对话定期触发
#   event     — 累计情绪激活度超过阈值触发
#   user      — 用户主动要求
# ============================================================================

import logging
import time
import json
from datetime import datetime, timedelta
from typing import Optional

from graph_model import KnowledgeGraph, Node, Edge, now_str

logger = logging.getLogger(__name__)


class ReflectionEngine:
    """认知反思引擎。

    执行完整反思循环：收集输入 → LLM 分析 6 个必答问题 → 解析候选 →
    门槛判断 → 持久化。
    """

    def __init__(self, kg: KnowledgeGraph, nlp_processor, config: dict,
                 episodic_buffer=None, disposition_store=None):
        self.kg = kg
        self.nlp = nlp_processor
        self.config = config
        self.buffer = episodic_buffer  # EpisodicBuffer 引用
        self.dispositions = disposition_store  # Reflection Evolution v2

        # 计数器
        self._turn_count = 0
        self._last_reflection_time = 0.0
        self._emotion_accumulator = 0.0
        self._pending_reflections: list = []  # 已完成的反思记录

    # ── 触发检查 ──────────────────────────────────────────

    def check_triggers(self, emotion_context: dict = None) -> Optional[str]:
        """检查是否应该触发反思。返回 trigger 类型或 None。

        在每次 NLP 轮次末尾调用。
        """
        self._turn_count += 1

        # 累积情绪激活度
        if emotion_context:
            emotions = emotion_context.get("active_emotions", [])
            if emotions:
                with self.kg._lock:
                    for nid in emotions:
                        node = self.kg.get_node(nid)
                        if node and node.activation > 0.1:
                            self._emotion_accumulator += node.activation

        # ── Event trigger: 情绪超阈值 ──
        threshold = self.config.get("reflection", {}).get(
            "emotion_trigger_threshold", 3.0
        )
        if self._emotion_accumulator >= threshold:
            # P0-5② 修复：先记日志再清零（原实现清零后打印，日志恒为 0.00）
            logger.info(f"[Reflection] 事件触发 (情绪累积={self._emotion_accumulator:.2f})")
            self._emotion_accumulator = 0.0
            return "event"

        # ── Periodic trigger: 轮次计数 ──
        interval = self.config.get("reflection", {}).get("periodic_interval_turns", 20)
        if self._turn_count >= interval:
            self._turn_count = 0
            logger.info(f"[Reflection] 定期触发 (每 {interval} 轮)")
            return "periodic"

        return None

    # ── 主循环 ────────────────────────────────────────────

    def run(self, trigger: str = "periodic",
            topk_nodes: list = None,
            chat_log_entries: list = None) -> dict:
        """执行一次完整反思循环。

        Args:
            trigger: "periodic" | "event" | "user"
            topk_nodes: 当前激活的 TopK 节点
            chat_log_entries: 对话日志条目

        Returns:
            {"reflection_id": str, "candidates": [...],
             "approved": [...], "pending": [...], "deferred": [...]}
        """
        ref_config = self.config.get("reflection", {})
        min_exp = ref_config.get("min_experiences_for_reflection", 5)

        # ── 1. 收集输入 ──
        context = self._collect_input(topk_nodes, chat_log_entries)

        # 检查是否有足够的数据
        exp_count = len(context.get("recent_experiences", []))
        if exp_count < min_exp and trigger != "user":
            logger.info(
                f"[Reflection] 数据不足: {exp_count} < {min_exp} 条经历，跳过"
            )
            return {"reflection_id": None, "skipped": True,
                    "reason": f"insufficient_experiences ({exp_count} < {min_exp})"}

        # ── 2. LLM 分析 6 问 ──
        llm_output = self._llm_reflection(context)
        if llm_output is None:
            return {"reflection_id": None, "skipped": True,
                    "reason": "llm_failed"}

        # ── 3. 解析候选 ──
        candidates = self._parse_candidates(llm_output)
        max_candidates = ref_config.get("max_candidates_per_cycle", 5)
        if len(candidates) > max_candidates:
            candidates = sorted(candidates, key=lambda c: c.get("confidence", 0),
                               reverse=True)[:max_candidates]

        # ── 4. 门槛判断 ──
        from self_model import SelfMemoryUpdater
        updater = SelfMemoryUpdater(self.kg, self.config)
        gated = updater.process_reflection_candidates(
            candidates, reflection_id=""  # 先占位，持久化时填入
        )

        # ── 4b. Reflection Evolution v2: 行为倾向处理 ──
        # LLM 只提候选/建议；数值与 stable 判定全部由代码按证据执行
        disposition_result = self._process_dispositions(llm_output)

        # ── 5. 持久化反思节点 ──
        reflection_id = self._persist_reflection(
            context, llm_output, candidates, gated, trigger
        )

        # 更新候选中的 reflection_id
        for entry in gated.get("approved", []) + gated.get("pending", []):
            entry["reflection_id"] = reflection_id

        self._last_reflection_time = time.time()
        self._pending_reflections.append({
            "reflection_id": reflection_id,
            "trigger": trigger,
            "candidate_count": len(candidates),
            "approved": len(gated.get("approved", [])),
            "pending": len(gated.get("pending", [])),
            "deferred": len(gated.get("deferred", [])),
        })

        result = {
            "reflection_id": reflection_id,
            "trigger": trigger,
            "candidates": candidates,
            "dispositions": disposition_result,
            "six_questions": {
                "what_happened": llm_output.get("what_happened", ""),
                "worth_keeping": llm_output.get("worth_keeping", ""),
                "new_knowledge": llm_output.get("new_knowledge"),
                "user_relation_change": llm_output.get("user_relation_change"),
                "self_understanding_change": llm_output.get("self_understanding_change"),
                "new_goal": llm_output.get("new_goal"),
            },
            "approved": gated.get("approved", []),
            "pending": gated.get("pending", []),
            "deferred": gated.get("deferred", []),
        }

        logger.info(
            f"[Reflection] 完成: {reflection_id} "
            f"approved={len(gated.get('approved',[]))} "
            f"pending={len(gated.get('pending',[]))} "
            f"deferred={len(gated.get('deferred',[]))}"
        )
        return result

    # ── 输入收集 ──────────────────────────────────────────

    def _collect_input(self, topk_nodes=None, chat_log_entries=None) -> dict:
        """收集反思所需的所有输入数据。"""
        context = {
            "recent_experiences": [],
            "emotional_events": [],
            "topk_nodes": [],
            "conversation_summary": "",
        }

        # 情景缓冲
        if self.buffer:
            recent = self.buffer.retrieve_recent(n=20)
            context["recent_experiences"] = [
                {"text": e.get("raw_text", "")[:200],
                 "timestamp": e.get("timestamp", ""),
                 "importance": e.get("importance", 0)}
                for e in recent
            ]

        # 高情绪事件：扫描 episodic 空间中有情绪边的节点
        window_days = self.config.get("reflection", {}).get(
            "emotion_event_window_days", 7
        )
        cutoff = (datetime.now() - timedelta(days=window_days)).strftime(
            "%Y/%m/%d %H:%M:%S"
        )
        with self.kg._lock:
            for edge in self.kg.edges:
                if edge.relation in ("引发", "感受"):
                    src_node = self.kg.get_node(edge.src)
                    if src_node and getattr(src_node, "label", "") == "declarative-episodic":
                        created = getattr(src_node, "created", "") or ""
                        if created >= cutoff and edge.activation > 0.05:
                            dst_node = self.kg.get_node(edge.dst)
                            emotion_name = dst_node.id if dst_node else "?"
                            context["emotional_events"].append({
                                "event": src_node.id,
                                "emotion": emotion_name,
                                "activation": edge.activation,
                            })

        # TopK 节点
        if topk_nodes:
            context["topk_nodes"] = [
                {"id": n.id, "activation": round(n.activation, 4),
                 "label": getattr(n, "label", ""),
                 "space": getattr(n, "graph_space", "semantic")}
                for n in topk_nodes[:15]
            ]

        # Reflection Evolution v2: FAS 表达事件（行为观察）
        if self.buffer:
            annotated = self.buffer.unreflected_expressions(n=15)
            context["behavior_observations"] = [
                {
                    "turn": e.get("ts", ""),
                    "context": e.get("context", ""),
                    "behavior": e.get("behavior", ""),
                    "outcome": e.get("outcome", ""),
                    "outcome_detail": e.get("outcome_detail", ""),
                    "user_text": str(e.get("user_text", ""))[:60],
                }
                for e in annotated
            ]

        # Reflection Evolution v2: 现有行为倾向
        if self.dispositions:
            context["current_dispositions"] = self.dispositions.list_dispositions()[:15]

        # 对话摘要
        if chat_log_entries:
            recent_chats = chat_log_entries[-10:]
            context["conversation_summary"] = "\n".join(
                f"用户: {c.get('user_input', '')[:100]}"
                for c in recent_chats if c.get("user_input")
            )

        return context

    # ── LLM 调用 ──────────────────────────────────────────

    def _llm_reflection(self, context: dict) -> Optional[dict]:
        """调用 LLM 进行 6 问反思分析。"""
        # 模板经语言注入面取（system_prompt 缺席=遗留环境，回退旧直连，逐字等价）
        _sp = getattr(self.nlp, "system_prompt", None)
        if _sp is not None:
            REFLECTION_ANALYSIS = _sp("reflection_analysis")
        else:
            from prompt_templates import REFLECTION_ANALYSIS
        from langchain_core.prompts import ChatPromptTemplate

        # 构建用户消息
        exp_text = "\n".join(
            f"- [{e.get('timestamp','')}] {e.get('text','')}"
            for e in context.get("recent_experiences", [])[:10]
        ) or "(无)"

        emo_text = "\n".join(
            f"- {e.get('event','')} → {e.get('emotion','')} (act={e.get('activation',0):.3f})"
            for e in context.get("emotional_events", [])[:5]
        ) or "(无)"

        topk_text = "\n".join(
            f"- {n.get('id','')} (act={n.get('activation',0):.4f}, space={n.get('space','')})"
            for n in context.get("topk_nodes", [])[:10]
        ) or "(无)"

        # Reflection Evolution v2: 行为观察 + 现有倾向
        beh_text = "\n".join(
            "- [{turn}] 情境={ctx} 行为={bhv} 结果={oc}({detail}) | 用户原话: {ut}".format(
                turn=str(e.get("turn", ""))[:16],
                ctx=e.get("context", ""),
                bhv=e.get("behavior", ""),
                oc=e.get("outcome", ""),
                detail=e.get("outcome_detail", ""),
                ut=e.get("user_text", ""),
            )
            for e in context.get("behavior_observations", [])[:12]
        ) or "(无)"

        disp_text = "\n".join(
            "- {id} 状态={st} 强度={str_} 证据数={ec} 正反馈={pf} 负反馈={nf}".format(
                id=d.get("id", ""),
                st=d.get("status", ""),
                str_=d.get("strength", ""),
                ec=d.get("evidence_count", 0),
                pf=d.get("pos_feedback", 0),
                nf=d.get("neg_feedback", 0),
            )
            for d in context.get("current_dispositions", [])[:12]
        ) or "(无)"

        user_msg = f"""【最近经历】
{exp_text}

【高情绪事件】
{emo_text}

【当前认知焦点】
{topk_text}

【FAS 行为观察】
{beh_text}

【现有行为倾向】
{disp_text}

【对话摘要】
{context.get('conversation_summary', '(无)')}"""

        try:
            prompt = ChatPromptTemplate.from_messages([
                ("system", REFLECTION_ANALYSIS),
                ("human", "{{input}}")
            ], template_format="mustache")

            chain = prompt | self.nlp.llm
            import fas_log
            with fas_log.llm_purpose("reflection"):
                response = chain.invoke({"input": user_msg})
            raw = response.content if hasattr(response, 'content') else str(response)

            # 尝试解析 JSON
            # 移除可能的 markdown 代码块
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw

            result = json.loads(raw.strip())
            logger.info(f"[Reflection] LLM 分析完成: {list(result.keys())}")
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"[Reflection] JSON 解析失败: {e}, raw={raw[:200]}")
            return None
        except Exception as e:
            logger.error(f"[Reflection] LLM 调用失败: {e}")
            return None

    # ── 候选解析 ──────────────────────────────────────────

    def _parse_candidates(self, llm_output: dict) -> list:
        """从 LLM 输出中提取候选更新列表。"""
        candidates = llm_output.get("candidates", [])
        if not isinstance(candidates, list):
            return []

        parsed = []
        for c in candidates:
            if not isinstance(c, dict):
                continue
            ctype = c.get("type", "")
            if ctype not in ("belief_update", "preference_update"):
                continue

            content = c.get("content", c.get("target", ""))
            if not content:
                continue

            confidence = float(c.get("confidence", 0.5))
            confidence = max(0.1, min(0.95, confidence))

            parsed.append({
                "type": ctype,
                "content" if ctype == "belief_update" else "target":
                    content,
                "confidence": confidence,
                "evidence": c.get("evidence", []),
                "source": "llm_inference",
            })

        return parsed

    # ── Reflection Evolution v2: 行为倾向处理 ──────────────

    def _evidence_traces_to_event(self, evidence: str) -> bool:
        """反思证据的可追溯校验：证据必须命中一条真实事件的文本片段。

        语料 = 近期表达事件（user_text/answer）+ 近期经历原文。任一长度 ≥4
        的证据子串出现在语料里即算可追溯。防的是"LLM 凭空归纳出她喜欢 X"。
        """
        if self.buffer is None:
            return False
        e = "".join(str(evidence or "").split())
        if len(e) < 4:
            return False
        corpus = []
        try:
            for ex in self.buffer.get_expressions(30):
                corpus.append(str(ex.get("user_text", "")))
                corpus.append(str(ex.get("answer", "")))
                for t in (ex.get("topics") or []):
                    corpus.append(str(t))
            for exp in self.buffer.retrieve_recent(30):
                corpus.append(str(getattr(exp, "raw_text", "") or ""))
        except Exception:
            return False
        blob = "".join("".join(c.split()) for c in corpus)
        if not blob:
            return False
        # 证据里任意一段 ≥4 字的连续片段能在语料里找到即可
        for n in (8, 6, 4):
            for i in range(0, max(1, len(e) - n + 1)):
                if e[i:i + n] in blob:
                    return True
        return False

    def _process_dispositions(self, llm_output: dict) -> dict:
        """处理 LLM 的倾向候选与调整建议，并执行证据驱动的数值更新。

        规则（代码，非 LLM）：
        - candidate_dispositions 最多新建 3 条/周期，新建一律 candidate
        - 已标注 outcome 的表达事件批量回写（正强化/负衰减），随后标记已消费
        - adjust_dispositions 仅轻量步长调整
        - 周期衰减一次
        """
        result = {"new_candidates": [], "applied_outcomes": 0,
                  "llm_adjustments": 0, "errors": []}
        if not self.dispositions or not self.buffer:
            return result

        from disposition_store import BEHAVIORS, DIALOGUE_ACT_CONTEXT, DEFAULT_CONTEXT

        # 1. LLM 候选倾向（新建或补证据）
        raw_cands = llm_output.get("candidate_dispositions") or []
        if isinstance(raw_cands, list):
            created = 0
            for c in raw_cands:
                if not isinstance(c, dict) or created >= 3:
                    break
                bhv = str(c.get("behavior", "")).strip()
                ctx = str(c.get("context", "")).strip()
                if bhv not in BEHAVIORS:
                    continue
                # 情境名校验：映射不到词表的丢弃（防复合情境节点）
                if not (ctx.startswith("情境:") and
                        (ctx in set(DIALOGUE_ACT_CONTEXT.values())
                         or ctx in (DEFAULT_CONTEXT, "情境:用户情绪低落", "情境:熟悉关系"))):
                    result["errors"].append(f"invalid context: {ctx}")
                    continue
                raw_ev = c.get("evidence")
                if isinstance(raw_ev, str):
                    raw_ev = [raw_ev]
                ev = "; ".join(str(x) for x in (raw_ev or [])[:2])[:120]
                if not ev.strip():
                    continue  # 无证据不提（设计文档 §五）
                # 人格学习架构 §七：反思只能"发现"候选，不能"发明"人格。
                # LLM 提出的证据必须能追溯到**至少一条真实事件**（经历原文或
                # 表达事件文本），否则拒绝——杜绝"她似乎喜欢 X"这种无源断言。
                if not self._evidence_traces_to_event(ev):
                    result["errors"].append(
                        f"untraceable evidence: {ctx}/{bhv}: {ev[:40]}")
                    logger.info(
                        f"[Reflection] 候选拒绝（证据无法追溯到真实事件）: "
                        f"行为:{bhv}@{ctx} evidence={ev[:50]!r}")
                    continue
                pair = self.dispositions.get_or_create(
                    ctx, bhv, evidence_ref=ev, source="reflection")
                if pair is not None:
                    created += 1
                    result["new_candidates"].append(pair.id)

        # 2. 证据驱动回写：已标注 outcome 的表达事件
        #    （学习闭环修复：四态 outcome 都是证据；neutral/ambiguous 计数
        #     不动强度；反思来源步长温和，不让归纳性结论压过真实互动）
        exprs = self.buffer.unreflected_expressions(n=30)
        for e in exprs:
            outcome = e.get("outcome")
            if outcome in ("positive", "negative", "neutral", "ambiguous"):
                self.dispositions.apply_outcome(
                    e.get("context", ""), e.get("behavior", ""),
                    outcome, evidence_ref=str(e.get("user_text", ""))[:60],
                    source="reflection", allow_create=False)
                result["applied_outcomes"] += 1
        if exprs:
            self.buffer.mark_expressions_reflected(exprs)

        # 3. LLM 调整建议（轻量）
        raw_adj = llm_output.get("adjust_dispositions") or []
        if isinstance(raw_adj, list):
            for a in raw_adj:
                if not isinstance(a, dict):
                    continue
                direction = str(a.get("direction", "")).strip()
                if direction not in ("reinforce", "weaken"):
                    continue
                bhv = str(a.get("behavior", "")).strip()
                ctx = str(a.get("context", "")).strip()
                if bhv in BEHAVIORS:
                    self.dispositions.llm_adjust(ctx, bhv, direction,
                                                 reason=str(a.get("reason", "")))
                    result["llm_adjustments"] += 1

        # 4. 周期衰减
        self.dispositions.decay_all()
        return result

    # ── 持久化 ────────────────────────────────────────────

    def _persist_reflection(self, context: dict, llm_output: dict,
                            candidates: list, gated: dict,
                            trigger: str) -> str:
        """将反思节点和候选持久化到图谱。"""
        import time as _time

        ref_id = f"反思_{int(_time.time())}"

        node = Node(
            id=ref_id,
            weight=0.6,
            label="declarative-episodic",
            graph_space="episodic",
            extra_attrs={
                "type": "reflection",
                "trigger": trigger,
                "what_happened": llm_output.get("what_happened", ""),
                "worth_keeping": llm_output.get("worth_keeping", ""),
                "new_knowledge": llm_output.get("new_knowledge"),
                "user_relation_change": llm_output.get("user_relation_change"),
                "self_understanding_change": llm_output.get("self_understanding_change"),
                "new_goal": llm_output.get("new_goal"),
                "candidates": candidates,
                "approved_count": len(gated.get("approved", [])),
                "pending_count": len(gated.get("pending", [])),
                "deferred_count": len(gated.get("deferred", [])),
            }
        )
        self.kg.add_node(node)

        # 连接涉及的 TopK 节点
        topk_ids = [n.get("id", "") for n in context.get("topk_nodes", [])[:10]]
        for nid in topk_ids:
            if self.kg.get_node(nid) and not self.kg.get_edge(ref_id, nid, "涉及"):
                self.kg.add_edge(Edge(
                    src=ref_id, dst=nid, relation="涉及", weight=0.3,
                    relation_category="semantic_relation"
                ))

        # 连接批准的候选节点
        for entry in gated.get("approved", []):
            cand = entry.get("candidate", {})
            ctype = cand.get("type", "")
            if ctype == "belief_update":
                target_id = f"信念: {cand.get('content', '')}"
            elif ctype == "preference_update":
                target_id = f"偏好: {cand.get('target', '')}"
            else:
                continue
            if self.kg.get_node(target_id) and not self.kg.get_edge(ref_id, target_id, "产生"):
                self.kg.add_edge(Edge(
                    src=ref_id, dst=target_id, relation="产生", weight=0.5,
                    relation_category="cognitive_relation"
                ))

        logger.info(f"[Reflection] 反思节点已创建: {ref_id}")
        return ref_id

    # ── 查询接口 ──────────────────────────────────────────

    def get_pending_candidates(self) -> list:
        """获取所有待审批的反思候选。"""
        pending = []
        with self.kg._lock:
            for nid, node in self.kg.nodes.items():
                if not nid.startswith("反思_"):
                    continue
                extra = node.extra_attrs or {}
                if extra.get("type") != "reflection":
                    continue
                candidates_raw = extra.get("candidates", [])
                approved = extra.get("approved_count", 0)
                pending_count = extra.get("pending_count", 0)
                if pending_count > 0:
                    pending.append({
                        "reflection_id": nid,
                        "created": node.created,
                        "trigger": extra.get("trigger", ""),
                        "candidates": candidates_raw,
                        "approved": approved,
                        "pending": pending_count,
                    })
        return pending

    def get_recent_reflections(self, n: int = 5) -> list:
        """获取最近的反思记录。"""
        return self._pending_reflections[-n:]
