# 实验 B：Attention/Diffusion → Relevant Knowledge Access

- **日期**: 2026-09-28
- **runner**: `scripts/run_exp_diffusion.py`（CHANGE_LOG **C18**）
- **对应审计**: `PRE_EXPERIMENT_AUDIT.md` §2（-Diffusion 操作化）/ §5.2（可测差分落点）/ §8.2（构造草案 B）
- **遵守声明**: 核心机制零改动；C18 修的是**沙箱装配层**（补生产扩散驱动器），全部采样/统计在记录层。

---

## 1. 因果问题

Diffusion（β_spread=1.0）是否让相关知识在注意力地板直写之外的**邻居节点**上被点亮（知识访问层），并改变行为？

- 条件 full = `diffusion_on=True`（β=1.0）；条件 nodiff = `diffusion_on=False`（**β_spread=0.0：直写保留、传播截断**，audit 精确操作化）。其余机制（goal/causal/prior/gap）两条件同权。
- 任务：obtain oak_planks；世界 oak@(6,0)（视野外、12 格 find 半径内）+ birch@(-2,0)（视野内，无配方无用途 = 干扰谱系）。种子配方表两条件同一份。

## 2. 前置发现：沙箱扩散执行器缺失（C18）——本实验最重要的机制级修正

**B pilot 首跑指纹**：full 与 nodiff 的激活曲线 **35/35 采样点逐点相同**。调用点审计（diffusion_engine.py:940 `diffuse_step`）链出事实：

- `diffuse_step`/`diffuse_round` 的生产驱动方只有两个：**app.py 的 LLM 回合管线**（app.py:3703-3728）与 **continuous_cognition.CCLoop.tick_once**（continuous_cognition.py:215-219，`decay_step + diffuse_step`，`_running` 门控）；
- **沙箱 `build_stack` 从未装配 CC 循环，autonomy 决策路径也不调扩散** → 沙箱里扩散与衰减**双重缺失**，激活为冻结直写值；
- 因此 pre-C18 的矩阵消融（C12–C16）里 Full 与 -Diffusion **都没有扩散在跑**——`−Diffusion 零差分` 不能支撑"传播不对达成施加影响"的消融声明；它只证明"表格闭包达成不依赖激活"（激活存在与否都达成）。**论文引用矩阵 −Diffusion 结论必须带此修正**（已入 CHANGE_LOG C18；矩阵数字本身无需重跑——它们的含义是"达成层对激活无感"，而非"传播无贡献"）。

修复（环境/装配层，零核心改动）：`build_stack(..., cc_diffuse=True)` 挂 `SandboxDiffuser`（按 CC.tick_once 的 diffusion 段同语义，每认知 tick 一次 decay+diffuse）；`cc_diffuse=False` 回归 pre-C18 行为。

## 3. 结果（C18 装配后，seeds 1–5）

### 3.1 知识访问层：扩散点亮真差分（全 seeds 确定性一致）

探针 = 缺口"指向"对象 birch_log、**视野外纯扩散信号 oak_log**、缺口源与 hub（每 2 tick ACTSNAP）：

| 节点 | full（seeds 1–5） | nodiff（seeds 1–5） |
|---|---|---|
| birch_log（t0） | **1.50** | 1.14 |
| birch_log（t2→t3） | 4.22 → **5.0（封顶）** | 1.65 → 1.49（徘徊） |
| **oak_log（视野外）**（t4） | **0.43** | **0.0** |
| 缺口:用途(birch_log)（t0→t1） | 0.71 → 0.16（**泄放转移**） | 1.43 → 1.29（**钉住不放**） |
| reach_le2_lit（端态，2 跳内激活≥0.05） | **5** | **4** |
| 物品:oak_planks（t0→t4） | 0.855 → 0.711 | 0.855 → 0.662 |

- **birch_log**：活缺口每步 floor 0.9 直写是两条件同权（autonomy.py:1511-1523）——差分完全来自缺口源经"指向"边的扩散：full 下持续累积至 activation_max 封顶；nodiff 只剩感知/直写噪声。
- **oak_log（视野外、感知不可及）**：full 0.43 vs nodiff 0.0——**未经感知的知识节点被扩散点亮**，这是"扩散到达关联知识"的纯信号（来源：物品/配方链 + 行动期 touch 的二次发射），正中审计 §5.2 的观测点。
- **发射即转移签名**（2026-09-13 能量守恒机制）：缺口源节点能量在 full 里 1.43→0.16 泄放进邻域，nodiff 里无出口钉在 1.43——扩散不是"复印"，是从源转移。
- 行为层**零差分**（逐 seed 相同）：success@9、3 动作、red=0、首动 birch、首接 oak —— 表格闭包驱动达成，目标 floor 直写使达成路径对传播无感（audit §5.1 结构保证，负结果如实入库）。

## 4. 结论（论文口径，诚实定版）

1. ✅ **扩散在知识访问层有真实、可测、稳定的激活差分**（全 seeds 确定性）：缺口"指向"对象累积点亮至封顶、视野外知识节点经扩散由 0 变 0.43、能量守恒的源泄放签名、2 跳点亮域 5 vs 4。
2. ❌ **行为层零差分**：达成由表格闭包 + 目标 floor 直写保证，任何条件下的行为轨迹相同——结构保证的负结果（不是假阴性）。
3. ⚠️ **修正声明（论文必改）**：矩阵 −Diffusion 消融的"零差分"测量于扩散执行器缺失的装配（pre-C18），只能解读为"达成层对激活无感"；扩散的**机制级正证据**改由本实验 §3.1 承担。
4. 论文建议表述：B 给出"扩散是把注意力地板直写扩散到相关知识邻域的真实机制（知识访问层）"的机制级证据 + "其因果空间止步于达成层之前"的范围声明。

## 5. 产物与复现

- runner: `python scripts/run_exp_diffusion.py --condition {full|nodiff} --seed N --max-ticks 150`
- 汇总: `FAS_Research_Experiments/diffusion/expB_{full|nodiff}_seed{1..5}.json`（含完整 activation_curves 数组 + reach_le2_lit）
- 原始: `diffusion/run_*/`（events.jsonl + 图起点/终点快照 + metrics.json）
- 装配修正: CHANGE_LOG **C18**（SandboxDiffuser + cc_diffuse）

## 6. 停止条件对照（审计 §10）

- [B] "扩散对知识访问有可测差分——或达成层零差分作为负结果证据"：**双侧满足**——知识访问层正差分（§3.1）+ 达成层零差分（§3.1 末）如实入库。
- [D] 全量原始日志存档 ✓  [E] 失败保留 ✓（pre-C18 pilot 运行目录原样留存）  [F] C18 装配修正属环境层，design_preserving=是 ✓