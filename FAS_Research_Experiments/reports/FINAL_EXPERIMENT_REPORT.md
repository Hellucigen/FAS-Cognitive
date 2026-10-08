# FINAL_EXPERIMENT_REPORT — Fascinator (FAS) 认知架构实验套件

- 实验日期：2026-09-28（10:19–12:00 UTC+8）
- 实验模式：`FAS_EXPERIMENT_MODE=LEARNING_CLOSED_LOOP`（MC 段）/ `SANDBOX`（场景段）/ `NORMAL`（基线散射段）
- 报告遵守：\§31 change 账本 + 诚实边界（未核实不写、失败保留、假成功剔除）
- 全部原始数据：本目录姊妹文件夹 minecraft/ sandbox/ ablation/ baselines/ 的 run_*（metadata/events/snapshots/metrics 齐）

> **修订注记（2026-09-28 晚段收口）**：本报告正文为当天上午 10:19–12:00 实验窗口的原始记录，原样保留。晚段（17:0x–19:0x）追加三因果实验（A 知识写回 / B 扩散→知识访问 / C 跨任务迁移，各 5 seeds、沙箱全确定）与声明审计收口：新增 **§21 三因果实验**、**§22 声明审计（PAPER_CLAIM_AUDIT 摘要）**；执行摘要与 §13/§19/§20 打补丁（含 **C18**：矩阵 −Diffusion 各 run 在沙箱扩散执行器缺失装配下运行的口径修正——见 §21.2）。三实验详细报告：`EXPERIMENT_A_REUSE.md` / `EXPERIMENT_B_DIFFUSION.md` / `EXPERIMENT_C_TRANSFER.md`；变更账本 C17–C19。

---

## 1 执行摘要

| 段 | 计划 | 交付 | 状态 |
|---|---|---|---|
| PRE 实验审计 | 测试/启动/图完整/连接/记录器 | 103/103 测试绿；M1-M2 实机 2 run；记录器修复 2 处并冒烟 | 完成 |
| M1 知识发现 | 实机观察 25min | iron_ingot 目标形成；图谱 +100 节点/+365 边；400 events | 完成 |
| M2 知识写回复用 | 实机观察 25min | 图谱 +35 节点/+95 边（增幅收敛=复用形态）；主导行为 investigate(crafting_table) | 完成 |
| M3 情景影响行为 | 观察 + 实时因果账本 | live causal 89 aggregations / 78 hypotheses / 29 promoted（全部来自真实行为，未注入） | 完成（观测式） |
| M4 环境变化 | 实机改世界（不可行） | 降级为观测 + 沙箱 S7（环境变化）/S11（过时知识）等价 | 降级，如实标注 |
| M5 多目标 / M6 中断恢复 | 实机 | M5/M6 无独立运行环境差（沙箱 S8/S9 完成不作为等价）—— 观测记录 | 部分（沙箱等价） |
| M7 LLM 基线 | 沙箱等价 + 短实机 | 沙箱完成；实机（bot 闲置）以观测代替 | 部分（沙箱等价） |
| S1–S11 | 19 场景 | 全部 run 落盘（S5 修复后 12 hyp/9 agg） | 完成 |
| 消融矩阵 | 9 组 ×3 seeds | 短链任务 9/9 达成；**修复前后对比推翻初始 -Prior 差分** | 完成（含负结果） |
| 基线 | reactive/memory/graph/LLM | 前三 @6×3；llm_heavy 4/5 @step2（失败 run 保留） | 完成 |
| 三因果实验 A/B/C | 写回→复用；扩散→知识访问；跨任务迁移（§21） | A 写回+继承链路成立/达成层零差分；B 知识访问层正差分+达成层零差分；**C 行为层正差分**（首动翻转、−3 tick/−1 动作，5/5 全确定） | 完成（含双负结果） |

**一句话结论**：在沙箱与实机可及的观测口径内，未发现短链获取任务对各单变消融（−Diffusion/−Episodic/−Prior/−Gap/−Writeback/−RelevantSelf）的达成率差分（ceiling effect）；−GoalFormation 恒不达成属结构定义。初始观察到的 "−Prior 差分" 经修复沙箱探索伪影后消失，判定为**环境伪影，非架构机制**——这是本次实验最重要的方法学结果。

**三因果实验收口结论（晚段追加，详见 §21）**：①知识写回在达成层被表格闭包遮蔽（A/C 双负结果），但②扩散在**知识访问层**有真实、稳定、可测的激活差分（B：视野外知识 0→0.43、能量守恒"发射即转移"签名），③跨任务迁移在**行为层**有可测差分（C：同进程因果账本的 action_prior 成功率记忆精确命中共享结构动作（1.0/obs=8 vs None/0），首动作竞择翻转 birch→oak，−3 tick/−1 动作，5/5 沙箱全确定）——认知机制的证据落在知识访问层与路径效率，达成率由表格闭包保证。

## 2 实验环境与方法

- 宿主：Windows 11 Pro；Python 3.12.13（E:/Miniforge.envs/Fascinator，`-X utf8`）；git main@4685726
- 系统：Flask 单进程 + ContinuousCognition 守护线程；KnowledgeGraph（live 3,461 节点/8,721 边）→ DiffusionEngine → ActionManager → AutonomousLoop
- LLM：MiMo mimo-v2.6-flash（data/llm_secret.json，gitignored）；沙箱全程零 LLM（`MiMoBackend._create` 守卫在 import 时安装）
- 沙箱：scripts/sandbox_lab.py SandboxWorld（16s/拍仿真时钟；配方表/RECIPE_TABLE；方块表 BLOCK_META 含 deepslate_iron_ore；SMELT_PRIOR）
- 记录器：RunRecorder（metadata.json/events.jsonl/snapshots/metrics.json/manifest.jsonl + exp_events 归档）
- 实验纪律：零 LLM 注入任务答案；constructor 固定 seed；失败 run 保留；本文所有数字从 run 产物 re-read 生成

## 3 PRE_EXPERIMENT_AUDIT（前审计）

审计面（10:19–10:25，UTC+8）：

| 面 | 结果 | 证据 |
|---|---|---|
| 测试套件 | 全套通过 | tests/ 103/103（test_energy_conservation 长跑护栏在内） |
| 启动 banner/模式 | experiment_mode 单点真源；shield 表 12 tag | xm.xlog 分发正常 |
| 图谱完整 | live 3,461 n / 8,721 e，公共孤点清零 | graph_runtime_* 快照 |
| 激活/扩散 | 空闲能量低（修复后 12） | 扩散守恒护栏 |
| 因果账本 | 读写正常 | experience_timeline.json 存在且可读 |
| MC 连接 | bridge 127.0.0.1:5010 在线 | /api/mc/status ok |
| 记录器 | **2 处缺陷修复** | 见 §18；修复后续冒烟通过 |

修复（记录器，section §31 change 账本 C07/C08）：`RunRecorder._flush_events` 只有 %100 触发且 finalize 不 flush → 486 事件只留 400；`self._lock` 在部分实例缺失 → AttributeError 被静默吞。修复 = 增量 flush + finalize 强制 flush + 模块级锁。**M1 前 400 事件即此缺陷产物（补救快照补全 graph delta）**。

## 4 系统快照（run 起点）

| 量 | 值 | 来源 |
|---|---|---|
| 图谱（M1 起点） | 3,462 n / 8,724 e | run_20260928_103435/snapshots/graph_start.json |
| 图谱（M2 终点） | 3,497 n / 8,819 e | run_20260928_112330/snapshots/graph_end.json |
| cycle ids | c_%06d 格式，实验期间约 380–470 | /api/internal/state |
| 先验条件 | C（more complete：图+配方表+方块表+SMELT_PRIOR） | world_prior 闭包（配方=9 方块=16 人工=1，iron 目标） |
| 实验种子 | MC 段 seed=1；场景段 1–3 | run metadata |

## 5 实验模式实施（FAS_EXPERIMENT_MODE）

- 三模式：NORMAL（默认，向后兼容）/ LEARNING_CLOSED_LOOP（MC 实机，目标经 `experiment_obtain` 注入）/ SANDBOX（沙箱场景）
- 单点真源：experiment_mode.py；config["experiment"]（mode/goal_obtain/attention_floor/prior_extra/seed）
- Banner：启动输出 experiment_id/mode/timestamp/git hash/seed/config/enabled units/MC endpoint/LLM
- 守卫：**零 LLM 契约**（沙箱 import 即安装 `_create` 守卫，违反即 raise）；目标注入巴别塔（目标语言与目标名直连白名单校核）；LLM 计次 `_create` 唯一遥测点

## 6 M1 知识发现（实机，10:25–11:00）

- run：run_20260928_103435；25min 观察；400 events（STATE 99 / OBSERVATION 99 / CANDIDATE 113 / ACTION 55 / GOAL 24 / TARGET 9）
- 目标：iron_ingot 经 `experiment_obtain` 注入（goal created 10:25:58）
- 图谱：**net +100 节点（+100/−20）、+365 边（+365/−143）**
- 过程证据：探索/挖矿/采集/合成链被真实触发（ACTION 55）；目标候选出现 9 次（TARGET）
- 局限（如实）：25min 内未形成完整"挖铁→熔炼→取锭"闭环（实机 bot 行为时长不足 + 熔炼链需多蹲守窗口）；图谱净增与目标链知识展开（含 deepslate_iron_ore 作为 raw_iron 来源、furnace 相关节点）构成"发现"的可量化口径
- 数据即时性（诚实声明）：该 run 事件文件为记录器缺陷（§3）产物，事件行缺失 86 条；图快照经 live API 补救写入目录并标注 `快照补救`；接口未被修改

## 7 M2 知识写回复用（实机，11:23–11:48）

- run：run_20260928_112330；545 events + 1,697 条 exp_events 归档（cognition.jsonl 时间窗抽取）
- 图谱：**net +35 节点 / +95 边**（M1:+100/+365 → M2:+35/+95 增幅收敛）
- 行为主导：TARGET 89 条几乎全为 `investigate_location crafting_table score=0.142`（循环调查工作台/配方源）；无新 obtain 目标形成
- 解读（不越界）：M2 呈现"检索-复核既有知识"形态而非"M1 的发现形态"；增幅收敛与主导行为共同支持"知识写回后图谱密度已高、后续行为以复用/复核为主"的观测口径；但 25min 未观察到复用闭环（obtain 达成）——**知识写回→行为改进的因果桥未在实机窗口内建立**，作为负观测如实记录

## 8 M3 情景记忆对行为的影响

- 实时因果账本（只读采样，每 60s，M1/M2 全程）：**89 aggregations / 78 hypotheses / 29 promoted**
- 全部来自真实行为重放（无注入；§9 禁令遵守，无一次假事件）
- 代表性：`smelt_item(iron_ingot)→failed:furnace_not_found` support=5 conf=0.625（实机炉侧行为失败归因链）；`furnace_take(iron_ingot)→failed:furnace_not_found` support=3 (weakened)——**失败的客观归因成立，未宣称成功**
- M1/M2 后未重演注入测试：情景影响行为的**直接因果测量未完成**（需要对照 run；见 §13/§19 局限），现有口径 = 情景记忆账本实时增长 + M2 行为趋势

## 9 M4 环境变化（降级）

- 实机改世界不可行（远程无世界编辑接口）→ **降级**：观测记录 + 沙箱等价
- 沙箱等价：S7（环境变化：搬家/换资源带，两组 run 完成）；S11（过时知识：知识失效后行为变化，3 run 完成）
- S11 样本：run_20260928_110049/110216/110246（过时知识场景，block 移除后 prior→图上活动衰退）

## 10 M5 多目标 / M6 中断恢复

- 沙箱等价：S8（多目标，run_110020/110316）、S9（中断恢复，run_110035/110318）
- 实机：多目标中断等长周期条件未安排（19:30 截止 + bot 会话稳定性），如实记为未实施

## 11 M7 LLM 基线

- 沙箱等价已记录（§14）；实机短 run 未独立执行（bot 闲置）
- LLM 用量实测（沙箱 llm_heavy）：全程 ≤5 步/run；4/5 run @step2 达成，1/5 未达成（5 步上限，**失败 run 保留**）
- 对比口径（§14 表）

## 12 沙箱场景 S1–S11 明细

| 场景 | run 数 | 关键结果 |
|---|---|---|
| S1 扩散联想 | 2 | 关联扩散被观察到；作为对照 face（见 S2） |
| S2 扩散消融 | 2 | 开/关对照 run，无达成率差异（短链 ceiling） |
| S3 知识发现&写回 | 5 (EpA) + 1 (EpB) | EpA 发现 oak_planks→stick 链；EpB **继承上一 episode 图**（inherit_graph）复用 stick 直接达成 |
| S4 知识组合 | 7 | 多轮组合（木镐→石镐等）正常 |
| S5 情景因果 | 9 runs | **修复一后终版：12 hypotheses / 9 aggregations / 15 轮石子循环实证因果窗归因成立**（修复前 runs 保留：hyp=0 的失败版全部在目录内；探索量程修复在其后，因果数字为保守下界） |
| S6 持续学习 | 3 (EpA/EpC/汇总) | EpA planks + EpC pickaxe 跨 seed；汇总 run 合并账本 |
| S7 环境变化 | 3 | 资源带替换后行为重定向 |
| S8 多目标 | 2 | 两目标在行动空间共存 |
| S9 中断恢复 | 2 | pending 动作被中断后恢复无悬挂 |
| S10 Self Model 消融 | 2 (full/minus_self) | full @6 达成；minus_self @6 达成（短链无差异） |
| S11 过时知识 | 3 | 旧知识节点活性衰退，行为转向新供给 |

> 表内行为性断言（S3-EpB 达成、S7/S8/S9/S11 行为形态）来自各 run 当日运行输出（记录器对这些场景只落在最终 ok=True + 图快照，行为细节见运行现场日志/会话记录）；本表每行 run 完成态均已重新核验（metrics ok=True）。

零 LLM 全程；场景间构件独立性成立（每 run 新 KnowledgeGraph；EpB/继承例外按设计）。

## 13 消融矩阵（§7，9 组 ×3 seeds）

### 13a 任务：oak_planks（短链）

| 组 | seed1 | seed2 | seed3 |
|---|---|---|---|
| full | @6 | @6 | @6 |
| −Diffusion | @6 | @6 | @6 |
| −Episodic | @6 | @6 | @6 |
| −Prior | @6 | @6 | @6 |
| −Gap | @6 | @6 | @6 |
| −Writeback | @6 | @6 | @6 |
| −Episodic−Writeback | @6 | @6 | @6 |
| −Diffusion−Episodic | @6 | @6 | @6 |
| −Diffusion−Gap | @6 | @6 | @6 |
| −RelevantSelf | @6 | @6 | @6 |
| −GoalFormation | — | — | — |

### 13b 任务：cobblestone（长链）

| 组 | seed1 | seed2 | seed3 |
|---|---|---|---|
| full | @15 | @15 | @15 |
| −Diffusion | @15 | @15 | @15 |
| −Episodic | @15 | @15 | @15 |
| −Prior | @15 | @15 | @15 |
| −Gap | @15 | @15 | @15 |
| −Writeback | @15 | @15 | @15 |
| −Episodic−Writeback | @15 | @15 | @15 |
| −Diffusion−Episodic | @15 | @15 | @15 |
| −Diffusion−Gap | @15 | @15 | @15 |
| −RelevantSelf | @15 | @15 | @15 |
| −GoalFormation | — | — | — |

### 13c 任务：iron_ingot（长链+熔炼）

所有组、所有 seed 均**未达成**（上限 450 ticks）。诊断链（§18 附诊断记录）：熔炼链卡在"烧完不取"，根因 = 沙箱探索技能伪影（见 §19 威胁 2）。**本任务对护程序差分无效，作为环境受限负结果。**

### 13d 修复前后对比（方法学重点）

1. **修复前矩阵**（探索伪影期，run_112517–113433）：oak_planks 8/9，**−Prior 不达成** → 初判"先验层=短链唯一通道"
2. **修复后矩阵**（run_114852+）：前提条件只有环境侧修改（沙箱探索量程匹配 explore_max_time_s/step/distance + 预置工作台环境事实），认知/技能零改动
3. **修复后 −Prior 与其他单消融全部达成** → 初始差分**消失**
4. 结论：初始 −Prior 差分由探索伪影（无限巡航抢占行动空间）在选择动力学上放大，**不是先验机制的达成率效应**。**任何"消融造成差异"的说法在本实验都不可维持**；仅可维持：短链任务上各组件均非达成瓶颈（ceiling）；−GoalFormation 恒不达成为设计语义。

> **C18 装配修正（晚段补，必读）**：§13a/§13b 各 −Diffusion 组的 run 在沙箱**扩散执行器缺失**的装配下运行（diffuse_step 的生产驱动方只有 app.py 回合管线与 continuous_cognition.CCLoop，沙箱 build_stack 从未装配 CC 循环——激活为冻结直写值，β=1 与 β=0 无差别是装配事实，非机制事实）。因此该两表的 −Diffusion 列**不能**作为"传播不影响达成"的机制声明，只读为"表格闭包达成不依赖激活存在与否"。扩散的机制级证据改由 **§21.2 实验 B**（C18 补装 SandboxDiffuser 后：知识访问层正差分）承担。

## 14 基线对比（§8；沙箱同世界同种子）

| 基线 | seed 达成 | 动作步 | LLM 调用 |
|---|---|---|---|
| reactive（无图/无学习/无先验） | s1@s6, s2@6, s3@6 | 2 | 0 |
| memory_only（继承图，无扩散） | s1@6, s2@6, s3@6 | 2 | 0 |
| graph_retrieval（继承图+扩散，无因果） | s1@6, s2@6, s3@6 | 2 | 0 |
| llm_heavy（MiMo，≤5 步） | 4/5 达成 @step2；1 失败保留 | 2 | 2/run 实测 |

- 补跑声明（诚实）：reactive/memory_only 的原始 run 未在磁盘（记录缺陷/早段未落盘），本节为**补跑结果**（run_12xxxx 之后）；补跑时基线代码未改动
- llm_heavy 失败 run（112936，5 步上限未达成）留在目录内，不做删除

## 15 先验知识条件 A/B/C

操作化定义（build_stack `prior_level`；scripts/run_prior_conditions.py）：

| 条件 | 操作化 | 计划知识供给 |
|---|---|---|
| A（zero/minimal） | 图谱先验查询通道关 + 配方表/方块表清空 + prior_extra 不注入 | 无任何计划知识，行为只能从感知/扩散/因果累积涌现 |
| B（limited） | 图谱先验查询通道关（prior.enabled=False）+ 机制表保留 | 只余世界机制知识（配方/方块掉落/工具要求） |
| C（more complete） | 现状 | 图谱先验边 + 机制表 + 任务先验（SMELT_PRIOR） |

结果（每条件 ×3 seeds；零 LLM）：

| 条件 | oak_planks（≤200 ticks） | cobblestone（≤300 ticks） |
|---|---|---|
| A | 0/3（未达成） | 0/3（未达成） |
| B | 3/3 @6 | 3/3 @15 |
| C | 3/3 @6 | 3/3 @15 |

**结论（B=C、A=0 双任务一致）**：
1. **配方/方块机制表是短链获取任务达成的必要计划知识**——A 下动作只剩收集（无合成驱动），永不达成。
2. **图谱先验边在短链任务上可或缺**（B=C）——与 −Prior 消融无差分（§13d ceiling 解释）互为印证，且给出机制回答：达成所需的计划知识在机制表里，不在图谱先验边里。
3. "no-prior learning"不作任何声称——**A 条件的 0/3 明确否定了"无先验也能学习短链"的说法**。

## 16 时间预算与纳入失败

- §11 纪律执行：iron_ingot 链 stuck 诊断共 3 个 fix 周期（crafting_table 预置、探索量程、技能 trace），超 10min/次的执行上限，**按纪律停手转入 §13c 记录**（未无限 pathfinding；未改认知；未改技能）
- 失败 run 全保留：llm_heavy 112936（未达成）；S5 全部 pre-fix runs（hyp=0）；iron_ingot 矩阵全部 27 runs；M1 事件缺失（缺陷记录保留在目录）
- 未删除任何 run；未隐藏任何异常

## 17 代表性轨迹

### 17a 沙箱全配置达成轨迹（oak_planks，run_20260928_114852 恢复版可复现；代表性节选）

```
tick 0   感知：oak_log×9（树）
tick 1   [TARGET] 下一步 gather oak_log（先验闭包 oak_planks：配方=1 方块=2 depth=2）
tick 2   [ACTION] gather_resource@oak_log → inventory:oak_log 0→2
tick 3   [TARGET] 下一步 craft oak_planks（配方:oak_planks:hand:0 原料已齐）
tick 4   [ACTION] craft_item@oak_planks → inventory:oak_planks 0→? 
tick 6   [RESULT] 达成 @tick6（goal_done）
```

### 17b 沙箱铁链卡点轨迹（iron_ingot，run_20260928_114051 代表）

```
tick 206  cobblestone=8（熔炉材料齐）
tick 210  raw_iron=4
tick 211  craft furnace → inventory:furnace=1
tick 212  place furnace（落地）
tick 214  smelt raw_iron 4→3（_smelt_done_queue=iron_ingot）
tick 217  explore_direction（探索缺口通道胜出）
tick 217–300+  explore 永续巡航（壁钟 deadline=300s 在沙箱内不可达）
          → 熔炼产物永不 furnace_take → 目标永不达成
```

### 17c 实机 M1 图谱增长（graph_delta_M1）

```
nodes_added=100  nodes_removed=20  edges_added=365  edges_removed=143
```

## 18 原始数据索引

| 族 | 目录 | 详情 |
|---|---|---|
| 实机 MC | FAS_Research_Experiments/minecraft/ | run_20260928_103435 (M1), run_20260928_112330 (M2)；另含 graph_snapshots/ trajectories/ raw_logs/ |
| 沙箱场景 | .../sandbox/ | 50 个 run_（S1–S11 全部；label 见各 metadata.json） |
| 消融 | .../ablation/ | 60+ run_（两轮矩阵 + iron 标量诊断）；matrix_summary.json（当前=cobblestone 11 组）、matrix_oak_planks_final.json / matrix_cobblestone_final.json（终版 11 组归档） |
| 基线 | .../baselines/ | 12 run_（reactive/memory_only/graph_retrieval ×3；llm_heavy ×5 含失败 1）；baselines_summary.json |
| 先验条件 | .../prior_conditions/ | 18 run（A/B/C × 两任务 × 3 seeds）；prior_{goal}_{seeds}.json 汇总 |
| 三因果-A | .../reuse/ | 10 run_ + reuse_{A,B}_seed{1..5}.json + ep1_graph_seed1.json（写回产物） |
| 三因果-B | .../diffusion/ | 10 run_ + expB_{full,nodiff}_seed{1..5}.json（含完整 activation_curves） |
| 三因果-C | .../transfer/ | 20 run_（含 pilot 期口径产物保留）+ expC_{Transfer,No-Transfer}_seed{1..5}.json + c1_ep1_graph_seed{1..5}.json |
| 因果账本 | data/experience_timeline.json | live：89 agg / 78 hyp / 29 promoted |
| 变更账本 | FAS_Research_Experiments/CHANGE_LOG.md | §31 账本（本文第一节附表首引） |

## 19 对有效性的威胁（threats to validity）

1. **Ceiling effect（达成率尺度）**：oak_planks/cobblestone 的步骤 ≤15，任何单组件缺失都构不成瓶颈 → 消融无差分**不能**解释为"组件无用"，只解释为"在该任务尺度上无达成率影响"；需要更长链任务（如 iron_ingot 修好后的版本）才有区分度。
2. **沙箱探索伪影（con环境仿真）**：explore_* 技能 deadline 用壁钟（time.time()+300），而沙箱 run 全程几十真实秒 → 探索技能在沙箱内永续巡航，抢占行动空间；修复在环境侧（探索量程匹配），认知/技能零改动；**修复后所有 −Prior 等差分消失** → 所有在修复前得出的"差异"均按伪影处理（已丢弃的结论：none 被保留）。
3. **补跑的基线**：reactive/memory_only 为补跑数据（原始 run 未落盘），其结论"与前两者相同"在新数据成功后成立，无独立交叉验证。
4. **M1 事件缺失 86 条**：记录器缺陷所致；图快照补救；可能漏掉少量目标映射事件 → M1 的 TARGET 计数（9）是最小界。
5. **实机 vs 沙箱不等价**：M4/M7 的实机部分未执行；观测结论只到"行为形态"级；知识写回→行为改进的因果桥未建立（M2/M3 负观测）。
6. **种子数**：场景级运行 1–3 seeds；消融 3 seeds。无假统计显著性声称（未做推理性统计）。
7. **图写入的干预**：M1/M2 全程真实行为；无注入实验事件。zero-prior 声称：**A 条件 0/6 实测否定了"无先验也能学习短链"**（§15），故"no-prior learning"不作任何声称；B=C 表明图谱先验边在短链任务上非达成关键（机制表才是）。
8. **沙箱扩散执行器缺失（C18，投稿必读）**：pre-C18 全部沙箱 run（矩阵、基线、场景 S1–S11）的扩散+衰减均未在跑（冻结直写值）——所有"−Diffusion 零差分"只能读为"达成层对激活无感"，不得引用为传播机制声明（§13d 注记）。三因果实验 C18 之后全程装配驱动器（B/C 语境内数字有效）。
9. **共享时间轴统计口径**：实验 C 的 Transfer 条件共享 Ep1 时间轴（账本通道设计），统计必须按 episode 段界切分（C19 修复；pilot 期跨段合并口径产物保留在 run 目录可复核）。

## 20 结论与展望

1. FAS 在沙箱短链获取任务上，达成率为全配置 × 全部单变消融 = 100%，−GoalFormation = 0%（结构定义）；**短链任务无法提供组件级消融差分**。
2. 初始 −Prior 差分 → 伪影解释（修复后消融）。**下一次消融实验必须带长链+多步骤任务、按修复后环境重跑，且把"修复前后对比"制度化**（作为消融实验自身的前置校验）。
3. 实机 M1/M2 提供了真实环境下的知识发现（+100n/+365e）与收敛形态（+35n/+95e）观测；因果账本实时增长（89/78/29）。
4. 已量化的工程债：记录器 flush 缺陷（已修）、探索技能壁钟-仿真时钟失配（沙箱侧已绕行，**真实 MC 无需修复**——该技能在真实 30min+ run 中能正常 deadline 结算）、iron_ingot 链的沙箱驱动缺口（furnace_take 需环境驱动支持，CHANGE_LOG 记档待办）。
5. 下一轮（建议）：A/B 先验条件 run；多 seed × 长链（cobblestone+furnace 全链）消融；M4 真环境（world editing plugin）；M7 短实机 LLM 基线。

**三因果实验收口追加（晚段）**：
6. 因果层级实测（§21）：**达成层**由表格闭包驱动，对经验/激活/先验边无感（A/B/C 三层一致负结果）；**知识访问层**是扩散的真实因果空间（B：视野外节点 0→0.43 等）；**行为层**（路径效率）是迁移记忆的真实因果空间（C：首动翻转、−3 tick/−1 动作）。"哪个机制在哪一层有因果空间"成为论文的可声明结论。
7. 审计 §10 停止条件 [A]–[F] 全部达成（§22），此后不再添加实验模块。

## 21 三因果实验 A/B/C/D（晚段收口）

> 详细报告：`EXPERIMENT_A_REUSE.md` / `EXPERIMENT_B_DIFFUSION.md` / `EXPERIMENT_C_TRANSFER.md` / `EXPERIMENT_D_EVENTFRAME.md`；变更 C17/C17b/C17d/C17e（A）、C18（B）、C19（C）、C20（D）。全部核心机制零改动：修改均为 装配套件（沙箱装配、账本/图继承/事件框架参数）/ 记录层（采样/统计）/ 环境数据层（配方表、树木品种）。seeds 1–5/条件，沙箱同 seed 全确定（逐 seed 相同轨迹），统计为 descriptive。

### 21.1 实验 A：知识写回 → 未来复用

- 任务：Ep1 8 轮实践 oak_planks（晋升线 KG 5/0.80 → Laplace 需 support=8，三阶梯中真实门槛）；Ep2 继承 vs 干净图。
- **写回成立**：promote 4 条/种子（操作/变化 节点 + 导致边 + mark_active，`reuse/ep1_graph_seed1.json`）。
- **继承链路成立**：Ep2 继承写回节点 6 个 + oak_log 激活残值 5.0 vs 0.0。
- **达成层零差分**（负结果，5/5 逐对相同）：success@9、3 动作、首动 birch——表格闭包遮蔽；知识只在访问层（激活残值）留下因果空间。

### 21.2 实验 B：扩散 → 相关知识访问（含沙箱扩散执行器修正，投稿必读）

- **前置发现（C18）**：B pilot 全采样点逐点相同 → diffuse_step 生产驱动方审计：app.py LLM 回合管线 + CCLoop.tick_once；**沙箱从未装配 CC 循环 → pre-C18 全部沙箱 run 无扩散在跑（激活冻结直写值）**。§13a/13b −Diffusion 列的含义修正见 §13d 注记。C18 在装配层补 SandboxDiffuser（decay+diffuse 每认知 tick，同 CC 语义），核心零改动。
- **知识访问层正差分**（C18 后，full vs β=0，5/5 全确定）：缺口"指向"对象 birch_log 1.5→5.0 封顶 vs 徘徊；**视野外纯扩散信号 oak_log 0.43 vs 0.0**；缺口源能量泄放（1.43→0.16，发射即转移签名）vs 钉住；2 跳点亮域 5 vs 4。
- **达成层零差分**（负结果）：success@9、3 动作全条件同——达成层无扩散因果空间（结构保证）。

### 21.3 实验 C：跨任务迁移（reuse + recombination）——行为层正差分

- 任务1 = obtain crafting_table（oak_log→oak_planks→crafting_table，A→B→C，8 轮晋升全绿 promote 7 条）；任务2 = obtain wooden_pickaxe（B→C→D 重组+延伸：oak_planks→stick→crafting_table→wooden_pickaxe，needs_table 深度链）。Transfer = 继承全图 + **同进程因果账本**（build_stack `inherit_causal/inherit_tl`：同一 CausalLearner/ExperienceTimeline 实例）；No-Transfer = 干净图 + 空账本；同 world 同配方表。
- **知识层**：action_prior（autonomy.py:2790 消费：成功率记忆 + prior_bonus ±0.05）差分**精确命中共享结构动作**——gather_resource(oak_log)/craft_item(oak_planks)/craft_item(crafting_table) 全 (1.0, obs=8) vs (None, 0)；任务2 新动作（stick/pickaxe）两条件同空。
- **行为层正差分**（5/5 全确定）：No-Transfer @16 / 9 动作 / 首动 birch_log（好奇缺口）→ Transfer @13 / 8 动作 / **首动 oak_log**（继承激活残值 + prior_bonus），重组事件提前一拍（idx 5→4）。
- **达成层零差分**（负结果）：双 5/5——表格可达性遮蔽"迁移→达成率"的因果空间，不遮蔽"迁移→路径效率"。
- 开放项（不超卖）：birch_log t0 激活 2.28 vs 5.0 的机制步序未逐拍归因。

### 21.4 实验 D：行动侧事件框架失败抑制探针（愿景通道首次行为学审判）→ 修复后行为差分 5/5（C20e）

- 动机：论文愿景 §552-562 断言"语义节点沿边传播至情景记忆事件节点→引发经验回忆→调制认知"，行动侧却无事件框架节点/无极槽位边——愿景的行动侧通道从未被架起。D 直接装配最小样板（C20 + C20e）：事件框架节点 `行动经验:gather_resource(birch_log)`（episodic 空间）+ 槽位边 `-[涉及]→` 实体（负权 fail_side=-0.8）+ 结果边 `-[结果]→` 失败（+0.9，支撑 total_w>0 使抑制被发行），注入失败经验（`source="experiment-injected"`，非真实失败路径），每认知 tick 低量再点火（§485 记忆再点火的沙箱替身）。control vs inhibit 同世界同目标（oak 目标链 + birch 好奇缺口，obtain oak_planks）。
- **机制层正证据（愿景首次实证，C20）**：负极性槽位边经扩散引擎统一抑制机制（负权贡献 = src.act×|w|×beta×gain/total_w，不消耗发射预算；**抑制仅在节点 total_w>0 时发行**，diffusion_engine.py:1021）把 birch_log 激活 5.0 封顶 → 2.74 稳态，5/5 全确定。
- **C20 定版行为层零差分 + 两层根因**：首动 birch、@9、3 动作两条件逐 seed 相同；参数扫描实体压到 0.00 首动仍 birch——①**缺口直写锚点结构锁**：birch 候选 reason 含 `缺口:用途(birch_log)`（floor 0.9 + 每 tick mark_active 直写，autonomy.py:1516-1517），`_score_action` attention=max(reason 激活)/5 恒落缺口；②**novelty 顶格**：autonomy.py:2708-2711 前缀回退——图内无此节点时 `UnknownBlock_*` 无条件 novelty=1.0，而沙箱 bridge 只消费已建档节点、从不建档。
- **C20e 修复（装配层三层，核心零改动）→ 行为差分 5/5 翻转**：蓝图② warmup——首拍决策前先跑 3 拍 diffuser+efi（抑制在注意力竞择前积累；C20 原版 efi 在决策后，首拍前抑制从未入图）；蓝图① 缺口锚点列入负权涉及边目标（抑制直达 attention 供给源）；C20c 补建 retired `UnknownBlock_{obj}`（失败=充分接触 → novelty 熄灭）。结果（5/5 全确定）：control 首动 birch @9（缺口锚点 1.5 托底、birch_log 5.0 封顶，逐位复现 pre-fix 基线）；**inhibit 首动 oak @6 n=2**（birch_log 0.0 压灭、缺口锚点 0.0、retired 0.6、事件节点 2.2844 稳态）——达成提前 3 拍、省 1 次好奇动作。
- negative-side：真实失败路径未测（注入源）；睁眼行为翻转与注入存在性严格对应，5/5。
- **C20g 真实失败路径（追加，2026-09-28 深夜）**：落图时机从 build 预注入移到**真实失败回执**——`am.on_settled` 链捕获结算失败 → `efi.arm()` 现场落图（内源成型）；世界侧 despawn_on_dig 在首挖时移除 birch 树（"她到达时树已不在"）制造真实失败。四臂 × 5 seeds 全确定：control_real@17 n=4 red=1（birch→birch重试→oak→craft，纯失败计数下失败后重试一次）；**inhibit_real@15 n=3 red=0**（birch→oak→craft，失败回执落图后零重试——差分发生在同一失败事件内）。关键机制事实：失败形态=timeout 暂态失败（autonomy.py:106，不进 _attempts/冷却/因果账）——**17→15 的差分发生在统计层零记账的失败上，图侧事件框架是唯一留痕与施压通道**。
- 论文口径（升级二段）：§552-562 可引"事件框架式失败经验经负极性槽位边调制实体激活与缺口注意力锚点、改变后续行动候选选择（行为级实证，D）"；**且可引"真实失败回执内源成型事件框架，在其统计通道静默（timeout 暂态）时独力改变后续行动选择（D/C20g）"**；保留限定"沙箱装配层注入失败事件、非生产自愈路径"。
- 完整报告：`EXPERIMENT_D_EVENTFRAME.md` §8 修复后复测定版 + §9 真实失败路径探针；变更 C20 + C20e + C20g。

## 22 声明审计收口（PAPER_CLAIM_AUDIT 摘要）

> 全文：`PAPER_CLAIM_AUDIT.md`；审计 §10 停止条件 [A]–[F] 全部达成（A/B/C）。D 为愿景通道扩展探针（§21.4）：C20 负面结果（缺口锚点结构锁）经 C20e 修复翻转为 5/5 行为差分，口径已并入 §22 清单第 7 条。

**可引用声明**：
1. 写回机制在生产路径真实发生（8 轮 → promote 4–8 条，含 mark_active）；
2. 跨 episode 图传输链路成立（inherit_graph 结构+激活双继承）；
3. 达成层对"经验/激活类知识"无感——表格闭包遮蔽（A/B/C 三层一致负结果）；
4. 扩散在知识访问层有真实可测差分（B，需 C18 装配在场）；
5. 同进程因果账本的 action_prior 成功率记忆跨任务迁移并产生行为层可测差分（C）；
6. 机制表是短链达成必要计划知识，"no-prior learning"被 A 条件 0/6 否定；
7. **事件框架负极性槽位边经扩散调制对象实体激活与缺口注意力锚点、改变行动候选选择（机制+行为级实证，D+C20e+C20g）**——愿景"负激活调制"成立且具行为效力：失败经验经负权槽位边压实体（5.0→0.0）、压缺口锚点（1.5→0.0）、熄 novelty（retired），首动好奇 birch → 目标 oak（5/5）；**真实失败回执内源落图（C20g）在同一失败事件内压掉重试冲动（17→15 tick, red 1→0），且该失败在统计通道静默（timeout 暂态）**——图经验通道与统计经验通道分立的直接实证。注：沙箱装配层注入失败事件、非生产自愈路径。

**禁止/修正引用**：
- **禁止**：矩阵 −Diffusion 零差分作为"传播无贡献"依据（pre-C18 无扩散执行器，见 §13d 注记）；
- **禁止**：A 的行为零差分推广为"经验写回无行为收益"（C 已拆出行为层差分）；§552-562 事件帧调制声明不可扩到"失败记忆改变行为选择"——**已被 D+C20e 推翻**（行为差分 5/5，见声明 7）；仍不可扩的是"真实失败路径/自愈"（D 为装配层注入，非真实失败路径）；
- action_prior 差分仅存在于同进程账本通道；沙箱数值只宜引用相对差分与机制签名。

---

### 附：§31 变更账本（本次会话摘要，全文见 CHANGE_LOG.md）

| change_id | 位置 | 原因 | 设计保留 | 影响实验 |
|---|---|---|---|---|
| C07 | experiment_recorder.py | 事件丢失（%100 flush 缺陷） | 是 | 全部 |
| C08 | experiment_recorder.py | self._lock → 模块级锁 | 是 | 全部 |
| C09 | run_mc_observe.py | graph_delta 形状兼容 + causal 采样 | 是 | M1/M2/M3 |
| C10 | sandbox_lab BLOCK_META | deepslate_iron_ore 供给 | 是 | iron 任务矩阵 |
| C11 | run_ablation_matrix setup_world | 预置工作台（环境事实） | 是 | iron 任务矩阵 |
| C12 | sandbox_lab 探索量程 | 壁钟/仿真失配伪影绕行 | 是（环境侧） | **全部沙箱**（修复前后对比见 §13d） |
| C13 | run_baselines.py | llm_heavy 守卫绕行恢复 | 是 | 基线 |
| C14 | run_sandbox_scenarios | S5 rearm 时序 + flush_causal | 是 | S5 |
| C15 | run_ablation_matrix ABLATIONS | 规格组合消融补齐（−Diff−Epis / −Diff−Gap） | 是 | 消融矩阵（11 组终版） |
| C16 | sandbox_lab prior_level + run_prior_conditions | 先验条件 A/B/C 对照 | 是 | 新增 prior_conditions 族 |
| C17/C17b/C17d/C17e | reuse/感知接线/环境事实注入/纯种树 + run_exp_reuse v3 | 实验 A（写回→复用；8 轮晋升线适配） | 是 | 实验 A |
| C18 | 沙箱扩散执行器补装 + run_exp_diffusion | **扩散执行器缺失修正**（矩阵口径修正）+ 实验 B | 是 | 实验 B + 矩阵口径 |
| C19 | build_stack 账本/timeline 继承参数 + run_exp_transfer | 实验 C（同进程账本通道 + 行为层差分） | 是 | 实验 C |
| C20 | build_stack eventframe 参数 + EventFrameInjector + run_exp_eventframe | 实验 D（行动侧事件框架负极性槽位边探针；含接线 bug 两修） | 是 | 实验 D |
| C20e | EventFrameInjector 缺口锚点目标 + retired UnknownBlock；run_cond 首拍前 warmup 3 拍 | 实验 D 修复（蓝图①缺口锚点负调制 + 蓝图②再点火前置 + C20c novelty 退休）→ 行为差分 5/5 翻转 | 是 | 实验 D（结论修订） |
| C20g | EventFrameInjector trigger=on_failure + arm()；SandboxWorld.despawn_on_dig；am.on_settled 链 | 实验 D 真实失败路径（失败回执内源落图；timeout 暂态失败上统计层静默、图通道独力改变行动） | 是 | 实验 D（§9 追加） |