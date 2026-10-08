"""fas.companion.temporary_llm — 临时语言后端（temporary implementation）。

包装现状的 NLPProcessor（nlp_processor.py:48，MiMo/DeepSeek/Ollama 云/本地 LLM）。
它不是 FAS 认知主张的一部分：语言生成机制成熟后（fas/registry.py 槽
LanguageBackend / FASLanguageBackend），在装配处整只换掉，认知层不动。

本文件是认知↔语言的唯一跨界点之一：
  CognitiveLanguageAdapter  = CognitiveLanguageAccess 的实现（三成员直通）；
  TemporaryLLMBackend       = LanguageBackend 的实现（陪伴主管道用）。
"""
from __future__ import annotations

import logging

from fas.contracts import LanguageRequest, Utterance

logger = logging.getLogger("fas.companion.temporary_llm")


class CognitiveLanguageAdapter:
    """认知侧最小面的直通包装（llm / chat_llm / ask / system_prompt）。

    现状消费点（全部经属性访问，duck-type 直通即零行为变化）：
      reflection_engine.py:341      self.nlp.llm
      curiosity_engine.py:866       nlp_processor.chat_llm
      continuous_cognition.py:965   self.nlp.chat_llm
      self_graph.py:798（经 generate_thought） nlp_processor.ask("thought", ctx)
    system_prompt：认知触点的模板文本一律从这里取（= prompt_templates.build_prompt
    同一字符串，逐字等价），认知模块不再直接 import prompt_templates。
    注：FAS_IDENTITY 前置模板的问题属提示词侧（登记债务 §6.4），本包装不改变行为。
    """

    __slots__ = ("_nlp",)

    def __init__(self, nlp_processor):
        self._nlp = nlp_processor

    @property
    def llm(self):
        return self._nlp.llm

    @property
    def chat_llm(self):
        return self._nlp.chat_llm

    def ask(self, task: str, user_input: str) -> str:
        return self._nlp.ask(task, user_input)

    def system_prompt(self, task: str) -> str:
        from prompt_templates import build_prompt   # 惰性：仅陪伴运行时可达
        return build_prompt(task)


class TemporaryLLMBackend:
    """LanguageBackend 的临时实现：委托 NLPProcessor 的既有方法。"""

    def __init__(self, nlp_processor):
        self._nlp = nlp_processor
        self._access = CognitiveLanguageAdapter(nlp_processor)

    # ── LanguageBackend 协议面 ──────────────────────────────

    def parse(self, text: str, *, fast: bool = False) -> dict:
        if fast:
            return self._nlp.process_fast(text)     # nlp_processor.py:335
        return self._nlp.process(text)              # nlp_processor.py:262

    def extract_memories(self, text: str, context_nodes: list = None) -> dict:
        return self._nlp.extract_assertion_graph(text, context_nodes or [])

    def extract_intentions(self, text: str) -> dict:
        return self._nlp.extract_intentions(text)   # nlp_processor.py:433

    def realize(self, request: LanguageRequest) -> Utterance:
        """语言实现：认知状态编译产物 → 话语。
        L1 = answer_short(text, action_result, cognitive_context)（app.py:4674 同形）
        L2 = answer_question(original_text, topk_nodes, topk_edges,
                             cognitive_context, mode, attention_context)（app.py:4771 同形）
        """
        payload = request.payload or {}
        if request.path == "L1":
            action_result = request.evidence.get("mc_action") or {}
            text = self._nlp.answer_short(
                request.user_text, action_result=action_result,
                cognitive_context=payload)          # nlp_processor.py:487
        else:
            state = payload.get("state") or {}
            text = self._nlp.answer_question(
                request.user_text, request.topk_nodes, request.topk_edges,
                context_type=payload.get("context_type"),
                cognitive_context=payload,
                mode=payload.get("mode"),
                attention_context=(state.get("attention")
                                   or payload.get("attention_context") or {}))
        return Utterance(text=text or "", origin="reactive",
                         refs={"path": request.path, "channel": request.channel,
                               "intent": (request.decision or {}).get(
                                   "response_intent")})

    def generate(self, task: str, user_input: str) -> str:
        return self._nlp.ask(task, user_input)      # nlp_processor.py:1526

    def access(self) -> CognitiveLanguageAdapter:
        return self._access

    # ── 运维直通（LLM 热切换属陪伴侧，app.py:7417 现状不变） ──
    def __getattr__(self, name):
        # 显式白名单之外的属性不代理：陪伴主管道若要用 nlp 的其他方法，
        # 应在本协议面上补齐，避免"包装器变成万能转发"这一反模式。
        raise AttributeError(
            f"TemporaryLLMBackend 不暴露 '{name}'（协议面：parse/extract_memories/"
            f"extract_intentions/realize/generate/access；"
            f"如需新能力，先加进 fas.protocols.LanguageBackend）")
