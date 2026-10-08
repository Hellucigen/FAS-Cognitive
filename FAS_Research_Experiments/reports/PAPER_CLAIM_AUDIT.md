# 声明审计（PAPER_CLAIM_AUDIT）— 投稿前收口定版

- **日期**: 2026-09-28
- **依据**: `PRE_EXPERIMENT_AUDIT.md` §10 停止条件 [A]–[F]（全部达成）
- **范围**: 三因果实验（A 写回 / B 扩散 / C 跨任务迁移）闭包后，论文可引用声明及禁止引用清单

---

## 0. 实验资产总览（全部已落盘，可复现）

| 实验 | runner | 结果 | 报告 |
|---|---|---|---|
| A 知识写回 → 未来复用 | `scripts/run_exp_reuse.py`（C17/C17b/C17d/C17e） | `reuse/reuse_{A,B}_seed{1..5}.json` | EXPERIMENT_A_REUSE.md |
| B 注意力/扩散 → 知识访问 | `scripts/run_exp_diffusion.py`（C18） | `diffusion/expB_{full,nodiff}_seed{1..5}.json` | EXPERIMENT_B_DIFFUSION.md |
| C 跨任务迁移（reuse+recombination） | `scripts/run_exp_transfer.py`（C19） | `transfer/expC_{Transfer,No-Transfer}_seed{1..5}.json` | EXPERIMENT_C_TRANSFER.md |

三个实验全部：seeds 1–5 ≥ 5 run/条件、沙箱同 seed 全确定（逐 seed 相同轨迹，统计为 descriptive，不做显著性声称）、失败保留、原始日志（events/snapshots/metrics/manifest）全量存档。

## 1. 可引用声明清单（每条附证据位置）

1. **写回机制在生产路径真实发生**：8 轮观测 → promote 4–8 条（操作/变化 节点 + 导致边 + mark_active），支撑 = A §3.1、C §3.3（promoted=7）。
2. **跨 episode 图传输链路成立**：inherit_graph 结构+激活双继承（语义节点与激活值同传），支撑 = A §3.2。
3. **达成层对"经验/激活类知识"无感（表格闭包遮蔽）**：A（success@9、同轨迹）、B（success@9）、C（5/5 双条件）三层一致；支撑 = 矩阵 11 组 + 三实验 negative-side。
4. **扩散在知识访问层有真实可测差分**：缺口"指向"对象累积点亮至封顶、视野外知识节点 0→0.43、能量守恒发射即转移签名、2 跳点亮域 5 vs 4；支撑 = B §3.1。
5. **同进程因果账本使同族动作成功率记忆跨任务迁移，且产生行为层可测差分**：action_prior 差分精确命中共享结构动作（3/3 (1.0, obs=8) vs (None, 0)），首动作竞择翻转（birch→oak）、−3 tick / −1 动作、重组动作提前一拍；支撑 = C §3.2/§3.3。
6. **n 动作目标动态达成不依赖先验知识注入**（结构限制）：表格化配方是短链达成的必要计划知识（先验条件 A 0/6 vs B/C 6/6）；"no-prior learning"被明确否定；支撑 = 先验条件补跑（prior_conditions/）。

## 2. 禁止引用 / 必须带修正引用的清单

- **禁止**：矩阵 −Diffusion 消融作为"传播对行为无贡献"的声明依据——pre-C18 全部矩阵 run 在**扩散执行器缺失**的装配下运行（沙箱从未装配 CC 循环，扩散+衰减双缺，C18 修正）；其叠加之零差分只能读为"表格闭包达成不依赖激活"，扩散机制级证据必须改引实验 B。
- **禁止**：实验 A 的"行为零差分"作为"写回无行为收益"的推广——相同遮蔽结构下，实验 C 已分离出行为层差分（该差分依赖账本通道与图继承，A 的结构使首拍竞择锁死在 birch 好奇路径）。
- **引用需带口径**: `action_prior` 差分仅存在于**同进程账本**（inheritance）通道；跨进程时间轴（新 base dir）清空账本是设计事实。
- **引用需带限定**: 一切沙箱指标准确值（@tick、激活数值）受仿真时钟/感知装配约束，论文只宜引用相对差分与机制签名。

## 3. 审计停止条件对照

- [A] A 有效（写回链路逐步验证，行为影响环断裂如实记录）✓
- [B] B 有效（知识访问层正差分 + 达成层零差分负结果双侧）✓
- [C] C 有效（行为层/知识层可见差分 + 达成层零差分双侧）✓
- [D] 全量原始日志存档 ✓（reuse/diffusion/transfer 全部 run_*/ + snapshots + metrics + manifest）
- [E] 失败保留 ✓（A v2 自制对象世界 negative 对、B pre-C18 pilot、C pilot v1 跨段合并口径 run 均原样保留）
- [F] 本文件 = 声明审计完成 ✓

**收口结论**：投稿声明按 §1 五条可引用；§2 三条禁止/修正引用已入 CHANGE_LOG C18/C19 账本；三实验后不再添加模块（audit 停止指令）。
---

# 收口更新（2026-09-29 Paper-I 收尾实验 campaign 后定版）

> 依据：FINAL_PAPER_EXPERIMENT_REPORT.md（sandbox-only campaign，A2/B2/C-emo/D-CI/F/I/G/H/J/K 完成，E 跳过）。原有审计 §1–§3 保留作历史口径；下表为逐 claim 现行状态。

## A. 论文 claim 状态表

| # | Claim | 状态 | 证据 |
|---|---|---|---|
| 1 | 写回机制在生产路径真实发生（promote 写回） | SUPPORTED_BY_EXPERIMENT | 既有 A/C + A2（B2 谱系 13 写回节点入图）+ G（8 节点） |
| 2 | 跨 episode 图传输链路（结构+激活双继承） | SUPPORTED_BY_EXPERIMENT | 既有 A/C + A2/G（继承面隔离干净：B1=0） |
| 3 | 达成层对经验/激活/先验边类知识无感（表格闭包遮蔽） | SUPPORTED_BY_EXPERIMENT（三重复现，升级环境仍成立） | A2 B0/B1/B2 逐位一致；G 三条件一致；H L0=L2 |
| 4 | 扩散在知识访问层有真实可测差分 | SUPPORTED_BY_EXPERIMENT（升级：与噪声/理想检索三分离） | B2 四条件 |
| 5 | 同进程因果账本跨任务迁移产生行为层差分 | SUPPORTED_BY_EXPERIMENT | 既有 C（本轮未改动证据面） |
| 6 | 机制表=必要计划先验；"no-prior learning"否定 | SUPPORTED_BY_EXPERIMENT（升级：5-episode 累积不改变边界） | J（P0 0/25 且图增长 +29） |
| 7 | 事件框架负极性槽位边调制行动选择 | SUPPORTED_WITH_LIMITATION | 既有 D+C20e+C20g（装配层口径不变） |
| 8 | **情绪/动机状态调制扩散动力学**（原 NOT_TESTED） | **SUPPORTED_BY_EXPERIMENT（新）** | C-emo：gain 0.85/1.0/1.3→质量 49.8/52.5/57.4 单调、E2 关断自证、5/5 全确定 |
| 9 | **CI 生命周期图上运行**（原 NOT_TESTED） | **SUPPORTED_WITH_LIMITATION（新）** | D-CI：形成/竞争/强化/滞留/丢弃 + 表达决策滞留极；表达极未触达（需社交信号） |
| 10 | **Hebbian 共激活强化改写边权**（原 NOT_TESTED） | **SUPPORTED_WITH_LIMITATION（新）** | F：H0 三边单调增强 vs H1 零变化；行为面 30 轮亚阈 |
| 11 | **结构化事件知识跨情境检索复用**（原 NOT_TESTED） | **SUPPORTED_WITH_LIMITATION（新）** | G：G0 4/12 vs G1 0/8 标记节点再点亮；行为层遮蔽 |
| 12 | **"diffusion drives action selection"** | **REFUTED（按原表述）→ 改述** | B2（知识访问层）+ H-L1（−扩散长链更快 259 vs 315）：改述为"扩散驱动知识访问与联想域；行动达成由任务闭包驱动" |
| 13 | 前瞻记忆/延迟目标 | NOT_IMPLEMENTED | ARCH_CHANGE_PROSPECTIVE_MEMORY.md（实验 E 跳过） |
| 14 | 感知管线/符号推理/RL 策略/外部知识获取/激素节点架构 | NOT_IMPLEMENTED | 预审计 §1.15–18；campaign §2 禁令 |
| 15 | 记忆再点火的因果作用 | NOT_TESTED（机制在位、冷场生态条件未现；场温 1.61–2.12 vs 阈 0.35 已量化） | D-CI C2=C0 |
| 16 | 环境变化下账本更新通道的因果作用 | NOT_ANSWERED（量程不足：探索通道即时吸收，0 失败差分） | I（U0=U1） |
| 17 | 幻觉缓解、长期记忆一致性 | NOT_TESTED | 无测量设计 |

## B. 新增禁止/必须带口径的引用（并入既有 §2）

- **禁止**："diffusion 驱动行动选择/达成"——只能引知识访问层（B2）与路径效率（L1 略快于 L0 属如实负结果，引用时不得隐去）。
- **引用需带口径**：长链（iron_ingot）沙箱达成依赖 ENVIRONMENT_SIMPLIFICATION（smelt 即时结算，C29）；furnace_take 环节由环境吸收。
- **引用需带限定**：情绪调制证据为**标量状态→param_gain 通道**（C-emo），不得扩为"情绪节点/激素系统全局调制"（后者未实现，论文 §821 自认口径维持）。
- **引用需带限定**：CI 证据截至"滞留/沉默"极；"决定表达"的语言实现极无沙箱证据。
- Hebbian 证据只能引机制层（权重通道因果隔离）；行为面声明禁止。
- L3（−GraphPrior）0/5 不得单独引用为"图谱先验边必要"——必须带 L3b 分解（缺的是 SMELT_PRIOR 任务先验）。

## C. 机制修复记录

- 核心机制零改动（campaign 全程）。基础设施修复：C26b（runner 汇总缺陷）、C25b（CI 装配对称性）、C32（RunRecorder run_id 秒级碰撞→毫秒+序号；reuse_a2 丢失 raw 已补跑）。
- 回归：修复后全套件 104/104 绿（C32/C31 改动后复跑）。
