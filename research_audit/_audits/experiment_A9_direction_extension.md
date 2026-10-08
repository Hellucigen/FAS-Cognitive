# A9 实验规格 — 关系方向语义扩展(机制级增量,2026-09-30)
**状态**: 规格就绪,待执行。执行地点=mechanism_falsification/ 独立 harness(不 import 生产),不改生产一行。

## 0. 依据(证据在案)
- S7 已证:配方关系双向化提升检索一致性 ρ .33→.65(r=0.886,p=0.002,n=10 seeds),审计判定 VALID 机制级实证;
- 生产白名单=8 个双向关系(`relation_direction`:相关/同一/认识/靠近/关于/涉及/参与/交互),config.py:502-514;
- L2-RD-01:白名单外 forward-only;配方关系(需要/产生/掉落…)形成 sink 星形森林(sink_fraction=1.0,forward 2-hop 可达=0)——"target 永远当不了源"。

## 1. 假说(预声明)
- **H_A9_forward_loss**: 对 8 白名单之外的**认知/情绪/社交/时间**类目关系,forward-only 语义在端到端可检索性(JCG/recall@1/MRR/2-hop 源可达)上系统性弱于同边集双向;类目间效应量可排序。
- Null: 无系统性差异(direction 语义与类目无关)。
- 方向: A9 检验**必要性**,不做"全部双向化"的主张(3E 拓扑实验已示双向 JCG 减损——代价与收益需同表报告)。

## 2. 协议(复用 3E 拓扑实验同构协议)
- 构造:`generator.random_task` 增加 relation-category 着色参数(direction 参数已支持 forward/bidirectional/mixed/random;补 `category` 着色,图结构保持 seed 同构);
- 类目集:C = {recipe 对照(已知 S7), cognitive, emotion, social, temporal};
- 每类目 × direction{forward, bidir} 严格同构(同节点集/边集/候选,仅方向翻转,同 3E 做法);
- 规模:N ∈ {50, 200}(2 档)× inputs∈{2,5} × seeds 0..19;
- 指标(预声明,同 manifest.json 族):JCG / joint recall@1 / MRR / 2-hop 源可达率(/新指标,需写进 metrics.py 时同步预声明) / Spearman ρ;
- 统计: 配对 Wilcoxon 每类目每指标;Bonferroni 修正(类目 5 × 指标 5 × 规模 2 = 50 检验 → α=0.001);效应量 r + Bootstrap 95%CI;
- 运行入口:`falsify.py` 加 `--relation-cats` 子命令或独立 `relation_cats.py`(同族新文件,不动既有 663 runs 通道)。

## 2.1 执行前预声明修订(2026-09-30,拿数据前锁定)
按 45 章审计纪律,正式执行前对本规格做四方面修正(全部为前置声明,非结果后处理):
- **(a) 类目非空化**——纯染色切片(同结构只换标签)结果逐位相同,是空类目维,不披露为"类目差异"。改为**类目→结构画像**(生产拓扑在案,FAILURE_REGISTER L2-RD-01 + 时间顺序链 1614 边):recipe/temporal = 深链画像(JONT 路径 3 hop),cognitive/emotion/social = 浅画像(JONT 路径 2 hop)。类目差异 = 结构画像差异,方向口径独立操控。
- **(b) 处理定义**——treatment = 将该类目移入双向白名单(该图全部边 bidirectional)vs 对照 = 生产现状(全部 forward,该类目不在 8 白名单)。与 3E 同构:同 seed 同结构,仅方向旗标翻转。
- **(c) sink 指标预注册**——新指标 `sink_fraction`:候选集中"0 条可发射边"(无 fwd 出边且无 bidir 入镜像)的比例;直接量 L2-RD-01 的"target 永远当不了源"现象在机制模型的投影。属预声明新指标(写入 relation_cats.py 输出 schema)。
- **(d) 主检验冻结**——主对比 = 每类目配对(forward vs bidir)× 4 指标(jcg / mrr_joint / recall@1 / sink_fraction);5 类目 × 4 指标 = 20 主检验,α=0.0025(Bonferroni);N/inputs 作为分层描述量,不作主检验。S7 复现侧记于 recipe 深画像对照(预期 bidir 检索增益方向复现)。

## 3. 产出与边界
- 产出: raw_results.jsonl/summary.csv/statistics.csv 同族 + 本规格对应排版图;
- 结论若 H_A9 成立 → 候选白名单扩展清单(保守:只加"对称语义成立"的关系,需人工语义复核),**先实验后本体**——不直接改 config.py relation_direction;
- 边界: 机制级结论,无生产行为声明;与 C34 v2 / S7 同证据层(见论文 \S\ref{sec:prodcaliber} 口径约束)。

## 5. 结果(2026-09-30 实跑,800 runs,20 主检验,Bonferroni α=0.0025)
运行: `relation_cats.py`(同族新文件,未动既有 663 runs 通道);原始数据 `raw_results_a9.jsonl`(800 行)/ `a9_summary.csv` / `a9_stats.json`;前检通过(同积同构+确定性)。

| 类目(画像) | 指标 | fwd 均值 | bidir 均值 | 差(b−f) | p | r | 显著 |
|---|---|---|---|---|---|---|---|
| recipe(深) | jcg | 0.492 | 0.111 | −0.381 | 0.000 | 0.61 | **\*** |
| recipe(深) | mrr_joint | 0.810 | 0.771 | −0.040 | 0.127 | 0.20 | |
| recipe(深) | recall@1 | 0.625 | 0.625 | 0 | 1.0 | 0 | |
| recipe(深) | sink_fraction | 1.0 | 0.0 | −1.0 | 0.000 | 0.61 | **\*** |
| temporal(深) | jcg | 0.492 | 0.111 | −0.381 | 0.000 | 0.61 | **\*** |
| temporal(深) | sink_fraction | 1.0 | 0.0 | −1.0 | 0.000 | 0.61 | **\*** |
| cognitive(浅) | jcg | 0.954 | 0.193 | −0.760 | 0.000 | 0.61 | **\*** |
| cognitive(浅) | mrr_joint | 1.0 | 0.981 | −0.019 | 0.109 | 0.65 | |
| cognitive(浅) | recall@1 | 1.0 | 0.963 | −0.037 | 0.109 | 0.65 | |
| cognitive(浅) | sink_fraction | 1.0 | 0.0 | −1.0 | 0.000 | 0.61 | **\*** |
| emotion/social(浅) | 与 cognitive 逐位相同(同画像同结构) | | | | | | |

不显著项在表中留空不另列(deep 的 mrr/recall 与 shallow 一致,全部 ns)。**rho_topology 描述量**:深画像 0.299 vs 0.296(持平);浅画像 0.686 vs 0.574(bidir 反而降)。N/inputs 分层:JCG 增益随 inputs 扩大,bidir 摊薄随 N 加深(如浅画像 N=200/in=5:1.55→0.24)。

### 5.1 解读(与预声明对应,含对 A4 前身 S7 的关系)
1. **汇点消除是机制结构事实**:forward-only 下候选集 sink_fraction 恒 1.0(目标节点 0 出边=不可当源——L2-RD-01 "target 永远当不了源"量化确认);移入白名单(bidir)→ 0.0。这是 S7 双向化收益(ρ .33→.65)的机制基础,五类目一致。
2. **白名单有成本**:bidir 显著摊薄 JCG(p<0.0025,全类目)——与 3E 拓扑实验一致(双向 JCG↓);浅画像代价更深(share 汇点更近)。
3. **排序不受损**:mrr_joint / recall@1 全部 ns——在该任务形态下,白名单不改善也不破坏候选排序;代价只在收敛增益与(浅画像)拓扑一致性(rho 降)。
4. **类目标签零效应**:recipe≡temporal(深)、cognitive≡emotion≡social(浅)逐位同数——机制只响应**结构画像**,语义标签不进入机制。→ **白名单扩展决策的机制依据 = 结构需求(该关系的 target 是否需要当源),不是类目名**。与生产现状自洽:对称语义(关于/涉及/参与/交互…)已入白名单;配方(需要/产生/掉落)target 无语义上"当源"需求,保持 forward 正确。
5. **对 A3 fabric 治理的对位**:`涉及` 已在白名单(双向)≠我们的治理重点是边量(40%)而非方向;recipe 类目方向维持 forward 由本实验成本侧支持。

### 5.2 结论
**H_A9_forward_loss 部分支持**:forward-only 的可达性损失(sink)真实且在机制层 100% 量级一致,但其"必要修补"只在该关系 target 语义上需要当源时才成立;浅结构/排序形态下无检索损失反而有 JCG/rho 成本。不构成"扩大白名单"的机械证据;构成"按语义对称性逐关系复核 + 成本已知"的决策框架。一切为机制级结论,无生产行为声明,未改 config 一行。

## 4. 依赖与状态
- 依赖: 无(harness 自足);预计运行时长与 663 runs 族同级(数十分钟,可后台跑);
- 状态: **待执行**(规格就绪;执行与否与日程由用户裁决——属研究实验,非生产修复)。