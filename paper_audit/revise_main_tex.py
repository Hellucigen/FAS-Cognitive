# revise_main_tex.py — PHASE 7: 论文重构（routing 实验 + 声明联动修订）
import io

P = r"E:\Project\Fascinator\FAS_Paper_I\main.tex"
t = io.open(P, encoding="utf-8").read()
n0 = len(t)

# 1. 摘要补充路由实验结果
old1 = (r"Equally important are the boundaries we characterize: task-completion rates are systematically insensitive to these internal differences (closure masking); broader spreading activation does not improve---and can slightly slow---task completion; with zero prior knowledge the agent locks into unproductive goal-directed exploration before any attributable experience is generated.")
new1 = (r"Equally important are the boundaries we characterize: task-completion rates are systematically insensitive to these internal differences (closure masking); broader spreading activation does not improve---and can slightly slow---task completion; with zero prior knowledge the agent locks into unproductive goal-directed exploration before any attributable experience is generated. Finally, in a paired LLM decision-head experiment (five information conditions, four task types, twenty matched seeds, identical menus and budgets), supplying the LLM with FAS-selected graph context produced no measurable advantage over raw history, embedding retrieval, or even randomly sampled memory---at 1.4--2$\times$ the input-token cost---because in all tested tasks the information needed for each decision was already present in the observation. Explicit resource routing paid off nowhere that information asymmetry was absent; we report this rejection of our own hypothesis as a boundary condition on when structured cognitive routing can help an LLM-based agent.")
assert old1 in t, "abs anchor missing"
t = t.replace(old1, new1)

# 2. Introduction 贡献 4 扩写
old2 = (r"\item \textbf{A systematic characterization of boundaries}: zero-prior experience starvation, task-closure masking, and the exact scope of diffusion's contribution (Section~\ref{sec:negative}). These negative and boundary results are reported as first-class findings.")
new2 = (r"\item \textbf{A systematic characterization of boundaries}: zero-prior experience starvation, task-closure masking, the exact scope of diffusion's contribution, and a paired five-condition test in which FAS-selected context \emph{failed} to outperform raw history, retrieval, or randomly sampled memory for an LLM decision head (Section~\ref{sec:negative}). These negative and boundary results are reported as first-class findings.")
assert old2 in t, "intro anchor missing"
t = t.replace(old2, new2)

# 3. 方法章加 4.6 决策头协议（插在 Reproducibility 前）
old3 = (r"\subsection{Reproducibility}" + "\n" +
        r"Fixed seeds; single-process deterministic execution; no wall-clock-dependent logic in the measured paths (one known exception is documented above); no LLM; archived raw runs with content summaries; a regression suite of $104$ tests passing at the time of writing. Local paths and endpoints are omitted from the paper and recorded in the artifact documentation.")
new3 = (r"\subsection{LLM decision-head protocol (routing experiment)}" + "\n" +
        r"One experiment family places a real LLM at the decision point. A single model (temperature $0$, fixed output budget, one call per decision) chooses one action from a menu derived from the environment; the menu, environment state, task facts (recipe prior), experience stream, and per-decision budget are identical across five information conditions that differ \emph{only} in what context the model receives before deciding: (A)~none; (B)~the raw last-8 experience entries; (C)~top-5 retrieved entries by embedding similarity (pre-registered $k{=}5$); (D)~context selected by the real FAS machinery (graph ingestion of each step, spreading, Top-$k$ focus, and the three-layer cognitive-demand/gap/routing analysis); (E)~randomly sampled memory entries (counterfactual control). Three ablations of D remove diffusion, demand annotation, or graph structure. Four task types (knowledge reuse, distractor suppression, environment-change re-routing, competing goals) are run with twenty matched seeds per condition; all runs, prompts, selected contexts, and token counts are archived. Two task designs were corrected before the main run (a leading instruction in the distractor task; an unobservable relocation in the re-routing task), and one ingestion defect in our harness was found after the first full run and repaired with a full re-run of the affected conditions (Section~\ref{sec:negative})." + "\n\n" +
        r"\subsection{Reproducibility}" + "\n" +
        r"Fixed seeds; single-process deterministic execution; no wall-clock-dependent logic in the measured paths (one known exception is documented above); archived raw runs with content summaries; a regression suite of $104$ tests passing at the time of writing. The routing experiment follows the same archive discipline with per-decision JSON traces. Local paths and endpoints are omitted from the paper and recorded in the artifact documentation.")
assert old3 in t, "method anchor missing"
t = t.replace(old3, new3)

# 4. 新 §10 路由结果章（插在 Discussion 前）
old4 = (r"\section{Discussion}" + "\n" + r"\label{sec:discussion}")
new4 = (r"\section{Explicit Resource Routing vs.\ LLM Baselines}" + "\n" +
        r"\label{sec:routing}" + "\n" +
        r"The architecture claims that its value lies not in storing more information but in allocating attention over what is stored. This claim implies a testable hypothesis: given a fixed LLM, environment, task, prior, and budget, context selected by FAS's activation and routing machinery should improve information \emph{utilization} relative to raw history, static retrieval, or random selection. We ran this test under the paired decision-head protocol of Section~\ref{sec:method} ($n{=}20$ matched seeds; Wilcoxon signed-rank; effect size $r$)." + "\n\n" +
        r"\textbf{The hypothesis is rejected.} FAS-selected context produced no measurable advantage on any primary metric in any task. On knowledge reuse, FAS reached the goal in 85\% of episodes against 100\% for every baseline (the deficit is not significant at $n{=}20$, $p{=}0.083$, but the direction is consistent); on re-routing it was significantly \emph{slower} than the history condition (median re-route 1.1 vs.\ 0.2 decisions after the change, $p{=}0.039$); on distractor suppression and competing goals all conditions were statistically indistinguishable. Because the FAS context block costs 1.4--2$\times$ the input tokens of the baselines, context efficiency (success per 1{,}000 input tokens) is significantly \emph{worse} in most task--condition cells (e.g., $0.49$ vs.\ $0.87$ on reuse, $p{<}0.001$, $r{=}0.79$). Removing diffusion, demand annotation, or graph structure changes nothing: no ablation differs from full FAS, so the null is not carried by a removable component." + "\n\n" +
        r"\textbf{Why.} Three mechanisms, all diagnostic rather than exculpatory. First, every task in the suite is \emph{fully observable at decision time}: the environment state shown to the LLM each step already contains the inventory, nearby resources, and task facts, so the marginal value of any memory context is near zero while its token cost is real. Routing value requires information asymmetry between what the observation shows and what the decision needs; we did not create such asymmetry, and the result says the mechanisms as implemented do not manufacture it. Second, the FAS focus set mixes action and outcome nodes with entities (an intrinsic property of co-activation dynamics), yielding a selected-context precision of 0.51--0.62 against 1.0 for embedding retrieval on these small stores. Third, in short episodes most memory entries concern the goal chain anyway, so even a \emph{random} memory sample is mostly relevant and matches or beats FAS on efficiency---selection quality only has room to matter when the store is large and distractor-rich." + "\n\n" +
        r"\textbf{What this does and does not show.} It does not show that graph-based routing cannot help LLM agents; it shows that this implementation, in observable-state tasks of this scale, does not---and it identifies the condition under which routing could matter (information asymmetry between decision-time needs and current observations) as the design target for future work. Within the evidence-first standard of this paper, the routing hypothesis joins the negative results as a characterized boundary." + "\n\n" +
        r"\section{Discussion}" + "\n" + r"\label{sec:discussion}")
assert old4 in t, "discussion anchor missing"
t = t.replace(old4, new4)

# 5. 讨论加一段
old5 = (r"\textbf{What remains unresolved.}")
new5 = (r"\textbf{What the routing experiment teaches.} The decision-head study operationalized our own headline claim---\emph{explicit, structured resource allocation over stored information}---and the claim failed its test. The failure is informative in two directions. It bounds the architecture's value: FAS should be presented as a substrate for grounded knowledge acquisition and persistence, not as a context-selection engine for LLMs, at least until routing demonstrates value under information asymmetry. And it names the missing experimental ingredient: tasks in which the decision-relevant fact is \emph{not} in the current observation (partially observable states, delayed consequences, cross-session dependencies), designed without favoring either side." + "\n\n" +
        r"\textbf{What remains unresolved.}")
assert old5 in t, "discussion para anchor missing"
t = t.replace(old5, new5)

# 6. Limitations 补路由两条
old6 = (r"(8) The event-framework behavioral result was obtained with an experiment-layer assembly of a default-off production mechanism.")
new6 = (r"(8) The event-framework behavioral result was obtained with an experiment-layer assembly of a default-off production mechanism. (9) The routing experiment's tasks are fully observable at decision time; its negative result is conditional on that regime. (10) The decision-head protocol measures context-selection value with the FAS loop not controlling execution; a full-agent LLM-in-the-loop evaluation was out of scope.")
assert old6 in t, "limitations anchor missing"
t = t.replace(old6, new6)

# 7. 声明表加 U7 行
old7 = (r"U3 Interventional causality & U & No do-test (\S\ref{sec:limits}) \\")
new7 = (r"U3 Interventional causality & U & No do-test (\S\ref{sec:limits}) \\" + "\n" +
        r"U7 FAS routing adds value beyond LLM memory/retrieval & U & Five-condition paired test: no advantage, 1.4--2$\times$ tokens (\S\ref{sec:routing}) \\")
assert old7 in t, "claims table anchor missing"
t = t.replace(old7, new7)

io.open(P, "w", encoding="utf-8").write(t)
print("revised: %d -> %d chars" % (n0, len(t)))
