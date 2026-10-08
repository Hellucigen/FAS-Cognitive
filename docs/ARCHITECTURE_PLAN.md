# FAS 架构解耦设计方案（Phase 2）

> 输入：`docs/ARCHITECTURE_AUDIT.md`、`docs/COGNITIVE_MODULE_STATUS.md`。
> 裁决标准（用户任务书）：半年后某个认知模块实验成功，**只替换该模块，不重写 Companion**；
> 换掉 LLM/对话方案，认知层基本不动；某机制实验失败，陪伴功能照跑。
> 原则：Research validity > architecture purity > code elegance。不改算法、不改实验参数、不顺手修 bug。

---

## 1. 目标架构

```text
                      FAS 运行时
                          │
        ┌─────────────────┴─────────────────┐
        │                                   │
  fas/core/ (Cognitive Core)        fas/companion/ (Companion Layer)
  graph_model·diffusion·fields      LanguageBackend（临时=LLM）
  episodic·experience·drives        ExpressionChannel（Charon桥/UI/游戏）
  locks·regulation·internal_state   PerceptionSource（ear/eye/mc/charon→事件）
  disposition·self·action           GameBackend（临时=mineflayer agent）
  dialogue_decision(零LLM决策头)     STT/TTS（占位，待补充）
        │                                   │
        └─────────── fas/contracts.py ──────┘
              （唯一跨界数据面：事件/状态/请求/话语）
```

设计姿态（尊重审计结论，不做大规模重写）：

- 审计表明**认知模块本体已经干净**（零模块级 LLM import、参数注入）。本次解耦的重心不在"搬家"，
  而在三件事：
  1. **把已经存在的隐式缝隙变成显式契约**（语言访问面、语言请求、话语、事件）；
  2. **把"装配即副作用"的 app.py 变成可独立装配的两侧**（新增 `build_cognitive_core` / `build_companion`，
     复用测试中已有的裸装配模式，tests/test_closed_loop_consistency.py:362–367 为证）；
  3. **给每个临时组件一个可拔插的槽位**（backend/channel 注册），为 Charon 等外部壳提供标准接入点。
- `app.py` 的 2350 行 `process_nlp` 单体**本次不拆**（拆=重写，违反最小改造原则）；
  但 façade 提供等价的分步 API，新集成一律走 façade，`process_nlp` 标记为"待迁移的遗留管线"。

## 2. 数据契约（fas/contracts.py，纯 stdlib dataclass）

命名依据现有代码事实（非照搬任务书示例）：

| 契约 | 方向 | 字段（对齐现有调用面） |
|---|---|---|
| `CognitiveEvent` | Companion→Core | `kind`(user_message/perception/action_result/environment/tick), `source`(web/minecraft/ear/eye/charon/internal), `text`, `payload`, `ts` |
| `LanguageRequest` | Core→Companion | `path`(L1/L2), `payload`（= `compile_for_language` 的产物，键零改动）, `task_hint` |
| `Utterance` | Companion→用户 | `text`, `origin`(reactive/proactive/question), `event_ref`, `ts` |
| `TurnDecision` | Core 内部→两侧 | `decision`, `desire`, `constraints`, `factors`（= `dialogue_decide` 返回字典的包装） |
| `CognitiveStateSnapshot` | Core→Companion | `topk_nodes/edges`, `attention`, `drives`, `mood`, `mode`, `raw`（= `engine.active_snapshot()`+扩展） |
| `MemoryEvent` | 两侧→Core | `subject`, `predicate`, `object`, `provenance`（经 Cognitive API 入图，不再让陪伴直写 kg） |
| `ActionProposal` | Core→Companion | `action`, `params`, `confidence`, `source`（= action_system.propose 入参面） |

规则：
- 跨 `core ↔ companion` 边界的**只允许**这些数据结构（或原始 JSON 兼容 dict）。
- Companion 记录记忆/提交感知/提出动作一律经 `CognitiveCore.record/perceive/...`，
  **不得**再拿 `kg._lock`/`engine.mark_active`/直写 `activation`。
  （存量违例：expression_feedback、dialogue_signals、eye/screen_ocr、vision bridge、mc_knowledge——
  本次仅登记入 §6 债务清单，不做行为改动；新契约自新代码起强制。）

## 3. 稳定接口（fas/protocols.py，typing.Protocol）

### 3.1 认知侧对语言层的最小面（关键发现：只有 3 个成员！）

审计证明认知模块（curiosity 866、reflection 341、self_graph 798、cc._express 960–965、
diffusion._nlp_ref→generate_thought）实际只用到 `NLPProcessor` 的：

```python
class CognitiveLanguageAccess(Protocol):
    llm: Any          # 推理任务后端（langchain Runnable）
    chat_llm: Any     # 对话任务后端
    def ask(self, task: str, user_input: str) -> str: ...
```

→ `TemporaryLLMBackend` 按此 duck-type 直通包装 `nlp`（零行为变化），
app.py 的注入点（748/762/783 及 curiosity 4612 传参处）改为传包装器。
未来 `FASLanguageBackend` 实现同一 Protocol 即可整体替换——**这就是验收问题 B 的答案**。

### 3.2 完整语言后端（陪伴主管道用）

```python
class LanguageBackend(Protocol):
    def parse(self, text, *, fast=False) -> dict              # nlp.process / process_fast
    def extract_memories(self, text, context_nodes) -> dict    # nlp.extract_assertion_graph
    def extract_intentions(self, text) -> dict
    def realize(self, request: LanguageRequest) -> Utterance   # L1/L2 → answer_short/answer_question
    def generate(self, task, input) -> str                     # nlp.ask
    access(self) -> CognitiveLanguageAccess                    # 3.1 的最小面
```

实现槽位（每个槽标注 status）：

```text
LanguageBackend     ← TemporaryLLMBackend（MiMo/DeepSeek/Ollama，现状，✅已接）
                      FASLanguageBackend（未来研究项，⏳占位）
CognitiveCore 内嵌调用点：curiosity 提问 / reflection 分析 / free thought / 主动表达
                      —— 四处的"提问/反思/思考文本生成"若 FAS 自建机制成熟，逐点替换
MemoryWriter        ← 现状= LLM 抽取 + app 直写图（temporary）
                      未来 = FAS RelationalMemory（写回链 promote_to_kg 已 partially_validated）
ExpressionChannel   ← WebUI（现状）、MinecraftChannel（现状）、
                      CharonBridgeChannel（本次实现，见 §4）、
                      TTS/形象（⏳缺件，请求用户补充选型）
GameBackend         ← TemporaryMineflayerAgent（现状 minecraft/），
                      FASActionController（未来：autonomy 已被验证可直接驱动，见状态台账 A10）
```

### 3.3 CognitiveCore façade（fas/core/facade.py）

薄封装，全部委托既有函数，**不复制任何算法**：

```python
core = build_cognitive_core(config, kg=..., engine=..., buffer=..., ...)  # 独立装配（不依赖 Flask）

core.perceive(event: CognitiveEvent) -> ActivationReport
    # = emb_mgr.search + engine.activate_from_inputs + engine.diffuse_round + buffer.add_experience
core.decide(event, parsed, ctx) -> TurnDecision          # = dialogue_decide(...)（零 LLM）
core.compile_language_request(decision, state, evidence) -> LanguageRequest
    # = build_cognitive_context(...) + compile_for_language(...)（现存函数原样复用）
core.snapshot() -> CognitiveStateSnapshot                # = engine.active_snapshot()+get_topk
core.record_memory(event: MemoryEvent) -> None           # 图写回的唯一合法入口（内部走 kg.upsert/add_edge）
core.propose_action(p: ActionProposal)                   # = action_manager.propose
core.tick(dt)                                            # 驱动 ContinuousCognition 语义（可注入假时钟）
core.attach_language_backend(backend)                    # 替换 3.1 的最小面（验收 C 的机制）
```

认知失败降级路径（验收 C）：`core.snapshot()`/`core.decide()` 抛异常或返回空时，
Companion 端按 `FallbackLanguageBackend`（模板/直答）继续对话——陪伴层不依赖认知层存活。

## 4. Charon 集成（E:/Project/Charon，本次落到实现）

审计结论：管道已建好，FAS 侧缺一个客户端。本方案新增 `fas/companion/charon_bridge.py`：

- **下行（FAS→Charon，表达/生活记录）**：Bridge HTTP `http://127.0.0.1:17734`，
  头 `X-Charon-Token`（token 读 `~/.personal-terminal/config.json` 的 `bridge_token`）。
  - `Utterance` → `POST /api/events {topic:"fas.utterance", payload}` +（可选）`/api/notify` toast；
  - `MemoryEvent` 的陪伴向副本 → `/api/diary`、`/api/fleeting`、`/api/notes`；
  - 注册为 ChannelHub 之外的独立 `ExpressionChannel`，config 开关 `companion_charon_enabled`（默认关，
    零生产行为变化）。
- **上行（Charon→FAS）**：Charon 已代理 `/api/nlp`（fascinator.go SendNLP）；
  `fas/companion/perception_sources.py` 定义 `as_cognitive_event(...)`，把 Charon 数据
  （`/api/summary`、日记、todo）转成 `CognitiveEvent(kind="environment", source="charon")` 入认知层。
- **前端最小增量**（文档，不在本仓库实施）：Charon `App.tsx` 加 `EventsOn('fas.utterance')`。
- **不采用插件路线**（PluginPanel 运行时半成品，审计 §5）。

⏳ 缺件请求（按任务书"借来的部分可以暂时占位 请求我寻找或补充"）：
- TTS/STT 输出语音：Charon 无 TTS；候选=前端 Web Speech API 或本地 CosyVoice/F5-TTS——**需用户选型**。
- STT 实时麦克风流：现 ear/ 只支持手动喂文件；需用户确认是否补实时采集。
- Charon 侧 `fas.utterance` 监听组件：属 Charon 仓库改动，本次只提供协议文档与 payload 样例。

## 5. 实施步骤（Phase 3 最小改造清单）

| # | 改动 | 规模 | 行为影响 |
|---|---|---|---|
| 1 | 新增 `fas/contracts.py` `fas/protocols.py`（纯定义） | 新文件 | 无 |
| 2 | 新增 `fas/companion/temporary_llm.py`（TemporaryLLMBackend + CognitiveLanguageAccess 包装） | 新文件 | 无（直通委托） |
| 3 | 新增 `fas/core/assembly.py` + `fas/core/facade.py`（委托既有函数；沙箱装配模式复用 tests 的裸构造） | 新文件 | 无（app.py 不经它） |
| 4 | 修改 app.py 注入点 4–6 行：`engine._nlp_ref`、`cc`、`reflection`、curiosity 传参处换包装器 | 极小 | 无（duck-type 等价） |
| 5 | 新增 `fas/companion/charon_bridge.py` + `fas/companion/perception_sources.py`（默认不接线，开关在 config） | 新文件 | 无 |
| 6 | 新增 `docs/FAS_INTERFACE_GUIDE.md`（契约使用说明 + 迁移路线） | 文档 | 无 |
| 7 | 集成测试（Phase 4）：`tests/test_arch_decoupling.py` | 新文件 | 无 |

**不做**：不拆 `process_nlp`；不动任何算法/参数/模板文本；不改 index.html 契约；
不把 expression_feedback 等存量直写改造（登记债务，见 §6）。

## 6. 已知债务与后续迁移路线（显式登记，不在本次实施）

1. `process_nlp` → 按 façade 分步迁移（每步一个 route 变体，用 fas_log 差分对拍）。
2. 存量直写图违例（expression_feedback/dialogue_signals/eye/vision/mc_knowledge）
   → 改走 `core.record_memory()`；需先给 `MemoryEvent` 补 `frame/edge_type` 字段。
3. `config.py` 1954 行共享 dict → 按认知/陪伴切分为两个子命名空间（涉及热更 API，单独立项）。
4. FAS_IDENTITY 进全部认知任务模板 → 拆 `identity_for(task)`，认知任务用无身份中性模板
   （**会改提示词、需实验回归，属研究侧决策**，本次不动）。
5. cc busy 握手 → façade 化后改事件队列。
6. `mark_accessed` 死路径（L1-MEM-01）→ 认知侧修复项，与解耦无关，本次不碰。

## 7. 验收问题预答（Phase 4 将用测试证明）

- **A（认知成功→单点接入）**：每个成功机制实现一个 Protocol（backend/channel）后在
  `assembly` 处替换注入即可；façade/契约/Companion 不动。示例路径：
  RelationalMemory 成熟 → 替换 `MemoryWriter` 槽；FAS 语言成熟 → 替换 `LanguageBackend` 槽。
- **B（换 LLM 方案，认知不动）**：认知侧只见 `CognitiveLanguageAccess` 三成员；
  测试用两个不同假后端跑同一 core，认知输出逐字段相等、话语不同。
- **C（认知失败，陪伴照跑）**：core 抛异常时 companion 走 fallback 通道出话语；测试注入炸芯。
- **D（真认知 vs 借来）**：`COGNITIVE_MODULE_STATUS.md` + `fas/registry.py`（槽位状态表）共同回答。
- **E（半年后逐个替换）**：§3.2 槽位图即清单；每个槽有 Protocol 文件锚点与测试样例。
