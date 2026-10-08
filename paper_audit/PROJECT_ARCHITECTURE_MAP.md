# PROJECT_ARCHITECTURE_MAP — FAS 实际代码地图（Paper-I 重构基线）

- 日期：2026-09-29
- 方法：调用链追踪（非文件名猜测）。状态标记：IMPLEMENTED / PARTIALLY_IMPLEMENTED / UNUSED / EXPERIMENTAL / NOT_IMPLEMENTED / DEPRECATED
- 本图只列**进入论文叙述判断**的模块；纯工程件（Flask 路由、前端）从略。

## 1. 图与记忆

| 模块 | 状态 | 调用链证据 |
|---|---|---|
| graph_model.KnowledgeGraph（Node/Edge，graph_space/label/weight/activation） | IMPLEMENTED | 所有实验 stack 均实例化；persistence via to_dict/load |
| 统一图谱（semantic/episodic/self/procedural 空间标签） | IMPLEMENTED | sandbox_lab.build_stack 种子节点；实验读写全走同一 kg |
| episodic 事件结构（事件头 + 时间顺序边） | PARTIALLY_IMPLEMENTED | app.py:645-674 焦点引导消费；子事件层级化组织无专机制 |
| self_model.SelfGraphManager（信念/偏好/关系） | IMPLEMENTED | self_graph 装配；S10 消融在测 |
| episodic_buffer.py | EXPERIMENTAL（沙箱未装配） | 仅生产装配引用；实验管道绕过 |

## 2. 激活与调制

| 模块 | 状态 | 证据 |
|---|---|---|
| diffusion_engine.DiffusionEngine（Δa=a·w̃·β·gain/Σw̃₊；decay；发射预算；负权抑制） | IMPLEMENTED | B2/消融矩阵全实验驱动（SandboxDiffuser C18 补装） |
| param_gain 情绪调制（update_param_modulation → 发射比率 :1271） | IMPLEMENTED | C-emo 实验 E0/E1/E2 直接测量 |
| cognitive_field（arousal/stress 语境读数） | IMPLEMENTED | CC tick 每拍喂值（continuous_cognition.py:272-288） |
| Hebbian `_hebbian`（共激活边强化，ε=0.0015，白名单，运行时） | IMPLEMENTED | F 实验 H0/H1 隔离（diffusion_engine.py:268, 调用点 :1043/:1072） |
| 软遗忘 `_forget_factor` | IMPLEMENTED（未单独实验） | engine 内激活路径 |
| internal_state（dopamine/cortisol 激素标量） | IMPLEMENTED | modulator_dev 慢分量 → stress；**腺体/受体/激素节点架构 NOT_IMPLEMENTED**（论文 §821 自认） |

## 3. 经验与学习

| 模块 | 状态 | 证据 |
|---|---|---|
| experience.ExperienceTimeline（统一时间轴） | IMPLEMENTED | 全实验 flush_causal 消费 |
| experience.CausalLearner（归因窗 8–120s、聚合→假设→晋升、support/Laplace conf/contra、action_prior） | IMPLEMENTED | A/C/A2/G 全实验；autonomy.py:2790 消费 action_prior |
| promote_to_kg（操作:/变化: 节点 + 导致边 + mark_active） | IMPLEMENTED | A1_B1=0 vs A1_B2=13 写回节点隔离证据 |
| experience_replay（离线经验重放） | EXPERIMENTAL | CC 可选组件；无行为实验 |
| C21 事件框架生产化（_write_event_frame，默认 enabled=False） | IMPLEMENTED（默认关） | 14 项测试电池；**生产默认关闭** |
| action_system.EventFrameInjector（实验装配） | IMPLEMENTED | D/C20e/C20g |

## 4. 决策与行动

| 模块 | 状态 | 证据 |
|---|---|---|
| autonomy.AutonomousLoop（目标闭包规划、缺口候选、行动竞择） | IMPLEMENTED | 全沙箱实验主循环 |
| action_prior 消费（成功率记忆 → prior_bonus ±0.05） | IMPLEMENTED | C 实验行为差分（autonomy.py:2790） |
| dialogue_decision（回应欲望/沉默/约束） | IMPLEMENTED（语言实现依赖 LLM） | 代码在；沙箱零 LLM 下仅决策层可达 |
| continuous_cognition.CCLoop（tick 零 LLM；CI 生命周期；再点火；表达决策；预算门） | IMPLEMENTED | D-CI 实验（llm_budget 桩装配） |
| 前瞻记忆/延迟目标触发 | NOT_IMPLEMENTED | 全仓仅 app.py:652 prospective 排除标记 |
| 符号推理引擎/Prolog | NOT_IMPLEMENTED | 无代码 |
| RL 策略行动节点（hasPolicy） | NOT_IMPLEMENTED | 无代码 |
| skills（gather/craft/place/smelt/furnace_take/explore…） | IMPLEMENTED | 沙箱与实机共用 |

## 5. 感知与具身

| 模块 | 状态 | 证据 |
|---|---|---|
| MinecraftEmbodiment（感知入图、动作推进、结算回执） | IMPLEMENTED | 沙箱 fake-bridge 与真机共用 |
| minecraft bridge（实机 LAN） | IMPLEMENTED | M1/M2 历史实机 run（本轮 sandbox-only 未用） |
| 感知管线（情节缓冲器/跨模态对齐/激素调制窗口） | NOT_IMPLEMENTED（沙箱口径） | 沙箱感知=结构化方块表旁路；ear/eye 子系统未入实验 |
| ear/ eye/ 子系统 | EXPERIMENTAL（生产装配，未实验验证） | 无实验 run |

## 6. 实验基础设施

| 模块 | 状态 | 证据 |
|---|---|---|
| scripts/sandbox_lab.SandboxWorld + build_stack（装配参数：diffusion/causal/writeback/prior_level/inherit_graph/inherit_causal/eventframe/prior_extra） | IMPLEMENTED | C16-C31 全部装配参数 |
| experiment_recorder.RunRecorder | IMPLEMENTED（C07/C08/C32 修复后） | manifest 530+ 条 |
| experiment_mode（shield 表） | IMPLEMENTED | mode=off 时 shield 不生效（CI 实验依赖） |
| 9 个收尾 runner（run_exp_a2_reuse/b2_diffusion/emotion/ci/hebbian/envchange/event_transfer/longchain/prior_boundary） | IMPLEMENTED | 本轮 120+ run |

## 7. LLM 集成

| 模块 | 状态 | 证据 |
|---|---|---|
| llm_provider/nlp（对话生成、结构映射接口） | IMPLEMENTED | 生产对话链路；**实验全程零 LLM（守卫）** |
| 六种参与模式 + 全局预算管理器 | IMPLEMENTED | llm_budget 桩即同一机制的替身（D-CI） |
| 外部知识获取（微调 LLM 抽取/C_source/δ/在线查询） | NOT_IMPLEMENTED | 无代码 |
