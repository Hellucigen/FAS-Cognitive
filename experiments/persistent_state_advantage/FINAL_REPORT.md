# FINAL_REPORT.md — Persistent State Advantage 实验

## 直接回答
```text
FAS demonstrates a conditional behavioral advantage: persistent relational state is causally necessary (FAS-full > FAS-reset, FAS-full > Direct) and survives context removal, but history/RAG baselines that can still read the experience stream remain equal or stronger under the budgets tested here — retrieval remains stronger when external memory is available.
```

## 总体结果

| Task | Condition | 关键指标 | n |
|---|---|---|---|
| T1 | B0-direct | 饥饿恢复=11/11 | 11 |
| T1 | B1-h2048 | 饥饿恢复=10/10 | 10 |
| T1 | B2-rag | 饥饿恢复=10/10 | 10 |
| T1 | FAS-full | 饥饿恢复=11/11 | 11 |
| T1 | FAS-reset | 饥饿恢复=11/11 | 11 |
| T1 | FAS-sham | 饥饿恢复=10/10 | 10 |
| T2 | B0-direct | success=0/11 tokens/run=2263 | 11 |
| T2 | B1-h128 | success=10/10 tokens/run=847 | 10 |
| T2 | B1-h2048 | success=9/10 tokens/run=3752 | 10 |
| T2 | B1-h512 | success=10/10 tokens/run=2424 | 10 |
| T2 | B2-rag | success=10/10 tokens/run=1104 | 10 |
| T2 | FAS-full | success=11/11 tokens/run=1182 | 11 |
| T2 | FAS-reset | success=0/11 tokens/run=5579 | 11 |
| T2 | FAS-sham | success=10/10 tokens/run=1256 | 10 |
| T3 | B0-direct | 失败重复=11.3 转向=3 | 11 |
| T3 | B1-h2048 | 失败重复=0.0 转向=2 | 10 |
| T3 | B2-rag | 失败重复=0.0 转向=10 | 10 |
| T3 | FAS-full | 失败重复=2.8 转向=11 | 11 |
| T3 | FAS-reset | 失败重复=7.6 转向=11 | 11 |

## 预注册检验(Holm 前的原始 p;主假设 H1 α=0.05)

| 假设 | n | wins | losses | p |
|---|---|---|---|---|
| H1 T2 FAS-full>FAS-reset (primary) | 10 | 10 | 0 | 0.001953 |
| H2 T2 FAS-full>B1-h128 | 10 | 0 | 0 | 1.0 |
| H2 T2 FAS-full>B1-h512 | 10 | 0 | 0 | 1.0 |
| H2 T2 FAS-full>B1-h2048 | 10 | 1 | 0 | 1.0 |
| H3 T2 FAS-full>B0 | 10 | 10 | 0 | 0.001953 |
| H5 T3 reset_fails>full_fails | 10 | - | - | 0.000977 |
| L4 T2 FAS-full vs B2-rag (not required) | 10 | 0 | 0 | 1.0 |

## 成功层级(§18)

- L1 FAS-full>FAS-reset: **PASS**
- L2 FAS-full>B0: PASS (p=0.001953)
- L3 FAS-full>history-limited: B1-h128 p=1.0; B1-h512 p=1.0; B1-h2048 p=1.0
- L4 FAS-full>RAG: FAIL/不成立 (不要求成立)

(数字与机制探针详见 mechanism_results / raw_results;逐层解释见 ARCHITECTURE_DIAGNOSTIC 与 DESIGN。)

## 图
- fig1_budget_curve.png:外部上下文预算 vs T2 成功率
- fig2_failure_transfer.png:T3 失败重复对比

## 分层解释(§21)

**Mechanism**:Phase-1 后持久状态形成并经审计(full 图含经历边、reset=基图
哈希一致、sham=节点无经历边);Phase-1 期间 10 条先验闭包边权重经
Hebbian 连续强化(CONSOLIDATION_AUDIT 的连续强化在此任务上自然复现)。
**Behavior**:上下文移除后,FAS-full 在 T2 达 10/10、FAS-reset 0/10
(H1 p=0.00195)——持久状态对行为是因果必要的;T3 失败重复 3.1 vs
7.5(H5 p=0.00098)——持久负证据改变未来行为。
**Comparison**:对 B0-direct 优势成立(H3);对 history/RAG 在
128/512/2048 三个预算下全部持平(H2 不成立)。

## 为什么预算曲线没有咬合(任务设计局限,如实报告)

经历流仅 465 token,且每行 obs 内嵌库存快照(如 inventory 含
oak_planks:4)——即使 h128 截断,尾部无关行的 obs 库存字段仍把
"oak_planks" 带进上下文并打开菜单门控。这是本任务设计的泄漏通道
(非作弊:对 FAS 与基线同等存在),它使"低预算伤害基线"的机制
在本任务中无法显现。若要让曲线咬合,需要更长的经历流(数千 token)
+ 去除 obs 中的库存回显——属于任务再设计,不在本轮范围内。

## §27 收官判定

本实验给出了 FAS 核心主张的最终证据形态:

- **成立且可辩护**:持久关系状态在上下文移除后仍然因果地改变行为
  (H1/H3/H5 全部显著;机制链 experience→persistent state→activation
  →behavior 完整闭合,含连续强化与负极性两条通道的自然复现)。
- **不成立**:在"外部记忆仍可读"的条件下,history/RAG 与 FAS 持平
  或更强——FAS 的当前价值不在替代外部记忆检索。
- 因此 FAS 的诚实定位:**一种具有可验证机制、明确边界与已观测
  行为效应(持久状态因果必要性 + 上下文压缩 + 目标维持)的
  persistent relational cognitive architecture**。
- 按任务书 §27:不再扩展"优势任务"搜索,本轮收官。
  若继续,唯一值得做的后续是"长经历流 + 无库存回显"的预算曲线
  重测(任务再设计,非机制修改)。
