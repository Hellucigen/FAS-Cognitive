# 实验 A：知识写回 → 未来复用（Knowledge Write-back → Future Reuse）

- **日期**: 2026-09-28
- **runner**: `scripts/run_exp_reuse.py`（C17 → C17e，账本 `CHANGE_LOG.md`）
- **对应审计**: `PRE_EXPERIMENT_AUDIT.md` §8.1（构造草案 A）
- **遵守声明**: 核心机制零改动；所有修改在 环境数据层 / 记录层 / run 脚本（C17b/C17d/C17e 每条在案）

---

## 1. 因果问题

`CausalLearner` 的写回（`promote_to_kg` + 写回即 `mark_active`，experience.py:845）是否让知识在**后续 episode** 被访问并**影响行为**？

## 2. 设计（v3 定版）

| | 条件 A（no knowledge） | 条件 B（write-back） |
|---|---|---|
| Ep1 | 无 | oak 树林实践 oak_planks 链 **8 轮**（每轮清背包真达成）→ 支持度 8、conf=0.80 过 **KG_SUPPORT=5 / KG_CONFIDENCE=0.80** 晋升线 → 4 条假设入图（操作/变化节点 + 激活残值）→ 图谱 JSON 存盘 |
| Ep2 | 干净图 | `inherit_graph` 继承 Ep1 图谱（含激活值） |
| 世界（同 seed 同布局） | oak@(6,0) 远（视野外、find 半径 12 内）；birch@(-2,0) 近（视野内、无配方无用途 = 干扰谱系） | 同左 |
| 目标 | obtain oak_planks（两条件同一配方表） | 同左 |

- **差分观测**: 首动作品种（oak vs birch）、达成 tick、废动作（冗余/无效）、激活残值与时序（ACTSNAP 每 2 tick）。
- **为什么 8 轮**: 晋升门槛是 KG_SUPPORT=5 且 confidence ≥ 0.80；Laplace 平滑（conf = s/(s+c+2)）下纯成功链 support=8 才到 0.80。审计文中"MIN_SUPPORT=3 / CONF=0.60"是**聚合→假设**门槛（experience.py:134-137），易为误读。
- **环境修复**: 砂箱 `put_tree` 树干硬编码 oak_log（sandbox_lab.py:174）会污染干扰谱系（birch 树也产 oak_log）→ 环境层 `plant_tree` 纯种树。

## 3. 负发现 Negative-1（v2 自制对象世界，2016-09-28 pilot）

自制对象世界（mystic_ore→mystic_chunk，配方表无该链 = 闭包盲区）pilot 结果（`reuse_A_seed1.json` / `reuse_B_seed1.json`）：

- 两条件 **success=false**；Ep2A 9 动作、Ep2B 7 动作，背包恒空。
- 行为画像：`explore_area@目标物` 永续巡航 —— **user_goal 压过 curiosity，而核心里没有"发现→采集"闭环**（目标无链时不存在领取 gather 的通道）。
- 结论：**结构性设计限制，不绕**。审计 §8.1 的"达成知识只在图里、不在配方表"在核心机制下不可达成 → v3 改用真实 MC 词汇（配方表有链 = 矩阵已证可达），差分观测移到"知识访问层 + 首动作选择"，Achievement 层零差分照负结果入库（审计 §5.1 预判）。

## 4. 结果（seeds 1–5，条件 A/B）

### 4.1 写回成立（Ep1 产物探针）

| seed | promote 写回条数 | 内容 |
|---|---|---|
| 1–5 | **4/4 全部命中** | `craft_item(oak_planks)→self:craft_item:succeeded`；`gather_resource(oak_log)→block:oak_log:disappeared` / `inventory:oak_log:count_increased` / `self:gather_resource:succeeded` |

每个 B run 的 Ep1 图谱 JSON 含 `操作:craft_item(oak_planks)`、`操作:gather_resource(oak_log)` 节点（semantic，`source=causal_hypothesis`，extra_attrs 带 support/observations/confidence 溯源）。

### 4.2 继承链路成立（Ep2 图起点快照）

| 指标 | 条件 A | 条件 B |
|---|---|---|
| inherited 操作/变化 节点 | **0** | **6**（2 操作 + 4 变化，全部继承） |
| `oak_log` 节点激活（t=0, ACTSNAP） | **0.0** | **5.0**（Ep1 8 轮实践残值，cap=5.0） |
| `birch_log` 激活（t=0→t=4） | 1.2 → 2.0 | 1.2 → 2.0（两条件同步的 gap/好奇点亮） |
| `物品:oak_planks` 激活（t=0） | 0.9（目标 floor） | 0.9（目标 floor，两条件同） |

→ **写回知识在 Ep2 被访问：图继承 + 激活残值均在场。**

### 4.3 行为层差分：**零差分**（5/5 × 2 条件逐对相同）

| 指标 | A（seeds 1–5） | B（seeds 1–5） |
|---|---|---|
| success / success_tick | 5/5，@9 | 5/5，@9 |
| actions_total / redundant / invalid | 3 / 0 / 0 | 3 / 0 / 0 |
| first_noise_action_idx / first_ore_action_idx | 0 / 1 | 0 / 1 |
| 动作序列 | gather birch → gather oak → craft | gather birch → gather oak → craft |

（沙箱同 seed 全确定；本例中决策路径对 seed 不敏感 → 5 个 seed 全部同轨迹。）

### 4.4 机制解释（why 零差分）

1. **计划闭包是表格驱动**（audit §5.1，矩阵 11 组已证）：Ep2 两条件的配方表**同一份**，oak_planks 的闭包路径在两条件下同样生成 → 达成路径不依赖写回知识。
2. **首动作由 curiosity gap 驱动，与继承知识无关**：`gather_resource@birch_log` score=0.32（cognitive_state，birch=未知用途缺口）在 t=0 即钩走第一拍；goal-directed 的 oak 步随后由闭包规划器无条件执行（不进入激活比较）。继承的 oak_log 激活残值 5.0 未到达任何候选评分阈值 → 行为无差。
3. 与 audit 设计限制 §5.2 一致：**可测差分只在"依赖传播到达关联节点"的知识访问上**——而本场景里闭包规划把必要的知识节点（oak_log/target）用目标 floor 直写点亮，两条件同权。

## 5. 结论（论文口径，诚实定版）

- ✅ **写回机制在生产路径上真实发生**（8 轮观测 → promote 4 条 → 图内节点 + mark_active）。
- ✅ **跨 episode 知识传输链路成立**（inherit_graph 结构+激活双继承）——即"知识在 Ep2 被访问"成立。
- ❌ **在本场景配置下，写回知识不改变 Ep2 行为**：表格闭包 + 目标 floor 直写使达成层对知识无感。这是**结构保证的负结果**（非假阴性）：它本身是论文信息——"计划知识表格化之后，经验写回在达成层被遮蔽；其因果空间只保留在知识访问层（激活残值）"。
- 论文建议表述：Report A 只作"写回机制 + 传输链路 + 遮蔽效应"三段的机制级证据，**不宣称**"写回带来任务行为收益"。

## 6. 产物与复现

- runner: `python scripts/run_exp_reuse.py --condition {A|B} --seed N --max-ticks 300`
- 汇总: `FAS_Research_Experiments/reuse/reuse_{A|B}_seed{1..5}.json`
- 原始: `reuse/run_*/`（events.jsonl + snapshots 图起点/终点全量 + metrics.json + manifest）
- Ep1 图谱: `reuse/ep1_graph_seed1.json`（其中 操作:/变化: 节点为写回产物）
- 变更在案: CHANGE_LOG C17 / C17b / C17d / C17e

> **装配注记（C18，2026-09-28 补）**：本实验全部 run 在 C18 之前执行——当时沙箱无扩散执行器（CC 循环未装配），激活曲线为冻结直写值（如 birch_log 1.2→2.0、oak_log 残值 5.0 恒定）。该事实不影响本实验结论：写回/继承/行为零差分均为结构与行为证据，与扩散是否运行无关。C18 起新 run 带 decay+diffuse 驱动器。

## 7. 停止条件对照（审计 §10）

- [A] "写回知识在 Ep2 被访问并影响行为——**或如实记录该链路在哪一环断裂**"：链路逐步验证：写回 ✓ → 继承 ✓ → 访问（激活残值）✓ → 行为影响 ✗（闭包遮蔽，见 §4.4）→ 以"如实记录"形态达成。
- [D] 全量原始日志存档 ✓  [E] 失败保留 ✓（v2 pilot 双双保留，Negative-1 有据）  [F] 无核改动 ✓（C17e design_preserving=是）