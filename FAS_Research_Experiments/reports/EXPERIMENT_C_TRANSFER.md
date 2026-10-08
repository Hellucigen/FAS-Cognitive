# 实验 C：跨任务知识迁移（Cross-task Knowledge Transfer: reuse + recombination）

- **日期**: 2026-09-28
- **runner**: `scripts/run_exp_transfer.py`（CHANGE_LOG **C19**）
- **对应审计**: `PRE_EXPERIMENT_AUDIT.md` §3（机制通道预判）/ §8.3（构造草案 C）/ §9（统计计划）
- **遵守声明**: 核心机制零改动；C19 = 装配套件（build_stack 账本/timeline 继承参数）+ 记录层 + 环境数据层

---

## 1. 因果问题

任务 1 学到的经验是否向**共享结构的第二任务**（重组+延伸，非背诵同一链）迁移？

- 任务1 链 A→B→C：`gather oak_log → craft oak_planks → craft crafting_table`（8 轮实践，promote 晋升线全绿）
- 任务2 链 = B→C→D 重组+延伸：`craft oak_planks → craft stick → craft crafting_table → craft wooden_pickaxe`（wooden_pickaxe 是 needs_table 深度链末端，达成必须经历任务1 的成果物 chain 再延伸）
- 条件 Transfer：Ep2 继承 Ep1 全图（inherit_graph，含激活残值）+ **同进程因果账本**（C19 装配套件：同一 CausalLearner / ExperienceTimeline 实例——事件追加同一时间轴，聚合连续，`action_prior` 直接读到任务1 成功率记忆）
- 条件 No-Transfer：Ep2 干净图 + 全新账本
- 同一 world（oak@(6,0) 远 / birch@(-2,0) 近干扰谱系）同一配方表——**环境知识对两条件相等**

## 2. 机制通道与预判（审计 §8.3）

| 通道 | 预判 | 兑现 |
|---|---|---|
| a) 规划闭包（配方表） | 两条件同样可及 → 达成率可能"都 5/5" | ✅ ✅ 达成层零差分 |
| b) action_prior（同族动作成功率记忆，experience.py `action_prior`；autonomy.py:2790 消费：结构性阻碍满额罚 + `prior_bonus=±0.05` 钳位 B6/§12） | Transfer 有任务1 成功率记忆，No-Transfer 空 | ✅ Transfer：(1.0, obs=8)；No-Transfer：(None, 0) |
| c) 图激活残值（Ep1 写回节点 8 个 + oak 系激活） | Ep2 评分抬升 | ✅ 首动路径翻转（见 §4） |

## 3. 结果（seeds 1–5，沙箱全确定，逐 seed 相同轨迹）

### 3.1 达成层：零差分（预判 a，negative-side 证据）

| | No-Transfer（seeds 1–5） | Transfer（seeds 1–5） |
|---|---|---|
| success | **5/5** | **5/5** |
| 背包产物 | wooden_pickaxe | wooden_pickaxe |

表格闭包让 wooden_pickaxe（needs_table 深度 4 链）在两条件下同样可达——**经验迁移不改变达成率**。

### 3.2 行为层：正差分（三实验 A/B/C 首次分离出行为差分）

| 指标 | No-Transfer（seeds 1–5） | Transfer（seeds 1–5） |
|---|---|---|
| 达成 tick | **@16** | **@13**（−3） |
| 动作数 | **9** | **8**（−1） |
| redundant / invalid | 2 / 0 | 2 / 0 |
| **首动作** | **gather birch_log**（好奇心缺口钩走第一拍） | **gather oak_log**（目标链直行） |
| 重组事件 idx（首 craft crafting_table） | **5** | **4** |
| 动作序列 | birch → oak → planks×3 → stick → crafting_table → place → pickaxe | oak → planks → stick → planks → crafting_table → place → pickaxe |

→ Transfer 条件跳过 No-Transfer 的 birch 探索首拍，直入 oak 链：少 1 动作、早 3 tick 达成、重组动作提前一拍。**跨任务知识改变了任务 2 的执行路径**（这是 A/B 里被表格闭包完全遮蔽的行为层空间；任务2 = 共享中段 + needs_table 新末端使得"首动作竞择"发生在有差分条件的候选之间）。

### 3.3 知识访问层：账本通道与图继承双证据

| 探针（t0） | No-Transfer | Transfer |
|---|---|---|
| `action_prior` gather_resource(oak_log) | (None, 0) | **(1.0, obs=8)** |
| craft_item(oak_planks) | (None, 0) | **(1.0, obs=8)** |
| craft_item(crafting_table) | (None, 0) | **(1.0, obs=8)** |
| craft_item(stick) / (wooden_pickaxe) | (None, 0) | (None, 0)（任务2 新动作，无历史——差分精确落在**共享结构动作**上） |
| 继承 操作:/变化: 写回节点 | 0 | **8**（promoted=7 条已披露于 Ep1） |
| oak_planks 激活（t0） | 3.53 | **4.32** |
| 物品:crafting_table 激活（t0） | 0.27 | **2.93** |
| birch_log 激活（t0） | 5.0 | 2.28（干扰谱系亮度差，机制步序开放项，见 §5） |

→ action_prior 差分**精确落在任务1 教过的 3 个共享动作**上，任务2 的新动作（stick / wooden_pickaxe）两条件同为无历史——按预期。账本通道 = 同进程 CausalLearner 实例（8 轮聚合 6 组 + 假设 13 条 + 晋升 7 条全部延续到 Ep2）。

## 4. 机制解释（为什么首动作翻转了）

1. **首动作竞择面**：No-Transfer 里 birch_log 的好奇缺口（感知建档+未知用途 gap）与 oak_log 的目标链直写在同门槛竞逐，birch 赢。Transfer 里 oak_log 一侧多出两类继承信号——①图激活残值（写回 8 节点 + 8 轮 practice 的 oak 系 4.32 vs 3.53）；②`prior_bonus`（gather_resource(oak_log) 成功率 1.0/obs=8 → +0.05 钳位加成，autonomy.py:2790 的 B6/§12 经验回流通道）——竞择翻转为 oak。
2. **达成层为什么仍然零差分**：wooden_pickaxe 的闭包路径两条件同样生成（表格可达），达成不依赖"先摸 birch"还是"直入 oak"——行为差分离散在首拍路径上，不改变成败。
3. 与 A 实验对照的关键差异：A 的 oak_planks @9 首拍同样是 birch 胜（闭包遮蔽全部路径差异）——C 中任务2 的新末端 wooden_pickaxe（needs_table）把达成路径拉长，使"首拍绕路与否"首次进入了**可观测的 tick/动作差分**（−3/−1）。

## 5. 开放项（如实记录，不超卖）

- **birch_log t0 激活 2.28 vs 5.0**：干扰谱系在 Transfer 里显著更暗。现象实锤（5/5），但直写当量（感知建档/gap/好奇）与继承图内 oak 系高激活对 birch 方向的发射转移(excited-state 占比)如何量化竞争，机制步序未做逐拍归因——保留为开放项，不写入论文因果链。
- 本实验差分驱动量为 **descriptive**（n=5 确定性轨迹，5/5 逐 pair 相同；沙箱同 seed 全确定语义），不做显著性声称。

## 6. 结论（论文口径，诚实定版）

- ✅ **达成层**：Transfer 与 No-Transfer 均 5/5 达成——表格闭包仍遮蔽"迁移→达成率"的因果空间（negative-side 证据，预判 a 兑现）。
- ✅ **行为层**：跨任务知识**真实改变了任务 2 的执行路径**（首动作 birch→oak、−3 tick、−1 动作、重组提前一拍，5/5 全确定）——这是 A/B 未分离出的行为层正差分，机制通道 = 账本 action_prior 成功率记忆（obs=8 全域在场）+ 继承图激活残值。
- ✅ **知识层**：转移信号精确落在共享结构动作上（3/3 动作有记忆，任务2 新动作无记忆），图写回 8 节点 + 激活残值量级一致。
- 负结果宣言：**表格可达性遮蔽的是"达成率"而不遮蔽"路径效率"**——任务 2 的深度链（受 needs_table 约束）为迁移效应打开了行为层观测窗。
- 论文建议表述：C 报告"同进程因果账本使同族动作成功率记忆跨任务迁移（action_prior 精确命中共享动作）+ 该记忆与继承图激活在首动作竞择上产生可测行为差分（路径速度），但达成率由表格闭包保证（对迁移无感）"。

## 7. 产物与复现

- runner: `python scripts/run_exp_transfer.py --condition {Transfer|No-Transfer} --seed N --max-ticks 400`
- 汇总: `FAS_Research_Experiments/transfer/expC_{Transfer|No-Transfer}_seed{1..5}.json`
- 原始: `transfer/run_*/`（events.jsonl + snapshots 图起点/终点 + metrics.json + manifest）
- Ep1 图谱: `transfer/c1_ep1_graph_seed{1..5}.json`（写回产物）
- 装配套件: CHANGE_LOG **C19**（build_stack inherit_causal/inherit_tl + 统计段界修复 + 汇总序列化修复）

## 8. 停止条件对照（审计 §10）

- [C] "transfer 有可见差分——或表格可达性遮蔽效应的负结果"：**双侧满足**——行为层/知识层可见差分（§3.2/§3.3）+ 达成层零差分负结果（§3.1）如实入库。
- [D] 全量原始日志存档 ✓（全部 run_* 目录 + snapshots + metrics）
- [E] 失败保留 ✓（pilot 期 v1 run 目录与旧口径汇总 json 原样留存，含跨段合并事故修复前的 32/26 原始记录——统计口径修正已在 C19 披露）
- [F] C19 属装配套件/记录层，design_preserving=是 ✓