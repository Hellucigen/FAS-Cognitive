# activation_audit — FAS Activation Landscape Audit（只读机制诊断）

**目的**：判断 FAS 的 activation / diffusion / competition / inhibition / cognitive-demand / resource-routing 链，是否按设计预期形成了"与当前整体输入最协调的高激活知识子图"；并解释 core_routing campaign 中 FAS 无行为优势、T3 重路由偏慢的原因与其发生层级。

**性质**：只读。未修改任何生产代码、参数、prompt、任务、数据；未重跑任务；未新增 task-run。所有分析仅解析既有 `raw_results.jsonl` 与既有统计文件；重放使用**新建的 in-memory 图**，不触碰生产对象。

## 一句话结论

本轮实验中 **FAS 的动力学链路从未真正运行过**：条件 D 的图是**无边图**（实测 0 边），demand/gap/routing 层**100% 抛 KeyError 静默降级**，step 0 焦点恒空。故该实验观测到的"FAS"是"实体名集合 + 注入计数排序"的退化替代物，**对 FAS 激活/扩散/竞争/demand/routing 不构成检验（既不能证成也不能证伪）**。

## 三个根因（均已实证）

| 编号 | 根因 | 证据 |
|---|---|---|
| R1 | **图无边**：runner 只 `append edge_specs` 从不 `add_edge`；生产 API `activate_from_inputs` 语义是"仅激活**已有**边" | 实测重放：9 节点 / **0 边**；实体↔实体边 0。对照生产图 219,328 节点 / 479,140 边 |
| R2 | **demand 层零输出**：`out[0],out[1],out[2]` 对 **dict** 取整数下标 → `KeyError: 0`，被 except 吞成占位文本 | **521/521（100%）** 上下文含 `(demand module unavailable: KeyError)` |
| R3 | **首步空焦点**：先建上下文后 ingest，首步图为空 | 80/80（4 任务×20 种子）phase-1 step-0 的 `n_nodes=0` |

R1+R2 的连带后果：**D-nodemand 消融与 D 实质同条件** → 其"零差分"是恒等式，不构成对照。

## 关键量化

- **浓度不存在**：Top-K 内平均只有 **1.91 个不同激活值**；Gini **0.083**（近均匀）；激活因 `activation_max` 封顶 + 重复注入压成平台，Top-K 次序由 tie-break 决定。
- **结构一致性为零**：Top-K 诱导子图 density **0.000**，8 个选中节点中位 **8 个孤立**。
- **≈ 简单相关性选择**：焦点集与"扩散关闭"重合 **85%**、与"无传播扁平文本相似度"重合 **67%**。
- **噪声占比 26%**（wait / unparseable_reply / hunger）；relevant 占比仅 0.46。
- **T3 陈旧滞留已量化**：变化后第一步 **90%** 种子旧上下文占优（12.09 vs 9.49）；step 1–2 交叉；step 4–5 新上下文稳定占优。
- **内部有差异、行为无差异**：−diffusion 精度 0.33（vs 0.46）、stale 0.89（vs 0.31）——但幅度小且因无边图无下游后果。

## 判定（审计 §13 四假设）

**D（主）+ A（表观）+ B（部分）**：故障主要在 **FAS→decision-head 装配/接口层**，而非 FAS 内部动力学。不成立的是 C。

**明确不能推断**：FAS 理论失败 / 统一图谱设计错误 / LLM 不需要认知路由 / FAS 只是 RAG——数据不支持这些结论。

## 建议（RECOMMENDED NEXT STEP）

**修装置，不修机制**：(1) ingest 真正建边使扩散有结构；(2) 修 demand 接口使其运行并落盘；(3) 统一三消融序列化格式；(4) 首步前先 ingest。修复后重跑才构成对 FAS 动力学的第一次有效检验；在此之前，§10 的否定结论须加限定。

## 目录

```
README.md                        本文件（总览）
diagnostic_report.md             完整诊断报告（根因/量化/矩阵/七问/判定/建议）
activation_statistics.csv        2,814 条 decision 的焦点统计
trajectory_summary.csv           步级动作序列
selection_overlap.csv            126 组跨条件焦点 Jaccard
t3_reroute_analysis.csv          82 条 T3 phase-2 old/new 演化
ablation_internal_analysis.csv   四条件内部指标对照
replay_structure.csv             129 步重放的图结构量（含保真度标注）
figures/                         5 张图（成功的/失败的浓度演化、T3、结构对照、消融内部）
```

**保真度声明**：激活状态未以足够精度落盘以逐节点精确重建（并列导致集合不可复现，集合级一致率 23.3%，top-1 中位差 0.000）。激活值主张一律取自**已落盘**上下文；重放文件仅用于**结构量**（边数/密度/连通性）。

*audit 脚本（只读，位于本目录）*：`audit_parse.py`、`audit_compare.py`、`audit_replay.py`、`gen_audit_figures.py`
