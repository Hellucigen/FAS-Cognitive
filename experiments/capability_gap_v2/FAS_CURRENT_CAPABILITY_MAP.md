# FAS_CURRENT_CAPABILITY_MAP.md — 最终能力图谱(Step 11)

日期:2026-10-01。来源:capability_gap_v2 战役(A/B/C/E/F/I/K,Tier 1 全量)+
既有归档证据。判据:任务书 §22 三层证据、§24 失败分类、§27 状态词表。
n=10 配对种子(决策头族);B 机制 40 run 零 LLM;F 30 run 零 LLM。

| Capability | FAS | LLM-history | RAG | Mechanism evidence | Behavioral evidence | Comparative evidence | Status |
|---|---|---|---|---|---|---|---|
| Continual learning | A: 10/10 | A: 10/10 | A: 10/10 | 写回隔离 0 vs 13(既有) | 10/10 达成,决策均值 2.0 | vs fresh-fas 10/0(Holm p=0.0195);vs history/retrieval p=1.0 | **BASELINE-EQUIVALENT** |
| Delayed credit | B: 归因 0/32s 10/10,80s 10/10(inventory)/0/10(self),160s 0/10 | B: 未测(机制层) | — | 归因窗 90s=信用地平线(生产数据表) | — | — | **MECHANISM-ONLY**(窗口外不形成;无行为层延迟检验的正证据) |
| Persistent intention | C: **9/10** 重拾 | C: **0/10** | — | CI 形成→衰减(既有);表达级未达(既有) | 9/10(P3 闭卷自由相) | vs history/direct 均 9/0 discordant,McNemar p=0.0039 | **SUPPORTED(比较级,本轮唯一)** |
| Goal revision | E: 7/10@4.7,失败 10.5;**修复后 fas-ef: 9/10@3.1,失败 7.2(Wilcoxon p=0.011)** | E: 10/10@1.0,失败 1.0 | — | 事件框架负极性(生产存在,默认 OFF) | 有 | 修复显著但未到基线(E1/E3 残留) | **PARTIALLY SUPPORTED(修复轮)**:E2 装配缺失证实;残余为设计边界 |
| Autonomous subgoal generation | 未测(无 planner,生产事实) | — | — | 无 | 无 | 无 | **NOT DEMONSTRATED**(架构不含 planner;本轮不新增) |
| Self-model behavioral relevance | F v7(修复后):consumer ON 派生目标 5/5,gathers 1.0→6.0(failure 历史) | — | — | 写入+消费双路径均经遥测验证(goal_attention 开关) | **有**(ON/OFF 行为分离) | 生产默认关(边界保留) | **MECHANISM-ONLY(修复验证)**:write-only 缺口已闭合;幅度待 LLM 层复测 |
| Transfer(跨环境) | 未测(Tier 2,未达) | — | — | 账本迁移 13 vs 16 tick(既有,同域) | 同域有 | 跨环境无 | **PARTIALLY SUPPORTED**(仅同域账本,既有证据) |
| Long-horizon planning | I(FAS): 0-2/10(20 决策预算) | I(LLM): 10/10 | I: 8/10 | 闭包规划+先验(既有) | LLM 读原始经历流可完成 7 步链;FAS 上下文不能 | FAS < 基线(Holm p=0.041) | **FAILED(FAS 侧)**:决策头界面下 FAS 状态表达不了可执行的完整链,原始文本流反而可以 |
| Multi-experience convergence | I3: 0/10,I4: 2/10 | I5: **10/10** | I6: 8/10 | JCG 200/200;shared-hub 30/30+84 格扫描(既有,机制层) | FAS 行为层几乎不达成 | **I3 vs I5: 0/10 discordant,Holm p=0.041(基线优)** | **BASELINE-SUPERIOR-BY-LLM**(行为层;机制层证据不变) |
| Continuous cognition(100-cycle) | J(n=5,零LLM):100 拍节点+3.7%/边+4.6%,激活钳制 top1=5.0、均值 1.67→2.76,无失控 | — | — | 聚合 3→7;假设/晋升=0(重复饥饿) | 稳定性成立;巩固/复用未触发 | 无(消融为空操作) | **PARTIALLY SUPPORTED(稳定性维度)**;巩固/复用待重复情节协议 |
| History compression(K) | 923 tok/run,11/11 | 13290 tok/run(history200),11/11 | 1173 tok/run,10/11 | — | 成功率全条件持平 | 成本 14× 低于全历史;性能无差 | **SUPPORTED(成本维度)/ BASELINE-EQUIVALENT(性能维度)** |

## 状态词表使用统计
- SUPPORTED:1(压缩成本维度,附性能等效声明)
- BASELINE-EQUIVALENT:2(A continual learning;K performance)
- MECHANISM-ONLY:2(B delayed credit;I multi-experience convergence)
- PARTIALLY SUPPORTED:1(transfer,仅同域既有证据)
- NOT DEMONSTRATED:3(subgoal;self-model;continuous cognition 100-cycle)
- FAILED/F5:1(long-horizon at decision-head interface)

## 一句话总回答(任务书 §31)
在当前冻结架构与决策头界面下:FAS 的持久关系状态在"经历→可复用知识→行为"链上
**机制真实、行为可复现,但在本战役的短历史、单目标任务上,其任务成功率与
LLM-history/RAG 无可区分差异**;它当前可辩护的独特价值集中在**状态压缩带来的
token 经济性**(923 vs 13290 tokens,成功率相同),以及机制层证据(收敛、
延迟信用窗、再激活)——这些是 LLM 基线不提供可审计对应物的部分,而非性能优势。
