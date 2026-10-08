# test_cognitive_complexity.py — 输入复杂度自适应 + 意图映射测试
# 覆盖：Level1/Level2 分类、L1 解析产物 → ActionNode、L2 多意图 → ActionNode、
# 反射解析 → ActionNode（同一条 Action 管路，无旁路）。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_cognitive_complexity.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from cognitive_complexity import estimate_complexity
from action_intents import l1_to_action, intention_to_action, reflex_to_action

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ── 1. 复杂度估计（Level 1：短/单意图）──────────────────────
for text in ("跟我走", "停下", "过来", "你看那里", "挖一下铁矿", "等等我",
             "这个是什么？", "跳一下"):
    cx = estimate_complexity(text)
    check(f"L1: 「{text}」→ 轻认知", cx["level"] == 1, str(cx["reasons"]))

# 1b. 真实运行日志回归（2026-09-20）：ASCII 词按词计不按字母计——
# "来玩minecraft吧，端口号是51027" 曾被按 22 字符误判 L2（4 次串行大模型），
# 会话状态机在解析前已完成连接，这类短输入就该走 L1。
for text in ("来玩minecraft吧，端口号是51027", "51027", "进我的世界Haru",
             "陪我玩mc吧"):
    cx = estimate_complexity(text)
    check(f"L1(实测日志): 「{text}」", cx["level"] == 1, str(cx["reasons"]))
check("语义单位度量对 ASCII 友好（minecraft+51027 只算 2 单位）",
      estimate_complexity("来玩minecraft吧，端口号是51027")["components"]["semantic_units"] <= 12,
      str(estimate_complexity("来玩minecraft吧，端口号是51027")["components"]))

# ── 2. 复杂度估计（Level 2：长/多意图/条件）─────────────────
for text in ("我们先去那个村庄看看，如果有铁的话就挖一点，然后晚上回来。",
             "刚才那个地方好像有个洞穴，你要不要跟我进去看看？不过你现在血量不高。",
             "跟着我走然后帮我挖点木头再回来",
             "去看看那边有没有煤矿，顺便看看有没有怪"):
    cx = estimate_complexity(text)
    check(f"L2: 「{text[:12]}…」→ 全认知", cx["level"] == 2, str(cx["reasons"][:2]))
    check(f"L2: 「{text[:12]}…」理由可解释", len(cx["reasons"]) > 0)

# ── 3. L1 解析产物 → ActionNode ─────────────────────────────
spec = l1_to_action({"intent": "follow_user", "target": "用户", "urgency": 0.9,
                     "needs_action": True}, "Hellucigen")
check("follow_user → follow_entity(用户名)",
      spec["action_type"] == "follow_entity"
      and spec["params"]["entity"] == "Hellucigen", str(spec))
spec = l1_to_action({"intent": "mine_block", "target": "铁矿", "count": 2,
                     "urgency": 0.8}, "Hellucigen")
check("mine_block(铁矿) → gather_resource(iron_ore, qty=2)",
      spec["action_type"] == "gather_resource"
      and spec["params"]["resource"] == "iron_ore"
      and spec["params"]["quantity"] == 2, str(spec))
spec = l1_to_action({"intent": "stop_action", "urgency": 0.95}, "Hellucigen")
check("stop_action → stop（高紧迫）",
      spec["action_type"] == "stop" and spec["urgency"] == 0.95, str(spec))
check("闲聊意图不产生动作",
      l1_to_action({"intent": "none"}, "Hellucigen") is None)
check("未知意图不产生动作（不猜）",
      l1_to_action({"intent": "dance_for_me"}, "Hellucigen") is None)

# ── 4. L2 意图分解 → ActionNode（队列目标）──────────────────
spec = intention_to_action({"type": "go_to", "target": "村庄", "params": {}})
check("go_to(村庄) → explore_area(village)（目标化探索，不是操作序列）",
      spec["action_type"] == "explore_area"
      and spec["params"]["goal"] == "village", str(spec))
spec = intention_to_action({"type": "return_home", "target": None, "params": {}})
check("return_home → navigate_home", spec["action_type"] == "navigate_home")
spec = intention_to_action({"type": "craft_item", "target": "木镐", "params": {}})
check("craft_item(木镐) → craft_item(wooden_pickaxe)",
      spec["action_type"] == "craft_item"
      and spec["params"]["item"] == "wooden_pickaxe", str(spec))
spec = intention_to_action({"type": "attack_entity", "target": "僵尸", "params": {}})
check("attack_entity(僵尸) → attack_entity(zombie)（中文翻译）",
      spec["action_type"] == "attack_entity"
      and spec["params"]["entity"] == "zombie", str(spec))
spec = intention_to_action({"type": "mine_block", "target": "铁",
                            "note": "如果路上看到铁", "params": {}})
check("条件句保留在 note（后续执行可参考）",
      spec["params"].get("note") == "如果路上看到铁", str(spec))
check("词表外意图被拒绝（不猜）",
      intention_to_action({"type": "fly_to_moon", "params": {}}) is None)

# ── 5. 反射解析 → ActionNode（零 LLM 快通道同管路）──────────
spec = reflex_to_action({"action": "follow", "params": {}}, "Hellucigen")
check("反射 follow → follow_entity", spec["action_type"] == "follow_entity")
spec = reflex_to_action({"action": "dig", "params": {"block": "铁矿",
                                                     "target": "铁矿"}}, "Hellucigen")
check("反射 dig(铁矿) → gather_resource(iron_ore)",
      spec["action_type"] == "gather_resource"
      and spec["params"]["resource"] == "iron_ore", str(spec))
spec = reflex_to_action({"action": "attack", "params": {"entity": "zombie"}},
                        "Hellucigen")
check("反射 attack → attack_entity", spec["action_type"] == "attack_entity")
check("反射 none → 无动作", reflex_to_action({"action": "none"}, "u") is None)

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 复杂度自适应 + 意图映射测试全过")
