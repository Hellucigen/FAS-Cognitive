"""fas.companion.pipeline — 陪伴回合参考管线（迁移目标形态）。

    外部事件 → (陪伴)语言解析 → (认知)perceive/decide/compile → (陪伴)realize → 通道

这是 app.py `process_nlp`（2999–5347 单体）所做的事情的**最小契约化重述**：
不复制其回合内全部机制（反射快路径、预算门、周期记账、异步反思…），
只给出四步骨架，作为未来逐段迁移的落点与集成测试的被验对象。
`run_turn` 的每一段都可独立失败降级：认知炸 → fallback 后端仍出话语（验收 C）。
"""
from __future__ import annotations

import logging

from fas.companion.perception_sources import user_message
from fas.contracts import Utterance

logger = logging.getLogger("fas.companion.pipeline")


def run_turn(core, backend, *, text: str, source: str = "web",
             channels: list = None, fallback: bool = True,
             path_hint: str = None, fast_parse: bool = False) -> Utterance:
    """跑一个最小对话回合，返回话语。

    core    : CognitiveCore（fas.core.facade）
    backend : LanguageBackend（临时=TemporaryLLMBackend / 测试=Echo…）
    fallback: 认知段异常时是否退到"直接语言应答"（True=陪伴照跑）
    """
    # ① 陪伴→认知：外部事件入认知层
    ev = user_message(source, text)
    try:
        parsed = backend.parse(text, fast=fast_parse)
        if parsed.get("nodes"):     # 空解析不覆盖调用方预置的 payload
            ev.payload.update(nodes=parsed["nodes"],
                              edges=parsed.get("edges") or [],
                              similarity_map=parsed.get("similarity_map"))
        core.perceive(ev)

        # ② 认知：状态迁移后的决策与语言请求
        decision = core.decide(parsed, text)
        snap = core.snapshot()
        path = path_hint or ("L1" if not decision.should_respond else "L2")
        request = core.compile_language_request(
            decision=decision, parsed=parsed, text=text, path=path,
            channel=source,
            topk_nodes=snap.topk_nodes, topk_edges=snap.topk_edges,
            mood=snap.mood, drives=snap.drives)
    except Exception as e:                      # noqa: BLE001 —— 陪伴可用性优先
        logger.warning("[pipeline] 认知段异常，降级直答：%r", e)
        if not fallback:
            raise
        from fas.contracts import LanguageRequest
        request = LanguageRequest(path="L1", user_text=text,
                                  payload={}, decision={"decision": "respond"})
    # ③ 认知→陪伴：语言实现
    utterance = backend.realize(request)
    # ④ 陪伴出口：投递到全部已接通道（失败容忍）
    for ch in channels or []:
        try:
            ch.deliver(utterance)
        except Exception:                       # noqa: BLE001
            logger.warning("[pipeline] 通道 %s 投递异常", getattr(ch, "name", "?"))
    return utterance
