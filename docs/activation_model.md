# Fascinator 激活模型：有限注意资源约束下的认知激活传播

> 理论—实现重新对齐 2026-09-19b · 本文档是 activation/emission/diffusion
> 子系统的权威语义表述。代码注释与本描述冲突时，以此为准并修注释。

## 一、理论表述

**Fascinator 的 activation diffusion 是一种有限注意资源约束下的认知激活传播机制。**

它不是图上的物理能量守恒模拟。图中的 activation 数值表示"节点当前在认知系统中
的激活程度 / 注意相关程度"——是认知状态变量；使它稳定、有限、可解释的，是一组
**运行时注意资源约束**（emission budget / transfer / fire-once / source 登记）。
约束属于工程运行时，不属于对"认知"的本体论定义。

## 二、概念表

| 概念 | 语义 | 实现载体 |
|---|---|---|
| activation | 节点当前认知激活程度（注意相关度）。可累积、可衰减、可被抑制；不是守恒量 | `node.activation` |
| emission budget | 节点本轮可向外传播的注意资源 = activation × 配比 | `_emission_budget(node)` |
| emission ratio | 发射配比，**运行参数而非认知常数**：base + 语境调制项 | `config.emission_ratio`(0.5) + `activity_emission_bonus`(0.15) |
| emission transfer | 资源不无成本复制：发出的量从自身激活中扣除。防止 activation 在图上无成本翻倍（旧复制语义曾致 733 节点全亮） | `config.emission_transfer`(1.0) |
| activation source | 本轮激活的注入点，初始激活不属于上一轮图内资源 → 发射免扣 | `engine._sources: {node_id → type}` |
| source types | external_input / perception / internal_drive / memory_recall / goal / emotion（后两类预留） | `register_activation_source()` |
| fire-once | 一次 propagation cycle 内至多一次 **outward emission**。激活可持续累积，受限的只是发射动作——防图环重复自激 | `_fired_round` |
| relation weight | w̃ = w + bounded(r)：知识关系对传播的调制。运行时增量有界、**永不回写静态权重 w** | `_edge_w_eff()` |
| inhibition | 负权重 = 竞争性抑制；激发与抑制在同一更新规则内竞争，接收端不为负 | 负 `edge.weight` 传播 |
| decay | 每个传播步的激活消退（step-level）；max_depth 因此真正表示认知传播深度 | `decay_step(clear_fired=False)` in `diffuse_round` |
| CurrentActivity | "我现在正在做什么"的认知层状态（图一等节点，Haru-[当前活动]→） | `activity_tracker.py` |
| Action / Perception | 活动产生的具体行动 / 行动后的环境反馈（分层，不混同于活动） | `action_system.py` / `minecraft_perception.py` |

## 三、单步数据流

```
seed / source injection
   ↓
activation accumulation（可多条传入累加）
   ↓
emission budget = activation × (base_ratio + context modulation)
   ↓
relation-aware propagation: Δ = a · w̃ · β_space · gain_rel / Σw̃₊
   ↓
inhibition（负贡献同式传播，不占发射预算）
   ↓
resource transfer（发出的量从自身扣除；来源节点免扣）
   ↓
decay（每步 (1-λ)，按节点空间）
   ↓
下一 step（fire-once：每节点每轮只发射一次）
   ↓
attention selection（Top-K）
```

## 四、CurrentActivity 的注意力作用方式（§12 契约）

当前活动通过 **emission ratio 的语境调制**影响注意力，而不是通过激活注入：

- 语境集合 = 活动节点 + 它的类型/状态/目标 + 具身状态（当前Minecraft状态及槽位）；
- 语境内的节点发射配比获得 `activity_emission_bonus`（+0.15）——
  "正在挖铁时，铁相关的知识更容易被联想展开"；
- 作用范围是活动的 **1-hop 局部**：语境外节点配比不变，活动节点本身
  **不**向全图广播激活。

## 五、来源登记的责任划分

| 注入方 | source_type | 位置 |
|---|---|---|
| 对话输入 / 记忆抽取落图 | external_input | `app.py activate_from_inputs`（默认） |
| 驱动力刷新（CuriosityDrive） | internal_drive | `drive_engine._apply_drive_activation` |
| 好奇信号（需要确认/未知*） | internal_drive | `curiosity_engine.detect_cognitive_signals` |
| 情境偏置（行为倾向点亮） | internal_drive | `app.py [Attention] 情境种子` |
| 记忆再点火 | memory_recall | `continuous_cognition._reignite_memory` |
| 行动留痕 | memory_recall | `action_system._write_action_memory` |
| 环境事件（敌对出现/资源发现…） | perception | `autonomy._apply_event_effects` |
| 新未知对象的好奇信号 | perception | `minecraft_perception._unknown` |

**不要登记**：只做激活维持的路径（如感知在视注意力地板）——维持不是注入。

**生命周期**：对话回合边界由 `clear_anchors()` 清空（app.py 回合末）；
空闲期由 CC 循环每 tick 末清空——每个 tick 是自包含的传播周期。

## 六、预留的调制入口（不要一次性硬接）

`_emission_ratio_for()` 注释中预留：emotion_gain / context_gain / source_gain /
activity_gain 细分。论文的参数节点化路线（k/θ/λ/β 受情绪调制）按 §十五的约束
逐步迁移，先保持结构清晰。

## 七、调试

`config.emission_trace = true` 时，每个 diffuse_step 输出 DEBUG 级：

```
[EMISSION] 本步发射明细（top8 by emitted）:
  节点X(internal_drive) act=2.1 budget=1.05 emitted=0.87 remaining=1.23 inh=0.0
[PROPAGATION] 本步流入 top10（+激发/-抑制）:
  Iron +0.31 / Stone +0.18 / Zombie -0.07
```

## 八、不变式（由 tests/test_attention_model.py 锁定）

1. 来源节点发射免扣；普通节点发射即扣；
2. 语境节点配比 = base + bonus，语境外 = base；
3. 环路（A→B→C→A）场总量单调不增、被衰减排空——无指数放大；
4. 负权重传播构成抑制，接收端不为负；
5. 能量随传播深度单调递减（step-level decay）。
