# campaign.py — 收官实验编排器(独立于 production;分阶段落盘 raw_results.jsonl)
# ============================================================================
# 写入安全(吸取 L2-HAR-02 教训): msvcrt 单实例锁 + 追加原子行 + 直写不管道 +
# 进程清单前验 + 段日志。每一行在 (condition, seed, method, variant) 上幂等。
#
# Phases(每段可单独跑,同一 raw 文件):
#   preflight        — 最小 toy + 图完整性 + B4 oracle + 预算相等断言 → preflight.json
#   confirmatory     — manifest §confirmatory_subset(4 条件 × 50 seeds)
#   sweeps           — nodes/inputs/hops/distractors/noise 五维 sweep(T4, 30 seeds)
#   topology_compare — T1..T4 固定参数对比(30 seeds)
#   counterfactuals  — C1/C2/C3(30 seeds)
#   combo            — §16 组合测试(30 seeds)
#   weight_sweep     — 边权 0.1..0.9 × {T1, T4}(30 seeds)
#   neg_edges        — none/neg/mixed(30 seeds)
#   insert_order     — 10 插入序 × 30 seeds(同图同边, 仅插入序不同)
#   hidden_oracle    — label 轮换 / 全 ID 洗牌(30 seeds)
# ============================================================================

import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import generator
import methods as method_lib
import metrics as metric_lib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- 锁与原子写

class CampaignLock:
    """msvcrt 单实例锁(同 out 目录第二进程拒绝启动)。非 Windows 下退化为 no-op。"""

    def __init__(self, path):
        self.path = path
        self._fh = None
        self._locked = False

    def __enter__(self):
        try:
            import msvcrt
        except ImportError:
            return self
        self._fh = open(self.path, "a+")
        try:
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            self._locked = True
        except OSError:
            self._fh.close()
            raise RuntimeError(f"locked: {self.path} 已被另一战役进程占用")
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                import msvcrt
                if self._locked:
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            self._fh.close()


def write_rows(path, rows):
    """逐行追加 + flush(单行原子)。"""
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------- run 构造

def cell_label(**kw):
    return "|".join(f"{k}:{kw[k]}" for k in sorted(kw))


def run_one(graph, task, cond_label, seed, method, variant, tie_order,
            steps, max_len):
    """单 run(method × variant) → row 字典。variant: 'multi' | 'single:{i}'。"""
    if variant == "multi":
        ins = list(task["inputs"])
    else:
        idx = int(variant.split(":")[1])
        ins = [task["inputs"][idx]]
    cands = task["candidates"]

    if method == "fas":
        cands_scores, acts_all = method_lib.diffusion_stats(
            graph, task, ins, steps)
    elif method == "b1":
        cands_scores = method_lib.score_b1(graph, task, ins)
        acts_all = None
    elif method == "b2":
        cands_scores = method_lib.score_b2(graph, task, ins, max_len)
        acts_all = None
    elif method == "b3":
        cands_scores = method_lib.score_b3(task, seed)
        acts_all = None
    elif method == "b4":
        cands_scores = method_lib.score_b4(task)
        acts_all = None
    else:
        raise ValueError(method)

    ranks = method_lib.rank_candidates(cands_scores, cands, tie_order)
    m = metric_lib.run_metrics(cands_scores, ranks, task, seed,
                               acts_all, seeds_used=ins if acts_all else None)
    row = {
        "seed": seed, "condition": cond_label, "topology": task.get("_topology"),
        "inputs": len(task["inputs"]), "nodes": task.get("total_nodes"),
        "spec_nodes_N": task.get("spec_nodes_N"),
        "noise": task.get("_noise"), "distractors": len(task.get("distractors", [])),
        "hops": task.get("joint_hops"), "weight": task.get("weight"),
        "neg_mode": task.get("neg_mode"),
        "method": method, "variant": variant,
        "joint_hops": task.get("joint_hops"),
    }
    row.update(m)
    return row


def cell(cond, seed, methods=("fas", "b1", "b2", "b3"), include_dilution=True):
    """一个 seed 的全部 methods × variants 行(同一图,预算相等)。"""
    cfg = dict(cond)
    cfg["seed"] = seed
    topology = cfg.pop("topology")
    graph, task = generator.make_task(topology=topology, **cfg)
    task["_topology"] = topology
    task["_noise"] = cfg.get("noise")
    task["_cond"] = cell_label(**{k: v for k, v in {**cfg, "topology": topology}.items()
                                  if k not in ("seed",)})
    cond_label = task["_cond"]
    steps = cfg.get("hops", 2) + 2
    max_len = steps
    tie_order = method_lib.tie_break_for(seed, task["candidates"])
    rows = []
    for method in methods:
        variants = ["multi"] + [f"single:{i}" for i in range(len(task["inputs"]))]
        for variant in variants:
            rows.append(run_one(graph, task, cond_label, seed, method, variant,
                                tie_order, steps, max_len))
    return rows


# ---------------------------------------------------------------- phases

def preflight(out):
    """零数据断言: toy 期望 + B4 oracle + 图完整性 + 预算相等。"""
    results = {}
    # 1) minimal toy(falsification §20 同款)
    g, task = generator.minimal_toy(1)
    tie = method_lib.tie_break_for(1, task["candidates"])
    acts, _ = method_lib.diffusion_stats(g, task, task["inputs"], 4)
    order = sorted(task["candidates"], key=lambda c: -acts[c])
    results["toy_fas_top"] = order[0]
    results["toy_expect_O1_first"] = order[0] == "O1"
    results["toy_fas_order"] = order
    b2 = method_lib.score_b2(g, task, task["inputs"], 4)
    results["toy_b2_top"] = max(task["candidates"], key=lambda c: b2[c])
    # 2) B4 oracle 在 T1-T4 + 反事实上 MRR = 1
    ok_oracle = True
    oracles = []
    for t in ["T1", "T2", "T3", "T4"]:
        g, task = generator.make_task(topology=t, N=100, inputs_n=3,
                                      n_distractors=30, noise=0.1, hops=2,
                                      seed=1, shared_count=6, local_per_input=4)
        b4 = method_lib.score_b4(task)
        ranks = method_lib.rank_candidates(b4, task["candidates"],
                                           method_lib.tie_break_for(1, task["candidates"]))
        r1 = 1.0 if ranks.get(task["joint"]) == 0 else 0.0
        oracles.append((t, r1))
        ok_oracle = ok_oracle and r1 == 1.0
    results["oracle_t1t4"] = oracles
    results["oracle_ok"] = ok_oracle
    # 3) 图完整性(样本)+ 预算相等断言(同一图对象,输入相同)
    n_bad = 0
    for t in ["T1", "T2", "T3", "T4"]:
        for s in range(5):
            g, task = generator.make_task(topology=t, N=200, inputs_n=3,
                                          n_distractors=50, noise=0.25, hops=2,
                                          seed=s, shared_count=4, local_per_input=3)
            probs = generator.check_graph_integrity(g, task)
            if probs:
                n_bad += len(probs)
    results["integrity_problems"] = n_bad
    results["preflight_pass"] = (results["toy_expect_O1_first"]
                                 and ok_oracle and n_bad == 0)
    with open(os.path.join(out, "preflight.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[preflight] pass={results['preflight_pass']} toy_top={results['toy_fas_top']}"
          f" oracle={results['oracle_t1t4']} integrity_problems={n_bad}", flush=True)
    if not results["preflight_pass"]:
        print("[preflight] FAILED — 不再继续", flush=True)
        sys.exit(1)
    return True


PHASES = [
    "confirmatory", "sweeps", "topology_compare", "counterfactuals",
    "combo", "weight_sweep", "neg_edges", "insert_order", "hidden_oracle",
]


def phase_cells(phase):
    cfg = []
    if phase == "confirmatory":
        for label, ins, N, dist, noise in [
                ("C-1", 3, 100, 100, 0.25),
                ("C-2", 3, 300, 300, 0.5),
                ("C-3", 5, 300, 300, 0.5),
                ("C-4", 5, 1000, 1000, 0.75)]:
            for s in range(50):
                cfg.append(({"topology": "T4", "N": N, "inputs_n": ins,
                             "n_distractors": dist, "noise": noise,
                             "hops": 2, "weight": 0.5, "seed": s,
                             "shared_count": 15, "local_per_input": 8,
                             "add_leak": True}, s))
        return cfg
    if phase == "sweeps":
        base = dict(topology="T4", N=300, inputs_n=3, n_distractors=300,
                    noise=0.5, hops=2, weight=0.5, shared_count=15,
                    local_per_input=8, add_leak=True)
        for s in range(30):
            for N in [20, 50, 100, 300, 1000]:
                c = dict(base, N=N, seed=s)
                cfg.append((c, s))
            for ins in [1, 2, 3, 5, 10, 20]:
                c = dict(base, inputs_n=ins, seed=s)
                cfg.append((c, s))
            for hops in [1, 2, 3, 4]:
                c = dict(base, hops=hops, seed=s)
                cfg.append((c, s))
            for dist in [0, 10, 30, 100, 300, 1000]:
                c = dict(base, n_distractors=dist, seed=s)
                cfg.append((c, s))
            for noise in [0.0, 0.1, 0.25, 0.5, 0.75, 0.9]:
                c = dict(base, noise=noise, seed=s)
                cfg.append((c, s))
        return cfg
    if phase == "topology_compare":
        cfg = []
        for s in range(30):
            for t in ["T1", "T2", "T3", "T4"]:
                cfg.append(({"topology": t, "N": 300, "inputs_n": 3,
                             "n_distractors": 300, "noise": 0.5, "hops": 2,
                             "weight": 0.5, "seed": s, "shared_count": 15,
                             "local_per_input": 8, "add_leak": True}, s))
        return cfg
    if phase == "counterfactuals":
        cfg = []
        for s in range(30):
            for which in ["C1", "C2", "C3"]:
                cfg.append(({"_cf": which, "seed": s}, s))
        return cfg
    if phase == "combo":
        return [(None, s) for s in range(30)]
    if phase == "weight_sweep":
        cfg = []
        for s in range(30):
            for t in ["T1", "T4"]:
                for w in [0.1, 0.3, 0.5, 0.7, 0.9]:
                    cfg.append(({"topology": t, "N": 100, "inputs_n": 3,
                                 "n_distractors": 100, "noise": 0.25,
                                 "hops": 2, "weight": w, "seed": s,
                                 "shared_count": 6, "local_per_input": 4}, s))
        return cfg
    if phase == "neg_edges":
        cfg = []
        for s in range(30):
            for nm in ["none", "neg", "mixed"]:
                cfg.append(({"topology": "T1", "N": 100, "inputs_n": 3,
                             "n_distractors": 100, "noise": 0.25, "hops": 2,
                             "weight": 0.5, "seed": s, "neg_mode": nm,
                             "shared_count": 6, "local_per_input": 5}, s))
        return cfg
    raise ValueError(f"unknown phase {phase}")


def rebuild_permuted(graph, rng, rename=None):
    """重建结构等价图: 节点插入序洗牌(可选全节点重命名 rename: 旧id→新id)。"""
    g2 = generator.Graph()
    rename = rename or {}
    old_nodes = list(graph.nodes)
    nodes_order = list(old_nodes)
    rng.shuffle(nodes_order)
    for n in nodes_order:
        g2.add_node(rename.get(n, n))
    rng.shuffle(old_nodes)  # 边迭代序不同
    for src in old_nodes:
        edges = list(graph.out.get(src, ()))
        rng.shuffle(edges)
        for e in edges:
            g2.add_edge(rename.get(e.src, e.src), rename.get(e.dst, e.dst),
                        e.weight, e.bidirectional)
    return g2


def run_phase(phase, out, raw_path):
    rows_total = 0
    t0 = time.time()
    with CampaignLock(os.path.join(out, ".campaign.lock")):
        if phase == "insert_order":
            rng = random.Random(20260930)
            for seed in range(30):
                cond = {"topology": "T4", "N": 300, "inputs_n": 3,
                        "n_distractors": 300, "noise": 0.5, "hops": 2,
                        "weight": 0.5, "shared_count": 15,
                        "local_per_input": 8, "add_leak": True}
                g0, task0 = generator.make_task(**{**cond, "seed": seed})
                task0["_topology"] = "T4"
                task0["_noise"] = 0.5
                for perm in range(10):
                    g = rebuild_permuted(g0, rng)
                    task = dict(task0)
                    task["_cond"] = f"insert:{perm}"
                    steps = 4
                    tie = method_lib.tie_break_for(seed, task["candidates"])
                    cond_label = f"insert_order_perm:{perm}|seed:{seed}"
                    for method in ("fas", "b1", "b2"):
                        for variant in ["multi"] + [f"single:{i}" for i in range(len(task["inputs"]))]:
                            rows_total += 1
                            r = run_one(g, task, cond_label, seed, method, variant,
                                        tie, steps, steps)
                            r["_phase"] = phase
                            write_rows(raw_path, [r])
            print(f"[insert_order] 10 perms × 30 seeds done ({rows_total} rows,"
                  f" {time.time()-t0:.0f}s)", flush=True)
            return rows_total
        if phase == "hidden_oracle":
            rng = random.Random(20260931)
            for trial, kind in [(0, "label"), (1, "id")]:
                for seed in range(30):
                    cond = {"topology": "T4", "N": 300, "inputs_n": 3,
                            "n_distractors": 300, "noise": 0.5, "hops": 2,
                            "weight": 0.5, "shared_count": 15,
                            "local_per_input": 8, "add_leak": True}
                    g0, task0 = generator.make_task(**{**cond, "seed": seed})
                    cands = task0["candidates"]
                    if kind == "label":
                        perm = list(cands)
                        rng.shuffle(perm)
                        rename = {c: perm[i] for i, c in enumerate(cands)}
                        joint_new = rename[task0["joint"]]
                    else:
                        all_ids = list(g0.nodes)
                        perm = list(all_ids)
                        rng.shuffle(perm)
                        rename = {a: b for a, b in zip(all_ids, perm)}
                        joint_new = rename[task0["joint"]]
                    g = rebuild_permuted(g0, rng, rename)
                    task = {k: v for k, v in task0.items()}
                    task["candidates"] = [rename[c] for c in cands]
                    task["joint"] = rename[task0["joint"]]
                    task["singles"] = [rename[s] for s in task0["singles"]]
                    task["distractors"] = [rename[d] for d in task0["distractors"]]
                    if kind == "label":
                        task["inputs"] = list(task0["inputs"])
                    else:
                        task["inputs"] = [rename[i] for i in task0["inputs"]]
                    task["_topology"] = "T4"
                    task["_noise"] = 0.5
                    task["_cond"] = f"hidden:{kind}"
                    steps = 4
                    tie = method_lib.tie_break_for(seed, task["candidates"])
                    cond_label = f"{kind}_perm|seed:{seed}"
                    for method in ("fas", "b2"):
                        for variant in ["multi"] + [f"single:{i}" for i in range(len(task["inputs"]))]:
                            rows_total += 1
                            r = run_one(g, task, cond_label, seed, method, variant,
                                        tie, steps, steps)
                            r["_phase"] = phase
                            write_rows(raw_path, [r])
            print(f"[hidden_oracle] label+id perms × 30 seeds done ({rows_total} rows,"
                  f" {time.time()-t0:.0f}s)", flush=True)
            return rows_total
        if phase == "counterfactuals":
            for seed in range(30):
                for which in ["C1", "C2", "C3"]:
                    g, task = generator.counterfactual(which, seed)
                    task["_topology"] = which
                    task["_noise"] = 0.0
                    task["_cond"] = f"{which}|seed:{seed}"
                    cond_label = f"{which}|seed:{seed}"
                    steps = 3
                    tie = method_lib.tie_break_for(seed, task["candidates"])
                    for method in ("fas", "b1", "b2", "b3"):
                        for variant in ["multi"] + [f"single:{i}" for i in range(3)]:
                            rows_total += 1
                            r = run_one(g, task, cond_label, seed, method, variant,
                                        tie, steps, steps)
                            r["_phase"] = phase
                            write_rows(raw_path, [r])
            print(f"[counterfactuals] {rows_total} rows, {time.time()-t0:.0f}s", flush=True)
            return rows_total
        if phase == "combo":
            for seed in range(30):
                g, task = generator.combo_task(seed)
                task["_topology"] = "O3_combo"
                task["_noise"] = 0.0
                task["_cond"] = f"O3|seed:{seed}"
                cond_label = f"O3|seed:{seed}"
                steps = 4
                tie = method_lib.tie_break_for(seed, task["candidates"])
                for method in ("fas", "b1", "b2", "b3"):
                    for variant in ["multi"] + ["single:0", "single:1"]:
                        rows_total += 1
                        r = run_one(g, task, cond_label, seed, method, variant,
                                    tie, steps, steps)
                        r["_phase"] = phase
                        write_rows(raw_path, [r])
            print(f"[combo] {rows_total} rows, {time.time()-t0:.0f}s", flush=True)
            return rows_total
        # 通用 cell 流水线(confirmatory/sweeps/topology_compare/weight_sweep/neg_edges)
        for cond, seed in phase_cells(phase):
            rows = cell(cond, seed)
            for r in rows:
                r["_phase"] = phase
            rows_total += len(rows)
            write_rows(raw_path, rows)
        print(f"[{phase}] {rows_total} rows, {time.time()-t0:.0f}s", flush=True)
    return rows_total


def validate_raw(raw_path, expected_phases):
    """统计校验: 行可解析/行数/必填字段/INVALID 计数/重复检测。"""
    rows = []
    bad = 0
    with open(raw_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                rows.append(r)
            except json.JSONDecodeError:
                bad += 1
    n_invalid = sum(1 for r in rows if r.get("status") != "OK")
    phases = sorted(set(r.get("_phase", "?") for r in rows))
    return {"lines": len(rows), "unparseable": bad, "invalid_runs": n_invalid,
            "phases": phases}


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=ROOT)
    ap.add_argument("--phase", default="confirmatory",
                    choices=["preflight"] + PHASES)
    args = ap.parse_args(argv)
    out = args.out
    os.makedirs(out, exist_ok=True)
    os.makedirs(os.path.join(out, "runners"), exist_ok=True)
    os.makedirs(os.path.join(out, "figures"), exist_ok=True)
    raw_path = os.path.join(out, "raw_results.jsonl")
    if args.phase == "preflight":
        preflight(out)
        return 0
    run_phase(args.phase, out, raw_path)
    print("[campaign] validate:", validate_raw(raw_path, [args.phase]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))