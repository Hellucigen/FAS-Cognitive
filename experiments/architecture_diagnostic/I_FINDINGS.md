# I_FINDINGS.md — 多经验汇聚的表示审计(Layer 1-6)

## Layer 1 — Knowledge:通过
物品:oak_planks/物品:stick/配方:*/动作:*/结果:* 节点全部在图
(探针 P-I;EXCL_I 排除目标配方后,两条练习链的知识仍由经历写回建立)。

## Layer 2 — Relations:通过(带噪声)
- 动作→结果 边存在:`动作:gather_oak_log→结果:got:oak_log`、
  `动作:craft_stick→结果:crafted:stick`。
- 噪声:同 obs 行的全部实体互连(动作:gather_oak_log~dirt、~实体:hunger)
  ——共现边把动作语义稀释。

## Layer 3 — Activation:通过
练习链节点激活 2.1–4.7;多输入汇聚的机制层证据(JCG 200/200、
84 格扫描)不受本轮影响。

## Layer 4 — Working set:纯激活 Top-k
`focus(k) = eng.get_topk(k)`,无结构加权、无意图提升、无失败抑制。
craft_stick(#8)进入 Top-8,但其结果节点(act≈0.2)进不来——
关键边在"选中集内部边"截断下缺席。

## Layer 5 — Serialization:丢失面清单

| 信息 | 图中存在 | 进 payload |
|---|---|---|
| 节点 id | ✓ | ✓ |
| activation | ✓ | ✓ |
| 关系类型/方向 | ✓(边对象) | 仅选中集内部边 ≤8;关键跨节点边缺席 |
| created 时间戳 | ✓(Node/Edge 均有) | **✗** |
| 结果值 | ✓(结果节点 id 内嵌字符串) | 仅当结果节点进 Top-8 |
| 事件顺序 | 部分(时间戳可推) | **✗** |
| episode 身份 | **✗(schema 不存在)** | ✗ |
| 因果评分 | ✓(CausalLearner 侧) | ✗(不在此装配) |

## Layer 6 — LLM 实际看到什么
`[cognitive-context mode=full]` + 8 行 `- <id> (act <x>)` +
demand/gap/routing 全零摘要。**没有任何一行同时包含
"动作→结果"与"该结果解锁的下一步"。**

## 关键判定(Q7):时序结构能否从图唯一恢复?
- **数据层:部分可恢复**(Node/Edge.created 时间戳存在;动作→结果
  边存在)。若消费端做时间排序+链提取,理论可重建
  "A→X→Y→因为 Y→Z"。
- **表示层:当前 schema 不把情节顺序当一等对象**:episode 身份
  不存在;共现边抹平行内时间;跨行顺序只藏在时间戳字符串里,
  序列化器不使用。
- **消费层:序列化器丢弃时间戳与边语义**(rep 研究 0/30 证明补
  结构块与预算都不够)。

**结论:PRIMARY = Interface/Representation(序列化+叙事形式缺失);
SECONDARY = Architecture(schema 无情节一等对象——这属于设计选择,
修复它=架构变更,超出最小修复权限)。**

## 对论文的表述建议
I 的负结果应表述为:"经验图状态经当前决策接口呈现时,不保留
原始经历流的时序叙事结构;在 k(8/16/32)与结构补块(rich)均
无效后,该接口的叙事信息损失是行为层汇聚失败的主因假设——
修复需要叙事式图上下文或非 LLM 消费者,属于消费者架构问题
而非机制缺陷(机制层证据完整)。"
