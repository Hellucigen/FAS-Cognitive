# Capability–Action–Executor 图谱化（2026-09-20）

> 知识在图上，激活在图上，能力在图上，行动在图上；代码只提供执行这些东西的现实接口。

## A. 修改文件

| 文件 | 改动 |
|---|---|
| `capability_graph.py` **（新）** | CapabilityIndex：config 种子表幂等种图（能力节点/需要/实现于/诱发/驱动/抑制边）；路径索引每拍从图重建（无缓存双源）；gates 数值求值（图槽位优先、live 感知缓存回退）；可达性检查（executors∩caps、活跃抑制、显式需要→物品 满足）；能力缺口信号上图；learned_from 执行溯源 |
| `tests/test_capability_graph.py` **（新）** | 33 断言 = §十六验收 1–6 + dangerous 红线 + 回滚开关 + 种子幂等 + 真实 diffuse 传导 + autonomy 端到端 + "sing 零 if 入链"证明 |
| `config.py` | `capability_graph` 段（105 技能→19 能力聚合表、8 新概念含 gates、concept_requires、诱发/驱动/抑制种子、缺口参数）；`hostile_entities` 唯一名单（19）；relation_ontology 登记 `诱发`；`tension_sources.capability_gap` 接缺口采样；drive_edges 具身扩展 |
| `autonomy.py` | `_action_candidates` 变薄为调度器：cap_index 在位 → `_graph_action_candidates`（概念→binder 参数绑定→默认绑定兜底：**新概念零代码改动入链**）+ `_if_chain_candidates(state_driven=False)`（只留 (b) 机会/策略与 (c) 目标透传）+ 去重合并；binder 表 9 个概念的参数绑定（RECOVER/EAT/FORAGE/RETREAT/OBSERVE/APPROACH/FOLLOW/CONVERSE/EXPLORE，含红线：无文本不 communicate、dangerous 不自主）；novelty 分量改读图 `type=="unknown"`（前缀兜底）；`cap_index` 属性 |
| `minecraft_perception.py` | 背包低频上图（`Haru-[拥有]->物品:x`，count in-place、快照外置 0、cap 24）；`附近的生物` 附 entities dist + hostile_dist 标注（名单由调用方注入） |
| `minecraft_embodiment.py` | init 收 config（注入 SkillContext 与感知）；`capabilities()` 退役为"技能全集∪communicate"（cap_index 注入后 8 旧语义词不再充当能力面，仅作 execute 输入别名）；perceive 低频拉 inventory + 传敌对名单；`cap_index` 属性 |
| `capability_registry.py` | **图为主、JSON 降缓存**：`has/can_handle/get_status` 图节点优先读；`_sync_to_graph` 节点统一 `type=capability, channel=engine`；status 真相源=节点属性 |
| `action_system.py` | `normalize_action(spec, kg=…)`：priority 取值链 = 显式 > 概念节点属性（图上数据）> BASE_PRIORITY 兜底；幽灵键清理（come_here/build_shelter/wait）；`_write_action_memory` 接 `cap_index.record_execution`（learned_from 溯源）+ `cap_index` 属性 |
| `actions/action_engine.py` | Capability_* 4 节点 type 归一为 `capability/channel=cognitive`（幂等迁移）；定性为留痕/展示视图，不再扩建（unwanted §6 判词执行） |
| `skills/observation.py` | 敌对名单改读 `ctx.config["hostile_entities"]`（常量降为离线兜底）——名单三处重复收敛 |
| `drive_engine.py` | 注册 `graph.capability_gap` 采样器（缺口节点→capability_gap 张力→LearningDrive） |
| `cognitive_field.py` | capability_gap 张力默认组件加缺口采样 |
| `dialogue_decision.py` | affordance 表识别 `诱发`；语言竞技场过滤 `channel==embodied` 候选（具身概念归 ActionManager 竞技场） |

## B. Schema

**节点三族**（统一 `type` 标记，`CapabilityIndex.build_index` 按 `type=="capability"` 识别）：
- 能力节点 `能力:<中文名>`：label=procedural, space=cognitive, extra_attrs={type:capability, channel:embodied|engine|cognitive, executors:[技能名], learned_from:[行动_*id ≤10], dangerous?, description}
- 行动概念：沟通=行为:*/行动:*（label=disposition/self，上一轮契约）；具身=MINE 等英文大写 + 新增 RECOVER/EAT/FORAGE/RETREAT/OBSERVE/EXPLORE/CONVERSE/BUILD（label=procedural/semantic，认知不可见契约不变，点亮靠边不靠召回）；proposed 新概念同族共存
- 状态端点：复用 7 槽位树 + 新增 `物品:x`（type=inventory_item, count 属性）

**边**（全部已在 `relation_ontology` 登记，参与现有 diffuse_step，无第二套传播）：

| 关系 | 方向/类别 | 权重语义 | 维护者 |
|---|---|---|---|
| 需要 | 概念→能力、能力→物品 | 可发现性/前置（0.6）；能力→物品边由**因果/教学生长，启动不伪造** | 种子 + 未来 CausalLearner 晋升 |
| 实现于 | 能力→载体（进入Minecraft世界等） | 现实接口归属（0.7） | 种子；executor 名单在能力节点属性 |
| 诱发 | 状态节点→概念 | 该状态在场时此行动值得考虑（与先验同类，非触发管道） | 种子表 + 可增 |
| 驱动 | Drive→概念 | 内驱压力（curiosity→OBSERVE/APPROACH/EXPLORE、social→FOLLOW/CONVERSE） | action_space 通道复用 |
| 抑制 | 条件节点→能力 | 激活≥suppression_min_activation 时能力路径关闭 | 种子 2 条起步，随证据增长 |
| 拥有 | Haru→物品 | 此刻背包持有（count 属性=数量） | 感知层低频刷新 |
| 实施 | 行动_*→概念 | 事件溯源 | 上轮已建，本轮补 learned_from |

**学习语义**（唯一通道，不建第二套）：能力边界的学习真相 = causal 时间轴（`tool_missing:` blockers→评分压制）+ `操作:→导致→变化:` 晋升边（图上长出的 需要→物品 边被 reachability 消费）+ 能力节点 learned_from 溯源环。概念激活学习 = 先验/激活边权（disposition）。Hebbian 白名单不变（不接新边类别）。

## C. 完整链路（重构后）

```
感知（bridge /state + 低频 /inventory）→ 图槽位/物品节点（值真相源上图）
→ 事件（detect_environment_events，名单=config）→ _apply_event_effects 注激活（保留）
→ Drive/CognitiveField（保留；capability_gap 张力 ← 能力缺口节点）
→ diffuse_step：状态-[诱发]->概念、Drive-[驱动]->概念、情绪-[倾向]->概念（现有传播）
→ CapabilityIndex.discover_candidates：
    概念激活/供性源 ∧ gates（图槽位值 ∪ live 回退）∧ 可达性
    （executors∩capabilities() ≠ ∅ ∧ 无活跃抑制 ∧ 需要→物品 满足）
    —— 接口缺失 → 能力缺口信号上图（认知可见的学习入口）；抑制 → 静默
→ binder 参数绑定（新契约：autonomy 不再枚举"什么状态→什么行动"）
→ opportunity 规则（(b) 事件机会/优先级、(c) 用户目标透传——保留）→ 去重
→ _score_action（现有权重体系，novelty/attention 读图）→ 生存置顶 → 阈值
→ _feasible_action（executor∈caps、锁、在场、重试退避——保留）→ ActionManager.propose
→ execute（skills/bot.js 原样）→ _settle：_recent/causal/reward/disposition
    + 行动_*-[实施]->概念 + 能力.learned_from ← 下次 discover/评分消费 —— 闭环
回滚：capability_graph.enabled=false 或 cap_index 未注入 → state_driven=True，
     全部 (a) 规则回到旧 if 链（逐位一致，测试双路径均覆盖）。
```

## D. 真相源收敛结果（§十四）

| 曾经的重复 | 现在 |
|---|---|
| skills REGISTRY | 唯一 executor 注册表（name→实现），语义元数据不再新增于此 |
| action_concepts 12 概念 | 图概念节点的 executor 字段保留为种子输入；运行时权威=能力节点 executors 属性（EXECUTOR_TO_CONCEPT 仍供 spec.concept 反查） |
| embodiment CAPABILITIES 8 词 | 退役为 execute 输入别名；能力面 = 技能全集 ∪ communicate（图能力节点为语义层） |
| autonomy INTENT_TYPES | (c) 目标类型词表（用户 goal API 契约面），不再是能力定义 |
| Capability_* 4 节点 | type=capability/channel=cognitive 留痕视图，冻结不扩建 |
| capability_registry JSON 主存 | 图节点 status 为真相源，JSON 降缓存 |
| 敌对名单 ×3 | config["hostile_entities"] 唯一（observation/autonomy/感知标注共读；_check_emergency 8 项为执行安全子集，故意保留独立） |

**留在代码的（§十一 执行安全边界，一条未删）**：_check_emergency、SURVIVAL_TYPES 抢占、interrupt_margin、min_interval/安静期/max_attempts/退避/recency、SAFE_GATHER_BLOCKS、skill_busy 互斥、cancel 物理停止链、dangerous 概念不自主执行、communicate 不编台词、超时结算、skip_disposition。

## E. 验收映射（§十六）

1. [唱歌] 入图 → 可理解（概念节点+边可寻址）✅ test §3
2. requires 无 executor → 能想到（缺口信号上图、喂 capability_gap 张力）、不能执行（不产候选）✅ §3 + drive_engine 采样
3. 注册 sing + 接 realized_by/需要边 → 候选自然出现，**autonomy 零改动**（默认 binder 兜底）✅ §3/§10（autonomy 级证明）
4. build_shelter 新技能接边 → 入链 ✅ §4
5. 缺材料（需要→物品 count=0）→ 概念在、路径不可达、缺口记录 ✅ §5
6. 活跃条件 → 能力被抑制、定义未改；条件熄灭 → 恢复 ✅ §6
附加：诱发边真实参与 diffuse_step（概念激活可测量）✅ §2；回滚开关 ✅ §8；端到端旧路径零漂移（test_autonomy 全绿）✅

**验证记录**：tests 51/51 + verify_p0_all/p0_2 全绿；真实 app 装配日志种入 19 能力/8 概念/17 需要/9 诱发/2 抑制/5 驱动边；`/api/drive`、`/api/autonomy/state`（drives 四键）、`/api/action/state`、`/api/skills`（105）契约面正常；真实对话两回合（"帮我找铁矿+我需要工具挖矿"→"孤独是什么感觉"）：回合尾观测 `[CognitiveField] tension=…capability_gap:0.14`——**请求含未具备的执行手段时缺口信号自动进入学习张力**；`[ActionTrace] ctx=用户请求 cands=[respond:0.95,…] → respond` 行动溯源正常；MC 未连接时 discover 按设计返回空（离线≠缺口），自主循环零异常。
**未覆盖（如实）**：Minecraft 真客户端连接下的 discover→propose→_settle 全链未实跑（executor 连接态、gates 读真图槽位为单测/假桥级验证）；`需要→物品` 边的因果自动生长（CausalLearner 晋升到该形态）是下一步，当前该类边仅消费不生产。
