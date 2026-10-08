# config.py — Fascinator 超参数配置
# ============================================================================
# 所有可调超参数的默认值。
# 运行时可通过 PUT /api/config 热更新（无需重启服务）。
#
# 参数分类：
#   [扩散核心] lambda_decay, beta_spread, max_depth, theta_threshold
#   [认知聚焦] k_top, activation_max
#   [行动触发] theta_action
#   [语义匹配] cosine_threshold, edge_relation_factor
#   [时序控制] auto_interval
#   [前端渲染] render_full_max_nodes, render_neighbor_depth
# ============================================================================

DEFAULT_CONFIG = {

    # ------------------------------------------------------------------
    # 统一运行日志（fas_log）—— 只影响观测，不参与任何认知决策
    # ------------------------------------------------------------------
    # 日志总开关（关= fas_log 不落盘，行为与接入前完全一致）
    "logging_enabled": True,
    # 日志目录（相对项目根）
    "log_dir": "logs",
    # 总级别：DEBUG | INFO | WARNING | ERROR | CRITICAL
    # DEBUG 会打开图谱变更/激活细节等高频记录，只建议排障时临时用
    # set_level("DEBUG", "graph")。
    "log_level": "INFO",
    # [RUNTIME] 周期摘要间隔（秒）；0=关闭
    "log_summary_interval_s": 300,
    # 输入/回复等文本字段入盘策略：full（全文）| truncate（截断+哈希）|
    # hash（只留哈希）——防止日志无限堆积敏感全文（§5）
    "log_text_policy": "truncate",
    # 日志内文本截断长度（输入/回复等字段），0=不截断
    "log_text_truncate": 120,
    # 单文件轮转大小（MB）与保留份数
    "log_rotated_mb": 10,
    "log_keep_files": 5,
    # 异步落盘队列深度（满即丢弃并计数，绝不阻塞认知线程）
    "log_queue_size": 20000,
    # 同类异常去重窗口（秒）：窗口内只写首条 + 折叠计数
    "log_exc_dedup_window_s": 60,

    # ------------------------------------------------------------------
    # 扩散核心参数
    # ------------------------------------------------------------------
    # 节点激活衰减率（每步），取值范围 [0, 1]
    # 模拟人类注意力随时间的自然消退。越大遗忘越快，0 表示永不衰减。
    "lambda_decay": 0.05,

    # 回合间衰减率，取值范围 [0, 1]
    # 新输入到达前，全局激活值乘以 (1 - inter_round_decay)。
    # 比 lambda_decay 更强，用于防止跨轮次激活噪声累积。
    # 0.75 的语义：注意力残留只保留一轮——上一轮的满量程焦点（5.0）
    # 衰减到 1.25，低于本轮输入种子（相似度映射到 [0, activation_max]），
    # 于是"这一轮在想什么"由本轮输入决定，而不是最近五轮的平均。
    "inter_round_decay": 0.75,

    # 语义召回广度：每个输入词从 FAISS 取多少个近邻进入激活区。
    # 取值过窄会系统性截掉"次相关但关键"的节点——例如能力节点总是
    # 排在对象/事件之后（"minecraft" 邻域里 进入Minecraft世界 约第 7 位），
    # 于是她能想起 Minecraft 却想不起自己能玩 Minecraft。取值范围 [1, +∞)。
    "faiss_recall_topk": 8,

    # 语义相似度地板：低于本地板视为"不算焦点"，种子激活为 0。
    # 相似度 → 种子强度的映射为 [floor, 1] → [0, activation_max] 线性：
    # 1.0 = 该节点就是输入本身（满量程），floor = 不激活。
    # 地板挡住的是高频词/枢纽节点的人人有份式命中：一句"Fascinator"能
    # 召回一串 CI 节点（0.58~0.60），它们会把整片邻域泵到饱和，把真正
    # 相关的节点挤出回答区。0.5 以下的意义强度不足以构成注意力焦点。
    "input_similarity_floor": 0.5,

    # 无相似度时的种子强度（[0,1]，× activation_max）。
    # 用于 LLM 抽取出的实体：没有向量分数，但确实是本轮所述内容。
    "input_default_bonus": 0.5,

    # 启动时自动打开前端页面（http://127.0.0.1:<port>/）。
    # 调试期反复重启嫌烦可设 false，或起服务时带环境变量 FAS_NO_BROWSER=1。
    "auto_open_browser": True,

    # 嵌入推理设备：auto | cpu | cuda
    # auto = 有 CUDA 就用 GPU。实测（bge-small-zh，789 条节点文本）：
    #   全量索引重建 GPU 1.25s vs CPU 8s；单条查询 GPU 8ms vs CPU 17ms。
    # 但 GPU 首次调用要等显卡从低功耗态唤醒（WDDM，可达数十秒）——
    # 若在意"启动即可用"的确定性，写 cpu。
    "embedding_device": "auto",

    # bot 可执行文件路径（留空则自动探测：配置 > PATH > 常见安装位置）
    # 本机 node 装在 E:/Nodejs/node.exe 且不在 PATH，所以留空也能找到。
    "minecraft_bot_node_path": "",

    # ── 注意资源约束（emission）──
    # 理论定位：activation 是认知状态（注意相关度），不是守恒物理能量；
    # emission 是"本轮可向外传播的有限注意资源"的运行时约束。

    # 发射配比：节点一步最多发出 自身激活 × ratio 的注意资源（其余留在
    # 自己身上参与累积与排序）。这不是认知常数——它会被语境调制：
    #   实际配比 = emission_ratio + activity_emission_bonus（若节点处于
    #              当前活动的局部语境） + [预留: emotion/context/source 调制]
    # 0.5 只是当前默认运行参数，不是 Fascinator 的认知规律。
    "emission_ratio": 0.5,

    # 发射即转移：向外传播的量从自身注意资源中扣除——activation 不能在
    # 图结构中无成本复制（旧复制语义曾让空闲 40 tick 内 733 节点全亮）。
    # 这是运行时资源约束，不是认知本体论。0.0 仅为对照实验保留。
    "emission_transfer": 1.0,

    # CurrentActivity 语境调制（§12：影响注意力，不做全图广播）：
    # 处于当前活动局部语境（活动节点/类型/状态/目标 + 具身状态槽位）的
    # 节点，发射配比获得此加成——"正在挖铁时，铁相关的知识更容易被联想
    # 展开"。作用范围是活动的 1-hop 局部，不是激活注入。
    "activity_emission_bonus": 0.15,

    # 每步发射/传播明细日志（DEBUG 级）：研究"她为什么在注意这个"时打开。
    "emission_trace": False,

    # ── 情绪共振（dialogue_signals）──
    # 情绪当前状态的注入强度（有限、可衰减、受 activation cap 约束）。
    # 只在 NLP 语义佐证命中时注入（关键词命中只建长期结构）。
    # 情绪被激活 ≠ 必须共情——行为仍由行为竞争决定。
    "emotion_resonance_boost": 0.6,

    # 情绪 → 激素调制（§五：调制因素，绝不指定行为）。键=情绪节点名，
    # 值={激素: delta}。delta 走 internal_state.apply_delta 唯一写入口，
    # 幅度刻意很小（激素是慢变量）。未列出的情绪不产生激素调制。
    "emotion_hormone_modulation": {
        "焦虑":  {"cortisol": 0.03},
        "紧张":  {"cortisol": 0.03},
        "害怕":  {"cortisol": 0.04},
        "开心":  {"dopamine": 0.03},
        "兴奋":  {"dopamine": 0.04},
        "难过":  {"serotonin": -0.02},
        "沮丧":  {"serotonin": -0.02},
    },

    # 扩散传播增益系数 β，取值范围 [0, +∞)
    # 控制每次扩散时激活信号的放大倍数。越大信号越强、传播越远。
    "beta_spread": 1.0,

    # 最大扩散深度 D_max，取值范围 [1, +∞)
    # 限制每轮扩散的最大跳数，防止在图谱中无限传播。
    # 3 是论文语义下的默认：配合每步衰减，第 3 跳之后的信号已衰减到
    # 无认知意义；更深传播应由任务/参数节点动态放宽，而不是全局常开。
    "max_depth": 3,

    # 扩散终止激活阈值 θ，取值范围 [0, +∞)
    # 当所有节点的激活增量均小于此阈值时，认为扩散已收敛，提前终止。
    "theta_threshold": 0.01,

    # 扩散点火阈值，取值范围 [0, +∞)
    # 节点激活度低于此值时，不参与扩散传播。
    # 模拟神经元"全或无"放电场：弱信号不传递，减少认知噪声。
    "min_spread_threshold": 0.05,

    # 激活吸附阈值 ε。衰减后低于此值的激活吸附为 0 并移出活跃前沿。
    # 必须远低于 min_spread_threshold 与全库一切决策阈值（最低 0.01），
    # 吸附在行为上不可观察——它只是把浮点噪声尾巴归零（大规模图的
    # 活跃前沿稀疏性依赖此性质）。
    "activation_epsilon": 1e-4,

    # ------------------------------------------------------------------
    # 认知聚焦参数
    # ------------------------------------------------------------------
    # （2026-09-22 收尾清理：删除零引用死键 k_top——运行时 Top-K 由
    #  DiffusionEngine.get_topk 的调用参数决定，不读本键。）

    # 节点激活度上限，取值范围 [0, +∞)
    # 截断保护：任何节点的激活值不会超过此上限，防止少数节点霸占注意力。
    "activation_max": 5.0,

    # ------------------------------------------------------------------
    # 行动触发参数
    # ------------------------------------------------------------------
    # 程序性节点执行触发阈值 θ_action，取值范围 [0, +∞)
    # 当 procedural 节点的激活值 >= 此阈值时，进入行动队列等待执行。
    # 降低此值会让更多程序性节点被触发，升高则筛选更严格。
    "theta_action": 0.5,

    # ------------------------------------------------------------------
    # 语义匹配参数
    # ------------------------------------------------------------------
    # 模糊匹配余弦相似度阈值，取值范围 [0, 1]
    # Embedding 语义检索时，相似度低于此值的结果会被过滤。
    "cosine_threshold": 0.6,

    # 以关系名称激活边时的权重系数，取值范围 [0, 1]
    # NLP 解析出的关系名匹配到图谱边时，边激活值 = 此系数 * 语义相似度。
    "edge_relation_factor": 0.75,

    # （2026-09-22 收尾清理：删除零引用死键 edge_decay_factor——边衰减
    #  实际由 propagation_rules/空间动力学决定，全仓无人读本键。）

    # 边运行时激活 r 的上限（论文 w̃ = w + r 中的 r 值域）。
    # 加性注入：频繁使用的边最多把有效权重抬升这么多，杜绝旧的
    # 乘性 boost（w×(1+r×0.6)，r 可到 3.0 → 2.8×）造成的"高速公路
    # 自我强化"——常走的边越走越宽、永久占据传播优先级。
    "edge_runtime_activation_cap": 1.0,

    # ------------------------------------------------------------------
    # 时序控制参数
    # ------------------------------------------------------------------
    # 自动扩散间隔（秒），取值范围 [0.1, +∞)
    # 启动自动扩散后，每间隔此秒数自动执行一轮"衰减 + 扩散 + 行动刷新"。
    "auto_interval": 1.5,

    # ------------------------------------------------------------------
    # Everything is Graph 参数
    # ------------------------------------------------------------------
    # 内部思考生成阈值。Self 附近节点激活度超过此值时触发思考。
    "thought_threshold": 0.2,

    # （2026-09-22 收尾清理：删除零引用死键
    #  render_full_max_nodes / render_neighbor_depth（前端从不读，全仓无引用）、
    #  reflection_interval（反思节拍实际由 reflection_engine/CC 内部参数控制）、
    #  preference_decay_per_day / preference_min_weight / preference_max_weight /
    #  preference_increment、confidence_initial / confidence_confirm_delta /
    #  confidence_diffusion_factor——偏好与置信度语义已由图节点/边自身与
    #  disposition/personality 模块承载，这些全局键无人读取。）

    # ------------------------------------------------------------------
    # 安全 / 运行边界
    # ------------------------------------------------------------------
    # 是否允许通过 HTTP API 写节点的 execution 字段（程序性节点代码）。
    # 关闭（默认）的原因：execution 最终会被 diffusion_engine 的 exec() 执行，
    # 而本服务无鉴权，任何能发 POST /api/nodes 的客户端（含浏览器里的
    # 第三方页面）都能借此注入并执行任意 Python 代码。
    # 开发期需要用前端 EXEC 输入框写动作代码时，热更新开到 true：
    #   PUT /api/config  {"allow_api_execution_write": true}
    # 开启后仍只接受来自回环地址（127.0.0.1/::1）的写入，局域网客户端始终被拒。
    # 启动期播种（app.py / Action 注册表）与脚本直写不受此开关约束。
    "allow_api_execution_write": False,

    # 话题终止抑制时长（秒）。用户说"停止/换个话题"后，当前话题节点被抑制这么久。
    # 原先硬编码 600s 在 dialogue_decision.py 里，无法调节也无法审计。
    # 2026-09-22（§18.2 #10）：抑制期本体已是 cognitive_locks 里的
    # system 锁（expires_at/审计/解除 API），本键只是时长参数。
    "topic_termination_inhibit_sec": 600,

    # ── API 访问守卫与持久化兜底（2026-09-22，§18.2 #19 / §18.3 P1）──
    # api_token：非回环来源的访问令牌。空=不签发（本机使用零摩擦不变）；
    # FAS_HOST 绑定非回环时启动自动生成并持久化到 data/api_token.json。
    # Origin/Referer 核验与此开关无关，始终生效（防 CSRF/DNS rebinding）。
    "api_token": "",
    # true = 连 127.0.0.1 来源也要求 token（默认 False，保持本机体验）
    "api_token_require_loopback": False,
    # 图谱快照轮转：单写入器每次成功落盘后，距上次 ≥ interval 秒复制一份
    # 到 data/graph_snapshots/，只保留最近 keep 份（0 = 关闭）
    "graph_snapshot_interval_s": 1800,
    "graph_snapshot_keep": 12,
    # MC 桥活性 poller（§18.2 #7/#11）：CC 既有 poll 节拍按此间隔采样
    # 桥 /health；True→False 翻转自动把会话节点回写 disconnected
    "mc_bridge_poll_interval_s": 30,
    # navigate_to_entity 目标暂不可见时的可见宽限（秒）：连接/渲染距离边界的
    # 实体同步竞态用 pending 轮询消化，到期仍不可见才如实 entity_not_visible
    # （0 = 关闭宽限，立即失败）。技能层 ctx.config 是扁平读取，故放顶层。
    "navigate_visible_grace_s": 20,
    # 空闲踱步（2026-09-25，用户："真人会因为没到阈值就站游戏里不动吗"）：
    # 阈值下的空闲由 Amble 技能给身体——短距离走走看看，有界、节流、危险不逛。
    "amble_distance": 6,        # 单次踱步目标距离（格，随机 3~此值）
    "amble_timeout_s": 25,      # 踱步时长上限（到点自然收束，不记失败）

    # ------------------------------------------------------------------
    # LLM Provider 参数
    # ------------------------------------------------------------------
    # （2026-09-22 收尾清理：删除零引用死键 llm_provider——生产后端选择
    #  由 llm_provider.create_backend 与 data/llm_config.json 决定，本键
    #  无人读取；同理删除 ollama_model / deepseek_model / deepseek_api_key /
    #  deepseek_base_url——模型名与密钥走 llm_config.json / 环境变量
    #  DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL，不经过 config.py。）

    # 延迟优化（2026-09-19 设）：历史上 true 时把解析/抽取降到非推理档换速度。
    # 2026-09-22：MiMo 全场景统一思考模型 mimo-v2.6-flash，此开关不再换模型
    # （保留兼容）；改动需重启。
    "nlp_fast_parse": True,

    # A1（CON-1/CON-2 生产接线 2026-09-30）：把 attention_context（8 分区）
    # 与 analyze_cognitive_demand 三层结构（demand/gap/resource）渲染进
    # 生产回答 prompt。此前认知状态块被 answer_question 的形参短路永不
    # 渲染（论文证据全部来自渲染注解的 harness）——本键接通二者。
    # False = 整块跳过 = 精确回滚到审计前形态。
    "nlp_render_cognitive_context": True,

    # Ollama 本地后端开关：true 才启用 ollama_backend.py（平时不可见）
    "enable_ollama": False,

    # 世界事件观测面（收尾 B2）：true 时具身差分/回执世界事件在 fas_log
    # PERCEPTION 聚合行可见（排查用；平时关，防刷屏）。
    "debug_world_events": False,

    # （2026-09-22 收尾清理：删除 auto_gap_detection——gap 检测已标注为
    #  未接线的离线手动工具，见 conversation_gap_detector.py 头部说明。）

    # ------------------------------------------------------------------
    # Architecture Refactor 0.1: 认知空间与关系类别
    # ------------------------------------------------------------------
    # （2026-09-22 收尾清理：删除 graph_spaces / relation_categories 两个
    #  全局清单——全仓无人读取；空间/类别语义由 graph_model 与各模块内联
    #  判定，下面的 propagation_rules/relation_rules 才是运行时真源。）

    # 按 graph_space 差异化的传播动力学。
    # 未列出的空间回退到全局 lambda_decay / beta_spread。
    # semantic:   客观知识 — 正常传播，稳定衰减（与旧系统行为一致）
    # episodic:   经历记忆 — 快速衰减，单向传播，低激活上限
    # cognitive:  认知过程 — 快速形成注意，中等衰减
    # self:       自我节点 — 长期保持，缓慢传播
    "propagation_rules": {
        "semantic":   {"lambda_decay": 0.05, "beta_spread": 1.0, "allow_bidirectional": True,  "activation_cap": 5.0},
        "episodic":   {"lambda_decay": 0.15, "beta_spread": 0.7, "allow_bidirectional": False, "activation_cap": 3.0},
        "cognitive":  {"lambda_decay": 0.08, "beta_spread": 1.5, "allow_bidirectional": False, "activation_cap": 5.0},
        "self":       {"lambda_decay": 0.01, "beta_spread": 0.5, "allow_bidirectional": True,  "activation_cap": 5.0},
    },

    # 按 relation_category 差异化的传播规则。
    # direction: "bidirectional"=双向, "forward"=仅沿边方向(src→dst)
    # decay: 边激活衰减倍率（乘以源节点的 space lambda_decay）
    # gain:  边传播增益倍率（乘以 beta_spread）
    # 方向默认原则（架构对齐 2026-09-19）：不知道方向 → forward，绝不默认双向。
    # 只有语义上真正对称的关系（见 relation_direction 覆盖表）才双向。
    "relation_propagation": {
        "semantic_relation":   {"direction": "forward",       "decay": 1.0, "gain": 1.0},
        "episodic_relation":   {"direction": "forward",       "decay": 1.2, "gain": 0.8},
        "causal_relation":     {"direction": "forward",       "decay": 0.8, "gain": 1.2},
        "temporal_relation":   {"direction": "forward",       "decay": 1.5, "gain": 0.7},
        "emotional_relation":  {"direction": "bidirectional", "decay": 0.3, "gain": 1.5},
        "social_relation":     {"direction": "forward",       "decay": 0.8, "gain": 0.8},
        "cognitive_relation":  {"direction": "forward",       "decay": 0.5, "gain": 1.3},
        "procedural_relation": {"direction": "forward",       "decay": 0.5, "gain": 1.2},
    },

    # ======================================================================
    # 关系本体（架构对齐 2026-09-19 收敛版）
    # ======================================================================
    # 三张表配合使用，唯一消费者是 graph_schema.py：
    #   relation_ontology  — 规范关系词表：relation → relation_category。
    #                        只有这里列出的关系是合法的"原子关系"。
    #   relation_synonyms  — 表面形式 → 规范关系（LLM 同义变体在写图前归一）。
    #   relation_direction — 按关系的方向覆盖（默认取 relation_propagation
    #                        中所属类别的方向）。只列真正对称的关系。
    # 未收录且无同义映射的自由文本 → 兜底归一为 relation_fallback。
    "relation_fallback": "关联",

    "relation_ontology": {
        # ── 语义关系（客观知识）──
        "是": "semantic_relation", "属于": "semantic_relation", "包含": "semantic_relation",
        "具有": "semantic_relation", "位于": "semantic_relation", "靠近": "semantic_relation",
        "拥有": "semantic_relation", "使用": "semantic_relation", "名字叫": "semantic_relation",
        "类型": "semantic_relation", "相关": "semantic_relation", "同一": "semantic_relation",
        # ── 经历关系（情景事件论元）──
        "参与": "episodic_relation", "经历": "episodic_relation", "观察": "episodic_relation",
        "讲述": "episodic_relation", "完成": "episodic_relation",
        # ── 时间关系 ──
        "发生时间": "temporal_relation", "发生于": "temporal_relation",
        "时间顺序": "temporal_relation", "发生于时段": "temporal_relation",
        "之前": "temporal_relation", "之后": "temporal_relation", "持续": "temporal_relation",
        # ── 因果关系 ──
        "导致": "causal_relation", "影响": "causal_relation", "抑制": "causal_relation",
        # ── 情绪关系 ──
        "喜欢": "emotional_relation", "讨厌": "emotional_relation", "感受": "emotional_relation",
        # ── 社交关系 ──
        "请求": "social_relation", "感谢": "social_relation", "问候": "social_relation",
        "对话": "social_relation", "认识": "social_relation", "用户关系": "social_relation",
        # ── 认知关系（思维产物/自我状态/引擎通路）──
        "关于": "cognitive_relation", "基于": "cognitive_relation", "关联": "cognitive_relation",
        "涉及": "cognitive_relation", "指向": "cognitive_relation",
        # C21（行动侧事件框架 2026-09-28）：失败/成功结算落图的 `-[结果]->` 槽位边。
        # 实验 D 沙箱首画这条边时词表还没有这个词 ⇒ 落盘被兜底归一成 `关联`
        # （语义抹平；行为不受影响，见 EXPERIMENT_D_EVENTFRAME.md §8 修正）。与
        # 上面"网络"/"掉落"同理：词表必须跟写在图上的词同步。
        "结果": "cognitive_relation",
        "目标": "cognitive_relation", "子目标": "cognitive_relation", "意图": "cognitive_relation",
        "驱动": "cognitive_relation", "信念": "cognitive_relation", "偏好": "cognitive_relation",
        "价值观": "cognitive_relation", "推断": "cognitive_relation", "记得": "cognitive_relation",
        "注意": "cognitive_relation", "未知": "cognitive_relation", "产生": "cognitive_relation",
        "先验": "cognitive_relation", "激活": "cognitive_relation", "处于": "cognitive_relation",
        "当前状态": "cognitive_relation", "状态项": "cognitive_relation", "状态": "cognitive_relation",
        "当前活动": "cognitive_relation", "当前目标": "cognitive_relation", "认知焦点": "cognitive_relation",
        # R2 P11/D-7：`Self-[网络]->CENetwork/DMNetwork` 自 2026-09-20 就在写
        # （drive_engine.py:461），但这个词从没进过词表 ⇒ 落盘被兜底归一成 `关联`
        # （实测运行图里 `网络` 边 0 条）。注册后语义不再被抹平；**存量那两条不改**
        # （load 不走规范化，改它们要重写图谱数据，超出 R2 范围）。
        "网络": "cognitive_relation",
        "trigger_curiosity": "cognitive_relation", "uncertainty_signal": "cognitive_relation",
        "resolve": "cognitive_relation", "intent_child": "cognitive_relation",
        "建议": "cognitive_relation", "推荐": "cognitive_relation", "查询": "cognitive_relation",
        # 言语行为图谱化（speech_act_graph 2026-09-20）：说出=用户→话语事件；
        # 言外行为=话语→概念；倾向=概念/回应方式→行为（扩散通道非映射表）；
        # 细化=概念→情境（层级合并 dialogue_act 表示，双向语义）
        "说出": "cognitive_relation", "言外行为": "cognitive_relation",
        "倾向": "cognitive_relation", "细化": "semantic_relation",
        # ── 动作概念（Action Concept 架构升级 2026-09-19）──
        # 表面表达 → 稳定动作概念：表达方式边只建在
        # action_expression 节点（"跟着我"）与 action_concept 节点（FOLLOW）之间，
        # 图谱提供候选激活，不直接触发执行（执行必须经 Action Intent）。
        "表达方式": "semantic_relation",
        # ── 行动空间（Action Concept 一等公民 2026-09-20）──
        # 别名=表面形式→行动概念（提议去重）；实施=行动_*事件→概念（溯源）；
        # 包含/属于/可实现为=行动本体（族→成员、成员→族、抽象→具体）。
        "别名": "semantic_relation", "实施": "episodic_relation",
        "包含": "semantic_relation", "属于": "semantic_relation",
        "可实现为": "procedural_relation",
        # ── 能力图谱（Capability–Action–Executor 重构 2026-09-20）──
        # 诱发=世界状态/条件节点→行动概念（该状态在场时此行动值得考虑，
        # 与 先验/驱动 同类的**可发现性**种子边，非固定触发管道）；
        # 需要/实现于/拥有/抑制 复用既有词（需要→能力/资源、实现于→载体、
        # 拥有→背包物品、条件节点→能力的活跃抑制）。
        "诱发": "cognitive_relation",
        # ── MC 世界语义（具身图谱化 2026-09-21）──
        # 造成=因果后果（熔岩→烧伤）；需要工具/产生=行动-世界机制知识；
        # 服务于=行动→动机归属；受保护=物种→资产保护约束（关系不是永久
        # 禁止，授权可解除）；授权针对=授权节点→物种；不兼容/敌对=行动
        # 互斥与生物关系语义。
        "造成": "causal_relation", "产生": "causal_relation",
        "威胁": "causal_relation",
        # 掉落=运行时 block_meta 核验过的"方块→物品"来源关系（学习实验
        # 2026-09-25）。和 产生 同类：不注册就会被兜底归一成"关联"，语义抹平
        # 且读图侧（world_prior.drop_sources）再也认不出来源边——与上面"网络"
        # 那条教训同类（词表必须跟写在图上的词同步）。
        "掉落": "causal_relation",
        # ── 神经调制（R2 P5，2026-09-21）──
        # 调制器→认知目标/需求/Drive 的**系数边**。符号优先写在关系上（可解释、
        # 可审计），权重只表示强度：
        #   调制 = 方向由权重符号给出的通用调制弧（留作"符号在权重里"的写法）；
        #   增强 = 正向（gain>0）；抑制 = 负向（已注册，复用）；
        #   交互 = 调制器之间的对称耦合（协同/拮抗），故进 relation_direction 双向表。
        # 这 5 个词（含既有 影响/抑制）是调制子系统允许的全部关系词——闭合的只有
        # 词表，拓扑与强度都是图上的数据（禁令 2：不用固定映射表替代图）。
        "调制": "causal_relation", "增强": "causal_relation", "交互": "causal_relation",
        "需要工具": "procedural_relation", "服务于": "cognitive_relation",
        "受保护": "social_relation", "针对": "semantic_relation",
        "不兼容": "semantic_relation", "敌对": "semantic_relation",
        # ── 程序关系（能力/执行）──
        "执行": "procedural_relation", "能": "procedural_relation", "能力": "procedural_relation",
        "擅长": "procedural_relation", "正在学习": "procedural_relation", "需要": "procedural_relation",
        "前置": "procedural_relation", "绑定": "procedural_relation", "作用于": "procedural_relation",
        "实现于": "procedural_relation", "相关能力": "procedural_relation",
    },

    # 表面形式 → 规范关系。键不必是规范词；值必须是 relation_ontology 中的规范词。
    # 覆盖历史上 LLM 产出的高频变体；未覆盖的自由文本由 graph_schema 兜底归一。
    "relation_synonyms": {
        # 命名类
        "名字是": "名字叫", "名字": "名字叫", "叫法": "名字叫", "名称": "名字叫",
        # 因果类
        "引发": "导致", "触发": "导致", "triggers": "导致", "因为": "导致",
        "源于": "导致", "源自": "导致", "由于": "导致", "促使": "导致",
        # 偏好类
        "想去": "喜欢", "想玩": "喜欢", "想要": "喜欢", "偏爱": "喜欢", "钟爱": "喜欢",
        "感到": "感受",
        # 属性类
        "特性": "具有", "功能是": "具有", "味道是": "具有", "颜色是": "具有",
        "形状是": "具有", "产出": "具有", "has_attribute": "具有", "核心玩法": "具有",
        "玩法目标": "具有", "核心载体": "具有", "特点": "具有",
        # 位置/空间类
        "在": "位于", "处于": "位于", "居住于": "位于", "通向": "位于", "放置地点": "位于",
        # 经历类
        "玩过": "经历", "在过": "经历", "参加过": "经历", "攻读": "经历", "进行": "经历",
        "发生过": "经历", "参与过": "经历", "喝了": "经历", "通关": "完成",
        "appears_with": "相关", "same_as": "同一",
        # 包含类
        "包括": "包含", "包含情节": "包含", "包含任务": "包含", "组成": "包含",
        # 部位/构成
        "部位": "具有", "组成": "包含",
        # 时间类
        "时间": "发生时间", "发生于时间": "发生时间",
        # 事件论元类（MEMORY_EXTRACT 常见输出）
        "参与者": "参与", "同行": "参与", "动作": "涉及", "论元": "涉及",
        # 社交类
        "监护关系": "认识", "师生关系": "认识", "朋友关系": "认识",
        # 认知类
        "思考": "关于", "针对": "关于", "记忆": "记得", "回忆": "记得",
        "驱动力": "驱动", "回应": "对话", "回答": "对话", "询问": "请求",
        "线索连接": "关联", "类比": "相关", "类似": "相关", "负相关": "相关",
        # 程序类
        "can_do": "能", "acts_on": "作用于", "has_key": "绑定", "has_movement": "绑定",
        "bound_to": "绑定", "按下": "执行", "触发动作": "执行",
        # 杂项长尾（历史上出现过的自由文本，按最近语义归并）
        "态度": "相关", "帮助建设": "相关", "有导师": "相关", "同公司": "相关",
        "购买关系": "相关", "用户偏好": "喜欢", "解锁高级配方": "前置",
        "需要兼顾": "需要", "前置条件": "前置", "依赖": "前置", "需要": "需要",
        "易产生": "导致", "可能发展为": "导致", "引发需求": "导致",
        "强化": "导致", "满足": "导致", "扩展场景": "相关", "有时需要": "需要",
        "想要成为": "目标", "探索目标": "目标", "主要威胁": "相关",
        "攻击对象": "作用于", "交换": "对话", "发布": "对话", "韵律": "相关",
        "沸腾于": "导致", "排出": "导致", "输入输出": "相关",
        "并列需求": "需要", "在学习": "正在学习", "关注": "注意",
        "影响": "影响",
        # R2 P5：神经调制的常见表面形式（LLM 抽取/论文命名会写成这些）。
        # 只映射到已注册的规范词；不新增自由词。注意 "强化" 早已映射到 "导致"，
        # 那是存量图谱的既成事实，本轮**不改**（改了会让历史边含义漂移），
        # 所以调制语境下要写"增强"而不是"强化"。
        "调控": "调制", "调节": "调制", "modulates": "调制", "调制作用": "调制",
        "易化": "增强", "上调": "增强", "potentiates": "增强", "enhances": "增强",
        "抑制作用": "抑制", "inhibits": "抑制",
        "相互作用": "交互", "协同": "交互", "interacts_with": "交互",
    },

    # 按关系的方向覆盖（默认取所属类别的方向）。
    # 真正对称的关系才双向："关于/涉及/参与"是 aboutness（提及/论元/参与）
    # 关系——事件涉及对象 ⟺ 对象属于事件——双向是语义本身，不是偷懒；
    # 且图的写入方向是"记录→实体"，若单向会让实体种子失去回忆上下文的通路
    # （实测 48% 节点是纯汇点，全单向 = 召回断路）。
    # 因果/时间/程序/引擎通路严格单向；无向共现"关联"保持 forward。
    "relation_direction": {
        "相关": "bidirectional",
        "同一": "bidirectional",
        "认识": "bidirectional",
        "靠近": "bidirectional",
        "关于": "bidirectional",
        "涉及": "bidirectional",
        "参与": "bidirectional",
        # R2 P5：调制器之间的耦合是对称的物理事实（皮质醇∘多巴胺 与 多巴胺∘皮质醇
        # 是同一条弧），单向会逼系统"记两遍"。而 调制/增强/抑制 保留因果方向：
        # 调制器→认知目标 是有方向的（反向不构成调制）。
        "交互": "bidirectional",
    },

    # label → graph_space 推断表（graph_migration 使用；app.py bootstrap 使用）
    "graph_space_fallback_map": {
        "declarative-semantic": "semantic",
        "declarative-episodic": "episodic",
        "procedural": "cognitive",
        "infrastructure": "cognitive",
        "self": "self",
        "disposition": "self",
        "intention": "self",
    },

    # ------------------------------------------------------------------
    # Reflection Engine — 反思机制（Phase 2）
    # ------------------------------------------------------------------
    "reflection": {
        # 触发配置
        "periodic_interval_turns": 20,       # 每 N 轮对话触发一次定期反思
        "emotion_trigger_threshold": 3.0,    # 累计情绪激活度超过此值触发事件反思
        "min_experiences_for_reflection": 5,  # 最少需要收集多少条经历才触发

        # 候选审批
        "auto_approve_threshold": 0.75,      # importance >= 此值的候选自动批准
        "max_candidates_per_cycle": 5,        # 每次反思最多输出的候选数

        # 高情绪事件检测：扫描 episodic 空间中最近 N 天的情绪边
        "emotion_event_window_days": 7,
    },

    # ── 敌对生物名单（唯一真相源 2026-09-20）────────────────────
    # 此前 autonomy(10)/action_system 紧急检查(8)/observation(19) 三处各存
    # 一份。统一为观察层全集：感知标注、gates、危险检测都读这里；
    # action_system._check_emergency 的贴脸紧急判定保留更小语义子集
    # （那是执行安全边界，不是世界知识）。
    "hostile_entities": [
        "zombie", "skeleton", "creeper", "spider", "cave_spider", "enderman",
        "witch", "slime", "pillager", "husk", "drowned", "stray", "phantom",
        "zombie_villager", "silverfish", "guardian", "blaze", "ghast",
    ],

    # ------------------------------------------------------------------
    # MC World Semantics — 世界知识入图（具身图谱化 2026-09-21）
    # 这些表是**种子**：启动幂等种进主知识图谱（mc_knowledge.ensure_mc_world）；
    # 运行时的保护/授权/工具需求推理一律读图（不读表）。经验晋升（CausalLearner）
    # 与教学可在此之上继续生长。可采集成员 = SAFE_GATHER_BLOCKS 的图镜像，
    # 技能层保留原表仅作无图离线兜底（执行层物理校验，不再作保护裁判）。
    # ------------------------------------------------------------------
    "mc_world": {
        # 物种/方块 → 类别（-[属于]->）
        "taxonomy": {
            "chest": ["容器", "用户资产"],
            "barrel": ["容器", "用户资产"],
            "crafting_table": ["功能方块", "用户资产"],
            "furnace": ["功能方块", "用户资产"],
            "blast_furnace": ["功能方块", "用户资产"],
            "smoker": ["功能方块", "用户资产"],
            "bed": ["功能方块", "用户资产"],
            "door": ["功能方块", "用户资产"],
            "ladder": ["用户资产"],
            "torch": ["用户资产"],
        },
        # 类别 → 保护关系（容器/功能方块/用户资产 受 用户资产保护 约束）
        "protected_classes": ["用户资产", "容器", "功能方块"],
        "protection_node": "用户资产保护",
        "gatherable": [
            "oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log",
            "dark_oak_log", "mangrove_log", "cherry_log", "log", "wood",
            "stone", "cobblestone", "dirt", "grass_block", "sand", "gravel",
            "netherrack", "deepslate", "cobbled_deepslate",
            "coal_ore", "iron_ore", "copper_ore", "gold_ore", "diamond_ore",
            "redstone_ore", "lapis_ore", "emerald_ore",
            "deepslate_coal_ore", "deepslate_iron_ore", "deepslate_copper_ore",
            "deepslate_gold_ore", "deepslate_diamond_ore",
            "deepslate_redstone_ore", "deepslate_lapis_ore",
            "deepslate_emerald_ore",
            "wheat", "carrots", "potatoes", "beetroot", "sugar_cane",
            "sweet_berry_bush", "pumpkin", "melon", "cactus",
            "oak_leaves", "spruce_leaves", "birch_leaves", "jungle_leaves",
        ],
        # 挖掘机制知识（认知层可推理"先做石镐再挖铁"；技能层物理表并存）
        # 2026-09-26 离线验收修正：deepslate_* 矿在 gatherable/produces 里
        # 都有、tool_required 却整组缺失——规划器因此认为"挖深层铁矿无需
        # 工具"，定向探索永远排不进做镐（§16 链卡死 40 拍的直接根因）。
        # 深层变种与其普通版同工具等级（Minecraft 机制，非行为规则）。
        "tool_required": {
            "coal_ore": "wooden_pickaxe", "stone": "wooden_pickaxe",
            "cobblestone": "wooden_pickaxe", "copper_ore": "stone_pickaxe",
            "iron_ore": "stone_pickaxe", "lapis_ore": "stone_pickaxe",
            "gold_ore": "iron_pickaxe", "diamond_ore": "iron_pickaxe",
            "redstone_ore": "iron_pickaxe", "emerald_ore": "iron_pickaxe",
            "deepslate_coal_ore": "wooden_pickaxe",
            "deepslate_iron_ore": "stone_pickaxe",
            "deepslate_copper_ore": "stone_pickaxe",
            "deepslate_gold_ore": "iron_pickaxe",
            "deepslate_diamond_ore": "iron_pickaxe",
            "deepslate_redstone_ore": "iron_pickaxe",
            "deepslate_lapis_ore": "stone_pickaxe",
            "deepslate_emerald_ore": "iron_pickaxe",
        },
        "produces": {
            "stone": "cobblestone", "cobblestone": "cobblestone",
            "iron_ore": "raw_iron", "deepslate_iron_ore": "raw_iron",
            "copper_ore": "raw_copper", "gold_ore": "raw_gold",
            "diamond_ore": "diamond", "redstone_ore": "redstone",
            "lapis_ore": "lapis_lazuli", "emerald_ore": "emerald",
            "oak_log": "oak_log", "coal_ore": "coal",
        },
        # 危险因果：物种/情形 -[造成]-> 伤害概念 -[威胁]-> 健康
        "hazards": {
            "creeper": "爆炸伤害", "zombie": "近战伤害", "skeleton": "箭伤",
            "lava": "烧伤", "fall": "摔伤", "drowning": "溺水",
            "spider": "近战伤害", "husk": "饥饿伤害", "drowned": "近战伤害",
        },
        # 生物 -[敌对]-> 玩家
        "hostile_preys": ["player"],
        # 生存类行动的动机归属（行动概念 -[服务于]-> 生存需求）
        "serves_survival": ["RETREAT", "RECOVER", "EAT", "SEEK_SAFETY",
                            "FORAGE"],
        # 行动互斥（攻击与撤退不并存——认知一致性线索）
        "incompatible": {"ATTACK": ["RETREAT", "FOLLOW"]},
        # 授权有效期（秒）：用户明确命令形成的授权证据窗
        "grant_ttl_s": 600,
        # kernel：濒死停手线（唯一保留的数值兜底，控制原语非行为裁决）
        "death_floor_health": 4.0,
    },

    # ------------------------------------------------------------------
    # （2026-09-22 收尾清理：删除 "drive_system" 整段——CognitiveField
    #  重构（2026-09-20）后驱动动力学参数在 tension/drive/network 各段，
    #  本段的 curiosity/social/learning 权重与 eval_interval_turns 全仓
    #  零读取（tests/test_drive_system.py 用的是自造 CFG，不依赖此段）。）
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 神经调制系统 R2（2026-09-21）—— 调制器规格的唯一集中定义处
    # ------------------------------------------------------------------
    # 声明：名称是**功能类比**，不声称复现人体内分泌；下面所有数值都是
    # FAS 仿真参数（不是医学数据）。字段与消费者一一对应，见
    # docs/neuromodulation_R2_audit.md §5 —— 禁止出现"有字段没人读"。
    # internal_state.MODULATOR_SPEC 只是本表缺省时的兜底（离线测试/旧快照）。
    #
    # 双通道（用户裁定 2026-09-21）：
    #   tonic（慢分量）→ hormone.<名> 信号 → ModulationLayer → 认知场参数
    #   phasic（事件脉冲）→ 图谱激活注入（稀疏、只在事件时、受发射预算约束）
    "modulator_system": {
        "enabled": True,
        # ① Self-[处于]->调制器 锚定边（语义完整 vs 稀释 Self 出边份额的取舍）
        "link_to_self": True,
        "link_relation": "处于",
        "link_weight": 0.20,          # 12 条 ×0.20 → Self 正权出边和 49.3→51.7（摊薄 ~4.5%）
        # ② phasic → 图激活（Drive 范式：level×5 量纲 + mark_active + 刺激源登记）
        "graph_pulse": True,
        "graph_pulse_threshold": 0.08,
        "graph_pulse_scale": 5.0,
        "graph_pulse_source": "modulator",
        # ④ 图谱子图（R2 P6，用户批准 A3；P9 增补 需求↔调制器 与 网络/驱动 落点；
        #    P10 增补 时段/昼夜→调制器）
        #    14 个 `调制目标:*` 靶点 + 69 条出厂边（其中 30 条投影成**参数系数**、
        #    35 条是 网络/驱动/需求/时段/心情/行为 的耦合、4 条 `交互` 只改事件写入增益）
        #    + 12 条 Self-[处于]-> 锚定边。
        #    幂等 bootstrap 把这些数据种进图，**种完后图是权威**（改边=改效力，
        #    见 tests/test_modulator_subgraph.py 的"图是真源"断言）；本表只是出厂拓扑。
        "subgraph": True,
        # ── 图上耦合的幅度/速率参数（§24：默认值集中在这里，模块常量只是兜底）──
        #   bias_cap：`调制器→网络/驱动/需求` 的**稳态偏置**每一格最多挪多少。
        #     0.5 的含义是"单边顶格也只能把某个水位从量程中点推到端点"，
        #     再大就等于调制器直接决定网络取值（那是模式开关，不是偏置）。
        #   need_drift_rate_per_min：`需求→调制器` 的漂移速率（每分钟的系数单位）。
        #     它与各调制器自己的 decay_per_min 相互拉扯，共同决定稳态位移量：
        #     实测（1 分钟节拍、探索/安全缺口 0.30/0.20 恒定）多巴胺样
        #     0.50→0.522、皮质醇样 0.30→0.287 —— 抬的是**水位**，不是脉冲。
        "bias_cap": 0.5,
        "need_drift_rate_per_min": 0.02,
        #   circadian_drift_rate_per_min：`时段/昼夜→调制器` 的漂移速率（P10）。
        #     比需求漂移快一档是**时间尺度**决定的（§27）：昼夜要在一个夜段里真的
        #     把水位抬起来（melatonin decay 0.035/min），需求缺口则要慢到"持续缺口
        #     才留得住痕迹"。两者不同值不是不一致。
        "circadian_drift_rate_per_min": 0.06,
        # ── 心情（R2 P11：派生、单向、有界）────────────────────────
        #   node：`调制器→心情` 这些边的**端点**（self 空间的状态锚点）。它的数值
        #     不存在图上（存在 PersonalityBaseline 里，混合原则：数值在对象、
        #     语义/符号/强度在边），节点的作用是①让边有处可连、②让"心情"在
        #     self 图上可达（调制器脉冲会沿边点亮它，P6 给调制器用的同一条证据）。
        #     刻意**不建出边**：出边为 0 = 图上写着的单向性（心情不反哺任何东西），
        #     闸门测试断言的就是这一点（tests/test_mood_one_way.py）。
        #   cap：Σ(权重 × dev) 的绝对值上限。为什么只有 0.35：心情的两个来源里，
        #     一次真实交互事件的偏移是 0.12/0.18（personality.mood.event_effects），
        #     0.35 意味着"激素包络最多相当于两次强事件"，能把底色推离中性，
        #     但不能替用户刚说的话定性（§15：mood 可派生，但不得成为万能变量）。
        #     出厂 5 条边的 |w| 和是 1.10，钳到 0.35 后拓扑再被改宽也钉不住心情。
        "mood": {"node": "心情", "cap": 0.35},
        # ── 结果→学习 的调制系数（R2 P11 收口：这五个数原先硬写在 reward.py 里）──
        #   learning factor = base + phasic_coef×max(0,脉冲) + tonic_coef×(tonic−baseline)
        #                       − stress_coef×max(0, cortisol_tonic−baseline)
        #                       ×(1 + surprise_coef×|RPE|)，钳在 [min, max]。
        #   为什么放在调制子系统而不是 reward 段：这一项是"激素改变这次经历值多少
        #   学习"（激素→学习率，不是激素→人格），三个读数都是调制器通道的量。
        #   三条通道用三个不同的读数（脉冲 / tonic−基线 / 压力侧 tonic−基线），
        #   互不重复计数——审计 D-2 修的就是这个（读 level 会把脉冲算两遍）。
        "learning_modulation": {
            "base": 1.0, "phasic_coef": 0.60, "tonic_coef": 0.15,
            "stress_coef": 0.25, "surprise_coef": 0.30,
            "min": 0.4, "max": 1.8,
            "phasic_mod": "dopamine", "tonic_mod": "dopamine", "stress_mod": "cortisol",
        },
        # 认知场目标：短名 → 真实参数键。调制器边只连"调制目标:<短名>"节点，
        # 投影器按节点 extra_attrs.param 找参数（不靠字符串解析，改名安全）。
        "targets": {
            "扩散发射配比": "diffusion.emission_ratio",
            "扩散轮间衰减": "diffusion.inter_round_decay",
            "扩散增益": "diffusion.param_gain",
            "扩散深度": "diffusion.max_depth",
            "注意广度": "attention.width_scale",
            "检索广度": "retrieval.topk_scale",
            "重燃周期": "retrieval.reignite_every",
            "回答温度": "llm.temperature_answer",
            "认知预算": "llm.budget_factor",
            "探索速率": "behavior.exploration_rate",
            "探索余量": "behavior.explore_margin",
            "行动门槛": "action.score_threshold",
            "成形阈值": "cognition.form_threshold",
            "表达阈值": "cognition.express_threshold",
        },
        # 边：[调制器名, 关系, 目标, 权重, 语义理由]
        #   目标写成 "调制目标:短名"（本表 targets 里的）或直接写图节点 id；
        #   符号约定：增强=正、抑制=负、调制=符号写在权重上（如"抬高行动门槛"
        #   这种"抑制行为但参数为正"的情形）；|权重|×sensitivity 就是投影后的系数。
        #   端点不存在则跳过（不为此造点）。
        #   ⚠️ 每个调制器必须至少 1 条**正权出边**：扩散里纯负边的节点永不发射
        #      （实测 `if total_w > 0`），那样它在图上等于不存在。
        "edges": [
            # ── 迁移：既有 7 条 effects 逐字搬进图（系数=权重×sensitivity，三者
            #    sensitivity 都是 1.0 ⇒ 数值一字不变；ModulationLayer 那 7 行随后删除）──
            ["dopamine", "增强", "调制目标:扩散发射配比", 0.80, "奖励预期高→发射更放开（原 effects 行）"],
            ["cortisol", "增强", "调制目标:扩散轮间衰减", 0.40, "压力→痕迹消得更快（原 effects 行）"],
            ["cortisol", "增强", "调制目标:成形阈值", 0.15, "压力→更难形成新结论（原 effects 行）"],
            ["oxytocin", "抑制", "调制目标:表达阈值", -0.12, "联结感强→更愿意说（原 effects 行）"],
            ["cortisol", "增强", "调制目标:表达阈值", 0.10, "压力→倾向不说（原 effects 行）"],
            ["dopamine", "抑制", "调制目标:行动门槛", -0.08, "奖励预期高→门槛降（原 effects 行）"],
            ["cortisol", "增强", "调制目标:行动门槛", 0.10, "压力→门槛升（原 effects 行）"],
            # ── 奖赏与探索 ──
            ["dopamine", "增强", "调制目标:探索速率", 0.20, "正性奖赏史→探索偏好上升"],
            ["dopamine", "增强", "调制目标:认知预算", 0.10, "回报预期好→愿意多花资源"],
            # ── 注意与切换 ──
            ["norepinephrine", "增强", "调制目标:检索广度", 0.30, "意外/不确定→检索面拉开"],
            ["norepinephrine", "抑制", "调制目标:扩散深度", -0.20, "警觉→少绕远，收敛到当前焦点"],
            ["norepinephrine", "增强", "调制目标:回答温度", 0.10, "切换压力→表达上多一点变体"],
            # ── 信号保真与编码 ──
            ["acetylcholine", "增强", "调制目标:检索广度", 0.20, "精度加权→相关证据取得更多"],
            ["acetylcholine", "抑制", "调制目标:成形阈值", -0.10, "保真度高→更早可以下结论"],
            ["glutamate", "增强", "调制目标:扩散增益", 0.30, "兴奋增益（发射侧唯一读点 param_gain）"],
            ["glutamate", "抑制", "调制目标:扩散轮间衰减", -0.10, "兴奋痕迹留存更久"],
            ["gaba", "抑制", "调制目标:扩散增益", -0.30, "全局抑制：防 runaway（正出边另给）"],
            ["gaba", "调制", "调制目标:行动门槛", 0.20, "抬高门槛=抑制行动，符号写在权重上"],
            ["gaba", "增强", "调制目标:注意广度", 0.10, "抑制噪声→有效注意面反而变窄而稳"],
            # ── 应急与钝化 ──
            ["adrenaline", "调制", "调制目标:行动门槛", -0.30, "急性应急→立刻可做（门槛降）"],
            ["adrenaline", "抑制", "调制目标:探索速率", -0.30, "应急下先利用已知，不探索"],
            # 正出边不是凑数：应急把"当前正在想的东西"放大（聚焦），且纯负出边的
            # 节点在扩散里永不发射（实测 `if total_w > 0`）——那上面两条边都是死边。
            ["adrenaline", "增强", "调制目标:扩散增益", 0.20, "应急聚焦：已激活内容被放大"],
            ["endorphin", "增强", "调制目标:探索余量", 0.20, "钝化挫折→能坚持（margin 放宽）"],
            ["endorphin", "抑制", "调制目标:成形阈值", -0.20, "负效价被缓冲→不易被单次事件定形"],
            # ── 稳定、联结、清醒、昼夜 ──
            ["serotonin", "调制", "调制目标:行动门槛", 0.15, "冲动抑制：抬门槛（正系数=抑制行动）"],
            ["serotonin", "抑制", "调制目标:探索速率", -0.15, "稳定态→偏好熟悉路径"],
            # （oxytocin 对表达阈值只有上面那条迁移行 -0.12：同一调制器对同一参数
            #  再写一条会在投影时撞键，effects 表按信号索引装不下两行）
            ["histamine", "增强", "调制目标:认知预算", 0.20, "清醒→允许更多资源"],
            ["histamine", "调制", "调制目标:重燃周期", -0.30, "清醒→更早重燃冷字段"],
            ["melatonin", "调制", "调制目标:认知预算", -0.25, "夜段→预算收缩"],
            ["melatonin", "调制", "调制目标:行动门槛", 0.20, "夜段→更少发起行动"],
            # ── 调制器 ↔ 网络 / Drive / 行为概念（图上可见的耦合）──
            ["cortisol", "抑制", "DMNetwork", -0.25, "压力压走神（场景 11）"],
            ["norepinephrine", "增强", "CENetwork", 0.25, "意外→控制网络占优（场景 14）"],
            ["acetylcholine", "增强", "CENetwork", 0.20, "保真→任务聚焦"],
            ["melatonin", "增强", "DMNetwork", 0.30, "夜段→走神/反思偏置"],
            ["glutamate", "增强", "CENetwork", 0.15, "兴奋增益供给执行"],
            ["dopamine", "增强", "CuriosityDrive", 0.30, "奖赏预期→好奇驱动"],
            ["oxytocin", "增强", "SocialDrive", 0.35, "联结→社交驱动"],
            ["serotonin", "增强", "ConsistencyDrive", 0.20, "稳定→一致性驱动"],
            ["norepinephrine", "增强", "LearningDrive", 0.15, "意外→要学点什么"],
            ["histamine", "增强", "行为:探索", 0.20, "清醒+空闲→主动发起（场景 12）"],
            ["dopamine", "增强", "行为:分享", 0.15, "好结果→讲给人听"],
            ["cortisol", "增强", "行为:沉默", 0.15, "压力→收着说"],
            ["oxytocin", "增强", "行为:共情", 0.30, "联结→共情回应"],
            ["endorphin", "增强", "行为:延续", 0.20, "钝化→接着干"],
            # ── 需求 ↔ 调制器（多对多，走图；场景 13）──
            #   正向（需求→调制器）：未被满足的需求把对应通道抬上去/压下去，
            #     由 `modulator_subgraph.apply_need_drift()` 每拍经唯一写入口慢慢写（R2 P9）。
            #   反向（调制器→需求）：**稳态回路**——通道水位反过来收敛需求的靶值
            #     （`update_needs_from_signals(bias=…)`）。一正一反成负反馈：
            #     缺口→抬通道→缺口收敛→通道回落，不会互相推爆（有界性见 P9 闸门）。
            ["生存需求", "增强", "皮质醇样", 0.20, "口渴式需求→压力通道上升"],
            ["安全需求", "抑制", "皮质醇样", -0.25, "安全感→压低压力"],
            ["社交需求", "增强", "催产素样", 0.20, "饥饿的社交需求→联结通道"],
            ["探索需求", "增强", "多巴胺样", 0.20, "探索饥渴→奖赏预期通道"],
            ["掌控需求", "增强", "血清素样", 0.15, "掌控感→稳定通道"],
            # 需求→调制器补齐（R2 P9：NEED_SPEC["modulators"] 迁移无损——
            # competence 原来就声明走 dopamine+cortisol 两条通道，图上此前缺这两条）
            ["掌控需求", "增强", "多巴胺样", 0.15, "没做成的事→回报预期通道被牵动"],
            ["掌控需求", "增强", "皮质醇样", 0.15, "能力缺口→压力通道上升"],
            ["皮质醇样", "增强", "安全需求", 0.25, "持续压力→安全感缺口被抬高（想避险）"],
            ["多巴胺样", "抑制", "探索需求", -0.20, "奖赏水位已高→探索缺口收敛（饱足）"],
            ["催产素样", "抑制", "社交需求", -0.20, "联结感已足→社交缺口收敛"],
            # ── 时段/昼夜 → 调制器（R2 P10：§11 melatonin 由时间事实驱动）──
            #   源节点属性 `circadian_strength` = 此刻有多"夜"（0~1，唯一作者
            #   temporal_awareness.update_clock_state；白天节点的在势是它的补）。
            #   显著性不乘 activation：扩散的货币不借来当调制驱动（P6 同一纪律），
            #   而且不乘才让"同一 bucket 序列 ⇒ 同一曲线"成立（P10 闸门）。
            #   权重尺度按"抬幅 = rate×w×强度/decay"反推：目标是深夜褪黑素样落在
            #   饱和拐点(0.70)附近、白天压到量程低段但**不钉在 0**（钉满就没信息了）。
            #   实测曲线见 docs/neuromodulation_R2_audit.md 附一〇。
            ["夜间", "增强", "褪黑素样", 0.25, "夜段→睡意水位上升（小时级，不是脉冲）"],
            ["白天", "抑制", "褪黑素样", -0.05, "白天压回低段（小权重：留分辨率）"],
            ["深夜", "增强", "褪黑素样", 0.08, "最深的夜在夜段之上再推一点（时段级形状）"],
            ["凌晨", "增强", "褪黑素样", 0.06, "凌晨仍在睡"],
            ["白天", "增强", "组胺样", 0.12, "白天→清醒基线抬起来"],
            ["夜间", "抑制", "组胺样", -0.12, "夜段→清醒度回落（与褪黑素同向的两端）"],
            # ── 调制器 → 心情（R2 P11：心情的"包络"成分，单向）──
            #   乘数是 `dev = 受体响应(浓度−基线)`（与各条参数通道同一个量），
            #   所以过载反转在这里同样生效：皮质醇钉在 max 附近时它对心情的
            #   下拉会**回推**（耗竭/麻木的钝化读法），而不是继续往负端钉死。
            #   为什么是这 5 条：血清素=背景稳态（张力性的那个，权重最大）、
            #   皮质醇=压力底色、多巴胺=预期水位、催产素=联结感、
            #   褪黑素=夜里的钝重（它让"熬夜后情绪差"这句有图上的通路）。
            #   **没有** 需求→心情：需求已经通过 P9 的边抬/压这些调制器，再连一条
            #   就是同一件事数两遍（也违反"每条边一个可说清的理由"）。
            ["serotonin", "增强", "心情", 0.30, "背景稳态好→底色偏暖（唯一张力性主导项）"],
            ["cortisol", "抑制", "心情", -0.30, "压力水位高→底色发沉"],
            ["dopamine", "增强", "心情", 0.20, "预期/奖赏水位抬底色（ tonic 侧，不含脉冲）"],
            ["oxytocin", "增强", "心情", 0.20, "联结感是底色的正项（与被忽略时回落对应）"],
            ["melatonin", "抑制", "心情", -0.10, "深夜意压一点亮度（P10 的昼夜由此自然进心情）"],
            # ── 调制器 ↔ 调制器（`交互`：P7 的事件写入增益；协同为正、拮抗为负）──
            # 这些边**不产参数系数**（靶点不是 调制目标:*），只在 apply_event 里
            # 改变"某类事件写进某调制器时乘多少"：gain = Π(1 + w × dev(源))。
            # 为什么需要：禁令要求"一个调制器的作用可被另一个调节"这件事也是图数据，
            # 而不是在 pulse/apply_event 里写 if endorphin>0.6。
            ["endorphin", "交互", "cortisol", -0.60, "拮抗：内啡肽高→压力事件的写入被钝化（场景 10）"],
            ["oxytocin", "交互", "cortisol", -0.40, "拮抗：联结感缓冲压力"],
            ["gaba", "交互", "glutamate", -0.50, "拮抗：抑制偏置压住兴奋增益（防 runaway）"],
            ["acetylcholine", "交互", "norepinephrine", 0.30, "协同：保真度高时意外信号更有效（场景 6）"],
        ],
        # ⑤ 速率/增益表（R2 P9）：调制器 → 动力学的"变化速度"与"亲和增益"落点。
        #    为什么写在 config 而不是图上：这些不是"谁连谁"的拓扑事实（那种边在上面的
        #    edges 里，已经 50+ 条），而是**同一条耦合的量纲常数**——"curiosity.rise
        #    抬多少"没有图谱语义，是仿真参数（§24：出厂默认集中在 config）。
        #    统一公式：gain = 1 + Σ(coef × dev(调制器))，钳在 [GAIN_MIN, GAIN_MAX]。
        #    没有任何 `if 名字 == "dopamine"`：调制器名只是这一行的**键**（禁令 2）。
        #    ⚠️ 与 R2 之前的一处语义变化：`affinity` 现在**双向**（催产素低于基线会把
        #    社交亲和增益压到 1 以下），旧代码是 `max(0, oxy)` 只抬不压。理由与实测见
        #    审计附九。
        "rate_gains": {
            # 张力场整体加速：压力状态下张力起落都快（原 rate_gains_t 的 1+0.3·cortisol）
            "tension": {"*": {"cortisol": 0.30}},
            # 驱动的上升/消退速率：奖赏预期高→好奇升得快、退得慢；学习同理
            "drive": {
                "curiosity.rise": {"dopamine": 0.50},
                "curiosity.fall": {"dopamine": -0.40},
                "learning.rise": {"dopamine": 0.50},
            },
            # 亲和增益 "<drive>.<tension>"：这一格张力对该驱动的牵引乘多少
            "affinity": {
                "social.belonging_need": {"oxytocin": 0.60},
                "social.social_presence": {"oxytocin": 0.40},
            },
        },
        # ③ 衰减/回归类写入合并成一条历史（避免每 tick 刷屏挤掉真实事件）
        "decay_history_merge_min": 5.0,
        "recent_sources_keep": 5,
        "specs": {
            # overload_pull 的**单位**（R2 P8/D5，重要）：浓度顶到 `max` 时从响应峰值里
            # 扣掉的量（绝对值，不是二次项系数）。内部换算成
            # `pull/(max−overload)²` 再交给 `receptor_response`，所以
            #   · 0 = 过峰后只随 φ 缓降（R2 之前的行为，"过载"只是装饰）
            #   · 1 = 顶格时响应正好抵消回零；>1 直接**反号**（倒 U 的下行支真的走到对侧）
            # 之所以要归一：`overload` 都写在 .80–.95，可达的超出量只有 .05–.20，
            # 原始二次项在这个尺度上小到看不见 —— 曲线形状对，数字没牙齿。
            # 这些值是仿真参数（§10：生物学名只是功能类比），不是医学数据。
            # ── 既有 4 个：level/baseline/min/max/decay_per_min/desc 原样保留 ──
            "dopamine": {
                "category": "monoamine", "mirror": "多巴胺样",
                "level": 0.50, "baseline": 0.50, "min": 0.0, "max": 1.0,
                "decay_per_min": 0.02, "rise_per_min": 0.12,
                "phasic_decay_per_min": 0.30, "split_tonic": True,
                "momentum": 0.30, "sensitivity": 1.00,
                "saturation": 0.85, "overload": 0.95, "refractory_min": 0.5,
                "overload_pull": 0.70,   # R2 P8/D5：奖赏水位钉死→探索/表达增益塌回（"过度承诺"）
                "desc": "奖励与学习调制（tonic=总体回报预期；phasic=奖励预测误差脉冲）",
            },
            "norepinephrine": {
                "category": "monoamine", "mirror": "去甲肾上腺素样",
                "level": 0.45, "baseline": 0.45, "min": 0.0, "max": 1.0,
                "decay_per_min": 0.04, "rise_per_min": 0.15,
                "phasic_decay_per_min": 0.25, "split_tonic": True,
                "momentum": 0.25, "sensitivity": 1.10,
                "saturation": 0.80, "overload": 0.92, "refractory_min": 1.0,
                "overload_pull": 0.80,   # R2 P8/D5：焦虑式扫描过载→注意控制反而失守
                "desc": "注意与控制切换（提高注意阈值、加减速率、意外→打断当前意图）",
            },
            "acetylcholine": {
                "category": "cholinergic", "mirror": "乙酰胆碱样",
                "level": 0.50, "baseline": 0.50, "min": 0.0, "max": 1.0,
                "decay_per_min": 0.045, "rise_per_min": 0.10,
                "phasic_decay_per_min": 0.30, "split_tonic": False,
                "momentum": 0.20, "sensitivity": 1.00,
                "saturation": 0.85, "overload": 0.95, "refractory_min": 0.0,
                "overload_pull": 0.45,   # R2 P8/D5：保真度信号过冲只会糊掉权重
                "desc": "信号保真度（精度加权、检索广度、不确定性敏感度）",
            },
            "cortisol": {
                "category": "hormone", "mirror": "皮质醇样",
                "level": 0.30, "baseline": 0.30, "min": 0.0, "max": 1.0,
                "decay_per_min": 0.05, "rise_per_min": 0.06,
                "phasic_decay_per_min": 0.10, "split_tonic": False,
                "momentum": 0.10, "sensitivity": 1.00,
                "saturation": 0.75, "overload": 0.90, "refractory_min": 0.0,
                "overload_pull": 0.90,   # R2 P8/D5：极端压力下"全线收紧"这个机制本身先失效
                "desc": "压力与威胁调制（提高威胁优先级、下调风险容忍）",
            },
            "adrenaline": {
                "category": "hormone", "mirror": "肾上腺素样",
                "level": 0.20, "baseline": 0.20, "min": 0.0, "max": 1.0,
                "decay_per_min": 0.12, "rise_per_min": 0.30,
                "phasic_decay_per_min": 0.45, "split_tonic": False,
                "momentum": 0.15, "sensitivity": 1.30,
                "saturation": 0.60, "overload": 0.80, "refractory_min": 3.0,
                "overload_pull": 1.00,   # R2 P8/D5：应急过载直接熄火（最高，符合"别长期泡在应激里"）
                "desc": "急性应急（行动 urgency、抢占与反应速度；升得快落得也快）",
            },
            "serotonin": {
                "category": "monoamine", "mirror": "血清素样",
                "level": 0.55, "baseline": 0.55, "min": 0.0, "max": 1.0,
                "decay_per_min": 0.01, "rise_per_min": 0.04,
                "phasic_decay_per_min": 0.05, "split_tonic": False,
                "momentum": 0.10, "sensitivity": 0.90,
                "saturation": 0.80, "overload": 0.95, "refractory_min": 0.0,
                "overload_pull": 0.35,   # R2 P8/D5：稳定类：倒 U 下行支浅
                "desc": "稳定性与抑制调制（冲动抑制、情绪恢复、长期坚持）",
            },
            "oxytocin": {
                "category": "neuropeptide", "mirror": "催产素样",
                "level": 0.40, "baseline": 0.40, "min": 0.0, "max": 1.0,
                "decay_per_min": 0.03, "rise_per_min": 0.05,
                "phasic_decay_per_min": 0.08, "split_tonic": False,
                "momentum": 0.15, "sensitivity": 1.00,
                "saturation": 0.80, "overload": 0.95, "refractory_min": 0.0,
                "overload_pull": 0.35,   # R2 P8/D5：同上
                "desc": "社交联结调制（社交价值、同伴聚焦）",
            },
            "endorphin": {
                "category": "neuropeptide", "mirror": "内啡肽样",
                "level": 0.30, "baseline": 0.30, "min": 0.0, "max": 1.0,
                "decay_per_min": 0.06, "rise_per_min": 0.08,
                "phasic_decay_per_min": 0.12, "split_tonic": False,
                "momentum": 0.20, "sensitivity": 1.10,
                "saturation": 0.70, "overload": 0.85, "refractory_min": 10.0,
                "overload_pull": 0.45,   # R2 P8/D5：镇痛/缓冲过冲→钝化本身该退化
                "desc": "钝化与坚持（负效价事件的缓冲、疼痛/挫折容忍、persist 支撑）",
            },
            "gaba": {
                "category": "inhibitory", "mirror": "GABA样",
                "level": 0.55, "baseline": 0.55, "min": 0.0, "max": 1.0,
                "decay_per_min": 0.015, "rise_per_min": 0.05,
                "phasic_decay_per_min": 0.06, "split_tonic": False,
                "momentum": 0.10, "sensitivity": 0.90,
                "saturation": 0.85, "overload": 0.95, "refractory_min": 0.0,
                "overload_pull": 0.35,   # R2 P8/D5：抑制过强只是更安静，不反号
                "desc": "全局抑制偏置（竞争 margin、防 runaway；过载表现为反应迟滞）",
            },
            "glutamate": {
                "category": "excitatory", "mirror": "谷氨酸样",
                "level": 0.50, "baseline": 0.50, "min": 0.0, "max": 1.0,
                "decay_per_min": 0.08, "rise_per_min": 0.10,
                "phasic_decay_per_min": 0.30, "split_tonic": False,
                "momentum": 0.15, "sensitivity": 1.00,
                "saturation": 0.80, "overload": 0.90, "refractory_min": 0.0,
                "overload_pull": 0.60,   # R2 P8/D5：兴奋增益过冲必须回抽（防 runaway）
                "desc": "兴奋增益（扩散/发射增益、编码写入强度；过载＝信号失真）",
            },
            "histamine": {
                "category": "monoamine", "mirror": "组胺样",
                "level": 0.45, "baseline": 0.45, "min": 0.0, "max": 1.0,
                "decay_per_min": 0.025, "rise_per_min": 0.06,
                "phasic_decay_per_min": 0.10, "split_tonic": False,
                "momentum": 0.10, "sensitivity": 0.90,
                "saturation": 0.75, "overload": 0.90, "refractory_min": 0.0,
                "overload_pull": 0.50,   # R2 P8/D5：睡眠压力类
                "desc": "清醒度（警觉基线、空闲→主动发起、认知预算上限的许可）",
            },
            "melatonin": {
                "category": "circadian", "mirror": "褪黑素样",
                "level": 0.20, "baseline": 0.20, "min": 0.0, "max": 1.0,
                "decay_per_min": 0.035, "rise_per_min": 0.02,
                "phasic_decay_per_min": 0.04, "split_tonic": False,
                "momentum": 0.05, "sensitivity": 1.00,
                "saturation": 0.70, "overload": 0.85, "refractory_min": 0.0,
                "overload_pull": 0.40,   # R2 P8/D5：昼夜类（P10 由时段事实驱动）
                # 唯一由 circadian 语境驱动的调制器：不读系统时钟，只吃时段节点属性
                "circadian_driven": True,
                "desc": "昼夜与睡眠压力（夜间 urgency↓、反思/检索偏置↑；由时段图谱事实驱动）",
            },
        },
        # ⑤ 事件表（R2 P7，设计决定 D4）：**事件 → 调制器**的扇出是图数据，
        #    不是 reward.py 里的逐激素 if/else。启动时幂等种成
        #    `事件类型:<名> -[影响 w]-> 调制器镜像` 的边，种完后图是权威
        #    （改边权=改事件的激素效力；见 tests/test_modulation_events.py 的"图是真源"组）。
        #    每行：type 事件类型 / mod 调制器英文名 / w 权重（=旧代码里那个常数）
        #    signal 取哪个事件字段算幅度（默认 valence）/ channel 写哪条通道
        #    （tonic=慢分量 apply_delta；phasic=pulse，走习惯化+不应期）
        #    gate 门控（字段_min/_max、valence_sign）/ clamp 单次写入上限。
        #    ⚠️ 同一个 (type, mod) 只能有一条边：`kg.add_edge` 对重复三元组是
        #    max(weight) 合并，第二条会被静默吃掉——正负两个方向请拆成两个事件类型。
        "event_default": {"signal": "valence", "channel": "tonic", "clamp": 0.20},
        "event_rules": [
            # ── 迁移：reward.release() 里 .25/.06/.05/.04/−.05/.02 六个常数 ──
            #    原代码按 ev["source"] 分"社交/自身"两路；这里改由事件的
            #    social_relevance / goal_relevance 字段**门控**，分路判据也成数据。
            {"type": "reward_rpe", "mod": "dopamine", "w": 0.25, "signal": "rpe",
             "channel": "phasic", "gate": {"goal_relevance_min": 0.5}, "clamp": 0.60,
             "why": "原 PHASIC_SCALE=.25：奖励预测误差打 phasic（只对自己的目标结果）"},
            {"type": "reward_success", "mod": "dopamine", "w": 0.04, "signal": "intensity",
             "gate": {"goal_relevance_min": 0.5},
             "why": "原 DOPA_LEVEL_POS=.04：自身正结果抬慢分量"},
            {"type": "reward_success", "mod": "oxytocin", "w": 0.06, "signal": "valence",
             "gate": {"social_relevance_min": 0.5}, "why": "原 SOCIAL_OXY=.06 正向：社交联结"},
            {"type": "reward_failure", "mod": "dopamine", "w": -0.05, "signal": "intensity",
             "gate": {"goal_relevance_min": 0.5}, "why": "原 DOPA_LEVEL_NEG=−.05"},
            {"type": "reward_failure", "mod": "oxytocin", "w": 0.036, "signal": "valence",
             "gate": {"social_relevance_min": 0.5},
             "why": "原 SOCIAL_OXY×0.6：负性社交只降 6 成（刻意不对称）"},
            {"type": "reward_failure", "mod": "cortisol", "w": 0.05, "signal": "intensity",
             "why": "原 NEG_CORT=.05：负性事件→压力（不分社交/自身）"},
            {"type": "reward_failure", "mod": "serotonin", "w": -0.02, "signal": "intensity",
             "why": "原 SERO_NEG=.02：稳定感受损（修 D-1：血清素必须有真实效力）"},
            {"type": "reward_failure", "mod": "endorphin", "w": 0.03, "signal": "intensity",
             "why": "挫折调动钝化系统（场景 10 的输入侧）"},
            # ── 非奖赏来源：意外、新奇、阻碍（这些信号原先根本进不了激素）──
            {"type": "prediction_violation", "mod": "norepinephrine", "w": 0.10,
             "signal": "abs_rpe", "channel": "phasic",
             "why": "意外→注意切换脉冲（场景 14：意图被打断）"},
            {"type": "prediction_violation", "mod": "acetylcholine", "w": 0.06,
             "signal": "uncertainty", "why": "不确定→提高信号保真需求（场景 6）"},
            {"type": "novel_contact", "mod": "acetylcholine", "w": 0.05, "signal": "novelty",
             "why": "新奇→保真度上升"},
            {"type": "novel_contact", "mod": "glutamate", "w": 0.05, "signal": "novelty",
             "why": "新奇→编码增益（场景 5）"},
            {"type": "obstacle_hit", "mod": "adrenaline", "w": 0.12, "signal": "intensity",
             "why": "被挡住→急性应急（行动 urgency 上升、探索抑制）"},
            {"type": "obstacle_hit", "mod": "cortisol", "w": 0.02, "signal": "intensity",
             "why": "持续阻碍渗进压力通道（幅度刻意小于 reward_failure）"},
            {"type": "interrupted", "mod": "norepinephrine", "w": 0.10,
             "signal": "intensity", "channel": "phasic",
             "why": "场景 14：意图被取消/打断→注意切换脉冲。旧代码里 cancelled"
                    "完全不产生任何调制（没完成≠做错了），但它确实要抢占"},
            {"type": "interrupted", "mod": "adrenaline", "w": 0.06, "signal": "intensity",
             "why": "被打断→短时应急（衰减快，不留残余）"},
            # ── 时段迁移（R2 P10；P7 就把这一类事件留着没接靶点）──
            #   signal=valence 且调用方给的是**有符号**的夜间度之差
            #   （`load_to − load_from`，由 temporal_awareness 从图上事实算）：
            #   天黑→褪黑素抬、天亮→褪黑素降，一条边管两个方向。
            #   上午→中午 差值 0 ⇒ 事件被 `signal=…=0` 门掉（不是"每次都抖一下"）。
            #   这一格是**快通道**（跨段那一刻的一步），慢通道是上面的
            #   昼夜漂移边；两条通道同向但不重叠，正是 §27 的时间尺度分层。
            {"type": "time_bucket_changed", "mod": "melatonin", "w": 0.45,
             "signal": "valence", "channel": "tonic", "clamp": 0.25,
             "why": "跨时段→褪黑素水位被推/拉一步（幅度=图上夜间度之差）"},
            {"type": "time_bucket_changed", "mod": "histamine", "w": -0.35,
             "signal": "valence", "channel": "tonic", "clamp": 0.20,
             "why": "变夜→清醒度掉一格；变亮→抬起来（同一事件的两侧）"},
        ],
    },

    # ------------------------------------------------------------------
    # 基线人格层（出厂先验 + 心情瞬态）。心情 = 事件余韵 + 调制器包络（P11）。
    #   decay_per_hour：事件余韵每小时向 0 衰减的比例（线性：0.15/h ⇒ 3⅓ 小时
    #     减半、6⅔ 小时归零，`max(0, …)` 保证不翻号）。它对应 §27 时间尺度阶梯里
    #     最快的一档（phasic/心情 < 调制器 < disposition < trait）：但按"每分钟向
    #     目标回归的比例"比较，心情 0.0025/min 比调制器的 0.02~0.06/min **更慢**，
    #     与排序相反——数值本身待用户裁定（审计 §6 已标注），R2 不擅自改既有出厂
    #     速率（改了就是在动一个跑通过的动力学常数）。
    #   event_effects：一次交互事件对 valence 的偏移。负比正大是有意的（损失厌恶
    #     的功能类比，不是医学声明，禁令 9）。
    # 代码内置默认在 personality_baseline.py（MOOD_* 常量），此处为出厂真源。
    # ------------------------------------------------------------------
    "personality": {
        "mood": {
            "decay_per_hour": 0.15,
            "event_effects": {"positive": 0.12, "negative": -0.18},
        },
    },

    # ------------------------------------------------------------------
    # Cognitive Field — Tension → Drive → Network → Modulation（2026-09-20 重构）
    # Drive 不再是四类需求的分数，而是认知张力的持续聚合；
    # CEN/DMN 是连续网络调制态（Demand 回答"要多少资源"，Network 回答
    # "资源怎么组织"，Mode 只是记账档位——三者解耦）。
    # 本段是调参面；代码内置默认（cognitive_field.py DEFAULT_*）与之同构，
    # 此处缺省时用默认，逐键覆盖即可。新增张力/Drive/网络/参数只加数据。
    # ------------------------------------------------------------------
    "cognitive_field": {
        "enabled": True,
        "state_file": "data/tension_drive_state.json",
        "autosave_steps": 24,            # 每 N 拍落盘一次（CC ~10s/拍）
        # 激素基线（register_state 读数按 值−基线 进调制信号）。
        # **唯一真源是上面的 modulator_system.specs.<名>.baseline**；这里只作
        # 显式覆盖（缺省不填即可）。保留本键是为了兼容旧配置读取路径。
        "hormone_baseline": {},
        # 行动倾向（派生可解释字段；只进行为竞争的调制因子，不执行行为）
        "action_tendencies": {
            "continue_task": {"base": 0.0, "network.CEN": 0.40,
                               "drive.learning": 0.25,
                               "tension.unfinished_goal": 0.25,
                               "tension.blocked_action": 0.15,
                               "context.task_engaged": 0.10},
            "explore": {"base": 0.0, "drive.curiosity": 0.45,
                         "network.DMN": 0.20, "tension.novelty": 0.20,
                         "tension.discrepancy": 0.15, "network.CEN": -0.15},
            "ask_user": {"base": 0.0, "drive.social": 0.40,
                          "tension.social_absence": 0.20,
                          "tension.unresolved_interest": 0.25,
                          "network.DMN": 0.10, "context.player_near": 0.10},
            "reflect": {"base": 0.0, "drive.consistency": 0.45,
                         "tension.self_contradiction": 0.20,
                         "network.DMN": 0.25, "context.idle_minutes": 0.10},
        },
    },

    # 张力源：rise>fall = 张力天然持续（未完成的事不会一消失就归零）。
    # components: [{source 采样器名, coef 合成权重, scale 饱和归一, saturation 分量封顶}]
    "tension_sources": {
        "discrepancy":         {"rise": 0.55, "fall": 0.10},
        "novelty":             {"rise": 0.55, "fall": 0.10},
        "unresolved_interest": {"rise": 0.40, "fall": 0.05},
        "satiation":           {"rise": 0.50, "fall": 0.12},
        "prediction_error":    {"rise": 0.60, "fall": 0.10},
        "blocked_action":      {"rise": 0.50, "fall": 0.08},
        "unfinished_goal":     {"rise": 0.50, "fall": 0.06},
        "social_absence":      {"rise": 0.20, "fall": 0.15},
        # 刺激剥夺（2026-09-24）：与 social_absence 同构——"很久没有新体验"
        # 自己长成好奇的持续张力（components 见 cognitive_field 默认表；
        # 时钟源=autonomy._last_novel_ts，好奇心动机成功结算即归零）。
        "stimulation_hunger":  {"rise": 0.15, "fall": 0.30},
        "belonging_need":      {"rise": 0.35, "fall": 0.10},
        "social_presence":     {"rise": 0.50, "fall": 0.20},
        "negative_experience": {"rise": 0.40, "fall": 0.08},
        "mood_low":            {"rise": 0.30, "fall": 0.08},
        "self_contradiction":  {"rise": 0.40, "fall": 0.06},
        "capability_gap":      {"rise": 0.35, "fall": 0.10,
                                 "components": [
                                     {"source": "competence_need", "coef": 0.7},
                                     {"source": "graph.capability_gap",
                                      "coef": 0.5}]},
        # P3/§5：探索缺口（已知对象∧未知用途）是好奇的**持久**张力源——
        # 与 unresolved_interest（对话侧、瞬态）分工：这条读图上的缺口 hub。
        "exploration_gap":     {"rise": 0.45, "fall": 0.10,
                                 "components": [
                                     {"source": "graph.exploration_gap",
                                      "coef": 1.0}]},
        "unexpected_event":    {"rise": 0.70, "fall": 0.20},
        "uncertainty":         {"rise": 0.40, "fall": 0.10},
    },

    # Drive = 张力亲和度聚合（affinities 可负=抑制项）。
    # 旧四驱保留为高层语义标签：节点名/输出键不变（兼容契约）。
    "drive_field": {
        "drives": {
            "curiosity": {"rise": 0.50, "fall": 0.06},
            # 陪伴即满足（2026-09-24 "真实玩家各玩各的"）：social_presence 从
            # +0.25 翻为 -0.30——同伴在场**压低**社交驱动而不是喂饱它（旧语义
            # 是"越在一起越想跟"，跟屁虫循环的最后一条腿）。同处在场时驱动≈
            # 缺席时钟(×0.25 缓释)+归属 −在场抑制 → 目标归零，跟随候选自然
            # 竞争不过采集/探索/实验；人走远 presence 张力 ~20s 内退潮，
            # 缺席时钟恢复真实增速，她才会偶尔来找你。affinities 为整体覆盖
            # （_merged 一层深合并），三个键必须全带。
            "social": {"rise": 0.30, "fall": 0.08,
                       "affinities": {"social_absence": 0.45,
                                      "belonging_need": 0.30,
                                      "social_presence": -0.30}},
            "learning": {"rise": 0.35, "fall": 0.07},
            "consistency": {"rise": 0.28, "fall": 0.05},
            # 侧向软竞争（乘性压缩目标、非 winner-take-all；默认空表）
            "_lateral": {},
        },
    },

    # 行动空间（2026-09-20 行动重构）：候选宇宙来自图谱扫描而非枚举。
    # 下列表只是**启动期供性边种子**（先验/驱动/情绪→行动）与最小本体；
    # 运行时新行动概念经 action_space.propose_action 入图并获得同样的
    # 激活边学习通道。enabled=false → 候选退回 LEGACY 词表宇宙（精确回滚）。
    "action_space": {
        "enabled": True,
        "candidate_min_activation": 0.05,   # 行动节点自身激活入选阈值
        "candidate_top_k": 12,              # 计算限制（非语义封闭）
        "new_concept_similarity": 0.82,     # 嵌入去重阈值（复用优先）
        # Drive → 行动（cognitive 边；CuriosityDrive→行为:探索 那条由
        # disposition_store.ensure_competition_edges 继续维护，此处共存幂等）
        "drive_edges": {
            "CuriosityDrive": [["行为:追问", 0.35]],
            "SocialDrive": [["行为:分享", 0.30], ["行为:延续", 0.20]],
            "LearningDrive": [["行为:详述", 0.20]],
            "ConsistencyDrive": [["行为:确认", 0.15]],
        },
        # 情绪 → 行动（情绪节点在 self/图谱里按名存在，缺源的边静默跳过）
        "emotion_edges": {
            "好奇": [["行为:追问", 0.30]],
            "开心": [["行为:分享", 0.25]],
            "兴奋": [["行为:分享", 0.20]],
            "焦虑": [["行为:确认", 0.20]],
        },
        # 最小行动本体（三族起步；成员=既有 行为:*；后续经提议通道增长）
        "hierarchy": [
            ["行动族:社交表达", "包含", ["行为:分享", "行为:共情",
                                         "行为:延续", "行为:追问"]],
            ["行动族:信息获取", "包含", ["行为:探索", "行为:详述"]],
            ["行动族:自我维护", "包含", ["行为:沉默", "行为:收尾"]],
        ],
        "hierarchy_weight": 0.30,
        # 生命周期数值规则（proposed→observed→established→weakened）
        "lifecycle_promote_touches": 5,      # proposed 被考虑/执行 N 次→observed
        "lifecycle_establish_evidence": 4,   # observed+pair证据≥N→established
        "lifecycle_weaken_days": 14,         # 长期未触及降级
    },

    # 认知网络场：连续值 + 惯性（slew 限幅、非对称 rise/fall）+ 软互抑。
    # inputs 前缀: tension.* / drive.* / context.* / need.* / base（权重可负）。
    # 未来 Salience/Social/Memory 网络 = 在这里加一个 spec，代码零改动。
    "networks": {
        "field": {
            "CEN": {
                # `node` = 图上的网络标记节点（R2 P9：调制器 →网络 的边按它认领目标，
                # 与 drive_field.drives[*].node 同一套约定；没有这行就只能靠字符串猜）
                "node": "CENetwork",
                "inputs": {"base": 0.10, "context.task_engaged": 0.45,
                           "tension.unfinished_goal": 0.25,
                           "tension.blocked_action": 0.18,
                           "tension.discrepancy": 0.05,
                           "drive.learning": 0.15,
                           "tension.social_absence": -0.10,
                           "context.time_since_task": -0.25},
                "rise": 0.30, "fall": 0.10, "slew": 0.12,
            },
            "DMN": {
                "node": "DMNetwork",
                "inputs": {"base": 0.35, "context.task_engaged": -0.55,
                           "context.time_since_task": 0.35,
                           "tension.social_absence": 0.20,
                           "tension.negative_experience": 0.15,
                           "tension.mood_low": 0.10,
                           "tension.self_contradiction": 0.20,
                           "tension.novelty": 0.20,
                           "tension.unresolved_interest": 0.25,
                           "tension.unfinished_goal": -0.10,
                           "tension.blocked_action": -0.05,
                           "drive.curiosity": 0.10},
                "rise": 0.12, "fall": 0.22, "slew": 0.08,
            },
            "_lateral": {"CEN": {"DMN": 0.25}, "DMN": {"CEN": 0.20}},
        },
    },

    # 调制层：effective = clamp(baseline × Π(1+Σ effects))，EMA 平滑。
    # effects 信号键：network.* / tension.* / drive.* / hormone.*(基线偏移) /
    # context.* / constant。baseline_from 引用现有 config 值（装配时解析）。
    "modulation": {
        "smoothing_alpha": 0.30,
        "params": {
            # 扩散：CEN 浅聚焦（深度↓阈值↑），DMN 深联想（深度↑配比↑）
            "diffusion.max_depth": {"baseline_from": "max_depth",
                                     "min": 1.0, "max": 6.0,
                                     "effects": {"network.CEN": -0.40,
                                                  "network.DMN": 0.60}},
            "diffusion.emission_ratio": {"baseline_from": "emission_ratio",
                                          "min": 0.20, "max": 0.80,
                                          "effects": {"network.DMN": 0.30,
                                                       "network.CEN": -0.25,
                                                       "hormone.dopamine": 0.80,
                                                       "tension.novelty": 0.15}},
            "diffusion.min_spread": {"baseline_from": "min_spread_threshold",
                                      "min": 0.02, "max": 0.15,
                                      "effects": {"network.CEN": 0.60,
                                                   "network.DMN": -0.40}},
            # 语义：factor 是衰减量（keep=1−factor）。CEN 保留任务语境（−），
            # DMN 联想场快速腾位（+），皮质醇加速清空（+）
            "diffusion.inter_round_decay": {
                "baseline_from": "inter_round_decay",
                "min": 0.30, "max": 0.95,
                "effects": {"network.CEN": -0.20, "network.DMN": 0.10,
                             "hormone.cortisol": 0.40}},
            # 既有 update_param_modulation（唤醒/压力→发射增益）收编于此
            "diffusion.param_gain": {"baseline": 1.0, "min": 0.7, "max": 1.6,
                                      "alpha": 1.0,
                                      "effects": {"context.arousal": 0.30,
                                                   "context.stress": -0.15}},
            # 工作记忆/注意力宽度（MODE_CONTEXT_BUDGET 的兑现通道）
            "attention.width_scale": {"baseline": 1.0, "min": 0.6, "max": 1.6,
                                       "effects": {"network.DMN": 0.50,
                                                    "network.CEN": -0.20,
                                                    "drive.curiosity": 0.20}},
            # 检索（episodic/记忆）：DMN 抬权重
            "retrieval.topk_scale": {"baseline": 1.0, "min": 0.5, "max": 2.0,
                                      "effects": {"network.DMN": 0.60,
                                                   "tension.negative_experience": 0.20}},
            "retrieval.reignite_every": {"baseline": 3.0, "min": 2.0, "max": 6.0,
                                          "effects": {"network.DMN": -0.35,
                                                       "network.CEN": 0.45}},
            # LLM：温度/发散许可/mode 微调/预算软系数
            "llm.temperature_answer": {"baseline": 0.70, "min": 0.30, "max": 1.10,
                                        "effects": {"network.DMN": 0.35,
                                                     "network.CEN": -0.25}},
            "llm.temperature_expand": {"baseline": 0.35, "min": 0.10, "max": 0.90,
                                        "effects": {"network.DMN": 0.45,
                                                     "network.CEN": -0.30}},
            "llm.mode_nudge": {"baseline": 0.0, "min": -1.0, "max": 1.0,
                                "effects": {"network.DMN": 0.55,
                                             "network.CEN": -0.45,
                                             "tension.self_contradiction": 0.30,
                                             "drive.consistency": 0.20}},
            "llm.budget_factor": {"baseline": 1.0, "min": 0.6, "max": 1.4,
                                   "effects": {"context.task_engaged": 0.15,
                                                "network.CEN": 0.20,
                                                "network.DMN": -0.15}},
            # 行为竞争：CEN 抬高"行动切换成本"（探索须更大优势才打断）
            "behavior.explore_margin": {"baseline": 0.15, "min": 0.0, "max": 0.40,
                                         "effects": {"network.CEN": 0.40,
                                                      "network.DMN": -0.20,
                                                      "drive.curiosity": -0.30}},
            "behavior.exploration_rate": {"baseline": 1.0, "min": 0.4, "max": 1.8,
                                           "effects": {"drive.curiosity": 0.40,
                                                        "network.DMN": 0.30,
                                                        "tension.novelty": 0.20,
                                                        "network.CEN": -0.20}},
            # 行动候选：DMN 抬 Top-K 宽度（发散产生），CEN 抬已学倾向边增益
            # （聚焦筛选）——网络态是乘子，不进任何 if 分支
            "action_space.candidate_topk_scale": {
                "baseline": 1.0, "min": 0.6, "max": 1.8,
                "effects": {"network.DMN": 0.50, "network.CEN": -0.20,
                             "tension.novelty": 0.20}},
            "action_space.affordance_gain": {
                "baseline": 1.0, "min": 0.6, "max": 1.6,
                "effects": {"network.CEN": 0.40, "network.DMN": -0.25}},
            # 认知环阈值调制（统一认知循环 2026-09-20）：Drive/网络/激素
            # 连续移动 form/express/action 阈值与再点火参数——负效应=更易
            # 形成/开口/行动。"什么都不做"永远是合法出口，阈值只调宽窄。
            "cognition.form_threshold": {
                "baseline": 0.55, "min": 0.35, "max": 0.75,
                "effects": {"drive.curiosity": -0.12, "network.DMN": -0.10,
                             "drive.social": -0.08, "network.CEN": 0.08,
                             "hormone.cortisol": 0.15,
                             "context.fatigue": 0.20}},
            "cognition.express_threshold": {
                "baseline": 0.78, "min": 0.55, "max": 0.95,
                "effects": {"need.social": -0.10, "hormone.oxytocin": -0.12,
                             "drive.social": -0.08, "hormone.cortisol": 0.10,
                             "context.recent_expression": 0.28}},
            "action.score_threshold": {
                "baseline": 0.30, "min": 0.18, "max": 0.50,
                "effects": {"network.CEN": -0.12, "drive.curiosity": -0.06,
                             "drive.social": -0.06,
                             "hormone.dopamine": -0.08,
                             "hormone.cortisol": 0.10,
                             "context.task_engaged": -0.05,
                             "context.user_recent": 0.15}},
            "reactivation.cold_field": {
                "baseline": 0.35, "min": 0.20, "max": 0.60,
                "effects": {"network.DMN": 0.20, "network.CEN": -0.15}},
            "reactivation.boost_scale": {
                "baseline": 1.0, "min": 0.6, "max": 1.6,
                "effects": {"network.DMN": 0.35, "tension.novelty": 0.15,
                             "network.CEN": -0.15}},
        },
    },

    # ------------------------------------------------------------------
    # Embodied State Mapper — 感知事实 → 图谱激活（2026-09-21）
    # 只写激活与压力读数；不做需求 delta（internal_state provider 是单源）、
    # 不产候选（capability_graph/autonomy 负责）。
    # ------------------------------------------------------------------
    "embodied_mapper": {
        "survival_node": "生存需求",
        "threat_radius": 16.0,
        "gain": {"hostile": 1.6, "health": 2.0, "food": 1.2, "lava": 2.2,
                 "resource": 0.8, "night": 0.5},
        "pressure": {"health_critical": 6, "health_worried": 12,
                     "food_worried": 8, "food_critical": 4},
    },

    # ------------------------------------------------------------------
    # Capability Graph — 能力·行动·执行器 三权分立（2026-09-20）
    # 知识在图上：本段是**启动种子表**（能力节点、概念→需要→能力、
    # 能力→实现于→载体、状态→诱发→概念、条件→抑制→能力）；种入后
    # 图是权威真相源，运行期代码只走图检查可达性。skills REGISTRY 退化
    # 为"executor 标识→实现"的极薄索引。enabled=false 精确回滚到
    # autonomy 现行 if 链候选生成。
    # ------------------------------------------------------------------
    "capability_graph": {
        "enabled": True,
        # 抑制生效阈值：条件节点激活 ≥ 此值 → 该能力路径不可达
        "suppression_min_activation": 0.3,
        # 能力缺口信号节点（cognitive）；resolve 失败时点亮 → 喂张力
        "gap_node": "能力缺口",
        "gap_activation": 0.5,
        "gap_decay_note": "capability_gap",
        # 105 个真实技能 → 20 项能力（聚合语义单元；实现索引存节点属性）
        "capabilities": {
            "能力:移动": {"executors": ["walk_to", "run_to", "go_direction",
                                     "jump", "sneak", "descend", "swim",
                                     "turn_to", "maintain_distance",
                                     "recover_stuck"],
                          "carrier": "进入Minecraft世界", "desc": "身体位移"},
            "能力:导航": {"executors": ["navigate_to_entity", "navigate_home",
                                     "return_to_location", "investigate_location",
                                     "explore_area", "explore_direction",
                                     "explore_unknown_region", "revisit_location",
                                     "return_to_known_location"],
                          "carrier": "进入Minecraft世界", "desc": "跨距离寻路"},
            "能力:位置记忆": {"executors": ["mark_location", "remember_location",
                                       "mark_interesting_location"],
                           "carrier": "进入Minecraft世界", "desc": "空间记忆"},
            "能力:实体观察": {"executors": ["inspect_entity", "inspect_area",
                                       "inspect_block", "look_at", "look_at_block"],
                          "carrier": "进入Minecraft世界", "desc": "看清楚在场者"},
            "能力:环境感知": {"executors": ["detect_environment", "detect_hostile",
                                       "detect_cave", "detect_structure",
                                       "detect_nearby_resource", "detect_food",
                                       "monitor_vitals", "detect_inventory_full"],
                          "carrier": "进入Minecraft世界", "desc": "态势察觉"},
            "能力:库存察觉": {"executors": ["inspect_inventory", "count_item",
                                      "find_item"],
                          "carrier": "进入Minecraft世界", "desc": "知道自己有什么"},
            "能力:资源采集": {"executors": ["gather_resource", "gather_stone",
                                       "gather_wood", "chop_tree", "collect_drop",
                                       "collect_dropped_item", "collect_food",
                                       "collect_plant", "pickup_item", "break_block"],
                          "carrier": "进入Minecraft世界", "desc": "把方块变成物品"},
            "能力:农耕": {"executors": ["till_soil", "plant_seed", "harvest_crop",
                                    "replant_crop", "collect_wheat", "collect_carrot",
                                    "collect_potato", "collect_beetroot",
                                    "collect_sugar_cane"],
                          "carrier": "进入Minecraft世界", "desc": "种与收"},
            "能力:制造": {"executors": ["craft_item", "craft_batch",
                                    "open_crafting_table", "smelt_item",
                                    "use_furnace", "furnace_take"],
                          "carrier": "进入Minecraft世界", "desc": "把材料变成工具"},
            "能力:建造": {"executors": ["build_wall", "build_floor",
                                    "build_simple_shelter", "place_torch",
                                    "place_block", "place_named_block", "place_door",
                                    "place_chest", "place_crafting_table",
                                    "place_furnace", "place_bed"],
                          "carrier": "进入Minecraft世界", "desc": "改变世界形状"},
            "能力:容器整理": {"executors": ["chest_store", "chest_take",
                                      "sort_inventory", "drop_item", "equip_item",
                                      "unequip_item"],
                          "carrier": "进入Minecraft世界", "desc": "管理持有物"},
            "能力:装备选择": {"executors": ["choose_best_tool", "choose_food",
                                      "choose_weapon", "equip_weapon", "equip_shield"],
                          "carrier": "进入Minecraft世界", "desc": "挑对的东西用"},
            "能力:战斗": {"executors": ["attack_entity", "attack_animal",
                                    "hunt_animal", "recover_after_combat",
                                    "retreat_from_entity"],
                          "carrier": "进入Minecraft世界", "desc": "武力对抗",
                          "dangerous": True},
            "能力:动物互动": {"executors": ["find_animal", "approach_animal",
                                      "feed_animal", "breed_animal"],
                          "carrier": "进入Minecraft世界", "desc": "与生物打交道"},
            "能力:生存维持": {"executors": ["eat_food", "recover_health",
                                      "seek_safety", "avoid_lava", "escape_water",
                                      "sleep", "retreat"],
                          "carrier": "进入Minecraft世界", "desc": "照顾这副身体"},
            "能力:交互": {"executors": ["interact_with_block",
                                    "interact_with_entity"],
                          "carrier": "进入Minecraft世界", "desc": "使用 things"},
            "能力:跟随": {"executors": ["follow_entity"],
                          "carrier": "进入Minecraft世界", "desc": "陪着某人"},
            "能力:自控": {"executors": ["stop"],
                          "carrier": "进入Minecraft世界", "desc": "停下来"},
            "能力:言语表达": {"executors": ["communicate"],
                          "carrier": "进入Minecraft世界", "desc": "说出想法",
                          "note": "communicate 是具身层的发言口，无文本不编台词"},
        },
        # 新概念（补 12 旧具身概念之缺）：id 大写英文与旧概念同风格；
        # gates=数值条件（读图槽位 value，数据非行为分支）；
        # hints=优先 executor（旧 14 规则的 action_type 白名单契约保持）
        "concepts": {
            "RECOVER": {"name_zh": "恢复", "requires": ["能力:生存维持"],
                         "hints": ["recover_health"], "priority": 0.85,
                         "urgency": 0.9, "motivation": "survival",
                         "gates": [{"node": "Haru的血量", "op": "<=", "v": 10}]},
            "EAT": {"name_zh": "进食", "requires": ["能力:生存维持"],
                     "hints": ["eat_food"], "priority": 0.8, "urgency": 0.7,
                     "motivation": "survival",
                     "gates": [{"node": "Haru的饥饿", "op": "<=", "v": 10},
                                {"node": "Haru的血量", "op": ">=", "v": 10}]},
            "FORAGE": {"name_zh": "觅食", "requires": ["能力:资源采集"],
                        "hints": ["collect_food"], "priority": 0.7,
                        "motivation": "survival",
                        "gates": [{"node": "Haru的饥饿", "op": "<=", "v": 12}]},
            "RETREAT": {"name_zh": "撤离", "requires": ["能力:生存维持"],
                         "hints": ["retreat"], "priority": 0.85, "urgency": 0.9,
                         "motivation": "survival", "preempts": True,
                         "gates": [{"op": "hostile_within", "v": 15},
                                    {"node": "Haru的血量", "op": ">=", "v": 8}]},
            # 具身图谱化 2026-09-21：攻击不再被 dangerous 标记封杀候选——
            # "何时值得反制"是认知（gates：贴脸+自身状态良好），
            # "能不能打"是 Safety Kernel（目标必须有图上危险因果边）。
            "ATTACK": {"name_zh": "反制", "requires": ["能力:战斗", "能力:装备选择"],
                        "hints": ["attack_entity"], "priority": 0.6,
                        "urgency": 0.6, "motivation": "survival",
                        "gates": [{"op": "hostile_within", "v": 8.0},
                                   {"op": "health_above", "v": 14}]},
            "OBSERVE": {"name_zh": "视察",
                         "requires": ["能力:实体观察", "能力:环境感知"],
                         "hints": ["inspect_entity", "inspect_area"],
                         "motivation": "curiosity",
                         "gates": [{"op": "unknown_present"}]},
            "EXPLORE": {"name_zh": "探索", "requires": ["能力:导航"],
                         "hints": ["explore_area", "explore_direction"],
                         "priority": 0.35, "motivation": "curiosity", "gates": []},
            "CONVERSE": {"name_zh": "攀谈", "requires": ["能力:言语表达"],
                          "hints": ["communicate"], "motivation": "social",
                          "gates": [{"op": "players_present"}]},
            "BUILD": {"name_zh": "建造", "requires": ["能力:建造"],
                       "hints": ["build_simple_shelter"], "gates": []},
            # 具身图谱化 2026-09-21：生存/拾取由"生存需求/地面物品"状态
            # 经诱发边驱动（EmbodiedStateMapper 写激活），不是 hp 阶梯裁判
            "SEEK_SAFETY": {"name_zh": "寻安全",
                             "requires": ["能力:生存维持"],
                             "hints": ["seek_safety"], "priority": 0.9,
                             "urgency": 0.9, "motivation": "survival",
                             "preempts": True,
                             "gates": [{"node": "Haru的血量", "op": "<=",
                                         "v": 12}]},
            "PICKUP": {"name_zh": "拾取", "requires": ["能力:资源采集"],
                        "hints": ["collect_dropped_item"], "gates": []},
            # P3/§8：制作概念的候选来自**配方回包写的图状态**
            # （可制作物品 hub，见 prior_knowledge.note_craftable），
            # gates 是"此刻背包真有能做的东西"——查图不查攻略表。
            "CRAFT": {"name_zh": "制作", "requires": ["能力:制造"],
                       "hints": ["craft_item"], "priority": 0.45,
                       "motivation": "resource_opportunity",
                       "gates": [{"op": "node_active", "node": "可制作物品",
                                   "v": 0.05}]},
        },
        # 旧 12 概念的能力归属（只挂真实存在的能力；inline/缺口概念跳过）
        "concept_requires": {
            "MINE": ["能力:资源采集", "能力:装备选择"],
            "FOLLOW": ["能力:跟随"], "APPROACH": ["能力:导航"],
            "MOVE": ["能力:移动"], "JUMP": ["能力:移动"],
            "STOP": ["能力:自控"], "ATTACK": ["能力:战斗"],
        },
        # 诱发种子：世界状态节点 → 行动概念（可发现性；概念再沿 需要→
        # 能力→executors 找到现实接口。行为知识在图上，不在 Python）
        "affordances": {
            "Haru的血量": [["RECOVER", 0.6]],
            "Haru的饥饿": [["EAT", 0.6], ["FORAGE", 0.4]],
            "附近的生物": [["RETREAT", 0.5], ["ATTACK", 0.35]],
            "附近的玩家": [["FOLLOW", 0.45], ["CONVERSE", 0.3]],
            "未知信息": [["OBSERVE", 0.5], ["APPROACH", 0.45]],
            "Haru的位置": [["EXPLORE", 0.25]],
            # 资源机会的图路径：可见的可采物种（mapper 激活）经
            # 属于→可采资源 类节点汇入，类节点诱发采集概念——
            # "看见木头想采"是图上因果链，不是事件 if 表
            "可采资源": [["MINE", 0.45]],
            # 具身图谱化（2026-09-21）：生存压力/掉落物是图状态节点，
            # 它们对行动概念的"诱发"是可发现性（认知慢想时也能自然长出
            # 生存候选），高紧迫时由 priority/urgency 概念数据走调度。
            "生存需求": [["SEEK_SAFETY", 0.55], ["RETREAT", 0.50],
                         ["RECOVER", 0.40], ["EAT", 0.35],
                         ["FORAGE", 0.30]],
            "地面物品": [["PICKUP", 0.6]],
            "建筑结构": [["OBSERVE", 0.4]],
            # P3/§4-5/§8 先验层接通：探索缺口存在（open_gap 抬 hub 激活）
            # → EXPLORE 被点亮，"有东西没搞懂"本身驱动出门看看；对象专属
            # 的观察候选在 autonomy._gap_candidates（可见性核验在那）。
            # 可制作物品 hub 由配方回包写入 → CRAFT 进候选，
            # "材料变成果"的通道在图上，不在 if 分支里。
            "探索缺口": [["EXPLORE", 0.25]],
            "可制作物品": [["CRAFT", 0.5]],
            # 注：资源采集候选留在事件机会层（"发现→值不值得挖"是
            # opportunity 策略，不是状态诱发）；MINE 概念仍可被
            # 未知信息/手持物等通路点亮，接口经能力:资源采集。
        },
        # Drive → 具身行动概念（接通旧概念"认知不可达"的断点：
        # 内驱压力沿 驱动 边点亮概念，概念沿 需要 边找能力）
        "drive_concept_edges": {
            "CuriosityDrive": [["OBSERVE", 0.4], ["APPROACH", 0.35],
                                ["EXPLORE", 0.3],
                                # 屏幕永远在场：好奇心不依赖 MC 桥连接态
                                # （2026-09-22，执行链见 eye/observer.py）
                                ["看屏幕", 0.28]],
            "SocialDrive": [["FOLLOW", 0.35], ["CONVERSE", 0.3]],
        },
        # 条件→能力 抑制（保守起步；机制由单测覆盖，随证据增长）
        "inhibitions": [["附近的生物", "能力:建造", 0.5],
                         ["附近的生物", "能力:农耕", 0.4]],
    },

    # ── 先验知识层（P3/§2-5）：先验概念由 prior_knowledge.ensure_prior
    #    启动期幂等种入；探索缺口 = 已知对象∧未知用途，有界且随真实反馈
    #    开启/关闭。零 LLM（该层被 test_no_llm_on_fact_path 钉死）。
    "prior": {
        "enabled": True,
        "max_gaps": 24,               # 活缺口上限（超出按最旧退役，不删节点）
        "gap_activation_step": 0.6,   # 每开一个缺口对聚合节点的激活抬升
        "gap_close_decay": 0.4,       # 每关一个缺口的激活回落
        "gap_resurface_min_s": 1800,  # abandoned 缺口重开最小间隔（=一个目标生命周期）
        "gap_max_reopen": 2,          # 同一缺口重开次数上限（有界重试，非永久禁令）
        "gap_attention_floor": 0.9,   # 活缺口目标存在期间缺口节点的注意力地板（§9）
        # ── 探索实验通道（2026-09-23 §3/§13）："对象可被试着操作"是通用
        # 生活先验，不是环境知识：任何已知物品都能试"放置/持握"，执行侧
        # 如实成败（失败=条件性负证据，成功=用途知识→既有 close_gap）。
        # 表里**没有任何具体物品的答案**（没有"小麦→种植"）——那是经验/
        # 配方通道的产物。{obj} = 缺口对象裸名。
        "gap_trials": [
            {"trial": "place", "skill": "place_block",
             "params": {"item": "{obj}"},
             "why": "物体一般可以尝试放出去看会发生什么"},
            {"trial": "equip", "skill": "equip_item",
             "params": {"item": "{obj}"},
             "why": "物品一般可以尝试拿到手里"},
        ],
    },

    # ------------------------------------------------------------------
    # Cognitive Loop — 统一内部认知循环（2026-09-20 重构）
    # 原则：图谱是认知 substrate；activation→focus→intention→(表达|行动)
    # 是同一系统的不同阶段，不各建第二套大脑。"什么都不产生"是合法输出。
    # ------------------------------------------------------------------
    "cognitive_loop": {
        # ── 重新点亮：全图 eligible 竞争，不是随机 episodic 回忆 ──
        "reactivation": {
            # eligibility 分量权重（数据表，可被图谱参数/学习调整——
            # 不是写死的唯一公式）
            "weights": {"importance": 0.25,      # node.weight 长期重要性
                         "recency_gap": 0.20,    # 离开认知场的时长
                         "field_coupling": 0.25, # 与当前激活区域的边连接
                         "drive_tie": 0.15,      # Drive/情绪节点的边亲和
                         "novelty": 0.10,        # 组合新颖（未近点过）
                         "noise": 0.15},         # 探索噪声（走神的权利）
            "half_life_s": 900,             # recency_gap 饱和半衰
            "cold_field_activation": 0.35,  # top-k 平均激活低于此 = 场过冷 → 需要火种
            "min_interval_ticks": 2,        # cooldown 下限（防每 tick 连点，非闹钟）
            "count": 4,                     # 每轮最多点亮数（计算限制）
            "boost": 1.2,
            "recent_penalty": 0.6,          # 近期点亮过的惩罚乘子
            "recent_window_s": 180,
        },
        # ── 意图种类亲和度：focus 信号 → 意图 kind 的加权和（affinity 表）。
        #    kind 不是封闭 enum——新增类型只加一行；未列信号按 0 参与 ──
        "intention_kinds": {
            "communication": {"signals": {
                "social_relevance": 0.45, "user_relevance": 0.40,
                "emotion_present": 0.20, "drive.social": 0.35,
                "network.DMN": -0.15}},
            "exploration": {"signals": {
                "drive.curiosity": 0.50, "unknown_present": 0.35,
                "novelty": 0.10, "network.DMN": 0.25,
                "social_relevance": -0.20}},
            "action": {"signals": {
                "world_resource": 0.30, "world_hostile": 0.35,
                "goal_present": 0.30, "drive.learning": 0.25,
                "network.CEN": 0.25, "social_relevance": -0.10}},
            "reflection": {"signals": {
                "tension.self_contradiction": 0.45,
                "drive.consistency": 0.45, "network.DMN": 0.20}},
            "attention_shift": {"signals": {
                "salience": 0.35, "novelty": 0.30, "network.DMN": 0.30,
                "field_change": 0.20}},
        },
        "max_active_per_kind": 2,      # 同类意图并发上限（满了 → 竞争失败合法）
        # focus 质量分（不是"值不值得跟用户说"——那是表达阶段的事）
        "focus": {"topk_base": 15, "weights": {
            "concentration": 0.60,    # 焦点强度（主导项：够亮才谈得上形成）
            "coherence": 0.15,        # 内部边凝聚（成簇为加成）
            "continuity": 0.08,       # 与上一脉冲焦点的重叠（持续性）
            "novelty": 0.10,          # 新组合加成
            "drive_support": 0.07}},  # Drive/情绪在焦点中的存在感
        # 脉冲触发：状态变化驱动 + 时间兜底（不是固定闹钟）
        "pulse_gate": {"min_field_change": 0.8, "max_interval_s": 25},
        # ── 反思压力：pressure 驱动离线自问，时间只是 cooldown ──
        "pressure": {
            "node": "反思压力",
            "threshold": 1.2,          # 节点激活 ≥ 此值 → 反思（图衰减自然消退）
            "sources": {               # 各信号源注入量（可学习/配置调整）
                "action_failure": 0.25,
                "negative_feedback": 0.30,
                "prediction_error": 0.15,
                "intention_lost": 0.20,
                "novel_experience": 0.05},
            # 焦点"新到什么程度"才算一次新经历（R2 P11 闭 D-10：`novel_experience`
            # 这一档金额一直在表里，但全仓没有 emit 点 ⇒ 这个来源不存在）。
            # 判据 = 已经算好的 `novelty = 1 − 连续性`（本拍焦点集合与上一拍的
            # Jaccard 重叠），不为它另起一套新颖度算法。0.5 ⇒ 上一拍看到的底子
            # 至少一半换成新的才算。
            "novel_min_novelty": 0.5,
            "max_inject": 2.5,
            "cooldown_s": 600,
        },
    },

    # ------------------------------------------------------------------
    # Action System — 认知层 Capability（Phase 4 MVP）
    # ------------------------------------------------------------------
    "action_system": {
        "ask_threshold": 1.5,          # CuriosityDrive 激活度 >= 此值触发 Ask
        "suggest_min_pref_confidence": 0.6,  # 最低偏好置信度
        "suggest_min_drive": 0.1,      # 最低 Drive 激活度
        "cache_ttl_seconds": 3.0,      # 评估缓存时间
        # ── Action 节点系统（具身改造 2026-09）──
        "interrupt_margin": 0.12,      # 打断当前动作所需的优先级差
        "emergency_health": 8,         # 血量低于此 → 生存紧急动作（撤离）
        "danger_distance": 5.0,        # 敌对生物此距离内 → 紧急中断评估
        "max_goal_queue": 12,          # 复杂指令目标队列上限
        # ── 技能层参数（skills/ 消费）──
        "combat_retreat_health": 8,    # 战斗中血量低于此 → 撤退
        "combat_max_duration_s": 45,   # 单场战斗时长上限
        "navigate_timeout_s": 90,      # 单段寻路超时
        "follow_timeout_s": 600,       # 跟随持续上限
        "explore_max_distance": 64,    # 单次探索距离上限（格）
        "explore_max_time_s": 300,     # 单次探索时长上限
        "explore_step": 16,            # 探索路点间距
        "explore_stall_legs_max": 3,   # 连续失败腿数达此值且全程零位移 → 快败
                                       # （2026-09-25 真机：原地撞满 max_time，
                                       # 4 分钟零位移才收场）
    },

    # ------------------------------------------------------------------
    # 输入复杂度自适应（具身改造 2026-09）
    # ------------------------------------------------------------------
    # Level1（短输入/低复杂度）走短 prompt + 快速小模型 + 一次调用完成
    # 语义解析与决策要素；输出仍进同一条图谱激活/行为竞争管路。
    # enable=False 时全部输入走完整解析（旧行为）。
    "cognitive_complexity": {
        "enable": True,
    },

    # ------------------------------------------------------------------
    # Exploration / Curiosity — 探索行为（好奇心机制重构 2026-09）
    # ------------------------------------------------------------------
    # 架构语义：未知 ≠ 好奇；好奇 ≠ 提问；好奇 ≠ 一次性任务。
    # 未知/不确定性只作为产生探索信号的输入之一，经调制后注入图谱，
    # 由激活扩散形成 CuriosityDrive，再与 respond/silence 等行为统一竞争。
    # 提问（explore_ask）与搜索（explore_search）只是探索的两种出口。
    "curiosity": {
        # ── 检测调制（信号注入强度 = raw × (floor + (1-floor)×mod)）──
        "mod_floor": 0.4,               # 调制下限：再冷的信号也保留一点进入扩散
        # 场合系数：什么对话行为下概念类探索信号可以入场（1.0=正常，0=抑制）
        # sharing/information_statement 降低而非清零——"好奇但不问"是合法状态，
        # 是否打断交给行为竞争，不在检测层一刀切
        "occasion_factor": {
            "question": 1.0, "request": 1.0, "curiosity_expression": 1.0,
            "sharing": 0.5, "information_statement": 0.5, "opinion": 0.4,
            "answer": 0.6, "suggestion": 0.4, "comfort": 0.3,
            "agreement": 0.2, "backchannel": 0.1,
            "greeting": 0.0, "farewell": 0.0, "apology": 0.2,
            "thanking": 0.2, "emotion_expression": 0.0,
        },
        # ── 候选评分权重（全部归一化 [0,1]，改这张表=改"她更看重什么"）──
        "weights": {
            "drive": 0.40,              # CuriosityDrive 激活强度
            "interest": 0.25,           # 该目标的历史兴趣水位
            "signal": 0.20,             # 本轮 discrepancy 信号强度
            "novelty": 0.15,            # 图外/未知目标加分
            "satiation": 0.35,          # 最近已被回答/探索过的惩罚
            "searchability": 0.25,      # explore_search 专用：目标可搜索性
            "cost": 0.15,               # explore_search 专用：搜索成本
        },
        # 候选资格与胜出
        "explore_min_score": 0.35,      # 低于此分的候选不入场（好奇但不行动，合法）
        "explore_win_threshold": 0.55,  # 竞争中胜出所需最低分
        "explore_margin": 0.05,         # 需超出 respond 欲望的幅度（不打断正事）
        # drive 归一化：CuriosityDrive 激活值除以此值映射到 [0,1]
        "drive_norm": 3.0,
        # ── pending inquiry（待处理探索提问，可忽略、可过期）──
        "inquiry_timeout_s": 900,       # 超时自动失效（兴趣保留，不算被回答）
        # ── 兴趣水位（挂在目标节点 extra_attrs.interest）──
        "interest_signal_gain": 0.25,   # 每次信号命中 +level
        "interest_resolve_decay": 0.4,  # 被回答后 level × 此值（衰减不清零）
        "interest_ignore_decay": 0.85,  # 被忽略后 level × 此值
        "interest_min_for_ask": 0.15,   # 低于此水位的目标不值得追问
        # ── explore_search（论文 §在线知识扩展：稀疏邻域 → 外部查询）──
        "sparse_neighbor_threshold": 3, # 目标邻居数低于此 = 稀疏 = 可搜索
    },

    # ------------------------------------------------------------------
    # Disposition 学习率（人格学习架构改造 2026-09）
    # ------------------------------------------------------------------
    # 行为倾向更新不再直接吃 positive/negative：
    #   learning_signal = Σ ( valence_i × 权重_i × LEARN_BASE ) × 激素调制
    # 来源权重：**自我反馈高于社会反馈**（人格不能主要由用户满意度塑造）。
    "disposition_learning": {
        "weights": {"social": 0.4, "self": 0.6},
        "learn_pos": 0.25,        # 正向 valence→强度 的基线斜率
        "learn_neg": 0.40,        # 负向斜率（比旧 −0.12 固定步长更温和：
                                  #   拒绝≠讨厌，§九）
        "activation_blend": 0.15, # 行为节点实时激活混入有效强度的系数
    },

    # ------------------------------------------------------------------
    # 认知动力学调制（论文 V2 补账 2026-09-20）
    # ------------------------------------------------------------------
    # Hebbian 共激活边强化：w += ε·a(u)。白名单只放知识/情绪类关系——
    # cognitive_relation（含 disposition 的先验/激活/pair 边）永不被运行时
    # 改写，人格学习只走 disposition 通道。max_gain=单边相对首见基线 +50% 封顶。
    "hebbian": {
        "enabled": True,
        "epsilon": 0.0015,
        "max_gain": 0.5,
        "categories": ["semantic_relation", "emotional_relation"],
    },
    # 软遗忘：last_access 驱动"可达性"衰减（数据保留、权重不改、
    # 触碰即恢复）。self 空间/基础设施/程序性/disposition/intention 豁免。
    "soft_forgetting": {
        "enabled": True,
        "grace_days": 2,        # 宽限期：两天内访问过 = 完全可达
        "decay_per_day": 0.06,  # 每多闲置一天，种子强度 -6%
        "floor": 0.25,          # 最低仍保留 25% 可达性（不是删除）
    },
    # 参数调制：情绪唤醒 → 扩散增益；压力 → 收敛（β 乘子，界 [0.7,1.6]）
    "param_modulation": {
        "enabled": True,
        "arousal_gain": 0.3,
        "stress_damp": 0.15,
    },

    # 情景记忆生命周期
    "episodic_retention_days": 30,
    # （2026-09-22 收尾清理：删除 cognitive_cleanup_threshold，零读取）

    # ------------------------------------------------------------------
    # Self Model — 持续性主体模型（Phase 1）
    # ------------------------------------------------------------------
    # 重要性评估权重：决定候选更新是否写入 Self Graph
    "self_model": {
        # 写入门槛：importance_score >= threshold 才写入
        "importance_threshold": 0.5,

        # 重要性评分因子权重（总和 = 1.0）
        "importance_weights": {
            "explicit_statement": 0.40,   # 用户明确说"我喜欢/我相信/我是"
            "repeat_count": 0.25,         # 相似内容重复出现次数
            "emotion_intensity": 0.15,    # 当前激活情绪节点数 × 激活度
            "llm_confidence": 0.10,       # LLM 抽取时的置信度
            "temporal_recency": 0.10,     # 最近一次强化距今 (30天内线性衰减)
        },

        # 最小观察次数：source="single_observation" 需要 >= min_observations 才能写入
        "min_observations": 2,

        # confidence 增量：已有 belief/preference 被再次证实时增加此值
        "confidence_reinforce_delta": 0.05,

        # confidence 上限
        "confidence_max": 0.95,

        # 新 belief/preference 初始 confidence
        "confidence_initial": 0.55,

        # 社交/情绪表达的权重衰减（这些对话行为下重要性降低）
        "social_dialogue_weight_penalty": 0.4,
    },

    # ------------------------------------------------------------------
    # 前端显示过滤
    # ------------------------------------------------------------------
    # 是否隐藏自动生成的临时节点（如 思考_xxx, ear_text_xxx, UnknownObjectxxx）
    "hide_temp_nodes": True,

    # 临时节点 ID 匹配正则（不区分大小写）
    # 默认匹配：思考_数字 / 记忆_数字 / ear_text_数字 / UnknownObject数字
    "temp_node_pattern": r"^(思考|记忆|反思|情绪)_\d+|ear_text_\d+|UnknownObject\d+$",

    # ------------------------------------------------------------------
    # 学习实验模式（2026-09-25 §16/§20；消费者=experiment_mode.py 头注释）
    # mode=learning_closed_loop 时：全部拟人调制通道按 shield 表屏蔽
    # （经现成消音语义/一行级门，不删数据不改拓扑），注入结构化目标，
    # 打横幅。mode=off 时本段除 goal/prior 数据外无任何效果。
    # ------------------------------------------------------------------
    "experiment": {
        "mode": "off",                    # "learning_closed_loop"/"sandbox" 激活
        "experiment_id": "",              # 实验标识；空=启动时自动生成
        "seed": None,                     # 实验 random seed（None=不播种）
        "goal_obtain": "iron_ingot",      # 实验目标：自主获得铁锭
        "attention_floor": 0.9,           # 目标物品节点的注意地板（§9 反向驱动）
        "shield": {                       # True=屏蔽该通道（实验开启时默认全True）
            "hormone_to_params": True,        # ① tonic 读数不注册 ⇒ hormone.*≡0
            "graph_projection": True,         # ② 图投影/偏置/心情投影/漂移 旁路
            "graph_pulse": True,              # ③ phasic→图激活
            "drift": True,                    # ④ need/昼夜漂移速率=0
            "modulation_events": True,        # ⑤ 调制事件扇出入声
            "internal_state_heartbeat": True, # ⑥ cc 三个写入节拍跳过（钉值）
            "world_events_to_needs": True,    # ⑦ 世界事件→需求 写跳过
            "mood": True,                     # ⑧ 心情：投影不注入+afterglow 旁路
            "social_drive": True,             # ⑨ SocialDrive 关+social 候选过滤
            "personality_tendency": True,     # ⑩ tendency 权重0+学习调制中性
            "cognition_llm": True,            # ⑪ 自发表达/思考/反思/追问 LLM 关
            "idle_companion": True,           # ⑫ 空闲陪伴表达（cc 表达节拍）
            # ⑬ 定向探索的昼夜拦截降级（夜里也去找目标）。接线=explore 候选
            # 的 night_stop 参数（skills/exploration.py 既有语义），只作用于
            # 目标导向搜索；danger（敌对生物 8 格内）照停，日常好奇探索不吃这条。
            "night_preempt": True,
        },
        # 运行时数据缺口的人工先验（§5/§10：26.1 的 minecraft-data 没有
        # 熔炉配方——已离线核实；这一行是唯一"未经版本验证"的先验，
        # provenance 如实标注，实验中的真机熔炼=把它升格为 observed）。
        "prior_extra": [
            {"result": "iron_ingot", "kind": "smelt",
             "inputs": {"raw_iron": 1}, "fuel": True, "tool": "furnace",
             "provenance": {"knowledge_type": "prior",
                            "source": "manual:vanilla-furnace-mechanics",
                            "version_verified": False, "confidence": 0.4}},
        ],
    },

    # ======================================================================
    # 行动侧事件框架（C21 生产化，2026-09-28）
    # ======================================================================
    # 论文愿景 §552-562"事件帧调制认知"的生产落地：失败/成功结算落图为
    # 签名级事件框架节点（行动经验:，episodic）——[涉及] 极性槽位边压/推
    # 对象实体、[结果] 正权边撑 total_w>0 搭发射循环，经扩散统一抑制机制
    # （diffusion_engine.py:1023，负权边抑制发行条件）调制后续候选注意。
    # 沙箱实证：实验 D C20e 5/5 行为翻转、C20g 真实失败回执（timeout 暂态，
    # 统计/冷却/因果通道全静默）内源落图独力承担 17→15 差分——失败**不按
    # 暂态集合豁免**，图侧是统计通道静默时唯一留痕通道。
    # enabled=false → 精确回滚：零落图零点火（modulator_system/hebbian 同款
    # 门先例）。ignition=false 时仍落图但不起火（纯存档）。
    "experience_eventframe": {
        "enabled": False,             # 主门：False=零落图零点火（精确回滚）
        "success_side": True,         # 成功结算也落框架（正极性涉及边）
        "failure_polarity": -0.8,     # 失败 涉及 边权重（负=抑制实体，实验 D 值）
        "success_polarity": 0.5,      # 成功 涉及 边权重（正=强化实体，同实例节点同档）
        "result_polarity": 0.9,       # 结果 边权重（恒正，撑 total_w>0，实验 D 值）
        "reign_amt": 0.8,             # 每次结算点火增量（受 activation_cap 钳制）
        "activation_cap": 3.0,        # 事件节点激活上限（=episodic 空间 cap）
        "ignition": True,             # 结算点火；False=只落图形状（纯存档模式）
        "retire_unknown": False,      # C20c 附件：失败时退休已存在 Unknown* 节点
        "suppress_gap_anchor": False, # C20e 蓝图①附件：涉及 边兼指 缺口:用途({obj})
        "flip_cooldown_s": 0,         # 极性翻转冷却（0=关；v1 语义=最新一次结算定权）
    },
}
