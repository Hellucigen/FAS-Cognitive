# PAPER_IMPACT_REVISION.md — 对论文措辞的影响(仅建议;不改论文)

## 1. [PENDING-REVISION-CONTROLS] 占位如何落

修订对照实验**已执行到预检门槛即停止,没有任何行为结果**。
INTEGRATION_NOTES.md 的三分支中,应走**"无法判定"分支的弱化版**:
不是"结果混合",而是"呈现等价对照在冻结的朴素规则下不可实现,
实验未运行"。建议措辞(EN):

> The equivalence-preserving revision was attempted under a frozen
> preregistration but stopped at its presentation-equivalence gate:
> under the naive 2-hop adjacency rule the failure-evidence node is
> structurally reachable yet truncated out of the 8-slot budget
> (discovery weight 0.5 vs slot-8 weight 0.8), so no
> presentation-equivalent graph control was realized and the
> independent contribution of graph structure and activation remains
> untested.

ZH:

> 等价呈现的修订版对照在冻结预注册下已尝试,但在呈现等价门槛处
> 停止:朴素 2 跳邻接规则下,失败证据节点结构可达却被 8 槽预算
> 截断(发现边权 0.5 对第 8 槽权重 0.8),呈现等价的图对照未能
> 实现,图结构与激活的独立贡献仍未检验。

**不得**写成"修订对照确认/否定了任何假设"——没有数据。

## 2. 各措辞的影响

- **图结构 / spreading activation 的独立贡献**:维持现状
  (main §5.6 限定 (i)、§5.8 (T3) 条、限制 (20)、6.1 未证明
  清单、摘要"独立贡献则未定")——全部仍然准确,无需改动方向;
  只需把"待完成/pending"改为"已尝试,在门槛处停止"(更诚实,
  且是方法学加分项:门槛真的拦下了一次不等价的对照)。
- **失败证据可见性**:可增强一句——预检证明失败信息的缺失是
  距离 × 排序权重 × 预算的合取通道(ADDENDUM §D 的"1 跳不可达"
  表述过窄)。这支持"决策时可见的失败证据改变行为"的既有主张,
  不支持图/机制主张。
- **负极性标记(H_R3)**:未检验;论文中任何涉及"负极性通道
  贡献待分离"的句子保持开放。
- **方法学叙事**:本轮是预注册纪律的正面实例(硬门槛在 0 次
  LLM run 前拦下了一个不可比对照),值得在 §5.8 或补充材料
  一句话提及;若入补充材料,新增 S16 附注或 S18(由论文整合
  会话决定,不在本任务范围)。

## 3. T3 在论文中的放置

维持 ADDENDUM 的降级建议:T3 比较作为"接口/呈现效应"证据
(§5.6 限定 (i) + 限制 (20) + §5.8 (T3) 条),不作为
graph/activation 比较证据。fig2(failure transfer)图注保持
failure-evidence visibility 措辞。

## 4. 数字供给清单(若论文整合会话要落上述措辞)

可直接引用、全部可溯源到本目录脚本输出(report_numbers.json /
preflight_report.json):预检 10 seeds、60 条失败、C3b 0 失败、
失败节点 2 跳集 51 节点排 43、发现权重 0.5、第 8 槽 0.8、
C4b/C4c 字符比 0.349、C3b 字符比 0.753、C1 槽位 7、C3b 槽位 6、
C4b 负权边 2 / C4c 0、正式 run 数 0、回归 82/108。
