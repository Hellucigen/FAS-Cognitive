# 神经调制系统 R2 — Phase 1 架构审计报告

日期：2026-09-21　范围：**只读审计**（本阶段未创建、未修改任何运行时代码或数据文件）
对象：调制器（激素）层及其与知识图谱 / 认知场 / 驱动 / 需求 / 行动 / 对话 / 学习回路的接缝

行号均为本会话直接读取核实；标注「代理审计」的条目来自并行只读子代理，我对其中承重部分做了抽查复核。
运行时图谱数字为本次实测（`data/runtime_graph.json`，最后写入 2026-09-21 09:24:55，FAS 当前**未运行**）。

---

## 0. 结论摘要

1. **你要的东西已经建了一半，而且建在你没点名的地方。** 09-19/20 那批未提交重构（`cognitive_field.py` / `modulation.py` / `reward.py`）已经把「调制器→参数→认知场」这条通道做成了**数据驱动**的形态：信号 = `hormone.<名>`，效力 = config 里的一行 coef。所以 R2 不该新建平行系统，而应**沿这条既有接缝扩到 12 个调制器 + 把每激素的 if/else 换成图边**。
2. **调制器目前不是图谱公民，是 16 座孤岛。** 4 个镜像节点（多巴胺样等）+ 4 需求 + 8 性格在图里 `activation=0`、**零条边**（实测：16/16 出边入边均为 0）。这是 `internal_state.sync_graph` 注释里写明的**刻意决定**（:499-501，怕扰动 Self 的扩散归一化），不是遗漏。§「Everything as Graph」对调制器这一层确实还没兑现。
3. **兑现它有一处硬约束。** 发射归一化是**按发射节点自己的出边**算的（`total_w` 只计正权出边，diffusion_engine.py:1006-1043）。所以
   - 新增 `调制器 → 目标` 边：**不稀释**任何既有节点，代价只在被点亮时的能量注入；
   - 新增 `Self → 调制器` 边：**会稀释** Self 现有 76 条出边的份额（实测 Σw⁺=49.305，加 12 条 0.4 边 ≈ 摊薄 8.9%）。
   推论（重要）：**出边全为负权的节点永不发射**（`if total_w > 0`），所以"纯抑制剂"型调制器（GABA 样）必须至少挂一条正权出边，否则在图上完全无效。
4. **建议采用双通道拆分（这是对 §21 字面要求的有意偏离，需你批准）**：
   - **张力/紧张度（tonic，慢分量）→ 参数通道**：`level−baseline` 进 `hormone.*` 信号 → ModulationLayer，改变 14 个认知场目标的连续系数。有界、不注入场能量、可复算。
   - **事件脉冲（phasic）→ 图谱通道**：把脉冲写成调制器节点的激活并登记为刺激源（`register_activation_source(..., "modulator")`，`source_type` 是自由字符串，实测无需改受保护核心），再经它自己的 `调制/抑制` 边参与扩散/抑制。稀疏（只在事件时发生，不每 tick 写），因此不会重演 09-13 的"图谱暴动"饱和。
5. **14 个认知场目标里 12 个无需碰受保护核心**即可接线；1 个（activation gain）实际由既有 `diffusion.param_gain` 承担（扩散侧唯一的调制读点，实测 :346-349 + :1261-1271）；1 个（diffusion inhibition）**不该做成公式旋钮，应做成图上的负权边**——这才是"作为图谱的一部分参与抑制"。
6. **必须先修的既有缺陷 8 条**（§2），其中 3 条是"写了没人读"的死码：血清素全链路无效力、`tonic` 无行为读者、2 个已注册参数无消费者。
7. **不新增第 4 个状态 JSON**：调制器数值继续住 `data/internal_state.json` 的 `modulators`；图谱只存语义/关系/符号/强度；`data/tension_drive_state.json`（认知场自动落盘目标）**当前磁盘上不存在**，属未启用路径。

---

## 1. 十个审计问题

### Q1 当前四个激素具体在哪里定义？

单一状态源 `internal_state.py`：

| 位置 | 内容 |
|---|---|
| `internal_state.py:41-54` | `MODULATOR_SPEC` —— dopamine `level/baseline=.50 decay_per_min=.02 split_tonic`、cortisol `.30/.05`、serotonin `.55/.01`、oxytocin `.40/.03`；**模块常量，不在 config**（违反 §24「集中管理」） |
| `internal_state.py:57-82 / 86-95` | `NEED_SPEC`（safety/exploration/competence/social）、`TRAIT_SPEC`（8 项，α=.005 band ±.15） |
| `internal_state.py:33` | 落盘路径 `data/internal_state.json`（唯一的内部状态文件） |
| `internal_state.py:479-482` | 16 个图镜像 id 映射（`dopamine→多巴胺样` 等） |
| `cognitive_field.py:280` + `config.py:614` | **第二份基线**（hormone_baseline）——与 MODULATOR_SPEC 重复，靠人工保持一致（漂移风险） |
| `drive_engine.py:54-55` | `_HORMONE_NAMES` 白名单，决定 provider 走"调制器通道"而非"张力加数" |
| `config.py:103-111` | `emotion_hormone_modulation`：情绪词 → 激素增量的现行事件表（焦虑/害怕→cortisol、开心/兴奋→dopamine、难过/沮丧→serotonin） |
| `index.html:1903-1918` | 前端 `renderInternalModulators` 按 `Object.keys` 遍历，需 `level/baseline/min/max`（数值）+ 可选 `desc/last_sources` → **加到 12 个自动渲染，无需改前端** |

结论：**定义分散在 4 处**（internal_state 的 SPEC、cognitive_field 的 baseline、config 的情绪表、drive_engine 的名字白名单），但**运行时数值唯一**归 `internal_state`。

### Q2 level 怎么更新？

- **唯一数值写入者 = `internal_state.apply_delta(kind, name, delta, reason, cycle_id)`（:275-312）**：夹到 `[min,max]`、写 `history`（环 200，不变量 I3）、更新 `last_update`、把 `reason` 塞进 `last_sources`（:303-304，上限 3 条 —— 这就是 §6 要的 `recent_sources`，已存在，只是没记幅度）。dopamine 若有 `split_tonic` 则同步 `tonic = level`（:305-306）。
- 生产侧调用者（代理审计 + 抽查）：
  - `reward.py:187-250` `release()` —— 事件→激素的主通道，见 Q5；
  - `app.py:2921-2933` 情绪共振 → `apply_delta("modulator", ...)`，表来自 `config["emotion_hormone_modulation"]`；
  - `internal_state.update_needs_from_signals`（:338-368）走 need 侧；
  - `POST /api/internal/modulator/<name>`（前端滑块 `index.html:1954`，reason="前端滑块调节"）—— 人工写入口，已存在。
- **一处破坏不变量**：`tick_decay`（:715-749）直接 `item[k] = ...` 写回，**绕过 `apply_delta`** → 衰减不产生 history 条目、不记 reason。R2 里所有时间演化都必须走单一写者（或显式给 decay 一个 reason 前缀）。

### Q3 衰减怎么做的？

`internal_state.tick_decay(dt_min)`（:715-749），由 `continuous_cognition._loop` 每 tick 调一次（`continuous_cognition.py:199-206`，tick=2.5s 实测默认）：

- **level 指数回归 baseline**：`level += (baseline-level) × decay_per_min × dt` —— 每激素 decay **已经不同**（.01~.05/min），§禁令 8（"都做成相同 decay"）目前未违反，R2 必须保持；
- **phasic 独立快衰减**：常数 `PHASIC_DECAY_PER_MIN = 0.30`（≈ 3.3 分钟半衰），全激素共用（现在只有 dopamine 有 phasic）；
- **没有** momentum / rise_rate 上限（slew clamp）/ saturation 软顶 / refractory 不应期 —— 这四项目前完全不存在；
- 图谱镜像侧另有一套衰减：节点 activation 由 `propagation_rules` 的 per-space λ 负责（self 空间 λ=0.01，**排得很慢** —— 这是"每 tick 给 12 个节点补激活"会饱和的原因，见 §3-D3）；
- 情绪衰减在另一层（`personality_baseline.py:92-100` mood valence 衰减），与调制器衰减互不相干（正确，二者时间尺度不同）。

### Q4 dopamine 的 tonic/phasic 是怎样的？

- **实现**：`split_tonic: true`（`internal_state.py:41-54`）→ `apply_delta` 每次把 `tonic` 同步成 `level`；`pulse_dopamine(delta, cycle_id)`（:695）只加 `phasic`，**不写 history、不进 level**；`level = clamp(tonic + phasic)` 的合成**当前代码里并不存在**（level 只被 `apply_delta` 改，phasic 是平行量）。
- **谁读它**：
  - `reward.py:272` 学习因子 `factor = 1 + .60·max(0,phasic) + .15·(level−baseline) − .25·max(0,cortisol−baseline)`，夹 `[0.4,1.8]`（:286）→ 传给 `disposition_store.apply_experience`（:581-722）缩放"一次经历写进倾向多少"；
  - `cognitive_field.py:386-398` `hormone.dopamine` 调制 curiosity 驱动上升/下降速率与 learning.rise；
  - `config.py:759 / 841`（= `cognitive_field.py:128 / 206`）两处 param effects：一个探索相关增益 +0.80、一个 `action.score_threshold` −0.08；
  - `reward.py:211` `release()` 里 RPE → `pulse_dopamine(rpe × PHASIC_SCALE=.25)`。
- **缺陷**：`tonic` 字段被 `snapshot/restore/dump` 搬来搬去（:447/:515/:563）但**没有任何行为读者**；`phasic → level` 的合成缺口意味着 phasic 不影响 `hormone.dopamine` 信号（该信号读的是 `modulator_level()`）。**推广 tonic/phasic 时正好把这两件事做实**（§3-D2）。

### Q5 哪些事件会改变激素？

现行是**两条并行的、硬编码的事件→激素通道**（正是 §7 要结构化的东西）：

| 通道 | 位置 | 语义 |
|---|---|---|
| 奖赏/自我评估 | `reward.py:36-47`（SOCIAL_VALENCE：accepted .50 / amused .60 / engaged .45 / continued_discussion .40 / recognized .30 / ignored −.15 / timeout −.10 / rejected −.50）、`:86-97`（SELF_VALENCE：discovery .80 / knowledge_gain .55 / completion .70 / goal_success .60 / progress .30 / novelty .40 / blocked −.20 / goal_failure −.50） | 值域表 |
| 释放（fan-out 写死） | `reward.py:187-250` | `PHASIC_SCALE=.25`、`SOCIAL_OXY=.06`、`NEG_CORT=.05`、`DOPA_LEVEL_POS=.04`、`DOPA_LEVEL_NEG=−.05`、`SERO_NEG=.02` —— **逐激素 if/else，§禁令 1 的现行实例** |
| 情绪共振 | `app.py:2921-2933` + `config.py:103-111` | 词表→增量（这条已是数据驱动，可作 `调制` 事件种子表范式） |
| 具身/世界 | `autonomy.py:599-668`（hostile/health/hunger → **need** 增量，非激素）；`app.py:1155-1186`（血量/食物/敌距/夜晚 → need provider 闭包） | 只到 need 层 |
| 时段 | `temporal_awareness.py:137-197`（bucket floor .6 / phase floor .5 / 跃迁脉冲 +1.5） | **只写图谱 activation，没有任何 circadian 信号进调制层**（Q 见下） |

事件源全景（代理审计，Q1-Q4 节）：真实存在的"总线"只有一条 —— `state_monitors.observe` → `cognitive_triggers.evaluate` → `cognitive_regulation._dispatch`（`cognitive_regulation.py:52-119`），且当前只有 poller 与 `autonomy._publish_state`(:1464-1478) 接在上面；另有 4 条**平行非订阅式**汇聚点：`experience.ExperienceTimeline`（零订阅者）、`continuous_cognition.note_pressure`（**字符串**标签 + 固定表 :77-82）、`graph_evolution_log`、`episodic_buffer`。**天然的 `emit_modulation_event()` 注入点排序**（代理结论，与我抽查一致）：`reward.release`（一切 social/self 已过值域/强度）> `action_system._settle/_emit_result_event`（:435/:546）> `expression_feedback.observe_response`（app.py:3646-3701）> `autonomy._on_action_settled`(:239) > 情绪共振 app.py:2921 > `prediction_baseline`（app.py:3245-3261 surprise≥0.35）> `temporal_awareness._emit_transition`(:200) > `minecraft_perception._unknown`(:128-133)。

### Q6 激素真的参与知识图谱激活吗？

**不参与。** 实测：

- 16 个镜像节点全部 `label="declarative-semantic"`, `graph_space="self"`, `weight=0.4`, `activation=0`, `extra_attrs.canon="internal_state"`，**出边入边均为 0**；全图 1894 条边里没有任何一条端点是它们；
- `sync_graph`（:496-551）只更新 `extra_attrs` + `touch()`，注释（:499-501）说明"刻意不建边，因为 Self 出边权重和直接影响扩散归一化"；
- 全图**只有 2 条负权边**（`用户-[相关]->远方出行计划 −0.31`、`用户-[喜欢]->苦 −0.50`），都不是调制器边 —— 图上目前**没有抑制性结构**在用；
- 对比：**Drive 是真的图谱公民**（`drive_engine.py:404-419`）：`CuriosityDrive` 实测 42 条入边、5 条出边（`驱动 → 行为:探索/追问/OBSERVE/APPROACH/EXPLORE`），`_apply_drive_activation`(:375-399) 把 level×5 写进 node.activation 并 `mark_active` + `register_activation_source("internal_drive")`；
- 还有一个可借的范式：网络 marker（:433-444 + `step_network_markers`:460-468）—— `CENetwork`/`DMNetwork` 实测 activation 2.77/0.32（level×5 直写，**未 `mark_active`**），且 CI_*/思考_* 节点确实 `基于/关于` 指向 CENetwork，即"认知事件引用网络态"已有先例。注意：`Self-[网络]->CEN/DMN` 的关系名 **`网络` 未注册在词表里，落盘时被静默折叠成 `关联`**（实测 + `graph_schema.normalize_relation` fallback）—— 这是"图谱公民化"必须先在 `relation_ontology` 注册的直接证据。
- 注册状态实测：`抑制 ✓causal`、`影响 ✓causal`、`导致 ✓`、`处于 ✓cognitive`、`驱动 ✓`、`激活 ✓`、`状态项 ✓`；**`调制` `增强` `调节` `有状态` `提升` `削弱` `网络` 全部未注册 → 会被折叠成 `关联`**。而 `graph_integrity_audit.py` 对 `unknown_relations` **exit 1**（:232-235）→ 未注册的新关系会直接红 CI。

### Q7 它们影响扩散、注意、行动、对话吗？

| 目标 | 现状 | 证据 |
|---|---|---|
| 扩散 | **有，但只有一个旋钮**：`diffusion.param_gain`（引擎侧唯一调制读点 `_mod` :326-335 + `update_param_modulation` :337-358，且只在 `_emission_budget` :1261-1271 生效）；CognitiveField 以 `context.arousal/stress` 汇入 | 实测 |
| 注意 | `attention.*` 参数组存在（`config.py:747-853` modulation.params），由 `continuous_cognition` 的 `_mod_th` 消费（:483-492/:502/:734）；但 **`retrieval.topk_scale` / `retrieval.reignite_every` 注册了却无消费者**（死参数） | 代理审计，抽查一致 |
| 行动 | `action.score_threshold`（effects：CEN −0.12、curiosity −0.06、**dopamine −0.08**、cortisol +0.10、task_engaged −0.05、user_recent +0.15）被 `autonomy._threshold()`（:1148-1159）消费；`behavior.exploration_rate` 乘在 novelty 权重上（:1309-1317）；打分式 `:1333-1343` | 代理审计 + 我复核 `_score_action` 结构 |
| 对话 | `dialogue_decision` 每候选 `act = W_PRIOR·prior + W_LEARN·(learned+live)·horm_n`（:203，`horm_n` 来自 `reward.modulation()` 因子，app.py:3723）；`behavior.explore_margin` 决定"探索要赢多少才去探索"（:388-400）；`llm.budget_factor`/`mode_nudge`/`temperature_*` 走参数通道 | 代理审计 |
| 直接读 internal_state 的行为侧 | `autonomy._motivation_value`（:1108-1118）**直接读 needs 字典**（不经调制层）；`app.py:1184` 直读 oxytocin | 抽查 |

结论：**激素→行为这条链是真的（4 个 effect 项 + 学习因子），但覆盖面小**（dopamine/cortisol/oxytocin 共 5 处 effects；**血清素 0 处**），并且绕开了图谱。

### Q8 与 need / drive / trait / disposition / mood 的关系？

- **need（4 个）**：与调制器**目前无连接**。need 由 `update_needs_from_signals`（provider 驱动，:338-368）与 `autonomy` 事件增量（:662-668）写；`need_urgency`（:431-433）**无生产消费者**（死接口）。§14 要的"多对多"要从 0 开始建。
- **drive**：通过图（正确方向）—— `CuriosityDrive` 是节点，`hormone.*` 改的是**驱动速率/亲和增益**（`cognitive_field.py:386-398`）而非分数加数（`drive_engine.py:54` 注释明确"激素走调制器通道，不进张力加数"）。这是 R2 要推广的既有正确范式。
- **disposition（行为倾向）**：`disposition_store.apply_experience`（:581-722）用 `reward.modulation()` 因子缩放写入量 —— 即"调制器→学习率"已存在，但**只有多巴胺/皮质醇两项参与**，且系数写在 `reward.py` 里（硬编码，R2 应搬到边/表）。
- **trait（8 项，慢变量）**：唯一生产写者 `action_system._learn_traits`（:622-642，经 `_TRAIT_FAMILY/_TRAIT_RULES`）→ `record_trait_evidence`（:378-404，α=.005、band ±.15、缓冲 20）。**没有任何调制器直写 trait 的路径** —— 这正是 §18/§28 要求的形状，保持不动。
- **mood**：住在 `personality_baseline.py`（瞬态、内存态、不落盘；`MOOD_EVENT_EFFECTS` :30），读者是 prompt 的【当前心情】（`app.py:3884` → `nlp_processor.py:1215-1219`）与 `autonomy:1126-1132`（social 动机）、`mood_valence` 还是 cognitive_field 的 provider（`app.py:1276`）。**mood → 调制器 目前只有一条**：`reward.py` 释放时顺带 `persona.mood_event`。**调制器 → mood 不存在**（§16 要的"可派生"是新增，且必须单向、有界，避免 mood 变万能变量）。
- **时间尺度现状**：phasic≈分（λ=.30/min）、modulator≈时（.01-.05/min）、mood≈小时-日（`personality_baseline` 衰减）、disposition≈日-周（候选→稳定门）、trait≈周-月（α=.005）。层级已经正确，R2 不能压缩它。

### Q9 哪些机制仍在图外跑 if/else？（禁令 1/2 的现行清单）

1. `reward.py:211-236` —— 逐激素分支（`if kind==...: pulse_dopamine / oxytocin += / cortisol += / serotonin −=`）**最典型的应被边表替换的硬编码**；
2. `reward.py:254-286` —— 学习因子的 4 个系数写在函数体里（`.60/.15/.25`）；
3. `internal_state.py:41-54 / 57-82 / 86-95` —— SPEC 常量不在 config（§24）；
4. `cognitive_field.py:386-398` —— `_rate_gains` 用字面量 key 名逐激素取增益（"哪个调制器影响哪个驱动"是代码结构，不是数据）；
5. `continuous_cognition.py:211-232` —— 每 tick 从情绪 activation 手算 `arousal`、从 cortisol 手算 `stress` 再回灌 context（**这条其实已经不错**：它只是 provider；但公式在循环里）；
6. `autonomy.py:1108-1118` —— 需求→动机的映射是 4 条硬编码 if（survival→safety+0.3 等），完全绕过调制器；
7. `config.py:103-111` —— 情绪→激素表（数据驱动 ✓，但**在图外**，且与 R2 事件表重复）；
8. `drive_engine.py:54-55` `_HORMONE_NAMES` —— 闭合名单式词汇（禁令 2 的边缘：决定 provider 走哪条通道的判据是一个 set 常量，而不是节点元数据）；
9. `internal_state.tick_decay:715-749` —— 演化公式在图外（这个可以留在图外：**数值演化本来就该在状态对象里**，见 §3-D1 的边界说明）。

### Q10 哪些可直接扩展，哪些必须重构？

**可直接扩展（不动结构）**

| 接缝 | 为什么现成 |
|---|---|
| `MODULATOR_SPEC` → 12 项 + config 化 | 纯表扩展；`register/_ensure_mirror/sync_graph/dump/前端` 全是遍历式（实测 `Object.keys`）；旧快照 `restore` 按 key 取（:179-180）→ **缺字段容错即可向后兼容** |
| `hormone.<名>` 信号 + `modulation.params` effects | `modulation.py` 的设计意图就是"新增参数 = config 加一行"（:11 注释）；`_build_signals` 遍历 modulators（`cognitive_field.py:421-431`）→ 12 个自动出信号，无需改结构 |
| `ModulationLayer.register(param, fn)` | 存在且**生产零注册者** —— 正是放非线性曲线（倒 U / 软饱和 / overload）的位置，零新机制 |
| `rate_gains_n` 恒等槽 | `_rate_gains` 算了但网络侧是 identity（我实测的未用接缝）→ §17「调制器连续偏置 DMN/CEN」的干净落点 |
| `set_signal_provider` / `set_context_provider` | `app.py:1262-1284`（激素）/ `:1318-1321`（语境）已是注册式；melatonin 的 circadian 输入就挂这里 |
| Drive 图谱公民范式 | `drive_engine.py:375-399/:404-419` 照抄即可（level×5 + mark_active + register_activation_source("modulator") + 幂等建边） |
| 关系词表 | 只需在 `config.relation_ontology` 加 `调制/增强/有状态/交互`（否则折叠 + CI exit 1） |
| 前端 / 快照 / 审计 | 零改动或仅需 metadata；`declarative-semantic` 的 label×space 不受限（实测 LABEL_SPACE_RULES） |

**必须重构（不是扩展）**

1. **事件→调制器扇出**：`reward.release()` 的逐激素分支 → 结构化 `ModulationEvent` + 边表决定扇出（§7）；
2. **tonic/phasic 推广 + 合成规则缺失**：定义 `level = clamp(tonic + 合成(phasic))` 并让 tonic 成为参数通道读者（顺带修 Q4 的"tonic 无人读"）；
3. **时间动力学**：加 momentum / rise_rate slew / saturation 软顶 / refractory，并让 `tick_decay` 走单一写者（修 Q2 的 history 缺口）；
4. **血清素的去处**：现在它只有值没有效力（grep 核实 `hormone.serotonin` 零出现）→ 要么给它两三项真实工作（建议：编码稳定性 / 反应抑制 / 拒绝敏感度），要么诚实承认多余；
5. **`_HORMONE_NAMES` 闭合名单** → 改判据为节点 `extra_attrs.category/type`（消除禁令 2 的边缘实例）；
6. **重复基线**（internal_state SPEC vs `config.cognitive_field.hormone_baseline`）→ 唯一源 = config，另一处只读。

---

## 2. 已核实的落差与死代码（R2 顺手账）

| # | 事实（均本会话核实） | 处置建议 |
|---|---|---|
| D-1 | `hormone.serotonin` 在所有 effects 表中零出现 → 血清素等级变化**不改变任何行为** | ✅ **P6 已闭，P11 复核**：投影出 2 行系数（闸门 F1），且 `serotonin -[增强 w=+0.30]-> 心情` 是包络里唯一张力性主导项（附一一 §6） |
| D-2 | `tonic` 无任何行为读者；`level` 不含 phasic | ✅ **P3 已修**：`tonic` 成为唯一真值、`level` 成为派生视图；参数通道（`hormone.*`、`context.stress`、`reward.modulation()` 的 tonic 项）全部改读 `modulator_tonic()/modulator_dev()`，脉冲不再被算两遍（附三） |
| D-3 | `retrieval.topk_scale`、`retrieval.reignite_every` 注册无消费 | ✅ **P8 已闭，P11 复核**：`continuous_cognition.py` 两处真消费者（闸门 F3 盯住源码）；`_mood_band` 因此刻意**不**做成 config 旋钮——不留"假旋钮"是这条账定的规矩（附一一） |
| D-4 | effects 里 `need.social`（`cognitive_field.py:200`）**无信号生产者**（`_build_signals` 只产 5 个前缀）→ 恒乘 0 | ✅ **P9 已闭，P11 复核**：`_build_signals` 产 `need.*`（闸门 F4 用正则盯生产者） |
| D-5 | `config.action_tendencies` 4 项中 3 项仅展示用 | ⏸ **待用户裁定**（附一一 §6）：接进行为打分＝改自主决策数值，R2 不擅自选；或把注释改成"派生展示量" |
| D-6 | `tick_decay` 绕过 `apply_delta` → 无 history/reason | ✅ **P2-P3 已修**：衰减/吸收都经 `_write_numeric`（唯一入口），同源同向 5 分钟窗口内合并成一条历史（20 拍衰减只留 2 条，真实事件不被挤出 —— 有断言） |
| D-7 | `Self-[网络]->CEN/DMN` 落盘为 `关联`（关系未注册） | ✅ **P11 已闭**：`网络` 注册进 `relation_ontology` 并加入 `HUB_ALLOWED["Self"]`（warn-only 守卫，动力学一字不动；闸门 F5 既查注册也查新装配的边真的保住关系名）。存量那两条**不改**（load 不走规范化） |
| D-8 | `internal_state.need_urgency()` 无生产消费者 | ⏸ **已定性，待裁定去留**（附一一 §6）：`urgency` **字段**有真读者（`need_salience()` 的 max 口径，闸门 F8）；那个**访问器**仍零生产调用。删／留作调试读面，请裁定 |
| D-9 | `data/tension_drive_state.json`（`cognitive_field` 自动落盘目标，`config.py:608-633`）磁盘上不存在 → 该持久化路径实际未启用 | ⏸ **P11 改判**：路径是**活代码**（装配时 `_load_state()` + 每 `max(2,autosave_steps)` 步 `save_state()`），只是日志停在 09-14、重构在 09-20，生产还没跑满 24 拍。它存的是张力/驱动/EMA 连续性，不是语义事实，**不是 R2 造的平行状态库**；是否退役改由图谱+快照承载＝架构级裁定，R2 只登记不动 |
| D-10 | `note_pressure("novel_experience")` 定义但无 emit 点（代理审计） | ✅ **P11 已闭**：emit 点在 `continuous_cognition.py` 采纳焦点处，判据复用已算好的 `novelty`，阈值 `novel_min_novelty=0.5` 在 config（闸门 F6） |
| D-11 | `unexpected_event` 张力的唯一来源 `graph.cognitive_events` provider 未注册（代理审计） | ✅ **P11 已闭**：注册第 8 个图采样器，读 `事件类型:prediction_violation` 的 activation/5；实测点亮后张力从 0 抬起（闸门 F7） |
| D-12 | `app.py:3896` 一带：`hormone="dopamine" if drive == "explore" else ...` —— 把激素名当成**对话标签**凭空赋给一个认知语境条目（该轮并没有读过任何激素读数） | ✅ **已闭**：全仓无 `hormone="dopamine"` 式凭空标签残留（P11 复核）；读数走真实通道 |
| D-13 | `config.modulator_system` 里的 7 个键**零消费者**（grep 全仓除定义处无命中）：`link_to_self`/`link_relation`/`link_weight`（P4/P6 的 Self 锚定边）、`graph_pulse`/`graph_pulse_threshold`/`graph_pulse_scale`/`graph_pulse_source`（phasic→图激活）。它们是 P2 迁移规格时**提前写下的占位开关**，今天改它们没有任何效果 | ✅ **P6 已闭**：`link_to_self/link_relation/link_weight` 由 `ensure_modulator_subgraph` 消费（A4/A11 实测：开=12 条、关=0 条），`graph_pulse/graph_pulse_threshold/graph_pulse_scale/graph_pulse_source` 由 `InternalState._graph_pulse` 消费（D2/D7 实测：关=零注入、阈值下=零注入）。教训留在原话里：**"开关存在"≠"机制已生效"** |
| D-14 | 默认值矛盾（文档 vs config）：§3-D3 原写作"锚定边默认**关闭**（`modulator_link_to_self: false`）"，config 实际是 `link_to_self: True` | 用户裁定是"**默认建，留开关**" ⇒ 以 config 为准，D3 文本已按裁定更正；键名以 `modulator_system.link_to_self` 为准 |
| D-15 | **调制器镜像节点是孤岛**（P4 边级实测，只读 `data/runtime_graph.json`，1077 节点/1894 边）：`多巴胺样/皮质醇样/血清素样/催产素样` 各自**出边 0 条、入边 0 条**；`调制目标:*` 节点 **0 个**；Self 有 76 条出边（正权和 49.305，其中 `处于` 仅 1 条，不指向任何调制器）。这就是 Q6"激素真的参与图谱激活吗？"的**否**的直接证据：属性写进节点 ≠ 图上可达 | ✅ **P6 已闭**（附六）：**1099 节点 = 1077 存量 + 8 个 P4 镜像 + 14 个靶点**；**1955 边 = 1894（P4 时）+ 49 调制边 + 12 锚定边**；12 个调制器**全部有正权出边**（实测 0.20~1.55）；一次多巴胺脉冲沿边点亮 `调制目标:扩散发射配比 0.766 / CuriosityDrive 0.287 / 调制目标:探索速率 0.192 / 行为:分享 0.144` —— Q6 的答案从"否"变成"是"，且注入当轮总能量 4.46、200 轮后排空 |
| D-18 | **P6 顺带撞见的存量矛盾（不在 R2 范围内，未动）**：`data/runtime_graph.json` 里两条既存的 `抑制` 边带**正权重**——`附近的生物 -[抑制 +0.50]-> 能力:建造`、`-[抑制 +0.40]-> 能力:农耕`（种子来自 `config.capability_graph.inhibitions`，`capability_graph.py:170-177` 原样写入）。同一关系词在两个消费者那里意思相反：能力召回侧只看**边在不在**（`cap_inhibitors`，不看符号），扩散侧看**符号**（负=抑制），于是这两条边在扩散里其实是**兴奋**了能力节点 | 需用户裁定：把种子权重改负（改能力召回的既有动力学，需跑 bench 指纹）／或 `capability_graph` 按关系词取反（改代码不改数据）。P6 只在**新写的调制边**上强制"增强>0/抑制<0"（A5 + P5 测试 E 组），存量数据一字未动 |
| D-16 | `dump_modulator_state(top=8)` 的默认值在 R2 有 12 个调制器后**藏掉 4 个**（按 \|dev\| 截断），而"被藏起来的是不是在睡大觉"正是该函数要回答的问题 | ✅ **P4 已修**：默认全给 + 新增 `shown` 字段，显式传整数才截断（全仓 grep：生产代码零调用者，只有两个测试传 `top=`） |
| D-17 | 前端激素滑杆绑的是 `m.level`（响应视图），但 `/api/internal/modulator/<name>` → `set_value` 写的是**浓度真值**：饱和/过载区两者分家（附三实测 浓度 .99 ⇒ 响应 .618），于是滑杆把"松手处的响应值"当成浓度写回去——**读响应、写浓度**，是真 bug 不是显示问题 | ✅ **P4 已修**：滑杆改绑 `conc`，同时显示"响应/浓度"（差 >0.02 标"曲线压缩中"）；路由 docstring 写明 `level` 只是历史契约名。index.html 两段内联 `<script>` 均过 `node --check` |

---

## 3. R2 设计决定（D1–D8）

**D1 — 状态住在对象里，语义住在图里（混合式，非纯图）**
`ModulatorState` 继续是 `internal_state` 内的 dict/对象，**唯一写者仍是 `apply_delta`**（不变量 I1/I2/I3 不破）。图谱承载：谁（modulator 节点）—对什么（`调制/抑制/增强/交互` 边）—带什么符号与强度（relation + weight）。数值演化（衰减/动量/饱和）**留在对象里**——它是常微分方程，不是关系事实；把它塞进图只会造出"图外公式 + 图内抄本"的双份真相。

**D2 — 双通道：tonic→参数，phasic→图激活**（需批准，见 §6-A1）
- 参数通道：`dev = tonic − baseline`（血清素类非 split 者用 `level − baseline`）→ `hormone.<名>` 信号 → ModulationLayer。**有界、不注入能量、每 tick 都算但只是乘法**。
- 图通道：`pulse` 超阈值时把脉冲写成调制器节点 activation（`level×5` 风格的 Drive 范式）+ `mark_active` + `register_activation_source([id], "modulator")`，经其出边参与扩散/抑制。**只在事件时发生**（稀疏），并受既有发射预算与能量守恒约束。理由：每 tick 给 12 个 self 空间节点补激活＝外置能量泵，self 空间 λ=0.01 排得极慢，正是 09-13 饱和事故（`diffusion-energy-conservation`）的形状。
- 结构约束：**每个调制器节点至少 1 条正权出边**，否则（纯负边）永不发射＝图上无效（实测 `if total_w > 0`）。GABA 样因此需要"正边到 `调制目标:反应抑制`" + "负边到 CEN/行为概念"的组合。

**D3 — 图是系数的真源（回应禁令 2 的关键设计）**
新增 `调制目标:*` 基础设施节点族（label=`infrastructure` → `is_cognitive_visible=False`，实测不会污染 Top-K/回答区；与既有 `能力:*`/`行为:*` 同构，实测图里 92 个 infrastructure 节点、无此族 → 新建）：14 个认知场目标各一个节点。
边：`多巴胺样 -[调制 w=+0.80]-> 调制目标:探索速率`；`GABA样 -[抑制 w=−0.30]-> 调制目标:扩散增益`。
一个 **投影器**（不是规则引擎，无 `if 激素>0.6`）在引导时/图谱变更时把边表编译成 `(param, signal=hormone.<名>) → coef` 行，交给既有 `ModulationLayer`；~~符号由 relation 决定~~ **P6 实测改为"符号以权重为准"**（扩散引擎读的就是权重，若两者各说各话，图通道与参数通道会反号）：投影取 `weight × sensitivity`，同时把"关系词与权重符号矛盾"的边报告成 `sign_mismatch`（`增强` 必正、`抑制` 必负，`调制` 符号写在权重上）。强度=|weight|×调制器 `sensitivity`。
闭合词汇只剩**关系本体 4 个词**（`调制/抑制/增强/交互`），拓扑与强度全部是图上的可查、可改、可可视化数据。`drive_engine._HORMONE_NAMES` 的判据同时改成节点 `extra_attrs`。
⚠️ 撞键限制（P6 实测出来的）：`effects` 表按**信号键**索引，同一调制器对同一参数写两条边只能留一条——投影保留 |系数| 大的那条并报告 `conflicts`，**不按遍历顺序静默挑**（config 边表因此删掉了一条重复的 `oxytocin-[增强]->表达阈值`）。
Self 锚定边（`Self-[处于]->调制器`，`处于` 已在 hub 白名单内）按用户裁定 **默认建、留开关**：
`config.modulator_system.link_to_self = True`、`link_relation = "处于"`、`link_weight = 0.20`。
代价是摊薄 Self 的正权出边份额——**P6 实测**：49.305（76 条出边）→ **51.705（88 条）**，
其它 Self 出边的归一化份额被压掉 **4.6%**；`link_to_self=False` 时零条锚定边（测试 A11）。
 bench 指纹实测**未变**（仍 130 处差异，与 R2 之前同一批）：合成 bench 图里没有调制器节点、
也不发脉冲，所以它测不到这条新通道——图通道的动力学由 `tests/test_modulator_subgraph.py` 的
E/G 组在**真实运行图**上锁（附六）。

**D4 — 事件管线：`ModulationEvent` + 图上扇出**
`ModulationEvent(event_type, source, valence, intensity, novelty, uncertainty, social_relevance, goal_relevance, success, timestamp)`（dataclass，纯数据）。
扇出由图决定：`事件类型节点 -[影响 {gain}]-> 调制器节点` 边表（`影响` 已注册 causal ✓）+ 门控条件（`requires novelty>θ` 之类）放在事件类型节点的 `extra_attrs`（Node 有 extra_attrs，Edge 没有 —— 实测），由通用应用器读取。**没有新 JSON 状态库**：事件表是图数据，配置默认值在 `config.modulator_system.event_rules` 里，启动时幂等 upsert 进图（照 `ensure_drive_signal_edges` / `ensure_competition_edges` 的既有做法）。
`reward.release()` 改为**构造事件 + 交给 `ModulatorEngine.apply_event`**，其 `.25/.06/.05/.04/−.05/.02` 常数迁入边权重；`config.emotion_hormone_modulation` 迁入同一事件表（消除重复表）。数值写入仍一律经 `internal_state.apply_delta`。

**D5 — 非线性 & overload 连续化**
用 `ModulationLayer.register(param, fn)`（现零注册者）注册通用曲线工厂：`make_inverted_u(peak, hardness)` / `soft_saturation(cap)` / `refractory_gate`，形状参数来自 `config.modulator_system.specs.<名>`，**不是 per-hormone 代码**。low/normal/high/overload 由 `dev` 经 tanh/sigmoid 连续映射；overload 表现为该项贡献反号或衰减（例：多巴胺过高 → 探索增益转负＝"过度承诺"）。
`cognitive_field.py:119-217` 的 4 段式判据（若有）一并核查替换。

**D6 — melatonin 由 circadian 语境驱动（§11）**
`temporal_awareness` 已产出图谱侧时间事实（bucket/phase 节点、`当前作息感知` 状态节点、`发生于时段` 边、跃迁事件；`IDEAL_SLEEP 23–7` 是**节点属性**）。R2 新增一个 provider：`context.vigilance` / `circadian_phase` 从**图谱里的时段节点属性**推导（`current_bucket()` + 该节点 `extra_attrs`），经事件表升 `melatonin`、抑 `histamine`；**不在代码里比较 `datetime.now().hour`**。跃迁事件（`_emit_transition`）是天然 `ModulationEvent("time_bucket_changed")` 注入点。

**D7 — mood 保持独立，且只允许一条派生边（§16）**
`mood = f(Σ 有界调制器 dev)` 作为**可选、小权重、单向**输入进 `personality_baseline`；四轴继续分开：mood=感觉如何、modulator=处在什么调制态、trait=平常是哪样、disposition=此处倾向怎么做。禁止 mood 反向进参数通道当万能变量（现在 `mood_valence` 已是 tension provider —— 保留，因为它走的是 drive 侧，不是激素侧）。

**D8 — 长闭环只有一条（§18/§28）**
`调制器 → 行为选择 → 结果 → reward → disposition → (慢) trait`。trait 写者仍然只有 `_learn_traits`，α 仍 .005、band 仍 ±.15。R2 加**护栏测试**：把某调制器钉在高位跑 N 个周期，断言 trait 位移小于给定界（防"快变量污染慢变量"）。时间尺度表见 §5。

---

## 4. 提议的 12 个调制器规格（FAS 仿真参数，非医学数据）

现有 4 个数值**原样保留**（§25 迁移要求）；其余 8 个为新增建议值。单位：`decay_per_min` 指数回归速率，`rise_per_min` 上升 slew 上限，`refractory_min` 不应期，`overload` 软饱和拐点。

| 名 | category | UI 名 | base | decay | rise | momentum | sens | saturation/overload | refr | 必做的工作（它必须真实改变的东西） |
|---|---|---|---|---|---|---|---|---|---|---|
| dopamine | monoamine | 多巴胺样 | .50 | .020 | .12 | .30 | 1.0 | .85 / .95 | 0.5 | RPE 脉冲、探索速率、学习因子、行动门槛↓、编码增益 |
| norepinephrine | monoamine | 去甲肾上腺素样 | .45 | .040 | .15 | .25 | 1.1 | .80 / .92 | 1.0 | 注意阈值↑、网络惯性（CEN 起落 slew）、意外→意图切换/打断 |
| acetylcholine | cholinergic※ | 乙酰胆碱样 | .50 | .045 | .10 | .20 | 1.0 | .85 / .95 | 0 | 信号保真：precision 加权、检索广度、不确定性敏感度 |
| cortisol | hormone | 皮质醇样 | .30 | .050 | .06 | .10 | 1.0 | .75 / .90 | 0 | 张力上升速率、学习钝化、探索抑制、睡眠压力协作 |
| adrenaline | hormone | 肾上腺素样 | .20 | .120 | .30 | .15 | 1.3 | .60 / .80 | 3.0 | 行动 urgency、快速反应偏置、危险/抢占通道（短时高衰减） |
| serotonin | monoamine | 血清素样 | .55 | .010 | .04 | .10 | .9 | .80 / .95 | 0 | **稳定态**：拒绝敏感度、反应抑制、情绪韧性（修 D-1：目前零效力） |
| oxytocin | neuropeptide | 催产素样 | .40 | .030 | .05 | .15 | 1.0 | .80 / .95 | 0 | 社交 salience、亲和驱动增益、信任→检索社会知识广度 |
| endorphin | neuropeptide | 内啡肽样 | .30 | .060 | .08 | .20 | 1.1 | .70 / .85 | 10 | 压力/挫折钝化（负 valence 衰减）、持续/persistence、过载缓冲 |
| GABA | inhibitory | GABA 样 | .55 | .015 | .05 | .10 | .9 | .85 / .95 | 0 | 全局抑制偏置：扩散抑制、竞争 margin、防 runaway（**须带正出边否则图上无效**） |
| glutamate | excitatory | 谷氨酸样 | .50 | .080 | .10 | .15 | 1.0 | .80 / .90 | 0 | 激活增益（经 param_gain）、扩散增益、编码写入强度 |
| histamine | monoamine | 组胺样 | .45 | .025 | .06 | .10 | .9 | .75 / .90 | 0 | 清醒度/警觉基线、空闲→主动行为倾向、LLM 认知预算上限 |
| melatonin | circadian | 褪黑素样 | .20 | .030 | .02 | .05 | 1.0 | .70 / .85 | 0 | 睡眠压力、夜间检索/反思偏置、行动 urgency↓、预算↓；**只由 circadian 语境驱动**（§11） |

※ 需在你给的 7 类之外补一个 `cholinergic`（ACh 既非 monoamine 也非 excitatory/inhibitory 的调制类；不硬塞错类别）。`metabolic` 保留不用 —— 与其为凑数分配，不如留空。

## 5. 字段→作用 表（§6：每个字段都必须真的做一件事）

「状态」列是 **P3 落地后**的实测事实（2026-09-21）：`已落地` = 本轮已有真实消费者并有断言；
`待 P#` = 设计位置已定、消费者还没写（不许假装它在起作用）。

| 字段（config 键名） | 谁读它（真实消费者） | 状态 |
|---|---|---|
| baseline | 一切偏差的唯一参照；`decay` 的回归目标；`saturation/overload` 换算成相对距离的零点；与 `cognitive_field` 的 `hormone.*` 减法配对（基线表已合并，只此一份） | 已落地 |
| level | **派生响应视图** = `clamp(baseline + receptor((tonic−baseline) + phasic_to_level·phasic))`，只由 `_compose_view()` 写；图镜像属性、前端、`dump` 读它；`modulator_level()` 现算不读缓存（旁路改 phasic 也看不到过期值） | 已落地 |
| tonic | **张力性浓度 = 唯一真值**（不拆快通道的调制器也是它）；参数通道 `modulator_tonic() = baseline + receptor(tonic−baseline)` → `hormone.*` 信号 → ModulationLayer；`context.stress`（continuous_cognition）也改读它（修 D-2） | 已落地 |
| phasic | 只由 `pulse()` 动；合成进 level 视图（体验态）；学习因子（`reward.modulation()` 的 phasic 项）；**P6 才接图谱激活注入**（D3 双通道） | 部分（图通道待 P6） |
| momentum | 一阶低通 `mv ← mv·m + g·(1−m)`；效力除数 `1/(1+max(0, mv·g))` ⇒ **同向重复习惯化、反向保满**；`tick_decay` 里按 `MOMENTUM_DECAY_PER_MIN=.15` 退场 | 已落地 |
| rise_per_min | 写入侧 slew 夹速 `cap = old + rise·Δt`；被夹掉的余量进 `pending_rise`，`tick_decay` 每拍按速率吸收回 tonic（§13 累积：持续事件压力确实抬水位，但要花时间）。回落后不夹（只管上升），`decay/manual/restore/rise` 免夹 | 已落地（原计划"P7 才有消费者"提前实现） |
| decay_per_min | 回 baseline 的指数速率（**12 个各不相同**，禁令 8；测试直接断言互异 + 落点排序） | 已落地 |
| phasic_decay_per_min | phasic 回 0 的速率（与慢分量分家，`None` → `PHASIC_DECAY_PER_MIN=.30`） | 已落地 |
| sensitivity | `pulse()` 的增益乘子（adrenaline 1.3 / serotonin 0.9）；**P7 起可被 `交互` 边动态改变** | 已落地（边控待 P7） |
| saturation | 受体曲线拐点 S（=saturation−baseline）：u≤S 满灵敏度线性；u>S 起 `φ=1/(1+x²)` 压缩，x=(u−S)/(O−S) | 已落地 |
| overload | 曲线峰值点 O：**响应斜率在 u=O 处正好为 0** ⇒ 配置写的"过载点"就是"再多也没用"的那个点，`overload_pull` 只决定过了它以后掉多快（pull=0 仍因 φ 缓降）。最大响应 = `S+(O−S)/2`，与 max 无关 ⇒ 高浓度永不等于高效力 | 已落地 |
| refractory_min | 脉冲后不应期：增益 ×(1−剩余比例)，期内连打同一名调制器打不动；`refractory_left_s` 可观测；`reset_modulators()` 一并清零 | 已落地 |
| phasic_to_level | 快分量折算进视图的比例（默认 1.0；测试用 0.5 证明它真的参与合成） | 已落地 |
| category | 进图镜像属性与 `dump`，并且是 P6 边表的分组依据（同类别共享靶点族的出厂拓扑） | 已落地（P6） |
| circadian_driven | 标记位（出厂只有 melatonin）：`ModulatorEngine.apply_event` 拦掉非 `source=="circadian"` 的事件对它的作用并记入 `gated`；水位由 `apply_circadian_drift`（图上时段/昼夜边）驱动 | 已落地（P10，附一〇 §5） |
| split_tonic | 决定该调制器**有没有快通道**（`pulse()` 走 phasic 还是走带习惯化的慢位移）+ `reset` 时是否清 phasic | 已落地 |
| mirror | 图谱镜像节点 id（旧 4 个中文 id 逐字保留） | 已落地（P4 建节点） |
| last_update | `tick_decay` 的 dt 来源；`last_slow_ts` 单独记慢写入时刻（夹速用，不被脉冲污染） | 已落地 |
| recent_sources / source_detail | 可解释性：`[MODULATION]` trace、`dump_modulator_state()`、前端"最近来源"（上限 `recent_sources_keep=5`，带 delta 与 source） | 已落地 |


## 6. 时间尺度层级（§27/§28 必须保持）

| 层 | 现状半衰/步长 | R2 归属 |
|---|---|---|
| phasic | `PHASIC_DECAY_PER_MIN=.30` ≈ 3.3 min | 调制器（秒-分） |
| modulator level/tonic | .01–.12/min ⇒ ≈6–70 min | 调制器（分-时） |
| mood | 内存瞬态，事件驱动衰减 | 独立层（时-日） |
| disposition | candidate→stable 门 + `CANDIDATE_CAP` | 独立层（日-周） |
| trait | α=.005、band ±.15、缓冲 20 | 独立层（周-月，**禁止加速**） |
| tick | CC tick 2.5s；驱动评估缓存 5s；autonomy 4 tick≈10s | 演化时钟（R2 调制 tick 挂 `continuous_cognition.py:211-232` 现有位点） |

---

## 7. 阶段计划与验收闸门

| 阶段 | 内容 | 闸门（全绿才进下一阶段） |
|---|---|---|
| P1 | 本报告 ✓ | 人工评审 + 批准 §8 的 5 项决定 |
| P2 ✅ | **单一写者收口**：`tick_decay` 走统一写入口；SPEC 迁 `config.modulator_system.specs`（internal_state 读 config，内置兜底）；消除双基线 | 落地证据见"附：回归闸门现状"。（计划里的 `tests/test_modulator_core.py` **未单独建**：P2 的收口由既有 `test_internal_state.py` + P3 的 `test_modulator_dynamics.py` 覆盖，不再补重复文件） |
| P3 ✅ | **`ModulatorState` 全字段 + 动力学**（momentum/rise/saturation/refractory/tonic-phasic 合成）+ `dump_modulator_state()` + `[MODULATION]` trace | `tests/test_modulator_dynamics.py` 71 条断言全过（半衰互异、饱和/过载峰值位置、不应期衰减、习惯化、上升夹速+吸收、历史合并、旧快照迁移、奖赏管线不双计）；不变量 I1/I2/I3 测试仍过；见"附三" |
| P4 ✅ | **12 个调制器 + 8 个新镜像节点**（复用旧 4 个 id，命名式 `X样`；`label=declarative-semantic`、`graph_space=self`、`extra_attrs{type:modulator,category}`） | `tests/test_modulator_graph_presence.py` **42 条全过**（P6 时加了 2 条"接线后 sync 仍不改拓扑"的交互断言，并把 fixture 改成"剪掉调制接线后再测"，见附六）；真实图 1077→1085 节点、边 1894 不变；审计逐项计数**一字不差**（附四）。⚠️ 计划里的 "audit exit 0" 这条闸门 R2 之前就没满足（2 label×space + 42 错配 + 22 复合命名，均需用户裁定，见 §8），P4 只做到"不新增" |
| P5 ✅ | **关系注册**：`调制/增强/交互`（`抑制/影响` 已在）进 `config.relation_ontology` | `tests/test_modulator_relations.py` 23 条全过（词表闭合、归一往返幂等、方向语义、13 个表面形式收敛、存量归一 11 条逐字不变、四类边可写且符号约定自洽）；审计器**非规范关系仍为 0**（附五） |
| P6 ✅ | **图谱公民化**：`调制器 →(调制/抑制/增强)→ 调制目标:*/Drive/CEN/DMN/行为概念/需求镜像` 幂等 bootstrap（`modulator_subgraph.ensure_modulator_subgraph`）+ D3 投影器 + 每节点 ≥1 正出边 + phasic→图激活 | `tests/test_modulator_subgraph.py` **47 条全过**（A 幂等/开关/缺端点 11、B 投影正确性 7、C 图是真源 6、D 图通道 8、E 能量 7、G 传播 4、F 孤岛关闭 4）；真实运行图 1077→1099 节点、1894→1955 边；一次脉冲沿边点亮 3 个 `调制目标:*`；2000 轮长护栏见附六表。`test_energy_conservation.py` 仍全绿；`bench_diffusion --compare` **仍 130 处**（合成 bench 图没有调制器、不发脉冲 ⇒ 它测不到这条新通道，**无需重批基线**）；审计器新增项为 0（关系种类 74→76 是调制边入册，单次边 13→12） |
| P7 ✅ | **事件管线**：`ModulationEvent` + `ModulatorEngine.apply_event`（扇出读边表）；`reward.release`、情绪共振、action settle、prediction_error、time_bucket_changed、social_feedback 逐个改接 | `tests/test_modulation_events.py` **54 条全过**（A bootstrap 12、B 迁移无损 11、C 图是真源 8、D 门控/通道/夹幅 7、E 交互边 5、F 来源接线 6、G 边界 5）；`reward.release()` 内已无逐激素 if/else，六个写死常数迁入 `config.modulator_system.event_rules`（→ 图上 `事件类型:* -[影响 w]-> 调制器`）；`reward.modulation()` 兼容层保留（学习调制仍按原公式）。**time_bucket_changed 留给 P10**（它要有靶点才有意义）。真实启动 +14 类事件 +23 影响边（活图 1099/1961 → 1113/1988）；`run_tests` 59/59；bench 仍 130 处；审计器非规范关系仍 0。详见**附七** |
| P8 ✅ | **14 个认知场目标接线**（§8）+ 倒 U/overload 走到参数。**D5 的落地方式与计划不同**：参数侧不再挂第二条曲线（同一段过载会被算两遍——曲线已在 `modulator_dev` 施加一次），而是给 `overload_pull` 定义**无量纲单位**并逐个填值，使 12 条曲线的过载支真的走得到；曾实现 `ModulationLayer.register` 合力护栏，实测最坏激素合力仅 0.50（不足以推到边界）⇒ **删掉**，结论写进 `modulation.py` 头注。D-3 两个假旋钮（`retrieval.topk_scale`/`reignite_every`）接上真消费者 | `tests/test_modulation_targets.py` **80 条全过**（A 真实读者 15、B 单调有界 17、C 过载反转 14、D 图权威 29、E 三处一致 5）：14/14 靶点都有生产读者、14/14 都有激素系数边、12/12 调制器的过载支在**参数值**上可见回落、删边后参数对激素完全无感且加回来逐字复原。`run_tests` **60/60 / 44s**；verify_p0 exit 0；bench **仍 130 处同一批**；审计器**新增问题 0**。详见**附八** |
| P9 ✅ | **need 多对多 + drive 偏置 + DMN/CEN 连续偏置**（用 `rate_gains_n` 恒等槽；无固定模式切换）。三条通道语义分清：`rate_gains`=惯性（稳态不变）/`affinity`=张力权重（需张力在场）/`bias`=稳态水位（图上边，出不了 ±`bias_cap`）；`NEED_SPEC.modulators` 死表删除（D-1）、`need.*` 幽灵信号补生产者（D-4）、`urgency` 第一次有消费者（D-8，动机表驱动） | `tests/test_need_modulation_coupling.py` **45 条全过**（名册/无损/兜底一致 8、连续偏置+源码扫描 9、需求位移与 600 拍长回路 6、三通道分工 7、`need.*` 4、动机表 3、唯一写入口 6）。实测：全基线 `edges_used=0` 且偏置全空；NE+褪黑素高位 ⇒ CEN 0.0914→0.181、DMN 0.3436→0.5036（`capped=[]`）；顶格处偏置**变号**（倒 U 第一次进到网络稳态）。`run_tests --gates` **61/61 / 51s**；verify_p0 exit 0；bench **仍 130 处同一批**；审计器**新增问题 0**。踩到两个坑（`config={}` 丢增益表 ⇒ 补 `DEFAULT_RATE_GAINS` 兜底；合成时钟会静默关掉衰减 ⇒ P10 要先立统一时基）。详见**附九** |
| P10 ✅ | **melatonin/histamine 接 circadian**（时段节点属性 → provider，不硬编码小时）：`BUCKET_CIRCADIAN` 是全仓唯一"小时→事实"翻译点；`circadian_load`（知识）/`circadian_strength`（此刻在势，唯一作者=时钟）分开存；`SALIENCE_READERS` 类型注册表 + `_apply_state_drift` **一条实现**同时服务需求侧与昼夜侧；慢通道 6 条边 + 快通道带符号事件；`circadian_driven` 第一次有消费者；`InternalState.set_clock()/_now()` 统一时基（附九 6② 收口，也是 P12 实验模式的地基） | `tests/test_circadian_modulators.py`（新增）**44 条全过**：主闸门是**两次独立装配的曲线逐点 `==`**（同一 bucket 序列 ⇒ 同一褪黑素样/组胺样曲线，含迁移序列与事件流相等）+ 源码扫描确认调制侧无 `.hour`/小时比较/第二张昼夜表、`circadian_strength` 只有一个作者。实测 7 个昼夜：褪黑素样 昼 0.1020 / 夜 0.6643，组胺样 夜 0.1620 / 昼 0.7020（都不撞量程、不越 overload）。`run_tests` **62/62 / 57s**；verify_p0 exit 0；bench **仍 130 处同一批**；审计器**新增问题 0**。顺手修了 P6 fixture（改用生产同一个 `ensure_bucket_nodes` 补真依赖）。详见**附一〇** |
| P11 ✅ | **mood 派生（单向有界）+ 结果→调制器闭环收口 + 死代码账 §2 清完**：心情 = `clamp(事件余韵 + Σ(边权×dev), ±0.35→±1)`，权重是图上的 5 条 `调制器 -[w]-> 心情` 边（`mood_projection` 只读编译，与 P9 偏置共用同一个 `dev`）；`心情` 锚点节点在 self 空间、**出边恒 0**（单向性写在图上）；`reward.modulation()` 的六个常数迁入 `config.modulator_system.learning_modulation`；人格侧 decay/事件偏移迁入 `config.personality.mood`，时基与调制器共用（新增公开 `InternalState.now()`）。D-1/D-2/D-3/D-4 复核为已闭（证据在附一一 F 组），D-7 注册 `网络` 关系、D-10 补 `novel_experience` 的 emit 点、D-11 注册 `graph.cognitive_events` 采样器，D-5/D-8/D-9 明确处置 | `tests/test_mood_one_way.py`（新增）**45 条全过**。循环性用三个独立角度钉：① 反复读心情 + 记事件后**全状态指纹一字不动**（12 通道 tonic/phasic + 4 需求 + 全图激活 + 边数）；② `心情` 出边为 0（结构上无反哺路径）；③ 源码扫描 `personality_baseline.py` 无任何调制器/事件写入口。另有 B 组证明"顶格钉满 ≠ 最大贡献"（过载反转互相抵消，实测 raw 只到 0.029）与包络被 `cap` 钳住的独立断言。`run_tests` **63/63 / 57s**；verify_p0 全部通过；bench **仍 130 处同一批**（心情通道不碰扩散）；审计器**新增问题 0**（77 种关系、42 类别错配、2 label×space 一字未变）；曲线扫描 12 条仍全有效。详见**附一一** |
| P12 ✅ | **17 个场景测试 + 实验/调试模式 + 回归（按用户指示精简）**：`modulator_lab.py` 实验台=**薄驱动器**（不实现任何调制逻辑：emit / evaluate+release / set_value(source=manual) / 逐块重放 CC 状态拍；唯一新状态=虚拟时钟偏移，close 原样交还）；app 六个 `/api/internal/lab/*` 转调路由；`dump_modulator_state()` 补 `mood_envelope` 一行（交接③，只报投影不报合计，避免第二个心情数值所有权）；`tests/test_r2_scenarios.py` 17 场景全走生产装配形状。详见**附一二** | `test_r2_scenarios` **17/17 · 71 断言全过**（含台1 "lab.inject≡engine.emit 逐位相同"、台2 "advance(90)≡手工重放 18 拍"、台7 "实验台不长新边"）；P11 闸门 `test_mood_one_way` 复跑 **45 项全过**；编译检查过。全量 `run_tests`/bench/完整性审计**未重跑**（用户指示精简回归；P12 未触碰扩散数学与图谱存取路径） |

**17 个场景**（§29；必须走真实管线，禁测试期硬编码 —— §26）
1 奖赏脉冲→dopamine phasic→图激活；2 RPE 为负→phasic<0→学习因子<1；3 连续成功→饱和+不应期→增益递减；4 社会拒绝→oxytocin↓/cortisol↑→社交 salience 降；5 新奇→ACh/glutamate→编码增益升；6 不确定→NE/ACh→检索广度升；7 夜间 melatonin↑→urgency/预算↓；8 过载（多巴胺钉高）→探索倒 U 转负；9 GABA↑→扩散抑制+竞争 margin↑；10 endorphin 缓冲→负事件钝化；11 压力（cortisol 持续）→学习钝化+DMN 下降；12 空闲+histamine→自主探索发起；13 需求（口渴式）→调制偏置→行动选择变化；14 意图被打断→NE phasic→抢占；15 结果→disposition 位移→（慢）trait 位移受限；16 长期高多巴胺→trait 仍慢（护栏）；17 图谱边权改写→效力随之改变（图是真源）。

**回归命令集**（现状：**没有一条命令跑全套件**，R2 顺手建 `scripts/run_tests.py`）
`tests/test_energy_conservation.py`、`scripts/bench_diffusion.py --compare scripts/bench_baseline.json`、`graph_integrity_audit.py`、`scripts/verify_p0_all.py`、`tests/test_internal_state.py`、`tests/test_disposition.py`、`tests/test_dialogue_decision.py`、`tests/test_continuous_cognition.py`、新增 3 个调制器测试文件。

---

## 8. 需要你批准/裁定的 6 件事

| # | 事项 | 我的建议 |
|---|---|---|
| A1 | **双通道拆分**（tonic→参数、phasic→图激活）＝对 §21「激素必须参与激活/扩散」字面的有意偏离（每 tick 把 12 个慢衰减 self 节点补激活会重演 09-13 饱和） | 采纳拆分；理由与证据见 §0.4 / D2 |
| A2 | **是否建 `Self-[处于]->调制器` 锚定边**（语义上 hub 白名单允许；实测会把 Self 的 76 条正出边份额摊薄 ≈8.9%，改变现有扩散轨迹） | 默认 `false`，留 config 开关； introspection 靠 phasic 点亮节点本身即可 |
| A3 | **是否允许新增 `调制目标:*` infrastructure 节点族 + 4 个关系词**（约 14 节点 / 60–90 边；不动受保护代码，但图谱规模变化） | 允许；这是"图当系数真源"的最小实现 |
| A4 | **受保护核心**：本设计**零改动** `diffusion_engine` / `graph_model` / 扩散数学（`register_activation_source` 的 `source_type` 是自由字符串，实测无需登记新类型）。若 §8 的 activation gain / diffusion inhibition 要做成新旋钮，就必须动核心 | 不动核心；两个目标按 §0.5 折叠/改道 |
| A5 | **未提交的 09-19/20 重构**（`cognitive_field/modulation/reward/…` 共 30+ 文件在工作区，另有 `.zcode/` 第三方工具目录与多个未跟踪新模块）——R2 全部建立在它们之上 | 建议先提交一批可运行的 checkpoint（含 `bench_baseline.json` 重批说明），再开 R2 分支；否则回滚边界不清 |
| A6 | **§2 的 11 条既有缺陷**：顺手修（D-1 血清素给工作、D-2 tonic 做实、D-3/D-4 死参数与幽灵信号、D-6 衰减走单一写者…）还是另开一轮 | 顺手修 D-1/D-2/D-4/D-6（都在改动半径内），其余列 TODO |

---

## 9. 风险登记（诚实版）

1. **bench 指纹是行为契约**：`bench_diffusion --compare` 用合成图（seed 42）做严格逐数值指纹。P6 之后 phasic 注入会改变真实运行时的激活轨迹；合成基准不含调制器节点时可能仍全绿，一旦有节点被点亮进 frontier 就**必然漂**。漂了必须显式重批基线并说明原因，禁止悄悄 `--save`。
2. **能量守恒**：任何直写 activation 的新点都必须 `mark_active`（既有不变量），且必须只走事件驱动、且受 emission budget 约束。空闲长跑护栏不是可选项。
3. **规模**：+12 节点、+14 参数节点、+60~90 边 → 扩散 frontier 略增；投影表每步 O(边数)，可忽略。真正的每轮延迟仍由 LLM 主导（实测既有结论）。
4. **双份真相风险**：`level×5` 图镜像与对象态之间必然存在一拍延迟（与 Drive/CEN 现状相同）。投影器只在 bootstrap 与"图谱显式变更"时重建系数表，避免每 tick 扫图。
5. **词汇膨胀**：4 个新关系词 + 14 个 `调制目标:*` 会让 `graph_integrity_audit` 的"单次关系"统计变难看（不影响 exit code，`single_use` 只是打印项）。
6. **可解释性债务**：12 个调制器 × 14 个目标 = 168 条可能边。默认**只建 35–45 条**有语义理由的边，其余留空——宁可稀疏诚实，不做满配假象。

---

### 附：回归闸门现状（P2 落地后实测，2026-09-21）

| 闸门 | 结果 | 归因 |
|---|---|---|
| `tests/test_*.py` 全套（54 个文件） | **54 通过 / 0 失败** | P2 无回归 |
| `scripts/verify_p0_all.py` | 通过 | — |
| `scripts/bench_diffusion.py --compare scripts/bench_baseline.json` | **失败（exit 1）**：round3/4 `act_sum` 1201→165 等大差 | **早于 P2**：基线文件是 9 月 3 日生成的（`git log` = f5279ac），而 09-19/20 的架构对齐**故意**改了扩散数学（`_edge_w_eff` 改加性 + 发射守恒 2868→12）。bench 只 import `graph_model/diffusion_engine/config`，不碰本次改的任何模块。→ 需要一次**显式重批基线**（不是悄悄 `--save`） |
| `graph_integrity_audit.py` | **失败（exit 1）**：`label×space` 矛盾 2 条（`Fascinator`、`Minecraft` = `declarative-episodic` 落在 `semantic` 空间） | **早于 P2**（运行图由 09-19/20 代码写出）。另有 42 条 `产生` 边类别错配（config 里 `产生` 键重复，后者 causal 胜出）与 22 个"复合命名"节点 —— 后者正是论文规范的 `{活动}—{槽}—{实体}` 事件名（`饮用—对象—奶茶`），**审计规则与命名规范互相打架**，需要单独裁定 |

> 这两项在 R2 之前就是红的，P2 既没修好也没弄坏。P6 之前必须先把基线重批与这 3 条治理冲突处理掉，否则每阶段都要重新解释一遍"红是不是我造成的"。


### 附二：本会话实测数据（可复算）

`data/runtime_graph.json`：1077 节点 / 1894 边；`Self` 出 76 / 入 7、正权出边和 49.305；16 个 internal_state 镜像节点**边数 0**；全图负权边 2 条；不同关系词 74 种；`CENetwork` activation 2.7725、`DMNetwork` 0.3201（`Self-[网络]->` 已折叠为 `关联`）；infrastructure 92 个，无 `参数:*` 族。
`data/internal_state.json`：`cycle_seq 120`，最后写 09:24:55；`data/tension_drive_state.json` **不存在**。
FAS 进程：**未运行**（无 python 进程、:5000 无监听）。

### 附三：P3 落地记录（时间动力学真的起作用，2026-09-21）

**改了哪几个文件**（全部只在工作区，未提交）

| 文件 | 改了什么 |
|---|---|
| `internal_state.py` | 存储语义与动力学收口：`tonic` 成为**唯一真值**、`level` 降为派生响应视图（`_view_of/_compose_view`，读接口现算）；新增 `receptor_response()`（模块级纯函数）、`_apply_momentum()`、上升夹速 + `pending_rise`、`modulator_tonic/conc/pulse/dev`、`modulator_dynamics()`；`pulse()` 成为所有调制器的唯一脉冲入口（sensitivity→momentum→refractory→合成→trace），`pulse_dopamine()` 退化为一行转调；`tick_decay()` 一条 tick 内做四件事（phasic 回 0 / pending 吸收 / 习惯化退场 / tonic 回基线）；`reset_modulators()` 连余量-习惯化-不应期一起清；`restore()` 按真值（tonic+phasic）回滚而不是按视图；`_load()` 对 `tonic=null` 的旧快照做回落迁移 |
| `config.py` | `melatonin.decay_per_min .03 → .035`：12 个调制器的 decay 从此**两两互异**（禁令 8 现在有断言把守，撞值就是回归） |
| `reward.py` | `modulation()` 不再从 `state()` 里挑字段，改走接口：phasic→`modulator_pulse()`、tonic 项→`modulator_tonic()`（**关键：level 现在已含 phasic，再读 level 就是把同一个脉冲算两遍**） |
| `app.py` | 激素 provider 循环 `("dopamine","serotonin","cortisol")` → `internal_state.modulator_names()`（12 个全部接到调制器通道），读数改 `modulator_tonic`。⚠️ P2 的记录里写了这一步"已完成"，实际当时未落地，本轮才真正改上 |
| `continuous_cognition.py` | `context.stress` 从"自己算 `level − baseline`"改为读 `modulator_dev("cortisol")`（曲线后的慢分量偏移），少一处图外重复算术 |
| `cognitive_field.py` / `drive_engine.py` | **本轮未改**（P2 的基线合并与开放词表收口保持原样）；语义上 `hormone.*` 现在拿到的是"已过受体曲线的慢分量" |

**曲线的第二次修正（重要，因为它曾经是个摆设）**
第一版写作 `S + S·ln(1+(u−S)/S) − pull·(u−O)²`：斜率在 u=O 处仍为正，转折点在 O 之外，对 adrenaline（b=.20 / sat=.60 / over=.80）算出来峰值在 u≈0.95 > 量程上限 0.8 —— **过载支永远走不到，配置里的 overload 就是装饰**。改为
`φ(u) = 1/(1+x²)`、`x = max(0,(u−S)/(O−S))`、`r = S + (u−S)φ(u) − pull·max(0,u−O)²`，
斜率 `(1−x²)/(1+x²)² − 2·pull·(u−O)` 在 **u=O 处正好为 0**：配置写的过载点=响应峰值点，`overload_pull` 只管过峰后掉多快（=0 也仍因 φ 缓降），且最大响应恒等于 `S+(O−S)/2`（与 max 无关）。测试把这三条钉住：峰值位置、峰后单调下行、天花板有限。

**实测闸门（本轮亲自跑过）**

| 闸门 | 结果 |
|---|---|
| `tests/test_modulator_dynamics.py`（新建，71 条断言 / A–G 七组） | **exit 0，71/71** |
| `tests/test_*.py` 全套（55 个文件） | **55 通过 / 0 失败** |
| `scripts/verify_p0_all.py` | 通过（"结果: 全部通过 ✔"） |
| `scripts/bench_diffusion.py --compare` | **仍 exit 1，与 P3 无关**：bench 只 import `graph_model/diffusion_engine/config`；`diffusion_engine.py` 里 `modulator_system`、`hormone` 两个关键字**零命中**（grep 无输出），本轮也没动扩散数学。红的成因仍是"9/3 的基线 vs 9/19-20 的扩散对齐" |
| `graph_integrity_audit.py` | **仍 exit 1，逐项计数与 P2 相同**：未知关系 0、`label×space` 矛盾 2（Fascinator/Minecraft）、`产生` 类别错配 42、复合命名 22（论文事件名）、日期节点 0 → P3 没新增也没有解决任何治理项 |

**断言语义变更：0 条。** `test_internal_state.py:84-88`（"多巴胺 level 与 tonic 一致"）在新语义下仍然成立，因为那条用例的 phasic=0 且传 `config={}`（曲线关闭 ⇒ 视图恒等于浓度）；它现在同时检验了"回退路径逐字不变"。

**还没做的（别把"有 12 个调制器"读成"12 个都在起作用"）**
1. **phasic 仍未进图谱**（图激活注入是 P6）——现在脉冲影响的是 level 视图与学习因子，不改激活轨迹；也因此 bench 指纹不动是**设计后果**，不是运气。
2. `category` 只进图镜像属性与 dump；`circadian_driven` **零消费者**。melatonin/histamine 目前没有任何生产者，所以它们会一直停在出厂水位（.20/.45）直到 P10 把时段节点属性接成 provider。这是当前实现事实，写在这里免得被误读成"昼夜调制已经生效"。
3. `ModulationLayer.register()` 仍零注册：曲线现在落在调制器侧（`receptor_response`），参数侧的倒 U/软饱和留给 P8 的 14 个目标。
4. `state()` 已同时暴露 `conc` 与 `level`，但**前端还是只画 level 一根条**：人工把浓度拉到过载点之外时，界面显示的响应反而回落——看起来像"滑杆失灵"。要么前端补第二根条，要么在调试台标注（P12 一并处理）。

### 附三·补：轨迹（trace）两处真缺陷的修正（同日收尾）

为了确认"日志真的能解释一次调制"，把四类事件跑了一遍真实管线（临时目录、`logging.DEBUG`），
发现两个缺陷，都改了；两处都只动日志与返回值字段名，**不触碰扩散与图谱**。

1. **慢写完全没有轨迹**。`[MODULATION]` 原来只在 `pulse()` 的拆分分支里发，于是
   `apply_delta`/`set_value` 造成的皮质醇上升在日志里隐身——而 P3 之后所有慢分量写入的
   唯一落地点是 `_write_numeric`，因此把轨迹收到那里（单一落地点 = 单一轨迹点）。
   衰减与余量吸收每拍都有，降为 DEBUG 免刷屏，事件级写入保持 INFO。
   同时删掉 `pulse()` 未拆分分支里那次重复打印（同一次写入曾出两行）。
2. **`refr=` 的含义反直觉**。原字段是"不应期剩余比例"，但它离开窗口时保持默认 `1.0`，
   于是 `refr=1.00` 同时表示"不在不应期"和"刚进不应期"——一串两义。改为两个显式字段：
   `refractory_left`（窗口剩余比例，0=不在不应期）与 `refractory_damping`
   （**实际施加的增益比例**，1=全额生效），日志打 `damp:`。返回值里的
   `refractory_frac` 由这两个取代（全仓 grep 确认无外部消费者）。

实测输出（不是示例，是那次冒烟的真日志）：

```
INFO   [MODULATION] mod=cortisol  event=假设：持续噪音压力 source=stress tonic:+0.200 phasic:—      level:0.30→0.50
INFO   [MODULATION] mod=dopamine  event=假设：RPE 正向意外 source=reward tonic:+0.000 phasic:+0.300 level:0.50→0.80 damp:1.00
INFO   [MODULATION] mod=dopamine  event=假设：同事件重复   source=reward tonic:+0.000 phasic:+0.000 level:0.80→0.80 damp:0.00
INFO   [MODULATION] mod=serotonin event=假设：稳定社交反馈 source=social tonic:+0.090 phasic:—      level:0.55→0.64
DEBUG  [MODULATION] mod=cortisol  event=自然衰减回基线     source=decay  tonic:-0.100 phasic:—      level:0.50→0.40
```

第 2、3 行是**不应期真的在起作用**的证据：多巴胺 `refractory_min=5`，同一事件立刻连打第二次增益被压到 0。
第 4 行是"未拆分调制器的脉冲 = 一次带习惯化的慢位移"：0.1 经 sensitivity/动量折成 0.09。
`phasic:—` 表示该调制器没有快通道（`split_tonic=false`），不是数值缺失。

顺手核实的一件事：调制器英文名是 **`norepinephrine`**（不是 noradrenaline），
12 个名字的顺序与 §4 规格表一致 —— `dopamine, norepinephrine, acetylcholine, cortisol,
adrenaline, serotonin, oxytocin, endorphin, gaba, glutamate, histamine, melatonin`。

收尾后重跑（本轮亲自跑的）：`test_modulator_dynamics.py` 全过、
`tests/test_*.py` **55 / 0 失败**、`verify_p0_all.py` 通过、
`bench_diffusion --compare` 仍 exit 1（130 处差异，对 9/3 基线，成因同上）、
`graph_integrity_audit.py` 仍 exit 1（运行时 1077 节点、非法 label 0、
`label×space` 矛盾 2、`产生` 错配 42、复合命名 22）—— 与前一行表格逐项一致。

---

## 附四：P4 落地记录（12 个调制器都是图谱上的点，2026-09-21）

**结论先说**：P4 **不需要写新的生产代码**——P2/P3 将规格收进 `config` 并把
`mirror_modulator` 改为"由规格派生"之后，`sync_graph()` 已经在遍历 12 个调制器并
`_ensure_mirror` 建点（`internal_state.py:395` 启动即调）。所以 P4 的真实产出是
**一个把这件事钉住的回归测试 + 三项实测**，而不是新机制。这正是"先理解现有架构再改"
的正面例子：如果按最初的直觉去写 `ensure_modulator_nodes()`，就会造出第二个建点路径。

**实测（只读真实图谱副本，全程不写 `data/runtime_graph.json`）**

| 项 | 基线 | 补点后 |
|---|---|---|
| 节点数 | 1077 | **1085**（+8：去甲肾上腺素样、乙酰胆碱样、肾上腺素样、内啡肽样、GABA样、谷氨酸样、组胺样、褪黑素样） |
| 边数 | 1894 | **1894**（P4 刻意零建边） |
| 关系种类 | 74 | 74（未新增关系 ⇒ P5 仍待做） |
| 审计：非规范关系 / 复合命名 / label×space 矛盾 / 非法 label | 0 / 22 / 2 / 0 | **0 / 22 / 2 / 0（一字不差）** |
| Self 正权出边和 | 49.305（76 条出边，其中 `处于` 1 条） | 49.305（未变） |
| `调制目标:*` 节点 | 0 | 0（P6 才建） |
| 孤立（零出边且零入边）的调制器节点 | 4 | **12** ← 本轮唯一变差的数字 |

最后一行必须写在脸上：P4 让"图谱公民化"在**结构上**完成，但 12 个点仍是孤岛，
`graph_integrity_audit` 的可达性统计里它们谁都到不了。这是 §2 **D-15** 记的账，
P6（子图边 + 投影 + phasic 注入）才是把孤岛接进动力学的阶段。现在断言这一条
（测试 C 组）反而有价值：P6 落地后那条断言会**故意失败**，逼当时的实现更新契约，
避免"边悄悄加了，但没人说过为什么"。

**新增测试**：`tests/test_modulator_graph_presence.py`（A–F 六组 **40 条**，exit 0）
- A 存在性与形态（12 齐、旧 4 id 逐字、增量正好 8、label/space/category/desc）；
- B 属性是活的（镜像 level == `modulator_level()`；脉冲抬 level 但**不碰 tonic**；慢写抬皮质醇；`state()` 与节点属性同数）；
- C 幂等与零副作用（重复 sync 不加也不换对象、不加边、不动 Self 出边、无 `调制目标:*`）；
- D 存量图与回退（真实图可 load 并镜像；`VALID_LABELS` 未改；`config={}` 的老环境仍出兜底 4 个）；
- E 前端契约（`/state` 12 条、每条有 level/baseline/min/max/desc、`conc` 另给、整体可 JSON 序列化）；
- F 命名（12 个 mirror 显式写在 config、互不相同、运行时名单==配置名单）。

**顺手两处真缺陷（都不是"为测试改产品"，而是产品自身的可观测性漏洞）**
1. `dump_modulator_state(top=8)` 默认值把 12 个调制器藏了 4 个——而"被藏起来的是不是在睡大觉"
   恰是这函数要回答的问题。改为默认 **全给**，新增 `shown` 字段；显式传整数才截断。
   （全仓 grep：生产代码零调用者，只有两个测试传了 `top=`，改默认无外部影响。）
2. 前端滑杆绑的是 `m.level`（响应视图），但写接口 `set_value("modulator", …)` 写的是**浓度真值**。
   在饱和/过载区两者会分家：浓度拉到 0.99 时响应反而从 0.680 掉到 0.618（附三实测过），
   滑杆却把松手处的 0.68 当成新浓度写回去——**读的是响应、写的是浓度，是个真 bug**。
   现在滑杆绑 `conc` 并同时显示"响应 / 浓度"两个数（差 >0.02 时标"曲线压缩中"）。
   `index.html` 两段 `<script>` 均过 `node --check`（76366 字符那块整体 OK）。

**闸门现状**：全套件 **56 个文件**（新增 P4 测试）全过 / 0 失败；`verify_p0_all.py` 过；
`graph_integrity_audit.py` 仍 exit 1 但逐项计数与上表相同；`bench_diffusion --compare` 仍 exit 1，
**130 处差异——与 P3 收尾时同一数字**，即 P4 确实没有改变任何激活轨迹（复跑实测，非推断）。

---

## 附五：P5 落地记录（调制关系词注册，闭合小词表，2026-09-21）

**做了什么**：`config.relation_ontology` 增 `调制 / 增强 / 交互`（都归 `causal_relation`；
`抑制`、`影响` 早已在册，实测图上分别在用 2 条 / 9 条），`relation_direction` 增
`交互: bidirectional`，`relation_synonyms` 增 13 个表面形式（调控/调节/modulates/调制作用 → 调制；
易化/上调/potentiates/enhances → 增强；inhibits/抑制作用 → 抑制；
相互作用/协同/interacts_with → 交互）。**没有新机制、没有新代码路径**——P6 的投影器才用这些词。

**词表是闭合的（禁令 2 的正解）**：调制子系统允许的关系词只有这 5 个。闭合的是**词**，
不是映射表——哪条边、什么符号、多强都是图上的数据。测试 A 组反向钉住：本体里
**不得**再出现"激素/神经调/多巴胺/皮质醇"这类新造关系词。

**符号约定（D3 的落地前提）**：`增强` 权重必为正、`抑制` 必为负（测试 E 组自洽断言）；
`调制` 允许符号写在权重里；`交互` 双向且可带符号（协同为正、拮抗为负）。

**顺手记的两件事实**
1. **注册是加法，不是改写**：11 条存量归一（含 `强化→导致`、`在→位于`、`关注→注意`、
   兜底 `自由文本→关联`）逐字不变。特别注意 `"强化": "导致"` 是存量既成事实，
   所以调制语境要写 `增强`，**不能**改这条映射（改了历史边含义会漂移）。
2. **`处于` 是个坑，已经钉住**：同义表里存着 `"处于": "位于"`，而 `处于` 本身在
   `relation_ontology` 里是规范词 ⇒ 规范词优先，映射当前被遮蔽（实测被遮蔽的历史键共 4 个：
   处于→位于、针对→关于、需要→需要、影响→影响）。P6 的锚定边正是 `Self-[处于]->调制器`：
   今天安全，但哪天有人把 `处于` 从本体删掉，那些边会一夜之间变成**空间关系** `位于`。
   测试 D 组加了一条断言专门守这件事。

**闸门（P5 后重跑）**：全套件 **57 个文件**（新增 `tests/test_modulator_relations.py`，23 条）
全过 / 0 失败；`graph_integrity_audit.py` 逐项与 P4 表**一字不差**
（74 种关系 / 单次 13 / 非规范 **0** / 类别错配 42 / 复合 22 / 非法 label 0 / 矛盾 2），
`bench_diffusion --compare` 仍 130 处差异——注册关系词不动动力学，符合预期。

---

## 附六：P6 落地记录（调制器成为图谱公民；边是系数的真源，2026-09-21）

### 落地的四个部件

| 部件 | 位置 | 干了什么 |
|---|---|---|
| bootstrap | `modulator_subgraph.ensure_modulator_subgraph()` | 幂等种入 **14 个 `调制目标:*`**（label=`infrastructure`、space=`cognitive`、`extra_attrs.param` 挂真实参数键）+ **49 条边** + **12 条 `Self-[处于]->`** 锚定边。端点缺失只跳过并报告，**不为此造点** |
| 投影器 | 同文件 `projection()` / `project_coefficients()` | 只读 `调制器 -[调制/增强/抑制]-> 调制目标:X` 这一类边，编译成 `ModulationLayer` 的 `hormone.<名>` 系数行：`coef = weight × sensitivity`。**先回收图上已不存在的激素行再写**（否则层里的旧系数成了看不见的第二真源） |
| 图通道 | `InternalState._graph_pulse()`（在 `pulse()` 里调用） | phasic 事件按 Drive 范式点亮镜像节点：`activation=min(5, \|phasic\|×scale)`、`mark_active`、`register_activation_source([id], "modulator")`。**只在事件里发生**，`tick_decay` 不调 |
| 装配 | `app.py`（`drive_evaluator.bootstrap_drives()` 之后） | 一行 bootstrap + 一行投影，失败只降级不崩；日志 `[Modulation] 调制子图：靶点+14 边+49 锚定+12 跳过0；投影 30 条边→30 行系数（回收 0）` |

### 关键实测（都是本机跑出来的数）

**迁移无损**：R2 之前 `config.modulation.params` 里的 **7 行 `hormone.*` 出厂系数**，
在投影之后逐字复现（B3，误差 <1e-9）。之所以能精确，是因为带既有系数的三个调制器
（dopamine/cortisol/oxytocin）的 `sensitivity` 都是 1.0 —— 投影公式退化为权重本身。

**参数通道的规模**：49 条边里 **30 条**打在 `调制目标:*` 上 → 30 行系数，覆盖 14 个靶点参数；
其余 19 条是图通道边（`CENetwork/DMNetwork/*Drive/行为:*`）与需求→调制器边，不产系数。

**Self 摊薄（P4 时是预期，P6 是实测）**：正权出边和 49.305 → **51.705**（76 → 88 条出边），
其它 Self 出边的归一化份额被压掉 **4.6%**。锚定边走 `link_to_self` 开关，关掉即零条（A11）。

**Q6 的答案真的变了**：一次多巴胺脉冲（phasic 0.6 → 注入 3.0）沿边扩散一轮后点亮

```
多巴胺样 2.97 → 调制目标:扩散发射配比 0.766 → CuriosityDrive 0.287
                → 调制目标:探索速率 0.192 → 行为:分享 0.144 → 调制目标:认知预算 0.096
当轮总能量 4.455（6 个节点），200 轮后 <0.5 排空
```

——这是"激素参与图谱激活"第一次有可复现的证据。在此之前它只写在节点属性里（P4 的孤岛）。

**能量守恒没被新通道破坏**（真实 1099 节点运行图，`decay_step+diffuse_step+clear_anchors` 为一轮）：

| 事件密度 | 2000 轮总能量峰值 | 结束时 | 钉在上限的节点数峰值 |
|---|---|---|---|
| 空闲（零事件） | 4.86（载入残值） | **0.000** | 0 |
| 每 50 轮一个事件（40 个） | 9.32 | 1.36 | 0 |
| 每 10 轮一个事件（200 个，**超出真实生活**） | 16.66 | 12.06 | **1**（被反复点亮的调制器自己） |

第三行是**诚实的坏消息**：密集到每轮都有事件时，会有一个节点顶到 5.0 上限。这与 09-13 的
"图谱暴动"（374 个节点钉在上限、每轮统计完全相同）**不是同一件事**——总量仍有界、
停手即排空，钉住的只是持续注入源本身。护栏按这个实测写死：E6 断言峰值 <40、E7 断言满格 ≤2。

**图是真源，可逆可测**（C 组）：改边权重 → 系数跟着变；删边 → 系数被**回收**（且非激素信号行
`network./tension.` 原位不动）；把边加回去 → 系数复原。这是 §29 场景 17 的机器可验版本。

### 五处"顺手撞见"，都记了账

1. **`effects` 表装不下两条同参数边**（oxytocin 原本同时有 `-[抑制 -0.12]->表达阈值` 与
   `-[增强 +0.05]->表达阈值`）：投影会按遍历顺序静默留一条。改法是**config 删掉重复那条**
   （它的理由"正出边"已由 `SocialDrive/行为:共情` 满足）+ 投影器报告 `conflicts` 并固定保留
   |系数| 大的那条（C5）。拓扑错误因此现形，而不是变成一个说不清的系数。
2. **adrenaline 原本只有两条负权出边** → 按扩散语义（`if total_w > 0` 才发射）它在图上等于不存在，
   那两条抑制边也都是死边。补了 `-[增强 +0.20]-> 调制目标:扩散增益`（应急聚焦），
   并把这条规则写进 config 边表注释与 `no_positive_out` 报告（B1/G4 双向守着）。
3. **`ModulationLayer.__init__` 只抄顶层 dict**，`effects` 子表与 `config.DEFAULT_CONFIG` 共享——
   投影器一写就会**就地污染全局配置**（后建的层读到前一个实例写的系数）。已在 `modulation.py`
   修（effects 单独 copy），B7 断言"新层的 `action.score_threshold` 不含 melatonin 行"。
4. **`kg.add_edge` 对已存在的三元组是 `max(weight)` 合并**（`graph_model.py:451-458`）——
   所以"改 config 权重"永远不会覆盖已经在图上的边。bootstrap 因此**只种新边、不刷旧边**
   （图是权威的另一半含义：出厂表是拓扑的起点，不是每轮重申的命令）。想改效力就改图。
5. **§2 D-18**（未动，需裁定）：运行图里两条**存量** `抑制` 边带正权重
   （`附近的生物 -[抑制 +0.50]-> 能力:建造`、`+0.40 -> 能力:农耕`，种子来自
   `config.capability_graph.inhibitions`）。能力召回侧只看边在不在，扩散侧看符号 ⇒
   这两条边在扩散里其实是**兴奋**。P6 没改任何存量数据（改了会动能力召回动力学）。

### 一个必须说清的越界（以及它连带改的一条测试约定）

验证装配时用 `import app` 跑了一次真实启动（14.1s，无异常，上面那行 `[Modulation]` 日志即证据）。
副作用：**`data/runtime_graph.json` 被启动流程写盘**，从 1077/1894 变成 1099/1961
（P6 的 22 点/61 边 + 其它 bootstrap 的 6 条边）。该文件是 gitignore 的运行时数据，
种入也是幂等的——但这是**用户本地图谱的一次真实写入**，不是只读验证。

写完之后的下一个后果立刻出现：`tests/test_modulator_graph_presence.py`（P4 的闸门）
**读的就是这个文件**，它的"增量正好 8 个节点 / 调制器没有出边 / 图里没有 `调制目标:*`"
四条断言当场变红。这不是 P4  regression，而是**测试与生产写路径耦合**：一条断言"接线前形状"
的测试不能依赖活的运行时图。修法是把 fixture 变成显式的"接线前"状态——复制之后
先剪掉 `调制目标:*` 族、8 个新镜像、以及任何调制关系词（`调制/增强/抑制/交互/处于`）的边，
旧 4 个镜像**留点**（D 组要测的正是"存量只有 4 个 → sync 长出 8 个"）。
剪边只按关系词剪，别的子系统若给过镜像节点边一律原样保留（不是本文件要扰动的对象）。

**这条约定对后续所有读真实图的测试都成立**：P6 之后运行时图默认带调制接线，
任何"隔离/孤岛"式断言都必须自带剪枝，否则它会随生产代码的演进而失真。

### 闸门（P6 后重跑，本机实测）

- `scripts/run_tests.py`：全套件 **58 个文件 / 0 失败 / 50 秒**（新增 `tests/test_modulator_subgraph.py` 47 条；
  P4 文件从 40 → 42 条）
- `scripts/verify_p0_all.py`：exit 0
- `tests/test_energy_conservation.py`：全绿（新通道没有破坏守恒）
- `scripts/bench_diffusion.py --compare`：**130 处差异，与 R2 之前同一批**（抽样行逐字相同）
  ⇒ **没有动基线，也不需要重批**：bench 的合成图里没有调制器节点、也不发脉冲，
  所以它天生测不到 phasic→图激活这条通道。测到它的是上面的 E/G 组（真实运行图）。
- `graph_integrity_audit.py`：仍 exit 1，但**新增项为 0**——关系种类 74→76（`调制/增强/抑制/交互` 入册）、
  单次使用关系 13→12；非规范关系仍为 **0**，`产生` 错配仍 42、复合命名仍 22、非法 label 仍 0、
  label×space 矛盾仍 2（这四件都是待用户裁定的存量，见 §8）

---

## 附七：P7 落地记录（扇出来自图，不是 if/else；2026-09-21）

### 落地的部件

| 部件 | 位置 | 干了什么 |
|---|---|---|
| 事件对象 | `modulation_events.ModulationEvent` | 一次"发生了什么"的结构化描述：`event_type / source / valence / intensity / rpe / novelty / uncertainty / goal_relevance / social_relevance / success / cycle_id / ref`。**只描述，不决定**谁动多少 |
| 应用器 | 同文件 `ModulatorEngine.apply_event()` | 查 `事件类型:<名> -[影响 w]-> 调制器镜像` 的出边扇出：`delta = w × 信号值 × 交互增益`，按边的 `channel` 分派到 `st.pulse()`（phasic）或 `st.apply_delta("modulator", …)`（tonic）。数值写入**仍然只有 internal_state 一个入口**（不变量 I1 不破） |
| bootstrap | 同文件 `ensure_event_types()` | 幂等种入 **14 个 `事件类型:*` 节点 + 23 条影响边**；边的语义细节（signal/channel/gate/clamp/why）存在**事件类型节点的 `extra_attrs["edges"][调制器名]`**——实测 `Edge` 没有 extra_attrs，这是不新建平行 JSON 库的前提下唯一能带上语义的位置（禁令 11） |
| 交互边消费者 | 同文件 `interaction_gains()` | `调制器 -[交互 w]-> 调制器` 从"P6 里没人读的装饰"变成真消费者：`gain = Π(1 + w × dev(源))`，单边夹 [0.4, 2.0]、总夹 [0.25, 3.0]。源在基线时 gain 恒 1 ⇒ **出厂拓扑下无人被改变**（E 组断言） |
| 出厂表 | `config.modulator_system.event_rules`（16 行）+ `event_default` | 从 `reward.py` 的六个写死常数与 `emotion_hormone_modulation` 的 7 个词翻来；`reward_rpe / reward_success / reward_failure` 拆成三类，是因为**符号是事件语义**，而 `kg.add_edge` 对同三元组是 `max(weight)` 合并（附六第 4 条）——同类型同靶点写两行会被静默吃掉，`rules_index()` 因此报 `conflicts` |
| 接线 | `reward.release()` / `app.py` 情绪共振 + 预测基线 / `action_system._apply_reward()` | 五处来源各自只**构造事件**；`cancelled`→`interrupted`、`blocked`→`obstacle_hit` 是 P7 新增的两条（原先在激素层完全隐身）。`time_bucket_changed` 留给 P10 |

`emotion_hormone_modulation` 没有删：它降级成**同一张事件表的种子**（signal=`const` 时权重就是 delta），旧 config 键仍可读，避免为清理而破坏既有调用方。

### 关键实测（本机跑出）

**迁移无损**（B 组 11 条）：四种 `(source, outcome)` 组合的慢分量与旧常数**逐值相同**——
`self:discovery` dopamine +0.032（=0.04×0.8）；`social:rejected` oxytocin −0.018（=0.036×−0.5）、
cortisol +0.025、serotonin −0.01、**dopamine 不动**；`self:cancelled` 零写入。
RPE 脉冲 0.80×0.25=0.20 仍打 phasic。旧代码里 `if ev["source"]=="self"` 这个分支，
现在是边上的 `gate:{"goal_relevance_min":0.5}` —— **同一件事从控制流变成数据**（D 组验证门控真的挡）。

**图是真源，且三种状态语义互不混淆**（C 组 8 条）：

| 状态 | 结果 | 为什么这样设计 |
|---|---|---|
| 事件类型节点不存在 | 走兜底出厂表，`source="config"` | 图尚未接管这类事件 |
| 节点在、某条边删掉 | 该调制器**不写**（其余边照常），删光则 `reason=no_rules` | 故意消音，不能被兜底表复活 |
| 节点在、边权 0.05→0.20 | 写入同比例 4 倍（精确到 `2e-4`，残差来自 `交互` 增益） | 效力住在边上，这是 §29 场景 17 的机器可验版 |

**真实启动**（`import app`，日志逐字）：

```
[Modulation] 子图种入：目标节点 +0，边 +4，Self 锚定 +0，跳过 0
[Modulation] 调制子图：靶点+0 边+4 锚定+0 跳过0；投影 30 条边→30 行系数（回收 0）
[Modulation] 事件表种入：事件类型 +14（共 14 类），影响边 +23，跳过 0
[Modulation] 事件表：14 类事件，影响边 +0，跳过 0
```

活图 **1099/1961 → 1113/1988**（+14 事件类型节点、+23 影响边、+4 交互边，零其它新增）。
这仍是**用户本地图谱的一次真实写入**（gitignored 运行时数据，种入幂等）——写前留了临时备份
`%TEMP%/runtime_graph.pre-p7.json`，未进仓库。

**交互边真的在起作用**（E 组）：一次 `reward_failure` 把内啡肽抬离基线后，同一事件的
cortisol 写入增益从 1.0 降到 **0.982**；把那条边符号翻正 ⇒ 同位置变成放大。
`sensitivity` 出厂字段全程未动 ⇒ 增益是**图算出来的**，不是字段改的。

### 写测试时撞见的四件事（都不是测试能绕过去的）

1. **半接线陷阱（真 bug，已被修）**：`ensure_event_types` 原本"先建节点、端点接不上就跳过边"。
   于是一个只有 9 个节点的图（`tests/test_reward_disposition.py` 自建的小图）被建出
   **14 个光杆事件类型节点** → `graph_rules` 认为"图已接管"返回空规则 → 兜底表也不走 →
   奖赏脉冲静默归零（`phasic=0.0`）。改成**端点一个都接不上时整类不建**（报 `types_unwired`）。
   教训：`source="graph"` 必须是"图真的接管了"，否则权威源判定会反过来杀死兜底路径。
2. **上升夹速与不应期不是 bug，是 P3 的机制在 P7 里继续有效**：同一瞬间连写两次同向慢分量，
   第二次本来就该被 `cap = old + rise_per_min × Δt` 夹住（余量进 `pending_rise`）；
   norepinephrine 的 `refractory_min=1.0` 也会压住一分钟内第二脉冲。测试因此加了 `age()`
   ——把 `last_slow_ts` 拨到 10 分钟前，即"两次相隔足够久的写入"。**这不是给测试开后门**：
   生产里事件之间天然有分钟级间隔，被绕过的只是测试自己造成的"同一毫秒"。
3. **trace 字段有取整**（`requested` 4 位、`gain` 3 位），所以"精确 4 倍"的断言容差只能是 `2e-4`，
   不是 `1e-9`。要更严就在 `apply_event` 里回传未取整的浮点——为了测试改观测面不值得。
4. **`ModulationLayer` 的 `hormone.*` 信号名与 P7 无关**：参数侧系数仍来自 P6 投影，
   事件侧只写调制器浓度。两条通道各自可测，没有互相覆盖。

### 边界（禁令复验，G 组）

`modulation_events.py` 里没有 LLM 调用、没有 prompt 模板、没有回答文本、没有动作执行、
不 import 扩散引擎（图注入仍归 `internal_state._graph_pulse`）；每次写入都带
`reason=事件类型:ref` 与 `cycle_id`，`recent_sources` 记的是**事件名**而不是模块名——
"P7 之后仍不知道刚才发生了什么"这个失败模式被 G4 挡住。慢通道事件不注入图激活（G5）。

### 闸门（P7 后重跑，本机实测）

- `scripts/run_tests.py`：**59/59 通过 / 43 秒**（新增 `tests/test_modulation_events.py`）
- `scripts/verify_p0_all.py`：全部通过，exit 0
- `scripts/bench_diffusion.py --compare scripts/bench_baseline.json`：**130 处差异，与 R2 之前同一批**
  ⇒ 未动基线、无需重批（bench 不发调制事件）
- `graph_integrity_audit.py`：仍 exit 1，**新增问题 0**——关系种类 76→**77**（`影响` 入册后事件边不再算新词），
  单次使用关系仍 12、非规范关系仍 **0**、`产生` 类别错配仍 42、复合命名仍 22、非法 label 仍 0、
  label×space 矛盾仍 2（后四项是待用户裁定的存量，见 §8）

### 待裁定 / 未做

- **`NEED_SPEC[*]["modulators"]` 零消费者**：需求→调制器的出厂映射仍只是数据，图上那 5 条
  `需求 -[...]-> 调制器` 边实际只有 `生存需求` 会被点亮（Minecraft 具身映射器喂的）。
  P9 要把它接成真信号，或按 §2 记为死表。
- **§2 D-12 已过期**：审计写它时 `app.py` 里还有 `hormone="dopamine" if drive=="explore"`；
  当前工作区已搜不到这个构造（先前重构时消失）。**D-12 关闭**，不需要 P8 处理。

---

## 附八：P8 落地记录（14 个认知场目标都是真旋钮，2026-09-21）

### 先说一件事：计划的 D5 写法会造成双重计算

原计划写的是"`ModulationLayer.register` 挂倒 U/overload 曲线"。落地前查了信号来源：

```
config.modulator_system.specs[*].baseline
  → InternalState.modulator_tonic()      = base + modulator_dev()
  → modulator_dev()                      = receptor_response(conc−base, …, saturation, overload, pull)
  → cognitive_field._build_signals()     sig["hormone.<名>"] = 该响应量（已减基线）
  → ModulationLayer.compute()            raw = baseline × (1 + Σ coef·signal)
```

**曲线已经在调制器侧施加过一次**，`hormone.*` 传下来的就是响应量（禁令里"不得重复计入 phasic"
的同一条纪律）。在参数侧再挂一条曲线＝把同一段过载算两遍。所以 P8 的实际缺口不是"缺一条曲线"，
而是**曲线没有牙**：`overload_pull` 这个字段在 R2 之前的 12 个规格里根本不存在，过载支恒为 0。

### 1) `overload_pull`：定义单位，再填数（`config.py` + `internal_state.py`）

字段单位在规格块头部写明：**"顶格（concentration=max）时扣掉多少响应"** —
0 = 不过载；1 = 顶格时正好把响应拉回零；>1 = 顶格时反号（"过度承诺变成负效用"）。
换算发生在唯一的消费者里：

```python
def _pull_coefficient(self, name, item):        # internal_state.py
    pull = float(item.get("overload_pull", 0.0) or 0.0)
    if pull <= 0.0: return 0.0
    _lo, hi = self._bounds("modulator", name)
    span = float(hi) - float(item.get("overload", 1.0) or 1.0)
    return pull / (span * span)                 # 二次项系数：Δconc=span 处正好扣掉 pull
```

用绝对值写二次系数的话，`span` 只有 0.05–0.20 的调制器（dopamine、melatonin…）
扣掉的量是 1e-3 量级 = 没有过载支；除以 `span²` 之后 12 个调制器的"顶格扣掉 pull"才彼此可比。
`_view_of()` 与 `modulator_dev()` 都改走这一个换算点（不再有第二处公式），
`dump_modulator_state()` 同时回显 `overload_pull` 与换算后的 `pull_coefficient`。

`scripts/scan_modulator_curves.py`（新增，可复算）实测 12 条曲线：

| 调制器 | 基线 | 饱和 | 过载 | pull | 系数 | 峰值@浓度 | 峰值响应 | 顶格响应 |
|---|---|---|---|---|---|---|---|---|
| dopamine | .50 | .85 | .95 | .70 | 280.0 | .95 | +0.400 | **−0.304** |
| norepinephrine | .45 | .80 | .92 | .80 | 125.0 | .92 | +0.410 | −0.397 |
| acetylcholine | .50 | .85 | .95 | .45 | 180.0 | .95 | +0.400 | −0.054 |
| cortisol | .30 | .75 | .90 | .90 | 90.0 | .90 | +0.525 | −0.300 |
| adrenaline | .20 | .60 | .80 | 1.00 | 25.0 | .80 | +0.500 | −0.200 |
| serotonin | .55 | .80 | .95 | .35 | 140.0 | .95 | +0.325 | −0.028 |
| oxytocin | .40 | .80 | .95 | .35 | 140.0 | .95 | +0.475 | +0.122 |
| endorphin | .30 | .70 | .85 | .45 | 20.0 | .85 | +0.475 | +0.010 |
| gaba | .55 | .85 | .95 | .35 | 140.0 | .95 | +0.350 | −0.004 |
| glutamate | .50 | .80 | .90 | .60 | 60.0 | .90 | +0.350 | −0.260 |
| histamine | .45 | .75 | .90 | .50 | 50.0 | .90 | +0.375 | −0.134 |
| melatonin | .20 | .70 | .85 | .40 | 17.8 | .85 | +0.575 | +0.160 |

峰值都落在 **overload 点**（这是 P3 定的形状，不是巧合：过饱和惩罚项只在 overload 之后介入），
过峰后的下降段在 `(overload, max]` 这条窄缝里——所以 `pull/span²` 的归一化是必需的。
oxytocin/endorphin/melatonin 顶格仍为正：它们的 pull 较小，语义是"过度联结/过度愉悦只是变得没用，
不是变成痛苦"；dopamine/NE/cortisol/adrenaline 顶格为负：**奖赏水位钉死→探索与表达增益塌回负值**。

### 2) 合力护栏：实现了，量了一下，删掉

先按计划在 `ModulationLayer.register` 上注册了"激素合力"contributor（防 12 行 `hormone.*`
同时把某个参数推到量程端点）。实测每个参数在**所有调制器同时钉在响应峰值**时的最坏合力：

```
action.score_threshold   0.5014 (6 行)   diffusion.param_gain   0.3295 (3 行)
diffusion.emission_ratio 0.3200 (1 行)   behavior.exploration_rate 0.3189 (3 行)
llm.budget_factor        0.2512 (3 行)   diffusion.inter_round_decay 0.2450 (2 行)
cognition.form_threshold 0.2232 (3 行)   retrieval.topk_scale   0.2153 (2 行)
cognition.express_threshold 0.1095 (2 行) behavior.explore_margin 0.1045 (1 行)
retrieval.reignite_every 0.1012 (1 行)   diffusion.max_depth    0.0902 (1 行)
llm.temperature_answer   0.0451 (1 行)   attention.width_scale  0.0315 (1 行)
```

最坏 1 + 0.50 = 1.50，不足以把任何参数推到端点 ⇒ 护栏是**不会触发的代码**。
按"不为了激素数量增加复杂度"收尾要求删掉它，把量出来的结论写进 `modulation.py` 头注，
让"生产零注册者"读起来是**结论**而不是缺口；hook 本身保留（将来出现"表表达不出来的形状"时用它）。
这也是 §5 那条纪律的反向使用：字段/钩子必须有用，没有用就别留。

### 3) D-3：两个假旋钮接上真消费者（`continuous_cognition.py`）

| 参数 | 新读者 | 语义（为什么是这个而不是字面的"周期"） |
|---|---|---|
| `retrieval.topk_scale` | `_pulse()`：`k = round(15 × scale)`，下限 3 | 这次脉冲从认知场取多宽的素材面。DMN 高→翻更多角落；`tension.negative_experience` 抬升也放宽（反刍时更容易捞进旧账） |
| `retrieval.reignite_every` | `_reactivate_gate()`：冷场**连续成立 N 拍**才点火（`self._cold_beats`） | §十一 明写"不堆固定闹钟：reactivation 由场冷度触发"。所以它不能是定时器；它是"冷场要持续多久才值得翻旧账"的**防抖**。CEN 行 +0.45 → 聚焦时拉长（别打断自己），DMN −0.35 / histamine −0.27 → 发散/清醒态缩短 |

两处细节：
- 计数器用 `_cold_beats`（本门自己的观测拍数），不用 `self._tick` 差值：对话期 tick 会停，
  绝对 tick 差会把"冷场持续度"被暂停污染成假象。回暖即清零。
- `cfg["reignite_every_pulses"]` 从"死默认"降级为"调制层缺位时的兜底"（注释同步改写）。

`tests/test_cognitive_loop.py` S2 因此从"调一次 gate 就要求点火"改成"按生产节拍连打"，
并**新增**一条断言：冷场头一拍不点火。改测试不是放松——原断言测的是"无需输入也能点火"，
现在多了"要持续几拍"这一层真实机制，用循环喂拍数才是忠实地复现生产节律（同 P7 的 `age()`）。

### 4) 闸门：`tests/test_modulation_targets.py`（80 条，全走真实装配）

fixture 是 `config → CognitiveField`（含 `baseline_from` 解析）`→ ensure_modulator_subgraph →
project_coefficients`，不手搓 effects 表（§26）。所有读数在 EMA 收敛后才取（`settled()`），
否则读到的是"正在追"的中间值，单调性会被平滑过程假性破坏。

- **A 真实读者（15 条）**：扫全仓 137 个 `.py`（排除 tests/scripts/prototype/docs…），
  只认**调用位** `x.get("param"` / `_mod("param"`（多行也算），表定义位不算——否则 config 的
  出厂表会伪装成消费者。14/14 有读者，读者位置逐条打印（app/autonomy/diffusion_engine/
  dialogue_decision/cognition_modes/continuous_cognition…）。汇总断言"零假旋钮"。
- **B 单调 + 有界（17 条）**：每个靶点取其**主导致信号**（|coef| 最大行），按信号族扫物理量程
  （`hormone.*` 扫 ±0.45，其余扫 [0,1]）。弱单调（钳位后允许走平）+ 全在 `[min,max]` 内 +
  必须有牙（端点值与中点值差 >1e-6）。三条汇总。
- **C 倒 U 走到参数（14 条）**：每个调制器扫 41 点找响应峰值浓度，再取"从弱到强"第一条
  **确实改变参数值**的系数行，比较 `|v_顶格 − v_基线| < |v_峰 − v_基线|`。12/12 全部可见回落；
  从弱系数开始选是故意的——强系数容易把参数钉在量程端点，那处"看不出反转"是量程的问题不是曲线的问题。
- **D 图是权威（29 条）**：逐靶点删掉进入该 `调制目标:*` 的全部调制边 → 重新投影 →
  该行消失且**喂同样激素信号时参数值与零信号完全相同**（P6 证的是系数行，这里证的是参数值）；
  再用 `ensure_modulator_subgraph` 复原 → 系数逐字回来（工厂表不覆盖既有权重，所以复原是精确的）。
  非激素行（`network./tension./drive./context.`）全程原位不动。覆盖度汇总 **14/14**。
- **E 三处一致（5 条）**：targets 表短名互不重复、参数都在层内、靶点节点 `extra_attrs.param`
  与表一致、没有悬空靶点、`edges_read == 层内激素行数`。

装配实测：30 条调制边 → 30 行系数（回收 0 行），层内 21 个参数（14 个是认知场靶点）。

### 5) 顺手核实并修掉的既有测试污染

`tests/test_modulator_graph_presence.py` 的"调制器节点没有入边"在活图上变红：
运行时图是活的，P6/P7 的 bootstrap 已把 `调制目标:*` / `事件类型:*` 与 `影响/交互/处于` 边落盘。
把 fixture 的剪枝扩到这三类产物（并加 `影响/处于` 到调制关系词集合），
docstring 说明"剪掉 P4/P6/P7 产物后再测 P4"。这是**测试隔离**修正，不是放宽断言：
P6/P7 的"接了线"方向由它们自己的文件断言。

### 6) 边界复验（禁令 3/4/8/9/10/11/12）

- 没有新的平行 JSON 状态库：新增字段只在 `config.modulator_system.specs`（§24 集中默认值），
  新增节点属性沿用 P6 已有的靶点节点；`CognitiveField` 状态文件路径未变。
- 没有 per-hormone if/else：P8 全部改动的分支条件是**信号族名前缀**与**表里的行**，
  没有任何 `if name == "dopamine"`；`receptor_response` 仍是唯一曲线实现。
- 激素不决定回答文本、不调 LLM、不执行动作：`_pulse`/`_reactivate_gate` 读的仍是参数值，
  决策与文本生成都在这条链之外；`llm.temperature_answer` 的读者在 `app.py` 原有位置未变。
- 受保护核心零改动：`graph_model.py`、`diffusion_engine.py`、扩散数学、能量守恒逻辑
  **P8 一字未动**（`git diff` 里 `diffusion_engine.py` 的 402 增行是 09-20 Drive Phase 3 的
  既有未提交改动——`_mod()` 读点在那批里就已存在，不是 R2 加的）。
- 时间尺度层级（§27/§28）未破坏：`overload_pull` 只改响应形状，不改任何 decay 半衰；
  调制 < 情绪 < disposition < trait 的次序不变（P11 还要复核 mood 那条）。

### 7) 闸门（P8 后重跑，本机实测）

- `scripts/run_tests.py`：**60/60 通过 / 44 秒**（新增 `tests/test_modulation_targets.py`）
- `scripts/verify_p0_all.py`：全部通过，exit 0
- `scripts/bench_diffusion.py --compare scripts/bench_baseline.json`：**130 处差异，与 R2 之前同一批**
  （合成 bench 不发调制事件、不走 CC 的 `_pulse`/`_reactivate_gate` ⇒ 测不到这条通道，未动基线）
- `graph_integrity_audit.py`：仍 exit 1，**新增问题 0**：关系种类 77、单次使用 12、
  **非规范关系 0**、非法 label 0；待裁定的存量仍是 2 个 `label×space` + 42 条 `产生` 类别错配 + 22 个复合命名
- `scripts/scan_modulator_curves.py`：12 条曲线全达标（新增的第三道曲线闸门）

### 待裁定 / 未做（P9 起手就是这些）

- **NEED_SPEC `modulators` 表零消费者**（附七留到现在）：需求→调制器的映射仍只是数据。
  P9 的"need 多对多 + drive 偏置"要把它接成真信号，或按 §2 记为死表。
- **`cognitive_field.py:393-405` 的固定 rate gains**：DMN/CEN 连续偏置仍走硬编码增益，
  `rate_gains_n` 恒等槽没用上——P9 的主活。
- **`D-4` 幽灵信号 `need.social`**：effects 里有行、`_build_signals` 不产这个前缀 ⇒ 恒乘 0。
  与 P9 同批处理（要么补生产者，要么删行）。

## 附九：P9 落地记录（需求↔调制器 上图；网络/驱动 走连续偏置，2026-09-21）

### 0) 一句话：这一期把"调制器参与动力学"拆成**三条语义不同的通道**

P8 之前，`cognitive_field.step()` 里有几行硬编码：张力速率乘 `(1+0.3·cortisol)`、
好奇上升/消退乘多巴胺、social 亲和乘催产素。P9 把它们全部搬进表，并顺手把
"表能表达什么、不能表达什么"分清楚——这是本期最主要的设计结论：

| 通道 | 作用位置 | 改的是 | 稳态 | 需要输入在场吗 |
|---|---|---|---|---|
| `rate_gains` | rise/fall 速率（乘数） | **惯性** | 不变（只改到位快慢） | — |
| `affinity` | `<drive>.<tension>` 权重（乘数） | 该张力在这一驱里值多少 | 变 | **需要**（0×gain=0） |
| `bias` | 目标求和后、钳位前的加数 | **稳态水位** | 变 | 不需要（"奖赏水位高→坐不住"没有具体张力也算） |

所以 **`rate_gains_n`（网络段）是故意的恒等槽**：网络要的"压力压走神 / 清醒促聚焦"
是**持续**偏置，速率通道在稳态上没有发言权，放这里等于没放。持续偏置走图上的
`调制器-[抑制]->DMNetwork` 那类边（`graph_biases` → `NetworkField.target_of(bias=)`）。
表留着是接入点，不是遗留死表——闸门 D1/D2 直接把这条论证钉成断言：
同一目标、不同 rate gain，400 拍后**取值完全相同**（0.2 vs 0.2），而 bias 0.30 把稳态从 0.2 抬到 0.50。

### 1) 落点名册由"拥有字段的对象"给，不在中间层抄表

`调制器→网络/驱动` 的边落在图上用的是节点 id（`CENetwork`/`LearningDrive`…），
字段内部用的是短名（`CEN`/`learning`）。这个对应关系若写在 `modulator_subgraph` 里
就是第二份真值（config 改网络名就会漏改）。现在 `NetworkField.node_ids()` /
`DriveField.node_ids()` 各自从自己的 spec 里给（`"node"` 键），
`CognitiveField.node_names()` 汇总后交给 `graph_biases(..., node_names=)`。
名册不全 ⇒ 边没人认领 ⇒ 闸门 A2 直接红（"没有静默失效的边"）。

⚠️ 顺带纠正一处旧注释的误导：`NetworkField` 类头曾让人以为 `spec["node"]` 是 `inputs`
的一项加数。它不是加数，是**该网络在图上的落点名**，`inputs` 里也没有调制器前缀
（调制器不走 inputs）。

### 2) 需求 ↔ 调制器：一行只有一个方向，所以是负反馈而不是死循环

`config.modulator_system.edges` 里 需求↔调制器 共 7 行（58 行总数中占 7）：

```
需求 →调制器（漂移，写慢分量）    调制器 →需求（偏置，改靶值）
  探索需求 -[增强 +0.20]-> 多巴胺样      多巴胺样 -[抑制 -0.20]-> 探索需求
  社交需求 -[增强 +0.20]-> 催产素样      催产素样 -[抑制 -0.20]-> 社交需求
  安全需求 -[抑制 -0.25]-> 皮质醇样      皮质醇样 -[增强 +0.25]-> 安全需求
  掌控需求 -[增强 +0.15]-> 血清素样      …
  生存需求(mc_state) -[增强 +0.20]-> 皮质醇样
```

**同一对节点不许同时有"抬"和"被抬"两条同向行**——那样才是正反馈死循环。
现在的形状是：缺口→抬通道→通道反过来把靶值收敛（缺口变小）→漂移失去燃料→通道回落。
长拍实证（闸门 C4/C5，双向都开满、600 拍）：需求水位全程落在 **[0.126, 0.524]** 内区，
既没钉端点也没回 0。出厂表实测 **58 行 = 增强 35 / 抑制 13 / 调制 6 / 交互 4**，
`rate_gains` 三段 = tension 1 / drive 3 / affinity 2。

`NEED_SPEC` 里那张 `modulators` 表（审计 D-1 的"零消费者"）已删除——它的数据现在
真的在图上，留两份就是漂移源。

判据是节点的 `extra_attrs.type`（`need` = InternalState 镜像；`mc_state` = 具身状态
节点如 `生存需求`，由 `embodied_mapper` 写 `pressure` 0~5），不是"名字以需求结尾"
那种字符串猜测（那才是禁令里的闭合词表）。`生存需求` 没有对应的内部需求维度，
所以 `need_salience_of()` 对它取 `pressure/5`，其余取 `activation/5`。

### 3) 显著性（不是裸 level）才是乘数

`need_salience(name) = max(urgency, 离靶距离)`（`internal_state.py`）。
理由：需求水位本身就含"目标"信息，用 level 当乘数会把"我把它设计成这样"当成"我现在缺"。
这一版顺带把 **D-4 幽灵信号**和 **D-8 无人认领的 urgency** 一起收掉：

- `_build_signals` 现在产 `need.<名>`（只产注册过的；没注册就不产，不用 0 伪装）
  ⇒ `cognition.express_threshold` 里那行 `need.social: -0.10` 不再是恒乘 0 的装饰（闸门 E1–E4）。
- `autonomy._motivation_value` 从 if 链改成 `cfg["motivation_needs"]` 表驱动
  （`生存→safety coef/bias` 等），`urgency` 有了第一个真消费者，且表里加一维即生效（F1–F3）。

### 4) 漂移仍只走唯一写入口，所以 §13 的"要花时间"自动成立

`apply_need_drift()` 的 delta = `need_drift_rate_per_min × dt_min × w × 显著性`，
经 `apply_delta(source="needs")` ⇒ 受 rise 夹速、动的是 **tonic**、进历史、留痕带边义。
实测（全基线、dt=1 分钟）一拍写 **6 行**，历史里长这样：

```
需求漂移: 探索需求 显著性 0.25 ×+0.20（增强）
```

`source="needs"` 不在 `SLEW_EXEMPT_SOURCES` 里 ⇒ 一次拍推不爆，余量进 `pending_rise`
由 `tick_decay` 吸收（闸门 G3）。关掉 `modulator_system.subgraph` ⇒ 什么都不写（G4）；
没有 kg 或 dt=0 ⇒ 也不写，不静默拿默认时长顶替（G5）；子图关闭时 `graph_biases`
返回 `skipped=True` 而不是"算出 0"（G6，避免把"没接线"混成"接了但没效果"）。

生产接线在 `continuous_cognition` 的每一拍：`tick_decay()` → `apply_need_drift(dt=本拍秒/60)`
→ `update_needs_from_signals(bias=graph_biases()["need"])`。dt 用本拍实际时长，
所以回合暂停期间不会补跳。

### 5) 实测：偏置确实把倒 U 传到了稳态水位

真实装配（`config → CognitiveField → ensure_modulator_subgraph → project_coefficients
→ set_bias_source`）+ 400 拍 settle，全部在临时图/临时目录上：

| 状态 | bias(CEN) | bias(DMN) | CEN 稳态 | DMN 稳态 |
|---|---|---|---|---|
| 全基线（edges_used=0） | — | — | 0.0914 | 0.3436 |
| NE 0.85 + 褪黑素 0.85（dev +0.393 / +0.575） | **+0.098** | **+0.173** | **0.181** | **0.5036** |

驱动侧同时出现 `learning +0.059`（稳态 0.0 → 0.059）：这是"没有张力在场但水位有观点"
的那一格——affinity 通道在这格上是乘不动的（D4），bias 才走得到（D6）。
`capped=[]`、`skipped=False`、偏置出不了 ±`bias_cap`。

过载反转同样落到稳态水位上：浓度继续往上钉（顶格）时偏置**变号**（闸门 B4），
所以"越警觉越聚焦"在 overload 之后自动变成"越警觉越散"——不是我们写了 if，
是 `modulator_dev()` 的曲线本来就是倒 U，而 P9 让这条曲线第一次进入网络稳态。
由此得出一条方向断言的纪律：**测"方向对不对"要钉在响应峰值浓度（`peak_conc()`），
钉在量程顶测的是反转**。C2/C3 用峰值，C2b 专门测顶格反号。

### 6) 踩到并修掉的两个坑

**① `CognitiveField(config={})` 丢了增益表（P9 自己引入的回归）。**
把出厂耦合表从 `step()` 搬进 config 之后，离线测试常传的 `config={}` 连表一起没了 ⇒
`test_cognitive_field.py` 的"高多巴胺→消退更慢"两侧衰减完全相同（0.03729 vs 0.03729）。
本仓的既有契约是"裸装配 = 生产形态"（`_normalize_mod_specs` 头注明写），所以修法是在
`cognitive_field.py` 补 `DEFAULT_RATE_GAINS` 兜底、config 段**整行覆盖**它
（与 `DEFAULT_MODULATION`/`DEFAULT_DRIVES` 同一套路），而不是放宽那条测试。
两份副本必然漂移 ⇒ 闸门 A6 钉住"模块兜底 == config 声明"，A6b 钉住裸装配拿到同一张表。

**② 合成时钟会静默关掉衰减（P10/P12 必须先解决）。**
测"带衰减的长拍平衡点"时给 `tick_decay(now=t0+i×60)` 喂合成时间：漂移那一次
`_write_numeric` 不带 stamp ⇒ 把 `last_update` 盖成**真实墙钟**，于是下一次
`dt_min = (合成 now − 真实 now)/60` 变成负数或几百分钟——测出来的是
**死区极限环**（`decay` 有 `abs(cur−base)>0.02` 的死区）而不是平衡点。
数字因此不采信（本附不引用它），但结论要留：**统一时基**（同一处时钟注入，
写侧与衰减侧共用）是 P10"时间重放"与 P12"实验模式快进"的前置条件。

### 7) 幅度参数入册（§24）

`bias_cap`、`need_drift_rate_per_min` 原先只是模块常量 ⇒ 现在 config 声明、模块常量兜底。
闸门 A7 断言两者一致（防漂移），A8/A8b 断言**钳位读 config**：把 `bias_cap` 调到 0.05，
同一批边给出更小的偏置，且削平会记进 `capped`（不静默）。

### 8) 闸门（P9 后重跑，本机实测）

- `tests/test_need_modulation_coupling.py`（新增）：**45 条全过**
  （A 名册/无损/兜底一致 8、B 网络连续偏置+源码扫描 9、C 需求位移与长拍 6、
   D 三通道分工 7、E need.* 信号 4、F 动机表 3、G 唯一写入口 6，另 A6/A6b/A7/A8/A8b）
- `scripts/run_tests.py --gates`：**61/61 通过 / 51 秒**
- `scripts/verify_p0_all.py`：全部通过，exit 0
- `scripts/bench_diffusion.py --compare`：**仍 130 处差异，与 R2 之前同一批**
  （合成 bench 图没有调制器、不发脉冲 ⇒ 测不到这三条通道；未动基线）
- `graph_integrity_audit.py`：仍 exit 1，**新增问题 0**：1113 节点 / 1988 边、关系 77 种、
  单次 12、**非规范关系 0**、类别错配 42、复合命名 22、非法 label 0、`label×space` 2
  （后四项都是待用户裁定的存量，见 §8）
- 活图尚未 bootstrap P9 的 7 条需求↔调制器边（下次真实启动 +边）

### 9) 边界复验与未做

- 禁令 2（per-hormone if/else）：`step()` 里已无 `dopamine/cortisol/oxytocin` 字面量，
  只剩"把表乘开"的通用循环；B9 用源码扫描兜住"不许出现 `mode ==` / 拿 0.5 当开关比"。
- 禁令 6/7（网络不许二值切换）：CEN/DMN 全程开区间（B7），抬一个网络不把另一个压到近零（B6），
  双高可达（B5），每拍只挪一点（B8）。
- 禁令 10/11：没有新建平行 JSON 状态；新参数只在 `config.modulator_system`；
  网络/驱动的落点名册来自字段自己的 spec。
- 激素不决定文本、不调 LLM、不执行动作：P9 的三条通道终点都是 `[0,1]` 水位或速率乘数。
- 受保护核心零改动：`graph_model.py` / `diffusion_engine.py` / 扩散数学 / 能量守恒
  本期一字未动。
- **未做（P10 起手）**：① 统一时基（见 6②）；② melatonin/histamine 仍无 circadian 驱动源——
  `temporal_awareness` 的时段节点属性要经 provider 进调制器，代码里不许再出现 `now().hour` 判据；
  ③ `time_bucket_changed` 事件（P7 已入表）还没有真正的靶点消费链，等 ② 落地才有着落。

## 附一〇：P10 落地记录（昼夜是图上的事实，不是代码里的小时，2026-09-21）

一句话：**褪黑素样/组胺样终于有了生产者，而生产者是节点属性，不是 `now().hour` 判断。**
附九 §6② 留下的统一时基是这一期的前置条件——没有它，"时间重放"测出来的每条曲线都是假的。

### 1) 三层各管什么

| 层 | 落点 | 干什么（不干什么） |
|---|---|---|
| 传感器 | `temporal_awareness` | `BUCKET_CIRCADIAN` 是**全仓唯一**把小时翻译成事实的地方（闸门 E1 源码扫描钉住）；`update_clock_state` 是 `circadian_strength` 的**唯一作者**（E2）；`circadian_facts()` 只读视图，时钟没跑过 ⇒ `ready=False`，不拿默认值冒充 |
| 事实（节点属性） | 时段桶 / 昼夜相位节点 | `circadian_load`（这个时段本身有多夜，静态知识）与 `circadian_strength`（**此刻**这一事实有多成立）分开存：语言线索点亮"深夜"不改变 strength ⇒ 不伪装成"现在真是深夜" |
| 通道 | `modulator_subgraph` | `_apply_state_drift` 是**唯一**的"状态事实→调制器慢漂移"实现；P9 的需求侧与 P10 的昼夜侧只差 `source_types` 与速率，不是两段代码。`SALIENCE_READERS`（类型→读法）就是"什么算一个驱动性状态事实"的完整定义 |

新增 6 条边全部在 `config.modulator_system.edges`（58→**64** 行）：`夜间→褪黑素样 +0.25`、
`白天→褪黑素样 −0.05`、`深夜 +0.08`、`凌晨 +0.06`（时段级形状，叠在昼夜之上）、
`白天→组胺样 +0.12`、`夜间→组胺样 −0.12`。幅度旋钮入册 §24：
`circadian_drift_rate_per_min=0.06`、`bias_cap`（P9）。

### 2) 显著性为什么不乘 `activation`

`_circadian_salience()` 只读 `circadian_strength`，**故意不乘节点激活**：

1. activation 是扩散用的货币（P6 的同一纪律：调制通道不借别的系统的钱）；
2. 乘了就等于把"昼夜曲线"变成"今天聊了多少次夜晚"——§11 明确禁止的东西换个形式回来；
3. 不乘才让"**同一 bucket 序列 ⇒ 同一曲线**"成立：这是本期主闸门的可重放性来源。

代价是时段节点在语言里被提到不会推褪黑素——**这是设计意图**，不是缺陷（B3 钉住）。

### 3) 两条通道不重叠（§27 时间尺度分层）

| 通道 | 触发 | 幅度 | 性质 |
|---|---|---|---|
| 慢 | 每拍 `apply_circadian_drift` | `0.06 × dt × Σ(边权×显著性)`，dt 上限 5 分钟 | 水位；小时级 |
| 快 | 跨段 `time_bucket_changed` 事件 | `0.45 × 带符号夜间度之差`（钳 0.25）；组胺样 `−0.35×…`（钳 0.20） | 那一步的推/拉；分钟级 |

事件 valence 是**有符号**的 `load_to − load_from`（由 `temporal_awareness` 从图上事实算好），
所以一条边同时管"天黑推高"和"天亮压低"；差值为 0 的迁移（上午→中午）被 `signal=valence=0`
自己挡掉 ⇒ 不会每小时抖一下（C4/D4）。慢通道速率（0.06/min）与需求侧（`need_drift_rate_per_min=0.02`）**不同值**：
不是笔误——需求缺口是分钟级起伏，昼夜是小时级在势，同一速率两头都不对（§27）。

### 4) 实测幅度（7 个昼夜、5 分钟/拍、只给昼夜通道）

| 调制器 | 出厂基线 | 白天 | 夜里 | saturation / overload |
|---|---|---|---|---|
| 褪黑素样 | 0.20 | **0.1020** | **0.6643** | 0.70 / 0.85 |
| 组胺样 | 0.45 | **0.7020** | **0.1620** | 0.75 / 0.90 |

两端都不撞量程（C7），夜里也不越 overload（C8）。稳态位置可解析：
小拍长下 `gap = drift_rate × Σ(边权×显著性) / decay_per_min`（与拍长无关）——
按出厂边权，褪黑素样钉在"深夜"的稳态 ≈ 0.20 + 0.06×0.311/0.035 ≈ **0.73**，组胺样 ≈ **0.19**。
本机把时钟**钉死在一个时段**长跑 2000 分钟实测（同一份代码、只改拍长）：

| 拍长 | 褪黑素样@深夜 | 组胺样@深夜 | 褪黑素样@中午 | 组胺样@中午 |
|---|---|---|---|---|
| 1 分钟 | 0.7143 | 0.1905 | — | — |
| 5 分钟 | 0.6391 | 0.1890 | 0.1143 | 0.7020 |
| 20 分钟 | 0.2392 | 0.3834 | — | — |

第三行不是 bug 而是**护栏**：`_apply_state_drift` 的 dt 上限 5 分钟（一次写不许跳太远），
而衰减用真实 Δt ⇒ 长时间空闲后昼夜通道**欠驱动**（慢慢追回来），绝不会一键 teleport 到稳态。
生产里 CC 的拍长是 `tick_seconds`（远小于 5 分钟），落在第一、二行之间。
所以"夜里到多高"由**图上权重 + 各自的衰减速率**决定，改曲线改边、不改代码。

### 5) `circadian_driven` 第一次有消费者

死代码账 §2 里"字段只声明不使用"这一项关闭：`ModulatorEngine.apply_event` 在规则循环
开头拦掉非 `source=="circadian"` 的事件对这类调制器的作用，并且**记进 `gated`**（看得见，
不静默丢）。出厂只有褪黑素样打了这个标记（D8），对照组胺样照常被同一奖赏事件推动（D7）
⇒ 门卫不是"顺手关掉事件系统"。语义上它挡的是"吃了甜点→褪黑素上升"这类荒谬耦合。

### 6) 统一时基（附九 §6② 的坑，本期收口）

`InternalState.set_clock(fn)` + `_now()`：凡参与 Δt 计算的地方（写入时间戳、不应期三处、
周期起终、证据缓冲、`pulse`、`tick_decay` 缺省）都走同一时基；不注入时 `_now()==time.time()`，
**生产逐字不变**（F3）。这条不是锦上添花：P9 的探针实测过"合成 now 落在墙钟之前 ⇒ dt≤0 ⇒
衰减静默跳过"（数字看起来对但其实整段没衰减）。闸门 F1 故意用 `1.7e9`（远在墙钟之前）跑衰减，
F1b 钉住"时基倒挂时宁可不动，也不会把水位砸回基线"，F2 钉住"写入时间戳与注入源同域"。
这也是 P12 实验/调试模式（快进、回放）的地基——**同一份代码，喂不同时间源**。

### 7) 顺手修的既有回归

`tests/test_modulator_subgraph.py`（P6 闸门）的 fixture 现在调用**生产同一个**
`temporal_awareness.ensure_bucket_nodes()` 播种时段节点：否则新增的 6 条昼夜边会被判
"端点不存在"，A2/A3 假红。生产里时钟初始化（`app.py:937`）本来就早于调制子图装配
（`app.py:1081`），所以这是**补真依赖**而不是放宽断言。

### 8) 闸门（P10 后重跑，本机实测）

- `tests/test_circadian_modulators.py`（新增）：**44 条全过**
  （A 事实层 7、B 显著性读法 6、C 时间重放 12、D 快通道+门卫 11、E 源码扫描 2、F 时基 4、G 观测面 2）
  重放断言用 `==` 比较整条曲线（不是近似）：两次独立装配给出逐点相同的褪黑素样/组胺样序列
- `scripts/run_tests.py`：**62/62 通过 / 57 秒**（新文件自动入册）
- `scripts/verify_p0_all.py`：全部通过，exit 0
- `scripts/bench_diffusion.py --compare`：**仍 130 处差异，与 R2 之前同一批**（未动基线）
- `graph_integrity_audit.py`：仍 exit 1，**新增问题 0**（1113 节点 / 1988 边、非规范关系 0、
  类别错配 42、复合命名 22、`label×space` 2 —— 后三项仍是待裁存量）
- `scripts/scan_modulator_curves.py`：12 条曲线的 saturation/overload/pull 仍都在真实改变响应

### 9) 边界复验与未做

- 禁令 8（所有激素同一 decay）：褪黑素样 0.035/min vs 组胺样 0.025/min；驱动速率也分档
  （昼夜 0.06/min vs 需求 0.02/min），没有两个通道共用一个数。
- 禁令 9（生物学名当医学模拟）：`BUCKET_CIRCADIAN` 的注释与本文都写明是功能类比；
  0.66 这个数字没有任何医学含义，它只是"这条边集在当前速率/衰减下的稳态"。
- 禁令 10/11（绕开 Field/Graph、平行 JSON 状态）：驱动源是图节点属性，落点是唯一写入口；
  新参数只在 `config.modulator_system`，没有新状态文件。
- 禁令 12：dopamine tonic/phasic 未动；昼夜走 tonic。
- §26：本期没有新增任何"只在测试里走"的分支——`set_clock` 是生产可用的实验接口，
  回放用的三个函数（`update_clock_state` / `apply_circadian_drift` / `emit`）就是 CC 每拍调的那三个。
- 受保护核心零改动：`graph_model.py` / `diffusion_engine.py` / 扩散数学 / 能量守恒一字未动。
- 活图尚未 bootstrap 这 6 条昼夜边（下次真实启动 +边）；`circadian_strength` 是节点属性，
  随图落盘，重启后时钟一拍即复。
- **未做（P11 起手）**：① mood 仍是事件驱动的一次性值，没按 §15 从"调制器+需求+最近结果"**派生**，
  也没验过"不反哺激素"（循环性）；② 结果→调制器闭环里 `outcome` 一侧还差最后一条边
  （表现反馈→奖赏→调制器已有，但"结果好不好"没有作为事实进图）；
  ③ 死代码账 §2 剩 D-2/D-3/D-5…D-12 的 grep 复核（D-1/D-4 已在 P9 关闭，D-15/D-16 已在 P6 关闭）；
  ④ `MOOD_DECAY_PER_HOUR=0.15`（≈0.0025/min）与调制器衰减速率同量级，
  §27 要求 调制 < 情绪 < disposition < trait 的次序——**待用户裁定**（改数还是接受）。

---

## 附一一：P11 落地记录（2026-09-21）— 心情是派生读数，且方向只有一边

**一句话**：心情的**权重在图上**（5 条 `调制器 -[w]-> 心情` 边）、**数值在 persona 里**、
**方向只有一个**（余韵 + 包络 → valence → 语气/社交动机/低落张力，绝不回调调制器）。
结果侧同时收掉两处硬编码：`reward.modulation()` 的六个常数进 config，人格侧
decay/事件偏移进 config；死代码账 §2 全部给出"已闭 / 已处置 / 待裁定"三选一。

### 1 心情这一格现在的形状

| 成分 | 真源在哪 | 生产者 | 读者（全部，就这三个） |
|---|---|---|---|
| 事件余韵 `afterglow` | `PersonalityBaseline._mood_valence`（数值） | `mood_event(kind)`，金额在 `config.personality.mood.event_effects` | `current_mood()` |
| 调制器包络 `modulator_term` | 边上的**权重**（config 出厂 → 图落地后图为权威） | `modulator_subgraph.mood_projection()`（纯读，Σ 权重×dev，钳 ±`mood.cap`） | 同上 |
| 合成 valence | `clamp(余韵 + 包络, ±1)` | `current_mood()` | ① 语言层语气（`mood_context`）② 社交动机（`autonomy._motivation_value`）③ 低落张力 `mood_low`（经 `state.mood_deficit` 采样器） |

包络与 P8/P9 的参数通道**用同一个 `dev`**（`modulator_dev` = 受体响应(浓度−基线)），
所以：过载反转在这条通道上自动生效（B5 实测）、phasic 脉冲不进包络（B4 实测：
tonic 不动 ⇒ term 一字不变）、在基线上的调制器根本不出现（B2b/B6）。

### 2 为什么数值不进图、图也不写数值

`_ensure_mirror` 那一族（16 个状态镜像）是"数值 → 节点属性"的既有做法，但心情
刻意**不照抄**：`心情` 节点上没有 `valence` 属性，只有 `numeric_owner=
personality_baseline` 这个指针。理由是禁令 11 的精神——同数量别有两个落点：
`current_mood()` 是**被读**的，任何给它加写的路径都会把"读一次改一次"变成隐式状态
推进（C1 的指纹断言当场就会红）。节点存在的价值是两条，都不是装饰：
① 那 5 条边要有端点（边是包络的真源）；② "心情"因此在 self 图上**可达**——
调制器脉冲沿边点亮它，与 P6 给 12 个调制器补的那条证据同一种（D-15 的教训：
属性写进节点 ≠ 图上可达，这里是反过来：边写进图 ⇒ 真的可达）。

### 3 单向性怎么证（闸门 C 组，三个独立角度）

1. **数值**：反复 `current_mood()`/`mood_context()`/`afterglow()` + 连记 3 次
   `mood_event`，`fingerprint()` 一字不动 —— 12 通道的 tonic 与 pulse、4 个需求
   水位、**全图每个节点的 activation**、边数。
2. **结构**：`心情` 出边 == 0；config 边表里没有任何一行以心情为源（A4/A6）。
3. **源码**：`personality_baseline.py` 里 `apply_delta(` / `set_value(` / `pulse*(` /
   `apply_event(` / `emit(` / `release(` / `tick_decay(` / `mark_active(` 全部零命中（C3）。

**必须区分清楚的一件事**（否则这条要求会被误读成"不许有回路"）：
`Modulator → 心情 → 行为（社交动机/主动发起）→ 结果 → 事件 → Modulator` 这条环
**是规格要的**——人格只能经 Modulator→behavior→outcome→disposition→trait 改变。
它经由真实世界的一拍（一次行动、一次交互），不是同一拍里的数值反馈。
P11 禁的是后者：读完心情，激素不许因此动一下。

### 4 cap 为什么是 0.35，以及 B3 撞出来的一个反直觉事实

选尺度的依据是同表里已有的两个数：一次正反馈 +0.12、一次负反馈 −0.18。0.35 ⇒
"激素包络最多相当于两次强交互事件"，能把底色推离中性，但不能替用户刚说的话定性
（§15：mood 可派生，但不得成为万能变量）。出厂 5 条边 |w| 和 = 1.10，所以 dev 平均
要 0.32 才够到界——正常波动（dev 0.05~0.20）落在 0.02~0.07 这一段，与"底色"相符。

反直觉的那条：**把 12 个通道全钉到量程上限，包络并不饱和**（实测 raw=0.0292）。
原因是多数通道过了过载点后 `dev` 翻负（P8 的 inverted-U），正负贡献互相抵消——
所以"钉满 ⇒ 顶到 cap"是个错误的测试设计。改成的正确断言（B3）：把各通道钉在
**它自己的响应峰值**（overload 点）上、再把边加宽到 Edge 的权重上限 2.0，
raw 越过 cap、term 被钳住、`capped=True`。这才是"界"真正的界。

顺手抓到并修掉的一个自身 bug：`capped` 原先拿**已 round** 的 term 与未 round 的
total 比，1e-5 的取整差会被报成"钳过"。现在用未取整值判、再取整输出。

### 5 结果→调制器 这条链现在从头到尾没有硬编码

`行动结算 → reward.release() → ModulationEvent →(图上 `事件类型:x -[影响 w]-> 调制器`)
→ tonic/phasic →(P8 参数曲线 / P9 三通道 / P10 昼夜 / **P11 心情包络**)
→ 语气与动机 →(具体结果入体验轴 `experience` + trait 慢学习映射)`。

P11 补的是最后一处系数外溢：`reward.modulation()` 里的
`1.0 / 0.60 / 0.15 / 0.25 / 0.30 / [0.4,1.8]` 与三个调制器名，迁入
`config.modulator_system.learning_modulation`（真源），`reward.DEFAULT_LEARNING_MOD`
只是裸装配兜底（D4 断言两边同数）。闸门 D3 的源码扫描盯住函数体：这六个字面量
再出现就算失败（docstring 里作为公式说明出现是允许的，扫描已跳过文档串）。

### 6 死代码账 §2 的处置（P11 收口）

| 项 | 处置 | 证据 |
|---|---|---|
| D-1 血清素无效力 | ✅ 已闭（P6 给的工作，P11 复核）：投影出 **2 行**系数 | 闸门 F1 |
| D-2 tonic 无读者 / level 含 phasic | ✅ 已闭（P3），P11 复核结果侧不再读 level | 闸门 F2 |
| D-3 topk_scale / reignite_every 假旋钮 | ✅ 已闭（P8：`continuous_cognition.py` 两处真消费者），P11 复核 | 闸门 F3 |
| D-4 `need.social` 空乘数 | ✅ 已闭（P9：`_build_signals` 产 `need.*`），P11 复核 | 闸门 F4 |
| D-7 `网络` 关系未注册 | ✅ **本阶段闭**：注册进 `relation_ontology`，并加进 `HUB_ALLOWED["Self"]`（hub 守卫是 warn-only，改的是"每次启动少一条真警告"，动力学一字不动）。**存量那两条 `关联` 不改**（load 不走规范化，改它们要重写图谱数据） | 闸门 F5 + 实测新装配出边落盘为 `网络` |
| D-10 `novel_experience` 有表无源 | ✅ **本阶段闭**：emit 点补在 CC 采纳焦点处（`_last_focus_sig` 更新旁），判据复用**已算好的** `novelty = 1 − 连续性`，阈值 `novel_min_novelty=0.5` 在 config | 闸门 F6 |
| D-11 `unexpected_event` 恒 0 | ✅ **本阶段闭**：注册第 8 个图采样器 `graph.cognitive_events`，读 `事件类型:prediction_violation` 的 activation/5（与其余 `graph.*` 同一个量程换算）。实测点亮该节点 4.0 后张力从 0 抬起 | 闸门 F7 |
| D-5 `action_tendencies` 三项仅展示 | ⏸ **待裁定**：`field.action_tendencies()` 的读者只有 `cognitive_context`（渲染）与 `/api`；config 注释写的是"只进行为竞争的调制因子"，**实际没有进竞争**。要么接进行为打分（改自主决策的数值），要么把注释改成"派生展示量"。R2 不擅自选 | — |
| D-8 `need_urgency()` 无生产读者 | ⏸ **已定性，待裁定去留**：`urgency` **字段**有真读者（`need_salience()` 取 max(urgency, gap) 是唯一口径，闸门 F8 盯住"只有一个口径"）；那个**访问器**至今零生产调用。删掉是 2 行 + 1 处测试，但它也是前端 `n.urgency` 的语义锚，留着不影响任何数值 | — |
| D-9 `tension_drive_state.json` 不存在 | ⏸ **改判为"路径是活的，只是从没跑到"**：`_load_state()`(装配时) 与每 `max(2,autosave_steps)` 步 `save_state()` 都接好了；`data/server.log` 停在 09-14，而该重构在 09-20 ⇒ 生产还没跑满 24 拍。**这不是 R2 造的平行状态库**（它是 09-20 那次 Cognitive Field 重构的既有落盘，存的是张力/驱动/网络/EMA 连续性，不是语义）。要不要退役它、改由图谱+快照承载，属**架构级裁定**，R2 只登记不动 | — |

### 7 待用户裁定（P11 新增/沿用的四条）

1. `MOOD_DECAY_PER_HOUR=0.15`（0.0025/min）**慢于**调制器 decay（0.02~0.06/min），
   与 §27"phasic/心情 < 调制器 < disposition < trait"的次序相反。改数（例如 0.6/h
   ≈ 0.01/min 仍偏慢；真要"快于调制器"得 3.6/h ⇒ 一次事件 17 分钟就基本忘光）
   还是接受"心情按小时计"的设计意图？R2 只把数迁进 config，一个没动。
2. D-5 的 `action_tendencies`：进竞争，还是改注释成展示量。
3. D-8 的 `need_urgency()` 访问器：删，还是留作调试读面。
4. D-9 的张力落盘：退役还是保留（涉及 09-20 那次重构的既有决定，不在 R2 范围）。

### 8 P12 交接清单

① 17 个场景测试（规格 §"≥17 named scenarios"，Events→Modulators→Graph→
Drive/Need→Cognitive Field→Action/Dialogue→Outcome 全链，含本阶段的心情段）；
② 实验/调试模式：**必须走真实事件管线**（§26），地基已给齐——
`InternalState.set_clock()`（P10）+ `source="manual"`（夹速豁免，P3）+
`mood_facts()`/`explain_subgraph()`（观测面）；
③ `dump_modulator_state()` 里补一行心情包络（现在只有调制器侧看得全）；
④ 附六那条前端项：`state()` 同时有 `conc`/`level`，界面仍只画一根条；
⑤ 门禁基线本身待用户批准重跑 `--save`（现在这 130 处差异是历史遗留，不代表正确）。

---

## 附一二：P12 落地记录（2026-09-21）

### 1 实验台的形态：驱动器，不是第二实现

`modulator_lab.py` 的验收判据不是"它能跑"，而是**它与真实管线不可区分**：

- 台1：`lab.inject(...)` 与直接 `engine.emit(...)` 打出的每条 delta **逐位相同**；
- 台2：`lab.advance(90)` 与**手工**按生产函数序列（tick_decay → 昼夜/需求漂移 →
  需求靶值收敛）重放 18 个 5 分钟拍得到同一水位（±0.02）；对照组自己注入
  步进时钟——否则 Δt≈0，"重放"是假的（这个坑当场踩过一次）；
- 台3/台4：open/close 成对、close **原样交还**原时钟（不硬设 None，否则会踩掉
  别的回放会话的注入时钟）；`advance` 拒绝未 open 的会话，宁可报错不静默失效；
- 台7：所有动作零新边（禁令 11）；台8：dump 的 `mood_envelope` 只报投影的
  `term` 分量，**不报心情合计**——合计的唯一作者仍是 persona。

HTTP 面（`/api/internal/lab/{observe,chain,event,outcome,dial,time}`）六个路由
全部是转调；`time` 的 advance 改的是**活体状态**，回滚手段仍是 snapshot 端点。

### 2 场景闸门跑出来的四条"断言错、物理对"

第一遍 7/17，全部失败的原因都值得记下来——它们是这个系统真实动力学的
测量值，比出厂表里的静态数字更有说服力：

1. **make_env 必须复现 app.py 装配的最后一步** `project_coefficients`
   （app.py:1095）。少了它，边在、节点在、explain 全对，但参数通道整体是
   死的——**"图是效力真源"的另一面：投影器才是"边→效力"的编译器**。
   这一行补上直接修好 10 条断言。
2. **需求漂移给了调制器平衡点**：dial(1.00) 一整天后多巴胺停在 ≈0.565，
   不是基线 0.5——是"基线 + Σ(漂移边 w×显著性)"的不动点。场景 16 的断言
   因此改成"落在平衡点邻域、绝不留在旋钮值上"。同理，场景 2 想"清场"时
   advance 太久反而会把多巴胺**托在基线上方**，负 RPE 的 factor 折扣只能
   **相对事件前状态**来断言（`f_clean − f > 1e-4`），不能断言绝对 <1。
3. **同一通道上的反向边会互相吃掉**：口渴式缺口两小时，皮质醇净 **−0.02**
   ——`掌控需求-[+0.15]` 输给了 `安全需求-[抑制 −0.25]`（默认显著性下）。
   多对多是真在竞争的，不是装饰；断言改为"多巴胺升、皮质醇净不升、
   两条边符号相反都在场"。
4. **社交好事不打多巴胺快通道**：6 次 accepted 的 factor **恒为 1.0**
   （`reward_rpe` 的 goal_relevance≥0.5 门控把社交源挡在外面）。场景 15
   因此变成一条**门控证明**：社交经历走催产素慢通道、factor=1；自我完成
   才有 phasic>0、factor>1；且 `learning_signal = 原始效价 × factor`
   逐字可复算，factor>1 之后 trait 步长仍是 1α（快慢通道互不冒充）。

### 3 回归范围（用户指示："测试少干一点"）

- `test_r2_scenarios.py`：**17/17 · 71 断言全过**。
- `test_mood_one_way.py`（P11 闸门，internal_state 本阶段有改动）：复跑 **45 项全过**。
- `py_compile` app / modulator_lab / internal_state：过。
- **未跑**：全量 `run_tests`（64 文件）、`bench --compare`、`verify_p0_all`、
  `graph_integrity_audit`、`scan_modulator_curves`——P12 未触碰扩散数学、
  能量守恒与图谱存取路径；如后续任何改动碰到这些，按老规矩全量补跑。
- 交接④（前端第二根条）单独处理；交接⑤（bench 基线 `--save`）仍待用户裁定。

