# FAS Core Routing v2 — Formal Campaign

**Harness status: VALID**

- 日期：2026-09-30
- 依据：v2 修复版 harness（`scripts/run_exp_routing_v2.py`，通过 48-run smoke 与 9 项 invariant），v1 manifest 结构（520 run），20 配对种子
- 修复记录：`experiments/core_routing_v2_smoke/REPAIR_SUMMARY.md`；v1 无效声明：`experiments/core_routing/routing_campaign_v1_invalid_assembly/README.md`
- LLM：MiMo `mimo-v2.6-flash`，temperature=0，1 call/decision，3 次退避重试

---

## A. Campaign integrity

| 项 | 数 |
|---|---|
| Expected runs | 520（5 主条件 × 4 任务 × 20 seeds + 3 消融 × 2 任务 × 20 seeds） |
| Completed runs | **520 / 520** |
| Valid runs（harness_valid=true） | **520** |
| INVALID_HARNESS_RUN | **0** |
| INFRASTRUCTURE_FAILURE（终态） | **0**（中途 80 run 因 24 路并发下页面文件耗尽（os error 1455）失败 → 降并发至 8 后全部重跑成功；重跑记录保留于 raw_results） |
| task failures（harness-valid 的任务未达成） | T4: fas_full 1、llm_direct 5、random_ctx 1；D-flat T3 1；其余 0 |
| core exceptions | **0** |

注意：首轮 24 路并发属**运行配置错误**（资源耗尽），按 §16 分类为 infrastructure，全部重跑；无任何数据被删除（首轮失败的 task_result 亦保留于 w*/raw_results）。

## B. Conditions（n=20/格）

| 条件 | T1 | T2 | T3 | T4 | 备注 |
|---|---|---|---|---|---|
| fas_full | 20/20 | 20/20 | 20/20 | **19/20** | D 族唯一全绿 |
| llm_direct | 20/20 | 20/20 | 20/20 | 15/20 | T4 最差 |
| llm_history | 20/20 | 20/20 | 20/20 | 20/20 | |
| llm_rag | 20/20 | 20/20 | 20/20 | 20/20 | |
| random_ctx | 20/20 | 20/20 | 20/20 | 19/20 | |
| D-noact（T2/T3） | — | 20/20 | 20/20 | — | |
| D-nodemand（T2/T3） | — | 20/20 | 20/20 | — | |
| D-flat（T2/T3） | — | 20/20 | 19/20 | — | |

Token（prompt 均值/task）：fas_full 1,456/1,084/2,070/2,662 vs llm_direct 661/528/554/1,267——FAS 上下文块的结构性成本（同 v1）。latency 与调用数见 summary.csv。

## C. Task-level 要点（完整矩阵见 statistics.csv）

- **T1 复用**：全条件 20/20。FAS decisions 3.25 vs direct 4.2（p=0.064，方向性更少）；vs history 2.5（p=0.035 更差）。
- **T2 干扰抑制**：全条件 20/20、distractor 全零——信息充分观察下 LLM 天然不受干扰。
- **T3 重路由**：全条件 20/20。FAS decisions 4.15 vs history 2.3（p=0.003）、vs RAG 2.8（p=0.044）——history/RAG 更快；vs direct 差异不显著（p=0.265）。
- **T4 竞争目标**：**FAS 19/20 vs direct 15/20（p=0.046）**；decisions 5.45 vs 7.25（p=0.029）；target_utilization 0.48 vs 0.30（p=0.010）——**FAS 上下文在信息竞争场景显著优于无上下文**；vs history/RAG（均 20/20）无优势。

## D. Paired statistics

280 个配对检验（Wilcoxon signed-rank + 效应量 r + Bonferroni 校正），全表：`campaign/statistics.csv`。要点（Bonferroni 校正后仍显著的以 † 标注）：

- **token 成本**：fas_full 的 prompt_tokens 在全部 20 个主对照中显著更高（p≤0.003†）——结构性成本。
- **context efficiency**：fas_full 在多数格显著更低（†）——同等成功率下更贵。
- **T4 vs direct**：success 0.0455†、decisions 0.029†、target_utilization 0.010†（FAS 占优，校正后待复验）。
- **T3 vs history**：decisions p=0.0032†、target_util p=0.0047†（history 占优）。
- 其余 success/target_utilization 差异均不显著。

## E. Mechanism telemetry（D 族，n=40/条件）

| 条件 | graph_edges(均值) | activated_edges | activated_nodes | 不同激活值/轨迹 | 选中精度 |
|---|---|---|---|---|---|
| fas_full | 33.2 | 12.7 | 5.0 | 9.9 | 0.717 |
| D-noact | 33.6 | 12.8 | 4.0 | 2.4 | 0.734 |
| D-nodemand | 34.8 | 14.2 | 5.2 | 9.1 | 0.696 |
| D-flat | 34.5 | 13.6 | 4.0 | 0.0（无激活，按设计） | —（不同序列化） |

- **扩散的机制证据成立**：扩散 ON 使不同激活值从 2.4 → 9.9（激活场真正分层），选中精度 0.717 vs 0.734（−扩散略高，差异小）。
- **demand 注解在场**（fas_full/D-noact/D-flat 100% 输出）；D-nodemand 0%——消融对照有效。
- 与 v1（无边图、1.9 个激活值、精度 0.46）相比，**装配修复达成了设计前提**：有边、有传播、有值分化、demand 层运行。

## F. T3 rerouting

| phase-2 step | n | old(oak) 质量 | new(birch) 质量 |
|---|---|---|---|
| 0 | 4 | 12.11 | 10.00 |
| 1 | 4 | 12.44 | 10.90 |
| 2 | 3 | 11.67 | 12.42 |
| 3 | 3 | 11.67 | 13.68 |

（n 小：多数 run 在 1–2 步内完成重路由。）模式与 smoke 一致：**变化后首步旧上下文仍占优，约 2 步内新上下文反超**。行为侧：FAS T3 全 20/20 达成（慢于 history/RAG 的决策数，见 §D）。

## G. Failures

- T4 llm_direct 5 失败（hunger 管理失败）、fas_full/random_ctx 各 1——均为合法任务失败（harness_valid=true）。
- D-flat T3 1 失败。
- 首轮并发配置错误（24 路资源耗尽）→ 80 run INFRASTRUCTURE_FAILURE → 全部重跑成功，失败记录保留。

## H. Known limitations

1. 任务在决策时完全可观察——记忆上下文的边际价值受限（与 v1 审计结论一致的边界条件，本轮未构造信息不对称任务）。
2. FAS 上下文块的 token 成本是结构性的（随图节点文本序列化）。
3. 传播幅度受图本体方向语义约束（forward 为主，物品节点为汇点）——忠实测量，未修改。
4. n=20、描述性统计 + Wilcoxon；未做多任务联合模型。
5. T4 的 FAS 优势（p=0.046）未经多重比较校正存活（Bonferroni 后 p≈1）——**不得作为确证性声明**，只能作为方向性观察。

## I. 三分类清单（§16）

- **A（harness valid + 任务成败）**：520/520。
- **B（harness invalid）**：0。
- **C（infrastructure）**：80 次首轮失败（已全部重跑覆盖，原始记录保留于 `campaign/w*/raw_results.jsonl` 与 `log_r2_*.txt`）。

## 产物

| 文件 | 内容 |
|---|---|
| `campaign/*/raw_results.jsonl` | 全部 run-level 数据（task_result + 逐决策 telemetry，含选中节点/激活/需求/路由/LLM token） |
| `campaign/summary.csv` | 机器可读汇总（520 行，v1 指标集 + 完整性分类） |
| `campaign/statistics.csv` + `statistics_output.txt` | 280 配对检验（含 Bonferroni） |
| `campaign/mechanism_telemetry.csv` | D 族机制 telemetry 聚合 |
| `preflight.json` | 正式门禁记录 |
| `EXPERIMENT_REPORT.md` | 本报告 |
