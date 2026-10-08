# cognitive_complexity.py — 输入复杂度自适应估计器（Level 1 / Level 2）
# ============================================================================
# 目标（延迟优化规范 §二/§十六）：
#   不是"三次调用 → 一次调用"，而是**让不同认知任务使用不同计算预算**。
#   所有输入仍然完整进入 FAS 认知体系（感知 → 图 → 激活 → 决策 → 行动/回应）；
#   被优化的是认知过程本身的计算粒度与 LLM 调用方式。
#
#   Level 1（轻认知）：短输入 / 单意图 / 无需上下文推理
#     → 短 prompt + 快速小模型 + 一次调用完成语义解析与决策要素
#     → 输出结构化结果（speech_act / intent / target / urgency /
#        needs_reply / needs_action），后续照常进入图激活与行动竞争
#   Level 2（全认知）：长输入 / 多意图 / 条件与顺序 / 需要上下文
#     → 完整解析 prompt + 强模型 + 允许多意图与推理
#     → 产出 intentions 列表进入目标队列，由 Action 层逐步执行
#
# 本模块是纯函数估计器：零 LLM、零图谱写入，只做"选哪条路"的判断。
# 估计依据全部可解释（返回 components），前端/日志可直接展示。
# ============================================================================

import re

# Level 1 的语义长度上限（"概念单位"数）。注意不是字符数——
# "minecraft"/"51027" 这类 ASCII 串各算 1 个单位（按词计），否则
# "来玩minecraft吧，端口号是51027" 会被字母数撑到 22 误判为复杂输入，
# 一次本可秒回的会话指令被拖进全管线。
L1_MAX_LEN = 16

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def semantic_length(text: str) -> int:
    """语义单位数 = CJK 字符数 + ASCII 词数（游戏聊天输入的真实复杂度度量）。"""
    t = str(text or "")
    return len(_CJK_RE.findall(t)) + len(_ASCII_WORD_RE.findall(t))

# 条件/顺序/假设等复杂语义标记：出现即升 Level 2
_COMPLEX_MARKERS = [
    "如果", "要是", "假如", "的话", "然后", "接着", "再", "先", "之后",
    "最好", "顺便", "或者", "一边", "同时", "等.*再", "回来", "回来之前",
    "不然", "否则", "尽量", "看看有没有", "有没有.*就",
]
_COMPLEX_RE = re.compile("|".join(_COMPLEX_MARKERS))

# 多意图信号：一句话里出现多个动作动词（挖了又要、跟了又停）
_ACTION_VERB_RE = re.compile(
    r"跟着|过来|停下|别动|挖|采|砍|收集|拿|放|放好|建造|搭|做一|合成|制作|"
    r"攻击|打|杀|吃|喝|睡觉|回|去|走|探索|看看|检查|跟随|帮我|找|烧|熔")

# 生存/状态类词汇（含上下文依赖判断："你现在血量不高"）
_STATE_RE = re.compile(r"血量|饥饿|生命|危险|小心|晚上|天黑|怪物|苦力怕|僵尸|骷髅")

# 疑问/指代（"这个是什么""你看那里"）——仍是 Level 1（短、单意图）
_DEICTIC_RE = re.compile(r"这个|那个|这里|那里|它|什么")


def estimate_complexity(text: str, graph_known_terms: int = 0) -> dict:
    """估计输入复杂度，返回 {level, components, reason}。

    graph_known_terms: 输入解析出的实体中图里已存在的数量（调用方可选提供，
    用于判断"是否在谈图里的事"，未知实体多 → 需要解释器 → Level 2）。
    """
    t = str(text or "").strip()
    slen = semantic_length(t)
    comps = {
        "length": len(t),
        "semantic_units": slen,
        "markers": sorted(set(m for m in _COMPLEX_MARKERS if re.search(m, t)))[:6],
        "action_verbs": len(set(_ACTION_VERB_RE.findall(t))),
        "state_refs": bool(_STATE_RE.search(t)),
        "deictic": bool(_DEICTIC_RE.search(t)),
        "clauses": len(re.findall(r"[，。！？!?；;]", t)) + 1,
        "known_terms": graph_known_terms,
    }

    reasons = []

    # ── 直接 Level 2 的信号 ──
    # 1) 语义长度超过短句上限（ASCII 按词计，字母数不虚增）
    if slen > L1_MAX_LEN:
        reasons.append(f"语义单位 {slen} > {L1_MAX_LEN}")
    # 2) 条件/顺序/复合语义标记（"先…再…"、"如果…就…"）
    if comps["markers"]:
        reasons.append(f"复合语义标记 {comps['markers']}")
    # 3) 多个不同动作动词（多意图）
    if comps["action_verbs"] >= 2:
        reasons.append(f"{comps['action_verbs']} 个动作动词（疑似多意图）")
    # 4) 多分句
    if comps["clauses"] >= 3:
        reasons.append(f"{comps['clauses']} 个分句")
    # 5) 状态类词汇（生存判断依赖上下文推理）
    if comps["state_refs"]:
        reasons.append("包含生存/状态语义")

    level2 = bool(reasons)

    # ── Level 1 的信号（仅在无 Level 2 信号时成立）──
    if not level2:
        if slen <= L1_MAX_LEN:
            reasons.append(f"短输入（{slen} 个语义单位，单句单意图）")
        else:
            reasons.append("无复合语义标记，按轻认知处理")

    return {
        "level": 2 if level2 else 1,
        "components": comps,
        "reasons": reasons,
    }
