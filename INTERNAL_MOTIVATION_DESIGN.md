# FAS 内部动机 / 激素调制 / 奖励系统 —— 设计文档（Phase 0）

> 状态：**设计已确认（用户答复见 §F 下方"已确认项"）；Phase 1 已实现**。
> 本文只做三件事：① 代码审查事实；② 完整设计（数据结构 / 接口 / 集成方案）；
> ③ 分阶段实施计划与风险清单。

### 已确认项（2026-09-13）

| # | 问题 | 用户答复 → 落地方式 |
|---|---|---|
| 1 | 激素是否前端可调 | **可调** → Phase 1 已提供 `POST /api/internal/modulator/<name>` + Internal 面板滑块（经唯一入口写，留痕 source=manual） |
| 2 | 性格是否可用户设定 | **支持** → Phase 1 已提供 `POST /api/internal/traits` + `POST /api/internal/reset`（恢复出厂）+ 滑块 |
| 3 | 需求先做 2 个还是 4 个 | **四个** → 安全/探索/掌控/社交，Phase 2 一次接管 DriveEvaluator |
| 4 | attention_context / MODE_CONTEXT_BUDGET | **接线** → Phase 7，先修 `nlp_processor.py` 的 UnboundLocalError 地雷 |
| 5 | 参数调制先开几个 | **先 3 个（θ_action / β / k_attention）**，但**必须写好"一键开启全部"的开关** → Phase 5 实现 `param_modulator.enable_full()` 与配置项（默认只开 3 个） |
| 6 | 是否允许改 bot.js 补时间/光照/背包 | **补上** → Phase 6 扩展 `/state`（`timeOfDay` / 脚下方块光照 / inventory 概要），缺失时对应需求驱动项保持不激活 |

### 实施进度

| Phase | 状态 | 交付 |
|---|---|---|
| 1 内部状态层 | ✅ 完成 | `internal_state.py`（唯一写入口 / cycle_id / 环形历史 / 原子落盘 / 16 个镜像节点 in-place / 快照恢复）、`tests/test_internal_state.py`（38 项）、10 个 API、前端 **Internal** 面板（激素与性格可调 + 需求只读 + 解释） |
| 2 需求系统 | ⏳ 待开始 | 4 需求紧迫度 + 接管 DriveEvaluator |
| 3 奖励与 PFE | ⏳ | `compute_reward` + expectations + 台账（接口已预留 `record_reward`） |
| 4 激素调制 | ⏳ | `update_modulatory_state` + tonic/phasic + 消费点接线 |
| 5 参数调制 | ⏳ | `param_modulator.py`（默认 3 参数 + 一键全开） |
| 6 Minecraft 闭环 | ⏳ | `/state` 补字段 + 端到端 |
| 7 LLM 门控 | ⏳ | `llm_gate.py` + 预算/token 修复 + attention_context 接线 |
| 8 评估与论文 | ⏳ | 六项验证 |

---

## A. 代码审查报告

### A.1 模块结构与职责（与本次设计相关的部分）

| 层 | 模块 | 现状职责 |
|---|---|---|
| 图 | `graph_model.py` | Node/Edge/KnowledgeGraph；`graph_space` ∈ semantic/episodic/cognitive/self；原子落盘 `save()`；结构变更回调（`_cb_node_added` 等）；`_note_evo` 演化日志（**不落盘**） |
| 图 | `self_graph.py` | self 空间引导（`bootstrap_self` **全库无调用点**）、`inject_emotion`、`set_goal/reinforce_goal`、偏好、`generate_thought` |
| 图 | `self_model.py` | `SelfGraphManager`（信念/偏好/用户关系），`SelfMemoryUpdater.process_turn` + `evaluate_importance` 重要度门控 |
| 扩散 | `diffusion_engine.py` | 激活注入/扩散/衰减/前沿/topk/行动队列/锁门控；**所有参数每次调用现读 `self.config`** |
| 认知循环 | `continuous_cognition.py` | 2.5s tick：decay+diffuse、记忆再点火、pulse（CI 形成/竞争）、自由思考、检测器轮询、自主行动节拍、离线反思 |
| 认知循环 | `app.py` /api/nlp | 一轮对话的完整编排（1858 行起，插入点见 A.5） |
| 动机 | `drive_engine.py` | 仅 `CuriosityDrive` 有评估（5 项加权、硬编码）；`SocialDrive/LearningDrive/ConsistencyDrive` 是 **stub（activation 恒 0）**；直写节点激活绕过扩散 |
| 情绪 | `self_graph.inject_emotion` | 事件 `-[引发]->` 情绪 `-[感受]->` Self，**权重恒 0.7/0.6，无强度/衰减字段** |
| 性格 | `personality_baseline.py` | `mood_valence`（Python 瞬态，不落盘）+ 6 条 `BASELINE_PRIORS` 播种 disposition |
| 性格 | `disposition_store.py` | 「情境×行为」倾向标量动力学（delta/decay/晋升/证据门控）——**全库唯一的晋升机制** |
| 奖赏 | `app.py:2407-2417` | 唯一汇聚点：`classify_outcome` → `buffer.annotate_expression` → `cc.note_outcome` + `persona.mood_event` + `disposition_store.apply_outcome` |
| 调节 | `cognitive_regulation.py` | 锁 / 状态检测器 / 触发器 + 认知事件总线（`submit_state` / `drain_cognitive_events`） |
| 自主 | `autonomy.py` | 意图候选/竞争/可行性/执行/反馈；权重表可替换；`_apply_reward` 已接既有奖赏链 |
| 具身 | `minecraft_embodiment.py` | 感知（含未知检测/指纹）、8 类语义意图 → 已有动作、真实回执、取消 |
| 模式 | `cognition_modes.py` | 6 种 LLM 模式 + `demand_score` + `BudgetManager` + `attention_context`（**后者零消费**） |
| 审计 | `graph_evolution_log.py` | 演化日志接口齐全，`save/load` **零调用点 → 纯内存** |
| 持久化 | `json_store.py` | 原子写原语（fsync + os.replace），锁/检测器/触发器/自主在用 |

### A.2 可直接复用的接口（**不重复造**）

1. **奖赏出口**：`persona.mood_event("positive"|"negative")`、`cc.note_outcome(negative)`、`disposition_store.apply_outcome(ctx, behavior, outcome, evidence)`、`buffer.annotate_expression` —— 新奖励系统**汇总到这些出口**，不新建第二套情绪/倾向。
2. **结果判定**：`disposition_store.classify_outcome(prev_expr, text, parsed, gap) -> (outcome, detail)`（38 个关键词标记）—— 作为「外部奖励」的一个**分量**，不再是唯一来源。
3. **图驱动表示**：`Self -[驱动力]-> {CuriosityDrive,SocialDrive,LearningDrive,ConsistencyDrive}` 节点 + `node.activation` —— 需求系统**接管**这些节点的评估，而不是新增平行节点。
4. **情绪载体**：13 个 `type=="emotion"` 节点（图上已有、参与扩散与反思）—— 给它们**加数值字段**，不新建情绪节点。
5. **事件总线**：`regulation.submit_state/register_handler/drain_cognitive_events` —— 内部状态变化以结构化事件暴露，前端/触发器直接可用。
6. **自主行动骨架**：`AutonomousLoop` 的候选→评分→可行性→执行→反馈全链路，以及「无依据不动手 / 用户优先 / 不做攻击 / 安全采集清单」四条硬约束。
7. **参数热路径**：λ/β/Dmax/θ/ε/种子映射/θ_action 全部**每次调用现读**同一个 config dict → 调制器写这个 dict 即对所有模块立刻生效（无需改读取点）。
8. **持久化原语**：`json_store.atomic_write_json`（已用于 4 个模块）。
9. **可解释性雏形**：`autonomy` 的 `explain`（逐分量数值）、`disposition` 的 `evidence[]`、`importance.factors`。

### A.3 已实现（不要重复）

- 激活扩散 / 注意选择 / TopK / 行动队列 / 锁门控（7 个介入点）
- 情绪节点 + 情绪共振注入 + 反思中的情绪事件窗口
- mood valence（事件→数值→prompt/自主评分）
- 「情境×行为」倾向的学习（正负反馈、证据、晋升、衰减）
- 好奇心（未知检测→提问→沉淀）
- 反思（触发条件、候选门控、图写入）
- 自主行动闭环（Phase D）+ Minecraft 具身（感知/动作/回执/取消）
- LLM 预算（次数/日额）+ 6 模式

### A.4 缺失（本次要补的）

1. **没有需求系统**：`drive_engine` 只有 1 个在跑的公式 + 3 个空壳；无「当前水平/理想区间/紧迫度/增长-满足来源」。
2. **没有激素/神经调制层**：`hormone/多巴胺/皮质醇/血清素/催产素` 全库零命中；`docs/fas_original_vision.md:71-74` 与 `fas_cognitive_architecture_v2.md:112-119` 描述过但未实现。
3. **没有奖励预测误差（PFE）**：`fas_mechanism_audit.md:460-464` 已自认「只有原始奖赏，无 PFE」。
4. **没有性格维度**：`personality_baseline` 只有瞬态 mood + 6 条先验；无「探索倾向/风险容忍/社交开放/坚持/谨慎/新奇偏好/节约/反馈敏感」这类稳定偏置。
5. **没有参数调制器**：`p(t)=clip(...)` 无实现；`k_top/drive_system/action_system` 是**死配置**，`inter_decay_floor` 想调也调不到（不在 config 里）。
6. **没有 cycle_id / 结构化内部状态日志**：无法把一轮里的 self_model 更新、情绪注入、drive 评估、倾向回写、演化条目串成因果链。
7. **token 用量不真**：`BudgetManager` 只数次数；`estimated_tokens` 形参未被使用；`per_turn`/`max_tokens_turn` 从未被检查；provider 丢弃 `usage`。
8. **`attention_context` / `MODE_CONTEXT_BUDGET` 全链路死代码**（前者还有一处 UnboundLocalError 地雷：`nlp_processor.py:689-706` 若真传参会用到未定义的 `cog_str`）。
9. **`EpisodicBuffer` 无落盘**；晋升是空操作（只打标记）。
10. **`graph_evolution_log` 不落盘**。

### A.5 冲突与最小兼容方案

| # | 冲突 | 最小兼容方案（不重构） |
|---|---|---|
| C1 | **「需求」与现有 Drive 概念重叠**（`fas_mechanism_audit.md:355-368` 自己也建议「drive 应为对图内动机节点的周期重估计」） | 需求系统**接管** `DriveEvaluator`：需求 = 内部变量，Drive 节点 = 需求在图上的可解释投影。保留 `Self-[驱动力]->X` 边与 `node.activation` 语义（既有消费方：`autonomy._score`、`action_engine._check_ask` 读 `CuriosityDrive.activation>=1.5`）。**不新增第 5 套动机表示**。 |
| C2 | **mood valence 在图外**（瞬态、不落盘），而新设计要持久化内部状态 | valence **内化**到新状态层（`data/internal_state.json`），`PersonalityBaseline.mood_event/current_mood/mood_context` 保留同名签名改为读写新层 → 消费方（app.py:2639、autonomy 评分）零改动。 |
| C3 | **情绪节点无强度/衰减**，而激素需要数值载体 | 给**现有 13 个情绪节点**加 `extra_attrs.strength / half_life / last_update`（缺省值时按现在的行为退化：强度=activation）。`inject_emotion` 增加可选 `intensity` 参数，默认保持 0.7/0.6 不变。**不建新情绪节点**。 |
| C4 | **`PUT /api/config` 无法新增键、嵌套块整块替换、无校验/回滚/持久化；bool 强转 `bool("false")==True`** | 参数调制器**不走 API**：进程内直接写同一 config dict（已验证所有热路径现读），并由调制器自己做**边界/分组/速率限制/快照/恢复/日志**。API 侧仅新增只读观测端点。PUT 的这三处缺陷单独记为待修项（改它属于 Phase 4 的小修，不在本次设计里顺手改）。 |
| C5 | 三个 Drive stub 在 live 图里 `graph_space="semantic"`（代码意图是 cognitive），会走错传播参数 | 需求系统上线时**一次性数据修复**（把 4 个 Drive 节点 space 校正为 cognitive，写演化日志）。属数据修正，不动图结构。 |
| C6 | 奖赏链现有出口是「用户反馈驱动」的对话路径 | 新奖励接口输出**分量结构**，再分别喂给：`mood_event`（情绪瞬态）、`disposition.apply_outcome`（行为倾向，仅当事件确实来自对话行为）、以及新增的 expectation 表。**不改这三处的语义**。 |
| C7 | `autonomy._score` 里 `rest=0.6 / withdraw=0.9` 是硬编码常量 | 改为读需求紧迫度（安全需求→rest/withdraw，探索需求→observe/explore…）并受性格调制；权重表结构不变。 |
| C8 | `EpisodicBuffer`/`evolution_log` 不落盘 | 新状态层**只持久化聚合量**（需求/调制变量/性格/期望表/奖励台账环），不接管 buffer。演化日志落盘作为 Phase 1 的可选项（`json_store` 已有）。 |

### A.6 不能确定 / 需要实测的点

1. `mineflayer` 的 `bot.time.timeOfDay` 与方块光照能否进 `/state`（当前 `/state` **无时间/光照/背包**字段）——「夜晚/缺少庇护所」这两个安全需求驱动项依赖它，实现前需实测（Phase 6 前置）。
2. 现有 13 个情绪节点与 `EMOTION_NODES` 列表不一致（`好奇` 不在列表、`沮丧` 是孤儿）——新增数值字段时按 **`type=="emotion"` 从图上推导**，不信任硬编码列表。
3. 用户是否希望激素水平**可手工编辑**（前端滑块）——影响是否暴露写接口。
4. 性格维度是否允许用户设定（出厂值 vs 可调）——影响 trait 的持久化与 API。
5. `attention_context` 是接线还是删除——取决于你是否还要保留「注意力上下文进 prompt」这个方向。

---

## B. 总体架构

三层加一条链路（严格遵守「不要让运行时计算变成图数据库操作」）：

```
① 图谱层（长期结构 + 可解释状态节点）
   Self ─驱动力→ {CuriosityDrive, SocialDrive, LearningDrive, ConsistencyDrive}
   Self ─感受→ {13 个情绪节点}（+ 新增 strength/half_life）
   Self ─信念/偏好/用户关系→ …（既有）
   Self ─内部状态→ {多巴胺样, 皮质醇样, 血清素样, 催产素样}（镜像节点，解释用）
   Self ─需求→ {安全需求, 探索需求, 掌控需求, 社交需求}（镜像节点，解释用）

② 运行时状态层（唯一可写状态源，data/internal_state.json，原子落盘）
   modulators{4} · needs{4} · traits{8} · expectations{} · cycle_log[]

③ 控制器层（每轮/每 tick 确定性执行，零 LLM）
   InternalStateController
     ├─ update_modulatory_state(cycle)   ← 激素更新（唯一入口）
     ├─ update_needs(cycle)              ← 需求更新（增长/满足/紧迫度）
     ├─ compute_reward(...)              ← 奖励 + PFE
     ├─ update_traits(...)               ← 极慢性格学习（有证据门槛）
     ├─ modulate_params(...)             ← 写 config（有边界/速率/快照）
     └─ gate_llm(purpose, cycle)         ← LLM 门控（预算+不确定性+代价）

④ 链路：感知 → 需求/调制变量 → 参数调制 → 扩散/注意 → 目标竞争 → 行动 →
        结果 → 奖励/PFE → 期望更新 → 需求满足/调制变量变化 → 下一轮
```

**核心不变量**（实现时必须测试）：

- I1 运行时状态只有**一个写入口**（控制器）；其它模块**只读**。
- I2 每轮/每 tick 生成一个 `cycle_id`，所有状态写入、演化条目、奖励记录、LLM 调用都带它。
- I3 参数调制的每一步都有：边界裁剪 → 分组总量限制 → 单轮 Δ 上限 → 漂移日志。
- I4 图只承载**可解释状态节点**（每个调制变量/需求/性格各一个镜像节点，in-place 更新），不承载逐轮数值序列。
- I5 任何自主行为都必须能回答「为什么」（候选评分分量 + 需求紧迫度 + 调制变量贡献）。

---

## C. 数据结构与接口

### C.1 运行时状态文件 `data/internal_state.json`

```jsonc
{
  "version": 1,
  "updated_at": "2026-09-13 20:00:00",
  "cycle_seq": 12345,                       // 单调递增，生成 cycle_id 用
  "modulators": {
    "dopamine":   {"tonic": 0.50, "phasic": 0.0, "baseline": 0.50,
                   "min": 0.0, "max": 1.0, "decay_per_min": 0.02,
                   "last_update": 1789..., "last_sources": ["goal_progress"],
                   "effects": {"exploration_bias": 0.15, "memory_gain": 0.10}},
    "cortisol":   {"level": 0.30, "baseline": 0.30, "min": 0.0, "max": 1.0,
                   "decay_per_min": 0.05, "last_update": 1789...,
                   "last_sources": ["danger_nearby"],
                   "effects": {"threat_priority": 0.20, "risk_tolerance": -0.25}},
    "serotonin":  {"level": 0.55, "baseline": 0.55, "min": 0.0, "max": 1.0,
                   "decay_per_min": 0.01, "last_update": 1789...,
                   "last_sources": ["stable_interaction"],
                   "effects": {"impulse_suppression": 0.20, "mood_recovery": 0.15}},
    "oxytocin":   {"level": 0.40, "baseline": 0.40, "min": 0.0, "max": 1.0,
                   "decay_per_min": 0.03, "last_update": 1789...,
                   "last_sources": ["shared_activity"],
                   "effects": {"social_value": 0.25, "companion_focus": 0.20}},
    "history": [{"cycle_id": "c_000123", "ts": "...", "changes": {"cortisol": [0.30, 0.42]},
                 "sources": ["danger_nearby"], "explain": "敌对生物 3.0 格内"}]   // 环形，保留最近 200
  },
  "needs": {
    "safety":     {"level": 0.20, "setpoint": 0.0, "ideal_range": [0.0, 0.30],
                   "urgency": 0.0, "growth_rate": 0.0, "satisfaction_rate": 0.0,
                   "drivers": {"health": 20, "food": 20, "danger_dist": 12.0, "night": false},
                   "last_update": 1789..., "peak": 0.20, "satisfied_count": 3},
    "exploration":{"level": 0.55, "setpoint": 0.30, ...},
    "competence": {"level": 0.40, "setpoint": 0.25, ...},
    "social":     {"level": 0.35, "setpoint": 0.30, ...}
  },
  "traits": {
    "exploration_bias": 0.55, "risk_tolerance": 0.45, "social_openness": 0.60,
    "persistence": 0.50, "caution": 0.50, "novelty_preference": 0.55,
    "resource_thrift": 0.50, "feedback_sensitivity": 0.60,
    "baseline": { /* 出厂先验，永不改动，用于恢复 */ },
    "history": [{"cycle_id": "...", "trait": "risk_tolerance", "from": 0.45,
                 "to": 0.4512, "evidence": "连续 4 次高危行动成功", "confidence": 0.3}]  // 环形
  },
  "expectations": {
    "explore@world":    {"expected": 0.18, "count": 7, "last_rpe": 0.05, "decay": 0.99},
    "collect@oak_log":  {"expected": 0.25, "count": 3, "last_rpe": -0.10, "decay": 0.99},
    "follow@Hellucigen":{"expected": 0.40, "count": 12, "last_rpe": 0.02, "decay": 0.99}
  },
  "reward_ledger": [{"cycle_id": "c_000123", "ts": "...", "reward": 0.12,
                     "components": {"internal_goal_progress": 0.20, "cost_time": -0.05,
                                    "social_user_positive": 0.0},
                     "prediction_error": 0.07, "source_event": "action_result",
                     "affected_needs": ["competence"], "affected_goals": [],
                     "explanation": "采集成功：预期 0.25 实际 0.32"}],
  "last_mood": {"valence": 0.12, "updated_at": 1789...}   // C2 内化
}
```

**写入策略**：每轮结束（和自主 tick 结束时）**批量一次**原子写；`cycle_log/reward_ledger/history` 都是环形（各自上限 200/200/200），避免文件膨胀。

### C.2 控制器接口（`internal_state.py`，新文件）

```python
class InternalState:
    """运行时状态唯一写入口。所有数值变更必须经此处（I1）。"""

    def __init__(self, kg=None, engine=None, config=None, persona=None,
                 disposition_store=None, regulation=None,
                 data_dir="data", save_graph_fn=None): ...

    # ── 生命周期 ──
    def begin_cycle(self, kind: str, meta: dict = None) -> str:   # → cycle_id
    def end_cycle(self, cycle_id: str, outcome: dict = None) -> dict  # 结算+落盘

    # ── 唯一更新接口（规范 §5.3 签名，参数按现有代码落地）──
    def update_modulatory_state(self, cycle_id, event=None, reward_signal=None,
                               internal_state=None, delta_time=None) -> dict
    def update_needs(self, cycle_id, event=None, perception=None,
                     reward_signal=None, delta_time=None) -> dict

    # ── 奖励 ──
    def compute_reward(self, previous_state, current_state, action=None,
                       event=None, user_feedback=None) -> dict
    #   → {reward_value, components, prediction_error, source_event,
    #      affected_goals, affected_needs, explanation}

    # ── 性格（极慢学习，有证据门槛）──
    def update_traits(self, cycle_id, reward_signal=None) -> dict

    # ── 参数调制 ──
    def modulate_params(self, cycle_id) -> dict      # → {param: [old, new], ...}
    def snapshot_params(self) -> dict                # 存基线+当前
    def restore_params(self, snapshot: dict = None) -> dict

    # ── LLM 门控 ──
    def gate_llm(self, purpose: str, cycle_id=None, ctx=None) -> dict
    #   → {call: bool, mode: str, reason: str, est_tokens: int, policy: str}

    # ── 观测 ──
    def state(self) -> dict                          # 前端/API 总览
    def recent_rewards(self, n=20) -> list
    def recent_cycles(self, n=20) -> list
    def explain(self, cycle_id: str) -> dict          # 「为什么她现在想探索」
```

### C.3 激素层（`neuromodulators.py` 或并入 `internal_state.py`）

- 4 个变量，字段见 C.1；**声明为功能抽象，不声称生理对应**（写入模块 docstring 与本文档）。
- **Dopamine 拆两个量**（避免「开心=多巴胺高」）：
  - `tonic`：慢变量，表征「总体回报预期水平」，参与探索/利用权衡与记忆增益；变化率小。
  - `phasic`：脉冲量，由 RPE 驱动，秒级衰减，用于**标记学习事件**（goal priority 提升、记忆强化权重、相似行动的期望更新）。
- 更新公式（每个事件一次，统一入口）：

```
input_signal(e)      = Σ_k w_k · feature_k(e)          # 事件特征：目标进展/危险/社交/新奇…
tonic_delta          = η_tonic · (input_signal − tonic) − decay_per_min · Δt_min
phasic               = clamp(RPE, −p_max, +p_max) · exp(−Δt/τ)     # τ≈30s
level                = clip(level + Σ α_m·a(m,t)（需求/性格调制项）, min, max)
```

- **反馈给图**：每个变量一个镜像节点（`Self -[内部状态]-> 多巴胺样` 等，`graph_space="self"`，`extra_attrs={type:"modulator", level, tonic, phasic, updated}`），in-place 更新；同时把变化写 `regulation.submit_state("调制:皮质醇", level, source="perception")`，用户可用触发器对激素水平做反应。
- **消费点（这是「调制」真正落地的地方，必须全部接线，否则只是记数）**：
  - `tonic(dopamine)` → 探索/利用权重（`autonomy._score` 的 explore/observe 分量）、`memory_gain`（新节点 weight / 检索 topk 小幅增益）、新链接的记忆强化阈值（`self_model.evaluate_importance` 的 `llm_confidence`/`repeat_count` 分量偏置）。
  - `cortisol` → 危险类候选优先级提升、`risk_tolerance` 临时下调、未解决问题（失败尝试）的注意力偏置、行动阈值 `θ_action` 上调。
  - `serotonin` → 冲动抑制（对高成本行动的抑制项）、情绪恢复速率（mood valence 的回归基线速度）、长期目标坚持（`persistence` 的短期修正）。
  - `oxytocin` → 社交行为价值（`follow/communicate` 的奖励权重）、伙伴聚焦（该玩家相关节点激活增益）、共享经历的记忆强化。

### C.4 需求系统（`needs.py`）

4 个需求，统一结构（字段见 C.1）。**紧迫度**是行动竞争力的核心量：

```
deficit(n)   = max(0, ideal_range_hi(n) − level(n)) + max(0, level(n) − ideal_range_hi(n))×0.5
urgency(n)   = clip( deficit(n) × w_need(n) × (1 + λ_trait(n)) × (1 + κ_mod(n)), 0, 1 )
w_need(n)    = 基础权重 × 性格调制（见 C.5）
λ_trait(n)   = 关联性格维度偏离中位的比例（如 safety ↔ caution/risk_tolerance）
κ_mod(n)     = 关联调制变量（如 safety ↔ cortisol+，social ↔ oxytocin+）
```

**增长/满足来源（全部来自可观测事实，可解释）**：

| 需求 | 增长来源（drivers） | 满足来源 | 关联目标类型 |
|---|---|---|---|
| 安全 safety | 血量低、饥饿低、敌对生物距离、夜晚/暗处、无庇护（若可感知） | 血量/饥饿恢复、威胁消失、进入安全状态 | rest / withdraw / 获取食物 |
| 探索 exploration | 距上次新奇刺激时长、未知对象数、信息缺口节点激活、探索正反馈历史 | 发现新对象/新区域、获得新知识 | observe / approach / explore |
| 掌控 competence | 未完成目标数、近期失败次数、缺工具/缺材料、任务接近完成 | 目标完成、失败重试成功、取得资源/能力 | collect / 目标推进 |
| 社交 social | 与用户互动间隔、用户情绪表达、共同活动结束后的空白 | 用户回应、共同完成目标、共享记忆被重新激活 | follow / communicate |

**与 Drive 节点的关系（C1 接管方案）**：`DriveEvaluator.evaluate()` 保留签名与返回结构，内部改为读需求紧迫度：

```
CuriosityDrive.activation  = 5.0 · urgency(exploration)
SocialDrive.activation     = 5.0 · urgency(social)
LearningDrive.activation   = 5.0 · urgency(competence)
ConsistencyDrive.activation= 5.0 · min(1, 未解决矛盾数/3)     # 暂用既有信号，Phase 3 细化
```

- 节点 `extra_attrs` 增加 `need`, `urgency`, `drivers`, `updated`（解释用）。
- 保留「直写 activation + mark_active」，但**新增**：写完后经 `engine` 的锁门控检查（被 `scope=action` 锁住的 Drive 不参与行动竞争）——与 Phase D 的锁语义一致。

### C.5 性格系统（`traits.py`）

8 个维度 ∈ [0,1]，`baseline` 出厂先验（不可变，用于恢复），`current` 极慢学习：

- **调制对象（规范 §9 的 8 条，逐条落地）**：
  1. 需求增长/衰减速率：`growth_rate(n) ×= (0.5 + trait)`。
  2. 奖励权重：`compute_reward` 的 `w_*` 乘性偏置（如 `social_value×(0.5+social_openness)`）。
  3. 行动选择偏好：`autonomy._score` 的分量权重再乘 trait 偏置。
  4. 探索/利用：`explore_weight ×= (0.5 + novelty_preference)`，并由 `dopamine.tonic` 再调。
  5. 注意候选容量：`k_attention = round(base_k × (0.75 + 0.5×exploration_bias))`（进参数调制器，带边界）。
  6. 扩散深度/增益：`persistence/caution` 分别对 `Dmax/β` 施加**小**偏移（经参数调制器的速率限制）。
  7. 失败恢复：`failure_cooldown ×= (1.5 − persistence)`。
  8. LLM 介入倾向：`gate_llm` 的阈值偏移（`feedback_sensitivity` 高 → 更愿意为社交细腻度调用）。
- **学习规则（防「一句夸奖改性格」）**：
  - 只有**聚合证据**才更新：同一维度的候选 Δ 先累积到 `evidence_buffer`，达到 `min_evidence`（默认 5 条同向证据）才应用一次，且**单次 |Δ| ≤ 0.005**、**每日累计 |Δ| ≤ 0.01**。
  - 每次应用写 `history`（含 cycle_id、from/to、evidence、confidence），可回滚。
  - 与 disposition 的语义切分（写进 docstring）：`traits` = 跨情境的稳定偏置；`disposition` = 具体「情境×行为」倾向；`mood` = 瞬态效价。**三者不互写**。

### C.6 奖励系统（`reward.py`）

统一接口（规范 §7 签名）：

```python
def compute_reward(previous_state, current_state, action, event,
                   user_feedback=None) -> dict
```

输出：`{reward_value, components, prediction_error, source_event, affected_goals, affected_needs, explanation}`。

**分量表（每条都可由事实算出，权重可配 + 受性格调制）**：

| 类别 | 分量 | 来源 |
|---|---|---|
| 外部 | `external_user_praise` / `external_user_objection` | `classify_outcome` 的标记 + 用户明确表扬/否定的措辞命中（**仅为分量之一**） |
| 内部 | `internal_novelty`（新对象/新区域）、`internal_goal_progress`、`internal_knowledge`（新链接/新事实）、`internal_resource`（获得物品）、`internal_danger_reduced`、`internal_prediction_correct` | 感知差分、图变化、行动结果 |
| 社交 | `social_shared_activity`、`social_user_initiated`、`social_proactive_answered`、`social_shared_memory_recalled` | chat_log / buffer / 用户关系节点 |
| 成本 | `cost_failure`、`cost_resource`（耗时/消耗）、`cost_time`、`cost_risk`、`cost_need_pressure`、`cost_user_refusal`、`cost_prediction_error`、`cost_infeasible` | 行动结果、需求紧迫度、期望表 |

**PFE 与学习**：

```
predicted    = expectations[key].expected              # key = f"{action_type}@{target_type}"
actual       = reward_value
rpe          = clip(actual − predicted, −0.5, +0.5)
expected    ← expected + α · rpe,   α = 0.1 (可配), 期望值 clip[−1, 1]
```

RPE 的四个去向（规范 §8）：`dopamine.phasic`、目标优先级（`autonomy` 候选分数加 `β·expected`）、记忆强化（新节点 weight/`importance` 偏置）、相似行动的未来期望（同 key 表）。

**反失控护栏（逐条对应规范 §8 的「必须避免」）**：

| 风险 | 护栏 |
|---|---|
| 失败后无限降低所有动机 | 成本只作用于**该 key 的期望**与**该需求**；不存在全局「motivation」变量；`cost_failure` 有上限且随 `persistence` 衰减 |
| 成功后无限增加激素 | tonic 有 `baseline` 回归 + 上限；phasic 有 τ 衰减与 clamp；每日总变化量上限 |
| 奖励数值直接等于情绪文本 | `reward.components` 是数值结构；`mood` 由 `mood_event(positive/negative)` 单独驱动，文本永不直接进奖励 |
| 一句夸奖永久改变性格 | traits 走证据缓冲 + 单次/每日上限 + 可回滚历史 |
| 奖励绕过行动结果 | `compute_reward` 的 `action/event` 缺省时 `cost_infeasible` 记账且 `reward_value` 上限压到 ≤0.05；LLM 文本推断出的奖励**标记 source=text_inferred 并乘 0.2 折扣** |

### C.7 参数调制器（`param_modulator.py`）

`p(t) = clip(p₀ + Σ_m α_{m,p} · a(m,t), p_min, p_max)`，其中 `a(m,t)` = 调制源的归一化偏离（需求紧迫度 / 激素水平 / 性格偏离 / 情绪强度）。

**注册表（第一阶段只开放这些，避免失控）**：

| 参数 | 基准 p₀ | 范围 | 分组 | 调制源 | 单轮 Δ 上限 |
|---|---|---|---|---|---|
| `lambda_decay` | 0.05 | [0.02, 0.15] | attention | 情绪强度(+)、血清素(−) | 0.01 |
| `beta_spread` | 1.00 | [0.6, 1.5] | attention | 多巴胺 tonic(+)、皮质醇(−) | 0.06 |
| `max_depth` | 6 | [3, 8] | attention | 坚持(+)、皮质醇(−) | 0（**整轮只允许 ±1，且需高紧迫度**） |
| `theta_threshold` | 0.01 | [0.005, 0.05] | attention | 血清素(+)、多巴胺(−) | 0.002 |
| `theta_action` | 0.50 | [0.30, 0.80] | action | 皮质醇(+)、安全需求(+)、风险容忍(−) | 0.03 |
| `k_attention` | 20 | [8, 30] | attention | 探索倾向(+) | 1（整数） |
| `inter_round_decay` | 0.75 | [0.55, 0.90] | memory | 情绪强度(−)、血清素(+) | 0.02 |
| `autonomy_score_threshold` | 0.35 | [0.25, 0.55] | autonomy | 多巴胺 tonic(−)、坚持(−) | 0.02 |

**机制约束（规范 §10 的七项要求）**：基准值 + 范围（表内）；调制系数（`α` 表）；快照/恢复（`snapshot_params/restore_params`，恢复时写 config 与日志）；漂移日志（`cycle_log` + 可选 `graph_evolution_log.note_weight_changed`）；单元测试（边界、速率、分组上限、恢复幂等）；**分组配额**（同一分组每轮 Σ|Δ| ≤ `group_budget`，attention 组 ≤ 0.10）；**平滑**（对 `λ/β/θ` 用一阶低通，避免振荡：`p ← p + γ(p_target − p)`, γ=0.5）。

**接入点**：进程内写同一个 config dict（A.2-7 已证明热路径现读）。写前走 `_validate(name, value)`（表内范围），写后记 `{cycle_id, param, old, new, sources, explain}`。

### C.8 LLM 门控（`llm_gate.py` + `cognition_modes` 小修）

**四档策略 × 现有 6 模式的映射**（不新建平行枚举，规范 §3.4 的「至少考虑」四档在此落地）：

| 策略 | 语义 | 映射到现有模式 |
|---|---|---|
| OFF | 不调用，只用规则/图/已有模块 | `graph_only` |
| REACTIVE | 仅用户输入/复杂事件/必要行动 | `language`（问答轮保底） |
| SELECTIVE | 由内部状态+不确定性+代价决定 | `interpret` / `reason` 之间按门控函数选 |
| REFLECTIVE | 规划/冲突/长期反思 | `reflect` / `deep` |

```python
def gate_llm(purpose, cycle_id=None, ctx=None) -> dict:
    # purpose ∈ {"answer","extract","narrative","curiosity_question",
    #            "proactive_express","reflection","goal_parse","plan"}
    # 决策链（全部确定性）：
    #  0) 策略 = 由 config.llm_gate.policy[purpose] 给出四档之一
    #  1) OFF → 拒绝
    #  2) 预算（修好的 BudgetManager：per_minute + daily + per_turn + tokens）
    #  3) 价值 − 代价：
    #     value = importance(purpose) × uncertainty × failure_cost
    #     cost  = est_tokens × token_price_factor + latency_penalty
    #  4) 内部状态调制：cortisol↑ → 更倾向调用（威胁需要解释）；
    #     serotonin↑ → 更克制；feedback_sensitivity↑ → 社交细腻度要求更高
    #  5) 输出 {call, mode, reason, est_tokens, policy}
```

**配套修复（Phase 7）**：`BudgetManager` 真正执行 `per_turn` 与 `max_tokens_turn`、按 `MODE_COST` 加权、`estimated_tokens` 参与；provider 回传 `usage`（`llm_provider._raw_invoke` 取 `usage.total_tokens`，缺失时回退估算）并写入 `reward_ledger`/`cycle_log`；把 `answer_question`/`nlp.process`/记忆抽取/好奇心提问这些**当前绕过预算**的调用统一进门控。

### C.9 与自主行动 / Minecraft 的集成

- 候选生成（`autonomy._candidates`）：需求紧迫度作为**新的候选来源与评分输入**，替换硬编码常量：
  `rest/withdraw ← urgency(safety)`；`observe/approach/explore ← urgency(exploration)`；`collect ← urgency(competence)`；`follow/communicate ← urgency(social)`。
- 评分（`autonomy._score`）：保留现有 7 分量结构，新增两项：`+ w_expect·expected(action@target)`、`+ w_need·urgency(need_of(intent))`；权重进 `config.autonomy.weights`（可配、可测）。
- 结果回写：`AutonomousLoop._settle` 已写 episodic 留痕 + `mood_event` + `note_outcome`；新增调用 `InternalState.compute_reward(...)` + `update_needs(...)` + `expectations` 更新，并把 `cycle_id` 写进留痕节点 `extra_attrs`。
- 对话轮：`/api/nlp` 在现有三个锚点接线（见 A.5 与 §D）：
  - ① 扩散完成后（app.py:2177 附近）：`update_needs(perception)` + `update_modulatory_state(reward_signal=None)` 的**慢通道**（衰减/回归）；
  - ② LLM 决策链之前（app.py:2646 之前）：`gate_llm("answer", ...)` 取代裸 `can_call`；
  - ③ 回合末（app.py:2943 之后）：`compute_reward` + `update_traits` + `modulate_params` + `end_cycle()` 落盘。
- Minecraft 感知缺口（Phase 6 前置，需实测）：`/state` 补 `time.timeOfDay`（昼/夜）、脚下方块或光照、背包装填度；缺字段时对应需求驱动项**保持不激活**（不猜、不伪造）。

### C.10 可观测性

- **`cycle_id`**：`begin_cycle` 生成 `c_{seq}`，随 `meta` 传遍一轮；写入：奖励台账、需求/调制变化、演化条目 `details`、LLM 调用记录、自主行动留痕节点 `extra_attrs.cycle_id`。
- **结构化日志字段**（每条 cycle 一条记录，环形 200）：`cycle_id / kind / needs{level,urgency} / modulators{level,Δ} / traits_snapshot / reward{value,components}/ pfe / params_delta / candidates[{type,score}] / selected / action_result / llm_calls[{purpose,mode,reason,tokens}] / state_diff`。
- **新增 API**（只读为主）：
  - `GET /api/internal/state`：需求/调制/性格/当前参数（含 baseline 与偏离）
  - `GET /api/internal/explain?cycle_id=`：一轮的完整因果解释
  - `GET /api/internal/rewards?n=`：奖励台账
  - `GET /api/internal/cycles?n=`：最近轮次
  - `POST /api/internal/params/snapshot` / `restore`：参数快照与恢复（写操作，需显式调用）
  - 前端：**Internal** 标签页（需求条 + 调制变量条 + 性格雷达 + 最近奖励/解释 + 参数偏离表 + 「为什么现在想探索」一句话解释）。

---

## D. 实施计划（Phase 1–8）

> 每阶段都必须：只做该阶段的事、跑全套件、更新文档与记忆。**不变量 I1–I5 全程不得破坏。**

| Phase | 交付物 | 关键接口 | 测试要点 | 不得改动 |
|---|---|---|---|---|
| **1 内部状态与需求** | `internal_state.py` + `data/internal_state.json` + 需求/调制/性格的空壳（只读基线值）+ `begin/end_cycle` + 镜像图节点 | `begin_cycle/end_cycle/state/explain` | 持久化往返、环形上限、原子写、镜像节点 in-place（不新增节点）、单写入口（I1）、cycle_id 唯一（I2） | 不改扩散公式、不改奖赏出口签名 |
| **2 需求系统** | 4 个需求 + 紧迫度公式 + 感知→驱动项映射（先用现有感知字段） | `update_needs` | 驱动项→紧迫度的单调性、竞争（不会同时全满足/全饿死）、`DriveEvaluator` 接管后**既有消费方不变**（`CuriosityDrive.activation` 语义、`action_engine._check_ask` 阈值仍工作） | 不新增 Drive 节点、不改 `Self-[驱动力]->` 语义 |
| **3 奖励系统** | `reward.py`：分量表 + PFE + expectations + 台账；接既有三出口 | `compute_reward` | 分量可解释、RPE 限幅、期望收敛、护栏表逐条（尤其「不绕行动结果」「文本推断折扣」） | 不改 `classify_outcome`、不改 `mood_event` 语义 |
| **4 激素调制** | 4 个变量 + `update_modulatory_state` + `phasic/tonic` 分离 + 消费点接线（探索偏向/风险/恢复/社交价值） | `update_modulatory_state` | 衰减/回归/边界、单调响应、消费点确实改变行为（可测的 A/B）、**唯一入口**（直接改 `level` 会被测试拦住） | 不改情绪节点既有权重与语义 |
| **5 参数调制** | `param_modulator.py` + 注册表 + 分组配额 + 速率/低通 + 快照/恢复 + 漂移日志；`k_attention` 接进 `get_topk` 调用点 | `modulate_params/snapshot_params/restore_params` | 边界/速率/分组上限、恢复幂等、**不振荡**（长跑 200 轮参数轨迹有界）、参数不会同时全动（分组配额） | 不改扩散数学（只改参数值）、PUT /api/config 缺陷单独列项 |
| **6 Minecraft 闭环** | `/state` 补时间/光照（实测后）+ 安全需求驱动项接真实值 + 端到端跑通「感知→需求→意图→行动→奖励→需求变化」 | 复用 `minecraft_embodiment` | 真实回执链路、危险→安全需求→rest/withdraw、探索→新奇→奖励；缺字段时驱动项不激活 | 不加攻击类动作、不碰用户资产 |
| **7 LLM 门控与反思** | `llm_gate.py` + `BudgetManager` 修复（per_turn/tokens/MODE_COST）+ provider `usage` 回传 + 统一门控接线 + REFLECTIVE 用途（目标规划/冲突解释） | `gate_llm` | OFF 档零调用、预算真正生效、token 真实统计、注入失败仍能跑基础功能、**不出现每轮自动调用** | 不改 `answer_question` 的输入输出契约 |
| **8 评估与论文同步** | 实验脚本 + 指标 + 论文更新 | — | 6 项验证（见下） | — |

**Phase 8 的六项验证**（规范 §15.8）：① 非用户指令驱动的行动（已有 autonomy 测试可扩）；② 行动结果改变后续选择（PFE→期望→候选分数的可测差异）；③ 内部状态稳定（长跑 500 轮，需求/激素/参数有界且无锁死）；④ 性格差异可观测（两组 trait 配置跑同一场景，行为分布不同）；⑤ LLM 调用显著下降（同样场景 OFF/SELECTIVE 对比）；⑥ 任意行为可由日志解释（`explain(cycle_id)` 覆盖每次行动）。

---

## E. 风险清单

### E.1 理论风险

1. **稳态器坍缩/漂移**：需求长期无法满足 → 紧迫度饱和 → 行为僵化（比如一直 rest）。缓解：饱和上限 + 满足通道必须可达 + 每阶段测试「需求竞争有轮换」。
2. **奖励黑客（reward hacking）**：`classify_outcome` 只有 38 个关键词 → 系统可能学会追求「被夸」而非「做对」。缓解：外部奖励只是分量之一且权重受上限；内部奖励（进展/新奇/危险下降）权重大；文本推断奖励打折。
3. **PFE 信号稀疏且噪声大**：成功/失败样本稀少 → 期望表过拟合。缓解：`α` 小 + 期望值限幅 + 计数门槛（`count < 3` 时只记不学）。
4. **激素reification**：把「皮质醇高」当成事实解释。缓解：文档与前端文案统一为「皮质醇样（功能抽象）」；解释里给出原始事件而不仅是变量名。
5. **性格学习不可逆**：慢学习仍有方向性偏差累积。缓解：基线不可变 + 每日上限 + 历史可回滚 + 证据门槛。
6. **参数振荡/极限环**：多个调制源叠加 → 参数抖动 → 扩散行为突变。缓解：分组配额 + 单轮 Δ 上限 + 一阶低通 + 长跑有界性测试。
7. **需求冲突死锁**：安全需求高 + 社交需求高 → 行动互相否决。缓解：紧迫度是**连续竞争**而非否决；`autonomy` 的候选顺延机制（Phase D 已实现）保证不会整轮停摆；测试必须覆盖「双高」场景。

### E.2 工程风险

1. **并发写**：request 线程与 CC 线程都会写内部状态。缓解：`InternalState` 单一 RLock + 每轮一次批量写；**锁序**沿用 `state._lock → kg._lock`（与 `disposition_store` 一致）。
2. **与 `_save()`/`kg.save()` 的写盘竞态**：新状态文件独立（`json_store` 原子写），不与图落盘耦合；顺序：先图后状态（或反之但必须固定并测试崩溃一致性）。
3. **`PUT /api/config` 的三处缺陷**（新键被忽略 / 嵌套块整块替换 / `bool("false")==True`）会让「人工改参数」与「调制器写参数」互相打架。缓解：调制器只写注册表内的键；新增只读观测 API；PUT 的修复单列小任务（不在本次顺手改）。
4. **死配置误导**：`k_top/drive_system/action_system` 现在是死键，接线时要么用起来要么删掉，避免「改了没反应」。
5. **`attention_context` 地雷**：`nlp_processor.py:689-706` 若被真的传入会 `UnboundLocalError`（引用未定义的 `cog_str`）。接线前必须先修。
6. **图膨胀**：镜像节点若按变量×轮次建节点会爆炸。缓解：**in-place 单节点**（每个变量/需求/性格各一个）+ 序列只在 JSON 状态文件里。
7. **`EpisodicBuffer` 无落盘**：奖励历史若依赖它会丢。缓解：奖励台账与需求历史存在新状态文件，不依赖 buffer。
8. **`inter_decay_floor` 不在 config**：想调也调不到。缓解：注册表启动时把缺失键补进 config（仅内存），或改用调制器自带的默认值并记录偏离。

### E.3 可能让系统失控的点（需要显式护栏监控）

- 参数联动无上限 → **分组配额 + 低通 + 长跑有界测试**（每阶段回归都跑）。
- 需求紧迫度全高 → **竞争而非否决 + 候选顺延 + 顶层「同一时间只执行一个行动」**（Phase D 已有）。
- 激素 tonic 单向漂移 → **baseline 回归 + 上下限 + 每日变化总量上限**。
- traits 快变 → **证据缓冲 + 单次/每日 Δ 上限 + 可回滚**。
- LLM 门控被内部状态长期推高（皮质醇高就一直调 LLM）→ **每日 token 硬顶 + 门控自身也受预算约束 + 记录拒绝原因**。

---

## F. 需要你拍板的几个问题

1. **激素水平是否要在前端可手工调节**（滑块），还是只读观测？
2. **8 个性格维度**是否要支持用户设定出厂值（写入 `DEFAULT_TRAITS`）？默认按「中性偏好奇/友好」给。
3. **需求数量**：第一阶段就做 4 个（安全/探索/掌控/社交），还是先做 2 个（安全/探索）跑通再加？
   —— 我的建议：**4 个一起做**，因为 `DriveEvaluator` 的 3 个 stub 本来就在等评估，一次接管比两轮改更省。
4. **`attention_context`/`MODE_CONTEXT_BUDGET`**：接线（把注意力上下文真正送进 prompt）还是删除？
   —— 我的建议：**Phase 7 接线**，同时修掉那个 UnboundLocalError 地雷。
5. **参数调制的开放范围**：先只开 `theta_action` + `beta_spread` + `k_attention` 三个（最稳），还是按 C.7 表格全开 8 个？
   —— 我的建议：**先 3 个**，跑 500 轮稳定后再逐步放开。
6. **是否允许 Phase 6 修改 `minecraft_bot/bot.js`** 补时间/光照/背包字段（用于安全需求驱动项）？

确认以上之后，我按 Phase 1 开始实现（先只做内部状态层 + 需求骨架，附测试）。
