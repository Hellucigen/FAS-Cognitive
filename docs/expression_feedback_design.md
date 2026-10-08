# 表达反馈的图谱内机制（outcome 架构升级，2026-09-19）

## 核心哲学

> FAS 不再被一个 outcome 分类器告诉"用户刚才喜欢不喜欢"。
> 她说了一句话 → 这句话成为经历（图谱事件）；用户又发生了一件事 →
> 两件事在时间轴与统一图谱中产生关系；关系改变激活、奖励、惊讶、
> 激素与行为倾向；于是下一次她的行为发生变化。

**Outcome 从先验分类变成后验关系。**

## 新链路

```
FAS 表达（含选择沉默）
  ↓ record_expression（app 回合末）
表达事件节点（episodic，type=expression_event）
  ├─ Haru -[经历]-> 表达        （hub 白名单内合法）
  ├─ 表达 -[涉及]-> 行为:X       （行为:追问/分享/… 既有 disposition 词表节点）
  ├─ 表达 -[关于]-> 话题×        （只连图内已存在节点，不造第二套事实）
  ├─ 锚定激活 0.4 + mark_active  （表达本身就是激活源）
  └─ _反馈注视 指针（graph 内存活，跨重启）+ buffer 兼容视图
  ↓ 下一轮用户输入走既有管线（解析→FAISS 召回→activate→diffuse）
pre_baseline（activate 前采样）→ ……扩散……→ observe_response（扩散后）
  ├─ 共激活增量 Δ=表达激活(后)-表达激活(前) ≥ 0.15 → 回应成立
  │   （时间越久锚激活经既有回合间衰减越淡 → 关联强度天然衰减；
  │     用户重新提及原话题会把结构再次点亮 → 隔 6h 也能成立）
  ├─ 会话控制指令（dialogue_signals 既有检测）→ 也算指向上一表达的回应
  ├─ 成立 → 用户表达事件节点 + [对话]边 + SocialFeedback 事件节点
  │         （基于/关于边；读本轮共激活情绪节点 valence 属性合计）
  │         反馈节点自身 mark_active → 进入下一轮注意力场
  └─ 不成立 → 什么都不记（未观察到结果 ≠ neutral）
派生解释层（只为 reward/日志兼容）：图态读取 →
  control→rejected / emo_valence≤-0.5→rejected / 前行为=ask+回应→engaged /
  emo_valence≥+0.5→amused / dialogue_act=thanking→accepted, agreement→recognized /
  其余→continued_discussion
  ↓
reward_system.evaluate/release/modulation → 激素/RPE 学习率
  ↓
disposition_store.apply_experience（情境-[倾向]->行为 边权更新，只此一路）
  ↓
dialogue_decision（既有输入口：last_outcome_negative=派生 legacy 投影 +
  反馈节点激活回流进注意力场）→ 下一次行为竞争
```

## 退役清单

| 旧机制 | 处置 |
|---|---|
| `classify_outcome`（抵触/积极词表、Jaccard、prev-ask 规则）| 生产零调用；函数保留标注 DEPRECATED（旧测试/回滚参考） |
| `_POSITIVE_MARKERS/_NEGATIVE_MARKERS` | 不再决定 outcome；情绪语言经既有情绪节点/共振机制参与激活 |
| `gap>240min → neutral/timeout` | 删除；Δt 作为时间轴/边事实记录，关联强度由既有扩散衰减机制承担 |
| `classify_social_outcome((polarity,detail))` | 生产不再调用；social 标签直接由图态派生（复用 SOCIAL_VALENCE 词表） |
| buffer 纯内存记录、重启即盲 | 真相入图（事件节点+注视指针跨重启存活）；buffer 降为反思兼容视图 |

## 文件

- 新增 `expression_feedback.py`（record_expression / pre_baseline /
  observe_response / _derive——零 LLM、零文本分类器）
- `self_graph.py`：`EMOTION_VALENCE`（情绪概念的属性，-1~+1）
- `app.py`：情绪 bootstrap 补 valence 自愈；回合内
  pre_baseline→observe 替换规则标注块；表达侧 record_expression 替换
  add_expression
- `disposition_store.classify_outcome`：DEPRECATED 标注
- 测试 `tests/test_expression_feedback.py`（案例 A-E + 词表退役静态契约）
