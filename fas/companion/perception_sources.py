"""fas.companion.perception_sources — 外部世界 → CognitiveEvent 的规范化入口。

陪伴侧各感知/通道源（ear/eye/vision/minecraft/Charon/UI）向认知层供给的
一切外部信息，都收敛成 fas.contracts.CognitiveEvent 后调用 core.perceive。
本模块只做形状转换，不做认知判断（判断是扩散/决策头的职责）。
"""
from __future__ import annotations

from fas.contracts import CognitiveEvent, EVT_USER_MESSAGE, EVT_PERCEPTION, \
    EVT_ENVIRONMENT, EVT_ACTION_RESULT


def user_message(source: str, text: str, *, parsed: dict = None,
                 channel: str = "web") -> CognitiveEvent:
    ev = CognitiveEvent(kind=EVT_USER_MESSAGE, source=source, text=text,
                        payload={"channel": channel})
    if parsed:
        ev.payload.update(nodes=parsed.get("nodes") or [],
                          edges=parsed.get("edges") or [],
                          similarity_map=parsed.get("similarity_map"))
    return ev


def perception(source: str, text: str, nodes: list, edges: list = None,
               **extra) -> CognitiveEvent:
    return CognitiveEvent(kind=EVT_PERCEPTION, source=source, text=text,
                          payload={"nodes": nodes, "edges": edges or [],
                                   **extra})


def environment(source: str, state: dict, edges: list = None,
                text: str = "") -> CognitiveEvent:
    return CognitiveEvent(kind=EVT_ENVIRONMENT, source=source, text=text,
                          payload={"edges": edges or [], "state": state})


def action_result(source: str, result: dict) -> CognitiveEvent:
    return CognitiveEvent(kind=EVT_ACTION_RESULT, source=source,
                          text=str(result.get("describe") or ""),
                          payload={"result": result})


def from_charon_summary(summary: dict) -> CognitiveEvent:
    """Charon Bridge GET /api/summary 的回读 → 环境事件。
    Charon 侧字段以 backend/bridge/bridge.go:285 返回为准，这里不猜语义，
    原样放进 state——入图/点亮策略是认知侧既有机制的事。"""
    return environment("charon", dict(summary or {}))
