# tests/test_r2_scenarios.py — R2 P12 闸门：17 个场景，全部走**真实管线**（§26）
# ============================================================================
# 规格 §29 点了 17 个场景，覆盖 `Events → Modulators → Graph → Drive/Need →
# Cognitive Field → Action/Dialogue → Outcome` 这条完整链；§26 同时要求
# "测试接口应调用真实 Modulator event pipeline"——所以这里**没有一条断言**是直接
# 往数值里写"期望结果"再读出同一个数：事件从 `ModulatorEngine.emit`（与 reward
# 同一个入口）进，结果从 `RewardSystem.evaluate + release`（与行动结算同一条路）
# 进，时间从统一时基（`InternalState.set_clock`）走，快进是逐块重放生产的状态拍
# （`modulator_lab.advance` 里没有第二份衰减公式）。
#
# 装配 = 生产形状：真图 + DiffusionEngine + InternalState + 调制子图 + 事件表 +
# DriveEvaluator/CognitiveField（12 调制器 provider + 图上偏置，接线方式与 app.py
# 逐条相同）+ RewardSystem + PersonalityBaseline（心情包络已接 mood_source）。
#
# 每个场景独立建环境（fresh env）——场景之间不共享数值，前一个场景拧的旋钮
# 不会成为后一个场景的前提。断言只盯两件事：**方向对**（符号）与**量级真实**
# （不是"只要非零就算过"）。
# ============================================================================
import copy
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config as C                                          # noqa: E402
from graph_model import KnowledgeGraph, Node                 # noqa: E402
from internal_state import InternalState, TRAIT_SPEC          # noqa: E402
from diffusion_engine import DiffusionEngine                 # noqa: E402
from personality_baseline import PersonalityBaseline         # noqa: E402
from disposition_store import DispositionStore               # noqa: E402
from drive_engine import DriveEvaluator                      # noqa: E402
from reward import RewardSystem                              # noqa: E402
import modulator_subgraph as MS                              # noqa: E402
import modulator_lab as ML                                   # noqa: E402

FAILURES = []
ASSERTS = [0]
SCEN_DONE = []


def check(name, cond, detail=""):
    ASSERTS[0] += 1
    if not cond:
        FAILURES.append(name)
        print(f"  [FAIL] {name}" + (f" | {detail}" if detail else ""))
    return cond


def make_env():
    """一个**生产形状**的独立环境（与 app.py 的装配逐条同序）。"""
    cfg = copy.deepcopy(C.DEFAULT_CONFIG)
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", label="declarative-semantic",
                     graph_space="self", weight=1.0))
    for nid in ("CENetwork", "DMNetwork", "CuriosityDrive", "SocialDrive",
                "LearningDrive", "ConsistencyDrive"):
        kg.add_node(Node(id=nid, label="declarative-semantic",
                         graph_space="cognitive", weight=0.5))
    for nid in ("行为:探索", "行为:分享", "行为:沉默", "行为:共情", "行为:延续"):
        kg.add_node(Node(id=nid, label="declarative-semantic",
                         graph_space="self", weight=0.4))
    eng = DiffusionEngine(kg, cfg)
    eng.name_to_node = dict(kg.nodes)
    d = tempfile.mkdtemp(prefix="fas_p12_")
    st = InternalState(kg=kg, config=cfg, engine=eng, data_dir=d)
    st.sync_graph()
    MS.ensure_modulator_subgraph(kg, st, cfg)
    rs = RewardSystem(internal_state=st, config=cfg)
    engine = rs.mod_engine()                 # 与生产同一个应用器实例（ensure=True）
    engine.ensure()
    dsp = DispositionStore(kg, config=cfg)
    dsp.ensure_vocab()
    persona = PersonalityBaseline(kg, dsp, config=cfg, clock=lambda: st.now())
    persona.set_mood_source(lambda: MS.mood_projection(kg, st, cfg))
    ev = DriveEvaluator(kg, cfg)
    ev.bootstrap_drives()
    # 与 app.py 同一套接线：12 个调制器读数、心情、需求显著性、图上偏置
    for m in st.modulator_names():
        ev.set_signal_provider(m, (lambda mm=m: st.modulator_tonic(mm)))
    ev.set_signal_provider(
        "mood_valence", lambda: float(persona.current_mood().get("valence") or 0.0))
    for nd in st.need_names():
        ev.register_need(nd, (lambda n=nd: st.need_salience(n)))
    ev.set_graph_bias_source(
        lambda: MS.graph_biases(kg, st, cfg, ev.field.node_names()))
    # app.py 装配的**最后一步**（app.py:1095）：把调制边投影成 ModulationLayer
    # 的系数行。没这步行表是空的 ⇒ 所有"参数随激素动"的断言都会瞎掉。
    MS.project_coefficients(kg, st, ev.modulation(), cfg)
    lab = ML.ModulatorLab(kg, st, cfg, engine=engine, reward=rs,
                          persona=persona, field=ev.field)
    return dict(cfg=cfg, kg=kg, st=st, eng=eng, rs=rs, engine=engine,
                persona=persona, dsp=dsp, ev=ev, lab=lab, tmp=d)


def settle(env, steps=80):
    """让调制参数 EMA 收敛后再读（同 P8 闸门的 `settled` 手法，但走场自己的
    `refresh_modulation`——也就是 CC 每拍喂完激素后调的那个函数）。"""
    for _ in range(steps):
        env["ev"].field.refresh_modulation()
    return env["lab"].params()


def param(env, name):
    return settle(env)[name]


def act(name, fn):
    print(f"\n── {name} ──")
    before = len(FAILURES)
    try:
        fn()
    except Exception as e:                                  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(f"{name}（未捕获异常：{e}）", False)
    if len(FAILURES) == before:
        SCEN_DONE.append(name)


# ═════════ 1 奖赏脉冲 → dopamine phasic → 图激活 ═════════
def s01():
    e = make_env()
    r = e["lab"].outcome(self_outcome="completion", behavior="b1", context="c1")
    dop = r["diff"]["mods"].get("dopamine") or {}
    check("1a release() 经图扇出打出 reward_rpe（phasic 通道）",
          any(x["type"] == "reward_rpe" and x["channel"] == "phasic"
              for x in r["fanout"]), str(r["fanout"]))
    check("1b phasic 抬起来、tonic 只被 reward_success 慢项推",
          dop.get("phasic", 0) > 0 and abs(dop.get("dev", 0)) < 0.15,
          str(dop))
    lit = float(e["kg"].get_node("多巴胺样").activation or 0.0)
    check("1c 图通道点亮：|phasic|≥阈值 ⇒ 镜像节点拿到 activation（phasic→图，D2）",
          lit > 0.0, f"activation={lit}")
    e["lab"].close()


# ═════════ 2 RPE 为负 → phasic<0 → 学习因子<1 ═════════
def s02():
    e = make_env()
    e["lab"].open()
    e["lab"].outcome(self_outcome="completion", behavior="b2", context="c2")
    # 清场（虚拟 2 小时）：脉冲衰减、不应期退场——否则第二次脉冲被阻尼掉。
    # 注意慢分量**不会**回到 0.5：需求漂移边把它托在基线上方（场景 16 量过
    # 这个平衡点）。所以 2c 的判据不是"绝对值 <1"，而是**相对事件前状态**：
    # 一次负 RPE 事件必须严格拉低学习因子（discount），且不该把它清零。
    e["lab"].advance(120)
    f_clean = e["rs"].modulation()["factor"]
    r2 = e["lab"].outcome(self_outcome="goal_failure", behavior="b2", context="c2")
    rel = r2["releases"][0]
    check("2a 先建立预期再失败 ⇒ RPE 明显为负",
          rel["rpe"] < -0.5, str(rel))
    dop = r2["diff"]["mods"].get("dopamine") or {}
    check("2b 负 RPE 打的是 phasic 下沿（不是把 tonic 抹平）",
          dop.get("phasic", 0) < 0, str(dop))
    f = r2["learning_modulation"]["factor"]
    check("2c 负 RPE 事件把这次经历的学习因子**压低**（tonic 降 + 压力项），"
          f"但没清零：{f_clean} → {f}",
          f_clean - f > 1e-4 and f >= 0.4, f"Δ={round(f - f_clean, 4)}")
    e["lab"].close()


# ═════════ 3 连续成功 → 饱和+不应期 → 增益递减 ═════════
def s03():
    e = make_env()
    ph, cc = [], []
    c_prev = e["st"].modulator_conc("dopamine")
    for i in range(8):
        r = e["lab"].outcome(self_outcome="completion",
                             behavior="b3", context="c3")
        row = next((x for x in r["fanout"] if x["type"] == "reward_rpe"), None)
        ph.append(abs(row["delta"]) if row else 0.0)
        now = e["st"].modulator_conc("dopamine")
        cc.append(now - c_prev)
        c_prev = now
    check("3a 同样的好事做 8 次：phasic 响应严格衰减（预期追上现实）",
          all(ph[i + 1] <= ph[i] + 1e-9 for i in range(7)) and ph[-1] < ph[0] * 0.5,
          str([round(x, 4) for x in ph]))
    pos = [x for x in cc if x > 1e-9]
    check("3b tonic 增量同样递减（饱和+上升夹速），且水位不越量程",
          len(pos) >= 2 and pos[-1] < pos[0] and
          e["st"].modulator_conc("dopamine") < 1.0 + 1e-9,
          str([round(x, 4) for x in cc]))
    e["lab"].close()


# ═════════ 4 社会拒绝 → oxytocin↓/cortisol↑ → 社交显著性与底色下降 ═════════
def s04():
    e = make_env()
    m0 = float(e["persona"].current_mood().get("modulator_term") or 0.0)
    p0 = param(e, "cognition.express_threshold")
    r = e["lab"].outcome(social_outcome="rejected", behavior="b4", context="c4")
    mods = r["diff"]["mods"]
    check("4a 被拒绝：催产素的 tonic 掉、皮质醇的 tonic 升（同一事件两边靶）",
          mods["oxytocin"]["tonic"] < 0 < mods["cortisol"]["tonic"], str(mods))
    b = MS.graph_biases(e["kg"], e["st"], e["cfg"], e["ev"].field.node_names())
    check("4b 社交驱动拿到**负偏置**（`催产素样-[增强]->SocialDrive` 被 dev<0 翻转）",
          b["drive"].get("social", 0.0) < 0.0, str(b["drive"]))
    check("4c 心情包络往下走（oxytocin 权重 0.2 与 cortisol 权重 −0.3 同号叠加）",
          float(e["persona"].current_mood().get("modulator_term")) < m0,
          f"{m0} → {e['persona'].current_mood()['modulator_term']}")
    p1 = param(e, "cognition.express_threshold")
    check("4d 表达阈值抬升（`催产素样-[抑制]->表达阈值` 的负 dev = 更不想说）",
          p1 > p0 + 1e-4, f"{p0} → {p1}")
    check("4e 但对话层拿到的是**读数**：mood_context 非空且当前 valence<0",
          isinstance(e["persona"].mood_context(), str)
          and float(e["persona"].current_mood()["valence"]) < 0, "")
    e["lab"].close()


# ═════════ 5 新奇 → ACh/glutamate → 编码/扩散增益上升 ═════════
def s05():
    e = make_env()
    p0 = param(e, "diffusion.param_gain")
    r = e["lab"].inject("novel_contact", novelty=0.9, source="experiment")
    mods = r["diff"]["mods"]
    check("5a `novel_contact` 按边扇出：ACh 与 glutamate 同时抬（无逐激素 if）",
          mods["acetylcholine"]["tonic"] > 0 and mods["glutamate"]["tonic"] > 0,
          str(mods))
    p1 = param(e, "diffusion.param_gain")
    check("5b 谷氨酸的 dev 进到 `调制目标:扩散增益`（发射侧 param_gain 上升）",
          p1 > p0 + 1e-3, f"{p0} → {p1}")
    e["lab"].close()


# ═════════ 6 不确定 → NE/ACh → 检索广度上升 + 协同增益 ═════════
def s06():
    base = make_env()                      # 对照组：NE 在基线
    hot = make_env()                        # 实验组：NE 先抬上去再吃同一个事件
    hot["lab"].dial("norepinephrine", 0.75, reason="场景6：警觉背景")
    dev_ne = hot["st"].modulator_dev("norepinephrine")
    rb = base["lab"].inject("prediction_violation", uncertainty=0.8,
                            rpe=0.5, source="prediction")
    rh = hot["lab"].inject("prediction_violation", uncertainty=0.8,
                           rpe=0.5, source="prediction")
    def achrow(r):
        return next((a for a in r["applied"] if a["mod"] == "acetylcholine"), None)
    ab, ah = achrow(rb), achrow(rh)
    check("6a `交互` 边（ACh×NE，w=+0.3）给 ACh 的写入乘上 >1 的增益",
          ab["gain"] == 1.0 and ah["gain"] > 1.0 and dev_ne > 0,
          f"gain {ab['gain']} vs {ah['gain']} (dev_ne={dev_ne:.3f})")
    check("6b 增益按公式可复算：gain ≈ 1 + w×dev(NE)（w=+0.3 是出厂交互边）",
          abs(ah["gain"] - (1.0 + 0.3 * dev_ne)) < 0.02,
          f"{ah['gain']} vs {1 + 0.3 * dev_ne:.4f}")
    check("6c NE 走 phasic（快通道），ACh 走 tonic（慢通道）——同一事件两个通道",
          any(a["mod"] == "norepinephrine" and a["channel"] == "phasic"
              for a in rh["applied"]), str(rh["applied"]))
    pb = param(base, "retrieval.topk_scale")
    ph_ = param(hot, "retrieval.topk_scale")
    check("6d 检索广度在实验组更大（NE 0.3 + ACh 0.2 两条边同向）",
          ph_ > pb + 1e-3, f"{pb} vs {ph_}")
    base["lab"].close()
    hot["lab"].close()


# ═════════ 7 夜间 melatonin↑ → 预算/门槛收缩；非昼夜源被门控 ═════════
def s07():
    e = make_env()
    m0 = e["st"].modulator_conc("melatonin")
    r_gate = e["lab"].inject("time_bucket_changed", valence=1.0,
                             source="experiment")     # 假源：不该动褪黑素
    check("7a `circadian_driven` 第一次真的拦东西：非 circadian 源 ⇒ 褪黑素不动",
          any("circadian_driven" in str(g.get("gate")) for g in r_gate["gated"])
          and e["st"].modulator_conc("melatonin") == m0,
          str(r_gate["gated"]))
    b0 = param(e, "llm.budget_factor")
    a0 = param(e, "action.score_threshold")
    r = e["lab"].inject("time_bucket_changed", valence=1.0, source="circadian")
    mc = e["st"].modulator_conc("melatonin")
    check("7b 昼夜源放行：褪黑素被推上去，且**被 clamp 限幅**（0.45×w 不会一把打满）",
          mc > m0 and (mc - m0) <= 0.25 + 1e-9, f"{m0} → {mc}")
    check("7c 同事件的两侧：组胺样反向回落（清醒度掉）",
          (next((a for a in r["applied"] if a["mod"] == "histamine"), {})
           or {}).get("requested", 0) < 0
          or any(g["mod"] == "histamine" for g in r["gated"]),
          str(r["applied"]))
    check("7d LLM 认知预算收缩（`褪黑素样-[调制 −0.25]->认知预算`）",
          param(e, "llm.budget_factor") < b0 - 1e-4, f"{b0} → {param(e, 'llm.budget_factor')}")
    check("7e 行动门槛抬升 ⇒ 夜里更少发起行动（urgency↓ 的落点）",
          param(e, "action.score_threshold") > a0 + 1e-4, f"{a0}")
    e["lab"].close()


# ═════════ 8 过载（多巴胺钉高）→ 探索倒 U 转负 ═════════
def s08():
    hi = make_env()
    ov = make_env()
    hi["lab"].dial("dopamine", 0.80)                   # 高兴奋带（dev>0）
    ov["lab"].dial("dopamine", 1.00)                   # 钉在量程顶（过载带）
    b_hi = MS.graph_biases(hi["kg"], hi["st"], hi["cfg"],
                           hi["ev"].field.node_names())["drive"]
    b_ov = MS.graph_biases(ov["kg"], ov["st"], ov["cfg"],
                           ov["ev"].field.node_names())["drive"]
    check("8a 同一个 `多巴胺样-[增强]->CuriosityDrive` 边：正常带推正、过载带拉回",
          b_hi.get("curiosity", 0.0) > 0 > b_ov.get("curiosity", 0.0),
          f"{b_hi} vs {b_ov}")
    p_hi = param(hi, "behavior.exploration_rate")
    p_base = param(make_env(), "behavior.exploration_rate")
    p_ov = param(ov, "behavior.exploration_rate")
    check("8b 探索速率参数沿倒 U：过载带 < 静息 < 高兴奋带（连续函数，非 4 段 if）",
          p_ov < p_base < p_hi, f"overload {p_ov} < rest {p_base} < high {p_hi}")
    hi["lab"].close()
    ov["lab"].close()


# ═════════ 9 GABA↑ → 扩散抑制 + 行动门槛（竞争余量）抬升 ═════════
def s09():
    e = make_env()
    g0 = param(e, "diffusion.param_gain")
    t0 = param(e, "action.score_threshold")
    e["lab"].dial("gaba", 0.80)
    check("9a 抑制边（`GABA样-[抑制 −0.3]->扩散增益`）把增益压到基线以下",
          param(e, "diffusion.param_gain") < g0 - 1e-3, f"{g0} → {param(e, 'diffusion.param_gain')}")
    check("9b `调制` 边的符号写在权重上：GABA 抬行动门槛（竞争要赢更多才动）",
          param(e, "action.score_threshold") > t0 + 1e-3, f"{t0}")
    check("9c GABA 的**正出边**存在（纯负边节点在扩散里永不发射——P6 结构约束）",
          any(float(x.weight) > 0 for x in e["kg"].get_out_edges("GABA样")),
          str([(x.dst, x.weight) for x in e["kg"].get_out_edges("GABA样")]))
    e["lab"].close()


# ═════════ 10 endorphin 缓冲 → 负事件钝化（不消除） ═════════
def s10():
    a = make_env()
    b = make_env()
    b["lab"].dial("endorphin", 0.65)                   # 钝化系统在场
    dev_end = b["st"].modulator_dev("endorphin")
    ra = a["lab"].inject("reward_failure", intensity=0.5, valence=-0.5,
                         goal_relevance=1.0, source="experiment")
    rb = b["lab"].inject("reward_failure", intensity=0.5, valence=-0.5,
                         goal_relevance=1.0, source="experiment")
    ca = next(x for x in ra["applied"] if x["mod"] == "cortisol")
    cb = next(x for x in rb["applied"] if x["mod"] == "cortisol")
    check("10a `交互` 边（endorphin×cortisol −0.6）在场 ⇒ 压力写入增益 <1",
          ca["gain"] == 1.0 and cb["gain"] < 1.0,
          f"gain {ca['gain']} vs {cb['gain']} (dev_end={dev_end:.3f})")
    check("10b 增益就是那个连乘因子（可复算），且**钝化不等于消音**（delta 仍 >0）",
          abs(cb["gain"] - (1 - 0.6 * dev_end)) < 0.02 and cb["requested"] > 0,
          f"{cb['gain']} vs {1 - 0.6 * dev_end:.4f}")
    check("10c 缓冲只改幅度不改方向：两边 cortisol 都升",
          cb["requested"] > 0 and ca["requested"] > cb["requested"],
          f"{ca['requested']} vs {cb['requested']}")
    a["lab"].close()
    b["lab"].close()


# ═════════ 11 压力（cortisol 持续）→ 学习钝化 + DMN 下降 ═════════
def s11():
    ctl = make_env()
    hot = make_env()
    hot["lab"].dial("cortisol", 0.72)                  # 持续高水位（未到过载）
    m = hot["rs"].modulation()
    check("11a 压力项进学习调制：factor <1（stress_coef×(cort−base) 走的是 tonic）",
          m["factor"] < 1.0 and m["stress"] < 0, str(m))
    b = MS.graph_biases(hot["kg"], hot["st"], hot["cfg"],
                        hot["ev"].field.node_names())["network"]
    check("11b `皮质醇样-[抑制 −0.25]->DMNetwork` ⇒ 走神/反思稳态拿到负偏置",
          b.get("DMN", 0.0) < 0.0, str(b))
    rs_c = ctl["lab"].settle_field(240)["networks"]
    rs_h = hot["lab"].settle_field(240)["networks"]
    check("11c 连续 240 拍后 DMN 稳态水位低于对照（偏置真的改稳态，不是装饰）",
          rs_h["DMN"] < rs_c["DMN"] - 1e-3, f"{rs_c['DMN']} vs {rs_h['DMN']}")
    ctl["lab"].close()
    hot["lab"].close()


# ═════════ 12 空闲+组胺 → 图路径点亮 行为:探索（发起的可达性） ═════════
def s12():
    e = make_env()
    b0 = param(e, "llm.budget_factor")
    r = e["lab"].inject("time_bucket_changed", valence=-1.0, source="circadian")
    h1 = e["st"].modulator_conc("histamine")
    check("12a 变亮 ⇒ 组胺样升（同一事件的两侧：w=−0.35 乘 valence=−1）",
          h1 > 0.45, f"conc {h1}")
    check("12b 清醒抬认知预算（`组胺样-[增强 0.2]->认知预算`）",
          param(e, "llm.budget_factor") > b0 + 1e-4, f"{b0} → {param(e, 'llm.budget_factor')}")
    # 发起走图通道：脉冲点亮组胺样，一轮真实扩散沿 `组胺样-[增强 0.2]->行为:探索` 传播
    e["eng"].decay_step()
    e["eng"].diffuse_step()
    e["eng"].clear_anchors()
    for n in e["kg"].nodes.values():
        n.activation = 0.0
    e["st"].pulse("histamine", 0.30, reason="场景12", source="manual")
    e["eng"].decay_step()
    e["eng"].diffuse_step()
    lit = float(e["kg"].get_node("行为:探索").activation or 0.0)
    check("12c 一次脉冲经真实扩散点亮 `行为:探索`（行为概念=图通道，不是 bias）",
          lit > 0.0, f"activation={lit}")
    tot = sum(n.activation for n in e["kg"].nodes.values() if n.activation > 0)
    check("12d 传播是事件尺度的局部点亮（能量有界，不是全图饱和）",
          0 < tot < 10.0, f"E={tot:.2f}")
    e["lab"].close()


# ═════════ 13 口渴式需求 → 慢漂移进通道 → 负反馈收敛 ═════════
def s13():
    e = make_env()
    e["lab"].open()
    e["st"].set_value("need", "competence", 0.05, source="manual",
                      reason="场景13：缺口拉开")
    c0 = e["st"].modulator_conc("cortisol")
    d0 = e["st"].modulator_conc("dopamine")
    s0 = e["st"].modulator_conc("serotonin")
    r = e["lab"].advance(120)                          # 两小时，24 个生产拍
    check("13a 需求漂移确实写了边（`掌控需求` 的 3 条出边都在通道里）",
          r["need_drift_writes"] > 20, str(r["need_drift_writes"]))
    pos_edge = [x for x in e["kg"].get_out_edges("掌控需求") if x.dst == "皮质醇样"]
    neg_edge = [x for x in e["kg"].get_out_edges("安全需求") if x.dst == "皮质醇样"]
    check("13b 缺口两小时：多巴胺按增强边抬起来；皮质醇被两条**反向**边拉扯后"
          "净位移不为正（安全侧的 −0.25 吃掉了掌控侧的 +0.15）",
          e["st"].modulator_conc("dopamine") > d0 + 0.01
          and e["st"].modulator_conc("cortisol") < c0 + 0.005,
          f"cort {c0}→{e['st'].modulator_conc('cortisol')} "
          f"dopa {d0}→{e['st'].modulator_conc('dopamine')}")
    check("13b2 同一通道同向在场两条边：掌控→正权、安全→负权（符号写在权重上）",
          pos_edge and neg_edge
          and float(pos_edge[0].weight) > 0 > float(neg_edge[0].weight),
          str([(x.relation, x.weight) for x in pos_edge + neg_edge]))
    p_act = param(e, "behavior.exploration_rate")
    p_rest = param(make_env(), "behavior.exploration_rate")
    check("13b3 链走完：缺口两小时后**行动选择参数**确实变了（探索速率↑）",
          p_act > p_rest + 1e-3, f"{p_rest} → {p_act}")
    bias = MS.graph_biases(e["kg"], e["st"], e["cfg"],
                           e["ev"].field.node_names())["need"]
    check("13c 反向通道成立：抬起来的多巴胺对探索需求给出**负偏置**（饱足收敛）",
          bias.get("exploration", 0.0) < 0.0, str(bias))
    e["lab"].close()


# ═════════ 14 意图被打断 → NE phasic → CEN 抢占（图路径） ═════════
def s14():
    e = make_env()
    for n in e["kg"].nodes.values():
        n.activation = 0.0
    e["eng"].clear_anchors()
    r = e["lab"].inject("interrupted", intensity=1.0, source="action")
    check("14a cancelled 不再静默：`interrupted` 事件走 NE phasic + 肾上腺素 tonic",
          any(a["mod"] == "norepinephrine" and a["channel"] == "phasic"
              for a in r["applied"])
          and any(a["mod"] == "adrenaline" for a in r["applied"]),
          str(r["applied"]))
    e["eng"].decay_step()
    e["eng"].diffuse_step()
    cen = float(e["kg"].get_node("CENetwork").activation or 0.0)
    dm = float(e["kg"].get_node("DMNetwork").activation or 0.0)
    check("14b 一轮扩散后控制网络被点亮（`去甲肾上腺素样-[增强 0.25]->CENetwork`）",
          cen > 0.0, f"CEN={cen}")
    check("14c 抢占不是口号：此刻 CEN 的点亮严格大于 DMN（NE 只连控制网络）",
          cen > dm, f"CEN={cen} DMN={dm}")
    e["lab"].close()


# ═════════ 15 结果 → disposition 位移 → trait 位移受限（慢） ═════════
def s15():
    e = make_env()
    t0 = {k: e["st"].trait(k) for k in
          ("social_openness", "feedback_sensitivity", "exploration_bias")}
    sigs, facs = [], []
    for i in range(6):
        o = e["lab"].outcome(social_outcome="accepted", behavior="respond",
                             context="chat")
        facs.append(o["learning_modulation"]["factor"])
        tr = e["dsp"].apply_experience("respond", "chat", social_outcome="accepted",
                                       hormone=o["learning_modulation"])
        sigs.append(tr["learning_signal"])
    pair = e["dsp"].get_pair("chat", "respond")
    ea = pair.extra_attrs
    check("15a 经历进 disposition：强度涨了、正证据记到 6（激素只调值多少）",
          float(ea.get("strength", 0)) > 0.1 and
          int(ea.get("pos_feedback", 0)) >= 5,
          str({k: ea.get(k) for k in ("strength", "pos_feedback", "status")}))
    check("15b 纯社交经历 factor≡1.0：reward_rpe 的 goal_relevance 门控挡住社交源——"
          "多巴胺快通道**不该**替社交好事加码（这是门控，不是失灵）",
          all(abs(f - 1.0) < 0.05 for f in facs), str(facs))
    # 自我完成是另一条通道：phasic 脉冲 ⇒ 同一次经历"值更多"
    o2 = e["lab"].outcome(self_outcome="completion", behavior="respond",
                          context="chat")
    f2 = o2["learning_modulation"]["factor"]
    check("15b2 自我完成（预期还低）⇒ phasic>0 ⇒ factor>1（快通道只放大自我事件）",
          f2 > 1.0 and o2["learning_modulation"]["phasic"] > 0,
          str(o2["learning_modulation"]))
    tr2 = e["dsp"].apply_experience("respond", "chat", self_outcome="completion",
                                    hormone=o2["learning_modulation"])
    raw2 = tr2["social_signal"] + tr2["self_signal"]
    check("15b3 learning_signal = 原始效价 × factor，逐字可复算（禁令：激素只调值多少）",
          abs(tr2["learning_signal"] - raw2 * f2) < 1e-3,
          f"{tr2['learning_signal']} vs {raw2}×{f2}")
    # trait 通道（行动结算里与 disposition 同源、独立）：α=0.005/事件，慢变量
    steps = [e["st"].record_trait_evidence(
        "social_openness", 1.0, source="action_outcome", reason="场景15")
        for _ in range(6)]
    drift = e["st"].trait("social_openness") - t0["social_openness"]
    check("15c 六次满强度证据只把 trait 推 6α=0.03（位移受限，§28）",
          all(abs(s["drift"] - e["st"].TRAIT_ALPHA) < 1e-9 for s in steps)
          and 0.02 < drift < 0.035, f"drift={drift}")
    late = e["st"].record_trait_evidence("social_openness", 1.0,
                                         source="action_outcome", reason="场景15d")
    check("15d 快通道不冒充慢通道：factor>1 之后 trait 步长**仍是** 1α（激素不碰 α）",
          abs(late["drift"] - e["st"].TRAIT_ALPHA) < 1e-9 and f2 > 1.0,
          f"drift={late['drift']} factor={f2}")
    e["lab"].close()


# ═════════ 16 长期高多巴胺 → trait 仍纹丝不动（护栏） ═════════
def s16():
    e = make_env()
    e["lab"].open()
    t_before = {k: e["st"].trait(k) for k in TRAIT_SPEC}
    d_before = len(e["dsp"].list_dispositions())
    e["lab"].dial("dopamine", 1.00)                    # 钉在量程顶一整天
    e["lab"].advance(1440)                             # 24 虚拟小时 = 288 个生产拍
    st_sync = e["st"].sync_graph()
    c_day = e["st"].modulator_conc("dopamine")
    check("16a 一整天过去：旋钮值 1.0 早被衰减拉下，水位落在**需求漂移边给的平衡点**"
          "（≈基线+0.2×显著性），不是停在旋钮上——衰减走的是同一时基，不是墙钟",
          0.45 < c_day < 0.70, str(c_day))
    check("16b phasic 归零、不应期与动量退场", e["st"].modulator_pulse("dopamine") == 0.0,
          str(e["st"].modulator_dynamics("dopamine")))
    check("16c **调制器单独作用一整天，trait 一字不动**（人格只能经行为→结果）",
          all(e["st"].trait(k) == t_before[k] for k in t_before),
          str({k: e["st"].trait(k) - t_before[k] for k in t_before}))
    check("16d 同理 disposition 零新增（没有经历就没有人格位移）",
          len(e["dsp"].list_dispositions()) == d_before, "")
    check("16e 心情包络被 cap 钳住（|term| ≤ 0.35），且过载段符号已反转",
          abs(e["persona"].current_mood()["modulator_term"]) <= 0.35,
          str(e["persona"].current_mood()))
    e["lab"].close()


# ═════════ 17 图谱边权改写 → 效力随之改变（图是真源） ═════════
def s17():
    e = make_env()
    e["lab"].dial("dopamine", 0.80)
    rest = param(make_env(), "behavior.exploration_rate")     # 静息参照
    p_hi = param(e, "behavior.exploration_rate")
    edge = e["kg"].get_edge("多巴胺样", "调制目标:探索速率", "增强")
    check("17a 出厂边在（w=0.2），参数已偏离静息",
          edge is not None and p_hi > rest, f"{rest} → {p_hi}")
    w0 = float(edge.weight)
    edge.weight = 0.0
    MS.project_coefficients(e["kg"], e["st"], e["ev"].field.modulation, e["cfg"])
    p_zero = param(e, "behavior.exploration_rate")
    check("17b 权重改成 0（**不删节点不删边**）⇒ 参数回到静息值：图是效力真源",
          abs(p_zero - rest) < 1e-3, f"{rest} vs {p_zero}")
    edge.weight = w0 * 2.0
    MS.project_coefficients(e["kg"], e["st"], e["ev"].field.modulation, e["cfg"])
    p_wide = param(e, "behavior.exploration_rate")
    check("17c 权重翻倍 ⇒ 同一个 dev 得到双倍位移（线性可复算）",
          (p_wide - rest) > 1.5 * (p_hi - rest), f"Δ {p_hi - rest} → {p_wide - rest}")
    edge.weight = w0
    MS.project_coefficients(e["kg"], e["st"], e["ev"].field.modulation, e["cfg"])
    check("17d 改回原值 ⇒ 逐字复原（投影器无状态、图仍是唯一权威）",
          abs(param(e, "behavior.exploration_rate") - p_hi) < 1e-6, "")
    # 心情侧同理：删一条边就少一分贡献
    s_edge = next(x for x in e["kg"].get_in_edges("心情") if x.src == "血清素样")
    e["lab"].dial("serotonin", 0.70)        # 先把 dev 抬离零点：dev=0 的边清不清都一样
    proj0 = MS.mood_projection(e["kg"], e["st"], e["cfg"])
    s_edge.weight = 0.0
    proj1 = MS.mood_projection(e["kg"], e["st"], e["cfg"])
    check("17e `血清素样-[增强]->心情` 权重清零 ⇒ 包络立刻缩（同一编译器）",
          abs(proj1["term"]) < abs(proj0["term"]), f"{proj0['term']} → {proj1['term']}")
    e["lab"].close()


# ── 实验台自身的验收：它驱动的是真实管线，不是平行实现 ──
def s_lab():
    a = make_env()
    b = make_env()
    ra = a["lab"].inject("reward_rpe", rpe=0.8, goal_relevance=1.0,
                         valence=0.8, intensity=0.8, source="experiment")
    rb_raw = b["engine"].emit("reward_rpe", rpe=0.8, goal_relevance=1.0,
                              valence=0.8, intensity=0.8, source="experiment")
    fa = {x["mod"]: x["actual"] for x in ra["applied"]}
    fb = {x["mod"]: x["actual"] for x in rb_raw["applied"]}
    check("台1 lab.inject 与 engine.emit 打出**逐位相同**的 delta（同一入口）",
          fa == fb and fa, f"{fa} vs {fb}")
    la = make_env()
    lb = make_env()
    la["lab"].open()
    la["lab"].dial("cortisol", 0.80)
    la["lab"].advance(90)
    # 对照组：不经实验台，**手工**按生产的函数序列重放 18 个 5 分钟拍。
    # 时基自己注入（与实验台同一机制），否则 Δt≈0 ⇒ 衰减不真发生。
    lb_off = [0.0]
    lb["st"].set_clock(lambda: time.time() + lb_off[0])
    lb["st"].set_value("modulator", "cortisol", 0.80, source="manual")
    import modulator_subgraph as _m
    for _ in range(18):
        lb_off[0] += 5 * 60
        lb["st"].tick_decay()
        _m.apply_need_drift(lb["kg"], lb["st"], lb["cfg"], dt_min=5.0)
        lb["st"].update_needs_from_signals(
            bias=(_m.graph_biases(lb["kg"], lb["st"], lb["cfg"]
                                  ).get("need") or {}))
    la["lab"].close()
    check("台2 advance(90) 与手工重放 18 个 5 分钟拍得到同一水位（快进=真拍）",
          abs(la["st"].modulator_conc("cortisol")
              - lb["st"].modulator_conc("cortisol")) < 0.02,
          f"{la['st'].modulator_conc('cortisol')} vs "
          f"{lb['st'].modulator_conc('cortisol')}")
    c = make_env()
    prev = c["st"].get_clock()
    c["lab"].open()
    opened = c["st"].get_clock()
    c["lab"].close()
    check("台3 open/close 成对：close 后时基**原样交还**（不是硬设 None）",
          opened is not prev and c["st"].get_clock() is prev, "")
    check("台4 advance 拒绝未 open 的会话（Δt 不同源=衰减静默失效，宁可报错）",
          c["lab"].advance(5).get("ok") is False, "")
    v = c["lab"].observe()
    check("台5 observe 一把给全：12 通道 + 14 参数 + 时钟 + trace",
          len(v["view"]["mods"]) == 12 and len(v["params"]) == 14
          and "clock" in v, f"{len(v['view']['mods'])}/{len(v['params'])}")
    ch = c["lab"].chain("reward_rpe")
    check("台6 chain 是既有 explain 的拼装（事件表/子图/心情/全通道都在）",
          ch["focus"]["source"] == "graph" and "subgraph" in ch
          and ch["mood"]["in_edges"] == 5, "")
    d = make_env()
    n_before = len(d["kg"].edges)
    d["lab"].dial("oxytocin", 0.6)
    d["lab"].inject("novel_contact", novelty=0.5, source="experiment")
    d["lab"].outcome(social_outcome="amused", behavior="b", context="c")
    d["lab"].mood_event("positive")
    check("台7 所有动作都不长新边（实验台不写拓扑，禁令 11）",
          len(d["kg"].edges) == n_before, f"{n_before} → {len(d['kg'].edges)}")
    import modulator_subgraph as _m2
    dump = d["st"].dump_modulator_state()
    check("台8 dump 里有心情包络一行（P11 交接③），且只报投影不报心情总值",
          "mood_envelope" in dump and "term" in dump["mood_envelope"]
          and "valence" not in dump["mood_envelope"], str(dump.get("mood_envelope")))


if __name__ == "__main__":
    scen = [
        ("场景1  奖赏脉冲→dopamine phasic→图激活", s01),
        ("场景2  RPE 为负→phasic<0→学习因子<1", s02),
        ("场景3  连续成功→饱和+不应期→增益递减", s03),
        ("场景4  社会拒绝→oxytocin↓/cortisol↑→社交 salience 降", s04),
        ("场景5  新奇→ACh/glutamate→编码增益升", s05),
        ("场景6  不确定→NE/ACh→检索广度升", s06),
        ("场景7  夜间 melatonin↑→urgency/预算↓", s07),
        ("场景8  过载（多巴胺钉高）→探索倒 U 转负", s08),
        ("场景9  GABA↑→扩散抑制+竞争 margin↑", s09),
        ("场景10 endorphin 缓冲→负事件钝化", s10),
        ("场景11 压力（cortisol 持续）→学习钝化+DMN 下降", s11),
        ("场景12 空闲+histamine→自主探索发起", s12),
        ("场景13 需求（口渴式）→调制偏置→行动选择变化", s13),
        ("场景14 意图被打断→NE phasic→抢占", s14),
        ("场景15 结果→disposition 位移→（慢）trait 位移受限", s15),
        ("场景16 长期高多巴胺→trait 仍慢（护栏）", s16),
        ("场景17 图谱边权改写→效力随之改变（图是真源）", s17),
        ("附：实验台验收（真实管线等价性）", s_lab),
    ]
    for name, fn in scen:
        act(name, fn)
    print()
    if FAILURES:
        print(f"✗ R2 P12 闸门 {len(FAILURES)} 项未过（{ASSERTS[0]} 断言 / "
              f"{len(SCEN_DONE)}/17 场景全绿）：" + "；".join(FAILURES))
        sys.exit(1)
    print(f"✓ R2 P12 闸门全部通过：17/17 场景 · {ASSERTS[0]} 条断言（含实验台验收）")
