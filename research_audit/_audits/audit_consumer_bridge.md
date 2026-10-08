# Layer 1 Audit — Consumer Bridge（阶段 1.6）

审计日期:2026-09-30
审计对象:E:\Project\Fascinator(8459 行 app.py,cognitive architecture,FAS)
审计范围:知识图谱激活 → 行为/回答链条上 knowlege activated → serialized → consumed 的真实分界
性质:只读审计,未改任何生产代码

---

## 1. 真实执行链图(逐段标注数据通路)

### 主链:/api/nlp(用户输入 → 回答)

```
user input
  → app.py:2981 process_nlp()  [端点;cc.set_busy(True);llm_budget.turn_reset()]
  → app.py:3092-3095  mc["get_state"]()   (失败静默置 None)
  → app.py:3103      resolve_minecraft_intent()    [MC 反射快路径:动作概念→ActionManager]
  → app.py:3210-3293  mc_session 会话状态机(连接/要端口/取消)
  → app.py:3298-3318  cognitive_complexity.estimate_complexity()  [L1/L2 分档]
  → app.py:3329/3335  nlp.process_fast() / nlp.process()   [LLM 解析 → nodes/edges]
  → app.py:3343-3345  _fuzzy_match_node()   [名称归并到图内真实节点 ID]
  → app.py:3405      inject_utterance()     [言语行为→episodic 事件节点,种子并入]
  → app.py:3441-3470  FAISS 语义召回(emb_mgr.search, top_k=8, sim≥0.3) → 注种
  → app.py:3644-3653  engine.apply_inter_round_decay(0.4, floor=0.5)   [旧噪声清除]
  → app.py:3686      engine.activate_from_inputs(parsed_nodes, parsed_edges, similarity_map)
  → app.py:3701-3729  engine.diffuse_round(max_steps≤6)   [激活扩散]
  → app.py:3746      engine.get_topk(k=10)   [日志:扩散后Top10]
  → app.py:4069-4157 记忆抽取: nlp.extract_assertion_graph(text, topk_check, focus_events)
                    → buffer.add_experience → _auto_consolidate_curiosity_knowledge(写回 KG)
  → app.py:4239-4295  dialogue_decide()   [行为竞争:respond/silence/explore_ask/search]
  → app.py:4316      analyze_cognitive_demand() → mode(0~3)  [预算门输入]
  → app.py:4437      build_cognitive_context()   [七分区]
  → app.py:4560/4649  compile_for_language(_cog_full, path=L1/L2)
  → app.py:4601      engine.get_topk(k=20) ← 回答区上下文的唯一知识源
  → app.py:4605-4613  过滤: is_cognitive_visible && !坐标( && !locks.hides_node("topk")
  → app.py:4617-4620  边过滤: 两端都在 topk_nodes 内
  → app.py:4666      nlp.answer_question(text, topk_nodes, knowledge_edges,
                       context_type, cognitive_context=_cog_ctx)   [★ mode/attention_context 未传]
  → nlp_processor.py:975-1008  Top-K 序列化: 前 15 节点 "id(激活度,标签,+能力描述,+value)"
                                + 前 20 边 "src-[rel]->dst(边权,激活)"
  → nlp_processor.py:1289-1297  user_prompt = cog_str + 【相关记忆】+【用户原始输入】→ LLM
  → app.py:4760-4809  Say-Do 一致性钩子:回答承诺动作→action_manager.propose(真执行)
  → app.py:5000-5018  expression_feedback.record_expression()   [表达事件节点入图]
  → app.py:4987      chat_log.record()   [回答入对话日志]
  → app.py:4743-4754  timeline ACTION event("say") + causal_learner.record_action()
  → app.py:4849/4126  _save()  [30s 节流异步落盘;force 即时]
graph write-back
```

回答区知识的唯一来源是 **engine.get_topk(k=20) 按 activation 降序**(并列时点亮序、入图序,见 diffusion_engine.py:545-551);序列化格式见 nlp_processor.py:975-1008(节点 id + activation + label + self_capability 描述 + extra_attrs.value;边三元组 + weight + activation)。**没有**按相似度/recency 的二次排序;attention_context(带 episodic 时间序的另一把尺子)在生成路径上不通(见 L1-CON-1)。

### 动作消费链(激活的 procedural → 执行 → 回执 → 结果落图)

```
激活过阈的 procedural 节点(activation≥theta_action, label=procedural,
  非 action_concept/expression, 未被 action 锁隐藏)
  → diffusion_engine.py:577 _refresh_action_queue()   [排序 (-activation, 入图序)]
  → 两条消费支路:
     A) 遗留队列: /api/actions/execute (app.py:5812) 手工端点(前端按钮)
        → engine.execute_action() → Action 注册表 → 回退 exec(node.execution) 沙箱
        → 结果 inject_emotion(程序执行/失败) → _save          [仅手工可达]
     B) 新架构(生产主路): action_manager.propose() (action_system.py:239)
        来自: app.py:3137(mc 反射) / 4003(用户意图) / 4793(Say-Do) /
              autonomy.py:1036(自主) / continuous_cognition.py:1042(交流意图)
        → ActionManager.tick() (autonomy.py:933 / continuous_cognition.py:384)
        → embodiment.execute() → _settle (action_system.py:534)
        → 回执链: _emit_result_event(timeline)
                 + _write_action_memory(行动_* 事件节点 + 涉及边 + 时间顺序链
                    + 实施→概念边 + 能力溯源)           [action_system.py:891]
                 + _write_event_frame(C21,默认 enabled=False) [action_system.py:1033]
                 + reward/disposition/trait 慢学习 + on_settled(autonomy 回写)
                 + cc.note_delivery(表达回执)
```

### 实验 harness:routing v2(scripts/run_exp_routing_v2.py)

```
task.setup_phase → SandboxWorld
  → FASContext.ingest()   [真 kg.add_edge 共现边;activate_from_inputs 注种]
  → step_dynamics()       [decay_step + diffuse_step]
  → focus(k=8)            [真 get_topk]   ← 与生产同一排序
  → demand_block()        [真 analyze_cognitive_demand]
  → serialize_context()   [run_exp_routing_v2.py:457: selected_nodes(id+act) /
                           selected_edges / demand top-3 / gap top-2 / routing JSON]
  → BASE_PROMPT.format(context=ctx_text) → LLM.decide(reply) → ex.execute()
demand 注解**确实**进了实验 prompt(serialize_context 渲染 demand/gap/routing 行);
生产 /api/nlp 同一条 demand 数据**不进** prompt(仅预算门 + 调试视图)——见 L1-CON-2。
```

---

## 2. 五级判定:图谱知识到底有没有进入行为链

| 级别 | 结论 | 证据 |
|---|---|---|
| 1. knowledge exists | ✅ 成立 | 启动建图(情绪/好奇/能力/焦点基础设施 app.py:464-495)、`_auto_consolidate_curiosity_knowledge`(app.py:2168)、`inject_utterance`、`_write_action_memory`(行动留痕 episodic)、叙事抽取、"搜索记录"节点(4690) |
| 2. knowledge activated | ✅ 成立 | app.py:3686 `activate_from_inputs` + 3701-3729 `diffuse_round`;FAISS 召回注种(3441-3470);"扩散后Top10"日志(3748);激活排序经 `get_topk`(diffusion_engine.py:545) |
| 3. knowledge serialized | ✅ 部分成立 | TopK≤15 节点 + ≤20 边进【相关记忆】块(nlp_processor.py:977-1008,按 activation 排序,序列化 id/激活度/标签/能力描述/value)。**但**:attention_context 七分区与 demand/gap/routing 三层只进 `_cog_full`/调试视图,**生产 prompt 里不渲染**(见 L1-CON-1/2);social 路径二次注入相同 TopK(1256-1261) |
| 4. knowledge consumed | ✅ 成立 | 【相关记忆】块直接进入 user_prompt 且 LLM 被指示"根据认知分析及你的记忆回应"(1280-1287);行为竞争(dialogue_decide)与 MC 意图解析都由图激活/候选驱动 |
| 5. knowledge changes behavior | ✅ 成立(有强证据) | (a) **回答开闸**:"LLM回答"节点 activation≥θ_action(0.5) 或存在动作证据/respond 裁决才生成回答,否则整轮无回答(app.py:4589-4599);(b) silence/explore_ask/explore_search 由扩散竞争裁决(4239-4295);(c) 回答承诺动作→ActionManager 真执行(Say-Do,4760-4809);(d) 实验证据 C20e 5/5 行为翻转(行动侧事件框架,默认关)。**限制**:生产路径上"模式/需求"对回答的影响只体现为预算门与温度(demand 注解不进 prompt);回答内容质量仍高度依赖图密度——稀疏图时 TopK 落旧激活残余(见 L1-CON-4) |

**总判定:链是通的**(activated→serialized→consumed→behavior 全程有代码路径与日志证据),但存在三处"编译了却没消费"的死末端(attention 块、demand 注解、buffer 晋升)、一处静默边丢失、若干无声降级。

---

## 3. 发现条目

### L1-CON-1 — attention_context 每轮构建但生产 prompt 永不渲染(死代码)
```
ID: L1-CON-1
Component: nlp_processor.py:967-1035(answer_question 的 mode/attention_context 分支);
          app.py:4666(调用点未传参); cognition_modes.py:237-291(每轮构建)
Category: L1-ORDERING(兼具 L1-TELEMETRY 成本)
Severity: minor
Expected / Observed:
  期望: mode + attention_context(active_core/recent_episodic/emotion/goal)进「认知状态」块
  实际: app.py:4666 只传 text/topk_nodes/knowledge_edges/context_type/cognitive_context,
        answer_question:1021 的 `if mode or attention_context:` 恒假——「认知状态」块
        生产从不出现;全仓 33 处 answer_question 调用无一传 mode/attention_context
        (nlp_processor.py 自身消费侧也无 cognitive_context["attention_context"] 读取)。
        attention_context 每轮仍全量构建(一次 get_topk + 全边邻域扫描),只进前端调试视图。
Root cause: 2026-09-20 cognitive_context 重构把 attention 搬进 _cog_full["state.attention"]
           并编译进 _cog_ctx["attention_context"],但消费端参数化契约未接线。
Proposed fix: app.py:4666 增加 mode=_mode, attention_context=_cog_ctx.get("attention_context");
           或 answer_question 从 cognitive_context 字典回退读取该键。
Risk: 低(行为上无回归风险;不改则 recent_episodic 时间序信息永不进 LLM 上下文)
```

### L1-CON-2 — demand/gap/routing 三层分析编译进上下文但生产 prompt 不渲染
```
ID: L1-CON-2
Component: cognitive_context.py:262-270、424-426(编译键);
          nlp_processor.py:1010-1261(cog_str 无 demand 渲染);
          app.py:4968-4983(仅调试视图 + 日志)
Category: L1-ORDERING
Severity: major(实验-生产口径断裂的直接证据)
Expected / Observed:
  期望: demand 注解(实验 harness serialize_context 渲染 "demand (top): k=v / routing: json")
        在生产回答 prompt 中同样可见——cognitive_demand/analyze_cognitive_demand 是论文
        声称的消费环节
  实际: production LLM 只见 mode 的**间接**作用:预算门(can_call, app.py:4659)+ 温度微调
        (4363-4369)+ nudge(4347);demand/gap/resource 三层数值永远不出现在 LLM 输入中。
        routing v2 harness(scripts/run_exp_routing_v2.py:457-497)与生产行为不一致——
        实验结论外推到生产的因果链在此断裂。
Root cause: demand→prompt 的渲染只在实验 harness 实现,生产编译侧(compile_for_language)
           只搬运键,消费侧(answer_question)从未读取
Proposed fix: 在 answer_question cog_str 增加【资源路由】块渲染 top-3 demand/top-2 gap/
           routing(mode 已有),复用 serialize_context 的现成格式
Risk: 中——改变生产回答生成语义,需与论文口径对齐后由用户裁决
```

### L1-CON-3 — add_edge 静默丢边(端点缺失返回 False 无日志),LLM 草稿边在漏斗处消失
```
ID: L1-CON-3
Component: graph_model.py:489-490(add_edge 端点缺失 return False);
          app.py:2232(_auto_consolidate_curiosity_knowledge 边循环不查返回值);
          app.py:5299(/api/memory/approve 边循环不查返回值)
Category: L1-SILENT-FAILURE
Severity: minor
Expected / Observed:
  期望: 写入失败可见(至少 warning),已存在节点优先确保端点在
  实际: LLM 抽取草稿中,src/dst 经 _fuzzy_match_node 归并后若一端图内不存在
        (如 dst="和朋友"),add_edge 静默返回 False;added["edges"] 不计、无日志——
        用户教给 FAS 的知识边无声丢失。同构问题在 replay 路径(app.py:5398)同理。
Root cause: add_edge 契约是"静默跳过"(历史为兼容),调用方未约定检查
Proposed fix: 调用侧检查返回值并 logger.warning(f"边被丢弃: {src}-[{rel}]->{dst}");
           或 add_edge 增加 log_skip 参数(批量建图场景仍安静)
Risk: 低;只影响数据完整性可观测性
```

### L1-CON-4 — NLP 全失败(LLM 双次都抛)后零种子继续跑整条认知链
```
ID: L1-CON-4
Component: nlp_processor.py:292-303(process 双失败返回空 parsed)、335-366(process_fast 失败返回默认);
          app.py:3686(activate_from_inputs 用空/默认种子)、4599-4668(回答区仍可能产出)
Category: L1-SILENT-FAILURE(带 L1-ORDERING 含义)
Severity: minor
Expected / Observed:
  期望: 解析失败时本轮认知链有"种子为空"守卫或显式降级语义
  实际: 双失败有 logger.exception(不静默),但 parsed 为空 → seeds=[]
        → activate_from_inputs 空转 → 扩散后 TopK = 上轮残留激活前沿(仅经 40% 衰减),
        LLM 回答仍照常生成,基于陈旧或无关的【相关记忆】。日志里无"种子为空"标记,
        该轮回答的"依据"与用户输入无图侧关联——行为链上最隐蔽的错位。
Root cause: process 的异常降级契约只保证"不炸",未定义"本轮认知语义"(空种子)
Proposed fix: app.py 在 activate_from_inputs 前检查种子清单为空且解析失败(_pfe 置标记)
           → 回答区显式标注"本轮无图证据"或走最小回应
Risk: 低-中;修复影响回答降级行为
```

### L1-CON-5 — LLM 回答失败占位文本进时间轴/对话日志被当作一次"她说的话"
```
ID: L1-CON-5
Component: nlp_processor.py:1300-1302(answer_question 内层 catch 返回占位串);
          app.py:4738-4740(外层 catch 同占位);
          app.py:4743-4754(占位串被 record 为 EVENT_ACTION "say");
          app.py:4987(chat_log.record(system_response=占位))
Category: L1-SILENT-FAILURE(带数据污染面)
Severity: minor
Expected / Observed:
  期望: 生成失败与真实回答在经历层可区分
  实际: "[回答生成失败: openai...]" 被当作 llm_answer 继续走:记入 timeline ACTION、
        causal_learner.record_action、对话日志、expression 事件节点——失败占位变成
        一条"她说的话"进入经历,后续反思/统计可读到这条假表达。
Root cause: 双层 catch 都以"返回字符串"作为失败契约,与成功路径共用同一收尾
Proposed fix: 占位串常量 + 收尾判断 llm_answer.startswith(PLACEHOLDER) 时跳过
           timeline/expression 写入(chat_log 保留,前端要显示真实原因)
Risk: 低
```

### L1-CON-6 — 循环内静默 pass 清单(/api/nlp 主请求路径)
```
ID: L1-CON-6
Component: app.py 主路径多处:3281-3282(register_invitation)、3506/4220(timeline reward append)、
          3776-3777(cc.note_pressure)、3923-3924(file_action JSON 解析失败置 {} 无日志)、
          4171-4172(chat_log().all())、4192-4193(buffer.annotate_expression)、
          4245-4246(时间差解析)、4314-4315(unknown_now 兜底)、4360-4361(mode nudge)、
          4368-4369(温度设置)、4417-4418/4828-4829(drive 快照)、4657-4658(budget_factor 兜底 1.0)、
          4838-4839(cycle_end 观测)、5142-5143/5154-5155(调试日志)
Category: L1-SILENT-FAILURE
Severity: minor(单点) / 汇总为发现
Expected / Observed:
  期望: 观测性辅助失败至少 debug 级可见——其中 4360/4368(网络调制→温度/模式)与
        4657(budget_factor)静默失败会让"mode 微调/温度/预算调制"悄然失效
  实际: 全部裸 `except Exception: pass`,无任何日志
Root cause: 逐点防御式编程,未统一"观测失败可忽略但须留痕"约定
Proposed fix: 至少改为 `logger.debug(..., exc_info=False)`;4360/4368/4657 升级 warning
Risk: 低
```

### L1-CON-7 — 记忆晋升候选无生产驱动(buffer 只在手工端点晋升)
```
ID: L1-CON-7
Component: app.py:4107-4109(get_promotion_candidates 结果只打日志);
          episodic_buffer.py:128-155(promote_threshold_access/importance/activation 齐备);
          app.py:5245(/api/memory/approve 手工)、5554(/api/buffer/promote 手工)
Category: L1-ORDERING(DESIGN)
Severity: major(经历层→长期记忆的唯一自动化通路是 curiosity/叙事/断言抽取;
                高频访问记忆永不晋升)
Expected / Observed:
  期望: access_count≥3 / importance≥0.7 / activation_count≥5 的经历自动入图
  实际: 阈值机制存在但无人驱动——上线后所有草稿滞留 buffer(inn-memory+json),
        仅 /api/memory/approve(有前端按钮,依赖人来点)与 /api/memory/replay 可晋升。
        dialogue_act 寒暄类(非 teaching)连抽取都不进 buffer(4071-4084,守卫设计)。
Root cause: 人工审批时代遗留:模块支持自动阈值晋升,但接管对话后没有把
           "promote candidates → _auto_consolidate 式落图"接入 CC 空闲循环
Proposed fix: continuous_cognition 空闲刻度检查 get_promotion_candidates 并走
           _auto_consolidate_curiosity_knowledge 语义(注意:该函数名带 curiosity
           但逻辑不要求 curiosity——改名或拆通用落图函数)
Risk: 中——自动落图增加图噪声面,需先跑 dry-run 观察晋升率
```

### L1-CON-8 — 行动链路闭合性核验(结论:闭合,OK;附遗留队列孤立)
```
ID: L1-CON-8
Component: action_system.py:239(propose)/355-432(tick)/534-612(_settle)/891-1028(_write_action_memory);
          autonomy.py:868-1057; continuous_cognition.py:207-384;
          diffusion_engine.py:1428-1575(遗留 execute_action)/577-647(_refresh_action_queue)
Category: OK(带一条 L1-DESIGN 附注)
Severity: info
Expected / Observed:
  实际: 激活 enqueue → 执行 → 回执 → 落图**闭合并带双保险**:
        _write_action_memory 每次都写(行动_* 节点 + 涉及/时间顺序/实施边 + 能力溯源),
        on_settled 回写自主层 recency,cc.note_delivery 对表达回执,失败不走暂态豁免
        (C21/C20g 语义);时间顺序链头不落盘(重启断链可接受)。
        附注:遗留 `_refresh_action_queue → engine.action_queue → /api/actions/execute`
        (diffusion_engine.py:577-647, app.py:5812-5890)只有前端手工按钮可达
        (index.html:1641);CC/autonomy 均走 ActionManager。Action 注册表 get_action_func
        退化为 wasd.py 少量条目,其余靠 node.execution exec 沙箱——遗留路径无自动消费方,
        建议标注 deprecated 或删除端点。
Root cause: 架构演进未清理旧入口
Proposed fix: 端点保留但标记 deprecated;文档注明队列语义
Risk: 无(行为已由 ActionManager 全接管)
```

### L1-CON-9 — C21 事件框架与因果台账:存在、默认关闭、生产无 sweep
```
ID: L1-CON-9
Component: action_system.py:1033-1150(_write_event_frame)/190-202(evf 兜底默认);
          config.py:1934-1946(experience_eventframe.enabled=False);
          experience.py:360-576(CausalLearner)/567-576(sweep);
          app.py:4743-4754、action_system.py:686/695(timeline 事件 → causal)
Category: OK(设计已定) + L1-TELEMETRY 附注
Severity: info
Expected / Observed:
  实际: C21 事件框架进图机制**完整存在**(签名级节点 行动经验:{atype}({sig})、
        极性:涉及 w=±、恒正:结果 叶子、force_weight 极性翻转、点火两件套
        activation_cap=3.0+register_activation_source),生产默认 enabled=False
        (config.py:1935)——与记忆"C21 enabled 默认关"一致。开启即生效(成功/失败
        结算后落图并点火,失败不豁免暂态)。
        ledger(CausalLearner 聚合)默认开启:窗口关闭靠 `_on_timeline_event` 的
        wall-clock 检查(experience.py:544)与 record_action 的 deadline 检查(506),
        **生产任何路径都不调 sweep()**——对真实时钟正确(事件驱动自然关窗),
        但对"动作后系统立即停机/长时间无事件"的窗口,聚合永远不记账(下次事件
        到达即补记,不丢但延迟)。沙箱加速时钟下必须显式 flush_causal 的事实
        (memory: sandbox-causal-window-sweep)与此一致。
Root cause: 无(设计如此);sweep 缺席是时钟语义差异,不是缺陷
Proposed fix: 可选:CC 空闲 tick 调 causal.sweep() 兜底(天然幂等,刷新需全量快照成本)
Risk: 无(若补 sweep:低,幂等)
```

### L1-CON-10 — 经验回写完整性核验(OK)
```
ID: L1-CON-10
Component: app.py:2168-2250(_auto_consolidate,返回计数被 4120-4126 检查);
          app.py:2069-2165(_wire_event_structure,各步存在性守卫);
          app.py:5000-5018(expression_feedback.record_expression);
          app.py:4987(chat_log.record); app.py:4743-4754(timeline ACTION)
Category: OK
Severity: info
Expected / Observed:
  实际: 回答结束后写入:(1) 表达事件节点入图(每轮,含沉默);(2) 用户断言类输入
        抽取→buffer→(curiosity 或断言路径)自动沉淀进 KG,带名称归并/事件框架收尾/
        焦点指针;(3) 答案本身**故意不落节点**(P0-6 语义:回答是痕迹不是状态,
        归宿=chat_log+timeline);(4) _save(force=True) 在新增后同步调用。
        返回值检查: _auto_added 非空才记 log+save; _wire_event_structure 全程
        存在性判断。缺**失败回执**检查: _save 异步(30s 节流),崩溃窗口的可接受性
        已在守卫设计内(带缩小守卫,memory: graph-shrink-guard)。
Root cause: 无
Proposed fix: 无
Risk: 无
```

---

## 4. 结论摘要

1. **主问题不是"知识进不了行为链",而是"编译了却消费不了"**:attention 七分区(cognition_modes.attention_context)与 demand/gap/routing 三层(cognitive_demand)是论文"认知上下文"的实义部分,生产 prompt 两者都不渲染(L1-CON-1/2)——实验 harness 与生产在此分叉,论文外推要打问号。
2. **五级判定 = 第 5 级(改变行为),证据集中在 θ_action 开闸、行为竞争、Say-Do、动作闭环**;但"回答里知识用得多准"取决于图密度与 TopK 排序(纯 activation 轴),输入解析失败时会落到陈旧激活上(L1-CON-4)。
3. **静默降级面小但存在**:18 处裸 pass(观测类)+ 2 处占位串污染经历(L1-CON-5)+ 边静默丢(L1-CON-3)。
4. **C21 事件框架生产默认关、安装完整**;ledger 默认开、关窗机制在真实时钟下自然生效(L1-CON-9)。
5. **动作链路**是本次审计中闭合度最高的一段:enqueue→propose→tick→settle→双轨落图→回执,无断点。

### 建议后续动作(按优先级)
- P0: L1-CON-2 决策(试验口径 vs 生产口径,与论文 §15/附录C 对账)
- P1: L1-CON-1 接线(两行代码,恢复 recent_episodic/mode 进 prompt)
- P1: L1-CON-4 空种子守卫(防陈旧激活回答)
- P2: L1-CON-3/5/6 日志与占位区分
- P2: L1-CON-7 buffer 自动晋升(需 dry-run 观察噪声)