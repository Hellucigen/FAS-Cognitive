# test_p3_goals_repetition.py — P3/§5-7/§12/§6 §16：缺口目标生命周期 + 重复边际递减
# ============================================================================
# autonomy 层离线确定性：临时 data_dir + StubEmbodiment + 真实先验图。
# cap_index=None（kill switch 形态）：只验本批接的三条线——
#   A 缺口→目标→候选→真实结果回流（进度/放弃/TTL/用户承诺不被挤占）
#   B habituation：成功过的同类候选边际递减（不禁）
#   C next_ts 软退避：消费的死字段（有限冷却≠永久禁令；成功清零）
#   D _bind_craft：候选只来自图 hub hints（执行侧无配方=不生成注定失败）
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_p3_goals_repetition.py
# ============================================================================

import copy
import logging
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.WARNING)

from graph_model import KnowledgeGraph
from autonomy import AutonomousLoop
import config as _C
import mc_knowledge as mck
import prior_knowledge as pk
from cognitive_regulation import CognitiveRegulation

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class StubEmbodiment:
    name = "stub_env"

    def __init__(self):
        self.capabilities_set = {"observe", "follow", "explore", "inspect_entity",
                                 "inspect_block", "craft_item", "communicate"}
        self.percept = {"connected": True, "health": 20, "food": 20,
                        "position": {"x": 0.0, "y": 64.0, "z": 0.0},
                        "players": [], "entities": [], "blocks": [],
                        "unknown_entities": [], "unknown_blocks": []}

    def available(self):
        return True

    def capabilities(self):
        return set(self.capabilities_set)

    def perceive(self):
        return dict(self.percept)


def make(tag):
    base = os.path.join(tempfile.gettempdir(), f"fas_p3_{tag}")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    kg = KnowledgeGraph()
    cfg = copy.deepcopy(_C.DEFAULT_CONFIG)
    mck.init_protection_node(cfg)
    mck.ensure_mc_world(kg, cfg)
    pk.ensure_prior(kg, cfg)
    cfg.setdefault("autonomy", {})["mode_default"] = "off"
    reg = CognitiveRegulation(kg=kg, data_dir=base)
    loop = AutonomousLoop(kg=kg, config=cfg, regulation=reg,
                          data_dir=base)
    emb = StubEmbodiment()
    loop.register_embodiment(emb)
    return kg, cfg, loop, emb, base


def gaps_of(loop):
    return [g for g in loop.goals() if g.get("source") == "exploration_gap"]


def gap_retired(kg, obj):
    n = kg.nodes.get(pk.gap_node_id(obj))
    return (n.extra_attrs or {}).get("retired") if n else None


# ═══ A. 缺口 → 目标 → 候选 → 真实结果回流（§5/§7/§12）═══
kg, cfg, loop, emb, base = make("goals")
T0 = 50000.0
pk.open_gap(kg, None, "cow", "unknown_use", cfg)
pk.open_gap(kg, None, "furnace", "unknown_use", cfg)
per = dict(emb.percept, entities=[{"name": "Cow", "dist": 4.0}],
           blocks=[{"name": "furnace", "dist": 2.0}])
cands = loop._gap_candidates(per, T0)
by_t = {c["action_type"]: c for c in cands}
check("活缺口 ∧ 对象在视 → 观察候选（生物/方块分流）",
      "inspect_entity" in by_t and "inspect_block" in by_t
      and by_t["inspect_entity"]["target"] == "cow"
      and by_t["inspect_block"]["params"].get("block") == "furnace",
      str([(c["action_type"], c["target"]) for c in cands]))
check("候选 reason 带缺口节点与 hub（注意力分量有据可查）",
      pk.gap_node_id("cow") in (by_t.get("inspect_entity") or {}).get("reason", [])
      and "探索缺口" in (by_t.get("inspect_entity") or {}).get("reason", []))
check("缺口注册为持久目标（type=explore_object）",
      {g.get("gap_obj") for g in gaps_of(loop)} == {"cow", "furnace"},
      str(gaps_of(loop)))
loop2 = AutonomousLoop(kg=kg, config=cfg, data_dir=base)
check("§12 目标跨重启存活（同一 data_dir 重载）",
      {g.get("gap_obj") for g in gaps_of(loop2)} == {"cow", "furnace"},
      str(gaps_of(loop2)))
check("对象不在视野 → 不产注定失败的候选，但目标留存",
      loop._gap_candidates(dict(emb.percept), T0 + 5) == []
      and len(gaps_of(loop)) == 2)
loop._on_action_settled({"action_type": "inspect_entity", "target": "cow"},
                        {"success": True}, True, now=T0 + 10)
cg = next(g for g in gaps_of(loop) if g["gap_obj"] == "cow")
check("观察成功 = 进度（描述≠用途：inspect 不关缺口）",
      cg.get("progress") == 1 and gap_retired(kg, "cow") is None, str(cg))
for k in range(3):
    loop._on_action_settled({"action_type": "inspect_entity", "target": "cow"},
                            {"success": False, "reason": "no_line_of_sight"},
                            False, now=T0 + 20 + k)
check("§7 反复失败 → 放弃：撤目标 + 缺口退役 abandoned（非永久禁，新证据可重开）",
      not any(g["gap_obj"] == "cow" for g in gaps_of(loop))
      and gap_retired(kg, "cow") == "abandoned"
      and not any(str(x.get("object")) == "cow" for x in pk.open_gaps(kg)),
      str(gap_retired(kg, "cow")))
pk.open_gap(kg, None, "torch", "unknown_use", cfg)
loop._gap_candidates(dict(emb.percept), T0 + 60)
loop._on_action_settled({"action_type": "craft_item", "target": "torch",
                         "params": {"item": "torch"}},
                        {"success": True}, True, now=T0 + 70)
check("使用类成功 = 一手用途证据 → 关缺口（used:craft_item）",
      gap_retired(kg, "torch") == "used:craft_item")
loop._gap_candidates(dict(emb.percept), T0 + 80)
check("缺口在别处关闭 → 目标自动销账",
      not any(g["gap_obj"] == "torch" for g in gaps_of(loop)))
pk.open_gap(kg, None, "glass", "unknown_use", cfg)
loop._gap_candidates(dict(emb.percept), 90000.0)          # glass_since=90000
loop._gap_candidates(dict(emb.percept), 91801.0)          # TTL 1800s 到
# §16 分型（2026-09-23 晚）：从没试过就被 TTL 掐掉 ≠ 失败，记 ttl_untried
check("§12 超时且从未获尝试 → 放弃记 abandoned:ttl_untried",
      not any(g["gap_obj"] == "glass" for g in gaps_of(loop))
      and gap_retired(kg, "glass") == "abandoned:ttl_untried")
loop._gap_candidates(dict(emb.percept), 91802.0)
check("放弃后不留尸复活（陈旧集合不复注册）",
      not any(g["gap_obj"] == "glass" for g in gaps_of(loop)))
pk.open_gap(kg, None, "glass", "unknown_use", cfg)
loop._gap_candidates(dict(emb.percept), 92000.0)          # 重新登记目标
_gg = next(g for g in gaps_of(loop) if g["gap_obj"] == "glass")
_gg["gap_since"] = 92000.0
_gg["trials"] = {"place": {"n": 1, "reason": "no_support"}}  # 真试过没成
loop._gap_candidates(dict(emb.percept), 93801.0)          # TTL 到
check("§12 真试过仍无进展 → 放弃记 abandoned:ttl（与 untried 区分）",
      gap_retired(kg, "glass") == "abandoned:ttl")
# 用户承诺不被自发目标挤占（§7）+ 并发上限 3
loop.clear_goals()
for o in ("cobweb", "dolphin", "kelp", "turtle"):
    pk.open_gap(kg, None, o, "unknown_use", cfg)
loop._goals.append({"type": "follow", "target": "Steve",
                    "source": "user", "text": "跟着我"})
per2 = dict(emb.percept,
            entities=[{"name": n} for n in ("Cow", "Dolphin", "Kelp",
                                            "Turtle", "Cobweb")],
            players=[{"name": "Steve", "dist": 3.0}])
out = loop._action_candidates(per2, [], 95000.0)
mot = [c.get("motivation") for c in out]
check("§7 缺口目标 ≤3 并发（第 4 个排队不占坑）",
      len(gaps_of(loop)) == 3, str([g.get("gap_obj") for g in gaps_of(loop)]))
check("用户承诺透传不被探索缺口挤占（user_goal 候选仍在）",
      "user_goal" in mot and sum(1 for m in mot if m == "curiosity") == 3,
      str([(c["action_type"], c.get("motivation")) for c in out]))

# ═══ B. habituation：重复的边际价值递减（§6，不禁）═══
kgB, cfgB, loopB, embB, _ = make("hab")
w_hab = float(loopB.cfg["weights"].get("habituation", 0.18))
cand = {"action_type": "craft_item", "target": "stick", "params": {},
        "motivation": "resource_opportunity", "reason": []}
s0, e0 = loopB._score_action(dict(cand), dict(embB.percept), 10000.0)
loopB._success_by_key["craft_item@stick"] = 9900.0        # 100s 前刚做成
s1, e1 = loopB._score_action(dict(cand), dict(embB.percept), 10000.0)
d0 = w_hab * (1.0 - 100.0 / 600.0)
check("成功过的同类候选按 habituation 窗口降值（软降权，非禁止）",
      abs((s0 - s1) - d0) < 0.005 and "hab=" in e1 and "hab=" not in e0,
      f"Δ={s0 - s1:.4f} 期望≈{d0:.4f}")
s2, _ = loopB._score_action(dict(cand), dict(embB.percept), 10600.0)
check("窗口（600s）过后价值恢复（不永久罚）",
      abs(s2 - s0) < 1e-9, f"{s0:.4f} vs {s2:.4f}")
check("习惯化按 (动作@目标) 记键（别的物品不受影响）",
      abs(loopB._score_action({"action_type": "craft_item",
                               "target": "torch", "params": {},
                               "motivation": "resource_opportunity",
                               "reason": []},
                              dict(embB.percept), 10000.0)[0] - s0) < 1e-9)
loopB._on_action_settled({"action_type": "craft_item", "target": "torch"},
                         {"success": True}, True, now=10000.0)
check("真实结算成功 → 自动记 habituation 账（无需手工注入）",
      loopB._success_by_key.get("craft_item@torch") == 10000.0)

# ═══ C. next_ts 软退避（消费死字段；有限冷却 ≠ 已拆除的永久禁令）═══
kgC, cfgC, loopC, embC, _ = make("backoff")
bc = {"action_type": "craft_item", "target": "diamond", "params": {},
      "motivation": "resource_opportunity", "reason": []}
k = "craft_item@diamond"
loopC._on_action_settled(dict(bc), {"success": False,
                                    "reason": "missing"}, False, now=20000.0)
att = loopC._attempts.get(k) or {}
check("失败 1 次 → 退避 45s（retry_backoff_s）",
      abs(float(att.get("next_ts", 0)) - 20045.0) < 1e-6, str(att))
ok, why = loopC._feasible_action(dict(bc), dict(embC.percept), 20010.0)
check("冷却窗口内 → 可行性拒（防每拍撞同一堵墙）",
      ok is False and "重试冷却" in why, why)
check("窗口外恢复可行",
      loopC._feasible_action(dict(bc), dict(embC.percept), 20050.0)[0] is True)
for _ in range(2):
    loopC._on_action_settled(dict(bc), {"success": False,
                                        "reason": "missing"}, False, now=20060.0)
att = loopC._attempts[k]
check("≤max_attempts 次失败退避保持 45s（倍增只在超预算后）",
      abs(float(att["next_ts"]) - 20105.0) < 1e-6, str(att))
loopC._on_action_settled(dict(bc), {"success": False,
                                    "reason": "missing"}, False, now=20110.0)
att = loopC._attempts[k]
check("超出 max_attempts → 退避倍增（45×2=90s；封顶 8×）",
      int(att["count"]) == 4 and abs(float(att["next_ts"]) - 20200.0) < 1e-6,
      str(att))
loopC._on_action_settled(dict(bc), {"success": True}, True, now=20300.0)
check("一次成功清零尝试账（软退避随经验走）",
      k not in loopC._attempts
      and loopC._feasible_action(dict(bc), dict(embC.percept), 20301.0)[0] is True)

# ═══ D. _bind_craft：候选只来自图 hints；做不到不进候选（§8）═══
kgD, cfgD, loopD, embD, _ = make("craft")
pk.note_craftable(kgD, None, [{"result": "stick",
                               "ingredients": {"oak_planks": 2},
                               "needs_table": False}], cfgD)
d = {"action_type": "craft_item", "motivation": "resource_opportunity",
     "priority": 0.45}
out = loopD._bind_craft(d, {}, 100.0)
check("hub hints → craft_item 候选（reason 带 配方: 节点）",
      any(c["action_type"] == "craft_item" and c["target"] == "stick"
          and f"配方:stick" in c["reason"] and "可制作物品" in c["reason"]
          for c in out), str(out))
with kgD._lock:                                     # 注入执行侧无配方的假线索
    kgD.nodes[pk.CRAFT_HUB].extra_attrs["hints"].append(
        {"item": "quantum_battery", "ingredients": [], "needs_table": False})
out2 = loopD._bind_craft(d, {}, 101.0)
check("执行侧解析不到配方 = 做不到 → 不生成注定失败的候选",
      not any(c["target"] == "quantum_battery" for c in out2), str(out2))
check("hub 无 hints → 无候选（背包空则 CRAFT 自然静默）",
      loopD._bind_craft(d, {}, 102.0) != []
      and (pk.note_craftable(kgD, None, [], cfgD),
           loopD._bind_craft(d, {}, 103.0) == [])[1] is True)

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
    sys.exit(1)
print("PASS: 缺口目标生命周期/习惯化/软退避/制作绑定（确定性全链）")
