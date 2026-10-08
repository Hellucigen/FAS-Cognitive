# PATCH_LOG.md — 修复台账(附验证)
**基线**: git 4685726;全部修复可回滚(单文件 diff,无结构性重构)。

| ID | 文件 | 变更 | 最小性 | 验证 |
|---|---|---|---|---|
| L1-TH-01 | tests/conftest.py | 一期:8 项显式 collect_ignore;二期(2026-09-30):AST 全量扫描证实 114 个测试文件中 93 个为脚本式回归(模块顶层裸 exit,import 即 SystemExit),显式清单不可维护(第 18 个文件 test_world_events.py 击穿复查);另 1 个(test_modulation_targets.py)test_* 函数带装配参数、靠 __main__ 注入,收集必 fixture-not-found。改 AST 自动分类:①顶层裸 exit(非函数内/非 __main__ 守卫)②test_* 签名含非内置 fixture 参数,两者任一即跳过收集 | 仅测试基建 | **82 passed 0 errors**(12 真 pytest 文件);脚本式回归仍独立运行 |
| L1-DFU-01 | diffusion_engine.py | `_emit` 正/反向纯负集:分母 `total_abs_neg`,混合集不变;同步 `_edge_is_blocked` 过滤 | 6 行判断 | test_diffusion_inhibition 3/3 |
| L1-DGR-01 | app.py | 空输入早退:收周期+clear_anchors+set_busy(False) | 13 行出口清理 | 语法+全套回归 |
| L1-DGR-02 | dialogue_decision.py ×2, cognition_modes.py ×1, continuous_cognition.py ×1 | get_topk 移出 kg._lock(锁序 engine→kg);reactivate_field 第 4 处同款 | 只挪锁范围,零逻辑变动 | 脚本回归全过 |
| L1-DGR-03 | app.py | 异常出口+clear_anchors | 4 行 | 同上 |
| L1-DGR-04 | dialogue_decision.py | collect_action_candidates catch 加 warning | 3 行 | 脚本回归 |
| L1-DGR-05 | continuous_cognition.py | `_field_signature`/`_field_temperature` 失败→None,两门跳过拍;focus_ids 失败 warning | 行为不变(仅失败路径) | 脚本回归 |
| L1-CON-3 | app.py ×2 | add_edge 返回值检查,失败 warning+计 skipped | 每处 5 行 | 语法+回归 |
| L1-CON-4 | app.py | 零种子留痕(不改回答行为) | 6 行 | 语法 |
| L1-CON-5 | app.py | 占位串不进 timeline/expression;Say-Do 不被占位驱动;chat_log 保留 | 判定+2 处条件 | 语法+回归 |
| **A1-A** | config.py + nlp_processor.py | **CON-1/CON-2 生产接线(2026-09-30,用户裁决后执行)**:①新键 `nlp_render_cognitive_context`(默认 True,False=精确回滚);②answer_question 门控从"只认形参"改为"形参→cognitive_context 键回退"(生产调用只传 cognitive_context,形参恒空=本块永不渲染的根因);③attention 渲染 4 键扩 8 键(补 self/relevant_semantic/unknowns,active_core 带激活度);④新增【认知资源路由】块渲染 demand top3/gap top2/资源定档(只给维度名,不暴露数值,机制不外显) | 1 键+1 块,零调用方行为变化(经查全仓无按旧 4 键格式传形参的调用方) | `scripts/verify_a1_wiring.py` **10/10 PASS**(L2 四分区+三层结构、生产形态调用 8 键渲染、不外显原则、门=False 精确回滚断言);test_cognitive_context.py 脚本回归 PASS;pytest tests/ **82 passed** |
| **A5-A** | graph_model.py + continuous_cognition.py | **PERS-01 三重守卫 + PIN-11 留痕(2026-09-30)**:①load() JSONDecodeError → `_load_failed=True` 标记;②save() 若 `_load_failed` 直接拒绝(防空图覆写损坏源);③save() shrink-guard except 路径同步升级:损坏旧文件 → 备份 `.bak.unreadable_<ts>` + 拒绝保存(原先会静默放行);④add_edge 端点缺失 → `logger.warning("[图谱] add_edge 被丢弃(端点缺失)…")`;⑤continuous_cognition.py 意图 hub 边失败 warning | 守卫/log 各 ≤5 行 | `tests/test_pers01_guard.py` **8/8 PASS**(守卫 1–3 主/对照路径 + PIN-11 失败/正常路径,零生产图触碰) |
| **A6-A** | scripts/run_all_regression.py | **全仓回归执行器(2026-09-30)**:双通道(真 pytest 文件→pytest;脚本式/装配式→python 直跑,exit 0=PASS);conftest AST 分类器预分类(不依赖 pytest exit 5——实测 pytest 对命令行显式文件不应用 collect_ignore);main-guard 盲区以 exit 5 fallback 补上(4 文件);real_* 默认排除,--with-real 强制;`--parallel/--only/--timeout` | 纯测试基建,零生产触碰 | 全量 **105/105 PASS**(93 脚本 + 12 pytest,墙钟 ~39s,6 并行) |
| **A7-A** | app.py(≈33 处) | **裸 except 批量升级(2026-09-30)**:静默失效点逐处 `logger.warning`(命名 `as _X`,统一格式);保留 4 处干净默认返回(187/683/706/1506-1626——首启/断连期合法默认,非静默失败) | 仅日志,零行为变化 | py_compile + 10 文件定向 sweep 全 PASS |
| **A4-A** | episodic_buffer.py + app.py + tests/test_a4_access_observation.py | **晋升 dry-run 观测接入(2026-09-30)**:A4 验证结论=access/activation 计数死(episodic_buffer.py mark_accessed 全仓零调用 → 排序/阈值退化仅 importance;faiss 命中不触碰经历)。接入:①新增 `find_by_node`(只读检索,按图节点 id 找经历);②FAISS 语义命中循环后 → `mark_accessed`(每轮每经历去重 +1,`[A4-Obs]` 日志);③process_nlp 候选点增强日志:access≥3 驱动候选明细。**不启用任何自动晋升**:晋升仍仅手动 `/api/buffer/promote` 与 `_auto_consolidate_curiosity_knowledge`(importance 路径,已存在)触发;候选≠晋升锁死 | 检索+计数+日志,零自动晋升路径新增 | `tests/test_a4_access_observation.py` **12/12 PASS**(find_by_node 5 项、access 达标/排序/安全边界 5 项、遗忘保留 2 项);test_promote_trigger.py PASS(晋升链经不受扰);全仓回归见 REGRESSION |

**未改(记录在案,非遗漏)**:
- L1-CON-1/2(attention/demand 渲染)—— **已修复(2026-09-30,A1-A)**:见上表。原记录在此保留历史脉络:论文 §method 如实描述 C34 v2 D 条件装配"三层认知需求/缺口/路由分析"在 harness 内渲染,生产对话链路不渲染;属实验口径 vs 生产口径断裂,非虚报。
- L1-CON-6(18 处裸 pass)—— **已修复(2026-09-30,A7-A)**:见上表(约 33 处含此前逐点加过 warning 的位置)。
- L1-CON-7(晋升无驱动)—— **已接入观测(2026-09-30,A4-A)**:见上表。残余:①`activation_count` 计数仍零接入(扩散激活→经历计数的第二期观测,本项只接入 faiss 语义访问路径);②自动晋升 driver 未启用,等观测窗口数据(高频访问经历激活轨迹 vs 手动审批分布)收敛后再定。
- L1-PIN-01/11、L1-PERS-01 —— **已修复(2026-09-30,A5-A)**:见上表。PERS-01 三重守卫 + PIN-11 add_edge 端点缺失留痕落地;感知侧 16 项其余留痕(除主漏斗 CON-3 已覆盖外)详见 `_audits/audit_perception_ingestion.md`,残留项登记在案。

**回归**: 见 REGRESSION_RESULTS.md(全套 pytest + 脚本回归)。
**机制面独立实验**: 见 MECHANISM_RESULTS.md + mechanism_falsification/。