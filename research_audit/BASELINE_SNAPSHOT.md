# BASELINE SNAPSHOT — FAS 三层故障归因审计起跑基线

- 快照时间:2026-09-30(审计启动)
- git commit: `4685726`(main 分支 HEAD,`git rev-parse HEAD` 实测)
- git describe: `4685726`(无 tag)
- 工作树:有未提交修改(状态见 git status 快照;审计中新增文件均在 `research_audit/` 与 `experiments/mechanism_falsification/`,与生产代码物理隔离)
- 生产图谱文件:`data/runtime_graph.json`(2026-09-28 14:09 写入)

## 生产图谱基线统计(实测,`runtime_graph.json` 全量扫描)

- nodes: **3648**(declarative-episodic 2335 / declarative-semantic 1035 / procedural 78 / infrastructure 132 / disposition 46 / intention 20 / self 2)
- graph_space: episodic 2252 / semantic 1062 / cognitive 201 / self 133
- edges: **9088**
- edge relation_category:cognitive_relation **4793(52.7%)** / temporal_relation 1730 / episodic_relation 941 / semantic_relation 717 / procedural_relation 507 / causal_relation 300 / social_relation 66 / emotional_relation 34
- **top relation:"涉及" 3634 条(40.0%)**; "时间顺序" 1614; "实施" 525; "关于" 332; "属于" 308
- 负权重边 30 条;max weight 2.816;weight>1 共 124 条
- dangling edges: **0**; isolated nodes: 38
- 密度 0.00137;平均出度 2.49(中位 2,max 305);平均入度 2.49(中位 1,max 982)
- **sink 节点(out-degree=0):513(14.1%)**;source 节点(in-degree=0):702(19.2%)

## 核心引擎基线事实(python 实测 config)

- `DiffusionEngine` 参数:activation_max=5.0, lambda_decay=0.05, beta_spread=1.0,
  max_depth=3, theta_threshold=0.01, min_spread_threshold=0.05, activation_epsilon=1e-4,
  input_similarity_floor=0.5, input_default_bonus=0.5, theta_action=0.5,
  emission_transfer=1.0, emission_ratio=0.5(默认)
- relation_propagation:除 emotional_relation(bidirectional)外全部 **forward**;
  全图仅 8 个关系显式 bidirectional:相关/同一/认识/靠近/关于/涉及/参与/交互
- propagation_rules(beta/lambda/cap 按 space):semantic 1.0/0.05/5.0;
  episodic 0.7/0.15/3.0; cognitive 1.5/0.08/5.0; self 0.5/0.01/5.0
- relation_ontology 规范关系总数 103;fallback 关系 = "关联"

## 测试基线(P0 回归)

- **全部 6 个脚本式回归文件(pytest 不可收集,`sys.exit` 在模块级)**:
  `test_amble_fallback.py` / `test_circadian_modulators.py` / `test_cog.py` /
  `test_mc_approach_race.py` / `test_presence_satisfies.py` / `test_offline_chain_acceptance.py`
  ——**pytest 全量收集直接 INTERNALERROR,SystemExit 中断整个套件,0 测试运行**。
  运行方式:这 6 个文件是独立脚本(`python tests/xxx.py`)。→ **L1-TEST-HARNESS 缺陷(登记)**
- 剩余文件的 pytest 全量结果:见 REGRESSION_RESULTS.md(审计启动时全量跑)

## 既有实验证据(接管,不重跑,不删除)

| 实验 | 目录 | 状态 |
|---|---|---|
| Routing v1 | experiments/core_routing/ | **INVALID ASSEMBLY**(v1 无效声明在 core_routing/routing_campaign_v1_invalid_assembly/README.md) |
| Routing v2 | experiments/core_routing_v2/ | **VALID ROUTING EXPERIMENT**(520/520 harness-valid;T4 19/20 vs llm_direct 15/20;**Bonferroni 后不显著**,方向性证据) |
| Relation direction | experiments/relation_direction_campaign/ | **机制级有效**(Forward-sink 图 vs Bidirectional projection;ρ 0.33→0.65,p=0.002,r=0.886,10/10 seeds;**非行为级 superiority 声明**) |
| Mechanism campaign | experiments/mechanism_campaign/ | 探针:配方图对 forward 扩散不可穿越 |

## 审计产出索引

- `failures` 注册:FAILURE_REGISTER.md
- 理论声明 vs 代码:CLAIM_IMPLEMENTATION_MATRIX.md
- 旧实验有效性:EXPERIMENT_VALIDITY_MATRIX.md
- 修复:阶段 1 修复 + PATCH_LOG.md
- falsification:mechanism_falsification/ 完全独立最小参考模型