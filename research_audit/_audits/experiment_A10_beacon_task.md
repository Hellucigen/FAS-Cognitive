# A10 实验规格 — 打破"任务闭包遮蔽"的信标实验(2026-09-30)
**状态**: 战役收官(2026-09-30)。数据=四启段(seeds 0-14 + 15 三条件)+ 批次A(seeds 16-19)16 局 + 批次C(llm_direct seed15)1 局,三源合并共 80 局:合并前校验 80 行/pre-registered 配对、0 INVALID、0 重复。执行史与装配事故见 §4.1(h)、FAILURE_REGISTER L2-HAR-01/02。

## 0. 背景(证据在案)
- 论文 \S\ref{sec:negative-closure}:闭包保证的任务上,规划器足以达成,激活级知识落在决策包络之外 → 表示层变化在成功指标上不可见;
- 路由 v2 战役边界假设:"任务完全可观察 ⇒ 记忆上下文边际价值受限"(\S\ref{app:routing} 解读要点)——未测场景=信息不对称;
- 论文 \S\ref{sec:future} 已列入 open question。

## 1. 假说(预声明)
- **H_A10_closure_break**: 决策所需信息不在当前观察中(目标配方/位置只存在于记忆),信息不对称信标任务上,带记忆上下文(FAS 图检索)的条件达成率/决策步差分子 0(基线条件任务对照;同 C34 v2 配对协议)。
- Null: 信标任务仍被闭包遮蔽(规划器总能从当前观察推出路径,记忆上下文不值钱)。

## 2. 协议起点(复用路由 v2 harness)
- harness:机制实验族已备 RQ/配对结构;信标任务构造 = 环境初始观察**不含**目标所需的一环配方信息(需先"复位——观察——再决策"或跨 episode 检索);
- 条件:记忆上下文(图检索注入) vs 无记忆(direct);配对种子 n=20,单尾(预期方向);
- 指标:达成率/决策步数/token(同 \S\ref{app:routing} 表结构),预声明 + Bonferroni 校正;
- 需新代码:信标环境生成器(属实验 harness,非生产)。

## 3. 产出与边界
- 若 H_A10 成立:闭包遮蔽的条件边界被划出(记忆价值=信息不对称时的可达性),直接支撑论文 §future 的路由价值讨论;
- 否则:遮蔽比假说更强健,如实报告否定结果(同 45 章审计纪律)。
- 状态: **待执行**(设计已定,执行需信标生成器实现 + 一轮配对战役,时间与 663 runs 族同级)。

## 4. 操作化与预注册修订(2026-09-30,数据前锁定)
**信标 B1 "closed-book"(配方知识只存在于记忆)**:
- **任务**: goal="Obtain 1 oak_planks.",场景=T1 同款(oak trees 开局可见)。**facts 全条件同一且剥离配方**: "You do not know the recipe for oak_planks; nothing in the current observation explains how it is made. Learned recipes are remembered in the knowledge graph (物品 // `* 产出 *` edges)."
- **信息不对称操作化(craft 菜单门控,实验 harness 新增,非生产)**: 工艺书对未知配方不可见——`craft_X` 仅当 `物品:X` 出现在当前轮上下文的 selected node ids 中才列菜单。baseline 无图上下文 → 恒不可见(配方知识真缺失=确定性对照);D 族 → 由各自机制决定是否把 `物品:oak_planks` 带出。
- **防泄漏订正**: 置 `seed_goal=False`(FASContext 子类)——ingest 不再把 goal 实体(oak_planks)直接注入激活(生产 v1/v2 任务该行为对闭包测试 = 目标点先验,在信标中会杀死不对称);节点/边照常存在。
- **条件**: fas_full / D-noact / D-flat / llm_direct × 20 seeds(temp=0,需如实声明高复制率)。
- **指标(预声明)**: success(0/1)、decisions、target_utilization、first_action、steps_to_craft(首个 craft 决策步;∞=未达成)、token(prompt/completion)、menu_craft_locked(每轮隐藏的 craft 条目数)。主对比 3 组 × 2 指标(success, decisions)= **6 检验,α=0.0083**(Bonferroni);success 用配对 McNemar,decisions 用配对 Wilcoxon。
- **预期方向(单尾)**: fas_full > llm_direct(知识存在 vs 缺失);fas_full > D-noact(链式回忆必要);fas_full ⊘ D-flat(名字相似 vs 链路——无先验方向,双向)。
- **判定预注册**: fas_full 成功率为 0 或与 llm_direct 无差 → H_A10 不支持(如实否定报告);预检(零 LLM)必须证明 `物品:oak_planks` 可由扩散从 `物品:oak_log` 点亮(链存在),否则装配无效退出。
- **成本**: ≤80 runs × ≤8 决策 ≈ ≤640 次 MiMo 调用(小 prompt;与既有战役同档)。产出 `experiments/beacon_v1/`。

## 4.1 装配修订(2026-09-30,预检阶段,零 LLM 数据前锁定)

预检(`run_exp_beacon.py --preflight`,零 LLM)按预注册裁定触发,但以**装配级发现**收尾。逐条记录:

**(a) v2 harness 史实补记**: `run_exp_routing_v2.py` 只 import 了 `install_fake_bridge` 从未调用(line 47 noqa 真义)——整个 routing v2 战役(663 同族域外,866/T1-T4)的图里**配方先验闭包从未建立**,物品节点是孤立空节点;v2 的对比信息来自 goal 实体直注入 + 观察共现边,与闭包无关。此事实与本规格正交,不影响 v2 已出结论(facts=tok 等均在 prompt 侧),但更正了"相关侧记忆上下文含世界知识"的隐含说法。

**(b) 扩散方向语义(生产真源,graph_schema/config 在案)**: `产生/需要` 属 procedural_relation,按 `relation_propagation` 全类别 forward;8 白名单双向之外无回传。配方闭包边 = 配方→物品 单向 → 物品:oak_log 是**入边且无出边**(A9 sink_fraction=1.0 的生产图投影),从原料方向经扩散**永不可达** 配方/产物。即预注册 §4 设计的记忆载体(产出边)在该架构语义下不可检索——配装前提不成立,预检裁定正确。

**(c) 预注册的 v2 preflight 探测伪阳性**: v2 `preflight` 的"新点亮"对比基线 `before` 在激活注入**之前**采样,种子节点自身会进入 newly_lit → 传播探测恒过、不证多跳可达。本实验预检改为**专用断言**: `物品:oak_planks ∈ 点亮集`(种子之外),不再依赖 v2 的笼统探测。

**(d) 装配修订(数据前锁定)**,条件/指标/检验数/α/判定规则**一律不变**:
1. `BeaconFASContext` 构造前调用 `sandbox_lab.install_fake_bridge(world)`——配方先验经**生产建图路径** `ensure_mc_world` + `build_recipe_closure` 用沙箱世界自身 RECIPE_TABLE 生成(与真机 minecraft-data 的供给侧关系相同;节点/边 id 方案与 world_prior 一致:`配方:{res}:{hand|table}:{i}`、`物品:x`、产生/需要 单向)。这是补装配缺失,非改机制。
2. **记忆检索通道**: 每食谱加 `物品:{ing} -相关-> 物品:{result}` 关联边——`相关` 属生产 8 白名单(双向),是生产图内"对称关联知识"的既有载体,也是 S7 已验证的配方双向化检索 treatment 的同族表示。`产生/需要` 方向语义保持 forward 不动(A9 裁决维持)。此通道是"学过的关系"在架构词表里的诚实投影:先验(怎么做)与关联(有关)并存。
3. 预检断言随之变为: seed=物品:oak_log → 4 步扩散后 `物品:oak_planks` 点亮(且非种子自身)。失败仍按预注册退出。

**(f) 观测词表解析(预检阶段同批发现,预数据)**: 环境观测格式 `oak_logx9`(计数后缀)在 v2 的 tokenizer(`[a-zA-Z_]+`)下整个落不进 GOAL_VOCAB——物品名进图的通道在 v2 战役中实际失效,**v2 的物品边全部来自 goal 实体直注入**(与 (b) 同源:goal-seed 掩盖了三处装配缺陷)。信标 `seed_goal=False` 后该通道必须真实: B1 专用 `entities_of` 剥计数后缀(`xN`)+ 按节点名实体存在性解析(单字词表不含下划线块名如 `oak_log`)。属 harness 观测解析修补,无关记忆/扩散机制。

**(g) 决策头 token 预算(2026-09-30 预检阶段发现,预数据)**: `v2.LLMHead` 硬编码 `max_tokens=40`。miMo 侧现返回 `reasoning_tokens`(vendor 端推理模式默认开销)——真实决策 prompt 下 40 预算被推理吃光(复现:39 reasoning/40,content=''),finish=length → **每次决策必空回复**,战役 30/30 全 wait。200 预算下正常(`{"action": 0, ...}`)。修订: `llm.max_tokens = 200`(装配属性,四条件共用同一决策头,无条件偏差)。v2 战役是否同病属待核(此处只记 Harness 史实,见 FAILURE_REGISTER L2-HAR-01)。

**(e) 判定规则不变**: fas_full 成功率 0 或与 llm_direct 无差 → H_A10 不支持;三组主对比 6 检验 α=0.0083 照旧;D-flat(名字相似)与 fas_full(链路)双向无先验方向。

**(h) 并发写污染事故与单实例锁(2026-09-30 四启前,数据前锁定)**: 三启(b04raw4xa,max_tokens=200 修复版)与二启孤儿进程(b55muu05n,会话结束仅杀 shell、python 存活)并发向同一 `raw_results.jsonl` 写入——文件 103 行 task_result(预注册 80)、seeds 10-16 双份、NUL 损坏行、summary.csv(20:50)为二启的全 wait 数据而 raw 头为健康数据,证据链完整(见 FAILURE_REGISTER L2-HAR-02)。**处置**: 三启立即杀死、污染文件整体作废(备份 `experiments/beacon_v1/polluted_2proc_evidence/`,不作挑选),`run_exp_beacon.py` main() 前加 msvcrt 单实例锁(同 out 目录第二进程拒绝启动),四启为干净重跑、进程清单前验(python 空)+ 直接写 log 不走管道。指标/检验/判定不变。

---

## 5. 结果(2026-09-30,战役收官)

**数据来源**: 四启段(seeds 0-15 的 63 局,四启进程 21:46 被后台时限杀)+ 批次A seeds 16-19(16 局)+ 批次C llm_direct seed15(1 局);三源合并前逐项校验:80 行、配对 (cond,seed) 唯一且覆盖全部 4×20、0 INVALID、0 重复。最终文件 `raw_results_final80.jsonl`。

**汇总(n=20/条件)**:

| 条件 | success | 成功率 | dec 均值/中位 | steps_to_craft 均值 | craft 菜单可见步均值 | unparseable 总计 |
|---|---|---|---|---|---|---|
| fas_full | 20 | 1.0 | 2.15 / 2.0 | 2.15 | 1.15 | 3 |
| D-noact | 0 | 0.0 | 8.0 / 8.0 | — | 0.0 | 29 |
| D-flat | 20 | 1.0 | 2.1 / 2.0 | 2.1 | 1.05 | 2 |
| llm_direct | 0 | 0.0 | 8.0 / 8.0 | — | 0.0 | 16 |

首动分布: 三条件 19-20/20 gather_oak_log;llm_direct 17 gather / 1 explore_north / 1 explore_east / 1 wait(种子级 MiMo 随机性,见下)。

**6 主检验(Bonferroni α=0.0083,配对)**:

| 对比 | success 配对 McNemar | decisions 配对 Wilcoxon |
|---|---|---|
| fas_full vs llm_direct | 20/0 vs 0/20, **p=2e-06** ✔ | 中位 2.0/8.0, **p=0.00009, r=0.620** ✔ |
| fas_full vs D-noact | 20/0 vs 0/20, **p=2e-06** ✔ | 中位 2.0/8.0, **p=0.00009, r=0.620** ✔ |
| fas_full vs D-flat | 20/20 vs 20/20(全一致), p=1.0 ✘ | 中位 2.0/2.0, p=0.715, r=0.129 ✘ |

**判定(预注册)**: fas_full 成功率 1.0≠0、与 llm_direct 差异显著(两项 p<α)→ **预注册的否定条件未触发,H_A10_closure_break 得到支持**:信息不对称信标任务上,带记忆上下文的条件(扩散检索)达成率/决策步差分子 0。

**结果的精益解读(诚实边界)**:
1. **记忆价值确认(信息不对称域)**: llm_direct 配方恒不可见(craft 菜单可见步 0.0)→ 成功率恒 0;任一图检索条件(扩散或 flat)都能在 ~1 步内把 `物品:oak_planks` 带进上下文解锁菜单 → 成功率 1.0。**记忆上下文在闭包被剥除时是可达成性的必要条件**,这是对"任务闭包遮蔽"条件边界的正面回答。
2. **扩散激活不构成增量(相对名字相似)**: fas_full vs D-flat 双向无差(预注册即无先验方向)——flat_focus 的名字相似检索在本信标上同样可达配方(相关链 vs 名字匹配均经 物品:X 节点进上下文,门控只认节点是否在上下文,不认检索方式)。**结论需按"记忆上下文有值、扩散激活在此任务上与名字检索等价"表述**;不能声称链式回忆相对平面检索的优势。
3. **D-noact 的 unparseable 偏高(29)**: 图在但激活不传播 → 上下文缺配方,LLM 多次无解输出;与 craft_visible=0 一致,属机制后果的附带观测,不单独计为缺陷。
4. **temp=0 随机性据实声明**: 同种子同 prompt 下 MiMo 回复仍有差异(seed15 llm_direct 首动 explore vs 其他 gather;fas_full seed12 曾出现 context 已含 planks、门控已开仍连续 wait×8 的个例——先后两役观察),种子作为乘法随机源保留,不做任何统计调整(预注册立场)。

**成本**: 全役 405 次 MiMo 调用,prompt 113,182 + completion 44,533 = 157,715 tokens(preflight 零 LLM)。不报金额(MiMo 计价未核实)。

**装配史附注**: 四启进程在 21:46 被后台时限杀,数据经批次化补跑(新 out 目录+单实例锁)达成完整性;写入中途被杀不污染已落盘行(每局 write+flush 原子性由 log 直接写保证,实测无撕裂行)。