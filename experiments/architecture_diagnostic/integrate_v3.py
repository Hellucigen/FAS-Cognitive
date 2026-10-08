# -*- coding: utf-8 -*-
# integrate_v3.py — 论文措辞重写(数字核对后;EN+ZH 同步)
import os

P = r"E:\Project\Fascinator\FAS_Paper_I"
edits_log = []


def rd(f):
    return open(os.path.join(P, f), encoding="utf-8").read()


def apply(f, pairs, label):
    s = rd(f)
    n = 0
    for old, new in pairs:
        if old in s:
            s = s.replace(old, new, 1)
            n += 1
            edits_log.append({"file": f, "old": old[:80], "new": new[:80]})
        else:
            print("MISS %s: %r..." % (f, old[:60]))
    wr(f, s)
    print("%s: %d/%d applied" % (label, n, len(pairs)))


def wr(f, s):
    open(os.path.join(P, f), "w", encoding="utf-8").write(s)


# ═══ EN 主文 ═══════════════════════════════════════════════════════
en = []

# 1. 标题
en.append((
    "\\title{\\bfseries From Embodied Experience to Reusable Relational Knowledge:\\\n"
    "An Auditable Unified-Graph Architecture and a Preregistered\\\n"
    "Boundary Analysis of Activation-Based Access}",
    "\\title{\\bfseries Persistent Experience-Derived State in an Auditable\\\n"
    "Cognitive Architecture:\\\n"
    "Formation, Persistence, and Measured Behavioral Effects}"))

# 2. 摘要 "ten extra" → "five extra"
en.append(("costs ten extra\nfailed attempts", "costs five extra\nfailed attempts"))

# 3. 摘要:移除 "Reusable Relational Knowledge" 的标题式重申
en.append((
    "Embodied agents accumulate experience faster than they consolidate it: "
    "action histories remain logs instead of becoming reusable knowledge.",
    "Embodied agents accumulate experience faster than they consolidate it: "
    "action histories remain logs instead of becoming persistent, "
    "experience-derived state."))

# 4. 新小节:audit-driven corrections + negative results + PSA 结果
# 插入位置:在 \section{Discussion} 前
en.append((
    "\\section{Discussion}",
    """\\subsection{Audit-driven corrections and negative results}
\\label{sec:audit-negative}

Four methodological corrections emerged during this work and are
reported as first-class contributions:

\\textbf{(C) Misattributed resumption.} The goal-resumption advantage
(9/10 vs 0/10, Section~\\ref{sec:res-campaign}) was initially labelled
``persistent intention.'' A zero-experience control reproduces the same
working set: the driver is the prior-closure edge re-lit by observation,
not an intention-lifecycle mechanism. The effect is relational
working-set salience, regime-dependent (short-history only).

\\textbf{(E) Assembly defect.} The goal-revision deficit (10.5 failed
repeats vs history's 1.0) was traced to a missing consumer: the
production event-frame's negative-polarity channel was not wired into
the campaign assembly. Wiring it reduced repeats to 7.2
($p{=}0.011$)---a partial repair, with residual inertia attributable
to design-level goal pressure.

\\textbf{(F) Consumer gap.} The self-model's goal state was written to
the graph (telemetry 5/5/5) but had zero production readers. Adding a
uniform state consumer changed autonomous behavior (gathers 1.0
$\\rightarrow$ 6.0), confirming the write-only hypothesis.

\\textbf{(T3) Presentation inequivalence.} The T3 advantage of FAS-full
over flat-store and no-activation controls (Addendum) was traced to
failure-evidence visibility: the control implementations structurally
excluded failure information from the decision context. An equivalence-
preserving revision is pending ([PENDING-REVISION-CONTROLS]).

\\textbf{Negative results preserved:} behavioral convergence (I) fails
at all budgets; the credit horizon (B) is 90\\,s; cross-episode
behavioral advantage over retrieval does not hold; history/RAG
baselines match FAS whenever the experience stream is short enough to
fit their budget.

\\section{Discussion}"""))

# 5. 5.6 C 段:加 regime-dependence
en.append((
    "This is the campaign's one clean comparative advantage for persistent "
    "relational state. Mechanism attribution:",
    "This advantage is regime-dependent (short-history streams only; with "
    "longer streams the baseline matches FAS). Mechanism attribution:"))

# 6. 5.6 E 段:加 "still weaker than history"
en.append((
    "reduced repeated failures from 10.5 to \\textbf{7.2} (one-sided Wilcoxon "
    "$p{=}0.011$)---statistically significant, still short of the baseline,",
    "reduced repeated failures from 10.5 to \\textbf{7.2} (one-sided Wilcoxon "
    "$p{=}0.011$)---statistically significant, but still far weaker than the "
    "history baseline's 1.0 repeats, confirming residual design-level goal "
    "pressure,"))

# 7. 5.6 T 段:加 T2b 全 10/10 + 呈现等价限制
en.append((
    "the combined working set crowds the failed action out of the top-8 "
    "entirely.",
    "the combined working set crowds the failed action out of the top-8 "
    "entirely. A corrected resumption probe (fresh world) shows all "
    "conditions---including the full-history baseline---at 10/10, confirming "
    "that the resumption advantage is regime-dependent and that the T3 "
    "comparison does not isolate graph structure or activation."))

# 8. Limitations 追加 (18)
en.append((
    "regime-dependent (short-history regime only).",
    "regime-dependent (short-history regime only). (18) The "
    "persistent-state advantage experiment (FAS-full vs FAS-reset, 10/0, "
    "p=0.00195) used a single task family with a 465-token experience "
    "stream; the budget curve did not separate conditions because "
    "inventory snapshots in the observation lines leak answer-bearing "
    "strings into truncated history. (19) FAS-sham (nodes without "
    "experience edges) achieves 9/9 on the goal task, indicating that "
    "node-name traces plus menu gating suffice at this task surface. "
    "(20) Flat-store and no-activation controls match FAS on "
    "goal-completion; the independent contribution of graph structure "
    "and spreading activation is demonstrated only for failure-informed "
    "behavior (T3), and even there the presentation inequivalence of "
    "the controls limits the attribution."))

# 9. "consolidation" → "continuous reinforcement"(如出现)
en.append(("consolidation", "continuous reinforcement"))
en.append(("Consolidation", "Continuous reinforcement"))

# 10. 移除禁用表述
en.append(("continual learning", "persistent learning across episodes"))
en.append(("cross-episode reasoning", "cross-episode relational salience"))

for pair in en:
    old, new = pair
    if old in s_tmp:
        s_tmp = s_tmp.replace(old, new, 1)
        edits_log.append({"file": "main.tex", "old": old[:70], "new": new[:70]})
    else:
        print("EN MISS: %r" % old[:60])
wr("main.tex", s_tmp)
print("EN main.tex done (%d edits)" % len(edits_log))

# ═══ ZH 主文 ════════════════════════════════════════════════════════
zh = []
zh.append((
    "\\title{\\bfseries 从具身经历到可复用的关系知识：\\\n可审计的统一图谱架构与基于激活访问的\\\n预注册边界分析}",
    "\\title{\\bfseries 可审计认知架构中的持久经验派生状态：\\\n形成、持久性与可测量的行为效应}"))
zh.append(("多付出十次失败尝试", "多付出约五次失败尝试"))
zh.append((
    "具身智能体积累经历的速度远快于巩固经历的速度：行动史停留为日志，而未成为可复用的知识。",
    "具身智能体积累经历的速度远快于巩固经历的速度：行动史停留为日志，而未成为持久的经验派生状态。"))
zh.append((
    "\\section{讨论}",
    """\\subsection{审计驱动的更正与负结果}
\\label{sec:audit-negative}

四项方法学更正在本工作中浮现,作为一等贡献报告:

\\textbf{(C) 重拾的误归因。}目标重拾优势(9/10 vs 0/10)最初被标记
为``持久意图''。零经历对照复现同一工作集:驱动是先验闭包边被观测
再点亮,而非意图生命周期机制。效应是关系工作集显著度,状态依赖
(仅短历史)。

\\textbf{(E) 装配缺陷。}目标修订 deficit(失败重复 10.5 vs history 1.0)
被追溯到缺失的消费者:生产事件框架的负极性通道未接入战役装配。
接线后降至 7.2($p{=}0.011$)——部分修复,残余惰性归设计层目标压力。

\\textbf{(F) 消费者缺口。}自我模型的目标状态写入了图谱(遥测 5/5/5)
但生产零读取者。加统一状态消费者改变自主行为(gathers 1.0→6.0),
确认只写假说。

\\textbf{(T3) 呈现不等价。}FAS-full 对扁平存储与无激活对照的 T3
优势被追溯为失败证据可见性:对照实现结构性地把失败信息排除出
决策上下文。等价呈现的修订版对照待完成([PENDING-REVISION-CONTROLS])。

\\textbf{保留的负结果:}行为汇聚(I)在所有预算下失败;信用视野(B)
为 90\\,s;对检索的跨经历行为优势不成立;history/RAG 在经历流足以
放入其预算时与 FAS 持平。

\\section{讨论}"""))
zh.append((
    "该优势是状态依赖的(短历史流;更长流下基线追平 FAS)。机制归因:",
    "该优势是状态依赖的(仅短历史流;更长流下基线追平 FAS)。机制归因:"))
zh.append((
    "统计显著,但仍远弱于 history 基线的 1.0 次重复,确认残余设计层目标压力,",
    "统计显著,但仍远弱于 history 基线的 1.0 次重复,确认残余设计层目标压力,"))
zh.append((
    "组合工作集把失败动作完全挤出 top-8。",
    "组合工作集把失败动作完全挤出 top-8。修正后的重拾探针(新世界)显示"
    "全条件——包括全历史基线——均为 10/10,确认重拾优势是状态依赖的,"
    "且 T3 对照未分离图结构或激活。"))
zh.append((
    "状态依赖的(仅短历史状态)。",
    "状态依赖的(仅短历史状态)。(18) 持久状态优势实验(FAS-full vs "
    "FAS-reset,10/0,p=0.00195)使用单任务族与 465-token 经历流;预算"
    "曲线未分离条件,因 obs 行内嵌库存快照把答案承载字符串泄漏进截断"
    "历史。(19) FAS-sham(节点无经历边)在目标任务达 9/9,说明节点名"
    "痕迹+菜单门控在该任务表面已足够。(20) 扁平存储与无激活对照在"
    "目标完成上与 FAS 等效;图结构与扩散的独立贡献仅在失败信息行为"
    "(T3)上被证明,且对照的呈现不等价限制了归因。"))
zh.append(("consolidation", "continuous reinforcement"))
zh.append(("Consolidation", "Continuous reinforcement"))
zh.append(("continual learning", "persistent learning across episodes"))

s_tmp = rd('main_zh.tex')
for old, new in zh:
    if old in s_tmp:
        s = s.replace(old, new, 1)
        edits_log.append({"file": "main_zh.tex", "old": old[:70], "new": new[:70]})
    else:
        print("ZH MISS: %r" % old[:60])
wr("main_zh.tex", s_tmp)
print("ZH main_zh.tex done (%d edits total)" % len(edits_log))

# 保存编辑日志
json.dump(edits_log, open(os.path.join(P, "_edits_log.json"), "w",
           encoding="utf-8"), ensure_ascii=False, indent=1)
