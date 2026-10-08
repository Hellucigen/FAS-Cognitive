# PAPER_IMPACT.md — 对照实验对论文的影响(仅建议,不直接改论文)

## 逐条影响

### 标题 / 中心主张
- 本实验**不支持**把"图结构 + spreading activation"作为一般性行为
  优势来源的宽泛表述:在 goal-completion 任务(T2)上,扁平持久
  存储与 1 跳邻接与完整 FAS 等效(未检出差异,功效有限)。
- 建议中心表述收敛为:**persistent experience-derived relational
  state**,并把 activation/graph 的贡献限定到其被独立证明的场景
  (failure-informed behavior)。

### "relational"
- 保留,但加限定:relational 结构的独立行为价值目前仅在 T3
  (失败抑制)上有显著证据;T2 上 relational 与 flat-store 等效。

### "graph"
- 不建议在主张层面使用 "the graph structure yields behavioral
  advantage" 的无限定句;改为 "graph representation supports
  failure-informed adaptation beyond flat persistence"。

### "spreading activation"
- 现有边界分析(H3 被拒、shared-hub 优势区、C4 对照)一致指向:
  activation 的独立贡献是**条件性的**。本实验新增一条正向条件证据
  (T3,C1>C4,p_bonf=.0156)——可以写,但必须与 T2 的"未检出"
  并列,不得只报 T3。

### "consolidation"
- 不受影响(CONSOLIDATION_AUDIT 已区分连续强化与阈值化晋升;
  C3/C4 对照不触及该结论)。

## 需要修改的具体章节(建议清单)

1. **Abstract**:若含"graph/activation 带来行为优势"的宽句 → 加
   "under failure-informed conditions"限定。
2. **Introduction 贡献列表**:比较级主张处补一句"activation/graph 的
   独立贡献经 flat-store 与 no-activation 对照分离,仅在
   failure-informed 任务显著"。
3. **Results — comparative behavioral evidence 小节**:新增
   "Controls (flat store, activation-off)" 段落,报告 T2 等效
   (0 discordant)与 T3 显著差(HL=−9,CI[−11,−7.5],
   Bonferroni p=.0156),并引用本目录 RESULTS.json。
4. **Discussion — 机制归因段**:C 的归因(relational working-set
   salience)需补注:T2 上该 salience 可被扁平存储替代;
   不可替代性仅在 T3 成立。
5. **Limitations**:新增一条:"对照实验 n=10、单任务对;
   T2 的门控表面性使等效结论受任务设计限制;C3 规则对失败
   证据的排序偏向已声明。"
6. **补充材料**:新增 S18(本实验的预注册、审计、RESULTS、
   INTERPRETATION 全文)。

## 不需要改的
- 论文已有的负结果(H3 拒绝、D-flat、I 行为失败)不受影响;
- K 压缩、C 目标维持、E/F 修复证据不受影响。
