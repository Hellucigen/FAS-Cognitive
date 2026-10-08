# action_resolver.py — 语义动作解析器（Action Intent 的唯一产地）
# ============================================================================
# 架构升级（2026-09-19）：统一"用户显式动作/工具指令"的解析通路。
#
#   自然语言
#     ↓ 否定优先（NEGATION_RE，沿用 minecraft.reflex 单一真源）
#     ↓ 动作候选识别
#         fast pattern（现有正则原样保留 → 高置信快捷入口，0 LLM）
#     ↓ miss
#         Action Concept 语义层：seed 表面形式 + 图谱/embedding 召回
#     ↓ 歧义（两候选接近 / 置信不足 / 缺必填参数）
#         LLM 消歧（可注入；Minecraft 反射快路径永不走 LLM）
#     ↓ 命令性门（§十七 误触发防线：叙事/第三人称/犹豫/疑问 ≠ 命令）
#     ↓ 参数绑定（动作识别与参数抽取解耦，§十四）
#   Action Intent = {action, parameters, polarity, confidence, source, ...}
#
# 图谱的角色（§五）：提供动作语义概念、关系与候选激活——不是执行开关。
# 任何层（含 fast pattern）产出的候选都必须过命令性门并形成 Intent，
# 执行由调用方（app → ActionManager / 内联执行器）裁决。
#
# 边界（§十一）：本模块只解析"用户命令"（SRC_USER）。它不读 drive/emotion，
# 不参与自主行为竞争；自主层产物（EAT/LIGHT/EXPLORE 的图谱激活）绝不
# 经由本层冒充用户命令。
# ============================================================================

import json
import logging
import re

import minecraft.reflex as _reflex
from action_concepts import (
    ACTION_CONCEPTS, ActionConcept, POLARITY_NEGATIVE, POLARITY_POSITIVE,
    REFLEX_ACTION_TO_CONCEPT, CONCEPT_TO_REFLEX_ACTION,
    concept_node_id, concepts_for_domain,
)

logger = logging.getLogger(__name__)

# 否定检测单一真源（§十：不破坏 NEGATION_RE）
NEGATION_RE = _reflex.NEGATION_RE

# 端口数字/槽位回指（CONNECT 槽位填解用；原 minecraft.session.handle_reply
# 的 PORT_RE/REUSE_RE 字符串表 2026-09-21 降级至此，判定语义改由
# "图上有无待填槽"驱动，不再是对整轮输入的独立行为枚举）
_PORT_DIGITS_RE = re.compile(r"(?<!\d)(\d{4,5})(?!\d)")
_PORT_ANAPHORA_RE = re.compile(r"刚才|上次|之前|原来|那个")

# ── 命令性证据（确定性启发式；§十七 误触发防线）────────────────

# 叙事/转述标记：动作词是"被讲述的事件"，不是对 FAS 说的命令
_STORY_RE = re.compile(r"有人|别人|人家|刚才|刚刚|之前|以前|上次|那天|昨天|"
                       r"看到|看见|听说|梦到|想起|记得|那时候|的人|都说|都这么|"
                       r"大家都|经常|总是|每次|往往")
# 第三人称主语（"他让我过来"）
_3P_RE = re.compile(r"^[他她它]|[他她它们](让|叫|要|说|又|还|在)|被.{0,4}(跟着|盯着)")
# 第一人称/过去式已发生叙述（"我看了一下屏幕"/"谢谢你帮我查询了那么多"）
_FIRST_PAST_RE = re.compile(
    r"我?.{0,6}?(看了|试过|过了|了一下|了一眼|了一遍|过了)"
    r"|(查询|搜索|查看|检查)了|查过了|搜过了|了(那么多|很多|不少)|^我刚|已经")
# 犹豫/自问（"不知道该怎么搜"）
_DELIB_RE = re.compile(r"该不该|要不要|是不是该|不知道|犹豫|纠结|考虑")
# 询问属性而非命令动作（"这个文件叫什么"）
_NAMEQ_RE = re.compile(r"叫什么|是什么|是哪|谁的|多少|为什么")
# 疑问句形
_QUESTION_RE = re.compile(r"[?？]|^(为什么|干嘛|干啥|怎么|哪里|哪个|是不是)")
# 祈使框架
_IMPER_HEAD_RE = re.compile(r"^(请|帮我|给我|麻烦|你帮我|你来|你去)")
_SECOND_PERSON_RE = re.compile(r"(^|[，,。 ])你(来|去|帮|把|先)?|帮我|给我|替我")

# 各层证据下限（fast 最宽松；越靠语义层越严格——召回更容易误触）
EVID_MIN_FAST = 0.45
EVID_MIN_SEED = 0.60
EVID_MIN_EMBED = 0.65

# 挖/攻击的量词与指代（参数绑定层；minecraft.reflex 保持原样以兼容既有测试）
_QTY_RE = re.compile(r"(?P<n>[一二两三四五六七八九十几\d]+)\s*(?:个|块|条|组|堆|份|颗)")
_DEMO_RE = re.compile(r"^(那个|这个|那只|这只|那张|那些|这些|一个|一只|那个?|那?个)")
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}

# 搜索查询清洗（原 app.py 内联逻辑迁入：参数绑定与执行解耦）
_SEARCH_STRIP_RE = re.compile(
    r"上网|网上|帮我|一下|搜搜|搜索|搜|查查|查|找找|找|资料|【.*?】"
    r"|[。？！?，,.!~\s]")
_SEARCH_DEMO_RE = re.compile(r"[这那一](首歌|部电影)")
_SEARCH_DEMO_MAP = {"这首歌": "歌曲", "那首歌": "歌曲", "一首歌": "歌曲",
                    "这部电影": "电影", "那部电影": "电影"}

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?|一|两|几)\s*(秒|分钟|会儿|分钟|会)")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def command_evidence(text: str, matched: str) -> tuple:
    """命令性评分（0~1）+ 证据列表。否定词不在这里判（polarity 先行）。

    原则：整句即动作短语/祈使框架 → 加分；叙事/转述/自问/疑问 → 重罚。
    """
    t = str(text or "").strip()
    ev = []
    score = 0.5
    mlen = len(str(matched or "").strip())
    if mlen and t == matched:
        score += 0.4
        ev.append("整句即动作表达")
    elif mlen and len(t) - mlen <= 3:
        score += 0.3
        ev.append("短句命令形")
    if _IMPER_HEAD_RE.search(t):
        score += 0.3
        ev.append("祈使框架(帮我/请/给我)")
    elif _SECOND_PERSON_RE.search(t):
        score += 0.15
        ev.append("第二指向(FAS)")
    penalty = False
    if _STORY_RE.search(t) or _3P_RE.search(t):
        score -= 0.6
        ev.append("叙事/转述（非对FAS的命令）")
        penalty = True
    if not penalty and _FIRST_PAST_RE.search(t):
        score -= 0.6
        ev.append("第一人称过去叙述")
    if _DELIB_RE.search(t):
        score -= 0.5
        ev.append("犹豫/自问")
    if _QUESTION_RE.search(t) or _NAMEQ_RE.search(t):
        score -= 0.25
        ev.append("疑问形")
    return _clamp(score, 0.0, 1.0), ev


def _intent(concept: ActionConcept, *, parameters, polarity, confidence,
            source, text, matched, evidence, reasons=None) -> dict:
    return {
        "action": concept.concept_id,
        "parameters": dict(parameters or {}),
        "polarity": polarity,
        "confidence": round(float(confidence), 3),
        "source": source,                      # regex | graph | llm
        "utterance": str(text or "")[:200],
        "matched": str(matched or ""),
        "domain": concept.domain,
        "executor": concept.executor,
        "evidence": list(evidence or []),
        "reasons": list(reasons or []),
    }


# ── 参数绑定（识别与抽取解耦，§十四）─────────────────────────

def _int_num(tok: str):
    tok = str(tok or "").strip()
    if tok.isdigit():
        return int(tok)
    if tok == "十":
        return 10
    if tok.endswith("十"):
        return 10 + (_CN_NUM.get(tok[-1]) or 0)
    if "十" in tok:
        a, b = tok.split("十", 1)
        return (_CN_NUM.get(a) or 1) * 10 + (_CN_NUM.get(b) or 0)
    return _CN_NUM.get(tok)


def bind_parameters(concept: ActionConcept, text: str,
                    base: dict = None) -> dict:
    """在已识别概念上绑定参数。base 来自上游层（如 reflex params/LLM）。"""
    p = dict(base or {})
    t = str(text or "")
    if concept.concept_id == "MINE":
        raw = str(p.get("target") or "")
        q = _QTY_RE.search(raw)
        if q:
            n = _int_num(q.group("n"))
            if n:
                p["quantity"] = n
            raw = (raw[:q.start()] + raw[q.end():]).strip()
        raw = _DEMO_RE.sub("", raw).strip()
        p["target"] = raw
        if raw:
            p["block"] = _reflex.BLOCK_ALIASES.get(raw, p.get("block") or raw)
    elif concept.concept_id == "ATTACK":
        raw = str(p.get("target") or "")
        raw = _DEMO_RE.sub("", raw).strip()
        p["target"] = raw
        if raw and not p.get("entity"):
            p["entity"] = _reflex.translate_entity(
                _reflex.HOSTILE_ALIASES.get(raw, raw))
    elif concept.concept_id == "MOVE":
        if p.get("seconds") is not None:
            p["duration"] = p["seconds"]
    elif concept.concept_id == "FOLLOW":
        m = _DURATION_RE.search(t)
        if m:
            p["duration"] = m.group(0)
        p.setdefault("target", "USER")
    elif concept.concept_id == "APPROACH":
        p.setdefault("target", "USER")
    elif concept.concept_id == "CONNECT":
        # 端口绑定：4-5 位数字；范围裁决留给消费方（槽位分支保等待，
        # session.connect 失败态），与 minecraft.session 的端口铁律同源
        m = _PORT_DIGITS_RE.search(t)
        if m:
            p["port"] = int(m.group(1))
    elif concept.concept_id == "SEARCH":
        q = re.sub(r"在网上", "", t)
        q = _SEARCH_STRIP_RE.sub("", q)
        q = _SEARCH_DEMO_RE.sub(
            lambda mm: _SEARCH_DEMO_MAP.get(mm.group(0), ""), q)
        if not q.strip():
            # 动词全被剥光 → 用原句兜底（不比现状差：搜索引擎自己会利用语境）
            q = re.sub(r"[【】]", "", t).strip()
        p["query"] = q[:30]
    elif concept.concept_id == "SCREEN_OBSERVE":
        p.setdefault("target", "SCREEN")
    return p


# ── 图谱召回索引（概念节点 + 表达节点）───────────────────────

class ConceptIndex:
    """从图谱读出 concept 候选召回表（表达节点/概念节点 → 概念 id）。

    图在这里只负责"语义候选激活"：命中≠执行，全部产物过命令性门与
    参数完整度后才允许成为 Intent（§五：图谱不是动作关键词词典）。
    """

    def __init__(self, kg):
        self.node_to_concept = {}
        self.concept_nodes = []
        try:
            nodes = list(kg.nodes.items())
        except Exception:
            nodes = []
        for nid, n in nodes:
            ea = getattr(n, "extra_attrs", {}) or {}
            if ea.get("type") == "action_concept":
                cid = ea.get("concept_id") or nid
                self.node_to_concept[nid] = cid
                self.concept_nodes.append(nid)
            elif ea.get("type") == "action_expression":
                cid = ea.get("concept_id")
                if cid in ACTION_CONCEPTS:
                    self.node_to_concept[nid] = cid


# ── 语义层：seed 表面形式 + embedding 召回 ───────────────────

def _semantic_candidates(concept: ActionConcept, text: str,
                         kg=None, embedder=None, index: ConceptIndex = None) \
        -> list:
    """返回 [(concept, sim, via, matched_expr)]，按 sim 降序。"""
    t = str(text or "").strip()
    out = []
    best_seed = None
    for expr in concept.seed_expressions:
        if t == expr:
            best_seed = (concept, 0.80, "seed_exact", expr)
            break
        if expr in t and (best_seed is None or len(expr) > len(best_seed[3])):
            best_seed = (concept, 0.62, "seed_sub", expr)
    if best_seed:
        out.append(best_seed)
    # 图谱/embedding 召回（只有注册了概念索引的域可用）
    if embedder is not None and kg is not None and index is not None:
        try:
            hits = embedder.search(t, top_k=12, min_similarity=0.5)
        except Exception as e:
            logger.debug(f"[ActionResolve] embedding 召回失败: {e}")
            hits = []
        for h in hits:
            cid = index.node_to_concept.get(h.get("node_id"))
            if cid != concept.concept_id:
                continue
            sim = float(h.get("similarity") or 0.0)
            out.append((concept, sim, "embed", h.get("node_id") or ""))
            break
    return out


# ── MC fast 层：复用 minecraft.reflex（否定优先已在其中）─────

def _mc_underlying_reflex_action(text: str):
    """在 reflex 输出（stop 可能被否定折叠）之外找回底层动作名。"""
    for act, pat in _reflex.ACTION_PATTERNS:
        if act == "stop":
            continue
        if pat.search(text):
            return act, pat.search(text).group(0)
    return None, ""


def _awaiting_port_slot(kg):
    """读图谱节点 "Minecraft会话" 上的槽位事实：是否正等待用户给端口。

    （2026-09-21 重构）原来这套判定住在 minecraft.session.handle_reply 的
    PORT_RE/CANCEL_RE/REUSE_RE 字符串表里——"输入→正则→行为"的封闭枚举。
    现在它是概念解析的一部分：会话节点记录 `state=awaiting_port`（事实），
    下一轮输入带数字，resolver 因为**图上有槽待填**而把它解读为端口。
    返回节点 extra_attrs（等待中）或 None。
    """
    try:
        node = kg.get_node("Minecraft会话") if kg is not None else None
    except Exception:
        return None
    if node is None:
        return None
    attrs = getattr(node, "extra_attrs", None) or {}
    return attrs if attrs.get("state") == "awaiting_port" else None


def resolve_minecraft_intent(text: str, *, kg=None, embedder=None,
                             index: ConceptIndex = None, action_space=None):
    """Minecraft 域解析（零 LLM 保证；slot → fast → reflex → seed/embed）。

    返回 (intent | None, info)：info 携带 rejected/refused/need_param 细节。
    need_param = 槽位缺失且该参数可问（askable）：不执行、不猜，
    把"向用户索取"作为结构化事实交给对话层措辞。
    """
    raw = str(text or "").strip()
    if not raw:
        return None, {}
    if index is None and kg is not None:
        index = ConceptIndex(kg)
    info = {}
    negated = bool(NEGATION_RE.search(raw))

    # ── 槽位分支：图谱说"正在等端口"，本轮输入按填槽解读 ──
    slot = _awaiting_port_slot(kg)
    if slot is not None:
        c = ACTION_CONCEPTS.get("CONNECT")
        if c is None:
            return None, info
        if negated:
            # 取消只有一个来源：显式否定（NEGATION_RE 单一真源）。
            # "等一下/还没开"不再被字符串表当成取消——它们就是"继续等待"。
            info["refused"] = {"concept": "CONNECT",
                               "reason": "否定：用户明确暂不进入（槽位保留）"}
            return None, info
        pm = _PORT_DIGITS_RE.search(raw)
        if pm:
            port = int(pm.group(1))
            if 1024 <= port <= 65535:
                return _intent(c, parameters={"port": port},
                               polarity=POLARITY_POSITIVE, confidence=0.92,
                               source="slot", text=raw,
                               matched=pm.group(1), evidence=[]), info
            # 格式裁决是工程约束（与 session.connect 同源范围）：如实反馈，
            # 槽位保持等待，用户可重试
            info["invalid_port"] = port
            return None, info
        if _PORT_ANAPHORA_RE.search(raw) and slot.get("last_port"):
            # "刚才那个"是对**图谱槽位数据**的回指：复用值从节点读，
            # 不是行为枚举——指代消解查记忆，不查字符串表
            return _intent(c, parameters={"port": int(slot["last_port"])},
                           polarity=POLARITY_POSITIVE, confidence=0.90,
                           source="slot", text=raw, matched="reuse",
                           evidence=[],
                           reasons=["复用会话节点槽位 last_port"]), \
                   {**info, "reused": True}
        info["awaiting_port"] = True   # 无数字无回指：继续等待（事实在节点上）
        return None, info

    reflex = _reflex.parse_reflex_command(raw)
    act = reflex.get("action")
    refused = reflex.get("refused")   # B1 修复：否定 dig/attack → 显式拒绝

    if refused:
        cid = REFLEX_ACTION_TO_CONCEPT.get(refused)
        if cid:
            info["refused"] = {"concept": cid,
                               "reason": reflex.get("reason", "否定优先")}
        return None, info

    if act and act != "none":
        # 底层概念：stop 可能由否定折叠而来（"别跟着我"→stop，FOLLOW+NEGATIVE）
        underlying = None
        matched = reflex.get("matched") or ""
        if act == "stop" and negated:
            ua, um = _mc_underlying_reflex_action(raw)
            if ua:
                underlying, matched = ua, um
        concept_id = underlying and REFLEX_ACTION_TO_CONCEPT.get(underlying) \
            or REFLEX_ACTION_TO_CONCEPT.get(act)
        concept = ACTION_CONCEPTS.get(concept_id or "")
        if concept is None:
            return None, {}
        polarity = POLARITY_NEGATIVE if negated else POLARITY_POSITIVE
        evid, ev_list = command_evidence(raw, matched)
        if not negated and evid < EVID_MIN_FAST:
            info["rejected"] = {"concept": concept.concept_id,
                                "evidence": round(evid, 2),
                                "reasons": ev_list}
            return None, info
        params = bind_parameters(concept, raw, reflex.get("params") or {})
        missing = [k for k in concept.required_params
                   if not params.get(k) and not (concept.dangerous and k == "target"
                                                 and negated)]
        if missing:
            info["rejected"] = {"concept": concept.concept_id,
                                "reason": f"缺参数 {missing}（安全拒绝，不猜目标）",
                                "reflex_reason": reflex.get("reason", "")}
            return None, info
        intent = _intent(concept, parameters=params, polarity=polarity,
                         confidence=0.80 + 0.18 * evid if not negated
                         else 0.92,
                         source="regex", text=raw, matched=matched,
                         evidence=ev_list)
        # 下游兼容：携带原 reflex dict，供 reflex_to_action / ACK 表使用
        intent["reflex"] = {**reflex, "params": params}
        return intent, info

    # ── 概念层快速入口（反射通道之外的 fast_via_concept 概念，如 CONNECT）──
    #    正则在这里只是"入口身份"：否定优先、参数绑定、缺槽 need_param
    #    全部由本层裁决——app 既不持有字符串表，也没有行为分支。
    for c in concepts_for_domain("minecraft"):
        if not c.fast_via_concept:
            continue
        m = c.matches_fast(raw)
        if not m:
            continue
        if negated:
            info["refused"] = {"concept": c.concept_id,
                               "reason": "否定优先：用户明确说不做"}
            return None, info
        params = bind_parameters(c, raw)
        missing = [k for k in c.required_params if not params.get(k)]
        if missing:
            if all(k in c.askable_params for k in missing):
                # 可问参数缺失 → 交给对话层执行"索取"（措辞由认知决定，
                # "缺哪个参数"是这里的结构化事实）；绝不猜值执行
                info["need_param"] = {"concept": c.concept_id,
                                      "params": missing,
                                      "reason": "槽位缺失：应向用户索取参数而非猜",
                                      "utterance": raw[:120]}
            else:
                info["rejected"] = {"concept": c.concept_id,
                                    "reason": f"缺参数 {missing}（安全拒绝，不猜目标）"}
            return None, info
        evid, ev_list = command_evidence(raw, m.group(0))
        if evid < EVID_MIN_FAST:
            continue   # 入口证据弱：不在这里裁决，落回语义层
        intent = _intent(c, parameters=params, polarity=POLARITY_POSITIVE,
                         confidence=0.84, source="regex", text=raw,
                         matched=m.group(0), evidence=ev_list)
        return intent, info

    # ── fast 未命中：语义层（seed → embed）──
    if negated:
        # 纯否定且无动作命中（"别这样"）→ 不猜动作
        return None, {}
    cands = []
    for c in concepts_for_domain("minecraft"):
        if not c.allow_semantic:
            continue
        cands.extend(_semantic_candidates(c, raw, kg, embedder, index))
    intent, info = _resolve_action_candidate(cands, raw, info,
                                  action_space=action_space)
    if intent is not None:
        # 语义层意图同样携带 reflex 视图：下游 ACK 表/兜底分发零改动
        intent["reflex"] = {
            "action": CONCEPT_TO_REFLEX_ACTION.get(intent["action"]),
            "params": dict(intent.get("parameters") or {}),
            "matched": intent.get("matched"),
            "negated": False,
            "reason": f"语义识别（{intent.get('source')}）"}
    return intent, info


# ── 语义候选裁决（seed/embed 通用）───────────────────────────

def _resolve_action_candidate(cands: list, raw: str, info: dict, llm=None,
                   action_space=None):
    """在行动概念候选中裁决一个（seed/embed 双通路）。

    它消费的是图谱候选（概念+相似度），不是任何 intent 枚举表——
    低置信/歧义交给 LLM 消歧，缺参数拒绝猜测。旧名 _pick_semantic
    易被误读为"查语义意图表"，2026-09-21 改名。
    """
    if not cands:
        return None, info
    cands.sort(key=lambda x: -x[1])
    top = cands[0]
    concept, sim, via, matched = top
    # 歧义=不同概念间的竞争（同一概念的 seed/embed 双通路不算歧义）
    second = max((c[1] for c in cands[1:] if c[0] is not concept), default=0.0)
    gate = EVID_MIN_SEED if via.startswith("seed") else EVID_MIN_EMBED
    evid, ev_list = command_evidence(raw, matched)
    ambiguous = (second > 0 and sim - second < 0.06) or \
                (via == "embed" and sim < 0.62)
    weak = evid < gate
    if ambiguous or weak:
        # §九：低置信或歧义才交给 LLM；LLM 不可用（MC 零延迟域/未注入）
        # 时如实记录，绝不猜着执行。embedding 太弱的候选连 LLM 都不打扰。
        if llm is None or sim < 0.55:
            if ambiguous:
                info["ambiguous"] = {"top": concept.concept_id, "sim": sim,
                                     "second_margin": round(sim - second, 3),
                                     "via": via}
            else:
                info["rejected"] = {"concept": concept.concept_id, "evidence":
                                    round(evid, 2), "reasons": ev_list,
                                    "via": via}
            return None, info
        out = _llm_disambiguate(list({c.concept_id: c for c, *_ in cands}.values()),
                                raw, llm, action_space=action_space,
                                info=info)
        if out is None:
            info["ambiguous" if ambiguous else "rejected"] = {
                "top": concept.concept_id, "sim": sim, "via": via,
                "llm": "no_result"}
            return None, info
        c2, p2, pol2 = out
        params = bind_parameters(c2, raw, p2)
        missing = [k for k in c2.required_params if not params.get(k)]
        if missing or pol2 == POLARITY_NEGATIVE:
            # LLM 说否定/说不全参数 → 不猜执行（否定语义留给 fast 层处理）
            info["ambiguous"] = {"top": c2.concept_id, "llm": "not_actionable",
                                 "missing": missing, "polarity": pol2}
            return None, info
        intent = _intent(c2, parameters=params, polarity=pol2,
                         confidence=0.72, source="llm", text=raw,
                         matched="llm", evidence=ev_list)
        _write_back_surface(action_space, raw, c2.concept_id)
        return intent, info
    conf = (0.72 + 0.2 * (evid - 0.6)) if via.startswith("seed") \
        else (0.6 + max(0.0, sim - 0.55) + 0.2 * max(0.0, evid - 0.65))
    if via.startswith("seed"):
        conf = _clamp(conf, 0.6, 0.88)
    else:
        conf = _clamp(conf, 0.55, 0.85)
    params = bind_parameters(concept, raw, {})
    missing = [k for k in concept.required_params if not params.get(k)]
    if missing:
        if all(k in concept.askable_params for k in missing):
            # 与 fast 层同一条铁律：可问参数缺失 = 索取，不是猜也不是硬拒
            info["need_param"] = {"concept": concept.concept_id,
                                  "params": missing,
                                  "reason": "语义候选槽位缺失：向用户索取，不猜"}
        else:
            info["rejected"] = {"concept": concept.concept_id,
                                "reason": f"语义候选缺参数 {missing}（拒绝执行）"}
        return None, info
    if conf < concept.min_confidence:
        info["rejected"] = {"concept": concept.concept_id,
                            "confidence": round(conf, 2),
                            "reason": "置信度低于执行阈值"}
        return None, info
    intent = _intent(concept, parameters=params, polarity=POLARITY_POSITIVE,
                     confidence=conf, source="graph", text=raw,
                     matched=matched, evidence=ev_list,
                     reasons=[f"via={via} sim={sim:.2f}"])
    _write_back_surface(action_space, raw, concept.concept_id)
    return intent, info


def _write_back_surface(action_space, raw: str, concept_id: str):
    """语义命中回写表面形式（行动重构 2026-09-20）：让"词表只是 seed"
    真的成立——每次泛化成功都把这句话登记成概念的别名边，
    下次同类口语在 seed/别名层直接命中，无需再走 embedding。"""
    if action_space is None or not raw:
        return
    try:
        action_space._register_surface(raw, concept_id)
        action_space.touch(concept_id)
    except Exception as e:
        logger.debug(f"[ActionResolve] 表面回写跳过: {e}")


def _domain_of(cands: list) -> str:
    return cands[0][0].domain if cands else "desktop"


def _llm_disambiguate(concepts: list, raw: str, llm, action_space=None,
                      info: dict = None):
    """歧义消解（仅歧义时调用）。llm(prompt:str)->str(JSON) 由 app 注入。

    返回 (concept, params, polarity) 或 None（不猜）。
    行动重构（2026-09-20）：LLM 可以是新行动概念的**来源之一**——候选全
    不合拍时允许提议（is_new）；提议只入图为 proposed 概念（无 executor=
    能力缺口，能理解≠能做，本次不产执行意图），学习/候选通道照常。
    """
    try:
        payload = {
            "utterance": raw,
            "candidates": [{"concept": c.concept_id, "name": c.name_zh,
                            "description": c.description} for c in concepts],
            "instruction": ("从 candidates 中选最贴合的动作概念并抽参数；"
                            "若没有合适的且这确实是祈使式动作请求，可输出 "
                            '{"is_new": true, "new_action": "≤8字动词短语", '
                            '"description": "一句话说清做什么"}。'),
        }
        out = llm(json.dumps(payload, ensure_ascii=False))
        data = json.loads(str(out or "").strip()
                          .removeprefix("```json").removeprefix("```")
                          .removesuffix("```").strip())
        if not isinstance(data, dict):
            return None
        if data.get("is_new") and action_space is not None:
            try:
                key = action_space.propose_action(
                    str(data.get("new_action") or raw)[:16],
                    channel="communication",
                    description=str(data.get("description") or ""),
                    evidence=raw)
                if info is not None and key:
                    info["proposed_action"] = key
            except Exception:
                pass
            return None
        cid = str(data.get("concept") or "").strip()
        if not data.get("is_command", False) or cid not in ACTION_CONCEPTS:
            return None
        pol = (POLARITY_NEGATIVE
               if str(data.get("polarity") or "").upper() == "NEGATIVE"
               else POLARITY_POSITIVE)
        return ACTION_CONCEPTS[cid], (data.get("parameters") or {}), pol
    except Exception as e:
        logger.debug(f"[ActionResolve] LLM 消歧失败: {e}")
        return None


# ── 桌面域（web/file/screen）解析 ────────────────────────────

def resolve_desktop_intents(text: str, *, kg=None, embedder=None,
                            index: ConceptIndex = None, llm=None,
                            action_space=None):
    """返回 {concept_id: intent}（可执行）与 info["rejected"/"ambiguous"]。
    action_space（行动重构 2026-09-20）：语义命中回写表面形式；LLM 歧义
    通道可提议新行动概念（proposed 入图，不产执行意图）。"""
    raw = str(text or "").strip()
    intents = {}
    info = {"rejected": {}, "ambiguous": {}, "refused": {}}
    if not raw:
        return intents, info
    if index is None and kg is not None:
        index = ConceptIndex(kg)
    negated = bool(NEGATION_RE.search(raw))
    concepts = [c for c in concepts_for_domain("desktop")]
    for c in concepts:
        m = c.matches_fast(raw)
        if m:
            if negated:
                info["refused"][c.concept_id] = {
                    "concept": c.concept_id,
                    "reason": "否定优先：不执行显式工具动作"}
                continue
            matched = m.group(0) or "".join(
                g for g in m.groups() if g)
            evid, ev_list = command_evidence(raw, matched)
            if evid < EVID_MIN_FAST:
                info["rejected"][c.concept_id] = {
                    "concept": c.concept_id, "evidence": round(evid, 2),
                    "reasons": ev_list}
                continue
            params = bind_parameters(c, raw, {})
            missing = [k for k in c.required_params if not params.get(k)]
            if missing:
                info["rejected"][c.concept_id] = {
                    "concept": c.concept_id,
                    "reason": f"缺参数 {missing}"}
                continue
            intents[c.concept_id] = _intent(
                c, parameters=params, polarity=POLARITY_POSITIVE,
                confidence=0.80 + 0.18 * evid, source="regex", text=raw,
                matched=matched, evidence=ev_list)
            continue
        # fast 未命中 → 语义层（同域互斥裁决：一次收集全部候选）
    if not negated:
        cands = []
        for c in concepts:
            if not c.allow_semantic or c.concept_id in intents:
                continue
            cands.extend(_semantic_candidates(c, raw, kg, embedder, index))
        if cands and not intents:
            intent, sub = _resolve_action_candidate(cands, raw, info, llm=llm,
                                         action_space=action_space)
            if intent is not None:
                intents[intent["action"]] = intent
            elif sub.get("rejected"):
                info["rejected"]["semantic"] = sub["rejected"]
            elif sub.get("ambiguous"):
                info["ambiguous"]["semantic"] = sub["ambiguous"]
    return intents, info


# ── 统一入口 ─────────────────────────────────────────────────

def resolve_turn_intents(text: str, *, kg=None, embedder=None, llm=None,
                         user_name: str = None, action_space=None):
    """一轮用户输入的全部显式动作意图（桌面三类 + 调用方可另用 MC 入口）。"""
    index = ConceptIndex(kg) if kg is not None else None
    intents, info = resolve_desktop_intents(text, kg=kg, embedder=embedder,
                                            index=index, llm=llm,
                                            action_space=action_space)
    info["index_ready"] = index is not None
    return intents, info


def describe_intent(intent: dict) -> str:
    if not intent:
        return "none"
    pol = "" if intent.get("polarity") == POLARITY_POSITIVE else "不"
    p = intent.get("parameters") or {}
    p_str = " ".join(f"{k}={v}" for k, v in p.items() if v)
    return (f"{pol}{intent.get('action')}"
            f"{'（' + p_str + '）' if p_str else ''}"
            f" src={intent.get('source')} conf={intent.get('confidence')}")


# ── 桥接：Action Intent → 现有执行管路（增量兼容，§十五）─────

def intent_to_minecraft_flow(intent: dict, user_name: str = "user",
                             current_action_type: str = None):
    """把 MC 域 Intent 落到现有 ActionNode/执行通路。

    返回 (spec | None, flow_info)：
      - POSITIVE → 复用 action_intents.reflex_to_action（执行映射单一真源）
      - NEGATIVE FOLLOW/APPROACH/MOVE/JUMP → stop（现状折叠语义不变）
      - NEGATIVE MINE/ATTACK → 若当前正在同类动作 → stop；否则拒绝执行
    """
    if not intent:
        return None, {"flow": "none"}
    from action_intents import reflex_to_action
    if intent.get("polarity") == POLARITY_NEGATIVE:
        concept = intent.get("action")
        if concept in ("FOLLOW", "APPROACH", "MOVE", "JUMP", "STOP"):
            spec = reflex_to_action({"action": "stop", "params": {}},
                                    user_name=user_name)
            return spec, {"flow": "negation_stop", "concept": concept}
        if concept in ("MINE", "ATTACK"):
            busy_family = {"MINE": "gather_resource",
                           "ATTACK": "attack_entity"}.get(concept)
            if current_action_type and current_action_type == busy_family:
                spec = reflex_to_action({"action": "stop", "params": {}},
                                        user_name=user_name)
                return spec, {"flow": "negation_stop_current",
                              "concept": concept}
            return None, {"flow": "refused", "concept": concept,
                          "reason": "否定：不做该动作"}
        return None, {"flow": "refused", "concept": concept}
    refl = intent.get("reflex") or {
        "action": CONCEPT_TO_REFLEX_ACTION.get(intent.get("action")),
        "params": intent.get("parameters") or {}}
    spec = reflex_to_action(refl, user_name=user_name)
    if spec is None:
        return None, {"flow": "unmapped"}
    return spec, {"flow": "execute"}
