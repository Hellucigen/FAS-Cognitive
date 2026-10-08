# Layer 1 审计（阶段 1.3）：demand / gap / routing / LLM 决策接口层

- 日期：2026-09-30
- 范围：continuous_cognition.py（CC 循环/认知脉冲/CI 节点/主动交流）、curiosity_engine.py（好奇/缺口检测）、dialogue_decision.py（行为竞争）、cognitive_demand.py（需求→缺口→路由）、cognitive_context.py（七分区上下文）、cognition_modes.py（六模式/预算）、conversation_gap_detector.py（离线工具）；交叉核验 diffusion_engine.py、graph_model.py、disposition_store.py、action_system.py、app.py 的回合编排
- 方法：静态追链（全量读 7 个目标模块 + 交叉调用点），无运行注入
- 结论摘要：**没有发现 critical 级错误**。发现 2 个 major：① `/api/nlp` 空输入早退路径不解除 CC 的 busy 暂停（CC 循环粘滞停摆，直到下一次成功请求才恢复）；② 三处 `kg._lock → engine.get_topk` 锁序反转，与引擎内大量 `engine._lock → kg.*` 的标准序构成 AB-BA 死锁风险（概率低、代价高）。fallback 语义整体是"诚实降级"（_express / generate_question / dialogue 兜底都有日志），主要缺口是**静默吞异常的观测性**（行动概念竞技场整体静默禁用、脉冲签名探测失败静默变空）与**两处无界增长**（_express_count_hour / _reactivated_recent）。CI 生命周期自洽（非扩散的 extra_attrs 平行强度 + 自管衰减），空输入路径除 busy 泄漏外均有合理出口。

---

## 一、重点清单核查结果总表（§1–§7）

| 核查点 | 结论 | 关联发现 |
|---|---|---|
| §1 dict/list 裸下标、schema mismatch、可变默认参数 | 大部分有守卫（`or [None]`、`or {}`、`isinstance` 分支）；真正风险点 4 处：`_express` 空 basis、`_d_action` 对 current_action 的隐式 dict 契约、BudgetManager 权重死代码、CI 强度平行状态 | DGR-07/09/10/11/14 |
| §2 空/单字/无实体输入 | 有合理出口：空文本 400（app.py:3054，但泄漏 busy → DGR-01）；0–2 字走"极短"降抑+沉默倾向（dialogue_decision.py:393-401）；无实体→无信号→无候选→graph_only；空认知场→`basis_pool` 空→只衰减。唯二例外：DGR-01、DGR-09 | DGR-01/09/17 |
| §3 first-step ordering / 清 anchor 边界 | tick 末（continuous_cognition.py:314-317）与回合末（app.py:4854）双清幂等，正确；`_pulse_seq` 出生拍豁免自身一致；**首脉冲必然发生且 novelty=1.0**（启动即注入一次"新经历"压力）；成功/异常两条回合尾路径只有成功路径清锚 | DGR-03/13/19 |
| §4 stale context | `_sources` 在异常路径（app.py:5208-5232）不清空→上一轮来源身份残留进下一轮（自限性）；`_express_count_hour` 只 append 不修剪（无界）；`_reactivated_recent` 只写不删（有界于节点数但单向增长） | DGR-03/06 |
| §5 fallback routing | 全部为**诚实降级**：LLM 失败→warning+返回 False/规则兜底；预算不足→返回 False 走图谱。无一处"异常当成功"。可改进的是静默点（见 §6） | DGR-18/10/04/05 |
| §6 exception swallowing | 7 文件共 48 处 try/except；可接受 40+ 处（观测日志/可选模块/类型转换），**2 处不可接受**（dialogue_decision.py:321 整个行动概念竞技场静默消失、continuous_cognition.py:398 脉冲签名探测静默空表），另 8 处需上移日志等级 | 表格见 §四 |
| §7 CI/意图生命周期 | 生成：focus≥form_threshold（调制）→kind argmax→`CI_` 节点+Self/基于 边；竞争：basis 重叠≥40% reinforce、同类满 2 抑制；消亡：×0.9/脉冲（出生拍豁免）→<0.10 discard→MAX_CI_NODES=20 按 created 裁剪。**强度存 extra_attrs，不经扩散**（Node.activation 恒 0）；表达候选排序=自定义强度−抑制分，并列取遍历序 | DGR-12/14 |

---

## 二、发现明细

### L1-DGR-01 — /api/nlp 空输入早退路径不解除 CC busy（循环粘滞停摆）
- **Component**: app.py:3054-3065（空文本 400 早退）↔ app.py:2990（set_busy(True)）/ 4859（成功路径解除）/ 5227（异常路径解除）
- **Category**: L1-ORDERING
- **Severity**: major
- **Expected**: busy 暂停必须与请求生命周期配对——handler 内所有出口（成功/异常/各早退）都应在离开前 `set_busy(False)`；CC 循环只在"请求处理中"让位。
- **Observed（证据）**: `process_nlp` 唯三出口中，空文本路径 `return jsonify({"error": "输入文本不能为空"}), 400`（app.py:3063-3065）既不经过 4859 也不经过 5227。`cc.set_busy` 是 Event 置位（continuous_cognition.py:145 `self._busy = threading.Event()`；186-191 set_busy），一次置 True 未配对即**永久 busy**：tick 扩散/脉冲/表达决策/激素衰减全部停摆（tick_once 的 214 行 `if not self._busy.is_set()` 整块跳过），直到下一次 /api/nlp 成功走到 4859。若客户端连续发空串（前端校验缺失或脚本轮询），CC 可停摆任意久，且图上衰减停摆（激活滞留）。
- **Root cause**: busy 用"置位"而非"引用计数/范围清理"表达，早退路径漏掉配对；异常路径在 2026-09-22 收尾修复里补过（5227），空输入路径是同一类遗漏的另一出口。
- **Proposed fix（最小修复）**: 空输入分支 return 前加 `cc.set_busy(False)`；更稳妥：在 `try` 入口用 `try/finally` 包裹整个 handler 体，`finally: cc.set_busy(False)`（连同 anchors 清理一起）。
- **Risk**: 无日志噪音的静默停摆；只有"主动消息消失/图不衰减"这类间接症状；修复风险极低。

### L1-DGR-02 — 锁序反转：kg._lock → engine._lock（AB-BA 死锁风险）
- **Component**: dialogue_decision.py:369-373（dialogue_decide 话题共振块）、dialogue_decision.py:188-189（inhibit_topics）、cognition_modes.py:260-263（attention_context）
- **Category**: L1-ORDERING
- **Severity**: major
- **Expected**: 全仓锁序一致。diffusion_engine.py:436-437 明示约定"锁序：engine → kg（与 activate_from_inputs 一致，防死锁环）"；引擎在持 engine._lock 期间反复进入 kg 锁函数（get_topk 在 536-561 内调 `kg.get_out_edges/get_in_edges`，diffuse_step 在 964-1005 内调同样的函数；graph_model.py:611-612 get_out_edges 内 `with self._lock` 取 kg 锁）。
- **Observed（证据）**: 请求线程在上述三处**先持 kg._lock 再调 `engine.get_topk`（取 engine._lock）**；同时段 CC 线程（tick 非忙期、以及 busy 期仍运行的 autonomy/tick 尾部 358-386 → action_manager.tick）可能在引擎锁内等待 kg 锁（diffuse_step/get_topk 持 engine._lock 后逐节点 `kg.get_out_edges`）。两线程交叉即互等：A 持 kg 等 engine，B 持 engine 等 kg。两类锁均为无超时 RLock，一旦碰撞：请求线程挂死（Flask worker 阻塞）、CC 线程静默停摆，无自愈。
- **Root cause**: 三处调用点在重构时未对齐引擎的锁序约定；"只在 kg 锁内读 topk"缺少代码评审拦截。
- **Proposed fix（最小修复）**: 三处改为先取 topk 再入 kg 块（dialogue_decision.py:369-373 改 `topk, _ = engine.get_topk(k=10)` 在前，`with kg._lock:` 只包 INFRA 集合构造；inhibit_topics/attention_context 同理）；或在 `engine.get_topk` 增加"调用方不得持 kg 锁"的断言。
- **Risk**: 低概率高代价；修复后无行为变化。

### L1-DGR-03 — 回合异常路径不清 _sources（激活来源身份跨轮残留）
- **Component**: app.py:5208-5232（异常出口只 set_busy(False)）↔ continuous_cognition.py:314-317（契约注释"来源由 app 回合末的 clear_anchors 清空"）
- **Category**: L1-ORDERING（stale context）
- **Severity**: minor
- **Expected**: 契约承诺回合末清空本轮激活来源；异常回合同样应清（异常不一定发生在来源登记之前——curiosity 注入 3613、disposition 种子 3667-3673 都可能已登记）。
- **Observed（证据）**: 成功路径 4854 `engine.clear_anchors()`；异常路径（5208-5232）与 DGR-01 的空输入路径都没调。例外回合里 `engine._sources`（diffusion_engine.py:117/1235）保留上一轮登记项 → 下一轮扩散中这些节点仍被 `_sources` 判定为"本轮外来输入"，`diffuse_step` 发射免扣（1106-1117），即上一轮失败回合注入的能量以零资源成本残留进下一轮。自限：下一轮正常结束会清空，影响幅度小（一轮）。
- **Root cause**: 收尾修复只补了 busy 配对，锚点清理没进异常出口。
- **Proposed fix（最小修复）**: 异常出口在 `set_busy(False)` 旁并列 `engine.clear_anchors()`（try/except 包一层，与 4853-4856 同式）。
- **Risk**: 低；一轮内的激活分布微扰。

### L1-DGR-04 — 行动概念竞技场整体静默禁用（collect_action_candidates 失败无任何痕迹）
- **Component**: dialogue_decision.py:304-322（1b 行动空间泛化块，321-322 `except Exception: graph_cands = {}`）
- **Category**: L1-SILENT-FAILURE
- **Severity**: minor
- **Expected**: 候选收集是行为竞争的主输入之一（2026-09-20 行动重构后与 LEGACY 词表并列）；失败时至少 warning，因为竞争结果会"少了整个维度"——不是掉了边角料。
- **Observed（证据）**: `action_space.collect_action_candidates(...)` 抛任何异常 → `graph_cands = {}` → candidates 只剩 LEGACY 10 键，竞争照常出胜者、日志照常打——**从输出无法区分"没有行动候选"与"收集失败"**。同类：306-314 三处 modulation 读取失败静默回默认（可接受，降低的是参数精度）。
- **Root cause**: 防御性兜底写成了整块吞；该块内部还有多处 try（313）保底，外层 catch 本意是"收集可选"，但把失败与"没候选"合并了。
- **Proposed fix（最小修复）**: 外层 except 加 `logger.warning("[DialogueDecision] 行动候选收集失败，本轮退化为 LEGACY 竞争: %r", e)`；candidates 输出侧保留一个 `"graph_arena_error": str(e)[:80]` 进 factors。
- **Risk**: 修完只是可观测性变好，零行为变化。

### L1-DGR-05 — 认知场签名探测失败静默变空表（脉冲在"空场"语义上继续）
- **Component**: continuous_cognition.py:396-400（`_field_signature` 的 `except Exception: return []`）
- **Category**: L1-SILENT-FAILURE
- **Severity**: minor
- **Expected**: get_topk 失败时脉冲门应跳过本次判定或降频；至少留一条 debug/warning 说明"本拍无签名"。
- **Observed（证据）**: `_field_signature` 抛错 → 返回 [] → `_pulse_gate` 里 `_sig_change(prev, [])` 得到"所有旧节点全部消失"的大变化量（430-437：sum |0 − v|）→ **触发脉冲** → `_pulse` 在 basis_pool 空时只执行 `_decay_intentions()`（500-502）→ 意图集体掉一口衰减。即：探测失败被放大成"场剧烈变化 + 意图衰减一轮"，全程零日志。同样静默的还有 1197-1199（`_field_temperature` 失败→0.0→判定"过冷"→触发再点火，方向放大错）、1220-1221（reactivation focus_ids 静默空）。
- **Root cause**: 探测类函数的"失败=默认值"约定，默认值恰好落在"变化量最大/温度最低"的恶劣端。
- **Proposed fix（最小修复）**: `_field_signature` 失败返回 `None`（而非 []），`_pulse_gate` 对 None 直接 return 跳过本拍，并 `logger.debug`；`_field_temperature` 失败返回 None 并跳过 `_reactivate_gate` 判定。
- **Risk**: 低；避免一次故障被放大成错误决策链。

### L1-DGR-06 — `_express_count_hour` / `_reactivated_recent` 无界增长（小内存泄漏）
- **Component**: continuous_cognition.py:820（hour_expr 过滤后不写回）↔ 962（每次表达无条件 append）；140/1262/1288（`_reactivated_recent` 只写不删）
- **Category**: L1-BUG
- **Severity**: minor
- **Expected**: 两个时钟型容器应随窗口滚动裁剪。
- **Observed（证据）**: `_evaluate_expression` 里 `hour_expr = [t for t in self._express_count_hour if now - t < 3600]` 是局部变量，从未赋值回 `self._express_count_hour`；`_express` 每表达一次 append 一条。表达有 inhibition_cooldown_s=60s 的调度 floor → 上限约 60 条/小时 → 1.4k/天，长期运行线性增长。`_reactivated_recent` 同理：每次再点火写 `node → now`，永不清理（约等于节点数上界，随图增长也单向涨）。
- **Root cause**: 修剪逻辑只做了局部筛选，没管存储侧。
- **Proposed fix（最小修复）**: append 处顺带 `self._express_count_hour[:] = [t for t in self._express_count_hour if now - t < 3600]`；reactivated 键在 `_reactivate_field` 末尾按 `window` 裁剪一次。
- **Risk**: 极低；纯内存卫生。

### L1-DGR-07 — 概念信号注入漏 `register_activation_source`（与情绪路径不一致，违背模块自述）
- **Component**: curiosity_engine.py:478-480（`_inject` 只 mark_active）↔ 502-504（情绪路径 mark_active + `register_activation_source(..., "internal_drive")`）↔ continuous_cognition.py:900-905（note_pressure 两件套）
- **Category**: L1-SCHEMA（行为一致性）
- **Severity**: minor
- **Expected**: 探索信号是本轮外来认知输入（模块自身注释：novelty/relevance 调制后注入），应与其他外来注入同等对待：登记 `_sources` 后扩散"发射免扣"（diffusion_engine.py:1221-1235 注释明确列举"好奇信号（internal_drive）"为登记方）。
- **Observed（证据）**: 情绪路径（502-504）与反思压力（continuous_cognition.py:902-903）都做了两件套；`_inject` 概念/关系信号（480）只 mark_active。未登记的注入节点在扩散中"发射即扣除"（1106-1117：`_remaining = activation − emitted`）——本轮新注入的信息被当作旧图资源从自身扣，弱信号更容易在到达 CuriosityDrive 前湮灭。与 2016-09-13"能量守恒"修复的语义（发射即转移+刺激源锚点）相左。
- **Root cause**: 重构时两处路径分别演化，登记动作只补在了一处。
- **Proposed fix（最小修复）**: `_inject` 内 mark_active 后加 `engine.register_activation_source([node.id], "internal_drive")`（与 503 同式）。
- **Risk**: 信号传播强度回升（这不是降级而是对齐设计意图）；需跑好奇链路回归用例确认无饱和。

### L1-DGR-08 — pending_inquiry 闸门把情绪信号一并挡掉
- **Component**: curiosity_engine.py:405-407（`if is_curiosity_active(kg): return {"triggered": False, "reason": "pending_inquiry"}`）
- **Category**: L1-ORDERING / DESIGN
- **Severity**: minor
- **Expected**: "一次一个"约束针对的是**概念/关系探索**（注释与候选取 `approach` 语义）；情绪确认（需要确认 节点，共情通道）是另一类信号，不受探索队列约束。
- **Observed（证据）**: 早退发生在情绪扫描（416-423）之前——等待回答 存续期间（最长 inquiry_timeout_s≈900s）用户任何情绪表达都不会提升"需要确认"节点，情绪→社交节点→行为竞争 的图通路缺一路信号。虽然情绪主要载体（emotion 节点扩散）仍工作，但这是检测器自身职责内的漏讯。
- **Root cause**: 门条件写在整个检测函数最前，比设计意图（探索类信号的闸门）范围更大。
- **Proposed fix（最小修复）**: 把早退移到情绪检测之后（情绪分支不查 pending 门，只对概念/关系注入做 `is_curiosity_active` 判定）。
- **Risk**: 行为差异小；改动局部。

### L1-DGR-09 — `_express` 在基础节点全部丢失时仍调 LLM（凭空想法）
- **Component**: continuous_cognition.py:931-945（ctx 由 basis 拼装）↔ 954-958（失败/空文本回退但无"无素材"闸门）
- **Category**: L1-SCHEMA（empty context）
- **Severity**: minor
- **Expected**: 表达素材（basis 有效节点）是表达的前置条件；basis 全丢的 CI 不应进入语言生成（会产出"图外凭空"的内容，违反 CI 可解释性：basis/激活路径/得分）。
- **Observed（证据）**: 正常路径下 CI 形成即带 basis；但图上其他清理（记忆清理、graph prune、_prune_history 外的节点删除——历史上有 574 垃圾节点清理先例）可删除 basis 节点。此时 `ctx` 为空串，prompt 的【想法来源】块为空，LLM 在零信息下自由发挥；`if not text` 只挡了 LLM 输出空文，不挡空输入。`_evaluate_expression` 的 rich 检查（841-850）只挑 episodic/emotion 型 basis 判断"有没有可说的"，不检查"basis 节点是否还存在"。
- **Root cause**: 素材完整性检查缺失：表达决策看的是 extra_attrs 里的历史 basis，不核对图上存续。
- **Proposed fix（最小修复）**: `_evaluate_expression` 的 rich 判定改为只统计 `self.kg.get_node(b) is not None` 的 basis；`_express` 内 ctx 为空时 return False + `logger.warning`。
- **Risk**: 低概率；修复即加一道检查。

### L1-DGR-10 — BudgetManager 模式权重预算是死代码（MODE5 与 MODE1 同价）
- **Component**: cognition_modes.py:206-209（`w = MODE_COST.get(mode, 1); if len(recent) + w > per_minute: pass`）
- **Category**: L1-TELEMETRY（设计声称 vs 实现）
- **Severity**: minor
- **Expected**: 文档/注释声称"按 mode 的权重"收费（MODE_COST: deep=8, language=1）；max_tokens_per_turn 也应参与 can_call。
- **Observed（证据）**: `can_call` 的分钟限额只按调用次数（197-205 `len(recent) >= per_minute`）；207-209 计算了 `w` 并比较后 `pass`——**权重从不影响任何判定**。`register` 也只计数不记权（215-217）；`_tokens_turn` 累计后没有任何消费方读取（stats() 只展示），`max_tokens_turn` 限额形同虚设。即：MODE5_DEEP 每一跳与 MODE1 等价消耗一次配额，与"模式成本权重"的文档语义不符。
- **Root cause**: 预算重构时把权重语义表（MODE_COST）留下但落库逻辑没接。
- **Proposed fix（最小修复）**: 二选一——(a) 计数改成权值累计（`weighted = sum(MODE_COST.get(m,1) for m in ...)`）并重写 per_minute 比较；(b) 删掉 206-209 死分支，docstring 改注"预算=调用次数制（未按权重）"。若近期无预算压力，选 (b) 最稳。
- **Risk**: 选 (a) 会改变实际预算行为（deep 模式更快触顶），需回归自发言语节流。

### L1-DGR-11 — analyze_cognitive_demand 唯一调用点无防护，provider 隐式依赖 dict 型契约
- **Component**: app.py:4316（调用点无 try/except）↔ cognitive_demand.py:195（`s['current_action'].get('action_type')`）
- **Category**: L1-SCHEMA
- **Severity**: minor
- **Expected**: 需求分析是回合中间件，失败不应炸穿整轮（其上游 dialogue_decide 有兜底 4276-4280，下游 LLM 生成有预算门）；provider 应容忍调用方传值类型漂移。
- **Observed（证据）**: `_d_action` 在 `current_action` 处直调 `.get()`——现状 `action_manager.current` 恒为 dict/None（action_system.py:479 `self.current = action`），契约靠"调用方内部实现"而非签名保证；若 action 态未来改成对象（ActionNode）即 AttributeError → analyze_cognitive_demand 抛 → app.py:4316 无兜底 → 进 5208 外层 500（日志有，但整轮挂）。另：`_d_emotion` 的 kg=None 走 try 兜底（146-153），`cognition_modes.demand_score` 的 kg=None 靠 `_legacy_score` 的 except 静默归零（324-325）——防御风格不一致。
- **Root cause**: provider 签名用 `s` 大字典传递，无 schema 校验；调用点假设"永远不会失败"。
- **Proposed fix（最小修复）**: `(s.get("current_action") or {}).get("action_type")`；并在 app.py:4316 外包一层 `try/except Exception → _demand_analysis = {…全零…} + logger.warning`（与 4276 同模式）。
- **Risk**: 现状类型正确，纯健壮性；修复零行为变化。

### L1-DGR-12 — 表达候选并列取节点遍历序（无确定 tie-break）
- **Component**: continuous_cognition.py:836-866（`_evaluate_expression` 的 `val > best_val` 只取严格大于）
- **Category**: L1-ORDERING
- **Severity**: minor
- **Expected**: 竞争胜者应有确定性优先级（对比 engine.get_topk 的显式 tie-break：(activation, 点亮序, −入图序)，diffusion_engine.py:531-534）。
- **Observed（证据）**: 多个 READY CI 同分时选第一个（kg.nodes 遍历序 = 插入序）；插入序依赖形成时序，语义任意。两个 CI 同分且同 kind 时表达哪个不可解释（可解释性规范第五条要求"决策原因"）。
- **Root cause**: 竞争选择器没写 tie-break。
- **Proposed fix（最小修复）**: `(val, activation, -created)` 三元组比较或排序取首。
- **Risk**: 极低；表达选择的稳定性/可解释性提升。

### L1-DGR-13 — 首拍必脉冲且 novelty=1.0（开机被计为一次"新经历"）
- **Component**: continuous_cognition.py:430-437（`_sig_change` 无 prev 返回 5.0）、402-412（gate 首拍必然放行）、515-518（overlap 对空集=0 → novelty=1.0）、588-590（novel_exp ≥0.5 → note_pressure）
- **Category**: L1-ORDERING（first-step）
- **Severity**: info
- **Expected**: "新到什么程度"判据应只度量真实焦点变化；启动（无先前焦点）不应被当作一次新经历注入 0.05 反思压力。
- **Observed（证据）**: `_last_focus_sig` 初始化为 `[]`（136），与"上一拍焦点为空集"不可区分 → 首脉冲 overlap=0、novelty=1.0 ≥ novel_min_novelty=0.5 → 注入"novel_experience"压力。此后冷场（overdue 25s）脉冲时同一焦点集 novelty=0，不再注入——即只有开机那一次。
- **Root cause**: 用 `[]` 表示"从未脉冲"，与"恰好空焦点"共享同一值。
- **Proposed fix（最小修复）**: 初始化改为 `None` 哨兵；`_pulse` 里 `prev_focus = self._last_focus_sig` 为 None 时跳过 novelty/pressure 判定。
- **Risk**: 极低；消除一次启动伪事件。

### L1-DGR-14 — CI 强度是 extra_attrs 平行值，不经扩散（回答审计问题 7 的事实陈述）
- **Component**: continuous_cognition.py:666-697（创建 Node 不带 activation &rarr; Node.activation=0.0 恒）、762-799（_decay_intentions 自管 ×0.9/脉冲）、836-866（表达排序读 extra_attrs）
- **Category**: DESIGN
- **Severity**: info
- **Expected / Observed**: CI 是"图节点"但**不是扩散参与节点**：Node.activation（graph_model.py:205 独立 float，默认 0.0）从未被设置、从未 mark_active、从未进活跃前沿/get_topk；强度生命周期（形成/增强/竞争抑制/衰减/丢弃）由模块自管代码实现。这与"激活是图状态"的一贯语义（感知/情绪/驱力都走扩散）不同——CI 是例外。模块内部一致（basis 排除、表达决策、state() 都读 extra_attrs），无功能错误；但任何第三方读图侧（分析工具/前端 topk/后续 diff 机制）看到的是激活 0 的 CI，无法感知"有一个 READY 意图在等待表达"。
- **Root cause**: CI 初版设计时未接扩散场；decay_per_pulse 语义模仿扩散衰减但独立实现。
- **Proposed fix（建议）**: 不做修复；在模块头注释补一句"CI 强度存 extra_attrs（不经扩散场），意图的图内可见性=节点存在性"；若未来要让 CI 参与扩散竞争，需同时改 mark_active 语义与 basis 排除逻辑（有回退风险，勿轻动）。
- **Risk**: 维持现状=零风险；贸然接通扩散会改变表达节律。

### L1-DGR-15 — generate_question 的 emotion 分支是永久死代码
- **Component**: curiosity_engine.py:833-842（emotion+unknown_properties 分支）、898-899（fallback 同型）、804（unknown_properties 读取）
- **Category**: DESIGN（dead code）
- **Severity**: info
- **Expected**: 分支应可达，或显式标注为何不可达。
- **Observed（证据）**: `unknown_properties` 在全部生产代码中零填充（grep 唯一赋值在 scripts/verify_p0_2.py:114、verify_p0_all.py:149，均为常量 []）。且 trigger_type="emotion" 的候选根本不会进入 explore_ask（build_exploration_candidates 要求 concept 类信号，600-604）——emotion 分支永远不执行，落到 else 兜底（846-854）。detect 的 emotion 触发路径（496-504）只服务于"需要确认"节点提升，与提问无关。
- **Root cause**: 旧状态机（P0-2 删除流水线节点）留下的分支未随裁剪删除。
- **Proposed fix**: 删除两条 emotion 分支，或在 else 前加注释"emotion 触发不产生探索候选（见 detect_cognitive_signals），此分支仅为历史签名保留"。
- **Risk**: 零。

### L1-DGR-16 — settle_inquiry 谓词兜底"宁可错收"：长期副作用是兴趣被误衰减
- **Component**: curiosity_engine.py:963-975（谓词兜底 `(是|就是|是指|指的是|叫|叫做|…)` 命中即视为回答）↔ 1000-1001（resolved → interest ×0.4）
- **Category**: DESIGN（已文档化的取舍，长期代价未评估）
- **Severity**: info
- **Expected**: 注释自认"宁可错当回答，不可错杀教学（有抽取守卫兜底）"；但未考虑的长期项：**误收也走 resolved 衰减**——用户任意一句含"是/叫"的寒暄（"是的是的"）都会把尚未回答的 inquiry 结清并 `interest ×0.4`、`resolved_count+1`。resolved_count 喂给 satiation（474：`min(1, resolved_count×0.4)`）与候选评分（617）→ 同一目标未来探索信号被系统性压低——与"兴趣被回答后衰减而非清零"的理念（注释 17-22）在"误收"分支上矛盾。
- **Observed（证据）**: 971-975 谓词正则无目标名校验；978-984 仅"完全无命中"才走 ignored（×0.85 轻衰减）——误收走的是比 ignored 更重的衰减路径。
- **Root cause**: 判据把"可能回答"与"已回答"合并为一个布尔，衰减剂量未区分置信度。
- **Proposed fix（建议）**: 谓词兜底命中但目标名未出现的路径：`note_interest(..., "ignored")`（轻衰减）或不衰减直接结清 inquiry，resolved_count 只给目标名出现过的结清。
- **Risk**: 中等——改动会影响"发育期脚手架"的受益场景（用户解释时不提目标名）；建议先观察生产统计再改。

### L1-DGR-17 — 空输入/空上下文路径盘点（除 DGR-01/09 外均有合理出口）
- **Component**: 全链路
- **Category**: OK
- **Severity**: info
- **Expected / Observed（证据）**:
  - 空文本：app.py:3054 400 拒绝，不进认知（busy 泄漏即 DGR-01）。
  - 0–2 字短输入：dialogue_decision.py:393-401 极短分支（表达类 −0.20、acknowledge −0.05、silence +0.20）→ 倾向沉默/确认，不会无素材硬说。
  - 无实体/无未知：detect_cognitive_signals 不注入（430-448 空列表）→ 无候选；demand 各维趋 0 → mode=graph_only（cognitive_demand.py:306-314）；compile_for_language L2 各键空安全（cognitive_context.py:396-437 全部 `or {}` / `or []`）。
  - 空认知场：basis_pool 空 → 只衰减意图（continuous_cognition.py:500-502），合法。
  - 空 decision：cognitive_context.py:185-186 `dd = decision or {}`，decision_sec 默认 should_respond=False？——注意：`should_respond`（205-206）由 `dec_val in (...)` 判定，`dec_val = dd.get("decision")` 为 None → False → should_speak=False → demand language gap=0 → graph_only。即"无决策→不调 LLM"是安全方向，正确。
- **Root cause**: —（无）
- **Proposed fix**: 无。
- **Risk**: 无。

### L1-DGR-18 — LLM 不可用/超时/解析失败的 fallback 全链盘点（均为诚实降级）
- **Component**: continuous_cognition.py:946-958、curiosity_engine.py:857-882、dialogue_decision.py（零 LLM）、cognition_modes.py:180-210、app.py:4276-4280、4496-4524
- **Category**: OK
- **Severity**: info
- **Expected / Observed（证据）**（逐条）:
  - `_express` 异常：logger.warning（954-956）+ return False，CI 保持 ready，下拍可再试；status 不落 expressed、不写 chat_log、不入 _queue——失败不被当成功。空文本响应（957-958）：return False 但**无日志**（建议补 debug）。
  - `generate_question` 异常：logger.error + `_fallback_question` 规则兜底（880-882、885-902），兜底再失败 → None → app 4523-4524 明确日志"降级为不问"。诚实。
  - 预算不足：`_express`（925-929）、`_free_think`（1308-1312）、`_maybe_offline_reflect`（1337-1341）、app explore_ask 预算门（4499-4501）全部显式日志并走图谱/保留语义。MODE_MANDATORY 全 False（cognition_modes.py:52-59）与预算语义一致。
  - dialogue 决策兜底：app.py:4276-4280 `logger.warning` + 默认 respond（desire 0.6）——"决策失败默认回应"方向正确；注意其 factors/constraints 缺失 → 语言层以 normal 措辞（无 no_question 等约束），可接受。
  - 预算对象缺位（llm_budget=None）各调用点都有 getattr/None 防护（925、1308、1337、4499）。
- **Root cause**: —（无）
- **Proposed fix**: 仅建议 `_express` 空文本路径补一条 debug 日志。
- **Risk**: 无。

### L1-DGR-19 — tick 末/回合末双清 anchors 与 _fired_round 清空（一致，无冲突）
- **Component**: continuous_cognition.py:314-317 ↔ app.py:4854；diffusion_engine.py:1202/1323/1602（_fired_round 三处清空）、1214-1217（clear_anchors 幂等）
- **Category**: OK
- **Severity**: info
- **Expected / Observed**: `clear_anchors` 只清 `_sources`（无状态累加，幂等）；非忙期 tick 每拍末清一次、回合末 app 再清一次，语义互补（tick 清 tick 内来源，回合清回合内来源）；busy 期 tick 不清（314 行在非忙分支内）而回合末补清——闭环成立。`_fired_round` 在 diffuse 回合/decay 处清空（1323/1602），不存在跨轮残留。除 DGR-03（异常路径）外，stale context 无其他来源。
- **Root cause**: —（无）
- **Proposed fix**: 无。
- **Risk**: 无。

### L1-DGR-20 — B5 出生拍豁免逻辑自洽（同一脉冲内形成+衰减竞态已处理）
- **Component**: continuous_cognition.py:146-149（_pulse_seq 语义注释）、484-485、773-784（born_exempt 判定）
- **Category**: OK
- **Severity**: info
- **Expected / Observed**: `_decay_intentions` 在 `_pulse` 内紧跟 `_merge_or_form` 调用（591-593），`_pulse_seq` 自增在两者之前（485）——同拍形成的 communication CI 因 `born_pulse == _pulse_seq` 免吃本拍衰减（777-778），非沟通类不豁免（但非沟通类形成时 status 已是 READY，无"出生即濒死"问题）。reinit 路径（reinforce）不更新 born_pulse——旧 CI 正常衰减，正确。用 `_pulse_seq` 而不用 `_tick`（busy 期 tick 冻结）的注释与实现一致。
- **Root cause**: —（无）
- **Proposed fix**: 无。
- **Risk**: 无。

### L1-DGR-21 — conversation_gap_detector 离线标注正确，无生产风险
- **Component**: conversation_gap_detector.py:3-5（未接线标注）、24（环形上限）、73-118
- **Category**: OK
- **Severity**: info
- **Expected / Observed**: 模块自注"未接线的离线手动工具"，生产链路零调用（grep app.py 无引用）；`record_turn` 的 None 输入会在 `analyze_session` 的 `t['user'][:60]`（83）TypeError——但只在离线脚本复现，不触及生产。_CONV_LOG_CAP=200 已防泄漏。
- **Root cause**: —（无）
- **Proposed fix**: 无（使用方自行注意传非 None）。
- **Risk**: 无。

---

## 三、exception swallowing 全景清单（§6，逐条判定）

7 文件全部 48 处 `except`。判定口径：**可接受**=有日志/可选模块/类型转换/防御性默认值且失败不影响主语义；**需上移**=静默且失败会改变本层输出语义。

| 文件:行 | 语句 | 判定 |
|---|---|---|
| continuous_cognition.py:197-201 | 循环体异常 → warning+fas_log.exception | 可接受（观测完备） |
| continuous_cognition.py:233-234 | experiment_mode.shield 错误 → pass | 可接受（配置探测） |
| continuous_cognition.py:259-260 | modulator_subgraph ImportError → pass | 可接受（可选模块） |
| continuous_cognition.py:263-264 | internal_state 控制块 → debug | 可接受 |
| continuous_cognition.py:287-288 | drive_evaluator refresh_modulation 失败 → **pass 无日志** | 需上移 debug（调制参数静默不刷新） |
| continuous_cognition.py:290-291 | 参数调制 → debug | 可接受 |
| continuous_cognition.py:307-308 | 检测器轮询 → warning | 可接受 |
| continuous_cognition.py:349-350 | 时段迁移事件 → debug | 可接受 |
| continuous_cognition.py:351-352 | 时钟更新 → debug | 可接受 |
| continuous_cognition.py:370-372 | 自主行动 → warning | 可接受 |
| continuous_cognition.py:385-386 | Action 推进 → debug | 可接受 |
| continuous_cognition.py:398-399 | **_field_signature → return []（无日志，失败被放大成"场大变+意图衰减"）** | 需上移 → DGR-05 |
| continuous_cognition.py:450-451 | _cognitive_snapshot → pass 全零快照 | 需上移 debug（drive 读失败静默零值喂 kind 亲和） |
| continuous_cognition.py:567-568 | _world_signals → pass | 需上移 debug（世界信号整体丢弃） |
| continuous_cognition.py:609-610 | _mod_th → 回退默认 | 可接受（调制缺位=静态默认是本层设计） |
| continuous_cognition.py:834-835 | set_context_value(recent_expression) → pass | 需上移 debug（表达抑制调制少一路输入） |
| continuous_cognition.py:904-905 | note_pressure 的 mark_active/source 注册 → pass | 需上移 debug（压力节点不出前沿=无衰减） |
| continuous_cognition.py:954-956 | _express LLM 失败 → warning+False | 可接受（DGR-18） |
| continuous_cognition.py:973-974 | chat_log.record → warning | 可接受 |
| continuous_cognition.py:976-977 | _save_fn → warning | 可接受 |
| continuous_cognition.py:999-1000 | buffer.add_expression → pass | 需上移 debug（Reflection 闭环丢一次表达事件） |
| continuous_cognition.py:1007-1008 | _propose_communicate → debug | 可接受 |
| continuous_cognition.py:1043-1044 | am.propose 异常 → 当作被拒（reason 入 delivery） | 可接受（有状态可查） |
| continuous_cognition.py:1124-1125 | 投递再试 → debug | 可接受 |
| continuous_cognition.py:1162-1163 | 经验重放 → debug | 可接受 |
| continuous_cognition.py:1197-1199 | _field_temperature → return 0.0（放大成"过冷"→再点火） | 需上移 debug → DGR-05 |
| continuous_cognition.py:1220-1221 | reactivation focus_ids → pass | 需上移 debug（再点火少一路耦合） |
| continuous_cognition.py:1250-1251 | last_access 解析 ValueError → 视为久远 | 可接受（类型转换） |
| continuous_cognition.py:1318-1319 | 自由思考 → warning | 可接受 |
| continuous_cognition.py:1367-1368 | 离线反思 → warning | 可接受 |
| continuous_cognition.py:1386-1387 | experiment_mode.shield → pass | 可接受 |
| continuous_cognition.py:1394-1395 | 投递再试跳拍 → debug | 可接受 |
| curiosity_engine.py:285-286 | fas_log 好奇事件 → pass | 可接受（观测层） |
| curiosity_engine.py:470-471 | relevance topk → pass（回 0.2） | 需上移 debug（相关性因子静默归零） |
| curiosity_engine.py:663-664 | n_neighbors → 0（判稀疏） | 需上移 debug（可搜索性判定静默偏差） |
| curiosity_engine.py:778-779 | mark_active(等待回答) → pass | 需上移 debug（与收尾修复同一洞） |
| curiosity_engine.py:880-882 | generate_question LLM 失败 → error+规则兜底 | 可接受（DGR-18） |
| dialogue_decision.py:99-101 | 抑制锁查询 → warning 按无锁处理 | 可接受 |
| dialogue_decision.py:109-111 | inhibit_sec 类型转换 → 默认 | 可接受（类型转换） |
| dialogue_decision.py:131-132 | 抑制锁写入 → warning 浮点通道保留 | 可接受 |
| dialogue_decision.py:143-145 | expires_at 解析 → False | 可接受 |
| dialogue_decision.py:271-272 | horm_n 解析 → 1.0 | 可接受（类型转换） |
| dialogue_decision.py:282-283 | explore_margin/rate 调制 → pass 回常量 | 需上移 debug |
| dialogue_decision.py:313-314 | topk_scale/afford_gain → pass 回 1.0 | 可接受（等级微调） |
| dialogue_decision.py:321-322 | **collect_action_candidates 失败 → graph_cands={} 静默禁用整个行动概念竞技场** | 不可接受 → DGR-04 |
| dialogue_decision.py:565-566 | action_space.touch → pass | 需上移 debug（候选生命周期记录丢失） |
| cognitive_demand.py:66-69 | _node_activation → 0.0 | 可接受（防御性） |
| cognitive_demand.py:152-153 | 情绪激活扫描 → 0.0 | 可接受（防御性） |
| cognitive_demand.py:324-325 | _legacy_score → 全零（静默吞真实错误） | 可接受（legacy 通道，已标注兼容层） |
| cognitive_context.py:54-56 | pv strength 解析 → 跳过 strength | 可接受（类型转换） |
| cognition_modes.py:190-191 | shield 探测 → pass | 可接受 |
| cognition_modes.py:194-196 | budget_factor 解析 → 1.0 | 可接受 |
| cognition_modes.py:255-256 | attention_context 宽度调制 → 回默认 | 可接受（缺位=旧行为） |

**汇总**：需上移 ≥debug 的静默点 12 处（多数为"失败回默认值但值会改变下游语义"）；不可接受 2 处（DGR-04、DGR-05）。没有 `except: pass` 掩盖核心写图/落盘/结算错误的案例（那些位置都已带 warning）。

---

## 四、问题统计

| Severity | L1-BUG | L1-SCHEMA | L1-ORDERING | L1-SILENT-FAILURE | L1-TELEMETRY | DESIGN | OK |
|---|---|---|---|---|---|---|---|
| critical | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| major | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| minor | 1 | 4 | 3 | 2 | 1 | 0 | 0 |
| info | 0 | 0 | 0 | 0 | 0 | 4 | 4 |

主要建议修复优先级：DGR-01（busy 泄漏）> DGR-02（锁序）> DGR-03（锚点残留）> DGR-04/05（观测性）> 其余 minor。