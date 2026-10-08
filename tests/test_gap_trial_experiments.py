# test_gap_trial_experiments.py — 探索实验通道（2026-09-23 §20 Exploration/Goal 组）
# ============================================================================
# 链条：gap → goal → **背包对象 → 试做候选（绑定真实技能）** → 结算 → 试做账
#       → 边际递减/失败冷却 → 成功 → 关缺口（用途知识）。
# 断言同时钉住 §1：试做表里没有任何逐物答案（无 wheat/planks→workbench 类规则）。
# 离线：假桥（本文件不触桥）、临时目录、零 LLM、目标名用显式假想值。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_gap_trial_experiments.py
# ============================================================================

import logging
import os
import shutil
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.WARNING)

import config as _C
from graph_model import KnowledgeGraph, Node
from autonomy import AutonomousLoop
from cognitive_regulation import CognitiveRegulation
import prior_knowledge as pk

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


base = os.path.join(tempfile.gettempdir(), "fas_gap_trial")
shutil.rmtree(base, ignore_errors=True)
os.makedirs(base, exist_ok=True)

kg = KnowledgeGraph()
kg.add_node(Node(id="Self", graph_space="self"))
kg.add_node(Node(id="假想谷粒", weight=0.5, graph_space="semantic"))
eng = None  # gap 生命周期不需要真扩散（open_gap 里 engine 允许 None? 需要）
from diffusion_engine import DiffusionEngine
eng = DiffusionEngine(kg, dict(_C.DEFAULT_CONFIG))
eng.name_to_node = dict(kg.nodes)


class _P:
    def mood_event(self, kind): pass
    def current_mood(self): return {}


loop = AutonomousLoop(kg=kg, config=dict(_C.DEFAULT_CONFIG),
                      regulation=CognitiveRegulation(kg=kg, data_dir=base),
                      persona=_P(), data_dir=base,
                      note_outcome_fn=lambda neg: None)

# ── 前提：开一个缺口 → goal 登记 ──
check("缺口开启", pk.open_gap(kg, eng, "假想谷粒", config=dict(_C.DEFAULT_CONFIG)))
NOW = time.time()
PERCEPT = {"connected": True, "health": 20.0, "food": 20.0,
           "position": {"x": 0.0, "y": 64.0, "z": 0.0},
           "players": [], "entities": [], "blocks": [],
           "inventory_items": [{"name": "假想谷粒", "count": 2}]}
cands = loop._gap_candidates(PERCEPT, NOW)
trials = [c for c in cands if c.get("motivation") == "curiosity"
          and c.get("action_type") in ("place_block", "equip_item")]
check("1 gap→goal→背包对象产生试做候选（绑定真实技能名）",
      len(trials) == 2, str([(c.get('action_type'), c.get('params')) for c in cands]))
check("2 试做候选不绕过评分体系：带 priority/expected/explain 进常规候选池",
      all(0 < c.get("priority", 0) <= 0.45 and c.get("expected_effect")
          and c.get("explain") for c in trials),
      str([(c.get('priority')) for c in trials]))
check("2b 参数已绑定对象（是行动不是抽象 explore）",
      all(c["params"].get("item") == "假想谷粒" for c in trials))

# ── §6 失败 → 条件性负证据（冷却降权，非永久禁止）──
t_place = next(c for c in trials if c["action_type"] == "place_block")
loop._gap_outcome({"action_type": "place_block", "target": "假想谷粒",
                   "params": {"item": "假想谷粒"}},
                  success=False, now=NOW + 5,
                  result={"reason": "not_placeable"})
g = next(x for x in loop._goals if x.get("gap_obj") == "假想谷粒")
rec = (g.get("trials") or {}).get("place") or {}
check("3 失败记入试做账（n/last_failed/reason 可解释）",
      rec.get("n") == 1 and rec.get("reason") == "not_placeable",
      str(g.get("trials")))
c2 = loop._gap_candidates(PERCEPT, NOW + 10)
p_after = [c for c in c2 if c["action_type"] == "place_block"][0]["priority"]
check("4 近期失败该路径优先级显著下降（§6 降低而非禁死）",
      0 < p_after < 0.11, f"p={p_after}")
check("4b 候选仍存在（无永久硬禁）", any(c["action_type"] == "place_block" for c in c2))

# ── §15/§4 重复边际递减 ──
for k in range(2):
    loop._gap_outcome({"action_type": "equip_item", "target": "假想谷粒",
                       "params": {"item": "假想谷粒"}},
                      success=True, now=NOW + 20 + k, result={})
c3 = loop._gap_candidates(PERCEPT, NOW + 30)
eq = [c for c in c3 if c["action_type"] == "equip_item"]
check("5 已试过 2 次的操作边际递减（priority 减半再减半）",
      eq and abs(eq[0]["priority"] - 0.42 * 0.25) < 1e-6,
      str([c.get('priority') for c in c3]))
# 第 3 次后该 trial 休整（候选消失），但缺口与目标仍在——换新路径继续
loop._gap_outcome({"action_type": "equip_item", "target": "假想谷粒",
                   "params": {"item": "假想谷粒"}},
                  success=True, now=NOW + 40, result={})
c4 = loop._gap_candidates(PERCEPT, NOW + 50)
check("5b 单一操作试满 3 次后不再刷屏，其他操作仍开放（不死循环）",
      not any(c["action_type"] == "equip_item" for c in c4)
      and any(c["action_type"] == "place_block" for c in c4)
      and g in loop._goals)

# ── §7 成功 → 用途知识（真实结果，非预写答案）──
before_gaps = len(pk.open_gaps(kg))
g["fails"] = 0
loop._gap_outcome({"action_type": "place_block", "target": "假想谷粒",
                   "params": {"item": "假想谷粒"}},
                  success=True, now=NOW + 60, result={})
check("6 放置成功 = 用途证据 → 缺口关闭（progress 已记）",
      len(pk.open_gaps(kg)) == before_gaps - 1
      and int(g.get("progress", 0)) >= 1,
      f"gaps={len(pk.open_gaps(kg))} progress={g.get('progress')}")

# ── 目标不在场（看不见也不在背包）→ 无候选，不空转 ──
pk.open_gap(kg, eng, "假想远山", config=dict(_C.DEFAULT_CONFIG))
c5 = loop._gap_candidates({**PERCEPT, "inventory_items": []}, NOW + 70)
check("7 对象不在场时不产出注定失败的候选（允许有意义的等待）",
      not any(c.get("target") == "假想远山" for c in c5))

# ── §1 行为树防线：试做表是通用先验，无逐物答案 ──
import re as _re
tbl = _re.findall(r"\"skill\"\s*:\s*\"([a-z_]+)\"",
                  open("config.py", encoding="utf-8").read().split('"gap_trials"')[1][:800])
check("8 试做表只含通用操作（place/equip），无环境专有规则",
      set(tbl) == {"place_block", "equip_item"}, str(tbl))
src_loop = open("autonomy.py", encoding="utf-8").read()
seg = src_loop[src_loop.index("def _gap_candidates"):src_loop.index("def _graph_action_candidates")]
for banned in ("chop", "workbench", "wheat", "planks", "tree"):
    check(f"8b 缺口→候选通道无『{banned}』式硬编码", banned not in seg.lower())

# ── 零 LLM：本文件 import 链与通道源码无 llm 调用 ──
check("9 试做通道零 LLM", "llm" not in seg.lower())

# ── 10 真机断点回归（2026-09-23 实机抓到）：perceive() 必须把背包下发到
#    自主层 percept——此前背包只进"写图"路径，_gap_candidates 的 obj in inv
#    恒假，试做通道在真机上永远看不见物品（单测因手构 percept 而漏过）。──
import minecraft.embodiment as ME

class _FakeBridge:
    def get_state(self):
        return {"connected": True, "position": {"x": 0.0, "y": 64.0, "z": 0.0},
                "health": 20.0, "food": 20.0, "heldItem": "dirt",
                "playersNearby": [], "nearbyEntities": [], "nearbyBlocks": [],
                "chat": [], "timeOfDay": 6000}
    def inventory(self):
        return {"ok": True, "items": [{"name": "假想浆果", "count": 1}]}
    def recipes(self):
        return {"ok": False}

_real_bridge = ME.bridge
ME.bridge = _FakeBridge()
try:
    emb = ME.MinecraftEmbodiment(kg=kg, engine=eng, config=dict(_C.DEFAULT_CONFIG))
    emb._inv_last_pull = 0.0          # 时间门改造后：首拍即拉背包
    p10 = emb.perceive()
    inv10 = p10.get("inventory_items") or []
    check("10 percept 下发 inventory_items（写图路径之外也要透传给自主层）",
          any(str((i or {}).get("name")) == "假想浆果" for i in inv10),
          str(inv10))
    # 感知→缺口自动开启（考虑识别：无配方用途 → 开缺口）
    check("10b 未知物品经感知自动开缺口",
          any(gm.get("object") == "假想浆果" for gm in pk.open_gaps(kg)),
          str([gm.get("object") for gm in pk.open_gaps(kg)]))
    c10 = loop._gap_candidates(p10, time.time())
    check("10c 端到端：感知→缺口→试做候选（真实链路不再断）",
          any(c.get("action_type") in ("place_block", "equip_item")
              and c.get("target") == "假想浆果" for c in c10),
          str([(c.get('action_type'), c.get('target')) for c in c10]))
finally:
    ME.bridge = _real_bridge

# ── 11 真机断点②回归：abandoned 缺口有界复活；证毕关闭永不复活 ──
CFG11 = dict(_C.DEFAULT_CONFIG)
pk.open_gap(kg, eng, "假想石子", config=CFG11)
pk.close_gap(kg, eng, "假想石子", reason="abandoned:ttl", config=CFG11)
_c11 = float((kg.get_node(pk.gap_node_id("假想石子")).extra_attrs or {})
             .get("closed") or 0)
check("11 间隔未满不复活（防刚放弃就原地复活）",
      not pk.resurface_gap(kg, eng, "假想石子", CFG11, now=_c11 + 600))
check("11b 间隔已满且额度未耗尽 → 复活开新生命周期",
      pk.resurface_gap(kg, eng, "假想石子", CFG11, now=_c11 + 1900)
      and any(gm.get("object") == "假想石子" for gm in pk.open_gaps(kg)))
pk.close_gap(kg, eng, "假想石子", reason="abandoned:fails", config=CFG11)
pk.resurface_gap(kg, eng, "假想石子", CFG11, now=_c11 + 3900)   # reopen_n=2
pk.close_gap(kg, eng, "假想石子", reason="abandoned:ttl", config=CFG11)
check("11c 复活额度有界（耗尽=排队终止，账目可查 reopen_n，非暗禁）",
      not pk.resurface_gap(kg, eng, "假想石子", CFG11, now=_c11 + 99999)
      and int((kg.get_node(pk.gap_node_id("假想石子")).extra_attrs or {})
              .get("reopen_n", 0)) == 2)
pk.open_gap(kg, eng, "假想铁锭", config=CFG11)
pk.close_gap(kg, eng, "假想铁锭", reason="used:place_block", config=CFG11)
check("11d 被真实结果证毕的缺口永不复活（成功=知识，不再骚扰）",
      not pk.resurface_gap(kg, eng, "假想铁锭", CFG11,
                           now=time.time() + 99999))

# ── 12 §9 目标反向驱动注意（真机"零星行为"根因修复）+ ttl 分型 ──
NOW12 = time.time()
loop.engine = eng                      # 本文件的 loop 建时未注入 engine
pk.open_gap(kg, eng, "假想贝壳", config=CFG11)
PERC12 = {**PERCEPT, "inventory_items": [{"name": "假想贝壳", "count": 1}]}
marked = []
_real_ma = eng.mark_active
eng.mark_active = lambda ids: (marked.extend(ids), _real_ma(ids))
try:
    loop._gap_candidates(PERC12, NOW12)                    # 登记目标
    g12 = next(x for x in loop._goals if x.get("gap_obj") == "假想贝壳")
    n12 = kg.get_node(pk.gap_node_id("假想贝壳"))
    n12.activation = 0.2                                   # 模拟一个周期衰减后
    floor12 = float((CFG11.get("prior") or {}).get("gap_attention_floor", 0.9))
    loop._gap_candidates(PERC12, NOW12 + 5)
    check("12 活缺口目标把缺口节点托上注意力地板并 mark_active（进扩散前沿）",
          float(n12.activation or 0.0) >= floor12 - 1e-9
          and pk.gap_node_id("假想贝壳") in marked,
          f"act={n12.activation}")
    check("12a 目标不偷分开候选以外的捷径：候选仍走常规池且数量有界",
          len(loop._gap_candidates(PERC12, NOW12 + 6)) <= 8)
    # 从未尝试就被 TTL 掐掉 → ttl_untried，复活不占额度
    g12["gap_since"] = NOW12 - 1900
    loop._gap_candidates(PERC12, NOW12 + 10)
    ea12 = kg.get_node(pk.gap_node_id("假想贝壳")).extra_attrs or {}
    check("12a2 没机会试就被 TTL 掐掉的放弃记 ttl_untried（与失败区分）",
          str(ea12.get("retired")) == "abandoned:ttl_untried", str(ea12.get("retired")))
    pk.resurface_gap(kg, eng, "假想贝壳", CFG11,
                     now=float(ea12.get("closed") or 0) + 1900)
    check("12a3 untried 复活不消耗重开额度（没赶上≠失败）",
          int((kg.get_node(pk.gap_node_id("假想贝壳")).extra_attrs or {})
              .get("reopen_n", 0)) == 0)
    # 试过仍无进展 → 普通 ttl，复活正常占额度
    pk.open_gap(kg, eng, "假想羽毛", config=CFG11)
    loop._goals.append({"type": "explore_object", "target": "假想羽毛",
                        "source": "exploration_gap", "params": {},
                        "text": "弄清 假想羽毛 有什么用", "gap_obj": "假想羽毛",
                        "fails": 1, "progress": 0,
                        "trials": {"place": {"n": 1}},
                        "gap_since": NOW12 - 1900, "created": "test"})
    loop._gap_candidates(PERC12, NOW12 + 20)
    nfea = kg.get_node(pk.gap_node_id("假想羽毛"))
    check("12b 真试过没成 → 普通 abandoned:ttl",
          str((nfea.extra_attrs or {}).get("retired")) == "abandoned:ttl")
    pk.resurface_gap(kg, eng, "假想羽毛", CFG11,
                     now=float((nfea.extra_attrs or {}).get("closed") or 0) + 1900)
    check("12c 试过的复活正常占额度",
          int((nfea.extra_attrs or {}).get("reopen_n", 0)) == 1)
finally:
    eng.mark_active = _real_ma

# ── 13 P3 最终验收（§六 accidental blocking 修复回归）：busy（长动作在飞）
#    期间决策不重决，但"世界状态上图"必须有慢心跳——背包/配方线索/缺口复活
#    都寄生在 perceive 里，perceive 停=缺口生命周期停（长 follow=盲跟）。──
class _StubEmb13:
    name = "stub13"

    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def capabilities(self):
        return {"observe"}

    def perceive(self):
        self.calls += 1
        return {}

loop13 = AutonomousLoop(kg=kg, config=dict(_C.DEFAULT_CONFIG), data_dir=base)
emb13 = _StubEmb13()
loop13.register_embodiment(emb13)
loop13.mode = "on"
_real_act = loop13.actions
try:
    loop13.actions.current = None
    loop13.actions.busy = lambda: True          # 只改实例：模拟长动作在飞
    loop13.actions.tick = lambda now: {}
    T13 = time.time() + 40000.0                 # 远离真实时间避免历史干扰
    r1 = loop13.tick(T13)
    loop13.tick(T13 + 5)                        # 心跳未满 20s，不该再感知
    r2 = loop13.tick(T13 + 21)                  # 满 20s → 第二次感知
    check("13 busy 期决策不重决（reason=action_progress，不产候选/行动）",
          r1.get("reason") == "action_progress" and not r1.get("acted")
          and r2.get("reason") == "action_progress", f"{r1} {r2}")
    check("13b busy 期感知慢心跳：首拍即刷、间隔未满不刷、满 20s 再刷",
          emb13.calls == 2, f"calls={emb13.calls}")
finally:
    loop13.actions.current = getattr(_real_act, "current", None)
    for k in ("busy", "tick"):
        loop13.actions.__dict__.pop(k, None)

shutil.rmtree(base, ignore_errors=True)
print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
    sys.exit(1)
print("PASS: 探索实验通道全部行为规格通过")
