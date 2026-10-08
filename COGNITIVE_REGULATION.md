# 认知调节机制：锁 / 状态检测器 / 触发器

Fascinator 的三项基础认知机制。三者都不预设具体应用（不含 Minecraft、传感器、
游戏字段），只提供"任意可定义状态的对象"都能用的通用设施。

```
状态提交 / 轮询 ──► StateMonitor ──► 状态事件 ──► TriggerEngine ──► 认知事件
（确定性）          （状态+变化）    （去重）      （结构化条件）    （动作派发）
                                                                     │
                                              ├─ 激活图谱节点（受锁约束）
                                              ├─ 记录
                                              └─ 通知（llm_mode≥1 时可进认知层）
```

## 1. 模块与职责

| 文件 | 职责 | 存储 |
|---|---|---|
| `cognitive_locks.py` | 认知锁：CRUD、拦截判定、优先级组合、审计、过期 | `data/cognitive_locks.json` |
| `state_monitors.py` | 状态检测器：状态维护、变化识别、事件生成与去重、轮询接口 | `data/state_monitors.json` |
| `cognitive_triggers.py` | 触发器：结构化条件评估、冷却/once/优先级、触发记录、Mode 1 解析 | `data/cognitive_triggers.json` |
| `cognitive_regulation.py` | 协调器：把三者接成链路，动作派发，事件水位 | 无（状态在三个子模块） |
| `json_store.py` | 原子 JSON 落盘原语（同目录临时文件 → fsync → os.replace） | — |

装配点：`app.py` 启动时创建 `regulation`，并把 `regulation.locks` 绑到扩散引擎
（`engine.set_lock_registry`），把 `regulation` 挂到持续认知循环（`cc.regulation`）
用于检测器轮询。

## 2. 锁语义

锁控制"某个认知对象是否参与某个认知过程"。它**不是**删除、不是隐藏、不是负权边：

- 删除是数据操作；锁是控制机制，对象数据（id/label/weight/文本）一个字节都不改。
- 负权边是图谱关系（参与计算、产生抑制）；锁不改变对象语义。
- 隐藏是展示层白名单（`config.hide_temp_nodes` / `is_cognitive_visible`）；锁是
  认知过程级的、可解释、可审计、可解除的控制。

### 两种锁型

| 锁型 | 语义 | 扩散中的表现 |
|---|---|---|
| `blocking` | **阻止参与** | 不被种子激活、不发射、不接收 → 激活值恒为 0，下游拿不到信号 |
| `non_blocking` | **允许内部处理，限制认知输出** | 照常传播激活值（下游能收到），但不进入 Top-K / 活跃快照 / 回答区 |

被 blocking 锁定的对象在锁生效瞬间会清掉**残留激活**并移出活跃前沿——否则它凭
锁前的旧值还会出现在输出里，看上去像没锁住。激活是瞬时认知状态（不是节点原始
数据），清它不违反"锁不改动对象"。

### 作用范围 scope

| scope | 含义 | blocking 效果 | non_blocking 效果 |
|---|---|---|---|
| `diffusion` | 注意力扩散 | 不参与扩散（激活被拦/清） | 参与扩散，但不进输出 |
| `topk` | 回答区输出 | 不进 Top-K / 回答上下文 | 同左（输出型过程，两种锁型收敛） |
| `action` | 动作队列 | 不进可执行队列（=不会被执行） | 同左 |
| `all` | 以上全部 | 三者都拦 | 三者都过滤 |

未知 scope 会被存下但当**惰性**处理（不做任何拦截）——不假装支持没实现的钩子。

### 扩散介入点（7 处，全部有测试覆盖）

`diffusion_engine.py` 中的显式注释点：

1. **节点激活前**（`activate_from_inputs` 种子循环）——被锁节点不获得激活。
2. **边参与扩散前**（`activate_from_inputs` 边加成循环）——被锁边不进被加成集合。
3. **节点发射前**（`diffuse_step` 发射循环）——被锁节点不向外发射。
4. **边传播前**（`diffuse_step` 正/反向传播循环）——被锁边两个方向都不传。
5. **目标接收前**（`diffuse_step` 接收阶段）——被锁节点不因他人传播获得注意力。
6. **进入 Top-K 前**（`get_topk`）——non_blocking 锁的对象不进输出（节点与边）。
7. **扩散输出/动作队列**（`active_snapshot`、`_refresh_action_queue`）——快照过滤；
   `scope=action` 的锁把 procedural 节点排除在可执行队列外。

对原有数学定义的影响（§7.2 要求说明）：
- 原行为：`weight` 求和归一化、按空间 β 与关系类别增益传播、发射总量守恒。
- 介入方式：**从传播中摘除**被锁对象，其余参数（total_w 的定义、β、增益、
  fire-once、能量守恒）一律不变——不改公式，只缩小参与集合。
- 对循环/发散的影响：只会减少能量注入，不会引入新的正反馈；负权边处理不变
  （现状：负权在传播中被 `max(0.0, w)` 丢为 0，本次**未改动**，见第 7 节）。
- 兼容性：无锁时七处钩子都是空集合查找（`bool(reg)` 短路），回归 16 套件通过。

### 优先级与组合规则

同类型锁之间是**并集**：任一 enabled 且未过期的 `blocking` 锁命中，对象即被阻塞；
`non_blocking` 同理。`priority` 不改变并集语义（阻塞是保守行为，高优先级锁不能
"解锁"低优先级锁），只决定对外展示的原因取哪一条（priority 高者优先，同级取创建早者）。

### 生命周期

创建 → 启用/禁用 → 更新 → 删除 → 过期（`expires_at`，到点自动停用并留审计）→
目标被删除后成为**悬空锁**（保留并标记 `target_missing`，不静默消失）。
所有状态变化写入审计环（`history`，最近 200 条）。

## 3. 状态检测器

**状态与事件分离**：状态是"对象现在是什么"（`current_value`，持久化），事件是
"状态发生了什么变化"（`state_changed`，带 previous/current/delta/时间戳/事件 id）。
**相同状态重复提交不产生事件**（去重），只刷新观测计数与时间——这是 §5.3 的硬要求。

| 字段 | 说明 |
|---|---|
| `id` / `name` / `description` | 标识与可读说明 |
| `target_type` / `target_id` | 被监测对象（不强制是图谱节点） |
| `state_type` | `number` / `bool` / `string` / `enum` / `object` |
| `unit` / `schema` / `epsilon` | 单位；范围与枚举校验；浮点容差 |
| `current_value` / `previous_value` / `last_changed_at` | 状态 |
| `source` / `mode` / `poll_interval_s` | 数据来源与运行方式 |
| `enabled` / `observation_count` / `last_error` | 开关、观测计数、最近错误 |
| `changes` | 最近 50 条变化（含事件 id，可追溯） |

运行方式：`event`（外部事件提交，本阶段主用）/ `manual`（显式提交）/
`poll`（调度器定期查询——接口已就绪：`register_poller` + `due()`，**本阶段不内置
任何数据源**，不绑定具体应用）。

校验失败（类型不符/schema 越界）**不污染当前状态**，只记 `last_error`。
检测器禁用后不再接受观测、不再产生任何检测结果。

## 4. 触发器

条件必须**结构化**，运行时不交给 LLM 判断：

```
{"op": "gt"|"gte"|"lt"|"lte"|"eq"|"ne"|"changed"|"in"|"all"|"any"|"not",
 "field": "current"|"previous"|"delta"|"observation_count"|"confidence",
 "value": ..., "values": [...], "conditions": [...]}
```

`field` 只允许读事件/检测器里确实存在的量，不接受任意表达式求值。

| 生命周期要素 | 实现 |
|---|---|
| 触发时机 | `fire_mode=edge`（默认，条件"不满足→满足"的跃变才触发，上一轮条件结果持久化）/ `level`（为真即触发，靠冷却约束） |
| 冷却 | `cooldown_s`；冷却期内的触发请求被**明确拒绝并写入日志**（不静默） |
| 一次性 | `once=true`：触发后自动停用，保留记录 |
| 优先级 | `priority`：同轮多触发器触发时按降序派发 |
| 禁用/删除 | 禁用后不评估；删除后不残留任务（触发器不持有线程，评估由事件驱动） |
| 触发记录 | `log` 环（最近 200 条，含 `fired` 与 `skipped`+原因） |
| 错误可见 | 条件评估异常记 `last_error` + `error_count`，不中断引擎 |

动作类型：`activate_nodes`（激活指定图谱节点——走 `engine.activate_from_inputs`，
因此**自动受锁约束**，锁定节点不会被触发器强拉进激活）、`record`、`notify`。

## 5. LLM 介入边界

| 级别 | 位置 | 本阶段状态 |
|---|---|---|
| Mode 0 确定性 | 状态更新、变化判定、去重、条件评估、锁判定、触发调度、冷却 | **全部实现**，链路中没有任何 LLM 调用 |
| Mode 1 结构化辅助 | `POST /api/regulation/triggers/parse`：自然语言 → 候选触发器定义（不落盘，人工确认后再创建） | 已实现（`nl_to_trigger`，需要注入 LLM 函数；解析失败如实报错不猜测） |
| Mode 2 认知推理 | 触发事件带 `llm_mode=2` 标记，由认知层按需取用 | 接口已实现：`drain_cognitive_events(llm_mode_min=1)` 把事件交给回答层渲染【认知触发】块；**Mode 0 的事件永不进入任何 LLM 通路** |

取用即推进水位：旧事件不会在后续每轮反复进入 prompt。

## 6. API

统一 `/api/regulation/*`，成功 `{"success": true, ...}`，失败 `{"error": ...}` + 状态码。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/regulation/state` | 总览：锁 + 检测器 + 触发器 + 最近认知事件 |
| GET/POST | `/api/regulation/locks` | 列表（可按 target 过滤）/ 建锁 |
| GET | `/api/regulation/locks/check?target_type=&target_id=` | 锁状态查询：是否被阻塞/隐藏、生效的锁与原因 |
| GET/PUT/DELETE | `/api/regulation/locks/<id>` | 单条查询 / 更新（含 `enabled`）/ 删除（不删对象） |
| GET/POST | `/api/regulation/monitors` | 列表（含最近事件）/ 创建 |
| GET/PUT/DELETE | `/api/regulation/monitors/<id>` | 单条 / 更新（含 `enabled`）/ 删除 |
| POST | `/api/regulation/monitors/<id>/observe` | 提交状态观测 `{value, source?, confidence?}`；变化时自动评估触发器 |
| GET/POST | `/api/regulation/triggers` | 列表（含触发记录）/ 创建 |
| GET/PUT/DELETE | `/api/regulation/triggers/<id>` | 单条 / 更新 / 删除 |
| GET | `/api/regulation/triggers/log?n=` | 触发记录（含被冷却/once 拒绝的条目） |
| POST | `/api/regulation/triggers/parse` | Mode 1：自然语言 → 候选定义 |
| GET | `/api/regulation/events?llm_mode_min=&n=` | 最近的认知事件 |

前端：右栏新增 **Reg** 标签页（锁 / 检测器 / 触发器三个管理面板 + 触发记录），
节点详情面板新增 **LOCK** 行与 `BLOCK` / `HIDE-ONLY` 按钮（一键给选中节点加锁）。
全部复用既有 UI 组件与 `api()` 封装，未引入新框架。

## 7. 已知限制与兼容性说明

1. **负权边在传播中被丢弃**：`diffusion_engine.py` 用 `max(0.0, edge.weight)` 处理，
   `weight<0` 的边当前不参与传播、也不产生抑制——但关系本体（`relation_ontology`）
   与 LLM 抽取模板里都有"抑制"语义（`prompt_templates.py` 的 `weight:-0.9` 示例）。
   这是**既有落差**，本次未改动（改动它等于改扩散数学，需单独评估）。锁机制**不是**
   负权边的替代：锁是认知控制，负权边是关系语义。
2. **`dialogue_decision._inhibit_until` 未合并**：话题终止抑制期（进程级浮点时间戳、
   硬编码 600s、无解除 API、无审计）是时间型抑制，与对象型认知锁不同轴。本次保留
   其行为不变，未强行统一——未来可把"抑制期"表达为带 `expires_at` 的锁。
3. **三处重复的 INFRA 过滤**（`continuous_cognition` / `dialogue_decision` /
   `cognition_modes` 各自维护 `label in ("infrastructure","procedural","disposition")`
   集合）未收敛：锁是**追加**在既有过滤之上的第二道门，不替换它们。
4. **轮询型检测器无内置数据源**：`mode=poll` 需要外部 `register_poller` 注入取值函数，
   否则不会被采样（接口就绪，接线留给具体应用）。
5. **锁只拦"参与"，不拦"已产生的输出快照"**：新建 blocking 锁会清残留激活，但若某个
   消费者已缓存了快照，需等下一次输出刷新。

## 8. 后续扩展点

- 感知/工具数据源接入 `mode=poll` 检测器（一条 `register_poller` 调用）。
- 把 `_inhibit_until` 话题抑制表达为带过期的锁，统一审计与解除入口。
- 触发器条件扩展复杂表达式（当前刻意限制：可验证、可解释、不可执行任意代码）。
- 事件优先级与认知资源调度（`BudgetManager` 已有配额语义，可在派发处接入）。
- 锁的批量操作（按 scope/target_type 批量解除、按 pack 启停联动）。
