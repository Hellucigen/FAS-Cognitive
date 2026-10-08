# B_FINDINGS.md — 归因窗调用链与意图性判定

## 1. 90s 的实际调用链

```text
ActionManager.on_settled(action, result, success)
  → _emit_result_event → timeline.append(EVENT_RESULT/OBSERVATION)
  → causal.record_action(evt)                    experience.py:488
      → window_for(evt):                          :407
          ACTION_WINDOW_S[subject] or ACTION_WINDOW_KIND_S[kind]
          or ASSOC_WINDOW_S
          # gather_resource / craft_item / break_block / communicate ...
          # = 90.0(显式数据表,:79-88)
          # ASSOC_WINDOW_S = 8.0(默认,:73)
      → deadline = evt.ts + window
      → 候选扫描:events_since 限定 ts ≤ deadline
      → _subject_relevant(§七主体门,:417)
      → _score_candidate(MIN_SCORE=0.60;时间接近分按窗归一,
        dt > ⅔窗 → 低于资格线)              :439-443
      → 未到期:pending 挂起;_on_timeline_event 按事件 ts 推进关窗
      → _finalize → 聚合 → (s≥5, conf≥0.80)晋升
```

超窗拒绝点:`_match` 的窗口过滤(事件 ts > deadline 永不成为候选)
+ `_score_candidate` 的 ⅔窗资格线。无旁路。

## 2. 实验曲线与语义一致

| delay | inventory 归因 | self-success 归因 | 语义解释 |
|---|---|---|---|
| 0 / 32s | 10/10 | 10/10 | 窗内且 ≤⅔窗(60s) |
| 80s | 10/10 | 0/10 | 窗内(90s)但 >⅔窗:inventory 观察分仍过线,self-state 事件(cons+0.5s≈80.5s)不过线 |
| 160s | 0/10 | 0/10 | 超 90s 窗 |

曲线形状与代码语义逐点吻合 → 实现正确,边界即设计。

## 3. 意图性判定:**intentional semantic boundary**

证据:
1. `experience.py:73-78` 注释明确记录参数的设计理由(8s 窗曾把
   §8 核心闭环"系统性筛空",故对获取/制作/言语类显式提到 90s)
   ——参数是被思考过并调过的,不是遗留默认值。
2. MIN_SCORE/⅔窗/§七主体门是配套语义族,单独改 90s 会破坏
   资格线语义。
3. 窗口大小本质是"主体相关性门"的时距投影:防止"行动后一切
   变化皆后果"。放宽到长程需要的是不同的信用分配机制
   (如反事实/干预测试),不是更大的窗。

## 4. 补偿机制排查(是否存在长程信用接管者)

| 候选 | 判定 |
|---|---|
| CausalLearner.action_priors | 成功率记忆,无跨事件因果链 |
| Hebbian 共激活 | 无归因语义 |
| ExperienceTimeline | 纯存储,无评分 |
| ledger 先验(±0.05) | 单步结果加成,非延迟信用 |
| CI/意图层 | 无因果评分输入 |

**无补偿机制** → 长程(>90s)信用分配在当前架构内不可得,
归为 Architecture-level limitation(论文表述:"the attribution
window is the architecture's credit horizon")。

## 5. 结论
- 不修改 90s(任务书 §8 铁律 + 本审计判定为语义边界)。
- 论文表述:作为架构边界报告,并把"反事实/干预式长程信用"列入
  future work(与既有 Limitation 1 的"无 do 式检验"衔接)。
