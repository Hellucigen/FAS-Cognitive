# Drive 重构 + 认知网络调制层（2026-09-20）

## 架构

```
既有系统读数（图谱 activation / 感知 / 失败账 / 预测基线 / 奖赏 / 激素 /
反思队列 / chat 间隔 / internal_state 需求水位）
        ↓ 采样器闭包（set_signal_provider，名字与旧版一致）
TensionField    张力场：每种张力 level∈[0,1]，非对称动力学（rise>fall=持续），
        ↓         聚合目标 = Σ coef×min(1, raw×scale)（config tension_sources）
DriveField      驱动力：张力亲和度加权聚合 + 侧向软竞争（乘性压缩，非互斥）；
        ↓         激素调制上升/消退速率（多巴胺=驱动更黏，皮质醇=张力变化更快）
NetworkField    认知网络：CEN/DMN 注册表 spec，连续值、slew 限幅+非对称
        ↓         rise/fall（惯性/迟滞）、软互抑 κ（允许双高混合态）
ModulationLayer 参数闭环：effective = clamp(baseline × Π(1+Σ effects))，
        ↓         EMA 平滑；signals = network/tension/drive/hormone/context
消费者          扩散深度·发射配比·点火阈值·回合衰减·param_gain·attention 宽度·
                检索 topk/再点火节奏·LLM 温度·mode 微调·预算软系数·
                行为竞争 margin/探索率·autonomy novelty 权重
```

`Tension → Drive → Network → Modulation` 由 `cognitive_field.py` 每拍编排
（CC 周期 ~10s 走动力学；回合内 `evaluate(force=True)` settle 到目标读数）。
四层互不知情，`cognitive_field` 是唯一装配点——新增张力/驱动/网络/参数
全部是 config 数据（见各段注释），代码零分支。

## 关键语义决定

1. **Drive 不执行行为**。唯一输出是 DriveField level×5 精确写回同名图谱
   节点（`CuriosityDrive`…，`mark_active` + `register_activation_source
   ("internal_drive")` 发射免扣），效力经扩散进入行为竞争（dialogue_decide /
   autonomy）。不存在 drive→LLM 直连；探索先行动（approach/observe），表达
   由竞争与表达检查裁决。
2. **多驱并存**。`dominant` 只是派生兼容字段（旧 API/前端契约），新核心
   逻辑不消费它。侧向 `_lateral` 默认空表——竞争强度由数据决定，不是
   winner-take-all。
3. **旧四驱 = 四个高层语义标签**。底层换成张力聚合（curiosity=探索张力族，
   social=联结张力族，learning=能力缺口族，consistency=未消化经历族）。
   稳态数值锚定在单测中校验（social>2.0 / learning>1.5 / consistency>2.2）。
4. **Demand ≠ Mode ≠ Network**。`cognitive_demand`（要多少资源）原样保留；
   网络态只做 mode ±1 档微调（下限 MODE1）、温度、预算软系数
   （`llm.budget_factor` 只缩放分钟级软限，daily 硬顶不变）。
5. **激素是调制器**。`dopamine/serotonin/cortisol/oxytocin` 经
   `set_signal_provider` 注册后自动走调制器通道（速率/亲和增益），
   不进任何 Drive 加数——`_HORMONE_NAMES` 路由，防第二套 Drive。
6. **网络不建扩散边**。CENetwork/DMNetwork 是 cognitive 空间 marker 节点
   （level×5 写激活，供观测与未来接边），效力主通道是参数调制。
7. **无平行状态库**。唯一新增落盘是 `data/tension_drive_state.json`
   （四层 level + 调制平滑值）；其余读数全部现读既有系统，采样缺位按 0。

## 消费点清单

| 参数 | 消费者 | 效果 |
|---|---|---|
| diffusion.max_depth / min_spread / emission_ratio / inter_round_decay / param_gain | `diffusion_engine`（`_mod` 读点，无调制层回退 config） | CEN 聚焦浅扩散、DMN 联想深扩散；param_gain 收编旧 arousal/stress 通路 |
| attention.width_scale | `cognition_modes.attention_context`（MODE_CONTEXT_BUDGET 由此兑现，此前是死表） | 工作记忆宽度随 mode+网络 |
| llm.temperature_answer/expand | `nlp_processor._chat/_expand`（backend.with_temperature 克隆） | 发散/收紧的措辞温度 |
| llm.mode_nudge / llm.budget_factor | `app.py` mode 门 + `BudgetManager.can_call(budget_factor=)` | 网络微调资源档位 |
| behavior.explore_margin / exploration_rate | `dialogue_decision`（打断门槛、探索候选乘子）、`autonomy._score_action`（novelty 权重） | 专注时行动连续性高、想时更爱探索 |
| retrieval.reignite_every | `continuous_cognition` tick | DMN 高 → 记忆再点火更频繁 |

## 调参入口（config.py）

- `tension_sources.<name>`: rise/fall/enabled；components 在
  `cognitive_field.DEFAULT_TENSIONS`（新增分量=加一行 dict）
- `drive_field.drives.<name>`: rise/fall/affinities（可负）；`_lateral` 竞争表
- `networks.field.<NET>`: inputs 权重表（`tension.*`/`drive.*`/`context.*`/
  `base`，可负）、rise/fall/slew；`_lateral` κ
- `modulation.params.<name>`: baseline/baseline_from/min/max/alpha/effects 表
- `cognitive_field.action_tendencies`: 倾向权重表

## 兼容契约（全部保留）

`DriveEvaluator(kg, config)`、`evaluate()/get_drive_state()/get_all_drives()/
get_dominant_drive()/set_signal_provider()/bootstrap_drives()/DRIVE_NODE_IDS/
CURIOSITY_SIGNAL_NODES`；evaluate 返回 `drives[{drive,drive_id,activation,
factors,components,(curiosity: signals)}]/dominant/timestamp`；四 Drive 节点
名与激活精确写回；12 个 provider 名；`GET/POST /api/drive` 旧字段；
`/api/autonomy/state.drives`；三条图通路边
（未知*→CuriosityDrive→行为:探索）；internal_drive 发射免扣。
`get_drive_state()` 新增 `tensions/networks/action_tendencies/modulation` 段。

## 观测

- 回合尾日志：`[CognitiveField] tension=… | drive=… | CEN=0.60 DMN=0.10 |
  depth=2 ratio=0.45 temp=0.62 bf=1.25 nudge=+0`（`explain_compact`）
- 完整五段快照：`field.explain()`；升级结构：`GET /api/drive`
- 竞争可解释：dialogue_decide `factors["探索落选"].margin` 显示调制后的门槛

## 已知边界 / 后续

- 网络态参数粒度：CC 每拍 `refresh_modulation`（2.5s），场推进 ~10s
- `unexpected_event`/`uncertainty` 张力的图采样是占位实现（读旧链节点），
  待接入 timeline COGNITIVE_EVENT 率
- Salience/Memory/Social 网络 = 往 `networks.field` 加 spec 即可
- 落盘 Drive 节点 `status: planned` / `graph_space: semantic` 的旧存档不一致
  由 `bootstrap_drives()` 幂等修正（无需迁移脚本）

测试：`tests/test_cognitive_field.py`（四层动力学+消费闭环）；
`tests/test_drive_system.py` / `test_curiosity_refactor.py`（旧契约护栏）。
