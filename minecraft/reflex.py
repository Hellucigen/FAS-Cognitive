# minecraft.reflex.py — 游戏内指令解析（纯函数，零 LLM）
# ============================================================================
# 为什么单独成文件：原来这段解析长在 app.py 的请求处理里，用一串位置分组
# 硬凑参数，结果出现了三类真 bug（都来自用户实际游玩）：
#
#   1. **否定句被当成肯定句**：用户说"不用跟着我了"，正则从里面匹配出"跟着我"
#      → 执行 follow_player（他开始跟了），而 LLM 的回答是"好，不跟了"。
#      嘴说停、身在跟。所以现在**第一步就判否定**：否定词 + 动作 → 走停止分支。
#   2. **分组错位**：`group(2) or group(3)` 取的是"秒数"而不是挖掘目标 →
#      dig_block("") → bot 返回 unknown_block；更糟的是 attack("") →
#      **攻击最近的非玩家实体**（可能打到用户的宠物/鸡）。
#      现在每个动作按自己的具名语义解析，取不到目标就如实拒绝，绝不"就近挑一个"。
#   3. **秒数解析**：`float(group(1))` 里 group(1) 是方向字（前/后/左/右）→
#      ValueError → 整轮回答变成"[回答生成失败]"。现在秒数独立用正则提取。
#
# 本模块只做解析（纯函数、可测试），执行仍在 app.py（调 mc_actions/mc_move）。
# ============================================================================

import re

NEGATION_RE = re.compile(r"不用|不要|别再|别|甭|停止|取消|不[要想用]|无需")

# 常见生物 中文名→游戏英文名（bot 侧 /attack /goto 按英文名匹配；
# 用户的自然说法是中文，必须翻译，否则永远 no_target——实测 bug）
ENTITY_ZH_EN = {
    "僵尸": "zombie", "骷髅": "skeleton", "蜘蛛": "spider", "洞穴蜘蛛": "cave_spider",
    "苦力怕": "creeper", "爬行者": "creeper", "末影人": "enderman", "女巫": "witch",
    "蝙蝠": "bat", "鱿鱼": "squid", "鲑鱼": "salmon", "三文鱼": "salmon",
    "鳕鱼": "cod", "热带鱼": "tropical_fish", "猪灵": "piglin",
    "僵尸猪灵": "zombified_piglin", "溺尸": "drowned", "僵尸村民": "zombie_villager",
    "牛": "cow", "猪": "pig", "羊": "sheep", "鸡": "chicken", "马": "horse",
    "狼": "wolf", "猫": "cat", "村民": "villager", "铁傀儡": "iron_golem",
}

def translate_entity(name: str) -> str:
    """中文实体名 → 英文游戏名（未知则原样返回，由 bot 侧模糊匹配）。"""
    n = str(name or "").strip()
    return ENTITY_ZH_EN.get(n, n)

# 动作模式：每个分支用**具名组**，不再依赖位置
# attack 有两条：把字句（"把僵尸杀掉"——宾语在动词前）必须在通用式之前
ACTION_PATTERNS = [
    ("follow", re.compile(r"(跟着我|跟上我|跟我走|跟我来|随我来|一起走)")),
    ("approach", re.compile(r"(过来|到我这边|来我这儿|到我这里|向我走来|向我走|朝我走来)")),
    ("stop", re.compile(r"(停下|别动|站住|停一下|别跟了|停止移动)")),
    ("jump", re.compile(r"(跳一下|跳一个|跳跳|蹦一下)")),
    ("dig", re.compile(r"挖(?:一?个|一些|点|些|掉)?(?P<target>[\u4e00-\u9fa5]{1,6})")),
    ("attack", re.compile(r"(?:把|将)(?:那|这)?(?:一?只|个)?(?P<target>[\u4e00-\u9fa5A-Za-z]{1,8})(?:杀掉|杀死|干掉|打死|打一下|打)")),
    ("attack", re.compile(r"(?:攻击|打死|杀掉|杀了|干掉|打)(?P<target>[\u4e00-\u9fa5A-Za-z]{0,8})")),
    ("move", re.compile(r"向?(?P<dir>前|后|左|右)走|(?P<dir2>前进|后退|左移|右移)")),
]
SECONDS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*秒")

# 方块中文名 → minecraft 名（与 Action/minecraft.BLOCK_MAP 同源；表在这里也留一份，
# 因为解析层不该依赖动作层的导入链）
BLOCK_ALIASES = {
    "石头": "stone", "圆石": "cobblestone", "泥土": "dirt", "沙子": "sand",
    "木头": "oak_log", "原木": "oak_log", "橡木": "oak_log", "煤矿": "coal_ore",
    "铁矿": "iron_ore", "金矿": "gold_ore", "钻石矿": "diamond_ore",
}
# 已知敌对生物（攻击时用于提示；不限制用户指定其它目标，但**绝不**替用户挑目标）
HOSTILE_ALIASES = {
    "僵尸": "zombie", "骷髅": "skeleton", "苦力怕": "creeper", "爬行者": "creeper",
    "蜘蛛": "spider", "末影人": "enderman", "女巫": "witch", "史莱姆": "slime",
}
DIR_WORDS = {"前": "forward", "后": "back", "左": "left", "右": "right"}


def parse_reflex_command(text: str) -> dict:
    """把一句游戏内指令解析成结构化动作。

    返回 {action, params, matched, negated, reason}；action="none" 表示没识别到指令。
    否定 + 移动/跟随类动作 → action="stop"（语义上"别做"就等于"停"）。
    """
    raw = str(text or "").strip()
    if not raw:
        return {"action": "none", "reason": "空输入"}

    negated = bool(NEGATION_RE.search(raw))
    seconds = None
    m_sec = SECONDS_RE.search(raw)
    if m_sec:
        try:
            seconds = float(m_sec.group(1))
        except ValueError:
            seconds = None

    for action, pat in ACTION_PATTERNS:
        m = pat.search(raw)
        if not m:
            continue
        matched = m.group(0)
        # ── 否定优先：说"别/不用"就等于要做相反的事（停） ──
        if negated and action in ("follow", "approach", "move", "jump"):
            return {"action": "stop", "params": {}, "matched": matched,
                    "negated": True,
                    "reason": f"识别到否定（{raw}）→ 执行停止，而不是「{matched}」"}
        # 否定 + 挖/攻击（安全修复 2026-09-19）：没有"反着挖/反着打"的语义——
        # "别挖这个/不要攻击他"必须**拒绝执行**，而不是照常 dig/attack。
        # action=none 保证所有现有调用方（含未迁移通路）都不会执行；
        # refused 字段供概念层（action_resolver）产出 NEGATIVE Intent 留痕。
        if negated and action in ("dig", "attack"):
            return {"action": "none", "params": {}, "matched": matched,
                    "negated": True, "refused": action,
                    "reason": f"识别到否定（{raw}）→ 不会执行「{matched}」"}

        if action == "move":
            key = m.group("dir") or ""
            dir2 = m.group("dir2") or ""
            direction = DIR_WORDS.get(key) or {"前进": "forward", "后退": "back",
                                               "左移": "left", "右移": "right"}.get(dir2)
            return {"action": "move", "matched": matched, "negated": negated,
                    "params": {"direction": direction, "seconds": seconds or 1.0},
                    "reason": ""}
        if action == "dig":
            target = (m.group("target") or "").strip()
            if not target:
                return {"action": "none", "matched": matched,
                        "reason": "没听清要挖什么（需要具体的方块名）"}
            return {"action": "dig", "matched": matched, "negated": negated,
                    "params": {"target": target,
                               "block": BLOCK_ALIASES.get(target, target)},
                    "reason": ""}
        if action == "attack":
            target = (m.group("target") or "").strip()
            # 去掉指示词/量词（"那个僵尸"→"僵尸"）
            target = re.sub(r"^(那个|这个|那只|这只|一个|一只|只|个)", "", target)
            if not target:
                # 关键安全线：绝不"就近攻击一个"。用户没点名就拒绝。
                return {"action": "none", "matched": matched,
                        "reason": "没点名攻击目标——不会替你随便打附近的东西"}
            hostile = HOSTILE_ALIASES.get(target)
            # 中文生物名 → 英文游戏名（bot /attack 按英文名匹配，不翻译必 no_target）
            entity = translate_entity(hostile or target)
            return {"action": "attack", "matched": matched, "negated": negated,
                    "params": {"target": target, "entity": entity,
                               "known_hostile": bool(hostile)},
                    "reason": "" if hostile else f"{target} 不在已知敌对生物表里（按你指定的目标执行）"}
        return {"action": action, "params": {}, "matched": matched,
                "negated": negated, "reason": ""}

    return {"action": "none", "negated": negated, "reason": "没有识别到游戏指令"}


def describe(parsed: dict) -> str:
    """给日志/回答用的短描述（不编造：只说解析结果）。"""
    a = parsed.get("action")
    p = parsed.get("params") or {}
    if a == "move":
        return f"移动 {p.get('direction')} {p.get('seconds')}s"
    if a == "dig":
        return f"挖掘 {p.get('target')}({p.get('block')})"
    if a == "attack":
        return f"攻击 {p.get('target')}({p.get('entity')})"
    return a or "none"
