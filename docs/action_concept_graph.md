# Action Concept 图谱一等公民 —— 行为/行动决策重构（2026-09-20）

## A. 修改文件列表

| 文件 | 改动 |
|---|---|
| `action_space.py` **（新）** | ActionSpace：候选收集（图谱扫描非枚举）、概念提议三层去重（规范化/别名边/嵌入相似）、生命周期（proposed→observed→established→weakened，数值规则在 config）、统一 Action Schema、[ActionTrace] 格式化、行动注册表视图 |
| `tests/test_action_space.py` **（新）** | 30 断言：候选来自图谱（无词表图仍产生行动）、供性边真实参与 diffuse_step、提议去重、生命周期迁移、竞争集成（新概念可胜出+touch）、双粒度学习、resolver 提议通道、真实节点形态防重 |
| `config.py` | `action_space` 段（供性边种子表/本体/阈值/生命周期参数）；relation_ontology 登记 别名/实施/包含/属于/可实现为；modulation 新增 `action_space.candidate_topk_scale`（DMN 宽候选）与 `action_space.affordance_gain`（CEN 聚焦已学倾向） |
| `dialogue_decision.py` | 候选宇宙 = LEGACY 词表 ∪ `action_space.collect_action_candidates()`（prior/learned/live 全从图上读）；EXPRESSIVE/_DAMP_* 动态化（新概念按 expressive 属性受调制）；新输出 `action_concept`/`secondary_actions`；胜者 touch 生命周期；decision 字符串契约不变 |
| `disposition_store.py` | 词表白名单 → **行动注册表校验**（`_known_action_node`：LEGACY ∪ 图谱行动节点含 proposed/action_concept）；`_activation_edge`/`remove`/`tendencies_for`/evo 日志全部走注册表；tendencies 项新增 `label`（概念的图谱中文名） |
| `action_concepts.py` | `EXECUTOR_TO_CONCEPT` 反查表（action_type→概念，inline/缺口不列入） |
| `action_system.py` | spec 新增 `concept` 字段（normalize 时反查）；`_record_disposition_outcome` **双粒度学习**（粗 explore/share 保留 + 概念级 `倾向:<CONCEPT>@主动发起` 新增）；`_write_action_memory` 建 `行动_*-[实施]->概念` 边 |
| `action_resolver.py` | 语义/LLM 命中**回写表面形式**（表达方式/别名边 + touch）——"词表只是 seed"补上学习环；`_llm_disambiguate` 可提议新行动概念（is_new → proposed 入图，不产执行意图）；桌面/MC 入口透传 `action_space` |
| `app.py` | 装配 ActionSpace；Drive bootstrap 后种供性边/本体；dialogue_decide 与两 resolver 入口注入；回合尾 `[ActionTrace]` 单行 |
| `nlp_processor.py` | tendencies 渲染优先 `label`（新概念的图谱中文名） |
| `cognitive_context.py` | decision 分区透传 `secondary_actions` |

未动：diffusion_engine（现有传播已覆盖新边）、curiosity_engine、speech_act_graph（细化边自动惠及新行动节点）、ActionManager 状态机、skills/bot.js 物理面、Drive/CognitiveField 四层。

## B. Action 图谱 Schema

**节点**（`extra_attrs.type ∈ {behavior, action_concept, action_proposed}`，`action_family` 为分组不入选候选）：
- 沟通行动：`label=disposition, graph_space=self`（λ0.01 常驻、软遗忘豁免、进 FAISS 零件过滤=设计如此，靠供性边点亮）；旧 `行为:X`（key 字段兼容）与新 `行动:<短键>` 共存
- 具身概念：沿用 action_concepts 既有节点（`label=procedural, semantic`，不进 name_to_node/行动队列——执行必须经 Intent，该契约保留）
- 表达节点：`label=infrastructure`，`表达:<归一短语>`，FAISS 全量索引用作提议去重

**边**（全部登记 relation_ontology，参与正常 diffuse_step；不加白名单也不会不传播，只是语义被劫持成"关联"）：

| 关系 | 方向/类别 | 权重语义 | 学习 |
|---|---|---|---|
| 先验 | 情境→行动, cognitive | 该情境考虑该行动的可发现性 | 静态种子；personality_baseline 可抬 |
| 激活 | 情境→行动, cognitive | **学到的倾向 strength（人格载体）** | apply_experience 写权（新概念同通道） |
| 驱动 | Drive→行动, cognitive | 内驱压力 | config 种子表（数据非代码） |
| 倾向 | 情绪/回应方式→行动, cognitive | 情绪可发现性 | config 种子 + 既有 speech_act 倾向边 |
| 别名/表达方式 | 表达→行动, semantic | 表面形式归属 | 语义命中回写、提议时登记 |
| 包含/可实现为 | 族→行动 / 行动→行动 | 本体组合 | 按需，不预建大表 |
| 实施 | 行动_*(episodic)→概念 | 事件溯源（不做开关） | 结算时建 |

学习语义总结：**学习的是 Context→Action 边权与 pair 证据，从不改行动概念的定义**；概念本身随证据只动 lifecycle。Hebbian 不接行动边（避免双学习系统）。

## C. 完整行动链路

```
用户输入/感知事件
→ NLP/speech_act → 情境节点点亮（app 回合内 +1.2 激活）
→ Drive/Tension（CognitiveField 每拍）写 Drive 节点激活
→ diffuse_step：情境-[先验/激活]->行动、Drive-[驱动]->行动、情绪-[倾向]->行动
→ ActionSpace.collect_action_candidates（激活 ∪ 活跃供性源；Top-K×DMN 宽度调制；
   激活边权×CEN 增益调制）
→ dialogue_decide（语言竞技场）：act=0.55·prior+0.45·(learned+live)·hormone(仅 expressive)，
   与探索候选同场竞争，硬约束裁决 → decision 串 + winner 概念节点 + secondary_actions
   （具身竞技场：autonomy 候选 _score_action（exploration_rate 调制）→ ActionManager 闸门）
→ 执行：language（LLM 只实现已选行动）/ minecraft（ActionManager→skills→bridge）
→ Outcome：expression_feedback / reward(valence) / _settle 失败账
→ 学习回写：apply_experience → pair.strength → 情境-[激活]->行动 边权（粗粒度+概念级双写）
   + 概念 touch（生命周期推进）+ 行动_*-[实施]->概念 溯源边
→ 下一轮竞争读新的边权/新激活 —— 闭环
```

## D. 向后兼容

- **decision 四值契约**、`行为:X` 节点 id、`倾向:<key>@<ctx>` pair 命名、`_activation_edge`、`tendencies_for` 返回结构（新增 label 为附加键）、`/api/*` 字段：全部不变；49 个旧测试 + verify_p0×2 原样通过。
- 旧数据零迁移：9 天的人格 pair 与新概念 pair 同命名空间同载体。
- **回滚**：`config.action_space.enabled=false` → collect 返回空、提议通道关闭，竞争退回纯 LEGACY 词表宇宙（旧行为逐位复现）。
- Minecraft 行动能力不变：执行闸门、否定优先、零 LLM 域承诺、skills/bot.js 全未触碰。

## 关键设计判断（审计驱动）

1. **`行为:*/情境:*/激活边` 本来就是图谱原生 Context→Action 学习机制**——重构不是替换它，而是把"遍历 10 键 + 白名单校验"两处封闭点泛化为"扫描行动节点类型 + 注册表校验"，使新概念自动获得同一竞争与学习通道。
2. 语言/具身保留**两个竞技场、一套 schema/注册表/trace/学习层**：decision 契约与 ActionManager 安全闸门（否定优先/命令性门）各有强约束，强行合并会破坏两侧锁定契约。
3. 具身概念节点"不可见+不经行动队列"是 2026-09-19 已定契约（执行必过 Intent）；本次经 驱动/实施 边把它接进认知侧扩散与学习，**不**反向解锁执行队列。
4. LLM 提议是来源之一：fast→seed→embedding→LLM 提议的优先级链，且提议概念 executor 为空=能力缺口，执行面零污染。

## 调参与观测

- 候选阈值/Top-K/提议相似度/生命周期门槛全在 `config.action_space`；网络调制在 `modulation.params`。
- 回合日志两行：`[CognitiveField] tension=… CEN=… depth=…` 与 `[ActionTrace] ctx=… cands=[k:strength,…] → winner`；完整五段 trace 用 `action_space.trace(...)`；`GET /api/drive` 含行动场状态。
