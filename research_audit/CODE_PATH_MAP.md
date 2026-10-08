# CODE_PATH_MAP.md — 仓库 → 机制 → 文件的路径映射
**基线**: git 4685726(2026-09-30 工作树)
**用途**: 机制声明、实验与故障归因的代码寻址;每次审计/实验先查此表,避免凭文件名猜测职责。

---

## 0. 入口与拓扑
- `E:/Project/Fascinator/app.py` — 生产 HTTP 服务(Flask)。/api/nlp 主循环:解析 → activate_from_inputs → 扩散 → Top-k → 回答生成(约 4300-5400 行)。回合成功/异常/空输入三出口见 L1-DGR-01/03。
- `config.py` — 唯一配置真源(DEFAULT_CONFIG,图 schema/关系词表/modulator 参数/experience_eventframe 等)。
- `tests/conftest.py` — pytest 收集假面(AST 自动分类脚本式回归,见 L1-TH-01)。

## 1. 认知图谱核心
| 机制 | 文件:关键点 |
|---|---|
| 图模型(节点/边/载荷/序列化) | graph_model.py(KnowledgeGraph/Node/add_edge/get_out_edges/save/load) |
| 传播激活 | diffusion_engine.py(diffuse_step/_emit;budget/decay/λ/cap;正负向支;锁序约定 engine→kg,436-437) |
| 锚点/种子管理 | diffusion_engine.py anchor 集(app.py activate_from_inputs 写入,clear_anchors 清理) |
| 图谱 schema/关系类型 | graph_schema.py + config.py 关系词表(连锁/涉及/结果/网络 等归一到 8 大类,config.py:345-380) |
| 图谱自省 | self_graph.py、graph_integrity_audit.py、graph_evolution_log.py |
| 相似度检索(语义"闭包"入口) | embedding_manager.py + capability_graph.py(FAISS 检索召回段) |

## 2. 状态与动力学
| 机制 | 文件:关键点 |
|---|---|
| 内部状态(需求/神经调制/性格唯一状态源) | internal_state.py(tonic=真值/level=派生;16 镜像节点 in-place) |
| 调制系统(Graph 权威) | modulator_subgraph.py(ensure_modulator_subgraph/投影器)、modulation.py、modulation_events.py、cognitive_field.py(层合并)、modulator_lab.py(实验台薄驱动器) |
| 情绪状态 | personality_baseline.py(mood 瞬态)、temporal_awareness.py(情绪时间窗) |
| 认知需求/复杂度 | cognitive_demand.py、cognitive_complexity.py、cognition_modes.py(六模式+budget)、cognitive_field.py |
| 持续认知循环 | continuous_cognition.py(CC tick:_field_signature/_pulse_gate/_reactivate_field;busy Event) |
| 注意力导出 | cognition_modes.py attention_context(七分区**构建但生产 prompt 不渲染**,L1-CON-1/C-10) |
| 意图生命周期 | activity_tracker.py、action_intents.py(CI_* 节点) |
| 目标形成 | current_goal(activity_tracker)、autonomy.py |

## 3. 感知/摄入(图污染风险面)
| 机制 | 文件:关键点 |
|---|---|
| NLP 解析与实体落图 | nlp_processor.py(1021 认知状态渲染分支=死代码),compound_phrase_handler.py,narrative_trigger.py |
| 对话渠道 | conversation_channel.py、conversation_gap_detector.py、dialogue_signals.py |
| 屏幕感知(Eye) | eye/ 子系统(截屏 OCR+文字方位;SCREEN_OBSERVE 经 EmbodimentRouter) |
| 图谱扩展(LLM 直产) | graph_expansion.py、curiosity_engine.py(CuriosityAuto 边写入 app.py:2232 add_edge 返回检查,CON-3)、knowledge_pack_manager.py |
| 感知留痕缺陷面 | add_edge 失败在感知侧无日志(L1-PIN-01/11,见 _audits/audit_perception_ingestion.md) |

## 4. 经验学习与写回
| 机制 | 文件:关键点 |
|---|---|
| 经历缓冲/时间轴 | episodic_buffer.py、experience.py、experience_replay.py(候选桶+收益驱动退避,零 LLM) |
| 因果归因(规则化,非 do) | 因果学习器(默认开;真实时钟依赖 wall-clock 事件,不调 sweep;加速沙箱时钟需 flush_causal) |
| 事件框架(C21) | action_system.py:1033-1150(**enabled 默认 False**,config.py:1934-1946) |
| 账本/奖赏台账 | disposition_store.py、reward.py(账本通道先验加成) |
| Hebb 强化 | hebbian(自学习循环,ε=0.0015) |
| 叙事注入 | narrative_trigger.py(NARRATIVE_EXTRACT 分段入图) |
| 晋升 | 归因管线候选关联(s≥5/conf≥0.80);能力/记忆晋升仅 /api/memory/approve 手工(L1-CON-7) |

## 5. 决策与行动
| 机制 | 文件:关键点 |
|---|---|
| 对话决策层 | dialogue_decision.py(budget/沉默抑制/话题共振/collect_action_candidates,外层 catch warning) |
| 行动系统 | action_system.py(动作执行+驳回+事件框架)、action_space.py、action_concepts.py、action_resolver.py |
| 行为竞争 | action_system/action_intents 竞争;账本先验进入行动候选 |
| 自主层(具身) | autonomy.py(意图产生)+ embodied_mapper.py(具身适配器)+ Minecraft 桥(minecraft/,BOT_DIR 单一真源) |
| 安全内核 | safety_kernel.py、api_guard.py(Origin 常开+token 按需) |

## 6. 消费者/表达
| 机制 | 文件:关键点 |
|---|---|
| 回答生成 | app.py answer_question(4666 不带 mode/attention_context → CON-1 断裂点)、prompt_templates.py |
| LLM 提供 | llm_provider.py(MiMo API,密钥 data/llm_secret.json)、ollama_backend.py(默认关)、cognitive_demand.py(预算因子) |
| 表达反馈 | expression_feedback.py(失败占位拦截 L1-CON-5)、speech_act_graph.py |
| 反思 | reflection_engine.py、causal_learner(连续反思) |
| 状态监控 | state_monitors.py(情绪/状态断言)、fas_log.py(观测层:cycle 真源/聚合防刷屏) |

## 7. 持久化与运维
| 机制 | 文件:关键点 |
|---|---|
| 存档/快照轮转 | graph_rotation.py(轮转)、graph_evolution_log.py、json_store.py |
| 载入守卫 | graph_model.load(save 缩小守卫:空图/缩水覆写拒绝) |
| 服务器 | app.py main;双进程抢端口/内存态词表坑见记忆 server-process-pitfalls |
| 实验基建 | experiment_mode.py、experiment_recorder.py(内容寻址归档)、prediction_baseline.py |

## 8. 测试通道(参见 REGRESSION_RESULTS.md §6)
- pytest 通道:`python -m pytest tests/` → 仅 12 个真 pytest 文件(82 测试,AST 自动分类)。
- 脚本通道:`python tests/<file>.py` → 93 个脚本式回归(退出码=结果);各文件头注释写明运行方式。
- 实机通道:`tests/real_*.py`(需真实 Minecraft/服务)。
- 机制证伪独立实验:`research_audit/mechanism_falsification/`(falsify.py/generator.py/metrics.py/aggregate.py/analyze_stats.py/figures.py;仅标准库,零生产依赖)。

## 9. 已知结构风险(审计摘要,细节见各 _audits/)
- 锁序约定 engine→kg(diffusion_engine.py:436-437);违规已清零(L1-DGR-02)。
- 词表同步陷阱:落盘归一化要求关系词先注册(config.py:345-380 注释);C21 `结果`/`网络` 补注册但存量边不改。
- 生产图 3648/9088;约 40% 边为 `涉及`(观察共现 fabric)= 高噪声环境(见 MECHANISM_RESULTS §3)。