# PAPER_INTEGRATION_PLAN.md — 修复轮/战役数据并入论文的预备计划

状态:数字全部就绪(来源文件标注),等 LLM 端点充值补完
trade-off 行为半区后**一次性并入**(避免两轮 churn)。
并入时执行与论文 v2 相同的铁律:程序化提取、EN/ZH 镜像、双扫描清零、
数字差异审计。

## 1. 新增实验事实(全部已归档)

| 事实 | 数字 | 来源 |
|---|---|---|
| C 持续意图(闭卷 P3) | fas 9/10 vs history 0/10、direct 0/10;McNemar p=0.0039,Holm p=0.0117 | `raw_C.jsonl` + analyze_campaign |
| C 机制重定性 | 零经历对照复现同 payload → 驱动=先验闭包+观测触发激活,**非** persistent intention | `PROBE_OUTPUT.txt` P-C |
| E 目标修订 | fas 10.5 fails/7-10 pivot@4.7;history 1.0/10@1.0 | `raw_E.jsonl`(dedup 后写) |
| E 修复轮(fas-ef) | 7.2 fails/9-10@3.1;Wilcoxon 单侧 p=0.0107 | `raw_E.jsonl` + REPAIR_TEST_RESULTS |
| F write-only | v6:ON/OFF 遥测 5/5/5 vs 0/0/0,行为逐位相同;current_goal 零行为读取者 | `raw_F_v6_writeonly.jsonl` + F_FINDINGS |
| F 修复(consumer) | v7:ON gathers 1.0→6.0(failure 历史),派生目标 5/5 | `raw_F.jsonl` |
| I 行为汇聚 | FAS 0-2/10 vs LLM 原始流 10/10,Holm p=0.041;rep 研究(k16/k32/rich)0/30 | `raw_I.jsonl` + v1.2 |
| B 延迟信用 | 0/32s 100%、160s 0/10;90s=有意边界 | `raw_B_mech.jsonl` + B_FINDINGS |
| A/K 等效与压缩 | A 全 10/10=p;K 923 vs 13290 tok(≈14×) | `raw_A/K.jsonl` |
| J 100-cycle | 节点+3.7%、激活钳制、晋升=0(重复饥饿)、目标累积 1→8 | `raw_J.jsonl` |
| 权衡机制半区 | ef:失败节点 1.12→5.0 首位+负边 1;sg:目标进 Top-8;efsg:失败动作被挤出(非可加) | `raw_T_mech.jsonl` |

## 2. 论文改动清单(并入时执行)

### 主文(main.tex / main_zh.tex)
1. **摘要**:贡献 2 加一句比较级证据:"under neutral instructions FAS
   resumes an abandoned goal in 9/10 runs where full-history LLM
   baselines do so in 0/10 (McNemar p=0.0039)"(≤250 词约束内替换
   一个次要数字)。
2. **5.x 新小节 "Comparative behavioral evidence (campaign v2)"**:
   C/E/I/F 四组数字 + 权衡机制表;明确 C 机制标签(relational
   working-set salience);E/F 修复轮作为"consumer 假设的直接检验"
   (E2 显著改善、F v7 行为分离)。
3. **6.x(讨论)**:trade-off 段落——同一持久机制在干扰下是优势
   (重拾)、在环境突变下是劣势(不放手);修复轮给出成对证据。
4. **局限性**:追加 (15) 归因窗 90s=信用地平线;(16) 决策头序列化
   丢时序叙事(I);(17) trade-off 行为半区待端点补测(并入时若已有
   数据则删除此条)。
5. **数据可用性**:补 capability_gap_v2 + architecture_diagnostic 归档。

### 补充材料(supplementary*.tex)
- 新 S15:战役协议(冻结设计+两次混淆修复记录:门控 v1→v2、
  C-P3 闭卷修正)。
- 新 S16:逐实验表(C/E/F/I/B/J + 修复轮),数字表照 REPAIR_TEST_RESULTS。
- 新 S17:故障分类表(FAULT_CLASSIFICATION 的英文版)。
- 代号映射表(S2)补:fas-ef、fas-sg、goal_attention、ef_context。

### 投稿材料
- highlights 第 3 条替换为 C 的比较级证据(85 字符内)。
- cover letter"负结果与预注册"段补一句双向权衡(优势+劣势都报告)。

## 3. 并入流程(端点充值后)
```text
1) python run_tradeoff.py                     # 行为半区 50 run(~35 min)
2) 更新 TRADEOFF 文档 + 能力图谱 T 行
3) 按本计划 §2 改 EN 主文/补充 → 编译 → 双扫描
4) 镜像 ZH → 编译 → 双扫描
5) 数字差异审计(基线=当前 main.pdf 快照)
6) 重打包 deliverables/paper_final + 更新 REPRODUCE.md
```

## 4. 不需要并入的(留在战役文档)
- 修复实施细节(CHANGELOG/TEST_RESULTS)——属于可复现性工件,
  正文引用一句即可。
- mock 冒烟(raw_T_mock.jsonl)——管线验证记录,非结果。
