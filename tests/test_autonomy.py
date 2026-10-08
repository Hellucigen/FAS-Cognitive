# test_autonomy.py — 自主认知与行动闭环测试
# 离线、临时 KG、临时具身环境桩（不碰真实数据、不连 Minecraft、不调 LLM）。
# 覆盖用户规范 §13：自主循环 / 意图与目标 / 行动反馈 / 认知整合 / LLM 边界。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_autonomy.py

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from graph_model import KnowledgeGraph, Node
from autonomy import AutonomousLoop, ST_OFF, ST_UNAVAILABLE, ST_IDLE
import config as _C
from capability_graph import CapabilityIndex
from action_concepts import ensure_action_concepts
from cognitive_regulation import CognitiveRegulation

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class StubEmbodiment:
    """具身环境桩：唯一职责是返回可编排的感知/执行结果。"""

    name = "stub_env"

    def __init__(self):
        self.available_flag = True
        self.capabilities_set = {
            # 旧语义意图（兼容）+ 新路径动作名（ActionManager 单一路径）
            "observe", "approach", "follow", "explore", "collect", "rest",
            "communicate", "withdraw",
            "inspect_entity", "inspect_area", "navigate_to_entity",
            "follow_entity", "explore_direction", "explore_area",
            "gather_resource", "stop", "retreat", "recover_health",
            "eat_food", "collect_food", "collect_dropped_item"}
        self.percept = {
            "connected": True, "health": 20, "food": 20,
            "position": {"x": 0.0, "y": 64.0, "z": 0.0},
            "players": [], "entities": [], "blocks": [],
            "unknown_entities": [], "unknown_blocks": [],
        }
        self.executed = []
        self.next_result = {"success": True, "action": "stub", "result": "ok"}
        self.pending_status = {"status": "done"}
        self.cancel_calls = 0

    def available(self):
        return self.available_flag

    def capabilities(self):
        return set(self.capabilities_set)

    def perceive(self):
        return dict(self.percept)

    def execute(self, intent):
        self.executed.append(dict(intent))
        return dict(self.next_result)

    def poll_action(self):
        return dict(self.pending_status)

    def cancel(self):
        self.cancel_calls += 1
        return {"ok": True}


class FakePersona:
    def __init__(self):
        self.events = []

    def mood_event(self, kind):
        self.events.append(kind)

    def current_mood(self):
        return {"valence": 0.2, "state": "平静偏暖"}


def build(tmp="/tmp_fas_auto_test"):
    """临时 KG + 临时数据目录 + 真实 CognitiveRegulation（锁要真生效）。"""
    base = os.path.join(tempfile.gettempdir(), "fas_test_autonomy")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    kg = KnowledgeGraph()
    for nid in ("用户", "Haru的位置", "Haru的血量", "Haru的手持物", "好奇", "看屏幕",
                "进入Minecraft世界"):
        kg.add_node(Node(id=nid, activation=0.0))
    kg.add_node(Node(id="CuriosityDrive", activation=3.0))

    outcomes = []
    persona = FakePersona()
    reg = CognitiveRegulation(kg=kg, data_dir=base)
    # mode_default=off：本文件锁定的是"关闭开关仍然有效"的契约
    # （默认值已于 2026-09-19 改为 on，见 config.autonomy.mode_default）
    loop = AutonomousLoop(kg=kg, config={"autonomy": {"mode_default": "off"}},
                          regulation=reg, persona=persona,
                          data_dir=base,
                          note_outcome_fn=lambda neg: outcomes.append(neg))
    emb = StubEmbodiment()
    loop.register_embodiment(emb)
    # 生产形态（2026-09-21 具身图谱化）：候选发现走能力图谱路径
    for slot in ("未知信息", "附近的生物", "Haru的血量", "Haru的饥饿",
                 "附近的玩家", "Haru的位置", "地面物品"):
        if slot not in kg.nodes:
            kg.add_node(Node(id=slot, weight=0.4, graph_space="cognitive",
                             extra_attrs={"type": "dynamic_state",
                                          "value": None}))
    # affordance 源节点激活（生产由感知/事件维护；桩里给地板值）
    with kg._lock:
        kg.nodes["未知信息"].activation = 1.0
        kg.nodes["附近的生物"].activation = 1.0
        kg.nodes["Haru的血量"].activation = 1.0
        kg.nodes["Haru的饥饿"].activation = 1.0
        kg.nodes["附近的玩家"].activation = 1.0
        kg.nodes["Haru的位置"].activation = 1.0
        kg.nodes["地面物品"].activation = 1.0
    ensure_action_concepts(kg, None)     # 生产同形：12 具身概念播种
    ci = CapabilityIndex(kg, None, dict(_C.DEFAULT_CONFIG))
    ci.embodiment = emb
    ci.ensure_graph()
    loop.cap_index = ci
    return kg, reg, loop, emb, persona, outcomes, base


def with_unknown(emb):
    emb.percept = dict(emb.percept)
    emb.percept["unknown_entities"] = [
        {"name": "axolotl", "displayName": "美西螈", "dist": 5.0}]
    emb.percept["entities"] = [
        {"name": "axolotl", "displayName": "美西螈", "dist": 5.0}]


# ── 1. 自主循环：没有用户指令也能跑；模式关闭即停 ──────────
kg, reg, loop, emb, persona, outcomes, base = build()
with_unknown(emb)
r = loop.tick(now=1000.0)
check("模式关闭时不行动", r.get("acted") is False and r.get("reason") == "mode_off", str(r))
check("模式关闭时 phase=off", loop.phase == ST_OFF)

loop.set_mode("on")
r = loop.tick(now=1000.0)
check("没有用户指令时自主产生并执行意图", r.get("acted") is True, str(r))
check("执行的确实是候选里的动作", r.get("intent") in
      ("inspect_entity", "navigate_to_entity", "explore_direction", "explore_area",
       "gather_resource", "communicate", "follow_entity", "recover_health",
       "eat_food", "collect_food", "retreat"),
      str(r))
check("具身环境真的收到了动作", len(emb.executed) == 1, str(emb.executed))
loop.set_mode("off")
r = loop.tick(now=2000.0)
check("关闭后立即停止行动", r.get("acted") is False and len(emb.executed) == 1, str(r))

# ── 2. 用户优先：交互打断 + 安静期 ────────────────────────
loop.set_mode("on")
loop.notify_user_activity(quiet_s=30, now=2000.0)
r = loop.tick(now=2001.0)
# 2026-09-21：固定安静期封门已拆除——用户优先由"取消在飞自主动作 +
# min_interval + user_recent 调制抬阈值"共同保证，不再是硬编码时段禁闭。
# 2026-09-21 语义：用户交互=让位礼（取消在飞自主动作）+ 调制抬阈值
    # （生产有 drive_evaluator 时 user_recent 抬高 action 门槛）；
    # 桩环境无调制层时允许立即决策——固定时段禁闭已不是设计。
check("用户交互让位：在飞自主动作被取消（若有），决策不被硬封门",
      (not loop.actions.busy()) and (r.get("acted") is True
      or r.get("reason") in ("min_interval", "no_change")), str(r))
loop.notify_user_activity(quiet_s=30, now=2039.5)
r = loop.tick(now=2040.0)      # 交互间隙已过
check("间隔后自主行动恢复（无 20s 硬封门）", r.get("acted") is True, str(r))

# ── 3. Bot 未连接 → 不执行动作 ────────────────────────────
emb.available_flag = False
loop.notify_user_activity(quiet_s=0, now=2999.0)
r = loop.tick(now=3000.0)
check("具身环境不可用时不动手", r.get("acted") is False
      and r.get("reason") == "embodiment_unavailable", str(r))
check("不可用时 phase=unavailable", loop.phase == ST_UNAVAILABLE)
emb.available_flag = True

# ── 4. 不会变成高速循环（最小间隔） ───────────────────────
loop.notify_user_activity(quiet_s=0, now=3999.0)
r1 = loop.tick(now=4000.0)
r2 = loop.tick(now=4001.0)
check("连续 tick 只行动一次（最小间隔生效）",
      r1.get("acted") is True and r2.get("reason") == "min_interval", str(r2))
r3 = loop.tick(now=4000.0 + loop.cfg["min_interval_s"] + 0.1)
check("间隔过后可以再次行动", r3.get("acted") is True, str(r3))

# ── 5. 候选来源真实、竞争可解释、确定性 ────────────────────
kg2, reg2, loop2, emb2, p2, o2, base2 = build()
loop2.set_mode("on")
with_unknown(emb2)
st = loop2.state()
loop2.tick(now=1000.0)
st = loop2.state()
cands = st["candidates"]
check("候选来自真实状态（未知生物 → 观察/靠近）",
      any(c["type"] == "inspect_entity" for c in cands)
      and any(c["type"] == "navigate_to_entity" for c in cands), str(cands))
check("每个候选都带可解释依据", all(c.get("explain") for c in cands), str(cands))
check("评分含分量明细（不是黑箱）",
      all("drive=" in c["explain"] and "att=" in c["explain"] for c in cands),
      str(cands[0]["explain"] if cands else ""))
# 确定性：同样状态 → 同样选择
kg3, reg3, loop3, emb3, p3, o3, base3 = build()
loop3.set_mode("on")
with_unknown(emb3)
a = loop3.tick(now=1000.0)
kg4, reg4, loop4, emb4, p4, o4, base4 = build()
loop4.set_mode("on")
with_unknown(emb4)
b = loop4.tick(now=1000.0)
check("同一状态 → 同一决策（非随机）",
      a.get("intent") == b.get("intent") and a.get("target") == b.get("target"),
      f"{a.get('intent')}@{a.get('target')} vs {b.get('intent')}@{b.get('target')}")
# 不同状态（危险在场）→ 决策不同（安全优先）
emb3.percept = dict(emb3.percept)
emb3.percept["entities"] = [{"name": "zombie", "displayName": "僵尸", "dist": 2.0}]
emb3.percept["unknown_entities"] = []
loop3.notify_user_activity(quiet_s=0, now=1099.0)
c = loop3.tick(now=1100.0)
check("状态变化（敌对生物靠近）→ 决策变为安全动作（撤离或经核验的反制）",
      c.get("intent") in ("retreat", "attack_entity")
      and c.get("target") == "zombie", str(c))

# ── 6. 意图与行动分离：低分只观察不动作 ────────────────────
kg5, reg5, loop5, emb5, p5, o5, base5 = build()
loop5.set_mode("on")
emb5.percept = {"connected": True, "health": 20, "food": 20, "position": {},
                "players": [], "entities": [], "blocks": [],
                "unknown_entities": [], "unknown_blocks": []}
loop5.cfg["explore_interval_s"] = 10 ** 9   # 本用例锁"无依据不编造"：关掉探索窗口
r = loop5.tick(now=1000.0)
# 新契约（2026-09-25，用户："真人会因为没到阈值就站游戏里不动吗"）：
# 无依据 ≠ 身体也停机——空闲给一个有界的踱步（不冒充有价值行动），
# 节流期内回到纯观察。
if r.get("reason") == "amble":
    check("无依据 → 空闲踱步（低强度身体活动，非编造意图）",
          r.get("acted") is True, str(r))
    _st5 = loop5.actions.status()
    _seen = ([_st5.get("current")] if _st5.get("current") else []) \
        + (_st5.get("recent") or [])
    _amb = [a for a in _seen if (a or {}).get("action_type") == "amble"
            or (a or {}).get("action") == "amble"]
    check("踱步是低优先级 ambient（priority≤0.31，不冒充高价值）",
          _amb and all(float(a.get("priority") or 0) <= 0.31 for a in _amb),
          str(_seen[-1] if _seen else "no-record")[:100])
    r2 = loop5.tick(now=1030.0)
    check("踱步节流期内不再提（回到纯观察）",
          r2.get("acted") is False, str(r2)[:100])
else:
    check("没有任何依据时不编造想法（idle 或有界空闲活动）",
          r.get("acted") is False
          and r.get("reason") in ("no_candidates", "below_threshold")
          or (r.get("acted") is True
              and r.get("reason") in ("amble", "idle_explore")),
          str(r))
    if r.get("acted") is False:
        check("idle 时没有动作被执行", not emb5.executed)

# ── 7. 行动反馈：成功入图 + 情绪 + 表达抑制链 ──────────────
kg6, reg6, loop6, emb6, p6, o6, base6 = build()
loop6.set_mode("on")
with_unknown(emb6)
emb6.next_result = {"success": True, "action": "observe", "result": "looked"}
loop6.tick(now=1000.0)
acts = [nid for nid in kg6.nodes if nid.startswith("行动_")]
check("成功行动写入图谱（episodic 留痕）", len(acts) == 1, str(list(kg6.nodes)[-3:]))
if acts:
    node = kg6.nodes[acts[0]]
    check("留痕含动作/目标/成败/动机",
          node.extra_attrs.get("action_type") and node.extra_attrs.get("success") is True
          and node.extra_attrs.get("motivation"), str(node.extra_attrs)[:120])
    check("留痕连到依据节点（可追溯）",
          any(e.src == acts[0] for e in kg6.edges), "")
check("成功 → 情绪正向", p6.events and p6.events[-1] == "positive", str(p6.events))
check("成功 → 表达抑制链收到非负反馈", o6 and o6[-1] is False, str(o6))
check("成功 → 统计更新", loop6.state()["stats"]["success"] == 1,
      str(loop6.state()["stats"]))

# ── 8. 失败：真实原因 + 不无限重试 + 影响后续选择 ──────────
kg7, reg7, loop7, emb7, p7, o7, base7 = build()
loop7.set_mode("on")
with_unknown(emb7)
emb7.next_result = {"success": False, "action": "approach",
                    "reason": "path_unreachable"}
now = 1000.0
seen = []
attempts = 0
per_action = {}          # 单一新路径下候选会在动作间轮换，上限按"同一动作"计
for i in range(8):
    before = len(emb7.executed)
    r = loop7.tick(now=now)
    for ex in emb7.executed[before:]:
        k = str(ex.get("action_type") or ex.get("type"))
        per_action[k] = per_action.get(k, 0) + 1
        attempts += 1
    seen.append(r)
    now += loop7.cfg["min_interval_s"] + 1
check("失败动作记录真实原因", any(
    (key.get("reason") == "path_unreachable") for key in seen), str(seen[-1]))
check("失败后统计到 failed", loop7.state()["stats"]["failed"] >= 1,
      str(loop7.state()["stats"]))
check("同一失败动作的重试不超过上限（不会无限重复）",
      all(1 <= n <= int(loop7.cfg["max_attempts"]) for n in per_action.values())
      and sum(per_action.values()) <= len(per_action) * int(loop7.cfg["max_attempts"]),
      f"per_action={per_action}")
check("重复尝试被降权或拒绝（后续要么换目标要么不动）",
      any(key.get("acted") is False for key in seen[1:]), str(seen[-2:]))
check("失败 → 情绪负向", "negative" in p7.events, str(p7.events))

# ── 9. 认知整合：不绕过图谱 / 不绕过锁 ────────────────────
kg8, reg8, loop8, emb8, p8, o8, base8 = build()
loop8.set_mode("on")
with_unknown(emb8)
uid = "UnknownEntity_axolotl"
kg8.add_node(Node(id=uid, activation=4.0))
locked = reg8.create_lock(target_type="node", target_id=uid,
                          lock_type="blocking", scope="action",
                          reason="测试锁")
check("锁创建成功（真实锁注册表）", locked.get("ok") is True, str(locked))
r = loop8.tick(now=1000.0)
executed_targets = [e.get("target") for e in emb8.executed]
check("被锁的未知对象从未被执行（锁真的拦住了）",
      "axolotl" not in executed_targets, str(executed_targets))
rejected = r.get("rejected") or []
check("锁拦截原因可读（含锁字样）",
      any("锁" in str(x.get("reason", "")) for x in rejected)
      or "锁" in str(r.get("detail", "")),
      str(r)[:160])

# ── 10. LLM 边界 ──────────────────────────────────────────
kg9, reg9, loop9, emb9, p9, o9, base9 = build()
loop9.set_mode("on")
with_unknown(emb9)
calls = []


def fake_llm(prompt):
    calls.append(prompt)
    return '{"type": "collect", "target": "木头", "text": "", "params": {"block": "木头"}}'


cands_before = len(loop9.state()["candidates"])
loop9.tick(now=1000.0)
loop9.tick(now=2000.0)
check("Mode 0：tick 全程零 LLM 调用（循环里根本没有 LLM 通路）", calls == [], str(calls))
res = loop9.goal_from_text("去砍点木头", llm_fn=fake_llm)
check("Mode 1：自然语言 → 结构化目标", res.get("ok") is True
      and res["candidate"]["type"] == "collect", str(res))
check("Mode 1 真的调用了一次 LLM", len(calls) == 1, str(len(calls)))
check("Mode 1：无 LLM 时明确报错不猜",
      loop9.goal_from_text("随便", llm_fn=None).get("ok") is False)
check("Mode 1：LLM 返回垃圾时明确报错",
      loop9.goal_from_text("随便", llm_fn=lambda p: "不是JSON").get("ok") is False)
check("Mode 1：类型不在允许集合内被拒绝",
      loop9.goal_from_text("随便", llm_fn=lambda p: '{"type":"fly"}').get("ok") is False)

# Mode 2：仅反复失败后可能升级，且不自己调 LLM
kg10, reg10, loop10, emb10, p10, o10, base10 = build()
loop10.set_mode("on")
loop10.cfg["llm_mode"] = 2
with_unknown(emb10)
emb10.next_result = {"success": False, "action": "approach", "reason": "no_path"}
now = 1000.0
for i in range(4):
    loop10.tick(now=now)
    now += loop10.cfg["min_interval_s"] + 1
esc = [l for l in loop10.recent_log(40) if l["kind"] == "escalate"]
check("Mode 2：反复失败后标记待反思（升级路径真实存在）", bool(esc),
      str([l["text"][:60] for l in loop10.recent_log(6)]))
check("Mode 2 的升级不直接调 LLM（只打标记）",
      all("llm" not in l["kind"] for l in loop10.recent_log(40)),
      str([l["kind"] for l in loop10.recent_log(6)]))

# ── 11. 目标容器（触发器/Mode1 可写入，参与后续候选） ──────
kg11, reg11, loop11, emb11, p11, o11, base11 = build()
loop11.set_mode("on")
g = loop11.add_goal({"type": "communicate", "target": "Hellucigen",
                     "text": "我在这儿"})
check("目标写入成功并持久化", g.get("ok") and len(loop11.goals()) == 1, str(g))
emb11.percept = dict(emb11.percept)
emb11.percept["players"] = [{"name": "Hellucigen", "dist": 3.0}]
loop11.tick(now=1000.0)
types = [c["type"] for c in loop11.state()["candidates"]]
check("有准备内容时才会产生 communicate 候选（不编台词）",
      "communicate" in types, str(types))
loop11.clear_goals()
check("目标可清空", loop11.goals() == [])

# ── 12. 状态单调：不得出现"没做事却声称在行动" ─────────────
kg12, reg12, loop12, emb12, p12, o12, base12 = build()
loop12.set_mode("on")
st = loop12.state()
check("初始为 idle（不假装在思考）", st["state"] == ST_IDLE and st["current_intent"] is None,
      str(st["state"]))
check("状态字段齐全（前端所需）",
      all(k in st for k in ("mode", "paused", "state", "state_reason",
                            "current_intent", "last_result", "stats", "drives")),
      str(list(st.keys())))

for d in (base, base2, base3, base4, base5, base6, base7, base8, base9, base10,
          base11, base12):
    shutil.rmtree(d, ignore_errors=True)

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 自主闭环测试全过（临时 KG + 桩具身环境，无真实数据/LLM/Minecraft）")
