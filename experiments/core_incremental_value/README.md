# core_incremental_value — FAS 核心机制收官实验

**问题**: 在相同知识图谱、相同输入、相同候选集、相同信息预算下,spreading activation(FAS)是否比 flat graph retrieval(B2)具有独立的 joint-relevance 判别增量价值?(预注册: `manifest.json`;完整结论: `FINAL_REPORT.md`。)

**一句话结论 (2026-09-30)**: 机制正确运行(JCG>0 200/200),但预注册主族 4/4 反向显著拒绝增量假设(ΔJCG<0,Holm p=1.5e-9,反超 0/200);唯一系统性正差分 = §16 共享枢纽组合拓扑(FAS 30/30 vs B2 0/30)。FAS 的实证贡献更接近 persistent relational memory,而非 activation dynamics 的 superiority(§35 变体 1 + 限定例外)。AS-IS(不修改生产、不优化论文结论)。

## 1. 目录布局

```
core_incremental_value/
├── manifest.json          # 预注册(数据前锁定)
├── generator.py           # 受控图/任务生成(T1–T4、C1–C3、O3、minimal toy)
├── runners/
│   ├── methods.py         # FAS / B1 直接激活 / B2 flat path-product / B3 随机 / B4 oracle
│   ├── metrics.py         # MRR、R@1/3/5、NDCG、margin、stability、JCG、ΔJCG、Wilcoxon、Holm、bootstrap CI
│   ├── campaign.py        # 编排(preflight/confirmatory/sweeps/topology/counterfactuals/combo/weight/neg/insert/oracle)
│   └── analyze.py         # 分析 → analysis.json / summary.csv / statistics.csv
├── figures.py             # 9 张发表级图(Figure 1–9,PDF/SVG/PNG)
├── figures/               # 生成的图
├── preflight.json         # 运行前校验结果(B4 oracle 全拓扑 1.0、toy、integrity 0)
├── raw_results.jsonl      # 34,016 行全量原始结果(0 INVALID、0 解析失败)
├── summary.csv            # 16 行 = 4 confirmatory 条件 × 4 方法
├── statistics.csv         # 每检验一行(主族 4 + 次级 4)
├── analysis.json          # 全部聚合分析
└── FINAL_REPORT.md        # 最终报告(8 问、5 维分类、§26–§35 全套)
```

## 2. 来源(provenance)

- 扩散语义提炼自 `research_audit/mechanism_falsification/`(663-run 已验证的独立参考模型,提炼自生产 `diffusion_engine.py` 工程语义: 归一化脉冲 + 发射预算 ratio=0.5 + 发射即转移 transfer=1.0 + fire-once + 步间衰减 0.05 + cap 5.0 + ε+ 双向 in-edge 语义)。
- **零 production import**;不修改任何生产文件(见 `manifest.json` production_policy)。
- 种子确定性强: 全局 seed = seed×100000 + 打点序号;图与 tie-break 同源派生。
- 数据/分析生成记录见 `FINAL_REPORT.md` §3(含 5 个运行/分析期 bug 的修复记录与对齐说明)。

## 3. 运行方法

本项目零第三方依赖(标准库 + 本机 Python;图脚本另需 matplotlib,已装于 `E:\Miniforge`)。

```bash
cd experiments/core_incremental_value
PYTHONIOENCODING=utf-8 E:/Miniforge/python.exe -c "from runners import campaign; campaign.main(['--phase', 'preflight'])"
PYTHONIOENCODING=utf-8 E:/Miniforge/python.exe -c "from runners import campaign; campaign.main([])"          # 全量运行
PYTHONIOENCODING=utf-8 E:/Miniforge/python.exe -c "from runners import analyze; analyze.main([])"
PYTHONIOENCODING=utf-8 E:/Miniforge/python.exe figures.py
```

- campaign 支持 `--phase <name>`(confirmatory/sweeps/topology_compare/counterfactuals/combo/weight_sweep/neg_edges/insert_order/hidden_oracle)与崩溃恢复(append-only JSONL + 进程锁;重跑自动跳过已完成的 cell)。
- 运行时长未计时留存;单格扩散成本量级: 最大工况(1237 节点/439 边)单次扩散实测 0.8 ms(基准命令见 FINAL_REPORT §8)。
- 核对命令: `FINAL_REPORT.md` 中全部数字可由 `analysis.json` / `statistics.csv` / `summary.csv` 复现;图脚本运行时打印 JCG 重构中位数与 combo 激活,与报告数值一致。

## 4. 结果文件速查

- 主族: `statistics.csv` filter `family==primary`(ΔJCG 4 条,Holm p=1.51e-9,r=0.62,反超 0/200)。
- 汇总: `summary.csv`(FAS JCG 0.0977/0.0977/0.2158/0.2177,MRR 0.0435/0.0426/1.0/1.0;B2 JCG 0.5/0.5/1.0/1.0)。
- 唯一正差分: `analysis.json` `combo` = `{"fas": 1.0, "b1": 0.0, "b2": 0.0, "b3": 0.167}`。

## 5. 50 年后的复现者须知

- 本实验是**负结果忠实呈报的样板**: 预注册方向被反向拒绝,仍完整交付(图、统计、诊断、限定例外)。
- 若未来要扩展,优先两个方向: ① 在 §16 共享枢纽结构族上做参数化扫描(目前仅 1 种权重组合);② 3 输入干扰谱的"支持度×干扰强度"联合面(目前 3 输入全为地板)。