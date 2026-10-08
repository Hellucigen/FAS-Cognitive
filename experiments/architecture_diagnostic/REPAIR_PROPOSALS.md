# REPAIR_PROPOSALS.md — 最小修复提案(默认不实施;§13 九条件逐条核对)

本审计**未修改任何生产文件**。以下提案按 §11 模板给出;实施与否
由作者决定。每个提案标注 §13 九条件的满足情况。

---

## P0-a 战役 ingest 的 result 截断(24 字符)

```text
Current behavior : harness.py ingest 的结果节点 id 取 result[:24]
                   ("结果:tool_missing:stone_picka" 丢尾)
Root cause       : 字面截断,无语义理由
Minimal change   : 上限提到 64 或取 result 首段(到 ':' 边界)
Unchanged        : 图谱 schema、边语义、激活管线
Verify           : E 任务单 seed 冒烟,结果节点 id 完整
Could regress    : 节点 id 变长→Top-8 文本略长(无语义影响)
§13 条件         : 1✓(战役层) 2✓ 3✓(1 文件) 4-7✓ 8✓(探针) 9✓
状态             : 可实施(战役层,非生产);影响仅信息完整性
```

## P0-b sandbox_lab self_goal_on 死钩子

```text
Current behavior : monkey-patch loop._sync_self_goal(不存在)→ no-op
Root cause       : 钩子名与实现脱节(实现真实在 action_system.set_goal_context)
Minimal change   : 改挂 self_graph.set_current_goal 模块属性(战役层已按此修)
Unchanged        : build_stack 其余装配
Verify           : F v6 遥测(已验证:5/5/5 vs 0/0/0)
Could regress    : 无(纯实验设施)
§13 条件         : 全部满足,且已在 capability_gap_v2 战役层完成;
                   sandbox_lab 本体建议只改注释指向战役层实现,避免
                   改动共享实验设施的历史语义。
状态             : 已在战役层解决;sandbox_lab 本体不动。
```

---

## P1-E 失败证据消费通道接进决策头 FAS 装配

```text
Current behavior : 失败→正权相关边(动作→结果);结果节点激活 0.000;
                   上下文呈现失败动作为高显著,失败事实不可见 →
                   均值 11.1 次重复失败
Root cause       : 战役 BeaconFASContext.ingest 未复刻生产事件框架的
                   负极性语义(config.failure_polarity=-0.8);demand
                   未传 action_result(生产有,app.py:4427)
Minimal change   : 二选一(均装配层,零生产修改):
                   (a) ingest 检测失败类结果(settlement failure/
                       tool_missing 前缀)→ 写 动作-[涉及]->实体 边
                       weight=-0.8 + 结果边 +0.9 + mark-activate
                       (严格复刻 EventFrameInjector 的生产签名)
                   (b) demand_block 调用补传 action_result=上一步
                       结算(与生产对齐)
Unchanged        : 扩散算法、写回晋升阈值、图 schema、成功路径语义、
                   C 的全部条件(成功结算不触发负极性)
Verify           : E 任务 n=10:预期 fail_gathers 11.1→≤3、pivot 8/10→10/10;
                   阴性对照:C/A/K 重跑 3 seeds 应逐位不变(无失败事件)
Could regress    : 若负极性泛化到非目标实体,可能伤 C 的重拾
                   (缓解:仅对结算失败写极性,生产语义本就如此);
                   I 的 FAS 条件若出现失败,上下文将含抑制边——
                   方向正确(阻止重复),不构成回退
§13 条件         : 1✓(装配缺口,机制已验证) 2✓ 3✓(1-2 文件,战役层)
                   4✓ 5✓ 6✓ 7✓(评价标准不变) 8✓(探针+冒烟) 9✓
状态             : **建议实施**(下一实验轮的第一项)
```

## P1-F current_goal 的激活消费(方案 A)

```text
Current behavior : 目标节点经 mark_active 进激活场,但工作集消费者
                   不存在;current_goal() 零读取者 → 自我模型 write-only
Root cause       : 读取侧缺位(架构缺口,非实现错误)
Minimal change   : 自主层候选生成/决策头装配处增加**统一 state consumer**:
                   每决策节拍读取 self_graph.current_goal(kg),若存在,
                   将其目标节点 activate_from_inputs(等价于"当前目标
                   状态进工作集")。无 goal 专用 if/else——任何写入
                   当前目标的状态自动获得同一通道;将来 self-graph 的
                   其他状态(偏好/信念)可复用同一 consumer 签名。
Unchanged        : 自主层自有 goal registry(双通道并存,不删除);
                   扩散算法;图 schema;LLM;评价标准
Verify           : (i) 遥测:目标节点进入 Top-k(确定性探针);
                   (ii) 行为:自由相(原 F 设计)ON vs OFF 首次可能
                   出现差异;C 重跑 3 seeds 应不变(目标节点本就经
                   先验边在场,增益路径重叠)
Could regress    : 目标压力增强可能加剧 E 的 inertia(与 P1-E 的
                   失败抑制方向相反)→ 两修复必须**成对**实施并
                   联合测量(E+C 同时跑),这本身就是论文的
                   trade-off 实验
§13 条件         : 1△(是架构缺口而非明确代码错误——条件 1 不完全
                   满足)→ **按 §13 只输出提案,不实施**。
                   实施前提:作者确认"当前目标应影响自主层注意"
                   属于期望语义而非设计争议。
状态             : patch proposal(待作者裁定)
```

## P1-I(不做,记录原因)

叙事式图上下文(episode 一等对象+时间排序序列化)= schema 变更
+ 序列化重写 → 违反 §13.4/§13.8 且属架构变更。**保留为 P2 边界**
与 future work("narrative-form graph context"与"非 LLM 图消费者"
两条路线)。

---

## 实施顺序建议(若作者批准)

1. P0-a(战役层,5 分钟)→ 2. P1-E + 阴性对照(30 run LLM + 9 run 零 LLM)
→ 3. P1-F 提案裁定 → 若批准:实施 + F 自由相重跑 + C/E 联合测量
(直接产出论文的 trade-off 曲线)。
