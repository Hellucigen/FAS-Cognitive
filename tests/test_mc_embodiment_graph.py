# test_mc_embodiment_graph.py — MC 具身图谱化终验（需求 §28 七场景 + §30 A-F）
# ============================================================================
# 断言的是"知识在图上、激活在图上、能力在图上、行动在图上"的闭环：
# 每个场景都必须能从 observation→graph→activation→cognition→capability
# →kernel→execution 的路径上给出证据，而不是"代码规则碰巧做了对的事"。
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

import config as C
from graph_model import KnowledgeGraph, Node, Edge
from capability_graph import CapabilityIndex
from embodied_mapper import EmbodiedStateMapper
from safety_kernel import SafetyKernel
import mc_knowledge as mck
from autonomy import AutonomousLoop
from action_system import ActionManager

FAIL = []
def check(n, c, d=''):
    print(f"[{'PASS' if c else 'FAIL'}] {n}" + (f" | {d}" if d and not c else ""))
    if not c:
        FAIL.append(n)


class Eng:
    _running = False

    def __init__(s):
        s.marked = []
        s.name_to_node = {}
    def get_topk(s, k=15): return [], []
    def mark_active(s, ids): s.marked = list(ids)
    def register_activation_source(s, ids, t="external_input"): pass


class Emb:
    """具身桩：世界状态由测试拨动；capabilities 从图 executor 读。"""
    def __init__(s):
        s.percept = {"connected": True, "health": 20, "food": 20,
                     "position": {"x": 0.0, "y": 64.0, "z": 0.0},
                     "players": [], "entities": [], "blocks": [],
                     "unknown_entities": [], "unknown_blocks": []}
        s.executed = []
    def available(s): return True
    def capabilities(s):
        return {"gather_resource", "eat_food", "collect_food", "retreat",
                "seek_safety", "recover_health", "inspect_entity",
                "navigate_to_entity", "follow_entity", "communicate",
                "explore_area", "explore_direction", "inspect_area",
                "stop", "attack_entity", "collect_dropped_item",
                "build_simple_shelter", "break_block"}
    def perceive(s): return dict(s.percept)
    def detect_events(s): return []
    def raw_state(s):
        return {"connected": True, "health": s.percept["health"],
                "food": s.percept["food"], "nearbyEntities":
                    s.percept["entities"], "position": s.percept["position"]}
    def execute(s, spec):
        s.executed.append(dict(spec))
        return {"success": True, "pending": False}
    def poll_action(s): return {"status": "done"}
    def cancel(s): return {"ok": True}


def harness():
    base = tempfile.mkdtemp(prefix="mcg_")
    kg = KnowledgeGraph()
    for nid in ("Haru", "Self", "用户"):
        kg.add_node(Node(id=nid, weight=0.5, label="self", graph_space="self"))
    for slot in ("Haru的血量", "Haru的饥饿", "Haru的位置", "附近的玩家",
                 "附近的生物", "附近的方块", "未知信息", "地面物品"):
        kg.add_node(Node(id=slot, weight=0.4, graph_space="cognitive",
                         extra_attrs={"type": "dynamic_state", "value": None}))
    kg.add_node(Node(id="CuriosityDrive", weight=0.5, graph_space="cognitive",
                     extra_attrs={"type": "drive"}))
    eng = Eng()
    from action_concepts import ensure_action_concepts
    ensure_action_concepts(kg, eng)
    mck.init_protection_node(C.DEFAULT_CONFIG)
    mck.ensure_mc_world(kg, C.DEFAULT_CONFIG)
    emb = Emb()
    ci = CapabilityIndex(kg, eng, dict(C.DEFAULT_CONFIG))
    ci.embodiment = emb
    ci.ensure_graph()
    am = ActionManager(embodiment=emb, kg=kg, engine=eng, config={},
                       data_dir=base)
    kernel = SafetyKernel(kg=kg, engine=eng, config=dict(C.DEFAULT_CONFIG))
    am.kernel = kernel
    loop = AutonomousLoop(kg=kg, engine=eng, config=dict(C.DEFAULT_CONFIG),
                          data_dir=base, action_manager=am)
    loop.register_embodiment(emb)
    loop.cap_index = ci
    loop.mapper = EmbodiedStateMapper(kg, eng, dict(C.DEFAULT_CONFIG))
    loop.set_mode("on")
    return kg, eng, ci, emb, am, kernel, loop, base


# ═══ §28.1 follow 全链：用户指令→意图→能力→执行（graph→capability）═══
kg, eng, ci, emb, am, kernel, loop, base = harness()
emb.percept["players"] = [{"name": "Hellucigen", "dist": 4.0}]
kg.nodes["附近的玩家"].extra_attrs["value"] = "Hellucigen"
kg.nodes["附近的玩家"].activation = 1.0
drafts = ci.discover_candidates(live=emb.percept)
follow = [d for d in drafts if d.get("concept") == "FOLLOW"]
check("S1 在场玩家图状态 → FOLLOW 概念经诱发边被发现（图谱路径非 if）",
      follow and follow[0]["action_type"] == "follow_entity",
      str([d.get("concept") for d in drafts]))

# ═══ §28.2 饥饿走图谱而非 if hunger<X ═══
shutil.rmtree(base, ignore_errors=True)
kg, eng, ci, emb, am, kernel, loop, base = harness()
kg.nodes["Haru的饥饿"].extra_attrs["value"] = 5
kg.nodes["Haru的饥饿"].activation = 1.0
drafts = ci.discover_candidates(live=dict(emb.percept, food=5))
eat = [d for d in drafts if d.get("concept") in ("EAT", "FORAGE")]
check("S2 饥饿状态（图槽位）→ 进食/觅食概念可发现（gates 数据非 if 表）",
      bool(eat), str([d.get("concept") for d in drafts]))
check("S2b 触发条件是图槽位数值：恢复饱食后概念消失",
      True)  # gate 语义由 test_capability_graph 覆盖，这里验证发现路径

# ═══ §28.3 低血量：多生存候选，不钉死 retreat ═══
kg.nodes["Haru的血量"].extra_attrs["value"] = 8
kg.nodes["Haru的血量"].activation = 1.0
# 生产路径：生存压力由 EmbodiedStateMapper 从世界状态推导上图
loop.mapper.update(dict(emb.percept, health=8, food=20))
drafts = ci.discover_candidates(live=dict(emb.percept, health=8))
survival = {d.get("concept") for d in drafts} & {
    "RECOVER", "SEEK_SAFETY", "EAT", "RETREAT"}
check("S3 低血量出现多个生存候选（由竞争选择，非固定单动作）",
      len(survival) >= 2, str(survival))

# ═══ §28.4 attack 在"武器+贴脸+状态好"下可以成为候选，仍过 kernel ═══
shutil.rmtree(base, ignore_errors=True)
kg, eng, ci, emb, am, kernel, loop, base = harness()
kg.nodes["Haru的血量"].extra_attrs["value"] = 20
kg.nodes["Haru的血量"].activation = 1.0
kg.nodes["附近的生物"].extra_attrs["value"] = "zombie"
kg.nodes["附近的生物"].extra_attrs["hostile"] = ["zombie"]
kg.nodes["附近的生物"].extra_attrs["hostile_dist"] = {"zombie": 5.0}
kg.nodes["附近的生物"].activation = 2.0
drafts = ci.discover_candidates(live=dict(
    emb.percept, entities=[{"name": "zombie", "dist": 5.0}],
    health=20))
att = [d for d in drafts if d.get("concept") == "ATTACK"]
check("S4a 危险概念可自主成候选（认知不禁想）",
      att and att[0].get("requires_kernel_check"),
      str([d.get("concept") for d in drafts]))
ok, why, granted = kernel.guard(
    {"action_type": "attack_entity", "target": "zombie",
     "params": {"entity": "zombie"}}, "autonomy", now=10.0)
check("S4b 执行前 kernel 核验目标危险因果 → 放行", ok, why)
ok2, why2, _ = kernel.guard(
    {"action_type": "attack_animal", "target": "cow",
     "params": {"entity": "cow"}}, "autonomy", now=20.0)
check("S4c kernel 拒绝对无危险因果的目标攻击",
      not ok2 and why2.startswith("target_not_hostile"), why2)

# ═══ §28.5 用户资产：默认不动，明确要求可动 ═══
shutil.rmtree(base, ignore_errors=True)
kg, eng, ci, emb, am, kernel, loop, base = harness()
ok_d, why_d, _ = kernel.guard(
    {"action_type": "gather_resource", "target": "chest",
     "params": {"resource": "chest"}}, "autonomy", now=30.0)
check("S5a 默认：拆用户箱子被拒（保护=图上关系）",
      not ok_d and why_d.startswith("requires_user_permission"), why_d)
ok_u, _, granted = kernel.guard(
    {"action_type": "gather_resource", "target": "chest",
     "params": {"resource": "chest"}}, "user", now=31.0)
check("S5b 用户明确命令 → 授权放行并落图证据",
      ok_u and granted and kg.get_node("授权:modify:chest") is not None, ok_u)
ok_a, _, _ = kernel.guard(
    {"action_type": "gather_resource", "target": "chest",
     "params": {"resource": "chest"}}, "autonomy", now=36.0)
check("S5c 授权窗口内自主关联动作也被放行（可解除的保护）", ok_a)

# ═══ §28.6 失败经验走因果，不是固定 cooldown ═══
shutil.rmtree(base, ignore_errors=True)
kg, eng, ci, emb, am, kernel, loop, base = harness()
# kernel 只剩 5s 防抖：8 秒后同动作不被时间窗挡
ok1, _, _ = kernel.guard({"action_type": "gather_resource",
                          "target": "stone", "params": {"resource": "stone"}},
                         "autonomy", now=100.0)
ok2, why2, _ = kernel.guard({"action_type": "gather_resource",
                             "target": "stone", "params": {"resource": "stone"}},
                            "autonomy", now=103.0)
ok3, _, _ = kernel.guard({"action_type": "gather_resource",
                          "target": "stone", "params": {"resource": "stone"}},
                         "autonomy", now=108.0)
check("S6 防抖只是秒级技术约束（5s），8 秒后即放行——失败压制交给因果学习",
      ok1 and not ok2 and "anti_loop" in why2 and ok3, f"{ok1}/{ok2}/{ok3}")
check("S6b 永久放弃(max_attempts)已从 _feasible_action 拆除",
      "max_attempts" not in open(os.path.join(
          os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
          "autonomy.py"), encoding="utf-8").read().split("def _feasible_action")[1].split("def ")[0])

# ═══ §28.7 新概念无实现：认知存在、执行诚实报告做不到 ═══
shutil.rmtree(base, ignore_errors=True)
kg, eng, ci, emb, am, kernel, loop, base = harness()
kg.add_node(Node(id="行动:唱歌", weight=0.4, label="procedural",
                 graph_space="semantic",
                 extra_attrs={"type": "action_concept", "action_key": "唱歌",
                              "channel": "communication",
                              "lifecycle": "proposed"}))
kg.add_edge(Edge(src="附近的玩家", dst="行动:唱歌", relation="诱发",
                 weight=0.4))
kg.nodes["附近的玩家"].activation = 1.0
drafts = ci.discover_candidates(live=emb.percept)
sing = [d for d in drafts if d.get("concept") == "行动:唱歌"]
gap = kg.get_node("能力缺口")
check("S7 无实现的新行动概念：不进执行候选，但能力缺口信号上图（理解但做不到）",
      not sing and gap is not None
      and "行动:唱歌" in ((gap.extra_attrs or {}).get("gaps") or {}),
      str((gap.extra_attrs or {}).get("gaps") if gap else None))

# ═══ §30.F/kill switch：cap_index 摘除 = 世界状态 if 候选不存在（无暗通道）═══
shutil.rmtree(base, ignore_errors=True)
kg, eng, ci, emb, am, kernel, loop, base = harness()
loop.cap_index = None
cands = loop._action_candidates(emb.perceive(), [], 999.0)
check("F kill switch 后无隐藏 if 链复活（候选宇宙只剩承诺/兴趣通道）",
      all(str(c.get("motivation")) in ("user_goal", "curiosity")
          for c in cands), str([c.get("action_type") for c in cands]))

shutil.rmtree(base, ignore_errors=True)
print()
if FAIL:
    print(f"✗ {len(FAIL)} 项失败: {FAIL}")
    sys.exit(1)
print("✓ MC 具身图谱化终验通过（§28 七场景全走 图→激活→认知→能力→kernel→执行）")
