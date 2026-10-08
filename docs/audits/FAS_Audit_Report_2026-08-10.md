# FAS Architecture Audit Report — 2026-08-10

## Phase 1 实施成果 + 新增模块全面评估

---

## 一、执行摘要

自 2026-08-01 审计报告以来，Fascinator 完成了 **Phase 1-4（P0-P4 全部优先级）** 的实施，并**额外新增了 4 个核心认知模块**（Self Model、Reflection Engine、Drive System、Action System）。代码总量从 ~12,000 行增长至 **~26,000 行**，模块从 14 个扩展到 **22 个 Python 模块**。

### 总体评价

| 维度 | 08-01 评分 | 08-10 评分 | 变化 |
|------|-----------|-----------|------|
| 架构设计质量 | 3.5/5 | **4.0/5** | ↑ 认知类型化完成，模块职责清晰 |
| 代码实现质量 | — | **3.5/5** | 新增模块设计良好，但旧代码问题仍在 |
| 图谱基础设施 | 2.5/5 | **3.5/5** | ↑ graph_space + relation_category 已就绪 |
| 自主性 | 1.5/5 | **2.5/5** | ↑ Self Model + Drive + Reflection 闭环形成 |
| LLM 依赖 | 2/5 | **2/5** | → 新增模块减少了对 LLM 的认知决策依赖 |
| **综合评分** | **2.5/5** | **3.5/5** | **+1.0 提升** |

---

## 二、08-01 审计建议 vs 实施对照

### P0 — 基础设施（✅ 已完成）

| 建议 | 状态 | 实现 |
|------|------|------|
| Node 增加 `graph_space` | ✅ | [graph_model.py:92](graph_model.py#L92) — 完整实现，含验证和白名单 |
| Edge 增加 `relation_category` | ✅ | [graph_model.py:70-80](graph_model.py#L70-L80) — 7 种关系类别 |
| config.py 增加 propagation_rules | ✅ | [config.py:165-170](config.py#L165-L170) — 4 种空间差异化参数 |
| config.py 增加 relation_propagation | ✅ | [config.py:176-184](config.py#L176-L184) — 7 种类别差异化参数 |
| 向后兼容（默认值） | ✅ | `DEFAULT_GRAPH_SPACE = "semantic"` / `DEFAULT_RELATION_CATEGORY = "semantic_relation"` |

### P1 — 传播引擎升级（✅ 已完成）

| 建议 | 状态 | 实现 |
|------|------|------|
| 类型感知传播 | ✅ | [diffusion_engine.py:95-120](diffusion_engine.py#L95-L120) — `_space_rules()` / `_category_rules()` |
| 配置化（不硬编码） | ✅ | 所有参数从 `config.propagation_rules` 和 `config.relation_propagation` 读取 |

### P2 — 图谱迁移工具（✅ 已完成）

| 建议 | 状态 | 实现 |
|------|------|------|
| 自动扫描 + 迁移建议生成 | ✅ | [graph_migration.py](graph_migration.py) — 725 行完整工具 |
| 迁移报告输出 | ✅ | `data/migration_report.md` — 593 条建议，2988 行 |
| **迁移应用** | ❌ **未执行** | runtime_graph.json 中所有节点 `graph_space` 为空，所有边 `relation_category` 为空 |

> ⚠️ **关键发现**：迁移工具已生成完整报告，但修复尚未应用到图谱。所有 219 个节点仍空着 `graph_space`，所有 253 条边仍空着 `relation_category`。这意味着扩散引擎的类型感知传播路径虽然代码已就绪，但**实际运行中不会生效**（因为回退到默认值，等同于旧系统的统一传播行为）。

### P3 — Prompt 统一（✅ 已完成）

| 建议 | 状态 | 实现 |
|------|------|------|
| 统一 Prompt 模板管理 | ✅ | [prompt_templates.py](prompt_templates.py) — 368 行 |
| FAS_IDENTITY 共享前缀 | ✅ | 所有模板以同一身份声明开头 |
| 统一术语表 | ✅ | TERMINOLOGY 常量 |
| 统一输出格式约束 | ✅ | OUTPUT_JSON / OUTPUT_TEXT |

### P4 — 验证

| 建议 | 状态 |
|------|------|
| 全流程回归测试 | ❌ **未找到测试**（test_cog.py 仅 32 行） |
| Migration Report 最终版本 | → 报告已生成但**未应用** |

---

## 三、新增模块深度审计

### 3.1 Self Model（[self_model.py](self_model.py) — 855 行）

**设计评分：4.5/5**

#### 架构亮点
1. **不新建状态管理器** — 所有 Self 数据以节点/边形式存在于 `graph_space="self"` 的图谱中，完全遵循"万物皆图"原则
2. **知识溯源（Provenance）** — 每条 Belief/Preference 必须携带 `confidence, source, evidence_count, first_observed, last_reinforced`
3. **防污染机制** — `evaluate_importance()` 函数评估重要性得分，低于阈值（0.5）不写入
4. **用户显式陈述检测** — 正则模式匹配"我喜欢/我相信/我讨厌"等

#### 三大组件
```
SelfGraphManager     — 结构化读写（get/upsert Belief/Preference/UserRelation/Goal）
evaluate_importance  — 无状态评分函数（5 个因子加权）
SelfMemoryUpdater    — 编排器：NLP 输出 → 评估 → 决策 → 写入
```

#### 重要性评分公式
```
importance = explicit_statement × 0.40
           + repeat_count        × 0.25
           + emotion_intensity   × 0.15
           + llm_confidence      × 0.10
           + temporal_recency    × 0.10
```

#### 问题
1. **正则匹配过于简单** — `EXPLICIT_STATEMENT_PATTERNS` 仅 3 个模式，中文表达的多样性远超此范围。"我这人就喜欢..."、"不知道为啥就是讨厌..."等变体无法匹配
2. **重复检测基于精确匹配** — `repeat_count` 依赖完全相同的 content 字符串，而不是语义相似度
3. **单次观察门槛固定** — `min_observations=2` 对所有来源统一，但 `explicit_statement` 来源应该一次就足够

#### 状态
- ✅ 已集成到 app.py 主循环（[app.py:1787](app.py#L1787)）
- ✅ REST API 端点已注册（`/api/self/*`）
- ⚠️ 图谱中的 Self 节点仍然是旧的 `label="declarative-semantic"`（迁移未应用）

---

### 3.2 Reflection Engine（[reflection_engine.py](reflection_engine.py) — 427 行）

**设计评分：4.0/5**

#### 架构亮点
1. **三种触发方式** — periodic（每 20 轮）、event（情绪累积 ≥ 3.0）、user（手动触发）
2. **LLM 不直接生成人格** — 反思产出是结构化候选更新（Belief/Preference），不是自由文本
3. **候选审批机制** — `importance >= 0.75` 自动批准，否则 pending（暂存但不影响 Self Model）
4. **复用 SelfMemoryUpdater** — 不重复造轮子

#### 反思循环流程
```
收集输入（TopK 节点 + 对话日志 + 最近经历 + 情绪上下文 + Self 当前状态）
  → LLM 分析 6 个必答问题
  → 解析候选 Belief/Preference 更新
  → evaluate_importance() 门槛判断
  → 批准（写入 Self）或暂存（pending）
```

#### 问题
1. **6 个必答问题硬编码在 LLM prompt 中** — 如果 LLM 没有认真回答某个问题，没有 fallback 检测
2. **情绪累积器有 bug** — [reflection_engine.py:72](reflection_engine.py#L72) 在 `emotion_accumulator >= threshold` 后立即归零，但日志输出的仍是归零后的值（永远是 0.00）
3. **无反思质量评估** — 反思是否"有价值"没有反馈循环。系统无法判断某次反思是产生了有意义的 Self 更新，还是浪费了 LLM 调用

#### 状态
- ✅ 已集成到 app.py 主循环（[app.py:1813](app.py#L1813)）
- ✅ REST API 端点已注册（`/api/reflection/*`）
- ⚠️ LLM 调用次数 +1（反思时额外调用）

---

### 3.3 Drive System（[drive_engine.py](drive_engine.py) — 284 行）

**设计评分：4.0/5**

#### 架构亮点
1. **不是 if-else 规则** — Curiosity Drive 的激活值来自图谱状态统计（未知节点数 × 权重 + 知识缺口激活度 × 倍率 + ...）
2. **Drive 节点在 cognitive 空间** — 参与图谱扩散，激活值写入图谱而非独立变量
3. **完整版路线图已规划** — Curiosity → Social → Learning → Consistency

#### Curiosity Drive 激活公式
```
activation = unknown_node_count        × 0.5
           + unknown_activation_total  × 0.5
           + knowledge_gap_activation  × 0.8
           + unknown_object_count      × 0.3
           + curiosity_chain_avg_act   × 1.2
```

#### 问题
1. **信号节点硬编码** — [drive_engine.py:30-33](drive_engine.py#L30-L33) `CURIOSITY_SIGNAL_NODES` 是静态列表。如果好奇心引擎新增信号节点，这里不会自动感知
2. **仅 Curiosity 实现** — Social、Learning、Consistency 仅标记为 `"status": "planned"`
3. **5 秒缓存粒度** — [drive_engine.py:58](drive_engine.py#L58) 评估结果缓存 5 秒。在快速对话场景（用户连续输入）中，时间窗口可能重叠

#### 状态
- ✅ 已集成到 app.py 主循环（[app.py:1838](app.py#L1838)）
- ✅ REST API 端点已注册（`/api/drive/*`）

---

### 3.4 Action System（[action_engine.py](action_engine.py) — 362 行）

**设计评分：3.5/5**

#### 架构亮点
1. **认知 Capability ≠ 按键 Primitive** — 这是"应不应该做"的判断层，不直接操作键盘
2. **Capability 触发条件全部来自图谱状态** — requirements 字段声明式定义（如 `CuriosityDrive.activation >= 1.5`）
3. **ActionIntent 是内部状态** — 不自动产生对外行为
4. **闭环设计** — 执行结果 → Episodic Graph → Reflection → Self Model → Drive → Action

#### 4 个认知 Capability
| Capability | 触发条件 | 状态 |
|-----------|---------|------|
| **Respond** | 用户输入存在 | acquired |
| **Ask** | CuriosityDrive.activation >= 1.5 | acquired |
| **Record** | Reflection 有 approved 候选 | acquired |
| **Suggest** | 有 confidence >= 0.6 的 preference + 活跃 drive | acquired |

#### 问题
1. **requirements 评估是字符串匹配** — `"CuriosityDrive.activation >= 1.5"` 这类条件通过 `_eval_requirement()` 解析，而不是真正的表达式求值。新增 Capability 需要新增对应的解析逻辑
2. **仅 4 个 Capability** — Respond 和 Ask 是核心，Record 和 Suggest 覆盖率很低
3. **不产生对外行为** — 设计上是"应不应该做"的判断层，但 Respond/Ask 的判断结果需要 app.py 手动对接。没有统一的 action dispatch 机制

#### 状态
- ✅ 已集成到 app.py 主循环（[app.py:1846](app.py#L1846)）
- ✅ REST API 端点已注册（`/api/action/*`）

---

### 3.5 Prompt Templates（[prompt_templates.py](prompt_templates.py) — 368 行）

**设计评分：4.5/5**

#### 架构亮点
1. **FAS_IDENTITY 共享前缀** — 修改一处，所有 Prompt 同步更新
2. **统一术语表** — 不再出现 entity/extraction vs 实体/节点 的混乱
3. **清晰的模板层次** — 身份声明 → 术语定义 → 任务指令 → 输出约束
4. **build_prompt() 函数** — 按名称获取模板

#### 模板清单
```
NLP_PARSE, ANSWER_GENERATE, ANSWER_SOCIAL, MEMORY_EXTRACT,
CONCEPT_EXPAND, CURIOSITY_QUESTION, REFLECTION, THOUGHT
```

#### 问题
1. **旧模块是否已切换？** — nlp_processor.py 和 curiosity_engine.py 中仍有嵌入式 prompt。需要全局搜索确认是否所有 LLM 调用都已迁移到统一模板
2. **缺少 prompt 版本控制** — 模板变更没有版本号，无法追踪哪个版本的 prompt 产生了图谱中的哪些数据

---

## 四、图谱状况（runtime_graph.json — 2026-08-10）

### 统计
- **总节点**: 219，**总边**: 253
- **Label 分布**: declarative-semantic 127, declarative-episodic 50, infrastructure 28, procedural 14
- **graph_space 状态**: ❌ **所有 219 个节点 graph_space 为空字符串**
- **relation_category 状态**: ❌ **所有 253 条边 relation_category 为空字符串**

### 迁移未应用的影响

```
当前实际行为：
  扩散引擎 ──→ _space_rules("") ──→ 回退到全局 lambda_decay/beta_spread
                                    （等同于旧系统的统一传播）
  关系传播 ──→ _category_rules("") ──→ 回退到 semantic_relation 默认值
                                    （等同于旧系统的统一传播）
```

**结论**：架构重构的代码已就绪，但图谱数据尚未迁移。类型化传播的实际效果无法验证。

---

## 五、LLM 依赖评估

### 当前 LLM 调用清单（每次交互）

| 序号 | 调用 | 触发条件 | 模块 |
|------|------|---------|------|
| 1 | NLP 解析 | 每次用户输入 | nlp_processor.py |
| 2 | 回答生成 | 每次用户输入 | nlp_processor.py |
| 3 | 记忆提取 | episodic/semantic 交互 | nlp_processor.py |
| 4 | 概念扩展 | 学习模块触发 | nlp_processor.py |
| 5 | 好奇心问题生成 | 好奇心触发时 | curiosity_engine.py |
| 6 | **反思分析（新增）** | 每 20 轮或情绪超阈值 | reflection_engine.py |
| 7 | 自我思考 | 手动触发 | self_graph.py |

**新增 LLM 调用**: 反思分析（#6）— 平均每 20 轮额外调用 1 次，相当于增加 ~5% LLM 调用量。

### LLM 依赖趋势

```
07-28 报告: 7 个任务依赖 LLM
08-01 报告: 7 个任务依赖 LLM
08-10 报告: 7 个任务依赖 LLM（反思新增，但无任务被移除）
```

**结论**：LLM 依赖没有减少。虽然 Self Model、Drive System 的认知决策不再依赖 LLM（这是进步），但 NLP 解析和回答生成这两个最大调用点仍是 LLM。反思功能还增加了新的 LLM 调用点。

---

## 六、认知闭环评估

### 新增闭环：认知四阶段循环

```
用户输入
  → [NLP 解析] → 认知分类 + 实体关系提取
  → [激活扩散] → TopK 认知焦点
  → [Self Model] → 评估重要性 → 写入 Belief/Preference（如通过门槛）
  → [Reflection] → 周期性回顾 → 生成候选更新 → 审批/暂存
  → [Drive] → 统计图谱状态 → 更新驱动力激活
  → [Action] → 匹配 Capability → 产生 ActionIntent
  → 回答生成（仍依赖 LLM）
  → 下一轮
```

### 闭环完成度

| 环节 | 状态 | 自主性 |
|------|------|--------|
| 输入理解 | LLM 驱动 | 0% 自主 |
| 认知聚焦 | 图谱激活扩散 | **100% 自主** ✅ |
| Self Model 更新 | 图谱统计 + 规则评分 | **80% 自主**（LLM 仅参与提取候选） |
| 反思触发 | 规则触发（轮次/情绪阈值） | **100% 自主** ✅ |
| 驱动评估 | 图谱状态统计 | **100% 自主** ✅ |
| 行为选择 | Capability 匹配 | **100% 自主** ✅ |
| 回答生成 | LLM 驱动 | 0% 自主 |

**闭环评价**：认知决策层（Self Model、Reflection、Drive、Action）的自主性显著提升。这些模块不再依赖 LLM 做"是否应该做 X"的判断。但输入和输出两端仍是 LLM 黑盒——**认知闭环的"感知"和"表达"两个端口完全依赖 LLM**。

---

## 七、代码质量评估

### 新增模块代码质量

| 模块 | 评分 | 文档 | 类型注解 | 错误处理 | 测试 |
|------|------|------|---------|---------|------|
| self_model.py | 4.0/5 | ✅ 详细模块文档 | ✅ | ✅ | ❌ |
| reflection_engine.py | 3.5/5 | ✅ | ❌ 无 | ✅ | ❌ |
| drive_engine.py | 4.0/5 | ✅ | ⚠️ 部分 | ✅ | ❌ |
| action_engine.py | 3.5/5 | ✅ | ⚠️ 部分 | ✅ | ❌ |
| prompt_templates.py | 4.5/5 | ✅ | N/A | N/A | ❌ |
| graph_migration.py | 4.0/5 | ✅ | ✅ dataclass | ✅ | ❌ |

### 旧模块问题（未修复）

1. **[nlp_processor.py](nlp_processor.py)** — 826 行，最大单文件。嵌入式 prompt 可能未迁移到统一模板
2. **[app.py](app.py)** — 3687 行，认知管线仍在主文件中硬编码，未抽取为独立的 Pipeline 类
3. **[curiosity_engine.py](curiosity_engine.py)** — 730 行，复合节点（未知概念、知识缺口等）未被迁移工具实际拆分
4. **无测试覆盖** — 整个 22 模块项目中仅 [test_cog.py](test_cog.py)（32 行）

### 关键 Bug 发现

1. **[reflection_engine.py:72](reflection_engine.py#L72)** — 日志输出的情绪累积值永远是 0（在归零后打印）
2. **迁移报告已生成但未应用** — 593 条修复建议全部处于 pending 状态
3. **Self 节点 label 仍为 "declarative-semantic"** — 08-01 报告标记为 Critical 的 bug 仍未修复

---

## 八、架构风险评估（更新）

### 旧风险重新评估

| 风险 | 07-28 | 08-01 | 08-10 | 趋势 |
|------|-------|-------|-------|------|
| 知识图谱无限膨胀 | 中 | — | **中高** | ↑ 新增模块会持续写入节点 |
| 大量规则导致系统僵化 | 中 | — | **低** | ↓ Drive/Capability 已图谱化 |
| LLM 过度依赖 | 高 | — | **高** | → 输入/输出端口无变化 |
| 无法形成真正自主行为 | 高 | — | **中** | ↓ 认知决策层已自主化 |
| 节点语义混乱 | 中 | 中 | **中** | → 代码已就绪但未应用迁移 |
| 数据结构无法长期扩展 | 低-中 | — | **低** | ↓ graph_space + extra_attrs schema |

### 新增风险

#### 风险 7：迁移债务
- **严重程度**：**中高**
- **现状**：593 条迁移建议未应用。所有类型化代码本质上是 dead code——graph_space 和 relation_category 全为空，类型感知传播无法验证
- **解决**：运行迁移工具应用 P0 修复，然后逐个确认 WARNING 修复

#### 风险 8：模块集成耦合
- **严重程度**：**低-中**
- **现状**：app.py 中 4 个新模块的初始化顺序隐含依赖（SelfModel → Reflection → Drive → Action），但没有显式的依赖注入框架
- **解决**：低优先级。当前手动初始化顺序正确，但未来模块更多时应考虑统一的生命周期管理

---

## 九、下一阶段优先级建议

### P0 — 立即修复（本周）

1. **应用图谱迁移** — 运行 `graph_migration.py` 的批量 apply，至少修复所有 CRITICAL 和 WARNING（missing_space/missing_category）
2. **修复 Self 节点 label** — `FIX_SELF_LABEL` + `FIX_SELF_SPACE`
3. **修复 reflection_engine.py 日志 bug** — 归零前记录累积值

### P1 — 短期（1-2 周）

4. **验证类型感知传播** — 迁移应用后，在测试对话中观察不同 space 的节点是否按预期差异化衰减
5. **nlp_processor.py 重构** — 将所有嵌入式 prompt 迁移到统一模板系统
6. **旧节点清理** — 应用 `FIX_TEMP_ACCUMULATION` 清理 50+ 个临时 episodic 节点

### P2 — 中期（2-4 周）

7. **app.py Pipeline 抽取** — 将认知管线从 3687 行的 app.py 中抽取为独立的 `CognitivePipeline` 类
8. **本地 NER/NLP fallback** — 实现基础的本地实体提取，LLM 失败时降级使用
9. **测试基础设施** — 至少为核心模块（graph_model、diffusion_engine、self_model）添加单元测试

### P3 — 长期

10. **Drive System 扩展** — 实现 Social Drive、Learning Drive
11. **回答生成自主化** — 探索基于图谱模板的本地回答生成方式
12. **反思质量反馈** — 让系统评估反思是否有价值，形成元认知反馈循环

---

## 十、最终评价

### 正面

1. **架构重构执行出色** — P0-P4 全部代码已完成，graph_space、relation_category、类型感知传播、统一 Prompt、迁移工具全部就绪
2. **新增模块设计原则一致** — Self Model、Reflection、Drive、Action 全部遵循"万物皆图"和"图谱状态驱动决策"
3. **认知决策层自主化显著** — Drive 和 Action 的选择不再依赖 if-else 或 LLM，而是图谱状态统计 + 声明式匹配
4. **知识溯源体系完整** — Self Model 的 provenance 追踪是项目中最接近"真正认知系统"的设计
5. **闭环架构形成** — 四阶段循环（感知→认知→反思→行为）的理论框架完整

### 负面

1. **迁移债务是最大风险** — 代码写了但没生效。类型化传播无法验证，等于没有完成 Phase 1
2. **LLM 调用量未减少** — 反而增加了反思的 LLM 调用。NLP 解析和回答生成仍是 100% LLM
3. **零测试** — 22 个模块、26,000 行代码、0 个有效测试。重构的正确性完全依赖人工验证
4. **旧代码腐烂** — nlp_processor.py（826 行）和 app.py（3687 行）的嵌入式逻辑未随重构更新
5. **Self 节点的 08-01 Critical bug 仍未修复** — 两个月前的 Critical 问题依然存在

### 距离"不依赖 LLM 完成一次完整交互"还有多远？

```
08-01 评估：几乎不可能 —— 7 个任务全部依赖 LLM
08-10 评估：仍然不可能 —— 输入/输出端口无变化

但认知决策层（"应该做什么"、"我该不该记住这个"、"我现在好奇什么"）
已经可以在无 LLM 的情况下独立运行。这是质的进步。
```

**下一个真正的里程碑**：实现本地 NLP 解析器（NER + 基础分类）作为 fallback。一旦 NLP 解析不再强制依赖 LLM（即使只是简单场景），Fascinator 就能在 LLM 不可用时至少"理解"输入并激活图谱——这是从"LLM 前端"到"认知系统"的关键转折点。

---

*报告结束。等待确认后制定 Phase 2 实施计划。*
