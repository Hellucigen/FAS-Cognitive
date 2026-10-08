# PREREGISTRATION_REVISION.md — T3 呈现等价修订对照(2026-10-05,任何新 run 之前冻结)

> **POST-HOC 声明(适用于本文件的一切)**:本实验是在看到
> persistent_state_controls 的 T3 结果与 ADDENDUM_T3_AUDIT.md 的类别 1
> 判定之后设计的后续对照。它**不是**原 PREREGISTRATION.md 的修订,
> 不并入原预注册的假设族、p 值族与 Bonferroni(m=8)校正;本实验
> 自成一个独立的比较族(m 在 §6 声明)。原预注册文件保持原样不改。
> 本文件一旦提交,条件规则、指标、种子、阈值、解释表不再更改。

目录:`experiments/persistent_state_controls_revision/`。
零生产修改;新增代码全部在本目录;沿用 persistent_state_controls 的
harness 函数(`facts_from_stream`、`KNOWN_ENTS`、`run_task_c` 的任务
设置、`make_fas`/`phase1`、`build_ctx`、`run_menu_step`、`gate_from_context`)、
任务 T3、Phase 划分、paired seeds(0..9)、指标与统计方法。
本轮结束后不再追加任何对照(不做 C3c/C3d、不调规则);结果出乎
意料只报告,不救。

## 1. 研究问题(唯一)

在失败证据**呈现等价**之后:
(a) C1(FAS-full)是否仍优于扁平/无激活对照(H_R1/H_R2);
(b) 负极性标记与 2 跳可达各自的贡献是什么(H_R3)。
本实验只回答 T3(失败信息行为)。T2 的等效性结论不受本修订影响
(REVISION_PROPOSAL 判定,本轮不扩 T2)。

## 2. 条件

| 代号 | 名称 | 来源 |
|---|---|---|
| C1 | FAS-full | **复用** PSA `raw_results.jsonl` T3×FAS-full×seeds0-9(不重跑;论证见 §8) |
| C2 | FAS-reset | **复用** PSA `raw_results.jsonl` T3×FAS-reset×seeds0-9 |
| C3b | Flat-Store-FailureFirst | 新实现,§3.1 |
| C4b | Graph-2Hop | 新实现,§3.2 |
| C4c | Graph-2Hop-NoPolarity | 新实现,§3.3 |

## 3. 新条件实现规则(全文,朴素且固定)

### 3.1 C3b Flat-Store-FailureFirst
- 存储与 C3 完全相同:`facts_from_stream`(同一 Phase-1 事件流、同一
  记录结构、同一 result[:64] 截断、同一 FAIL_PREFIXES 极性判定);
  Phase-3 内与 C3 相同的自追加规则(每步把上一步存为新事实)。
- 选择规则与 C3 唯一的差异是排序键:
  C3:(polarity **降序**, index 降序) → 成功事实优先,失败被挤出;
  **C3b:(polarity 升序, index 降序)** → **失败事实优先**,同为失败
  时新的在前;命中集与补齐逻辑、8 条预算、2 行/条的渲染与截断、
  注入模板(`[cognitive-context mode=flat-store-failurefirst]` +
  `selected nodes:` + `- {id} (act {score})`,score=1.0−0.05×rank)
  与 C3 逐字符相同。
- 除排序键外任何实现差异 = 实现无效。

### 3.2 C4b Graph-2Hop
- 图与 C4 完全相同:`make_fas(w,"full")`(含写回边)后
  `fc.ef_context = True`(与 C1/C4 一致,负极性边在图中),
  Phase-1 同一 `phase1()` 调用;Phase-3 每步仍执行与 C4 相同的
  `ingest`,不调用 step_dynamics/diffuse。
- 选择规则:当前 observation 实体(经 `entities_of`/`node_id`)为种子,
  取**双向 2 跳邻接**(BFS 深度 2;种子→1 跳→2 跳;无衰减、无扩散,
  仅结构可达)。每个节点的排序属性 = 发现它的边的权重(种子=1.0);
  排序:权重降序、BFS 插入序;截断 8;模板同 C1/C4,
  mode=graph-2hop,act 字段=边权(种子 1.0)。
- 有效性验证(审计):working set 中不得出现 3 跳及以上才可达的节点。

### 3.3 C4c Graph-2Hop-NoPolarity
- 与 C4b 逐行相同的 Phase-3 选择规则与模板(mode=graph-2hop-nopolarity);
- 唯一差异:Phase-1 与 Phase-3 的图以 **`fc.ef_context = False`** 构建/
  演化——`ingest` 不写 `事件:failed:*` 节点、不写 −0.8 负极性边与
  +0.9 结果边(即无负极性标记;写回路径的 `动作:→结果:` 边与
  `结果:tool_missing:...` 节点仍在,与 C4b 相同)。
- 结构断言(预检):C4c 图中不存在负权边;C4b 图中存在 ≥1 条负权边。
- C4b vs C4c 的差 = "负极性标记"的单独贡献;若预检发现两条件的
  失败事实可见性不同(即极性边是失败事实进入 2 跳可达集的唯一
  通道),该混杂如实记录并限制 H_R3 的解释。

## 4. 呈现等价预检(硬门槛;零 LLM;不通过则不得运行正式实验)

对每个 seed(0..9)× 每个条件(C1/C3b/C4b/C4c),在 **Phase-1 存储
基线**上生成 T3 第 0 步注入文本(C1:phase1 后执行 run_task 的
step-0 路径 ingest(obs0,None,"init")+step_dynamics+build_ctx;
C3b/C4b/C4c:phase-1 存储 + 各自选择规则 + T3 初始 obs)。
记录:失败串 `tool_missing` 是否出现、其首个出现的**条目槽位**
(C1/C4b/C4c:selected nodes 行号 1..8;C3b:事实条目号 =
⌈行号/2⌉)、含失败前缀的行数、注入文本行数、字符数、
估算 token 数(字符/4)、失败行是否被截断。

**通过标准(全部满足才运行):**
- P1(可见性):每个 seed 上,C3b、C4b、C4c 的注入文本中
  `tool_missing` 均出现(与 C1 相同),且失败行未被 64 字符截断
  吞掉(串完整可见)。
- P2(位次):每个 seed 上,槽位满足
  slot(C3b) ≤ slot(C1)+2、slot(C4b) ≤ slot(C1)+2、
  slot(C4c) ≤ slot(C1)+2。
  (说明:REVISION_PROPOSAL 的"位次差 ≤2"在此定为一侧界——
  混杂机制是失败信息**缺失/被挤出**,对照把失败排在更前不构成
  混杂;"失败优先"规则按设计会把失败放在槽位 1,绝对差判据会
  自相矛盾地否决其自身目的。该定稿选择在此声明,不事后改。)
- P3(长度):每个 seed 上,各对照注入字符数 ∈ [0.5, 2.0]×C1。
- P4(结构):C4c 无负权边;C4b 有负权边;C4b 与 C4c 的
  `结果:tool_missing` 可达性差异如实记录(见 §3.3 混杂条款)。

**信息等价性(沿用原审计)**:C3b 事实集 = C3 事实集(同一
`facts_from_stream` 输出,仅排序键不同)——程序化断言;
C4b/C4c 与 C4 的图差异仅为 §3.3 声明的极性边(结构断言)。
**状态隔离**:C3b/C4b/C4c 的注入只来自其自身存储;Phase-3 无法
访问 Phase-1 原始流文本(独立对象;无 inventory echo 通道)。
**泄漏**:存储中不含 T2/T3 目标提示或答案句(沿用原审计检查)。

## 5. 假设与指标

主指标 = T3 `fail_repeats`(对已作废目标 gather_iron_ore 的失败重复数,
决策预算 12)。次指标:`first_pivot`(首次转向 gather_oak_log/craft_*
的步号;None=从不转向)、转向率、触顶率(fail_repeats=12)。

- **H_R1**:C1 vs C3b —— 失败呈现等价后,图+激活是否仍有贡献。
- **H_R2**:C1 vs C4b —— 扩散激活相对纯 2 跳结构邻接的贡献。
- **H_R3**:C4b vs C4c —— 负极性标记的单独贡献。
- **H_R4**(对照有效性):C3b/C4b/C4c 各自 vs C2 —— 确认对照携带
  有用信息。若任一对照在 T3 上仍**劣于** C2(方向:fail_repeats
  更多且校正显著),视为该对照实现无效:报告并停止,不得自行再改
  (停止条件,见 §9)。

## 6. 统计

- 配对单位 = seed,n=10(0..9),与 PSA/原 controls 相同。
- 检验:Wilcoxon signed-rank,**双侧**(exact,n≤25)。
- 本实验为独立比较族:**Bonferroni,m=6**(H_R1、H_R2、H_R3、
  H_R4×3)。报告原始 p、校正 p(对比 α=.05;等价口径:原始 p
  对比 .05/6=.00833)、效应量 Hodges–Lehmann 中位差 + bootstrap
  95% CI(10k 重采样,固定种子)、r = Z/√n。
- 功效声明(运行前承认):n=10 下仅 ≈9/10–10/0 级差异可稳定检出;
  "未检出差异"一律不写成"等价"。
- 等价边界(预注册,仅用于 fail_repeats):HL 中位差 ∈ [−1, +1] 且
  90% CI 完全落于 [−1, +1] 内,方可写"数值等价";否则写
  "未检出差异,功效有限"。

## 7. 解释表(结果出来前锁定;只用表中措辞)

| 结果模式 | 结论(仅此措辞) |
|---|---|
| C1 与 C3b 未检出差异 | 失败可见性是 T3 原效应的主因;图结构的独立贡献未被证明(呈现等价条件下无差异,受 n=10 功效限制约束) |
| C1 显著优于 C3b **且** 优于 C4b | 呈现等价后仍有差异:图结构/激活可能有独立贡献(限 T3、n=10);仍需讨论 C1 与对照在其余通道上的残余差异(见 §8 不对称声明) |
| C1 优于 C3b 但 ≈ C4b(或相反) | 如实写明拆分:差异可归到"排序规则 vs 结构可达"的哪一侧 |
| C4b 显著优于 C4c | 负极性标记有独立贡献(限 T3、n=10) |
| C4b 与 C4c 未检出差异 | 负极性标记的贡献未检出(功效限制声明必附) |
| C3b 或 C4b 优于 C1 | 如实报告:呈现等价的扁平/2 跳对照优于 FAS——图/激活在此任务无优势甚至有害 |
| 任一对照仍劣于 C2(校正显著) | 该对照实现无效:报告并停止(§9),本轮结论降级为审计发现 |

"≈"一律按 §6 等价规则处理。任何模式之外的情形(如混合方向)按
"如实报告 + 功效声明"处理,不做事后追加对照。

## 8. 可比性与已知不对称(运行前声明)

- C1/C2 复用 PSA 归档行:harness 文件(harness.py、run_experiment.py、
  run_exp.py、run_exp_routing_v2.py、run_controls.py 的存储函数)
  自原实验后**未改动**(本实验只 import,不修改),与原
  PREREGISTRATION §复用条款及 ADDENDUM §E 的同一论证成立。
- **不对称 1(T2 前史)**:C1/C2 归档行的 Phase-3 存储含 T1/T2 阶段
  的演化;原 C3/C4 先跑 T2 以对齐;本轮按 30-run 上限只跑 T3,
  C3b/C4b/C4c 的 T3 起点为 Phase-1 存储。影响限于 8 槽预算中的
  填充内容;失败事实可见性由 §4 预检在 Phase-1 基线上直接度量。
- **不对称 2(端点漂移)**:LLM 端点非位级确定(确定性重跑实验
  已证);C1/C2 与原 C3/C4 同为归档复用,新条件在同一时间窗内
  连跑。措辞上把跨时间窗比较的差异只作方向性解释。
- 排除规则:INFRASTRUCTURE_FAILURE(端点错误)允许对同一
  (cond,seed) 重跑一次并记录;其余一律保留;所有被排除 run
  在 RESULTS 中全列。
- 新代码禁止 silent failure:任何异常显式记录为 error 行并计数,
  不计入分析;error 行数 >0 时在 AUDIT 中逐条列出。

## 9. 有效性标准与停止条件

- 预检(§4)任一 P1–P3 不满足 → 不运行,报告原因。
- 信息等价/状态隔离审计失败 → 停止。
- C1 既有数据发现会改变结论的有效性问题 → 停止并报告。
- 任一对照仍劣于 reset(校正显著)→ 报告,不再调整。
- 回归(pytest + run_all_regression)与基线不一致 → 不运行。

## 10. 运行量

新 run = T3 × {C3b, C4b, C4c} × 10 seeds = **30 次 LLM run**
(另有零 LLM 的预检/审计)。不扩大。
