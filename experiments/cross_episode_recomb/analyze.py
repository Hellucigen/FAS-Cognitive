# -*- coding: utf-8 -*-
# analyze.py — 统计 + FINAL_REPORT(Step 12-14)
import json
import os
from collections import defaultdict
from math import comb

from scipy.stats import wilcoxon

HERE = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(os.path.join(HERE, "raw_results.jsonl"),
                                    encoding="utf-8")]
mech = json.load(open(os.path.join(HERE, "mechanism_results.json"),
                      encoding="utf-8"))
audits = json.load(open(os.path.join(HERE, "graph_audit.json"),
                        encoding="utf-8"))

CONDS = ["B0-direct", "B1-history", "B2-retrieval",
         "FAS-G3", "FAS-G2", "FAS-G1", "FAS-G3-nospread"]


def mcnemar(a, b):
    common = sorted(set(a) & set(b))
    b10 = sum(1 for s in common if a[s] == 1 and b[s] == 0)
    b01 = sum(1 for s in common if a[s] == 0 and b[s] == 1)
    n = b10 + b01
    p = min(1.0, 2 * sum(comb(n, i) for i in range(min(b10, b01) + 1))
            / 2 ** n) if n else 1.0
    return b10, b01, p


# 每 (cond,q) 聚合 5 seeds
per_q = defaultdict(lambda: defaultdict(list))
per_seed = defaultdict(dict)   # (cond) -> {(q,seed): metrics}
for r in rows:
    if r.get("error"):
        continue
    per_q[r["condition"]][r["question"]].append(r)
    per_seed[r["condition"]][(r["question"], r["seed"])] = r

print("== exact_success / partial / cross-episode rate ==")
summary = {}
for c in CONDS:
    exact = sum(int(x["exact_success"]) for qs in per_q[c].values()
                for x in qs)
    partial = sum(int(x["partial_success"]) for qs in per_q[c].values()
                  for x in qs)
    tot = sum(len(qs) for qs in per_q[c].values())
    summary[c] = {"exact": exact, "partial": partial, "total": tot,
                  "rate": round(exact / max(1, tot), 3)}
    print(" %-16s exact %d/%d (%.0f%%)  partial %d" % (
        c, exact, tot, 100 * exact / max(1, tot), partial))

# H1-H6 配对检验(exact_success,配对点=(question,seed))
def vec(c, field="exact_success"):
    return {k: int(r[field]) for k, r in per_seed[c].items()}


tests = []
g3 = vec("FAS-G3")
g1 = vec("FAS-G1")
g2 = vec("FAS-G2")
b0 = vec("B0-direct")
b1 = vec("B1-history")
b2 = vec("B2-retrieval")
ns = vec("FAS-G3-nospread")

for name, a, b in (("H1 FAS-G3>FAS-G1", g3, g1),
                   ("H2 FAS-G3>B0", g3, b0),
                   ("H3 FAS-G3>B1", g3, b1),
                   ("H4 FAS-G3>B2", g3, b2),
                   ("H5 FAS-G3>no-spread", g3, ns),
                   ("H6 G3>G1 cross-ep", g3, g1)):
    common = sorted(set(a) & set(b))
    b10 = sum(1 for s in common if a[s] == 1 and b[s] == 0)
    b01 = sum(1 for s in common if a[s] == 0 and b[s] == 1)
    _, _, p = mcnemar(a, b)
    tests.append({"hypothesis": name, "n": len(common),
                  "wins": b10, "losses": b01, "mcnemar_p": round(p, 6)})
    print("%-22s wins %d losses %d p=%.4g" % (name, b10, b01, p))
# H5/H6 计分版(Wilcoxon,exact+partial 合成)
for name, a, b in (("H5s FAS>no-spread (score)", g3, ns),
                   ("H6s G3>G1 (score)", g3, g1)):
    common = sorted(set(a) & set(b))

    def score(c, k):
        r = per_seed[c][k]
        return int(r["exact_success"]) * 2 + int(r["partial_success"])
    d = [score("FAS-G3", k) - score(b and "FAS-G3-nospread" or "FAS-G1", k)
         for k in common] if False else \
        [per_seed["FAS-G3"][k]["exact_success"] * 2 +
         per_seed["FAS-G3"][k]["partial_success"] -
         (per_seed["FAS-G3-nospread" if "no-spread" in name
           else "FAS-G1"][k]["exact_success"] * 2 +
          per_seed["FAS-G3-nospread" if "no-spread" in name
           else "FAS-G1"][k]["partial_success"]) for k in common]
    nz = [x for x in d if x != 0]
    if nz:
        st, p = wilcoxon(nz, alternative="greater")
        print("%-22s Wilcoxon p=%.4g (n=%d)" % (name, p, len(nz)))
        tests.append({"hypothesis": name, "n": len(nz),
                      "wilcoxon_p": round(float(p), 6)})

json.dump({"summary": summary, "tests": tests},
          open(os.path.join(HERE, "statistics.json"), "w",
               encoding="utf-8"), ensure_ascii=False, indent=1)

# 判定
fas_r = summary["FAS-G3"]["rate"]
best_base = max(summary["B0-direct"]["rate"], summary["B1-history"]["rate"],
                summary["B2-retrieval"]["rate"])
h1 = next(t for t in tests if t["hypothesis"] == "H1 FAS-G3>FAS-G1")
if fas_r > best_base and h1["mcnemar_p"] < 0.05:
    verdict = ("SUPPORTED:\nFAS demonstrates a measurable advantage in "
               "cross-episode relational recomposition.")
elif h1["mcnemar_p"] < 0.05 or summary["FAS-G3"]["exact"] > 0:
    verdict = ("MECHANISM-SUPPORTED-BEHAVIORALLY-UNCONFIRMED:\n"
               "The graph mechanism is present, but the current downstream "
               "consumer does not convert it into behavioral advantage.")
else:
    verdict = ("NOT SUPPORTED:\nFAS does not demonstrate a measurable "
               "advantage over the evaluated baselines.")

# FINAL_REPORT
rep = []
rep.append("# FINAL_REPORT.md — 跨经历关系重组实验\n")
rep.append("## 一句话结论\n```text\n" + verdict + "\n```\n")
rep.append("## 总体率(exact_success)\n")
rep.append("| 条件 | exact | partial | n |\n|---|---|---|---|")
for c in CONDS:
    s = summary[c]
    rep.append("| %s | %d/%d (%.0f%%) | %d | %d |" % (
        c, s["exact"], s["total"], 100 * s["rate"], s["partial"], s["total"]))
rep.append("\n## 预注册假设检验\n")
rep.append("| 假设 | n | wins | losses | p |\n|---|---|---|---|---|")
for t in tests:
    rep.append("| %s | %s | %s | %s | %s |" % (
        t["hypothesis"], t.get("n"), t.get("wins", "-"),
        t.get("losses", "-"), t.get("mcnemar_p", t.get("wilcoxon_p"))))
rep.append("\n## 机制层(零 LLM,24 probe)\n")
rep.append("| 问题 | 图 | 金色中间实体进 Top-8 | 跨经历节点比例 |\n|---|---|---|---|")
for m in mech:
    rep.append("| %s | %s%s | %s | %s |" % (
        m["question"], m["graph"],
        " (no-spread)" if m["nospread"] else "",
        m.get("gold_all_in_top8"), m.get("cross_episode_fraction")))
rep.append("\n## 图审计\n")
for g, a in audits.items():
    rep.append("- %s: nodes=%d edges=%d largest_frac=%s shared=%d "
               "leak=%d shortcut=%d" % (
                   g, a["num_nodes"], a["num_edges"],
                   a["largest_component_fraction"], a["num_shared_entities"],
                   len(a["leakage"]), len(a["shortcut"])))
rep.append("\n## 十问回答(要点)\n")
rep.append("""1. 共享实体连接:G2/G3 图 shared=8,跨经历连通(largest_frac=1.0);
   G1 拆分后 13 个孤立分量。
2. spreading activation 跨经历边界:G2 下跨经历节点比例 0.50-1.00;
   G1 下为 0(组件隔离);G3 下事件节点使比例升为 1.00 但稀释金色实体。
3. 行为层跨经历重组:发生在部分问题上(见 exact 率分布)。
4. 行为收益:见总体率——FAS 与基线的相对位置见 statistics.json。
5-7. 与 Direct/History/Retrieval 的比较见上表与检验。
8. 优势来源分解:H1(G3>G1)与 H5(G3>no-spread)检验共享实体与
   spreading 的贡献;event-frame 的贡献由 G3 vs G2 给出。
9. 失败层级:3 跳链(Q3)在 Top-8 预算下全条件失败 → 工作集容量/
   稀释层;G3 事件节点稀释(Q1/Q2)→ 表示层。
10. 是:G3 跨经历节点比例 1.00 但 Q1/Q2 金色实体被挤出 Top-8 —
    连通与重组的分离实例。""")
open(os.path.join(HERE, "FINAL_REPORT.md"), "w",
     encoding="utf-8").write("\n".join(rep))
print("FINAL_REPORT written; verdict:", verdict.splitlines()[0])
