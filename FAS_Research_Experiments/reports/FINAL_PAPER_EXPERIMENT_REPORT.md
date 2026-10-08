# FINAL_PAPER_EXPERIMENT_REPORT — FAS Paper-I 收尾实验 campaign

- **日期**: 2026-09-29（sandbox-only；真实 MC 全程未启动）
- **纪律**: 证据优先；失败全保留；pre-fix/post-fix 双保留；所有结论可从 run 产物 re-read
- **前置**: FINAL_CAMPAIGN_PREAUDIT.md（机制实现状态 18 项判定）+ ARCH_CHANGE_PROSPECTIVE_MEMORY.md（R7 跳过依据）
- **变更账本**: CHANGE_LOG.md C22–C32（本轮全部为实验基础设施新增/缺陷修复，核心机制零改动）

---

## 1. Experiment inventory（§23.1）

| 实验 | 研究问题 | 条件 | N（run 数） | seeds | 状态 |
|---|---|---|---|---|---|
| A2 写回→复用（升级环境） | R1 | B0/B1/B2 | 15×2 谱系=20 ep（含补跑 5 raw） | 5 | 完成（含负结果） |
| B2 扩散因果四条件 | R2 | full/nodiff/rand/retr | 20 + 2 pilot | 5 | 完成 |
| C-emo 情绪/动机调制 | R5 | E0/E1_high/E1_low/E2 | 20 + 5 pilot | 5 | 完成（新正面证据） |
| D-CI 持续认知/交流意图 | R6 | C0/C1/C2 | 15 + 2 pilot | 5 | 完成（含量化边界） |
| E 前瞻记忆 | R7 | — | 0 | — | **跳过**（ARCHITECTURAL_CHANGE_REQUIRED） |
| F Hebbian 直接测量 | R8 | H0/H1 | 10（各 30 episode） | 5 | 完成（机制层正/行为层负） |
| I 环境变化/信念更新 | §15 | U0/U1 | 10 + 4 pilot | 5 | 完成（量程边界） |
| G 层级事件/跨情境复用 | R9 | G0/G1/G2 | 15 | 5 | 完成（访问层正/行为层负） |
| H 长链因果行动 | R10 | L0/L1/L2/L3/L3b | 25 + 3 pilot | 5 | 完成（L3 负结果+归因分解） |
| J 先验依赖边界（多 episode） | §16 | P0/P1/P2 | 15 | 5 | 完成（边界确认） |
| K 非天花板消融 | §17 | =H 的 L0–L3b 读数 | （复用 H） | 5 | 完成 |

每族均有 manifest.jsonl 条目 + raw run_*（metadata/events/snapshots/metrics）+ summary.json + failure_analysis.md（gen_campaign_summaries.py 生成）。

## 2. Positive evidence（§23.2——只列真正得到支持的结论）

1. **情绪/动机状态对扩散动力学有真实、单调、可关断的调制（R5，本轮最重要新正面结果）**。既有通道（CC 每拍 arousal/stress → engine.update_param_modulation → 发射比率乘子 param_gain，钳位 [0.7,1.6]）在 E0/E1_high/E1_low 下激活总质量 52.5 / 57.4 / 49.8（gain 1.0/1.3/0.85，剂量响应单调，5/5 seeds 全确定）；E2（同状态值+调制禁用）与 E0 逐位一致 → 差分隔离在调制通道本身。行动层零差分（3 动作 @9 全条件）。[emotion/expCemo_*.json]
2. **扩散的结构性传播与"有激活"和"理想检索"三者可分离（R2 加强）**。四条件（同世界同 seed）：full 激活质量 61.5/点亮域 39；nodiff 16.6/17；rand（同量级无结构噪声）质量 ~45/点亮域 49–55 但 oak_log 首亮 tick 抖动 1–9（无结构时序）；retr（目标链检索 oracle）17.2/18——结构性传播的访问域 > 噪声注入与理想检索的窄域，且时序确定。[diffusion_b2/expB2_*.json]
3. **写回→继承链路在更复杂环境（4 步重组链）复现成立（R1）**。B2 谱系图内 13 个"操作:/变化:"写回节点入图，B1 谱系 0 个（writeback_on=False 隔离面干净）；A1 oak_fence 链 8 轮 practice → promotion 真实发生。[reuse_a2/]
4. **跨情境事件结构复用在知识访问层可见（R9）**。G0（结构继承）Ep2 中 4/12 个 Ep1 事件/行动标记节点被再点亮；G1（扁平记忆：剥离 episodic 结构、事实量同构）0/8；G2 0/0——共享实体耦合的事件结构确被复用于检索。[event_transfer/]
5. **持续认知循环在无输入时从真实经历形成 CI 并完整走图上生命周期（R6 主体）**。C0 每轮 2–4 个 CI（kind=action/attention_shift）形成→强化→滞留→衰减→丢弃；C1（CC OFF）0 CI（对照干净）；环境事件（相关/无关/触发）后 CI 形成时刻与之对应。表达决策按既有门槛运行，本轮 CI 激活峰值 0.46 < 表达阈 0.78 → 决策输出=滞留（沉默是合法三态）。[ci/expD_*.json]
6. **Hebbian 共激活强化真实改写白名单边权（R8 机制层）**。H0：30 轮后恰 3 条白名单边单调增强（oak_log|属于|可采资源 +0.0737，逐 seed 一致）；H1（enabled=False）0 条边变化——权重通道因果隔离成立。[hebbian/]
7. **先验依赖边界精确落在"世界机制知识"上，且经验累积不能替代（§16 收口）**。P0（零先验）5 seeds × 5 episodes = 0/25 达成，但图谱照常增长 +29 节点（感知/经验通道活着）——"有经验累积"不改变边界；P1（仅机制表）=P2（全先验）25/25 @4——图谱先验边在短链可或缺再次复现。[prior_boundary/]
8. **长链（10+ 步，含工具中介双支路）首次在沙箱全链达成（R10 环境修复后）**。L0 5/5 @315 tick（46 动作/26 冗余/11 失败——真实行为形态）；链：oak_log→planks→stick→table→wooden_pickaxe→stone→cobblestone→stone_pickaxe→iron_ore→raw_iron→coal_ore→coal→furnace→smelt→iron_ingot。[longchain/]
9. **长链失败的归因分解：熔炼任务先验（非图谱先验边）是深链计划知识的关键缺口**。L3（−GraphPrior+B 口径）0/5 全灭；L3b（同 L3 + 仅注入 SMELT_PRIOR）5/5 @315 与 L0 逐位一致——差分 100% 归因于"iron_ingot=smelt(raw_iron)"这一非配方转换知识。[longchain/expH_L3*_seed*.json]

## 3. Negative evidence（§23.3——明确 not supported）

1. **达成层对经验/激活/先验边类知识仍然无感（表格闭包遮蔽，三重复现）**：A2 B0/B1/B2 逐位一致（5/5 @11）；G 三条件一致（5/5 @16）；C-emo 行动层一致。升级环境与三条件设计未打破遮蔽——"组件→达成率"的因果主张在本任务族不可维持（与既有矩阵 11 组结论一致）。
2. **扩散对长链行动效率无正贡献，甚至略负**：L1（−扩散）@259/42 动作 快于 L0 @315/46 动作（5/5 全确定）——闭包驱动的规划不需要扩散；扩散带来的额外联想激活在行动竞择中产生少量绕行。"diffusion drives action selection" 按原表述 **不成立**（见 §8 口径修正）。
3. **Hebbian 行为面在 30 轮量程不可测**：权重 +0.07（≈基线 7%）未转化为检索名次/激活时延变化（两条件 craft_rank 轨迹逐位相同）。ε=0.0015 × 30 轮 = 行为亚阈。"weight 变 → future behavior 变"的后半句 **not supported**（前半句 supported，见 §2.6）。
4. **沙箱量程下环境变化被探索通道即时吸收，统计更新通道未被压测（§15 边界）**：U0/U1 全部 5/5 @4、0 失败——新资源带经探索即达，无需失败归因驱动的信念修正。账本更新通道在环境变化下的因果作用 **未回答**（非否定，是量程不足）。
5. **记忆再点火在本场景未触发（机制在位、生态条件未现）**：场温 1.61–2.12 恒高于冷场阈 0.35（任务饱和场），C2（再点火禁用）与 C0 逐位一致——C2 条件因此不构成差分检验。再点火的因果作用 **not tested**（触发条件未出现，非机制缺失）。

## 4. Failed experiments 与分类（§23.4；§20 全保留）

| 失败/负结果 | 分类 | 依据 |
|---|---|---|
| L3（−GraphPrior）0/5 | **EXPERIMENT_DESIGN→归因分解后转机制结论** | L3b 5/5：缺的是 SMELT_PRIOR 任务先验（分解设计 C31） |
| A2/G/H 行为层零差分 | **MECHANISM_NEUTRAL（表格闭包遮蔽）** | 三重复现 + 访问层差分在场（§2.3/2.4） |
| F 行为面无差分 | **MECHANISM_NEUTRAL（ε 亚阈）** | 权重通道 5/5 分离（§2.6/§3.3） |
| I 零失败差分 | **EXPERIMENT_DESIGN_FAILURE（量程不足）** | 探索通道即时吸收；未做 if/else 强造失败 |
| C2 无差分 | **EXPERIMENT_DESIGN_FAILURE（冷场条件未现）** | 场温量化记录 |
| reuse_a2 5 个 run 目录丢失 | **INFRASTRUCTURE_FAILURE（已修 C32）** | run_id 秒级碰撞；summary JSON 完好；raw 已补跑 |
| run_exp_hebbian 首批 10 run 汇总 JSON 未写 | **INFRASTRUCTURE_FAILURE（已修 C26b）** | dict|None TypeError；EPISODE 数据在 run_* 内 |
| 旧 iron_ingot 27 run 全灭 | **ENVIRONMENT_LIMITATION（本轮环境侧修复后转正）** | furnace_take 重试窗壁钟失配 + 探索伪影（C12/C29） |
| 实验 E | **ARCHITECTURAL_CHANGE_REQUIRED** | ARCH_CHANGE_PROSPECTIVE_MEMORY.md |

## 5. Mechanism fixes（§23.5）

本轮**核心机制零改动**。全部修改为实验基础设施/环境数据层（详 CHANGE_LOG C22–C32）：

| 修改 | old→problem→fix | 架构一致理由 |
|---|---|---|
| C32 recorder run_id | 秒级→同秒碰撞覆盖目录 | 加毫秒+序号；目录结构/语义不变 |
| C29 sandbox smelt 即时结算 | furnace_take 重试窗壁钟失配→沙箱长链不可达 | 世界模拟器简化（ENVIRONMENT_SIMPLIFICATION），skills/autonomy 未动；**引用需带此口径** |
| C31 build_stack prior_extra 参数 | 熔炼任务先验与图谱先验边无法解耦 | 新增显式覆盖参数，默认 None=旧行为 |
| C25b CI 装配对称性 | C0/C2 双扩散驱动 vs C1 单驱动 | CC 条件 st.diffuser=None，单驱动器 |
| C26b/C25b/C24 runner 缺陷 | 键名/类型错误等 | 实验脚本内修复 |

## 6. Causal evidence 分级（§23.6）

- **direct causal evidence**（同 seed 逐位对照、单变量开关）：C-emo（调制通道，E2 关断自证）；B2（扩散结构传播 vs 三对照）；F（Hebbian 权重通道 H0/H1）；H（L3→L3b 任务先验归因）；A2（B1/B2 写回隔离）；G（G0/G1 事件结构隔离）。
- **indirect evidence**：J（P0 的 0/25 + 图增长证明经验通道活而计划知识缺——机制必要性的间接论证）。
- **observational evidence**：I（变化后行为形态）、D-CI 的环境事件-CI 形成时序对应、场温对再点火门的量化观测。
- 统计口径：全部沙箱同 seed 全确定（逐 seed 相同轨迹），descriptive，不做显著性声称（沿用既有纪律）。

## 7. Claims that remain unsupported（§23.7——逐项明确）

| 论文愿景 | 状态 |
|---|---|
| 前瞻记忆/延迟目标触发 | NOT_IMPLEMENTED（ARCH_CHANGE 记录，实验 E 跳过） |
| 感知管线（情节缓冲器/跨模态对齐/激素调制窗口） | NOT_IMPLEMENTED（沙箱口径；§2 禁令） |
| 符号推理引擎（Prolog/τ/β_logic） | NOT_IMPLEMENTED |
| RL 策略行动节点（hasPolicy/连续控制） | NOT_IMPLEMENTED |
| 外部知识获取（C_source/δ/在线查询） | NOT_IMPLEMENTED（沙箱零 LLM 契约） |
| 腺体/受体/激素节点架构 | NOT_IMPLEMENTED（param_gain 标量通道已证，见 §2.1） |
| 幻觉缓解、长周期记忆一致性 | NOT_TESTED（无测量设计） |
| 记忆再点火的因果作用 | NOT_TESTED（冷场生态条件未现，机制在位） |
| Hebbian→行为 | NOT_SUPPORTED（ε 亚阈，30 轮） |
| 账本通道在环境变化下的更新作用 | NOT_ANSWERED（量程不足） |
| 事件框架对 obtain 链的生产角色 | NOT_APPLICABLE（C21 生产通道默认关；本轮任务不经过） |

## 8. Recommended paper claims（§23.8；不改论文正文）

**SUPPORTED（可直接引用）**
1. 情绪/动机标量状态经统一调制层（param_gain）对扩散动力学产生单调、可关断的因果调制（C-emo）。
2. 激活扩散的知识访问域由结构性传播决定：同能量随机激活与理想链检索均不能复现其访问时序与覆盖（B2）。
3. 经验写回与跨 episode 图传输链路在 4 步重组链上成立；结构化事件知识的跨情境检索复用可见（A2/G）。
4. 世界机制知识是计划性多步任务的必要先验；经验累积（5 episodes）不改变该边界；图谱先验边在短链与长链均可或缺（J/H-L3b）。
5. CI 生命周期（形成/竞争/强化/滞留/丢弃）在无输入持续认知中从真实经历图上运行，表达决策依阈值产出滞留（沉默）（D-CI）。
6. Hebbian 共激活强化真实改写白名单边权且可关断（F 机制层）。

**QUALIFIED（引用需带限定）**
7. 达成率对全部组件消融不敏感——必须带"表格闭包遮蔽"口径（A2/G/H）。
8. 长链沙箱达成带 ENVIRONMENT_SIMPLIFICATION 口径（smelt 即时结算；H）。
9. CI 表达极未触达（需社交信号；沙箱仅到滞留极）；表达决策值的表达极证据缺（D-CI）。
10. Hebbian 仅机制层证据（权重通道），行为面 30 轮亚阈（F）。

**UNSUPPORTED / 不得声称**
11. "diffusion is the primary driver of action selection"——实际：知识访问层 + 长链路径效率略负（L1 快于 L0）。改述为"扩散驱动知识访问与联想域；行动达成由任务闭包驱动"。
12. "no-prior learning"——P0 0/25 维持否定。
13. 任何"涌现人格/情绪节点全局调制"表述——本轮只证标量调制通道；激素节点架构未实现。

**FUTURE WORK**
14. 前瞻记忆最小实现（触发条件谓词 + CC 脉冲评估钩子 + 命中激活增益）；冷场场景下的再点火因果检验；账本通道在强环境变化下的压力设计；Hebbian 行为面需要更长时程或更弱先验任务；实机（真实 MC）段的 M4/M5/M6/M7 复测。

## 9. 停止条件核对（§25）

1. pipeline 稳定（104/104 回归绿——见补记）✓ 2. 关键实验全有 pilot ✓ 3. 关键实验 ≥5 seeds ✓ 4. 旧失败已重新分类（§4）✓ 5. shortcut 已审计（目标注入全条件同构；L3b 差分变量=SMELT_PRIOR 即被操纵变量本身；C3 oracle 为标注对照；无 task-specific if/else 入 agent 代码）✓ 6. raw 全存 ✓ 7. manifest 完整（补跑后）✓ 8. 本报告 + 各族 summary/failure_analysis ✓ 9. PAPER_CLAIM_AUDIT 已更新 ✓ 10. 无大型架构重构（核心零改动；C31/C32 为装配/记录层）✓

**FINAL EXPERIMENT CAMPAIGN COMPLETE**

- Total runs（本轮新增）: ~120（含 pilot）+ 既有 530
- Valid runs: 全部落盘可复核；Invalid: 0（无 INVALID_FOR_CAUSAL_CLAIM 判定）
- Failed runs: 全保留（§4 分类）
- Experiments completed: A2/B2/C-emo/D-CI/F/I/G/H/J/K；Skipped: E（架构缺位）
- Mechanism fixes: 核心零改动；基础设施修复 C26b/C25b/C32
- Architecture changes: 无（R7 记 ARCHITECTURAL_CHANGE_REQUIRED）
- Positive findings: §2（9 条）；Negative findings: §3（5 条）
- New limitations: §7（11 项状态表）
- Paper claims strengthened: §8.1–8.6；weakened: §8.2/8.11/8.12；Future work: §8.14
