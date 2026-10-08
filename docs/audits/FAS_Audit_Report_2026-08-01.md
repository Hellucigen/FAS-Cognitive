# FAS Architecture Audit Report — 2026-08-01

## Architecture Refactor 0.1 Baseline

---

## 一、总体评估

| 维度 | 当前状态 | 目标状态 | 差距 |
|------|---------|---------|------|
| 统一认知空间 | ✅ 单图，未拆分 | 保持并增强 | 低 |
| 认知类型化 | ❌ 无 graph_space | 5 种 space | 高 |
| 关系类型化 | ❌ 无 relation_category | 7 种 category | 高 |
| 类型化传播 | ❌ 统一传播 | 差异化动力学 | 高 |
| 原子概念 | ❌ 大量复合节点 | 原子化 + 关系组合 | 中高 |
| Prompt 统一 | ❌ 分散、重复 | 统一模板风格 | 中 |
| 图谱迁移工具 | ❌ 无 | 自动扫描 + 建议 | 高 |

---

## 二、graph_model.py 审计

### 2.1 Node 当前字段
```
id, weight, activation, label, execution, confidence, extra_attrs, created, last_access
```

### 2.2 问题
1. **`label` 字段语义过载**：同时表示记忆类型（declarative-semantic）和节点角色（infrastructure, procedural, self），不区分"这是什么类型的知识"与"这个节点在系统中扮演什么角色"
2. **缺少 `graph_space`**：无法区分 semantic / episodic / cognitive / self 空间
3. **Self 节点 label 错误**：runtime_graph.json 中 Self 的 label="declarative-semantic"，应为 "self"
4. **Edge 缺少 `relation_category`**：无法按关系类型差异化传播

### 2.3 需要的变更
```python
# Node 新增
graph_space: str  # "semantic" | "episodic" | "cognitive" | "self"

# Edge 新增
relation_category: str  # "semantic_relation" | "causal_relation" | "temporal_relation" 
                        # | "emotional_relation" | "social_relation" | "cognitive_relation"
                        # | "procedural_relation"
```

---

## 三、当前图谱状况（runtime_graph.json）

### 3.1 统计
- 总节点: 219，总边: 253
- declarative-semantic: 127（含 Self、情绪、好奇心基础设施）
- declarative-episodic: 50（大量临时节点：思考_*, 回答记录_*, 反思_*, 事件:*）
- infrastructure: 28（能力节点）
- procedural: 14（WASD 动作 + 好奇心流程节点）

### 3.2 关键问题

#### A. 复合节点（不符合 Atomic Concept）
以下节点应该拆分为原子概念 + 关系组合：

| 节点 ID | 建议拆分 | 原因 |
|---------|---------|------|
| 社交互动 | 社交 + 互动 | 两个可独立复用的概念 |
| 学习目标 | 学习 + 目标（通过 TARGET 关系组合） | 可复用的基础概念 |
| 知识缺口 | 知识 + 缺口 | 原子概念 |
| 未知概念 | 未知 + 概念 | 原子概念 |
| 未知关系 | 未知 + 关系 | 原子概念 |
| 未知属性 | 未知 + 属性 | 原子概念 |
| 未知事件 | 未知 + 事件 | 原子概念 |
| 未知信息 | 未知 + 信息 | 原子概念 |
| 需要确认 | 需要 + 确认 | 认知状态可组合 |
| 需要学习 | 需要 + 学习 | 同上 |
| 主动提问 | 主动 + 提问 | 同上 |
| 回答完成 | 回答 + 完成 | 同上 |
| 知识完善 | 知识 + 完善 | 同上 |
| 等待回答 | 等待 + 回答 | 同上 |
| 生成好奇问题 | 生成 + 好奇 + 问题 | 三层复合 |

> **注意**：以上好奇心基础设施节点（未知概念、知识缺口等）在当前架构中作为认知链路的信号节点使用。它们的原子化需要与 curiosity_engine.py 重构协同进行。

#### B. 流程控制节点混入图谱
以下 procedural 节点本质是认知流程控制，不应作为知识节点：
- 生成好奇问题
- 登记学习目标
- 等待用户回答
- 更新记忆
- 结束好奇状态

建议：保留为 procedural 节点（它们确实承载执行代码），但标记 graph_space="cognitive" 与知识空间区分。

#### C. 临时节点累积
- 50 个 episodic 节点中包含大量临时节点：
  - 思考_* (15个)
  - 回答记录_* (20个)
  - 反思_* (5个)
  - 事件:* (6个)
- 这些节点应该受 episodic 空间的生命周期管理（保留但有衰减/遗忘策略）

#### D. 基础设施节点命名不规范
- `能力_NLP实体关系提取` → 建议 `Capability(nlp_extraction)`，或保持中文但统一前缀
- `UnknownObject0001`~`UnknownObject0008` → 应被好奇心流程消费后清理或合并

#### E. 边关系类型混乱
- 混合了语义关系（属于、导致）、认知基础设施关系（trigger_curiosity、resolve）、视觉关系（appears_with、has_attribute）、程序关系（can_do、acts_on、triggers）
- 没有统一的关系本体（Relation Ontology）
- 英语和中文混用：trigger_curiosity / 属于 / appears_with / 导致

#### F. Self 节点结构
- Self 的 label 应该是 "self" 而不是 "declarative-semantic"（这是 bug）
- Self 的连接节点结构合理（身份、能力、偏好、目标、情绪均有连接）

---

## 四、扩散引擎审计（diffusion_engine.py）

### 4.1 当前传播逻辑
```
a(v, t+1) = (1-λ) * a(v,t) + Σ Δa(u→v)(t)
Δa(u→v)(t) = a(u,t) * w_uv * β / |N+(u)|
```
- 所有节点统一衰减率
- 所有边统一传播增益
- 无 graph_space 感知
- 无 relation_category 感知

### 4.2 需要的变更
- 传播前检查 `graph_space` 选择不同的动力学参数
- 传播前检查 `relation_category` 选择方向/衰减/增益
- 传播规则从 config 读取，不硬编码

### 4.3 传播规则配置化设计
```python
# config.py 新增
"propagation_rules": {
    "semantic": {
        "lambda_decay": 0.05,
        "beta_spread": 1.0,
        "allow_bidirectional": True,
        "activation_cap": 5.0,
        "description": "语义知识：正常传播，稳定衰减"
    },
    "episodic": {
        "lambda_decay": 0.15,
        "beta_spread": 0.7,
        "allow_bidirectional": False,
        "activation_cap": 3.0,
        "description": "情景记忆：快速衰减，单向传播"
    },
    "cognitive": {
        "lambda_decay": 0.08,
        "beta_spread": 1.5,
        "allow_bidirectional": False,
        "activation_cap": 5.0,
        "description": "认知过程：快速形成注意，中等衰减"
    },
    "self": {
        "lambda_decay": 0.01,
        "beta_spread": 0.5,
        "allow_bidirectional": True,
        "activation_cap": 5.0,
        "description": "自我：长期保持，缓慢传播"
    }
},
"relation_propagation": {
    "semantic_relation": {"direction": "bidirectional", "decay": 1.0, "gain": 1.0},
    "causal_relation": {"direction": "forward", "decay": 0.8, "gain": 1.2},
    "temporal_relation": {"direction": "forward", "decay": 1.5, "gain": 0.7},
    "emotional_relation": {"direction": "bidirectional", "decay": 0.3, "gain": 1.5},
    "social_relation": {"direction": "bidirectional", "decay": 0.7, "gain": 0.8},
    "cognitive_relation": {"direction": "forward", "decay": 0.5, "gain": 1.3},
    "procedural_relation": {"direction": "forward", "decay": 0.6, "gain": 1.0, "conditional": True}
}
```

---

## 五、LLM Prompt 审计

### 5.1 当前 Prompt 清单

| 位置 | Prompt 名称 | 行数 | 风格 | 问题 |
|------|-----------|------|------|------|
| nlp_processor.py | SYSTEM_PROMPT | ~140 | "你是...解析器" | 过长，混合实体提取与认知分类 |
| nlp_processor.py | ANSWER_SYSTEM_PROMPT | ~25 | "你是...回答生成器" | 独立风格 |
| nlp_processor.py | SOCIAL_ANSWER_SYSTEM_PROMPT | ~20 | "你是Fascinator" | 与 ANSWER 部分重叠 |
| nlp_processor.py | extract_assertion_graph | ~80 | "你是 Fascinator" | 行内 prompt，难以维护 |
| nlp_processor.py | expand_concept | ~30 | "你是...扩充引擎" | 又一种风格 |
| curiosity_engine.py | generate_question | ~30 | "你是Fascinator" | 嵌入式 prompt |
| self_graph.py | _llm_reflection_summary | ~5 | 纯文本 | 最短但无结构 |
| self_graph.py | _llm_thought | ~5 | 纯文本 | 同上 |

### 5.2 问题
1. **风格不统一**：有时 "你是 Fascinator 认知图谱系统的 NLP 解析器"，有时 "你是Fascinator，一个好奇的认知系统"
2. **角色定义分散**：每个 prompt 独立定义 FAS 的身份
3. **重复规则**：多处重复 "不要输出额外文字" "只输出 JSON"
4. **术语不一致**：entity/extraction vs 实体/节点，speech_act vs illocutionary_act
5. **Token 浪费**：SYSTEM_PROMPT 中大量示例占用 token

### 5.3 统一方案
建立统一 Prompt 模板体系：
```
┌─ FAS_IDENTITY (共享前缀)
│   └─ 所有 Prompt 以相同身份声明开头
├─ TASK_SPECIFIC (任务指令)
│   ├─ NLP_PARSE
│   ├─ ANSWER_GENERATE
│   ├─ ANSWER_SOCIAL
│   ├─ MEMORY_EXTRACT
│   ├─ CONCEPT_EXPAND
│   ├─ CURIOSITY_QUESTION
│   ├─ REFLECTION
│   └─ THOUGHT
└─ OUTPUT_FORMAT (输出约束)
    └─ 统一的输出格式指令
```

---

## 六、config.py 审计

### 6.1 当前缺失参数
- ❌ `graph_spaces` 定义
- ❌ `propagation_rules`（按 space 差异化）
- ❌ `relation_propagation`（按 relation_category 差异化）
- ❌ `relation_ontology`（关系本体定义）
- ❌ `episodic_retention_days`（情景记忆保留天数）
- ❌ `cognitive_cleanup_threshold`（认知节点清理阈值）

---

## 七、知识表示规范审计

### 7.1 违反 Atomic Concept 的节点
（详见第三节 3.2.A）

### 7.2 认知最小行为问题
当前 NLP 的 dialogue_act 分类正确保留了认知行为完整性：
- greeting, question, sharing, request, emotion_expression, thanking, farewell...
- 这些**不应**被原子化，符合规范 ✅

### 7.3 关系本体缺失
当前边的 relation 字段是自由文本，没有统一的关系本体：
- "属于"、"导致"、"引发"、"触发"、"涉及"、"关联"、"源自"、"appears_with"、"has_attribute"、"can_do"、"acts_on"、"triggers"、"same_as"、"bound_to"、"has_key"、"has_movement"...
- 许多关系含义接近但有细微差异（触发 vs triggers vs 引发）
- 英文和中文混用

---

## 八、重构优先级排序

### P0 — 基础设施（必须最先做）
1. **graph_model.py**: Node 增加 `graph_space`，Edge 增加 `relation_category`
2. **config.py**: 增加 propagation_rules、relation_propagation 配置段
3. **向后兼容**：未指定 graph_space 的旧节点默认 "semantic"

### P1 — 传播引擎升级
4. **diffusion_engine.py**: 实现类型感知传播
5. **config.py**: relation_ontology 定义

### P2 — 图谱整理（自动扫描 + 手动确认）
6. **新建 graph_migration.py**: 图谱扫描 + 迁移建议生成
7. 运行扫描 → 输出迁移报告 → 用户确认 → 执行迁移

### P3 — Prompt 统一
8. **新建 prompts.py**: 统一 Prompt 模板管理
9. 各模块引用统一模板

### P4 — 验证
10. 全流程回归测试
11. Migration Report 最终版本

---

## 九、兼容性保证

所有变更遵循以下原则：
- ✅ 不改变现有 API 行为
- ✅ 新增字段有默认值（向后兼容旧图谱）
- ✅ 旧传播行为 = 默认配置的传播行为
- ✅ 迁移工具只生成建议，不自动修改
- ✅ 所有节点仍在同一图中，跨领域联想不受影响

---

## 十、为未来预留

本次重构为以下能力预留空间：
- **Self Model 深化**：Self 节点已有 graph_space="self"，长期保持激活
- **Drive System**：cognitive 空间支持条件传播（满足条件才传播）
- **Reflection 增强**：episodic 空间的衰减策略自然支持记忆巩固
- **Vision 集成**：视觉对象节点可使用 semantic 空间
- **Minecraft 交互**：程序性节点留在 procedural 空间，满足条件传播
- **Capability 发育**：infrastructure label 保留，新增能力自动归类

---

*报告结束。等待确认后进入 Phase 1 实施。*
