# test_skill_additions.py — 技能欠账补齐测试（耐久/床盾弓/高炉/箱子闭环）
# 离线假桥。运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_skill_additions.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging; logging.disable(logging.WARNING)
import minecraft.bridge as bridge
from skills.base import SkillContext
from skills.inventory import choose_best_tool, choose_weapon, best_pickaxe
from skills.crafting import resolve_recipe, missing_ingredients

FAILURES = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond: FAILURES.append(name)

# ── 耐久过滤 ──
class Ctx:
    def __init__(self, inv): self._inv = inv
    def inventory(self, refresh=False): return self._inv

half_worn = [{"name": "stone_pickaxe", "count": 1, "dur_percent": 5.0}]
tool, why = choose_best_tool(Ctx(half_worn), "iron_ore")
check("快断的石镐不再被选中（tool_worn_out）", tool is None and why == "tool_worn_out",
      f"{tool},{why}")
good = [{"name": "stone_pickaxe", "count": 1, "dur_percent": 60.0}]
tool2, why2 = choose_best_tool(Ctx(good), "iron_ore")
check("正常耐久石镐照常可用", tool2 == "stone_pickaxe", f"{tool2},{why2}")
legacy = [{"name": "iron_pickaxe", "count": 1}]     # 旧桥无 dur_percent
tool3, _ = choose_best_tool(Ctx(legacy), "gold_ore")
check("缺耐久字段按满耐久（旧桥兼容）", tool3 == "iron_pickaxe", str(tool3))
w = choose_weapon([{"name": "iron_sword", "count": 1, "dur_percent": 2.0},
                      {"name": "stone_sword", "count": 1, "dur_percent": 80.0}])
check("武器选择跳过快断的（退回石剑）", w == "stone_sword", str(w))

# ── 新配方 ──
for zh, en in (("床", "white_bed"), ("盾牌", "shield"), ("弓", "bow")):
    item, rec = resolve_recipe(zh)
    check(f"配方别名 {zh}→{en}", item == en and rec is not None, str(item))
_, rec = resolve_recipe("白床")
miss = missing_ingredients([{"name": "white_wool", "count": 3},
                            {"name": "oak_planks", "count": 3}], rec)
check("材料齐全时缺口为空（planks 组识别羊毛+木板）", miss == {}, str(miss))
miss2 = missing_ingredients([{"name": "oak_planks", "count": 1}], rec)
check("材料不足时列出缺口", "white_wool" in miss2 and "planks" in miss2, str(miss2))

# ── 高炉/烟熏炉：smelt 按 furnace→blast→smoker 顺序找 ──
calls = []
def fake_call(path, payload=None, timeout=3):
    calls.append((path, payload or {}))
    if path == "/find_blocks":
        if payload.get("block") == "furnace":
            return {"ok": True, "positions": []}
        if payload.get("block") == "blast_furnace":
            return {"ok": True, "positions": [{"x": 1, "y": 64, "z": 1}]}
        return {"ok": True, "positions": []}
    return {"ok": True}
bridge.call = fake_call
bridge.get_state = lambda: {"connected": True, "position": {"x": 0, "y": 64, "z": 0}}
bridge.inventory = lambda: {"ok": True, "items": [{"name": "iron_ore", "count": 2},
                                                  {"name": "coal", "count": 1}]}
from skills.crafting import SmeltItem
ctx = SkillContext(bridge=bridge)
r = SmeltItem().start(ctx, {"input": "iron_ore"})
found_blast = any(c[0] == "/find_blocks" and c[1].get("block") == "blast_furnace"
                  for c in calls)
check("普通熔炉没有 → 找高炉（blast_furnace）", found_blast, str(calls))

# ── 箱子闭环 ──
calls2 = []
def fake_call2(path, payload=None, timeout=3):
    calls2.append((path, payload or {}))
    return {"ok": True, "moved": 12}
bridge.call = fake_call2
bridge.chest_store = lambda keep=None, all_items=False: (
    calls2.append(("/chest_store", {"keep": keep, "all": all_items})),
    {"ok": True, "moved": 12})[1]
bridge.chest_take = lambda item, count=1: (
    calls2.append(("/chest_take", {"item": item, "count": count})), {"ok": True})[1]
from skills.base import get as get_skill
ctx2 = SkillContext(bridge=bridge)
r = get_skill("chest_store").start(ctx2, {})
check("chest_store 成功回报件数", r["ok"] and "12" in r["describe"], str(r))
r2 = get_skill("chest_take").start(ctx2, {"item": "铁锭", "count": 2})
check("chest_take 中文物品名可发起", any(c[0] == "/chest_take" for c in calls2), str(calls2))
r3 = get_skill("chest_store").start(ctx2, {})
bridge.chest_store = lambda keep=None, all_items=False: {"ok": False, "reason": "chest_not_found"}
r4 = get_skill("chest_store").start(ctx2, {})
check("没找到箱子如实失败（chest_not_found）",
      r4["ok"] is False and r4["reason"] == "chest_not_found", str(r4))

print()
if FAILURES: print("✗", len(FAILURES), FAILURES); sys.exit(1)
print("✓ 技能欠账补齐全过（耐久/床盾弓/高炉/箱子）")
