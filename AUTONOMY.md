# 自主认知与行动闭环（Phase D）

让 Haru 在**没有用户指令**时，从自身认知状态产生简单意图并尝试执行。
Minecraft 只是当前的具身环境——自主层不认识 Minecraft，它只会产生语义意图，
由注册进来的具身适配器执行。

```
环境感知(embodiment.perceive) → 知识图谱更新（感知节点/未知节点/扩散）
      ↓
自主意图候选生成（图激活 · 驱动力 · 新颖性 · 未完成目标 · 最近结果 · 环境变化）
      ↓
目标竞争（结构化评分 + 可解释，复用已有激活/驱动/倾向信号）
      ↓
可行性检查（认知锁 · 具身能力 · 目标是否还在场 · 重试上限 · 冷却）
      ↓
执行（交给已有动作面：bridge + Action/minecraft）
      ↓
行动结果反馈（真实成败 → episodic 图节点 + 情绪 + 表达抑制 + 状态检测器）
      ↓
下一轮
```

## 1. 模块与职责

| 文件 | 职责 |
|---|---|
| `autonomy.py` | **通用自主层**：模式/暂停、感知编排、候选生成、目标竞争、可行性、执行、反馈、日志。零 LLM |
| `minecraft_embodiment.py` | **Minecraft 具身适配器**：语义意图 → 已有 bot 动作；真实回执；感知进图 |
| `minecraft_bridge.py` | 桥接（Phase D 扩展）：`call/get_action_result/health/look_at/dig/goto_*/stop_goto/equip/inventory` |
| `minecraft_bot/bot.js` | bot 侧新增 `/action_result`、`/stop_goto`、`/look_at`；`/dig` 记录逐块结果；实体带相对坐标 |
| `app.py` | 装配（`autonomy` + `minecraft_embodiment`）、用户活动打断、10 个 API |
| `continuous_cognition.py` | `cc.autonomy.tick()` 挂在既有 tick 上（不新建线程） |
| `index.html` | **Auto** 标签页：状态/意图/候选/目标/日志 + ON/STOP/PAUSE/RESUME/STEP |

## 2. 自主循环如何运行

- **启动条件**：`mode == "on"` 且未暂停、用户安静期已过、具身环境可用、无在飞动作、距上次行动 ≥ `min_interval_s`。
- **驱动方式**：`continuous_cognition` 每 4 tick（≈10 秒）调一次 `autonomy.tick()`——**不新建线程**，
  与检测器轮询共用同一节拍；`tick()` 全程确定性、零 LLM 调用、异常不外抛。
- **启动默认开启**（2026-09-19 起，`config.autonomy.mode_default`，可改回 `off`）：模式仍**不持久化**，重启回到默认值；`set_mode("off")` / API 随时可停（可停止性不依赖默认值）。
- **频率控制**：行动最小间隔（默认 12s）+ 动作冷却窗口（180s，同类动作降权）+ 失败退避（45s）
  + 同一 `(意图,目标)` 最多尝试 3 次；在飞动作有超时（20s）兜底，不会永远卡住。

## 3. 自主意图如何产生

候选**只从真实信号来**，不做随机抽取、不硬编码行为序列：

| 来源 | 产生的意图 |
|---|---|
| 感知里不认识的生物（图里有 `UnknownEntity_*`） | `observe` / `approach`（保持安全距离） |
| 敌对生物进入危险距离 / 血量或饥饿过低 | `withdraw`（安全优先，压过探索）/ `rest` |
| 同伴玩家在场 | `follow`；只有**已备内容**时才 `communicate`（不编台词） |
| 附近有安全清单内的材料 | `collect`（只挖 木头/圆石/沙子/泥土/煤矿…，绝不碰箱子/工作台/建筑） |
| 很久没探索 | `explore`（方向轮转 = 访问最少的方向，确定性） |
| 以上都没有但有可看的东西 | `observe`（低分兜底，如实说"先观察一下"） |

**没有依据就不产生意图**：`basis` 为空的候选不会被评分通过，循环进入 `idle` 并在
`state_reason` 里说明"没有任何可解释的依据（不编造想法）"。

## 4. 目标如何竞争和选择

评分是最小可替换接口（`config["autonomy"]["weights"]`），分量全部归一化到 `[0,1]`：

```
score = w_drive·驱动力 + w_attention·图激活 + w_novelty·新颖性 + w_tendency·行为倾向
        − w_risk·风险 − w_recency·刚做过 − w_failure·失败过
```

- `drive`：`CuriosityDrive` 等已有驱动节点的激活（不新造动机系统）
- `attention`：候选 basis 节点在图里的**真实激活值**（复用扩散结果）
- `novelty`：目标是否未知对象（图里有没有 `Unknown*` 节点）
- `tendency`：`DispositionStore.tendencies_for(["情境:主动发起"])`
- `risk / recency / failure`：危险、刚做过、失败过的惩罚

低于 `score_threshold`（默认 0.35）时**不做任何动作**，如实进入观察状态。
每个候选都带 `explain`（各分量数值 + 最终分），前端直接展示，可复算。

## 5. Minecraft 行动如何执行

语义意图 → 已有动作面（不新造动作系统）：

| 意图 | 动作 | 回执方式 |
|---|---|---|
| `observe` | `/look_at`（目标世界坐标 = Haru 位置 + 相对偏移；坐标→yaw/pitch 交给 bot 自己算） | 同步 |
| `approach` | `/goto` 到"目标方向、保留 `keep_distance` 格"的点 | 异步（`/action_result`） |
| `follow` | `/follow` | 同步（持续行为） |
| `explore` | `/goto` 朝访问最少的方向走 `step` 格 | 异步 |
| `collect` | `/dig`（中文名 → 英文名，只用安全清单） | 异步（逐块结果） |
| `rest` | `/stop` + `/stop_goto`（+ 解跟随） | 同步 |
| `communicate` | `/say`（无文本直接拒绝） | 同步 |
| `withdraw` | `/goto` 朝远离威胁的方向 | 异步 |

**失败必须真实返回**：`not_connected / entity_not_visible / no_target_coords /
block_not_in_safe_list / invalid_coords / noPath / dig timeout / unknown_block` 等
原始原因一路上传，不翻译成笼统的"失败了"，也不假装成功。

**取消**：`cancel()` = `/stop` + `/stop_goto` + `/stopfollow`（`/stop` 只清控制状态，
pathfinder 会继续走完当前 goal，所以必须调 `/stop_goto`）。

## 6. 行动结果如何反馈到 FAS

1. **图谱（万物皆图）**：写 `自主行动_{ts}` 节点（episodic 空间，
   `type=autonomous_action`），带意图/目标/参数/成败/原因/时长/依据解释，
   并连到 basis 节点（`涉及` 边）→ 参与扩散、可被反思与检索。
2. **情绪**：`persona.mood_event("positive"|"negative")`。
3. **表达抑制链**：`cc.note_outcome(negative)`（既有奖赏入口）。
4. **状态检测器**：`regulation.submit_state("自主行动", "success"|"failed", source="tool")`
   → 用户可用触发器对自主行动结果做后续反应（如连发失败就通知）。
5. **日志**：`autonomy.recent_log()`（前端展示）。

## 7. 与动机 / 奖赏 / 知识图谱的整合

- 不新建动机系统：驱动力读 `CuriosityDrive` 等**已有** drive 节点激活；情绪走已有 `Persona`。
- 不绕过扩散：候选的注意力分量直接读引擎算出的激活值；行动留痕进图后被 `mark_active`。
- 不绕过锁：可行性检查用 `regulation.locks.blocks_node/hides_node(..., "action")`——
  被锁的目标不会被自主靠近/采集，被锁的**能力节点**可以整类禁用自主动作。
- 用户优先：`/api/nlp` 每次进入都调 `autonomy.notify_user_activity()` → 自主行动让位并
  打断在飞动作（用户新指令永远优先，不需要 LLM 判断谁更重要）。

## 8. LLM 介入边界

| 级别 | 位置 | 本阶段状态 |
|---|---|---|
| Mode 0（默认，全实现） | 感知/候选/竞争/可行性/执行/反馈/冷却/重试 | **循环里没有任何 LLM 调用**（测试断言：多次 tick 后 LLM 计数为 0） |
| Mode 1 | `POST /api/autonomy/goals {"text": "..."}`：自然语言 → 结构化目标（写进待办目标，不直接执行） | 已实现，需 `llm_budget` 有余量；解析失败如实报错不猜 |
| Mode 2 | 动作失败且 `config.autonomy.llm_mode >= 2` 时**只打标记** `needs_reflection`，由认知层（自由思考/反思）按自己的预算与频率决定是否真的调 LLM | 已实现；**自主循环自己绝不调 LLM**（去重窗口 = 退避时间） |

`config["autonomy"]["llm_mode"]`：`0` 纯确定性（默认）/ `1` 允许目标解析 / `2` 允许失败升级。

## 9. 前端如何观察和控制

右栏 **Auto** 标签页（`switchTab('auto')`，可见时跟随 12s 轮询）：

- **状态**：模式徽章（OFF/ON/PAUSED）+ 阶段（idle/acting/cooldown/unavailable/blocked）
  + 具体原因 + 具身环境 + 成功/失败/取消计数。
- **当前意图/行动**：类型 → 目标、得分、分量解释、依据节点、最近结果（含真实失败原因）。
- **候选与竞争**：本次 tick 的全部候选与得分明细（可解释，不是黑箱）+ 动机摘要。
- **待办目标**：一句话加目标（走 Mode 1）、删除。
- **自主日志**：每次决策/执行/结果/拒绝的原文记录。
- **控制**：`ON` / `STOP`（关模式+取消在飞）/ `PAUSE` / `RESUME` / `STEP`（手动跑一次决策）。

界面只显示后端真实状态，不编造"她正在思考"之类的文案。

## 10. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/autonomy/state` | 模式/阶段/意图/候选/目标/统计/日志 |
| POST | `/api/autonomy/mode` | `{"mode": "on"｜"off"}` |
| POST | `/api/autonomy/pause` / `resume` | 暂停 / 恢复 |
| POST | `/api/autonomy/stop` | 关模式 + 取消在飞动作 |
| POST | `/api/autonomy/step` | 手动跑一次决策（调试/验证） |
| GET/POST | `/api/autonomy/goals` | 目标列表 / 加目标（结构化或 Mode 1） |
| DELETE | `/api/autonomy/goals/<index>` | 删目标 |
| GET | `/api/autonomy/log` | 自主日志 + 候选历史 |

## 11. 安全约束（本阶段落实的边界）

- Bot 未连接 → 能力面为空 → 不执行任何动作（`embodiment_unavailable`）。
- 用户交互 → 让位 + 打断在飞动作（`user_active` 安静期，默认 20s）。
- **不做攻击**：能力面不含 `attack`（规范明确禁止"发现未知 → 自动攻击"）。
- **不碰用户资产**：`collect` 只接受安全材料清单；箱子/工作台/熔炉被显式拒绝。
- 失败不无限重试：同一 `(意图,目标)` 最多 3 次 + 退避；到上限后如实"放弃"。
- 不阻塞前端/对话：`tick()` 是纯函数式一步（无阻塞循环），最坏情况只做 HTTP 调用；
  在飞动作有 20s 超时兜底。
- 锁机制复用：被锁对象/能力不会被自主行动绕过。

## 12. 已知限制与下一步

1. **寻路取消是"尽力而为"**：`/stop_goto` 调 `pathfinder.setGoal(null)`，但已发出的
   移动指令可能要一拍才停；无逐帧中断。
2. **`rest` 不找避难所**：本阶段只是"停住不动"，不假装会生存策略。
3. **`explore` 是方向轮转**而不是地图探索（不做未探索区域建模）。
4. **`observe` 对无坐标目标退化为"看一眼周围"**（方块观察还没有目标坐标）。
5. **bot 生命周期仍由人工启动**：`ensure_bot_process=lambda: True` 未接管 `node bot.js`
   的启动/重启，掉线后会话节点也不会自动回 disconnected（既有缺口）。
6. **奖赏只到"情绪 + 表达抑制 + 图留痕"**：没有把结果回写行为倾向（`DispositionStore`
   的行为词表是对话行为，避免混淆），也未实现强化学习。
7. **下一步**：把 `needs_reflection` 标记接进自由思考；给 `explore` 加访问计数持久化；
   接 bot 进程自启/掉线侦测；把成功/失败序列作为触发器条件（如连续 3 次失败 → 通知用户）。
