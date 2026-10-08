# FAS 架构审计（Phase 1 — Read Only）

> 审计基线：2026-10-08 工作区（含未提交改动）。所有行号为实测。
> 本文档只描述现状，不含任何改造决策（决策见 `ARCHITECTURE_PLAN.md`）。
> 认知模块的逐项实验状态见 `COGNITIVE_MODULE_STATUS.md`。

---

## 0. 一页结论

- FAS 没有独立运行的认知核心进程：运行时主干是一个 **8584 行的 Flask 单文件**（`app.py`），
  import 即装配约 40 个模块级全局单例、启动后台认知线程（`continuous-cognition`，app.py:765）与落盘线程（2415）。
- **好消息：认知核心代码本体是干净的。** 图谱/扩散/场/记忆/驱力/调制/锁/内部状态/动作等核心动力学模块
  只依赖 `graph_model` + stdlib，**没有任何认知模块在模块级 import LLM/陪伴侧代码**；
  LLM 依赖全部经由参数注入（`nlp` 实例）或 4 处函数内 `prompt_templates` import。
- **坏消息：真正的耦合在装配层与管线层。** 对话回合主管道 `process_nlp()`（app.py:2999–5347，约 2350 行）
  把"认知状态迁移"与"语言实现/UI 组装"写在同一个函数体内；
  `nlp`（NLPProcessor，事实上的语言总线）被 6+ 个模块以裸对象方式持有并直接取其内部句柄（`.llm` / `.chat_llm`）；
  `engine._nlp_ref`（app.py:748）让扩散引擎反向持有 LLM；
  多个陪伴侧/外设模块直接改图谱内部（`kg._lock`、直写 `activation`、`engine.mark_active`）。
- 陪伴层与外部壳 **Charon（E:/Project/Charon）已有成熟双向管道**：
  Charon→FAS 走进程托管 + REST 代理（backend/modules/fascinator.go），
  FAS→Charon 走 Bridge HTTP `127.0.0.1:17734`（token 鉴权，支持事件推送/通知/日记/便签）。

---

## 1. 当前 FAS 的真实模块边界

### 1.1 运行时主干（app.py）

| 区段 | 行号 | 内容 |
|---|---|---|
| 安全/Flask 创建 | 19–165 | `app = Flask(...)` 106；CORS 111；`config = dict(DEFAULT_CONFIG)` 113 |
| 图谱加载/播种 | 266–700 | 能力节点种入 577–614；`action_space` 635 |
| LLM 配置持久化 | 673–746 | `_load_llm_config` 695，`data/llm_config.json` |
| **服务装配区（import 即执行）** | 739–1800 | 约 40 个全局单例（见 §1.2） |
| 落盘线程 | 2327–2450 | `_save_writer_loop` 2357 |
| 图谱 CRUD/可视化路由 | 2452–2998 | `/api/graph`、`/api/activate` 2883/2924 |
| **`/api/nlp` 回合主管道** | 2999–5347 | `process_nlp()`：聊天+认知合体主入口 |
| 手动扩散/调试路由 | 5752–5852 | `/api/diffuse/*` |
| 陪伴/具身/自主 API | 6147–6786 | `/api/proactive/poll` 6188、regulation、autonomy、channels |
| 内部状态/自我模型 API | 6787–7368 | `/api/internal/*`、`/api/self/*`、drives 7306 |
| LLM 开关 API | 7406–7452 | `/api/llm/switch`、`/api/llm/status` |
| 学习/知识包/感官 API | 7735–8398 | ear 8139、eye/vision 8202–8344 |
| 启动/收尾 | 8402–8584 | 端口 5000（env `PORT`），`app.run` 8549 |

### 1.2 装配区关键全局单例（认知/陪伴归属标注）

| 全局对象 | 行号 | 侧 |
|---|---|---|
| `nlp = NLPProcessor(...)` | 739–743 | **Companion**（语言总线） |
| `engine._nlp_ref = nlp` | **748** | **反向注入：认知持有陪伴** ⚠ |
| `buffer = EpisodicBuffer` | 755 | Cognitive |
| `cc = ContinuousCognition(kg, engine, nlp, buffer, config...)` + `cc.start()` | 762–765 | 混合（认知循环，内含主动表达 `_express`） |
| `persona = PersonalityBaseline` | 780 | 混合（种子属认知，`_mood_band` 措辞属表演） |
| `reflection_engine = ReflectionEngine(kg, nlp, ...)` | 783–785 | 混合（LLM 嵌在认知逻辑内） |
| `llm_budget = BudgetManager` | 787 | Companion 资源治理 |
| `regulation = CognitiveRegulation` | 794–799 | Cognitive |
| `autonomy = AutonomousLoop` ↔ `cc` | 1130–1156 | Cognitive（tick 零 LLM，红线注释 autonomy.py:26） |
| `internal_state = InternalState` | 1163–1167 | Cognitive |
| `action_manager ↔ cc` | 1215–1225 | Cognitive（双向互引 ⚠） |
| `channel_hub` + MinecraftChatChannel | 1288–1292 | Companion |
| `_channel_turn_runner`（`app.test_client()` 回环 POST 自己） | 1298–1312 | Companion ⚠ |
| `drive_evaluator` / `self_updater` / `emb_mgr` | 1370 / 414 / 1752 | Cognitive |

### 1.3 主循环与入口

- 唯一后台主循环：`ContinuousCognition._loop`（continuous_cognition.py:193–205），
  tick 2.5s；`tick_once` 207–393 按拍挂激素/检测器/autonomy/action/时钟。
- 对话入口唯一：`POST /api/nlp`（网页 index.html:1575 与 Minecraft 通道 app.py:1302 共用）。
- 主动消息：CC `_express`（936）入队 → UI 12s 轮询 `/api/proactive/poll`（app.py:6188）。
- 通信：纯 HTTP+JSON 轮询，全仓无 WebSocket/SSE。

---

## 2. 哪些代码属于认知（Cognitive Core 候选清单）

以下模块经 AST 扫描确认**零 LLM/陪伴侧 import**，且只依赖 `graph_model`/stdlib/彼此：

```
graph_model / graph_schema（词表）           统一知识图谱
diffusion_engine（惰性 self_graph 仅 generate_thought 一处）  扩散激活
cognitive_field / tension_field / drive_field / cognitive_networks / modulation  场与调制
episodic_buffer / experience（CausalLearner.promote_to_kg）  情景记忆与写回学习
drive_engine（DriveEvaluator）               驱力评估
cognitive_locks / cognitive_regulation / cognitive_triggers / state_monitors  调节/锁/触发器
modulation_events / modulator_subgraph       调制子图
internal_state                               需求/激素/性格唯一数值真源
disposition_store                            人格=图上边权（学习性）
self_model / self_graph（generate_thought 除外）  自我模型
action_system / action_space / action_concepts / action_intents / action_resolver（llm 参数注入）  行动决策
dialogue_decision                            行为竞争决策头（零 LLM；但直接读写 engine/kg 内部 ⚠）
safety_kernel                                执行安全不变量
prior_knowledge / world_prior / mc_knowledge 世界先验入图（数据表，非特判）
compound_phrase_handler                      复合短语→图谱关系（语言学规则，属认知输入分析）
cognition_modes                              LLM 资源治理（MODE 阶梯/预算；两侧共用）
```

**认知模块内部嵌 LLM 的全部位置**（均为参数注入、非模块 import；有规则兜底）：

| 位置 | 调用点 | 兜底 |
|---|---|---|
| curiosity_engine.py:859–869 | `chain = prompt \| nlp_processor.chat_llm` | `_fallback_question` 885 |
| reflection_engine.py:273/341 | `chain = prompt \| self.nlp.llm` | 候选需代码把关 |
| self_graph.py:614→798 | `nlp_processor.ask("thought", ctx)` | — |
| continuous_cognition.py:943/960–965 | `_express` 主动表达 | 预算不足→沉默 938 |
| diffusion_engine.py:181/1425 + app.py:748 注入 | `generate_thought` 委托 self_graph | — |
| graph_expansion.py:536（离线管线） | 自 import llm_provider | 仅 scripts/ 使用 |

---

## 3. 哪些代码属于陪伴（Companion Layer 候选清单）

```
app.py process_nlp 内的语言段：parse 调用 3365–3371、回复生成 4665–4771（L1/L2）、响应组装 5000–5314
nlp_processor.py            LLM 编排（三后端、15 任务模板消费方）
llm_provider.py             云/本地 LLM 网关（所有云端流量物理收口于 _create() 66–139）
ollama_backend.py           可选本地后端
prompt_templates.py         模板 + FAS_IDENTITY（21–29，全仓唯一 persona 硬编码点）
conversation_channel.py / ChannelHub     对话通道（干净，不碰图谱）
chat_log.py / chat_log_replay.py         对话日志（图谱外，注释自证）
index.html                单文件 UI（145K）
ear/（FunASR Paraformer + emotion2vec + YAMNet）  STT/声学（外部模型，硬编码 E:/Models 路径）
eye/（RapidOCR + mss + YOLO）             屏幕感知（外部模型）
vision/（SAM2 + CLIP + FAISS）            物体感知（外部模型）
actions/web_search.py（爬 Bing/Baidu HTML）  搜索（临时方案）
minecraft/（mineflayer Node 子进程 + HTTP 桥）  游戏具身（从根级 minecraft_*.py 重构而来，包内文件未 git-add）
Action/wasd.py（ctypes 物理键盘）          真实外设
```

**混合体（两侧共同拥有，拆分时最需要接口化）**：
`continuous_cognition`（循环是认知，`_express`/`poll` 是陪伴出口）、
`dialogue_decision`（决策是认知，但直写引擎内部）、
`expression_feedback`（自我监控是认知，实现直改 `kg._lock`/`add_node`/`add_edge`/`mark_active`，
行号 94/96/118–127/136/170–193/253–275）、
`dialogue_signals`（`emotion_resonance` 建边注激活、`repair_social_wiring` 启动删边自愈 137–142）、
`personality_baseline`（`_mood_band` 表演性措辞表 53–63）、
`internal_state`（状态真源是认知，`/api/internal/*` 展示面是陪伴）、
`config.py`（单一 1954 行 dict 同时是认知参数与陪伴运维配置真源，且支持热更 PUT /api/config:7688）。

---

## 4. 阻碍未来拆分的依赖（按严重度）

1. **app.py import 即装配**：任何"只 import 认知模块"的尝试会牵起 Flask/MC/ear/eye/vision 全栈；
   替换对话层必须整体重写这份装配代码。
2. **`process_nlp` 2350 行单体**：激活/扩散（3750–3792）、记忆沉淀（4190–4250）、自我模型/反思/驱力结算
   （5186–5237）与沉默决策（4360）、L1/L2 回复（4674/4771）、UI payload 组装（5000–5314）
   同函数同 try 块、共享局部变量。认知状态迁移无法按调用取出。
3. **`nlp` 裸总线**：cc(762)/reflection(783)/curiosity(4612)/resolver(3886)/self_graph(614) 各自取
   `NLPProcessor` 的不同内部句柄（`.llm`、`.chat_llm`、`.ask`、langchain chain 拼装）。
   没有"认知层→语言层"接口；换语言方案要同时动 6+ 模块。
4. **`engine._nlp_ref` 反向持有**（app.py:748；diffusion_engine.py:181）：认知核心直接依赖陪伴对象。
5. **FAS_IDENTITY 前置进全部 15 个任务模板**（prompt_templates.py:21 起）：解析/记忆抽取/反思等
   纯认知任务的 system prompt 也带陪伴人设。换人设 = 全部认知任务提示词回归。
6. **config.py 单点共享真源**：llm_provider:340、graph_schema:22、nlp_processor:250/1030、
   self_graph:317、embedding_manager:46 绕过注入直接 `import config`；认知核心无法携带自己的配置独立运行。
7. **全局单例环形互引**：cc↔autonomy（1130–1156）、cc↔action_manager（1224–1225）、
   persona 闭包读 `globals()["internal_state"]`（771–776）、regulation 事件回调（816）。
8. **busy 握手**：HTTP 线程 `cc.set_busy(True/False)`（3008/4970/5335）冻结/解冻认知 tick
   （continuous_cognition.py:214）；进程分离后需整体重设计。
9. **test_client 回环**（1302–1309）：游戏通道与 Flask 路由层物理焊死。
10. **UI 契约混合**：`/api/nlp` 一个 JSON 同时返回对话答案 + 图谱快照 + curiosity + memory_draft +
    regulation 视图（5000–5312），index.html 按字段消费（1575–1600）。
11. **陪伴/外设直写认知内部**：见 §3 混合清单（expression_feedback、dialogue_signals、
    eye/screen_ocr.py:322–353、vision/vision_graph_bridge.py:55–165、minecraft/perception、
    mc_knowledge:33–55、disposition_store、dialogue_decision 改 `n.activation` 190–195）。

### 正向发现（可拆的现成缝隙）

- 对话决策头 `dialogue_decide` 是零 LLM 纯函数（dialogue_decision.py:233，文件头 1–24 硬边界注释）。
- autonomy tick 全程零 LLM、执行统一走 ActionManager；goal_from_text 是注入式 llm_fn（autonomy.py:3095）。
- 全部云端 LLM 流量物理收口 `llm_provider._create()` 一处，fas_log 已带 purpose/caller 归因
  （llm_provider.py:69–97）——替换语言网关切入成本最低。
- 认知模块已普遍采用"参数注入 nlp"模式，缺的只是一个**接口定义**而非重构调用方逻辑。

---

## 5. Charon（E:/Project/Charon）集成面现状

Charon/PersonalTerminal 是 Go+Wails+React+SQLite 桌面 "Life OS"，且**已是 FAS 的官方操作控制台**
（README.md:32）。对外集成面（全部证据实测）：

| 方向 | 机制 | 证据 |
|---|---|---|
| Charon→FAS | 子进程托管 `python app.py`（注入 PORT）+ 存活 ping | backend/modules/fascinator.go:109–111, 192–200 |
| Charon→FAS | 通用 REST 代理 `Call(method,path,payload)`→`http://127.0.0.1:5000/*`，8s 超时 | fascinator.go:224–263；SendNLP 265 |
| FAS→Charon | Bridge HTTP `127.0.0.1:17734`，头 `X-Charon-Token`（token 持久化于 `~/.personal-terminal/config.json` 的 `bridge_token`） | backend/bridge/bridge.go:61–120；core/config.go:63–65 |
| FAS→Charon | `POST /api/events {topic,payload}` 直推 webview 事件总线；`/api/notify` toast；`/api/diary`、`/api/fleeting`、`/api/notes`、`/api/health`、`/api/finance`、`/api/summary`（注释明言"让 Fascinator 用 Charon 数据 grounding 回答"） | bridge.go:124–325；端到端测试 bridge_test.go:17–74 |
| 前端 | 纯 Wails IPC；现仅监听 `charon.notify`（加 `EventsOn('fas.*')` 即可做气泡/播报，最小增量） | frontend/src/App.tsx:66–73 |

**缺口与雷区**：
- 无 TTS/形象/悬浮小窗（语音输出需 FAS 侧或前端 Web Speech API 补）。
- 插件系统文档完备（docs/PLUGIN_DEVELOPMENT.md）但运行时是半成品
  （PluginPanel.tsx:70–100 假 notes、`pt.fascinator`/权限校验未实现）——**不要走插件路线**，直接走 Bridge。
- 认知后端替换在 Charon 侧成本 = 改 config 三项（python 路径/app 路径/端口）+ 维持 `/api/*` 契约
  （app.go:150–203 SaveFascinatorSettings）。

---

## 6. 实验已验证 vs 假设（详见 COGNITIVE_MODULE_STATUS.md）

要点（避免"代码在跑=机制成立"的错觉）：

- **机制层最扎实**：扩散汇聚被 663-run 独立证伪实验 SUPPORTED；写回隔离 B1/B2、账本迁移 C、
  A10 信标 80/80、G0/G1/G2 访问层均为 VALID。
- **增量价值被否定**：扩散 vs flat 检索主族预注册实验 REJECTED（4/4 confirmatory ΔJCG<0，
  FAS 反超 0/200），唯一例外为共享枢纽拓扑；行为层"−扩散更快"REFUTED。
- **决策头基线完败**：I 实验 FAS 0–2/10 vs LLM 原始流 10/10；C34 路由 v2 T4 p=0.046 经 Bonferroni
  不存活（v1 为 bugged_experiment）。
- **长期"写而不读"**：self-model v6 证明行为无关，10-02 v7 修复后才有首个 consumer 差分；
  episodic 自动晋升是死路径（mark_accessed 全仓零调用）；事件框架差分 5/5 但生产默认关
  （config.py:1935）。
- **无对照实验的机制**：curiosity 提问质量、reflection、disposition、personality、locks/regulation——
  全部停留在单元测试/代码存在级。

---

## 7. 审计用工具

- `scripts/_audit_llm_imports.py`（本次新增，只读扫描器）：AST 枚举全仓对
  nlp_processor/prompt_templates/llm_provider/ollama_backend/app 的 import（含函数内惰性 import）。
- 环境：`E:\Miniforge.envs\Fascinator\python.exe`（Python 3.12.13，flask/langchain/numpy 可用；
  PATH 上的 python.exe 是 WindowsApps 占位符，不可用）。
