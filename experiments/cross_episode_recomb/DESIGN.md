# DESIGN.md — 跨经历关系重组实验(预注册,2026-10-03,数据产生前冻结)

目录:`experiments/cross_episode_recomb/`。零生产修改。

## 0. 研究问题

多个独立经历共同作用于一个持续关系图时,FAS 能否利用共享实体、
跨经历连接和 spreading activation,重组出任何单一经历中都没有直接
出现的新关系组合,并相对于 Direct LLM / Raw History / Retrieval
产生可测的行为优势?

## 1. 数据集(冻结)

13 个单事件经历(每经历=一个事件框架:主体-动作-客体):

| id | 主体 | 动作 | 客体 |
|---|---|---|---|
| E1 | 人 | 发现 | 苹果 |
| E2 | 苹果 | 位于 | 树 |
| E3 | 人 | 拿取 | 苹果 |
| E4 | 苹果 | 放入 | 篮子 |
| E5 | 人 | 携带 | 篮子 |
| E6 | 篮子 | 位于 | 厨房 |
| E7 | 钥匙 | 打开 | 门 |
| E8 | 门 | 位于 | 厨房 |
| E9 | 钥匙 | 位于 | 箱子 |
| E10 | 人 | 需要 | 钥匙 |
| E11 | 箱子 | 位于 | 地窖 |
| E12 | 地窖 | 位于 | 厨房 |
| E13 | 人 | 吃 | 面包 |

canonical entity registry:13 个实体名一一对应节点;
G1(隔离)条件按 `实体@E{i}` 复制拆分。

共享实体:人(E1,E3,E5,E10,E13)、苹果(E1,E2,E3,E4)、
篮子(E4,E5,E6)、厨房(E6,E8,E12)、门(E7,E8)、钥匙(E7,E9,E10)、
箱子(E9,E11)。

## 2. 测试问题(6 个;金色链均跨 ≥2 经历,逐条验证无泄漏)

| id | 类型 | 问题(种子实体) | 金色中间链 |
|---|---|---|---|
| Q1 | A 路径重组 | 苹果 与 厨房 | 苹果→篮子→厨房 (E4+E6) |
| Q2 | A 路径重组 | 钥匙 与 厨房 | 钥匙→门→厨房 (E7+E8) |
| Q3 | B 共享锚 | 树 与 厨房 | 树→苹果→篮子→厨房 (E2+E4+E6) |
| Q4 | B 共享锚 | 篮子 与 钥匙 | 篮子→人→钥匙 (E5+E10) |
| Q5 | C 多输入 | 树 和 门 都和什么有关 | 厨房(树经 3 跳,门经 1 跳) |
| Q6 | C 多输入 | 箱子 和 苹果 都和什么有关 | 厨房(箱子→地窖→厨房;苹果→篮子→厨房) |

泄漏审计:每问题金色链按边级检查——任何单 episode 只含一条边,
无完整链;无直达捷径边。审计失败即剔除该问题(预注册剔除规则)。

## 3. 条件(7)

- B0-direct:仅问题。
- B1-history:13 条经历原文(seed 决定呈现顺序)。
- B2-retrieval:嵌入检索 top-6 经历行。
- FAS-G3(完整):共享实体图+事件框架节点(事件-[主体/客体]->实体、
  事件-[后继]->事件)+spreading activation+Top-8 工作集序列化。
- FAS-G2(-event-frame):共享实体图(主体-动作->客体直连边),无事件节点。
- FAS-G1(-shared-entities):实体按经历拆分(苹果@E4 等),经历重新隔离。
- FAS-G3-nospread(-spreading):同一 G3 图,工作集改用嵌入相似度
  选节点(不传播)。

FAS 机制:问题种子实体 activate_from_inputs → diffuse_round(max_depth=4)
→ get_topk(8) → serialize(节点+激活+选中集内部边)→ LLM 单次作答。

## 4. 成功判据(程序化,预注册)

- exact_success:答案文本包含金色链全部中间实体+两端点。
- partial_success:包含 ≥1 个金色中间实体。
- path_validity:被提及的金色实体构成金色链的连续子链。
- cross_episode_success:exact_success 且金色链跨 ≥2 经历(本设计
  所有金链均跨 ≥2,故 exact_success 即 cross-episode 成功)。
- relation_validity:答案提及金色动作(放入/位于/打开/携带/需要 之一)。
- 机制指标(零 LLM):activation_mass、n_activated、
  cross_episode_activation_fraction、金色中间实体进 Top-8 与否。

## 5. 预注册假设

- H1 FAS(G3) > FAS(G1) exact_success(共享实体必要)。
- H2 FAS(G3) > B0 direct。
- H3 FAS(G3) > B1 history。
- H4 FAS(G3) > B2 retrieval。
- H5 -spreading < FAS(G3)。
- H6 G1 的跨经历重组率 < G2/G3。

失败判据(预注册):任何假设方向相反或 p>0.05,如实报告为
"未支持";整体判定按 §24 三选一(见 FINAL_REPORT)。

## 6. 统计

n=5 种子(seed=经历呈现顺序与 tie-break)× 6 问 = 每条件 30 个配对点。
条件对比:McNemar(二值)与 Wilcoxon(计分)配对;族内 Holm。
机制指标零 LLM,确定性单次。

## 7. 禁止事项(执行约束)

不因结果调整图谱/问题/基线/统计;失败与反向结果全保留;
production 零修改;所有产物落 `experiments/cross_episode_recomb/`。
