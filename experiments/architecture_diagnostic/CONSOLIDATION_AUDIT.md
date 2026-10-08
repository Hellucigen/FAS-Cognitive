# CONSOLIDATION_AUDIT.md — FAS 经验巩固/记忆强化机制代码级审计(2026-10-03)

方法:生产代码调用链追踪 + 三个只读确定性探针
(`probe_consolidation.py` → `CONSOLIDATION_PROBE_OUTPUT.txt`)。
零生产修改。

---

## A. 当前真实机制(调用链)

### 链 1:Hebbian 连续强化(真正的记忆强化机制,连续)

```text
每个认知/沙盒 tick:decay_step(clear_fired=True) → diffuse_step
  → DiffusionEngine 发射循环(正向 :1053、反向 :1087)
      每条实际传输激活的边:
        edge.activation += 0.02/0.01(边激活,运行时)
        self._hebbian(edge, node)            diffusion_engine.py:268
            → 白名单:relation_category ∈ {semantic_relation,
              emotional_relation}(config.hebbian.categories;
              cognitive_relation 类永不被改写)
            → Δw = ε·a_u = 0.0015 · min(1, act/5.0)
            → clamp:|w| ≤ w0·(1+max_gain) = w0·1.5
              (w0 = 运行时首见基线;跨会话以当前持久权重为新基线)
  → 权重写回 Edge.weight(持久化随图保存)
```

触发条件 = **边被实际用于传播激活**,无次数阈值、无状态机。
J/沙盒路径:`SandboxDiffuser.__call__ → decay_step + diffuse_step`
(sandbox_lab.py:515-519),`ENG_CFG` 不含 hebbian 键 → 引擎取默认
`enabled=True`(config.py:1819-1825)→ **J 路径每 tick 都在调用**。

### 链 2:CausalLearner 统计聚合 → 假设 → 结构晋升(阈值化,结构层)

```text
am.on_settled → causal.record_action(evt)        experience.py:488
  → 窗口内主体相关结果 → _finalize(:578)
      aggregation[a_sig].obs += 1
      outcome[a_sig|o_sig].support += 1(含 §P2b 世界确认覆盖、
      反例记账 EXPECTED_RATE=0.60)
  → _hypothesize(a_sig)(:704,每次关窗后调用)
      假设:support ≥ MIN_SUPPORT=3 且 conf=s/(s+c+2) ≥ 0.60
      晋升:support ≥ KG_SUPPORT=5 且 conf ≥ KG_CONFIDENCE=0.80
        → promote_to_kg:写 操作:{sig} / 变化:{sig} 节点 + 边
        (c=0 时 conf=0.80 需要 s=8——Laplace 平滑使名义 5 实为 8)
```

### 链 3:软遗忘(对称面)

`_forget_factor`(:299):last_access 驱动的可达性衰减(-6%/天,2 天
宽限,floor 0.25,**不改权重、只改唤醒难度**)——与 Hebbian 强化
正交:一个管"权重多强",一个管"多容易被唤醒"。

## B. 是否存在固定阈值

**存在,但仅用于 hypothesis/ promotion(结构层),不用于 reinforcement。**

- reinforcement(Hebbian):`w += ε·a_u`,无任何次数/状态条件。
- hypothesis:support≥3 ∧ conf≥0.60(第 3 次重复即成假设)。
- KG promotion:support≥5 ∧ conf≥0.80(c=0 时实际 s≥8)。

## C. 连续强化是否真实存在

**是——代码存在、默认启用、J 路径确实调用,但 J 的度量没有观测它。**

确定性证据(探针 P1,修正回合边界后):同一条 semantic_relation 边
连续 10 个 tick 被用于传播:

```text
w: 0.500000 → 0.500713 → 0.502440 → 0.504686 → 0.507178
   → 0.509786 → 0.512450 → 0.515141 → 0.517844 → 0.520553 → 0.523264
单调递增:是 | 阶跃(>0.05):无 | 上限 0.75(未及)
```

增量 Δw=ε·a_u 随源激活增长而渐增(a_u 随激活驻留上升)——
连续、渐进、受上限约束。注意:第一版探针直接连调 diffuse_step
得到"平线",那是 **fire-once 伪影**(不调 decay_step 就没有新回合,
`_fired_round` 阻止重复发射)——生产节拍每 tick 都有 decay_step
清回合,平线不代表生产行为。

## D. J 实验为什么 promotion=0(代码级核算,探针 P3 重放)

J 协议重放(1 种子,100 拍)的时间轴统计:

```text
distinct action signatures = 7
top: gather_resource(oak_log) ×2 | explore_direction(west) ×2
     amble(None) ×2 | craft_item(oak_planks) ×1 | explore ×3 各 ×1
gather_resource(oak_log) 的 2 次观测还分裂到 5 个 outcome 签名
(每个 support 1-2)
→ 任何 (action,outcome) 对的最大 support = 2
```

- 假设需要 support≥3 → **最大值 2 < 3,假设层就从未越过**,晋升更不可能。
- 根因分解:
  1. **PROTOCOL(主)**:J 注入的 episode 目标要么即时达成
     (gather oak_log 一次即达),要么不可达(diamond/cobblestone
     → 转为 explore/amble)→ 自主层 100 拍内几乎不重复同一
     动作-结果对。这是情节设计问题:巩固探针需要的是**重复**,
     J 给的是**多样性**。
  2. **PROTOCOL(次)**:outcome 签名碎片化——同一 gather 的证据
     分裂到 5 个结果字符串(inventory:oak_planks:count_increased /
     inventory:oak_log:... / succeeded / block:disappeared×2),
     即使重复也难以聚到同一对上。
  3. **度量缺口(PROTOCOL)**:J 的 checkpoint 只统计
     aggregations/hypotheses/promoted,**没有统计 Hebbian 权重漂移**
     ——真正连续发生的强化(链 1)完全在度量之外。raw_J 的
     "hypotheses=3→7 实为 aggregations"的展示亦因此误导。
- **不是 CODE、不是 HARNESS、也不是阈值设计错误**:P2 证明同一对
  重复 10 次 时假设@3、晋升@8 精确按代码语义发生。

## E. 设计一致性判断(最重要)

**FAS 的实现符合"每次有效激活逐渐强化、趋近正常记忆上限"的设计原则。**

- 连续强化 = Hebbian 链:每次有效传播 Δw=ε·a_u,受 w0×1.5 封顶,
  跨会话以持久权重为新基线(限幅稳定)。探针 P1 的轨迹
  (单调、渐缓、无阶跃)就是设计原则的直接实例。
- 阈值化的 promotion 是**更高层的结构抽象事件**(把重复验证过的
  action→outcome 泛化为 操作:/变化: 节点+边),不是"记忆开始
  强化"的开关。两者分层正确。
- 一个真实的设计参数观察(非违背):KG_CONFIDENCE=0.80 与
  Laplace 平滑叠加,使零反例下的实际晋升要求 s=8(名义 s≥5)——
  这是阈值族的隐式耦合,值得在文档中显式记录,但属于参数选择。

## F. 不适用(实现符合设计;无最小修复提案)

- 唯一建议的**非代码**修正:J/未来巩固实验的度量必须包含
  Hebbian 权重轨迹(探针 P1 的 instrumentation 可直接复用),
  并把 promotion 显式表述为结构层事件。
- 若未来认为 s=8 过严,那是结构晋升策略的参数讨论(需反事实/
  干预证据支撑),不属于本审计的修复范围。

## G. 符合设计 → 只读探针已证明 activation→reinforcement→saturation

- P1(修正后):10 次重复使用,权重单调 0.5000→0.5233,增量
  渐缓、上限 0.75 未及——**连续强化+上限约束**成立。
- P2:阈值链逐次轨迹(support 1→9;假设@3;晋升@8)——**离散
  结构层**按设计发生。
- P3:J 协议核算(上文 D)。

## H. 文件级证据

| 文件 | 类/函数 | 作用 | 关键证据 |
|---|---|---|---|
| diffusion_engine.py | `_hebbian`(:268) | 连续边强化 Δw=ε·a_u,cap w0×1.5 | 白名单/封顶/基线逻辑 |
| diffusion_engine.py | 发射循环(:1053、:1087) | 每次边传输调用 `_hebbian` | 正向+反向都调用 |
| diffusion_engine.py | `decay_step(clear_fired=True)`(:1595) | 每 tick 开新 fire-once 回合 | 生产节拍语义 |
| diffusion_engine.py | `activate_from_inputs`(:684) | 输入激活(**不清回合**) | 探针 v1 平线的根因 |
| diffusion_engine.py | `_forget_factor`(:299) | 软遗忘 -6%/天,floor 0.25,不改权重 | 与强化正交 |
| config.py | `hebbian`(:1819)/`soft_forgetting`(:1930) | ε=0.0015、max_gain=0.5、白名单;遗忘参数 | 默认 enabled=True |
| experience.py | `KG_SUPPORT=5`/`KG_CONFIDENCE=0.80`/`MIN_SUPPORT=3`/`HYP_CONFIDENCE=0.60`(:134-137) | 结构层阈值 | c=0 时晋升实际 s≥8 |
| experience.py | `_finalize`(:578) | 聚合/反例记账 | support 计数语义 |
| experience.py | `_hypothesize`(:704) | 假设评估+晋升唯一调用点 | "够稳才进图"链的末端 |
| experience.py | `promote_to_kg`(:754) | 结构晋升:操作:/变化: 节点+边 | 幂等(_promoted) |
| scripts/sandbox_lab.py | `SandboxDiffuser`(:500) | J/沙盒的生产扩散驱动 | decay_step+diffuse_step 每 tick |
| scripts/sandbox_lab.py | `ENG_CFG`(:83) | 沙盒引擎配置(无 hebbian 键→默认 True) | J 路径 Hebbian 在场 |
| experiments/capability_gap_v2/run_j_stability.py | `snapshot` | J 的度量集合(无权重轨迹) | 度量缺口所在 |
| experiments/architecture_diagnostic/probe_consolidation.py | P1/P2/P3 | 本审计的只读探针 | CONSOLIDATION_PROBE_OUTPUT.txt |

## I. 最终判定

**`Consolidation works, but promotion is a separate mechanism`**

附注(同判定内的两个从属事实):
1. J 协议不足以观测巩固(度量未含 Hebbian 权重轨迹;情节设计
   不产生重复动作-结果对)——即 `J protocol is insufficient to
   test consolidation` 作为 J 实验本身的判定成立。
2. 修正探针(本文件 P1/P2)已补上该证据:连续强化真实发生,
   结构晋升按阈值族设计发生,两层分离且各自符合设计。
