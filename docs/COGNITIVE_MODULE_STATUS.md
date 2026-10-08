# FAS 认知模块状态台账

> 2026-10-08。原则：**代码存在 ≠ 实验验证成功**。每个状态必须能给出实验证据路径；
> 没有对照实验的机制一律标 `untested`，不得因"已写进运行时"而升级。
> 状态词汇：`validated` / `partially_validated` / `failed` / `bugged_experiment` / `untested` / `hypothesis`。
> 证据源：`research_audit/FINAL_DIAGNOSIS.md`、`research_audit/EXPERIMENT_VALIDITY_MATRIX.md`、
> `research_audit/FAILURE_REGISTER.md`、`FAS_Research_Experiments/reports/PAPER_CLAIM_AUDIT.md`、
> `experiments/*`、`FAS_Research_Experiments/reports/FINAL_EXPERIMENT_REPORT.md`。

| Module | 当前实现 | 实验状态 | 是否核心 | 是否可独立替换 |
|---|---|---|---|---|
| Unified KG | `graph_model.py`(Node/Edge/KnowledgeGraph，仅 stdlib) | `partially_validated` | Yes | Yes |
| Spreading Activation / Diffusion | `diffusion_engine.py` + `cognitive_field.py` | `partially_validated`（机制成立；增量价值被否） | Yes | Yes |
| Relational/Episodic Memory | `episodic_buffer.py`、`experience.py` Timeline | `partially_validated`（访问层 VALID；自动晋升是死路径） | Yes | Yes |
| Write-back / Learning | `experience.py:767 promote_to_kg`、账本迁移 | `partially_validated` | Yes | Yes |
| Goal / Drive | `drive_engine.py`、`drive_field.py`、autonomy 目标链 | `partially_validated` | Yes | Yes |
| Curiosity | `curiosity_engine.py`（LLM 提问 866） | `untested` | Yes | Yes |
| Self Model | `self_model.py`、`self_graph.py`、goal_attention consumer | `partially_validated`（10-02 起首个行为差分） | Yes | Yes |
| Action Selection (autonomy) | `autonomy.py`(零 LLM tick)、`action_system/space/concepts/intents` | `partially_validated`（事件框架沙箱 5/5，生产默认关） | Yes | Yes |
| Action Decision Head (语言/意图→行动) | `dialogue_decision.py`、`action_resolver.py`、路由战役 | `failed`（主结果不支持 FAS 优于基线） | Yes | Yes |
| Modulation（标量增益） | `modulation.py`、`modulator_subgraph.py`、C-emo | `validated`（限定：仅标量 gain 通道） | Yes | Yes |
| Cognitive Regulation / Locks | `cognitive_regulation.py`、`cognitive_locks.py` | `untested` | Yes | Yes |
| Internal State（需求/激素/性格） | `internal_state.py` | `partially_validated`（间接：S5/C-emo 经它供数） | Yes | Yes |
| Disposition Store | `disposition_store.py`（人格=边权） | `untested` | Yes | Yes |
| Personality Baseline / Mood | `personality_baseline.py` | `untested` | No（表演性偏多） | Yes |
| Reflection | `reflection_engine.py`（LLM 341，代码把关候选） | `untested`（claim #15/#17 NOT_TESTED） | Yes | Yes |
| Narrative/Memory Extract (LLM) | `nlp.extract_assertion_graph` 等 app 侧 | `hypothesis`（依赖 LLM 质量，无对照） | No（属语言输入分析） | 是（Companion 侧） |
| Hebbian F 通道 | diffusion 权重漂移 | `failed`（亚阈：+0.074 机制漂移、行为持平，如实负结果） | No | — |
| Prospective Memory（前瞻） | 未实现 | `hypothesis`（claim #13 NOT_IMPLEMENTED） | Yes | — |
| Language Realization | LLM + 15 模板（`nlp_processor`） | 非 FAS 认知主张（Companion 临时方案） | No | 是 |
| Dynamic Revalidation（实机） | autonomy 重验链 | `partially_validated`（mechanism only；Case A/B unavailable、C not observed） | Yes | Yes |

---

## 逐项证据说明

### 1. Unified KG — partially_validated
- 结构与持久化被反复审计（3648 节点/9088 边）；空图覆写事故（2026-09-13）后加三重守卫，
  `test_pers01_guard.py` 8/8（`research_audit/FAILURE_REGISTER.md` L1-PERS-01）。
- **负面事实必须记录**：约 40% 边是"涉及"共现 fabric（FAILURE_REGISTER L2-RD-01），治理方案
  （`research_audit/_audits/design_fabric_governance_A3.md`）尚未裁决。
- 无"KG 作为知识表征优于其他表征"的行为实验——结构载体成立，表征价值未证。

### 2. Spreading Activation / Diffusion — partially_validated
- **机制层 SUPPORTED（最强证据）**：`research_audit/mechanism_falsification/MECHANISM_RESULTS.md`
  （663 runs、纯标准库参考实现、预注册）——H_join SUPPORTED（JCG>0 率 0.970；≥2 输入 r@1=MRR=1.0）；
  H_noise REJECTED（噪声下调但增益保正）；H_topology SUPPORTED。
- **增量价值 REJECTED**：`experiments/core_incremental_value/FINAL_REPORT.md`（34,016 runs）——
  主族 4/4 confirmatory 条件 ΔJCG<0，FAS 反超 B2 次数 0/200；唯一例外=共享枢纽拓扑 30/30 vs 0/30。
- **行为层 REFUTED**：论文 U1 一致——−扩散消融 259 tick < 完整 315（"扩散不驱动行动选择"，
  PAPER_CLAIM_AUDIT #12）。
- 装配警示：pre-C18 沙箱矩阵扩散执行器缺失（FINAL_EXPERIMENT_REPORT §13d-C18），
  该矩阵 −Diffusion 列禁止引用。`experiments/mechanism_campaign` 正式 90-run 未运行（探针失败：
  配方图深度 1 星形森林）；A9 reverse-projection 修复（ρ .33→.65, p=.002）仅机制级、生产图未改。

### 3. Episodic/Relational Memory — partially_validated
- 访问层：G0 4/12 vs G1 0/8 vs G2 0/0 再激活，VALID（EXPERIMENT_VALIDITY_MATRIX）；
  三条件行为一致——"表示层≠行为层"是实测边界。
- **死路径**：`episodic_buffer.mark_accessed` 全仓零调用 → access_count 恒 0 →
  `check_promotion` 的 acc≥3/act≥5 分支永不触发（FAILURE_REGISTER L1-MEM-01）；
  晋升仅 `/api/memory/approve` 手工触发（L1-CON-7 DEFER）。A4-A（09-30）只接入观测。
- `experiments/cross_episode_recomb/FINAL_REPORT.md`："MECHANISM-SUPPORTED-BEHAVIORALLY-UNCONFIRMED"；
  H1/H3/H4 全部反方向显著（p=0.0039/0.000122/3.1e-05）。

### 4. Write-back / Learning — partially_validated
- B1/B2 写回隔离 VALID（0 vs 13 个操作节点）；A2 复用（8 轮后每种子晋升 4 条）VALID；
  账本迁移 C VALID（L3 行为级，13t vs 16t，首动翻转 5/5——"改变如何、不改变是否"）。
- **负结果**：`experiments/capability_gap_v2/J_FINDINGS.md`——100 周期协议下"知识巩固未被触发
  （假设=0、晋升=0）"，writeback 消融为空操作；延迟信用 B 实验 160s 0/10 不形成（90s 归因窗）。

### 5. Goal / Drive — partially_validated
- A10 信标战役 80/80 收官：fas_full 20/20 vs llm_direct 0/20（p=2e-06）、vs D-noact 0/20；
  H_A10 支持"记忆价值=信息不对称域的必要条件"（research_audit/NEXT_ACTIONS.md）。
- D-CI 意图生命周期：机制级 VALID 但表达未达（峰值 0.46 < 阈 0.78，claim #9
  SUPPORTED_WITH_LIMITATION）。
- **负结果**：目标修订/止损 E——fas 平均 11.1 次失败才转向 vs history 1 次；J："活跃目标 1→8
  线性累积、无清理通道"。
- DriveEvaluator/curiosity 作为驱力源**无独立行为实验**；INTERNAL_MOTIVATION_DESIGN.md Phase 2
  标注滞后于代码（drive_engine 已接 cognitive_field），谨慎引用。

### 6. Curiosity — untested
- 仅单元测试（test_curiosity_refactor / test_curiosity_satiation）；LLM 提问质量从未对照评估。
- 有规则兜底（curiosity_engine.py:885）故可降级运行；嵌 LLM 于认知逻辑（866）。

### 7. Self Model — partially_validated
- v6 曾证"写而不读"：消融有效（ON 5/5/5 vs OFF 0/0/0）仍无行为差异，`current_goal()` 生产零读取者
  （capability_gap FINDINGS；ARCHITECTURE_DIAGNOSTIC Q5）。
- 2026-10-02 F v7 修复（REPAIR_TEST_RESULTS.md）：接入 goal_attention consumer 后派生目标 0/5→5/5、
  gathers 1.0→6.0 —— 首个行为差分；novel 条件 0/5 仍受限。S10 短链消融无差异。

### 8. Action Selection — partially_validated
- 事件框架 D+C20e 行为差分 5/5（首动 birch→oak）、C20g 失败路径 17→15 tick——但限定沙箱装配层，
  **生产默认关**（`config.py:1935 experience_eventframe enabled=False`，A2 待观测窗口裁决）。
- autonomy 候选评分/绑定链只有回归测试，无对照实验。

### 9. Action/对话决策头 — failed（诚实记录）
- I 实验（7 步链）："FAS 0–2/10，LLM 原始流 10/10"——基线完胜；两个表示修复假设均被拒绝
  （capability_gap EXPERIMENT_REPORT）。
- C34 路由战役：v1 = **bugged_experiment**（harness 从不建边，INVALID ASSEMBLY，数据作废保留）；
  v2 = 520/520 有效但仅方向性——T4 19/20 vs 15/20，p=0.046 **Bonferroni 后≈1 不存活**；
  T3 显著更慢（p=0.003）；token 成本 2.1–3.7×（experiments/core_routing_v2/EXPERIMENT_REPORT.md）。

### 10. Modulation — validated（限定范围）
- C-emo 剂量反应 49.8/52.5/57.4、"禁用≡中性"（VALIDITY_MATRIX；claim #8 由 NOT_TESTED 升
  SUPPORTED）。**限定：只标量 gain 通道，不得扩为激素全局调制**。R2 14 靶点 PASS
  （docs/neuromodulation_R2_audit.md）。

### 11. Regulation / Locks — untested
- 仅单元测试（test_cognitive_locks / test_cognitive_triggers / test_state_monitors）；
  COGNITIVE_REGULATION.md 的"全部实现"指代码，不是实验。

### 12. Internal State — partially_validated（间接）
- Phase 1 落地 38 项测试+落盘守卫；S5/C-emo 实验经它供数（间接支持其作为状态真源的定位）。

### 13. Disposition / Personality — untested
- disposition 仅单元测试 + test_behavior_competition 覆盖选择效应；personality 无专属实验。
  `BASELINE_PRIORS`（personality_baseline.py:35–42）是出厂种子，演化有效性未证。

### 14. Reflection — untested
- claim #15（记忆再点火因果作用）、#17（幻觉缓解）NOT_TESTED；LLM 反思质量无对照测量。
  代码把关模式（候选→人工/规则审批）有单元测试。

### 15. Dynamic Revalidation — partially_validated（mechanism only）
- `experiment_dynamic_revalidation/experiment_report.md`（实机 MC）：Case A/B unavailable
  （超平坦世界从未形成资源目标）、C not observed、D partially confirmed；
  总判 "Q1 not confirmed · Q2 not observed · Q3 partially confirmed"——
  典型"代码存在但触发条件在真实环境从未出现"。

### 16. Hebbian F 通道 — failed（如实负结果）
- 机制层 +0.074 权重漂移、行为持平、"亚阈"（VALIDITY_MATRIX；claim #10）。

### 17. 语言实现（Companion，非认知主张）
- 当前全部回复/主动表达由 LLM（MiMo/DeepSeek/Ollama）+ `FAS_IDENTITY` 模板实现。
  prompt_templates.py:277 明文"**不存在预设的说话风格或情境规则**"——项目自我立场是不假装
  已解决"FAS 独特语言"问题；无台词库/口头禅表。FAS 自有语言机制：**NOT_IMPLEMENTED**。

---

## 附：论文口径核查

- `FAS_Paper_I/README.md`：证据优先定稿（"every number programmatically extracted from archived
  raw runs"），routing H1 rejected 已反映；`FINAL_REVIEW.md` 三审稿人反向评审无 overclaim 可攻。
- 终稿标题已重定位为 "…a Preregistered Boundary Analysis of Activation-Based Access"，
  显式分离"持久关系复用有行为收益"与"激活必要性未支持"两命题（deliverables/paper_final/CHANGELOG.md）。
- 本台账与论文口径一致；若冲突，以 `research_audit/` 与 `experiments/` 原始报告为准。
