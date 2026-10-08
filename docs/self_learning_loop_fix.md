# Self Model 学习闭环修复记录（2026-09-18）

> 目标：让 **Experience → Outcome → Candidate → Evidence → Self Graph → Future Behavior**
> 这条闭环真正转起来——不是"图里多出几个节点"，而是**学习后的图能改变未来行为**。

---

## 1. 三个断点的定位（先诊断后动手）

| # | 断点 | 位置 | 根因 |
|---|---|---|---|
| 1 | **turn 级反馈被静默丢弃**（outcome 恒 0/0 的主因） | `disposition_store.apply_outcome` 旧版首行：pair 不存在即 `return` | 旧设计"turn 级绝不新建 pair"，而 pair 只由 6 条出厂先验 + 反思产出创建。绝大多数 (情境,行为) 组合的真实正/负反馈找不到落点，蒸发。 |
| 2 | **自主行动结果不流向人格** | `autonomy._apply_reward` 只进 mood/note_outcome 瞬态链 | 任务 §5 的 B/C/D 三个自我来源（目标成败、自发重复行为）没有写回 disposition 的通路——人格证据只剩用户反馈一种，闭环先天缺三条边。 |
| 3 | **belief/preference 恒 0 是设计后果，不是 bug** | `_extract_self_candidates` 的 P0-7 约束（只收 FAS 第一人称主语边） | 用户说"我喜欢X"被正确隔离在用户模型，不进自我模型；而 FAS 自己的话不走记忆抽取。来源天然稀疏，且首次出现必被 0.5 门槛挡下（这本身符合 §7"一次经历只产生 candidate"）。旧代码没有 trace，导致"0 产出"无法归因。 |

**关键设计原则的贯彻**：全程**未降低 importance threshold、未降低 stable 门槛**。修的是"证据根本进不去"和"没有 trace"两件事。

---

## 2. 改动清单（按模块）

### `disposition_store.py`（核心）

- **outcome 四态**：`positive / negative / neutral / ambiguous`。正负标记同现 → ambiguous（不强行二选一）；neutral/ambiguous **只计数不动强度**（中性绝不被当正向奖励）。
- **多来源证据 + 完整 provenance**：每条证据带 `source ∈ {user_feedback, self_goal_success, self_goal_failure, repeated_action, reflection, environment, baseline}`；pair 档案含 `positive_count / negative_count / neutral_count / ambiguous_count / sources{计数} / first_observed / last_reinforced / confidence`——全部长在 pair 节点的 `extra_attrs` 上，**无平行 JSON 持久层**。
- **来源感知步长**（`SOURCE_DELTAS`）：用户反馈基准（+0.08/−0.12）；自我目标成功 +0.05、失败 −0.06（温和，靠反复积累）。**人格不收敛成"讨用户喜欢"**。
- **首条经历建 candidate**：`apply_outcome(..., allow_create=True)`——一条真实 positive/negative 经历即可在封闭词表内建立 candidate（13 情境 × 10 行为有界）；neutral 不建。一次经历只产生 candidate。
- **stable 保护与可塑性**：单次负反馈只降权、不删、不降级；负证据累积（neg≥3 且 ≥0.7×pos）→ **降级回 candidate 并设强度地板**（不被同一手证据连坐删除）。稳定人格可以被重新塑开，但不被一次冲突抹掉。
- **晋升只数真实经历**：`promotion` 看 `positive_count+negative_count ≥ 4`（修掉旧版把出厂引用计入 `evidence_count` 的历史隐患——真实图里 pair 被灌水到 ev=5~8，一次反馈即可误推 stable）。
- **全链路 trace**：每次证据落账/建立 candidate/promotion 判定/降级都打日志，含 outcome、source、前后强度、四态计数、来源分布。
- 词表新增 `行为:探索`（自主探索类意图的图落点）。

### `autonomy.py`

- `_apply_reward` 增加 `_record_disposition_outcome`：行动意图 → (情境:主动发起, 行为) 映射，成功 → `self_goal_success` positive 证据，失败 → `self_goal_failure` negative 证据；cancelled 不算。人格获得第二个独立来源。

### `app.py`（turn 级）

- `apply_outcome` 传 `source="user_feedback"` + `allow_create=True`，并记录该条证据是否成功落账。

### `reflection_engine.py`

- 表达事件回写扩到四态、标注 `source="reflection"`；反思来源不建 pair（与 turn 级互补，共用同一套证据数学）。

### `self_model.py`

- `evaluate_importance` 打全因子 trace + 驳回原因；`process_turn` 在候选提取/强化/驳回三个环节打 trace。"0 belief/preference" 现在可以从日志直接归因到"来源稀疏"或"首次出现被门槛挡（by design）"，而不是黑箱。

### `personality_baseline.py`

- **未动**——保持其定位：出厂低强度先验，bootstrap 只在强度低于先验时抬升；学习出的 stable 强度（0.5→1.0）天然超过先验（≤0.45）。

### `classify_outcome`（disposition_store 内）

- 增加 ambiguous 判定。

---

## 3. 三个时间尺度（§八）

| 尺度 | 载体 | 行为影响 |
|---|---|---|
| 瞬时 | emotion 节点激活 / persona mood / 本轮 outcome | 语气、当轮抑制 |
| 中期 | **candidate** disposition（强度封顶 0.30） | 以**低权重**参与竞争 |
| 长期 | **stable** disposition / preference / belief（全强度） | 明显改变行为选择 |

candidate 不再等于"完全不影响行为"——它被 `CANDIDATE_CAP=0.30` 封顶后仍以低权重参与竞争（`tendencies_for` 读出、`dialogue_decide` 计入 desire、`autonomy._score` 计入 tendency 分量），既不是一次经历改人格，也不是非等 stable 才生效。

---

## 4. 用户模型与人格的分离（§十/§十三/§十一）

- `interaction_count` / `attention_areas` 留在 **用户关系节点**——是"她对用户的认知"，不进人格。
- `偏好: X` 仍是 Self Graph 的边/节点（`Self-[喜欢]->X`，旧版语义保留）；**一次自发行为不产生 preference**——preference 需要明确自我陈述或多次证据，仍由 importance 门槛把守。
- belief 门槛原样保留（高门槛是特性不是缺陷）。

---

## 5. 不外显的机制（§十四）

所有计数、provenance、trace 只进日志与图属性，**不进语言层 prompt 的输出要求**：`behavior_tendencies` 注入的是行为名（respond/share）与强度，模板里无"根据我的 Self Graph/我的 disposition"类措辞。她只是更自然地做出不同选择。

---

## 6. 验收：`tests/test_self_learning_loop.py`（8 项 / 35 断言全过）

| 测试 | 锁住的性质 |
|---|---|
| T1 | 明确正反馈 → candidate 生成、evidence+1、provenance 完整、importance 全因子可追踪；**首次自我陈述仍不过门槛**（§7） |
| T2 | 一次经历不能 stable + **旧 pair evidence 灌水回归护栏** |
| T3 | 4 次一致经历 → candidate→stable，**激活边权同步上升**（图权重承载人格） |
| T4 | 自主目标成功无用户反馈也能建 `explore@主动发起` 证据；来源=self_goal_success；失败步长温和；cancelled 不算 |
| T5 | neutral/ambiguous 不建 pair、不动强度、不混入 positive |
| T6 | stable+单次负反馈不删不降；累积负反馈 → **降级 candidate 而非删除**，强度有地板 |
| **T7** | **同情境 before/after**：学习后 tendencies_for 以 full strength 出现、`dialogue_decide` 的 desire 显著上升、candidate 期被 cap 到低权重 |
| T8 | 无平行 JSON 持久层；KG save/load 往返证据完整（**图是唯一长期真源**） |

**端到端管线仿真**（模拟 app.py turn 级路径 4 轮）：旧代码 4 轮全部丢弃（pair 从未建立）；新代码 4 轮积累 `candidate(0.33→0.49) → stable(0.57)`，`tendencies_for` 立即读出全强度倾向——这正是 `dialogue_decide`/`autonomy._score` 消费的那个读点。闭环转通。

**全量回归**：24 套既有测试 + 本次新增 = 全过，无失败。

---

## 7. 遗留与下一步

1. **真实环境验证需要时间**：机制已通，但 stable 的"她"需要在真实多轮对话 + Minecraft 自主运行中积累数天才会出现——这是设计使然（门槛不该绕过）。日志可查。
2. `self_model.py` 的 upsert 路径与 disposition 路径仍是两套候选语义（belief/preference vs pair）——本任务未合并它们（符合"不要重构"约束），§12 的统一 Self Learning funnel 可作为后续。
3. `repeated_action` 来源已在词表里，但目前只有 smoke 测试写入；自发行为→该来源的专用回写（区别于 self_goal_success）留待下一步细化。

---

## 8. 追加（2026-09-18 第二阶段）：激素—奖赏—disposition 融合

把 §1 里"outcome→strength 直连"升级为**双路 outcome → RewardEvent → 激素 → 学习率调制 → disposition**：

- **新增 `reward.py`**（transient）：`classify_social_outcome`（accepted/amused/engaged/continued_discussion/rejected/ignored/ambiguous）、`classify_self_outcome`（discovery/goal_success/progress/blocked/cancelled）、`RewardSystem.evaluate/release/modulation`。不落文件、不写图。
- **`internal_state.py`**：激活死字段 `_expectations`（RPE 预期）+ `pulse_dopamine`（phasic）+ `tick_decay`（首个 `decay_per_min` 消费者）。激素是运行时内部状态，**不是人格**。
- **`disposition_store.apply_experience(behavior, context, social_outcome, self_outcome, hormone, …)`**：`learning_signal = social_valence×0.4×learn_pos + self_valence×0.6×learn_pos`，再 × 激素调制因子[0.4,1.8]。**自我权重高于社会**，人格不由用户满意度主导。`apply_outcome` 保留为兼容投影。四态计数、`learning_ledger`（可复算）、工程阈值注释（"not a psychological claim"）齐备。
- **§十一 disposition→attention**：turn 内扩散前点亮当前情境节点，`情境-[激活 w]->行为` 边把倾向转成行为节点实时激活；`tendencies_for` 混入 activation。
- **§七 reflection 降权**：`_process_dispositions` 新增 `_evidence_traces_to_event`——LLM 候选证据必须命中真实事件文本，否则拒绝留痕；LLM 仍只提名不决定 strength/promote。
- **`nlp_processor` prompt**：行为倾向注入去数值、用中文行为名+定性词（§十六不外显机制）。

验收：`tests/test_reward_disposition.py`（A–H，24 断言）+ 全量 25 套通过。cancelled 不记负、单次社会反馈温和、发现类自我奖赏最强、stable 不被一次拒绝抹除、激素不直接建 disposition、无证据反思被拒、倾向边真把激活传到行为节点——逐条见该测试。

---

## 9. 追加（2026-09-19 第三阶段）：通用经验时间轴 + 保守因果发现

新增 `experience.py`（核心机制，零环境字段、零 LLM）：`ExperienceEvent`（ACTION / OBSERVATION / SELF_STATE_CHANGE / COGNITIVE_EVENT 四类，字段刻意精简）、`ExperienceTimeline`（事件级去重合并 + 有界持久化 `data/experience_timeline.json`）、`CausalLearner`（主体相关性门槛 → 候选关联 → 重复聚合 → 假设 → 够稳才晋升 KG，反例记 contradiction、confidence 平滑永<1、假设可降级 weakened）。

接线（全部可选注入，不破坏既有功能）：autonomy `_execute` 发 ACTION 事件；minecraft_embodiment 轮询差分发 observation/self-state 事件（§五：只报观察不归因）；app turn 级发用户话语/她的回答/好奇触发/奖赏四类事件；debug API `/api/experience/timeline` + `/api/experience/causal`。

验收：`tests/test_experience_timeline.py`（A–F + 通用性红线 + KG 晋升 + autonomy 注入，全过）；全量回归 26/26。关键数字：3 次重复成假设（conf≥0.6）、8 次且无反例才晋升 KG（conf≥0.8）、无关事件因主体相关性门槛永不归因。

---

## 10. 追加（2026-09-19 第四阶段）：交流决策层重构为图谱行为竞争器

`dialogue_decision.py` 从"固定公式决策器"（ACT_PULL/EXPECT_PULL→标量 desire→阈值）改为**图谱行为竞争器**：

- **先验入图**：`BEHAVIOR_PRIOR_SEEDS`（含 silence/explore 的出厂行为学常识）以 `情境-[先验 w]->行为` 边种入图谱（幂等）；经验学习继续走 `情境-[激活]->行为` 边。两通道分离：先验=出厂基线偏置，激活=人格生长通道。
- **候选收集**：10 个行为词表全员候选（silence 是正式候选非缺省）；探索候选激活 = 0.6×好奇评分 + 0.4×行为:探索实时激活；`CuriosityDrive -[驱动]-> 行为:探索` 通路入图（好奇经扩散进行为候选，§九）。
- **调制器（B类）**：话题共振/回应期待/极短输入/负反馈/刚说过/沉默倾向抑制/激素（`horm_n=0.5+0.5×factor`，只缩放经验+实时分量，silence 豁免）。
- **硬约束（C类）**：工具结果强制汇报；话题终止（抑制期禁探索）；探索打断门槛（须超表达 urge 0.15）；no_question 只压**无学习支撑**的机械追问（stable 学习背书的 ask 胜出时约束让位留痕）。
- **表达层分离（§八）**：行为意图与表达方式（minimal/normal/question/search）解耦，输出 `expression` 字段。
- **兼容**：`desire` 保留为社会表达 urge（不再是裁决依据）；decision 串/签名不变；旧 16 断言在**图路径**下全过。

验收：`tests/test_behavior_competition.py`（10 场景 31 断言）——含"图真的在读"三铁证：改先验边→胜者改变；学习闭环→ask 从先验劣势反超 respond；激素只调增益不翻胜者不建行为。全量 27/27。
