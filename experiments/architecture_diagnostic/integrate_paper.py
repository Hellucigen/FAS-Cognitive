# -*- coding: utf-8 -*-
# integrate_paper.py — 修复轮/战役数据并入论文(EN+ZH 主文与补充材料)
import os

P = r"E:\Project\Fascinator\FAS_Paper_I"


def rd(f):
    return open(os.path.join(P, f), encoding="utf-8").read()


def wr(f, s):
    open(os.path.join(P, f), "w", encoding="utf-8").write(s)


# ── EN 主文 ───────────────────────────────────────────────────────────
s = rd("main.tex")
draft = rd("draft_campaign_section_en.tex").rstrip()
anchor = "\\subsection{Additional observations}"
if "sec:res-campaign" not in s:
    assert anchor in s, "EN obs anchor"
    assert "regime-dependent" in draft, "draft state"
    s = s.replace(anchor, draft + "\n\n" + anchor, 1)

    a_old = "All negative results, two assembly audits, and the full protocol are reported."
    a_new = ("A second, baseline-controlled campaign (360 runs) measures the "
             "trade-off directly: under neutral instructions the persistent state "
             "resumes an abandoned goal in 9/10 runs where full-history LLM "
             "baselines do so in 0/10, and the same persistence costs ten extra "
             "failed attempts when the environment changes. All negative results, "
             "two assembly audits, and the full protocol are reported.")
    assert a_old in s, "EN abstract"
    s = s.replace(a_old, a_new, 1)

    l_old = ("bit-level determinism is verified only by re-running "
             "(Section~\\ref{sec:res-rerun}).")
    l_new = (l_old[:-1] + " (15) The attribution window (90\\,s) is a hard "
             "credit horizon: delayed consequences beyond it are never assigned "
             "(Section~\\ref{sec:res-campaign}). (16) The decision-head context "
             "serialization drops the temporal narrative of the experience "
             "stream; behavioral convergence tasks therefore fail at this "
             "interface. (17) The resumption advantage over full-history "
             "baselines is regime-dependent (short-history regime only).")
    assert l_old in s, "EN limits"
    s = s.replace(l_old, l_new, 1)
    wr("main.tex", s)
    print("main.tex integrated")
else:
    print("main.tex already integrated; skipped")

# ── ZH 主文 ───────────────────────────────────────────────────────────
s = rd("main_zh.tex")
draft = rd("draft_campaign_section_zh.tex").rstrip()
anchor = "\\subsection{补充观察}"
assert anchor in s, "ZH obs anchor"
s = s.replace(anchor, draft + "\n\n" + anchor, 1)

a_old = "全部否定结果、两次装配审计与完整协议均如实报告。"
a_new = ("第二轮基线受控战役(360 run)直接测量了这一权衡:中性指令下,持久状态在 9/10 "
         "的运行中重拾被放弃的目标,而全历史 LLM 基线为 0/10;同一持久性在环境变化时"
         "多付出十次失败尝试。全部否定结果、两次装配审计与完整协议均如实报告。")
assert a_old in s, "ZH abstract"
s = s.replace(a_old, a_new, 1)

l_old = "位级确定性仅经重跑验证（第~\\ref{sec:res-rerun} 节）。"
l_new = l_old[:-1] + (" (15) 归因窗(90\\,s)是硬信用地平线:超过它的延迟后果永远不会"
                      "被归因(第~\\ref{sec:res-campaign} 节)。(16) 决策头上下文序列化"
                      "丢失经历流的时序叙事;行为汇聚任务因此在该界面失败。"
                      "(17) 相对全历史基线的重拾优势是状态依赖的(仅短历史状态)。")
assert l_old in s, "ZH limits"
s = s.replace(l_old, l_new, 1)
wr("main_zh.tex", s)
print("main_zh.tex integrated")

# ── EN 补充材料 S15 ──────────────────────────────────────────────────
s = rd("supplementary.tex")
s15 = r"""
\section{Capability-Gap Campaign v2: Protocol and Tables}
\label{s15:campaign}

A second, baseline-controlled campaign (360 runs; design frozen in
\texttt{experiments/capability\_gap\_v2/}) tested the persistent state
against LLM baselines at the behavioral layer. Two mid-course confounds
were caught and repaired with full archival separation (gate rule v1
$\rightarrow$ v2; P3 inventory carry-over), and a self-model ablation
turned out to hook a nonexistent method and was rebuilt with telemetry
verification (Table~\ref{tab:s-campaign}).

\begin{table}[h]
\centering\small
\caption{Campaign v2: headline results ($n{=}10$ paired seeds unless noted).}
\label{tab:s-campaign}
\begin{tabular}{L{4.6cm}L{4.6cm}L{5.6cm}}
\toprule
\textbf{Question} & \textbf{FAS} & \textbf{Baselines} \\
\midrule
Goal resumption (C, closed-book free phase) & \textbf{9/10} & history 0/10; direct 0/10 (McNemar $p{=}0.0039$) \\
Resumption, mechanism label & zero-experience control reproduces the same working set & prior-closure edge + observation-triggered activation (not an intention mechanism) \\
Goal revision (E) & 10.5 failed repeats; pivot 7/10 @4.7 & history 1.0 repeats; pivot 10/10 @1.0 (FAS worse) \\
E repair (event-frame polarity wired) & \textbf{7.2 repeats}; pivot 9/10 @3.1 (Wilcoxon $p{=}0.011$) & direction confirmed, parity not reached (E1/E3 residual) \\
Self-model (F) & write path verified (5/5/5 vs 0/0/0); consumer repair: gathers 1.0$\rightarrow$6.0 & \texttt{current\_goal} has zero production readers (write-only) \\
Behavioral convergence (I) & 0--2/10 (incl.\ $k{=}16/32$ and rich serialization 0/30) & raw history \textbf{10/10} (Holm $p{=}0.041$) \\
Credit horizon (B, zero-LLM, $n{=}10$/delay) & $\le$80\,s: 10/10 attributed; 160\,s: 0/10 & 90\,s attribution window = the credit horizon \\
100-cycle stability (J, zero-LLM) & nodes $+3.7\%$, activation clamped & promotions 0 (repetition-starved) \\
History compression (K) & 923 tokens/run, 10/10 & history-200 13{,}290 tokens/run, 10/10 \\
\bottomrule
\end{tabular}
\end{table}

The resumption advantage is regime-dependent (short-history streams
only). Full protocols, confound repair logs, and the fault
classification table ship with the campaign archive.
"""
assert "\\end{document}" in s
s = s.replace("\\end{document}", s15 + "\n\\end{document}")
wr("supplementary.tex", s)
print("supplementary.tex integrated")

# ── ZH 补充材料 S15 ──────────────────────────────────────────────────
s = rd("supplementary_zh.tex")
s15 = r"""
\section{能力缺口战役 v2:协议与表格}
\label{s15:campaign}

第二轮基线受控战役(360 run;设计冻结于
\texttt{experiments/capability\_gap\_v2/})在行为层检验持久状态。
两次中途混淆被捕获并修复,归档完全分离(门控规则 v1$\rightarrow$v2;
P3 库存遗留);一个自我模型消融钩子指向不存在的方法,已重建并以
遥测验证(表~\ref{tab:s-campaign-zh})。

\begin{table}[h]
\centering\small
\caption{战役 v2 头条结果($n{=}10$ 配对种子,另注者除外)。}
\label{tab:s-campaign-zh}
\begin{tabular}{L{4.6cm}L{4.6cm}L{5.6cm}}
\toprule
\textbf{问题} & \textbf{FAS} & \textbf{基线} \\
\midrule
目标重拾(C,闭卷自由相) & \textbf{9/10} & history 0/10;direct 0/10(McNemar $p{=}0.0039$) \\
重拾的机制标签 & 零经历对照复现同一工作集 & 先验闭包边+观测触发激活(非意图机制) \\
目标修订(E) & 10.5 次失败重复;转向 7/10 @4.7 & history 1.0 次;转向 10/10 @1.0(FAS 更差) \\
E 修复(事件框架极性接入) & \textbf{7.2 次};转向 9/10 @3.1(Wilcoxon $p{=}0.011$) & 方向确认,未达 parity(E1/E3 残留) \\
自我模型(F) & 写入路径验证(5/5/5 vs 0/0/0);consumer 修复:gathers 1.0$\rightarrow$6.0 & \texttt{current\_goal} 生产零读取者(只写) \\
行为汇聚(I) & 0--2/10(含 $k{=}16/32$ 与富序列化 0/30) & 原始历史 \textbf{10/10}(Holm $p{=}0.041$) \\
信用地平线(B,零 LLM,每延迟 $n{=}10$) & $\le$80\,s:10/10 归因;160\,s:0/10 & 90\,s 归因窗=信用地平线 \\
100 周期稳定性(J,零 LLM) & 节点 $+3.7\%$,激活受钳制 & 晋升 0(重复饥饿) \\
历史压缩(K) & 923 tokens/run,10/10 & history-200 13{,}290 tokens/run,10/10 \\
\bottomrule
\end{tabular}
\end{table}

重拾优势是状态依赖的(仅短经历流)。完整协议、混淆修复日志与故障
分类表随战役归档提交。
"""
assert "\\end{document}" in s
s = s.replace("\\end{document}", s15 + "\n\\end{document}")
wr("supplementary_zh.tex", s)
print("supplementary_zh.tex integrated")
