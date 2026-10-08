# FAS Mechanism Experiment — Activation Landscape Audit Report

- 日期：2026-09-30
- 性质：机制实验（multi-input convergence hypothesis 的首次直接检验）
- 状态：**PROBE FAILED → 按 §14 停止，正式 90-run 未运行**
- 纪律：未修改任何 FAS 核心算法/参数/图拓扑/方向语义；全部结论来自只读诊断与确定性探针

---

## Experimental Question

多个同时输入注入统一知识图谱后，activation propagation / competition 是否使**同时与多个输入具有结构关联**的节点（multi-input cluster）获得更高激活并进入 cognitive context——而不是简单的 input→similarity→top-k？

## Graph Construction

生产建图路径（与 routing v2 相同）：`mc_knowledge.ensure_mc_world()`（世界知识）+ `world_prior.build_recipe_closure(bridge=...)`（配方闭包，由世界配方表驱动）。结果：**44 节点 / 53 边**。multi_input_score 仅由该**实验前拓扑**计算（BFS 距离），未引用任何激活值。

## Conditions

| 条件 | 状态 |
|---|---|
| C1 FAS Full（注入+扩散） | 已运行（探针） |
| C2 Diffusion OFF（仅注入+衰减） | 已运行（探针对照） |
| **C3 Competition/Inhibition OFF** | **UNAVAILABLE**——本架构的抑制需负权边（本图无任何负权边），发射预算归一化是 `diffuse_step` 的内禀组成，无配置开关；制造开关=修改核心算法（禁） |
| C4 Flat（同图扁平文本相似度） | 未运行（探针失败即停） |

## Integrity

探针自证：C2（扩散 OFF）下注入后 **0 个新节点点亮**（off_no_spread=true ✓）；C1 下同样 **0 个新节点点亮**（full_spread=**false ✗**）；注入值不饱和（not_saturated=true ✓）；multi-input 节点可由拓扑识别（true ✓）。

## Activation Landscape —— 探针失败的结构性根因

确定性诊断（`propagation_reach.py`，可复现）：

1. **物品:* 类节点 13/13 全部是汇点**（0 条出边）。`需要`/`产生`/`掉落` 关系默认方向均为 forward（src→dst），而它们全部指向 `物品:*`。
2. **全图 44 节点中，从任何节点出发 2 步 forward 都无法到达任何新节点**（最大 2 步新增 = 0）。整张图是**深度为 1 的星形森林**：所有边都指向汇点。
3. 因此：从需求侧节点（物品:wooden_pickaxe / 物品:stone_pickaxe）注入的激活**在结构上不可能传播到共享组件**（物品:stick、物品:cobblestone——它们只能从需求节点**反向**到达，而 forward 扩散不能反向传播）。

这意味着：**"diffusion 使 multi-input 相关节点汇聚"的设计假设在当前图本体/方向语义下不可实现**——不是参数问题，而是**没有任何多跳 forward 路径存在**。

## Multi-input Convergence

无法测得。探针证明传播在 1 跳后即死（全部目标为汇点），multi-input 簇（stick/cobblestone，dA=dB=2）从注入点不可达。

## Top-K Enrichment

无法测得（同上；Top-K 由直接注入决定）。

## Input Coverage

无法测得（同上）。

## Competition / Inhibition

C3 unavailable（原因见 Conditions）。且在零传播的拓扑上，竞争/抑制的效果问题无意义。

## Statistical Analysis

未执行（正式 run 未运行；无数据可统计）。

## Negative / Unexpected Results

1. **【主要负结果】配方知识结构对 forward 扩散是"不可穿越"的**：需要/产生/掉落关系全部单向指向物品汇点，深度=1。FAS 的核心动力学（扩散）**无法沿配方结构从需求传播到成分**。在 生产 系统中，"从目标到达成分"由**符号闭包规划器**（world_prior.build_recipe_closure / 规划器读图）完成，而非扩散——**符号规划路径与激活动力学路径是彼此分离的两套机制**。这是对论文"激活扩散作为统一认知主驱动力"叙述的重要限定。
2. **routing v2 中扩散 ON 的正效应（激活分化 2.4→9.9、精度提升）的真实来源**：观察共现子图（ingest 建的双向 `关联` 对，经 `动作:` 中介枢纽连接共观察实体），**而非配方/世界知识结构**。FAS 在该实验中充当的是"共观察实体的联想记忆"，不是"配方知识的传播器"。
3. v1 审计发现的"无边图"缺陷修复后，扩散机制本身工作正常（能量守恒、fire-once、前沿管理全部按设计运行）——**问题不在扩散引擎，而在图本体给它的结构**。

## Limitations

1. 本结论限于生产配方闭包拓扑 + 当前 forward 方向语义。若关系方向语义不同（如 `需要` 为 bidirectional）或图含多跳结构，扩散行为可能不同——但修改这些属于被禁的机制改动。
2. 探针仅覆盖注入点为需求/物品节点的情形（设计意图内的注入方式）。
3. C3 无法运行（原因见上）。
4. 未检验观察共现子图自身的 multi-input 组织（该子图确有多跳路径——routing v2 的精度差分即来自它；这应作为后续机制实验的对象）。

## Conclusion

探针未通过（§14 判据 2：Full 未产生传播）。**正式机制实验按设计停止。**

五个问题的数据结论：

| 问题 | 结论 |
|---|---|
| Q1 diffusion 是否改变激活景观？ | **否**（在配方/需求注入口径下：无可传播路径；注入集合之外零点亮） |
| Q2 激活与 multi-input 结构关系是否相关？ | **结构性不可实现**（需求节点→共享组件无 forward 路径） |
| Q3 Top-K 是否富集 multi-input 节点？ | 不可测（同上） |
| Q4 competition/inhibition 是否改变分布？ | C3 unavailable；且在零传播下无意义 |
| Q5 激活差异是否进入 cognitive context？ | 无差异可进入 |

**最重要的正面证据**：扩散引擎本身按设计运行（能量守恒、fire-once、激活分层机制在 v2 routing 中已被证实——2.4→9.9 的分化）；探针的 OFF 对照精确自证（零泄漏）。

**最重要的负证据**：`需要/产生/掉落` 全部单向指向 `物品:*` 汇点；**全图 2 步 forward 新可达 = 0**。FAS 的配方知识结构对扩散而言是"只可写入、不可遍历"的。

**是否发现新的 FAS 机制问题**：**是**——且是前两轮实验都未暴露的深层问题：**符号规划路径（闭包规划器）与激活动力学路径（扩散）在图本体上互不相通**。这解释了为什么 routing 实验中扩散的正效应只出现在观察共现子图。

## 建议（RECOMMENDED NEXT STEP）

1. **本轮按 §14 停止，不跑 90-run，不改机制，不改论文**（论文 §10 的 v2 结果仍然有效——它测的是装配修复后的真实行为）。
2. 将"方向语义 vs 扩散可穿越性"立为独立的机制研究问题：例如检验 `需要` 关系改为 bidirectional（或引入反向语义边）后扩散能否沿配方结构传播——**这是机制假设检验，需要单独立项并重新走 probe → campaign 流程**，不属于本轮。
3. 后续机制实验建议转向**观察共现子图**（唯一有真实多跳路径的子结构）——routing v2 的正效应来源所在。
