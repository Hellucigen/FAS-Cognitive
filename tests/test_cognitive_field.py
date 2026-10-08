# test_cognitive_field.py — Tension→Drive→Network→Modulation 四层动力学单测
# ============================================================================
# Drive 重构（2026-09-20）新增层的验收：
#   * 张力/驱动的时间持续性（rise 快 fall 慢）与 settle 即时性
#   * 多驱并存 + 侧向软竞争（非 winner-take-all）
#   * 网络惯性（不允许 0.51/0.49 每秒翻转）与双高混合态
#   * 调制层 clamp / 平滑 / effects 表 / 代码 contributor
#   * CognitiveField 编排 + 持久化往返 + 异常降级
# 离线，无 LLM、无网络。运行:
#   E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_cognitive_field.py

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from tension_field import TensionField
from drive_field import DriveField
from cognitive_networks import NetworkField
from modulation import ModulationLayer
from cognitive_field import (CognitiveField, DEFAULT_TENSIONS, DEFAULT_DRIVES,
                             DEFAULT_NETWORKS)

FAILURES = []


def check(name, cond, detail=""):
    st = "PASS" if cond else "FAIL"
    print(f"[{st}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ═══ 1. TensionField 动力学 ═══

tf = TensionField(
    {"x": {"components": [{"source": "px", "coef": 1.0}], "rise": 0.5, "fall": 0.1}},
    samplers={})
tf.register_sampler("px", lambda: 1.0)
lv = tf.step()
check("张力上升沿趋近（rise）", 0.4 < lv["x"] < 0.6, str(lv))
for _ in range(3):
    lv = tf.step()
check("张力持续逼近 1", lv["x"] > 0.9, str(lv))
state = tf.get_state()          # 在高位取快照（持久化往返用）
tf.register_sampler("px", lambda: 0.0)
before = tf.intensity("x")
lv = tf.step()
check("信号消失后张力衰减缓慢（fall<rise → 持续性）",
      before - lv["x"] < before * 0.2, f"{before} → {lv['x']}")
lv = tf.step(settle=True)
check("settle 一步到位（回合内强制读数）", lv["x"] == 0.0, str(lv))
# 多分量合成 + 饱和
tf2 = TensionField({"y": {"components": [
    {"source": "a", "coef": 0.6, "scale": 0.5},
    {"source": "b", "coef": 0.5}], "rise": 1.0, "fall": 0.1}})
tf2.register_sampler("a", lambda: 10.0)   # 10×0.5=5 → 饱和 1 → 0.6
tf2.register_sampler("b", lambda: 0.4)    # 0.2
lv = tf2.step(settle=True)
check("多分量加权和+逐分量饱和", abs(lv["y"] - 0.8) < 1e-6, str(lv))
# 采样器异常/缺位 → 按 0 参与，不抛出
tf3 = TensionField({"z": {"components": [{"source": "boom"}, {"source": "gone"}]}})
tf3.register_sampler("boom", lambda: 1 / 0)
lv = tf3.step()
check("采样器异常降级为 0（不抛出）", lv["z"] == 0.0, str(lv))
# 持久化往返
tf_b = TensionField(
    {"x": {"components": [{"source": "px", "coef": 1.0}], "rise": 0.5, "fall": 0.0}})
tf_b.register_sampler("px", lambda: 0.0)
tf_b.load_state(state)
check("状态恢复（fall=0 → 保存的 level 保持）", tf_b.intensity("x") > 0.9,
      str(tf_b.intensity("x")))

# ═══ 2. DriveField 聚合与并存 ═══

tf4 = TensionField({
    "novelty": {"components": [{"source": "n"}], "rise": 1.0, "fall": 0.1},
    "absence": {"components": [{"source": "s"}], "rise": 1.0, "fall": 0.1},
    "stuffy": {"components": [{"source": "f"}], "rise": 1.0, "fall": 0.1},
})
tf4.register_sampler("n", lambda: 0.8)
tf4.register_sampler("s", lambda: 0.6)
tf4.register_sampler("f", lambda: 0.0)
t = tf4.step()
df = DriveField({
    "explore": {"node": "ExploreDrive",
                 "affinities": {"novelty": 0.5, "stuffy": -0.5},
                 "rise": 0.5, "fall": 0.1},
    "affiliation": {"node": "AffDrive", "affinities": {"absence": 0.6},
                     "rise": 0.5, "fall": 0.1},
    "_lateral": {"explore": {"affiliation": 0.5},
                  "affiliation": {"explore": 0.5}},
})
d = df.step(t, settle=True)
check("Drive = 张力亲和聚合（含负亲和）", abs(d["explore"] - 0.4) < 1e-6, str(d))
check("多驱并存：两驱同时 >0", d["explore"] > 0 and d["affiliation"] > 0, str(d))
check("激活换算到图谱尺度 [0,5]", df.activation("explore") ==
      round(d["explore"] * 5.0, 4))
# 侧向抑制是乘性压缩、不清零
df0 = DriveField({"a": {"affinities": {"absence": 1.0}}, "b": {
    "affinities": {"absence": 1.0}}, "_lateral": {"a": {"b": 1.0}}})
d2 = df0.step(tf4.levels(), settle=True)
check("极端侧抑（κ=1）也仅是压缩目标而非归零后互杀",
      d2["a"] > 0 and d2["b"] > 0, str(d2))
# 亲和增益（催产素式调制器落点）
d3 = df.step(t, settle=True, affinity_gain={"affiliation.absence": 1.0})
check("affinity_gain 抬升对应 Drive", d3["affiliation"] > d["affiliation"],
      f"{d['affiliation']} → {d3['affiliation']}")

# ═══ 3. NetworkField：惯性、双高、非二选一 ═══

nf = NetworkField({
    "CEN": {"inputs": {"context.task": 1.0}, "rise": 0.3, "fall": 0.1,
             "slew": 0.12},
    "DMN": {"inputs": {"context.idle": 0.9}, "rise": 0.12, "fall": 0.2,
             "slew": 0.08},
    "_lateral": {"CEN": {"DMN": 0.25}, "DMN": {"CEN": 0.2}},
})
lv = nf.step({}, {}, {"task": 0.5})
check("单拍 slew 限幅（0.5 任务 → CEN 不跳半程）", lv["CEN"] <= 0.12 + 1e-9,
      str(lv))
# 持续任务：多拍爬升，但不翻转
ctx_task, ctx_idle = {"task": 1.0}, {"task": 0.0}
levels = []
for _ in range(50):
    levels.append(nf.step({}, {}, ctx_task))
peak = levels[-1]["CEN"]
for _ in range(2):
    near = nf.step({}, {}, ctx_idle)
check("任务信号瞬时消失 → CEN 每拍回落受 fall/slew 限幅（惯性/迟滞）",
      peak - near["CEN"] < 0.25, f"{peak} → {near['CEN']}")
# 双高混合态可达
nf2 = NetworkField({
    "CEN": {"inputs": {"context.task": 1.0}, "rise": 1.0, "fall": 1.0},
    "DMN": {"inputs": {"context.wander": 1.0}, "rise": 1.0, "fall": 1.0},
    "_lateral": {"CEN": {"DMN": 0.25}, "DMN": {"CEN": 0.25}},
})
lv = nf2.step({}, {}, {"task": 1.0, "wander": 1.0}, settle=True)
check("双高混合态允许并存（非互斥开关）",
      lv["CEN"] > 0.6 and lv["DMN"] > 0.6, str(lv))
# 注册表扩展性：第三个网络零改结构
nf3 = NetworkField({
    "SN": {"inputs": {"tension.surprise": 1.0}, "rise": 1.0, "fall": 1.0},
})
lv = nf3.step({"surprise": 0.7}, {}, {}, settle=True)
check("新增网络（SN）只需 spec 数据，不改模块", abs(lv["SN"] - 0.7) < 1e-6,
      str(lv))

# ═══ 4. ModulationLayer ═══

ml = ModulationLayer({
    "p.width": {"baseline": 3.0, "min": 1.0, "max": 6.0,
                 "effects": {"network.CEN": -0.5, "network.DMN": 0.5},
                 "alpha": 1.0},
    "p.smooth": {"baseline": 1.0, "min": 0.0, "max": 10.0,
                  "effects": {"network.CEN": 9.0}, "alpha": 0.2},
    "p.clamped": {"baseline": 1.0, "min": 0.2, "max": 2.0,
                   "effects": {"network.DMN": 5.0}, "alpha": 1.0},
})
ml.compute({"network.CEN": 1.0, "network.DMN": 0.0})
check("effects 表乘子生效", ml.get("p.width") == 1.5, str(ml.get("p.width")))
check("平滑参数首拍只走 alpha 一段（1→2.8，非直达 10）",
      abs(ml.get("p.smooth") - 2.8) < 1e-9, str(ml.get("p.smooth")))
ml.compute({"network.CEN": 0.0, "network.DMN": 0.0})
check("信号归零 → 回 baseline", abs(ml.get("p.width") - 3.0) < 1e-9)
check("缺位信号按 0 参与", abs(ml.get("p.width") - 3.0) < 1e-9)
ml.compute({"network.DMN": 1.0})
check("min/max 限幅生效", ml.get("p.clamped") == 2.0, str(ml.get("p.clamped")))
ml.compute({"network.CEN": 1.0})
check("负向效应受 min 保护", ml.get("p.width") >= 1.0)
ml.register("p.width", lambda sig: 1.0 + 0.5 * sig.get("context.extra", 0))
ml.compute({"network.CEN": 1.0, "context.extra": 1.0})
check("代码 contributor 复乘（0.5×1.5 效应在 min 之上）",
      abs(ml.get("p.width") - 3.0 * 0.5 * 1.5) < 1e-6, str(ml.get("p.width")))
ml.compute({})
check("contributor 撤回信号后回稳", ml.get("p.width") > 1.4)
snap = ml.snapshot()
check("快照含 factors（可解释）", "effects" in snap["p.width"]["factors"],
      str(snap["p.width"]))

# ═══ 5. CognitiveField 编排 ═══

cfgf = tempfile.mktemp(suffix=".json")
cf = CognitiveField(config={}, state_file=cfgf)
cf.register_sampler("novel_objects", lambda: 8.0)
cf.register_sampler("prediction_surprise", lambda: 0.9)
cf.register_context("task_engaged", lambda: 0.9)
r = cf.step(settle=True)
check("step 输出三层 levels", {"tensions", "drives", "networks"} <= set(r),
      str(r.keys()))
check("novelty 张力被聚合进 curiosity", r["drives"]["curiosity"] > 0.05,
      str(r["drives"]))
check("任务语境抬 CEN 压 DMN", r["networks"]["CEN"] > r["networks"]["DMN"],
      str(r["networks"]))
snap = cf.snapshot()
check("调制快照有默认参数表", "diffusion.max_depth" in snap["modulation"],
      str(list(snap["modulation"])[:3]))
md = snap["modulation"]["diffusion.max_depth"]
check("CEN 压制扩散深度（<baseline）", md["value"] < 3.0, str(md))
ten = snap["action_tendencies"]
check("action_tendencies 派生（continue_task 高）",
      ten.get("continue_task", 0) > ten.get("ask_user", 0), str(ten))
expl = ("novelty" in snap["tension_detail"] and
        snap["tension_detail"]["novelty"]["level"] > 0)
check("张力明细含 raw 溯源", expl and
      snap["tension_detail"]["novelty"]["raw"], str(
          snap["tension_detail"]["novelty"].get("raw")))
cf.save_state()
cf2 = CognitiveField(config={}, state_file=cfgf)
check("状态持久化往返（张力连续性）",
      cf2.tensions.intensity("prediction_error") > 0.85,
      str(cf2.tensions.intensity("prediction_error")))
os.path.exists(cfgf) and os.remove(cfgf)
txt = cf.explain()
check("explain 快照含五段", all(k in txt for k in (
    "Current Tensions", "Current Drives", "Networks",
    "Effective modulation", "Action tendencies")), txt[:200])
# 无 context/hormone 接线时的降级（全部按 0，不抛出）
cf3 = CognitiveField(config={}, state_file=tempfile.mktemp())
r3 = cf3.step()
check("裸装配可运行（provider 全缺位）",
      all(v >= 0.0 for v in r3["drives"].values()), str(r3["drives"]))
# 激素调制器：多巴胺抬升上升速率、降低消退速率（不进加数）
cf4 = CognitiveField(config={}, state_file=tempfile.mktemp())
cf4.register_sampler("novel_objects", lambda: 6.0)
cf4.register_state("dopamine", lambda: 0.5)
cf4.step()          # 建 level
cf4.register_sampler("novel_objects", lambda: 0.0)
cf4.register_state("dopamine", lambda: 1.0)   # 高多巴胺
slow = CognitiveField(config={}, state_file=tempfile.mktemp())
slow.register_sampler("novel_objects", lambda: 6.0)
slow.register_state("dopamine", lambda: 0.5)
slow.step()
slow.register_sampler("novel_objects", lambda: 0.0)
for _ in range(3):
    hi = cf4.drives.level("curiosity")
    cf4.step()
    slow.step()
check("高多巴胺 → 消退更慢（调制器语义，非加数）",
      cf4.drives.level("curiosity") > slow.drives.level("curiosity"),
      f"{cf4.drives.level('curiosity')} vs {slow.drives.level('curiosity')}")

# ═══ 6. 默认亲和表数值锚定（旧四驱稳态方向一致）═══

cf5 = CognitiveField(config={}, state_file=tempfile.mktemp())
cf5.register_sampler("social_idle_minutes", lambda: 45.0)
cf5.register_sampler("social_need", lambda: 0.8)
cf5.register_sampler("players_present", lambda: 1.0)
cf5.register_sampler("recent_goal_failures", lambda: 2.0)
cf5.register_sampler("causal_blockers", lambda: 1.0)
cf5.register_sampler("novel_objects", lambda: 3.0)
cf5.register_sampler("competence_need", lambda: 0.6)
cf5.register_sampler("pending_reflection_candidates", lambda: 3.0)
cf5.register_sampler("recent_negative_reward_ratio", lambda: 0.5)
cf5.register_sampler("state.mood_deficit",
                     lambda: (0.4, {"valence": -0.4}))
cf5.register_state("oxytocin", lambda: 0.5)
r5 = cf5.step(settle=True)
check("social 稳态 > 2.0（旧阈值锚定）", r5["drives"]["social"] * 5 > 2.0,
      str(r5["drives"]))
check("learning 稳态 > 1.5", r5["drives"]["learning"] * 5 > 1.5)
check("consistency 稳态 > 2.2", r5["drives"]["consistency"] * 5 > 2.2)
check("催产素走调制通道：social 增益为正",
      cf5.drives.target_of("social", r5["tensions"],
                           {"social.belonging_need": 0.3}) >=
      r5["drives"]["social"] - 1e-9)

# ═══ 7. 调制 → 行为竞争 / 扩散 的消费闭环（集成）═══

from graph_model import KnowledgeGraph, Node
from dialogue_decision import dialogue_decide
from modulation import ModulationLayer


class _StubEngine:
    def get_topk(self, k=10):
        return [], {}

    def mark_active(self, ids):
        pass


kgd = KnowledgeGraph()
kgd.add_node(Node(id="Self", weight=0.5, graph_space="self"))
kgd.add_node(Node(id="行为:探索", weight=0.5, graph_space="self"))
parsed = {"dialogue_act": "information_statement",
          "response_expectation": "low", "needs_reply": False}
expl = {"candidates": [{"action": "explore_ask", "score": 0.9,
                        "target": "UnknownConcept_X"}]}
r_free = dialogue_decide(parsed, "嗯", kgd, _StubEngine(), [], False,
                         False, None, False, exploration=expl)
mod = ModulationLayer({
    "behavior.explore_margin": {"baseline": 1.0, "min": 0.0, "max": 2.0,
                                 "alpha": 1.0},
    "behavior.exploration_rate": {"baseline": 1.0, "alpha": 1.0},
}, smoothing_alpha=1.0)
mod.compute({})
r_cen = dialogue_decide(parsed, "嗯", kgd, _StubEngine(), [], False,
                        False, None, False, exploration=expl,
                        modulation=mod)
check("无调制层=旧静态行为（探索胜出）",
      r_free["decision"] == "explore_ask", str(r_free["decision"]))
check("调制层（高打断门槛）改变竞争结果（CEN 式守在正事上）",
      r_cen["decision"] != "explore_ask", str(r_cen["decision"]))
check("探索落选留痕含调制后 margin",
      "margin" in str(r_cen["factors"].get("探索落选", "")),
      str(r_cen["factors"].get("探索落选")))

# 扩散引擎回退契约：无 modulation → config 行为；有 → 读调制参数
import diffusion_engine as de
from diffusion_engine import DiffusionEngine
import config as _cfg
kg2 = KnowledgeGraph()
eng2 = DiffusionEngine(kg2, dict(_cfg.DEFAULT_CONFIG))
check("engine.modulation 默认缺位（离线回退 config）",
      hasattr(eng2, "modulation") and eng2.modulation is None)
check("无调制层 max_depth = config", eng2._mod(
    "diffusion.max_depth", _cfg.DEFAULT_CONFIG["max_depth"])
    == _cfg.DEFAULT_CONFIG["max_depth"])
eng2.modulation = ModulationLayer(
    {"diffusion.max_depth": {"baseline": 5.0, "alpha": 1.0}},
    smoothing_alpha=1.0)
eng2.modulation.compute({})
check("有调制层：读点切到 ModulationLayer",
      eng2._mod("diffusion.max_depth", 3) == 5.0)
_before = eng2._param_gain
eng2.update_param_modulation(0.5, 0.2)
check("调制层在位但无 param_gain 参数 → 保持旧值（不抛错不清零）",
      eng2._param_gain == _before, str(eng2._param_gain))

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ Tension→Drive→Network→Modulation 四层动力学验收通过")
