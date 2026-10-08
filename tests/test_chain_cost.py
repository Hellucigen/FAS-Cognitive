# test_chain_cost.py — world_prior._chain_cost 全链代价（2026-09-25 真机教训回归）
# 离线：桩桥数据（形状=bot.js /recipe_for 修复后真返回），不连 Minecraft。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_chain_cost.py
#
# 背景两案（2026-09-25 真机日志）：
#   ① 背包 acacia_log 攒到 15 个，next_step 仍回 gather（并列木板配方层对
#      背包视而不见，激活值说了算）→ 有木头不合成，全天乱转。
#   ② 挖圆石选的工具链绕经钻石镐→钻石→铁镐→铁锭(=目标本体,环)，单层
#      rank×10 看不见"取这颗钻石还要一整条工具链"。
# 回归点：
#   A. 背包感知：手上有哪种原木，并列配方就选哪种（代价差自然涌现）
#   B. 环罚：绕回链条已有节点的工具分支被杀，选浅而全的木镐/石镐
#   C. 深链不饿死：真实图形状（石镐→圆石→石头→木镐→木板→原木）仍有代价

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from graph_model import KnowledgeGraph
import config as _C
import mc_knowledge
import experiment_mode as xm
import world_prior as wp

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# 桩桥：26.1 真桥形状（/recipe_for 截断修复后：stick 13 条、crafting_table
# 12 条全量返回；石质工具三种石料并列；diamond_ore 需 iron_pickaxe）
RECIPES = {
    "iron_ingot": [{"result": "iron_ingot", "yield": 1, "needs_table": True,
                    "ingredients": {"iron_nugget": 9}}],
    "stone_pickaxe": [
        {"result": "stone_pickaxe", "yield": 1, "needs_table": True,
         "ingredients": {"cobblestone": 3, "stick": 2}},
        {"result": "stone_pickaxe", "yield": 1, "needs_table": True,
         "ingredients": {"cobbled_deepslate": 3, "stick": 2}}],
    "iron_pickaxe": [{"result": "iron_pickaxe", "yield": 1, "needs_table": True,
                      "ingredients": {"iron_ingot": 3, "stick": 2}}],
    "diamond_pickaxe": [{"result": "diamond_pickaxe", "yield": 1,
                         "needs_table": True,
                         "ingredients": {"diamond": 3, "stick": 2}}],
    "wooden_pickaxe": [{"result": "wooden_pickaxe", "yield": 1,
                        "needs_table": True,
                        "ingredients": {"oak_planks": 3, "stick": 2}}],
    "stick": [{"result": "stick", "yield": 4, "needs_table": False,
               "ingredients": {f"{s}_planks": 2}}
              for s in ("oak", "spruce", "birch", "jungle", "acacia",
                        "dark_oak", "mangrove", "cherry")],
}
for s in ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak",
          "mangrove", "cherry"):
    RECIPES[f"{s}_planks"] = [{"result": f"{s}_planks", "yield": 4,
                               "needs_table": False,
                               "ingredients": {f"{s}_log": 1}}]
RECIPES["crafting_table"] = [
    {"result": "crafting_table", "yield": 1, "needs_table": False,
     "ingredients": {"oak_planks": 4}}]
RECIPES["furnace"] = [
    {"result": "furnace", "yield": 1, "needs_table": True,
     "ingredients": {"cobblestone": 8}}]

BLOCKS = {
    "iron_ore": {"block": "iron_ore", "drops": ["raw_iron"],
                 "harvest_tools": ["stone_pickaxe", "iron_pickaxe",
                                   "diamond_pickaxe"]},
    "deepslate_iron_ore": {"block": "deepslate_iron_ore",
                           "drops": ["raw_iron"],
                           "harvest_tools": ["stone_pickaxe",
                                             "iron_pickaxe"]},
    "stone": {"block": "stone", "drops": ["cobblestone"],
              "harvest_tools": ["wooden_pickaxe"]},
    "cobblestone": {"block": "stone", "drops": ["cobblestone"],
                    "harvest_tools": ["wooden_pickaxe"]},
    "diamond_ore": {"block": "diamond_ore", "drops": ["diamond"],
                    "harvest_tools": ["iron_pickaxe", "diamond_pickaxe"]},
}
for s in ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak",
          "mangrove", "cherry"):
    BLOCKS[f"{s}_log"] = {"block": f"{s}_log", "drops": [f"{s}_log"],
                          "harvest_tools": []}


class FullBridge:
    def recipe_for(self, item):
        rs = RECIPES.get(str(item))
        if rs is None:
            return {"ok": False, "reason": "no_data"}
        return {"ok": True, "recipes": rs, "mc_version": "26.1"}

    def block_meta(self, name):
        m = BLOCKS.get(str(name))
        if m is None:
            return {"ok": False, "reason": "no_data"}
        return dict(m, ok=True, mc_version="26.1")


PRIOR = {"result": "iron_ingot", "kind": "smelt", "inputs": {"raw_iron": 1},
         "fuel": True, "tool": "furnace",
         "provenance": {"knowledge_type": "prior", "source": "manual:test",
                        "version_verified": False, "confidence": 0.4}}
CFG = {"experiment": {"mode": "learning_closed_loop", "goal_obtain": "iron_ingot",
                      "attention_floor": 0.9,
                      "shield": {k: True for k in (
                          "hormone_to_params", "graph_projection", "graph_pulse",
                          "drift", "modulation_events",
                          "internal_state_heartbeat", "world_events_to_needs",
                          "mood", "social_drive", "personality_tendency",
                          "cognition_llm", "idle_companion")},
                      "prior_extra": [PRIOR]}}

xm.configure(CFG)
wp.bind_config(CFG)
wp._RUNTIME["queried"].clear()
kg = KnowledgeGraph()
mc_knowledge.ensure_mc_world(kg, dict(_C.DEFAULT_CONFIG))
wp.build_recipe_closure(kg, None, "iron_ingot", FullBridge())

check("闭包含 oak/spruce 等全部木板配方（截断修复的 Python 侧同形验证）",
      bool(wp.producers_of(kg, "oak_planks"))
      and bool(wp.producers_of(kg, "jungle_planks"))
      and len(wp.producers_of(kg, "stick")) == 8,
      str(len(wp.producers_of(kg, "stick"))))

# ═══ A. 背包感知 ═══
c_oak = wp._chain_cost(kg, "oak_planks", {"oak_log": 5}, seen={"iron_ingot"})
c_birch = wp._chain_cost(kg, "birch_planks", {"oak_log": 5}, seen={"iron_ingot"})
check("手上的橡木：oak_planks 链价 1（背包直供）",
      c_oak == 1.0, str(c_oak))
check("没货的桦木：birch_planks 链价更高（要去野外采）",
      c_birch > c_oak, f"oak={c_oak} birch={c_birch}")
s = wp.next_step(kg, "stick", {"oak_log": 5})
check("背包有橡木 → 木棍的下一步是合成（不是去野外采木头）",
      s.get("action") == "craft" and s.get("item") == "oak_planks", str(s))
ch = s.get("chain") or []
check("链条穿过 oak_planks（背包感知主导并列配方选择）",
      "oak_planks" in ch and "cherry_planks" not in ch
      and "acacia_planks" not in ch, str(ch))
s2 = wp.next_step(kg, "iron_ingot", {"oak_log": 5})
check("目标铁锭 + 背包橡木 → 链条向前推进到合成木板（有木头不乱转）",
      s2.get("action") == "craft" and "planks" in str(s2.get("item")), str(s2))

# ═══ B. 环罚 ═══
ch3 = wp.next_step(kg, "iron_ingot", {}).get("chain") or []
check("空背包爬链不绕钻石（diamond_pickaxe/diamond 不进链）",
      "diamond_pickaxe" not in ch3 and "diamond" not in ch3, str(ch3))
d_wo = wp._chain_cost(kg, "diamond_pickaxe", {}, seen={"iron_ingot",
                                                       "raw_iron"})
d_w = wp._chain_cost(kg, "diamond_pickaxe", {}, seen=set())
check("diamond_pickaxe：链条里有 iron_ingot 时是死路（环），无链条时可爬",
      d_wo >= wp._CHAIN_DEAD > d_w, f"banned={d_wo} free={d_w}")
s4 = wp.next_step(kg, "iron_ingot", {"cobblestone": 3, "stick": 2,
                                     "oak_planks": 3})
ch4 = s4.get("chain") or []
check("原料在手时工具选择走石镐/木镐支路（不选钻石镐）",
      "diamond_pickaxe" not in ch4 and "copper_pickaxe" not in ch4, str(ch4))

# ═══ C. 深链不饿死 ═══
c_stone = wp._chain_cost(kg, "stone_pickaxe", {}, seen={"iron_ingot",
                                                        "raw_iron"})
check("真实深链（石镐→圆石→石头→木镐→木板→原木）有有限代价",
      c_stone < wp._CHAIN_DEAD, str(c_stone))
c_log = wp._chain_cost(kg, "oak_log", {}, seen=set())
check("无工具可采的原木链价 1（现场一跳）", c_log == 1.0, str(c_log))
c_tbl = wp._chain_cost(kg, "crafting_table", {"oak_planks": 4}, seen=set())
check("工作台：木板在手 → 链价 1（下一步就是合成工作台）",
      c_tbl == 1.0, str(c_tbl))
s5 = wp.next_step(kg, "crafting_table", {"oak_log": 8})
check("目标工作台 + 背包原木 → 先合成木板（工作台的下一步）",
      s5.get("action") == "craft" and s5.get("item") == "oak_planks", str(s5))
s6 = wp.next_step(kg, "crafting_table", {"oak_planks": 4})
check("木板齐 → 直接合成工作台", s6.get("action") == "craft"
      and s6.get("item") == "crafting_table", str(s6))

xm.configure({"experiment": {"mode": "off"}})

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未过：{FAILURES}")
    sys.exit(1)
print("PASS: 全链代价回归全部通过（离线桩桥）")
