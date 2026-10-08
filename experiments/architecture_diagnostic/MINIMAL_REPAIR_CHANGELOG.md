# MINIMAL_REPAIR_CHANGELOG.md — 最小修复实施记录(2026-10-02)

作者批准"全部修复"后实施。§13 九条件逐条核对见 REPAIR_PROPOSALS.md。
回滚方式见各项与 REGRESSION_RISKS.md。

## P1-F:统一 state consumer(生产文件 ×1)

**文件**:`autonomy.py` `_obtain_goals()`(+26 行,config 门控)

```text
Current behavior : self-graph 当前目标写入完整但行为零读取(F write-only)
Root cause       : 读取侧缺位(架构诊断 F_FINDINGS)
Minimal change   : _obtain_goals() 末尾增加统一 consumer:
                   config self_model.goal_attention(默认 False=生产零变化)
                   开启时读 self_graph.current_goal(kg);若目标文本引用
                   已知可获取物品(物品:X 节点且 X ∈ desc),派生等效
                   obtain 目标(source 同 OBTAIN_GOAL_SOURCE,registry
                   已有同目标则不重复)
Unchanged        : registry 语义;扩散/图 schema/晋升阈值/LLM/评价标准;
                   默认配置下生产行为逐字节不变(108/108 回归 + 探针 diff)
Verify             : F v7(见 REPAIR_TEST_RESULTS)
Rollback         : config 保持 False 即完全失效;或移除该 try 块(单点)
```

**文件**:`experiments/capability_gap_v2/run_f_selfmodel.py`(实验层)
- v7:被试变量改为 consumer 开关(两条件都同步目标);自由相清空
  registry obtain 目标(实验设置),使 consumer 成为唯一目标来源。

## P1-E:失败证据的事件框架负极性接入(战役装配层)

**文件**:`experiments/capability_gap_v2/harness.py` `CampaignFASContext.ingest`

```text
Current behavior : 失败→正权相关边;结果节点激活 0.000;失败动作高显著
Root cause       : 战役装配未复刻生产事件框架负极性签名(E_FINDINGS)
Minimal change   : ingest 检测失败前缀(tool_missing/not_found/
                   missing_ingredients/needs_crafting_table/not_in_inventory,
                   即 action_system 结算原因)→ 写
                   事件-[涉及]->物品 边 w=-0.8、事件-[结果]->结果节点 w=+0.9、
                   事件节点点火 +0.8(上限 3.0,mark_active)——
                   参数逐项等于 config.experience_eventframe 生产值
Unchanged        : 成功路径逐字节不变;开关 ef_context 默认 False;
                   生产事件框架代码零改动(仍默认 OFF)
Verify           : E2(fas-ef vs fas vs history vs direct,n=10)
Rollback         : 移除失败分支或保持 ef_context=False
```

## P0-a:结果节点 id 截断 24→64(战役装配层)

**文件**:`harness.py` ingest。语义:保留完整结算原因字符串。
影响:仅失败类节点 id 变长(阴性对照确认成功路径 payload 不变)。

## P0-b:sandbox_lab 死钩子

不改 sandbox_lab 本体(共享实验设施,历史语义保留);
capability_gap_v2 战役层已按正确挂点实现(F v6/v7)。

## 生产修改汇总

| 文件 | 行数 | 默认行为 |
|---|---|---|
| autonomy.py | +26 | 不变(goal_attention 默认 False) |

回归:pytest 82 passed;scripts/run_all_regression.py **108/108 PASS**;
audit_probes 默认关 diff = 仅 P0-a 一处(预期)。
