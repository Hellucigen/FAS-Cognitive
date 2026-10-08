"""fas.companion.assembly — 陪伴层装配。

替换点总闸（验收问题 B/E）：`build_language_backend` 是唯一决定"语言从哪来"的地方。
  temporary（现状唯一实现）：TemporaryLLMBackend(NLPProcessor)
  future                     ：FASLanguageBackend（研究项，实现 fas.protocols.LanguageBackend）
测试/降级占位：EchoLanguageBackend / TemplateFallbackBackend。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("fas.companion.assembly")


def build_language_backend(kind: str = "llm", *, nlp_processor=None):
    if kind == "llm":
        from fas.companion.temporary_llm import TemporaryLLMBackend
        if nlp_processor is None:
            from nlp_processor import NLPProcessor   # 陪伴侧才允许 import 语言总线
            nlp_processor = NLPProcessor()
        return TemporaryLLMBackend(nlp_processor)
    if kind == "echo":
        from fas.companion.stub_backends import EchoLanguageBackend
        return EchoLanguageBackend()
    if kind == "fallback":
        from fas.companion.stub_backends import TemplateFallbackBackend
        return TemplateFallbackBackend()
    raise ValueError(f"未知语言后端 kind={kind!r}（槽位台账见 fas/registry.py）")


def build_charon_channel(config: dict = None):
    """按配置开关构造 Charon 通道；默认关（零生产行为变化）。"""
    cfg = config or {}
    if not cfg.get("companion_charon_enabled", False):
        return None
    from fas.companion.charon_bridge import CharonBridgeChannel
    return CharonBridgeChannel(
        base_url=cfg.get("charon_bridge_url"),
        token=cfg.get("charon_bridge_token"),
        notify_toast=cfg.get("charon_notify_toast", False))
