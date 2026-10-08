# FAS-Cognitive

FAS（Fascinator）认知架构的 **Cognitive Track**：统一知识图谱、扩散激活
（spreading activation）、情景/关系记忆、写回学习、驱力/好奇、自我模型、
零-LLM 行为决策头、自主行动与认知调节。这里是 FAS 长期保留并逐步实验验证的核心。

陪伴功能（LLM 语言实现、UI、语音、游戏外壳）在姊妹仓 **FAS-Companion**——
依赖方向单向：Companion → Cognitive；本仓不依赖任何语言模型方案。

## 仓库结构

```
graph_model.py / diffusion_engine.py / cognitive_field.py ...   核心动力学（零 LLM）
episodic_buffer.py / experience.py                              记忆与写回学习
dialogue_decision.py                                            行为竞争决策头（零 LLM）
autonomy.py / action_system.py / skills/ / minecraft/           自主与行动
internal_state.py / disposition_store.py / modulation*.py       内部状态与调制
fas/                                                            架构解耦层：
  fas/contracts.py   跨界唯一数据结构（事件/请求/话语/提案）
  fas/protocols.py   稳定接口（认知对语言的最小面 = llm/chat_llm/ask）
  fas/core/          CognitiveCore 门面 + 独立装配（不依赖 Flask）
  fas/registry.py    组件槽位台账：哪些能力是自建、哪些是借来的
docs/               架构审计（ARCHITECTURE_AUDIT）、设计（ARCHITECTURE_PLAN）、
                    认知机制实验台账（COGNITIVE_MODULE_STATUS）、接口指南
FAS_Research_Experiments/ experiments/ research_audit/          实验与裁决记录
tests/              单元测试与架构红线测试（test_arch_decoupling.py）
```

## 快速开始

```bash
pip install -r requirements.txt
pytest tests/ -q          # 离线、零 LLM
```

## 研究纪律（重要）

- **代码存在 ≠ 机制验证**。每个认知机制的实验状态以
  [docs/COGNITIVE_MODULE_STATUS.md](docs/COGNITIVE_MODULE_STATUS.md) 为准
  （validated / partially_validated / failed / bugged_experiment / untested / hypothesis），
  证据指向 `research_audit/` 与 `experiments/` 的原始报告。
- 负结果是结果：预注册实验中扩散的增量价值 REJECTED、决策头 I 实验基线完胜等
  均如实记录在案，不做包装。
- 实验算法、参数与已归档 run 数据不因工程重构而改动（Research validity > architecture purity）。

## 隐私说明

本仓发布前经过自动令牌置换与扫描闸门：个人经历词、真实校名等已通用化；
含真实图谱导出的实验快照（`snapshots/`、`system_snapshot/`）与运行日志不发布，
仅存于本地档案。`data/` 运行时内容（真实图谱、情景记忆、内部状态）一律不入库。

## 与 FAS-Companion 的关系

陪伴仓通过同级路径（或 `FAS_COG_ROOT` 环境变量）引用本仓模块。
替换/接入某个认知机制的标准流程见
[docs/FAS_INTERFACE_GUIDE.md](docs/FAS_INTERFACE_GUIDE.md)。
