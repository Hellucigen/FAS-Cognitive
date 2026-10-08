# AUDIT_REPORT.md — Flat-Store / No-Activation 对照有效性检查
_2026-10-03,正式 run 前完成;检查 6(回归)见文末。_

## 1. 信息等价性(每 seed)
- seed 0: 事实数=13;FAS 缺动作节点=0;FAS 缺边=0;FAS 有而 C3 无的边=0
- seed 1: 事实数=13;FAS 缺动作节点=0;FAS 缺边=0;FAS 有而 C3 无的边=0
- seed 2: 事实数=13;FAS 缺动作节点=0;FAS 缺边=0;FAS 有而 C3 无的边=0
- seed 3: 事实数=13;FAS 缺动作节点=0;FAS 缺边=0;FAS 有而 C3 无的边=0
- seed 4: 事实数=13;FAS 缺动作节点=0;FAS 缺边=0;FAS 有而 C3 无的边=0
- seed 5: 事实数=13;FAS 缺动作节点=0;FAS 缺边=0;FAS 有而 C3 无的边=0
- seed 6: 事实数=13;FAS 缺动作节点=0;FAS 缺边=0;FAS 有而 C3 无的边=0
- seed 7: 事实数=13;FAS 缺动作节点=0;FAS 缺边=0;FAS 有而 C3 无的边=0
- seed 8: 事实数=13;FAS 缺动作节点=0;FAS 缺边=0;FAS 有而 C3 无的边=0
- seed 9: 事实数=13;FAS 缺动作节点=0;FAS 缺边=0;FAS 有而 C3 无的边=0

## 2. 状态隔离(C3/C4 Phase-3 不接触 Phase-1 原始流)
- C3 store 仅含解析后的事实记录(无 obs 原文、无库存快照字段);C4 store 仅含图对象。两者均为本进程内新建、不共享 PSA 运行残留;Phase-3 注入文本由 c3_select/c4_select 从各自 store 生成。
- C3 事实字段检查:0(应 0)

## 3. 泄漏检查
- C3 事实含答案句=0(应 0;事实字段为动作名/结果串,无解释性文本)
- C4 图与 C1 图构建一致(节点集+边数):True

## 4. Preflight 注入量级(C1 vs C3 vs C4)
- chars: C1=684 C3=443 C4=231;lines: C1=21 C3=17 C4=9
- gate 打开(C1/C3/C4 均含 oak_planks 痕迹):True/True/True
- 注:C3 因 obs 库存回显与朴素 recency 排序仍会带出 oak_planks 痕迹;C4 经 需要 边 1 跳可达 配方:oak_planks:hand:0——两者与 C1 的 gate 效果等价是**本对照的合法发现**,非实现缺陷。

## 5. 激活关闭验证(C4)
- C4 working set 中非 1 跳节点数:0(应 0)

## 结论:全部检查 **PASS**

## 6. 回归(检查 6)
- pytest tests/:**82 passed**
- scripts/run_all_regression.py:**108/108 PASS**(墙钟 229s)
- 新增代码仅位于 experiments/persistent_state_controls/ 与 PSA 目录;
  production 零修改。

## 运行后检查(7/8)
- 40/40 run 全部 harness-valid,无 INFRASTRUCTURE_FAILURE,无排除。
- C3 实现缺陷检查:未发现命名不一致导致的匹配失败(等价性审计 10/10 通过);
  但注意 C3 预注册的 "polarity 降序" 规则把失败事实排在最后,
  系统性弱化失败记忆的回溯——这是预注册规则本身的属性,
  在 INTERPRETATION 中作为限制讨论,不修改重跑。
