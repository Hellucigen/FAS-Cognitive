# Action Concept / Action Frame / Action Intent 架构升级（2026-09-19）

## 一、重构原则（用户指令的核心）

> 正则仍然可以让 FAS 很快地行动，但正则不再定义 FAS 能理解什么动作。

四类用户显式动作通路（网络搜索 / 文件操作 / 看屏幕 / Minecraft 反射）从
"正则匹配动作"升级为"语义动作概念 + Action Frame + Action Intent"，
正则降级为 **fast pattern（高置信快捷入口）**，全程增量迁移、保留现有
ActionManager / 执行器 / 否定机制 / 认知回注。

## 二、审计结论：升级前的真实调用链（以代码为准）

| 动作 | 触发 | 执行 | 认知回注 |
|---|---|---|---|
| 网络搜索 | app.py 内联正则 `搜一下\|搜搜\|搜索\|...` + 启发式（图外概念≥2 / 技术名词） | `actions/web_search.do_web_search` | `网络搜索` 节点 +3.0 激活、`搜索记录_*` procedural 节点、`_cog_ctx.web_results` |
| 文件操作 | 内联正则 `(创建\|新建\|写\|生成\|建).{0,12}(文件\|txt\|md)\|删除文件\|读取文件` → LLM 抽参 | `actions/file_action.execute_file_action`（仅 create_file） | `文件操作` 节点激活、`_cog_ctx.file_action` |
| 看屏幕 | 双正则（动作词 AND 屏幕物项） | `eye/screen_ocr.recognize_text` | `eye_text_*` 入图、`看屏幕` 激活、`_cog_ctx.eye_result` |
| MC 反射 | ①快路径 `parse_reflex_command`→`reflex_to_action`→`ActionManager.propose(SRC_USER)`；②MODE0 `_dispatch_mc_reflex` 直调（绕开 ActionManager 的兜底）；③L1/L2 认知映射 | embodiment→skills | `ActionRequest_*`、timeline ACTION/SELF_STATE 事件、`行动_*` episodic 节点+`mark_active`+`activate_from_inputs`、奖赏/disposition |

**审计确认的真实 bug（本次一并修复）**：

- **B1 否定漏网（安全）**：`NEGATION_RE` 否定只折叠 follow/approach/move/jump
  为 stop；`别挖这个`/`不要攻击他` 仍走 dig/attack 执行。
  → `minecraft_reflex.parse_reflex_command` 新增否定+dig/attack →
  `action="none" + refused=<动作>` 显式拒绝（所有调用方零改动即安全）。
- **B2 叙事误触发**：`我刚才看到有人跟着我`/`他让我过来` 命中 fast 正则即执行。
  → 命令性门（command_evidence）在每一层裁决前过滤。
- **B3 犹豫误触发**：`我不知道该不该搜索这个东西` 触发真实联网搜索。
  → 命令性门 + 自主信息需求启发式同步抑制。
- **B4 文件正则双缺陷**：`读取文件` 要求字面连写口语不命中；即便命中执行器
  永远拒绝。→ FILE_READ/FILE_DELETE 成为显式概念：能理解、如实告知能力缺口。
- **B5/6 参数缺陷**：dig 不剥指代词（`挖掉那个木头`→`那个木头`→unknown）；
  `挖三个铁矿` 数量丢失。→ 参数绑定层统一处理，`reflex_to_action` 不再
  硬编码 quantity=1。

## 三、新架构

```
自然语言 → 否定优先(NEGATION_RE 单一真源) → 动作候选识别
   ├─ fast pattern（现有正则原文：minecraft_reflex / 桌面触发正则）
   └─ 语义层：seed 表面形式(图上"表达方式"边) → embedding 图谱召回
        → 歧义/低置信（仅 web 通道）→ LLM 消歧(action_resolve 模板)
→ 命令性门（叙事/转述/犹豫/疑问/过去式 ≠ 命令）
→ 参数绑定（Action Frame 槽位：target/quantity/duration/query…，与识别解耦）
→ Action Intent {action, parameters, polarity, confidence, source, matched, evidence}
→ 桥接：MC 域 → action_intents.reflex_to_action → ActionManager.propose(SRC_USER)
        桌面域 → app 管线内联执行器（现状保留）
→ 执行结果回认知系统（mark_active / 行动_* 留痕 / timeline / 奖赏 / disposition）
```

### 新模块

- `action_concepts.py` — 概念注册表（12 个概念：FOLLOW/APPROACH/STOP/JUMP/
  MOVE/MINE/ATTACK/SEARCH/FILE_CREATE/FILE_READ/FILE_DELETE/SCREEN_OBSERVE）。
  每个概念绑定：name_zh、fast_patterns（引用现有正则，不复制语义）、
  seed_expressions（先验表面形式）、参数槽、negation 语义（stop/refuse）、
  executor、dangerous/min_confidence。
  `ensure_action_concepts(kg, engine)` 幂等种图：概念节点（MC 用英文概念名
  id；SEARCH/FILE_CREATE/SCREEN_OBSERVE **复用**现有 网络搜索/文件操作/看屏幕
  procedural 节点）+ 表达节点 `-[表达方式]->` 概念 + `Self-[能]->`、
  `概念-[实现于]->载体`。fast 表达（跟着我/过来）**不写图**。
- `action_resolver.py` — 分层解析器。产出 Action Intent；否定优先、
  命令性门、参数绑定、图谱/embedding 候选、LLM 消歧（可注入、MC 域永不
  调用）、`intent_to_minecraft_flow` 桥（含"否定挖/攻击+正在同类执行→stop，
  否则拒绝"的 ActionManager 真实状态转换）。

### 复用的既有设施（未新建第二套）

- `data/relation_phrase_table.json` 的 surface_forms→槽位模式 → Action Concept 的
  seed 表达→概念映射（§十九 与 relation frame 统一思想：自然语言表面形式不是
  认知对象，稳定的语义概念/关系/动作才是）。
- `embedding_manager.search` + `is_cognitive_visible`（self_capability 可见）
  → 语义候选召回；`graph_schema.normalize_relation`（新增规范关系"表达方式"）。
- `ActionManager`（propose/queue_goal/tick/settle/_write_action_memory）→ 唯一
  具身执行闸门；`_record_user_action_intent` → 请求入图留痕；
  L1/L2 认知映射、dialogue_decision 行为竞争（自主层）**原样保留、边界不变**。

## 四、关键安全/行为性质（锁定于测试）

1. 否定检测 > 动作匹配：`别跟着我→stop`、`别挖这个/不要攻击他→拒绝执行`。
2. 误触发清单（我刚才看到有人跟着我 / 他让我过来 / 我不知道该不该搜索这个东西 /
   这个文件叫什么来着 / 我看了一下屏幕…）在 MC 与桌面两域都**不产生意图**。
3. MC 反射快路径零 LLM：fast 命中毫秒级不变；miss 才进语义层（embedding 命中
   需 ≥0.55 且过命令性门，歧义在 MC 域**拒绝执行**而非猜）。
4. 图谱不是动作关键词词典：任何命中（含正则）都必须形成带 polarity/confidence/
   source 的 Action Intent 才允许执行。
5. 自主 ≠ 命令：`我好像有点饿了/这里好黑` 不进入用户命令通路；自主行为仍走
   激活→候选→竞争→ActionManager。
6. 执行后认知回注全部保留（mark_active、procedural 激活、行动留痕、奖赏、
   disposition、game ACK 失败如实）。

## 五、兼容与遗留

- `parse_reflex_command` / `_dispatch_mc_reflex` / `_MC_REFLEX_ACK` 保留；
  MODE0 旧通路作为快路径异常兜底（共享同一解析函数，B1 修复对其同样生效）。
- 桌面三类执行器暂仍内联（intent 消费），未强行迁入 ActionManager（避免一次
  改动所有模块）；`inline:<executor>` 字段已为后续迁移预留。
- FILE_READ/FILE_DELETE executor 为空=能力缺口：能理解，如实告知（capability
  `action_intent_resolution` 已登记）。
- 词表只是 seed/先验；新口语表达优先靠概念 description 的 embedding 泛化
  （§十三），测试已用注入 embedder 验证 graph 层通路。

## 六、测试

- `tests/test_action_concepts.py`（新增，约 100 项断言）：§16 全类别（FOLLOW/
  SEARCH/SCREEN/FILE/MC 方向·数量·持续时间·否定·目标、同义/口语/省略/否定/
  歧义）+ §17 误触发 + 图谱种子/幂等 + embedding 层 + 桥接否定状态转换。
- `tests/test_minecraft_reflex.py` 扩展：否定 dig/attack refused 回归。
- 全量 `tests/`（38 个脚本）升级后全绿。

## 七、上线首日修复的两处问题（2026-09-19 晚，用户实测反馈）

**① 前端图谱被动作节点污染（升级引入的回归）。** 初版把 73 个动作
概念/表达节点建成了**可见知识节点**（procedural+self_capability /
declarative-semantic）并注册进引擎名表。后果：普通聊天的近义句经 FAISS
召回注入点亮"挖掉/去打/网上找一下"等短口语节点，再沿表达方式边扩散到
MINE/ATTACK（实测 MINE 激活 2.99）；`/api/graph` 只按激活过滤 → 一堆
动作节点混进前端视图"乱死了"。
修复（`ensure_action_concepts` 重写 + 自愈）：概念/表达节点降为**引擎
零件**（无 self_capability、label=infrastructure）→
`is_cognitive_visible=False`：不再参与召回注入/回答区/前端视图；
从 `engine.name_to_node` 摘除（防模糊匹配精确定名激活）。FAISS 全量
索引不筛可见性，resolver 的语义召回通路**不受影响**。启动时自动把
已污染存量（重标签+清零激活+摘名表）修回（实测真实 runtime_graph：
70 节点自愈，名表只剩改造前就存在的 网络搜索/文件操作/看屏幕 三个
合法能力节点）。契约锁进测试（不可见性 + 名表摘除 + 重名让位）。

**② "进入我的世界"进不去。** 会话状态机本身正常（图内 stale connected
会被桥真相纠正 → 重新要端口）。根因在 `minecraft_session.handle_reply`：
等待端口时只认**纯数字**整句（`^\d{4,5}$`），用户答"端口61994"这类
口语直接落入 ignore → connect 永不触发（实证：该轮 config.json mtime
未变、haru.log 无新行）。修复：awaiting_port 状态下消息含 4~5 位数字
即视为端口（显式数字优先于"上次端口"复用；无数字仍 ignore，不放宽）；
取消语义优先级保持。测试 7d-7h 锁定。
另：bot.js 的同名互踢保护（"本实例退出"）与 03:06 那次"unknown"断连
属环境侧（双实例竞争/世界端掉），app 的重启链路会在新一次 connect 时
拉起干净实例。

**② 之二（用户提供完整控制台日志后的最终定位，2026-09-19 深夜）**：

日志实锤了"进不去世界"的完整链条——"来玩Minecraft吧，端口号是49679"
（20:04:30 进入）的回合里，LLM 解析单项耗时 65 秒（20:05:36 完成），
随后还要走记忆抽取/思考生成等多个 LLM 调用；世界接入（Phase C）长在
**LLM 回答分支内部**，用户 20:06:41 关服时该回合仍在前半段管线中，
connect 从未执行（config.json 未被重写是铁证）。次要问题：awaiting_port
的 L1 快轮次根本到不了回答分支，纯数字端口回复反而可能丢。

修复：**Phase C 整体前移到 turn 开头**，与反射快路径同层——纯确定性
正则 + 状态机 + 桥轮询，先于 NLP/LLM 执行（用户命令不该排队在语言
生成后面）；`_mc_session_action` 照常注入 _cog_ctx；awaiting_port 状态
强制走全管线（端口回复的回答需要连接结果）。

**日志同时暴露了图谱污染的完整路径链**（比先前修复多一环）：
`[FAISS] Minecraft → MINE (0.854)` → 注入 parsed_nodes → 种子激活
（MINE 3.54）→ 进 Top10 回答上下文 → 并且 **ActionQueue 把 label=procedural
的 MINE/MOVE/ATTACK 当成可执行动作**（`ACTION -> MINE act=2.88`，
3 个动作长期挂在行动队列）。已修：`diffusion_engine._refresh_action_queue`
全量/增量两分支都按 `type in (action_concept, action_expression)`
排除——动作概念是解析器内部结构，执行只走 Action Intent → ActionManager，
绝不允许进遗留执行队列。加上此前的可见性降级+名表摘除，污染链三环全断。

（另记一笔历史债，未在本次处理：`端口61994`/`54766` 这类旧连接事件
节点会以 0.75 相似度被新端口号召回，把思绪拉回旧端口——episodic
数字节点语义相近属 embedding 天性，待后续按时间衰减或归并处理。）

**③ 第二轮实测反馈修复（20:57 日志：进入成功但"不会动"+前端视图乱）。**

- 进入世界成功（`connect_minecraft port 64762`，前移生效），但连接后
  Haru 原地不动。补：connect 成功 → `ActionManager.propose(
  navigate_to_entity(user), source=SRC_USER)`——"来玩我的世界"的自然
  收束是走到用户身边；仍走承诺/中断规则，失败如实回执。
- 前端图谱按用户要求重构为**双子视图**：左=激活图谱（独立轮询，阈值
  选择器按图例分档 low/≥0.3/≥1.0/≥2.0，默认"高于mid"=≥1.0，只画高
  激活节点）；右=邻域查询（NODE EXPLORER 的查询结果冻结显示，不再被
  2 秒轮询劫持、也不再劫持激活视图）。渲染引擎由全局单例改造为
  `makeGraphPane` 工厂（两实例各自拥有物理/交互/相机状态）。
  `/api/nlp` 与 diffuse 的无阈值快照不再直接灌图，统一走阈值重拉。
- "查询邻居子图异常"根因：`/api/graph/auto?center=` 只认字面节点 ID，
  大小写差/简称/别名全部查无。补 `_resolve_center`：精确→大小写→别名→
  包含（最短名优先），响应回显真实命中中心。空查询不再触发错误 toast。
