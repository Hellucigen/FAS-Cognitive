# Curiosity 机制重构 — 阶段 1：架构分析

> 状态：**已实施**（2026-09-18）。本文档先作为方案评审稿，确认后按 §6/§7 落地。
> 实施记录见文末 §9。全部回归测试通过（新增 tests/test_curiosity_refactor.py 41 条断言）。
> 目标：按 16 条设计原则重构好奇心机制——从"未知→提问"的一次性状态机，
> 改为"持续探索驱动力 + 统一行为竞争"。
> 分析基准：工作区 2026-09-14 快照（含运行图谱 `data/runtime_graph.json` 实测）。

---

## 1. 当前架构的实际调用链

好奇心目前有**三条互不相通的通路**，这是最重要的现状事实。

### 通路 A：对话回合内的"提问通路"（app.py process_nlp，强制执行）

```
用户输入
  │
  ├─ [0c] app.py:2148  is_curiosity_active(kg)?
  │        └─ 是 → resolve_curiosity(kg, text)     ← 用户输入被无条件视为"回答"
  │
  ├─ [0d] app.py:2159  compound_phrase_handler 过滤关系型短语 → curiosity_nodes
  │
  ├─ [0e] app.py:2237  check_and_inject_curiosity(kg, engine, nodes, edges, text)
  │        └─ 检测未知概念/未知关系/情绪 → 直写 未知*/需要确认 节点激活
  │           返回 curiosity_info{triggered, trigger_type}
  │
  ├─ [扩散] app.py:2280  分阶段扩散：
  │        ├─ diffuse_round(max_steps=3)          ← "3 步定时器"确保信号到达提问节点
  │        ├─ should_generate_question(kg, config)  ← 图驱动阈值判定（这点是对的）
  │        ├─ generate_question(nlp, info)         ← LLM 生成问句
  │        ├─ set_curiosity_question → 等待回答节点 +2.0，状态挂 extra_attrs["curiosity"]
  │        ├─ cleanup_curiosity_activation         ← 好奇节点 ×0.3 防过冲
  │        └─ diffuse_round(剩余步数)
  │
  ├─ [独占] app.py:2634  llm_answer = curiosity_question
  │        ← 好奇问题替代普通回答，本轮不再有其他输出（无竞争）
  │        ← 反思也被跳过（app.py:3100）
  │
  ├─ [沉淀] app.py:2475  _auto_consolidate_curiosity_knowledge（trigger="curiosity"）
  │
  └─ [记录] app.py:2980  chat_log.record(curiosity=...) + response_data["curiosity"]
```

### 通路 B：驱动力通路（drive_engine.py，被两个系统消费）

```
DriveEvaluator.evaluate()（每轮 app.py:3134 调用，5s 缓存）
  └─ _eval_curiosity (drive_engine.py:83)
       CuriosityDrive.activation =
           min(2.5, 未知信号节点数 × 0.5)            # CURIOSITY_SIGNAL_NODES 9 个
         + min(1.5, 未知节点激活和 × 0.5)
         + min(1.0, 知识缺口激活 × 0.8)              # ← 节点不存在，恒 0（死路径）
         + min(1.0, UnknownObject 数 × 0.3)
         + min(1.5, 链路均值 × 1.2)                  # ← 好奇/需要学习/学习目标/主动提问
       → 直写 CuriosityDrive 节点激活 + mark_active   #    其中 3 个不存在，恒 0
       ↓
   消费者 1：autonomy._score (autonomy.py:510)
       observe/approach/explore/collect 的 drive 分量 = CuriosityDrive/5.0
   消费者 2：action_engine._check_ask (actions/action_engine.py:133)
       CuriosityDrive ≥ 1.5 且未知信号活跃 → Ask intent（confidence=0.5+act×0.1）
```

### 通路 C：自主行动通路（autonomy.py，唯一真正做了行为竞争的）

```
cc.tick（每 4 tick ≈10s）→ autonomy.tick()
  ├─ _perceive ← minecraft_perception（UnknownPlayer/Entity/Block 节点在图里）
  ├─ _candidates (autonomy.py:388)
  │    └─ 未知实体 → observe/approach 候选，basis=[UnknownEntity_x, "好奇"]  ← 硬编码
  ├─ _score (autonomy.py:505)  ← 真竞争：drive/attention/novelty/tendency − risk/recency/failure
  └─ score ≥ 0.35 才执行；否则如实 idle
```

### 图基础设施（bootstrap，app.py:347）

- 10 个 declarative 节点：`未知信息/未知概念/未知关系/未知属性/未知事件/需要确认/好奇/等待回答/知识完善/回答完成`
- 4 个 procedural 节点（带 execution 沙箱代码）：`生成好奇问题/等待用户回答/更新记忆/结束好奇状态`
- 7 条信号边 `CURIOSITY_SIGNAL_EDGES`（curiosity_engine.py:97-105）：五个 `未知* -[trigger_curiosity 0.5]-> 好奇`、`需要确认→好奇`、`好奇 -[0.6]-> 生成好奇问题`
- 另有 `未知信息 -[触发 0.8]-> 网络搜索 -[辅助 0.8]-> LLM回答`（app.py:382-383）

### 其余消费点

| 位置 | 用法 |
|---|---|
| `continuous_cognition.py:175-199` | `_pulse` 里 `curiosity_active`（好奇/未知信息/生成好奇问题 >0.5）→ affordance +0.25 → CI 表达评分。**这是全库最接近新架构的用法：好奇只是表达欲望的一个调节因素** |
| `continuous_cognition.py:280` | CI basis 含 未知信息/好奇 时走特定表达分支 |
| `dialogue_decision.py:134-137` | `curiosity_active` 作为回应欲望的加分因子（"真实信息需求是提问的唯一正当来源"） |
| `cognition_modes.py:121-128` | 未知实体/未知关系 → MODE2_INTERPRET / MODE3_REASON（LLM 参与档位） |
| `prompt_templates.py:362` | `CURIOSITY_QUESTION` 模板：**"你是 FAS，一个对世界充满好奇的认知系统"**（prompt 人设注入） |
| `prompt_templates.py:195` | ANSWER 模板："只有当你确实产生了信息需求或未完成的好奇目标时才提问"（行为规则写进 prompt，虽是抑制方向） |
| `chat_log.py` | curiosity 字段记录与统计 |
| `capability_registry.py:404-502` | `curiosity_driven_inquiry` 等能力条目描述 |
| `nlp_processor.py` / FAISS 召回 | `app.py:2140` 注释：召回时排除图外节点，"避免 FAISS 召回不存在的节点引发虚假的好奇心触发" |

### 运行图谱实测（2026-09-14）

- `未知*` 六节点 + `好奇`/`等待回答`/`知识完善`/`回答完成` 全部存在，**但 `graph_space=semantic`**——是旧版 bootstrap 创建的；现版 bootstrap 只在缺失时创建（`if kg.get_node(nid) is None`），从不修正，所以空间标注新旧不一致。
- `CuriosityDrive` 存在，当前激活 0.0056。
- `知识缺口`、`主动提问`、`需要学习`、`学习目标` **不存在**（P0-2 已删）——drive_engine 里三条贡献路径恒为 0。
- `Unknown*` 节点 8 个。
- 信号边实测：`未知概念/关系/属性/事件/信息/需要确认 → 好奇` 各 0.5，`好奇→生成好奇问题` 0.6，`未知信息→网络搜索` 0.8。注意 `未知信息→网络搜索` 是 `semantic_relation`（非 cognitive_relation），不一致。
- `等待回答.extra_attrs["curiosity"]` 当前为空。

---

## 2. 按层归类：哪些代码属于哪一层

| 层 | 代码 | 备注 |
|---|---|---|
| **检测** | `curiosity_engine.check_and_inject_curiosity` 的未知概念/未知关系检测、情绪节点扫描、`TRIVIAL_RELATIONS` 过滤（:318-392） | 检测本身合理；问题在检测结果的**语义**（直接当好奇信号注入固定强度） |
| **检测** | `minecraft_perception` 未知检测（:66-90） | 只建 Unknown 节点；**头注释声称"激活未知信息"但代码没有实现**——文档与实现脱节 |
| **检测** | `compound_phrase_handler`（app.py:2159 调用） | 把关系型短语从好奇候选中剔除——已经是对旧架构的补丁 |
| **图谱信号** | 7 条 `CURIOSITY_SIGNAL_EDGES`、`+0.5/+1.5/+0.25` 注入、`等待回答 +2.0`、resolve 时 `+2.0/+2.5`、`×0.3 cleanup` | 边的**命名**（trigger_curiosity）就是"未知=好奇触发器"语义 |
| **Drive** | `drive_engine._eval_curiosity` → `CuriosityDrive` 节点 | 结构正确（图谱统计→drive 节点→参与扩散），但输入只有"未知"，且含死路径 |
| **行为决策** | ① app.py `should_generate_question`→`llm_answer=curiosity_question` 独占回合② `action_engine._check_ask`（独立旁路，**产物 Ask intent 无执行器消费，半成品**）③ `autonomy._score`（真竞争）④ `dialogue_decide`（回应欲望竞争）⑤ `cc._pulse` affordance | **同一件事（要不要探索/提问）被五处决策，彼此不互通**——这是现状最大的结构问题 |
| **LLM 语言生成** | `curiosity_engine.generate_question`（:456-549）+ `_fallback_question` | **职责窄、设计合规**（只做转译，失败有规则兜底） |
| **LLM 语言生成（违规）** | `CURIOSITY_QUESTION` 模板人设句；ANSWER 模板"提问规则" | 见 §7 / P15 |

另有两处**装饰性死代码**：4 个 procedural 节点的 `execution` 沙箱代码只设置 `result["action"]` 字符串（如 `"generate_curiosity_question"`），全库无任何消费方——app.py 直接调 `should_generate_question/generate_question`，不经过 `execute_action`。

---

## 3. 违反设计原则的地方（逐条对照）

| 原则 | 现状 | 违反点 |
|---|---|---|
| **P1 未知不是好奇** | `check_and_inject_curiosity` 把"未知"以固定强度（+0.5）注入，`CURIOSITY_SIGNAL_EDGES` 命名即 trigger | 未知→好奇是**唯一通路、恒定强度、无任何调制因素**（无 attention/novelty/interest/emotion/cost） |
| **P2 好奇不是提问** | 图上 `好奇` 的唯一出边是 `→生成好奇问题`（w=0.6） | "好奇"这个认知状态在图上的**全部下游就是提问**，没有"看/搜/想/放着"的通路 |
| **P3 提问不是默认行为** | `should_generate_question` 过阈值 → `generate_question` 必然生成 → `llm_answer=curiosity_question` 独占回合 | 信号过阈值 = 必问 = 必打断；**没有"好奇但不问"的合法状态** |
| **P4 好奇是持续驱动力** | 一次性状态机 `active→waiting→resolved→reset`；`cleanup ×0.3`；`reset_curiosity` 清零 | 兴趣无法增强/减弱/被压制/转移；没有"对 X 的兴趣"这种持续量的表示 |
| **P5 探索进入统一竞争** | 三套决策系统（app.py 独占 / ActionSelector / autonomy）+ dialogue_decide | 提问在 app.py 里**不参与任何竞争**；observe/approach 在 autonomy 里竞争但提问不在；Ask intent 无人执行 |
| **P6 图谱决定是否探索** | `should_generate_question` 是图驱动的 | 这是现状里**最符合原则的一点**，应保留并扩展（多因素化） |
| **P7 LLM 只做语言** | `generate_question` 职责窄、有兜底 | 合规。但 `CURIOSITY_QUESTION` 模板开头是人格注入（"对世界充满好奇的认知系统"）——违反 P15 精神 |
| **P8 允许不行动** | `is_curiosity_active` → 下一轮用户输入**无条件**被 `resolve_curiosity` 当作回答 | 她问"SAO是什么？"之后，用户说"今天天气真好"也会被当成回答处理；**用户无法忽略她的提问**——这是"强制打断"的最尖锐体现 |
| **P9 兴趣可持续** | resolve 后 `reset_curiosity` 全清 | "学到 X = 对 X 的兴趣立即归零"；不可能出现"Minecraft→村民→路径规划→AI 行为模拟"的链式深入 |
| **P10 新经验改变未来兴趣** | 无任何从经历/结果到兴趣或探索倾向的回写 | disposition_store 只存对话行为词表；探索成败不影响下次对同类目标的兴趣 |

**总结成一句话**：现状把好奇心实现成了"**以提问为唯一出口、以回答为唯一闭合、以回合为唯一时间尺度**的检测-执行管道"；而它应该是"**以图上持续变化的激活/驱动力为本体、以行为竞争为出口、以兴趣记录为记忆**的内部状态"。

---

## 4. 重构前的三个结构性判断

这三个判断决定方案的形状，请先确认：

**判断一：统一行为竞争的落点在 `dialogue_decide` + `autonomy._score`，不新造竞争器。**
对话回合内的竞争落 `dialogue_decision`（respond / explore_ask / explore_search / silence 同台），自主期内的竞争落 `autonomy._score`（observe/approach/explore + 新增 explore_ask/explore_search 同台）。`ActionSelector._check_ask` 不再独立产生 Ask intent，降级为"消费竞争结果并落图记录"（或直接停用其触发职能）。

**判断二：`CuriosityDrive` 是好奇心的本体，"好奇"节点退役为兼容别名。**
新架构里只有一个持续量：`CuriosityDrive` 的激活值（由多因素图谱统计驱动、参与扩散、随时间自然衰减）。旧 `好奇` 节点保留在图里但不再是决策依据——所有信号边重定向/新增指向 `CuriosityDrive`。

**判断三："待回答的提问"降级为一种待处理交互（pending_inquiry），不是好奇心本体。**
仍需要一个最小状态来知道"她刚问过 X，用户下一句话可能是回答"（否则无法解析回答），但它：(a) 挂在被探索对象上而非流程节点上；(b) **可过期、可被忽略**——下一轮输入要经过相关性判定才被视为回答，判定不过则 inquiry 静默过期、兴趣保留。

---

## 5. 建议的新架构

```
                         检测层（只报告，不决定）
  用户输入解析            Minecraft 感知            FAISS 召回缺失
  (NLP 未知概念/关系)     (UnknownPlayer/Entity)    (相似度低=图外)
        │                      │                       │
        └──────────┬───────────┴───────────────────────┘
                   ▼
     detect_cognitive_signals()   ← 原 check_and_inject_curiosity 改造
       产出结构化信号: {type, target_node, raw_strength, factors{}}
       调制因素（第一版全部用现有图状态，不新增节点族）:
         attention   目标节点/其邻域当前激活（正在关注→加分）
         novelty     Unknown* / 首次出现（图里无节点→加分）
         recency     最近 N 轮反复出现（重复出现→先加分后钝化）
         relevance   与当前 TopK / 焦点事件的重合度（正在聊的事→加分）
         emotion     情绪在场（→抑制概念类探索，保留 关心 用户通路）
         satiation   该目标最近已被探索/回答过（→强抑制）
       → 注入 未知* 节点（强度 = raw × 调制系数，可≈0）
       → 注入目标节点自身激活（"这里有个值得看的东西"）
                   │
                   ▼  激活扩散（现有引擎，不加专用定时器）
     CuriosityDrive ← DriveEvaluator 多因素统计（重写 _eval_curiosity）
       drive = w1·discrepancy + w2·novelty + w3·relevance
             + w4·interest − w5·satiation          （删除全部死节点引用）
                   │
                   ▼  统一行为竞争（两个既有竞争器，不新增第三套）
   ┌────────────────────────┬──────────────────────────────┐
   │ 对话回合 dialogue_decide │ 自主期 autonomy._score        │
   │  respond（回应/共情）     │  observe / approach / explore │
   │  explore_ask（问用户）    │  collect / rest / follow …    │
   │  explore_search（自己搜） │  + explore_ask（用户在场时）   │
   │  silence                 │  + explore_search             │
   │  各候选 = f(激活, drive,  │  各候选 = f(drive, attention, │
   │   场合, 抑制, 倾向, 成本) │   novelty, tendency, risk…)   │
   └───────────┬────────────┴──────────────┬───────────────┘
               │ 胜者执行                    │ 胜者执行
   ┌───────────▼───────────┐   ┌───────────▼────────────┐
   │ ExploreByAskingUser    │   │ Minecraft observe/     │
   │  = generate_question   │   │ approach / explore     │
   │  （LLM 职责不变）        │   │ ExploreBySearch        │
   │ ExploreBySearch        │   │  = web_search          │
   │  = web_search          │   └───────────┬────────────┘
   └───────────┬───────────┘               │
               └────────────┬──────────────┘
                            ▼
              探索结果回写（现有奖赏链复用）
     目标节点 extra_attrs.interest {level, ask_count, resolved_count,
                                    last_outcome, last_explored_at}
       level 衰减不清零：回答后 level×0.4；新知识可派生新兴趣目标
       + mood_event + cc.note_outcome + regulation.submit_state
       + 自主行动/探索留痕 episodic 节点（现有机制）
                            ▼
              未来倾向（P10）：interest × outcome 影响
              下次同目标的 curiosity 调制（satiation/reward 因子）
```

**关键行为语义（对照第九节要求）**——用户说"我今天看到一个叫 SAO 的东西"时：

| 情形 | 新架构下的表现 |
|---|---|
| A. 没兴趣 | 信号注入后调制分≈0，`CuriosityDrive` 不涨，dialogue_decide 无 explore 候选过阈值 → 正常回应，不问 |
| B. 有一点兴趣 | drive 中等但 respond 更强 → 回答里可自然带过；目标节点 interest 记小值（"暂时记住"） |
| C. 很感兴趣且无更强行为 | explore_ask 胜出 → 调 `generate_question` 问"SAO是什么？" |
| D. 可自搜（技术名词/明确信息需求） | explore_search 胜出（提问不再天然最高优先级）→ 搜索后汇报 |
| E. 正在讨论别的重要事 | relevance 因素让当前话题的 respond 压过 explore → 不打断；SAO 的信号**留在图里**（激活自然衰减但 interest 记录仍在），后续可再起 |

---

## 6. 需要修改的文件和函数

### 第一阶段（纯改造，行为语义基本不变，可先行）

| 文件 | 位置 | 改动 |
|---|---|---|
| `drive_engine.py` | `_eval_curiosity` (:83-168) | 删除 `知识缺口/需要学习/学习目标/主动提问` 死引用；权重表迁到 `config.drive_system.curiosity`；输出带逐因子数值（可解释） |
| `curiosity_engine.py` | `check_and_inject_curiosity` (:258) | 改名 `detect_cognitive_signals`；返回结构化信号清单；注入强度 = raw × 调制系数（attention/novelty/relevance/emotion/satiation，全部从现有图状态读） |
| `curiosity_engine.py` | `bootstrap_curiosity` (:191) | 新增：对已存在节点做幂等对齐（graph_space/label/attrs）；新增 `未知* → CuriosityDrive` 信号边 |
| `minecraft_perception.py` | 未知检测块 (:66-90) | 补上与注释一致的图通路（Unknown* 节点 → 未知信息 信号，走注入而非直连边） |
| `curiosity_engine.py` | 4 个 procedural 的 `execution` 死代码 (:63-88) | 清除 execution 字符串（节点保留），或改留说明注释 |
| `config.py` | `drive_system.curiosity` | 新增调制/竞争权重（全部 `setdefault`，旧配置无需迁移） |

### 第二阶段（核心行为变化：竞争化 + 可忽略）

| 文件 | 位置 | 改动 |
|---|---|---|
| `dialogue_decision.py` | `dialogue_decide` (:88) | 候选集从 {respond, minimal, silence} 扩为 {respond, minimal, explore_ask, explore_search, silence}；explore 候选激活 = CuriosityDrive × 目标匹配 × 场合允许 − 抑制；输出带全部候选分量 |
| `app.py` | 分阶段扩散块 (:2280-2320) | **删除 3 步定时器**。改为：检测注入 → 正常扩散 → 把 explore 候选交给 dialogue_decide 竞争 |
| `app.py` | 好奇独占块 (:2634 附近) | **删除 `llm_answer = curiosity_question` 独占**。explore_ask 胜出才调 `generate_question`；respond 胜出时普通回答照常 |
| `curiosity_engine.py` | `resolve_curiosity` (:593) | 改为 `settle_inquiry`：先判定本轮输入与 inquiry 目标的相关性（目标词命中/语义相近），**不相关 = 忽略**（inquiry 过期、interest 保留、状态清空）；相关则按现有记忆抽取走，interest ×0.4 而非清零 |
| `curiosity_engine.py` | `set_curiosity_question` (:575) | 状态字段迁移为 `pending_inquiry`（含目标节点、提出时间、interest 快照）；`extra_attrs["curiosity"]` 读取兼容保留 |
| `app.py` | web search 块 (:2330-2380) | 从"触发即执行"改为 explore_search 候选（保留现有触发条件作为候选的 activation 来源；动作结果强制 respond 的现有逻辑保留） |
| `app.py` | `_auto_consolidate_curiosity_knowledge` (:1207) | 触发语义扩展：curiosity_resolved **或** explore_ask 被回答；写入目标节点 interest 回写 |
| `prompt_templates.py` | `CURIOSITY_QUESTION` (:362) | 删除"对世界充满好奇的认知系统"人设句，改为中性任务："把给定的探索目标转成一句自然的中文问题" |
| `app.py` | response_data (:2980 附近) | 新增 `exploration` 调试块：`signals / factors / drive_value / candidate_actions / selected_action / inhibition / exploration_target`；旧 `curiosity` 块保留兼容 |

### 第三阶段（自主探索扩展 + 兴趣持续化）

| 文件 | 位置 | 改动 |
|---|---|---|
| `autonomy.py` | `_candidates` (:418) / `_score` (:505) | 新增 explore_ask（仅用户在场且非飞行中动作时可行）与 explore_search 意图；basis 不再硬编码 `好奇`，改为 Unknown* 节点 + CuriosityDrive |
| `actions/action_engine.py` | `_check_ask` (:133) | 停用独立触发；Ask intent 改为消费 dialogue_decide/autonomy 的竞争结果落图（保留 intent 持久化与 /api/action/intents 兼容） |
| `curiosity_engine.py` | 新增 | `interest_of(target)` / `boost_interest` / `decay_interest`：兴趣记录挂在目标节点 `extra_attrs.interest`；探索结果（成功/失败/被忽略）回写；作为 P10 的最小实现 |
| `capability_registry.py` | :404-502 | 能力描述更新（id 不动） |
| `tests/`（新增 `test_curiosity_refactor.py`） | — | 关键不变量：①未知信号存在但 drive 低 → 不问、不搜、不靠近（合法）②drive 高但 respond 更强 → 不打断 ③inquiry 后用户答非所问 → 不当回答、兴趣保留 ④explore_ask 胜出 → 才调 LLM ⑤候选分量可从日志复算 |

**不改的**：`graph_model.py`、`diffusion_engine.py`（扩散引擎零改动——新架构完全复用现有注入/扩散/Top-K）、`episodic_buffer`、`chat_log` 结构、`/api/autonomy` 与 `/api/regulation` 全部端点。

---

## 7. 数据迁移 / 旧存档兼容方案

原则：**不删节点、不删边、不重写图**——所有变更通过"新增边 + 代码消费方式变更 + 幂等对齐"完成。

| 项 | 现状 | 迁移方案 |
|---|---|---|
| 旧信号边 `未知* -[trigger_curiosity]-> 好奇 -[0.6]-> 生成好奇问题` | 11 条在图 | **原样保留**。它们失去行为含义（代码不再读"生成好奇问题"的激活来决定提问），但信号经扩散仍会到达 CuriosityDrive 邻域，无副作用。新增 `未知* -[uncertainty_signal 0.5]-> CuriosityDrive` 六条边作为正式通路 |
| `未知信息 -[触发]-> 网络搜索` | 在图 | 保留（ExploreBySearch 的图上通路），顺手把 relation_category 修正为 cognitive_relation（幂等） |
| procedural 节点 `生成好奇问题/等待用户回答/更新记忆/结束好奇状态` | 在图（带死 execution） | 节点保留；execution 清空。它们从"流程剧本"降级为"历史事件标记" |
| `等待回答.extra_attrs["curiosity"]`（活跃提问状态） | 当前为空 | bootstrap 时一次性迁移：旧字段有值则转成 `pending_inquiry`（同节点存储，字段名换新）；旧字段保留只读不删。当前存档实际是空转，但代码必须写 |
| `未知*` 节点 `graph_space=semantic`（旧）vs 代码 `cognitive`（新） | 不一致 | bootstrap 幂等对齐（存在即修正 space/attrs），不删不重建 |
| `好奇` 节点 | 在图 | 保留为兼容别名；所有**决策读点**改读 `CuriosityDrive`；`continuous_cognition._pulse` 的 curiosity_active 判定同步切换 |
| `chat_log` 的 curiosity 字段 | 在用 | 字段不动，语义注释更新为"提出过探索性提问" |
| `/api/nlp` 返回的 `curiosity` 块 | 前端在用 | 保留；新增 `exploration` 块并行输出，前端迁移后再议移除 |
| `config.py` 旧键（`drive_system.curiosity.*` 五个权重） | 在用 | 保留为兼容；新增调制权重全部 `setdefault` |
| 能力注册表条目 | 在用 | 只改描述文本，id 不动（避免破坏依赖） |

**回滚策略**：第二阶段的行为开关集中放在 `config.curiosity_refactor = {"competition_enabled": true, "inquiry_dismissible": true, ...}`，可整体切回旧行为（旧代码路径保留一个版本周期）。

---

## 8. 风险与待确认项

1. **explore_ask 的"场合允许"判据**：我建议第一版用现成信号——`dialogue_act ∈ {question, request}` 且用户最近 2 轮活跃（非告别/终止场合），复用 `check_and_inject_curiosity` 现有的抑制逻辑。是否够？
2. **explore_search 的成本**：现在搜索是同步执行的（阻塞回合）。改为候选后，"搜索 vs 直接问用户"的取舍需要成本项（搜索延迟/失败率）。第一版建议：只在现有触发条件满足（明确意图词 / ≥2 图外概念 / 技术名词）时 explore_search 才有资格入场，避免每轮都犹豫。
3. **兴趣记录的粒度**：挂在**目标节点**（SAO、村民）上，而不是全局一个"好奇心状态"。这会引入"目标节点"的选取问题（多个未知同时出现时选哪个）——建议第一版取信号强度 × 调制分最高者，其余照常注入激活（留 diffusable）。
4. **`drive_engine` 与 `internal_state` 的关系**：`internal_state.py` 已有 `exploration` 需求（setpoint 0.30，Phase 2 才接入更新逻辑）。建议 CuriosityDrive（图上瞬时驱动）与 `exploration` 需求（慢变量）**分工**：前者管"现在想探索什么"，后者管"最近探索够不够"（satiation 的来源）。本方案第一版先不动 internal_state，只在 drive 公式里预留 satiation 因子。
5. **`好奇心检测`与 FAISS 的相互作用**：FAISS 召回会"吃掉"一部分未知检测（输入词没见过但语义近邻存在 → 不算未知）。这其实是合理的（已知近似概念），但意味着"未知"判定对措辞敏感。第一版维持现状（有 `_fuzzy_match_node` + compound_phrase 缓解），不动召回逻辑。

---

## 9. 实施记录（2026-09-18）

### 9.1 与方案的差异

| 方案条目 | 实施情况 |
|---|---|
| detect_cognitive_signals 调制注入 | ✅ 按方案（novelty/relevance/场合/satiation；情绪退让保留） |
| 场合抑制从一刀切改为系数 | ✅ sharing=0.5 / opinion=0.4 / greeting=0 等（config.curiosity.occasion_factor） |
| 删除 3 步定时器 | ✅ 信号整合相保留（这是扩散调度而非行为剧本），但信号到达后**只产生候选资格**，不再直接生成问题 |
| 删除好奇独占（llm_answer=question） | ✅ 改为竞争胜出分支 |
| dialogue_decide 五态竞争 | ✅ explore_ask/explore_search 与 respond/minimal/silence 同台；阈值随候选携带（本模块零好奇领域知识） |
| settle_inquiry（可忽略/可超时） | ✅ 三级相关性判据：目标名命中 → 未知名命中 → 解释性谓词兜底（发育期脚手架，明确注释） |
| 兴趣水位挂目标节点 | ✅ extra_attrs.interest；fires 下限 0.5、resolved ×0.4、ignored ×0.85——衰减不清零 |
| 探索目标落图 | ✅ ensure_exploration_target（论文 §任务二 CreateUnknownNode 语义），只在探索真正 fires 时落图，防节点爆炸 |
| drive 多因素 | ✅ 信号广度/强度 + Unknown* + 兴趣水位 − 满足感；删除 4 个死节点引用；输出带 components 逐因子 |
| bootstrap 幂等对齐 | ✅ 旧 semantic 空间节点 → cognitive；死 execution 清除；节点零删除 |
| 新决策通路边 | ✅ 未知* -[uncertainty_signal]→ CuriosityDrive；旧边原样保留；两边 bootstrap 均幂等补边（顺序无关） |
| Ask intent 反接 | ✅ _check_ask(exploration) 只记录竞争结果，独立触发逻辑删除 |
| autonomy basis 去硬编码 | ✅ "好奇" → "CuriosityDrive" |
| autonomy 兴趣追问 | ✅ _interest_ask_candidates：用户在场 + drive≥1.5 + 兴趣≥0.3 → communicate 候选（零 LLM 模板问句，借用既有执行面） |
| minecraft_perception 信号通路 | ✅ 新发现 Unknown 节点时给 未知信息 +0.3（仅首次，不重复） |
| prompt 去人设 | ✅ CURIOSITY_QUESTION 改为中性"语言实现"任务 |
| explore_search | ✅ 稀疏邻域判据（论文 §在线知识扩展）+ 技术名词加分；胜出后执行并转回应汇报 |

### 9.2 决策读点总览（重构后）

- **要不要探索**：`dialogue_decide`（对话回合）/ `autonomy._score`（自主期）统一竞争
- **能不能进候选席**：`should_generate_question`（图驱动门槛：扩散是否到达行为节点）
- **探索什么**：`build_exploration_candidates` 目标选择（信号最强者）
- **探索驱动强度**：`DriveEvaluator._eval_curiosity`（多因素图统计）
- **LLM 参与**：仅 `generate_question`（目标→问句转译，预算门控）与正常回答生成

### 9.3 验证

- `tests/test_curiosity_refactor.py`：41 条断言全过（P1/P3/P4/P5/P8/P9/图驱动门槛/存档兼容/落图幂等）
- 既有 25 套回归全过（test_continuous_cognition 需 ~300s，非回归）
- `scripts/verify_p0_2.py` / `verify_p0_all.py` 已迁到新 API
- app 模块完整导入烟测通过；真实图谱迁移已落盘（+5 条通路边、空间对齐、execution 清除）
