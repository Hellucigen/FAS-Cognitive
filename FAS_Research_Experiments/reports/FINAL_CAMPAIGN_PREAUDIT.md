# FINAL_CAMPAIGN_PREAUDIT — Paper-I 收尾实验前审计

- **日期**: 2026-09-29
- **方法**: 代码 grep 审计（18 项审计对象逐一定位实现与接线）+ 全套测试运行 + manifest 统计 + 既有报告（FINAL_EXPERIMENT_REPORT / PAPER_CLAIM_AUDIT / CHANGE_LOG）复核
- **范围**: sandbox-only（本轮禁真实 MC）
- **纪律**: 只审计与判定，不改核心机制

---

## 0. 运行基线

| 项 | 状态 |
|---|---|
| 测试套件 | **104/104 通过，失败 0，起不来 0**（206s，scripts/run_tests.py） |
| 既有 run 存量 | manifest.jsonl 共 530 run，10 族：ablation 307 / sandbox 44 / transfer 34 / eventframe 53 / baselines 32 / reuse 27 / prior_conditions 18 / diffusion 12 / smoke 1 / minecraft 2 |
| 既有修复 | C07/C08（记录器 flush+静默吞）、C12（探索壁钟伪影）、C18（沙箱扩散执行器补装）、C19（账本继承）、C20/e/g（事件框架注入器+真实失败路径）均已落盘并有测试 |

## 1. 机制实现状态表（18 项审计对象）

状态取值：implemented / implemented-but-untested / partially implemented / broken / not implemented / architecturally absent

| # | 机制 | 状态 | 证据（文件:行） | 对本轮实验的含义 |
|---|---|---|---|---|
| 1 | 统一图谱记忆（语义/情景/程序性） | implemented | graph_model.py；S1–S11/A/B/C/D 全部在测 | 已有证据，升级环境即可复用 |
| 2 | 激活扩散引擎 | implemented | diffusion_engine.py（Δa=a·w̃·β·gain/Σw̃₊；能量守恒护栏） | 实验 B 已证知识访问层差分；本轮补 C2/C3 对照 |
| 3 | 行动侧事件框架 | implemented | action_system.py（生产）+ scripts/run_exp_eventframe.py EventFrameInjector（实验装配） | 实验 D 已证机制+行为层（C20e/C20g）；本轮 R3 升级 |
| 4 | 因果账本（归因/晋升/action_prior） | implemented | experience.py CausalLearner；experience_timeline.json | 实验 A/C 已证；flush_causal 提供确定性关窗 |
| 5 | **Hebbian 共激活强化** | **implemented-but-untested** | diffusion_engine.py:268 `_hebbian`（调用点 :1043/:1072；护栏 config.hebbian.enabled；epsilon=0.0015；类别白名单 semantic/emotional_relation；首见基线 max_gain 封顶；**运行时不落盘**） | **R8 可直接测**：H0/H1 开关对照 + 逐 episode 边权/检索序记录。注意权重不持久化 → 跨 episode 测量须同进程 |
| 6 | **CI 生命周期** | **implemented-but-untested** | continuous_cognition.py:29-35 状态机 forming→ready→expressed/discarded；MAX_ACTIVE=3 竞争；basis/指向/目标边；:654/:746-793 全生命周期管理 | **R6 可测**：tick 体零 LLM（:388），表达唯一 LLM 点（:921）且 llm_budget 不足时"降级为不表达（CI 保持）"——沙箱零 LLM 恰好强制走决策层，测得到形成/竞争/滞留/丢弃全态 |
| 7 | 记忆再点火 | implemented-but-untested | continuous_cognition.py:109-111 reignite_*；:1179 retrieval.reignite_every 真消费者（参数调制入口） | R6 的 C2 条件（reactivation disabled）= 关 reignite |
| 8 | 对话决策（三态/约束/trace） | implemented-but-untested | dialogue_decision.py:57 BASE_SILENCE 常驻候选、:218 priors、表达层 minimal/normal/question、约束 | 语言实现层依赖 LLM；沙箱只测到决策层输出（respond/silence 判定），不测措辞 |
| 9 | **情绪/动机→扩散调制** | **partially implemented** | **已接线**：internal_state.py:45-48 dopamine/cortisol → cognitive_field.py:173-176（context.arousal +0.30 / context.stress −0.15）→ diffusion_engine.py:340-358 `param_gain`（钳位 [0.7,1.6]，CC 每拍喂值 :372/:484）；**未接线**：情绪节点作为注入源（diffusion_engine.py:114 注释"预留，当前未接线"）；腺体/受体/激素节点架构未实现 | **R5 可测且必须测**（论文声称存在调制）：E0 中性 / E1 改 arousal/stress / E2 调制禁用，测 param_gain→激活分布差分。论文 §821 已自认全局调制未完成，本实验给它第一份直接证据（或否定） |
| 10 | **前瞻记忆/延迟目标** | **architecturally absent** | 全仓唯一线索 app.py:652 `prospective` 属性（仅用于焦点引导时**排除**前瞻事件）；autonomy.py 无 触发条件/延迟目标/条件激活 机制 | **R7 无法在装配层回答**：无"低激活驻留+触发条件监测+焦点上升"机制可开关。按 §4 记 **ARCHITECTURAL_CHANGE_REQUIRED**，实验 E 记 limitation |
| 11 | 自模型 | implemented | self_model.py SelfGraphManager（beliefs/preferences/user_relationship） | S10 已测（短链无差分）；非本轮重点 |
| 12 | 驱动/动机引擎 | implemented | drive_engine.py DriveEvaluator（:151/:366 evaluate/:477 bootstrap_drives） | R5 的 motivation 状态可经既有机制注入，不新造变量 |
| 13 | 层级事件结构 | partially implemented | episodic 空间事件头 + 时间顺序边 + 焦点引导（app.py:645-674）存在；跨情境事件网络靠共享实体节点耦合（论文设计本身如此），无显式"层级检索"机制 | **R9 可测**：G0 事件框架 enabled / G1 flat（断槽位边）/ G2 no reuse（干净图）——用既有 eventframe+inherit_graph 装配，不新造机制 |
| 14 | 环境变化/信念更新 | partially implemented | S7（资源带替换）/S11（过时知识活性衰退）场景在；负权抑制通道存在（扩散统一负权机制，diffusion_engine.py:1021 附近，实验 D 已证） | **实验 I 可做**：U0/U1 对照 + 变化检测时延/旧边抑制/新边形成指标；负证据通路用既有失败归因（S5 通道），不需要新机制 |
| 15 | 感知管线（情节缓冲器/跨模态对齐） | architecturally absent（沙箱口径） | 沙箱感知=结构化方块表，绕过整条管线；ear/eye 生产子系统存在但不入沙箱 | §2 明令禁止实现 → LIMITATION / FUTURE_WORK |
| 16 | 符号推理引擎/Prolog | not implemented | 全仓无 prolog/symbolic_reason/logic_engine | §2 禁止 → LIMITATION |
| 17 | RL 策略行动节点 | not implemented | 全仓无 hasPolicy/policy_node | §2 禁止 → LIMITATION |
| 18 | 外部知识获取 | not implemented（沙箱口径） | 零 LLM 契约（MiMoBackend._create 守卫） | §2 禁止 → LIMITATION |

## 2. 实验基础设施状态

| 组件 | 状态 |
|---|---|
| RunRecorder | 修复后稳定（C07/C08），增量 flush+finalize+模块锁 |
| 沙箱扩散驱动 | SandboxDiffuser（C18）已入 build_stack 默认装配（cc_diffuse=True） |
| 账本/图继承 | inherit_graph / inherit_causal / inherit_tl 装配参数（C19） |
| 事件框架注入 | eventframe=(obj, fail_side, reign_amt[, trigger]) 装配参数（C20/e/g） |
| 先验条件 | prior_level A/B/C（C16） |
| 已知环境缺口 | furnace_take 沙箱驱动缺口（iron_ingot 链全灭根因之一）→ 实验 H 以环境模拟器侧修复（允许，§3.C） |
| 种子策略 | pilot 1 seed → N≥5；build_stack seed 参数生效（既有 run 逐 seed 全确定） |

## 3. 旧失败实验清单与分类预案（§20）

| 旧失败 | 数量 | 分类（预案） | 本轮动作 |
|---|---|---|---|
| iron_ingot 消融矩阵全灭 | 27 run | **ENVIRONMENT_LIMITATION**（furnace_take 驱动缺口 + 探索伪影，非机制失败） | 实验 H 重设计环境后重跑，pre/post-fix 双保留 |
| llm_heavy 112936 未达成 | 1 run | **RANDOM_FAILURE**（5 步上限，run 保留） | 不重跑（零 LLM 主线；如跑 H 附带则记录） |
| S5 pre-fix hyp=0 | 3 run | **IMPLEMENTATION_BUG**（rearm 时序 C14） | 已有 post-fix 终版（12 hyp/9 agg），分类归档即可 |
| M1 事件缺失 86 条 | 1 run | **INFRASTRUCTURE_FAILURE**（C07/C08，已修） | 实机段本轮禁止 → 归档 |
| 实验 B pre-C18 pilot 全同 | 10 run | **IMPLEMENTATION_BUG**（扩散执行器缺失 C18） | 已有 post-fix 正差分终版，分类归档 |
| 实验 C pilot v1 跨段合并口径 | 若干 | **EXPERIMENT_DESIGN_FAILURE**（C19 段界切分） | 已有 post-fix 终版，归档 |

## 4. R1–R10 → 实验 A–K 映射与可行性判定

| R | 实验 | 判定 | 说明 |
|---|---|---|---|
| R1 写回→复用 | A（升级） | **可行** | 既有 run_exp_reuse（A/B）+ run_exp_transfer（4 步链）为基座；补 B1 条件（learned graph + write-back disabled，build_stack writeback_on=False 已支持）+ 更复杂链 |
| R2 扩散因果 | B（升级） | **可行** | C18 后正差分已有；补 C2 随机激活对照、C3 直接检索对照（装配层新 runner） |
| R3 事件帧→访问/行为 | D 既有+升级 | **可行** | C20e/C20g 已证；R3 问法（知识访问/目标形成）在实验 B/D 证据面上，升级环境复测 |
| R4 迁移非 shortcut | C 既有+升级 | **可行** | action_prior 差分已精确命中共享结构动作；shortcut 审计（§19）制度化 |
| R5 情绪调制 | C(new) | **可行，高优先** | param_gain 通道已接线（见 §1.9）；E0/E1/E2 三条件，测激活分布非达成率 |
| R6 持续认知/CI | D(new) | **可行，本轮最重要新实验** | CC tick 零 LLM；CI 全生命周期在图上；C0 ON / C1 OFF / C2 再点火禁用 |
| R7 前瞻记忆 | E | **不可行** | architecturally absent（§1.10）→ ARCHITECTURAL_CHANGE_REQUIRED，记 limitation，跳过 |
| R8 Hebbian | F | **可行** | config 护栏开关 H0/H1；权重不落盘 → 同进程逐 episode 测量 |
| R9 层级事件迁移 | G | **可行** | 共享实体耦合即论文设计；G0/G1/G2 装配对照 |
| R10 长链复杂度 | H | **可行** | 环境模拟器侧补 furnace_take 类驱动 + 4–6 步链 + 干扰物；L0–L3 消融 |
| — 环境变化 | I | **可行** | U0/U1 + 既有负权/归因通道 |
| — 先验边界 | J | **可行** | 既有 A/B/C 基础上加"最小结构先验 + 多 episode 经验累积"口径 |
| — 非天花板消融 | K | **可行（依赖 H）** | 任务改用 H 的长链，指标加中间量（检索/激活/路径/失败动作/写回） |

## 5. 结论

- **可立即开工**：R5（情绪）、R6（CI）、R8（Hebbian）三个全新实验的机制全部已实现且可开关，缺的只是 runner——属于 §3.C 允许的实验基础设施新增。
- **升级复测**：R1/R2/R3/R4/R9/R10/I/J 在既有 runner 基座上加条件/换环境。
- **不可行**：R7 前瞻记忆 = ARCHITECTURAL_CHANGE_REQUIRED（无机制可测，禁止新造架构）。
- **永久 limitation**（§2 禁令）：感知管线、符号推理、RL 策略、外部知识获取、（沙箱口径的）多模态。
- 基础设施无需修复即绿（104/104）；Phase 1 的"修复"范围预计收敛为各新 runner 自带的缺陷修正（照 §3.A 口径记 CHANGE_LOG）。
