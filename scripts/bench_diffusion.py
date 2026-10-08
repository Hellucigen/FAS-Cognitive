# -*- coding: utf-8 -*-
"""扩散激活性能改造的行为等价指纹 + 性能对照基准。

三个模式：
  基线   python scripts/bench_diffusion.py --sizes 1000,10000 --out scripts/bench_baseline.json
  对比   python scripts/bench_diffusion.py --sizes 1000,10000 --compare scripts/bench_baseline.json
  计时   python scripts/bench_diffusion.py --timing

设计（对应 docs/fas_scale_performance.md 实施计划）：
  - 固定种子合成图（1k/10k 全指纹；50k 仅计时），随机序列与 PYTHONHASHSEED 无关
    （指纹只依赖 dict 插入序 / 列表序 / 稳定排序，不依赖 set 迭代序）
  - 输入序列覆盖：activate_from_inputs、mark_active 直写路径（好奇链路式 +2.0）、
    清零直写、好奇两段 diffuse_round(max_steps=3)→diffuse_round()、独立 decay_step、
    rename / 重复三元组 / 自环 / remove_edge / update_edge / remove_node
  - 指纹：top-30 (id, 激活) + 行动队列 + 激活总和 + 边激活总和 + 规模
  - 等价白名单：改造后值 == 0 且改造前值 < activation_epsilon(1e-4) 视为等价
    （EPS 吸附例外，见计划承诺）；top-30 允许激活值几乎相等的相邻项交换
  - 索引一致性断言（改造后生效）：_out/_in/_pair 索引与 kg.edges 列表逐条
    交叉验证 + rebuild_indexes() 前后等价——最灵敏的不变量检查
"""
import argparse
import copy
import json
import logging
import os
import random
import statistics
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.disable(logging.INFO)  # 生产级 INFO 日志会淹没计时；WARNING/ERROR 仍显示

# 中立化 evolution log（避免 bench 污染 data/ 下真实演进日志）
import graph_evolution_log


class _StubEvo:
    def record(self, *a, **k):
        pass


graph_evolution_log.get_evolution_log = lambda: _StubEvo()

from graph_model import KnowledgeGraph, Node, Edge  # noqa: E402
from diffusion_engine import DiffusionEngine  # noqa: E402
from config import DEFAULT_CONFIG  # noqa: E402

EPS = 1e-4          # activation_epsilon（与改造后 config 值一致）
VAL_TOL = 1e-8      # 浮点求和顺序漂移容差（远低于全库最低决策阈值 0.01）

RELS = [("属于", "semantic_relation"), ("导致", "causal_relation"),
        ("喜欢", "emotional_relation"), ("触发", "procedural_relation"),
        ("发生于", "temporal_relation"), ("思考", "cognitive_relation")]

# 10 周期 → 空间占比 semantic .6 / episodic .2 / cognitive .2 / self .1
SPACE_CYCLE = ["semantic"] * 6 + ["episodic"] * 2 + ["cognitive"] * 2 + ["self"]


def build_graph(n_nodes: int, seed: int):
    rng = random.Random(seed)
    kg = KnowledgeGraph()
    for i in range(n_nodes):
        nid = f"n{i:06d}"
        space = SPACE_CYCLE[i % 10]
        label = ("procedural" if rng.random() < 0.01
                 else ("declarative-episodic" if space == "episodic"
                       else "declarative-semantic"))
        kg.add_node(Node(id=nid, weight=round(rng.uniform(0.2, 1.0), 3),
                         label=label, graph_space=space))
    for i in range(1, n_nodes):
        for _ in range(rng.randint(1, 2)):
            j = rng.randint(max(0, i - 50), i - 1)   # 局部连接 → 活跃前沿天然稀疏
            rel, cat = RELS[rng.randrange(len(RELS))]
            kg.add_edge(Edge(src=f"n{j:06d}", dst=f"n{i:06d}", relation=rel,
                             weight=round(rng.uniform(0.3, 1.0), 3),
                             relation_category=cat))
    for i in range(0, n_nodes, 97):                   # 自环
        kg.add_edge(Edge(src=f"n{i:06d}", dst=f"n{i:06d}", relation="关联",
                         weight=0.5, relation_category="semantic_relation"))
    for e in list(kg.edges[: max(1, n_nodes // 100)]):  # 重复三元组
        kg.add_edge(Edge(src=e.src, dst=e.dst, relation=e.relation,
                         weight=e.weight, relation_category=e.relation_category))
    return kg, rng


def make_engine(kg):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["activation_epsilon"] = EPS
    return DiffusionEngine(kg, cfg)


# ────────────────────────── 场景 ──────────────────────────

def structural_ops(kg, engine, rng, all_ids):
    """rename / 重复三元组 / 自环 / remove_edge / update / remove_node。

    rename 用 getattr 分叉：改造后走 kg.rename_node；改造前复现生产直写语义
    （app.py:1219-1226）——两者必须产出相同的图状态。
    """
    # rename（目标 id 一定不存在）
    nid = all_ids[rng.randrange(len(all_ids))]
    new_id = f"renamed_{nid}"
    if kg.get_node(nid) is not None and kg.get_node(new_id) is None:
        if hasattr(kg, "rename_node"):
            kg.rename_node(nid, new_id)
        else:
            node = kg.nodes.pop(nid)
            node.id = new_id
            kg.nodes[new_id] = node
            for edge in kg.edges:
                if edge.src == nid:
                    edge.src = new_id
                if edge.dst == nid:
                    edge.dst = new_id
        all_ids.remove(nid)
        all_ids.append(new_id)
        engine.name_to_node.pop(nid, None)
        engine.name_to_node[new_id] = kg.nodes[new_id]

    # 再造一条重复三元组（add_edge 不查重——两版本语义一致）
    e = kg.edges[rng.randrange(len(kg.edges))]
    kg.add_edge(Edge(src=e.src, dst=e.dst, relation=e.relation,
                     weight=e.weight, relation_category=e.relation_category))

    # 自环
    nid2 = all_ids[rng.randrange(len(all_ids))]
    kg.add_edge(Edge(src=nid2, dst=nid2, relation="关联",
                     weight=0.4, relation_category="semantic_relation"))

    # remove_edge：随机删一条
    e2 = kg.edges[rng.randrange(len(kg.edges))]
    kg.remove_edge(e2.src, e2.dst, e2.relation)

    # remove_edge：删重复三元组的一条（清重复语义）
    key_counts = Counter((x.src, x.dst, x.relation) for x in kg.edges)
    dups = [k for k, c in key_counts.items() if c > 1]
    if dups:
        s, d, r = dups[rng.randrange(len(dups))]
        kg.remove_edge(s, d, r)

    # update_edge 权重
    e3 = kg.edges[rng.randrange(len(kg.edges))]
    kg.update_edge(e3.src, e3.dst, e3.relation,
                   weight=round(rng.uniform(0.1, 0.9), 3))

    # remove_node：删一个当前无边的节点（避免大面积断边干扰指纹可比性）
    linked = set()
    for x in kg.edges:
        linked.add(x.src)
        linked.add(x.dst)
    free = [x for x in all_ids if x not in linked and x in kg.nodes]
    if free:
        victim = free[rng.randrange(len(free))]
        kg.remove_node(victim)
        all_ids.remove(victim)
        engine.name_to_node.pop(victim, None)


def run_scenario(kg, engine, n_nodes, rounds):
    rng = random.Random(1234 + n_nodes)
    all_ids = list(kg.nodes.keys())
    edge_keys = [(e.src, e.dst, e.relation) for e in kg.edges]
    checkpoints = {}

    def record(tag):
        pos = sum(1 for n in kg.nodes.values() if n.activation > 0)
        if pos < 35:
            raise RuntimeError(f"检查点 {tag} 正激活节点仅 {pos} 个，top-30 指纹会包含零值填充，"
                               f"无法做前后等价比较——请加大激活注入量")
        top_nodes, _ = engine.get_topk(30)
        checkpoints[tag] = {
            "nodes": len(kg.nodes),
            "edges": len(kg.edges),
            "top30": [[n.id, round(n.activation, 9)] for n in top_nodes],
            "queue": [[nid, round(a, 9)] for a, nid in engine.action_queue],
            # 总和只统计 >= EPS 的值：(0, EPS) 是已承诺的吸附带（改造后
            # 归零），计入会把白名单例外放大成总量差异——故两边同滤
            "act_sum": round(sum(n.activation for n in
                                 sorted(kg.nodes.values(), key=lambda x: x.id)
                                 if n.activation >= EPS), 9),
            "eact_sum": round(sum(e.activation for e in kg.edges
                                  if e.activation >= EPS), 9),
        }

    for r in range(rounds):
        # 1) 输入激活（含 2 个 miss + 2 条错关系 spec）
        seeds = rng.sample(all_ids, 20) + ["不存在的概念甲", "不存在的概念乙"]
        sim_map = {s: round(rng.uniform(0.5, 1.0), 3) for s in seeds[:8]}
        especs = []
        for _ in range(10):
            s, d, rel = edge_keys[rng.randrange(len(edge_keys))]
            especs.append({"src": s, "dst": d, "type": rel})
        especs.append({"src": "不存在的概念甲", "dst": seeds[0], "type": "属于"})

        engine.activate_from_inputs(seeds, especs, sim_map)

        # 2) 直写路径（好奇链路式 +2.0；改造后必须 mark_active）
        dw = rng.sample(all_ids, 3)
        for x in dw:
            node = kg.nodes[x]
            node.activation = min(5.0, node.activation + 2.0)
        if hasattr(engine, "mark_active"):
            engine.mark_active(dw)

        # 3) 清零直写（EPS 修剪路径，两版本都无需额外动作）
        kg.nodes[all_ids[rng.randrange(len(all_ids))]].activation = 0.0

        # 4) 好奇两段扩散 + 5) 独立衰减（auto 线程路径）
        engine.diffuse_round(max_steps=3)
        engine.diffuse_round()
        engine.decay_step()

        record(f"round{r}")

        # 6) 结构操作（每 3 轮一次），随后显式全量刷新行动队列再记录
        if r % 3 == 2:
            structural_ops(kg, engine, rng, all_ids)
            engine._refresh_action_queue()
            record(f"struct{r}")

    return checkpoints


# ────────────────────────── 索引一致性断言 ──────────────────────────

def assert_index_consistency(kg):
    if not hasattr(kg, "_out_index"):
        return "SKIP(无索引)"
    errors = []
    listed = {id(e) for e in kg.edges}

    out_counter = {k: Counter(id(e) for e in v) for k, v in kg._out_index.items()}
    in_counter = {k: Counter(id(e) for e in v) for k, v in kg._in_index.items()}
    for e in kg.edges:
        if out_counter.get(e.src, Counter())[id(e)] < 1:
            errors.append(f"out_index 缺 {e.src} 的边 {id(e)}")
        if in_counter.get(e.dst, Counter())[id(e)] < 1:
            errors.append(f"in_index 缺 {e.dst} 的边 {id(e)}")
    for src, bucket in kg._out_index.items():
        for e in bucket:
            if id(e) not in listed:
                errors.append(f"out_index[{src}] 含列表外的边")
    for dst, bucket in kg._in_index.items():
        for e in bucket:
            if id(e) not in listed:
                errors.append(f"in_index[{dst}] 含列表外的边")

    # pair_index first-wins（= 列表序首条）
    expect_pair = {}
    for e in kg.edges:
        inner = expect_pair.setdefault((e.src, e.dst), {})
        if e.relation not in inner:
            inner[e.relation] = e
    got_pair = {k: {r: id(e) for r, e in v.items()} for k, v in kg._pair_index.items()}
    exp_pair = {k: {r: id(e) for r, e in v.items()} for k, v in expect_pair.items()}
    if got_pair != exp_pair:
        errors.append("pair_index 与列表首条语义不一致")

    # node_order 与节点集一致
    if hasattr(kg, "_node_order"):
        if set(kg._node_order) != set(kg.nodes):
            errors.append("_node_order 与节点集不一致")

    # rebuild 等价（_node_order 的绝对数值 rebuild 前后不同——全局计数器 vs
    # 0..N-1 重排——故比较其诱导的节点顺序而非数值本身）
    def norm_order(d):
        return [nid for nid, _ in sorted(d.items(), key=lambda kv: kv[1])]

    def snap():
        return ({k: [id(e) for e in v] for k, v in kg._out_index.items()},
                {k: [id(e) for e in v] for k, v in kg._in_index.items()},
                got_pair,
                norm_order(kg._node_order) if hasattr(kg, "_node_order") else None)

    before = snap()
    kg.rebuild_indexes()
    # got_pair 引用旧 dict——重新生成
    got_pair2 = {k: {r: id(e) for r, e in v.items()} for k, v in kg._pair_index.items()}
    before = (before[0], before[1], got_pair, before[3])
    after = ({k: [id(e) for e in v] for k, v in kg._out_index.items()},
             {k: [id(e) for e in v] for k, v in kg._in_index.items()},
             got_pair2,
             norm_order(kg._node_order) if hasattr(kg, "_node_order") else None)
    if before != after:
        errors.append("rebuild_indexes() 前后索引不一致")
    return errors or None


# ────────────────────────── 等价比较 ──────────────────────────

def val_eq(base, cur):
    """EPS 吸附白名单 + 浮点漂移容差。base=改造前, cur=改造后。"""
    if cur == 0.0 and 0.0 <= base < EPS:
        return True
    if base == 0.0 and 0.0 <= cur < EPS:
        return True
    return abs(base - cur) <= VAL_TOL * max(1.0, abs(base))


def seq_eq(base, cur, what, errs):
    """带相邻交换允许的序列比较（并列项顺序不稳定）。条目形状统一 [id, val]。"""
    if len(base) != len(cur):
        errs.append(f"{what}: 长度 {len(base)} → {len(cur)}")
        return
    i, n = 0, len(base)
    while i < n:
        if base[i][0] == cur[i][0]:
            if not val_eq(base[i][1], cur[i][1]):
                errs.append(f"{what}[{i}] {base[i][0]}: {base[i][1]} → {cur[i][1]}")
            i += 1
            continue
        if (i + 1 < n and base[i + 1][0] == cur[i][0] and base[i][0] == cur[i + 1][0]
                and val_eq(base[i][1], base[i + 1][1])
                and val_eq(cur[i][1], cur[i + 1][1])):
            i += 2
            continue
        errs.append(f"{what}[{i}]: {base[i]} → {cur[i]}")
        return


def compare_checkpoints(base_ckpt, cur_ckpt, graph_label):
    errs = []
    for tag, b in base_ckpt.items():
        if tag not in cur_ckpt:
            errs.append(f"[{graph_label}] 缺检查点 {tag}")
            continue
        c = cur_ckpt[tag]
        if b["nodes"] != c["nodes"] or b["edges"] != c["edges"]:
            errs.append(f"[{graph_label}]{tag} 规模: {b['nodes']}/{b['edges']} → "
                        f"{c['nodes']}/{c['edges']}")
        seq_eq(b["top30"], c["top30"], f"[{graph_label}]{tag}.top30", errs)
        seq_eq(b["queue"], c["queue"], f"[{graph_label}]{tag}.queue", errs)
        if not val_eq(b["act_sum"], c["act_sum"]):
            errs.append(f"[{graph_label}]{tag}.act_sum: {b['act_sum']} → {c['act_sum']}")
        if not val_eq(b["eact_sum"], c["eact_sum"]):
            errs.append(f"[{graph_label}]{tag}.eact_sum: {b['eact_sum']} → {c['eact_sum']}")
    return errs


# ────────────────────────── 计时模式 ──────────────────────────

def timing_mode():
    print("=" * 72)
    print("性能对照基准（每消息路径 + decay_step）")
    print("=" * 72)
    rows = []
    for n in (1000, 10000, 50000):
        t0 = time.perf_counter()
        kg, _ = build_graph(n, 42)
        t_build = time.perf_counter() - t0
        engine = make_engine(kg)
        all_ids = list(kg.nodes.keys())
        edge_keys = [(e.src, e.dst, e.relation) for e in kg.edges]
        rng = random.Random(99)
        # 预热 + 压出活跃前沿
        seeds = rng.sample(all_ids, 30)
        engine.activate_from_inputs(seeds, [], {})
        engine.diffuse_round()

        def med(fn, repeat):
            xs = []
            for _ in range(repeat):
                t = time.perf_counter()
                fn()
                xs.append((time.perf_counter() - t) * 1000)
            return statistics.median(xs)

        t_act = med(lambda: engine.activate_from_inputs(rng.sample(all_ids, 20), [], {}), 15)
        t_dif = med(lambda: engine.diffuse_round(), 8)
        t_top = med(lambda: engine.get_topk(20), 40)
        t_dec = med(lambda: engine.decay_step(), 20)
        rows.append((n, len(kg.edges), t_build, t_act, t_dif, t_top, t_dec))
        print(f"\n[graph {n} 节点 / {len(kg.edges)} 边]  构建耗时 {t_build:.2f}s")
        print(f"  activate_from_inputs : {t_act:8.2f} ms")
        print(f"  diffuse_round (6步)  : {t_dif:8.2f} ms")
        print(f"  get_topk(20)         : {t_top:8.2f} ms")
        print(f"  decay_step           : {t_dec:8.2f} ms")
        print(f"  ── 每消息路径合计 (act+dif+top): {t_act + t_dif + t_top:8.2f} ms")
    return rows


# ────────────────────────── 主流程 ──────────────────────────

def fingerprint_mode(sizes, out_path, compare_path):
    results = {}
    index_errors = None
    for n in sizes:
        rounds = 12 if n <= 2000 else 8
        print(f"── 合成图 {n} 节点 × {rounds} 轮 ──")
        kg, _ = build_graph(n, 42)
        engine = make_engine(kg)
        ckpt = run_scenario(kg, engine, n, rounds)
        errs = assert_index_consistency(kg)
        if isinstance(errs, list):
            index_errors = errs
            for e in errs[:10]:
                print(f"  [INDEX-FAIL] {e}")
        else:
            print(f"  索引一致性: {errs}")
        results[str(n)] = {"rounds": rounds, "checkpoints": ckpt}
        print(f"  检查点: {len(ckpt)}")

    if compare_path:
        with open(compare_path, "r", encoding="utf-8") as f:
            base = json.load(f)
        all_errs = []
        for n in sizes:
            b = base["graphs"].get(str(n))
            if b is None:
                all_errs.append(f"基线缺少 {n} 图数据")
                continue
            all_errs += compare_checkpoints(
                b["checkpoints"], results[str(n)]["checkpoints"], f"n={n}")
        print("=" * 72)
        if all_errs:
            print(f"行为等价: ✘ {len(all_errs)} 处差异")
            for e in all_errs[:30]:
                print(f"  - {e}")
            sys.exit(1)
        print("行为等价: ✔ 全部检查点一致（含 EPS 白名单与并列交换容差）")
        print(f"索引一致性: {'✔' if index_errors is None else '✘'}")
        if index_errors is not None:
            sys.exit(1)
    else:
        payload = {
            "meta": {"created": time.strftime("%Y/%m/%d %H:%M:%S"),
                     "eps": EPS, "val_tol": VAL_TOL, "seed_graph": 42},
            "graphs": results,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(f"基线已写入 {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="1000,10000")
    ap.add_argument("--out", default=os.path.join("scripts", "bench_baseline.json"))
    ap.add_argument("--compare", default=None)
    ap.add_argument("--timing", action="store_true")
    args = ap.parse_args()

    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    if args.timing:
        timing_mode()
    else:
        fingerprint_mode(sizes, args.out, args.compare)


if __name__ == "__main__":
    main()
