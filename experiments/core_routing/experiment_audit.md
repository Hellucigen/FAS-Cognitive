# experiment_audit.md — Core Routing Campaign（C33/C34）

- 日期：2026-09-29
- git：main @ 4685726（工作区含 C33/C34 新增 runner，未提交）
- LLM：MiMo `mimo-v2.6-flash`（`https://api.xiaomimimo.com/v1`），temperature=0，max_tokens=40，1 call/decision step，自动重试×3 退避
- 决策头协议：LLM 从环境推导的**同一动作菜单**中选择；四条件唯一自变量 = 决策前上下文的选择方式
- seeds：20 配对（1–20），同 seed 同世界同任务实例
- 执行器：透明沙箱技能原语（gather/craft/place/explore/eat），四条件一致

## 1. 条件定义

| 条件 | 决策前 LLM 获得的额外信息 |
|---|---|
| llm_direct (A) | 无（仅任务+事实+环境态+菜单） |
| llm_history (B) | 原始经历流最近 8 条（无精选） |
| llm_rag (C) | BGE（bge-small-zh-v1.5，本地）相似度 top-5 经历条目（k 预注册=5） |
| fas_full (D) | 真实 FAS 机制：KnowledgeGraph + DiffusionEngine（activate_from_inputs/decay/diffuse/get_topk）+ analyze_cognitive_demand（三层 demand/gap/routing）。无原始历史 |
| random_ctx (E) | 与 D 等量随机经历条目（反事实对照，§19） |
| D-noact / D-nodemand / D-flat | 消融：−扩散 / −demand 注解 / 扁平图文本相似度（T2/T3 only） |

任务：T1 知识复用（2 phases）/ T2 干扰物抑制 / T3 环境变化重路由（oak→birch 同构替代，2 phases）/ T4 竞争目标（饥饿漂移）。任务事实（配方）在 base prompt 中四条件同权（§7.1）。

## 2. Bug 记录与重跑（§28）

| id | bug | 影响 | 处置 |
|---|---|---|---|
| C33-b1 | `total` 字典误删（NameError） | smoke 全 40 run 失败 | 修复后 smoke 全量重跑 |
| C33-b2 | 节点 label="action" 不在图谱合法枚举 | D 族全部 error | 改 label="procedural"；smoke 重跑 |
| C33-b3（设计缺陷，正式跑前修正） | T2 目标文本含"忽略无关资源"提示 → 全条件天花板；T3 初版为盲方向搜索（温度 0 固定向北）→ 测不出重路由 | 任务设计 | 正式跑前重设计：T2 中性目标文本；T3 改同构替代资源重路由（oak 消失→birch 可合成且在视野内） |
| C33-b4 | menu() 未用 world 级配方表 + 回复解析只认序号 | T3 phase2 craft 不进菜单 | menu 改 w.recipe_table；解析支持动作名 |
| **C34** | **runner ingest 用正则全量吸纳观察文本英文词（实体:near/实体:inventory 等非实体节点）污染图谱 Top-k——违反 FAS 生产感知设计（结构化映射到已知实体）** | **首轮正式 D 族全部结果** | 修复为实体白名单（与 base prompt facts 同源，四条件同权）；**重跑 fas_full（80 run）与消融（120 run）全部受影响实验**；首轮结果保留于 `fas_full_preC34/ablations_preC34`（已重命名归档） |

首轮（pre-C34）结果摘要（保留备查）：FAS 在 T1 显著劣于全部基线（success 0.8 vs 1.0，p=0.045；且劣于 random_ctx，效应量 0.83）、t_reroute 显著慢（1.35 vs 0.05–0.4）、token 成本 1.5–2×。C34 修复后的正式结果见 statistics.csv。

## 3. 公平性核查（§7）

- 同一 LLM/参数：是（同一 LLMHead 配置逐字记录于 raw_results 每条）
- 同一环境信息：是（env state 文本由同一构建器生成）
- 同一先验：是（配方事实在 base prompt；T3 phase2 的新配方 facts 四条件同权）
- 同一动作菜单：是（环境推导，条件无关）
- 同一经历流：是（所有条件写入同一格式 store；B 读原始、C 检索、D 入图）
- LLM 预算：每决策 1 次调用、同 cap；token 用量逐任务记录（API usage 字段）

## 4. 威胁 to validity
1. 决策头协议中 FAS 不控制执行循环（生产中 FAS 的自主循环是 LLM-free 的）；本实验测的是"FAS 作为信息选择器"的价值，不是"完整 FAS agent"的价值。
2. 小型经历流（≤30 条）：检索/激活的规模效应未测。
3. temperature=0：单样本确定性策略，无采样方差。
4. T2 天花板：LLM 决策头在信息充分的观察下天然忽略干扰物。
5. C34 修复仅针对 runner 感知模拟的粗糙性；生产感知与实验 ingest 仍有差距。

## 5. 排除的 run
无删除。全部 error run（C33-b1/b2 时期）保留在 smoke 目录；正式 campaign task_error=0。

## 6. 最终结果（C34 修复后，n=20 配对，Wilcoxon signed-rank）

### 主对照（fas_full vs 基线）
| 任务 | 指标 | FAS | 最强基线 | p | 结论 |
|---|---|---|---|---|---|
| T1 复用 | success | 0.85 | 1.0（全部四基线） | 0.083 ns | 方向性劣后 |
| T1 | efficiency | 0.49 | 0.87 (direct) | <0.001 | **显著更差** |
| T2 干扰 | success | 1.0 | 1.0 | — | 全天花板；distractor 全零 |
| T3 重路由 | t_reroute | 1.1 | 0.2 (history) | 0.039 | **显著更慢** |
| T3 | success | 0.95 | 0.95–1.0 | ns | 无差 |
| T4 竞争 | success | 0.85 | 0.85–1.0 | ns | 无差 |
| 全部 | prompt_tokens | 1.4–2.0× | — | ≤0.02 | **显著更高** |

### 消融（T2/T3）
D-noact / D-nodemand / D-flat 与 fas_full 全部指标无显著差分（除 D-flat 检索精度更高 p<0.001）——负结果不由单一组件承载。

### 反事实（random_ctx）
随机上下文在 T1/T4 success 上 1.0 vs FAS 0.85（ns），效率 3/4 任务显著优于 FAS——短经历流中多数记忆条目天然与目标相关，随机≈相关；选择质量只在存储大/干扰富时才可能起作用。

## 7. 结论（§26 口径，情况 B/C）

**H1 被否定**：在四个任务的全部主指标上，FAS 显式认知资源路由未表现出相对 LLM-Direct / History / RAG / Random 的可测优势；token 成本显著更高（1.4–2×），context efficiency 因此显著更差；T3 重路由显著慢于 history。消融间无系统差分。

**机制解释**：四个任务的决策所需信息全部已在逐步观察中（完全可观察状态）——记忆上下文的边际价值≈0，任何附加上下文只产生 token 成本与噪声。FAS Top-k 焦点还混入动作/结果节点（动作与结果共激活的结构性后果），检索精度 0.51–0.62 vs RAG 1.0。**路由价值的成立条件是"决策时所需信息不在当前观察中"的信息不对称场景——本实验族不含此类场景，且如实报告。**

## 8. Q1–Q5 回答（§31）
- Q1（vs History）：否——history 相等或更好（T3 重路由更快 p=0.039，T1 success 更高 ns）。
- Q2（vs RAG）：否——RAG 处处相等或更好。
- Q3（选择 vs 状态）：协议上只有信息选择在变；在信息充分的观察下其边际价值 ≤0。
- Q4（重路由速度）：否——FAS 更慢或持平。
- Q5（消融）：无系统变化（全部 ns）。
