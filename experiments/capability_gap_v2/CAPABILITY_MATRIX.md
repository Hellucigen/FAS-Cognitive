# CAPABILITY_MATRIX.md — FAS 生产系统审计(Step 1–2)

日期:2026-10-01。审计原则:**不按文件名判断**——每项机制经真实调用探测
(`PRODUCTION_AUDIT.json`,13/13 项运行时探针通过),再叠加既有战役证据。
冻结架构承诺:本轮零 FAS 核心修改;消融只走参数级开关。

## 机制层审计(运行时探针结果)

| 机制 | 代码存在 | Runtime connected | 运行时探针结果 | 既有实验证据 |
|------|---------:|------------------:|----------------|--------------|
| graph persistence | ✓ graph_model | ✓ | save/load 往返一致(节点+边+激活值) | 全部战役依赖 |
| experience write-back + attribution | ✓ CausalLearner + sandbox_lab(writeback_on) | ✓ | 消融开关为参数级(kg=None 即断写回);beacon 装配含生产建图路径 | 写回隔离 0 vs 13 节点;15 晋升分类 |
| spreading activation | ✓ diffusion_engine | ✓ | 生产装配下 oak_log 种子 4 步点亮 oak_planks/lit_count=10 | 四条件 61.5/16.6/45.8/17.2;JCG 200/200 |
| scalar modulation | ✓ update_param_modulation | ✓ | gain: 中性 1.0 / 高唤醒 1.3 / 高压力 0.85(实测) | 剂量 49.8/52.5/57.4,禁用逐位中性 |
| persistent intentions(CI) | ✓ continuous_cognition | ✓(构造+tick) | form 0.55 / express 0.78 / discard 0.10;表达级历史上未达(0.46<0.78) | D-CI:形成→竞争→衰减 3–4/run |
| self-model | ✓ self_model.SelfMemoryUpdater | ✓(app.py 接线;line 415) | manager+process_turn 在;**无行为消费路径的机制级证据** | 自我图存在性;行为级未测 |
| drives / curiosity | ✓ drive_engine / curiosity_engine | ✓ | 好奇状态键齐全(unknown_nodes/relations 等) | 自主层探索行为(实况) |
| event-frame | ✓ action_system + config | ✓(**生产默认 enabled=False**) | 失败极性 -0.8、结果边 +0.9 等参数在位 | 实验装配 @6 vs @9、@15 vs @17 |
| ledger | ✓ internal_state(奖励账本 deque+持久化) | ✓ | gap_trials 试验账本经 config.prior | 迁移 13 vs 16 tick,首动作翻转 5/5 |
| routing/demand | ✓ FASContext.demand_block | ✓ | demand/gap/routing 全部在场;nlp_render_cognitive_context=True | 路由 v2 520/520(探索性);v1 已作废 |
| continuous cognition lifecycle | ✓ ContinuousCognition | ✓ | tick_once 无异常;阈值参数在位 | D-CI;100-cycle 稳定性未测 |
| forgetting | ✓ EpisodicBuffer(capacity=100) | ✓ | cleanup/promotion/find_* 在位 | 短程整合层(P3:只晋升标签) |
| inhibition(负边) | ✓ diffusion_engine | ✓ | 正边 0.625 → 加负边 0.0(实测抑制生效) | 负边检验 17.41 不变 |
| memory reuse(激活继承) | ✓ save/load+activate | ✓ | 激活值跨保存/加载继承(R2=0.625 保留,实测) | G0/G1/G2:4/12 vs 0/8 vs 0/0 |

## 能力层矩阵(任务书 §3 要求格式)

| Capability | Mechanism exists | Runtime connected | Existing evidence | Proposed new test |
|---|---|---|---|---|
| Continual learning | 部分(写回+激活继承) | ✓ | 写回隔离/再激活(机制);信标行为收益(带基线);多阶段后仍可用=未测 | **实验 A**(Tier 1) |
| Delayed credit | 归因窗 8–120s(短) | ✓ | 无延迟操纵实验 | **实验 B**(Tier 1) |
| Persistent intention | ✓(CI 形成→衰减;表达未达) | ✓ | D-CI(机制);长期目标维持行为=未测 | **实验 C**(Tier 1) |
| Goal revision | 部分(事件框架抑制) | ✓(默认关,装配可用) | 首动作翻转 @6 vs @9(装配层);完整"放弃-改道"未测 | **实验 E**(Tier 1) |
| Autonomous subgoal generation | ✗(无 planner) | — | 无 | 实验 D(Tier 2,预期 NOT DEMONSTRATED) |
| Self-model | ✓(自我节点/信念) | ✓(接线) | 存在性;**行为因果相关性未测** | **实验 F**(Tier 1) |
| Transfer | 部分(账本迁移) | ✓ | 13 vs 16 tick(同域迁移);跨环境=未测 | 实验 G(Tier 2) |
| Long-horizon planning | 部分(闭包规划+先验) | ✓ | 14 步链 315 tick(需任务先验);步数梯度未测 | 实验 H(Tier 2) |
| Multi-experience convergence | ✓(JCG 机制) | ✓ | 机制级充分(JCG 200/200;vs flat 被拒;shared-hub 30/30+84 格扫描);**行为级缺失** | **实验 I**(Tier 1,最关键) |
| Continuous cognition | ✓(生命周期) | ✓ | 100-cycle 长程稳定性=未测 | 实验 J(Tier 3) |

## 审计结论(诚实基线)

1. **机制层覆盖率高**(14/14 探针通过),但机制存在 ≠ 行为相关 ≠ 相对 LLM baseline 有增益——这正是本轮要量化的三层。
2. **已知短板先行声明**:relation direction(前向两跳结构性为零,论文 S9)、语义写回无规划器消费路径(U2)、表达级意图未达、事件框架生产默认关。这些不是本轮要修的,是要量化的。
3. **消融开关全部参数级**:sandbox_lab.build_stack 提供 diffusion_on/causal_on/prior_on/goal_on/writeback_on/self_goal_on/gap_on + eventframe 装配;决策头侧有 no-diffusion/no-demand/no-structure(路由战役已验证)。零新增认知代码。
4. 审计自身修复说明:探针脚本两处笔误(label、参数)在探测过程中修正,生产代码零改动;早期一次传播探针用错装配(v2 FASContext 缺配方闭包,信标战役已记录在案)已改用生产建图路径装配(BeaconFASContext)。
