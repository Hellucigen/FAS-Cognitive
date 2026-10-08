# MC 具身执行体系图谱化（2026-09-21）— 五层分权终态

> Minecraft 只是具身环境 + 一组可调用能力 + 一个感知源；
> 行为知识全部回到图谱。代码只保留执行接口与最小安全不变量。

## 一、旧体系 → 新归属（每条规则的迁移去向）

| 旧硬编码（位置） | 新身份 |
|---|---|
| `SAFE_GATHER_BLOCKS` 唯一终审闸门（gathering） | **降级**：物种-属于->可采资源 图关系（认知/授权判断用）+ 技能层防裸调用物理兜底；保护判断上移 kernel（图上 `受保护` 边 + 授权节点） |
| `dangerous=True → 永不进候选`（capability_graph） | **拆除**：危险概念可成认知候选（gates：贴脸+状态好），spec 带 `requires_kernel_check`；kernel 执行前核验目标危险因果 |
| `SURVIVAL_TYPES` 类别抢占 + 队列资格（action_system） | **拆除**：抢占=优先级差 + 概念节点 `preempts` 属性（图上数据）；队列按 source（user/goal）统一 |
| `_check_emergency` HP≤8→强制 seek_safety、8 项贴脸名单→强制 retreat | **拆除**。唯一保留：`death_floor`（HP≤4 无条件停当前动作，不指定去向——控制原语）；HP=6 采矿继续，生存去向由认知竞争选（mapper 已注入生存压力，SEEK_SAFETY/RECOVER/EAT/RETREAT 都在候选席） |
| `_SAFETY_FAMILY` 生存硬置顶（autonomy） | **拆除**（§13：认知优先级经 drive/priority 数据进排序） |
| 安静期 `user_quiet_s=20` 封门（autonomy） | **拆除**：notify=取消在飞自主动作（调度礼仪）+ `context.user_recent` 连续抬 action 阈值；强驱动可即时行动 |
| `max_attempts=3 永久放弃 + 45s 退避 + 180s 二值 recency` | **拆除封禁**：失败压制=评分 failure(软衰减)+causal(action_prior blockers/success_rate)+因果晋升边；kernel 只留 `anti_loop` 5s 防抖（纯技术）；recency 连续衰减 |
| `_RESOURCE_VALUE` / valuable 双表 | **退役**：资源价值经物种节点激活（mapper 0.4/事件 +0.8）+ salience（事件 urgency 数据） |
| 三份敌对名单 | **归一**：`config["hostile_entities"]`(19) 唯一；combat 技能内 retreat_health 等属执行层物理约束（配置键） |
| `if hunger<=10: eat()` 等候选生成 if 链 | **全删**：EmbodiedStateMapper(观测→图激活) + capability affordances/gates(诱发边+数值门+可达性) + binder(仅参数绑定) |
| 表达 300s 冷却/每小时 6 条硬抑制 | **调制化**：`context.recent_expression`（小时密度+刚说完衰减）抬 express_threshold；保留 60s 防抽风 floor |
| `normalize` 缺 action_type 兜底 inspect_area | **拆**：缺 action_type 直接拒绝（不伪造行动） |
| communicate 无文本失败 | **保留**（capability 无法实现 = payload 检查，正名为执行层约束） |
| reflex 否定/点名/误触发防线 | **保留**（语言诚实性，非行为裁判） |
| 承诺/互斥 skill_busy/超时/取消必停/不假成功/bridge 失联停 | **保留 = Safety Kernel K1**（执行诚实性） |

## 二、五层与文件

```
L1 World/Knowledge   mc_knowledge.py（物种/类别/属于/受保护/需要工具/产生/造成/威胁/
                     敌对/服务于/不兼容——config.mc_world 种子 + 因果晋升/教学生长）
L2 Activation        embodied_mapper.py（percept+events → 物种/可采资源/生存需求/
                     地面物品/建筑结构/MC夜·MC白天(source=minecraft) 激活，
                     强度=f(距离,血量,物种威胁边)连续函数）
                     + capability_graph（affordances/gates/reachability/缺口）
L3 Cognition         drive/tension/network（前几轮）+ survival_need 诱发边
                     + action_space 概念宇宙
L4 Capability        CapabilityIndex：概念→需要→能力(executors 属性)→caps 交集；
                     无实现=能力缺口信号（note_gap→capability_gap 张力）
L5 Execution+Kernel  ActionManager/ActionIntent/skills/bridge/bot.js 不变；
                     safety_kernel.py = K1 执行诚实 + K2 授权核验(读图) +
                     K3 攻击目标核验(读图) + K4 濒死停手 + K5 防抖
```

**授权链路**（§8）：用户"拆掉这个箱子"→ resolver MINE+source=user → kernel K2 放行 +
落 `授权:modify:chest` 图节点（TTL 600s）→ 技能带 `_kernel_granted` 执行；窗口内自主
关联动作也被放行；过期自动恢复保护。保护永远是**关系+可解除**，不是名单禁止。

**未知行动**（§23）：图谱长出 `行动:唱歌`（proposed，上轮提议通道）→ discover 解析
无 executors → `能力缺口` 节点激活 + capability_gap 张力 → LearningDrive——
"想做但做不到"成为学习输入，而非被 enum 抹掉。

## 三、闭环位置

```
observation → EmbodiedStateMapper → Knowledge Graph(activation)
→ CapabilityIndex.discover（诱发边+gates+可达性）→ binder 参数绑定
→ _score_action（drive/attention/novelty/intention[support=同一批 CI 节点]/risk/
  recency/failure/causal/salience）→ 阈值(action.score_threshold 调制) →
→ ActionManager.propose → SafetyKernel.guard（K2/K3/K5）→ skills/bridge/bot
→ outcome → timeline/causal/disposition/压力 → 图谱 → 未来认知
（tick 变化门：世界与内部状态无变化 → no_change 保持观察）
```

## 四、验收对照（§30）

A 逃跑=图因果链（zombie 物种激活→生存需求→RETREAT 候选胜出，含 causal 历史）；
B 反制=gates 允许 + kernel 验敌对边；C 箱子=属于容器→受保护边→无授权 kernel 拒
（reason 语义化，概念与意图仍存活）；D 用户明说→授权节点→放行；E 挖矿 3 次失败→
timeline→action_prior↓→评分压制（永久放弃不存在；5s 防抖除外）；F 新概念无实现→
能力缺口信号上图；G 见上图分层。
测试：test_mc_knowledge(20)+test_mc_embodiment_graph(15)+capability_graph/autonomy/
integration/acceptance 全面同步，54/54+verify 绿；真实 app：世界语义 92 物种/127 边
入图、空闲轨迹出现 kind=action 意图、零异常。
**未覆盖（如实）**：真机连服验证（dig 授权、creeper 反制、濒死停手实弹）；
MC 昼夜对 mc_world 危害知识的经验生长（因果晋升边目前为 0，需真运行积累）。
