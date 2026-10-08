# PRE_EXPERIMENT_AUDIT — 投稿前关键机制实验（A/B/C）实验前审计

- **日期**: 2026-09-28
- **审计方式**: 全程只读（grep/sed/读文件），零代码修改。基线 = 当前工作树（HEAD `4685726`，工作树有大量未提交修改——本审计覆盖的是这些文件在磁盘上的现状；基线以 HEAD 与工作树的组合如实记录）。
- **遵守声明**: 本审计先于任何代码修改。后续每个代码修改进 CHANGE_LOG（C17 起），全部命中"最小修改/记录层/环境层"，不允许触碰核心认知机制。

---

## 1. 状态快照

| 项目 | 状态 |
|---|---|
| git HEAD | `4685726`（"隐私: 图谱样本.json 退出跟踪"）；工作树含未提交改动（app.py、autonomy.py、diffusion_engine.py 等 + 一批未跟踪新模块） |
| 实验模式 | `experiment_mode.py` 三态（normal / learning_closed_loop / sandbox），sandbox = 调制屏蔽 + seed + [TAG] 日志；由 run 脚本环境变量注入 |
| 运行图谱 | live 服务器 1,115 节点（API 默认视图）；全量快照 ~3,497 节点 |
| 已有实验产物 | run 族：minecraft 2 / sandbox 43 / ablation 77 / baselines 8 / prior_conditions 7；存档 `matrix_oak_planks_final.json`、`matrix_cobblestone_final.json`（各 11 组消融 ×3 seeds） |
| 沙箱环境 | `scripts/sandbox_lab.py`：SandboxWorld（blocks/placed/recipe_table/block_meta/smelt_map）+ fake bridge + Clock(16s/tick) + build_stack（11 开关）+ C12 探索量程修正 + flush_causal |
| 因果账本 | live 89 aggregations / 78 hypotheses / 29 promoted（真实 MC 行为积累） |

## 2. 可复用清单（逐实验）

### 通用
- `scripts/sandbox_lab.py` build_stack —— 11 个条件开关全部在：`diffusion_on`（β_spread=0.0=激活不传播仅直写+decay）、`causal_on`、`prior_on`、`goal_on`、`writeback_on`、`self_goal_on`、`gap_on`、`prior_level`（A/B/C）、`inherit_graph`、`seed`。
- **inherit_graph 机制**（关键事实）：`KnowledgeGraph.load` 转录节点/边/**激活值**（Node.to_dict/from_dict 序列化 `activation` 字段, graph_model.py:231-253）；Ep2 继承 Ep1 结尾的激活分布。**experience timeline 不继承**（新 base dir → 新 ExperienceTimeline → CausalLearner 账本清零）→ 跨 run 的聚合/假设/action_prior 记忆只能靠同进程连续两个 episode，或另接 experience_replay_state.json。
- RunRecorder（experiment_recorder.py）：family/mode/seed/label；start/log/snapshot(graph_dict)/graph_delta/counter/finalize；增量 flush（C07/C08 已修）；events.jsonl + snapshots + metrics.json + manifest。
- flush_causal（远期 sweep 一次性关因果窗；沙箱加速时钟下必须收尾调用，`沙箱因果窗需自动 sweep`记忆）。
- prior_extra 注入点（SMELT_PRIOR 等已验证的老用法）。
- setup_world（run_ablation_matrix.py）：预置世界具置 + crafting_table 矿区供给（C11）。

### 实验 A（Knowledge Write-back → Future Reuse）
- **知识写入形态（已确认）**：`CausalLearner.promote_to_kg`（experience.py:767 起）是唯一生产写回点；MIN_SUPPORT=3 / HYP_CONFIDENCE=0.60；写入 semantic 空间「操作:X」-[导致 conf]->「变化:Y」；**promoted 后 `engine.mark_active(...)`**（experience.py:845）——写回即点亮（active-frontier 不变量在场）。
- **写回消费通道（已确认）**：
  1. `_score_action` attention 分量 = max(reason 节点 activation)/5（autonomy.py:2696-2700）——候选评分消费激活；
  2. 能力召回 = 激活/向量相似度（capability_graph 候选发现）；
  3. gap 通道（见下）。
- **Ep2 的信息继承**：inherit_graph 带图结构 + 激活快照 → 知识节点在 Ep2 可被扩散/衰减继续作用。
- **pilot 前置验证点**：沙箱里 causal_on 场景能否在 tick 窗内真实促成"操作:X(动作回执)"→"变化:Y(背包变化)"假设并通过支持度晋升（S5 已验证 12hyp/9agg，机制在场）。

### 实验 B（Attention/Diffusion → Relevant Knowledge Access）
- **-Diffusion 操作化已精确**：`diffusion_on=False → beta_spread=0.0`（sandbox_lab.py:522）——激活**直写保留**、**传播截断**。这正是"只切断扩散"的干净对照。
- **扩散的已知消费节点**：
  1. `_score_action` attention 分量（上述）；
  2. **gap 通道**（autonomy.py:1466 `_gap_candidates`）：`prior.enabled` 门控；prior_knowledge.open_gap 开缺口（对象已知用途未知 → exploration_gap_item 节点 + 指向边 + hub 聚合账）→ gap 节点 attention floor 0.9 直写 + engine.mark_active + "指向 物品:x"扩散抬手（TTL 1800s, maxn=3）；
  3. world_prior `_mark()`：闭包建完只 touch 激活（**不反向驱动闭包**）。
- **表格驱动的计划闭包**：`world_prior.build_recipe_closure` = 纯 BFS，数据源 = 运行时 bridge（recipe_for/block_meta）+ 手册先验（prior_extra）——**不读取 activation**。已由矩阵验证：短链两任务 -Diffusion 动作数/达成 tick 零差分（oak [2,2,2]@[6,6,6]；cobblestone [9,9,9]@[15,15,15]，11 组全绿，C12 伪影排除后不变）。

### 实验 C（Cross-task Transfer）
- **同一进程连续双 episode 可行**：build_stack 每次新建 base dir（timeline 隔离），但**可在同一进程依次构建两个 stack 并手传图**（inherit_graph 传 KG JSON；因果账本若要跨 run 需共享 timeline 路径或直接同进程延续——run 脚本层可控制）。
- action_prior 通道（experience.py `action_prior`）：从 aggregations 读"这件事我行不行"，候选评分消费——跨任务同族动作的成功率记忆是 transfer 的真实机制候选。
- 沙箱 world.recipe_table / block_meta 是自由字典——任意对象名可配（下节）。

### 环境可扩展性（三实验共同基石）
- **非 MC 词汇环境完全可行，零核心改动**：
  - 技能层 `CraftItem.resolve_recipe` → 静态 RECIPES 查不到 → `_runtime_recipe(ctx, item)`（skills/crafting.py:101）→ `bridge.recipe_for(item)` → sandbox `_mb.recipe_for` = `world.recipe_table.get(item)`（sandbox_lab.py:440）→ 配方表里有即能 execute；
  - 计划闭包 `_recipe_for` 走同一 bridge 数据源 → **规划与执行对同一配方表一致**；
  - world.call `/craft` 需要 needs_table 检查与 take_items，与对象名无关；
  - 即：**对象/配方/方块/掉落/熔炼均可自由命名**，构造任意深度配方链、任意 hidden relation，不需要改 skills/diffusion/autonomy/world_prior 任何一行。

## 3. 缺失清单（实验需要但目前没有）

| 缺什么 | 为哪个实验 | 归属层 |
|---|---|---|
| 激活时间序列采样（run 脚本层记录每 tick 指定知识节点的 activation 曲线） | B（核心指标 activation_latency / 到达 tick） | 记录层（RunRecorder 扩展或 run 脚本内采样） |
| 扩散事件采样（diffusion_engine 现仅启动横幅，无逐事件日志；需 run 层外置采样：每 tick 新激活/传播到的节点） | B（activation_before/after、diffusion events/paths） | 记录层 |
| 两段式实验 runner（同一进程 Ep1→Ep2 连续构建，控制 inherit_graph/账本端口） | A（Ep1/Ep2）、C（Transfer vs No-Transfer） | run 脚本（新脚本） |
| 写回验证探针（pilot 断言：promote 真发生、节点/边计数、mark_active 后激活值） | A | run 脚本 |
| 自制对象环境的配方配置（深度 ≥4 的链 + hidden relation 场景 + gap 目标） | B、C | 环境数据（sandbox 配置字典） |
| 检测器：每 tick 记录 action_type/目标 → first_relevant_action / redundant_actions / invalid_actions 统计 | A/B/C（规范字段表） | run 脚本 + recorder 已有事件复用 |

> 结论：**全部缺失项都在 run 脚本 / 记录 / 环境数据层**。核心机制（diffusion_engine、autonomy 决策、experience 归属与晋升、world_prior 闭包、skills 执行）**一行都不需要改**。

## 4. 审计发现的 bug

- 本轮只读审计**未发现新的生产代码 bug**（对照检查过：promote mark_active 无空引用（try/except 包裹）；open_gap 幂等+封顶退役；inherit_graph 索引重建齐全；-Diffusion 语义精确）。
- 已知在账的缺陷（不必此刻修，与本实验无关）：C10/C11（iron 链沙箱驱动缺口，furnace_take 排不上）、C12 类（仿真时钟多处壁钟/时钟失配——**每个新 run 场景都要自查量程**）、`/furnace_take` 无 "taken" 字段（sandbox quirk，记录在案）。

## 5. 设计限制（直接影响三实验的机制事实，报告必须如实呈现）

1. **计划闭包是表格驱动的**（world_prior 只吃 bridge 表格 + 手册先验）：只要任务目标在配方表里可闭包，达成路径不依赖 activation/diffusion——**行为达成层没有 diffusion 的因果空间**（矩阵零差分是结构保证，不是假阴性）。
2. **-Diffusion 只截传播不动直写**：写回 mark_active 与 gap floor 直写在两种条件下等效；可测差分只在"依赖传播到达关联节点"的知识访问上（→ B 的观测对象 = 知识节点的激活到达时序 + gap 余波，不是任务达成）。
3. **技能 RECIPES 只有 MC 词汇**，非 MC 对象必须走 `_runtime_recipe` bridge fallback；而 fallback 与闭包同源 → 非 MC 环境里"执行与规划一致"是免费的（见 §2 末尾）。
4. **inherit_graph 连激活值一起继承**、timeline 不继承：A/C 的"知识在 Ep2 可访问"通道是图（带激活）+ 同进程账本二选一，report 里要写清用的是哪条通道。
5. prior_level A（表格也清空）下 0/6 已证——**计划知识缺位图里也没有替身**（写回只写 操作/变化 因果对，不写配方）。非表格知识的 reuse 只能做"对象用途型"知识（用 X 产生 Y），不能做"配方型"。
6. goal TTL / gap TTL（1800s）与沙箱加速时钟的关系要在每个场景核对（已有 flush_causal 惯例，goal 生命周期同理自查）。

## 6. 必要修改（最小集；全部在 run 脚本/记录/环境层）

计划按 CHANGE_LOG 编号 C17+ 逐条记录（time/file/reason/before/after/bug_or_design/design_preserving）。

- **M1（记录层）**：run 脚本 activation/diffusion 采样模块（每 tick：目标知识节点集合 activation 快照；标记 promote/gap/floor 直写时刻；衍生 activation_latency / path）
- **M2（记录层）**：episode 统计器（first_relevant_action、redundant_actions、invalid_actions、total_actions、task_completion_tick —— 从 recorder 已有 ACTION 事件流派生）
- **M3（run 脚本）**：episodic runner（两段式连续 episode + inherit_graph 接线 + 各条件变体的 stack 组装；A 的 Ep1=发现段/Ep2=复用段；C 的 Transfer/No-Transfer 段）
- **M4（环境数据）**：自制对象链配置（配方深度 ≥4 的通用对象世界；B 用 hidden-relation 场景；配方表在 A/B 条件下持同一套，只吃图的差异）
- **M5（验证探针，脚本内断言）**：pilot 期断言 promote 计数 >0（A）、gap 打开数、写回节点在 Ep2 的激活可达性——失败即修正实验条件，不改核心机制

不需要的：任何 diffusion_engine/autonomy/experience/world_prior/skills 修改。无 BLOCKED_ARCHITECTURAL_CHANGE 请求。

## 7. 绝对禁止（红线）

- 禁止 `if task == "..."` / 等价形式的任何分支（包括按 goal/对象名特判行为）
- 禁止把答案写进图谱（answers written into KG）、硬编码配方进图去让闭包/评分作弊
- 禁止修改 diffusion_engine、autonomy 决策（候选/评分/缺口/目标生命周期）、experience 归属/晋升阈值、world_prior 闭包、skills 执行逻辑
- 禁止调整成功判据、删除失败 run、隐藏异常数据、人工接管达成
- 禁止大规模重构（→ 先写 BLOCKED_ARCHITECTURAL_CHANGE.md 等人工）
- 每个代码修改必须有 change_id 账目

## 8. 三实验构造草案

### 实验 A：Knowledge Write-back → Future Reuse
- 条件 B（write-back）：Ep1 真实经历（用户式：对象 X 的用途链通过动作-观察回执形成因果对，支持度达标 promote 入图 + mark_active）→ Ep2（相关任务，inherit_graph 带 Ep1 图谱）。
- 条件 A（no knowledge）：同 Ep2，Ep1 换成 causal_on=False / 不晋升 → 图里没有该知识。
- **关键诚实设计**：Ep2 的达成知识**只在图里、不在配方表**（对象用途型知识：表里没有 X→Y 配方，只有 Ep1 因果写回的"用 X 得到 Y"）→ 闭包无路时 Ep2 唯一信息源 = 图写回 → reuse 的行为/激活证据可分离。
- 观测字段按规范：knowledge_discovered / writeback_time / nodes_added / edges_added / knowledge_activation_time / knowledge_retrieval_time / knowledge_reuse_event / first_related_action / total_actions / redundant_actions / invalid_actions / task_success / task_completion_time / knowledge_id / origin_episode / reuse_episode / activation_path。A4 八问逐条回答。

### 实验 B：Attention/Diffusion → Relevant Knowledge Access
- 两个条件：Full vs -Diffusion（β_spread=0），其余机制不动。
- **先决决策（审计结论）**：任务达成层不做预期（结构上无扩散空间——负结果是报告内容）；**正结果观测点 = 知识访问层**：目标知识节点激活到达时序（写回/floor 直写事件后，Full 条件下传播到离源距离 >0 的知识节点的时间 vs β=0 永不到达/仅靠直写）、gap 余波中的评分差分（gap 节点 floor 相同、其"指向"对象的知识在 Full 下被点亮产生额外候选与注意抬升）。
- 任务：自制对象链（深度 ≥4，比 S1–S11 长），相关知识初始远离注意峰值（seed 世界无该对象视野）。
- 指标：time_to_relevant_knowledge / relevant_nodes_activated / activation_latency / graph_distance_to_useful_knowledge / irrelevant_actions / redundant_actions / total_actions / task_success + activation_before/after + diffusion events/paths。负结果（达成层零差分）按负结果如实入库。

### 实验 C：Cross-task Knowledge Transfer（reuse + recombination）
- 两任务共享结构：任务1 链 A→B→C；任务2 = B→C→D（重组+延伸，不是背诵同一链）。
- 两条件：Transfer（Ep2 继承 Ep1 全图+（同进程）因果账本）vs No-Transfer（Ep2 干净图+空账本）。同一 world 同一配方表（环境知识对两条件相等）。
- 机制通道公开预判：a) 闭包（表格）两条件同样可及 → 达成率可能"都 6/6"；b) 真实 transfer 差分出现在 action_prior（同族动作成功率记忆）与图激活（Ep1 写回节点在 Ep2 的评分抬升）→ 观测：达成 tick 差、首动作路径、冗余动作差。若零差分 = 负结果（"表格可达性遮蔽 transfer 效应"是论文信息）。
- 观测字段：transfer_benefit（tick/动作差分）、shared_structure_usage、recombination_event、no_transfer_ceiling。

## 9. Pilot 与统计计划（遵守规范）

- 每条件 pilot = 1 run：验证 recorder 落盘、激活采样清晰、promote/gap 探针、achievement 判定、无核心代码触碰。
- pilot 验证后 ≥5 seeds/条件（沙箱同 seed 全确定：每种子 1 run，注明 deterministic 语义）；样本不足时报告"descriptive evidence only"，不做显著性声称。
- 统计：mean/std/median/success rate/effect size（或 descriptive-only 标签按样本量）。
- 失败 run 全部保留（目录不删、report 列负结果）。

## 10. 停止条件

- [A] A 实验有效（写回知识在 Ep2 被访问并影响行为——或如实记录该链路在哪一环断裂）
- [B] B 实验有效（扩散对知识访问有可测差分——或达成层零差分作为负结果证据）
- [C] C 实验有效（transfer 有可见差分——或表格可达性遮蔽效应的负结果）
- [D] 全部原始日志存档（events/snapshots/metrics/manifest）
- [E] 全部失败保留
- [F] 声明审计完成（PAPER_CLAIM_AUDIT.md）

达成 [A]-[F] 后停止，不再添加模块。