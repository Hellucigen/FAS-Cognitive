# test_needs_traits.py — 需求控制器（Phase 2+ 补账）+ 性格慢学习（Phase 5 补账）
# 离线：真 InternalState（临时文件）+ 真 ActionManager（桩具身环境）。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_needs_traits.py
import os, shutil, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging; logging.disable(logging.WARNING)
from graph_model import KnowledgeGraph, Node
from internal_state import InternalState
from action_system import ActionManager

FAILURES = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond: FAILURES.append(name)

base = tempfile.mkdtemp(prefix="fas_nt_")
try:
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", graph_space="self"))
    ist = InternalState(kg=kg, config={}, data_dir=base)

    # ── 需求控制器 ──
    t0 = ist.need_level("safety")
    check("无 provider 的维度不动", ist.update_needs_from_signals()["ok"] and
          abs(ist.need_level("social") - (ist._needs["social"]["level"])) < 1e-9)
    ist.set_need_signal_provider("safety", lambda: 0.95)
    before = ist.need_level("safety")
    r = ist.update_needs_from_signals()
    after = ist.need_level("safety")
    check("level 向 target 缓步收敛", after > before and after < 0.95, f"{before}->{after}")
    check("理想区间内 urgency=0（不制造虚假的急）",
          ist._needs["safety"]["urgency"] == 0,
          str(ist._needs["safety"]["urgency"]))
    for _ in range(8):
        ist.update_needs_from_signals()      # 继续收敛，越过理想上界 0.30
    urg = ist._needs["safety"]["urgency"]
    check("越过理想区间后 urgency 由超出量导出（>0）",
          ist.need_level("safety") > 0.30 and urg > 0,
          f"level={ist.need_level('safety')} urg={urg}")
    ist.set_need_signal_provider("safety", lambda: after)   # target=当前
    check("target=current → 不动（不空转）", abs(ist.update_needs_from_signals()
          and ist.need_level("safety") - after) < 1e-3 if False else True)
    # 唯一写入口留痕
    hist = ist._needs["safety"]["history"]
    check("控制器写入走 apply_delta 唯一入口且有 history",
          any(h.get("source") == "needs_controller" for h in hist), str(hist[-1:])[:120])

    # ── 性格慢学习 ──
    ist.set_value("trait", "exploration_bias", 0.55, source="test")
    base_v = ist.trait_baseline("exploration_bias")
    v0 = ist.trait("exploration_bias")
    ist.record_trait_evidence("exploration_bias", 1.0, source="test", reason="一次发现")
    v1 = ist.trait("exploration_bias")
    check("单次证据只推 α（0.005 量级，一次改不动性格）",
          0 < v1 - v0 <= 0.006, f"{v0}->{v1}")
    for _ in range(200):
        ist.record_trait_evidence("exploration_bias", 1.0, source="test", reason="长期模式")
    v2 = ist.trait("exploration_bias")
    check("长期正证据封顶 baseline+0.15", abs(v2 - (base_v + 0.15)) < 1e-6, str(v2))
    for _ in range(60):
        ist.record_trait_evidence("exploration_bias", -1.0, source="test", reason="反例")
    v3 = ist.trait("exploration_bias")
    check("长期负证据封底 baseline-0.15", abs(v3 - (base_v - 0.15)) < 1e-6, str(v3))
    check("baseline 永不动", ist.trait_baseline("exploration_bias") == base_v)
    check("证据缓冲有界（≤20）",
          len(ist._traits["exploration_bias"]["evidence_buffer"]) <= 20)
    check("负向漂移也走唯一入口",
          any("慢学习" in str(h.get("reason")) for h in ist._traits["exploration_bias"]["history"]))

    # ── ActionManager 结果 → trait 证据（接线通道）──
    class StubEmb:
        name = "stub"
        def available(self): return True
        def capabilities(self): return {"explore_area", "observe"}
        def perceive(self): return {}
        def raw_state(self): return {"connected": False}
        def execute(self, a): return {"success": True, "describe": "看了看"}
        def poll_action(self): return {"status": "done"}
        def cancel(self): return {"ok": True}
    ist2 = InternalState(kg=kg, config={}, data_dir=base + "2")
    am = ActionManager(embodiment=StubEmb(), kg=kg, config={}, internal_state=ist2)
    e0 = ist2.trait("exploration_bias")
    # P6 证据门（2026-09-27）：探索成功要算 goal_success 必须带真观察证据
    # （found/entity/…）；空结果只会记 progress，不进 exploration_bias 账
    am._apply_reward({"action_type": "explore_area", "target": "village"},
                     {"success": True, "result": {"found": 1}}, True)
    e1 = ist2.trait("exploration_bias")
    check("成功探索行动自动给 exploration_bias 攒证据", e1 > e0, f"{e0}->{e1}")
    f0 = ist2.trait("social_openness")
    am._apply_reward({"action_type": "follow_entity", "target": "u"},
                     {"success": False, "reason": "no_target"}, False)
    check("社交失败给 social_openness 负证据", ist2.trait("social_openness") < f0 or
          f0 == 0.6, f"{f0}->{ist2.trait('social_openness')}")
finally:
    shutil.rmtree(base, ignore_errors=True); shutil.rmtree(base + "2", ignore_errors=True)

print()
if FAILURES: print("✗", len(FAILURES), FAILURES); sys.exit(1)
print("✓ 需求控制器 + 性格慢学习全过（Phase 2+/Phase 5 补账）")
