# RESULTS.md — 对照实验结果

## 汇总率

| 条件 | T2 success | T3 mean fail_repeats |
|---|---|---|
| C1 | 10/10 | 2.8 |
| C2 | 0/10 | 7.7 |
| C3 | 10/10 | 12.0 |
| C4 | 10/10 | 11.7 |
| C5 | 10/10 | - |

## 预注册检验(Bonferroni m=8,α=0.00625)

| 比较 | 任务 | 检验 | n | 结果 | p | p_bonf | 显著 | 效应 |
|---|---|---|---|---|---|---|---|---|
| H_A C1 vs C3 | T2 | McNemar | 10 | 0/0 discordant | 1.00000 | 1.0000 | 否 | RD=0.00 |
| H_B C1 vs C4 | T2 | McNemar | 10 | 0/0 discordant | 1.00000 | 1.0000 | 否 | RD=0.00 |
| H_C C3 vs C2 | T2 | McNemar | 10 | 10/0 discordant | 0.00195 | 0.0156 | 是 | RD=1.00 |
| H_C C4 vs C2 | T2 | McNemar | 10 | 10/0 discordant | 0.00195 | 0.0156 | 是 | RD=1.00 |
| H_A C1 vs C3 | T3 | Wilcoxon | 10 | medΔ=-8.0 | 0.00195 | 0.0156 | 是 | HL=-9.0 CI95=[-11.0, -7.5] r=1.0 |
| H_B C1 vs C4 | T3 | Wilcoxon | 10 | medΔ=-8.0 | 0.00195 | 0.0156 | 是 | HL=-9.0 CI95=[-10.0, -7.5] r=1.0 |
| H_C C3 vs C2 | T3 | Wilcoxon | 10 | medΔ=5.0 | 0.00195 | 0.0156 | 是 | HL=4.5 CI95=[3.0, 5.5] r=1.0 |
| H_C C4 vs C2 | T3 | Wilcoxon | 10 | medΔ=4.0 | 0.00195 | 0.0156 | 是 | HL=4.0 CI95=[3.0, 5.0] r=1.0 |

(完整 JSON:RESULTS.json;原始 run:raw_controls.jsonl + PSA raw_results.jsonl,只读)