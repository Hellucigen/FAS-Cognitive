# Layer 1 审计：持久化链条（experience → write-back → serialization → restart → reload → activation → reuse）

- 日期：2026-09-30
- 范围：experience.py（时间轴/因果层）、episodic_buffer.py、experience_replay.py、json_store.py、graph_model.py（序列化/加载）、knowledge_pack_manager.py、app.py（加载点/保存器/退出钩子）、continuous_cognition.py（再点火/重放门）、action_system.py（行动留痕/C21 事件框架）、config.py（experience_eventframe）
- 数据证据：data/runtime_graph.json（2026-09-28 14:09 快照，3648 节点 / 9088 边）、data/experience_timeline.json（2026-09-29 00:00，4000 事件，环形缓冲已到 max_raw 上限）、data/experience_replay_state.json（2026-09-28 23:52）
- 方法：静态追链 + **生产数据实测**（load→save→load 往返、app 启动 merge+overlay 全量模拟、时间线↔图对应逐来源统计）
- 结论摘要：**序列化层往返无损**（3648↔3648 节点、9088↔9088 边、50 边+50 节点字段抽样 0 差异、全量 (类别,权重) 多重集等价）；**重启恢复结构完整性 ≈100%**（pack 极小：37 节点/52 边，俄罗斯 runtime 内容 3611 节点全量重回，9088/9088 三元组全部在位）；**持久化知识→激活→消费是真实满链**（CC 每 tick 跑再点火门 + 重放门，注入遵循 mark_active + 源锚点不变量，重放历史实测 3336 批/2760 条/746 条 Hebbian 强化全落盘）。确认问题集中在：**损坏文件引导路径会静默覆盖生产图导致永久丢失**（缩小守卫在旧文件不可解析时失效）、**overlay 对 pack 族节点的运行时增量（extra_attrs/边权重）重启静默回退**（当前生产影响仅 3 条 pack 边，潜伏风险）、**时间线损坏后首写即覆盖无备份**、**观察者/点火异常静默**。

---

## 一、实测数字（本审计实测，非推断）

### 1.1 load→save→load 往返（graph_model 序列化层）

脚本：临时实测（只读生产文件，保存副本写入 /tmp/rt_roundtrip.json）

| 指标 | 数值 |
|---|---|
| load#1 节点 / 边 | 3648 / 9088 |
| save → /tmp 后 load#2 | 3648 / 9088（+0 / +0） |
| 随机 50 条边字段对比（src/dst/relation/relation_category/weight/extra_attrs） | 0 差异 |
| 随机 50 个节点对比（id/label/graph_space/confidence/weight/extra_attrs） | 0 差异 |
| 全量 9088 条边 (relation_category, weight) 多重集 | 相等（428 个不同键，逐一相等） |
| 往返后悬空边 | 0 |
| 边 activation>0 计数 | 保存前 2527 = 往返后 2527（序列化保留；启动时另有清零设计） |
| label 分布（往返前=后） | episodic 2335 / semantic 1035 / infrastructure 132 / procedural 78 / disposition 46 / intention 20 / self 2 |
| graph_space 分布（往返前=后） | episodic 2252 / semantic 1062 / self 133 / cognitive 201 |
| 生产文件结构 | 顶层仅 nodes/edges；3648/9088 条；缺必需键 0；extra_attrs 非 dict 0；内存中重复三元组 0 |

### 1.2 app 启动 merge+overlay 全量模拟（app.py:287、308-332）

| 项目 | 数值 |
|---|---|
| 启用 pack（common/computer/minecraft/user_teaching_2026-07） | 37 节点 / 52 边（整个 pack 体系极小） |
| overlay 后 | 3648 节点 / 9088 边（+3611 运行时独占节点） |
| 运行时文件三元组与 overlay 后集合重叠 | 9088/9088（0 丢失） |
| pack 族节点 extra_attrs 分歧 | 0（生产数据上暂无丢失实例，机制见 L1-PERS-02） |
| pack 族边权重分歧（重启将还原 pack 值） | **3 条**：用户-Hellucigen 名字叫 1.5→1.0；Fascinator-Haru 名字叫 1.00194→1.0；挖矿-Minecraft 属于 0.900046→0.9 |
| 启动清零激活 | 12 个 pack 节点残差异，属设计（app.py:394-403 显式清零） |

### 1.3 时间线↔图对应（question 5，逐来源）

| 来源 | 事件数 | subject/target 可解析到图节点 | 占比 |
|---|---|---|---|
| minecraft | 2186 | 1753 | 80.2% |
| action_system | 1636 | 473 | 28.9% |
| nlp / prediction_baseline / curiosity_engine / dialogue / temporal_awareness / mc_session / expression_feedback / reward | 57/47/24/15/13/12/5/5 | 0 | 0% |
| **TOTAL** | **4000** | **2226** | **55.6%** |

ACTION 事件 ↔ 行动留痕节点（图里 行动_ 前缀共 1753 个）：amble 210↔258、explore_area 159↔502、gather_resource 148↔407、say 15↔**0**（说说类走沟通通道，不经 settle 留痕——"只写时间线不进图"路径实例）。

因果晋升落图：文件 promoted=30 键，图内 操作: 20 个节点、变化: 15 个，28/30 键在图内有对应 操作 节点（2 个缺失对应 09-28 垃圾节点清理被删的存量）。

C21 事件框架：图内 `行动经验:` 节点 **0 个**——config.py:1934-1946 `"experience_eventframe": {"enabled": False}`，生产默认关（设计态，见 L1-PERS-10）。

### 1.4 重放/再点火历史（experience_replay_state.json）

batches 3336 / replays 2760 / seeds 2800 / **lit 75094** / **hebb_up 746** / promoted 0 / new_edges 0 / reach_gain 0 / aborted 3；zero_streak=0；processed 500 条（到上限）。

---

## 二、确认问题（Confirmed）

### L1-PERS-01 — 损坏的 runtime_graph.json 会在下次启动被静默覆盖，缩小守卫失效 → 全图数据丢失
- **Component**: graph_model.py:768-783（load 的 JSONDecodeError 分支）；app.py:1797-1798（启动无条件 `pack_mgr.save_runtime_graph()`）；graph_model.py:727-740（缩小守卫）
- **Category**: L1-SILENT-FAILURE
- **Severity**: major
- **Expected**: 主图谱文件损坏时启动应：保留原文件（改名 .corrupt_<ts> 或拒绝保存），让快照/人工可恢复；绝不能把"空图合并结果"原子替换进损坏文件。
- **Observed**: `KnowledgeGraph.load` 对 JSONDecodeError 打印 warning 后返回**空实例**（776-783，注释自述"创建新图谱"）；app.py:308-329 overlay 因此加不进任何节点；app.py:1797-1798 启动即 `save_runtime_graph()`——损坏文件被 ~37 节点（pack+能力引导）原子替换。缩小守卫（727-740）仅在**旧文件可解析**时生效：先读旧文件数节点，`json.load` 抛异常 → `except Exception: logger.debug 跳过` → **不做备份直接覆盖**。即文件越是损坏，守卫越是静默失效。空文件/纯空白文件（JSONDecodeError 同类）走同一条路。load 只接 JSONDecodeError，UnicodeDecodeError/IsADirectoryError 等传播到 app.py:311 的 except → 同样在 1798 覆盖。全库唯一的"不写坏"防线在损坏场景下变成"必然写掉"。
- **Root cause**: load 失败语义 = "空实例"（启动继续的健壮性设计），与启动立即保存组合成**数据销毁路径**；缩小守卫的备份条件依赖旧文件可解析，解析失败时连备份都不做。
- **Proposed fix**: ① load 捕获 JSONDecodeError/UnicodeDecodeError 等读错时先 `os.rename(path, path + ".corrupt_" + ts)` 再返回空图（原文件保留可回滚）；② app.py overlay 段记录 `_runtime_load_degraded = True`，启动保存时若为 True 则跳过写盘并打 ERROR（数据文件等人工处理）；③ 缩小守卫的 except 分支改为 logger.warning 并跳过保存（fail-safe 向）。
- **Risk**: 磁盘扇区损坏/复制中断/手动编辑导致 JSON 坏 → 下次启动 3648 节点全灭且无声；虽有 graph_snapshots 12 份（≤30 分钟粒度）可回，但自动覆盖发生在任何人发现之前。

### L1-PERS-02 — 启动 overlay 对 pack 族节点/边的运行时增量静默回退（权重/extra_attrs 不合并）
- **Component**: app.py:311-329（运行时 overlay）；knowledge_pack_manager.py:192-232（merge_runtime_graph）
- **Category**: L1-BUG（当前生产影响小，潜伏）
- **Severity**: minor
- **Expected**: 重启后图上状态 = 上次保存时状态（持久化链第一原则）。pack 族节点在运行时被更新的字段（extra_attrs：因果 provenance、retired 标记、C21 极性; graph_space; confidence; last_access）与 pack 族边的运行时权重（Hebbian 强化、boosts）都应恢复。
- **Observed**: overlay 只做两件事：新节点整体插入（311-315）、已有节点只拷 label 和（≠0.5 的）weight（318-321）；边只在三元组不存在时追加（322-326）——**pack 已存在边的权重/激活/extra_attrs 更新永远不恢复**。实测生产：pack 仅 37 节点/52 边，运行时对 pack 节点的 extra_attrs 更新 0 例，但 pack 边权重分歧 **3 条**（1.5→1.0、1.00194→1.0、0.900046→0.9），即 3 次 Hebbian/重放强化在重启时被还原。另注意 318-321 的语义：weight 只在「保存值 ≠ 0.5」时拷贝——把 pack 节点权重显式设为 0.5 的运行时修改同样丢。
- **Root cause**: overlay 是为"pack 只读基底 + runtime 增量"的分层假设写的，但 save 时落的是**合并后全图**；读回时没有按"pack 为基底、runtime 覆盖"的分层语义合并（缺 deep-merge：extra_attrs 逐键、weight 恒覆盖、graph_space/confidence 补拷、边按 same-triple 覆盖权重）。
- **Proposed fix**: overlay 的 else 分支改为完整合并：`existing.weight = node.weight`（去掉 0.5 特判）、`existing.graph_space/confidence = ...`、`extra_attrs.update(node.extra_attrs)`、`last_access` 取大；边循环改成「存在则 `max`/覆盖权重 + touch」，与保存侧语义对称。
- **Risk**: pack 体系当前几乎空（37 节点）——这是唯一让问题"现在无害"的原因；一旦未来把大量先验知识移入 pack（论文 pack 定位即如此），每次重启都会静默丢掉该族节点的全部学习增量（因果观察数、能力经验、Hebbian 权重），且无日志。

### L1-PERS-03 — 损坏的 experience_timeline.json 首写即覆盖，无备份无降级
- **Component**: json_store.py:20-29（load_json 损坏→default）；experience.py:248-264（_load 空载入）；experience.py:266-276（flush 无条件替换）
- **Category**: L1-SILENT-FAILURE
- **Severity**: minor
- **Expected**: 时间线（300+ 条因果聚合、79 条假设、30 条晋升记录=长期学到的泛化）损坏时应保留文件并告警，恢复前不落盘。
- **Observed**: `load_json` 失败返回 None → `_load` 得空时间轴（仅 warning）；此后**第一次** `append`/`put_section` 即 `flush` 原子替换——损坏文件（含其中可解析部分如 causal 段）被"只剩新事件"的文档覆盖。对比：图谱侧有缩小守卫+快照轮转，时间线侧**没有任何守卫或备份**（data/ 下无 experience_timeline.json.bak*）。
- **Root cause**: 与 L1-PERS-01 同模式：load 失败静默降级为空 + 写路径无"曾降级"记忆。
- **Proposed fix**: 与 01 相同的最小修复：load_json 检测到解析失败时重命名 `.corrupt_<ts>`；ExperienceTimeline._load 记录 `self._load_degraded`，degraded 时 flush 改为只写 `raw` 到另一个路径或跳过并 ERROR。
- **Risk**: 因果统计（支撑/反例/置信度）是纯本地学习成果、无外部真源，丢失不可重建；ring 缓冲 4000 条上限说明"raw 可丢"是设计，但 causal 段不是。

### L1-PERS-04 — 时间轴观察者异常只记 DEBUG，因果归因断链不可见
- **Component**: experience.py:324-328（观察者回调异常）；experience.py:843-848（promote_to_kg 的 mark_active）
- **Category**: L1-SILENT-FAILURE
- **Severity**: minor
- **Expected**: `CausalLearner._on_timeline_event`（归因窗推进的唯一驱动）若抛异常，至少 WARN 留痕——它一坏，"动作→迟到结果"整条归因链静默停摆，只有聚合数据不再增长才可事后察觉。promote 后的点火失败同样应 WARN。
- **Observed**: `except Exception as e: logger.debug("[Experience] 观察者异常（忽略）: %s", e)` ——DEBUG 级别，生产默认日志配置不可见；promote_to_kg 的 `engine.mark_active(...)` 异常被裸 `except: pass` 吞掉（843-848），连 DEBUG 都没有，且此时 `_promoted` 已记账、_persist 已写——晋升成功但点火失败的中间态无任何痕迹。
- **Root cause**: 观察者按「其他模块故障不应污染时间轴」的原则做宽，但把**主消费者**（CausalLearner）与普通旁路一视同仁。
- **Proposed fix**: 观察者异常至少 WARN + 异常对象里带观察者函数名；mark_active 失败改 `logger.warning`（一次失败本可重试）。
- **Risk**: 归因/晋升链静默退化；故障仅在审计聚合数据时偶然发现。

### L1-PERS-05 — Edge 反序列化的非法 relation_category 静默抹平且无告警（与 Node 不对称）
- **Component**: graph_model.py:300（Edge.__init__ 非法类别→DEFAULT）；graph_model.py:333-336（from_dict）
- **Category**: L1-SERIALIZATION
- **Severity**: info
- **Expected**: Node 侧有"磁盘非法 label/space → warning+降级"（262-263）的先例；Edge 侧非法 relation_category 应与 Node 同责（类别决定扩散方向/衰减倍率），至少要 warning。
- **Observed**: `relation_category if relation_category in RELATION_CATEGORIES else DEFAULT_RELATION_CATEGORY`——旧档/手工文件里任何拼错类别都被静默变成 semantic_relation，扩散方向语义被改写且无痕（Node 的 ValueError/降级 warning 存在，Edge 没有）。
- **Root cause**: Edge.__init__ 的历史宽容设计未随 Node 的"严格校验+显式降级"（2026-09-19 架构对齐）同步。
- **Proposed fix**: from_dict/__init__ 中对非法类别 `logger.warning`（含 id/边端点）；保留降级行为（不 raise，兼容旧档）。
- **Risk**: 关系类别语义被静默改写（causal→semantic），读图侧扩散方向与存盘意图不一致；低概率、难发现。

### L1-PERS-06 — timeline APPEND 合并去重只看末条（merge 窗口仅与 `_raw[-1]` 比较）
- **Component**: experience.py:302-316（append 合并逻辑）
- **Category**: OK（设计，但记录边界）
- **Severity**: info
- **Expected / Observed**: 合并条件（同 event_type/actor/subject/change 且 ts 差 ≤2s）只与**紧邻末条**比较，非末条同簇事件不会被合并。生产数据 4000 条中 meta.repeat≥1 的事件存在（实测 raw 内 `meta.repeat` 出现，如 minecraft 高频观察），说明去重工作；但多来源同拍交错时（具身+认知同 2s 窗）合并面偏窄。"记发生过什么，不记采样"目标大体达成。
- **Root cause / fix**: 设计取舍（O(1) 合并 vs 全簇扫描）；无需修，记档供数据解读。
- **Risk**: 无。

### L1-PERS-07 — 重放调度状态静默落盘失败（无日志）
- **Component**: experience_replay.py:415-448（_load_state/_save_state）
- **Category**: OK（设计，有注释背书）
- **Severity**: info
- **Expected / Observed**: `except Exception: pass` 两处，模块头注释自证"状态可丢：只影响节流，不影响学习收益（那些在图里）"；processed 500 条封顶 + 7 天 TTL，evict 后旧经验可重放，是有界设计。不修。
- **Risk**: 无（调度状态非学习成果载体）。

### L1-PERS-08 — 时间线持久化写盘节流最多丢 2 秒窗口，可接受但写盘频率高
- **Component**: experience.py:266-276（flush 节流）；json_store.py:32-54（atomic_write_json）
- **Category**: OK
- **Severity**: info
- **Expected / Observed**: 整文档"单一写者"（raw+全部统计节同帧合并写，修过 2026-09-22 互覆写事故）；原子写（同目录 mkstemp+fsync+os.replace，38-44）；失败时 `_dirty = not ok` 保脏可重试（274-275）。200KB 级文档每次 append 至多 2s 一次全量写——量级合理。`put_section` force 落盘，causal 统计永不丢。OK。
- **Risk**: 进程 kill -9 丢 ≤2s 事件；ring 缓冲 4000 上限内尾部事件，设计接受。

### L1-PERS-09 — 持久化知识→激活→消费链条走查（question 3，全链真实闭环）
- **Component**: continuous_cognition.py:296-298（每 tick 门）；continuous_cognition.py:1202-1298（_reactivate_field）；experience_replay.py:344-369（_inject）+ CC._replay_gate 1148-1163；app.py 启动 409（重建引擎）
- **Category**: OK（实测证据 + 不变量核对）
- **Severity**: info
- **Expected / Observed**: 重启后持久化知识重新进入扩散的路径**存在且满链**：
  1. CC tick 非 busy 分支每拍调 `_reactivate_gate()`（场温 <0.35 且连续冷 N 拍才点火）与 `_replay_gate()`（空闲+预算+退避）——门调用裸跑在 tick_once 内，整体被 `_loop` 的 try/except 包住（195-201），单拍故障不杀死循环；
  2. `_reactivate_field` 全图 eligible 竞争（排除引擎零件/热节点/焦点/瞬态记录），对选中节点**手动 activation+= · touch · mark_active · register_activation_source("reactivation")**（1284-1294，活跃前沿不变量四件套齐）；
  3. `ExperienceReplay._inject` 同款直写（assignment、cap 3.0、hot 0.3 跳过、mark_active+`memory_recall`，346-369），种子只解析**已存在**节点（_resolve_refs 204-217 宁缺勿假），随后 `diffuse_from` 交回引擎（319-324）；
  4. 消费历史实测：**3336 批 / 2760 条经验重放 / 2800 种子 / 75094 邻域点亮 / 746 条边 Hebbian 强化落盘**——learning 成果（边权重增量）进 runtime_graph.json，重启后 restore（见 1.2，pack 边除外见 02）；
  5. causal 晋升链路：aggregations 94 → hypotheses 79 → promoted 30 → 图内 操作: 20/变化: 15 节点（28/30 键对应）→ 行动先验 `action_prior` 消费（唯一 ACTION 签名 18 个 / 聚合 94 组）。
  一条真实例证：`gather_resource(dirt)` 聚合 20 次支撑 → 假设 → 晋升为 操作:gather_resource(dirt) 节点（extra_attrs 携带 observations/support/confidence provenance），重启后该节点随 runtime_graph 恢复、具备再点火资格、其动作签名供 replay 桶 2（未晋升假设）与重放种子解析。链路无明显断点。
- **Root cause / fix**: 无（设计）——唯一注意：见 02，pack 族节点的学习增量存在重启回退面。
- **Risk**: 无。

### L1-PERS-10 — C21 事件框架生产默认关（行动失败/成功的"内源落图"在大线处于休眠）
- **Component**: config.py:1925-1946（experience_eventframe）；action_system.py:190-202（_evf_defaults）
- **Category**: DESIGN
- **Severity**: info
- **Expected / Observed**: `"enabled": False`（主门：零落图零点火），实测图内 `行动经验:` 节点 0 个（而普通 `行动_` 留痕 1753 个、失败 ACTION 事件在时间线 845 条中可数）。experiment 文档自述 C20e 5/5 行为翻转、C20g 失败回执"图侧独力承担 17→15 差分"——机制在沙箱验证过但生产未启用（精确回滚门）。重放历史的 `promoted: 0 / new_edges: 0` 同样说明生产期因果→KG 晋升除既有 30 键外无新增（尚未越过 support≥5 & conf≥0.8 晋升线，假设 confidence 实测 0.60-0.83）。
- **Proposed fix**: 设计决定（DESIGN）；如需把"失败体验"纳入生产认知，开 `enabled: True` 即得（零代码改动），注意 C21 依赖的"失败不按暂态集合豁免"已由 diffusion 侧承载（diffusion_engine.py 条件注释在案）。
- **Risk**: 生产侧"行动失败"只反映在统计（action_prior）与朴素 行动_ 留痕，缺极性边对实体的抑制通道——实验证据显示此通道是统计静默时唯一差分来源。

---

## 三、链条逐环结论（question 1-5 答案汇总）

| 核查点 | 结论 |
|---|---|
| 1. 写盘完整性（timeline 序列化对称/原子/损坏行为） | 对称无损（0 缺键、结构一致、单一写者+原子写）；**损坏行为有洞**（L1-PERS-03） |
| 2. 重启重载（图结构一致性，实测） | **load→save→load 0 差异；重启 overlay 后 9088/9088 三元组在位、3611 runtime 独占节点全量恢复**；pack 族增量回退 3 边（L1-PERS-02） |
| 3. 持久化知识→激活→消费 | 真实满链（reactivation + replay 双门 + 不变量四件套 + 晋升读数 30 键/消费先验），实测重放 3336 批、Hebbian 746 边落盘复用（L1-PERS-09） |
| 4. 静默失败清点 | json_store 良好（失败告警+保脏重试）；图谱 save 异常→logger.exception 且节流不更新可重试；**三处短板**：损坏图覆盖（01）、观察者/点火吞错（04）、Edge 类别静默抹平（05）；replay 状态吞错有注释背书（07） |
| 5. 时间线↔图关系 | 55.6% 条目可解析到图节点（minecraft 80.2%、action_system 28.9%、纯时间线来源 0%）；设计上 timeline 是"经历层"不与图一一对应，桥接=晋升（操作/变化 节点）+ 行动留痕（行动_ 节点）+ C21（生产关）；存在"只写时间线不进图"路径（say/communicate 0 留痕、dialogue/emotion/reward/mc_session 全 0）——其中经沟通通道的言语事件在图上只有 utterance 观察节点，值得产品意识（语言经历只活在时间线，重启后仅经 reactivation 的 subject 解析才能再碰） |

补充：`scripts/` 下持久化相关脚本（run_graph_expansion / apply_claude_expansion / migrate_*）全部复用 `KnowledgeGraph.save`（原子+缩小守卫），无独立写盘路径；graph_rotation.py 快照轮转 12 份在案（data/graph_snapshots/），`_flush_save_at_exit`（app.py:2403-2413）退出强制落盘且拒启实例不写（2405-2408），保存器为单写者后台线程 + force 等待（2309-2376）——保存器本身无问题。