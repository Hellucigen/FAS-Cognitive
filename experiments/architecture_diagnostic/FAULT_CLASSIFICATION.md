# FAULT_CLASSIFICATION.md — 最终故障分类

规则:每个 Finding 只允许一个 PRIMARY CAUSE;SECONDARY CONTRIBUTOR
可并列;证据不足写 INCONCLUSIVE。分类码:CB=Code Bug,HB=Harness/Assembly
Bug,IF=Interface/Representation,AR=Architecture Design,TK=Task。

| Finding | Reproducible | Code Bug | Harness Bug | Interface | Architecture | Task | Confidence | PRIMARY CAUSE | SECONDARY |
|---|---|---|---|---|---|---|---|---|---|
| C recovery(9/10 vs 0/10) | 是(探针 P-C 双向复现) | 否 | 否(先验播种已声明) | **是** | 否 | 否 | 高 | **IF:观测→先验闭包边→扩散→工作集显著度**(零经历对照产生同 payload) | AR:先验播种是设计选择;任务书所称"persistent intention"不成立 |
| E inertia(11.1 vs 1.0 次失败) | 是(探针 P-E:失败节点 act=0.000,负边=0) | 否 | **是**(战役装配未接事件框架/账本;demand 未传 action_result) | 是(失败以正权呈现) | 部分(生产默认 OFF=有意边界;goal 恒压无衰减) | 否 | 高 | **HB:失败证据的消费通道在战役装配缺失**(生产机制存在且已验证 @6 vs @9) | E1 轻度(goal 恒压);E3(±0.05 先验即便接入也弱) |
| F self-model(ON/OFF 行为同) | 是(v6 遥测 5/5/5 vs 0/0/0) | 否 | 否(消融链 v1-v5 缺陷已修复并验证) | 否 | **是**(写而不读;唯一读者=前端 API) | 否 | 高 | **AR:current_goal 无行为消费路径** | IF:cognitive_context 渲染不含自我状态 |
| I aggregation(0-2/10 vs 10/10) | 是(探针 P-I;rep 研究 0/30) | 否 | 否 | **是** | 是(schema 无情节顺序一等对象) | 否(I5 可解) | 高 | **IF:序列化丢时序/关系语义;叙事形式缺失** | AR:episode 结构非一等对象是 schema 设计;TK:菜单泄漏(次要) |
| B credit horizon(160s 0/10) | 是(40 run 零 LLM) | 否 | 否 | 否 | **是**(90s 数据表+MIN_SCORE 语义,设计注释在案) | 否 | 高 | **AR:归因窗=有意信用边界** | — |
| A equivalence(全 10/10) | 是 | — | — | — | — | **是**(短历史单目标可解) | 高 | **TK/F7:基线等效非缺陷** | — |
| K compression(923 vs 13290 tok) | 是 | — | — | 是(压缩=接口收益) | — | 部分(性能天花板掩盖差异) | 高 | **IF:状态压缩是接口层收益** | TK:任务太短未触及历史退化区 |

## INCONCLUSIVE 项
- 无。所有 Finding 均达到"高"置信度(有代码证据+探针/遥测复现)。

## 与上一轮报告的修正
- F:上轮记 F2(机制断连)→ 本轮改 **AR(write-only,消融已验证)**。
- C:上轮记 SUPPORTED(persistent intention)→ 本轮机制标签改
  **IF(relational working-set salience)**;统计与比较结论不变,
  但"intention"归因被零经历对照证伪。
- E:上轮记 E5 混合 → 本轮 PRIMARY 收敛为 **HB(装配缺失)**,
  依据是生产侧三条通道(事件框架/账本/demand.action_result)
  全部存在而战役装配零接入。
