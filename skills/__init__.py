# skills/ — FAS Minecraft 具身技能库（Skill Library）
# ============================================================================
# 定位（具身技能规范 §十）：
#   认知系统决定"做不做"，Skill 系统决定"怎么做"。
#   技能是 FAS 的**具身能力**，不是行为脚本，更不是自主行为列表——
#   自主/交流/指令层产生 ActionNode（action_system.py），ActionNode 携带
#   action_type 落到这里；技能自己处理短时程的实时控制（挖掘节奏、战斗挥剑、
#   寻路卡住恢复），LLM 不参与任何一次技能执行。
#
# 约定：
#   1. 感知优先用 Mineflayer 状态（bot /state /inventory /find_blocks），
#      不为简单感知调 LLM。
#   2. 长动作（寻路/挖 N 块/战斗/探索）是 **pending 技能**：start() 启动，
#      poll() 每次推进一小步（状态机），绝不阻塞调用线程。
#   3. 失败必须真实返回（reason 语义化：tool_missing / not_found_in_radius /
#      no_path / timeout …），认知层据此学习（经验时间轴 + 因果假设）。
#   4. 工具选择在技能层完成（choose_best_tool）——认知层不需要知道
#      "挖铁矿要石镐"。
# ============================================================================

# 导入即注册：所有技能模块在此加载（import 顺序无关，registry 收口）。
from skills import base                     # noqa: F401
from skills import movement                 # noqa: F401
from skills import observation              # noqa: F401
from skills import inventory                # noqa: F401
from skills import blocks                   # noqa: F401
from skills import gathering                # noqa: F401
from skills import crafting                 # noqa: F401
from skills import survival                 # noqa: F401
from skills import combat                   # noqa: F401
from skills import building                 # noqa: F401
from skills import farming                  # noqa: F401
from skills import animals                  # noqa: F401
from skills import exploration              # noqa: F401

from skills.base import (run, poll, cancel, get, has, all_skills,
                         SkillContext, LocationMemory)  # noqa: F401
