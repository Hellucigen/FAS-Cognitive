# CALL_GRAPH.md — 四条调用链追踪(基于生产代码,非文档)

## 链 C:Persistent Intention → 恢复

```text
[练习相] 脚本动作(战役层) → v2.Executor.execute → world.add_item
  → fc.ingest(obs, action, result)            run_experiment/harness.py
      ├─ entities_of(obs) → 物品:oak_log 等实体节点
      ├─ _edge(实体共现) / _edge(动作→实体) / _edge(动作→结果)
      └─ activate_from_inputs(实体)            diffusion_engine
  → fc.step_dynamics() → diffuse_step + decay

[测试相 P3] goal="You have free choice of actions."
  → fc.ingest(新观测) → activate_from_inputs([物品:oak_log,...])
  → diffuse_step:物品:oak_log -相关-> 物品:oak_planks   ← 生产先验闭包边
      (build 时由生产建图路径播种,战役 C 未排除)
  → fc.focus(k=8) = eng.get_topk(8)          run_exp_routing_v2.py:388
  → fc.demand_block(goal) → cognitive_demand.analyze_cognitive_demand
      (只传 text/kg/engine/exploration;
       生产 app.py:4421 额外传 intentions/action_result/current_action
       ——战役装配未传)
  → v2.serialize_context(节点 id+activation+demand/gap/routing)
  → 门控 gate_from_context(facts+state+ctx) → craft_oak_planks 可见
  → LLM 选择 gather→craft → 恢复
```

**归因判定**:payload 复现(探针 P-C)显示零经历对照产生同构 payload
(物品:oak_planks 进 Top-8,门控开)。C 的驱动 =
**先验闭包边 + 观测触发扩散 + 工作集显著度**,与"persistent intention
/CI"无关(无 CI 组件在链上)。

## 链 E:失败证据 → (缺失的)修订

```text
[决策头层] ex.execute("gather_iron_ore") → False,"tool_missing:stone_pickaxe"
  → fc.ingest(obs, action, result)
      ├─ _node("动作:gather_iron_ore") / _node("结果:tool_missing:..."[:24])
      ├─ _edge(动作→结果, rel=相关, w=+0.5)     ← 正权!无极性
      └─ activate_from_inputs(实体)             ← 只点亮实体,不点亮结果节点
  → 探针 P-E:结果:tool_missing... act=0.000;图中负边=0
  → focus(k=8):物品:iron_ore 5.0、动作:gather_iron_ore 3.03 进上下文
  → demand_block("obtain raw_iron")            ← goal 每步恒定注入
  → 模型重复失败(均值 11.1 次)

[生产对照] 同一失败的三条既有通道(战役装配全部缺失):
  1. 事件框架:action_system._write_event_frame(action_system.py:1033,
     结算原生写回;负极性涉及边 -0.8 + 结果边 +0.9;config
     experience_eventframe.enabled=False 默认关)
  2. 账本:CausalLearner.action_priors(±0.05 先验加成;
     沙盒装配有,战役 BeaconFASContext 无)
  3. demand 的 action_result/current_action 通道(app.py:4427 传,
     战役 demand_block 不传)

[沙盒自主环对照] sandbox_lab.EventFrameInjector(:524)+ am.on_settled
  链(:820)→ 已验证行为翻转(@6 vs @9,论文实验 D)
```

**归因判定**:E = HB(战役装配缺全部三条失败消费通道)为主,
AR 轻度(生产默认 OFF + goal 恒压)。

## 链 F:Self-goal 写入 → (缺失的)行为读取

```text
[写] app.py:4092(多步指令)或 am.set_goal_context(显式 API)
  → action_system.set_goal_context(:1171)
      → self_graph.set_current_goal(:395)
          → set_goal(创建 "目标: {desc}" 节点, graph_space=self)
          → kg.add_edge(Self -[当前目标]-> 目标: ...)
          → engine.mark_active([goal_id])        ← 进激活场(:1190)
  → 遥测(v6):ON 5/5/5(开始/练习中/结束),OFF 0/0/0

[读] 全仓 grep(排除测试/写侧/定义):
  - current_goal():0 调用者
  - get_self_model_summary:唯一读者 app.py:7257 = HTTP API /api/self/model(前端)
  - cognitive_context 生产渲染:无 自我/当前目标 内容
  - 自主层:loop.add_goal 自有 registry,从不读 self-graph
  - 激活场:目标节点可被点亮,但 working-set 无语义消费者映射它

结论:WRITE 完整(含激活场);READ = 仅诊断/UI。行为惰性=架构性。
```

## 链 I:经历 → 图 → 激活 → 序列化 → LLM

```text
[写] fc.ingest(同链 C)
  → 动作:/结果: 节点(Node.created 时间戳存在)
  → 动作→结果 边(Edge.created 存在)+ 同 obs 行实体共现边(时间抹平)
  → 无 episode 身份、无顺序结构、无因果评分边

[读] focus(k) = get_topk(纯激活序)
  → serialize_context:节点只保留 {id, activation};
    边列表 = 选中集内部边 ≤8(关键边 动作:craft_stick→结果:crafted:stick
    因结果节点激活 ~0.2 不进 Top-8 而缺席);demand/gap/routing 全零
  → created 时间戳/关系类别/episode:全部不进 payload

[对照] history 条件:hist.window() 原始逐行 "obs|action|result"
  → 时序叙事完整 → 模型重演情节 → 10/10
```

**归因判定**:L1/L2/L3/L4 通过;L5 丢弃面=时间戳、关系语义、顺序、
episode;L6 实际 payload 见 PROBE_OUTPUT.txt。表示研究(rep 0/30)
已排除"预算/补结构块"两个浅修复 → IF 为主,AR(schema 无情节
一等对象)为次。

## 链 B:归因窗

```text
am.on_settled → causal.record_action(evt)      experience.py:488
  → window_for(evt) = ACTION_WINDOW_S[subj] or KIND or ASSOC_WINDOW_S
      (gather_resource/craft_item = 90.0;默认 8.0;:73-90 显式数据表,
       注释记录 8s→90s 的设计理由:否则 §8 核心闭环被系统性筛空)
  → deadline = ts + win;候选扫描限 [now, deadline]
  → _subject_relevant(§七主体门) + _score_candidate(MIN_SCORE=0.60,
    dt ≤ ⅔窗) → 聚合/晋升
  → 窗口推进:_on_timeline_event(观察者,事件 ts 驱动关窗)
  → 超窗:候选永不进入(_match 直接过滤)→ 160s 延迟 0/10

[补偿机制排查] action_priors=成功率记忆(非因果);Hebbian=共激活
(非归因);timeline=存储(无评分)。无长程补偿器。
```

**归因判定**:90s 是有意语义边界(注释在案、参数族配套)→ AR,
不改参数。
