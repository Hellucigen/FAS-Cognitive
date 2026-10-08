# EXPERIMENT_PROTOCOL.md — Capability Gap Campaign v2(设计冻结 2026-10-01)

冻结原则:运行前锁定假设、条件、样本量、统计;运行中只许修 harness bug(smoke 阶段),不许改任务定义与判据。

## 总设计

- **Harness 层**:决策头层(与信标战役同构:Task 分相 + Executor 菜单 + LLMHead 温度 0 + BeaconFASContext 生产建图装配)。FAS 条件 = 生产语义上下文(图谱+扩散+需求分析);ablation 走既有参数开关;LLM baseline 走同一菜单/同一世界。
- **练习相零 LLM 脚本化**:所有条件的练习经历由 harness 直接执行同一动作序列(先 gather 后 craft),逐条进入该条件的记忆系统(FAS ingest / history store / retrieval store)。这保证 (i) 三类记忆系统收到逐字节相同的经历流;(ii) 练习不消耗 LLM 决策预算;(iii) 公平性可审计。
- **测试相**:每相 LLM 决策预算固定(10),温度 0,max_tokens=200,3 次重试,同停止条件。
- **配对种子**:10 个(0..9);每种子全条件同一世界布局/同一练习流/同一 LLM。
- **统计**:成功用配对 McNemar 精确检验;决策数/tokens 用配对 Wilcoxon(scipy, exact≤25);族内 Holm 校正并报告校正前后 p;效应量 r。**任何"优于 baseline"主张只看校正后 p**。
- **证据分层**:每结论标注 L1 机制 / L2 行为 / L3 比较(任务书 §22)。

## 实验卡(Tier 1)

### A Continual Learning
- 假设:H-A1 FAS 在 3 相无关经历后仍能用第 1 相经历解决闭卷任务;H-A2 该能力并非 LLM-history/LLM-retrieval 不可及(比较层结论服从数据)。
- 相:P1 练习 A 链(gather oak_log→craft oak_planks,脚本);P2 练习 B(gather birch_log→craft birch?无 birch 配方 → B=craft stick 链的练习,基于既有库存?→ **B 练习=craft_crafting_table 链**,配方表存在);P3 无关经历(explore+gather dirt);P4 闭卷测试 obtain oak_planks(菜单门控+facts 剥离);P5 重复 P4(持久性)。
- 图谱播种:配方闭包**排除 oak_planks**(A 链知识只能来自经历)。
- 条件:FAS / FAS-no-writeback(ingest 不建边,只建节点+激活)/ LLM-history(全流) / LLM-retrieval(嵌入 top-8) / Fresh-FAS(同播种、无练习经历)。
- 指标:P4/P5 success、决策数、首动作、记忆延迟(P4 首个相关动作所在决策序号)、tokens;机制:图谱中 A 链关系在 P4 前的存在与激活。
- 失败判据:FAS 在 P4/5 与 Fresh-FAS 无差 → continual learning NOT DEMONSTRATED(行为层);如实报告。

### B Delayed Consequence / Credit Assignment
- 机制部分(零 LLM):sandbox_lab 生产栈,动作 A=gather oak_log,后果 C=craft oak_planks 成功观测,中间插入无关事件 0/2/5/10 个(各占 1 拍 sim 16s),flush 因果窗后查 A→C 关系是否形成(action_prior/关系边)。假设:窗 8–120s → delay≥8 拍(128s)后不形成;画 delay–performance 曲线。
- 行为部分(决策头):delay-d 练习后,测试相选择 obtain oak_planks(闭卷),看 FAS(继承因果)是否仍选 A;对照 LLM-history(拿到含延迟后果的完整流——LLM 无窗口限制,可能不衰减)。条件:FAS-inherit / LLM-history / Direct-LLM,delays {0,2,5,10},n=10。
- 失败判据:FAS 随 delay 下降而 LLM-history 不降 → 如实报告(窗口限制=FAS 真实短板)。

### C Persistent Intention
- 相:P1 指令 obtain oak_planks(开卷,LLM 决策,3 步预算,大概率部分完成);P2 指令 obtain dirt(无关,完成);P3 中性提示"You have free choice"(2 步预算×3 轮),成功=在无指令下完成 oak_planks 或至少重拾其链(首动作 gather_oak_log/craft_oak_planks)。
- 条件:FAS / LLM-history / Direct-LLM / Fresh-FAS。
- 指标:P3 重拾率(首动作相关)、完成率、goal 相关节点在 FAS 上下文中的在场(机制遥测)。
- 注意:FAS 无生产级 no-intention 决策头开关(F2 记录);消融解读限于机制遥测。

### E Goal Revision
- 相:P1 指令 obtain oak_planks,世界中 **只有桦树?无** → 设计:世界初始只有 birch_log 方块 + oak_log 方块在远处;指令目标改为"obtain stick"(需 planks→stick,planks 需 oak_log);P2 中途 harness 移除全部 oak_log(harness 级环境变化,allowed),替代方案出现:无 → **改设计**:目标 obtain oak_planks; oak_log 方块 2 个,先移除一个(失败经历),另一个在 16 格外(需 explore);测:重复 gather_oak_log 失败后是否探索/转向。事件框架装配(FAS+ef)提供失败抑制。
- 条件:FAS+eventframe / FAS / LLM-history / Direct-LLM。
- 指标:重复失败 gather 计数、到成功 gather 的决策数、放弃率。

### F Self-model Behavioral Relevance(生产环,零 LLM)
- sandbox_lab.build_stack:三段历史:P-s 成功任务(obtain oak_planks,5 seeds 全成)、P-f 失败任务(obtain iron_ingot,无熔炉先验,必然失败)、P-n 无经历;然后自由选择相(loop 无 goal 注入,读自主层目标选择遥测)。
- 条件:self_goal_on(True/False)× 历史类型,5 配对种子。
- 指标:自由选择相的首目标类型分布、重试/探索计数、成功任务知识复用。
- 判据:无显著行为差 → "mechanism present but behavioral benefit not demonstrated"。

### I Multi-experience Convergence(核心)
- 任务:闭卷 obtain oak_fence(需 planks×4+stick×2;配方闭包**排除 oak_planks/stick/oak_fence**)。
- 经历(脚本):A=gather oak_log+craft oak_planks;B=craft stick(练习,基于脚本预设库存?脚本练习在同一世界连续执行,库存累积:B 的 craft_stick 直接用 A 产生的 planks——经历流如实记录);C=craft_crafting_table(无关)。
- 条件:I1 FAS+A;I2 FAS+B;I3 FAS+A+B;I4 FAS+A+B+C;I5 LLM-history(A+B 原始流);I6 LLM-retrieval(A+B);I7 FAS-no-diffusion(A+B+C)。
- 指标:success、决策数、机制遥测(共享节点 oak_planks 激活、上下文组成)。
- 关键比较:I3 vs I1/I2(汇聚增益的行为面)、I3 vs I5/I6(FAS 汇聚态 vs LLM 拿同样经历)。
- 失败判据:I3≈I5/I6 → BASELINE-EQUIVALENT,如实报告。

### K History vs FAS State Compression
- 任务:闭卷 obtain oak_planks(同 A 的 P4)。
- 条件:FAS state / LLM-history-window K∈{10,25,50,100,200}(练习流 22 条;不足 K 时给全量)/ LLM-retrieval / Direct-LLM。
- 指标:success、相关上下文 tokens、决策数;画 history-length→performance 与 →token cost。

## Tier 2 / Tier 3
- D(自主子目标,预期 NOT DEMONSTRATED,记录即完成)、G(跨环境迁移)、H(步数梯度)、J(100-cycle)视 Tier 1 结果与时间执行;若 Tier 1 暴露架构性阻塞(如 relation direction),Tier 3 可停(任务书 §25)。

## Token 拆解(§18)
每次决策记录 prompt/completion tokens,并按来源拆分:task+obs / history / retrieval / FAS graph serialization / activation metadata。由 harness 在 serialize 时分别计数。

## Harness 有效性(§19)
每个实验正式运行前 smoke:3 seeds × 全条件,检查:图非空、边非空、激活传播、demand/gap/routing 无异常、各条件获得正确信息、ablation 确实关闭(no-diffusion:上下文 activation 全 None;no-writeback:测试前图谱无经历边)、token 记账、闭卷门控生效。任一失败 → STOP 修 harness。


---

## 协议修订记录

**v1.1(2026-10-01,K 正式运行中途、K 结论产生前)**:K 的练习流仅 6 行,
history 窗口操纵(10..200)全部退化为"给全量历史",自变量失效。修复:
新增 `script_practice_long`(60 个确定性小情节,~200+ 行经历流,资源即时重生),
K 重跑;首轮 K 的 35 行数据改名 `raw_K_v1_design_inert.jsonl` 留档不入结论。
I 的决策预算 10→16→20 与 facts 对称化、B 练习预置量修正,均记录于
PRECHECK_REPORT.md 附录(smoke 阶段修复)。统计判据、假设、配对结构不变。


**v1.2(2026-10-02,F 归因修正与 I 表示研究)**:
1. F 重做:原 self_goal_on 消融钩子指向不存在的方法(F3 harness 缺陷,结论作废)。
   修正链:①消融改挂生产同步点 self_graph.set_current_goal(模块层封写,运行后恢复);
   ②发现沙盒装配缺 Self 节点(build_stack 只建 "Haru",SELF_ID="Self")→
   调生产公开 API bootstrap_self 对齐;③发现需显式调用生产入口
   ActionManager.set_goal_context(装配内无人调用);④发现并修复消融补丁
   未恢复导致的跨运行污染。最终 v6:ON 同步遥测 5/5/5(开始/中/结束),
   OFF 0/0/0——开关有效性经遥测证明。另发现 current_goal() 在生产代码中
   零读取者(写入路径存在、消费路径不存在,与论文 U2 同构)。
2. I 表示研究(假设先冻结):H-rep 富序列化(在 top-k 之外补 动作→结果
   配对与动作~实体共现,同一图谱内容仅改呈现)恢复行为层完成;
   H-budget 单纯增大 k(16/32)不恢复。失败判据:两者都不恢复 →
   表示假设弱化,界面限制确认。零机制改动,消费者格式/预算参数级。
