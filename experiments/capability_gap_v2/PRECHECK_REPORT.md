# PRECHECK_REPORT.md — Step 7 baseline 公平性审计

日期:2026-10-01。零 LLM 可重复审计(`precheck_fairness.py`)。

| 检查项 | 结果 | 细节 |
|---|---|---|
| A 练习流逐字节一致(FAS vs history) | PASS | hash=ffcc50d08007.. n=6 |
| I1(A-only)经历流不含 stick 配方线索 | PASS | obs: near: oak_logx4, dirtx1; inventory: {}; hunger: 20 / action: gather_oak_log / result: got:oak_log
obs: near: oak_lo |
| 门控规则确定性(同 ctx 同 gate) | PASS | ['oak_planks', 'stick'] |
| no-writeback:经历不建边(exp_edges=0) | PASS | exp_edges=0 |
| FAS 全量:经历建边(exp_edges>0) | PASS | exp_edges=7 |
| direct:无 fc 无 store 写入 | PASS |  |
| 排除播种:oak_planks 相关先验边为 0 | PASS | n=0 |
| I facts 对称(全条件同一文本) | PASS |  |

## 已知不对称声明(正文口径)
1. FAS 图谱含生产配方闭包播种(排除项除外);凡目标配方被排除的实验(A/I/K),任务解不依赖该播种;其余配方闭包仍在 FAS 一侧——scope 注记。
2. LLM-history 输入侧无 token 上限,FAS 上下文固定 top-8:这是被研究的压缩价值本身,token 成本单独报告。
3. 菜单可见性本身泄漏部分配方(持有原料时 craft_X 出现在菜单):对 I 实验,经历子集间的严格隔离受此限制,已在结果解读中声明。
4. 实验 F 的 self_goal_on=False 消融钩子指向 autonomy 中不存在的方法(静默 no-op)→ 该消融为 F2(机制断连),F 的 self-model 行为相关性结论不受支持(见 EXPERIMENT_REPORT)。

## 附录:Smoke 阶段修复日志(§19 允许范围内的 harness 修复;全部发生在正式数据产生前)

| # | 发现(smoke) | 分类 | 修复 |
|---|---|---|---|
| 1 | run_menu_step 计算了 gate 但未写回执行器 → 闭卷门控失效,FAS 全灭 | F3 harness bug | ex.gate 每步更新 |
| 2 | 温度 0 下部分回复被 200 token 截断致 unparseable | F3 harness | max_tokens 200→400 |
| 3 | I 初版把 oak_fence 配方也排除播种 → 无任何条件能完成(目标配方无人教授) | F5 任务设计 | fence 配方对称公开于 facts;排除项仅 planks/stick |
| 4 | I 的 B 练习预置 4 木板并双次 craft → B 单独泄漏 planks 知识且菜单可见性本身泄漏配方 | F5 任务设计 | B 练习改预置 2 木板单次 craft;泄漏在解读中显式声明 |
| 5 | I 决策预算 10→12→16→20(模型在 7 步链上反复 wait/explore) | F5 任务限制 | 预算 20;若仍全灭则如实报告"决策头界面无法完成该链" |
| 6 | C 初版 P1 开卷但排除播种 → P1 无法完成,意图无从形成 | F5 任务设计 | C 不排除播种(考目标维持不考配方知识);facts 公开配方 |
| 7 | run_open_phase 无 success 字段 | F3 | 补 success 判定 |
| 8 | E 初版"移除方块致 gather 失败"不成立(方块不在近旁则菜单无此项,无失败可言) | F5 任务设计 | 改 iron_ore 工具门槛失败源(tool_missing 可重复触发) |
| 9 | F 自由相遥测读不存在的 _last_action → 全空 | F3 | 改用时间轴事件计数 |
| 10 | F 的 self_goal_on=False monkey-patch 目标方法在 autonomy 中不存在 | **F2 机制断连(生产侧,不修)** | 如实记录;F 结论降级 |

所有修复只触及 experiments/capability_gap_v2/ 内的 harness 代码;生产 FAS 零改动。

## 附录 B:门控规则 v2 修订(C 首轮分析后发现,正式 C/E/I 数据产生前)

C 首轮(n=10)出现 fas 9/9 vs history 0/10 的强对比,但分析发现混淆:
门控 v1 只看上下文文本,history 条件因上下文中无 "oak_planks" 字样而在
开卷 P1 就无法看到 craft 项(P1 0/10)——对比伪影而非能力差异。
规则 v2:craft_X 可见 ⟺ X 名出现在 facts∪state∪context 全文(全条件同规则)。
- C/E/I 在 v2 下全量重跑;v1 数据改名 raw_C_v1_gate_artifact.jsonl /
  raw_I_v1_gate_artifact.jsonl 留档不入结论。
- A/K 在 v1 下完成:其结论不依赖门控边界(所有记忆条件已 10/10 或 0 完成,
  v2 只会让基线更容易而非更难;A 的 fresh-fas 0/10 与 K 的 direct 0/11
  由 facts 闭卷性保证,v2 不改变 facts 文本)。为一致性,报告注明此不对称。
- 教训入档:门控/可见性规则必须在冒烟阶段做"基线可完成性"正检
  (给 history 一个开卷任务验证它能走到 craft 项),已加入本附录。

## 附录 C:F 消融修复全程(v1→v6,均为 harness 层;2026-10-02)

| 版本 | 发现 | 处置 |
|---|---|---|
| v1(首轮) | self_goal_on 钩子指向不存在的 loop._sync_self_goal → 静默 no-op | 归因 F3;结论作废(raw_F_v1_noop_ablation.jsonl 留档) |
| v2 | 消融改挂 self_graph.set_current_goal;但生产同步入口无人调用 → ON 也 0/5 | 定位入口 ActionManager.set_goal_context(raw_F_v2 留档) |
| v3 | 调用 set_goal_context 后边仍被丢弃:沙盒装配缺 Self 节点(build_stack 只建 "Haru",SELF_ID="Self") | 调生产公开 API bootstrap_self 对齐(raw_F_v3 留档) |
| v4 | ON 仅 1/5 存活:消融补丁未恢复 → 毒化同轮后续 ON 运行 | 模块层保留原函数,OFF 后恢复(raw_F_v4/v5 留档) |
| v5 | 探针 NameError(补丁误删 import)被 try 吞掉 | 修 import(raw_F_v5 留档) |
| v6(终) | **ON 同步遥测 5/5/5,OFF 0/0/0,行为逐位相同**;另证 current_goal() 生产零读取者 | 结论:写入路径存在且可验证,消费路径不存在 → 行为惰性是架构性的 |

## 附录 D:当前目标消费路径审计
`grep current_goal(` 生产代码(排除测试/定义/写侧)→ **0 个读取者**。
Self 图的目标状态被 action_system 写入,但自主层/动作选择/需求分析
均不读取。与论文 U2(语义写回无规划器消费)同构:写而不读。
