# minecraft/ — Minecraft 具身子系统（2026-09-28 从主系统分离）
# ============================================================================
# 独立知识包/子系统：FAS 的 Minecraft 世界连接的**全部**代码收容在此目录，
# 主系统（app/autonomy/action 链）只通过这里的公开面使用它：
#   bridge.py       FAS ↔ Mineflayer 桥（Python 侧 HTTP 客户端）
#   session.py      Minecraft 会话生命周期（连接/重连/换世界/图谱会话节点）
#   embodiment.py   具身适配器（感知→图、动作执行、channel/能力注册挂接）
#   perception.py   世界状态→图谱原子槽位映射
#   reflex.py       反射级命令解析（低层自动反应）
#   channel.py      MC 聊天频道（收到玩家消息/广播）
#   actions.py      结构化动作封装（move/jump/stop/...，原 Action/minecraft.py）
#   bot/            Mineflayer 实体（bot.js + node_modules + config.json）
#
# 分离原则：
#   - 主系统对 MC 的 import 全部为 `from minecraft.xxx import ...`；
#   - 图谱层命名（"minecraft_bridge" state 键、桥活性 subject 等）保持不变，
#     知识语义不因代码搬迁改名，历史数据兼容；
#   - 不 import 主系统模块（单向依赖：主系统 → minecraft）。
# ============================================================================

# 刻意保持空：子模块由引用方按需加载，避免包级导入引入副作用/循环依赖。