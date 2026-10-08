# test_reward_disposition.py — 激素-奖赏-disposition 融合验收（A–H）
# ============================================================================
# 任务 §十七 的八项测试。核心：社会反馈与自我奖赏**分离**，激素只调制学习率
# 不直接成人格，reflection 无证据不得造人格，disposition 真进 attention。
# 全部用临时 data_dir + 内存图，不碰真实存档。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_reward_disposition.py
# ============================================================================

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


import config as C
from graph_model import KnowledgeGraph, Node, Edge
from disposition_store import DispositionStore
from internal_state import InternalState
from reward import RewardSystem, classify_self_outcome, classify_social_outcome
from diffusion_engine import DiffusionEngine


def build(with_reward=True):
    base = os.path.join(tempfile.gettempdir(), "fas_reward_test")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    cfg = dict(C.DEFAULT_CONFIG)
    kg = KnowledgeGraph()
    for nid in ("用户", "Self", "CuriosityDrive", "网络搜索", "行为:回应",
                "情境:用户分享", "Minecraft"):
        kg.add_node(Node(id=nid))
    ds = DispositionStore(kg, cfg)
    is_state = InternalState(kg=kg, config=cfg, persona=None, engine=None,
                             data_dir=base, save_graph_fn=lambda: None)
    reward = RewardSystem(internal_state=is_state, config=cfg) if with_reward else None
    return kg, ds, is_state, reward, base


# ═══════ A：用户喜欢 ≠ FAS 必须长期喜欢 ═══════
def test_A_social_not_dominant():
    print("\n── A 社会反馈不主导人格（§五）──")
    kg, ds, isst, reward, base = build()
    # 纯社会正反馈（用户 accepted），自我无收获（self=None）
    t_soc = ds.apply_experience("share", "情境:用户分享",
                                social_outcome="accepted", self_outcome=None,
                                hormone={"factor": 1.0}, allow_create=True)
    # 同等强度下的自我成功
    ds.apply_experience("explore", "情境:主动发起",
                        social_outcome=None, self_outcome="goal_success",
                        hormone={"factor": 1.0}, allow_create=True)
    soc_sig = t_soc["learning_signal"]
    # goal_success valence 0.6 self weight 0.6 → 0.6*0.25*0.6=0.09 > accepted 0.05
    self_sig = ds.get_pair("情境:主动发起", "explore").extra_attrs["learning_ledger"][0]["sig"]
    check("单次社会正反馈学习信号温和（≤0.06）", abs(soc_sig) <= 0.06, str(soc_sig))
    check("自我成功学习信号 > 社会赞许（self 权重更高）", self_sig > soc_sig,
          f"self={self_sig} social={soc_sig}")
    p = ds.get_pair("情境:用户分享", "share")
    check("社会正反馈后仍 candidate（一次喜欢≠长期喜欢）",
          p.extra_attrs["status"] == "candidate" and p.extra_attrs["positive_count"] == 1)
    shutil.rmtree(base, ignore_errors=True)


# ═══════ B：自身目标成功可形成倾向 ═══════
def test_B_self_success_forms_tendency():
    print("\n── B 自身目标成功 → 倾向增长 ──")
    kg, ds, isst, reward, base = build()
    ev = reward.evaluate(behavior="explore", context="情境:主动发起",
                         social_outcome=None, self_outcome="goal_success",
                         ref="自主explore成功")
    rel = reward.release(ev)
    mod = reward.modulation(key="explore@情境:主动发起",
                            surprise=rel["surprises"].get("explore@情境:主动发起"))
    t = ds.apply_experience("explore", "情境:主动发起",
                            social_outcome=None, self_outcome="goal_success",
                            hormone=mod, allow_create=True)
    check("自我目标成功建立 explore 倾向", t["written"] and t["new"] > t["old"])
    check("奖赏释放触发多巴胺 phasic（自身成功=奖赏）",
          abs(isst.dopamine_phasic()) > 1e-6, f"phasic={isst.dopamine_phasic()}")
    check("来源记为 self:goal_success（非 social）",
          ds.get_pair("情境:主动发起", "explore").extra_attrs["sources"] == {"self:goal_success": 1})
    shutil.rmtree(base, ignore_errors=True)


# ═══════ C：自身发现可形成探索倾向 ═══════
def test_C_discovery_explore():
    print("\n── C explore→discovery→reward→倾向↑ ──")
    kg, ds, isst, reward, base = build()
    # P6 证据门（2026-09-27）：observe 未知目标要算 discovery 必须带真观察
    # 证据（entity/dist/…）；纯字符串回执会被如实重分类为 goal_success
    so = classify_self_outcome("observe", success=True,
                               result={"result": {"entity": "zombie", "dist": 5.0}},
                               target_is_unknown=True)
    check("观察未知目标成功 → self_outcome=discovery", so == "discovery", so)
    t = ds.apply_experience("explore", "情境:主动发起", self_outcome=so,
                            hormone=reward.modulation(), allow_create=True)
    s0 = t["old"]; s1 = t["new"]
    check("发现给 explore 倾向带来最大正学习（valence 0.8）", s1 - s0 >= 0.12,
          f"{s0}->{s1}")
    # 重复发现持续累积（Test C 的"形成"含累积）
    for _ in range(4):
        t2 = ds.apply_experience("explore", "情境:主动发起", self_outcome="discovery",
                                 hormone=reward.modulation())
    st = ds.get_pair("情境:主动发起", "explore").extra_attrs["status"]
    check("反复发现 → 倾向可晋升", st in ("candidate", "stable"), str(st))
    shutil.rmtree(base, ignore_errors=True)


# ═══════ D：用户负反馈不直接删除 stable ═══════
def test_D_negative_not_delete_stable():
    print("\n── D stable + 单次负社会反馈不被抹掉 ──")
    kg, ds, isst, reward, base = build()
    for _ in range(6):
        ds.apply_experience("explore", "情境:主动发起",
                            self_outcome="discovery", hormone=reward.modulation(),
                            allow_create=True)
    p = ds.get_pair("情境:主动发起", "explore")
    p.extra_attrs["status"] = "stable"   # 确保前置 stable
    before = ds.list_dispositions()
    ds.apply_experience("explore", "情境:主动发起", social_outcome="rejected",
                        self_outcome=None, hormone=reward.modulation())
    p2 = ds.get_pair("情境:主动发起", "explore")
    check("单次社会拒绝后 pair 仍存活（不删除）", p2 is not None)
    check("负反馈降强度但不一次抹除 stable",
          p2 is not None and p2.extra_attrs["negative_count"] >= 1)
    shutil.rmtree(base, ignore_errors=True)


# ═══════ E：取消不算失败/负奖赏 ═══════
def test_E_cancelled_no_negative():
    print("\n── E 取消不产生负学习（§十）──")
    kg, ds, isst, reward, base = build()
    ds.apply_experience("explore", "情境:主动发起", self_outcome="goal_success",
                        hormone=reward.modulation(), allow_create=True)
    p = ds.get_pair("情境:主动发起", "explore")
    s0 = float(p.extra_attrs["strength"])
    neg0 = int(p.extra_attrs.get("negative_count", 0))
    r0 = isst.modulator_level("cortisol")
    ds.apply_experience("explore", "情境:主动发起", self_outcome="cancelled",
                        hormone=reward.modulation())
    p = ds.get_pair("情境:主动发起", "explore")
    check("cancelled 不减低强度", abs(float(p.extra_attrs["strength"]) - s0) < 1e-9,
          f"{s0}->{p.extra_attrs['strength']}")
    check("cancelled 不记负证据", int(p.extra_attrs["negative_count"]) == neg0)
    check("cancelled 不触发压力激素", abs(isst.modulator_level("cortisol") - r0) < 1e-9)
    so = classify_self_outcome("explore", True, result={"cancelled": True})
    check("classify_self_outcome 对 cancelled 返回 cancelled", so == "cancelled", so)
    shutil.rmtree(base, ignore_errors=True)


# ═══════ F：激素不直接成为人格 ═══════
def test_F_hormone_not_personality():
    print("\n── F 激素只调制学习率，不直接建人格 ──")
    kg, ds, isst, reward, base = build()
    n0 = len(ds.list_dispositions())
    # 拉满多巴胺
    isst.set_value("modulator", "dopamine", 1.0, reason="test high reward")
    isst.pulse_dopamine(0.6, reason="test phasic")
    hi = reward.modulation()
    isst.set_value("modulator", "dopamine", 0.0, reason="test low")
    isst._modulators["dopamine"]["phasic"] = 0.0
    isst.set_value("modulator", "cortisol", 1.0, reason="test stress")
    lo = reward.modulation()
    check("高奖赏态学习调制 > 低/压力态", hi["factor"] > lo["factor"],
          f"hi={hi['factor']} lo={lo['factor']}")
    check("调制项可解释（含 phasic/stress 分量）",
          {"base", "phasic", "stress", "factor"} <= set(hi.keys()))
    # 关键：只改激素，不产生任何 disposition
    check("改变激素不创建任何 disposition", len(ds.list_dispositions()) == n0,
          f"n0={n0} now={len(ds.list_dispositions())}")
    shutil.rmtree(base, ignore_errors=True)


# ═══════ G：Reflection 无证据不得造人格 ═══════
def test_G_reflection_evidence_gate():
    print("\n── G 反思候选必须可追溯到真实事件 ──")
    kg, ds, isst, reward, base = build()
    from episodic_buffer import EpisodicBuffer
    from reflection_engine import ReflectionEngine
    buf = EpisodicBuffer(capacity=50)
    buf.add_expression({"user_text": "我最近在玩Minecraft", "answer": "好玩吗",
                        "behavior": "ask", "context": "情境:用户分享",
                        "topics": ["Minecraft"]})
    eng = ReflectionEngine(kg, nlp_processor=None, config=dict(C.DEFAULT_CONFIG),
                           episodic_buffer=buf, disposition_store=ds)
    # 编造的、与真实事件无关的证据
    fake = {"candidate_dispositions": [
        {"behavior": "explore", "context": "情境:用户分享",
         "evidence": "她似乎天生热爱探索深海火山口"}]}
    r1 = eng._process_dispositions(fake)
    check("无真实事件支撑的候选被拒绝", r1["new_candidates"] == [] and r1["errors"],
          str(r1))
    check("拒绝后未创建该 disposition", ds.get_pair("情境:用户分享", "explore") is None)
    # 可追溯的证据（命中真实表达文本）
    real = {"candidate_dispositions": [
        {"behavior": "share", "context": "情境:用户分享",
         "evidence": "用户：我最近在玩Minecraft，聊得很开心"}]}
    r2 = eng._process_dispositions(real)
    check("可追溯到真实事件的候选允许建立", "倾向:share@用户分享" in r2["new_candidates"],
          str(r2["new_candidates"]))
    shutil.rmtree(base, ignore_errors=True)


# ═══════ H：disposition 能进入 attention ═══════
def test_H_disposition_into_attention():
    print("\n── H 情境激活 → 倾向边 → 行为节点获激活 ──")
    base = os.path.join(tempfile.gettempdir(), "fas_reward_attn")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    cfg = dict(C.DEFAULT_CONFIG)
    kg = KnowledgeGraph()
    for nid in ("用户", "Self"):
        kg.add_node(Node(id=nid))
    ds = DispositionStore(kg, cfg)
    # 建一条 strong 倾向（stable），边权=强度
    for _ in range(6):
        ds.apply_experience("respond", "情境:用户分享", self_outcome="goal_success",
                            allow_create=True)
    pair = ds.get_pair("情境:用户分享", "respond")
    pair.extra_attrs["status"] = "stable"
    ds._sync_activation_edge(pair)
    eng = DiffusionEngine(kg, cfg)
    # 情境节点被点亮（app 回合里注入的情境种子）
    ctx = kg.nodes["情境:用户分享"]
    ctx.activation = 2.0
    eng.mark_active(["情境:用户分享"])
    beh_before = kg.nodes["行为:回应"].activation
    eng.diffuse_round(max_steps=3)
    beh_after = kg.nodes["行为:回应"].activation
    check("行为节点因倾向边获得激活（disposition→attention）",
          beh_after > beh_before and beh_after > 0.01,
          f"回应 act {beh_before}->{beh_after}")
    # tendencies_for 混入实时激活
    t = ds.tendencies_for(["情境:用户分享"])
    check("tendencies_for 报告实时 activation 字段",
          t and "activation" in t[0], str(t))
    shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    test_A_social_not_dominant()
    test_B_self_success_forms_tendency()
    test_C_discovery_explore()
    test_D_negative_not_delete_stable()
    test_E_cancelled_no_negative()
    test_F_hormone_not_personality()
    test_G_reflection_evidence_gate()
    test_H_disposition_into_attention()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAIL: {len(FAILURES)} 项未通过")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS: A–H 全部通过")
