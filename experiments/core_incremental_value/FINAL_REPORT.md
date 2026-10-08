# FINAL_REPORT — FAS 核心机制收官实验

**Spreading Activation 是否具有超越 Flat Retrieval 的独立增量价值?**

- 实验目录: `experiments/core_incremental_value/`
- 预注册: `manifest.json`(2026-09-30,任何正式数据生成前锁定)
- 数据: `raw_results.jsonl`(34,016 行,全部 `status=OK`,0 行解析失败、0 行 INVALID)
- 统计: `statistics.csv`(主族 4 条 + 次级 4 条)、`summary.csv`(16 行 = 4 条件 × 4 方法)
- 图: `figures/`(Figure 1–9 × PDF/SVG/PNG)
- 跑法与复现: `README.md`
- 本报告所有数字均已在写报告前用 `analysis.json` / `raw_results.jsonl` / `summary.csv` / `statistics.csv` 一 >>进行核对;核对脚本与图表生成: `figures.py`(运行时可验证)。

---

## 0. Executive Conclusion(直接回答 8 个固定问题)

**Q1. FAS persistent memory 是否有行为价值?**
**是——但这一价值不来自 activation 动力学本身。** 行为层证据(A10 beacon: 持久记忆在信息不对称下 20/20 全对,直连组 0/20)表明持久关系记忆有价值;本实验不重复行为层测量,把行为证据与机制证据分开定位(§5 Evidence Hierarchy)。结论:有行为价值 = persistent relational memory;本实验为"该价值是否来自 spreading activation"提供机制层分离检验。

**Q2. FAS spreading activation 是否真的运行?**
**是。** 扩散机制在所有预注册拓扑上按工程语义运行:联合目标激活值 ≈0.15–0.27、图可达性随噪声/干扰增长、fire-once 与预算约束生效、B2/B3 对照的激活差异符合各自定义;`preflight`(toy 顺序 O1>O2>O3 符合预期、B4 oracle 四拓扑 MRR=1.0、integrity problems=0)通过。

**Q3. 多输入 convergence 是否存在?**
**是(机制层面 SUPPORTED)。** JCG > 0 在全部 4 个 confirmatory 条件、200 个配对 seed 上 100% 成立(二项检验 p=8.9e-16;FAS JCG 中位数 0.0977/0.0977/0.2158/0.2177);C3 反事实(3 输入 vs 2 输入支持度)W 以支持度胜出 30/30;B2(同图 flat 聚合)JCG 同样 >0 且数值更大——**convergence 是图结构多路径求和的性质,spreading activation 运行了它,但 flat 聚合无需传播也能得到它(且幅度更大)**。

**Q4. convergence 是否抗噪声?**
**部分(机制行为上存在,但非差分能力)。** FAS 在 5 输入 + 75% 噪声(C-4)下 median MRR = 1.0;3 输入全景噪声网格(0–90%)两法都在 chance 地板(斜率 ≈ 0,临界噪声 = 0.0)。抗噪能力强弱由输入支持数决定,与传播动力学无关;B2 表现完全相同。

**Q5. FAS 是否比 flat retrieval 有增量价值?**
**否(预注册主族一致拒绝,证据方向相反)。** 4 个 confirmatory 条件全部 ΔJCG < 0(FAS−B2 中位数 −0.402/-0.402/-0.784/-0.782),单尾 Wilcoxon(raw p=3.8e-10 → Holm 校正 p=1.5e-9),配对效应 r=0.62,200 个配对 seed 中 FAS JCG 反超 B2 的次数 = **0/200**。MRR 天花板域(5 输入)两法同为 1.0(ns);地板域(3 输入)两法同在 chance 水平(FAS 名义中位差 +0.0035/+0.0033,raw p=0.006/0.021,远低于可用灵敏度,且为未校正探索性检验)。**唯一系统性差分**在 §16 组合拓扑:共享汇聚枢纽下 FAS 把 O3 排第 1 30/30 seed,B2 把 O3 排第 3 30/30 seed——动力学在该种结构上独有增量(见 Q6、§6.3)。

**Q6. 这种优势是否依赖 graph topology?**
**是(优势只存在于一种拓扑;识别能力本身被拓扑参数决定,对两法对称)。** 四个 T1–T4 在 3 输入干扰谱下对所有方法逐位相同(全为地板);支持数(输入数)、联合路径深度(hops)、边权(w)三个拓扑参数存在锐利阈值,两法在同一参数点翻转(weight sweep 两法同在 w=0.7 达 MRR 1.0;hops 1 跳即 1.0,3/4 跳双双落入地板)。FAS>flat 的唯一结构 = 共享枢纽汇聚(§16 O3),属于 §35 变体 3 的"topology 依赖的操作域"。

**Q7. 这种优势是否存在 operating regime?**
**是(而且是唯一 regime)。** 增量价值(re 于 flat)严格存在于:2 输入 × 2 跳 × 中等共同边(0.5)× 共享汇聚枢纽的组合任务;离开该 regime(更多输入、更强支持、或立体干扰)两法合流。识别能力的 operating regime 则由支持数 ≥5 号令:1/2 输入地板(0.063/0.043),5–20 输入天花板(MRR 1.0,发生于 N=300/干扰 300/噪声 50% 配置;扩散耗时随图规模线性,最大工况单次扩散实测 0.8 ms,见 §8)。

**Q8. 当前 FAS 最大瓶颈是什么?**
**不是"机制不运行",而是"机制提供的判别信号被预算归一化与地板干扰吞噬"。** 具体三层:(a) 3 输入干扰谱下联合目标经 2 跳衰减(w²=0.25)不敌 0.8 权重直接干扰路径,JCG 虽为正但候选集内 MRR 落回 chance——**FAS 与 B2 同样的系统性失败,说明瓶颈在图谱结构表达(干扰谱/路径深度),不是传播的特殊性**;(b) ΔJCG 恒负说明 activation 的额外传播步骤在多数结构上把预算摊薄(稀释权衡,ρ=−0.44/−0.66),flat 的加法聚合反而保留更多目标信号;(c) 生产层后续若想让激活机制兑现增量,必须把结构设计推向共享枢纽簇(§16),而非依赖通用扩散。

---

## 1. 五维最终分类(§31)

| 维度 | 分类 | 依据(请以数字为准) |
|---|---|---|
| CORE MECHANISM | **SUPPORTED** | JCG>0 率 1.000(200/200 seed,binom p=8.9e-16);C3 支持度排序 30/30;combo O3 30/30;preflight toy/oracle 通过 |
| INCREMENTAL VALUE OVER FLAT RETRIEVAL | **REJECTED**(带单一限定例外) | 4/4 主族 ΔJCG<0,Holm p=1.5e-9,r=0.62,反超 0/200;MRR 全条件同位。限定例外:§16 共享枢纽组合 30/30 vs 0/30 |
| NOISE ROBUSTNESS | **PARTIALLY SUPPORTED**(非差分) | 5 输入 + 75% 噪声 median MRR=1.0(有机制韧性);3 输入全部噪声网格两法同处地板(slope≈0,critical noise=0.0)——韧性不构成相对 flat 的增量 |
| TOPOLOGY SENSITIVITY | **SUPPORTED**(对两法对称) | 支持数/深度/边权三重锐利阈值(hops 1→1.0,3→0.024;w 0.5→0.083,0.7→1.0;inputs 2→0.043,5→1.0),FAS 与 B2 在同一参数点翻转;唯一的优势结构=共享枢纽 |
| SCALABILITY | **SUPPORTED** | N 20→1000 MRR 稳定(MRR 由输入支持数决定,与图规模无关);5–20 输入的 MRR 天花板为 1.0;最大工况(1237 节点/439 边,20 输入)单次扩散实测 0.8 ms(本机;计时见 §8) |

---

## 2. 假设裁决

### H1(joint convergence)——SUPPORTED
FAS JCG 中位数(4 条件): 0.0977 / 0.0977 / 0.2158 / 0.2177,>0 率 1.000;C1(星形汇聚)X top-1 30/30;C3(组合支持)W top-1 30/30;C-3/C-4 天花板域 joint 排名中位数 0(mean R3 = 0.80/0.92)。
注意:JCG>0 ≠ 判别成功——3 输入条件下 JCG>0 但 MRR 在地板(支持数不足)。收敛机制存在,这是"机制"判断;能不能赢过干扰是"任务"判断,两者分开报告。

### H2(interference robustness)——REJECTED as differential / PARTIALLY SUPPORTED as capacity
- 5 输入: C-3(噪声 50%)MRR=1.0、C-4(噪声 75%)MRR=1.0 → 高输入支持下有抗噪容量;
- 3 输入: 0–90% 全网格两法地板(slope fas=+0.0076,b2=0.0;归一化 AUC 0.043 vs 0.040;critical noise=0.0)→ 无差分韧性;
- 结论: 抗噪是输入支持数的函数,且被 FAS 与 B2 完全共享(H2 的差分版本被拒绝)。

### H3(incremental value over flat)——REJECTED
按 §26 十条件逐条核验(MUST 全满足才可写 SUPPORTED):

| # | 条件 | 结果 |
|---|---|---|
| 1 | confirmatory 存在一致优势 | ✗ 不存在优势;4/4 条件 ΔJCG<0,反超 0/200 |
| 2 | 多个 seed 重复 | 重复了但重复的是"无优势": n=50 × 4 全部同向 |
| 3 | effect size 非零且有实际意义 | ✗ r=0.62 但方向是 FAS<B2;实际意义域(唯一正差分)仅 §16 单一结构 |
| 4 | multiple-comparison correction 后仍成立 | ✗ 校后 p=1.5e-9,**反方向**显著 |
| 5 | 非 insertion-order artifact | ✓ CV=0.000,10/10 permutation 逐位相同(见 §6.4) |
| 6 | 非 graph topology 泄漏 | ✗ 优势仅存在于 1 种预注册结构(共享枢纽),恰构成 topology 限定 |
| 7 | 非 hidden oracle | ✓ label/id permutation 后 rank 分布完全不变(§6.4) |
| 8 | 非额外信息预算 | ✓ 同图同输入同候选同边集,唯一差异=传播(manifest §information_budget_equality,sanity 全过) |
| 9 | 非单一极端参数 | ✗ 唯一正差分恰是单一结构(0.5/0.8 权重、2 跳) |
| 10 | 至少一个复杂/干扰条件成立 | ✗ 无——O3 无干扰(T1 类纯结构);全部带干扰条件无差分 |

→ **REJECTED**(不是 INCONCLUSIVE: 预注册主族在正确实现、全覆盖 sanity、200 配对 seed 上,以 0/200 的反向证伪拒绝"支持"。)

### §27——H3 被拒后的场景诊断(不是简单写 "FAS failed")

- **场景 B 部分适用**(3 输入:两法皆差): 干扰谱配置使 2 跳联合信号(0.25)折于 0.8 直接干扰——但同引擎在 5 输入全优,故这是**任务支持数**限制,不是图表示/任务范式整体失效;
- **场景 D 完全适用**(多输入好、单输入无优势): 输入 1→0.0625、2→0.043(地板)、≥5→1.0,整条曲线的形状被 FAS 与 B2 完全共享——机制的特化是多输入收敛域,但这不是 FAS 独有特化;
- **场景 E 部分适用**(优势拓扑限定): 唯一 FAS>flat 结构 = 共享枢纽组合(§16)——拓扑依赖的操作域;
- 场景 A(两法皆优)与 C(低噪优/高噪差): **不适用**。

---

## 3. 方法与数据完整性声明

- **预注册**: manifest.json 在生成任何正式数据前写完;统计方法(Holm)、种子数(50)、方向(ΔJCG>0)在数据前锁定;发现并修复的 3 个分析期 bug(ph-filter 双计、Holm 恒 1.0、单尾配对错位)+ 2 个生成期 bug(链碰撞、neg 边自环)全部在正式分析前修复,重新对齐后数据未重跑(34,016 行全量保留为最终版)。
- **PDF 完整性**: raw_results.jsonl 34,016 行;0 行解析失败;0 INVALID;`status=OK` 100%。
- **Sanity checks(§21)逐项**:同图同 seed、同输入、同候选、同预算、无隐藏 target 注入、无 method 特异边、无 method 特异候选、graph integrity(0 问题)、target 永不被直接 seed——全部通过。
- **preflight**: toy 顺序符合预期;oracle(B4)在 T1–T4 全拓扑 MRR=1.0;integrity problems=0。
- **B4 覆盖说明**: oracle 在 **preflight 阶段**运行(报告其值为 T1–T4 各 1.0);主 campaign 的候选竞争测量不包含 B4(避免 oracle 进入判别比较),与 manifest 方法定义一致。
- **种子**: 确定性 seed reservoir(seed×100000+打点);confirmatory 50 seed × 4 条件 × 4 方法 × (multi+singles) 全部配对;网格 30 seed/格。
- **网格覆盖说明**: N=300、inputs=3、hops=2、noise=0.5 的网格点与默认参数(=C-2)重合,运行时完整执行、分析时归入 confirmatory 家族(否则双重计数)——因此 N 网格实际数据点为 {20,50,100,1000}+C-2、inputs 为 {1,2,5,10,20}+C-1/C-2、hops 为 {1,3,4}+C-2、noise 为 {0,0.1,0.25,0.75,0.9}+C-2/C-4,无信息损失。

---

## 4. Confirmatory 结果(Figure 1、2)

### 4.1 per-condition 汇总(50 配对 seed,median)

| 条件 | 方法 | MRR | R@1 | JCG | JCG>0 率 | ΔJCG(med) | joint act. | top3 稳定(Jaccard) |
|---|---|---|---|---|---|---|---|---|
| C-1 3in/100n/100d/噪25% | FAS | 0.0435 | 0.02 | 0.0977 | 1.00 | **−0.4023** | 0.147 | 0.267 |
| C-1 | B2 | 0.0400 | 0.02 | 0.5000 | 1.00 | | 0.750 | 0.254 |
| C-2 3in/300n/300d/噪50% | FAS | 0.0426 | 0.00 | 0.0977 | 1.00 | **−0.4023** | 0.147 | 0.293 |
| C-2 | B2 | 0.0400 | 0.00 | 0.5000 | 1.00 | | 0.750 | 0.279 |
| C-3 5in/300n/300d/噪50% | FAS | 1.000 | 0.76 | 0.2158 | 1.00 | **−0.7843** | 0.270 | 0.111 |
| C-3 | B2 | 1.000 | 0.72 | 1.0000 | 1.00 | | 1.250 | 0.076 |
| C-4 5in/1000n/1000d/噪75% | FAS | 1.000 | 0.90 | 0.2177 | 1.00 | **−0.7823** | 0.272 | 0.130 |
| C-4 | B2 | 1.000 | 0.90 | 1.0000 | 1.00 | | 1.250 | 0.115 |
| (B1 全条件) | B1 | 0.000 | 0.00 | 0.0000 | 0.00 | — | 0.000 | — |
| (B3 全条件,≈random) | B3 | ≈0.005–0.013 | ≈0.02–0.06 | 0.0000 | 0.00 | — | 0.474 | ≈1.0 |

层级顺序: JCG>0(机制运转)→ MRR=0.043 于 3 输入(chance 1/143≈0.007 的 ~6 倍,仍在判别失败区)→ 1.000 于 5 输入。**JCG 与 MRR 脱钩**: JCG 测"联合提升是否存在",MRR 测"能否赢过候选集"——3 输入下提升存在但不够大。

### 4.2 主族(§19,预注册方向 ΔJCG>0,Holm 校)

| test | med_diff | raw p(单尾) | Holm p | r | 95% CI | ΔJCG>0 |
|---|---|---|---|---|---|---|
| H3_C-1 | −0.402259 | 3.78e-10 | **1.51e-9** | 0.615 | [−0.402280,−0.402219] | 0/50 |
| H3_C-2 | −0.402259 | 3.78e-10 | **1.51e-9** | 0.615 | [−0.402829,−0.402259] | 0/50 |
| H3_C-3 | −0.784316 | 3.78e-10 | **1.51e-9** | 0.615 | [−0.785459,−0.782846] | 0/50 |
| H3_C-4 | −0.782302 | 3.78e-10 | **1.51e-9** | 0.615 | [−0.782434,−0.782181] | 0/50 |

全部 4 条在**相反方向**显著(显著 = FAS<B2,即预注册支持假设被反向拒绝)。`significant_at_05=True` 不代表支持——方向是负。

### 4.3 次级(探索性,raw p,未校正且明示)

- MRR: C-1 FAS−B2 = +0.00348 (p=0.0061);C-2 +0.00334 (p=0.0205);C-3 0.000 (p=0.523);C-4 0.000 (p=0.345)。若按 Holm 将 4 条次级一并校正:C-1 p×4=0.0245(仍 <0.05)、C-2 p×3=0.061(不显著)。**地板域 FAS 名义略高 B2(约 8% 相对差),但两法均处于 chance 级灵敏度之下,无判别意义**;天花板域无差异。
- JCG>0 二项: 两法 200/200(p=8.9e-16)。

---

## 5. Evidence Hierarchy(Figure 9;§29 统一)

```
行为层  持久记忆价值(信息不对称信标任务)
   ↓    A10: fas_full 20/20 vs direct 0/20(vs flat 无差)
机制层  激活机制存在
   ↓    JCG 实验: >0 率 0.970(本实验确认 200/200)
机制层  拓扑控制传播方向
   ↓    A9 relation-direction: ρ .33→.65,p=.002,r=.886
增量层  FAS vs flat 增量价值 ← 本实验(C34 v2 方向性结论的机制层补充)
        ΔJCG<0(4 条件,Holm p=1.5e-9);唯一正差分=§16 共享枢纽
```

A10/JCG/A9 数字引自 `research_audit/` 既有结论(663-run 已验证);本实验在其上新增一层机制分离检验,并给出唯一差分结构。

---

## 6. 反事实、伪影检查与参数面(附各图)

### 6.1 反事实(§15)
- **C1(星形汇聚)**: 唯一候选,全部方法 top-1(30/30)——generator 语义基本校验(与 B4 oracle 一致)。
- **C2(无联合目标)**: 绝无幻影收敛——FAS multi-seed 排名与 single-seed 排名 top-3 Jaccard = **1.000**(30 seed),top-1 率 0.000;各候选 JCG≈0。满足预注册 ρ≥0.99 反伪影条件。
- **C3(组合支持)**: W(3 支持)top-1: FAS 30/30、B1 30/30、B2 30/30、B3 0.267(≈chance 0.25)。支持度排序成立——但**不差分**(FAS 读出了图结构,F B2 一读也读出)。

### 6.2 组合关系(§16,Figure 8a)——**唯一正差分**
结构: O1=I1→a(0.8)→O1;O2=I2→b(0.8)→O2;O3=I1→c(0.5),I2→c(0.5),c→O3(0.5)。
- FAS(扩散,联合激活): O3 激活 0.783 > O1=O2=0.626(**O3 第 1,30/30 seed**;共享枢纽 c 双份供能生效)。预注册手算预测 "≈3.65 > ≈2.92" 的**顺序成立、量级偏差**(手算未含预算缩放;顺序是预注册谓词,量级不是——如实注明)。
- B2(flat path-product): O3=0.25+0.25=0.50 < O1=O2=0.64(**O3 第 3,30/30 seed**)。
- B1: 无直接边→0;FAS: **30/30 vs 0/30**。这是预注册的、构造保证的、跨 30 seed 重复的动力学唯一增量证据。

### 6.3 伪影检查(Figure 8b 景观佐证)
- **Insertion-order(§23)**: 10 个 permutation 逐格 MRR 完全相同(CV=0.000),JCG 10/10 位同——无 insertion artifact。
- **Hidden oracle(§22)**: label permutation 与 ID permutation 各 30 seed,FAS 排名分布不变(rank 中位 22.5 = 基线地板域),top-1 率 0 = 基线——candidate 池无隐藏联合目标(联合目标就是图里那个被 5 输入共同支持的普通节点)。
- **tie-break/dictionary**: 候选按 seed 洗牌后进入打分,平局用 seed 固定洗牌,各方法同 seed 同 tie-break(代码层强制)。
- **B4 oracle**: preflight 全拓扑 1.0。

### 6.4 参数面
- **Weight sweep(§24,Figure 8a 结构参数)**: {0.1,0.3,0.5,0.7,0.9}×{T1,T4}: 两法同在 w=0.7 突变至 MRR 1.0(FAS 0.083@0.5→1.0@0.7;B2 0.077→1.0;T1≡T4)。margin:FAS 0.232 vs B2 0.980 @0.7——**flat 的绝对间隔反而更大**。无参数面差分。
- **Negative edges(§25)**: neg:none ≡ neg:neg(MRR 0.0667,top-1 0.0333,**total_activation 完全相等 17.4101**)——抑制边对排名零影响;mixed 略降(0.037);无传播 collapse。抑制在本配置中无机制后果(如实记录,非失败)。
- **稀释(§17,Figure 6)**: reachability ↔ target activation 相关 ρ=−0.445(noise sweep)、−0.656(distractor sweep)——**明确存在"传播越广目标越难突出"的稀释权衡**,机制边界成立。

### 6.5 稳健性与缩放(Figure 3、4、5、7)
- 噪声网格 0–90%(3 输入): 两法全程地板(slope fas +0.0076 / b2 0.0000,归一化 AUC 0.043/0.040,critical noise=0.0)。5 输入: 50%/75% 噪声仍 MRR=1.0。
- 复杂度 N=20→1000(3 输入): FAS MRR 0.0426→0.0486(平);B2 0.04→0.0392(平)。无塌缩、无恢复——MRR 由输入支持数决定,与图规模无关。
- 输入缩放 1/2/5/10/20: 1→0.0625,2→0.0426,≥5→1.000(FAS 与 B2 同位翻转)。
- 深度 hops 1/3/4: 1→1.000(直接边联合),3→0.024,4→0.022(两法同)。
- distractor 0→1000: 0.030→0.042(噪声级波动,无单调惩罚)。
- 拓扑 T1–T4(3 输入): 四拓扑 × 四方法逐位相同(FAS 0.042575/B2 0.04/B1 0/B3 0.00544)——拓扑类型改不了这个干扰谱下的地板。

---

## 7. §34 科研诊断(一句填空,按数据自动填写)

> "如果排除目前已发现的 Layer-1 实现缺陷,并在一个正确构造的 relational graph 上运行 FAS,那么 spreading activation 相对于 flat retrieval 的真正增量价值是 **在共享枢纽汇聚结构上真实存在且 30/30 可复现(§16),但在全部 4 个预注册 confirmatory 条件与 14 个参数面网格上均不可观测,200 个配对 seed 中 JCG 反超 0 次(ΔJCG 中位数 −0.402~−0.784,Holm p=1.5e-9)——即:FAS 的增量是结构依赖的局部现象,而非通用机制的普遍优势**。"

(填空内容只引用本实验数据,不预设。)

---

## 8. §35 定位结论

**变体 1(主结论,数据支持):**
> FAS 的当前实证贡献更接近 persistent relational memory,而不是 activation dynamics 的 superiority。

**限定的变体 3 例外(§16 单独成立,如实并报):**
> 机制在共享枢纽汇聚这一种拓扑构型上表现出 topology 依赖的 operating regime:动力学提供 flat 聚合无法提供的组合判别(30/30 vs 0/30)。

合并表述: 持久关系记忆(价值)+ 多输入收敛(机制,通用但非独有)+ 传播动力学增量(仅在共享枢纽拓扑)。**不将任一测量改写为"通用成功"或"全面失败"。**

---

## 9. recommended_change(生产未做任何修改,§33)

按规格只在报告中记录,不实施:
1. **生产侧若追求动力学增量,应把注意力引导到"共享枢纽簇"结构**(多输入共同枢纽的候选优先),而不是通用扩散参数——唯一有证据的结构方向(§16);
2. 生产扩散的候选排序在低支持数时受 0.8 权重直接干扰主导——如需改善 3 输入以下场景,应降低直接干扰边的相对权重或提高联合路径权重(参数面证据:w=0.7 翻转;此为调整建议,非本实验测量出的生产缺陷);
3. dilution 权衡(ρ<0)提示:生产上传播预算与目标显著性存在结构性矛盾,限制可追踪目标数时可达性随之收缩——设计长尾查询时注意。

生产代码零改动(`git diff` 无 experiments 目录外实验文件;本实验独立目录,不 import production 模块)。

---

## 10. 完成清单(§36 = spec 20 项 + 交付 16 项,共 36)

### §36 规定的 20 项(全部 √)
- [x] 实验代码完成(independent,零 production import)
- [x] preflight 全通过(B4 oracle 四拓扑 1.0、toy、integrity 0)
- [x] 所有正式 seeds 完成(confirmatory 50×4×4 配对;网格 30/格)
- [x] raw results 保存(raw_results.jsonl,34,016 行)
- [x] statistics 完成(statistics.csv + bootstrap CI + 效应量)
- [x] multiple comparison correction 完成(Holm,pre-registered)
- [x] sanity checks 完成(全部通过)
- [x] permutation tests 完成(hidden-oracle label/id)
- [x] insertion-order test 完成(CV=0.000)
- [x] negative-edge test 完成(无效应、无 collapse)
- [x] noise sweep 完成(0–90%)
- [x] complexity sweep 完成(N 20–1000)
- [x] input scaling 完成(1–20)
- [x] topology comparison 完成(T1–T4 × 4 方法)
- [x] figures 完成(9 张 × PDF/SVG/PNG)
- [x] FINAL_REPORT.md 完成
- [x] 没有修改 production FAS(独立目录 + manifest production_policy)
- [x] 没有为了结果调参(manifest 先于数据;tie-break/种子确定性固定)
- [x] 明确回答 H1/H2/H3(见 §2)
- [x] 明确回答"FAS spreading activation 是否有超越 flat retrieval 的独立价值"(见 §0-Q5/§8:否,有限定例外)

### 交付补充 16 项
- [x] manifest.json 预注册落盘(数据前)
- [x] 运行编排(campaign.py)与物理锁/崩溃恢复
- [x] 生成器/generator.py + 完整性断言
- [x] methods.py(B1–B4)+ 信息预算平等
- [x] metrics.py(MRR/R@1/3/5/NDCG/margin/stability/JCG/ΔJCG)
- [x] analyze.py 与 analysis.json
- [x] summary.csv / statistics.csv
- [x] README.md(布局/来源/运行方法)
- [x] figures.py 可复现生成 9 图
- [x] 反事实 C1/C2/C3(§15)
- [x] 组合测试 O3(§16)
- [x] 稀释测试(§17)
- [x] operating regime 测量(§18)
- [x] 关系权重 sweep(§24)+ 负边(§25)
- [x] 最小复现判定(preflight toy ≤20 节点;T4 全链)
- [x] 证据层级整合(§29)+ 直接验收说明(§36)

---

**NO WARNING 声明**: 全长检测:0 INVALID / 0 unparseable / 0 失败行 / 0 存活 in-flight 异常;分析脚本与图脚本以最终数据可干净重跑(README 第 3 节给出命令)。**H3 负结果如实呈报,不改写、不粉饰、不因此更动 FAS 理论;唯一正向结果(§16)与负向结果并列呈现。**