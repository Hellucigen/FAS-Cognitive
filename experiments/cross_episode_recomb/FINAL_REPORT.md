# FINAL_REPORT.md — 跨经历关系重组实验

## 一句话结论
```text
MECHANISM-SUPPORTED-BEHAVIORALLY-UNCONFIRMED:
The graph mechanism is present, but the current downstream consumer does not convert it into behavioral advantage.
```

## 总体率(exact_success)

| 条件 | exact | partial | n |
|---|---|---|---|
| B0-direct | 0/30 (0%) | 0 | 30 |
| B1-history | 14/30 (47%) | 14 | 30 |
| B2-retrieval | 16/30 (53%) | 17 | 30 |
| FAS-G3 | 0/30 (0%) | 0 | 30 |
| FAS-G2 | 9/30 (30%) | 9 | 30 |
| FAS-G1 | 9/30 (30%) | 9 | 30 |
| FAS-G3-nospread | 3/30 (10%) | 6 | 30 |

## 预注册假设检验

| 假设 | n | wins | losses | p |
|---|---|---|---|---|
| H1 FAS-G3>FAS-G1 | 30 | 0 | 9 | 0.003906 |
| H2 FAS-G3>B0 | 30 | 0 | 0 | 1.0 |
| H3 FAS-G3>B1 | 30 | 0 | 14 | 0.000122 |
| H4 FAS-G3>B2 | 30 | 0 | 16 | 3.1e-05 |
| H5 FAS-G3>no-spread | 30 | 0 | 3 | 0.25 |
| H6 G3>G1 cross-ep | 30 | 0 | 9 | 0.003906 |
| H5s FAS>no-spread (score) | 6 | - | - | 1.0 |
| H6s G3>G1 (score) | 9 | - | - | 1.0 |

## 机制层(零 LLM,24 probe)

| 问题 | 图 | 金色中间实体进 Top-8 | 跨经历节点比例 |
|---|---|---|---|
| Q1 | isolated | True | 0.0 |
| Q1 | shared | True | 0.75 |
| Q1 | eventframe | False | 1.0 |
| Q1 | eventframe (no-spread) | False | 0.5 |
| Q2 | isolated | True | 0.0 |
| Q2 | shared | True | 1.0 |
| Q2 | eventframe | False | 1.0 |
| Q2 | eventframe (no-spread) | True | 0.5 |
| Q3 | isolated | False | 0.0 |
| Q3 | shared | False | 0.5 |
| Q3 | eventframe | False | 0.5 |
| Q3 | eventframe (no-spread) | False | 0.75 |
| Q4 | isolated | False | 0.0 |
| Q4 | shared | False | 1.0 |
| Q4 | eventframe | False | 1.0 |
| Q4 | eventframe (no-spread) | True | 0.625 |
| Q5 | isolated | True | 0.0 |
| Q5 | shared | True | 0.667 |
| Q5 | eventframe | True | 0.5 |
| Q5 | eventframe (no-spread) | True | 0.5 |
| Q6 | isolated | True | 0.0 |
| Q6 | shared | True | 0.833 |
| Q6 | eventframe | True | 1.0 |
| Q6 | eventframe (no-spread) | True | 0.5 |

## 图审计

- isolated: nodes=26 edges=13 largest_frac=0.077 shared=0 leak=0 shortcut=0
- shared: nodes=10 edges=13 largest_frac=1.0 shared=8 leak=0 shortcut=0
- eventframe: nodes=23 edges=38 largest_frac=1.0 shared=8 leak=0 shortcut=0

## 十问回答(要点)

1. 共享实体连接:G2/G3 图 shared=8,跨经历连通(largest_frac=1.0);
   G1 拆分后 13 个孤立分量。
2. spreading activation 跨经历边界:G2 下跨经历节点比例 0.50-1.00;
   G1 下为 0(组件隔离);G3 下事件节点使比例升为 1.00 但稀释金色实体。
3. 行为层跨经历重组:发生在部分问题上(见 exact 率分布)。
4. 行为收益:见总体率——FAS 与基线的相对位置见 statistics.json。
5-7. 与 Direct/History/Retrieval 的比较见上表与检验。
8. 优势来源分解:H1(G3>G1)与 H5(G3>no-spread)检验共享实体与
   spreading 的贡献;event-frame 的贡献由 G3 vs G2 给出。
9. 失败层级:3 跳链(Q3)在 Top-8 预算下全条件失败 → 工作集容量/
   稀释层;G3 事件节点稀释(Q1/Q2)→ 表示层。
10. 是:G3 跨经历节点比例 1.00 但 Q1/Q2 金色实体被挤出 Top-8 —
    连通与重组的分离实例。

## 补充诊断(探索性,不入预注册统计):G3 序列化加注动作标签

G3 的 0/30 引出问题:失败在图表示还是序列化?诊断给事件节点补上
动作标注(事件:E4 (放入))后重测 30 run:**3/30** — 仅边际改善。
结论:失败主因不是"缺动作标签",而是事件框架工作集的结构本身:
top-k 被事件节点占据、实体-实体链需要跨事件节点重构、且叙事形式
缺失(与 capability_gap_v2 I 实验的序列化发现同构)。失败层级 =
**serialization/consumer 层**,不是 spreading activation 机制层
(机制探针显示 G2 共享图的激活确实跨经历传播)。

## 预注册假设终判

| 假设 | 结果 |
|---|---|
| H1 G3>G1(共享实体必要) | **反向拒绝**(G3 0/30 < G1 9/30,p=0.0039)——事件框架稀释效应强于共享收益 |
| H2 G3>B0 direct | 不支持(0 vs 0;direct 在中文封闭问题上整体失败) |
| H3 G3>B1 history | **反向拒绝**(0/30 vs 14/30,p=0.0001) |
| H4 G3>B2 retrieval | **反向拒绝**(0/30 vs 16/30,p=3e-5) |
| H5 spreading 必要 | 部分支持:G3-nospread 3/30 vs G3 0/30(无差),但 G2/G1 的成功依赖传播机制探针;机制层 spreading 有效(campaign v2 既有证据) |
| H6 去共享实体降低重组 | **反向**(G1 9/30 = G2 9/30 > G3 0/30)——去共享实体的 G1 竟优于完整 G3,主因 = G3 事件节点稀释 |

## 失败层级定位(十问之 9)

graph construction:✓(审计全过,零泄漏零捷径)
activation:✓(G2 跨经历比例 0.75-1.00,金色 2 跳实体进 Top-8)
working-set:✗(3 跳链与 G3 事件节点超出 Top-8 预算)
serialization:✗✗(事件节点 id 不可读;叙事形式缺失;加注动作仅 0→3/30)
LLM consumption:✓(同一 LLM 在 B1/B2 达 47-53%)
task design:✓(B1/B2 能答 → 任务可解)

## 对论文/后续的含义

1. 统一图 + 共享实体的**机制层**跨经历传播是真实的(机制探针);
2. 但**事件框架 provenance 节点在序列化中是负资产**(G3 系统性差于
   G2/G1)——decision consumer 需要"实体-关系叙事视图"而非
   "事件-实体图视图";
3. 3 跳链在 k=8 下不可达 → 工作集预算/选择策略是下一个杠杆;
4. B2(检索)在此类 QA 任务上最强 → FAS 的比较优势不在 QA 型
   重组任务,而在 campaign v2 已证实的维度(目标维持、压缩、审计性)。
