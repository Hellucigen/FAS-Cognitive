"""fas.protocols — 两侧的稳定接口（typing.Protocol）。

替换规则（验收问题 A/B/E）：实现同一 Protocol 的组件可在装配处（fas/core/assembly.py、
fas/companion 装配）逐个替换，两侧既有代码不需要修改内部逻辑。
状态词汇与"当前占用者是否临时"见 fas/registry.py。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from fas.contracts import (ActionProposal, CognitiveEvent, CognitiveStateSnapshot,
                           LanguageRequest, MemoryEvent, TurnDecision,
                           ActivationReport, Utterance)


# ── 认知侧对语言层的最小面 ─────────────────────────────────────────
# 审计事实：认知模块（curiosity_engine.py:866、reflection_engine.py:341、
# self_graph.py:798、continuous_cognition.py:965、diffusion_engine.generate_thought）
# 对语言总线只用到 llm/chat_llm/ask 加按任务取模板文本，共四类。
# 认知核心被允许持有的语言对象上限就是这个面。

@runtime_checkable
class CognitiveLanguageAccess(Protocol):
    """认知逻辑内部 LLM 触点的最小接口（llm/chat_llm 是 langchain Runnable 句柄，
    ask(task, input)->str 是任务化文本生成，system_prompt(task)->str 按任务名取
    语言侧模板文本）。模板归陪伴层所有：认知模块不得再直接 import
    prompt_templates（2026-10 两仓拆分修复的三个触点：continuous_cognition._express /
    curiosity_engine.generate_question / reflection_engine._llm_reflection；
    system_prompt 缺席时触点回退旧直连 import，兼容统一仓遗留环境）。"""

    @property
    def llm(self) -> Any: ...

    @property
    def chat_llm(self) -> Any: ...

    def ask(self, task: str, user_input: str) -> str: ...

    def system_prompt(self, task: str) -> str: ...


# ── 陪伴主管道对语言层的完整面 ─────────────────────────────────────

class LanguageBackend(Protocol):
    """对话回合的语言服务：解析（语言→结构）、实现（认知状态→话语）、
    记忆/意图抽取。TemporaryLLMBackend 是现状唯一实现；
    FASLanguageBackend（研究项）成熟后实现同一接口整体替换。"""

    def parse(self, text: str, *, fast: bool = False) -> dict: ...

    def extract_memories(self, text: str, context_nodes: list = None) -> dict: ...

    def extract_intentions(self, text: str) -> dict: ...

    def realize(self, request: LanguageRequest) -> Utterance: ...

    def generate(self, task: str, user_input: str) -> str: ...

    def access(self) -> CognitiveLanguageAccess: ...


# ── 表达出口 ───────────────────────────────────────────────────────

class ExpressionChannel(Protocol):
    """话语的投递通道（UI 轮询队列 / Charon Bridge / 游戏内聊天 …）。
    deliver 必须幂等可失败（返回 bool），通道故障不得反压认知层。"""

    name: str

    def deliver(self, utterance: Utterance) -> bool: ...


# ── 游戏/具身后端 ──────────────────────────────────────────────────

class GameBackend(Protocol):
    """TemporaryMineflayerAgent（minecraft/ 包）实现此接口；
    未来 FAS autonomy 验证充分后由 FASActionController 同位替换。"""

    def world_snapshot(self) -> dict: ...

    def execute(self, proposal: ActionProposal) -> dict: ...


# ── 认知核心门面（Companion 看到的 Core 全部形状） ─────────────────

class CognitiveCoreAPI(Protocol):
    """陪伴层允许调用的认知 API 全集。除只读快照外，一切状态变化
    必须经由这里（perceive/record_memory/propose_action/tick）进入认知层。"""

    def perceive(self, event: CognitiveEvent) -> ActivationReport: ...

    def decide(self, parsed: dict, text: str, **context) -> TurnDecision: ...

    def compile_language_request(self, **context) -> LanguageRequest: ...

    def snapshot(self) -> CognitiveStateSnapshot: ...

    def record_memory(self, event: MemoryEvent) -> bool: ...

    def propose_action(self, proposal: ActionProposal) -> Any: ...

    def attach_language_backend(self, access: CognitiveLanguageAccess) -> None: ...
