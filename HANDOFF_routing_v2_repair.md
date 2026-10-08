# HANDOFF — core_routing 装配修复（v2）

**暂停时间**：2026-09-30 凌晨。**恢复点：Step 3 收尾。**

---

## 一、已完成（Step 1–2 全部完成）

### Step 1：真实 API 定位（结论，免重查）
| 项 | 结论 |
|---|---|
| 建边 API | `kg.add_edge(Edge(src,dst,relation,weight), force_weight=False) -> bool`；**要求两端节点已存在**，relation 经 `graph_schema` 归一 |
| 生产建图路径 | `mc_knowledge.ensure_mc_world(kg, config)` + `world_prior.build_recipe_closure(kg, engine, target, bridge)`（bridge = 执行 `sandbox_lab.install_fake_bridge(world)` 后的 `minecraft.bridge` 模块）→ 世界表驱动，**非任务硬编码** |
| `activate_from_inputs(node_ids, edge_specs, ...)` | **仅激活已存在的节点与边**（`existing_edge = kg.get_edge(...); if existing_edge:`）——不创建边。这是 R1 的机制根因 |
| `analyze_cognitive_demand(...)` 返回 | **dict**：`demand / gap / resource / mode / legacy / legacy_score / reasons / explain / why`（v1 误用 `out[0]` → KeyError: 0） |
| 边方向默认语义 | 全部 **forward**（src→dst），仅少数对称关系（涉及/关于/参与）为 bidirectional。→ 注入实体若无出边则不传播（配方图里 `物品:X` 是汇点，这是 preflight 只点亮 1 节点的原因） |
| 四条件语义 | `fas_full`=全机制 / `D-noact`=关扩散 / `D-nodemand`=关 demand 注解 / `D-flat`=同图扁平文本相似度（无激活无传播） |

### Step 2：v2 harness 已写完并通过自检
文件：**`scripts/run_exp_routing_v2.py`**（v1 `scripts/run_exp_routing.py` 原样保留存档）

修复内容：
- **R1**：`FASContext.__init__` 用生产路径播种真实图（37 节点/20 边）；`ingest` 用 `kg.add_edge` 真建**观察共现边**（实体↔实体、实体↔动作、动作↔结果）；每次图变更后 `_sync_index()` 刷新 `eng.name_to_node`
- **R2**：`demand_block()` 按真实 dict schema 取键，缺键即抛 `CoreModuleError`；**核心环节（graph/diffusion/demand/gap/routing/context）异常一律向上抛，不吞**
- **R3**：主循环改为 obs → **ingest** → dynamics → context → decision → act
- **统一序列化**：`serialize_context()`，四条件同一 schema，仅 `mode` 字段与被研究机制不同
- **preflight**：图有边 + 传播自证 + demand schema 自检，失败即 `InvariantViolation`
- **invariant fail-fast**：`graph_edges>0`、`core_exceptions==0`、`step0_context` 非空
- 逐决策 telemetry：graph_nodes/graph_edges/activated_nodes/activated_edges/demand/gap/routing/exception_count

**preflight 已 PASS**：`{graph_nodes:37, graph_edges:20, propagation_newly_lit:1, demand_keys:[action,curiosity,emotion,knowledge], gap_type:dict, routing_mode:graph_only}`

**单条 probe 已 PASS**（T1 seed1，四条件）：edges 30–37（原 **0**）、activated_edges 6–10.5（原 0）、step0 3–8 节点（原 **0**）、核心异常 **0**
**消融有效性已从 telemetry 证明**：fas_full demand 2/2 在场 vs D-nodemand **0/2 缺席**；激活值多样性恢复（6 个不同值 vs v1 的 1.9）

---

## 二、未完成（明早从这里继续）

### 1. 收尾 smoke test（**只差 2 个 run**）
`D-flat` 跑到 **10/12**（每 run 约 3 分钟，因 `flat_focus` 每次新建 `EmbeddingProvider` 重复加载模型——已知效率问题，非正确性问题，**本轮不改**）。

```
cd E:/Project/Fascinator
# 检查：应显示 12/12 × 4
for c in fas_full D-noact D-nodemand D-flat; do echo "$c: $(grep -c '^\[v2\]' experiments/core_routing_v2_smoke/log_$c.txt)/12"; done
# 若 D-flat 未完成，补跑（会覆盖式重跑 12 个，可接受；或等其自然完成）
E:/Miniforge.envs/Fascinator/python.exe -X utf8 scripts/run_exp_routing_v2.py \
  --conditions D-flat --tasks T1,T2,T3,T4 --seeds 1,2,3 \
  --out experiments/core_routing_v2_smoke/D-flat
```

### 2. 跑七项验证
`experiments/core_routing_v2_smoke/verify_smoke.py` 已写好（只读）。直接运行：

```
E:/Miniforge.envs/Fascinator/python.exe -X utf8 experiments/core_routing_v2_smoke/verify_smoke.py
```
输出覆盖要求的一~七项：Graph / Diffusion / Activation landscape / Demand-Gap-Routing / First decision / Serialization / Ablation validity。

### 3. 合并 summary + 写修复报告
- 各条件已各自输出 `summary.csv`（3 个已落盘；D-flat 待完成）——需要合并成一份总表（可仿 `scripts/merge_routing.py` 写 v2 版，按 condition/task/seed 去重）
- 按用户指定格式输出 **Repair Summary**（Files changed / R1 / R2 / R3 / Serialization / Smoke Test / Invariants / Remaining Issues / Recommendation）

### 4. 完成后**停止**
不跑完整 campaign、不改论文、不删 v1 数据。只输出 `READY FOR FULL CAMPAIGN` 或列出 FAIL 项。

---

## 三、已落盘产物

| 路径 | 说明 |
|---|---|
| `scripts/run_exp_routing_v2.py` | 修复版 harness（唯一新增的生产侧脚本） |
| `experiments/core_routing_v2_smoke/` | smoke 产物：`log_<cond>.txt`、`preflight.json`、`<cond>/raw_results.jsonl`、`<cond>/summary.csv`、`verify_smoke.py` |
| `experiments/core_routing/routing_campaign_v1_invalid_assembly/README.md` | v1 无效声明（v1 数据原样未动） |
| `experiments/core_routing/activation_audit/` | 上一轮只读审计（根因证据） |

## 四、纪律遵守声明
- 未改：diffusion 算法 / activation 公式 / 边权规则 / 抑制竞争 / demand·gap·routing 算法 / 参数 / prompt / 任务定义 / baseline / seed / 统计 / 成功判定
- 未加任何 task-specific 分支；建图完全由世界表与配方表驱动
- v1 数据未删未改；未跑完整 campaign；未改论文
