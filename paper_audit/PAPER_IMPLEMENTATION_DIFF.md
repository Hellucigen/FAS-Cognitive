# PAPER_IMPLEMENTATION_DIFF — 旧论文（Novel_latex.txt，2025-04 稿）vs 当前系统

判定依据：PROJECT_ARCHITECTURE_MAP.md（调用链）+ FAS_Research_Experiments 原始数据。Action ∈ {KEEP, REWRITE, DOWNGRADE, MOVE_TO_FUTURE_WORK, REMOVE}。

| 旧论文声明 | 当前实现 | 状态 | 证据 | Action |
|---|---|---|---|---|
| 统一知识图谱（语义/情景/程序性同图，节点+有向加权边形式化 G=(V,E)） | IMPLEMENTED | 一致 | graph_model；全实验 | KEEP |
| 节点五元组 v=(id,τ,t,w,a)、边复合结构（静态 w + 运行时 ŵ） | IMPLEMENTED（命名不同：graph_space/label/weight/activation） | 一致 | graph_model.py | KEEP（按代码字段改写） |
| 事件框架（复合节点+嵌套槽位子图+映射边锚定语义实体） | PARTIALLY_IMPLEMENTED | 行动侧经 C21/EventFrameInjector 在；层级化子事件组织无 | action_system.py; D 实验 | REWRITE（只写已实现形态） |
| 自认知子图谱（腺体-受体-激素节点架构） | NOT_IMPLEMENTED | 仅有 dopamine/cortisol 标量 + param_gain 通道 | diffusion_engine.py:337-358 | REWRITE→标量调制通道；节点架构 REMOVE/MOVE_TO_FUTURE_WORK |
| 参数节点化 p(t)=clip(p0+Σα·a)（情绪/性格调制扩散参数） | PARTIALLY_IMPLEMENTED（param_gain 一条通道已实验验证；k/D_max 等调制未实现） | 部分一致 | C-emo 实验；论文 §821 自认 | DOWNGRADE（只写已验证通道） |
| 性格（proto-personality）作为调制偏置 | PARTIALLY_IMPLEMENTED（disposition 通道存在，无实验） | 无验证 | disposition_store.py | MOVE_TO_FUTURE_WORK |
| 情节缓冲器/跨模态对齐/激素调制缓冲窗口（感知章全章） | NOT_IMPLEMENTED（沙箱口径；ear/eye 生产存在未验证） | 无实验 | 预审计 §1.15 | REMOVE→一段"感知接口"说明 |
| 激活扩散形式化（Δa、衰减 λ、终止条件、Top-k） | IMPLEMENTED（实现含归一化因子/能量守恒，与论文公式略有出入） | 基本一致 | diffusion_engine.py; B2 实验 | KEEP（按实现改公式） |
| 关系语义同步激活（前缀索引） | IMPLEMENTED（rel_rules 增益） | 一致 | diffusion_engine.py:1012-1063 | KEEP（简写） |
| LLM 组装 Top-k 候选→认知焦点事件框架 | IMPLEMENTED（生产链路） | 一致但实验零 LLM | app.py 回合管线 | KEEP（标注实验口径） |
| 行动节点双执行路径（函数代码 + RL 策略 hasPolicy） | 函数路径 IMPLEMENTED；RL 路径 NOT_IMPLEMENTED | 部分一致 | 无 hasPolicy 代码 | REMOVE（RL 路径）/MOVE_TO_FUTURE_WORK |
| 外部符号推理工具（Prolog facts、τ、β_logic 边增强） | NOT_IMPLEMENTED | 无代码 | 全仓 grep | REMOVE |
| 目标事件框架+触发条件驻留（前瞻记忆） | NOT_IMPLEMENTED | 无机制（仅 app.py:652 排除标记） | ARCH_CHANGE 记录 | MOVE_TO_FUTURE_WORK |
| 持续认知循环（衰减/扩散/脉冲/再点火/自由思考） | IMPLEMENTED（tick 零 LLM） | 一致 | continuous_cognition.py; D-CI 实验 | KEEP |
| 记忆再点火 | IMPLEMENTED | 机制在位；冷场生态条件未现（场温 1.61-2.12 vs 阈 0.35） | D-CI C2=C0 | DOWNGRADE（如实报边界） |
| CI 图化生命周期（形成→就绪→表达/丢弃；竞争；内容充实度门控） | IMPLEMENTED | 一致；表达极未触达（沙箱无社交信号） | D-CI; dialogue_decision | KEEP（限定到滞留极） |
| 表达决策/回应约束/交流决策层 | IMPLEMENTED（决策层零 LLM 可测） | 一致 | dialogue_decision.py | KEEP（限定） |
| LLM 六参与模式+全局预算 | IMPLEMENTED | 一致（D-CI 用预算桩） | llm_provider/budget | KEEP |
| 学习章：微调 LLM 外部知识抽取（C_source/δ 增量/在线稀疏邻域查询） | NOT_IMPLEMENTED | 无代码；零 LLM 契约 | 预审计 §1.18 | REMOVE |
| Hebbian 共激活强化 w←w+ε·a·a | IMPLEMENTED（ε=0.0015、白名单、max_gain 封顶、运行时不落盘） | 一致（参数与论文不同需按代码写） | diffusion_engine.py:268; F 实验 | REWRITE（按真实公式+实验口径） |
| 经验写回（行动结果→事件框架→情景记忆→激活传播调制行动） | IMPLEMENTED（操作:/变化: 节点+导致边；promote 线 support≥5/conf≥0.80） | 一致但命名/形态需按代码 | experience.py:774-800; A2/G | KEEP（核心，重写措辞） |
| 实验章（第 8 章 JSON 原型 Answer/Apple 演示） | DEPRECATED | 已被 530+ run 实验套件取代 | FAS_Research_Experiments | REMOVE（换新实验章） |
| 贡献声明（AGI 路径、类人认知等宏观表述） | 无证据支持 | — | — | REMOVE |
| 情绪以节点参与扩散与表达决策 | PARTIALLY_IMPLEMENTED（节点注入扩散"预留未接线"；表达决策消费情绪信号在） | 部分 | diffusion_engine.py:114 | DOWNGRADE（只写已验证的标量调制+表达决策信号） |
