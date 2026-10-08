# scripts/ab_diffusion_test.py — 结构治理 A/B 扩散测试
# ============================================================================
# 同一引擎代码、同一扩散参数，分别加载：
#   A（baseline）= data/runtime_graph.json.bak.ab_baseline_20260919（治理前）
#   B（after）    = data/runtime_graph.json（治理后）
# 对固定种子组跑标准扩散（种子激活 2.5 → diffuse_round(max_depth=3)），
# 比较扩散的"局部性与语义性"。
#
# 指标：
#   - 静态 BFS 逐跳覆盖（1/2/3 跳，占全图比例）——结构本身发散程度
#   - 每种子：活跃节点数（activation>0.01）、平均激活、Top-20 构成
#     （枢纽占比 / 种子 1 跳邻居占比）、Haru/Self/用户 是否进 Top-20
#
# 运行： python scripts/ab_diffusion_test.py
# ============================================================================

import copy
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_model import KnowledgeGraph          # noqa: E402
from diffusion_engine import DiffusionEngine    # noqa: E402
import config                                    # noqa: E402

BASELINE = os.path.join("data", "runtime_graph.json.bak.ab_baseline_20260919")
AFTER = os.path.join("data", "runtime_graph.json")

HUB_SET = {"用户", "Haru", "Self", "CuriosityDrive", "能力_NLP实体关系提取",
           "能力注册表", "无聊"}

SEEDS = ["僵尸", "Minecraft", "Haru", "用户", "当前Minecraft状态", "无聊", "远方市"]

ADJ = None


def bfs_cover(kg, seed, hops):
    global ADJ
    adj = defaultdict(set)
    for e in kg.edges:
        adj[e.src].add(e.dst)
        adj[e.dst].add(e.src)
    seen = {seed}
    frontier = {seed}
    for _ in range(hops):
        nxt = set()
        for u in frontier:
            nxt |= adj.get(u, set()) - seen
        seen |= nxt
        frontier = nxt
    return seen


def load_fresh(path):
    """加载图并清零全部激活/边激活（公平比较：baseline 有历史残留激活）。"""
    kg = KnowledgeGraph.load(path)
    for n in kg.nodes.values():
        n.activation = 0.0
    for e in kg.edges:
        e.activation = 0.0
    kg.rebuild_indexes()
    return kg


def run_probe(kg, seed, cfg):
    """标准探针：单种子 2.5 激活 → max_depth=3 扩散。返回指标 dict。"""
    engine = DiffusionEngine(kg, cfg)
    engine.activate_from_inputs([seed], [])
    info = engine.diffuse_round()
    active = [n for n in kg.nodes.values() if n.activation > 0.01]
    topk, _ = engine.get_topk(k=20)
    top_ids = [n.id for n in topk]
    one_hop = bfs_cover(kg, seed, 1)
    hub_in_top = [nid for nid in top_ids if nid in HUB_SET]
    return {
        "seed": seed,
        "exists": seed in kg.nodes,
        "steps": info["steps"],
        "active_count": len(active),
        "mean_act": round(sum(n.activation for n in active) / max(len(active), 1), 3),
        "max_act": round(max((n.activation for n in active), default=0.0), 3),
        "top20": top_ids,
        "top20_hub_count": len(hub_in_top),
        "top20_hubs": hub_in_top,
        "top20_onehop_count": sum(1 for nid in top_ids if nid in one_hop),
        "haru_in_top": "Haru" in top_ids,
        "self_in_top": "Self" in top_ids,
        "user_in_top": "用户" in top_ids,
    }


def static_coverage(kg):
    n = len(kg.nodes)
    out = {}
    for seed in SEEDS + ["Self"]:
        if seed not in kg.nodes:
            continue
        out[seed] = {h: f"{len(bfs_cover(kg, seed, h)) * 100 // n}%" for h in (1, 2, 3)}
    return out


def main():
    cfg = copy.deepcopy(config.DEFAULT_CONFIG)
    cfg["max_depth"] = 3
    cfg["auto_open_browser"] = False

    results = {}
    for tag, path in (("A_baseline", BASELINE), ("B_after", AFTER)):
        if not os.path.exists(path):
            print(f"[跳过] {path} 不存在")
            continue
        kg = load_fresh(path)
        print(f"===== {tag}: {path} ({len(kg.nodes)} 节点 / {len(kg.edges)} 边) =====")
        results[tag] = {"graph": {"nodes": len(kg.nodes), "edges": len(kg.edges)},
                        "coverage": static_coverage(kg), "probes": {}}
        for seed in SEEDS:
            if seed not in kg.nodes:
                print(f"  [{seed}] 种子节点不在图中，跳过")
                continue
            r = run_probe(kg, seed, cfg)
            results[tag]["probes"][seed] = r
            print(f"  [{seed}] active={r['active_count']:3d} mean_act={r['mean_act']:.2f} "
                  f"top20: 枢纽{r['top20_hub_count']} 1跳邻居{r['top20_onehop_count']} "
                  f"Haru={'Y' if r['haru_in_top'] else '-'} "
                  f"Self={'Y' if r['self_in_top'] else '-'} "
                  f"用户={'Y' if r['user_in_top'] else '-'}")
        print()

    # ── 汇总对比 ──
    if "A_baseline" in results and "B_after" in results:
        a, b = results["A_baseline"], results["B_after"]
        print("===== A/B 汇总 =====")
        print(f"{'指标':<28} {'A(治理前)':<14} {'B(治理后)':<14}")
        for h in (1, 2, 3):
            seeds_a = a["coverage"].get("僵尸", {})
            seeds_b = b["coverage"].get("僵尸", {})
            print(f"{'僵尸 BFS ' + str(h) + ' 跳覆盖':<26} "
                  f"{seeds_a.get(h, '-'):<14} {seeds_b.get(h, '-'):<14}")
        for seed in SEEDS:
            pa = a["probes"].get(seed)
            pb = b["probes"].get(seed)
            if not pa or not pb:
                continue
            print(f"{seed + ' 扩散活跃节点':<26} "
                  f"{pa['active_count']:<14} {pb['active_count']:<14}")
            print(f"{seed + ' Top20枢纽数':<27} "
                  f"{pa['top20_hub_count']:<14} {pb['top20_hub_count']:<14}")
        hubs_a = sum(p["top20_hub_count"] for p in a["probes"].values())
        hubs_b = sum(p["top20_hub_count"] for p in b["probes"].values())
        tot = max(len(a["probes"]) * 20, 1)
        print(f"{'Top20 枢纽总占比':<27} {hubs_a}/{tot} ({hubs_a * 100 // tot}%)   "
              f"{hubs_b}/{tot} ({hubs_b * 100 // tot}%)")

    out = os.path.join("data", "ab_diffusion_report_20260919.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[JSON] {out}")


if __name__ == "__main__":
    import json
    main()
