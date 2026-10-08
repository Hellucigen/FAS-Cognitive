# test_curiosity_refactor.py — 好奇心机制重构回归护栏（2026-09）
# ============================================================================
# 锁住新架构的核心不变量（对应 16 条设计原则）：
#   P1 未知≠好奇        — 信号经调制，弱场合注入≈0，"未知"不保证任何行为
#   P3 提问非默认        — 候选必须赢过回应欲望才执行；"好奇但不行动"合法
#   P4 好奇是持续驱动力   — CuriosityDrive 多因素；兴趣衰减不清零
#   P5 统一行为竞争      — Ask intent 只来自竞争结果，无旁路
#   P8 允许不行动        — pending_inquiry 可被忽略（答非所问→过期，兴趣保留）
#   P9 兴趣可持续        — 被回答后 level 衰减保留；链式兴趣可能
#   图驱动门槛          — 信号没扩散到行为节点 = 无候选
#   存档兼容            — bootstrap 幂等对齐，不删节点不重建
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_curiosity_refactor.py
# ============================================================================

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


from graph_model import KnowledgeGraph, Node, Edge
import config as cfgmod
import curiosity_engine as ce
from drive_engine import DriveEvaluator
from dialogue_decision import dialogue_decide
from actions.action_engine import ActionSelector

CONFIG = dict(cfgmod.DEFAULT_CONFIG)


class FN:
    def __init__(s, id, act=0.0, space="semantic"):
        s.id, s.activation, s.graph_space = id, act, space
        s.extra_attrs = {}
        s.label = "declarative-semantic"


class FakeEngine:
    _running = False

    def __init__(s):
        s.topk = []
        s.marked = []
        s.name_to_node = {}

    def get_topk(s, k=15):
        return s.topk[:k], []

    def register_activation_source(s, ids, source_type="external_input"):
        pass

    def mark_active(s, ids):
        s.marked = list(ids)

    def mark_edges_active(s, edges):
        pass


def build_kg(with_drive=True):
    kg = KnowledgeGraph()
    eng = FakeEngine()
    ce.bootstrap_curiosity(kg, engine=None)
    if with_drive:
        DriveEvaluator(kg, CONFIG).bootstrap_drives()
    # name_to_node 指向真实节点（检测器用它判"未知"）
    eng.name_to_node = kg.nodes
    return kg, eng


# ═══ 1. 检测层：未知 ≠ 好奇（调制） ═══

def test_detection_modulation():
    print("\n── 1. 检测层调制（P1）──")
    # 场合抑制：greeting 场合系数 0 → 不注入
    kg, eng = build_kg()
    r = ce.detect_cognitive_signals(
        kg, eng, ["SAO"], [], "你好呀",
        cognitive_context={"dialogue_act": "greeting"}, config=CONFIG)
    check("greeting 场合不注入概念信号", not r["triggered"])
    check("greeting 后 未知概念 激活为 0",
          (kg.get_node("未知概念").activation or 0.0) == 0.0)

    # question 场合：正常注入
    kg, eng = build_kg()
    r = ce.detect_cognitive_signals(
        kg, eng, ["SAO"], [], "我今天看到一个叫SAO的东西",
        cognitive_context={"dialogue_act": "question"}, config=CONFIG)
    check("question 场合信号触发", r["triggered"] and r["trigger_type"] == "unknown_concept")
    sig = (r.get("signals") or [{}])[0]
    check("信号带逐因子解释", {"novelty", "relevance", "satiation"} <= set(sig.get("factors", {})),
          str(sig))
    act_q = kg.get_node("未知概念").activation
    check("信号已注入且为正", act_q > 0.0, f"act={act_q}")

    # sharing 场合：降低而非清零（好奇但不问，交给竞争）
    kg, eng = build_kg()
    r_s = ce.detect_cognitive_signals(
        kg, eng, ["SAO"], [], "我今天看到一个叫SAO的东西",
        cognitive_context={"dialogue_act": "sharing"}, config=CONFIG)
    act_s = kg.get_node("未知概念").activation
    check("sharing 场合信号降低但非零（0 < act_s < act_q）",
          0.0 < act_s < act_q, f"act_s={act_s}, act_q={act_q}")

    # 情绪在场：概念探索退让
    kg, eng = build_kg()
    kg.add_node(Node(id="难过", weight=0.3, extra_attrs={"type": "emotion"}))
    eng.name_to_node = kg.nodes
    r_e = ce.detect_cognitive_signals(
        kg, eng, ["SAO"], [], "我今天很难过，看到一个叫SAO的东西",
        cognitive_context={"dialogue_act": "question"}, config=CONFIG)
    check("情绪在场 → trigger_type=emotion", r_e.get("trigger_type") == "emotion")
    check("情绪在场时概念信号不注入",
          all(s["type"] != "unknown_concept" for s in r_e.get("signals", [])))

    # 语法性关系词过滤
    kg, eng = build_kg()
    kg.add_node(Node(id="用户"))
    kg.add_node(Node(id="Minecraft"))
    eng.name_to_node = kg.nodes
    r_r = ce.detect_cognitive_signals(
        kg, eng, [], [{"src": "用户", "dst": "Minecraft", "type": "玩"}],
        "我在玩Minecraft",
        cognitive_context={"dialogue_act": "question"}, config=CONFIG)
    check("语法性关系词不触发未知关系信号",
          not any(s["type"] == "unknown_relation" for s in r_r.get("signals", [])))


# ═══ 2. 图驱动门槛与候选构建 ═══

def test_candidates():
    print("\n── 2. 候选构建（图驱动门槛 + 可解释）──")
    kg, eng = build_kg()
    # 门槛未过（好奇/生成好奇问题激活为 0）→ 无候选
    r = ce.build_exploration_candidates(kg, CONFIG, gate=False,
                                        signal_result={"signals": []})
    check("gate=False → 无候选", not r["eligible"] and r["reason"] == "signal_below_threshold")

    # 门槛过 + 有信号 → 候选可解释
    sig = {"type": "unknown_concept", "target": "SAO", "injected": 0.4}
    r = ce.build_exploration_candidates(kg, CONFIG, gate=True,
                                        signal_result={"signals": [sig]})
    if r["eligible"]:
        c = r["candidates"][0]
        check("候选带 score/factors/win_threshold",
              all(k in c for k in ("score", "factors", "win_threshold", "target")))
        check("图外目标 explore_search 可搜索性高",
              any(c["action"] == "explore_search" and
                  c["factors"].get("searchability", 0) >= 0.8
                  for c in r["candidates"]), str(r["candidates"]))
    else:
        # 分数不足也是合法结果，但要说明原因
        check("无候选时给出原因", bool(r["reason"]), r["reason"])
        print(f"  （本环境权重下未入场: {r['reason']}）")

    # 图外目标 → explore_search 的稀疏邻域判据（论文 §在线知识扩展）
    if r["eligible"]:
        s_cand = [c for c in r["candidates"] if c["action"] == "explore_search"]
        if s_cand:
            check("图外目标邻域=0 → searchability≥0.8",
                  s_cand[0]["factors"]["searchability"] >= 0.8)


# ═══ 3. 统一行为竞争（P3/P5） ═══

def test_competition():
    print("\n── 3. 行为竞争（探索必须赢过回应欲望）──")
    kg = KnowledgeGraph()
    for nid in ("用户", "Minecraft", "钻石"):
        kg.add_node(Node(id=nid))
    eng = FakeEngine()
    eng.topk = [FN("Minecraft", 1.5)]
    eng.name_to_node = kg.nodes

    P = {"dialogue_act": "sharing", "illocutionary_act": "assertive",
         "response_expectation": "medium"}

    # 弱候选：分数低于 win_threshold → 不打断，正常回应
    weak_cand = [{"action": "explore_ask", "score": 0.30, "win_threshold": 0.55,
                  "margin": 0.05, "target": "SAO"}]
    r = dialogue_decide(P, "我今天看到一个叫SAO的东西", kg, eng, [],
                        False, False, 3600.0, False, exploration={"candidates": weak_cand})
    check("弱候选不改变决策", r["decision"] in ("respond", "minimal", "silence"))
    check("弱候选落选被记录（可解释）", "探索落选" in r["factors"], str(r["factors"]))

    # 候选分数不够超出回应欲望 → 不打断
    close_cand = [{"action": "explore_ask", "score": 0.60, "win_threshold": 0.55,
                   "margin": 0.05, "target": "SAO"}]
    r2 = dialogue_decide(P, "我今天看到一个叫SAO的东西", kg, eng, [],
                         False, False, 3600.0, False, exploration={"candidates": close_cand})
    check("候选未超出回应欲望 → 不打断", r2["decision"] in ("respond", "minimal"))

    # 强候选 + 低回应欲望 → 探索胜出
    P_low = dict(P, response_expectation="low")
    strong = [{"action": "explore_ask", "score": 0.80, "win_threshold": 0.55,
               "margin": 0.05, "target": "SAO"}]
    r3 = dialogue_decide(P_low, "随便说点啥", kg, eng, [],
                         False, False, 3600.0, False, exploration={"candidates": strong})
    check("强候选 + 低回应欲望 → explore_ask 胜出", r3["decision"] == "explore_ask",
          r3["decision"])
    check("胜出带解释", r3["factors"].get("探索胜出", {}).get("target") == "SAO")

    # 有动作结果待汇报 → 回应优先（探索可以下一轮再来）
    r4 = dialogue_decide(P_low, "随便说点啥", kg, eng, [],
                         False, False, 3600.0, True, exploration={"candidates": strong})
    check("动作结果待汇报 → 探索让位", r4["decision"] == "respond", r4["decision"])

    # 不传 exploration → 行为与旧版完全一致（回归兼容）
    r5 = dialogue_decide(P, "我今天看到了一个东西", kg, eng, [],
                         False, False, 3600.0, False)
    check("不传探索候选 → 决策三态照旧", r5["decision"] in ("respond", "minimal", "silence"))
    check("exploration 键始终存在（消费方安全）", "exploration" in r5)


# ═══ 4. pending_inquiry：可忽略、可超时、可回答（P8） ═══

def test_pending_inquiry():
    print("\n── 4. pending_inquiry（可忽略/超时/回答）──")
    kg, eng = build_kg()
    cfg = dict(CONFIG)
    ce.record_pending_inquiry(kg, "SAO是什么？", "SAO", "UnknownConcept_SAO",
                              {"unknown_nodes": ["SAO"]}, cfg)
    check("登记后 is_curiosity_active", ce.is_curiosity_active(kg))
    lv0 = ce.get_interest(kg, "UnknownConcept_SAO")["level"]
    check("fires 后兴趣水位 > 0", lv0 > 0.0, f"level={lv0}")

    # 答非所问 → 忽略并过期，兴趣保留
    r = ce.settle_inquiry(kg, "今天天气真好啊", parsed_nodes=["天气"],
                          engine=eng, config=cfg)
    check("答非所问 → dismissed", r.get("dismissed") is True and not r.get("settled"))
    check("忽略后不再 active", not ce.is_curiosity_active(kg))
    lv1 = ce.get_interest(kg, "UnknownConcept_SAO")["level"]
    check("忽略后兴趣保留（衰减不清零）", 0.0 < lv1 <= lv0, f"level {lv0}→{lv1}")

    # 无解释性谓词的无关输入不再被当回答（旧版会无条件收下）
    kg2, eng2 = build_kg()
    ce.record_pending_inquiry(kg2, "SAO是什么？", "SAO", "UnknownConcept_SAO",
                              {"unknown_nodes": ["SAO"]}, cfg)
    r2 = ce.settle_inquiry(kg2, "哈哈哈哈", parsed_nodes=[], engine=eng2, config=cfg)
    check("无关寒暄不被当作回答", r2.get("dismissed") is True)

    # 目标名出现 → 视为回答；兴趣衰减不清零
    kg3, eng3 = build_kg()
    ce.record_pending_inquiry(kg3, "SAO是什么？", "SAO", "UnknownConcept_SAO",
                              {"unknown_nodes": ["SAO"]}, cfg)
    lv_a = ce.get_interest(kg3, "UnknownConcept_SAO")["level"]
    r3 = ce.settle_inquiry(kg3, "SAO是一部动漫", parsed_nodes=[],
                           engine=eng3, config=cfg)
    check("目标名命中 → settled", r3.get("settled") is True and r3.get("resolved") is True)
    check("结算后不再 active", not ce.is_curiosity_active(kg3))
    lv_b = ce.get_interest(kg3, "UnknownConcept_SAO")["level"]
    check("被回答后兴趣衰减保留（P9：学到≠兴趣归零）",
          0.0 < lv_b < lv_a, f"level {lv_a}→{lv_b}")

    # 解释性谓词兜底（不提目标名的真回答）
    kg4, eng4 = build_kg()
    ce.record_pending_inquiry(kg4, "SAO是什么？", "SAO", "UnknownConcept_SAO",
                              {"unknown_nodes": ["SAO"]}, cfg)
    r4 = ce.settle_inquiry(kg4, "是一部动漫", parsed_nodes=[], engine=eng4, config=cfg)
    check("解释性谓词兜底 → settled", r4.get("settled") is True)

    # 超时失效
    kg5, eng5 = build_kg()
    st = ce.record_pending_inquiry(kg5, "SAO是什么？", "SAO", "UnknownConcept_SAO",
                                   {"unknown_nodes": ["SAO"]}, cfg)
    # 直接改 asked_at 模拟超时（状态在 等待回答 节点 extra_attrs 里）
    wait = kg5.get_node("等待回答")
    wait.extra_attrs["pending_inquiry"]["asked_at"] = time.time() - 99999
    r5 = ce.settle_inquiry(kg5, "随便什么", parsed_nodes=[], engine=eng5, config=cfg)
    check("超时 → dismissed(reason=timeout)", r5.get("dismissed") and r5.get("reason") == "timeout")
    check("超时后兴趣保留", ce.get_interest(kg5, "UnknownConcept_SAO")["level"] > 0)


# ═══ 5. Drive 多因素（P4） ═══

def test_drive():
    print("\n── 5. CuriosityDrive 多因素 ──")
    kg, eng = build_kg()
    ev = DriveEvaluator(kg, CONFIG)
    ev.engine = eng
    base = ev.evaluate(force=True)
    a0 = base["drives"][0]["activation"] if base["drives"] else 0.0

    # 注入信号 → drive 上升
    kg.get_node("未知概念").activation = 0.6
    kg.get_node("未知信息").activation = 0.4
    r1 = ev.evaluate(force=True)
    a1 = r1["drives"][0]["activation"]
    check("discrepancy 信号 → drive 上升", a1 > a0, f"{a0} → {a1}")
    check("drive 结果带 components（可解释）",
          "components" in r1["drives"][0], str(r1["drives"][0].keys()))

    # 兴趣水位参与 drive（P4：好奇是持续状态）
    kg.add_node(Node(id="SAO", weight=0.4, graph_space="cognitive"))
    kg.nodes["SAO"].extra_attrs["interest"] = {"level": 0.8, "resolved_count": 0}
    r2 = ev.evaluate(force=True)
    a2 = r2["drives"][0]["activation"]
    check("兴趣水位 → drive 上升", a2 > a1, f"{a1} → {a2}")

    # 满足感抑制（最近被回答过的目标）
    kg.nodes["SAO"].extra_attrs["interest"]["resolved_count"] = 3
    r3 = ev.evaluate(force=True)
    a3 = r3["drives"][0]["activation"]
    check("满足感 → drive 回落", a3 < a2, f"{a2} → {a3}")

    # 旧死引用确认：知识缺口/需要学习/学习目标 不再被引用
    from drive_engine import CURIOSITY_SIGNAL_NODES
    check("drive 信号节点表已清理死引用",
          not ({"知识缺口", "需要学习", "学习目标", "主动提问"} & set(CURIOSITY_SIGNAL_NODES)))


# ═══ 6. ActionSelector：Ask 只记录竞争结果（P5） ═══

def test_action_selector():
    print("\n── 6. Ask 无旁路 ──")
    kg, eng = build_kg()
    kg.get_node("CuriosityDrive").activation = 4.0   # drive 很高
    sel = ActionSelector(kg, CONFIG)

    # 旧版会在这里独立触发 Ask；新版：无竞争结果 → 无 Ask intent
    r = sel.evaluate(force=True, user_input="随便一句")
    check("drive 高但无探索胜出 → 无 Ask intent",
          not any(i.get("capability") == "Ask" for i in r["intents"]))

    # 竞争胜出 → Ask intent 记录竞争结果
    r2 = sel.evaluate(force=True, user_input="随便一句",
                      exploration={"action": "explore_ask", "target": "SAO",
                                   "question": "SAO是什么？", "executed": True})
    ask = [i for i in r2["intents"] if i.get("capability") == "Ask"]
    check("探索胜出 → Ask intent 存在", len(ask) == 1)
    if ask:
        check("Ask intent 记录探索目标与执行情况",
              ask[0].get("exploration_target") == "SAO"
              and ask[0].get("exploration_executed") is True)


# ═══ 7. autonomy：basis 去硬编码 + 兴趣追问候选 ═══

def test_autonomy_integration():
    print("\n── 7. autonomy 探索接线 ──")
    # basis 用 CuriosityDrive（源码级确认，避免拉起整套 autonomy 依赖）。
    # 能力图谱重构 2026-09-20：候选生成拆为 调度器+图谱 binder+if 链，
    # 断言跨这三段源查找。
    import inspect
    import autonomy as auto_mod
    src = "".join(inspect.getsource(getattr(
        auto_mod.AutonomousLoop, m)) for m in (
        "_action_candidates", "_graph_action_candidates",
        "_bind_observe", "_bind_approach"))
    check("未知实体候选 basis 不再硬编码 '好奇'",
          '[uid, "好奇"]' not in src and '"CuriosityDrive"' in src)
    check("兴趣追问候选方法存在",
          hasattr(auto_mod.AutonomousLoop, "_interest_ask_candidates"))
    ask_src = inspect.getsource(auto_mod.AutonomousLoop._interest_ask_candidates)
    check("追问候选要求用户在场", "percept.get(\"players\")" in ask_src)
    check("追问候选受 drive 门槛约束", "ask_min_drive" in ask_src)


# ═══ 8. 存档兼容（bootstrap 幂等） ═══

def test_bootstrap_compat():
    print("\n── 8. 旧存档兼容 ──")
    kg = KnowledgeGraph()
    # 模拟旧存档：未知概念 是 semantic 空间、生成好奇问题 带死 execution
    kg.add_node(Node(id="未知概念", weight=0.3, graph_space="semantic",
                     extra_attrs={"type": "curiosity"}))
    kg.add_node(Node(id="生成好奇问题", weight=0.3, label="procedural",
                     graph_space="cognitive", execution="result['action']='x'"))
    kg.add_node(Node(id="CuriosityDrive", weight=0.5, graph_space="cognitive"))
    ce.bootstrap_curiosity(kg, engine=None)
    check("旧 semantic 空间节点被幂等对齐为 cognitive",
          kg.get_node("未知概念").graph_space == "cognitive")
    check("死 execution 被清除，节点保留",
          kg.get_node("生成好奇问题").execution is None
          and kg.get_node("生成好奇问题").id in kg.nodes)
    check("新决策通路边（未知* → CuriosityDrive）已创建",
          kg.get_edge("未知概念", "CuriosityDrive", "uncertainty_signal") is not None)
    check("旧信号边保留（未知概念 → 好奇）",
          kg.get_edge("未知概念", "好奇", "trigger_curiosity") is not None)
    # 幂等：再跑一遍不新增
    n_nodes, n_edges = len(kg.nodes), len(kg.edges)
    ce.bootstrap_curiosity(kg, engine=None)
    check("bootstrap 幂等（不重复建节点/边）",
          len(kg.nodes) == n_nodes and len(kg.edges) == n_edges)

    # 旧状态字段兼容读取
    kg2, _ = build_kg()
    wait = kg2.get_node("等待回答")
    wait.extra_attrs["curiosity"] = {"active": True, "question": "旧问题",
                                     "unknown_nodes": ["旧目标"]}
    check("旧 curiosity 字段兼容读取", ce.is_curiosity_active(kg2))
    st = ce.get_curiosity_state(kg2)
    check("旧字段 target 兜底", st.get("target") == "旧目标")


# ═══ 9. 探索目标落图（论文 CreateUnknownNode 语义） ═══

def test_exploration_target():
    print("\n── 9. 探索目标落图 ──")
    kg, _ = build_kg()
    nid = ce.ensure_exploration_target(kg, "SAO")
    check("图外目标 → UnknownConcept_ 节点", nid == "UnknownConcept_SAO"
          and nid in kg.nodes)
    check("节点带 type=unknown 标记",
          kg.nodes[nid].extra_attrs.get("type") == "unknown")
    nid2 = ce.ensure_exploration_target(kg, "SAO")
    check("幂等（不重复建）", nid2 == nid and
          sum(1 for n in kg.nodes if n == nid) == 1)
    kg.add_node(Node(id="Minecraft", weight=0.5))
    nid3 = ce.ensure_exploration_target(kg, "Minecraft")
    check("已有节点直接复用", nid3 == "Minecraft")


if __name__ == "__main__":
    test_detection_modulation()
    test_candidates()
    test_competition()
    test_pending_inquiry()
    test_drive()
    test_action_selector()
    test_autonomy_integration()
    test_bootstrap_compat()
    test_exploration_target()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAIL: {len(FAILURES)} 项未通过")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS: 全部通过")
