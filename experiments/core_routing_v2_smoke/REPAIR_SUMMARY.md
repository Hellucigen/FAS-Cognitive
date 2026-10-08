# Repair Summary — core_routing 实验装配修复（v2）

- 日期：2026-09-30
- 性质：**实验装配/接口/序列化/校验修复**。未修改任何 FAS 核心机制（diffusion 算法、activation 公式、边权规则、抑制/竞争、demand/gap/routing 算法）、未调参、未改 prompt/任务/baseline/seed/统计/成功判定、未加任何 task-specific 分支。
- 依据：`experiments/core_routing/activation_audit/diagnostic_report.md`（只读审计，R1/R2/R3 根因）

## Files changed

| 文件 | 变更 |
|---|---|
| `scripts/run_exp_routing_v2.py` | **新增**（v2 harness；v1 `run_exp_routing.py` 原样保留存档） |
| `experiments/core_routing/routing_campaign_v1_invalid_assembly/README.md` | **新增**（v1 campaign 装配无效声明） |
| `experiments/core_routing_v2_smoke/` | **新增**（smoke 产物 + `verify_smoke.py` + preflight.json + summary.csv） |

## R1 Graph

**Before**：edges = **0**（v1 只 append edge_specs；生产 API `activate_from_inputs` 仅激活已有边、不创建边）→ 扩散无结构可传播，Top-K ≈ 直接注入排序（与"扩散关闭"重合 85%）。

**After**（生产建图路径，非任务硬编码）：
- `mc_knowledge.ensure_mc_world()`（世界知识）+ `world_prior.build_recipe_closure(bridge=...)`（配方闭包，sandbox fake bridge 驱动）
- ingest 用 **`kg.add_edge`** 真建观察共现边（实体↔实体、实体↔动作、动作↔结果）
- 图变更后刷新 `eng.name_to_node`（v1 遗漏的接线点）

Smoke 实测（每条件 46–62 决策）：nodes = 42.2–42.7，**edges = 36.1–39.0（min 23）**，**activated_edges mean 14.9–18.5、zero_frac = 0.00**。

## R2 Demand/Gap/Routing

**Before**：`out[0],out[1],out[2]` 对 dict 取整数下标 → `KeyError: 0` → 被 except 吞 → **521/521（100%）** 决策无 demand 输出；`D-nodemand` 与 `D` 实质同条件。

**After**：按真实 dict schema 取键（`out["demand"] / out["gap"] / out["resource"] / out["mode"]`），缺键或非 dict 即抛 `CoreModuleError`；**核心环节（graph/diffusion/demand/gap/routing/context）异常一律显式失败**。

Smoke 实测：demand **60/60, 49/49, 60/60, 62/62**（fas_full / D-flat / D-noact / D-nodemand 按各自语义），gap 与 routing 同为 100%；**core exceptions = 0（全部条件）**。

## R3 First Decision

**Before**：step-0 n_nodes = **0**（80/80；先建 context 后 ingest）。

**After**：顺序改为 obs → ingest → diffusion → context → decision → act。Smoke 实测 step-0 mean_nodes = **3.9 / 3.9 / 4.4 / 8.0**（四条件），empty = **0/53**。

## Serialization

- 统一 schema（`serialize_context`）：**PASS**（四条件 `selected_nodes/selected_acts/graph_*/demand/gap/routing/mode/exception_count` 字段一致；`mode` 字段标明所测机制）
- D vs D-nodemand 区分：**PASS**（telemetry 证明：fas_full demand_present 60/60，D-nodemand **0/62**——v1 时两者恒等）

## Smoke Test

runs = **48**（4 条件 × 4 任务 × 3 seeds），failed = **0**（首轮 3/4 条件即 0 错误；D-flat 因 HF Hub 网络抖动崩 5 run → C35 修复：`HF_HUB_OFFLINE=1` + embedder 进程级单例 → 离线重跑 **12/12 成功**）。

行为概览（smoke 仅为装配验证，非统计结论）：全 48 格 success ≥ 2/3；无任何条件被特殊照顾。

## Invariants

| 不变量 | 结果 |
|---|---|
| graph_edges > 0 | **PASS**（min 23） |
| diffusion_path_exists | **PASS**（preflight 传播探针新点亮 ≥1；activated_edges zero_frac=0） |
| demand API | **PASS**（100%，schema 校验通过） |
| gap API | **PASS**（100%） |
| routing API | **PASS**（100%，mode=graph_only） |
| step0_context 非空 | **PASS**（empty=0/53） |
| serialization 统一 | **PASS** |
| core_exceptions == 0 | **PASS**（0/281 决策） |
| D vs D-nodemand 有效对照 | **PASS**（telemetry 证明） |

## Remaining Issues

1. **传播幅度有限**：preflight 探针在 4 步扩散后仅新点亮 1 个节点。原因有二：(a) `物品:oak_log` 类节点在 forward 方向语义下是汇点（需要/产生/掉落均 src→dst 指向它）；(b) 配方图的连接经由 `配方:*` 中介节点。**这是 FAS 图本体设计的既有事实，本轮按禁令未动**——但它意味着"多跳传播的丰富程度"取决于图的本体结构，值得后续单独研究（记录，不修改）。
2. **D-flat 每决策重新嵌入全图节点**（效率问题，非正确性）——留待正式 campaign 前决定是否缓存。
3. v1 campaign 的 baseline 条件（llm_direct/history/rag/random）本身逻辑未受 R1–R3 影响，但按规范应与修复后的 D 族**同批重跑**后才能配对比较。

## Recommendation

**READY FOR FULL CAMPAIGN**（harness 装配已通过全部 invariant；等待指示后再启动完整 520-run，本轮按令未自行启动）。
