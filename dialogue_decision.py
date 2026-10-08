# dialogue_decision.py — 行为竞争器（交流决策层重构 2026-09）
# ============================================================================
# 旧角色（已废弃）：独立规则决策器——ACT_PULL/EXPECT_PULL 固定公式算出标量
#   desire，过阈值即回应。那等于让本文件当"她该做什么"的独立大脑。
#
# 新角色：**图谱驱动的行为竞争器**。
#   1. 收集行为候选（图上的行为节点，人人有份，含 silence/explore）
#   2. 读图谱激活：先验边（出厂种子）+ 经验边（人格学习闭环写入）
#      + 实时激活（情境种子扩散进行为节点）
#   3. 施加系统级调制（§五 B 类）：共振/期待/极短输入/负反馈/刚说过话/
#      沉默倾向抑制/激素状态
#   4. 候选间竞争，胜者即行为
#   5. 应用硬约束（§五 C 类）：工具结果强制汇报 / 话题终止 / 探索打断门槛
#   6. 输出胜者 + 表达层（minimal/normal/question）+ 约束 + 全程 trace
#
# 分层（§八）：行为意图（respond/ask/empathize/share/continue/end/
#   acknowledge/elaborate/silence/explore）与表达方式（minimal/normal/
#   question/search）分离——未来加 joke/challenge/invite 只加候选，不加 if。
#
# 硬边界：本模块**零 LLM**；LLM 只在胜者产生后做语言实现。
# 沉默不是"没达到说话条件"，而是竞争中的一种行为结果（§七）。
# ============================================================================

import logging
import re
import time

from graph_model import ENGINE_PART_LABELS
from disposition_store import (
    BEHAVIORS, DIALOGUE_ACT_CONTEXT, DEFAULT_CONTEXT,
    BEHAVIOR_PRIOR_SEEDS, context_for_dialogue_act)

logger = logging.getLogger(__name__)

# 话题终止抑制的兜底时长（秒）。真实取值由调用方从运行时 config 传入。
DEFAULT_INHIBIT_SEC = 600.0

# 话题终止指令（言语行为级识别：directive + topic termination）
_TERM_RE = re.compile(
    r"停止.{0,4}话题|换个话题|换话题|别说了|别问.{0,3}了|打住|到此为止|不想聊(这个|天)?了?")

# ── 兼容回退先验（仅当图中无先验边时使用；正常路径全部读图）──
LEGACY_ACT_PULL = {
    "question": 0.85, "request": 0.90, "greeting": 0.60, "thanking": 0.50,
    "emotion_expression": 0.55, "sharing": 0.50, "opinion": 0.45,
    "information_statement": 0.40, "answer": 0.50, "suggestion": 0.45,
    "agreement": 0.20, "disagreement": 0.45, "backchannel": 0.15,
    "farewell": 0.35, "apology": 0.50, "comfort": 0.55,
}
# 沉默/其他行为的图外回退基线（正常时它们来自先验边）
FALLBACK_SILENCE = 0.10
FALLBACK_OTHER = 0.12

# ── 竞争权重与调制（系统级，B 类；非人格）──────────────────
W_PRIOR = 0.55          # 先验通道权重
W_LEARN = 0.45          # 经验通道权重（tendencies_for 已含实时激活混合）
BASE_SILENCE = 0.05     # 沉默候选的常驻底座（§七：沉默总在候选席上）
EXPLORE_SCORE_W = 0.60  # 探索候选：好奇心评分分量
EXPLORE_LIVE_W = 0.40   # 探索候选：行为:探索 节点实时激活分量
EXPLORE_MARGIN = 0.15   # C 类硬约束：探索须超出社会表达 urge 0.15 才可打断
RESO_W = 0.25           # 话题共振对表达类候选的调制
RESO_DAMP_INHIBIT = 0.3

EXPRESSIVE = {"respond", "ask", "acknowledge", "elaborate", "empathize",
              "share", "continue", "end"}
# 各调制器作用的候选集合
_DAMP_SHORT = {"respond", "share", "elaborate", "continue", "ask"}
_DAMP_NEG = {"respond", "ask", "share", "elaborate", "continue"}

# 行为 → 旧决策串映射（兼容 app.py 的分支；表达层单独输出）
_LEGACY_MAP = {"respond": "respond", "acknowledge": "respond",
               "elaborate": "respond", "empathize": "respond",
               "share": "respond", "continue": "respond", "end": "respond",
               "ask": "respond", "silence": "silence"}

# 状态：话题终止抑制。2026-09-22（§18.2 #10）：绑定 LockRegistry 后，
# 抑制期表达为一条 target_type="system" 的认知锁——带 expires_at、进
# 审计历史、可经 release_termination() 显式解除、/api/regulation 可见；
# system 锁不参与任何扩散拦截（见 cognitive_locks.TARGET_TYPES 注释）。
# 未绑定（离线单测/无调节层部署）回退进程级浮点（原语义不变）。
_inhibit_until = 0.0
_lock_registry = None
_INHIBIT_TARGET = "dialogue_topic_inhibit"


def bind_lock_registry(registry):
    """app 启动时注入 LockRegistry（regulation.locks）。None=回退浮点。"""
    global _lock_registry
    _lock_registry = registry


def _find_inhibit_lock():
    if _lock_registry is None:
        return None
    try:
        for lk in _lock_registry.list(target_type="system",
                                      target_id=_INHIBIT_TARGET):
            return lk
    except Exception as e:
        logger.warning(f"[DialogueDecision] 抑制锁查询失败(按无锁处理): {e!r}")
        return None
    return None


def mark_termination(inhibit_sec=None):
    """话题终止：记录抑制期，并抑制当前高激活的事件/话题节点。"""
    global _inhibit_until
    try:
        _sec = float(inhibit_sec)
    except (TypeError, ValueError):
        _sec = DEFAULT_INHIBIT_SEC
    if _sec <= 0:
        _sec = DEFAULT_INHIBIT_SEC
    expires = time.time() + _sec
    _inhibit_until = expires          # 浮点通道始终同步（回退与快速读）
    if _lock_registry is not None:
        try:
            lk = _find_inhibit_lock()
            meta = {"inhibit_sec": _sec}
            reason = f"话题终止抑制期（{_sec:.0f} 秒）"
            if lk is None:
                _lock_registry.create("system", _INHIBIT_TARGET,
                                      lock_type="non_blocking",
                                      reason=reason,
                                      source="system", expires_at=expires,
                                      metadata=meta)
            else:
                _lock_registry.update(lk["id"], expires_at=expires,
                                      reason=reason, enabled=True,
                                      metadata=meta)
        except Exception as e:
            logger.warning(f"[DialogueDecision] 抑制锁写入失败（浮点通道仍生效）: {e}")
    logger.info(f"[DialogueDecision] 话题终止：抑制期 {_sec:.0f} 秒")


def inhibition_active() -> bool:
    if _lock_registry is not None:
        lk = _find_inhibit_lock()
        if lk is not None:
            if not lk.get("enabled"):
                return False
            try:
                return time.time() < float(lk.get("expires_at") or 0.0)
            except (TypeError, ValueError):
                return False
    return time.time() < _inhibit_until


def release_termination(reason: str = "manual") -> dict:
    """显式解除话题终止抑制（§18.2 #10 补上缺失的解除 API + 审计）。"""
    _global_clear()
    if _lock_registry is not None:
        lk = _find_inhibit_lock()
        if lk is not None:
            out = _lock_registry.update(lk["id"], enabled=False,
                                        reason=f"解除（{reason}）")
            logger.info(f"[DialogueDecision] 抑制锁解除（{reason}）")
            return out
    return {"ok": True, "released": "float"}


def _global_clear():
    global _inhibit_until
    _inhibit_until = 0.0


def termination_state() -> dict:
    """当前抑制状态（前端/审计可读）。"""
    active = inhibition_active()
    out = {"active": active}
    if _lock_registry is not None:
        lk = _find_inhibit_lock()
        if lk is not None:
            out.update({"lock_id": lk.get("id"),
                        "expires_at": lk.get("expires_at"),
                        "reason": lk.get("reason"),
                        "remaining_s": max(
                            0.0, float(lk.get("expires_at") or 0.0)
                            - time.time())})
            return out
    out.update({"expires_at": _inhibit_until or None,
                "remaining_s": max(0.0, _inhibit_until - time.time())})
    return out


def inhibit_topics(kg, engine):
    """把当前高激活的事件/话题节点压下去（终止后旧话题不应继续作为回应依据）。"""
    # L1-DGR-02:锁序 engine→kg;get_topk 不得在 kg._lock 内调用
    topk, _ = engine.get_topk(k=15)
    with kg._lock:
        hit = []
        for n in topk:
            if (n.graph_space == "episodic" and n.activation > 0.2
                    and not n.id.startswith(("回答记录", "搜索记录", "CI_"))):
                n.activation *= 0.25
                hit.append(n.id)
        if hit:
            engine.mark_active(hit)
    return hit


# ── 图谱先验读取（先验边；缺失时回退常量）────────────────────

def _read_priors(kg, ctx_ids: list, da: str) -> dict:
    """情境-[先验]->行为 边 → {behavior: weight}（取各情境最大值）。"""
    priors = {}
    with kg._lock:
        for ctx in ctx_ids:
            if kg.get_node(ctx) is None:
                continue
            for key, bhv_node in BEHAVIORS.items():
                e = kg.get_edge(ctx, bhv_node, "先验")
                if e is not None:
                    w = float(e.weight)
                    if w > priors.get(key, 0.0):
                        priors[key] = w
    if not priors:
        # 回退：图里还没有先验边（如裸测试图）——用旧常量充当先验
        priors = {"respond": LEGACY_ACT_PULL.get(da, 0.40),
                  "silence": FALLBACK_SILENCE}
    return priors


def _live_activation(kg, behavior_key: str) -> float:
    node = kg.nodes.get(BEHAVIORS.get(behavior_key, ""))
    if node is None:
        return 0.0
    return min(1.0, float(getattr(node, "activation", 0.0) or 0.0) / 5.0)


# ── 主入口 ────────────────────────────────────────────────────

def dialogue_decide(parsed: dict, text: str, kg, engine,
                    tendencies: list, curiosity_active: bool,
                    last_outcome_negative: bool, last_expr_gap_s: float,
                    has_action_result: bool,
                    exploration: dict = None,
                    hormone: dict = None,
                    modulation=None,
                    action_tendencies: dict = None,
                    action_space=None) -> dict:
    """行为竞争：收集图谱候选 → 调制 → 竞争 → 硬约束 → 胜者。

    返回 decision/desire(兼容)/factors/constraints/tendencies/exploration
    + candidates（全部候选与激活来源）+ expression（表达层）。

    action_space（2026-09-20 行动重构）：竞争对象从"封闭行为类别"泛化为
    "具体行动候选"——除 LEGACY 10 键（保留为兼容通道与先验种子宿主）外，
    图谱中被点亮或有活跃供性源的**行动概念节点**（含 proposed 新概念）
    进入同一竞争：prior=先验/驱动/情绪边权（按活跃源），learned=激活边
    权（人格载体，与旧行为同一学习通道），live=节点实时激活。
    候选宇宙来自图谱扫描而非枚举；Top-K 是计算限制。
    胜者若是新概念，decision 串仍映射为 respond（语言竞技场契约不变），
    语义载荷走 action_concept/secondary_actions 字段与 OGCTX。

    modulation / action_tendencies（Drive 重构）：网络行为参数与派生行动
    倾向以乘子/因子进既有打分公式，不直接指定胜者；缺位=旧静态行为。
    """
    da = parsed.get("dialogue_act", "information_statement")
    exp = parsed.get("response_expectation", "medium")
    t = str(text or "").strip()
    inhibiting = inhibition_active()

    # ── 1. 图谱候选收集 ──────────────────────────────────
    ctx_ids = [context_for_dialogue_act(da)]
    priors = _read_priors(kg, ctx_ids, da)
    tend_map = {x.get("behavior"): x for x in (tendencies or [])}
    horm_n = 1.0
    if hormone:
        try:
            horm_n = max(0.6, min(1.4, 0.5 + 0.5 * float(hormone.get("factor", 1.0))))
        except (TypeError, ValueError):
            horm_n = 1.0
    # 网络→行为参数（有调制层时读参数表，缺位=静态常量）
    explore_margin = EXPLORE_MARGIN
    explore_rate = 1.0
    if modulation is not None:
        try:
            explore_margin = float(modulation.get(
                "behavior.explore_margin", EXPLORE_MARGIN))
            explore_rate = max(0.0, float(modulation.get(
                "behavior.exploration_rate", 1.0)))
        except Exception:
            pass

    candidates = {}
    for key in BEHAVIORS:
        prior = float(priors.get(key, 0.0))
        tn = tend_map.get(key)
        learned = float(tn.get("strength", 0.0)) if tn else 0.0
        live = float(tn.get("activation", 0.0)) / 5.0 if tn else _live_activation(kg, key)
        # 经验/实时分量受当前激素状态调制（§十：调增益，不做 if-dopamine-then）
        act = W_PRIOR * prior + W_LEARN * (learned + live) * (horm_n if key in EXPRESSIVE else 1.0)
        if key == "silence":
            act += BASE_SILENCE
        candidates[key] = {"activation": round(min(1.0, max(0.0, act)), 3),
                           "prior": round(prior, 3),
                           "learned": round(learned, 3),
                           "live": round(live, 3),
                           "kind": "behavior"}

    # ── 1b. 行动空间泛化：图谱行动概念进入同一竞争场（非枚举候选）──
    extra_expressive = set()
    extra_graph_trace = []
    if action_space is not None:
        try:
            topk_scale = afford_gain = None
            if modulation is not None:
                try:  # DMN 高→候选更宽（发散产生）；CEN 高→已学倾向放大（聚焦）
                    topk_scale = float(modulation.get(
                        "action_space.candidate_topk_scale", 1.0))
                    afford_gain = float(modulation.get(
                        "action_space.affordance_gain", 1.0))
                except Exception:
                    pass
            _cfa = (getattr(action_space, "config", None) or {}).get(
                "action_space") or {}
            _base_k = int(_cfa.get("candidate_top_k", 12))
            graph_cands = action_space.collect_action_candidates(
                top_k=max(3, int(_base_k * (topk_scale or 1.0))),
                affordance_gain=afford_gain)
        except Exception as _ace:
            # L1-DGR-04:整块吞掉把"收集失败"与"没有候选"合并,行为竞争
            # 少了整个维度却无痕——至少留 warning,竞争结果才有可诊断性。
            logger.warning(
                "[DialogueDecision] 行动候选收集失败，本轮退化为 LEGACY 竞争: %r",
                _ace)
            graph_cands = {}
        for key, c in graph_cands.items():
            if key in candidates:
                continue          # 与 LEGACY 词表同键的概念走词表通道
            if c.get("channel", "communication") != "communication":
                continue          # 具身行动概念归 ActionManager 竞技场
            prior = learned = 0.0
            for rel, (src, w, _sa) in (c.get("affordances") or {}).items():
                if rel == "激活":
                    learned = max(learned, w)
                elif rel == "先验" and src.startswith("情境"):
                    prior = max(prior, w)
                elif rel in ("驱动", "倾向", "诱发"):
                    prior = max(prior, 0.5 * w)   # 内驱/情绪/状态供性折算
            live = float(c.get("activation", 0.0)) / 5.0
            life_w = {"proposed": 0.5, "weakened": 0.3}.get(
                c.get("lifecycle", ""), 1.0)
            _is_expr = bool(c.get("expressive"))
            act = (W_PRIOR * min(1.0, max(0.0, prior))
                   + W_LEARN * (learned + live)
                   * (horm_n if _is_expr else 1.0)) * life_w
            if _is_expr:
                extra_expressive.add(key)
            candidates[key] = {
                "activation": round(min(1.0, max(0.0, act)), 3),
                "prior": round(min(1.0, prior), 3),
                "learned": round(learned, 3),
                "live": round(live, 3),
                "kind": "action_concept",
                "action_node": c.get("node_id", key),
                "lifecycle": c.get("lifecycle", ""),
            }
        extra_graph_trace = [
            (k, candidates[k]["activation"]) for k in graph_cands
            if k in candidates and k not in BEHAVIORS][:4]

    expr_set = set(EXPRESSIVE) | extra_expressive
    damp_short = (set(_DAMP_SHORT) | extra_expressive) & set(candidates)
    damp_neg = (set(_DAMP_NEG) | extra_expressive) & set(candidates)

    # ── 2. 系统级调制（B 类，作用于候选激活）─────────────
    factors = {}
    mods = {}
    if extra_graph_trace:
        factors["行动候选（图谱）"] = extra_graph_trace

    # 话题共振：当前认知焦点强度 → 表达类候选整体抬升
    # L1-DGR-02:锁序 engine→kg;get_topk 不得在 kg._lock 内调用
    topk, _ = engine.get_topk(k=10)
    with kg._lock:
        INFRA = {nid for nid, n in kg.nodes.items()
                 if n.label in ENGINE_PART_LABELS}
    acts = [n.activation for n in topk
            if n.id not in INFRA and n.activation > 0.1]
    resonance = min(1.0, (max(acts) / 3.0)) if acts else 0.0
    if inhibiting:
        resonance *= RESO_DAMP_INHIBIT
    for key in expr_set:
        candidates[key]["activation"] = round(
            min(1.0, candidates[key]["activation"] + RESO_W * resonance), 3)
    mods["话题共振"] = round(RESO_W * resonance, 3)
    factors["话题共振"] = round(resonance, 2)

    # 回应期待：高期待抬表达，低期待压表达
    expect_mod = {"high": 0.10, "medium": 0.0, "low": -0.10}.get(exp, 0.0)
    if expect_mod:
        for key in expr_set:
            candidates[key]["activation"] = round(
                max(0.0, candidates[key]["activation"] + expect_mod), 3)
        factors["expectation"] = exp

    # 文字投入度：超短回执本身几乎不产生交流需求（但 acknowledge 仍合身）
    if len(t) <= 2:
        for key in damp_short:
            candidates[key]["activation"] = round(
                max(0.0, candidates[key]["activation"] - 0.20), 3)
        candidates["acknowledge"]["activation"] = round(
            max(0.0, candidates["acknowledge"]["activation"] - 0.05), 3)
        candidates["silence"]["activation"] = round(
            min(1.0, candidates["silence"]["activation"] + 0.20), 3)
        factors["投入度"] = "极短"
    elif len(t) <= 4:
        for key in damp_short:
            candidates[key]["activation"] = round(
                max(0.0, candidates[key]["activation"] - 0.08), 3)

    # 上一轮负反馈：表达类受抑，沉默略得（一次拒绝≠讨厌任何行为，§九）
    if last_outcome_negative:
        for key in damp_neg:
            candidates[key]["activation"] = round(
                max(0.0, candidates[key]["activation"] - 0.15), 3)
        candidates["silence"]["activation"] = round(
            min(1.0, candidates["silence"]["activation"] + 0.10), 3)
        factors["用户负反馈"] = True

    # 刚说过话
    if last_expr_gap_s is not None and last_expr_gap_s < 90:
        for key in expr_set:
            candidates[key]["activation"] = round(
                max(0.0, candidates[key]["activation"] - 0.10), 3)
        factors["刚说过话"] = True

    # 沉默倾向（学到的）抑制表达类——沉默是学出来的行为，不是默认
    sl = tend_map.get("silence")
    if sl and float(sl.get("strength", 0)) > 0.05:
        damp = 0.30 * min(1.0, float(sl["strength"]))
        for key in expr_set:
            candidates[key]["activation"] = round(
                max(0.0, candidates[key]["activation"] - damp), 3)
        factors["沉默倾向抑制"] = round(damp, 3)

    # 好奇信号：待处理探索提问在场 → 追问/探索候选增强
    if curiosity_active:
        candidates["ask"]["activation"] = round(
            min(1.0, candidates["ask"]["activation"] + 0.10), 3)
        factors["好奇"] = True

    # 行动倾向（Drive×Network 派生）：社交接触倾向抬升追问（乘性因子，
    # 与其他调制同构——倾向改变竞争，不裁决竞争）
    _ask_t = float((action_tendencies or {}).get("ask_user", 0.0) or 0.0)
    if _ask_t > 0.35:
        candidates["ask"]["activation"] = round(
            min(1.0, candidates["ask"]["activation"] + 0.08 * _ask_t), 3)
        factors["社交倾向"] = round(_ask_t, 2)

    # ── 3. 探索候选进同一竞争场（§九：curiosity 经图通路，非外挂）──
    exp_cands = list((exploration or {}).get("candidates") or [])
    explore_live = _live_activation(kg, "explore")
    urge = max((c["activation"] for k, c in candidates.items() if k in expr_set),
               default=0.0)
    for c in exp_cands:
        if inhibiting:
            factors["探索落选"] = "话题终止抑制期内不探索"
            break
        act = round(min(1.0, explore_rate * (
            EXPLORE_SCORE_W * float(c.get("score", 0.0))
            + EXPLORE_LIVE_W * explore_live)), 3)
        key = c.get("action", "explore_ask")
        candidates[key] = {"activation": act, "prior": 0.0,
                           "learned": round(float(c.get("score", 0.0)), 3),
                           "live": round(explore_live, 3), "kind": "exploration",
                           "target": c.get("target"),
                           "win_threshold": c.get("win_threshold"),
                           "score": c.get("score")}

    # ── 4. 竞争 + 硬约束（C 类）──────────────────────────
    ranked = sorted(candidates.items(), key=lambda kv: -kv[1]["activation"])
    winner_key, winner = ranked[0]
    decision = None

    # 硬约束 1：工具结果待汇报 / 高回应期待 → 回应优先（探索可以下一轮再来）
    if has_action_result or exp == "high":
        decision = "respond"
        factors["强制回应"] = "动作结果待汇报" if has_action_result else "高回应期待"
        factors["探索落选"] = "正事优先，探索让位" if exp_cands else factors.get("探索落选")
    # 硬约束 2：探索打断门槛（即使赢得竞争，也须超表达 urge 一定幅度）
    elif winner_key in ("explore_ask", "explore_search"):
        if winner["activation"] >= urge + explore_margin:
            decision = winner_key
            factors["探索胜出"] = {"action": winner_key,
                                   "score": winner.get("score"),
                                   "target": winner.get("target"),
                                   "activation": winner["activation"],
                                   "urge": round(urge, 3)}
        else:
            factors["探索落选"] = {
                "action": winner_key, "score": winner.get("score"),
                "activation": winner["activation"], "urge": round(urge, 3),
                "margin": round(explore_margin, 3)}
            winner_key, winner = next(
                (k, v) for k, v in ranked if k in expr_set or k == "silence")
    # 硬约束 3：no_question 场合压 Conversational 追问——但只压**无学习支撑**
    # 的追问（防机械反问）。若 ask 是靠稳定学习倾向赢的（人格学习闭环的产物），
    # 约束让位于竞争结果（§十二：约束描述胜者，不决定胜者）。
    if decision is None and winner_key == "ask" and inhibiting is False:
        da_noq = da in ("sharing", "information_statement", "opinion",
                        "emotion_expression") and not curiosity_active
        learned_backed = float(winner.get("learned", 0.0)) >= 0.5
        if da_noq and not learned_backed:
            factors["追问落选"] = "分享/陈述场合禁机械追问（no_question）"
            winner_key, winner = next(
                (k, v) for k, v in ranked
                if k != "ask" and (k in expr_set or k == "silence"))
        elif da_noq and learned_backed:
            factors["追问胜出"] = "稳定学习倾向支撑的追问（非机械反问）"

    if decision is None:
        decision = _LEGACY_MAP.get(winner_key, "respond")
        if decision == "respond":
            # 表达层（§八）：行为与表达方式分离
            weak = winner["activation"] < 0.35 or exp == "low" or len(t) <= 4
            if winner_key in expr_set and weak:
                decision = "minimal"

    desire = round(urge, 3)          # 兼容字段：社会表达 urge（不再是裁决依据）
    # 探索落选留痕（§十四：竞争全程可解释）——凡有探索候选而未胜出，必留痕
    if (exp_cands and winner_key not in ("explore_ask", "explore_search")
            and "探索落选" not in factors):
        best_exp = max(exp_cands, key=lambda c: c.get("score", 0.0))
        factors["探索落选"] = {
            "action": best_exp.get("action"), "score": best_exp.get("score"),
            "activation": round(min(1.0, explore_rate * (
                EXPLORE_SCORE_W * float(best_exp.get("score", 0.0))
                + EXPLORE_LIVE_W * explore_live)), 3),
            "urge": round(urge, 3), "margin": round(explore_margin, 3)}
    factors["desire"] = desire
    factors["竞争胜者"] = {"behavior": winner_key,
                            "activation": winner["activation"],
                            "urge": round(urge, 3)}
    factors["候选数"] = len(candidates)
    if hormone:
        factors["激素调制"] = hormone

    # ── 5. 回应约束（描述胜者该怎么说；不决定行为）────────
    constraints = []
    if inhibiting:
        constraints += ["ack_only", "no_new_topic", "no_question"]
    if da in ("sharing", "information_statement", "opinion",
              "emotion_expression") and not curiosity_active \
            and decision not in ("explore_ask", "explore_search") \
            and not (winner_key == "ask"
                     and float(winner.get("learned", 0.0)) >= 0.5):
        constraints.append("no_question")
    if decision == "minimal":
        constraints.append("minimal_len")
    if exp == "low":
        constraints.append("minimal_len")

    logger.info(
        "[DialogueDecision] 竞争: winner=%s(%s) act=%.2f urge=%.2f "
        "candidates=%s mods=%s",
        winner_key, decision, winner["activation"], desire,
        {k: v["activation"] for k, v in ranked[:5]}, mods)

    _is_explore_win = winner_key in ("explore_ask", "explore_search")
    # 行动泛化输出（2026-09-20）：次级行动（组合表达的原料，OGCTX 可见，
    # 不另起执行——语言实现仍一次一句）+ 胜者概念节点 + 生命周期记录
    secondary = [k for k, v in ranked[1:5]
                 if v.get("kind") in ("behavior", "action_concept")
                 and k not in ("silence", winner_key)
                 and float(v.get("activation", 0)) > 0.25][:2]
    if action_space is not None and winner.get("kind") == "action_concept":
        try:
            action_space.touch(winner_key)
        except Exception:
            pass
    return {"decision": decision, "desire": desire, "factors": factors,
            "constraints": constraints, "tendencies": tendencies,
            "expression": ("question" if winner_key in ("ask", "explore_ask")
                           else "search" if winner_key == "explore_search"
                           else "minimal" if decision == "minimal" else "normal"),
            "winner_behavior": winner_key,
            "action_concept": winner.get("action_node",
                                         f"行为:{winner_key}"
                                         if winner_key in BEHAVIORS else ""),
            "secondary_actions": secondary,
            "candidates": [{"behavior": k, **v} for k, v in ranked],
            "exploration": {
                "candidates": exp_cands,
                "selected": winner_key if _is_explore_win else None,
                "target": winner.get("target") if _is_explore_win else None,
            }}
