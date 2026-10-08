"""fas.companion.stub_backends — 非 LLM 占位后端（测试 + 降级用）。

两个用途：
  1. 集成测试证明"换语言后端不动认知"（EchoLanguageBackend）；
  2. 认知/LLM 故障时陪伴功能照跑的兜底表达（TemplateFallbackBackend，
     只做最小可读输出——注意：这是可用性兜底，不是人格方案，
     未来由 FASLanguageBackend 替换，不在认知层写死任何表达规则）。
"""
from __future__ import annotations

from fas.contracts import LanguageRequest, Utterance


class CognitiveNoAccess:
    """空语言触点：认知内部 LLM 触点全部走各自既有规则兜底
    （curiosity._fallback_question 885、reflection 代码把关、_express 预算降级沉默）。"""
    llm = None
    chat_llm = None

    def ask(self, task: str, user_input: str) -> str:
        raise RuntimeError("语言后端缺席（认知侧应命中各自规则兜底）")

    def system_prompt(self, task: str) -> str:
        raise RuntimeError("语言后端缺席（无模板文本可提供）")


class EchoLanguageBackend:
    """确定性回声后端：realize 原样复述认知请求的关键裁决字段。
    测试用它验证 core→companion 数据面完整、且认知输出不随语言后端改变。"""

    def __init__(self):
        self._access = CognitiveNoAccess()

    def parse(self, text, *, fast=False):
        return {"nodes": [], "edges": [], "dialogue_act": "information_statement",
                "response_expectation": "medium", "echo": text}

    def extract_memories(self, text, context_nodes=None):
        return {"nodes": [], "edges": []}

    def extract_intentions(self, text):
        return {"intentions": []}

    def realize(self, request: LanguageRequest) -> Utterance:
        dec = request.decision or {}
        payload = request.payload or {}
        outline = payload.get("outline") or {}
        must = "; ".join((outline.get("must") or [])[:3])
        top = ""
        if request.topk_nodes:
            n0 = request.topk_nodes[0]
            top = str(getattr(n0, "id", n0))
        return Utterance(
            text=(f"[echo:{request.path}] mode={dec.get('decision')} "
                  f"intent={dec.get('response_intent')} focus={top} must={must}"),
            origin="reactive",
            refs={"path": request.path, "backend": "echo"})

    def generate(self, task, user_input):
        return f"[echo:{task}]"

    def access(self):
        return self._access


class TemplateFallbackBackend:
    """降级后端：认知失败/语言缺席时仍能给出最小可读话语（可用性兜底）。"""

    def __init__(self, template="我在。刚才那句我没处理清楚，可以说一遍吗？"):
        self._tpl = template
        self._access = CognitiveNoAccess()

    def parse(self, text, *, fast=False):
        return {"nodes": [], "edges": []}

    def extract_memories(self, text, context_nodes=None):
        return {"nodes": [], "edges": []}

    def extract_intentions(self, text):
        return {"intentions": []}

    def realize(self, request: LanguageRequest) -> Utterance:
        return Utterance(text=self._tpl, origin="reactive",
                         refs={"backend": "fallback"})

    def generate(self, task, user_input):
        return self._tpl

    def access(self):
        return self._access
