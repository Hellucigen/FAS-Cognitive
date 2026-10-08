# REPAIR_TEST_RESULTS.md — 最小修复验证结果(2026-10-02)

## 1. 回归门槛(全部通过后才计为有效修复)

| 检查 | 结果 |
|---|---|
| pytest tests/ | **82 passed** |
| scripts/run_all_regression.py | **108/108 PASS**(墙钟 171s) |
| 默认关阴性对照(audit_probes 三探针 diff) | 唯一差异 = P0-a 截断修复本身(结果节点 id 完整化);C/E/I payload 逐字节不变 → ef_context/goal_attention 默认关时零行为变化 |

## 2. F v7(consumer 验证,零 LLM,30 run)

被试变量:autonomy `_obtain_goals` 的 self-graph consumer
(goal_attention ON/OFF;两条件都做目标同步;自由相清空 registry,
consumer 成为唯一目标来源)。

| 历史 | consumer OFF | consumer ON |
|---|---|---|
| failure(iron_ingot) | 派生目标 0/5,gathers=1.0,total=3.0 | **派生目标 5/5,gathers=6.0,total=7.0** |
| success(oak_planks) | 0/5,0.0/2.0 | 派生 5/5,0.0/2.0(库存已达成→无需行动) |
| novel | 0/5,1.0/3.0 | 0/5,1.0/3.0(先验闭包未注入时 物品: 节点缺席→consumer 无匹配,见注) |

**判定:P1-F 修复有效**——self-graph 当前目标状态首次驱动自主行为
(failure 历史:gathers 1.0→6.0,行为差异从"逐位相同"变为显著分离)。
novel 注:派生依赖 物品:X 节点已在图中(先验闭包在首个感知拍注入,
novel 无练习拍→自由相初期缺席);属 consumer 的输入前提,非缺陷,
但记录为已知边界。

## 3. E2(P1-E 修复验证,LLM 决策头,n=10)

| 条件 | mean fail_gathers | pivot | mean pivot step |
|---|---|---|---|
| direct | 11.9 | 1/10 | 3.0 |
| fas(无负极性,现役) | 10.5 | 7/10 | 4.7 |
| **fas-ef(负极性接入)** | **7.2** | **9/10** | **3.1** |
| history(基线) | 1.0 | 10/10 | 1.0 |

- fail_gathers fas-ef < fas:单侧 Wilcoxon **p=0.0107**(n=10)。
- 方向正确、统计显著、**部分恢复**(10.5→7.2,距基线 1.0 仍远)——
  与审计预测精确一致:E2(装配缺失)是主因但非全部;
  E1(goal 恒压设计)与 E3(±0.05 弱)残留。
- 数据完整性注:raw_E.jsonl 68 行 = 修复前 30 + 修复后 38
  (2 个 infra 失败 run 由修复前同键行补足 n=10;已按 (cond,seed)
  后写优先去重)。

## 4. C 退化检查(修复的副作用)

C 不经失败路径(练习与测试全成功)且 goal_attention 不在决策头装配
生效 → 代码路径论证 + 探针 diff 已覆盖(payload 逐字节不变)。
未重跑 LLM(阴性对照在确定性层更强)。

## 5. 修复后状态

- F:write-only 缺口已闭合(consumer 存在、默认关、验证有效)。
- E:失败证据消费通道已接入装配层并显著改善;残余 inertia 归 E1/E3
  (设计侧),保留为论文边界。
- 生产默认行为:goal_attention=False、事件框架 enabled=False 不变
  → 论文已冻结数字全部有效;修复后数据标注为 repair-round。
