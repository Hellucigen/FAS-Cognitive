# F_FINDINGS.md — Self-model 数据流审计

## 1. 写入路径(已验证)

```text
入口 A:app.py:4092(多步指令开始)
入口 B:ActionManager.set_goal_context(goal_desc)   action_system.py:1171
  → self_graph.set_current_goal(kg, desc)           self_graph.py:395
      → set_goal(kg, desc)                          :351(创建 目标: 节点)
      → kg.add_edge(Self -[当前目标]-> 目标:, w=0.9)
      → engine.mark_active([goal_id])               action_system.py:1190
```

出口(任务结束):`_end_goal_context` → `clear_current_goal`(:1200)。

v6 遥测(30 run,零 LLM):ON S/M/E=5/5/5,OFF=0/0/0——写入与消融
均真实。历史版本缺陷(noop 钩子/缺 Self 节点/补丁泄漏/NameError)
见 PRECHECK_REPORT.md 附录 C,全部修复并留档。

## 2. 读取路径(全仓审计)

| 候选读者 | 结论 | 证据 |
|---|---|---|
| `current_goal()` 访问器 | **0 调用者** | 全仓 grep,仅测试 |
| `get_self_model_summary` | 仅前端 API | app.py:7257 `/api/self/model`(GET) |
| 生产 prompt 渲染 | 不含自我状态 | cognitive_context.py 无 自我/当前目标 |
| 自主层行动选择 | 不读 self-graph | loop.add_goal 自有 registry |
| 激活场 | 目标节点可被点亮(mark_active) | 无语义消费者把"Self-目标在场"映射为决策 |

## 3. 判定

- **PRIMARY:Architecture(write-only)**。不是"self-model 无效":
  写入真实、可消融、可遥测;缺的是行为读取者。
- 与 U2(语义写回无规划器消费)同构:本项目已有两处"写而不读"。
- 修复收益预测:F 方案 A(激活消费)后,目标节点会像 C 的
  物品:oak_planks 一样进工作集——C 已证明"链在工作集→行为跟随",
  这是方案 A 值得做的最强依据。

## 4. 数据流图

```text
am.set_goal_context
  ↓
self_graph.set_current_goal ──→ Self-[当前目标]->目标: 节点
  │                                    │
  └→ engine.mark_active ──→ 激活场(可点亮)
                                       ↓
                        ???(唯一读者 = /api/self/model 前端)
                                       ↓
                              [行为决策:无连接]
```
