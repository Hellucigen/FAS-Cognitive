# dialogue_signals.py — 对话信号检测（社交互动 / 情绪共振）
# ============================================================================
# 架构原则（2026-09-19c 重构）：
#
#   检测 ≠ 行为决定。
#
# 社交互动检测和情绪检测只是**认知信号输入源**：它们把"这轮输入涉及人际
# 互动 / 用户表达了情绪"转成图谱状态变化（节点激活 + 结构），最终行为
# （回应/沉默/追问/先做动作）仍由认知场、dialogue_decision 行为竞争、
# 行为倾向与激素/奖赏调制共同产生。
#
# 明确禁止的模式（本项目的设计红线）：
#   if greeting: reply_greeting()
#   if sadness: generate_empathy()
#   社交互动 -[触发]-> LLM回答          ← 已移除的行为导向直连边
#   用户悲伤 → 共情 → LLM安慰           ← 从未允许建立
#
# 允许且有意设计的结果：
#   检测到社交/情绪 → 图谱状态变化 → 行为竞争 → 无候选过阈值 → 沉默。
#
# 本模块只有纯函数：不做 LLM 调用、不持有全局状态，可独立单测。
# ============================================================================

import logging
import re

logger = logging.getLogger(__name__)

# 会话控制指令：对当前认知场的明确控制信号（不是社交行为模拟）。
# 保留 termination/suppression 机制；但它不应再额外触发回答。
_TOPIC_CONTROL_RE = re.compile(
    r"停止.{0,4}话题|换个话题|换话题吧|别问了|打住|到此为止|不想聊这个")

# 社交类对话行为（NLP dialogue_act 词表）
SOCIAL_DIALOGUE_ACTS = {"greeting", "farewell", "thanking", "apology",
                        "emotion_expression", "sharing"}


def is_topic_control(text: str) -> bool:
    """会话控制指令（停止话题/换话题/打住…）。"""
    return bool(_TOPIC_CONTROL_RE.search(str(text or "")))


def is_social_input(parsed: dict, text: str = "") -> bool:
    """这轮输入是否涉及人际互动关系。

    语义（重构后）：社交互动节点代表"当前事件涉及人与人之间的互动"，
    是认知语境标记——不是"应该启动社交行为"的模块开关。
    判据全部来自 NLP 语义解析（言外行为/对话行为），不做关键词社交分类。
    """
    p = parsed or {}
    if p.get("illocutionary_act") == "expressive":
        return True
    if p.get("dialogue_act") in SOCIAL_DIALOGUE_ACTS:
        return True
    return False


def semantic_emotion_hits(kg, text: str,
                          parsed_nodes: list, parsed_edges: list) -> set:
    """NLP 语义佐证的情绪命中（§D：优先语义解析，降低字符串误判）。

    情绪词出现在文本里只是候选（"这个游戏的角色叫焦虑"也含"焦虑"）；
    只有当 NLP 解析也把该情绪节点抽为实体、或某条抽取边的端点指向它时
    （MEMORY_EXTRACT 的 引发→情绪 模式），才算"用户真的在表达这种情绪"。
    """
    p_nodes = set()
    for n in (parsed_nodes or []):
        p_nodes.add(n if isinstance(n, str) else (n.get("id", "") if isinstance(n, dict) else ""))
    p_ends = set()
    for e in (parsed_edges or []):
        if isinstance(e, dict):
            p_ends.add(str(e.get("src", "")))
            p_ends.add(str(e.get("dst", "")))
    evidence = p_nodes | p_ends
    with kg._lock:
        emotion_ids = [nid for nid, n in kg.nodes.items()
                       if (n.extra_attrs or {}).get("type") == "emotion"]
    return {e for e in emotion_ids if e in text and e in evidence}


def keyword_emotion_hits(kg, text: str) -> list:
    """关键词层命中（候选集，可能有误报——因此只建结构不强激活）。"""
    if not text:
        return []
    with kg._lock:
        emotion_ids = [nid for nid, n in kg.nodes.items()
                       if (n.extra_attrs or {}).get("type") == "emotion"]
    return [e for e in emotion_ids if e in text]


def emotion_resonance(kg, engine, text: str,
                      parsed_nodes: list = None, parsed_edges: list = None) -> dict:
    """情绪共振（每轮对话调用一次）。

    两层效果（缺一不可）：
      长期结构：事件-[引发]->情绪-[感受]->Self —— 所有命中都建
                （经历/情绪记忆/自我认知的素材，激活为 0、无副作用）
      当前状态：仅语义佐证的命中获得有限的、可衰减的激活
                （emotion_resonance_boost，走正常扩散/发射/衰减体系，
                 register_activation_source(source_type="emotion")）
    关键词命中但无语义佐证（§D 误报场景）：只建结构，不改变当前认知场。
    """
    from self_graph import inject_emotion

    report = {"structural": [], "activated": []}
    keyword_hits = keyword_emotion_hits(kg, text)
    if not keyword_hits:
        return report
    semantic = semantic_emotion_hits(kg, text, parsed_nodes, parsed_edges)

    for emo in keyword_hits:
        # 长期结构（幂等；inject_emotion 内部有 get_edge 守卫）
        inject_emotion(kg, text, emo)
        report["structural"].append(emo)

    for emo in semantic:
        # 当前状态：有限激活 + 来源登记（emotion）
        inject_emotion(kg, text, emo, engine=engine, inject_activation=True)
        report["activated"].append(emo)

    if report["structural"]:
        logger.info(f"[EmotionResonance] 结构命中: {report['structural']}"
                    f" → 当前激活: {report['activated'] or '无（仅关键词命中，不改变认知场）'}")
    return report


# ── 社交互动接线的自愈（行为导向直连边的移除）────────────────

# 旧架构把 社交互动 直接接向行为节点，等于"社交输入天然获得回应/工具
# 资格"。重构后社交信号只改变认知场，回应资格由行为竞争产生。
STALE_SOCIAL_EDGES = [("社交互动", "LLM回答"),
                      ("社交互动", "文件操作"),
                      ("社交互动", "看屏幕")]


def repair_social_wiring(kg) -> list:
    """移除 社交互动 → 行为节点 的历史直连边（幂等，启动时自愈旧图）。"""
    removed = []
    for src, dst in STALE_SOCIAL_EDGES:
        if kg.get_edge(src, dst, "触发") is not None:
            kg.remove_edge(src, dst, "触发")
            removed.append(f"{src}-[触发]->{dst}")
    if removed:
        logger.info(f"[Social] 已移除行为导向直连边（检测≠行为决定）: {removed}")
    return removed
