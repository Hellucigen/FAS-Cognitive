# routing_campaign_v1_invalid_assembly — v1 campaign 装配无效声明

**结论：v1 campaign 的 D 族条件（fas_full / D-noact / D-nodemand / D-flat）不可用于评估 FAS 的 routing/diffusion 假设。**

原因（详见 `../activation_audit/diagnostic_report.md`，只读审计）：

1. **R1 实验图谱零边**：v1 runner 只 `append edge_specs`，从未调用 `kg.add_edge`；而生产 API `diffusion_engine.activate_from_inputs` 的语义是"仅激活**已有**边"，不创建边。→ 实验图 nodes≈9–15 / **edges=0**，扩散无结构可传播，Top-K 退化为直接注入排序（与"扩散关闭"重合 85%、与无传播扁平相似度重合 67%）。
2. **R2 demand/gap/routing 零输出**：v1 写 `out[0],out[1],out[2]`，而 `analyze_cognitive_demand()` 返回 dict → `KeyError: 0`，被 `except` 吞成占位文本。**521/521（100%）** 决策命中该错误。→ 被检验的招牌机制从未运行；`D-nodemand` 与 `D` 实质同条件，其"零差分"是恒等式。
3. **R3 首步空焦点**：v1 先建 context 后 ingest → 80/80（4 任务×20 种子）phase-1 step-0 焦点为空。

**处置**：v1 数据**原样保留在此前的目录结构中**（`../fas_full/`、`../ablations/`、`../llm_*/` 等），未删除、未修改，以保留审计与 provenance。本目录仅作**无效声明**，不复制数据。

**修复版**：`scripts/run_exp_routing_v2.py`（装配修复：真建边 / 真实 demand schema + 不吞核心异常 / 先 ingest 后建 context / 统一序列化 / preflight + invariant fail-fast）。其 smoke 验证见 `../core_routing_v2_smoke/`。

**v1 仍然有效的是什么**：baseline 条件（llm_direct / llm_history / llm_rag / random_ctx）不依赖 FAS 图与 demand 层，其内部逻辑未被 R1–R3 影响；但作为**对照**它们只应与修复后的 D 族同批重跑，不应与 v1 的 D 族配对使用。
