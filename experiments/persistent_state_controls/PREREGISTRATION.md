# PREREGISTRATION.md — Flat-Store 与 Activation-Off 对照实验(2026-10-03,任何新 run 之前冻结)

目录:`experiments/persistent_state_controls/`。零生产修改;复用
`persistent_state_advantage` 的 harness、任务(T2/T3)、Phase 划分、
指标与统计。不重跑已有实验;C1/C2/C5 直接复用其 `raw_results.jsonl`
(同一 harness 文件、未改动的代码路径,可比性成立)。

## 1. 研究问题(唯一)

在信息量相同的前提下,图结构与 spreading activation 是否对 Phase-3
行为有可测量的独立贡献?(持久化本身已由 PSA 实验证明:H1 10/0。)

## 2. 条件

| 代号 | 名称 | 定义 |
|---|---|---|
| C1 | FAS-full | 复用 PSA 既有数据(Phase-1 图谱+扩散+demand+门控) |
| C2 | FAS-reset | 复用 PSA 既有数据 |
| C5 | FAS-sham | 复用 PSA 既有数据(T2;T3 无既有数据,不新跑) |
| **C3** | **Flat-Store** | 新实现,见 §3 |
| **C4** | **Graph-NoActivation** | 新实现,见 §4 |

## 3. C3 Flat-Store 实现规则(全文,朴素且固定)

- Phase 1 与 C1 完全相同的事件流;但**不写图**。每步存为扁平事实记录:
  `{"entities": [obs 实体…], "action": 动作名, "result": 结果串[:64],
    "polarity": +1(成功)/-1(失败), "index": 步骤序号}`。
- 事实的呈现字符串与 FAS 节点命名同式(实体名、`动作:{act}`、
  `结果:{result}`、`{action}->{result}`),保证信息内容可审计等价。
- Phase 3 每步选择规则(无嵌入、无激活、无图遍历):
  1. 用与 harness 相同的 `entities_of` 提取当前 observation 实体;
  2. 命中 = 事实 entities 含任一当前实体;
  3. 排序:(polarity 降序, index 降序);
  4. 不足 8 条时以未命中事实按同序补齐至 8;
  5. 注入格式与 C1 同模板:`[cognitive-context mode=flat-store]` +
     `selected nodes:` + `- {id} (act {score})`,score=1.0-0.05×rank。
- 预算:8 条,与 C1 working-set 大小一致。

## 4. C4 Graph-NoActivation 实现规则

- Phase 1:与 C1 完全相同路径构建图(含写回边)。
- Phase 3:每步仍执行与 C1 相同的 `ingest`(节点/边写入一致),
  但**不调用 step_dynamics/diffuse**;working set =
  当前 observation 实体 + 其 1 跳邻接(双向),排序:边权降序、插入序,
  截断 8;注入模板同 C1,mode=graph-1hop,act 字段=边权。
- 有效性验证:working set 中不得出现需 ≥2 跳才可达的节点
  (审计记录,见 AUDIT)。

## 5. 假设与指标

- **H_A**:C1 vs C3 —— T2 成功率(McNemar exact)、T3 失败重复
  (Wilcoxon signed-rank, 双侧)。
- **H_B**:C1 vs C4 —— 同上两指标。
- **H_C**(对照有效性):C3 vs C2、C4 vs C2 —— 同上;若 C3/C4 不优于
  C2,对照实现无效,报告并回到有效性检查。
- 主指标 = T2 success(二元)。次指标 = T3 fail_repeats(计数)。

## 6. 统计

- 配对单位 = (seed);n=10 paired seeds(0..9),与 PSA 相同。
- McNemar exact;Wilcoxon signed-rank(exact n≤25)。
- 多重比较:**Bonferroni,m=8**(H_A×2 任务 + H_B×2 + H_C×4)。
- 效应量:二元差(风险差 + discordant 计数 + 精确二项 95% CI);
  Wilcoxon 报 r 与 Hodges–Lehmann 中位差 + bootstrap 95% CI。
- 等价声明规则:"不显著 ≠ 等价"。T2 成功率等价边界预注册为
  |Δ|≤0.10;仅当 90% CI 完全落入边界内才可写"等价",否则写
  "未检测到差异,功效有限"。

## 7. 功效说明(运行前承认)

n=10 配对:McNemar 可稳定检出 10/0(p=.002);8/2 时 p≈.055——
Bonferroni 后(α=.00625)仅 10/0 或 9/0 级别可显著。本实验对
"中等效应"功效不足,结论措辞受此约束。

## 8. 有效性标准与排除规则

- 沿用 PSA harness-valid 标准;INFRASTRUCTURE_FAILURE(端点错误)
  允许对同一 (cond,task,seed) 重跑一次并记录,其余一律保留。
- 信息等价性检查(每 seed:C3 事实集 = FAS 写回事实集,不多不少)。
- 状态隔离:C3/C4 Phase-3 无法访问 Phase-1 原始流(独立进程、
  独立 store、无 inventory echo 通道——C3/C4 的注入只来自其自身存储)。
- 泄漏:C3 存储不含 T2/T3 目标提示或答案句;C4 图写入与 C1 一致。
- Preflight:每新条件 1 seed dry-run(格式/token/working-set 与 C1
  同量级;无被吞异常——新代码禁止 broad except 静默)。
- 回归:pytest + run_all_regression 与基线一致后才允许正式 run。

## 9. 解释表(结果出来前锁定)

| 结果模式 | 结论(仅此措辞) |
|---|---|
| C1 显著优于 C3 且优于 C4 | 图结构与 spreading activation 对本任务有独立贡献;graph/relational 措辞可保留(限 T2/T3) |
| C1 ≈ C3(未检出差异) | 本任务上持久化是主要因素;图结构独立贡献未被证明;中心表述应为 persistent experience-derived state |
| C1 优于 C3 但 ≈ C4 | 图结构有贡献;spreading activation 贡献未证明 |
| C1 ≈ C4 且优于 C3 | 同上方向的另一种拆分,如实写明 |
| C3 优于 C1 | 如实报告:图/激活在此任务无优势甚至有害 |
| C3/C4 不优于 C2 | 对照实现无效(H_C 失败),本轮结论降级为审计发现 |

"≈"一律按 §6 等价规则处理;功效不足时写"未检测到差异,功效有限"。

## 10. 运行量

新 run = T2×{C3,C4}×10 + T3×{C3,C4}×10 = **40 次 LLM run**
(另有 dry-run/审计的零 LLM 开销)。不扩大。
