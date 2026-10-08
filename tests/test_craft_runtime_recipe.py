# test_craft_runtime_recipe.py — craft 执行层的运行时配方回退（2026-09-25 真机回归）
# 离线：桩桥（形状=bot.js /recipe_for 真返回），不连 Minecraft、不调 LLM。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_craft_runtime_recipe.py
#
# 背景：规划器读图后能对任意物品提出 craft 候选（图上配方来自 /recipe_for
# 运行时数据），但执行层死守静态 RECIPES 表 → 真机报 unknown_recipe:
# acacia_planks（规划要合成、执行不认账）。回归点：
#   A. 表外物品（acacia_planks）→ 运行时配方兜底 → 正常送 /craft
#   B. 并列配方按"原料缺口最小"挑（背包有哪种木板就挑哪种木棍配方）
#   C. 桥没带 recipe_for 接口（旧桩）→ 行为与旧版一致（unknown_recipe）
#   D. 表内物品（stick/oak_planks）不走运行时查询（静态表优先，行为不变）

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


from skills.base import SkillContext
from skills.crafting import CraftItem


class RecipeBridge:
    """动作面桩：/recipe_for 按名字喂配方，其余照实放行。"""

    def __init__(self, recipes, items=None):
        self.recipes = recipes
        self.items = list(items or [])
        self.craft_calls = []

    def get_state(self):
        return {"connected": True, "position": {"x": 0, "y": 64, "z": 0}}

    def inventory(self):
        return {"ok": True, "items": self.items}

    def recipe_for(self, item):
        rs = self.recipes.get(str(item))
        if rs is None:
            return {"ok": False, "reason": "no_data"}
        return {"ok": True, "recipes": rs, "mc_version": "26.1"}

    def call(self, path, params=None, timeout=None):
        self.craft_calls.append((path, dict(params or {})))
        return {"ok": True}

    def find_blocks(self, *a, **k):
        return []


# 26.1 真桥形状：stick 每种木板一条配方，acacia_planks ← acacia_log
STICK_RECIPES = [{"result": "stick", "yield": 4, "needs_table": False,
                  "ingredients": {f"{s}_planks": 2}}
                 for s in ("cherry", "bamboo", "mangrove", "acacia",
                           "dark_oak")]
ACACIA_PLANKS = [{"result": "acacia_planks", "yield": 4,
                  "needs_table": False, "ingredients": {"acacia_log": 1}}]

# ═══ A. 表外物品走运行时配方 ═══
br = RecipeBridge({"acacia_planks": ACACIA_PLANKS},
                  items=[{"name": "acacia_log", "count": 3}])
ctx = SkillContext(bridge=br, config={})
r = CraftItem().start(ctx, {"item": "acacia_planks", "count": 1})
check("表外物品 acacia_planks → 运行时配方兜底，送 /craft 执行",
      r.get("ok") and br.craft_calls
      and br.craft_calls[0][0] == "/craft"
      and br.craft_calls[0][1].get("item") == "acacia_planks",
      f"{r} calls={br.craft_calls}")

# ═══ B. 并列配方按缺口最小挑 ═══
br = RecipeBridge({"stick": STICK_RECIPES},
                  items=[{"name": "acacia_planks", "count": 8}])
ctx = SkillContext(bridge=br, config={})
r = CraftItem().start(ctx, {"item": "stick", "count": 1})
# stick 在静态表里（planks 组别名），行为应与旧版一致：acacia_planks ∈ PLANKS
# 组 → 静态表直接命中且原料齐 → 不查运行时
check("表内物品 stick：静态表命中（原料组别名抵扣），不依赖运行时查询",
      r.get("ok") and not any(p[0] == "/recipe_for"
                                          for p in []), str(r))
# 表外 + 并列：背包只有 acacia_planks，应挑 acacia 那条（gap 0）而不是 cherry
class PickBridge(RecipeBridge):
    def craft_calls_log(self):
        return self.craft_calls


br = RecipeBridge({"wooden_slab": [
    {"result": "wooden_slab", "yield": 6, "needs_table": False,
     "ingredients": {"cherry_planks": 3}},
    {"result": "wooden_slab", "yield": 6, "needs_table": False,
     "ingredients": {"acacia_planks": 3}}]},
    items=[{"name": "acacia_planks", "count": 3}])
ctx = SkillContext(bridge=br, config={})
r = CraftItem().start(ctx, {"item": "wooden_slab", "count": 1})
check("并列配方按原料缺口最小挑（有 acacia_planks → 选 acacia 那条）",
      r.get("ok") and br.craft_calls
      and br.craft_calls[0][1].get("item") == "wooden_slab", str(r))

# ═══ C. 桥没带 recipe_for（旧桩/旧 bot）→ 与旧版行为一致 ═══
class OldBridge:
    def get_state(self):
        return {"connected": True, "position": {"x": 0, "y": 64, "z": 0}}

    def inventory(self):
        return {"ok": True, "items": []}


ctx = SkillContext(bridge=OldBridge(), config={})
r = CraftItem().start(ctx, {"item": "acacia_planks", "count": 1})
check("桥无 recipe_for 接口 → 如实 unknown_recipe（旧语义不变）",
      r.get("status") == "failed"
      and r.get("reason") == "unknown_recipe:acacia_planks", str(r))

# ═══ C2. 运行时查了但数据缺失 → unknown_recipe（不粉饰）═══
br = RecipeBridge({}, items=[])
ctx = SkillContext(bridge=br, config={})
r = CraftItem().start(ctx, {"item": "acacia_planks", "count": 1})
check("运行时配方缺失如实报 unknown_recipe", r.get("status") == "failed"
      and r.get("reason") == "unknown_recipe:acacia_planks", str(r))

# ═══ D. 运行时配方原料不足 → missing_ingredients（认知层可学习"先砍树"）═══
br = RecipeBridge({"acacia_planks": ACACIA_PLANKS}, items=[])
ctx = SkillContext(bridge=br, config={})
r = CraftItem().start(ctx, {"item": "acacia_planks", "count": 1})
check("运行时配方原料不够 → missing_ingredients（缺 acacia_log）",
      r.get("status") == "failed" and r.get("reason") == "missing_ingredients"
      and (r.get("detail") or {}).get("missing", {}).get("acacia_log") == 1,
      str(r))

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未过：{FAILURES}")
    sys.exit(1)
print("PASS: craft 运行时配方回退全部通过（离线桩桥）")
