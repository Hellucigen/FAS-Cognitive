# E_FINDINGS.md — Goal Revision 消费链审计

## A. 为什么旧目标保持高压力(逐机制定位)

| 机制 | 状态 | 证据 |
|---|---|---|
| goal text 注入 | 每步以相同措辞进 BASE_PROMPT 的 Task 字段 | run_experiment.exp_E;无衰减/失效逻辑 |
| demand/gap | goal 文本驱动 knowledge/language 维;无 goal-validity 检查 | cognitive_demand.py(analyze 入口无失效参数) |
| 失败证据(结果节点) | 已写入图谱但激活 0.000;ingest 只点亮实体节点 | 探针 P-E:`结果:tool_missing:stone_picka act=0.000` |
| 负极性抑制 | **图中负边 = 0**;战役 ingest 只写正权相关边 | 探针 P-E |
| 事件框架 | 生产存在(`_write_event_frame` action_system.py:1033,负极性 -0.8)但默认 OFF 且未接入战役装配 | config.py:1941;grep 计数 routing/beacon/campaign harness = 0 |
| 账本 ±0.05 | 战役 BeaconFASContext 无账本组件 | 装配代码审查 |
| goal invalidation | 不存在(无任何"目标不可行"状态机) | 全仓无相关符号 |

## B. 三种装配的事件框架接入差异

| 装配 | 事件框架 | 账本 | demand.action_result |
|---|---|---|---|
| 生产(app.py) | 结算原生写回,**默认 OFF**(config.experience_eventframe.enabled) | 有(奖励账本) | **有**(app.py:4427) |
| 沙盒自主环(sandbox_lab) | EventFrameInjector + on_settled 链(装配层,可开) | 有(CausalLearner) | 自主环不经 demand_block |
| 战役决策头(BeaconFASContext) | **无** | **无** | **无**(只传 text/kg/engine) |

已验证的事件框架行为效力:论文实验 D(@6 vs @9 首动作翻转、
@15 vs @17 真实回执)——机制有效,只是不在战役装配里。

## C. 分类判定

**E2(missing consumer / assembly defect)= PRIMARY**:
- 模型本身会用失败信息(history 条件 1.0 次失败即转向,10/10);
- 生产三条失败通道存在且(事件框架)已验证有效;
- 战役装配零接入 → FAS 条件的上下文把失败动作呈现为高显著
  (act 3.03)而失败事实不可见(act 0.000)。

**E1(genuine inertia)= SECONDARY**:即便接通,goal text 恒压与
"persistence-first"设计仍会保留部分黏性(C 的优势同源);
±0.05 先验量级太弱(E3)。

修复验证实验设计见 REPAIR_PROPOSALS.md P1-E。
