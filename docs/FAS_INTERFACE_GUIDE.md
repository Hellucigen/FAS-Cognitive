# FAS 接口指南（解耦层使用说明）

配套：`docs/ARCHITECTURE_PLAN.md`（设计与理由）、`fas/` 包（实现）、
`tests/test_arch_decoupling.py`（可执行验收）。

## 1. 一分钟版

```python
from fas.core.assembly import build_cognitive_core
from fas.companion.assembly import build_language_backend
from fas.companion.pipeline import run_turn

config = {"lambda_decay": 0.05, ...}          # 认知参数由调用方切分提供
core    = build_cognitive_core(config=config)  # 不依赖 Flask/app.py
backend = build_language_backend("llm")        # temporary：MiMo/DeepSeek/Ollama
reply   = run_turn(core, backend, text="我家猫今天特别粘人")
```

跨界只允许 `fas/contracts.py` 的数据结构；
认知模块对语言对象的持有上限是 `CognitiveLanguageAccess`（`llm/chat_llm/ask`）；
陪伴写认知状态只走 `core.perceive / record_memory / propose_action / tick`。

## 2. 三类替换操作（逐槽）

### 换语言方案（LLM → LLM 迁移 / FAS 自建语言机制成熟）
1. 实现 `fas.protocols.LanguageBackend`（六个方法）；
2. 装配处换一行（app.py:754 `_language_backend = TemporaryLLMBackend(nlp)`，
   或独立装配 `build_language_backend("mine")`）。
认知侧四个 LLM 触点（curiosity 提问 / reflection 分析 / free thought / 主动表达）
经 `access()` 的最小面直通，不需要改任何认知模块。

### 接入一个实验成功的认知机制（验收 A 的标准动作）
例：FAS 自建"关系记忆巩固"实验 validated 之后——
1. 新模块实现被替换对象的同名 API（或在 `CognitiveCore.record_memory` 内改一行委托）；
2. `build_cognitive_core(..., 参数=新模块)` 注入；
3. `fas/registry.py` 把槽的 `temporary` 改为 False、`occupant` 改写；
4. `docs/COGNITIVE_MODULE_STATUS.md` 状态升级需附实验证据路径。
Companion 层代码零改动。

### 换陪伴外壳 / 加输出通道（如 Charon）
实现 `ExpressionChannel.deliver(utterance) -> bool`，加进 `run_turn(channels=[...])`。
`fas/companion/charon_bridge.py` 是现成样例（Bridge 127.0.0.1:17734，
`X-Charon-Token` 读 `~/.personal-terminal/config.json`）；
默认关，开启：`config["companion_charon_enabled"]=True`。

## 3. 当前红线（测试自动守护）

- `fas/core/*` 禁止 import：`nlp_processor / prompt_templates / llm_provider /
  ollama_backend / app / flask`（test_core_has_no_companion_imports）。
- `fas/contracts.py` 仅 stdlib（test_contracts_are_pure_stdlib）。
- `TemporaryLLMBackend` 拒绝协议面之外的属性透传（test_wrapper_does_not_leak_full_bus）
  ——防止包装器退化成万能总线。

## 4. 与 app.py 遗留单体的关系

`/api/nlp`（app.py:2999–5347）暂不迁移；新契约化管线 `fas/companion/pipeline.run_turn`
是其**迁移目标形态**（回合内反射快路径/预算门/周期记账/异步反思等机制按
`ARCHITECTURE_PLAN.md §6` 逐段搬入，每段用 fas_log 差分对拍）。
迁移期间两套入口并存是有意状态；`process_nlp` 头部的认知段（激活/扩散/决策/沉淀）
每次重构一段就改为调用 façade 对应方法，禁止在 façade 里复制算法。

## 5. 已知缺件（等待补充，均已在 fas/registry.py 占位）

| 缺件 | 现状 | 候选方案 |
|---|---|---|
| TTS 输出 | 空缺 | Charon 前端 Web Speech API（零新依赖）或本地 TTS 模型 |
| STT 实时流 | ear/ 仅手动喂音频文件 | 需选型（麦克风采集 + VAD） |
| Charon 前端 `fas.utterance` 监听 | 未写（在 Charon 仓库） | App.tsx EventsOn + 气泡组件（小改） |
| 正式搜索 API | actions/web_search.py 爬 HTML | 需选型 |
