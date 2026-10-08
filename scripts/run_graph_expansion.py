# scripts/run_graph_expansion.py — 图谱知识扩展一次性执行器（2026-09-22）
# ============================================================================
# 用法（离线，确认没有活着的 app.py 服务后运行）：
#   python scripts/run_graph_expansion.py --dry-run          # 只出统计与队列
#   python scripts/run_graph_expansion.py                    # 完整跑（可中断续跑）
#   python scripts/run_graph_expansion.py --phase expand     # 只跑某阶段
#   python scripts/run_graph_expansion.py --max-batches 3    # 限量试跑
#
# 阶段：expand(d1) → expand(d2 高价值) → link(既有节点配对) → bridge(跨簇)
# 每批完成：台账 append + state 落盘 + kg.save()（断点安全，Ctrl-C 可续）。
# 首次运行把原图备份到 data/graph_expansion/pre_expansion.json。
# 全程不调用 Web 搜索（时间敏感事实已在提示词层禁止，见 §11 的"必要时"）。

import argparse
import json
import logging
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("graph_expansion_run")

import graph_expansion as gx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH_PATH = os.path.join(ROOT, "data", "runtime_graph.json")
WORK_DIR = os.path.join(ROOT, "data", "graph_expansion")
STATE_PATH = os.path.join(WORK_DIR, "state.json")
LEDGER_PATH = os.path.join(WORK_DIR, "edge_ledger.jsonl")
BACKUP_PATH = os.path.join(WORK_DIR, "pre_expansion.json")


def one_time_backup():
    os.makedirs(WORK_DIR, exist_ok=True)
    if not os.path.exists(BACKUP_PATH):
        shutil.copy2(GRAPH_PATH, BACKUP_PATH)
        logger.info(f"[Expansion] 原图已备份 → {BACKUP_PATH}")
    else:
        logger.info(f"[Expansion] 备份已存在（续跑模式）: {BACKUP_PATH}")


def snapshot_stats(kg):
    degree = gx.node_degree(kg)
    classes = gx.classify_all(kg)
    return gx.graph_stats(kg, classes, degree), classes, degree


def append_ledger(lines):
    if not lines:
        return
    with open(LEDGER_PATH, "a", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")


def checkpoint(kg, state):
    gx.save_state(STATE_PATH, state)
    kg.save(GRAPH_PATH)


def top_pool(kg, degree, n=150):
    """按度排序的既有节点名池——给 LLM 做名称复用/消歧的参考面。"""
    return [x for x, _ in sorted(degree.items(), key=lambda kv: -kv[1])[:n]]


def seed_payload(kg, seed_ids):
    out = []
    for sid in seed_ids:
        n = kg.nodes.get(sid)
        ea = (n.extra_attrs or {}) if n else {}
        desc = str(ea.get("description") or ea.get("category") or "")[:60]
        out.append({"id": sid, "desc": desc})
    return out


def run_expand_phase(backend, kg, state, canon_index, guard, seeds_tiered,
                     batch_size, max_batches, tag, degree):
    """一或二深度扩展。seeds_tiered: [(id,tier)]；已展开的 seed 跳过（缓存）。"""
    done = [s for s, _t in seeds_tiered
            if state["expanded"].get(s, {}).get(f"expand_ts_{tag}")]
    todo = [s for s, _t in seeds_tiered if s not in done]
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    if max_batches:
        batches = batches[:max_batches]
    logger.info(f"[{tag}] 种子 {len(todo)}（已缓存跳过 {len(done)}）→ "
                f"{len(batches)} 批")
    ok = fail = 0
    for bi, batch in enumerate(batches):
        bid = f"{tag}-b{bi + 1}"
        user = gx.build_expand_prompt(
            seed_payload(kg, batch),
            existing_pool=top_pool(kg, degree) + batch)
        data, err = gx.call_batch(backend, gx._EXPAND_SYSTEM, user)
        if data is None:
            fail += 1
            state["errors"].append({"batch": bid, "error": err})
            logger.warning(f"[{bid}] LLM 失败（跳过续跑）: {err}")
            gx.save_state(STATE_PATH, state)
            continue
        items = data.get("items") or []
        lines = []
        st = gx.apply_items(kg, items, canon_index, state, bid, guard, lines)
        for s in batch:
            state["expanded"].setdefault(s, {})[f"expand_ts_{tag}"] = \
                gx.now_str()
        append_ledger(lines)
        checkpoint(kg, state)
        ok += 1
        logger.info(f"[{bid}] +节点{st['new_nodes']} +边{st['new_edges']} "
                    f"合并{st['merged_edges']} 拒{sum(st['rejected'].values())}"
                    f"（累计 节{state['_run']['new_nodes']}/边{state['_run']['new_edges']}）")
        degree = gx.node_degree(kg)      # d2 选种要用新度分布
        time.sleep(1)   # 对 API 温柔一点
    return {"batches_ok": ok, "batches_fail": fail}


def run_link_phase(backend, kg, state, canon_index, guard, classes, degree,
                   batch_size, max_batches):
    # 重算分类快照：一阶段新节点也参与配对（expanded_knowledge → public）
    classes = gx.classify_all(kg)
    state["_classes"] = classes
    pairs = gx.gen_link_candidates(kg, classes, degree)
    batches = [pairs[i:i + batch_size] for i in range(0, len(pairs), batch_size)]
    if max_batches:
        batches = batches[:max_batches]
    logger.info(f"[link] 候选对子 {len(pairs)} → {len(batches)} 批")
    ok = fail = added = 0
    for bi, batch in enumerate(batches):
        bid = f"link-b{bi + 1}"
        data, err = gx.call_batch(backend, gx._LINK_SYSTEM,
                                  gx.build_link_prompt(batch))
        if data is None:
            fail += 1
            state["errors"].append({"batch": bid, "error": err})
            logger.warning(f"[{bid}] LLM 失败: {err}")
            continue
        items = []
        for link in data.get("links") or []:
            items.append({"seed": str(link.get("src") or ""),
                          "nodes": [], "edges": [link]})
        lines = []
        st = gx.apply_items(kg, items, canon_index, state, bid, guard, lines)
        append_ledger(lines)
        checkpoint(kg, state)
        ok += 1
        added += st["new_edges"]
        logger.info(f"[{bid}] 补边 {st['new_edges']}（拒 {sum(st['rejected'].values())}）")
        time.sleep(1)
    return {"batches_ok": ok, "batches_fail": fail, "new_edges": added}


def run_bridge_phase(backend, kg, state, canon_index, guard, classes, degree):
    classes = gx.classify_all(kg)
    state["_classes"] = classes
    comps = gx.public_components(kg, classes)
    logger.info(f"[bridge] 公共子图分量数: {len(comps)}")
    if len(comps) < 2:
        return {"bridges": 0}
    pool = sorted(kg.nodes.keys())
    total = 0
    # 最大簇依次与后面的较大簇尝试桥接（至多 4 对，防失控）
    big = comps[:5]
    tried = 0
    for i in range(1, len(big)):
        if tried >= 4:
            break
        a = sorted(big[0], key=lambda n: -degree.get(n, 0))[:12]
        b = sorted(big[i], key=lambda n: -degree.get(n, 0))[:12]
        data, err = gx.call_batch(backend, gx._BRIDGE_SYSTEM,
                                  gx.build_bridge_prompt(a, b, pool))
        tried += 1
        if data is None:
            state["errors"].append({"batch": f"bridge-{i}", "error": err})
            continue
        items = [{"seed": e.get("src", ""), "nodes": [], "edges": [e]}
                 for e in data.get("bridges") or []]
        lines = []
        st = gx.apply_items(kg, items, canon_index, state, f"bridge-{i}",
                            guard, lines)
        append_ledger(lines)
        total += st["new_edges"]
        logger.info(f"[bridge-{i}] 跨簇连接 {st['new_edges']} 条")
    checkpoint(kg, state)
    return {"bridges": total}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计不出网")
    ap.add_argument("--phase", default="all",
                    choices=["all", "expand", "expand2", "link", "bridge"])
    ap.add_argument("--batch-size", type=int, default=10)
    ap.add_argument("--max-batches", type=int, default=0, help="0=不限")
    ap.add_argument("--t1", type=int, default=40, help="Tier1 种子上限")
    ap.add_argument("--t2", type=int, default=70)
    ap.add_argument("--t3", type=int, default=50)
    ap.add_argument("--min-conf", type=float, default=0.55)
    args = ap.parse_args()

    one_time_backup()
    kg = gx.KnowledgeGraph.load(GRAPH_PATH)
    before, classes, degree = snapshot_stats(kg)
    seeds = gx.select_seeds(kg, classes, degree,
                            tier1_max=args.t1, tier2_max=args.t2,
                            tier3_max=args.t3)
    est_batches = (len(seeds) + args.batch_size - 1) // args.batch_size

    print(json.dumps({
        "before": before,
        "public_classes": {k: v for k, v in before["class_counts"].items()},
        "seeds": {"total": len(seeds),
                  "tier1": sum(1 for _s, t in seeds if t == 1),
                  "tier2": sum(1 for _s, t in seeds if t == 2),
                  "tier3": sum(1 for _s, t in seeds if t == 3)},
        "estimated_llm_batches_depth1": est_batches,
    }, ensure_ascii=False, indent=1))

    if args.dry_run:
        # 队列清单（前 40 个）供肉眼审一遍
        print(json.dumps([{"seed": s, "tier": t} for s, t in seeds[:40]],
                         ensure_ascii=False))
        return

    state = gx.load_state(STATE_PATH)
    state["_classes"] = classes
    canon_index = gx.build_canon_index(kg)
    guard = {"min_confidence": args.min_conf,
             "max_edges_per_seed": 12, "max_new_nodes_per_seed": 6,
             "new_node_budget": 500, "new_edge_budget": 1200}
    backend = gx.llm_backend()

    results = {}
    if args.phase in ("all", "expand"):
        results["expand1"] = run_expand_phase(
            backend, kg, state, canon_index, guard, seeds,
            args.batch_size, args.max_batches, "d1", degree)
        degree = gx.node_degree(kg)
        classes = gx.classify_all(kg)
        state["_classes"] = classes
    if args.phase in ("all", "expand2"):
        # 深度 2：只对一阶段新节点里连接最活跃的 ≤24 个
        new_names = []
        seen = set()
        for info in state["expanded"].values():
            for nm in info.get("new_nodes") or []:
                if nm not in seen and nm in kg.nodes:
                    seen.add(nm)
                    new_names.append(nm)
        d2 = [(n, 4) for n in sorted(new_names,
                                     key=lambda n: -degree.get(n, 0))[:24]]
        if d2:
            results["expand2"] = run_expand_phase(
                backend, kg, state, canon_index, guard, d2,
                args.batch_size, args.max_batches or 3, "d2", degree)
            degree = gx.node_degree(kg)
            classes = gx.classify_all(kg)
            state["_classes"] = classes
    if args.phase in ("all", "link"):
        results["link"] = run_link_phase(
            backend, kg, state, canon_index, guard, classes, degree,
            batch_size=12, max_batches=args.max_batches or 10)
    if args.phase in ("all", "bridge"):
        results["bridge"] = run_bridge_phase(
            backend, kg, state, canon_index, guard, classes, degree)

    after, classes2, degree2 = snapshot_stats(kg)
    report = {
        "before": before, "after": after,
        "delta": {"nodes": after["nodes"] - before["nodes"],
                  "edges": after["edges"] - before["edges"],
                  "isolated": after["isolated"] - before["isolated"],
                  "avg_degree": round(after["avg_degree"] - before["avg_degree"], 3),
                  "largest_component": after["largest_component"] - before["largest_component"]},
        "phases": results,
        "errors": state["errors"][-10:],
        "top_degree_after": gx.top_nodes(kg, degree2, 20),
    }
    checkpoint(kg, state)
    rpt_path = os.path.join(WORK_DIR, f"report_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(rpt_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    logger.info(f"[Expansion] 报告 → {rpt_path}")


if __name__ == "__main__":
    main()
