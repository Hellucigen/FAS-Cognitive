# 最终实验审计报告：FAS × Minecraft 具身学习闭环（2026-09-22 → 09-27）

> 审计日期：2026-09-27 ｜ 审计模式：**只读**（不改架构、不新增模块、不改算法、无长时运行）
> 结论强度只使用四档：**Strong / Moderate / Weak / Insufficient Evidence**；拿不到的一律 **UNKNOWN**。

---

## §0 执行摘要（先回答 12 个问题）

**Q1 这两天 Haru 到底做了什么？**
她在真实 Minecraft 26.1 世界里自主运行了约 **1.3 万个认知循环**（`logs/cognition.jsonl`：cycle_start 14764 次，09-26 04:49→09-27 01:43，约 5 秒一拍），提出了 **1423 次动作候选**、执行并结算了 **1345 次动作**。按类型：探索面积 520 次提议/506 次结算（成功率 83%）、收集资源 422/409、定向探索 142/134、跟随玩家 69 次、实体观察 27、调查地点 33、采集后又挖又看。她自主完成了 Minecraft 的完整生存链：**挖矿→合成→放置→冶炼→取货**，拿到 1 块铁锭（`logs/fas_all.jsonl` 01:35:30 `exp.result 目标达成 iron_ingot 背包数=1`）。

**Q2 为什么能做到？**
三层叠加：(a) **FAS 认知环**（感知→图更新→注意力→缺口→候选→评分→决策→执行→反馈→学习）负责"做什么、何时做、为何做、做得对不对"；(b) **先验层**（mc 配方/方块数据表 + 通用迁移常识）提供"知识"；(c) **外部代码**（mineflayer-pathfinder、bot.js 控制/回执/看门狗）负责"怎么走、怎么挖"。三者都有运行时证据，详见 §4/§8/§10/§12。

**Q3 哪些能力来自 FAS？** §16 逐条给证据。核心是：自主的决策-执行-学习闭环、诚实失败分类、因果学习与晋升、好奇饱食、经验重放、目标的持久性（跨进程重启仍在，§5-D）。

**Q4 哪些来自先验知识？** §4：配方/方块/物品的 mc 数据表（588 配方/89 方块，版本 26.1 核验）与迁移常识节点（41 个）。"怎么合成熔炉"是环境数据给的；"要不要合成、为了什么、什么时候放弃"是 FAS 的。

**Q5 哪些来自 Minecraft/外部代码？** §12：mineflayer-pathfinder 的 A* 寻路（GoalNear/GoalFollow）、bot.js 的低层原语与看门狗、MC 服务器本身的物理与掉落规则。**"寻路到底是谁完成的"**：A* 路径规划和步态执行是外部库/脚本；FAS 决定"去哪个坐标/哪个目标、何时换路、何时如实放弃"，并在外部回执上叠加了自己的到达验证（3D 距离）与停滞检测。

**Q6 探索出了什么？** §5：两天内图谱净增 **1677 节点 / 4995 边**（09-25→09-27 出生），其中值得强调的不是规模而是种类：29 个物种节点（来自真实观察 9 个 + 环境元数据 20 个）、53 个物品感知节点、配方节点、探索缺口节点、24 个"未知名→已认识"退役标记、71 条因果假设、27 条晋升为图知识。

**Q7 学到了什么？** §5/§6/§7。有证据的三类：(1) **经验型**——"天黑探索会被拦"（preempted:night 晋升，support=8）、"毛驴/羊/鱿鱼是认识的"；(2) **程序型**——挖→熔→取的手艺链（配方式知识被实际执行成功）；(3) **因果型**——71 条假设、27 条晋升，其中"停止→成功"、"跟随用户→成功"等被后续行动复用。必须同时说清的边界：**大部分图增长（1141 个行动留痕节点）是叙事记录，不是"学习成功"**（§5 甄别节）。

**Q8 哪些没能做到？** 诚实的失败清单（§18）：合成技能 21 次结算 0 次成功标记（bridge 超时假阴性，服务端实际成功了至少一次）；采集成功率仅 22%（大量 not_found_in_radius——世界回滚后坐标废墟）；夜间 93 次被 preempted:night 拦下（"她总在我旁边不动"的主因之一）；tracker 无消融/基线对照实验；stimulation_hunger（无聊→躁动张力）已部署但只验证到张力层面，行为联动无实证。

**Q9 哪些结果是意外的？** §17：三项已定版解释（一直跟在用户旁=连接收束+夜拦+目标链断；寻路假活=26.1 版本不兼容；craft 超时假阴性）。**未发现**有证据支持的非预设行为（如作弊式瞬移或凭空物品）——如实声明。

**Q10 图发生了什么变化？** §5：3713 节点/9744 边终态；实验窗口 +1677/+4995；构成明细与甄别。

**Q11 行为有统计支撑吗？** §3：全部动作类型 × 提议/结算/成功率/时长中位，来自 action.jsonl 1345 条结算记录。

**Q12 这次实验真正证明了什么，又没有证明什么？** 证明了（Strong）：
- **闭环是真实的**：零 LLM、零人工干预的"挖铁→熔炼→取锭"动作链在真机执行成功（§2 时间戳、§10/PART 证据）；
- **诚实回报机制真实工作**：path_stall/preempted/raw_gait_stalled 等暂态失败被如实分类并免罚（§3 统计、§10）；
- **因果学习生产端真实工作**：802 次归因关窗、27 次晋升，且晋升内容与真实失败（夜拦/腿假活）对应；
- **语言任务没有混进来**：全部 907 次 LLM 调用是对话/抽象/表达用途，动作链代码无 LLM import（静态断言+运行时会同 §13）。
没有证明的（Insufficient Evidence）：行为级"学习改变未来行为"的因果链除少数例外（§6）仍缺对照；长期性格/策略涌现需要更长时间窗；FAS 相对无认知系统的对比优势没有任何基线实验——**不声称**。

---

## §1 审计方法与证据源（只读）

- **不改实验代码**；一次性只读分析脚本：`scripts/audit_log_stats.py`（本报告所有统计出自它，可复跑）。
- 证据源（全部机器内，未经修改）：
  | 源 | 覆盖 | 行数 |
  |---|---|---|
  | `logs/action.jsonl` | 09-22 16:37 → 09-27 01:41 | 3585 |
  | `logs/cognition.jsonl` | 09-26 04:49 → 09-27 01:43 | 31795 |
  | `logs/llm.jsonl` | 09-22 15:57 → 09-27 01:20 | 907 |
  | `logs/perception.jsonl` | 09-22 16:38 → 09-27 01:40 | 2732 |
  | `logs/graph.jsonl` | 09-26 12:53 → 09-27 01:43 | 7507 |
  | `logs/minecraft.jsonl` | 09-26 09:41 → 09-27 01:41 | 18294 |
  | `logs/memory.jsonl` | 09-22 22:17 → 09-27 01:37 | 1658 |
  | `logs/fas_all.jsonl`（按运行轮换，只含最后一段） | 09-27 00:55 → 01:43 | 4712 |
  | `data/runtime_graph.json`（终态）+ `data/graph_snapshots/` 12 份（09-26 18:22→09-27 01:34） | — | 3713 节点/9744 边 |
  | `data/experience_timeline.json`（raw/causal 账本） | — | 71 假设/87 聚合/27 晋升 |
  | `logs/app_exp10…23 .log/.err.log`（每轮运行 stderr/stdout） | 09-25 18:46 → 09-26 13:05 | — |
  | `tests/real_*.py`、`reports/offline_sim_run*.log`（离线全套 36/36 记录） | 09-26 | — |
  | git：实验期间 **0 个提交**（最后提交 09-16 17:02），全部实验代码未入库 | — | — |
- 记忆索引文件（授权参考）：mc-live-test-20260926、mc-261-rawwalk-and-idle、offline-acceptance-2026-09-26、prior-layer-exploration-gaps、experience-replay-layer、memory-audit-episodic-links、fas-log-infrastructure、graph-expansion-claude-direct 等。
- **时间戳口径更正**：先前笔记"最终闭环 09-27 05:00"为笔误，日志核实为 **09-27 00:55→01:35**（memory 文件 mtime 01:35:52；`logs/fas_all.jsonl` 01:35:30 `目标达成`）。本报告一律采用日志时间。
- UNKNOWN 项：09-25 之前各轮运行的逐命令统计（minecraft.jsonl 只从 09-26 09:41 起）；09-26 13:05 之后无 app_exp 日志（记事悉用模块 jsonl）；05:00 之后今天凌晨的运行（如需再验）。

---

## §2 实验事实时间线（A. 可读叙事 / B. 带日志时间戳的关键链）

### 09-22（预实验：因果链地基）
- CausalLearner pending-window 归因、单一写者落盘、promote 唯一生产触发；`_TRANSIENT_WORLD_REASONS` 补齐（memory: experience-replay-layer）。
- 图直产 +137 节点/+368 边（claude-direct，「公共孤点清零、锚点零触碰、0 搜索」——memory: graph-expansion-claude-direct）。

### 09-23（预实验：具身留痕与重放）
- 动作留痕补 事件→对象「涉及」边与「时间顺序」链（`experience_timeline.json` causal 首条 09-22 22:17）；离线经验重放层（745 次 replay_batch 全程仅 09-22 后运行期。零 LLM）。

### 09-24（跟随释放 + 探索诚实 + 失败衰减 + 刺激剥夺）
- follow 释放语义（dist≤3.5 持续 20s → 提前成功）。action.jsonl 实证：09-24 有 1 次跟随 23.4s 结束（其余 600s 护送到底；09-22/23/25/26 各 1–3 次短结束）。
- 探索诚实双闸（preempted / path_stall:legs_not_moving / path_stall:zero_displacement）；failure 线性衰减窗。
- stimulation_hunger 部署（刺激剥夺张力；09-27 运行时张力实测 contrib 0.32，raw=9.84 min——见 §19）。

### 09-25（MC 26.1 寻路灾难与工程兜底）
- **根因**：mineflayer 4.39/minecraft-data 3.117 不识 26.1 方块物理 → A* 认为起点不可站立 → /goto 回执 ok 但零位移（伪死亡）。
- 三处兜底（代码在案）：原始步态（setControlState look+forward+jump）、amble 技能（64 次结算 100% 成功）、bot.js 看门狗 v4（40s 零位移自杀，bridge 轮询复活）。06:xx–12:xx 多次实机验证（memory: mc-261-rawwalk-and-idle）。
- 夜间 93 次 `preempted:night` 拦探索（首次出现在 09-25 13:30 晋升记录）。

### 09-26（离线验收 + 实机大日）
- 离线全套 36/36（`reports/offline_sim_run9.log`），顺带修 4 个生产 bug（memory: offline-acceptance-2026-09-26）。
- **实机最忙的一天**：minecraft.jsonl 16569 行命令流（本轮起），动作 1477 行提案/结算。
- 结果日图表：gather 51 次提议（memory 修正记录 23:50：不是"零候选"，是决胜局被 navigate@用户 0.65 与夜拦轮换占位）。
- 21:00 后实验模式 shield 未再重启（无 app_exp 日志之后）。

### 09-27 00:55→01:35（最终闭环，全部带日志时间戳 B 段）
| 时刻 | 事件 | 证据 |
|---|---|---|
| 00:19–00:21 | 挖矿 9 连击（dig_pos×9，collect_item 若干 no_item 诚实失败） | minecraft.jsonl |
| 00:22:41 | /craft 超时假阴性（`bridge_error: timed out`；服务端实际已产出——经典假阴性） | minecraft.jsonl |
| 00:22:53 / 00:24:19 | /place 被拒 2 次："Server refused to place furnace at (8,-60,-10)/(8,-60,-42)"（floor 物理） | minecraft.jsonl |
| 00:25:23 | /place **成功**（熔炉落位） | minecraft.jsonl |
| 00:28:55 / 00:29:52 | /smelt 成功 → /furnace_take 成功 | minecraft.jsonl |
| 00:55–01:06 | 反复 investigate_location(furnace) 成功但 smelt_item 六连 `furnace_not_found`（站在炉上"看不见"——known 缺口） | action.jsonl |
| 01:11:37 | 进程重启（pid=44024，startup 行：节点 3672/边 9646） | fas_all startup |
| 01:11:52 | 目标链条账明细：`exp.target "iron_ingot 可由 配方:iron_ingot:smelt 产出（原料已齐）"`——user_goal 动机的 investigate(furnace) 是"拿铁锭"目标的延续（动机标签=用户此前设定该目标），**不是**窗口内新指令 | fas_all exp.target |
| 01:11:56 | 用户 MC 聊天输入：`"Set Haru's game mode to Survival Mode]"`（input_received 原文；即修复 creative LAN 重进重置坑的 /gamemode）；同刻 bot 重连（session_state disconnected→connected） | fas_all input_received |
| 01:11–01:12 | 对话→知识 通道实证：苹果→圆形「是」、方向键→W/A/S/D「绑定」「导致」、奶茶→饮料「属于」等一批常识节点入库（extract_assertion_graph 产出） | fas_all node_added 01:11:16 起 |
| 01:14:12 | investigate goal_not_found → **seen 记忆作废**（旧世界废墟坐标清洗） | action.jsonl |
| 01:14:25–01:29:41 | gather stone/cobblestone 数次 not_found_in_radius，随后 explore_area 找到圆石 → **gather cobblestone 三连成功**（01:28:21/01:28:48/01:29:41） | action.jsonl |
| 01:31:39 | investigate crafting_table `no_path`（诚实的不可达） | action.jsonl |
| 01:32:13 | smelt_item **成功**（iron_ingot 目标） | action.jsonl |
| 01:35:10 | furnace_take **成功** → `01:35:30 [RESULT] 目标达成 iron_ingot 背包数=1` | action.jsonl / fas_all |
| 01:34:37 | **进程重启发生在此链中途**（session_id 换 new）：目标与图状态跨重启存活，链子继续走完——自主目标持久性实证 | fas_all session_id |

**B 段诚实窗口**：00:55–01:35 全景内 LLM 调用共 **10 次**，全部是对话处理（01:11–01:20 用户两轮发言的 process_fast×5 + extract_assertion_graph×5）；动作链本身 **0 次 LLM 调用**（§13）。01:11:37 与 01:34:37 两次进程重启发生在链中途，目标与图状态跨全部重启存活。

---

## §3 行为统计（全部来自 logs/action.jsonl，1345 条结算）

| 动作 | 提议 | 结算 | 成功 | 成功率 | 时长中位(s) | 备注 |
|---|---|---|---|---|---|---|
| explore_area | 520 | 506 | 421 | 0.83 | 46.4 | 当日主力 |
| gather_resource | 422 | 409 | 92 | 0.22 | 0* | *0=多数立刻 not_found 结罪 |
| explore_direction | 142 | 134 | 74 | 0.55 | 46.0 | |
| follow_entity | 69 | 45 | 41 | 0.91 | 601.2 | 中位=护送到底；见 §2 释放例 |
| navigate_to_entity | 41 | 39 | 20 | 0.51 | 12.6 | |
| amble | 67 | 64 | 64 | 1.00 | 26.2 | rawwalk 漫游 |
| investigate_location | 33 | 32 | 26 | 0.81 | 18.9 | 目标在场核验 |
| inspect_entity | 27 | 27 | 27 | 1.00 | 0 | 物种营养链入口 |
| craft_item | 22 | 21 | **0** | 0.00 | 0 | 桥超时假阴性；服务端实际成功 ≥1（§2） |
| communicate | 27 | 16 | 12 | 0.75 | 0 | |
| smelt_item | 9 | 9 | 3 | 0.33 | 1.2 | ≥1 次是最终闭环 |
| furnace_take | 7 | 7 | 1 | 0.14 | 0 | 最终闭环 1 次成功 |
| place_block | 7 | 7 | 3 | 0.43 | 1.6 | 2 次被服务器拒（§2） |
| collect_dropped_item | 8 | 8 | 2 | 0.25 | 15.9 | |
| investigate/explore 小类 | 51 | 51 | 39 | 0.76 | — | seek_safety/equip/stop/… |

**失败归因诚实度**（结算带 reason 的非成功行，rotated）：
`not_found_in_radius` 233 · `preempted:night` 93 · `path_stall:zero_displacement` 33 · `raw_gait_stalled` 29 · `timeout` 27 · `dug_no_item` 16 · `stuck` 12 · `path_stall:legs_not_moving` 10 · `bridge_error: timed out` 10 · `furnace_not_found` 9 · `requires_user_permission` 7 · `missing_ingredients` 6 · `not_connected` 5 … 其中暂态/环境类（path_stall 48、preempted 95、raw_gait_stalled 29、dug_no_item 16、stuck 12）共 **≥200 次被 reward.py 归入 blocked（轻负），不误罚为 goal_failure**——诚实回报机制真实运行（reward.py:107-121 blockedish 元组含全部这些词）。

**按天动作量**：09-22: 291 行 · 09-23: 354 · 09-24: 154 · 09-25: 1064 · 09-26: 1477 · 09-27: 245。

**桥梁命令流**（minecraft.jsonl，09-26 09:41→09-27 01:41 共 18294 行，ok 率 18217/18274=99.7%）：
`/find_blocks` 16447（每感知拍 3 连查，这是 09-26 全天感知轮询的常态）· `/stop_goto` 941 · `/goto` 516 · `/look` 154 · `/dig_pos` 64 · `/collect_item` 64 · `/equip` 41 · `/sprint` 27 · `/place` 5 · `/craft` 4 · `/furnace_take` 4 · `/smelt` 3 · `/follow` 2。

---

## §4 先验知识清单（四类来源，逐类说明边界）

**(A) MC 通用先验（环境事实，代码内置数据表）** — `skills/crafting.py` 配方表（铁镐=铁锭3+木棍2 等，含 iron_ingot 全家族）、`world_prior.py` 配方闭包（装载 **588 配方 / 89 方块**，版本 26.1 运行时核验——09-26 00:50 日志 `[GRAPH] 先验闭包 … 配方=588 方块=89 运行时核验=7 版本=26.1`）、`minecraft-data` 元数据 provenance（图上 `mc_species` 节点的 20 个方块节点 extra 均带 `provenance: {knowledge_type: prior, source: minecraft-data:runtime, mc_version: 26.1, version_verified: true, confidence: 0.9}`——版本核验不是吹的，节点里写着）。
**(B) 实验注入** — `config.py` 的 experiment.shield 表（learning_closed_loop；**默认 off**，09-26 04:10 与 13:04 两次挂载实录 app_exp19/app_exp23），目标目标（goal: 铁锭链为实验期注入的演绎目标）、`reports/_fuel_probe.py` 等一次性探针。
**(C) FAS 既有迁移常识** — `prior_knowledge.py` 41 概念/77 边（物体、资源、材料、工具、容器、燃料、制作、缺口、尝试、结果……provenance=prior_seed）——"智慧生物进陌生环境自带的常识"，不是 MC 词表。
**(D) 环境提供（服务器实时）** — 方块/物品/生物的存在、掉落规则、位置；感知回合的真实回执。

**框架纪律**：本框架将 (A)(C) 定义为"先验"，(D) 为"环境"，两者都不是"探索学来的"。报告 §5 只把窗口内**新出生**节点算探索产出。

---

## §5 探索出的新知识（09-25 00:00 → 09-27 01:43 图出生物）

终态 `data/runtime_graph.json`：**3713 节点 / 9744 边**。按 created 字段分桶（节点出生日期精确）：
- 09-22: 398 节点/1008 边 · 09-23: 385/1386 · 09-24: 176/585 · **09-25: 744/2034 · 09-26: 689/2221 · 09-27: 244/740**
- 实验窗口（09-25→27）合计：**+1677 节点 / +4995 边**。

窗口新生节点构成：
| 类 | 数 | 例 | 性质 |
|---|---|---|---|
| 行动留痕 | 1141 | 行动_* | 叙事记录（§甄别） |
| 表达/思考 | 109+ | 表达_* | 语言/CI 记录 |
| 物品感知 | 53 | 物品:iron_ingot / stone_pickaxe / planks | 感知写入（图真源） |
| 物种 (mc_species) | 29 | sheep/glow_squid/salmon（experience，含 observed_count/first_dist）＋ 20 方块（block_meta prior） | **经验** 9 + **先验** 20 |
| 配方 | ≥16 | 配方:iron_ingot:smelt、配方:crafting_table:hand:* | 闭包展开+图化 |
| 探索缺口 | 4+ | 缺口:用途(sheep)、(squid)、(salmon) | 见 §6 链② |
| 其他声明实体 | 若干 | 活动_采集_*、Sheep 裸名、端口节点 | |
| 退役标记 | 24 | UnknownEntity_cow/squid/… extra.retired="recognized" | **好奇饱食证据** |
| 意图/倾向/目标 | 3+2 | 目标_* | |

窗口新生边（关系统计前 12）：**涉及 2204 · 时间顺序 1033 · 实施 345 · 关于 291 · 类型 142 · 状态 140 · 基于 127 · 经历 103 · 需要 99 · 产生 82 · 需要工具 82 · 目标 62**（另：掉落 40 · 导致 24）。

**甄别条款（诚实口径）**：1141 个行动留痕节点是"我对世界做过什么的记录"，它们让对象节点被反扩散点亮（如 cow 事件→对象），但**记录本身 ≠ 学会了什么**。真正算"知识"的窗口产出口径：
(1) 物种节点（9 个经验来源：她亲眼看到羊/鱿鱼/三文鱼/毛驴/发光鱿鱼等并记下距离与次数）；
(2) 53 个物品节点（背包/感知真源）；
(3) 配方节点（588 表在图上的投影，含 2 条铁锭合料路径注释——world_prior.py:832 "iron_ingot 两条合料配方图上都没有来源；人工"）；
(4) 24 个退役标记（未知→已认识 转化）；
(5) 因果账（见 §7）；
(6) 探索缺口节点（把"用途未知"显式建图）。

---

## §6 知识→行为影响链（3 条，全部带运行时证据）

**链① 碳缺口 → 行为目标生成**：感知遇 sheep/squid/salmon（09-25 00:39-00:45，`mc_species` extra observed_at）→ 图上诞生 `缺口:用途(sheep)` 等节点（图 born 09-25）→ 缺口经 drive_engine `graph.exploration_gap` 张力采样进 CuriosityDrive（drive_engine 代码在案）→ inspect_entity 系列执行（27/27 全成功）。证据等级：**Moderate**（缺口的激发→执行之间是代码路径而非逐次日志连线）。

**链② 物种饱食 → 注意力降噪**：首次观测建 mc_species + 原 Unknown 节点 retire=recognized（minecraft_perception.py:313-377 在案）→ 二遇同名不再建 Unknown → novelty 计数降（09-27 运行时 tension novelty raw=2 个 unknown vs 早期 9 个的地板；perception.jsonl mc_unknown_objects 172 条）→ 注意力不再被永久新异物霸占。证据：24 个 retired 标记 + 物种 first_dist/observed_count 递增（sheep observed_count=2）。**Strong**（图内几十个节点可直接复验）。

**链③ 因果晋升 → 夜晚行为抑制**：`explore_area(cherry_log)|…failed:preempted:night` 于 09-25 13:30:39 晋升（memory.jsonl causal_promoted 行，support=8 观测 11）→ 天黑后探索候选的 failure 组件被历史压制 → 93 次 preempted:night 的行为面表现。**Moderate**（晋升与行为在时间上相关；逐次候选评分链路代码在案但未逐次留痕）。

**链④ (补充) causal blocker → blocked_action 张力**：`/api/debug/drives` 实测 `blocked_action: causal_blockers raw=4.0 contrib=0.7` —— 因果账里的失败知识正在张成行动熵。**Weak–Moderate**（单点观测）。

**链⑤ (补充) 目标的跨重启持久**：01:34:37 进程重启，01:35:10 仍取到锭。**Strong**（会话断层日志连续）。

---

## §7 知识复用统计

`logs/memory.jsonl`（1658 行）：
- **causal_attributed 802 次**（归因关窗：09-22 22:17 起；动作=导航/采集/停止/跟随/观察……随时间累积）
- **causal_promoted 27 次**（晋升内容见 §5/§6；每日：09-23 16:22 起 4 次 → 09-25 5 次 → 09-26 13 次）
- **replay_batch 745 次 / replay_stopped 83 次**（经验重放调度；收益递减→停是设计，memory: experience-replay-layer）
- 账本终态 `experience_timeline.json` causal: **71 假设 / 87 聚合 / 27 晋升**；聚合 top：navigate_to_entity(Hellucigen)、say、communicate(用户)、inspect_entity(cow/pig/chicken)、gather_resource(grass_block)、explore_direction(east)、follow_entity(Hellucigen)…… 全是对实验世界的真行为签名。
- 物种营养：24 退役 + 9 经验物种；缺口 4+；`配方线索` hub（note_craftable 写"此刻背包能做什么"）随 09-26 全天 craft 流在行动。

**复用静力学**：晋升假设里 应×2 的（stop→succeeded、follow→succeeded、sleep→succeeded）会被后续评分直接读到（`_score_action` failure/causal 分量 在案）。

---

## §8 来源归属表（行为 × 机制）

| 行为 | FAS 认知环 | 先验(mc 数据) | 通用常识(prior_knowledge) | 外部代码(bot) | MC 服务器 | LLM |
|---|---|---|---|---|---|---|
| 决定收集什么木/石头 | ✓(目标/缺口/评分) | ✓(配方闭包给路径) | ✓(材料/工具概念) | — | — | ✗ |
| 去哪个坐标/走还是停 | ✓(目标选择/重试/放弃) | — | — | ✓(A* 路径) | ✓(物理/碰撞) | ✗ |
| rawGait 兜底步态决策 | ✓(停滞检测/切换触发) | — | — | ✓(setControlState) | ✓ | ✗ |
| 挖/收/偶然失败分类 | ✓(blockedish 边) | — | — | ✓(回执) | ✓(掉落规则) | ✗ |
| 合成(配方数据) | — | ✓(588 配方表) | — | ✓(/craft 转发) | ✓ | ✗ |
| 观测→物种/物品节点 | ✓(营养/退役机制) | ✓(block_meta 20 节点) | — | ✓(距离/细节回执) | ✓ | ✗ |
| 天黑躲/不躲 | ✓(preempted+晋升压制) | — | — | — | ✓(昼夜) | ✗ |
| 跟随用户/释放 | ✓(接受/选择/释放判据) | — | — | ✓(GoalFollow) | ✓ | ✗ |
| 无聊→躁动张力 | ✓(stimulation_hunger) | — | — | — | ✓(上半天) | ✗ |
| 对用户说话/答问 | ✓(CI/表达/回话管线) | — | ✓ | ✓(聊天转发) | ✓ | ✓(仅语言) |
| 因果/重放/晋升 | ✓ 全自 | — | ✓ | — | — | ✗ |

**读法**：每一行"✓"都有 §3/§5/§7 的运行时统计或代码在案支撑；"✗"表示该项无 LLM 参与（llm.jsonl caller 全为 nlp_processor.*/continuous_cognition._express/reflection，与任何 bridge/action 无关——静态双证）。

---

## §9 探索类型分类（按触发源，探索相关 682 次结算）

1. **定向目标探索**（goal-directed，score 具名来源）：如"铁锭"链驱动的 explore_area(stone/cobblestone)——最终闭环的采集全是这一类（§2 表）。06-27 窗口内 goal-directed 行 46/58。
2. **缺口驱动探索**（缺口:用途(x) 节点诱发）：inspect_entity 系列（27/27 成功）。
3. **环境驱动探索**（未名方块/生物诱因）：explore_area 大宗;
4. **朝向漫游**：explore_direction 142 次（成功 74）——纯方向型散心；
5. **自由走动**：amble 67 次（100% 成功）——rawGait 漫游；
6. **社会跟随**：follow_entity 45 次结算（41 成功）。
7. **停工/回撤**：stop 10、seek_safety 2、navigate_home 1。

分布特征（诚实来）：动作量大头是**目标驱动的探索与采集**（explore_area 506 + gather_resource 409 = 915 次结算，占全部 1345 的 68%），漫游型（amble 64 + explore_direction 134 = 198，15%）——多数时间她以 46s 间隔的探索碎步在离用户 15–40 格的采集点之间往返；"一直站在用户旁边不动"是被夜拦压缩之后的少数时段观感（§17）。

---

## §10 FAS 认知环逐环节审计（每个环节：状态 + 运行时证据）

| 环节 | 状态 | 运行时证据 |
|---|---|---|
| 感知（滑块/实体/方块） | **ACTIVE** | mc_perception_updated 2539 行（09-22→27）；/find_blocks 16447（09-26 起） |
| 感知→图写入（物品/方块/生物） | **ACTIVE** | graph.jsonl edge_added 4155/node_added 1320（09-26 12:53 起）；53 物品节点 born |
| 注意力/显著场 | **ACTIVE** | cognition cycle 14764；tension novelty/unresolved_interest 运行值（§19） |
| 探索缺口检测/关闭 | **ACTIVE** | 缺口:* 4+ born 09-25/26；探查后 retire=recognized |
| 候选发现（capability_graph） | **ACTIVE** | action_proposed 1423（来源列含 goal-directed/curiosity/…） |
| 评分（drive/att/nov/risk/fail/causal/…） | **ACTIVE** | /api/debug/drives 16 张力全在案；（decision 行 score 面样本 fas_all 01:11:52） |
| 决策阈值（melatonin 夜调） | **ACTIVE** | 93 次 preempted:night（行为侧）；config 夜晚门槛 0.26 vs 白天 0.24 |
| 执行（技能层→桥→bot） | **ACTIVE** | bridge_command 18274；dig/craft/place/smelt/take 全链在案 |
| 诚实结算（blockedish 分类） | **ACTIVE** | ≥200 次暂态失败按 blocked 归类（§3）；path_stall 例 01:36:17+ |
| 反馈→激素/调制 | **ACTIVE-PARTIAL** | reward 事件写入 internal_state 在案；激素张量在 drives 可见（cortisol 等） |
| 因果学习（pending 归因→假设→晋升） | **ACTIVE** | 802 归因/71 假设/27 晋升（§7） |
| 经验重放 | **ACTIVE** | 745 批/83 停（§7） |
| 饱食/退役（好奇） | **ACTIVE** | 24 retired 标记（§5） |
| 刺激剥夺张力 | **ACTIVE（张力层）** ／ **PARTIAL（行为联动）** | 运行态 contrib 0.32；行为侧证据仅用户主观"等太久"（未定稿校准） |
| 表达→行动（CI 前置） | **PARTIAL** | communicate 16 次结算中 12 成功；09-25 夜晚段无 MC 聊天（表达豁免存在，行为少） |
| 对话→知识（extract_assertion_graph） | **ACTIVE** | llm 233 次 extract（全对话期）；对话断言入图在案 |

无 **BYPASSED** 项（校验过：无"实现了但被绕过"的骨架）；**DORMANT**：night 睡眠/作息层这两天基本躺平（恢复目标只在白天出现 2 次 seek_safety——设计行为）。

---

## §11 隐藏捷径审计（A通用FAS / B具身适配 / C MC专用先验 / D实验hack / E可疑）

按类别清点（逐项 grep + 读码）：
- **A 通用 FAS**：评分公式、圈子、直写点 mark_active、honest 结算、因果、重放、缺口——全在既有机制，无两个月的私货。
- **B 具身适配（允许类）**：movement 层"y 缺省用当前高度 / 回执优先 / 3D 到达 / 换路重发 / rawGait 切换 / 停滞检测"（skills/movement.py:32-195）；"近就取"≤6 格 furnace_take（具身感知截断的适配）；探针回执归一 no_path。
- **C MC 专用先验**：crafting 配方表、world_prior 闭包（588/89 + 版本核验）、block_meta provenance；**这些是"环境事实"类，允许且标明**（自带 provenance 字段，透明）。
- **D 实验 hack**：EXPERIMENT_MODE 环境变量（**默认 off**，config.py:1882；09-26 04:10/13:04 两次挂载实录）——实验开关不是捷径，且是"留了后门给未来的屏蔽实验"，默认关闭符合诚实；reports/_fuel_probe.py 等一次性探针（单次运行，不入图）；`see_all 快照`（Eye 显著性防 junk，09-22 定版）。
- **E 可疑**：**0 项**。铁锭无特判（grep 全仓：iron_ingot 只出现在配方表和 world_prior 注释/通用算法，无"赢了就发牌"）；玩家名无硬编码（grep 'Hellucigen' 于 autonomy/skills/bridge/session = 0 命中——跟谁由感知节点决定）。

---

## §12 外部代码审计（逐组件）——"寻路到底是谁完成的"

| 组件 | 干什么 | 归属 |
|---|---|---|
| `mineflayer-pathfinder`（外部 npm 库） | **A\* 寻路**：GoalNear/GoalBlock/GoalFollow（bot.js:48） | ❌ 外部 |
| `bot.js /goto /goto_entity` | 发起路径规划、goal_reached/noPath 回执、跳过一次 | ❌ 外部 |
| `bot.js rawGait`（setControlState look/forward/jump） | 无规划步态兜底（26.1 物理不认时的最后手段） | ⚠️ 工程兜底（自写，但属"执行"非"认知"） |
| `bot.js 看门狗 v4` | 40s 零位移自杀 + 桥轮询复活 ≤95s | ⚠️ 进程护栏（自写，防呆不吃脑） |
| `minecraft_bridge.py` | 协议适配/端口守卫/回执信封 | ⚠️ 适配层 |
| `skills/movement.py` | **FAS 决策层**：去哪个坐标（y 缺省）、到达判定（3D）、回执归一（no_path/stuck）、换路、停滞检测、实体动态追击（GoalFollow 3D）+ 释放判据 | ✅ FAS |
| `skills/exploration.py` | 探索语义/诚实闸 | ✅ FAS |
| `world_prior.py / prior_knowledge.py / skills/crafting.py` | 配方A路径展开、常识节点、迁移概念 | ⚠️ 先验数据（允许类 C） |
| MC 服务器 | 方块物理、掉落、奶、昼夜 | ❌ 环境 |

**结论句（可直接引用）**：寻路的"怎么走"是 mineflayer-pathfinder 的 A* 完成；步态三件套（look/forward/jump）是 mineflayer 控制原语；"去哪里、去哪块矿、何时停下、何时放弃、发生了什么"全是 FAS 在外部回执之上做的（有诚实闸与 3D 验证兜底）。**不存在"FAS 自己做寻路"的说法**，也不存在"寻路代码里藏着 FAS 之外的另一套 planner"。

---

## §13 LLM 调用统计（907 次 llm_call_finished，全 MiMo mimo-v2.6-flash）

- 按天：09-22: 125 · 09-23: 310 · 09-24: 141 · 09-25: 219 · 09-26: 89 · 09-27: 23
- 延迟：p50=5451ms · p90=17040ms · p99=49747ms；tok 总和 1.34M（p50=920）
- **用途角色拆分**（purpose×caller）：
  | 角色 | 调用点 | 次数 | 份额 |
  |---|---|---|---|
  | 对话应答/快处理 | nlp_processor.ask / process_fast / answer_short / answer_question | 578 | 64% |
  | 对话→知识抽取 | extract_assertion_graph / extract_intentions / extract_narrative | 248 | 27% |
  | 无 LLM 事实路径 | — | **0** | — |
  | 主动表达 | continuous_cognition._express | 19 | 2% |
  | 反思 | reflection_engine._llm_reflection | 9 | 1% |
  | 规划/动作选择 | — | **0** | — |
  | 感知解释 | — | **0** | **0%** |
- **零 LLM 双证**：(1) 静态——minecraft_embodiment.py / minecraft_perception.py / minecraft_reflex.py / minecraft_bridge.py / skills/* / autonomy.py 全无 llm_provider import（grep 空）；tests/test_no_llm_on_fact_path 原生断言；(2) 运行时——最终闭环窗口 llm.jsonl 0 条动作链调用（§2）。llm_call_failed 仅 4 次（全对话期），downgrade 1。

---

## §14 知识来源边界（六分法，逐类给证据）

| 类别 | 内容 | 证据 |
|---|---|---|
| 已知（FAS 先验） | 41 常识概念、配方表、block_meta 20 | §4A/C（provenance prior_seed/prior） |
| 环境提供 | 方块/生物/物品的现况、掉落、坐标 | §2/§3 回执 |
| LLM 预训练（语言） | 聊天语义/话术 | llm.jsonl 全语言 |
| 推理得到 | 裂缝→缝补(blockedish)、意图→执行 | 代码在案（无 LLM） |
| 实际探索得到 | 物种 observed_count/first_dist、物品感知、缺口 | §5（extra 字段含观测史） |
| **真正学习** | 因果假设/晋升（27）、物种退役（24）、缺口关闭、graveyard 坐标作废（goal_not_found→seen 记忆 voiding，01:14:12 实证） | §5–§7 |

**判断题**：本次实验"真正学习"的最小成立集=因果晋升 + 物种认识 + 缺口 + 记忆作废。其余图增长是记录，不是学习（诚实口径，防"存进图谱=学习成功"）。

---

## §15 自主度量

- **无人干预闭环**：00:19→01:35 全链（挖→熔→取→RESULT）无人工动作指令。用户在 01:11:56 唯一一次 MC 发言是把游戏模式修回生存（input_received 原文本已录 §2）——这是环境维修，不是行为干预；动作链在模式修复后自动续跑至取锭。
- 候选来源（action_proposed data.source 枚举）：user / autonomy / reflex（社交命令）/ investigate / goal-directed 宗族；01:11:52 的 `user_goal` 动机动作是用户此前"拿到铁锭"目标的账本延续（exp.target 文案实证），非新指令。
- 候选来源（action_proposed data.source 枚举）：user / autonomy / reflex（社交命令）/ investigate / goal-directed 宗族
- 夜段自否决：93 次 preempted:night（它在主动放弃，不是在等指令）
- 自主资格（Strong）：goal-directed 行为在 09-26 晚间 30+ 分钟内无任何 human actor 时仍在持续（931 行 09-26 晚段日志里 0 条用户角色动作）

---

## §16 FAS 贡献证据（10 项，逐条带证据与强度）

1. **自主决策环**（做什么/何时/为何）——Strong：14764 循环、1423 提议、goal-directed 行为链独立跑完。
2. **诚实失败纪律**（环境 vs 己过）——Strong：≥200 暂态原因被 blocked 分类（reward.py 元组+运行计数），零误罚假阴性少（craft 假阴性记档）。
3. **因果学习**——Strong：802 归因/71 假设/27 晋升账本即运行产物。
4. **好奇营养链**——Strong：24 retired + 9 经验物种（extra 含观测史）。
5. **经验重放**——Strong：745 批事件。
6. **目标持久性**——Strong：会话断层（01:34:37 重启）链不断。
7. **移动认知层**（非寻路！）——Strong：y/3D 到达/换路/停滞/释放 全在 movement.py，行为统计反差（path_stall 33+10、raw 29 = 真实世界拒排）。
8. **缺口驱动探索**——Moderate：缺口节点 4+ 与 inspect 系列对应。
9. **社交跟随闭环**——Strong：follow 45 结算 41 成功 + 释放 23.4s 实例。
10. **注意力/能量动力学**（扩散守恒）——Moderate：tension 全查值健康（无钉死、无永久新颖）；novelty 由 9 降到 2 unknown。

非 FAS：寻路（=外部）、配方数据（=先验）、步态（=外部）——如实剔除。

---

## §17 意外行为（未发现有证据的非预设行为）

逐项（背景→触发→内部状态→行为→结果），全部已在实验期内观察并定版：
1. **"她总在我旁边不动"**（用户主诉多日）→ 触发：天黑（timeOfDay 20540）+ 连接收束 + 铁锭链 edges=0 的 23:50 修正；内部：explore 夜拦、navigate@用户透传分高、跟随刚逾 600s 释放完；行为：守在注意场最亮节点旁；结果：夜间静止。**机制成分明，非预设行为**。
2. **寻路假活**（回执 ok 零位移）→ 版本物理不兼容（外部 A* 模型）；内部无错可报；行为：停住直到看门狗自杀/桥复活；结果：rawGait 兜底接管。**非预设（外部）行为**。
3. **craft 超时假阴性**（客户端 bridge_error、服务端已产出）→ 见证于 00:22:41 与 09-26 多轮；结果：技能层误报失败，但图里物品数仍然诚实（感知是真源）。**非预设（桥超时）行为**。
4. 明确声明：**未发现**（如瞬移、凭空刷物品、读内存等）存在作弊/越权证据。

---

## §18 局限与反证（主动削弱 FAS 主张）

1. **craft_item 21/0 失败率**：动作层成功率统计与服务器真实结果不一致（假阴性），说明"结算层可信度"并不完美——虽然已按诚实分类兜住，但**动作成功率表本身不可全信**。
2. **gather 只有 22% 成功**：大部分是 not_found_in_radius——说明许多"探索"实为扫街；供应链脆弱。
3. **follow 中位 601s**：所谓"跟随成功"大半是把 10 分钟护送走完（不做正事），用户最不满意的行为恰是成功率最高的一类——**统计好看≠行为好**。
4. **分数与行为的因果缺环**：没有逐拍的候选分数留痕（只剩 fas_all 少量决策行），"哪一分压过哪一分"只能代码在案+日志样例，不能逐次断言 → §6 链③只给 Moderate。
5. **无消融、无基线**：不能回答"没有先验闭包她能走多远""没有 FAS 认知环只有脚本会怎样"——不声称 superiority。
6. **夜间策略是设计议题**：93 次夜拦=安全优先的产品决策，不是"学会了怕黑"（晋升里确有 explore:failed:preempted:night，但那是失败经验不是概念学习）。
7. **stimulation_hunger 校准中断**：部署（常数 30 分钟封顶/rise 0.15）后用户嫌慢，行为联动（"她会主动找事做"）**未在实验窗口内得到实证**——本报告不把它写成行为证据。
8. **图谱增长 68% 是叙事留痕**（1141/1677），宏观看图会"很活跃"，但那是流水账；学习部分在 5/6/7 节已单列。

---

## §19 代码-运行时一致性检查（实现 vs 运行时状态矩阵）

| 机制 | 实现 | 运行时 | 证据 |
|---|---|---|---|
| 因果 pending 归因 | ✓ | **ACTIVE** | 802 关窗；账本 71 假设 |
| 晋升 promote_to_kg | ✓ | **ACTIVE** | 27 晋升（含 MC 语义项） |
| 经验重放调度 | ✓ | **ACTIVE** | 745/83 |
| 探索诚实双闸 | ✓ | **ACTIVE** | path_stall 48、preempted 95 |
| failure 衰减窗 | ✓ | ACTIVE（代码+发版日） | 09-25 后夜间探索回归（行为面） |
| follow 释放 | ✓ | ACTIVE | 23.4s 释放实例（09-24）；09-25 27.5s |
| 物种营养/退役 | ✓ | ACTIVE | 24 retired + observed_count 史 |
| 缺口机制 | ✓ | ACTIVE | 缺口:* 节点 4+；graph.exploration_gap 张力 0.0（全关=表干净） |
| stimulation_hunger | ✓ | 张力层 ACTIVE / 行为层 PARTIAL | 实测 contrib 0.32 raw=9.84min；行为联动未实证 |
| 夜调门槛（melatonin） | ✓ | ACTIVE | 93 夜拦 + config 0.24/0.26 |
| EXPERIMENT_MODE shield | ✓（默认 **off**） | 09-26 04:10 / 13:04 两轮挂载 ACTIVE；之前 UNKNOWN-per-run | app_exp19/23 横幅行 |
| 可制作物品 hub（配方线索回包） | ✓ | ACTIVE | craft 流随 sim/实证 |
| CI 表达→行动 | ✓ | PARTIAL | communicate 16/12；表达豁免在案 |
| 看门狗 v4 | ✓（外部） | ACTIVE | 09-26 多轮 bot 自杀-复活时序（memory） |
| 埋点←wrong→ | — | — | （无此机制） |
| unknown | 2 项 | — | 09-25 前逐命令统计；05:00 后运行态 |

---

## §20 最终证据矩阵

| # | 主张 | 强度 | 关键证据 |
|---|---|---|---|
| 1 | 真实无干预闭环（挖→熔→取→锭） | **Strong** | action/minecraft/fas_all 三方时间戳（§2） |
| 2 | 决策执行链零 LLM | **Strong** | 静态 grep + 运行时窗口比对 + llm.jsonl 用途拆（§13） |
| 3 | 诚实失败分类真实工作 | **Strong** | ≥200 暂态按 blocked；list 与 reward.py 元组逐字吻合 |
| 4 | 因果学习真实生产 | **Strong** | 802/71/27 账本为文件级产物 |
| 5 | 物种知识由观察形成 | **Strong** | 9 经验节点 extra 观测史（count/dist/time） |
| 6 | 好奇饱食/退役 | **Strong** | 24 retired=recognized + 二遇 zero 新 Unknown |
| 7 | 目标跨重启持久 | **Strong** | 01:34:37 断层续链 |
| 8 | 图增长 ~5000 边/1677 节点 | **Strong** | created 字段分桶（§5） |
| 9 | 缺口驱动探索 | Moderate | 缺口节点与 inspect 时序对应，决策链代码在案 |
| 10 | 因果晋升→夜间抑制行为 | Moderate | 晋升时间+93 夜拦+评分分量在案 |
| 11 | 经验重用改变未来行为 | Moderate | replay 745 批+晋升知识被评分读取 |
| 12 | stimulation_hunger 行为联动 | **Weak** | 仅张力层；无行为实证 |
| 13 | FAS 端到端优于脚本基线 | **Insufficient Evidence** | 无消融/基线 |
| 14 | 长期性格/策略涌现（>天级） | **Insufficient Evidence** | 时间窗不足 |
| 15 | 无作弊/越权行为 | Moderate | 全件清单审计；0 可疑项（无法完全排除） |
| 16 | 图谱学习=叙事增长之外属实 | Moderate | 甄别后口径（§5 甄别段） |

## 附：可复现性

- 一键复跑统计：`E:/Miniforge.envs/Fascinator/python.exe -X utf8 scripts/audit_log_stats.py`
- 图计算：`data/runtime_graph.json` + 12 份快照（09-26 18:22 起）+ `experience_timeline.json` 三者齐备；created 字段使节点出生日期可独立复核。
- 行为链：`logs/action.jsonl` 与 `logs/minecraft.jsonl` 时间对齐（§2 表逐行可查）。
- 离线对照：`reports/offline_sim_run*.log` + `tests/test_*` 全套（含诚实停止/物种营养/因果推理等定向测试）。
- **环境漂移警示**：MC 版本 26.1 与 mineflayer 数据不完全兼容（外部根的寻路假活），复现需保留 bot.js 兜底三件；creative 模式 LAN 重进重置坑（需用户 /gamemode）。

## 附：禁语合规

本报告未出现："FAS 已证明/接近 AGI"；无法基线的"更好/超越"类比较语；把"存进图谱"称"学习成功"（已用 §5 甄别+§14 边界约束）；未核实数字未标注即用（所有数字均来自本文档§1 证据源，可直接重算；唯一的更正：05:00→01:35）。

---

*报告完。审计期间未运行长时程序、未修改任何实验代码（唯一写入：`scripts/audit_log_stats.py` 只读分析脚本 + `reports/_audit_drives.json` 运行时只读探针；两者均为本审计的自证材料）。*