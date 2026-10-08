# 统一内部认知循环（2026-09-20）— 持续认知 + 自主性重构

> 时间只负责告诉 FAS"现在是什么状态"；循环只负责让状态自己流动；
> "什么都不做"是合法输出。

## 一、旧架构（审计结论）

```
旧 reignite：random.sample(episodic)         ← 封闭资格 + 无竞争
旧 _pulse  ：topk + 固定四权重公式            ← "值不值得跟用户说话"打分器
旧 CI      ：只有 communication_intention    ← 一切念头都被迫是交流
旧 阈值    ：0.55/0.78/0.30 三个静态数        ← 与内部状态无关
旧 反思    ：15分钟 × 攒够3条表达 → 调 LLM    ← 定时器不是认知
旧 autonomy：if weak:…14 条 if 链 + 9 权重固定评分 ← 与 CI 系统互不相识
             2.5s tick × 固定取模（3/4/12拍闹钟）
```

违反点：reactivation 限定 episodic、脉冲以社交为中心、意图单类型、
反思无触发语义、行动评分与认知意图割裂（两套大脑）。

## 二、新架构

```
世界/用户/内部状态 → graph activation → decay+diffusion
   → reactivation（场冷度门；全图 eligible 竞争评分 → mark_active → 正常扩散）
   → cognitive pulse（场变化门；focus = concentration+coherence+continuity+novelty+drive）
   → intention kinds（affinity 数据表：communication/exploration/action/
     reflection/attention_shift——开放表，非封闭 enum）
   → intention competition（同类合并强化/基种漂移/kind 并发上限/衰减丢弃）
   → ┌ communication → 表达阈值（调制）→ LLM 语言实现 → outcome
     └ action/exploration → autonomy 评分 intention 分量 → 行动 → settle
                            → _consume_intention（意图被行动消耗）
   → 反馈/学习：reward、disposition 激活边、causal、反思压力节点
   → 改变未来认知
```

**一个关键合流**：CC 的 pulse 与 autonomy 的候选发现读**同一批图谱状态**
（CI 节点、activation、Drive 场、张力）——不是两套评分各算各的。

## 三、参数化与调制（不是 magic number 堆）

- 所有阈值经 ModulationLayer：`cognition.form_threshold`（curiosity/DMN
  降、CEN/皮质醇/疲劳升）、`cognition.express_threshold`（催产素/社交需求
  降、刚说过话/压力升）、`action.score_threshold`（CEN/任务/多巴胺降）、
  `reactivation.cold_field / boost_scale`（DMN 升 CEN 降）。
- `config["cognitive_loop"]` 是参数真源（reactivation 权重表、intention
  kinds 信号表、pressure 来源表、focus 权重）；代码内置同构默认（离线可跑）。
- 节律从"闹钟"改"门"：reactivation=场冷度+cooldown；pulse=场变化量+时间
  兜底；autonomy=世界/意图签名变化+min_interval+120s 兜底；reflection=
  压力≥阈值+cooldown。压力节点本身靠 cognitive 衰减自然消退（不手动清零）。

## 四、压力驱动的离线自问

图上 `反思压力` 节点（cognitive，type=cognitive_pressure）是**聚合信号**：
action_failure(.25)/negative_feedback(.30)/prediction_error(.15)/
intention_lost(.20) 由各既有回写点注入（ActionManager 结算、
note_outcome、prediction_baseline、CI 丢弃）；sources 环可解释
"压力从哪来"。≥threshold(1.2) 才触发反思，触发即消耗（×0.3）。
**实测**：空闲 4 分钟，旧 CI 衰减丢弃 → intention_lost 攒压 → 日志
"反思压力过阈值（…来源 ['intention_lost']），触发离线反思"。

## 五、意图消耗（两套大脑合并的闭环件）

`AutonomousLoop._consume_intention`：候选被 propose 成功 → basis 交集的
活跃意图 activation×0.4 + consumed_count++。没有它，一个意图会永远支持
候选驱动死循环；有了它，"意图→行动→消耗→（新焦点）→新意图"成为链：
实测 inspect→（意图消耗）→navigate 自然接续。

## 六、修改文件

| 文件 | 改动 |
|---|---|
| `continuous_cognition.py` | `_reignite_memory`→`_reactivate_gate/_reactivate_field`（全图 eligible：importance+recency_gap+field_coupling+drive_tie+novelty+noise，权重在 config）；`_pulse` 重写（focus 五分量、world/drive/network/tension 信号、kinds affinity、attention_shift 兜底不参与 argmax）；`_merge_or_form` 重写（form_th 读调制、basis 合并与 kind 漂移、kind 并发上限、非 comm type=intention）；`_decay/_prune/_active_intentions` 双类型；express 阈值读调制 + recent_expression 语境喂入；`note_pressure/_maybe_offline_reflect` pressure 驱动；`_reactivate_gate` 温度门；state() 观测（reflection_pressure/last_reactivation/双阈值 base 对照）；`_DEFAULT_LOOP` 内置默认 |
| `autonomy.py` | `_decision_signature` 变化门（no_change 不空转）；`_intention_support` 评分分量（权重 0.12 进 DEFAULT_WEIGHTS，explain 加 int=）；`_threshold()` 读 action.score_threshold；`_consume_intention`；`_on_action_settled` 重置签名（结算=状态改变） |
| `action_system.py` | `_apply_reward` 失败 → `cc.note_pressure("action_failure")`（cc 引用 app 注入） |
| `app.py` | `action_manager.cc = cc`；prediction_error → note_pressure（按 surprise 缩放）|
| `config.py` | `cognitive_loop` 段；`modulation.params` 增 5 个认知阈值/再点火参数（cognitive_field DEFAULT_MODULATION 同步） |
| 测试 | `test_cognitive_loop.py`（18 场景 24 断言）；`test_continuous_cognition.py` 同步新意图模型（_active_intentions、reactivation 概念资格、pressure 驱动反思、last_access 语义） |

**未动**：drive/tension/network 四层（上一轮）、dialogue_decision、
diffusion_engine（reactivation 就用 mark_active/register_activation_source）、
ActionManager 安全边界全保留（承诺/中断/退避/锁/紧急检查/红线）。

## 七、验收对照（§十六）

- **A 自主性**：自己想到（S2/S11 空闲形成 exploration 意图）、自己形成意图
  （实测 kind=communication affinity=0.62 仅在用户相关时）、自己决定不说
  （空闲 6 分钟 0 表达；express 阈值调制）、自己决定说、自己决定行动
  （S8/S12）、自己决定不行动（S10/S12a/`no_change`/`below_threshold`）。
- **B 图谱中心性**：唯一 substrate 是图谱；reactivation/pulse/意图/评分
  全部只读图状态；LLM 只在表达/思考/反思出口。
- **C 开放性**：intention_kinds 加一行=新意图类型；reactivation 无类型
  白名单（排除项只有引擎零件与感知自维护槽位）；任何节点（新概念/新能力/
  新未知/MC 对象）入图且冷却后自然获得被"想起"的资格——S4/S17 断言。
- **D 重新点亮**：concept/episodic/emotion/drive/unknown/self 统一走
  eligible 竞争（S2/S4/S17；旧事件甲乙+概念同场竞争实测）。
- **E 自然性**：想到 A→扩散到 B→组合 C→形成意图→最后什么都没做 =
  S1/S16/S7 全程可达；reactivation 注入源 `reactivation` 免扣发射，
  点亮后走正常 diffusion→竞争，不直达意图（S2 断言）。
- **F 完整闭环**：内部状态→意图(CI 节点)→行动(intention 分量+propose)→
  世界变化(stub/executed)→outcome(settle)→学习(消耗意图+压力+disposition+
  causal)→下次倾向（recency/failure/int 分量变化）——S8/S9/S12c 串成链。

**已知边界（如实）**：① autonomy `_if_chain_candidates` 的 (b) 类规则
（资源价值/事件 salience/目标透传）仍在代码——那是优先级策略与承诺管理，
世界状态检测类 (a) 规则已由上一轮 capability 图谱接管；② 意图 kind 的
"持续积累→升格为长期目标"尚未做（reinforcement 计数已在，消费端未接）；
③ 300 拍稳定测试为虚拟时钟加速，跨小时级的真实漂移（如 CI prune 节奏）
靠 S18 上界断言覆盖。
