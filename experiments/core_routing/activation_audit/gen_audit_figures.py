# gen_audit_figures.py — 审计图（只用已落盘数据；不使用重放数字作图）
import json, os, re, csv, collections
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import statistics as st

ROOT = r"E:\Project\Fascinator\experiments\core_routing"
OUT = os.path.join(ROOT, "activation_audit", "figures")
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"font.size": 9, "axes.spines.top": False,
                     "axes.spines.right": False})
RE_ACT = re.compile(r"^-\s+(\S+)\s+\(act\s+([\d.]+)\)\s*$")


def load_ctx(cond, task, seed):
    path = os.path.join(ROOT, "ablations" if cond.startswith("D-") else cond,
                        "raw_results.jsonl")
    out = []
    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if (d.get("type") == "decision" and d.get("condition") == cond
                and d["task"] == task and d["seed"] == seed):
            out.append(d)
    out.sort(key=lambda r: (r["phase"], r["step"]))
    return out


def acts_of(ctx):
    return [float(m.group(2)) for line in ctx.splitlines()
            if (m := RE_ACT.match(line.rstrip()))]


def gini(v):
    v = sorted(x for x in v if x > 0)
    if not v or sum(v) <= 0:
        return None
    n, s = len(v), sum(v)
    return (2 * sum((i + 1) * x for i, x in enumerate(v))) / (n * s) - (n + 1) / n


# ── Fig 1 / 2：成功与失败轨迹的浓度演化（已落盘激活值）──
def traj_curve(cond="fas_full"):
    # 找 T1 成功与失败各一例
    succ = fail = None
    for line in open(os.path.join(ROOT, cond, "raw_results.jsonl"), encoding="utf-8"):
        d = json.loads(line)
        if d.get("type") == "task_result" and d["task"] == "T1":
            if d["success"] and succ is None:
                succ = d["seed"]
            if not d["success"] and fail is None:
                fail = d["seed"]
    return succ, fail


succ_seed, fail_seed = traj_curve()
print("T1 成功 seed:", succ_seed, "失败 seed:", fail_seed)

for tag, seed in (("success", succ_seed), ("failure", fail_seed)):
    if seed is None:
        continue
    recs = load_ctx("fas_full", "T1", seed)
    steps, top1, mass, gin, ndist = [], [], [], [], []
    for i, r in enumerate(recs):
        v = acts_of(r["context"])
        if not v:
            continue
        steps.append(i)
        top1.append(max(v))
        mass.append(sum(v))
        g = gini(v)
        gin.append(g if g is not None else 0)
        ndist.append(len({round(x, 2) for x in v}))
    fig, axes = plt.subplots(1, 3, figsize=(9.6, 2.6))
    axes[0].plot(steps, top1, "o-", color="#2b6cb0", ms=4)
    axes[0].set_title("(a) top-1 activation")
    axes[0].set_xlabel("decision step")
    axes[1].plot(steps, mass, "o-", color="#dd6b20", ms=4)
    axes[1].set_title("(b) Top-$k$ activation mass")
    axes[1].set_xlabel("decision step")
    axes[2].plot(steps, gin, "o-", color="#2f855a", ms=4, label="Gini (concentration)")
    axes[2].plot(steps, [n / 8 for n in ndist], "s--", color="#c53030", ms=4,
                 label="distinct values / 8")
    axes[2].axhline(0, color="gray", lw=0.5)
    axes[2].set_title("(c) concentration (Gini) & value diversity")
    axes[2].set_xlabel("decision step")
    axes[2].legend(fontsize=6, frameon=False)
    fig.suptitle(f"FAS focus over steps — T1 seed {seed} ({tag}); "
                 "logged activations", fontsize=9)
    fig.savefig(os.path.join(OUT, f"fig_{tag}_trajectory.pdf"), bbox_inches="tight")
    plt.close(fig)
    print("fig:", tag, "steps:", len(steps))

# ── Fig 3：T3 old vs new 上下文激活质量（已落盘）──
t3 = list(csv.DictReader(open(os.path.join(ROOT, "activation_audit",
                                           "t3_reroute_analysis.csv"),
                              encoding="utf-8")))
bystep = collections.defaultdict(list)
for r in t3:
    bystep[int(r["step"])].append(r)
xs = sorted(bystep)
om = [st.mean(float(r["old_mass"]) for r in bystep[s]) for s in xs]
nm = [st.mean(float(r["new_mass"]) for r in bystep[s]) for s in xs]
frac = [st.mean(float(r["old_gt_new"]) for r in bystep[s]) for s in xs]
fig, ax = plt.subplots(figsize=(4.4, 2.9))
ax.plot(xs, om, "o-", color="#c53030", label="obsolete (oak) mass")
ax.plot(xs, nm, "s-", color="#2f855a", label="substitute (birch) mass")
ax.set_xlabel("decision step after environment change")
ax.set_ylabel("mean activation mass in focus")
ax.legend(frameon=False, fontsize=7)
ax2 = ax.twinx()
ax2.plot(xs, frac, "^--", color="#805ad5", ms=4, lw=1)
ax2.set_ylabel("fraction of seeds\nwith obsolete > substitute", color="#805ad5",
               fontsize=7)
ax2.tick_params(axis="y", colors="#805ad5")
ax.set_title("T3 re-routing: stale vs new context (n=20 seeds)")
fig.savefig(os.path.join(OUT, "fig_t3_reroute.pdf"), bbox_inches="tight")
plt.close(fig)
print("fig: t3_reroute")

# ── Fig 4：Top-K 结构一致性对照（实测图拓扑）──
fig, axes = plt.subplots(1, 2, figsize=(7.6, 2.8))
ax = axes[0]
names = ["production\ngraph", "experiment D\ngraph"]
edges = [479140, 0]
ax.bar([0, 1], edges, color=["#2b6cb0", "#c53030"], width=0.5)
ax.set_yscale("symlog")
ax.set_ylabel("edges in knowledge graph (log)")
for i, e in enumerate(edges):
    ax.text(i, max(e, 1) * 1.4, f"{e:,}", ha="center", fontsize=8)
ax.set_xticks([0, 1]); ax.set_xticklabels(names, fontsize=7)
ax.set_title("(a) graph edges: production vs experiment")
ax = axes[1]
dens = [0.0, 0.0]
ax.bar([0, 1], [1, 1], color="#e2e8f0", width=0.5)
ax.text(0, 0.5, "NOT COMPUTABLE\n(graph edgeless)", ha="center", va="center",
        fontsize=7, color="#c53030")
ax.text(1, 0.5, "0.000\n(all Top-K nodes\nisolated)", ha="center", va="center",
        fontsize=7, color="#c53030")
ax.set_xticks([0, 1]); ax.set_xticklabels(["Top-K induced\nsubgraph (prod.)",
                                           "Top-K induced\nsubgraph (D)"],
                                          fontsize=7)
ax.set_yticks([])
ax.set_title("(b) Top-K structural coherence")
fig.savefig(os.path.join(OUT, "fig_coherence.pdf"), bbox_inches="tight")
plt.close(fig)
print("fig: coherence")

# ── Fig 5：消融内部精度对照（已落盘统计）──
stats = list(csv.DictReader(open(os.path.join(ROOT, "activation_audit",
                                              "activation_statistics.csv"),
                                encoding="utf-8")))
g = collections.defaultdict(list)
for r in stats:
    g[r["condition"]].append(r)
fig, axes = plt.subplots(1, 2, figsize=(7.6, 2.8))
for ax, key, ylab in ((axes[0], "precision_relevant", "share of relevant nodes"),
                      (axes[1], "n_distinct_act", "distinct activation values")):
    cs = ["fas_full", "D-noact", "D-nodemand"]
    v = [[float(x[key]) for x in g[c] if x[key] not in ("", "None")] for c in cs]
    ax.bar(range(len(cs)), [st.mean(x) for x in v],
           color=["#dd6b20", "#7f8fa6", "#805ad5"], width=0.5)
    for i, x in enumerate(v):
        ax.scatter([i] * min(len(x), 60), x[:60], s=3, color="k", alpha=0.15, zorder=3)
    ax.set_xticks(range(len(cs)))
    ax.set_xticklabels(["FAS\n(diff on)", "−diffusion", "−demand"], fontsize=7)
    ax.set_ylabel(ylab)
fig.suptitle("Internal focus quality by ablation (logged data)", fontsize=9)
fig.savefig(os.path.join(OUT, "fig_ablation_internal.pdf"), bbox_inches="tight")
plt.close(fig)
print("fig: ablation_internal")
print("done ->", OUT)
