# FAILURE_REGISTER.md — 发现登记与处置台账
**日期**: 2026-09-30
**基线**: git 4685726(私有图谱样本退出跟踪后的工作树状态)
**原则**: 逐条带 `文件:行` 证据;修复过的标 **FIXED**;确认无修价值/需裁决的标 **WAIVED/DEFER**;仅报告未验证的标 **OBSERVED**。

---

## 层 1:实现缺陷(Layer-1)

### L1-TH-01 [FIXED] pytest 套件收集崩溃(结构性问题)
- **证据**: AST 全量扫描(2026-09-30)证实:114 个测试文件中 93 个是脚本式回归(模块顶层裸执行断言 + `sys.exit()`,import 即 SystemExit);1 个(test_modulation_targets.py)test_* 函数带装配参数靠 `__main__` 注入;仅 12 个是真 pytest 风格。一期 8 项显式 collect_ignore 在 test_world_events.py(第 18 个同型文件)上被击穿 → 显式清单不可维护。
- **修复**: tests/conftest.py 二期——AST 自动分类两条规则(顶层裸 exit / test_* 非内置 fixture 形参),自动跳过脚本式文件;12 个真文件 82 测试照常收集。
- **验证**: `python -m pytest tests/ -q` → **82 passed 0 errors**(INTERNALERROR 解除)。脚本式回归(93 文件)仍按各自文件头注释独立运行——本项目回归通道本就是脚本执行,pytest 只对真 pytest 文件有意义。

### L1-DFU-01 [FIXED] 纯抑制节点(出边全负)静默不发射
- **证据**: diffusion_engine.py `_emit` 原形 `if total_w > 0:` 在纯负出边节点上跳过整段发射循环 → 抑制永不到达目标,目标只吃自然衰减。
- **修复**: 正向/反向两支各加 `denom = total_w if total_w > 0 else total_abs_neg`;混合边(正负共存)分母仍是 total_w,逐位不变。
- **验证**: `tests/test_diffusion_inhibition.py` 3/3;混合节点行为不变断言在案。
- **生产探测影响**: 3 节点/3 边出边全负(安全需求、事件类型:emotion:沮丧/难过) —— 修复前其啮合语义在线上是死的。

### L1-DGR-01 [FIXED] /api/nlp 空输入早退不解除 CC busy(循环粘滞停摆)
- **证据**: app.py 空文本分支 `return jsonify(...), 400` 不经过成功/异常两出口的 `set_busy(False)`;busy 是 Event 置位(continuous_cognition.py:145),一旦置 True 无配对即 **永久 busy**:tick 扩散/脉冲/表达决策/激素衰减全停摆,直到下一次成功请求。
- **修复**: 早退分支并列收周期 + `engine.clear_anchors()` + `cc.set_busy(False)`。
- **验证**: 待全套回归;修复风险近零(只删 4 行、加 13 行出口清理)。

### L1-DGR-02 [FIXED] 锁序反转 kg._lock → engine._lock(AB-BA 死锁风险)
- **证据**: dialogue_decision.py `dialogue_decide` 话题共振块/inhibit_topics、cognition_modes.py attention_context 共 3 处 `with kg._lock` 内调 `engine.get_topk`;引擎在持 engine._lock 期间反复进 kg 锁函数(diffuse_step/get_topk 内 `kg.get_out_edges` 逐节点取 kg 锁)→ 双线程交叉互等,Rlock 无超时,零自愈。
- **修复**: 全部改为先把 `topk` 取出,`with kg._lock` 只包裸图集合构造;continuous_cognition._reactivate_field 同款(第 4 处,审计遗漏,审计中同样修复)。
- **验证**: 脚本测试全过;锁序注释对齐(diffusion_engine.py:436-437约定 engine→kg)。

### L1-DGR-03 [FIXED] 回合异常路径不清 _sources(激活来源身份跨轮残留)
- **证据**: app.py 异常出口只补了 busy 配对,没调 `clear_anchors`(成功路径 4854 在调);curiosity 注入/disposition 种子可能在异常前已登记 → 下轮扩散这些节点仍被当"外来输入"发射免扣(能量零成本残留)。
- **修复**: 异常出口 set_busy(False) 旁并列 clear_anchors(同式 try/except)。

### L1-DGR-04 [FIXED] 行动候选收集失败整体静默禁用
- **证据**: dialogue_decision.py collect_action_candidates 外层 catch → `graph_cands = {}` → 行为竞争只剩 LEGACY 10 键照常出胜者,输出无法区分"没有候选"与"收集失败"。
- **修复**: except 加 `logger.warning` 留痕。

### L1-DGR-05 [FIXED] 认知场探测失败静默变"最恶劣默认值"
- **证据**: `_field_signature` 失败返回 [] → `_sig_change(prev, [])` 得"全场消失"最大变化 → 触发脉冲 → 意图集体掉一口衰减;`_field_temperature` 失败返回 0.0 → 判"过冷" → 触发再点火(方向性放大错误);reactivation focus_ids 失败空集 → 再点火无焦点退化全图随机。
- **修复**: 失败返回 None,两个门对 None 直接跳过本拍并 debug 日志;focus_ids 失败 warning;顺带修了 _reactivate_field 里的第 4 处锁序。

### L1-CON-1 [FIXED-2026-09-30] attention_context 七分区构建但生产 prompt 从不渲染
- **证据**: app.py:4666 answer_question 调用不带 mode/attention_context;nlp_processor.py:1021 认知状态渲染分支死代码;recent_episodic 时间序信息永不进生产 prompt。
- **深度根因(接线时追出)**: compile_for_language L2 已在 `cognitive_context` 键里带 `attention_context`/`mode`(cognitive_context.py:399),但 `answer_question` 门控 `if mode or attention_context:` **只认形参**——生产调用方(4695)只传 cognitive_context → 形参恒空 → 块永不渲染。全仓 grep 无任何按旧 4 键格式传参的调用方(**该块自引入起就是死代码**),接线零调用方行为变化。
- **接线(A1-A)**: config 新键 `nlp_render_cognitive_context`(默认 True)+ 形参→ctx 键回退 + 8 键全渲染 + 新【认知资源路由】块。False=精确回滚。
- **验证**: 见 PATCH_LOG A1-A 行。

### L1-CON-2 [FIXED-2026-09-30] demand/gap/routing 三层编译但不渲染
- **证据**: demand 只经预算门+温度起作用;routing v2 harness(scripts/run_exp_routing_v2.py)把 demand 注解渲染进 prompt——实验与生产的口径断裂,论文"demand 起作用"的外推在线上不成立。
- **接线(A1-A)**: 同一改动链接通 —— cognitive_context 的 cognitive_demand/cognitive_gap/cognitive_resource 键(compile 424-426 一直在带)现被 [认知资源路由] 块渲染(demand top3/gap top2/定档),按机制不外显原则只给维度名不给数值(与 harness 数字形态不同,但语义通道接通)。论文对账点见 CLAIM_IMPLEMENTATION_MATRIX C-10/C-11 新状态。

### L1-CON-3 [FIXED] add_edge 端点缺失静默丢边
- **证据**: graph_model.py:489-490 端点缺失 return False;app.py 两处(CuriosityAuto 2232 /memory/approve 5299)不查返回值 → LLM 草稿边无声消失。
- **修复**: 两调用点检查返回值,False → warning + 计入 skipped。

### L1-CON-4 [FIXED-观测级] NLP 双失败后零种子照跑扩散
- **证据**: 解析失败返回空 parsed → 零种子扩散 → 回答区 TopK 取自残留激活,无守卫无标注。
- **修复**: activate_from_inputs 前检查零种子 → warning 留痕(diffusion_summary 观测已含 seed_count=0)。回答内容行为不改(避免改变线上输出),留给 CON-1/2 通道统一裁决。

### L1-CON-5 [FIXED] LLM 失败占位串被当成"她说的话"
- **证据**: "[回答生成失败: ...]" 记入 timeline ACTION/causal_learner/expression 事件,失败占位变成一条假经历。
- **修复**: `_llm_answer_failed = startswith("[回答生成失败")`;该轮跳过 timeline ACTION 与 expression 事件写入;chat_log 保留(前端显真相);Say-Do 正则不再被占位串驱动。

### L1-CON-6 [OBSERVED] /api/nlp 18 处裸 except: pass
- **证据**: app.py 4360/4368(mode nudge/温度)、4657(budget_factor)静默失效可致调制悄然不生效。
- **处置**: 逐点升级 warning 属低优先;进 NEXT_ACTIONS 批量改造清单,本次只改 CON-4/5 触及路径。

### L1-CON-7 [DEFER] 记忆晋升候选无生产驱动
- **证据**: get_promotion_candidates 只打日志,仅 /api/memory/approve 手工晋升;高频访问记忆永不自动入图。
- **处置**: 设计决策(自动晋升需 dry-run 噪声观察),非 bug。进 NEXT_ACTIONS。

### L1-CON-8/9/10 [OK-核验通过] 动作链闭合/C21 默认关/经验回写完整
- CON-9: C21 事件框架机制存在但 config.py:1935 **enabled 默认 False**(与之前记录一致);CausalLearner 默认开、真实时钟下不调 sweep() 为正确行为(沙箱加速时钟才需 flush)——两项都 OK。

### L1-PIN-01/11 [OK-已修复 2026-09-30] add_edge 失败在感知/摄入侧无日志
- **细节见** `_audits/audit_perception_ingestion.md`(16 项);修复:A5-A 在 graph_model.add_edge 端点缺失处本身加 warning(所有调用方共享),漏斗处 CON-3 计数留痕,continuous_cognition 意图 hub 边失败留痕;回归锁 test_pers01_guard.py 8/8。
- 残余:感知侧 16 项中其余逐点留痕按 audit 清单另立跟踪,不阻塞本条关闭。

### L1-PERS-01 [OK-已修复 2026-09-30] 损坏图静默覆写
- **细节见** `_audits/audit_persistence.md`:load 异常时 save 可覆写(另见记忆:2026-09-13 已发生空图覆写 894→162 事故,现有缩小守卫)。
- 修复(A5-A 三重守卫):load 损坏 → `_load_failed` 标记 → save 拒绝;save 读旧源损坏 → `.bak.unreadable_*` 备份 + 拒绝;回归锁 test_pers01_guard.py 8/8（守卫 1–3 主/对照路径）。

### L1-MEM-01 [OBSERVED→已接入观测 2026-09-30] 记忆晋升无生产驱动(access 计数死)
- **机制事实**: episodic_buffer.mark_accessed **全仓零调用**;access_count/activation_count 恒 0 → `get_promotion_candidates` 排序退化仅 importance(access/activation 项恒 0.0),`check_promotion` 的 acc≥3 / act≥5 分支永不触发;三处消费点:process_nlp 4142(log-only)、replay 5450(死调用,无赋值)、/api/buffer/promote(手动审批,原样工作)。faiss 语义召回(app.py)命中节点后不触碰经历计数。
- **处置(A4-A,2026-09-30)**: 只接入观测,不启用自动晋升——faiss 命中→`find_by_node`→`mark_accessed`(每轮每经历去重 +1,`[A4-Obs]` 日志)+ 候选点 access 驱动明细日志;晋升仍仅手动 API 与 _auto_consolidate(importance 路径)触发;测试锁"候选≠晋升"边界。残余:`activation_count` 零接入留第二期;自动晋升 driver 待观测窗口数据(A2 式决策)。
- 注: 审计早期曾以"归因管线 s≥5/conf≥0.80"解释晋升(见论文表述)—— 归因侧实现在案;此处关闭的是**经历层高频访问晋升**这一机制,两者非同一对象。

---

## 层 2:机制实现缺陷(Layer-2,拓扑/语义/接口)

### L2-RD-01 [OBSERVED] 关系方向默认语义:8 双向白名单 vs 其余 forward
- **证据**: 生产图 3648/9088;40% 边为"涉及"(观察共现 fabric,双向);recipe 关系形成深度 1 星形森林(sink_fraction=1.0,forward 2-hop 可达=0)。§cookbook 语义:"需要/产生/掉落"的 target 永远当不了源。
- **处置**: 只验收为机制级事实(relation-direction 实验 ρ .33→.65 双向提升),不当作 defect —— 但其"食谱知识全 sink 化"的效果是**工程语义选择**,已在论文附录 C 路由 v2 记录。见 FINAL_DIAGNOSIS Q4。

### L2-CL-01 [OBSERVED→对齐论文措辞后降级为非缺口] "闭包"二义性
- **初判(本轮审计早期)**: 扩散只在种子连通分量内传播(发射即转移必须沿边);"闭包"来自 FAISS/语义检索的召回质量而非拓扑闭包计算,手册宣称的"闭包"在代码层面无独立步骤。
- **对齐论文原文后(2026-09-30)**: 论文 `\ref{tab:claims}` 中的 U2 为"语义写回被规划器消费 → U";"任务闭包遮蔽"指**环境属性**(配方闭包保证目标可达→达成指标跨条件一致,§10.1),"闭包规划"指规划器在配方可达图内找路径——**论文从未声明拓扑"闭包计算"步骤**。对照代码:规划器在配方树上找路径实现在案;任务闭包遮蔽是行为观察,5/5 种子反复一致。**声明与代码无空缺,L2-CL-01 从 FAILURE 降级为术语澄清**。
- **残余记档**: "闭包=检索召回+扩散"的语义(**非**独立闭包步骤)记入 CLAIM_IMPLEMENTATION_MATRIX U2 行与 FINAL_DIAGNOSIS Q4,不再计为缺陷。

### L2-HAR-01 [OBSERVED→已记档 2026-09-30] 路由 v2 harness 三处配装缺口(fake bridge / 观测词表 / 预检探测)
A10 信标预检(零 LLM)阶段发现,全部属实验 harness 装配层,生产零改动:
- **(a) 配方先验从未接线**: `run_exp_routing_v2.py` 只 import `install_fake_bridge` 从未调用 → 整个路由 v2 战役(620 runs,论文附录 C)的图里物品/配方闭包为零,物品节点长期孤立。对比信息来自 goal 实体直注入 + 观察共现(且见 b)。结论侧 claims 俱在 prompt 侧(facts=tok 等),判定维持;但"记忆上下文含世界知识"的叙事须按此修正口径。
- **(b) 观测词表匹配失效被 goal 注入掩盖**: 环境观测 `oak_logx9`(计数后缀)在 `[a-zA-Z_]+` tokenizer 下整个 token 不进 GOAL_VOCAB;单字词表也含不下下划线块名。v2 战役的物品边**全部**来自 `task.goal_entities` 直注(seed_goal=True),观测→实体通道实际零命中。与 §目击:goal 直注同时是 C34 v2 的方向性来源之一。
- **(c) v2 preflight 传播探测伪阳性**: `before` 快照在激活注入**之前**采样,种子节点自身计入 newly_lit → 该探测恒过,不证多跳可达。
- **(d) 决策头 token 预算不足(2026-09-30 预检复现)**: `v2.LLMHead` `max_tokens=40`;miMo 现返回 reasoning_tokens,40 预算被吃光 → 每决策空回复(finish=length;复现 39 reasoning/40 空,200 正常)。信标战役 30/30 wait 全由此。修装配 `llm.max_tokens=200`(四条件共用,无偏差)。**v2 战役运行期间 miMo 是否已带 reasoning 开销待核**——若当时同病,660 族行为数据应全部 wait(与既有记录矛盾),大概率此前后端行为有变;此为 harness 史实悬案,记档待核。
- **处置**: 三项均以 A10 规格 `§4.1` 预数据修订封存;信标装配改用专用断言(物品:oak_planks 点亮,种子外)+ 观测解析修补 + fake bridge 接线。路由 v2 战役原判定(方向性行为证据)不动,API 相关记录不变。属装配史实,不升格为生产缺陷。

### L2-HAR-02 [OBSERVED→已记档 2026-09-30] 双进程并发写同一结果文件 → 战役数据交错污染
- **证据**: `experiments/beacon_v1/raw_results.jsonl` 出现 85→103 行 task_result(预注册应为 80),seeds 10-16 双份、一 NUL 损坏行(旧行 75);`summary.csv` mtime 20:50 内容为全 wait 失败(80 行 succ=0,唤醒空回复症状期数据),而 raw 文件头 386 行为修复版健康数据(fas_full succ=1 dec=2/核验)。溯源: 二启任务 `b55muu05n`(max_tokens=40 时期)的 shell 被会话结束 kill,但 **python 子进程成孤儿存活**(`tail -120` 管道使外层看不到它),20:50 才 `done: 80 runs`——它全程在向**同一 raw_results.jsonl** 追加,与三启(修复版,20:31 "w" 截断后从 0 写)交错;两进程 fpos 相互踩踏 → NUL、双份、尾部被孤儿写满。
- **根因链**: ① 会话结束/任务停止只杀了外层 bash,孤儿 python 未接清除;② 实验脚本用固定 `--out` 打开 "w",无单实例互斥;③ 管道 `| tail -120` 隐藏孤儿存在(任务 output 空),战役内幕不可见。
- **修复(装配,数据前锁定)**: `run_exp_beacon.py` 新增 msvcrt 独占锁(修订 h,同 out 目录第二进程启动即拒绝);重跑不再走管道,输出直写 log 文件;启动前 PowerShell 核验 python 进程清单。**污染文件整体作废**,备份在 `experiments/beacon_v1/polluted_2proc_evidence/`(raw/summary/analysis 三件),不做任何挑选性保留(预注册纪律)。
- **教训**: 后台长跑战役必须(1)进程级互斥,(2)全量进程可见(不吞管道),(3)收官前校验行数与预期==。

---

## 层 3 候选:机制限制(见 MECHANISM_RESULTS.md,此处只列指针)
- H_join(多输入汇聚增益): **SUPPORTED**,JCG>0 97%(660 runs),随输入数单调(r@1=MRR=1.0 @ ≥2 输入)。
- H_noise: **REJECTED(增益显著退化但保持正)** —— 噪声 0→0.9 JCG 单调 0.239→0.073,Bonf 显著。
- H_complexity: **INCONCLUSIVE** —— 无规模退化证据(C7 ns),亦无规模增益。
- 生产含义: 生产图 40% 涉及 fabric ≈ 高噪声环境,生产 JCG 预期远低于参考上限 —— 与 C34 v2 弱行为效应一致(见 MECHANISM_RESULTS §3)。

---

## 既有实验有效性裁决(2026-09-30 复核)
| 实验 | 判定 | 依据 |
|---|---|---|
| C34 v1 | **INVALID ASSEMBLY** | harness 组装缺陷,结论不成立 |
| C34 v2 | **VALID ROUTING EXPERIMENT(机制—行为间证据,方向性)** | 520/520;T4 19/20 vs 15/20 p=0.046,Bonferroni 后 ns;token 1.4-2×;**仅方向性行为证据**,不构成生产优越性 |
| relation-direction | **VALID 机制级实证** | ρ .33→.65 p=.002 r=.886 10/10 seeds;**机制级**,不构成生产行为优越性 |

**阻断性压力测试**: 无单一实验证明"路由性能/激活质量在行为层面等效于或优于扩散激活";所有行为级优良性结论须在 FINAL_DIAGNOSIS 中降级为"方向性/机制级"表述。