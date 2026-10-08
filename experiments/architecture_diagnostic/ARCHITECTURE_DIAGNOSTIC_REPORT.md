# ARCHITECTURE_DIAGNOSTIC_REPORT.md — FAS 架构诊断与最小修复可行性审计

日期:2026-10-02。方法:只读代码审计 + 调用链追踪 + 三个零 LLM 确定性探针
(`audit_probes.py`,输出 `PROBE_OUTPUT.txt`)。生产代码为 ground truth;
全程零生产修改。

---

## 0. 十个问题的直接回答

**Q1 FAS 当前最严重的真实代码问题是什么?**
没有发现"会算错"意义上的经典 code bug。最接近的是两处小实现瑕疵:
(a) 战役装配 `harness.py` ingest 把 result 截断到 24 字符
(`结果:tool_missing:stone_picka`),损失信息;
(b) `sandbox_lab.build_stack` 的 `self_goal_on=False` 钩子指向不存在的
`loop._sync_self_goal`(已在本诊断前的 F v1 发现)。两者都在 harness 层,
不在生产核心。

**Q2 FAS 当前最严重的架构问题是什么?**
**感知/行动状态与决策上下文之间存在"写而不读"断层,且断层是成对的**:
生产把失败写入因果账本、把目标写入 Self 图,但决策上下文的构成
(working-set Top-k + demand block)没有任何通道读取这两类状态
(F:`current_goal()` 零读取者、唯一读者是前端 API;E:失败结果节点
激活 0.000、无负极性边,context 反而把失败动作呈现为高显著)。
这不是"忘记实现",而是"写入侧完整、读取侧缺位"的结构性不对称。

**Q3 E 的失败主要是 persistence 设计还是缺少 goal-revision consumer?**
**主要是缺少 consumer(E2),混合轻度 E1**。代码证据:失败证据
(结果节点)被写入图谱但激活 0.000(探针 P-E);战役装配 ingest
只写正权相关边、零负极性(生产事件框架机制存在但默认 OFF 且未接入
战役装配);生产 demand block 有 `action_result` 输入通道(app.py:4427)
而战役 harness 的 demand_block 不传。设计侧的轻度贡献:goal text
每步以相同措辞注入,需求压力恒定,无衰减机制——但若失败证据被
负极性编码并进入 context,模型有足够信息转向(history 条件 1.0 次
失败即转向证明模型本身会用失败信息)。

**Q4 F 是不是已经证明 self-model 本身无效?**
**没有。**

**Q5 还是仅仅证明 current_goal 当前没有行为消费者?**
**是,且证据比上轮更硬**:v6 消融经遥测验证(ON 5/5/5 vs OFF 0/0/0),
行为仍逐位相同;全仓审计确认写入路径完整
(action_system.set_goal_context → self_graph.set_current_goal →
Self-[当前目标]->目标 节点 + engine.mark_active 进激活场),而
读取侧只有 (a) `current_goal()` 访问器 0 调用者,(b) `get_self_model_summary`
唯一读者是前端 API `/api/self/model`,(c) 生产 prompt 渲染
(cognitive_context)不含自我/目标状态,(d) 自主层用自有 goal registry,
从不读 self-graph。正确表述:**Self model is currently write-only
with respect to behavior**。目标节点虽经 mark_active 进场,但
working-set 的语义消费者不存在。

**Q6 I 的失败是否已足够排除 k/字段不足?**
**是。** k=8/16/32 与 rich(动作→结果配对+实体共现结构块)全部 0/10,
共 0/30;而同一模型同一菜单读原始流 10/10。

**Q7 I 最终是否属于 representation/consumer architecture problem?**
**是,且表示研究进一步收窄了定位**:图谱里知识(L1)、关系(L2)、
激活(L3)都在,节点/边也有 `created` 时间戳——**事件顺序在数据层
部分可恢复,但当前 schema 不把"情节顺序"作为一等对象**(episode
身份完全不存在;同 obs 行的实体共现边把时间结构抹平;序列化器
丢弃 created 时间戳与边的关系语义)。原始流的优势不是"信息更多"
而是**时序叙事形式**——逐行 obs|action|result 让模型重演情节。
判定:Interface/Representation limitation(序列化+schema),
不是 code bug,也不是 task limitation(I5 能完成证明任务可解)。

**Q8 B 是否属于真实 credit-assignment design limitation?**
**是。** 90s 窗是 `experience.py` 显式数据表(ACTION_WINDOW_S,附设计
理由注释:8s 窗曾系统性筛空核心闭环,故对 gather/craft 类提到 90s),
MIN_SCORE 0.60 + ⅔窗资格线是配套语义;窗口推进/关窗由时间轴观察者
驱动,逻辑自洽。无补偿机制可接管长程信用(action_priors 是成功率
记忆非因果链;Hebbian 是共激活非归因)。**不要改 90s。**

**Q9 哪些值得现在修,哪些保留为论文边界?**
- 值得修(P1):E 的失败证据消费通道(事件框架接进决策头 FAS 装配);
  F 的 current_goal 消费(方案 A:激活侧)。
- 保留为边界(P2):B 的 90s 信用地平线;I 的时序叙事表示缺失;
  C 的机制重定性(见下)。
- 不修(P3):A/K 基线等效;token≠认知成本的表述。

**Q10 如果只能再改两个地方?**
1. **E:把生产事件框架的负极性编码接进决策头 FAS 上下文**
   (失败→极性槽边→抑制失败动作+把 tool_missing 事实送入 context)。
   这是对既有生产机制的装配接线,不新增启发式;直接检验
   "goal inertia 是 consumer 缺失"假设;预期不破坏 C(C 无失败
   场景,负极性不触发)。
2. **F:给 current_goal 一个最小合法读取者**(方案 A:在 demand/focus
   管线把 Self-[当前目标]->目标 节点纳入激活种子/工作集,统一
   state consumer,无 goal 专用规则)。与 C 机制天然耦合
   (C 已证明"链在工作集中→行为恢复"),且不触碰 E 的负极性维度。
   两个都不碰 I(P2 保留),不破坏任何现有时结论。

---

## 1. C 的机制重定性(重要,影响论文表述)

探针 P-C(PROBE_OUTPUT.txt):C 的 P3 条件下,工作集含
`物品:oak_planks`(act 5.0)、门控开;**零经历对照产生同样的
payload**(物品:oak_planks act 1.19,门控同样开)。

结论:C 的行为优势**不是**"persistent intention / CI 机制"(战役
harness 的 demand_block 不传 intentions;无 CI 生命周期;配置层
排除与经历都排除后仍成功),而是:

> **观测(橡木原木在场)→ 生产先验闭包边(物品:oak_log-相关->物品:oak_planks)
> → 扩散激活 → 关系工作集 → 门控与上下文显著度**。

即:架构设计的"观测触发的先验关系激活"。这与 LLM-history 的差异
是真实的(FAS 把关系链放进了注意;平文历史没有),但:
- 它依赖**先验播种不对称**(FAS 图含配方闭包,基线没有)——
  BASELINE_MATRIX 已声明过该 scope;
- 论文/报告若把 C 表述为"persistent intention"是**错误归因**,
  应改为"relational working-set salience via prior+activation"。
- fresh-fas 10/10 与此完全一致(当时已注明"同化不作对照")。

C 的 9/10 vs history 0/10 统计不变;机制标签必须改。

## 2. Fault Classification(摘要,详见 FAULT_CLASSIFICATION.md)

| Finding | PRIMARY CAUSE |
|---|---|
| C recovery | Interface/Representation(先验闭包+激活的观测触发路径)——机制标签需改,统计保留 |
| E inertia | Harness/Assembly(事件框架未接入战役 FAS 装配;demand 的 action_result 通道未传)+ 生产默认 OFF 的架构边界 |
| F self-model | Architecture(写而不读;读取侧仅诊断/UI) |
| I aggregation | Interface/Representation(schema 无情节顺序一等对象;序列化丢时序;叙事形式缺失) |
| B credit horizon | Architecture(有意语义边界,90s 数据表+MIN_SCORE 配套) |
| A equivalence | Task(短历史单目标下基线可达,非缺陷) |
| K compression | Interface(压缩是真实的,性能天花板掩盖差异) |

## 3. 修复优先级(详见 REPAIR_PROPOSALS.md)

- **P0(真实错误,harness 层)**:战役 ingest 的 result[:24] 截断;
  sandbox_lab self_goal_on 死钩子(文档性修正即可,该脚本为实验设施)。
- **P1(最小修复,建议做)**:E 事件框架→决策头装配接线;
  F current_goal 激活消费(方案 A)。
- **P2(保留为边界)**:B 90s;I 时序叙事;C 机制标签修正(改表述不改数据)。
- **P3(不修)**:A/K 等效。

## 4. SAFE TO FREEZE 判定

生产核心(图谱/扩散/写回/归因/持续认知)在审计中未发现影响已发表
结论的实现错误;capability_gap_v2 的全部负结果在正确归因后**仍然成立**
且更精确。论文可冻结;E/F 两个 P1 修复属于"后续工作"章节的可验证
承诺,不是发表阻塞项。

## 5. 最终结论格式

```text
FAS ARCHITECTURE STATUS

[CODE BUGS]
- harness.ingest result 截断 24 字符(战役层,信息损失,非生产核心)
- sandbox_lab self_goal_on 死钩子(loop._sync_self_goal 不存在;实验设施层)

[HARNESS / ASSEMBLY BUGS]
- 战役 FAS 装配未接入:事件框架(负极性)、账本、demand 的
  action_result/current_action/intentions 通道(生产均有)
- 沙盒装配缺 Self 节点(bootstrap_self 未调,F v3 已修复于战役层)

[INTERFACE / REPRESENTATION LIMITATIONS]
- serialize_context:丢 created 时间戳/关系语义/情节顺序;节点摘要
  不含 action→result 配对(-rich 补块 0/10 证明补结构不够)
- 图 schema 无情节/顺序一等对象;episode 身份不存在
- token 摘要 ≠ 认知成本(K 的表述边界)

[ARCHITECTURAL LIMITATIONS]
- 归因窗 90s = 信用地平线(B,有意语义边界)
- Self 图写入完整、行为读取缺失(F,write-only)
- goal text 恒定注入 = 目标压力无衰减通道(E 的设计侧分量)

[EXPERIMENTAL LIMITATIONS]
- A/K 任务对基线可解(等效非缺陷);菜单可见性泄漏配方(I 的 scope)

[SAFE TO FREEZE]
- 图谱/扩散/写回/归因/持续认知核心;capability_gap_v2 全部统计结论
  (C 机制标签改为 relational working-set salience);论文 v2 主张

[WORTH A MINIMAL REPAIR] — 两项均已实施并验证(2026-10-02,作者批准"全部修复")
1. E:事件框架负极性接入战役装配(fas-ef):失败重复 10.5→7.2,
   转向 7/10→9/10(Wilcoxon p=0.011)——部分恢复,E1/E3 残余保留为边界
2. F:autonomy._obtain_goals 统一 state consumer(config 默认关):
   consumer ON 派生目标 5/5、gathers 1.0→6.0——write-only 缺口闭合
(另:P0-a 截断修复;回归 108/108;默认关 payload 逐字节不变)

[DO NOT TOUCH]
1. B 的 90s 归因窗与 MIN_SCORE(语义边界)
2. I 的图 schema/序列化(为 benchmark 重写叙事表示=架构变更,超最小修复)
3. C 的先验闭包播种与统计(只改机制表述,不动数据与装配)

[RESEARCH QUESTION AFTER REPAIR]
修复 E/F 两个 consumer 后,真正的问题变为:
"当失败证据与当前目标都能进入决策上下文时,goal inertia 与
intention persistence 的权衡曲线落在哪?"——这直接决定论文
"不同形状的记忆"叙事的最终形态。
```
