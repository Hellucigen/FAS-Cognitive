# test_learning_experiment.py — 学习实验层（world_prior / obtain 通道 / 屏蔽）
# 离线：桩桥数据（形状=bot.js 真实返回），临时目录，不连 Minecraft、不调 LLM。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_learning_experiment.py
#
# 覆盖：
#   A. world_prior：闭包 BFS（运行时配方/方块数据 + 人工先验行 provenance）
#      与 next_step 跳链（done / smelt / gather(带工具) / 无工具→链到橡木原木）
#   B. autonomy obtain 通道：目标→gather 候选 / 目标达成销账 / smelt_pending
#      →furnace_take / 结算记账 / 实验关闭时恒空
#   C. §2 降级：_pick_explore_goal 泛化（不枚举矿石白名单）
#   D. §16 屏蔽：world_events_to_needs 需求写跳过（事件上图不拦）
#   E. §8 归因窗数据行（gather/craft/smelt 类动作窗宽，8s 默认不再错配）
#      + 端到端 3 次重复 → 因果假设成立
#   F. skills/exploration._check_goal 未知目标按方块名通用兜底

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from graph_model import KnowledgeGraph, Node
import experiment_mode as xm
import world_prior as wp
import experience as exp

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class StubBridge:
    """桥数据面桩：recipe_for / block_meta 的返回形状与 bot.js 一致。
    （故意不含 iron_ingot 的熔炼配方——26.1 运行时数据真缺这一条；
    但**有**铁粒/铁块两条合料配方，形状照抄 2026-09-25 真桥实测：那两条
    的原料在图上没有任何来源，会把"取 prods[0]"的爬链带进死路。）"""

    RECIPES = {
        "iron_ingot": [{"result": "iron_ingot", "yield": 1,
                        "needs_table": True,
                        "ingredients": {"iron_nugget": 9}},
                       {"result": "iron_ingot", "yield": 9,
                        "needs_table": False,
                        "ingredients": {"iron_block": 1}}],
        "stone_pickaxe": [{"result": "stone_pickaxe", "yield": 1,
                           "needs_table": True,
                           "ingredients": {"cobblestone": 3, "stick": 2}}],
        "copper_pickaxe": [{"result": "copper_pickaxe", "yield": 1,
                            "needs_table": True,
                            "ingredients": {"copper_ingot": 1, "stick": 2}}],
        "wooden_pickaxe": [{"result": "wooden_pickaxe", "yield": 1,
                            "ingredients": {"planks": 3, "stick": 2},
                            "needs_table": True}],
        "stick": [{"result": "stick", "yield": 4, "needs_table": False,
                   "ingredients": {"planks": 2}},
                  # 26.1 真桥实测形状：同一物品每种木板各一条配方
                  # （/recipe_for?item=stick 返回 cherry/bamboo/mangrove/…，
                  #  而 /recipe_for?item=planks 是 unknown_item —— 没有泛型 planks）
                  {"result": "stick", "yield": 4, "needs_table": False,
                   "ingredients": {"cherry_planks": 2}},
                  {"result": "stick", "yield": 4, "needs_table": False,
                   "ingredients": {"acacia_planks": 2}}],
        "cherry_planks": [{"result": "cherry_planks", "yield": 4,
                           "needs_table": False,
                           "ingredients": {"cherry_log": 1}}],
        "acacia_planks": [{"result": "acacia_planks", "yield": 4,
                           "needs_table": False,
                           "ingredients": {"acacia_log": 1}}],
        "planks": [{"result": "planks", "yield": 4, "needs_table": False,
                    "ingredients": {"oak_log": 1}},
                   # 26.1 真桥形状：统一 planks 配方每种原木一条（实测同屏有
                   # cherry_log/acacia_log 并列）。并列时选谁必须由证据决定。
                   {"result": "planks", "yield": 4, "needs_table": False,
                    "ingredients": {"spruce_log": 1}}],
        # 26.1 真桥里工作台/熔炉都有合成配方（熔炉=8 圆石），先验行的
        # "前置设备"因此可以被爬链自己造出来——闭包要把它们拉进来
        "crafting_table": [{"result": "crafting_table", "yield": 1,
                            "needs_table": False,
                            "ingredients": {"planks": 4}}],
        "furnace": [{"result": "furnace", "yield": 1, "needs_table": True,
                     "ingredients": {"cobblestone": 8}}],
    }
    BLOCKS = {
        "iron_ore": {"block": "iron_ore", "drops": ["raw_iron"],
                     "harvest_tools": ["copper_pickaxe", "stone_pickaxe",
                                       "iron_pickaxe", "diamond_pickaxe"]},
        "deepslate_iron_ore": {"block": "deepslate_iron_ore",
                               "drops": ["raw_iron"],
                               "harvest_tools": ["copper_pickaxe",
                                                 "stone_pickaxe"]},
        "stone": {"block": "stone", "drops": ["cobblestone"],
                  "harvest_tools": ["wooden_pickaxe"]},
        "cobblestone": {"block": "stone", "drops": ["cobblestone"],
                        "harvest_tools": ["wooden_pickaxe"]},
        "oak_log": {"block": "oak_log", "drops": ["oak_log"],
                    "harvest_tools": []},
        "spruce_log": {"block": "spruce_log", "drops": ["spruce_log"],
                       "harvest_tools": []},
    }

    def recipe_for(self, item):
        rs = self.RECIPES.get(str(item))
        if rs is None:
            return {"ok": False, "reason": "no_data"}
        return {"ok": True, "recipes": rs, "mc_version": "26.1",
                "mcd_version": "3.117"}

    def block_meta(self, name):
        m = self.BLOCKS.get(str(name))
        if m is None:
            return {"ok": False, "reason": "no_data"}
        return dict(m, ok=True, mc_version="26.1")


PRIOR = {"result": "iron_ingot", "kind": "smelt",
         "inputs": {"raw_iron": 1}, "fuel": True, "tool": "furnace",
         "provenance": {"knowledge_type": "prior", "source": "manual:test",
                        "version_verified": False, "confidence": 0.4}}

EXP_CFG = {"experiment": {
    "mode": "learning_closed_loop", "goal_obtain": "iron_ingot",
    "attention_floor": 0.9,
    "shield": {k: True for k in (
        "hormone_to_params", "graph_projection", "graph_pulse", "drift",
        "modulation_events", "internal_state_heartbeat",
        "world_events_to_needs", "mood", "social_drive",
        "personality_tendency", "cognition_llm", "idle_companion")},
    "prior_extra": [PRIOR]}}


def reset_runtime_cache():
    wp._RUNTIME["queried"].clear()   # TTL 缓存跨用例隔离


# ═══ A. world_prior：闭包 + 跳链 ═══
import config as _C
import mc_knowledge
xm.configure(EXP_CFG)
wp.bind_config(EXP_CFG)
reset_runtime_cache()
kg = KnowledgeGraph()
# 生产同形：图谱先有 mc_world 种子（闭包负责核验种子边，不是从零发现方块）
mc_knowledge.ensure_mc_world(kg, dict(_C.DEFAULT_CONFIG))
st = wp.build_recipe_closure(kg, None, "iron_ingot", StubBridge())
check("闭包统计：配方/方块/人工先验都>0",
      st["recipes"] >= 4 and st["blocks"] >= 3 and st["manual"] == 1, str(st))
check("闭包版本记录（运行时协商版本）", st["mc_version"] == "26.1", str(st))
check("目标物品节点存在", "物品:iron_ingot" in kg.nodes)
man = kg.get_node("配方:iron_ingot:smelt")
check("人工先验熔炼行入图且 provenance 如实标注未核验",
      man is not None
      and (man.extra_attrs or {}).get("provenance", {}).get(
          "version_verified") is False
      and (man.extra_attrs or {}).get("provenance", {}).get(
          "source") == "manual:test",
      str(man and man.extra_attrs))
drop_e = kg.get_edge("iron_ore", "物品:raw_iron", "掉落")
tool_e = kg.get_edge("iron_ore", "物品:stone_pickaxe", "需要工具")
check("运行时掉落/工具边来自 block_meta（version_verified=True）",
      drop_e is not None and tool_e is not None
      and (drop_e.extra_attrs or {}).get("provenance", {}).get(
          "version_verified") is True,
      str(drop_e and drop_e.extra_attrs))
cnt_e = kg.get_edge("配方:stone_pickaxe:table:0", "物品:stick", "需要")
check("配方原料数量写在边 extra_attrs.count（3 圆石/2 木棍不失真）",
      cnt_e is not None and int((cnt_e.extra_attrs or {}).get("count")
                                or 0) == 2, str(cnt_e and cnt_e.extra_attrs))

# next_step 各跳
check("背包已有目标 → done",
      wp.next_step(kg, "iron_ingot", {"iron_ingot": 1}).get("done") is True)
s = wp.next_step(kg, "iron_ingot", {"raw_iron": 1})
check("有 raw_iron → smelt（kind 决定动作形状，不是分支表）",
      s.get("action") == "smelt" and s.get("item") == "iron_ingot"
      and s.get("tool") == "furnace", str(s))
s = wp.next_step(kg, "iron_ingot", {"stone_pickaxe": 1})
check("有工具无原料 → gather 铁矿取 raw_iron（深层/浅层皆可，边序不钉死）",
      s.get("action") == "gather"
      and s.get("block") in ("iron_ore", "deepslate_iron_ore")
      and s.get("item") == "raw_iron", str(s))
s = wp.next_step(kg, "iron_ingot", {})
check("空背包 → 链条沿图爬到最前一步（无工具可得的 gather，零攻略分支）",
      s.get("action") == "gather" and s.get("block")
      and not wp.tool_required(kg, s["block"])
      and "stone_pickaxe" in (s.get("chain") or []), str(s))
s2 = wp.next_step(kg, "iron_ingot", {"oak_log": 6})
check("给一摞原木 → 下一步自然前进（不是原地 explore）",
      s2.get("action") in ("craft", "gather") and s2.get("block") != "oak_log",
      str(s2))
# 证据降级：某方块近期反复"半径内找不到"→ 并列配方换物种（26.1 真机实测
# 链条死盯 cherry_log 一小时，20 次同因失败也不换），但绝不成死路。
sx = wp.next_step(kg, "iron_ingot", {}, exclude={"oak_log"})
check("exclude 让链条换到并列的另一物种（spruce_log）",
      sx.get("action") == "gather" and sx.get("block") == "spruce_log",
      str(sx))
sa = wp.next_step(kg, "iron_ingot", {}, exclude={"oak_log", "spruce_log"})
check("全部并列来源都被排除 → 链条仍前进（换物种或转定向探索，绝不做成死路）",
      sa.get("done") is False
      and sa.get("action") in ("gather", "craft", "explore"), str(sa))
# 26.1 真形状（实测 /recipe_for?item=planks = unknown_item，没有泛型 planks；
# stick 每种 *_planks 一条配方，*_planks 由对应 *_log 合成）：降级必须顺着
# "木板←原木"这条配方往上传，否则排除 cherry_log 后仍会绕回 cherry_planks。
sp = wp.next_step(kg, "stick", {}, exclude={"cherry_log"})
check("排除原木 → 用它做的木板配方一起降级（证据顺图传播，零物种名）",
      "cherry_planks" not in (sp.get("chain") or [])
      and sp.get("block") != "cherry_log", str(sp))
sa2 = wp.next_step(kg, "cherry_planks", {}, exclude={"cherry_log"})
check("单一种类被排除且无备选 → 仍给出可执行的一步（不成死路）",
      sa2.get("action") in ("gather", "craft", "explore"), str(sa2))
# 真桥 26.1 实测形状：iron_ingot 有"9 铁粒"/"1 铁块"两条合料配方，原料在图上
# 没有任何来源；爬链若按 prods[0] 走就死在那里。证据排序必须把它压下去。
ch = wp.next_step(kg, "iron_ingot", {}).get("chain") or []
check("证据排序：不被『铁粒/铁块』死路配方带走（链上无 nugget/block）",
      "iron_nugget" not in ch and "iron_block" not in ch, str(ch))
# 工具选择同理：铜镐的原料（铜锭）图上无来源，石镐有（圆石+木棍）
s3 = wp.next_step(kg, "iron_ingot", {"cobblestone": 3, "stick": 2})
check("工具选择按图证据（选石镐所需路径，不选断头的铜镐路径）",
      "copper_pickaxe" not in (s3.get("chain") or []), str(s3))
# 熔炉是先验行里的"前置设备"（不建 需要 边，只进闭包）：它自己也该能爬
sf = wp.next_step(kg, "furnace", {})
check("前置设备（熔炉）也进闭包：空背包可爬到无工具可采的方块",
      sf.get("action") == "gather" and sf.get("block"), str(sf))
check("采集目标必须在『可采资源』分类内（分类外的方块执行层必拒）",
      bool(sf.get("block")) and wp._gatherable_block(kg, sf["block"]),
      str(sf))

# ═══ B. autonomy obtain 通道 ═══
from autonomy import AutonomousLoop
from capability_graph import CapabilityIndex
from action_concepts import ensure_action_concepts
from cognitive_regulation import CognitiveRegulation
import config as _C


class StubEmb:
    name = "stub_env"

    def __init__(self):
        self.capabilities_set = {"gather_resource", "craft_item", "smelt_item",
                                 "furnace_take", "place_block", "explore_area",
                                 "explore_direction", "walk_to", "stop",
                                 "inspect_area", "communicate"}
        self.percept = {}
        self.executed = []

    def available(self):
        return True

    def capabilities(self):
        return set(self.capabilities_set)

    def perceive(self):
        return dict(self.percept)

    def execute(self, a):
        self.executed.append(dict(a))
        return {"success": True, "action": a.get("action_type"), "result": {}}

    def poll_action(self):
        return {"status": "done"}

    def cancel(self):
        return {"ok": True}


def build_loop():
    base = os.path.join(tempfile.gettempdir(), "fas_test_learning_exp")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    lkg = KnowledgeGraph()
    for nid in ("用户", "Haru的位置", "Haru的血量", "Haru的手持物"):
        lkg.add_node(Node(id=nid, activation=0.0))
    # 生产同形：主图谱启动即有 mc_world 种子（obtain 通道的爬链读的就是这些边）
    mc_knowledge.ensure_mc_world(lkg, dict(_C.DEFAULT_CONFIG))
    loop = AutonomousLoop(kg=lkg, engine=None,
                          config={"autonomy": {"mode_default": "off"}},
                          regulation=CognitiveRegulation(kg=lkg, data_dir=base),
                          data_dir=base, note_outcome_fn=lambda neg: None)
    emb = StubEmb()
    loop.register_embodiment(emb)
    ensure_action_concepts(lkg, None)
    ci = CapabilityIndex(lkg, None, dict(_C.DEFAULT_CONFIG))
    ci.embodiment = emb
    ci.ensure_graph()
    loop.cap_index = ci
    return lkg, loop, base


lkg, loop, lbase = build_loop()
# 先验闭包进这张图（生产里由 _obtain_candidates 首拍重建；这里用桩桥预建，
# 并把重建节流推到未来，避免真桥不可达时的时间浪费）
wp._RUNTIME["queried"].clear()
wp.build_recipe_closure(lkg, None, "iron_ingot", StubBridge())
now = time.time()
loop._closure_ts = {"iron_ingot": now + 1e6}
loop.add_goal({"type": "obtain", "target": "iron_ingot",
               "source": "experiment_obtain", "text": "实验目标"})
percept = {"connected": True, "health": 20, "food": 20,
           "position": {"x": 0, "y": 64, "z": 0},
           "players": [], "entities": [], "blocks": [],
           "unknown_entities": [], "unknown_blocks": [],
           "inventory_items": [{"name": "stone_pickaxe", "count": 1}]}
cands = loop._obtain_candidates(percept, now)
gather = [c for c in cands if c["action_type"] == "gather_resource"]
check("obtain 通道产出去向明确的 gather 候选（图上下一步）",
      len(gather) == 1 and gather[0]["params"]["resource"]
      in ("iron_ore", "deepslate_iron_ore"),
      str([(c['action_type'], c.get('target')) for c in cands]))
check("候选带目标理由（物品:iron_ingot 进 reason，供评分注意分量读取）",
      gather and any("物品:iron_ingot" in str(r)
                     for r in gather[0]["reason"]), str(gather))
check("§9 目标节点托在注意力地板",
      float(lkg.nodes["物品:iron_ingot"].activation or 0) >= 0.9,
      str(lkg.nodes["物品:iron_ingot"].activation))

# §4 定向探索：目标已知而 gather 刚报"半径内找不到"→ 空转重试不算探索
blk0 = gather[0]["params"]["resource"]
loop._on_action_settled(
    {"action_type": "gather_resource", "target": blk0, "params": {},
     "motivation": "user_goal"},
    {"reason": "not_found_in_radius"}, False, now=time.time())
cands_e = loop._obtain_candidates(percept, time.time())
expl = [c for c in cands_e if c["action_type"] == "explore_area"]
check("§4 gather 刚失败 → 同目标转定向探索（goal=该方块 + 方向轮换）",
      len(expl) == 1 and expl[0]["params"]["goal"] == blk0
      and expl[0]["params"].get("direction"), str(cands_e))
cands_b = loop._obtain_candidates(
    dict(percept, blocks=[{"name": blk0, "count": 2}]), time.time())
check("探索到手（近身感知里已有该方块）→ 立刻回 gather，不等窗口过期",
      any(c["action_type"] == "gather_resource" for c in cands_b)
      and not [c for c in cands_b if c["action_type"] == "explore_area"],
      str(cands_b))

# 反复找不到（≥3 次）：换物种，而不是"探索→回来→再撞同一堵墙"打转
# （2026-09-25 真机 14:24–14:43 实测：同因失败 20 次仍在追同一种原木）
for _i in range(2):
    loop._on_action_settled(
        {"action_type": "gather_resource", "target": blk0, "params": {},
         "motivation": "user_goal"},
        {"reason": "not_found_in_radius"}, False, now=time.time())
check("_locally_absent：≥3 次同因失败 → 进降级集（判据是失败回执，不是攻略）",
      blk0 in loop._locally_absent(time.time()),
      str(loop._locally_absent(time.time())))
cands_f = loop._obtain_candidates(percept, time.time())
g2 = [c for c in cands_f if c["action_type"] == "gather_resource"]
check("降级集生效：采集目标换到另一种来源（不再死盯同一种空转）",
      bool(g2) and g2[0]["params"]["resource"] != blk0, str(cands_f))
loop._on_action_settled(
    {"action_type": "gather_resource", "target": blk0, "params": {},
     "motivation": "user_goal"},
    {"reason": None}, True, now=time.time())
check("一次成功即从降级集销账（证据过期机制，不是永久封禁）",
      blk0 not in loop._locally_absent(time.time()),
      str(loop._attempts.get("gather_resource@" + blk0)))

# 世界拦截（夜里/危险）：暂态不进因果账，但也不能 12 秒一次反复撞墙
# （2026-09-25 13:32 真机实测到这条空转，[RESULT] preempted:night 连刷）
_t0 = time.time()
_exp_cand = {"action_type": "explore_area", "target": blk0, "params": {},
             "motivation": "user_goal"}
loop._on_action_settled(_exp_cand, {"reason": "preempted:night"}, False,
                        now=_t0)
_k = loop._attempt_key("explore_area", blk0)
check("暂态拦截不写 _attempts（因果先验/退避账不被一个夜晚污染）",
      _k not in loop._attempts, str(list(loop._attempts)))
check("暂态拦截 → 节拍闸门开启（下一拍不再重提同一族动作）",
      loop._preempt_pause(_exp_cand, _t0 + 1.0) > 0.0,
      str(loop._preempt.get(_k)))
loop._on_action_settled(_exp_cand, {"reason": "preempted:night"}, False,
                        now=_t0 + 60)
_p2 = loop._preempt_pause(_exp_cand, _t0 + 61)
check("同类拦截第二次 → 停顿指数变长（45s 起 ×2）",
      _p2 > float(loop.cfg.get("preempt_pause_s", 45)),
      f"剩余={_p2:.0f}s 账={loop._preempt.get(_k)}")
check("停顿到期即放行（天亮下一拍就能动，不留永久封禁）",
      loop._preempt_pause(_exp_cand, _t0 + 61 + _p2 + 1) == 0.0)
loop._on_action_settled(_exp_cand, {"reason": None}, True, now=_t0 + 300)
check("一次成功 → 节拍闸门清零", _k not in loop._preempt,
      str(loop._preempt))
loop._attempts.pop(loop._attempt_key("gather_resource", blk0), None)
loop._recent_by_key.pop(loop._attempt_key("gather_resource", blk0), None)
loop._preempt.clear()
loop._recent_by_key.pop(_k, None)
loop._attempts.pop(_k, None)

# ⑬ night_preempt 屏蔽：目标导向探索夜里照搜（危险拦截不在此列），
#    实验关闭即回到既有安全语义
_probe = loop._explore_candidates(lambda a, t, p, e: p, blk0, "x")[0]
check("shield(night_preempt)：定向探索候选 night_stop=False",
      _probe.get("night_stop") is False, str(_probe))
xm.configure({"experiment": {"mode": "off"}})
_probe2 = loop._explore_candidates(lambda a, t, p, e: p, blk0, "x")[0]
check("实验关闭：候选回到 night_stop=True（正常行为的安全语义一字未动）",
      _probe2.get("night_stop") is True, str(_probe2))
xm.configure(EXP_CFG)

# 熔炼在途 → furnace_take 优先
g = loop._goals[0]
g["smelt_pending"] = {"item": "raw_iron", "ts": time.time()}
cands = loop._obtain_candidates(percept, g["smelt_pending"]["ts"])
check("smelt_pending 未清 → 唯一候选是取出炉货（成功≠拿到产物）",
      len(cands) == 1 and cands[0]["action_type"] == "furnace_take", str(cands))
del g["smelt_pending"]

# 结算记账：smelt_item 成功挂账，furnace_take 清账
loop._on_action_settled({"action_type": "smelt_item", "params": {"item": "raw_iron"},
                         "target": "iron_ingot", "motivation": "user_goal"},
                        {"reason": None}, True, now=time.time())
check("smelt_item 成功 → 目标挂 smelt_pending（且实验目标不被 B4 误销）",
      (loop._goals[0].get("smelt_pending") or {}).get("item") == "raw_iron"
      and len(loop._goals) == 1, str(loop._goals))
loop._on_action_settled({"action_type": "furnace_take", "params": {},
                         "target": "iron_ingot", "motivation": "user_goal"},
                        {"reason": None}, True, now=time.time())
check("furnace_take 结算 → 清账", "smelt_pending" not in loop._goals[0])

# 目标达成销账：背包真有 iron_ingot
percept_done = dict(percept, inventory_items=[{"name": "iron_ingot", "count": 1}])
cands = loop._obtain_candidates(percept_done, time.time())
check("背包已有目标 → 销账且不再产候选",
      not cands and not loop._obtain_goals(), str(loop._goals))

# 实验关闭 → 通道恒空 + 屏蔽恒 False
xm.configure({"experiment": {"mode": "off", "goal_obtain": "iron_ingot"}})
loop.add_goal({"type": "obtain", "target": "iron_ingot",
               "source": "experiment_obtain", "text": "x"})
check("mode=off：shield 全部失效（正常行为零污染）",
      xm.shield("mood") is False and xm.shield("cognition_llm") is False)
xm.configure(EXP_CFG)   # 恢复实验开

# ═══ C. _pick_explore_goal 泛化 ═══
check("探索目标泛化：deepslate_iron_ore 原样成目标词（不再枚举白名单）",
      loop._pick_explore_goal([{"type": "resource_detected",
                                "resource": "deepslate_iron_ore"}])
      == "deepslate_iron_ore")
check("无资源事件 → any",
      loop._pick_explore_goal([]) == "any")

# ═══ D. world_events_to_needs 屏蔽 ═══
class FakeIS:
    def __init__(self):
        self.deltas = []

    def apply_delta(self, kind, name, delta, reason=""):
        self.deltas.append((name, delta))


fis = FakeIS()
loop.internal_state = fis
loop._apply_event_effects([{"type": "hostile_detected", "entity": "zombie",
                            "dist": 3}])
check("shield(world_events_to_needs)：事件效应不写需求", fis.deltas == [],
      str(fis.deltas))
xm.configure({"experiment": {"mode": "off"}})
loop._apply_event_effects([{"type": "hostile_detected", "entity": "zombie",
                            "dist": 3}])
check("实验关闭：事件→需求写恢复（安全通道不被实验永久拆除）",
      any(n == "safety" for n, _ in fis.deltas), str(fis.deltas))
xm.configure(EXP_CFG)

# ═══ E. 归因窗 + 端到端 3 次重复成假设 ═══
check("gather 类动作归因窗 90s（MIN_SCORE 下 dt≤60s 可归因）",
      exp.CausalLearner.window_for(
          {"event_type": "ACTION", "subject": "gather_resource"}) == 90.0)
check("smelt/furnace_take 归因窗 120s",
      exp.CausalLearner.window_for({"event_type": "ACTION",
                                    "subject": "smelt_item"}) == 120.0
      and exp.CausalLearner.window_for(
          {"event_type": "ACTION", "subject": "furnace_take"}) == 120.0)
check("未列动作仍走 8s 默认窗（数据表没吞掉保守性）",
      exp.CausalLearner.window_for(
          {"event_type": "ACTION", "subject": "dig"}) == exp.ASSOC_WINDOW_S)

tbase = os.path.join(tempfile.gettempdir(), "fas_test_learning_causal")
shutil.rmtree(tbase, ignore_errors=True)
os.makedirs(tbase, exist_ok=True)
tl = exp.ExperienceTimeline(path=os.path.join(tbase, "timeline.json"))
cl = exp.CausalLearner(tl)
T0 = time.time()
for i in range(3):
    t0 = T0 + i * 1000.0
    ae = exp.make_event(exp.EVENT_ACTION, "self", "gather_resource",
                        {"target": "raw_iron", "intent": "gather_resource",
                         "result": "success"}, source="test")
    ae["ts"] = t0
    tl.append(ae)
    cl.record_action(ae)
    oe = exp.make_event(exp.EVENT_OBSERVATION, "self", "inventory:raw_iron",
                        {"change": "count_increased", "target": "raw_iron",
                         "before": i, "after": i + 1}, source="test")
    oe["ts"] = t0 + 30.0
    tl.append(oe)
cl.sweep(now=T0 + 3000.0 + 200.0)
hyp = [h for h in cl._hypotheses.values()
       if "gather_resource(raw_iron)" in h["action"]
       and "inventory:raw_iron" in h["outcome"]]
check("§8 核心闭环：动作→世界增量→结果，3 次重复成因果假设（零 LLM）",
      len(hyp) == 1 and hyp[0]["support"] >= 3, str(cl._hypotheses))
# 反例：连续两次窗口空 → confidence 掉
for i in range(2):
    t0 = T0 + 4000.0 + i * 1000.0
    ae = exp.make_event(exp.EVENT_ACTION, "self", "gather_resource",
                        {"target": "raw_iron", "intent": "gather_resource",
                         "result": "success"}, source="test")
    ae["ts"] = t0
    tl.append(ae)
    cl.record_action(ae)
    oe = exp.make_event(exp.EVENT_OBSERVATION, "self", "inventory:dirt",
                        {"change": "count_increased", "target": "dirt"},
                        source="test")
    oe["ts"] = t0 + 20.0
    tl.append(oe)
cl.sweep(now=T0 + 7000.0)
h = cl._hypotheses.get("gather_resource(raw_iron)|obs:self:inventory:raw_iron:count_increased")
check("反例累积 → 置信度下降（重复抬、反例压，同一把尺）",
      h is not None and h["confidence"] < 0.6, str(h))

# ═══ F. exploration 通用兜底 ═══
import skills.exploration as ex


class _Ctx:
    def __init__(self, goal):
        self.session = {"goal": goal}

    def state(self):
        return {}


_orig = ex._find_blocks
ex._find_blocks = lambda ctx, name, r, c: (
    [{"x": 1.0, "y": 60.0, "z": 2.0}] if name == "deepslate_iron_ore" else [])
hit = ex._ExplorationBase._check_goal(None, _Ctx("deepslate_iron_ore"))
miss = ex._ExplorationBase._check_goal(None, _Ctx("any"))
named = ex._ExplorationBase._check_goal(None, _Ctx("iron"))   # 走表：iron→iron_ore
ex._find_blocks = _orig
check("未知目标按方块名通用兜底（deepslate_iron_ore 能找到）",
      hit.get("block") == "deepslate_iron_ore", str(hit))
check("any/空目标不搜（保持原语义）", miss == {}, str(miss))

# ═══ G. 第一条真实闭环的两处断点（26.1 真机 2026-09-25）═══
# ① 死盯物种：链在"配方层"就选定 cherry_planks → 叶子只探 cherry_log，
#    20 次 not_found_in_radius 不换手，而 24 格内真有大树。修法是图上并列
#    等价来源（_leaf_siblings）交给 gather 的 variants 现查 —— "哪种在场"
#    由世界回答，代码里依旧零物种名。
# ② 诚实失败：探测接口自己失败（unknown_block）不能粉饰成"此地没有"，
#    否则缺席降级账与因果先验都被喂假证据。
import autonomy as _au
import skills.gathering as sg
from skills.base import SkillContext


class FindBridge:
    """动作面桩：只喂 find_blocks 的真/假回执，其余接口一律放行。"""

    def __init__(self, by_name):
        self.by_name, self.calls = by_name, []

    def get_state(self):
        return {"connected": True, "position": {"x": 0, "y": 64, "z": 0}}

    def inventory(self):
        return {"ok": True, "items": []}

    def call(self, path, params=None, timeout=None):
        self.calls.append((path, dict(params or {})))
        if path == "/find_blocks":
            r = self.by_name.get(str((params or {}).get("block")))
            return r if r is not None else {"ok": True, "positions": []}
        return {"ok": True}

    def get_action_result(self):
        return {"status": "idle"}

    def stop_goto(self):
        return {"ok": True}

    def goto_coords(self, *a):
        return {"ok": True}

    def equip(self, item):
        return {"ok": True}


sl = wp.next_step(kg, "stick", {})
check("gather 步携带图上并列等价来源 blocks（stick 的多条并板配方物种）",
      sl.get("action") == "gather"
      and {"oak_log", "spruce_log"} <= set(sl.get("blocks") or [])
      and sl.get("block") in (sl.get("blocks") or []), str(sl))

loop._attempts.clear()   # G 节只管参数形状，与前节的失败账隔离
cc = loop._goal_step_candidates(
    {"type": "obtain", "target": "iron_ingot"}, "iron_ingot",
    {"action": "gather", "item": "stick", "block": "oak_log",
     "blocks": ["oak_log", "spruce_log"], "why": "测试", "chain": []},
    {}, set(), time.time())
check("认知层把 blocks 原样带进 gather 参数的 variants（不增删物种名）",
      bool(cc) and (cc[0].get("params") or {}).get("variants")
      == ["oak_log", "spruce_log"], str(cc and cc[0]))
cc1 = loop._goal_step_candidates(
    {"type": "obtain", "target": "iron_ingot"}, "iron_ingot",
    {"action": "gather", "item": "dirt", "block": "dirt",
     "blocks": ["dirt"], "why": "测试", "chain": []},
    {}, set(), time.time())
check("单一来源不传 variants（旧路径行为不变）",
      "variants" not in ((cc1[0].get("params") or {}) if cc1 else {}),
      str(cc1 and cc1[0]))

fb1 = FindBridge({"cherry_log": {"ok": True, "positions": []},
                  "acacia_log": {"ok": True,
                                 "positions": [{"x": 8, "y": 67, "z": 0}]}})
c1 = SkillContext(bridge=fb1, config={})
r1 = sg.GatherResource().start(
    c1, {"resource": "cherry_log", "quantity": 1, "search_radius": 16,
         "variants": ["cherry_log", "acacia_log", "diamond_block"]})
check("等价物种现查：标签方块不在场 → 具体化为眼前真在场的那种（走向它）",
      r1.get("status") == "pending"
      and c1.session.get("resource") == "acacia_log", str(r1))
check("variants 先过采集白名单（图上并列的不可采材质不进探测）",
      "diamond_block" not in (c1.session.get("variants") or []),
      str(c1.session.get("variants")))

fb2 = FindBridge({"dirt": {"ok": False, "reason": "unknown_block"}})
c2 = SkillContext(bridge=fb2, config={})
r2 = sg.GatherResource().start(c2, {"resource": "dirt", "quantity": 1})
check("探测接口失败如实报 find_query_failed（不伪装成\"半径内没找到\"）",
      r2.get("status") == "failed"
      and r2.get("reason") == "find_query_failed", str(r2))
check("find_query_failed 属暂态理由：不进 absent 降级账、不进因果先验",
      "find_query_failed" in _au._TRANSIENT_WORLD_REASONS)

# 真机链条形状（2026-09-25 exp11）：叶子在 chain 里连着出现两次
# （…→cherry_planks→cherry_log→cherry_log），固定取 chain[-2] 会取到叶子自己，
# 并列物种展开恒为空。取"其展开集包含 bare 的最近祖先"才对形状不敏感。
_dup = wp._leaf_siblings(kg, ["stick", "planks", "oak_log", "oak_log"],
                         "oak_log")
check("chain 尾部重复不影响并列层展开（找含 bare 的最近祖先，不取 chain[-2]）",
      _dup[0] == "oak_log" and "spruce_log" in _dup, str(_dup))

check("暂态理由集覆盖 raw_gait_stalled（原始步态撞地形，与 path_stall 同类）",
      "raw_gait_stalled" in _au._TRANSIENT_WORLD_REASONS)
check("奖赏层同口径：raw_gait_stalled 判 blocked，不记满额负奖赏",
      __import__("reward").classify_self_outcome(
          "gather_resource", False, {"reason": "raw_gait_stalled"}) == "blocked")
# §四D 判据修正（2026-09-25 真机假阳性：徒手挖 deepslate 掉 0 件仍报成功）
check("暂态理由集覆盖 dug_no_item（挖开但背包零增益=环境没给货）",
      "dug_no_item" in _au._TRANSIENT_WORLD_REASONS)
check("奖赏层同口径：dug_no_item 判 blocked（掉没掉是游戏物理说了算）",
      __import__("reward").classify_self_outcome(
          "gather_resource", False, {"reason": "dug_no_item"}) == "blocked")

fb3 = FindBridge({"cherry_log": {"ok": False, "reason": "unknown_block"},
                  "acacia_log": {"ok": True, "positions": []}})
c3 = SkillContext(bridge=fb3, config={})
r3 = sg.GatherResource().start(
    c3, {"resource": "cherry_log", "quantity": 1,
         "variants": ["cherry_log", "acacia_log"]})
check("一条查挂+一条真没有 → 仍按世界的证据判 not_found_in_radius",
      r3.get("reason") == "not_found_in_radius", str(r3))

shutil.rmtree(lbase, ignore_errors=True)
shutil.rmtree(tbase, ignore_errors=True)
# 收尾把实验模式关掉：xm 是进程级全局，留着会让同进程里后续 suite 的
# BudgetManager/屏蔽判定被污染（pytest 批量收集时实测踩过）。
xm.configure({"experiment": {"mode": "off"}})

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未过：{FAILURES}")
    sys.exit(1)
print("PASS: 学习实验层全部通过（离线桩桥，零 LLM，零真实连接）")
