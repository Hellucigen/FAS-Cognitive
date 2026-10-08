# CHANGE_LOG — Fascinator 实验单元（§31 变更账本）

遵守：每条含 change_id / timestamp / file / reason / before / after / **bug_or_design_issue** / **design_preserving** / affected_experiments。本台账只记实验单元期间的代码修改；此前工程项见 docs/ 既有审计文档。

---

## C07 — RunRecorder 事件丢失（flush 采样缺陷）

- **timestamp**: 2026-09-28 10:3x
- **file**: experiment_recorder.py
- **reason**: M1 观察 run 中 486 条事件仅落盘 400（进程 finalize flush 缺失 + 写入仅在 %100 触发）
- **before**: `_flush_events` 在循环计数 %100 时调用；finalize 不 flush；`self._lock` 部分实例缺失
- **after**: finalize 强制 `_flush_events()`；增量 flush（`_flushed_n` 游标）；`self._lock` → 模块级 `_LOCK`
- **bug_or_design_issue**: **bug**（数据丢失，M1 事件缺失 86 条即此所致）
- **design_preserving**: 是（事件语义/目录结构不变）
- **affected_experiments**: 全部（沙箱/基线/实机所有 run 的记录完整性）

## C08 — Recorder 静默异常吞噬

- **timestamp**: 2026-09-28 10:4x
- **file**: experiment_recorder.py
- **reason**: C07 中 `self._lock` AttributeError 被 `except Exception: pass` 静默→文件仍不写；冒烟测试暴露
- **after**: 模块级锁 + 冒烟（写/读往返）通过
- **bug_or_design_issue**: **bug**
- **design_preserving**: 是
- **affected_experiments**: 全部

## C09 — run_mc_observe 增强（graph_delta 形状 + causal 采样）

- **timestamp**: 2026-09-28 10:5x
- **file**: scripts/run_mc_observe.py
- **reason**: graph_delta 对 list-of-dict 图形状兼容；M3 观测面（实时因果账本规模）
- **after**: `_graph_ids` shape-agnostic；每 60s `_sample_causal` 读 data/experience_timeline.json 记 CAUSAL 事件
- **bug_or_design_issue**: 前者 bug（unhashable dict 崩溃静默），后者增强
- **design_preserving**: 是（只读观测器）
- **affected_experiments**: M1（补救快照路径）/M2/M3

## C10 — sandbox 方块表补 deepslate_iron_ore

- **timestamp**: 2026-09-28 11:0x
- **file**: scripts/sandbox_lab.py（BLOCK_META）
- **reason**: iron_ingot 目标链在 M1 经验中将 deepslate_iron_ore 引入 raw_iron 来源；沙箱世界缺该方块 → 铁链找矿依赖真实 MC 26.1 机制补齐
- **after**: `"deepslate_iron_ore": {"drops": ["raw_iron"], "harvest_tools": ["stone_pickaxe"]}`
- **bug_or_design_issue**: 环境模型不完整（沙箱世界与 26.1 世界机制不对齐）
- **design_preserving**: 是（仅环境事实表）
- **affected_experiments**: iron_ingot 任务（§13c 负结果路径上的一环，**不改变负结果**）

## C11 — setup_world 预置矿区供给（环境事实）

- **timestamp**: 2026-09-28 11:1x
- **file**: scripts/run_ablation_matrix.py（setup_world）
- **reason**: iron 链阻塞在"熔炉配方 needs_table 缺 crafting_table"（背包 planks 恒为 3）；矿区补给站（村庄/旧营地常见工作台）作为环境事实预置
- **after**: `put_block(0,62,0,"crafting_table")`
- **bug_or_design_issue**: 两者皆非——环境供给设计补丁（矿点旁有工作台是真实世界常态）
- **design_preserving**: 是
- **affected_experiments**: iron_ingot 矩阵（仍负结果；供后续长链任务使用）

## C12 — 沙箱探索量程匹配（壁钟/仿真时钟失配伪影绕行）★最重要

- **timestamp**: 2026-09-28 11:4x
- **file**: scripts/sandbox_lab.py（build_stack cfgd）
- **reason**: 探索技能 deadline = `time.time()+explore_max_time_s(300)`（**壁钟**），沙箱 run 全程几十真实秒 → 技能永续巡航（原点环上转圈，visits 罚分饱和使 distance_limit 不可达），抢占行动空间：熔炉烧着铁时 bot 漂去探索，`furnace_take` 排队不上（iron_ingot 链最终根因）；**同样机制使修复前消融矩阵出现 −Prior 假差分**
- **before**: 默认 explore_max_time_s=300 / consider ground `explore_step=16` / `explore_max_distance=64`
- **after**: `cfgd["explore_max_time_s"]=32.0`；`explore_step=6.0`；`explore_max_distance=24.0`（匹配 8×8 沙箱世界尺度）
- **bug_or_design_issue**: **bug**（环境侧仿真时钟扭曲，与"离线验收三坑"同族；真实 MC 下技能 300s 壁钟 deadline 正常结算，**无需修复**）
- **design_preserving**: 是（认知/技能/图谱零改动；只调沙箱环境参数）
- **affected_experiments**: 全部沙箱 run 的**行为动力学**；矩阵结论在修复前/后对比才成立（报告 §13d）。修复前一切"消融造成差异"的解读按伪影废弃

## C13 — llm_heavy 先验守卫绕行恢复

- **timestamp**: 2026-09-28 11:2x
- **file**: scripts/run_baselines.py
- **reason**: 沙箱零 LLM 守卫（`MiMoBackend._create` raise）阻断 llm_heavy 基线；临时替换 `MiMoBackend._create = OpenAICompatBackend._create`（仅 llm_heavy 段，run 后恢复）
- **bug_or_design_issue**: 无（按设计切换基线条件）；记录绕行以便审计
- **design_preserving**: 是（守卫语义对沙箱场景保持）
- **affected_experiments**: 基线 llm_heavy

## C14 — S5 收尾（rearm 时序 + flush_causal）

- **timestamp**: 2026-09-28 11:3x
- **file**: scripts/run_sandbox_scenarios.py + scripts/sandbox_lab.py（新增 flush_causal）
- **reason**: S5 因果窗在沙箱内永不关窗（壁钟窗口 >> run 时长）→ 账本悬浮；且 rearm 干扰石子轮次
- **after**: env 时序改 25/50/75；`flush_causal(st)` 用远期推进一次性关窗（sweep(now=t+2^31)）；S5 结果 12 hyp/9 agg
- **bug_or_design_issue**: bug（沙箱时钟与 causal 窗失配）
- **design_preserving**: 是
- **affected_experiments**: S5（修复前 runs 保留在目录）

## C15 — 组合消融补齐（−Diffusion−Episodic / −Diffusion−Gap）

- **timestamp**: 2026-09-28 12:1x
- **file**: scripts/run_ablation_matrix.py（ABLATIONS）
- **reason**: §7 规格"至少组合作为"要求三组组合；初版矩阵只有 −Episodic−Writeback
- **after**: 新增 `-Diffusion-Episodic`（diffusion_on=False, causal_on=False）、`-Diffusion-Gap`（diffusion_on=False, gap_on=False）；重跑两任务 ×3 seeds → 全部达成（oak @6 / cobblestone @15）
- **bug_or_design_issue**: 覆盖缺口（规格项遗漏提报）
- **design_preserving**: 是
- **affected_experiments**: 消融矩阵（oak/cobblestone 终版各 11 组）

## C16 — 先验知识条件 A/B/C（prior_level 参数）

- **timestamp**: 2026-09-28 12:1x
- **file**: scripts/sandbox_lab.py（build_stack prior_level）+ 新增 scripts/run_prior_conditions.py
- **reason**: §10 要求先验条件 A（zero）/B（limited）/C（more complete)对照；初版只在 C 下运行
- **after**: `prior_level` 参数：B=图谱先验通道关+机制表保留；A=B+配方/方块表清空；C=现状。A/B 下 prior_extra 不注入。
- **结果（双任务 ×3 seeds）**：A 0/6 达成；B 6/6（@6/@15）；C 6/6（@6/@15）→ **机制表是短链达成必要计划知识；图谱先验边可或缺；"no-prior learning"被 A 的 0/6 明确否定**
- **bug_or_design_issue**: 无（规格补齐）
- **design_preserving**: 是
- **affected_experiments**: 新增 prior_conditions 族（18 run）

---

## 尚未解决（记档待办）

- iron_ingot 链的沙箱驱动缺口：`FurnaceTake` 依赖 `_find_blocks(furnace,16)`；沙箱探索量程修复后仍未见该技能被选（290–450 ticks 行为），长链+熔炼任务需沙箱驱动面专项（不是 §11 修复——已按纪律停手）
- 实机 M4（world editing）/ M7（短实机 LLM）未执行，原因与降级见报告 §9/§11
## C17 — 实验 A runner：run_exp_reuse.py（记录层，零核心改动）

- **timestamp**: 2026-09-28 15:5x
- **file**: scripts/run_exp_reuse.py（新增）
- **reason**: 规范 §二三：Reuse 因果实验需要受控的"写回→复用"双段 run 与激活/事件采样，在先审计确认后实现（PRE_EXPERIMENT_AUDIT.md §6 M1/M2/M3/M5）
- **before**: 无（新脚本）
- **after**: 条件 B（Ep1 发现=3 串行 acquire 目标促 promote → Ep2 复用=inherit_graph）；条件 A（仅 Ep2 干净图）；世界=自制非 MC 对象（mystic_ore→mystic_chunk 掉落入 block_meta，配方表**无该链**=闭包盲区，知识只经写回/缺口通道可及）；每 tick 采样目标/矿石/写回节点激活 + ACTION 事件；flush_causal 收尾；recorder 全量落 FAS_Research_Experiments/reuse/
- **bug_or_design_issue**: 无
- **design_preserving**: 是（core 零改动；环境数据+记录层）
- **affected_experiments**: 实验 A（pilot 起）

## C17b — run_exp_reuse.py 感知接线补口（sandbox 缺口的装配断点）

- **timestamp**: 2026-09-28 16:1x
- **file**: scripts/run_exp_reuse.py（新增 bridge_perception_gaps）
- **reason**: pilot A 失败诊断：t0 感知含 mystic_ore 但 gaps=0 → explore_area@目标物永续巡航。根因=consider_recognition 感知端调用点只在 minecraft/perception.py（真机通道），fake-bridge 走 EmbodiedStateMapper 旁路，建档→用途→缺口 段无人在沙箱调用（审计时应判为环境装配断点，非核心 bug）
- **before**: gap 通道消费者（autonomy._gap_candidates）完好、生产者（perception）完好，中间捏合只在真机
- **after**: run 脚本每 tick 对已建档 UnknownBlock_* 节点：补建档 物品:x（inventory_item 节点，同 perception.py:200-215 语义）→ consider_recognition → 用途未知自动 open_gap → 缺口目标→inspect_block/试做实验候选照常规管线
- **bug_or_design_issue**: **bug**（sandbox 实验环境装配断点；真机不受影响）
- **design_preserving**: 是（零核心改动；复制 perception.py 已有职责到脚本层）
- **affected_experiments**: 实验 A pilot 及后续全部沙箱实验（缺此现在任何沙箱 run 的未知对象探索都哑火）

## C17d — build_stack 增加 mc_gatherable 环境事实参数 + run_exp_reuse v2

- **timestamp**: 2026-09-28 16:3x
- **file**: scripts/sandbox_lab.py（build_stack 签名+cfgd 注入段）、scripts/run_exp_reuse.py（v2 重写）
- **reason**: pilot 诊断链：自制矿石世界不可行—①gap 感知接线断（C17b 修）；②inspect_block 只观察不挖掘；③"可采资源"分类来自 config.mc_world.gatherable 数据表（种进图的核心词表），自制矿不在表 → 自主 gather 通道不存在。架构事实：可采分类=数据表（世界事实），配方表同级。故：环境事实经数据表注入（sandbox 装配参数），非答案硬编码；自创矿作为"世界事实"与 oak 位置同权
- **before**: build_stack 无 mc_world 数据表扩展口；run_exp_reuse v1 世界=非可采自制矿（不可能达成）
- **after**: `build_stack(..., mc_gatherable=None)` 追加进 cfgd["mc_world"]["gatherable"]（仅本实验配置对象内生效）；run_exp_reuse v2：mystic_ore/dull_rock 双双进可采表（环境事实），配方/掉落表同 v1（挖 ore→chunk 唯一目标链，dull→pebble 无用途干扰），Ep1=4 块 ore 三串联行 acquire（3 次观测→promote），Ep2=ore×1+dull×1 近邻，条件 A/B 差分=首动对象/冗余/达成 tick/激活残值
- **bug_or_design_issue**: 无（环境适配+pilot 驱动的实验配置定型）
- **design_preserving**: 是（核心零改动；config 数据表扩展=环境事实，与 C11 矿区工作台同族）
- **affected_experiments**: 实验 A/B（自制对象环境）
## C17e — run_exp_reuse v3：真实 MC 词汇 oak 链双谱系（弃自制对象世界）

- **timestamp**: 2026-09-28 17:1x
- **file**: scripts/run_exp_reuse.py（v3 重写；环境层 plant_tree 纯种树）
- **reason**: v2 自制对象世界的 audit 负发现实证（报告 A/Negative-1）：目标无链时 explore_area@目标物 永续（user_goal 0.345 恒压过 curiosity 0.075）——"发现→采集"闭环在核心里不存在，Ep2 双方都无法达成（reuse_A/B_seed1 pilot 均 success=false）。结构性设计限制，改 **实验条件** 而非核心：v3 用真实 MC 词汇 oak 链（配方表有链 = 矩阵 11 组已证可达），双谱系干扰（birch_log 可采无链无用途，gap 干扰），差分观测 = 首动作品种/达成 tick/废动作/激活残值。
- **before**: v2 世界 = mystic_ore/dull_rock（配方表无链、闭包盲区）
- **after**: Ep1 = oak 树林 8 串行轮次（清背包使每轮真达成 → support=8 → confidence=s/(s+c+2)=0.8 过 **KG_SUPPORT=5/KG_CONFIDENCE=0.80** 晋升线——审计文引用 3/0.60 是聚合→假设门槛，易误读，v3 按真实门槛适配轮数）；Ep2 = oak@(6,0) 远 + birch@(-2,0) 近（birch 树干修复：sandbox put_tree 树干硬编码 oak_log 会污染干扰谱系 → 环境层 plant_tree 全品种纯种树）。另修复 collect_stats 事件 schema（ACTION 事件 action 在 **subject**、target 在 content.target、结果走 SELF_STATE_CHANGE——v2 pilot 的 types/targets 全空即此）；ep2 记录 inherited_wb_nodes + start_activation（知识访问层指标）
- **pilot 结果（seed1）**: Ep1 promote 写回 4 条（craft_item(oak_planks)→succeeded、gather_resource(oak_log)→block 消失/inventory 增/succeeded）；Ep2B 继承 6 个 操作/变化 节点 + oak_log 激活残值 5.0（vs Ep2A 0 节点/0.0）→ **达成层行为零差分 spaces（A/B 均 success@9、首动 birch、3 动作、red=0）——与 audit 设计限制 §5.1 一致：表格闭包驱动达成，写回知识不改变达成轨迹；知识访问层差分（继承节点+激活残值）如实入报告**
- **bug_or_design_issue**: 设计（负发现 Negative-1 定版）+ 两处脚本 bug（collect_stats schema、背包轮次未清使 rounds2+ 假达成）
- **design_preserving**: 是（核心零改动；环境数据 + 记录层）
- **affected_experiments**: 实验 A（v3 全 seeds）
## C18 — 沙箱扩散执行器补装 + 实验 B：扩散 → 相关知识访问

- **timestamp**: 2026-09-28 18:1x
- **file**: scripts/sandbox_lab.py（build_stack cc_diffuse 参数 + SandboxDiffuser + tick 接线）、新增 scripts/run_exp_diffusion.py
- **reason（关键发现）**: 实验 B pilot 首跑 Full vs -Diffusion 激活曲线 **35/35 逐点相同**。深挖 diffuse_step 调用点（diffusion_engine.py:940）：生产只有两个驱动方—— app.py LLM 回合管线 + continuous_cognition.CCLoop.tick_once（decay+diffuse，_running 门控）；**沙箱 build_stack 从未装配 CC 循环，autonomy 也不调扩散** → 沙箱里扩散/衰减双重缺失，激活被冻结（β=1 与 β=0 无差别是装配事实，不是闭包遮蔽）。**矩阵 -Diffusion 消融（C12-C16）各 arm 均无扩散执行器 → "−Diffusion 零差分"不能支撑消融声明**（它只证明表格闭包达成不依赖激活——因为激活本身不存在也达成），论文引用此点时必须带此修正。
- **before**: 沙箱无扩散执行器（Stack 无 diffuser；tick 只 loop.tick）
- **after**: `build_stack(..., cc_diffuse=True)` 在 Stack 挂 SandboxDiffuser（continuous_cognition.tick_once 的 diffusion 段同语义：每认知 tick decay_step + diffuse_step，_running 门控，沙箱无对话回合故不涉 busy 让位）；`cc_diffuse=False` 回归 pre-C18 行为。实验 B runner：条件 full(β=1)/nodiff(β=0)，Ep2 单段 oak@(6,0)+birch@(-2,0) + open_gap(birch)，每 2 tick ACTSNAP 7 探针 + 端态缺口 2 跳点亮 BFS（reach_le2_lit）
- **结果（seeds 1-5）**: 知识访问层**真差分**——birch_log（缺口"指向"对象）：full 1.5→5.0 封顶 vs nodiff 1.1~1.7 徘徊；**oak_log（视野外纯扩散信号）：full=0.43 vs nodiff=0.0**；缺口节点能量 full 泄放转移（发射即转移签名）vs nodiff 钉 1.43；reach_le2_lit full=5/nodiff=4。行为层零差分（success@9、3 动作、red=0，逐 seed 相同）——达成层表格闭包遮蔽，负结果如实入库（audit §5.1/§5.2 预判兑现）
- **bug_or_design_issue**: **bug**（沙箱实验环境装配断点——真机 CC 循环在场不受影响；与 C17b 同类）；附带矩阵消融解释修正（见 reason）
- **design_preserving**: 是（零核心改动；装配层补生产驱动器 + 记录层采样）
- **affected_experiments**: 实验 B 全部 run；矩阵消融声明确认修正；A 报告加装配注记
## C19 — 实验 C：跨任务知识迁移（Transfer vs No-Transfer，reuse + recombination）

- **timestamp**: 2026-09-28 19:0x
- **file**: scripts/sandbox_lab.py（build_stack 增加 inherit_causal/inherit_tl 装配套件）、新增 scripts/run_exp_transfer.py、FAS_Research_Experiments/transfer/
- **reason**: 审计 §8.3 构造：任务1 链 A→B→C（obtain crafting_table：gather oak_log → craft oak_planks → craft crafting_table，8 轮晋升线全绿）；任务2 = B→C→D 重组+延伸（obtain wooden_pickaxe：craft oak_planks → craft stick → craft crafting_table → craft wooden_pickaxe，needs_table 路径走 place 表）。两条件同 world 同配方表（环境知识相等）；Transfer = 继承 Ep1 全图 + **同进程因果账本**（C19 装配套件：build_stack 可选 inherit_causal/inherit_tl 传入上一 stack 的 CausalLearner/ExperienceTimeline 实例——事件追加同一时间轴、聚合连续，autonomy.action_prior:2790 直接读到任务1 成功率记忆）；No-Transfer = 干净图 + 全新账本
- **before**: build_stack 无跨 stack 账本通道（inherit_graph 只传图，账本随新 base dir 清空）
- **after**: 装配参数 inherit_causal/inherit_tl（docstring 注明 C19 用途）；run_exp_transfer.py：Ep1 8 轮实践 + Ep2 双条件同构 runner + action_prior 通道探针（t0 快照 5 个共享/新动作的 success_rate/obs）+ 重组事件 idx + shared_structure_usage 计数
- **结果（seeds 1–5，沙箱全确定，逐 seed 相同轨迹）**:
  - 达成层零差分（预判 a 兑现）：双方 5/5 达成 wooden_pickaxe
  - **行为层正差分（三实验首次分离）**：No-Transfer @16 / 9 动作 / 首动 birch_log（好奇心缺口钩走第一拍）→ recomb(craft crafting_table)@5；Transfer @13 / 8 动作 / **首动 oak_log**（继承写回 8 节点 + 目标链直写同在场；gather_resource(oak_log) prior=(1.0, obs=8)）→ recomb@4。差分来源 = 账本通道的 prior_bonus（±0.05 钳位，autonomy.py:2790 消费；B6/§12 经验回流）叠加继承图激活残值，把 oak 系首动推过 birch 好奇门槛
  - **知识层证据**：Transfer t0 action_prior：gather_resource(oak_log)/craft_item(oak_planks)/craft_item(crafting_table) 全 (1.0, obs=8)（任务1 成功率记忆跨任务在场）；No-Transfer 全 (None, 0)。继承 操作:/变化: 写回节点 8 个、promoted=7；oak_planks 激活残值 4.32 vs 3.53、物品:crafting_table 2.93 vs 0.27；birch_log 2.28 vs 5.0（干扰谱系亮度差，机制步序保留为开放项）
  - negative-side：达成层零差分双条件并存 → "表格可达性仍遮蔽 transfer 对达成率的影响；行为层与知识层差分不受遮蔽"为论文口径
- **bug_or_design_issue**: 两处脚本 bug：①Transfer 共享 tl 时 collect_stats 未按 episode 切分（32=24+8 跨段合并事故），修 start_idx 段界；②汇总 json 误带 Stack 实例不可序列化，修 out 层。均统计/记录层，不涉核心
- **design_preserving**: 是（核心零改动；装配套件 + 记录层 + 环境数据）
- **affected_experiments**: 实验 C 全部 run；PAPER_CLAIM_AUDIT 待收口
## C20 — 实验 D：行动侧事件框架失败抑制探针（愿景通道首次行为学审判）

- **timestamp**: 2026-09-28 21:0x
- **file**: scripts/sandbox_lab.py（build_stack eventframe 参数 + EventFrameInjector 类 + tick 接线 `if st.efi is not None: st.efi()`）、新增 scripts/run_exp_eventframe.py、FAS_Research_Experiments/eventframe/、报告 EXPERIMENT_D_EVENTFRAME.md
- **reason（关键发现）**: 论文愿景 §552-562 断言"语义节点沿边传播至情景记忆事件节点引发回忆并调制认知"，但行动侧经验是 timeline 流水+统计聚合（无事件框架节点/无极槽位边）——愿景的行动侧通道从未被架起，也从未被行为学审判。实验 D 直接把愿景最小样板装进沙箱（装配层）：事件框架节点 `行动经验:gather_resource(birch_log)`（label=declarative-episodic / graph_space=episodic，与对话侧同规范）+ 槽位边 `-[涉及]→ birch_log(±0.9)` + 结果边 `-[结果]→ 变化:...:failed(±0.9)`，注入失败经验（extra_attrs.source="experiment-injected"，非真实失败路径），每认知 tick 低量再点火 1.2（模拟 §485 记忆再点火的沙箱替身）
- **before**: 行动侧无事件框架装配；build_stack 无 eventframe 参数
- **after**: 装配套件不变形（零核心改动）；run_exp_eventframe.py：control vs inhibit 同世界同目标（oak@(6,0) 远目标链 + birch@(-2,0) 好奇心缺口钩，obtain oak_planks），5 seed + ACTSNAP 激活采样 + --debug-reason（monkey-patch _score_action 打印候选 reason/激活）
- **结果（seeds 1-5，全确定）**:
  - **机制层正证据**：负极性槽位边经扩散引擎统一抑制机制（负权贡献 = src.act×|w|×beta×gain/total_w，不消耗发射预算；diffusion_engine.py:1021 抑制发行仅当节点 total_w>0）把 birch_log 激活 5.0 封顶(control) 压到 2.74(inhibit) 稳态——预测方向正确、5/5 一致，愿景"负激活调制下一次扩散"机制级首次实证
  - **行为层零差分**：首动 birch、@9、3 动作两条件逐 seed 相同。根因=新结构锁，非闭包遮蔽：birch 候选 reason 含缺口锚点节点 `缺口:用途(birch_log)`（floor 0.9 + 每 tick mark_active 直写，autonomy.py:1516-1517），_score_action attention=max(reason 激活)/5 永远落在缺口 1.5 上；参数扫描 fail_side -1.5/-2.0×reign 3.0/4.0 下 birch_log 实体被压到 0.00 首动仍 birch——行为不翻是缺口直写锚点的结构阻断（"她正在想着这个对象"对扩散抑制免疫），不是强度问题
  - 论文口径：§552-562 可引"事件节点沿负权槽位边调制实体激活（机制级）"，不可扩到"失败记忆改变行为选择"
- **bug_or_design_issue**: 两处装配 bug（内部修正后跑，均已在报告 §6 记录）：①Node label="episodic" 非法（graph_model.py:212 严格枚举）被注入器 try/except:pass 吞掉 → 事件节点从未建出来（假阴性麻痹），修 label="declarative-episodic"；②负权边首版连"事件→失败节点"（失败节点无对象出边，且 total_w>0 前提使 pure-负边节点整个分发循环被跳过）→ 修 layout：涉及边 ±0.9×2 撑 total_w，结果边负权指向失败节点。教训：探针先验接线（graph_start 快照含注入节点/边）再读行为结论
- **design_preserving**: 是（核心零改动；装配层注入 + 记录层采样；蓝图①缺口锚点负调制 / 蓝图②再点火前置竞择为下一步待用户裁决接口，未实施）
- **affected_experiments**: 实验 D（新增）；三实验结论不回滚（A/B/C 闭包遮蔽与 D 的缺口锚点锁是不同结构层）

## C20e — 实验 D 修复：蓝图①缺口锚点负调制 + 蓝图②再点火时序前置 + C20c novelty 退休（首动翻转 5/5）

- **timestamp**: 2026-09-28 22:0x
- **file**: scripts/sandbox_lab.py（EventFrameInjector：targets 默认含 `缺口:用途({obj})`、_ensure 补建 retired UnknownBlock 节点）、scripts/run_exp_eventframe.py（run_cond 首拍前 warmup 3 拍 diffuser+efi）
- **reason（关键发现）**: C20 定版时行为零差分的两层结构根因——①缺口直写锚点（gap floor 0.9 + mark_active，attention=max(reason)/5 恒落缺口，扩散抑制不可达）②novelty 顶格（autonomy.py:2708-2711 前缀回退：图内无此节点时 UnknownBlock_* 无条件 novelty=1.0；沙箱 bridge_perception_gaps 只消费已建档节点、从不建档，sandbox 中 gather 候选永远 novelty=1.0）。用户裁决"修复"→ 三层装配级修复：蓝图②把再点火前置到首拍决策前（warmup 3 拍，让抑制在注意力竞择发生时已积累）；蓝图①把缺口锚点纳入抑制目标（负权涉及边直指 `缺口:用途({obj})`）；C20c 补建 retired UnknownBlock 节点（失败经验=充分接触，novelty 熄灭）
- **before**: C20 定版状态——inhibit 首动恒 birch（birch_log 实体压到 0.00 也翻不了），行为零差分
- **after**: 全 5 seed 首动翻转 gather_resource@birch_log → gather_resource@oak_log；success_tick 9→6（省 1 次好奇 gather + 1 拍）：
  - control（对照，无注入）：success@9 n=3 first=birch，birch_log 终态 5.0 封顶，缺口锚点 act=1.5（pre-fix 基线逐位复现）
  - inhibit：success@6 n=2 first=oak，birch_log 终态 **0.0**（完全压灭），缺口锚点 act=**0.0**（蓝图①直达），事件节点再点火稳态 2.2844
  - dbg-reason：inhibit 首拍 birch 候选 reason act_raw=[0.6, 1.2, None, 0.6, **0.0**]——UnknownBlock 退休（0.6 而非顶格）、实体从 5.0 降到 1.2（warmup 前置的抑制在首拍已到）、缺口锚点 0.0
- **bug_or_design_issue**: 修复过程自身踩坑一枚（sandbox_lab.py 曾被坏 Edit 截断 EventFrameInjector.__init__ + _ensure 缺块，replay 修复类失误删掉结果边/`_wired` 标志，经语法校验+冒烟 wiring 校验补回；教训同 C20：改完先 ast.parse + 迷你 stack 验证节点/边落地再跑实验）。类当前形态：__init__（eid/failed_nid/targets/_wired）+ _ensure（事件节点 episodic + retired UnknownBlock + 槽位涉及边 fail_side + 结果边 +0.9 撑 total_w）+ __call__（再点火 1.2/拍 + mark_active）
- **design_preserving**: 是（warmup 只是把已有两个驱动器多调 3 次；缺口锚点负调制是新增一条注入边；retired 节点走已有 "retired" extra_attrs 语义——autonomy/graph_model/diffusion_engine 零改动）。**行为差分从零到 5/5 全翻转，且与注入存在性严格对应**；论文口径升级：§552-562 现可引"事件框架失败经验经负极性槽位边抑制实体与注意力锚点，**改变行动候选选择**（行为级实证，D 修复后）"
- **affected_experiments**: 实验 D（C20 定版结论被本条目修订：行为层由零差分改为真差分）；加入口径修正到 FINAL_EXPERIMENT_REPORT §21.4/§22 claim 7；A/B/C 不回滚

## C20g — 实验 D 真实失败路径探针：失败回执内源落图（trigger="on_failure"）

- **timestamp**: 2026-09-28 深夜
- **file**: scripts/sandbox_lab.py（SandboxWorld.despawn_on_dig 环境属性 + EventFrameInjector trigger="prearm|on_failure" + arm() + build_stack on_settled 链包）、scripts/run_exp_eventframe.py（四臂条件：+control_real/inhibit_real）
- **reason（关键发现）**: C20/C20e 的失败经验是 **build 时预注入**（source="experiment-injected"）——"先知道她会失败"的成分仍在。愿景"失败记忆改变行动"的完整链应自行动回执出发：失败发生 → 事件框架落图 → 行为改变。本条目把落图时机从"装配时"移到"真实失败回执时"（on_settled 链），并从环境侧制造真实失败（despawn_on_dig：首挖时树已不在 → dig_pos 固有 block_not_found 回执）
- **before**: 失败经验预注入；事件框架与经历时序无关（build 即有，虚拟失败）
- **after**: 事件框架在失败回执后才落图（dbg 探针：t0–t5 无节点 → 失败后下一拍 eid_in_graph=True，5/5）。结果（四臂×seeds，全确定）：control@9 n=3 red=0 birch（基线复现）；inhibit@6 n=2 red=0 oak（C20e 复现）；**control_real@17 n=4 red=1**（birch→birch重试→oak→craft——纯失败计数下失败后重试一次）；**inhibit_real@15 n=3 red=0**（birch→oak→craft——失败回执落图后重试冲动被压掉一拍，差分真实发生在"同一失败事件"内）
- **新机制事实（timeout 暂态账）**: 真实失败形态为 timeout 结算（despawn 后技能链超时），而 timeout ∈ _TRANSIENT_WORLD_REASONS（autonomy.py:106）→ 统计通道（_attempts/冷却）对失败完全静默（fail_tick 探针恒 None 即指纹）——**行为差分（17→15）发生在统计层零记账的失败上，图侧事件框架是唯一留痕与施压通道**，这是"图经验 vs 统计经验"分立的直接实证
- **bug_or_design_issue**: fail_tick 探针初版查 st.am._attempts（无此属性，记在 loop），改为 st.loop._attempts 后仍恒 None——查明 timeout 是暂态失败不进账，属如实记录而非装配 bug
- **design_preserving**: 是（世界侧 despawn 是环境数据属性；回执链是 on_settled 包链先保原语义；EventFrameInjector 加 trigger 分支不影响 prearm 路径）。**论文口径再升级（限定）**：可引"真实失败回执内源成型事件框架，且在其统计通道静默（timeout 暂态）时依然独力改变后续行动选择（行为级实证，D/C20g）"；开放项：生产路径失败离散热流未测、失败强度-重试窗口剂量反应未扫
- **affected_experiments**: 实验 D（§9 追加）；A/B/C 不回滚

## C21 — 行动侧事件框架生产化（投论文后继续开发的基建支线）

- **timestamp**: 2026-09-28 白昼
- **file**: config.py（relation_ontology 注册 `结果` + DEFAULT_CONFIG 新小节 `experience_eventframe`）、graph_model.py（add_edge 可选参数 force_weight，默认 False）、action_system.py（_write_event_frame 新方法 + _write_action_memory 锁内挂接 + 引擎尾部点火两件套 + __init__ setdefault 注入）、tests/test_action_eventframe.py（新，14 项电池）、scripts/verify_action_eventframe.py（只读回放审计）
- **reason（生产化动因）**: C20e/C20g 的事件框架落图只在沙箱**装配层**（EventFrameInjector，experiment-injected 预注入或 on_settled 链包）；生产侧失败经验只有统计流水（timeline/聚合/冷却）与实例级行动痕迹节点 `行动_{ms}`（无极性边），愿景 §552-562"事件帧调制认知"在生产无对应通道。C20g 实测 timeout 暂态失败时统计/冷却/因果通道全静默、图侧框架独力承担行为差分（17→15）——图侧是生产统计通道"盲区失败"的唯一留痕与施压通道，本条目把落图能力从装配层迁为生产经验路径内建。用户裁决：v1=落图闭环先行（C20c retire_unknown / C20e suppress_gap_anchor 为配置项默认关），验收=针对性冒烟 + verify 脚本扫图
- **before**: 失败/成功结算 → 仅 `行动_{ms}` 实例节点（涉及正权 0.4/0.5，无极性）；失败极性只活在 extra_attr reason 与统计账里；事件框架节点/极性槽位边只在实验装配层存在（生产图谱 `行动经验:` 0 条）
- **after**: 结算路径内建 `_write_event_frame`（同一 kg._lock 帧、独立 try/except 不破坏实例留痕）：签名级事件节点 `行动经验:{atype}({sig})`（episodic、签名归并重复经历、extra_attrs 挂 success/fail 计数与最近一次）-[涉及 成败极性]-> 已存在实体（绝不新建实体节点）+[结果 +0.9]-> `行动结果:{sig}:{succeeded|failed}`（semantic 叶子，恒正撑 total_w>0 使抑制被发行）；极性翻转走 add_edge(force_weight=True)（双向成立：成功 +0.5 洗白 −0.8、失败 −0.8 重入 +0.5——修复了 max 合并对负权不对称的锁死）；结算点火两件套照 _reactivate_field（手动 activation 赋值 cap 3.0 + touch + mark_active + register_activation_source("memory_recall")，免扣）；cancelled 不落；失败**不按暂态集合豁免**（C20g 证据）；默认 enabled=False 精确回滚。电池 14/14（默认关零写回滚保障、形状/极性/total_w>0、扩散抑制自证 0.344<0.717、双向翻转、重复归并、cancelled/降级签名、运行时翻转 enabled 零写、engine=None 守卫）；回归 test_experience_timeline（写图点守卫）/test_autonomy_integration/test_action_system 全绿
- **新机制事实（生产实况差异，如实入档）**: 实况失败结算同帧还有实例节点 activate_from_inputs(2.5) 正推对象（既有执行留痕行为）——单次失败时抑制（0.8 点火）不足以净负，**多次失败（事件节点激活随点火叠加至 cap 3.0）才翻负**；这与实验 D 的持续再点火+缺口锚点+retired novelty 三件合力不同，v1 抑制是边际调制而非压倒性力量（C20e 其余两件配置项待后续逐个开）。若实况观察到不想要的陈年教训重燃（冷场竞择点亮旧失败施压），关 ignition 或 enabled 即止
- **数据事实修正（诚实度）**: 实验 D 沙箱的 `-[结果]->` 边当时未在 relation_ontology 注册 ⇒ 落盘实为兜底 `关联`（行为不受影响：total_w 只看权重符号）。C21 注册 `"结果": "cognitive_relation"`（"网络"/"掉落"同款词表同步先例）并在此修正 EXPERIMENT_D_EVENTFRAME.md §8 的表述
- **bug_or_design_issue**: 初版自测失败两处如实在测试内修复——① `_write_event_frame` 建节点后未重新取回节点引用（add_node 不返回节点）→ node 恒 None 抛 AttributeError 被 try/except 吞为"跳过"（警示：吞咽异常会掩盖静默故障，测试断言把节点形状钉死才暴露）；② 测试共享 EVF_ON 常量被"运行时关 enabled"用例原地变异污染后组（改用 dict 拷贝）
- **design_preserving**: 是。graph_model.add_edge 加可选 force_weight 默认 False 零行为变化（只动数据层边权重，diffusion_engine 抑制/发射条件与传播公式一行未动）；落图只在 _write_action_memory 既有 kg._lock 帧内 + 引擎既有锁序；`结果` 词表注册纯表增补；配置门 enabled=false 精确回滚（存量节点/边保留但无活性来源，episodic λ=0.15 约 24 拍熄火）
- **affected_experiments**: 实验 D（§8 数据事实修正：`结果` 边落盘为兜底 `关联`）；A/B/C 不回滚。生产行为默认不变（enabled=False），需人工开 enable 后观测

---

# Paper-I 收尾实验 campaign（2026-09-29）变更账本

> 范围：FINAL_CAMPAIGN_PREAUDIT.md 判定的 sandbox-only 收尾实验。以下全部为**实验基础设施新增/实验内缺陷修复**，核心机制（graph_model/diffusion_engine/autonomy/continuous_cognition/skills）零改动。

## C22 — 实验 A 升级 runner（run_exp_a2_reuse.py，B0/B1/B2 × 升级环境）

- **timestamp**: 2026-09-29 上午
- **file**: scripts/run_exp_a2_reuse.py（新）
- **reason**: campaign §7——A1 新任务 oak_fence（4 步重组链）+ A2 复用任务 wooden_pickaxe（结构同构表面不同）+ B1 条件（learned graph + write-back disabled，build_stack writeback_on=False 既有开关）
- **before**: 实验 A 只有 oak_planks 双谱系（B=继承/A=干净）
- **after**: B0 fresh / B1 写回禁用谱系继承 / B2 写回启用谱系继承；三条件账本均新实例（隔离图谱写回通道）
- **bug_or_design_issue**: 无
- **design_preserving**: 是（纯新增 runner）
- **affected_experiments**: 实验 A2（reuse_a2 族，15 run）

## C23 — 实验 B 升级 runner（run_exp_b2_diffusion.py，C2 随机/C3 检索 oracle 对照）

- **timestamp**: 2026-09-29 上午
- **file**: scripts/run_exp_b2_diffusion.py（新）
- **reason**: campaign §8——既有 B 只有 full/nodiff；补 C2（同量级无结构随机激活）与 C3（目标闭包链检索 oracle）对照
- **after**: RandomDiffuser/RetrievalOracle 均为 runner 层对照装配（复刻 SandboxDiffuser __call__ 接口，engine 衰减沿用真实机制）；lit_ticks/激活质量/检索名次新指标
- **bug_or_design_issue**: 无
- **design_preserving**: 是（核心零改动；对照基线属 §3.C 允许的 baseline 新增）
- **affected_experiments**: 实验 B2（diffusion_b2 族，20 run + 2 pilot）

## C24 — 实验 C（情绪调制）runner（run_exp_emotion.py）

- **timestamp**: 2026-09-29 上午
- **file**: scripts/run_exp_emotion.py（新）
- **reason**: R5——param_gain 通道（CC→update_param_modulation→发射比率，diffusion_engine.py:1271 消费）从未被实验测量；E2 禁用经 config param_modulation={"enabled": False}（:351 守卫既有开关）
- **bug_or_design_issue**: E2 初版传 {} 触发守卫默认 enabled=True 的坑（pm.get("enabled", True)），修正为显式 False——runner 缺陷，非核心
- **design_preserving**: 是
- **affected_experiments**: 实验 C-emo（emotion 族，20 run + 5 pilot）

## C25 — 实验 D（CI）runner（run_exp_ci.py）+ C25b 装配修正

- **timestamp**: 2026-09-29 上午
- **file**: scripts/run_exp_ci.py（新）
- **reason**: R6——CI 生命周期/再点火/表达决策从未被实验测量；CC tick 体零 LLM，llm_budget 桩（既有预算机制替身）使表达决策可见且语言实现按预算静默
- **before/after（C25b 修正）**: 初版 C0/C2 同时保留 SandboxDiffuser 与 CC.tick_once 的 decay+diffuse → 每拍双驱动（C1 单驱动的非对称对照）；修正为 CC 条件 st.diffuser=None（单驱动器）。另：再点火观测从"激活上升"（被感知事件污染，C1 假阳性 7 次）改为 _reactivate_field 直接插桩计数
- **bug_or_design_issue**: C25b 属实验装配缺陷修正（污染对照对称性）；cc.state() 键名误用 cis→intentions 一处
- **design_preserving**: 是
- **affected_experiments**: 实验 D-CI（ci 族，15 run + 2 pilot）

## C26 — 实验 F（Hebbian）runner（run_exp_hebbian.py）+ C26b 缺陷修复

- **timestamp**: 2026-09-29 中午
- **file**: scripts/run_exp_hebbian.py（新）
- **reason**: R8——_hebbian 真实改写权重但从未被直接测量；H0/H1=config.hebbian.enabled 既有开关；轮间激活淬火（运行时态清零，两条件同协议）隔离权重通道
- **bug_or_design_issue**: C26b——初版 deltas 打印用了 `{...} | None`（dict|None TypeError）→ 10 个 run 的 EPISODE 数据已落 run_* 目录但汇总 JSON 未写；修复后重跑全矩阵
- **design_preserving**: 是
- **affected_experiments**: 实验 F（hebbian 族，30 轮 ×2 条件 ×5 seeds）

## C27 — 实验 I（环境变化）runner（run_exp_envchange.py）

- **timestamp**: 2026-09-29 中午
- **file**: scripts/run_exp_envchange.py（新）
- **reason**: §15——资源带消失/新带出现（视野外）；U0 活账本 vs U1 causal=None（统计通道静默）；旧带消失→激活衰退轨迹（S11 口径升级为对照实验）
- **bug_or_design_issue**: 无（诚实记录：沙箱量程下探索通道即时吸收变化，见最终报告）
- **design_preserving**: 是
- **affected_experiments**: 实验 I（envchange 族，10 run + 4 pilot）

## C28 — 实验 G（层级事件/跨情境复用）runner（run_exp_event_transfer.py）

- **timestamp**: 2026-09-29 下午
- **file**: scripts/run_exp_event_transfer.py（新）
- **reason**: R9——G1 扁平记忆对照 = 继承图剥离 episodic 空间节点（装配层过滤 flatten_graph），与 G0 结构继承同事实量；G2 干净图
- **bug_or_design_issue**: 无
- **design_preserving**: 是
- **affected_experiments**: 实验 G（event_transfer 族，15 run）

## C29 — 实验 H（长链）runner（run_exp_longchain.py）+ 环境侧修复三项

- **timestamp**: 2026-09-29 下午
- **file**: scripts/run_exp_longchain.py（新）
- **reason**: R10/§14——旧 iron_ingot 负结果重审：furnace_take 重试窗（autonomy.smelt_pending 300s 壁钟）在沙箱时钟量程不可达 = 环境限制非机制失败。环境侧修复：① /smelt 即时结算入包（runner 包装 world.call——**ENVIRONMENT_SIMPLIFICATION**，furnace_take 环节由环境吸收，论文引用需带此口径）；② coal_ore 方块表补录（燃料支路）；③ 石矿丰度 12 块 + 预置工作台（C11 先例，容纳规划器重复合成消耗）
- **before**: iron 链全灭（27 run 环境限制，PREAUDIT §3 分类）
- **after**: L0 达成 @315 tick（46 动作/26 冗余/11 失败——真实行为形态）
- **bug_or_design_issue**: runner 时钟写法初版凌乱（tick 双驱动隐患），改回标准 Clock 单实例
- **design_preserving**: 是（全部世界数据层/runner 层；skills/autonomy 零改动）
- **affected_experiments**: 实验 H/K（longchain 族，20 run + 3 pilot）

## C30 — 实验 J（先验边界）runner（run_exp_prior_boundary.py）

- **timestamp**: 2026-09-29 下午
- **file**: scripts/run_exp_prior_boundary.py（新）
- **reason**: §16——A/B/C 既有操作化 + 单 stack 串行 5 episode 累积维度（经验累积能否替代先验）
- **bug_or_design_issue**: 无
- **design_preserving**: 是
- **affected_experiments**: 实验 J（prior_boundary 族，15 run）
