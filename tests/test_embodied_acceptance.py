# test_embodied_acceptance.py — Minecraft 具身改造验收测试（规范 §十八）
# ============================================================================
# 离线仿真 6 个验收场景（真实 autonomy + ActionManager + 具身适配器 +
# Skill Library，仅 bot HTTP 层与 LLM 为桩）：
#
#   Test 1 短指令：L1 快速解析 → ActionNode → 立即执行（先行动后回复）
#   Test 2 复杂指令：多意图分解 → 目标队列逐步执行（不直接生成操作序列）
#   Test 3 自主模式：行为来自 事件→内部状态→动机→候选（不是随机列表）
#   Test 4 自主探索：不同内部状态 → 不同行动（同世界不同反应）
#   Test 5 行动中断：玩家呼叫 / 血量下降 / 苦力怕出现 → 按优先级打断
#   Test 6 行动失败学习：没镐挖矿 / 路径阻挡 / 目标消失 → 真实失败并记录
#
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_embodied_acceptance.py
# ============================================================================

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

import minecraft.bridge as bridge
from graph_model import KnowledgeGraph, Node
from diffusion_engine import DiffusionEngine
from autonomy import AutonomousLoop
from action_system import ActionManager
from minecraft.embodiment import MinecraftEmbodiment
from action_intents import l1_to_action, intention_to_action, reflex_to_action
from cognitive_complexity import estimate_complexity

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ═══════════════════════════════════════════════════════════
# 可编排的假世界（桥层桩）：测试直接改 WORLD / FIND / RECEIPT
# ═══════════════════════════════════════════════════════════

class FakeWorld:
    def __init__(self):
        self.state = {
            "connected": True, "username": "Haru",
            "position": {"x": 0.0, "y": 64.0, "z": 0.0},
            "health": 20, "food": 20, "heldItem": None,
            "playersNearby": [], "nearbyEntities": [],
            "nearbyBlocks": [], "chat": [], "connectedAt": 1,
            "timeOfDay": 1000, "isRaining": False,
        }
        self.find_positions = []
        self.inventory_items = []
        self.receipt = {"status": "done", "detail": {}}
        self.calls = []

    def install(self):
        w = self

        def _call(path, payload=None, timeout=3):
            w.calls.append((path, payload or {}))
            if path == "/find_blocks":
                return {"ok": True, "positions": list(w.find_positions)}
            return {"ok": True}

        bridge.get_state = lambda: dict(w.state)
        bridge.health = lambda: True
        bridge.say = lambda t: True
        bridge.stop = lambda: (w.calls.append(("/stop", {})), True)[1]
        bridge.stop_goto = lambda: (w.calls.append(("/stop_goto", {})),
                                    {"ok": True})[1]
        bridge.stopfollow = lambda: (w.calls.append(("/stopfollow", {})), 0)[1]
        bridge.stop_combat = lambda: (w.calls.append(("/stop_combat", {})),
                                      {"ok": True})[1]
        bridge.sprint = lambda on=True: {"ok": True}
        bridge.move = lambda d, s=1.0: True
        bridge.sneak = lambda on=True: {"ok": True}
        bridge.inventory = lambda: {"ok": True, "items": list(w.inventory_items)}
        bridge.get_action_result = lambda: dict(w.receipt)
        bridge.call = _call
        bridge.look_at = lambda x, y, z: _call("/look_at", {"x": x, "y": y, "z": z})
        bridge.goto_coords = lambda x, y, z: _call("/goto", {"x": x, "y": y, "z": z})
        bridge.dig = lambda b, count=1: _call("/dig", {"block": b, "count": count})


CFG = {"lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 4,
       "theta_threshold": 0.01, "activation_max": 5.0, "min_spread_threshold": 0.01,
       "activation_epsilon": 1e-4, "input_similarity_floor": 0.5,
       "input_default_bonus": 0.5, "theta_action": 0.5}

DYN_NODES = ("Haru的位置", "Haru的血量", "Haru的饥饿", "Haru的手持物",
             "附近的玩家", "附近的方块", "附近的生物")


class Harness:
    """一个可复位的认知-行动闭环（真实代码路径，只有桥与 LLM 是桩）。"""

    def __init__(self, world: FakeWorld = None):
        self.base = tempfile.mkdtemp(prefix="fas_accept_")
        self.world = world or FakeWorld()
        self.world.install()
        self.kg = KnowledgeGraph()
        self.kg.add_node(Node(id="Haru", graph_space="self"))
        self.kg.add_node(Node(id="用户"))
        self.kg.add_node(Node(id="好奇"))
        self.kg.add_node(Node(id="CuriosityDrive", activation=3.0))
        for nid in DYN_NODES:
            self.kg.add_node(Node(id=nid, label="declarative-semantic", graph_space="cognitive"))
        self.engine = DiffusionEngine(self.kg, dict(CFG))
        self.engine.name_to_node = dict(self.kg.nodes)
        self.emb = MinecraftEmbodiment(kg=self.kg, engine=self.engine,
                                       perceive_into_graph=True)
        self.am = ActionManager(embodiment=self.emb, kg=self.kg,
                                engine=self.engine, config={},
                                data_dir=self.base)
        self.loop = AutonomousLoop(kg=self.kg, engine=self.engine, config={},
                                   data_dir=self.base, action_manager=self.am)
        self.loop.register_embodiment(self.emb)
        # 生产形态接线（2026-09-21 具身图谱化）：概念/能力/世界语义/
        # 状态映射/安全 kernel 全部上图，与 app 装配同构
        import config as _C
        from action_concepts import ensure_action_concepts
        from capability_graph import CapabilityIndex
        from embodied_mapper import EmbodiedStateMapper
        from safety_kernel import SafetyKernel
        import mc_knowledge as _mck
        for nid in ("未知信息", "地面物品", "建筑结构", "生存需求"):
            if nid not in self.kg.nodes:
                self.kg.add_node(Node(id=nid, label="declarative-semantic",
                                      graph_space="cognitive"))
        ensure_action_concepts(self.kg, self.engine)
        _mck.init_protection_node(_C.DEFAULT_CONFIG)
        _mck.ensure_mc_world(self.kg, dict(_C.DEFAULT_CONFIG))
        ci = CapabilityIndex(self.kg, self.engine, dict(_C.DEFAULT_CONFIG))
        ci.embodiment = self.emb
        ci.ensure_graph()
        self.loop.cap_index = ci
        self.loop.mapper = EmbodiedStateMapper(self.kg, self.engine,
                                               dict(_C.DEFAULT_CONFIG))
        self.kernel = SafetyKernel(kg=self.kg, engine=self.engine,
                                   config=dict(_C.DEFAULT_CONFIG))
        self.am.kernel = self.kernel
        self.loop.set_mode("on")

    def flush(self):
        if self.emb.ctx.session.get("skill"):
            self.emb.ctx.session["poll_after"] = 0

    def run_until_settled(self, now, max_rounds=6, step_s=13.0):
        """推进 tick 直到当前动作结算（模拟 CC 循环节拍）。"""
        out = None
        for _ in range(max_rounds):
            self.flush()
            out = self.loop.tick(now=now)
            now += step_s
            if self.am.current is None:
                break
        return out

    def cleanup(self):
        shutil.rmtree(self.base, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# Test 1 短指令："跟我走。" → 快速认知 → Action → 随后回复
# ═══════════════════════════════════════════════════════════
print("── Test 1 短指令（跟我走）──────────────────────────────")
world = FakeWorld()
world.state["playersNearby"] = [{"name": "Hellucigen", "dist": 6.0,
                                 "rel": {"dx": 5.0, "dy": 0.0, "dz": 3.0}}]
h = Harness(world)

# 1a. 复杂度估计：短指令走 Level 1（轻认知）
cx = estimate_complexity("跟我走")
check("T1 短指令被估为 Level 1（轻认知快通道）", cx["level"] == 1, str(cx))

# 1b. L1 解析产物（桩：与 PARSE_FAST 的 JSON 输出同构）→ ActionNode
l1_parsed = {"intent": "follow_user", "target": "用户", "count": None,
             "urgency": 0.9, "needs_reply": True, "needs_action": True,
             "nodes": ["用户"], "memory_type": "episodic"}
spec = l1_to_action(l1_parsed, "Hellucigen")
check("T1 L1 意图映射成 follow_entity ActionNode",
      spec and spec["action_type"] == "follow_entity"
      and spec["params"].get("entity") == "Hellucigen", str(spec))
check("T1 ActionNode 带紧迫度（来自认知解析）",
      spec.get("urgency") == 0.9 and spec.get("priority", 0) >= 0.8, str(spec))

# 1c. 先行动：propose 立即启动（不等任何语言生成）
prop = h.am.propose(spec, source="user")
check("T1 动作立即开始（先行动）", prop.get("started") is True, str(prop))
check("T1 follow 指令已发到 bot（/follow）",
      any(c[0] == "/follow" for c in world.calls), str(world.calls))
check("T1 动作处于承诺期（持续跟随）", h.am.busy()
      and h.am.current["action_type"] == "follow_entity")

# 1d. 后回复：动作已开始后才生成语言（这里验证动作结果可用于语言层）
check("T1 动作结果携带 describe（供短回应引用：'好，跟着你走'）",
      isinstance(prop.get("describe"), str) and len(prop.get("describe", "")) > 0,
      str(prop))
h.am.cancel("测试结束")
h.cleanup()

# 反射快路径（零 LLM）也走同一条 Action 管路
reflex = {"action": "follow", "params": {}, "matched": "跟着我"}
spec2 = reflex_to_action(reflex, "Hellucigen")
check("T1 反射解析同样映射到 follow_entity（不绕过 Action 系统）",
      spec2 and spec2["action_type"] == "follow_entity", str(spec2))

# ═══════════════════════════════════════════════════════════
# Test 2 复杂指令："我们去找村子，路上看到铁就挖一点，晚上回来"
# ═══════════════════════════════════════════════════════════
print("── Test 2 复杂指令（多意图）────────────────────────────")
cx = estimate_complexity("我们先去那个村庄看看，如果有铁的话就挖一点，"
                         "没有的话就先找食物，然后晚上回来。")
check("T2 复杂指令被估为 Level 2（全认知）", cx["level"] == 2, str(cx["reasons"]))

# L2 意图分解产物（桩：与 INTENTION_EXTRACT 输出同构）
l2_intentions = [
    {"type": "go_to", "target": "村庄", "params": {}, "note": None},
    {"type": "mine_block", "target": "铁", "params": {}, "note": "如果路上看到铁"},
    {"type": "collect_food", "target": None, "params": {},
     "note": "没有铁就先找食物"},
    {"type": "return_home", "target": None, "params": {}, "note": "晚上之前"},
]
specs = [intention_to_action(it) for it in l2_intentions]
specs = [s for s in specs if s]
check("T2 四个高层意图全部映射成 ActionNode", len(specs) == 4,
      str([s.get("action_type") for s in specs]))
check("T2 go_to（村庄）→ 带目标的区域探索（不是具体操作序列）",
      specs[0]["action_type"] == "explore_area"
      and specs[0]["params"].get("goal") == "village", str(specs[0]))
check("T2 挖铁 → gather_resource(iron_ore)（中文已翻译成世界名）",
      specs[1]["action_type"] == "gather_resource"
      and specs[1]["params"].get("resource") == "iron_ore", str(specs[1]))
check("T2 回家 → navigate_home", specs[3]["action_type"] == "navigate_home",
      str(specs[3]))
check("T2 条件句保留在 note（不拆成两套计划）",
      specs[1]["params"].get("note") == "如果路上看到铁", str(specs[1]))

# 执行：第一个立即开始，其余进目标队列逐步执行
world = FakeWorld()
h = Harness(world)
prop = h.am.propose(specs[0], source="user")
for s in specs[1:]:
    h.am.queue_goal(s)
check("T2 第一个意图立即执行", prop.get("started") is True, str(prop))
check("T2 其余意图在目标队列（未生成一长串 Minecraft 操作）",
      len(h.am.status()["goal_queue"]) == 3, str(h.am.status()["goal_queue"]))
check("T2 当前动作是探索（bot 收到的是探索第一步，不是操作清单）",
      h.am.current["action_type"] == "explore_area"
      and any(c[0] == "/goto" for c in world.calls), str(world.calls[-3:]))
# 逐步执行：探索被取消/结算后，队列依次出队
h.am.cancel("测试推进")
out = h.am.tick(now=200.0)
check("T2 探索结束后 → 队列出队挖铁", out.get("started_goal") == "gather_resource",
      str(out))
check("T2 队列剩 2（找食物、回家）", len(h.am.status()["goal_queue"]) == 2)
h.cleanup()

# ═══════════════════════════════════════════════════════════
# Test 3 自主模式：不给指令 → 行为来自事件/需求/动机
# ═══════════════════════════════════════════════════════════
print("── Test 3 自主模式（事件驱动）──────────────────────────")
world = FakeWorld()
# 她周围：树、煤矿、铁矿、洞穴空气、一只没见过的生物
world.state["nearbyBlocks"] = [
    {"name": "oak_log", "count": 4}, {"name": "coal_ore", "count": 2},
    {"name": "iron_ore", "count": 1}, {"name": "cave_air", "count": 3},
]
world.state["nearbyEntities"] = [{"name": "axolotl", "displayName": "Axolotl",
                                  "dist": 5.0,
                                  "rel": {"dx": -4.0, "dy": 0.0, "dz": 3.0}}]
world.find_positions = [{"x": 3.0, "y": 64.0, "z": 0.0}]
h = Harness(world)
r = h.loop.tick(now=1000.0)
check("T3 自主产生了行动（不是站着不动）", r.get("acted") is True, str(r))
cands = h.loop.state().get("candidates") or []
check("T3 候选全部带动机标注（每个候选都能回答'为什么'）",
      cands and all(c.get("motivation") for c in cands), str(cands[:3]))
check("T3 候选全部带评分依据（可解释，无随机抽签）",
      all(c.get("explain") for c in cands), str(cands[:2]))
check("T3 当前动作在认知层留了依据（reason 非空）",
      (h.am.current and h.am.current.get("reason"))
      or (h.am.status()["recent"]
          and h.am.status()["recent"][-1].get("action")),
      str(h.am.current))
# 世界事件确实进入了认知（资源/未知对象事件）
evs = h.loop.state().get("world_events") or []
ev_types = {e.get("type") for e in evs}
check("T3 世界事件被检测（资源发现事件在场）", "resource_detected" in ev_types,
      str(ev_types))
# 图里出现了未知对象节点（新颖性来源）
check("T3 未知生物进入图谱（新颖性来源）",
      any(n.startswith("UnknownEntity_") for n in h.kg.nodes),
      str([n for n in h.kg.nodes if n.startswith("Unknown")][:4]))
# 承诺期：pending 动作（采集）在连续 tick 之间不换动作
world.receipt = {"status": "running", "detail": {}}
world.state["nearbyEntities"] = []          # 未知生物观察完（同步结算）了
h.am.propose({"action_type": "gather_resource", "target": "oak_log",
              "params": {"resource": "oak_log", "quantity": 2},
              "motivation": "resource_opportunity", "reason": ["oak_log_event"],
              "priority": 0.5}, source="autonomy")
first = h.am.current["action_id"] if h.am.current else None
check("T3 采集动作进入承诺期", first is not None, str(h.am.status()))
h.loop.tick(now=1005.0)
check("T3 动作承诺期内不重新选择（current_action 不变）",
      h.am.current is not None and h.am.current["action_id"] == first,
      str(h.am.current and h.am.current["action_id"]))
# 结算后行为留痕
world.receipt = {"status": "done", "detail": {}}
h.run_until_settled(now=1100.0)
check("T3 动作结算（成功记录）",
      h.am.status()["stats"]["success"] >= 1, str(h.am.status()["stats"]))
h.cleanup()

# ═══════════════════════════════════════════════════════════
# Test 4 自主探索：同一世界框架，不同内部状态 → 不同行动
# ═══════════════════════════════════════════════════════════
print("── Test 4 自主探索（内部状态决定行为）──────────────────")
# 4a. 饥饿状态 → 找食物/吃（而不是继续闲逛）
world = FakeWorld()
world.state["food"] = 5   # 很饿
world.state["nearbyBlocks"] = [{"name": "oak_log", "count": 5}]
world.state["nearbyEntities"] = [{"name": "cow", "displayName": "Cow",
                                  "dist": 8.0,
                                  "rel": {"dx": 6.0, "dy": 0.0, "dz": 4.0}}]
h = Harness(world)
r = h.loop.tick(now=1000.0)
# 2026-09-21 §28.2/§13：饥饿必须由图谱通路"产生"生存候选（不钉死
    # 谁赢——候选竞争是认知的；生存置顶类调度插队已拆除）。
_hungry_cands = {c["type"] for c in h.loop.state()["candidates"]}
check("T4a 饥饿时产生生存类候选（吃/找食物在候选席上）",
      r.get("acted")
      and _hungry_cands & {"eat_food", "collect_food"},
      str((r.get("intent"), sorted(_hungry_cands))))
h.cleanup()

# 4b. 低血量 → 回血（不是去挖矿）
world = FakeWorld()
world.state["health"] = 6
world.state["nearbyBlocks"] = [{"name": "diamond_ore", "count": 3}]
h = Harness(world)
r = h.loop.tick(now=1000.0)
check("T4b 血量低时优先恢复（recover_health）",
      # 2026-09-21 §28.3：低血量不钉死单一动作——生存家族内
      # 任一候选（恢复/寻安全/觅食）经认知竞争选出即合法
      r.get("acted") and r.get("intent") in (
          "recover_health", "seek_safety", "retreat", "eat_food",
          "collect_food"), str(r))
h.cleanup()

# 4c. 威胁中等距离 → 撤离（即使附近有钻石）
world = FakeWorld()
world.state["health"] = 14
world.state["nearbyBlocks"] = [{"name": "diamond_ore", "count": 3}]
world.state["nearbyEntities"] = [{"name": "zombie", "displayName": "Zombie",
                                  "dist": 10.0,
                                  "rel": {"dx": -8.0, "dy": 0.0, "dz": 6.0}}]
h = Harness(world)
r = h.loop.tick(now=1000.0)
check("T4c 威胁在场时撤离压过挖钻石（安全优先）",
      r.get("acted") and r.get("intent") == "retreat"
      and r.get("target") == "zombie", str(r))
h.cleanup()

# 4d. 资源丰富且安全 → 采集；一无所知 → 带方向探索（都不是随机走路）
world = FakeWorld()
world.state["nearbyBlocks"] = [{"name": "iron_ore", "count": 2}]
world.find_positions = [{"x": 4.0, "y": 64.0, "z": 0.0}]
h = Harness(world)
r = h.loop.tick(now=1000.0)
check("T4d 有铁矿时采集（机会驱动）",
      r.get("acted") and r.get("intent") == "gather_resource"
      and r.get("target") == "iron_ore", str(r))
h.cleanup()

world = FakeWorld()
world.state["nearbyBlocks"] = []
world.state["nearbyEntities"] = []
h = Harness(world)
r = h.loop.tick(now=1000.0)
check("T4d 空旷且一无所知时 → 带方向的探索（有目标/停止条件）",
      r.get("acted") and r.get("intent") in ("explore_direction", "explore_area"),
      str(r))
check("T4d 探索动作带 max_time（停止条件，不是无限乱逛）",
      h.am.current and h.am.current.get("timeout_s") is not None, str(h.am.current))
h.cleanup()

# ═══════════════════════════════════════════════════════════
# Test 5 行动中断：挖矿时 玩家呼叫 / 血量下降 / 苦力怕出现
# ═══════════════════════════════════════════════════════════
print("── Test 5 行动中断（优先级竞争）────────────────────────")
# 5a. 玩家呼叫打断挖掘
world = FakeWorld()
world.find_positions = [{"x": 3.0, "y": 64.0, "z": 0.0}]
h = Harness(world)
h.am.propose({"action_type": "gather_resource", "target": "oak_log",
              "params": {"resource": "oak_log"},
              "priority": 0.5}, source="autonomy")
check("T5a 挖掘进行中", h.am.busy(), str(h.am.status()["current"]))
p = h.am.propose({"action_type": "follow_entity", "target": "Hellucigen",
                  "params": {"entity": "Hellucigen"}, "priority": 0.85},
                 source="user")
check("T5a 用户呼叫 → 打断挖掘 → 开始跟随",
      p.get("started") and p.get("interrupted")
      and h.am.current["action_type"] == "follow_entity", str(p))
check("T5a 挖掘动作被真实取消（停寻路/解除跟随至少其一）",
      any(c[0] in ("/stop_goto", "/stop", "/stopfollow", "/stop_combat")
          for c in world.calls), str([c[0] for c in world.calls]))
h.cleanup()

# 5b. 血量下降打断（生存紧急）
world = FakeWorld()
world.find_positions = [{"x": 3.0, "y": 64.0, "z": 0.0}]
h = Harness(world)
h.am.propose({"action_type": "gather_resource", "target": "oak_log",
              "params": {"resource": "oak_log"},
              "priority": 0.5}, source="autonomy")
world.state["health"] = 5
out = h.am.tick(now=1010.0)
# 2026-09-21 新语义：调度器不再按血量代做行为决定（HP≤8 强制
# seek_safety 是"代码替认知决定"）。hp=5 时采矿继续，生存压力由
# mapper 注入图谱、由候选竞争选恢复动作（T4b 覆盖）；
# kernel 只剩濒死停手（不指定去向）。
check("T5b 血量 5 不再触发调度器代决定的逃跑（去向交给认知）",
      out.get("interrupted_by") != "seek_safety"
      and h.am.current is not None, str(out))
h.cleanup()
# 濒死停手（kernel K4）：HP≤4 → 无条件停止占用动作，不指定去哪
world = FakeWorld()
world.find_positions = [{"x": 3.0, "y": 64.0, "z": 0.0}]
h = Harness(world)
h.am.propose({"action_type": "gather_resource", "target": "oak_log",
              "params": {"resource": "oak_log"},
              "priority": 0.5}, source="autonomy")
world.state["health"] = 3
out = h.am.tick(now=1010.0)
check("T5b2 濒死（HP≤4）→ kernel 停手（只停，不指定去向）",
      out.get("interrupted_by") == "death_floor_stop"
      and not h.am.busy(), str(out))
h.cleanup()

# 5c. 苦力怕出现打断
world = FakeWorld()
world.find_positions = [{"x": 3.0, "y": 64.0, "z": 0.0}]
h = Harness(world)
h.am.propose({"action_type": "gather_resource", "target": "oak_log",
              "params": {"resource": "oak_log"},
              "priority": 0.5}, source="autonomy")
world.state["nearbyEntities"] = [{"name": "creeper", "dist": 4.0,
                                  "rel": {"dx": 0.0, "dy": 0.0, "dz": -4.0}}]
out = h.am.tick(now=1010.0)
# 2026-09-21：贴脸苦力怕不再由调度器强制 retreat（"看到敌对必逃"
# 是行为裁判）。危险经 mapper 注入生存压力，撤离/反制由候选竞争决定
# （T4c 覆盖撤离胜出的路径）。
check("T5c 敌对贴脸不再触发调度器代决定的撤退",
      out.get("interrupted_by") != "retreat", str(out))
h.cleanup()

# 5d. 不可打断承诺：生存动作不被普通动作抢占
world = FakeWorld()
world.state["nearbyEntities"] = [{"name": "zombie", "dist": 4.0,
                                  "rel": {"dx": 0.0, "dy": 0.0, "dz": -4.0}}]
h = Harness(world)
h.am.propose({"action_type": "seek_safety", "priority": 0.95},
             source="survival")
check("T5d 撤离进行中", h.am.busy()
      and h.am.current["action_type"] == "seek_safety", str(h.am.status()["current"]))
p = h.am.propose({"action_type": "explore_area", "priority": 0.4},
                 source="autonomy")
check("T5d 撤离中不会被探索打断（优先级纪律）",
      p.get("started") is False and h.am.current["action_type"] == "seek_safety",
      str(p))
h.cleanup()

# ═══════════════════════════════════════════════════════════
# Test 6 行动失败学习：没镐挖铁 / 目标消失 → 真实失败并记录
# ═══════════════════════════════════════════════════════════
print("── Test 6 行动失败学习（因果原料）──────────────────────")
# 6a. 没有镐却想挖铁 → 技能层如实拒绝（tool_missing）
world = FakeWorld()
world.state["nearbyBlocks"] = [{"name": "iron_ore", "count": 2}]
world.find_positions = [{"x": 5.0, "y": 64.0, "z": 0.0}]
world.inventory_items = []          # 空背包：没有任何镐
h = Harness(world)
r = h.loop.tick(now=1000.0)
h.run_until_settled(now=1000.0)
recent = h.am.status()["recent"]
check("T6a 没镐挖铁 → 失败原因语义化（tool_missing）",
      any("tool_missing" in str(a.get("reason")) for a in recent),
      str(recent[-2:]))
check("T6a 失败计入统计（不是假装成功）",
      h.am.status()["stats"]["failed"] >= 1, str(h.am.status()["stats"]))
# 6b. 学习：同一失败动作的重试受上限约束，之后必然改道或停手
h.loop._last_action_ts = 0.0   # 跳过最小间隔（专注测退避/上限）
iron_tries = 0
for i in range(6):
    r2 = h.loop.tick(now=1200.0 + i * (h.loop.cfg["min_interval_s"] + 1))
    if r2.get("acted") and r2.get("intent") == "gather_resource"             and r2.get("target") == "iron_ore":
        iron_tries += 1
check("T6b 同类失败重试有上限（不会无限重试挖铁）",
      iron_tries <= int(h.loop.cfg["max_attempts"]),
      f"iron_tries={iron_tries}")
att = h.loop._attempts.get("gather_resource@iron_ore") or {}
check("T6b 失败计数真实记录（供后续评分与退避）",
      int(att.get("count", 0)) >= 1, str(att))
h.cleanup()

# 6c. 目标消失：找到了方块但挖的时候没了 → 重新找/如实结算
world = FakeWorld()
world.state["nearbyBlocks"] = [{"name": "oak_log", "count": 2}]
world.find_positions = [{"x": 5.0, "y": 64.0, "z": 0.0}]
h = Harness(world)
r = h.loop.tick(now=1000.0)
check("T6c 采集启动（承诺期）", h.am.busy())
world.find_positions = []           # 方块被别人挖掉了
world.receipt = {"status": "failed", "detail": {"reason": "block_not_found"}}
h.run_until_settled(now=1010.0, max_rounds=4)
_st6 = h.am.status()["stats"]
check("T6c 目标消失 → 动作结清（不悬挂在承诺期）",
      h.am.current is None and _st6["started"] >= 1
      and (_st6["failed"] + _st6["success"] + _st6["cancelled"]) >= 1,
      str(h.am.status()))
h.cleanup()

# 6d. 失败留痕入图（原因可查，供反思/因果学习）
world = FakeWorld()
world.state["nearbyBlocks"] = [{"name": "iron_ore", "count": 2}]
world.find_positions = [{"x": 5.0, "y": 64.0, "z": 0.0}]
world.inventory_items = []
h = Harness(world)
r = h.loop.tick(now=1000.0)
h.run_until_settled(now=1010.0)
tl_events = list(h.am.timeline.events) if h.am.timeline else []
# 时间轴未注入 Harness（ActionManager.timeline=None）→ 通过图谱留痕验证
act_nodes = [nid for nid in h.kg.nodes
             if nid.startswith(("行动_", "自主行动_"))]
check("T6d 失败行动留痕入图（含原因，可供后续反思）",
      act_nodes and any(
          "tool_missing" in str(h.kg.nodes[n].extra_attrs.get("reason") or "")
          for n in act_nodes),
      str([(n, h.kg.nodes[n].extra_attrs.get("reason")) for n in act_nodes][-2:]))
h.cleanup()

# ═══════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════
print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 具身改造验收测试全过（6 场景离线仿真：真实认知-行动代码路径）")
