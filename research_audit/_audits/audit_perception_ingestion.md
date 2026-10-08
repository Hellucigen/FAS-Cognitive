# Layer 1 审计：perception → 知识图谱 摄入路径（本体污染 / 实体·关系抽取质量）

- 日期：2026-09-30
- 范围：nlp_processor.py（LLM 抽取）、app.py 主写图路径（_auto_consolidate_curiosity_knowledge / _ingest_narrative / _wire_event_structure / approve_memory）、continuous_cognition.py（CI 节点）、eye/screen_ocr.py（视觉）、speech_act_graph.py（话语）、self_graph.py（情绪）、expression_feedback.py、reflection_engine.py、action_system.py（行动留痕，涉及边大头）、graph_schema.py / graph_model.py（归一漏斗）、prior_knowledge.py / world_prior.py（先验）、graph_expansion.py（拓展）
- 数据证据：data/runtime_graph.json（2026-09-28 14:09 快照，3648 节点 / 9088 边）
- 方法：静态追写图调用链 + 运行下图数据全量统计（PYTHONIOENCODING=utf-8）
- 结论摘要：**观察类 token 本体污染基本不存在**（0 命中）；写图漏斗（关系归一 + 去重 + hub 守卫）有效，图上全部 9088 条边都在当前 ontology 内、无重复边、无悬空边。确认问题集中在：**主记忆写图路径静默吞边、关系兜底归一不可观测、命名式守卫有 nodes 列表漏口、情绪共振整句入 id、涉及 边膨胀（3634 条）**。

---

## 一、总体量化（生产图 2026-09-28）

| 指标 | 数值 | 说明 |
|---|---|---|
| 节点 | 3648 | 重复 id：0 |
| 边 | 9088 | 精确三元组 (src,dst,rel) 重复：0；自环 (src==dst)：4（历史数据）；端点缺失：0 |
| 边关系覆盖 | 9088/9088 | 全部在 config.relation_ontology 内（写时归一漏斗有效） |
| 关联（fallback 词）边 | 246 | 无法区分规范写入与兜底抹平（无 instrumentation） |
| 涉及 边 | 3634（40%） | 83%（3019 条）来自 action_system 行动留痕 |
| 时间顺序 边 | 1614 | 叙事链 + 动作链 + 父子事件挂接 |
| 发生于时段 边 | 105 | 固定时段桶，设计正确 |
| episodic 节点 | 2335 | 带 event_timestamp 仅 130（5.6%）；591（25%）无任何图级时间锚 |
| 节点带 source | 63%（2290/3648） | 1358 节点无 source |
| 边带 provenance | 3.2%（292/9088） | world_prior 程序边为主；LLM 抽取的 reason 字段从未落图 |

---

## 二、确认的问题（Confirmed）

### L1-PIN-01 — 主记忆写图路径静默吞掉 add_edge 失败
- **Component**: app.py:2213-2236（_auto_consolidate_curiosity_knowledge 边循环）；graph_model.py:489-493（add_edge 端点缺失 return False，无日志）
- **Category**: L1-SILENT-FAILURE
- **Severity**: major
- **Expected**: 写图边失败应至少留 warning（端点缺失 = 抽取结果部分丢失，属于可观测性缺陷）；world_prior.py:125 注释「add_edge 会静默丢端点缺失的边，教训在册」、叙事路径（app.py:1880/1889 显式 `in kg.nodes` 守卫）、动作路径（action_system.py:920-943 显式守卫）、graph_expansion.py:480（检查返回值）——唯独**主抽取路径不检查返回值**。
- **Observed**: `_auto_consolidate_curiosity_knowledge` 对 LLM 草稿逐边 `kg.add_edge(...)`，从不看返回；若 LLM 在 edges 里引用了未进 nodes 列表、也不在图中的实体，该边被 graph_model.add_edge 静默丢弃（端点过滤 DROP/VERB_FRAGMENTS 也会造成同样结果）。生产图无悬空边（0 条）说明丢的是整条边，不是残边。
- **Root cause**: add_edge 的静默 False 契约 + 调用方把"草稿节点先建、边上端点必然存在"当成不变量，但 LLM 输出的 edges 与 nodes 并无一致性保证。
- **Proposed fix**: 主路径写边前先 `_ensure_endpoint(src)`（不存在时按节点循环同规则补建或跳过并 log warning）；至少在 add_edge 返回 False 时 `logger.warning("[CuriosityAuto] 边丢弃: %s -[%s]-> %s（端点缺失）")`。
- **Risk**: 关系抽取静默丢数据，事后从图上看不出曾经有过这条关系；LLM 提及但漏入 nodes 列表的实体关系全部蒸发。

### L1-PIN-02 — 关系兜底归一不可观测（fallback 抹平无计数）
- **Component**: graph_schema.py:46-69（normalize_relation）；config.py:332（relation_fallback="关联"）
- **Category**: L1-SILENT-FAILURE
- **Severity**: major
- **Expected**: 未收录关系落 fallback 时应可统计/可告警（论文"关系有明确语义"的工程落点是词表，词表外语义应可见）。
- **Observed**: normalize_relation 对未收录自由文本静默返回 "关联"，无计数、无日志（graph_schema.relation_surface_stats 只是词表规模审计，不统计运行时 fallback 命中）。生产图 246 条 关联 边无法区分规范写入与兜底抹平。config.py:357-368 的注释自证事故史：「结果」/「网络」/「掉落」 三个词在未注册期间被兜底归一成 关联 落盘多日/多次后才发现语义被抹平（D-7 审计、EXPERIMENT_D_EVENTFRAME.md §8）。
- **Root cause**: 兜底函数无旁路观测钩子；词表与"图上实际写的词"靠人工同步，漂移只能事后发现。
- **Proposed fix**: 在 normalize_relation 内对 fallback 命中做幂等计数（graph_schema 模块级 Counter + 每 N 次一条 warning，或 hook 到 fas_log），并在 relation_surface_stats 中暴露 `fallback_hits`。
- **Risk**: 新关系语义被静默磨平成 共现，读图侧（扩散方向、因果通路）从此认不出该语义；且不可观测意味着只能靠抽样日志偶然发现。

### L1-PIN-03 — 命名式 X—Y—Z 事件节点守卫有 nodes 列表漏口
- **Component**: nlp_processor.py:779-802（nodes 列表原样放行）vs nlp_processor.py:866-894（只拆解 `event["summary"]`）；app.py:2189-2211（_auto_consolidate 节点循环无 COMPOUND_NODE_RE 检查）；app.py:2093-2100（_wire_event_structure 只守 API 草稿路径）
- **Category**: L1-SCHEMA
- **Severity**: major
- **Expected**: 「禁止 X—Y—Z 命名式事件节点（架构对齐 2026-09-19）」应在**所有** LLM 节点入图路径生效，包括普通 nodes 列表（LLM 完全可能把 "创建—对象—rim.txt" 放在 nodes 而不放在 event.summary）。
- **Observed**: 生产图 22 条 X—Y—Z 复合节点中 16 条 source=curiosity_auto，最后一条 **2026-09-19**（'请求—对象—僵尸'）——守卫上线当天仍有漏网，走的正是 nodes 列表路径（nodes 列表完全未过滤）；9-19 之后靠 prompt 纪律维持零新增，而非结构守卫。'创建—对象—桌面上txt文件'（09-05）、'看屏幕—内容—rim.txt'（09-06）等皆为违例存量。
- **Root cause**: 拆解逻辑只挂在 event.summary 一个出口上；nodes 列表写图时只有 PRONOUNS/VERB_FRAGMENTS 过滤（nlp_processor.py:775-802），没有 COMPOUND_NODE_RE 过滤。
- **Proposed fix**: 在 extract_assertion_graph 的 nodes 循环处加 COMPOUND_NODE_RE 命中即拆解（复用既有的拆分逻辑，抽出公共函数）；_auto_consolidate 节点循环再兜底一层（幂等）。
- **Risk**: prompt 回归 / 换模型即复发；违例节点把谓词+槽位烤进 id，违反原子性，且 _fuzzy_match_node 的包含匹配对复合名产生错误归并。

### L1-PIN-04 — 情绪共振把用户整句原文当作事件节点 id
- **Component**: dialogue_signals.py:113-119（emotion_resonance → inject_emotion(kg, text, emo) 传原始文本）；self_graph.py:285（`event_id = f"事件: {event}"`）
- **Category**: L1-ONTOLOGY（句子型节点）
- **Severity**: major（量小：5 条；但每次关键词命中都会新造句子节点，逐月累积）
- **Expected**: 事件节点 id 应为谓词短语/规范名（论文 §3.2 事件命名规范），原文存 extra_attrs；疑问句不是"事件"。
- **Observed**: 生产图 5 条 `事件: …` 节点：'事件: 你觉得孤独是什么感觉？'（**用户问句被标成情感事件**）、'事件: 我刚在游戏里看到一个没见过的发光方块，有点好奇那是什么' 等。atomicity_analyzer.py:51 已把「句子型（含 事件: 前缀的情绪注入句）」列为已知违规类，但代码未修。另外 keyword_emotion_hits 命中即建事件，无语义佐证也建（结构层设计如此，但节点名用整句有害）。
- **Root cause**: inject_emotion 的 event 形参直接拼接为节点 id；调用方把 user text 当 event 传。
- **Proposed fix**: id 生成 `事件: {event[:10]}_{ts}` 或动词短语化；全文进 extra_attrs["text"] + source=emotion_injection（已有）。
- **Risk**: 句子 id 破坏节点原子性；扩散/FAISS 索引/模糊归并对长句噪声敏感。

### L1-PIN-05 — 涉及 边 3634 条（40%）源头上是行动留痕的通用共现边
- **Component**: action_system.py:920-923（每条行动 → 依据 basis 1 条 涉及）、930-943（→ 对象 1 条 涉及）；expression_feedback.py:121-124；reflection_engine.py:554-559；self_graph.py:524-525；app.py:1881/1890（叙事）、4717（搜索记录）、2135（槽位拆解）；nlp_processor.py:880；prompt_templates.py:362（提示词把 涉及 定义为"事件中的客体/事物"）
- **Category**: L1-SCHEMA（非污染；语义混用/膨胀）
- **Severity**: minor（本应是 major；但因为扩散方向/类别一致、行为已稳定，定 minor 提示关注）
- **Expected**: 事件-客体 参与语义与"生成时刻注意场共现"是两种语义，不应共用同一关系词表达全部。
- **Observed**: 精确归因（图数据扫描）：3634 条 涉及 中 3019（83%）src 为行动留痕/意图式节点（episodic），走 action_system 的「依据」（basis，即"做动作时在想什么"）与「对象」两条循环写边；另 表达/反思/思考 记录约 400、LLM 抽取约 180、搜索/叙事 <60。设计自知其间（graph_schema.py:157 把 关于/涉及/关联 统称"共现边"，repair_hub_pollution 只修 hub 汇点不修普通节点），但 40% 边同一关系词意味着关系区分度被压低。
- **Root cause**: 行动留痕优先低成本可欠（0.4 权重共现边）而非语义精确；原子关系词表粒度细但写边方习惯用 涉及 兜底。
- **Proposed fix**: 行动留痕的 basis 边改用「基于」（cognitive，CC 意图边同款，语义=依据）；对象边保持 参与/涉及 二选一按对象类型。至少把 basis 与对象两种用途分开。
- **Risk**: 不修则扩散通路趋同（全部经 cognitive_relation 双向/前向同一通道），后续做关系加权/通路审计时区分度不足。

### L1-PIN-06 — 时间顺序 边方向约定在写边方不一致；25% episodic 无时间锚
- **Component**: app.py:1861-1863（叙事：事件i-1 → 事件i，前→后）；action_system.py:951-954（动作链 prev → cur，前→后）；app.py:2155-2158（_wire_event_structure：子事件 → 头部事件，**后→前**）；config.py:315（temporal_relation 传播方向 forward）
- **Category**: L1-SCHEMA
- **Severity**: minor
- **Expected**: 同一关系在图中应有一致的 src/dst 时间语义；若两种都是刻意的（当前事件回指头部 vs 时序推进），应分开关系词或在扩散方向表里区分。
- **Observed**: 叙事链/动作链 前→后；父子事件挂接 后→前（子事件指向头部事件）。方向是 forward 的单向传播意味着子事件→头部的链把激活向过去传导（语义=延续聚焦，_get_focus_context app.py:457 也按 后→前 找子事件，自洽）；叙事链把激活向未来传导。两条约定并存，第三方读图无法仅凭 src/dst 判断时序（如 '出发日群聊无动静 -> 询问同学出发情况' 实际后者更早）。另：2335 个 episodic 节点中 591（25%）无任何图级时间锚（无 event_timestamp、无 发生于时段、无 时间顺序 边；动作链跨重启断链是设计声明，action_system.py:946-947）。
- **Root cause**: 事件挂接与叙事/动作链由不同作者、按不同"谁指向谁"直觉实现；无 schema 级时间方向约束。
- **Proposed fix**: 文档化约定（子→父=延续、父早子晚；序贯=前→后），或引入 时间顺序_延续 变体关系；对无锚 episodic 在写链时补 created 时间桶边。
- **Risk**: 时间语义歧义影响依赖时序的行为（因果窗、记忆复用、focus 链检索）。

### L1-PIN-07 — 实体抽取规范化：无重复但单字/纯动词节点会进图
- **Component**: nlp_processor.py:775-776（PRONOUNS/VERB_FRAGMENTS 只盖 25 个虚词/介词: 是了的地得在...），nlp_processor.py:577（DROP 只盖代词）；app.py:1953-2033（_fuzzy_match_node：完全匹配 > 别名 > 包含≥60% > 边界打分）
- **Category**: L1-SCHEMA
- **Severity**: minor
- **Expected**: 语言层停用词表应覆盖实义高频动词/单字，防单字概念节点。
- **Observed**: 重复治理有效（节点重复 id=0、边重复=0、近重复靠 _fuzzy_match_node 全局归并，'收获日2'→'收获日2游玩' 实例在册），但生产图有 17 条单字节点（家/药/水/钱/涼/葵/累/甜/猫/树 + 无来源的 瓦/碰/吃/杠/苦/跳），以及 1 条纯动词节点 '感觉'（curiosity_auto）。'累/甜/苦' 是形容词单字，'碰/吃/杠' 是动词——LLM 抽取把口语片段直接当概念。
- **Root cause**: 过滤器只处理代词与句法虚词，不处理"单字/形容词/动词"这一类；LLM 提示词也未约束节点必须是名词性短语。
- **Proposed fix**: 抽取层加 stopword/单字审查（len==1 且非实体白名单 → 丢弃或并入上下文节点），MEMORY_EXTRACT 提示词加重"节点必须是名词性实体或规范概念"。
- **Risk**: 稀释概念空间，扩散/回答 top-k 里出现无信息量单字节点。

### L1-PIN-08 — 事件绝对时间供给不足（时间戳只落 5.6%）
- **Component**: nlp_processor.py:858-864（event_time 归一：None→""）、886-889；app.py:2122-2130（_wire_event_structure 只写入非空 event_timestamp）
- **Category**: L1-SCHEMA
- **Severity**: minor
- **Expected**: 论文 §3.2 事件节点中心化要求时间维度可靠；事件时间应尽可能落属性（event_timestamp）+ 时段桶（发生于时段）。
- **Observed**: 2335 episodic 节点仅 130 条带 event_timestamp（5.6%）；发生于时段 105 条（含叙事/动作场景）；无锚 591（25%，见 PIN-06）。LLM 大多数时候不产出可解析 event_time（"None" 归一为空），兜底只有 created 序号时间，事件间时序靠 时间顺序 链（1614 条）勉强维持。
- **Root cause**: LLM 抽取时间不稳定 + 写图侧无"抽取失败则用当轮时刻"的兜底（叙事有 created，动作有 created，对话抽取没有）；发生过 9-13 事故（时间锚点未传原轮次）后聊天恢复路径已修（app.py:5360-5371），但常态对话路径 event_time 为空仍常见。
- **Proposed fix**: extract_assertion_graph 在 event_time 解析失败时用当轮 now 的日期字符串作 event_timestamp；_wire_event_structure 未提供时以 created 日期补 发生于时段。
- **Risk**: 时间轴稀疏 → 情景记忆时序检索退化；C21 因果窗等时序依赖机制信号变弱。

### L1-PIN-09 — Provenance 不完整：37% 节点无 source，LLM 的 reason 字段从未落图
- **Component**: 节点：speech_act_graph.py:207-218（话语_ 无 source）、reflection_engine.py（反思_ 无 source）、expression_feedback.py:96-115（表达_ 无 source）、app.py:1864-1872（叙事角色/actor 节点 _add 仅 canon）、app.py:5276（approve_memory 无 extra_attrs）、action_system.py:902-919（行动留痕无 source attr，只有 type）；边：app.py:2232-2234（_auto_consolidate 丢弃草稿边的 reason 字段）
- **Category**: L1-SCHEMA（provenance）
- **Severity**: minor
- **Expected**: 每条图数据应可回答"谁、为何、何时写的"；抽取层已经为每条边产出 reason（nlp_processor.py:841），应随边落盘。
- **Observed**: 1358/3648 节点缺 source（分类：中文短名类 471、话语/思考/反思/表达/回答记录 282、配方/物品/缺口 137、Unknown/变化 57、CI/倾向/目标 44、其他 355）；边级 provenance 仅 3.2%（world_prior 程序边）；**抽取边的 reason（如"用户亲历事件"）在 _auto_consolidate_curiosity_knowledge 组装 GEdge 时被丢弃**（app.py:2232-2234 只带 src/dst/relation/weight/category）。
- **Root cause**: provenance 无 schema 强制（Node 有 source 可选字段、Edge 只有 extra_attrs）；各写边方自行决定带不带。
- **Proposed fix**: Edge.extra_attrs 在 _auto_consolidate/叙事/动作路径统一写 `{"reason": ..., "source": ...}`；节点循环统一补 source（叙事 actor 节点、approve 路径）。
- **Risk**: 审计/记忆更正（哪条是用户教的、哪条是模型猜的）无依据；事故复盘只能靠 fas_log。

### L1-PIN-10 — _ingest_narrative 事件命名死分支（两分支完全一致）
- **Component**: app.py:1849
- **Category**: L1-BUG
- **Severity**: major（逻辑错误，静默生效）
- **Expected**: `nid = f"{title}—{story_name}" if nid_ok(title) else f"情节{i}—{story_name}"`——命名安全的标题进入节点名。
- **Observed**: 实际代码 `nid = f"情节{i}—{story_name}" if nid_ok(title) else f"情节{i}—{story_name}"`，两个分支字面一致；nid_ok(title) 结果被丢弃。生产图叙事事件节点全部是 情节1—故事名 格式（如 '情节1—远方旅游'），标题只存在于 extra_attrs.summary；同名故事多次叙事时靠追 (timestamp) 消歧（app.py:1850-1851）。
- **Root cause**: 重构/复制时的笔误，条件恒真恒假无差别（死条件无告警）。
- **Proposed fix**: 恢复为 `f"{title}—{story_name}"`，并保留 nid_ok 守卫（现有）。
- **Risk**: 事件 id 失去识别力；同故事多轮叙事产生批量 (timestamp) 后缀节点，跨轮尾匹配（_fuzzy_match_node 包含匹配）易把整个故事名归并成同一节点。

### L1-PIN-11 — add_node/add_edge 静默失败模式：返回 False 全面无人检查
- **Component**: graph_model.py:415-417（add_node 重复 id return False）、489-493（add_edge 端点缺失 return False）；写边方普遍忽略返回值（已核对：continuous_cognition.py:669-696 预检查了 basis，但 hub 边 `Self→CI` 假设 Self 存在；expression_feedback.py:96-129 预检查了 hub/行为节点；app.py:4717 预检查）
- **Category**: L1-SILENT-FAILURE
- **Severity**: minor（主路径已被 PIN-01 单独点名；此处是模式性风险）
- **Expected**: 写图失败至少 debug 级留痕，或依赖守卫方显式 `if not kg.add_edge(...): logger…`（graph_expansion.py:480 范式）。
- **Observed**: 全仓 202 处 add_node/add_edge 调用，只有 graph_expansion.py 一处检查返回值。
- **Root cause**: add_edge 契约设计为静默 False（防止冷启动阶段端点未就绪反复打日志），调用方纪律不统一。
- **Proposed fix**: 把 add_edge 的 False 分支升级为可选 `debug_on_fail`/统一 fas_log 聚合计数；至少在主写图路径（PIN-01）落地。
- **Risk**: 结构写入缺失无感知；图数据完整性的唯一兜底是审计脚本。

### L1-PIN-12 — 叙事角色节点无 source（provenance 缺口的具体一例）
- **Component**: app.py:1864-1872（`_add(actor, {"canon": "concept"})`）
- **Category**: L1-SCHEMA
- **Severity**: minor
- **Expected**: `_add` 的 extra 至少带 `"source": "narrative"`（故事/事件节点同款）。
- **Observed**: 角色 actor 节点仅 `{"canon": "concept"}`，无 source；已计入 PIN-09 的 1358 条缺源集合。
- **Root cause**: _add 调用处漏传 source（故事节点 L1839-1840 与事件节点 L1852-1854 都传了）。
- **Proposed fix**: 一行：`_add(actor, {"canon": "concept", "source": "narrative"})`（已存在节点是 setdefault 语义，幂等）。

---

## 三、核实为 OK / 设计决定的内容

### L1-PIN-13 (OK) — 观察类 token 本体污染核查：未发现
- 生产图 3648 节点对 {观察, 看到, 看见, 看着, 在, 附近, near, at, in, has, looks_like, observed, 位于, 靠近, 的, 很远, 旁边} 全部 **0 精确命中**（'观察' 存在但为合法先验概念节点 prior_seed/prior_concept，无违例）；'位于'/'靠近' 仅作**边**关系词（ontology 收录）。
- 防线路径：nlp_processor.py:577（DROP 代词）、775-776（PRONOUNS+VERB_FRAGMENTS）、819-826（边上代词/虚词过滤）、834-836（边关系当场归一）；graph_model.py:494-500（add_edge 内再归一 + 类别补全 + hub 白名单 warning-only 守卫）；graph_schema.py:160-181（repair_hub_pollution 启动自愈 hub 共现边）。hub 白名单（graph_schema.py:123-149）与 _RECORD_HUB_RELATIONS 证明"主体不是 Hub"纪律在册。
- 图上残留的"疑似污染"仅有：复合 X—Y—Z（22 条，PIN-03）、事件: 句子（5 条，PIN-04）、单字/动词节点（17+1 条，PIN-07）——均为评审过的已知类别，且各自有守卫/自愈文档化。

### L1-PIN-14 (OK) — mark_active 不变量：SCREEN_OBSERVE / EAR / 各直接激活写点
- SCREEN_OBSERVE 对话路径 app.py:3947-3954 → eye/observer.py:34-35 → eye/screen_ocr.py:345-353：`看屏幕` 节点 +3.0 + touch + **mark_active**（不变量注释在 screen_ocr.py:312-313）；eye_text_* 为被动 episodic 记录（activation 0 不进前沿，设计如此，新颖性参照由 seen_all 快照承担，screen_ocr.py:340-344）。
- 自主路径 (eye/observer.py ScreenObserver) 与对话路径共用 run_observation 同一条管线；"看屏幕" 概念点亮走 capability_graph 标准 mark_active。
- EAR：ear/ 目录无任何 kg.add_node/add_edge/mark_active（听觉仍为纯特征管线 + Observer 经 app 注入，未直接写图）。
- CC 内直写点全部合规：压力节点（continuous_cognition.py:894-904 mark_active）、再点火（1284-1291 mark_active + register_activation_source）、时段时钟（temporal_awareness.py:238-240）、语言线索（323-326）、情绪共振激活（self_graph.py:317-323）、具身感知（embodied_mapper.py:267-272，注释自证 09-22 修过一次不变量违例）。
- **CI 节点（continuous_cognition.py:669-697）**：激活写进 **extra_attrs**（`ea["activation"]`），不触碰 node.activation，不进扩散前沿——是 CC 自己的内部记账（_merge_or_form 强化路径 L643-651 同样走 extra_attrs），**不构成"激活直写点未 mark_active"违例**。注意其推论：CI 永不在扩散拓扑中被看到（_reactivate_field L1233 也显式排除 CI_）。若未来希望 CI 的活性参与注意场/top-k，需改为 node.activation + mark_active（设计决定，暂不改）。

### L1-PIN-15 (OK) — 时间顺序/时间锚的正面项
- 事件框架：父/子事件挂接（子→父 时间顺序）+ 发生于时段 固定 5 桶（app.py:2145-2160、temporal_awareness.py:333-345）；事件时间原文已从"日期节点"改为属性 + 时段桶（app.py:2075-2078 注释）——2026-09-19 架构对齐落地，杜绝了 "2026-09-19 那类 25 入度日期枢纽节点" 复发；生产图无纯日期节点（DATE_NODE_RE 核查 0 命中）。
- LLM 事件时间经 now 参数重放锚定（app.py:5360-5371 / nlp_processor.py:736-741），恢复路径正确。

### L1-PIN-16 (OK) — 其余写图方质量
- graph_expansion.py:378-498：归一（L443）、self_loop 拒（L452）、受保护端点拒（L461-467）、置信度门（L468）、每 seed 上限/全局预算（L389-393）、add_edge 返回值检查（L480）、ledger 行溯源（L482-487）、noise 过滤 _looks_like_noise（L111-120，排 eye_text_* 等）——审计范围内最完备的写图方。
- prior_knowledge.py：幂等 + source=prior_seed + gap/craft 节点带 type；world_prior.py：`_ensure` 幂等建节点（"add_edge 会静默丢端点缺失的边"教训在册，L125）+ `_edge` 带 provenance + `_mark` mark_active（L158-165）——范式级。
- curiosity_engine.py：UnknownConcept_* 仅探索真正 fires 时建（L261-287，防节点爆炸）；等待回答 状态节点带 type；兴趣水位挂节点属性不建节点。
- speech_act_graph.py：话语节点无激活直写（seeds 由调用方进 activate_from_inputs 统一入口，L238-240）；内容边只连已存在节点（L228-234）；旧话语修剪 _prune_utterances。
- 表达/反思/话语记录含原始文本（extra_attrs.text / summary），属隐私口径（data/runtime_graph.json 已退出跟踪，如 .gitignore 所示），非本次审计缺陷但建议复核。

---

## 四、修复优先级建议

1. **L1-PIN-01 + PIN-11**（静默吞边）：主写图路径检查 add_edge 返回值、端点缺失打 warning —— 最小改动 6 行内。
2. **L1-PIN-02**（fallback 观测）：graph_schema 加 fallback 计数器。
3. **L1-PIN-03**（nodes 列表复合名漏口）：COMOUND_NODE_RE 兜底到 nodes 循环（复用已有拆分逻辑）。
4. **L1-PIN-04**（事件: 整句 id）：id 切片 + 原文入 attrs。
5. **L1-PIN-10**（死分支）：一行恢复。
6. **L1-PIN-05/06/07/08/09/12**：语义/时间/provenance 类改进，与论文机制实验节奏合并安排。

---

*审计方法备注：所有图数据统计用一次性 Python 脚本对 runtime_graph.json 全量扫描（非抽样）；代码行号基于 E:\Project\Fascinator 当前工作树（2026-09-30）；未修改任何生产代码。*