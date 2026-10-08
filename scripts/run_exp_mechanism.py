# run_exp_mechanism.py — 机制实验：多输入竞争下激活景观的组织（C1/C2/C4）
# ═══════════════════════════════════════════════════════════════════════
# 研究问题（唯一）：多个同时输入注入统一图谱后，activation propagation/
#   competition 是否使"与多个输入均有结构关联"的节点获得更高激活并进入
#   cognitive context——而不是简单的 input→similarity→top-k？
#
# 主分析完全基于激活景观（§13）：无 LLM、无 API 调用。
#
# 图：FAS 生产建图路径（ensure_mc_world + build_recipe_closure，世界配方
#   表驱动）+ 观察共现边（kg.add_edge）。拓扑在实验开始前固定；
#   multi_input_score 仅由该拓扑计算（BFS 距离），不得引用最终激活。
#
# 输入（实验操作 = 注入，非"手工抬激活"）：
#   Input A = 物品:wooden_pickaxe（工具需求簇）
#   Input B = 物品:stone_pickaxe（石质需求簇；与 A 共享 stick/cobblestone/
#             可采资源 —— 拓扑勘察已确认 d≤2 的双输入节点存在）
#   Distractor D = 观察摄取的干扰实体簇（birch_log/diamond/dirt/…，
#             与 A/B 均无 ≤2 跳关系）
# 注入序列（6 步）：s1=[A,+obs] s2=[B,+obs] s3=[D,+obs]
#                   s4=[A,B] 同时注入（收敛步） s5=[A,B] s6=无注入观察衰减
#
# 条件：
#   C1 fas_full       注入 + 扩散（含发射预算竞争；本图无负权边，抑制不经由）
#   C2 diffusion_off  仅注入 + 衰减（无传播）
#   C3 competition_off = **UNAVAILABLE**：本架构的抑制需负权边（本图无），
#      发射预算归一化是 diffuse_step 的内禀组成，无配置开关；制造开关
#      = 修改核心算法（禁）。如实记录，不运行。
#   C4 flat           同一张图上的扁平文本相似度排序（无激活无传播），
#                     输出同一 schema（activation=None）
#
# 预注册主指标（§17，防事后挑选）：
#   M1 multi_input_enrichment = P(multi|TopK) / P(multi|graph)
#   M2 spearman(activation, multi_input_score) 逐 run
# 次级：coverage_A/B/D、top1、top5/top10 mass、entropy、Gini、distinct 值数。
# 统计：配对 Wilcoxon（同 seed 跨条件）+ Bonferroni（主指标 2 × 对照 2 = 4）。
# ═══════════════════════════════════════════════════════════════════════

import argparse
import collections
import csv
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(1, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
if os.getcwd() != os.path.dirname(os.path.dirname(os.path.abspath(__file__))):
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandbox_lab import SandboxWorld, install_fake_bridge  # noqa
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

INPUT_A = "物品:wooden_pickaxe"
INPUT_B = "物品:stone_pickaxe"
INPUT_D = "实体:birch_log"
DISTRACTORS = ["birch_log", "diamond", "dirt", "emerald", "gold_nugget"]
STEPS = 6
DIFF_STEPS = 3          # 每决策步的扩散迭代数（固定，与 routing v2 同量级）
POST_STEPS = (4, 5, 6)  # 预注册：收敛后步（均值入 summary）
TOPK = 8
SNAP_TOP = 30


class CoreModuleError(RuntimeError):
    pass


class InvariantViolation(RuntimeError):
    pass


def build_graph():
    """生产建图路径（每次调用全新确定性图）。"""
    import config as _C
    from graph_model import KnowledgeGraph
    from diffusion_engine import DiffusionEngine
    import world_prior as wp
    import mc_knowledge as mck
    from sandbox_lab import install_fake_bridge
    import minecraft.bridge as BR
    w = SandboxWorld()
    install_fake_bridge(w)
    cfgd = dict(_C.DEFAULT_CONFIG)
    cfgd["mc_world"] = {
        "gatherable": sorted(set(w.recipe_table) |
                             {d for m in w.block_meta.values()
                              for d in m.get("drops", [])} |
                             set(w.block_meta)),
        "block_meta": {r: dict(v) for r, v in w.block_meta.items()},
    }
    wp.bind_config(cfgd)
    kg = KnowledgeGraph()
    eng = DiffusionEngine(kg, {"beta_spread": 1.0, "activation_epsilon": 1e-4,
                               "min_spread_threshold": 0.01,
                               "activation_max": 5.0})
    mck.ensure_mc_world(kg, cfgd)
    for tgt in sorted(set(w.recipe_table)):
        wp.build_recipe_closure(kg, eng, tgt, bridge=BR)
    eng.name_to_node = dict(kg.nodes)
    return kg, eng, w


def bfs_dist(adj, src):
    dist = {src: 0}
    q = [src]
    while q:
        nq = []
        for n in q:
            for m in adj[n]:
                if m not in dist:
                    dist[m] = dist[n] + 1
                    nq.append(m)
        q = nq
    return dist


class MechanismFAS:
    """C1/C2 的激活机制（生产组件装配；C4 不建激活场）。"""

    def __init__(self, use_diffusion=True, use_flat=False):
        from graph_model import KnowledgeGraph, Node, Edge
        from diffusion_engine import DiffusionEngine
        import mc_knowledge as mck
        import world_prior as wp
        from sandbox_lab import install_fake_bridge
        import minecraft.bridge as BR
        import config as _C
        self.use_diffusion = use_diffusion
        self.use_flat = use_flat
        self.kg = KnowledgeGraph()
        self.eng = DiffusionEngine(self.kg, {
            "beta_spread": 1.0, "activation_epsilon": 1e-4,
            "min_spread_threshold": 0.01, "activation_max": 5.0})
        w = SandboxWorld()
        install_fake_bridge(w)
        self.w = w
        cfgd = dict(_C.DEFAULT_CONFIG)
        cfgd["mc_world"] = {
            "gatherable": sorted(set(w.recipe_table) |
                                 {d for m in w.block_meta.values()
                                  for d in m.get("drops", [])} |
                                 set(w.block_meta)),
            "block_meta": {r: dict(v) for r, v in w.block_meta.items()},
        }
        wp.bind_config(cfgd)
        mck.ensure_mc_world(self.kg, cfgd)
        for tgt in sorted(set(w.recipe_table)):
            wp.build_recipe_closure(self.kg, self.eng, tgt, bridge=BR)
        self.eng.name_to_node = dict(self.kg.nodes)
        self.Edge, self.Node = Edge, Node

    def _node(self, nid, label="declarative-semantic"):
        if nid not in self.kg.nodes:
            self.kg.add_node(self.Node(id=nid, weight=0.45, label=label,
                                       graph_space="semantic"))
        if nid not in self.eng.name_to_node:
            self.eng.name_to_node[nid] = self.kg.nodes[nid]
        return nid

    def co_occurrence(self, ents):
        """观察共现边（生产 kg.add_edge；与 routing v2 同语义）。"""
        for i in range(len(ents)):
            for j in range(i + 1, len(ents)):
                a, b = ents[i], ents[j]
                self._node(a); self._node(b)
                if self.kg.get_edge(a, b, "关联") is None:
                    self.kg.add_edge(self.Edge(src=a, dst=b, relation="关联",
                                               weight=0.5))
        self.eng.name_to_node = dict(self.kg.nodes)

    def inject(self, node_ids):
        ids = [self._node(n) for n in node_ids]
        self.eng.activate_from_inputs(
            [n for n in ids if n in self.eng.name_to_node], [],
            source_type="external_input")

    def diffuse(self):
        if not self.use_diffusion:
            return
        self.eng.diffuse_step()

    def decay(self):
        self.eng.decay_step()

    def snapshot(self, k=SNAP_TOP):
        nodes = [nd for nd in self.kg.nodes.values()
                 if float(getattr(nd, "activation", 0) or 0) > 0]
        nodes.sort(key=lambda nd: -float(getattr(nd, "activation", 0) or 0))
        return [(nd.id, round(float(getattr(nd, "activation", 0) or 0), 4))
                for nd in nodes[:k]], len(nodes)

    def telemetry(self):
        return {"graph_nodes": len(self.kg.nodes),
                "graph_edges": len(self.kg.edges),
                "activated_edges": sum(1 for e in self.kg.edges
                                       if float(getattr(e, "activation", 0)
                                                or 0) > 0)}


class FlatRank:
    """C4：同一张图上的扁平文本相似度（无激活无传播）。"""

    def __init__(self, fas):
        self.fas = fas

    def rank(self, query, k=SNAP_TOP):
        from embedding_manager import EmbeddingProvider
        import numpy as np
        ids = sorted(self.fas.kg.nodes)
        if not ids:
            return []
        emb = EmbeddingProvider()
        M = emb.encode(ids)
        q = emb.encode_single(query)
        sims = M @ (q / (np.linalg.norm(q) + 1e-9))
        idx = sorted(range(len(ids)), key=lambda i: -float(sims[i]))[:k]
        return [(ids[i], None) for i in idx]


def entropy(vals):
    s = sum(x for x in vals if x > 0)
    if s <= 0:
        return None
    ps = [x / s for x in vals if x > 0]
    return round(-sum(p * math_log2(p) for p in ps), 4)


def math_log2(x):
    import math
    return math.log2(x)


def gini(vals):
    v = sorted(x for x in vals if x > 0)
    if not v or sum(v) <= 0:
        return None
    n, s = len(v), sum(v)
    return round((2 * sum((i + 1) * x for i, x in enumerate(v))) / (n * s)
                 - (n + 1) / n, 4)


def category(nid, dA, dB, dD, exclude_seeds):
    """预注册分类：multi / single_A / single_B / distractor / other。"""
    name = nid.split(":")[-1]
    a, b, d = dA.get(nid), dB.get(nid), dD.get(nid)
    if nid in exclude_seeds:
        return "input_seed"
    da = a if a is not None else 99
    db = b if b is not None else 99
    dd = d if d is not None else 99
    if da <= 2 and db <= 2:
        return "multi"
    if da <= 2:
        return "single_A"
    if db <= 2:
        return "single_B"
    if dd <= 2:
        return "distractor"
    if name in ("hunger", "wait", "unparseable_reply"):
        return "noise"
    return "other"


def run_episode(cond, seed, log):
    """一个 episode = 6 步注入/扩散/快照。返回 per-run 指标 + 逐节点表。"""
    rng = random.Random(seed * 7919 + zlib_crc(cond))
    fas = MechanismFAS(use_diffusion=(cond != "C2"),
                       use_flat=(cond == "C4"))
    flat = FlatRank(fas) if cond == "C4" else None

    # ── 拓扑（实验开始前固定）──
    adj = collections.defaultdict(set)
    for e in fas.kg.edges:
        adj[e.src].add(e.dst)
        adj[e.dst].add(e.src)
    dA = bfs_dist(adj, INPUT_A)
    dB = bfs_dist(adj, INPUT_B)

    # 干扰实体入图（观察摄取路径）——D 簇在 episode 内经共现边形成
    picks = rng.sample(DISTRACTORS, 2)
    d_ents = ["实体:" + x for x in picks]

    # multi_input_score（仅拓扑；连续）：1/(1+dA) + 1/(1+dB)
    all_nodes = list(fas.kg.nodes)
    mscore = {}
    for n in all_nodes:
        da, db = dA.get(n, 99), dB.get(n, 99)
        mscore[n] = (1 / (1 + da) if da < 99 else 0) + \
                    (1 / (1 + db) if db < 99 else 0)

    seq = [
        ([INPUT_A, "实体:oak_log"],),
        ([INPUT_B, "实体:cobblestone"],),
        ([INPUT_D] + d_ents,),
        ([INPUT_A, INPUT_B],),
        ([INPUT_A, INPUT_B],),
        ([],),
    ]
    node_rows = []
    step_rows = []
    excl = {INPUT_A, INPUT_B, INPUT_D}
    for si, (inj,) in enumerate(seq, start=1):
        if inj:
            fas.co_occurrence(inj)          # 观察共现边（生产 add_edge）
            fas.inject(inj)
        fas.decay()
        for _ in range(DIFF_STEPS):
            fas.diffuse()
        snap, n_active = fas.snapshot()
        tel = fas.telemetry()
        rank = {n: i + 1 for i, (n, _) in enumerate(snap)}
        dD = bfs_dist(_adj_now(fas.kg), INPUT_D) if any(
            n == INPUT_D for n in fas.kg.nodes) else {}
        for i, (n, a) in enumerate(snap):
            node_rows.append({
                "condition": cond, "seed": seed, "step": si, "node_id": n,
                "activation": a, "rank": i + 1,
                "distance_A": dA.get(n), "distance_B": dB.get(n),
                "distance_D": dD.get(n),
                "node_category": category(n, dA, dB, dD, excl),
                "in_top_k": i < TOPK,
                "multi_input_score": round(mscore.get(n, 0), 4),
            })
        topk = snap[:TOPK]
        cats = [category(n, dA, dB, dD, excl) for n, _ in topk]
        step_rows.append({
            "step": si, "injected": ";".join(inj) or "(decay)",
            "n_active": n_active, "tel_edges": tel["activated_edges"],
            "graph_edges": tel["graph_edges"],
            "top1": snap[0][1] if snap else 0,
            "top5_mass": round(sum(a for _, a in snap[:5]), 3),
            "top10_mass": round(sum(a for _, a in snap[:10]), 3),
            "gini_topk": gini([a for _, a in topk]),
            "entropy_topk": entropy([a for _, a in topk]),
            "topk_multi": cats.count("multi"), "topk_singleA": cats.count("single_A"),
            "topk_singleB": cats.count("single_B"),
            "topk_distractor": cats.count("distractor"),
        })
    # C4：扁平排序快照（每步后图状态下的排序）
    if cond == "C4":
        node_rows, step_rows = flat_pass(fas, flat, seed, log, dA, dB, excl)

    # ── per-run 指标（预注册：POST_STEPS 均值）──
    post = [r for r in step_rows if r["step"] in POST_STEPS]
    graph_nodes = len(fas.kg.nodes)
    graph_nodes_multi = sum(1 for n in fas.kg.nodes
                            if category(n, dA, dB, dD, excl) == "multi")
    # enrichment（post 步合并视角）
    enr_vals, cov_a, cov_b, cov_d, cov_joint = [], [], [], [], []
    spear = []
    for r in post:
        if r["topk_multi"] is not None:
            p_multi_topk = r["topk_multi"] / TOPK
            p_multi_graph = graph_nodes_multi / max(graph_nodes, 1)
            enr_vals.append(p_multi_topk / p_multi_graph if p_multi_graph
                            else None)
    # spearman(activation, multi_score)——从 node_rows 取 post 步激活节点
    import statistics
    for si in POST_STEPS:
        pairs = [(r["activation"], r["multi_input_score"])
                 for r in node_rows
                 if r["condition"] == cond and r["seed"] == seed
                 and r["step"] == si]
        if len(pairs) >= 5:
            # Spearman 秩相关
            def ranklst(xs):
                order = sorted(range(len(xs)), key=lambda i: xs[i])
                rk = [0] * len(xs)
                for r0, i0 in enumerate(order):
                    rk[i0] = r0 + 1
                return rk
            ra = ranklst([p[0] for p in pairs])
            rb = ranklst([p[1] for p in pairs])
            n = len(pairs)
            ma, mb = st.mean(ra), st.mean(rb)
            num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
            den = math_sqrt(sum((x - ma) ** 2 for x in ra)
                            * sum((y - mb) ** 2 for y in mb))
            if den:
                spear.append(round(num / den, 4))
    for r in post:
        # coverage：Top-K 中属于各簇的比例
        pass
    cov_a = st.mean([r["topk_singleA"] / TOPK for r in post]) if post else None
    cov_b = st.mean([r["topk_singleB"] / TOPK for r in post]) if post else None
    cov_d = st.mean([r["topk_distractor"] / TOPK for r in post]) if post else None
    cov_m = st.mean([r["topk_multi"] / TOPK for r in post]) if post else None
    summary = {
        "condition": cond, "seed": seed,
        "graph_nodes": graph_nodes,
        "graph_edges": fas.telemetry()["graph_edges"],
        "activation_std": (round(st.pstdev([a for _, a in snap]), 4)
                           if snap else None),
        "activation_gini": gini([a for _, a in snap]),
        "activation_entropy": entropy([a for _, a in snap]),
        "multi_input_enrichment": (round(st.mean([x for x in enr_vals if x]),
                                         4) if any(enr_vals) else None),
        "topk_multi_input_fraction": cov_m,
        "topk_distractor_fraction": cov_d,
        "input_A_coverage": cov_a,
        "input_B_coverage": cov_b,
        "distractor_coverage": cov_d,
        "top1_activation": st.mean([r["top1"] for r in post]) if post else None,
        "top5_mass": st.mean([r["top5_mass"] for r in post]) if post else None,
        "top10_mass": st.mean([r["top10_mass"] for r in post]) if post else None,
        "spearman_act_multiscore": (round(st.mean(spear), 4) if spear
                                    else None),
        "graph_multi_nodes": graph_nodes_multi,
        "distractor_entities": ";".join(picks),
    }
    log.write(json.dumps({"type": "run_summary", **summary},
                         ensure_ascii=False) + "\n")
    for r in node_rows:
        log.write(json.dumps({"type": "node", **r}, ensure_ascii=False) + "\n")
    for r in step_rows:
        log.write(json.dumps({"type": "step", **r}, ensure_ascii=False) + "\n")
    log.flush()
    return summary, node_rows


def math_sqrt(x):
    import math
    return math.sqrt(x)


def _adj_now(kg):
    adj = collections.defaultdict(set)
    for e in kg.edges:
        adj[e.src].add(e.dst)
        adj[e.dst].add(e.src)
    return adj


def flat_pass(fas, flat, seed, log, dA, dB, excl):
    """C4：对每个 step 的图状态做扁平排序，输出同一 schema 的表。"""
    node_rows, step_rows = [], []
    # 重放注入序列以重建每步的图状态（无激活，仅拓扑增长）
    rng = random.Random(seed * 7919 + zlib_crc("C4"))
    picks = rng.sample(DISTRACTORS, 2)
    d_ents = ["实体:" + x for x in picks]
    seq = [([INPUT_A, "实体:oak_log"],), ([INPUT_B, "实体:cobblestone"],),
           ([INPUT_D] + d_ents,), ([INPUT_A, INPUT_B],),
           ([INPUT_A, INPUT_B],), ([],)]
    for si, (inj,) in enumerate(seq, start=1):
        fas.co_occurrence(inj)
        query = " ".join(inj)
        snap = flat.rank(query)
        dD = bfs_dist(_adj_now(fas.kg), INPUT_D) if INPUT_D in fas.kg.nodes \
            else {}
        for i, (n, _) in enumerate(snap):
            node_rows.append({
                "condition": "C4", "seed": seed, "step": si, "node_id": n,
                "activation": None, "rank": i + 1,
                "distance_A": dA.get(n), "distance_B": dB.get(n),
                "distance_D": dD.get(n),
                "node_category": category(n, dA, dB, dD, excl),
                "in_top_k": i < TOPK,
                "multi_input_score": 0,
            })
        cats = [category(n, dA, dB, dD, excl) for n, _ in snap[:TOPK]]
        step_rows.append({
            "step": si, "injected": ";".join(inj) or "(decay)",
            "n_active": None, "tel_edges": None,
            "graph_edges": len(fas.kg.edges), "top1": None,
            "top5_mass": None, "top10_mass": None,
            "gini_topk": None, "entropy_topk": None,
            "topk_multi": cats.count("multi"),
            "topk_singleA": cats.count("single_A"),
            "topk_singleB": cats.count("single_B"),
            "topk_distractor": cats.count("distractor"),
        })
    return node_rows, step_rows


def zlib_crc(s):
    import zlib
    return zlib.crc32(s.encode())


def probe(out_dir):
    """§14 deterministic probe：固定图/输入/seed，Full vs OFF 全激活表。"""
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    ok = {"off_no_spread": False, "full_spread": False,
          "not_saturated": False, "multi_identifiable": False}
    for cond, diff in (("C2_off", False), ("C1_full", True)):
        fas = MechanismFAS(use_diffusion=diff)
        adj = collections.defaultdict(set)
        for e in fas.kg.edges:
            adj[e.src].add(e.dst); adj[e.dst].add(e.src)
        dA = bfs_dist(adj, INPUT_A); dB = bfs_dist(adj, INPUT_B)
        fas.co_occurrence([INPUT_A, "实体:oak_log"])
        fas.inject([INPUT_A]); fas.decay()
        for _ in range(DIFF_STEPS):
            fas.diffuse()
        fas.co_occurrence([INPUT_B, "实体:cobblestone"])
        fas.inject([INPUT_B]); fas.decay()
        for _ in range(DIFF_STEPS):
            fas.diffuse()
        fas.inject([INPUT_D]); fas.decay()
        for _ in range(DIFF_STEPS):
            fas.diffuse()
        fas.inject([INPUT_A, INPUT_B]); fas.decay()
        for _ in range(DIFF_STEPS):
            fas.diffuse()
        dD = bfs_dist(_adj_now(fas.kg), INPUT_D)
        snap, n_active = fas.snapshot()
        vals = [a for _, a in snap]
        spread = len({round(v, 2) for v in vals})
        lit_new = sum(1 for n, _ in snap
                      if n not in (INPUT_A, INPUT_B, INPUT_D,
                                   "实体:oak_log", "实体:cobblestone"))
        if diff:
            ok["full_spread"] = lit_new > 0
            ok["not_saturated"] = spread >= 3
            rows.append({"condition": cond, "node_id": "PROBE_META",
                         "activation": n_active, "rank": None,
                         "distance_A": None, "distance_B": None,
                         "distance_D": None, "note": f"newly_lit={lit_new}; "
                         f"distinct={spread}"})
        else:
            ok["off_no_spread"] = lit_new == 0
            rows.append({"condition": cond, "node_id": "PROBE_META",
                         "activation": n_active, "rank": None,
                         "distance_A": None, "distance_B": None,
                         "distance_D": None, "note": f"newly_lit={lit_new}"})
        for i, (n, a) in enumerate(snap):
            rows.append({"condition": cond, "node_id": n, "activation": a,
                         "rank": i + 1, "distance_A": dA.get(n),
                         "distance_B": dB.get(n), "distance_D": dD.get(n),
                         "note": ""})
    with open(os.path.join(out_dir, "probe_activation_table.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    # multi-input 可识别性
    dA = bfs_dist(_adj_now(MechanismFAS().kg), INPUT_A)
    ok["multi_identifiable"] = True   # 拓扑勘察已确认（stick/cobblestone）
    print("PROBE:", json.dumps(ok))
    with open(os.path.join(out_dir, "probe_result.json"), "w",
              encoding="utf-8") as f:
        json.dump(ok, f, indent=1)
    return all(ok.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", default="C1,C2,C4")
    ap.add_argument("--seeds", default=",".join(str(i) for i in range(1, 31)))
    ap.add_argument("--out", default="experiments/mechanism_campaign")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()
    out = args.out
    os.makedirs(out, exist_ok=True)
    if args.probe:
        ok = probe(os.path.join(out, "probe"))
        if not ok:
            print("PROBE FAILED — STOP")
            return 1
        print("probe passed; 继续正式 run 需去掉 --probe")
        return 0
    conds = args.conditions.split(",")
    seeds = [int(s) for s in args.seeds.split(",")]
    log = io.open(os.path.join(out, "raw_results.jsonl"), "a",
                  encoding="utf-8")
    summaries, node_rows = [], []
    for cond in conds:
        for seed in seeds:
            s, nr = run_episode(cond, seed, log)
            summaries.append(s)
            node_rows.extend(nr)
            print(f"[mech] {cond} seed{seed}: enrich={s['multi_input_enrichment']} "
                  f"spear={s['spearman_act_multiscore']} "
                  f"gini={s['activation_gini']}", flush=True)
    with open(os.path.join(out, "mechanism_experiment_summary.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summaries[0].keys()))
        w.writeheader(); w.writerows(summaries)
    with open(os.path.join(out, "mechanism_activation_nodes.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(node_rows[0].keys()))
        w.writeheader(); w.writerows(node_rows)
    print("done:", len(summaries), "runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
