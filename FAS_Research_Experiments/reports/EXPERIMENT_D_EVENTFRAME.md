# 实验 D：行动侧事件框架失败抑制探针（EXPERIMENT_D_EVENTFRAME）

- **日期**: 2026-09-28（§1–§7 首版定版）→ 2026-09-28 晚修复后复测定版（§8，C20e；行为层结论修订）→ 2026-09-28 深夜真实失败路径探针（§9，C20g；失败回执内源落图）
- **runner**: `scripts/run_exp_eventframe.py`（装配）、`scripts/sandbox_lab.py` EventFrameInjector（C20 + C20e + C20g）
- **前置**: 实验 A/B/C 三因果定版（`FINAL_EXPERIMENT_REPORT.md` §1–§20），审计 `PRE_EXPERIMENT_AUDIT.md`
- **因果问题**: 行动记忆若按愿景形态（签名级事件框架节点，槽位边连实体，结果极性可负）落图，注入的失败经验经扩散统一抑制机制发布负激活，能否压低对象实体激活、改变候选注意选择——完全不经过表格闭包/统计账本？
- **红线合规**: 零核心机制改动。触碰面 = 装配层（注入边/再点火，`build_stack` 新参 `eventframe`）+ 记录层（激活采样/reason 打印）。全部变更入 CHANGE_LOG C20。

---

## 1. 为什么做这个实验

论文原始愿景（2026-08-14 情景记忆规范 + 论文 §552-562 aagentic 声明）：

> 情景记忆以事件框架式存储，槽位通过边连接到客观事物，正/负激活调制下一次扩散。
> "当语义节点 v_s 被激活时，其激活值可以沿图谱边传播至与之相关联的情景记忆事件节点，从而引发经验回忆，并对当前认知状态产生调制"（§552-562，断言式）。

对话侧事件框架 2026-08-14 已定型（事件枢纽节点 -[涉及/参与者/发生时间/引发]→ 实体）。但**行动侧**是 timeline 流水 + 统计聚合（experience.py:11"Timeline = experience 层"），无事件框架节点、无极槽位边——愿景的行动侧通道从未被架起，也从未被行为学审判过。实验 A/B/C 三连证明：表格闭包/activation 传播/跨任务账本在沙箱达成层全部被闭合遮蔽。本实验直接把愿景形态的最小样板装进沙箱，问第一个问题：**失败抑制边经扩散是否改变候选注意选择**。

## 2. 装配（C20）

### 2.1 事件框架注入器（scripts/sandbox_lab.py，EventFrameInjector）

`build_stack(..., eventframe=(obj, fail_side, reign_amt))` 挂载。`obj=birch_log, fail_side=-0.8, reign_amt=1.2`。每认知 tick：经验节点低量再点火（模拟持续认知 §485 记忆再点火的沙箱替身：`n.activation = min(5.0, act + 1.2); engine.mark_active([eid])`），激活后沿边经扩散发射。

注入的图结构（`label="declarative-episodic"` / `graph_space="episodic"`，与对话侧事件框架同规范）：

```
行动经验:gather_resource(birch_log)   ← 签名级事件节点（episodic 空间）
  -[涉及]→ birch_log         w=+0.9  （对象槽位边，经验回忆正激发）
  -[涉及]→ 物品:birch_log    w=+0.9  （对象槽位边）
  -[结果]→ 变化:gather_resource(birch_log):failed  w=fail_side(-0.8) （结果槽位边，负极性=失败）
```

失败经验来源为**实验注入**（`extra_attrs.source="experiment-injected"`），非真实失败路径——本探针回答"失败抑制边经扩散是否改变候选注意选择"，不是"FAS 真实失败后自愈"。

### 2.2 两个关键机制事实（踩坑修正，见 §6）

1. **事件的 episodic 空间**：`label` 必须 `declarative-episodic`（graph_model.py:212 严格枚举），非法 label 在创建点 raise——C20 初版 `label="episodic"` 被 raise 后又被注入器 `try/except: pass` 吞掉，边从未建出来过（假阴性麻痹源）。
2. **抑制只在 total_w>0 时被发射**（diffusion_engine.py:1021-1024）：负权边的抑制贡献是"搭"在正权发射循环上的，某节点全部出边为负权时 total_w=0，整个分发循环跳过。C20 初版把 -0.8 连在事件→失败节点的结果边上，失败节点无对象出边——抑制信号从未到达 birch_log。

### 2.3 条件与协议

| | control | inhibit |
|---|---|---|
| 世界 | 同实验 A/B/C：oak@(6,0) 远（目标链）+ birch@(-2,0) 近 | 同左 |
| 事件框架 | 无 | `birch_log` 失败经验 + 每 tick 再点火 1.2 |
| 目标 | obtain oak_planks | 同左 |

- seeds 1–5，max_ticks=300，仿真时钟压缩三坑同前（poll 门槛/缓存 TTL/name_to_node 快照）→ `flush_causal` 收尾
- 记录层新增：`SAMPLES` 激活采样每 2 tick；ACTSNAP t0–t6；`--debug-reason` monkey-patch `_score_action` 打印候选 reason+激活值
- 输出：`FAS_Research_Experiments/eventframe/expD_{condition}_seed{N}.json`（events/snapshots/metrics/manifest 全量）

## 3. 结果（seeds 1–5，全确定——同 seed 逐位相同，descriptive 口径）

### 3.1 机制层（知识访问层）：抑制边真的压低实体激活

| 指标 | control | inhibit |
|---|---|---|
| birch_log 终态激活 | **5.0 封顶**（5/5） | **2.74**（5/5，±0.0000） |
| efi 事件节点激活 | 无节点 | 1.48（再点火稳态） |
| 首动竞择轨迹 | gather@birch → gather@oak → craft | 完全相同 |

扩散引擎统一抑制机制（负权边贡献 = `src.activation × |w| × beta × gain / total_w`，不消耗发射预算）沿 `-[结果]→` -0.8 边把 birch_log 实体激活从封顶 5.0 压到 2.74 稳态——预测方向正确、量级稳定、全部 5 seed 一致。

### 3.2 行为层：零差分（与 A/B 同型、且多一层根因）

| 指标 | control | inhibit |
|---|---|---|
| success | True@9（5/5） | True@9（5/5） |
| actions_total | 3（gather/gather/craft） | 3（同） |
| actions_redundant | 0 | 0 |
| 首动 | gather_resource@birch_log | 同 |

**首拍注意竞择不受抑制影响**：dbg-reason 显示 birch 候选 reason 列表 = `['UnknownBlock_birch_log', 'birch_log', 'CuriosityDrive', 'Haru的位置', '缺口:用途(birch_log)']`，其中 `缺口:用途(birch_log)` 是**缺口直写锚点**（autonomy.py:1516-1517 floor 0.9 + 每 tick mark_active，缺口目标活着=她正在想着这个对象）。`_score_action` 的 attention = max(reason 激活)/5.0 始终落在缺口的 1.5 上；抑制把 birch_log 实体压到 2.74 甚至 0.00，**max() 仍由缺口节点提供 → score 不变 → 竞择顺序不变**。

### 3.3 参数扫描（fail_side / reign_amt，seed=1）

| fail_side | reign_amt | birch_log 终态 | 首动 |
|---|---|---|---|
| -0.8 | 1.2 | 2.74 | birch |
| -1.5 | 1.5 | **0.00（完全压灭）** | birch |
| -2.0 | 2.0 | 0.00 | birch |
| -2.0 | 3.0 | 0.00 | birch |
| -2.0 | 4.0 | 0.00 | birch |

实体激活随参数单调下降到底（Edge 权重钳制 [-2,2] 前已到 0）——**行为零差分不是强度问题**（最大强度下 birch_log=0.00，首动仍 birch），是**缺口直写锚点结构锁死**。

## 4. 结论与对愿景的裁决

1. **机制级（知识访问层）正证据**：行动侧事件框架节点 + 负极性槽位边在现行扩散引擎下真实工作——失败经验的负激活沿 -0.8 结果边把对象实体激活 5.0→2.74 稳定压低，与引擎抑制路径（§2.2 机制事实 2）逐项吻合。愿景的"负激活调制下一次扩散"在机制层面**成立**，且这是首次直接观测（A/B/C 均未建事件框架节点）。
2. **行为级负证据**：事件框架的失败抑制改变不了候选注意选择。根因不是闭合遮蔽（本轮表格闭包仍在，但注意竞择走的是激活域而非表格），而是一层新的结构锁——**缺口直写锚点**：对象被压成 0 也无济于事，因为 attention 由 reason 中的缺口节点（floor+每 tick mark_active 直写）供给，缺口语义上"她正在想着这个对象"是扩散抑制的不可达层。
3. **对论文 aagentic 声明（§552-562）的口径**：可引用"语义事件节点沿负权槽位边调制关联实体激活（机制级实证）"，**不可**扩展到"失败记忆改变行为选择"——行为量级上缺口锚点先于事件框架生效。与 A/B/C 口径决议同族：机制存在、中间层可测、行为/达成层被结构性前置遮蔽。
4. **设计接口教训**：缺口直写锚点对扩散抑制免疫，是"失败记忆改变注意"这条愿景链的**结构阻断点**（不是参数问题）。若要让失败经验获得行为效力，接口上必须能让缺口锚点本身被负调制（直写路径叠加抑制），或让事件框架在其竞择发生前积累抑制（再点火前置）——两者都是接口重权，已列入蓝图（§5），不在本轮触碰。

## 5. 设计蓝图（修复前裁定：待实施 → C20e 已实施，见 §8）

- **蓝图 ①**：缺口/感知直写源接入抑制输入（锚点可被负调制）——**C20e 已实施**（缺口锚点列入负权涉及边目标）；
- **蓝图 ②**：事件再点火竞择前置（点火在感知直写前跑几拍）——**C20e 已实施**（warmup 3 拍）；
- 另增 **C20c**：失败经验=充分接触 → 补建 retired UnknownBlock 节点熄灭 novelty 顶格（装配层补沙箱只消费不建档的缺口）。

## 6. 踩坑记录（C20 内部，全部入账）

1. `<Node label="episodic"` → graph_model.py:212 raise「非法节点 label」→ 改成 `label="declarative-episodic"`；且注入器 `try/except: pass` 吞掉异常导致"接线成功"假象（`_ensure` 从未完成）→ 最小复现打印真实异常。
2. 负权结果边连向失败节点（孤立叶子）→ 抑制信号永远到不了 birch_log → 回路修正为"正权槽位边（涉及）+ 负权结果边"仍不够——负权边必须在节点有正权出边时才被发射（total_w>0），最终形态：涉及边 +0.9×2 撑 total_w>0，结果边 -0.8 指向失败节点。**修正后 5/5 压下**。
3. 行为零差分初判"抑制无效"是**假阴性**（接线假象 + 参数扫描前的推断）；修正接线后机制差分清晰——教训：探针先验接线（快照含节点/边）再读结论。

## 7. 资产

- runner: `scripts/run_exp_eventframe.py`（D 专用；四臂：control/inhibit/control_real/inhibit_real）
- 装配: `scripts/sandbox_lab.py` EventFrameInjector（trigger=prearm/on_failure）+ `build_stack` eventframe 参数（C20）+ warmup（C20e）+ despawn_on_dig + on_settled 链（C20g）
- 数据: `FAS_Research_Experiments/eventframe/expD_{control,inhibit,control_real,inhibit_real}_seed{1..5}.json`
- 账本: CHANGE_LOG C20 + C20e + C20g

## 8. 修复后复测定版（C20e，2026-09-28 晚）——行为差分 5/5 翻转

> **C21 数据事实修正（2026-09-28 白昼）**：实验装配的 `-[结果]->` 边当时**未在
> relation_ontology 注册**（配置词表），落盘实为兜底归一 `关联`（graph_schema 兜底；
> 行为不受影响——抑制发行只看 total_w 权重符号，不看关系词）。生产化（CHANGE_LOG
> C21）已注册 `"结果": "cognitive_relation"`（"网络"/"掉落"同款"词表必须跟写在图上
> 的词同步"先例）。本文 §2.1/§9.2 形制描述中的"结果边"在沙箱落盘数据里均为 `关联`。

### 8.1 修复内容（装配层，三层）

C20 定版的行为零差分有两层结构根因，修复按报告 §5 蓝图 + 新补第三层，全部装配级（autonomy / diffusion_engine / graph_model 零改动；CHANGE_LOG C20e）：

1. **蓝图② 再点火时序前置（warmup）**：run_cond 首拍决策 tick 前先跑 3 拍 `st.diffuser(); st.efi()`——C20 原版 efi 再点火在决策之后，首拍决策时抑制从未入图；前置后抑制在注意力竞择发生前已积累。
2. **蓝图① 缺口锚点负调制**：EventFrameInjector 默认目标追加 `缺口:用途({obj})`——负权涉及边直指注意力锚点，使抑制可达 attention=max(reason 激活)/5 的供给源（此前锚点对扩散抑制免疫）。
3. **C20c novelty 退休**：失败经验 = 充分接触 → `_ensure` 补建 retired `UnknownBlock_{obj}` 节点（`extra_attrs.retired="experiment-injected-failure"`）。autonomy.py:2708-2711 前缀回退在图内无此节点时无条件 novelty=1.0；沙箱 bridge_perception_gaps 只消费已建档节点、从不建档（真实感知会建档、经验认领后 retirement）——补建 retired 节点让"反复失败过的东西"不再持续喂满 novelty。

### 8.2 结果（seeds 1–5，全确定——逐 seed 逐位相同）

| 指标 | control | inhibit（修复后） |
|---|---|---|
| success | True@9（5/5） | True@**6**（5/5） |
| actions_total | 3（gather/gather/craft） | **2**（gather/craft） |
| actions_redundant | 0（好奇 gather 非冗余，但吃掉 3 拍） | 0 |
| **首动** | gather_resource@**birch_log** | **gather_resource@oak_log（翻转）** |
| birch_log 终态激活 | 5.0 封顶（5/5） | **0.0**（完全压灭，5/5） |
| 缺口锚点 `缺口:用途(birch_log)` 终态 | 1.5（直写托底，扩散不可达） | **0.0**（负权边直达压低） |
| efi 事件节点激活 | — | 2.2844（再点火稳态） |

首拍 dbg-reason（inhibit，seed1）：birch 候选 reason 激活 `[0.6, 1.2, None, 0.6, 0.0]`——retired UnknownBlock 0.6（不再顶格）、birch_log 实体 1.2（warmup 前置抑制已从 5.0 降下）、**缺口锚点 0.0**。对照 control：缺口锚点 1.5 托底、birch_log 1.2 起跑 → 首动 birch。**行为差分与注入存在性严格对应，5/5 逐 seed 确定。**

### 8.3 结论修订（覆盖 §4 第 2–3 点）

1. §4-1 机制级结论保持（§8 在机制级新增：缺口锚点也被负权边压到 0.0——抑制对直写锚点并非原则免疫，C20 的"结构锁"实为**时序锁**：抑制只要在竞择前积累到位即可达）。
2. **行为级由零差分修订为真差分**：失败经验经事件框架负极性槽位边压实体 + 压缺口锚点 + 熄 novelty，首动从好奇 birch 翻转为目标 oak，达成提前 3 拍、少 1 次好奇动作——**这是全实验室首次"记忆形貌 → 行动选择"的因果分离**（A/B/C 均困于闭包遮蔽或结构锁）。
3. 论文口径（§552-562）：升级为可引 **"事件框架式失败经验经负极性槽位边调制实体激活与缺口注意力锚点，改变后续行动候选选择（行为级实证，实验 D）"**；保留限定词"行动侧经装配层注入，非真实失败路径"。

### 8.4 修复过程自身教训

sandbox_lab.py 在修复中曾被坏 Edit 截断（EventFrameInjector.__init__ 内嵌 _ensure + eid/failed_nid 缺失），二次修复又误删结果边与 `_wired` 标志——一处 ast.parse 通过但 wiring 缺失（total_w>0 前提被破坏、抑制永不发射的复发）。防线：**改完先 ast.parse + 迷你 stack 显式断言节点/边落地，再跑实验**（本轮 smoke 脚本即如此，见 C20e）。

## 9. 真实失败路径探针（C20g，2026-09-28 深夜）——事件框架由真实失败回执内源落图

### 9.1 因果问题

§8 的行为差分来自 **build 时预注入**的失败经验（`source="experiment-injected"`）。真实系统里失败不是预注入的——她要先经历一次失败，经历本身才成为记忆。C20g 问：**失败回执（真实发生于行动链）能否内源触发事件框架落图，并产生同样的行为效力**？这正是愿景"失败记忆改变行动"的完整因果链（ffailure → 记忆 → 行为），不再含"实验员预先知道她会失败"的预注入成分。

### 9.2 装配（全装配层，核心零改动）

1. **世界侧（环境数据层）**：birch 树种下后标记 `world.despawn_on_dig={"birch_log": True}`——第一次 `/dig_pos` 时树已不在（"她到达时树已被拿走/枯死"），dig_pos 固有返回 `{"ok": False, "reason": "block_not_found"}`（sandbox_lab.py:259-261 既有事实逻辑，只补 despawn 属性）。感知照常建档（树在感知与决策时存在，缺口照常开）——**失败发生在行动中，不是世界里根本没有树**。
2. **回执链（装配层）**：`build_stack` 在 eventframe 四元组带 `trigger="on_failure"` 时，把 `am.on_settled` 链式包装：先保原回调（autonomy 的 recency/失败计数语义先行），再在 `not success and not cancelled` 时 `efi.arm()`——事件框架节点/槽位边**此刻**才落图（比 C20/C20e 的 prearm 晚一整个"先失败后记忆"的因果步）。
3. **点火**：arm 前 `__call__` 空转（`if not self._armed: return`）；arm 后同 C20e 每 tick 再点火 1.2 + warmup 不适用（失败前无图可暖）。

### 9.3 结果（四臂 × 5 seeds，全确定——逐 seed 相同）

| 条件 | 失败形态 | 首动 | 动作序 | n | red | success@ | birch 终态 |
|---|---|---|---|---|---|---|---|
| control（§8 基线） | 无失败（树在） | birch | birch→oak→craft | 3 | 0 | 9 | 5.0 封顶 |
| inhibit（§8） | 无失败（树在） | **oak** | oak→craft | 2 | 0 | 6 | 0.0 |
| **control_real** | 真实失败（树 at 首挖消失） | birch | birch→**birch(重试)**→oak→craft | 4 | **1** | 17 | 5.0 |
| **inhibit_real** | 真实失败 + on_failure 框架 | birch | birch→oak→craft | 3 | **0** | **15** | 4.21 |

事件框架落图时序（dbg 探针）：t0–t5 `armed=False wired=False eid_in_graph=False` → gather@birch 失败回执（timeout 结算）后下一拍 **`armed=True wired=True eid_in_graph=True`**——**落图严格发生在失败回执之后**（内源成型，非预注入）。

### 9.4 结论与差分归因

- **机制级**：失败回执 → on_settled 链 → 事件框架落图，链路完整可观测（5/5 全确定）。C20e 的预注入装配与 C20g 的内源成型装配**产出同一形态的事件框架**（节点/边/极性逐项相同），只差落图时机。
- **行为级（inhibit_real vs control_real 同世界对比）**：同一失败事件下，**有框架 15 tick 达成、零冗余重试；无框架 17 tick、失败后重试 birch 一次**（red=1）。差分与失败的一一对应精确：失败后的重试冲动被框架压掉一拍。
- **新机制事实（timeout 暂态账）**：真实失败回执形态为 timeout（despawn 后技能链走到超时结算），而 **timeout ∈ _TRANSIENT_WORLD_REASONS（autonomy.py:106）→ 统计通道（_attempts/冷却量）对此次失败完全静默**（fail_tick 探针恒 None 即指纹）。**行为差分（17→15）发生在统计层零记账的失败上，唯一留痕通道就是事件框架图侧**——这是"图经验 vs 统计经验"分立的直接实证：失败记忆走图通道在内源触发的真实失败上也成立。
- 论文口径再升级（限定的、如实的）：**"真实失败回执内源成型事件框架，且在其统计通道静默（timeout 暂态）时依然独力改变后续行动选择（行为级实证，D/C20g）"**——仍保留"沙箱装配层注入失败事件、非生产自愈"。剩余开放项：生产路径（真实 MC 失败离散热流）未测；失败强度（fail_side）与重试窗口的剂量-反应未扫。