# Relation Direction Experiment — Bidirectional Projection 与 Multi-input Convergence

- 日期：2026-09-30
- 性质：图本体/边方向语义的**最小机制实验**。未修改 diffusion engine、activation 公式、边权规则、抑制/竞争、demand/gap/routing 算法、参数、prompt、任务。生产 FAS 图未改动（B 图 reverse projection 仅存在于实验沙箱内存中）。
- 前置：`../core_routing/activation_audit/diagnostic_report.md`（R1/R2/R3 装配审计）+ `../mechanism_campaign/MECHANISM_EXPERIMENT_REPORT.md`（探针发现配方图对 forward 扩散不可穿越）

---

## Research Question

若不改变 diffusion engine、仅改变"关系是否允许激活沿两个方向传播"的图语义（实验性 bidirectional projection），FAS 能否在配方知识中形成真正的 multi-input activation convergence？

区分两个假设：
- 假设 A：问题来自 diffusion engine 本身。
- 假设 B：问题来自 graph edge direction / relation semantics。

## Existing Graph Semantics（静态审计）

`relation_direction_report.csv`（由 `paper_audit/relation_direction_audit.py` 生成，只读）：

| relation | edges | mean_target_out_degree | target_sink_fraction | forward_2hop_reach |
|---|---|---|---|---|
| 产生 | 8 | 0.0 | **1.0** | 0 |
| 属于 | 18 | 0.0 | **1.0** | 0 |
| 掉落 | 13 | 0.0 | **1.0** | 0 |
| 需要 | 11 | 0.0 | **1.0** | 0 |
| 需要工具 | 3 | 0.0 | **1.0** | 0 |

**全部 5 种关系的 target 都是汇点（sink_fraction=1.0）；任何节点 2 步 forward 新可达 = 0。** 配方知识结构是深度 1 的星形森林：`配方:* -[需要/产生]-> 物品:*`、`块 -[掉落]-> 物品:*`，物品节点零出边。

## Forward Graph

Graph F = 生产建图路径原样（44 节点 / 53 边，方向语义不动）。注入点（以实际 schema 为准——`需要` 边挂在 `配方:*` 节点上，物品节点是汇点）：
- Input A = `配方:wooden_pickaxe:table:0`
- Input B = `配方:stone_pickaxe:table:0`
- Shared component = `物品:stick`（两个目标均 `需要` 它——图拓扑核实，dA=dB=1）

## Bidirectional Projection

Graph B = F 图 + **通用 reverse-edge projection**：对图中每条已有边 `src -[rel]-> dst` 自动生成 `dst -[rel]-> src`（关系名/类别/权重沿用原边；`kg.get_edge` 去重）。**无任何节点特判、无任务知识**（`run_exp_reldir.py:build_graph(reverse=True)`）。本轮新增 reverse 边 41 条（53→94 边，去重后）。

## Deterministic Probe（2 graph × 3 input condition，seed=1）

| probe | reach1 | reach2 | reach3 | activated_nodes | shared_act | ρ(score,act) |
|---|---|---|---|---|---|---|
| F/I1 | 3 | **0** | 0 | 6 | 0.417 | 0.265 |
| F/I2 | 3 | **0** | 0 | 6 | 0.417 | 0.275 |
| F/I3 | 5 | **0** | 0 | 9 | 0.833 | 0.325 |
| B/I1 | 3 | **10** | 6 | 22 | 0.253 | 0.542 |
| B/I2 | 3 | **9** | 6 | 21 | 0.239 | 0.513 |
| B/I3 | 5 | **12** | 6 | 27 | 0.475 | 0.645 |

**PROBE PASS（5/5 判据）**：
1. F 与 B 的可达性确实不同（F reach2=0 vs B reach2=9–12）✓
2. B 使 F 中不可达的结构进入可达集（I1 从 A 出发：B 图 3 跳内跨目标到达对方成分）✓
3. 激活可观测传播（B activated_edges > F）✓
4. 无隐藏规则（reverse projection 为通用逐边机制）✓
5. diffusion engine 未修改 ✓

## Reachability

- F 图：从任一节点 2 步 forward 新可达 = **0**（结构性；`物品:*` 13/13 为汇点）。
- B 图：注入后 2 步可达 9–12 节点、3 步累计 6 个跨目标节点。**双向投影恢复了多跳结构可达性。**

## Activation Landscape

| cell (n=10) | shared_act | enrichment | ρ(score,act) | gini | act_nodes | reach2 |
|---|---|---|---|---|---|---|
| F/I1 | 0.417 | 5.75 | 0.265 | 0.417 | 6.0 | 0 |
| F/I2 | 0.417 | 0.275 | 0.417 | 6.0 | 0 | 0 |
| F/I3 | 0.833 | 0.325 | 0.417 | 9.0 | 0 | 0 |
| B/I1 | 0.253 | **5.75** | **0.542** | **0.824** | **22.0** | **10.0** |
| B/I2 | 0.239 | **5.75** | **0.513** | **0.816** | **21.0** | **9.0** |
| B/I3 | 0.475 | **5.75** | **0.645** | **0.792** | **27.0** | **12.0** |

- F 图激活景观 = 双峰平台（注入节点 ~4.5 + 1-hop 直取 ~0.4），Gini 0.417，无梯度。
- B 图激活景观 = **有梯度的 graded 分布**（Gini 0.79–0.82，21–27 个节点按距离衰减分层）。

## Multi-input Convergence

- 连续 score（=1/(1+dA+dB)，纯拓扑）与激活的 Spearman 相关：**B 图 0.51–0.65 vs F 图 0.26–0.33，全部 p=0.002（n=10 配对）**。
- 同时注入（I3）下 B 图 ρ=0.645 为全表最高——**双输入同时注入时，激活景观与"多输入结构关联度"的对齐度最强**。
- 共享节点（物品:stick）激活的加性收敛：B 图 I3 0.475 > I1 0.253 ≈ I2 0.239（加性预测 0.491，实测 0.475——接近线性叠加）。

## Top-K Enrichment

multi_input_enrichment = P(multi|Top8)/P(multi|graph) = **5.75，全部 6 格相同**。解读：本图规模小（44 节点）、multi-input 节点（stick 及其配方中介）在两种语义下都因 1-hop 直接相邻而进入 Top-8——**该指标在本规模上不区分条件**。区分力来自 ρ 与 reachability。

## Input Coverage

Top-8 中各簇占比（均值）：F/I3：multi+single_A 为主（direct 需要 邻居）；B/I3：multi 0.0875、其余为按距离分层的广泛覆盖——B 的上下文覆盖面显著更广（27 vs 9 活跃节点），且与 score 排序一致。

## Competition / Inhibition

C3 = **UNAVAILABLE**（抑制需负权边，本图无；发射预算归一化为 diffuse_step 内禀组成，无开关；制造开关=修改核心，禁）。本轮无法测竞争/抑制的独立贡献。

## Statistical Results

60 run（2 graph × 3 input × 10 paired seeds），配对 Wilcoxon signed-rank（同 seed 跨图），效应量 r：

| input | metric | F 均值 | B 均值 | p | r |
|---|---|---|---|---|---|
| I1 | reach2 | 0 | 10 | 0.002 | 0.886 |
| I1 | activated_nodes | 6 | 22 | 0.002 | 0.886 |
| I1 | ρ(score,act) | 0.265 | 0.542 | 0.002 | 0.886 |
| I2 | reach2 | 0 | 9 | 0.002 | 0.886 |
| I3 | reach2 | 0 | 12 | 0.002 | 0.886 |
| I3 | ρ(score,act) | 0.325 | 0.645 | 0.002 | 0.886 |
| I1/I3 | shared_activation | 0.42/0.83 | 0.25/0.48 | 0.002 | 0.886（F 更高） |

全部 21 个对照 p=0.001953（n=10 Wilcoxon 最小可能值；逐 seed 差分方向完全一致），r=0.886。**确定性系统：差分方向 100% 一致，无随机方差。**

## Negative / Unexpected Results

1. **shared_node 激活 F > B**（I3：0.833 vs 0.475）：F 的 1-hop 直连使共享组件获得更高绝对激活；B 的激活被更广的可达集稀释。**"双向语义提高共享节点激活"不成立**——它提高的是激活景观与多输入拓扑的**对齐度（ρ）**与**覆盖广度**，不是单点激活幅度。
2. **enrichment 指标无区分力**（6 格全部 5.75）——小图 + 1-hop 直达使该指标饱和。
3. seed 变异仅来自干扰实体身份（环境变异），配方组件内的激活动力学完全确定——种子级配对在激活值指标上差分恒定。

## Limitations

1. B 图 reverse projection 是**实验语义**，不是生产建议——双向边是否适合生产图需后续评估（噪声传播、成本、汇点稀释效应本轮已观察到）。
2. 图规模小（44/94）；大规模下的结论未检验。
3. 无 LLM 决策环节（按设计）；"激活差异 → 行为差异"的链路未在本轮检验。
4. C3（competition/inhibition off）unavailable。
5. multi-input 簇在本拓扑中距输入均为 2 跳以内——更深结构未测。

## Implications for FAS

- **假设 B 成立、假设 A 排除**：扩散引擎本身工作正常（能量守恒、fire-once、分级激活全部按设计）；多输入收敛失败的根本原因是**关系方向语义使配方结构对 forward 扩散不可穿越**。
- 生产含义（仅陈述事实，不做修复决定）：当前 FAS 中"从目标到达成分"由符号闭包规划器承担；激活扩散的可穿越子图只有观察共现 fabric（routing v2 正效应的来源）。两条路径的分工是架构事实。

## Q1–Q7 事实回答

| Q | 回答 |
|---|---|
| Q1 配方图为何无法支持多跳扩散？ | 全部 5 种关系的 target 都是汇点（sink_fraction=1.0）；`物品:*` 13/13 零出边；整图为深度 1 星形森林 |
| Q2 归因？ | **edge direction / relation semantics**（假设 B）。diffusion engine 排除：同一引擎在 B 图上正常多跳传播（reach2 9–12、27 节点点亮） |
| Q3 reverse projection 是否恢复结构可达性？ | **是**（reach2 0→9–12、reach3 0→6，p=0.002，r=0.886，10/10 seeds 方向一致） |
| Q4 可达性恢复后激活是否真传到 shared component？ | **是**（B/I1 shared=0.253 且 I3=0.475； activated_edges 全程 >0） |
| Q5 shared component 是否获得更高激活？ | **部分**——绝对值 F 更高（1-hop 直取 0.83）；但 B 图中同时注入产生**加性收敛**（I3 0.475 > I1 0.253，≈I1+I2 线性和 0.491）且整个景观与多输入拓扑对齐度翻倍 |
| Q6 同时注入 A+B 是否产生不同景观？ | 是——B 图 I3 的 ρ=0.645（全表最高）、reach2=12、27 节点点亮；F 图 I3 仅加性叠加于 1-hop 邻居 |
| Q7 是否足以支持 multi-input convergence hypothesis？ | **机制层面：是**——双向语义下，激活景观与多输入拓扑 score 的秩相关翻倍（0.33→0.65）且同时注入产生加性收敛。**行为层面：未检验**（本轮无 LLM 决策）。按 §十九措辞：实验证据表明 relation directionality 是当前配方图中扩散传播的限制因素；双向语义是否应进入生产图需后续实验 |

## 产物

| 文件 | 内容 |
|---|---|
| `relation_direction_report.csv` / `relation_direction_audit.json` | 静态逐关系审计 |
| `probe_results.csv` / `probe_nodes.csv` / `probe_check.json` | 6 个确定性探针 |
| `campaign_results.csv` / `campaign_nodes.csv` | 60 run（2×3×10 paired） |
| `paired_statistics.csv` | 21 个配对检验 |
| `raw_results.jsonl` | run/step/node 级原始记录 |
| `RELATION_DIRECTION_EXPERIMENT.md` | 本报告 |
