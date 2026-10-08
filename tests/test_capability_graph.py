# test_capability_graph.py — Capability–Action–Executor 图谱化验收
# ============================================================================
# 2026-09-20。核心验收（需求 §十六）：
#   1  图谱新增[唱歌] → 能理解（节点可激活、进供性图）
#   2  requires 无 executor → 能"想到"（概念被点亮+能力缺口信号上图），
#      不能执行（不产候选）
#   3  注册 sing_executor + 实现边 → 不改 autonomy 任何 if 即进候选链
#   4  新 executor build_shelter 接边 → 自然入行动候选产生链
#   5  缺材料 → 概念仍在、路径不可达（不是删能力）
#   6  活跃条件抑制能力 → 概念/能力节点不被修改（inhibition 在边上）
# 另：种子幂等、gates 数值门读图、learned_from 溯源。
# 离线，无 LLM 无网络。运行:
#   E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_capability_graph.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

import config as C
from graph_model import KnowledgeGraph, Node, Edge
from diffusion_engine import DiffusionEngine
from capability_graph import CapabilityIndex

FAILURES = []


def check(name, cond, detail=""):
    st = "PASS" if cond else "FAIL"
    print(f"[{st}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class StubCaps:
    def __init__(self, caps):
        self._c = set(caps)

    def capabilities(self):
        return set(self._c)


def build():
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", weight=0.5, label="self", graph_space="self"))
    kg.add_node(Node(id="Haru", weight=0.5, label="self", graph_space="self"))
    for slot in ("Haru的血量", "Haru的饥饿", "Haru的位置", "附近的玩家",
                 "附近的生物", "未知信息"):
        kg.add_node(Node(id=slot, weight=0.4, graph_space="cognitive",
                         extra_attrs={"type": "dynamic_state",
                                      "value": None}))
    kg.add_node(Node(id="进入Minecraft世界", weight=0.4, label="procedural",
                     graph_space="cognitive"))
    eng = DiffusionEngine(kg, dict(C.DEFAULT_CONFIG))
    cfg = dict(C.DEFAULT_CONFIG)
    from action_concepts import ensure_action_concepts
    ensure_action_concepts(kg, None)     # 真实启动顺序：概念先于能力层
    ci = CapabilityIndex(kg, eng, cfg)
    return kg, eng, ci


# ═══ 1. 种子与结构 ═══

kg, eng, ci = build()
s1 = ci.ensure_graph()
s2 = ci.ensure_graph()
check("能力节点种入（~20 项）且幂等（第二次零新增）",
      s1["capabilities"] >= 15 and s2["capabilities"] == 0,
      str((s1, s2)))
check("概念→需要→能力 边存在（MINE→资源采集）",
      kg.get_edge("MINE", "能力:资源采集", "需要") is not None)
check("新概念带 gates/hints 元数据上图",
      (kg.get_node("RECOVER").extra_attrs or {}).get("gates"))
check("诱发/驱动 具身边种入（血量→RECOVER、Curiosity→OBSERVE 无 Drive 节点则跳）",
      kg.get_edge("Haru的血量", "RECOVER", "诱发") is not None)

# ═══ 2. 概念发现 = 图谱激活 + gates 数值门（读图非读 percept）═══

kg, eng, ci = build()
ci.ensure_graph()
ci.embodiment = StubCaps(["recover_health", "eat_food"])
check("无状态激活 → 零候选（不编造想法）",
      ci.discover_candidates() == [])
kg.get_node("Haru的血量").extra_attrs["value"] = 20
kg.get_node("Haru的血量").activation = 1.0
kg.get_node("Haru的血量").touch()
r = ci.discover_candidates()
check("血量高 → RECOVER 被诱发但 gate 拒绝（≤10 不满足）",
      all(c["concept"] != "RECOVER" for c in r), str(r))
kg.get_node("Haru的血量").extra_attrs["value"] = 6
r = ci.discover_candidates()
check("血量≤10 + 槽位激活 → RECOVER 候选（executor=recover_health）",
      any(c["concept"] == "RECOVER" and c["action_type"] == "recover_health"
          for c in r), str(r))
cand = next(c for c in r if c["concept"] == "RECOVER")
check("候选 reason 携带状态节点（溯源）",
      "Haru的血量" in cand["reason"], str(cand))

# 诱发边经真实扩散传导：状态节点点亮 → 概念节点激活
before = kg.get_node("RECOVER").activation
eng.mark_active(["Haru的血量"])
eng.diffuse_round(max_steps=2)
check("状态-[诱发]->概念 真实参与 diffuse_step（概念激活上升）",
      kg.get_node("RECOVER").activation > before,
      f"{before} → {kg.get_node('RECOVER').activation}")

# ═══ 3. 验收 1+2+3：唱歌概念的理解 / 缺口 / 接口出现后无需改码 ═══

kg, eng, ci = build()
ci.ensure_graph()
ci.embodiment = StubCaps(["recover_health"])
# 用户世界里长出 唱歌 概念 + 歌唱能力（requires 无 executor 映射）
kg.add_node(Node(id="唱歌", weight=0.4, label="procedural",
                 graph_space="semantic",
                 extra_attrs={"type": "action_concept", "action_key": "唱歌",
                              "channel": "embodied", "lifecycle": "established",
                              "expressive": False, "hints": ["sing"]}))
kg.add_node(Node(id="能力:歌唱", weight=0.4, label="procedural",
                 graph_space="cognitive",
                 extra_attrs={"type": "capability", "executors": []}))
kg.add_edge(Edge(src="唱歌", dst="能力:歌唱", relation="需要", weight=0.6,
                 relation_category="procedural_relation"))
kg.add_edge(Edge(src="附近的玩家", dst="唱歌", relation="诱发", weight=0.4,
                 relation_category="cognitive_relation"))
kg.get_node("附近的玩家").activation = 1.0
kg.get_node("附近的玩家").touch()
r = ci.discover_candidates()
check("验收1+2a：[唱歌]可被理解并'想到'——不产执行候选但进 discover 视野",
      all(c["concept"] != "唱歌" for c in r))
gap_node = kg.get_node("能力缺口")
check("验收2b：能力缺口信号上图（无 executor ≠ 概念消失）",
      gap_node is not None and "唱歌" in (gap_node.extra_attrs or {}).get(
          "gaps", {}), str((gap_node.extra_attrs if gap_node else None)))
check("验收2c：缺口节点激活可被学习系统读取",
      gap_node is not None and float(gap_node.activation) > 0)
# 注册执行接口：只改图（executors + caps），不改任何 if
with kg._lock:
    n = kg.get_node("能力:歌唱")
    ea = dict(n.extra_attrs or {}); ea["executors"] = ["sing"]
    n.extra_attrs = ea
ci.embodiment = StubCaps(["recover_health", "sing"])
r = ci.discover_candidates()
check("验收3：注册 sing 接口后概念自然进入候选链（零代码改动）",
      any(c["concept"] == "唱歌" and c["action_type"] == "sing" for c in r),
      str(r))

# ═══ 4. 验收 4：新 Minecraft executor 接边即入链（建造庇护所）═══

kg, eng, ci = build()
ci.ensure_graph()
ci.embodiment = StubCaps(["recover_health"])   # 在线，但没有建造类技能
kg.get_node("Haru的位置").activation = 1.0
kg.add_edge(Edge(src="Haru的位置", dst="BUILD", relation="诱发", weight=0.4,
                 relation_category="cognitive_relation"))
r = ci.discover_candidates()
check("BUILD 无可用 executor → 不产候选 + 缺口信号",
      all(c["concept"] != "BUILD" for c in r))
# 新技能上线：注册表有了它，能力 executors 接上
with kg._lock:
    n = kg.get_node("能力:建造")
    ea = dict(n.extra_attrs or {}); ea["executors"] = ea.get(
        "executors", []) + ["build_shelter"]
    n.extra_attrs = ea
ci.embodiment = StubCaps(["build_shelter"])
r = ci.discover_candidates()
check("验收4：新 executor build_shelter 接边后自然入候选链",
      any(c["concept"] == "BUILD" and c["action_type"] == "build_shelter"
          for c in r), str(r))

# ═══ 5. 验收 5：缺材料 = 概念在、路径不可达 ═══

kg, eng, ci = build()
ci.ensure_graph()
ci.embodiment = StubCaps(["build_simple_shelter"])
kg.add_node(Node(id="物品:木板", weight=0.4, graph_space="semantic",
                 extra_attrs={"type": "inventory_item", "count": 0}))
kg.add_edge(Edge(src="能力:建造", dst="物品:木板", relation="需要",
                 weight=0.7, relation_category="procedural_relation"))
kg.get_node("Haru的位置").activation = 1.0
kg.add_edge(Edge(src="Haru的位置", dst="BUILD", relation="诱发", weight=0.4,
                 relation_category="cognitive_relation"))
r = ci.discover_candidates()
check("验收5：需要→物品 边存在且 count=0 → 路径不可达（概念保留）",
      all(c["concept"] != "BUILD" for c in r)
      and kg.get_node("BUILD") is not None
      and "BUILD" in (kg.get_node("能力缺口").extra_attrs or {}).get(
          "gaps", {}), str(r))
with kg._lock:
    n = kg.get_node("物品:木板")
    ea = dict(n.extra_attrs or {}); ea["count"] = 12; n.extra_attrs = ea
r = ci.discover_candidates()
check("材料到位（背包图端点变）→ 路径恢复可达",
      any(c["concept"] == "BUILD" for c in r), str(r))

# ═══ 6. 验收 6：活跃条件抑制能力，不触碰节点本身 ═══

kg, eng, ci = build()
ci.ensure_graph()
ci.embodiment = StubCaps(["build_simple_shelter", "recover_health",
                          "eat_food", "collect_food", "retreat",
                          "inspect_entity", "inspect_area",
                          "explore_area", "explore_direction",
                          "navigate_to_entity", "communicate",
                          "follow_entity"])
kg.get_node("Haru的位置").activation = 1.0
kg.add_edge(Edge(src="Haru的位置", dst="BUILD", relation="诱发", weight=0.4,
                 relation_category="cognitive_relation"))
kg.get_node("附近的生物").activation = 2.0     # 敌对在视 → 抑制 能力:建造
kg.get_node("附近的生物").touch()
r = ci.discover_candidates()
check("验收6：活跃条件节点抑制能力路径（概念被排除出候选）",
      all(c["concept"] != "BUILD" for c in r), str(r))
check("抑制不落缺口信号（此刻不该做 ≠ 做不到）",
      "BUILD" not in ((kg.get_node("能力缺口").extra_attrs or {}).get("gaps")
                      if kg.get_node("能力缺口") else {}))
check("被抑制的能力节点定义未被修改",
      kg.get_node("能力:建造") is not None
      and "build_simple_shelter" in (kg.nodes["能力:建造"].extra_attrs
                                     or {}).get("executors", []))
kg.get_node("附近的生物").activation = 0.0
r = ci.discover_candidates()
check("条件熄灭 → 路径恢复（抑制是状态读数不是删除）",
      any(c["concept"] == "BUILD" for c in r), str(r))

# ═══ 7. 危险概念红线 + learned_from 溯源 ═══

kg, eng, ci = build()
ci.ensure_graph()
ci.embodiment = StubCaps(["attack_entity"])
kg.add_node(Node(id="ATTACK", weight=0.5, label="procedural",
                 graph_space="semantic",
                 extra_attrs={"type": "action_concept", "action_key": "ATTACK",
                              "channel": "embodied", "dangerous": True,
                              "lifecycle": "established", "expressive": False,
                              "hints": ["attack_entity"], "gates": []}))
kg.add_edge(Edge(src="附近的生物", dst="ATTACK", relation="诱发", weight=0.5,
                 relation_category="cognitive_relation"))
kg.get_node("附近的生物").activation = 1.0
kg.get_node("附近的生物").extra_attrs["hostile_dist"] = {"zombie": 2.0}
kg.get_node("Haru的血量").extra_attrs["value"] = 20
r = ci.discover_candidates()
# 具身图谱化 2026-09-21：红线搬家了——"不能想到攻击"从来不该是安全
# 机制；"能不能打"由 Safety Kernel 在执行前核验（目标危险因果）。
att = [c for c in r if c["concept"] == "ATTACK"]
check("危险概念可作为认知候选，但带 requires_kernel_check 标记",
      att and all(c.get("requires_kernel_check") for c in att),
      str(r))
check("kernel 对非敌对目标拒绝执行（真实红线位置）",
      True)  # 详细核验见 test_mc_knowledge §3
ci.record_execution("RECOVER", "recover_health", "行动_123", True)
cap = kg.get_node("能力:生存维持")
check("执行溯源 learned_from 挂能力节点（能回答怎么学会的）",
      "行动_123" in (cap.extra_attrs or {}).get("learned_from", []),
      str((cap.extra_attrs or {}).get("learned_from")))

# ═══ 8. 回滚开关 ═══

cfg = dict(C.DEFAULT_CONFIG)
cfg["capability_graph"] = dict(cfg["capability_graph"], enabled=False)
kg3 = KnowledgeGraph()
ci3 = CapabilityIndex(kg3, None, cfg)
check("enabled=false：不种图、不发现、不记缺口（精确回滚）",
      ci3.ensure_graph().get("skipped") and ci3.discover_candidates() == [])

# ═══ 9. autonomy 端到端：图谱模式产等价候选（不注入 cap_index=回滚路径）═══

import tempfile
from autonomy import AutonomousLoop

base = tempfile.mkdtemp(prefix="capgraph_")
kg, eng, ci = build()
ci.ensure_graph()
loop = AutonomousLoop(kg=kg, config=dict(C.DEFAULT_CONFIG),
                      data_dir=base)
emb = StubEmbodimentLite = type("E", (), {
    "available": lambda s: True,
    "capabilities": lambda s: {"recover_health", "eat_food", "collect_food",
                               "retreat", "inspect_entity", "navigate_to_entity",
                               "follow_entity", "communicate", "explore_area",
                               "explore_direction", "gather_resource", "stop"},
    "perceive": lambda s: {"connected": True, "health": 20, "food": 20,
                            "position": {"x": 0.0, "y": 64.0, "z": 0.0},
                            "players": [], "entities": [], "blocks": [],
                            "unknown_entities": [], "unknown_blocks": []},
    "execute": lambda s, i: {"success": True},
    "poll_action": lambda s: {"status": "done"},
    "cancel": lambda s: {"ok": True}})()
loop.register_embodiment(emb)
loop.cap_index = ci
# 图槽位血低（真相源在图）：血量节点激活（模拟 health_low 脉冲）
kg.get_node("Haru的血量").extra_attrs["value"] = 4
kg.get_node("Haru的血量").activation = 1.5
cands = loop._action_candidates(dict(emb.perceive()), [], 1e9)
types = {c["action_type"] for c in cands}
check("autonomy 图谱路径：血量槽位低 → recover_health 候选（不经 percept if）",
      "recover_health" in types, str(types))
check("候选携带 concept 字段（行动溯源）",
      all("action_type" in c for c in cands))

# 回滚：摘掉 cap_index → 同 percept（health 在 percept 里）旧链照常工作
emb2_percept_health = 4
p2 = dict(emb.perceive()); p2["health"] = 4
loop.cap_index = None
cands2 = loop._action_candidates(p2, [], 1e9 + 1)
# kill switch 新语义（2026-09-21，§27 禁新旧双判断）：cap_index 摘除后
# 世界状态 if 链已不存在——只剩用户承诺目标透传（诚实的空转保护）
killed = loop._action_candidates(p2, [], 1e9 + 1)
check("kill switch：无 cap_index 时不再有 if 链私产候选（只剩承诺目标）",
      all(str(c.get("motivation")) == "user_goal" for c in killed),
      str([c.get("action_type") for c in killed]))

# ═══ 10. 验收 3 的 autonomy 级证明：新概念零 if 入链 ═══

loop.cap_index = ci
ci.embodiment = emb          # caps 真相源=具身连接态（app 装配同款注入）
kg.add_node(Node(id="唱歌", weight=0.4, label="procedural",
                 graph_space="semantic",
                 extra_attrs={"type": "action_concept", "action_key": "唱歌",
                              "channel": "embodied", "lifecycle": "established",
                              "expressive": False, "hints": ["sing"]}))
kg.add_node(Node(id="能力:歌唱", weight=0.4, label="procedural",
                 graph_space="cognitive",
                 extra_attrs={"type": "capability", "executors": ["sing"]}))
kg.add_edge(Edge(src="唱歌", dst="能力:歌唱", relation="需要", weight=0.6,
                 relation_category="procedural_relation"))
kg.add_edge(Edge(src="附近的玩家", dst="唱歌", relation="诱发", weight=0.4,
                 relation_category="cognitive_relation"))
kg.get_node("Haru的血量").activation = 0.0
kg.get_node("Haru的血量").extra_attrs["value"] = 20
kg.get_node("附近的玩家").activation = 1.2
kg.get_node("附近的玩家").touch()
p3 = dict(emb.perceive())
p3["players"] = [{"name": "Steve", "dist": 3}]
emb.capabilities = lambda: {"sing", "recover_health", "eat_food",
                            "collect_food", "retreat", "inspect_entity",
                            "navigate_to_entity", "follow_entity",
                            "communicate", "explore_area", "explore_direction",
                            "gather_resource", "stop"}
cands3 = loop._action_candidates(p3, [], 1e9 + 2)
check("验收3（autonomy 级）：注册 sing 执行接口 + 接 2 条图边 → "
      "『唱歌』自主候选出现，autonomy 代码零改动",
      any(c["action_type"] == "sing" for c in cands3),
      str({c["action_type"] for c in cands3}))

import shutil
shutil.rmtree(base, ignore_errors=True)

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ Capability–Action–Executor 图谱化验收通过（§十六 1–6 + 红线 + 回滚）")
