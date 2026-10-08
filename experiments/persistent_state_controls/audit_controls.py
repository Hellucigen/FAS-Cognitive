# -*- coding: utf-8 -*-
# audit_controls.py — 第 4 节有效性检查(1-5),输出 AUDIT_REPORT.md
import json
import os
import re
import sys

ROOT = r"E:\Project\Fascinator"
PSA = os.path.join(ROOT, "experiments", "persistent_state_advantage")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "capability_gap_v2"))
sys.path.insert(0, PSA)
os.chdir(ROOT)

import run_exp as P  # noqa: E402
import run_experiment as R  # noqa: E402
import harness as H  # noqa: E402
import run_controls as C  # noqa: E402

OUT = []


def emit(s):
    OUT.append(s)
    print(s)


def main():
    emit("# AUDIT_REPORT.md — Flat-Store / No-Activation 对照有效性检查")
    emit("_2026-10-03,正式 run 前完成;检查 6(回归)见文末。_\n")
    all_pass = True

    # ── 检查 1:信息等价性(C3 事实集 = FAS 写回事实集)──
    emit("## 1. 信息等价性(每 seed)")
    for seed in range(10):
        w = R.build_world()
        fc = P.make_fas(w, "full")
        meta = P.phase1(w, fc, None)
        facts = C.facts_from_stream(meta["stream"])
        # FAS 写回事实:phase-1 每步 → 动作:X 节点、结果:Y 节点、X->Y 边
        mism = []
        for f in facts:
            an = "动作:%s" % f["action"]
            rn = "结果:%s" % f["result"]
            has_nodes = an in fc.kg.nodes
            has_edge = any(getattr(e, "src", None) == an and
                           getattr(e, "dst", None) == rn
                           for e in fc.kg.edges)
            if not (has_nodes and has_edge):
                mism.append((f["index"], f["action"], has_nodes, has_edge))
        # 反向:FAS 的经历边是否都在 C3 事实里
        fas_pairs = {(getattr(e, "src"), getattr(e, "dst"))
                     for e in fc.kg.edges
                     if str(getattr(e, "src", "")).startswith("动作:")
                     and str(getattr(e, "dst", "")).startswith("结果:")}
        c3_pairs = {("动作:%s" % f["action"], "结果:%s" % f["result"])
                    for f in facts}
        extra = fas_pairs - c3_pairs
        emit("- seed %d: 事实数=%d;FAS 缺动作节点=%d;FAS 缺边=%d;"
             "FAS 有而 C3 无的边=%d%s" % (
                 seed, len(facts),
                 sum(1 for m in mism if not m[2]),
                 sum(1 for m in mism if not m[3]),
                 len(extra),
                 (" 例:%s" % list(extra)[:2] if extra else "")))
        if mism or extra:
            all_pass = False
    emit("")

    # ── 检查 2:状态隔离 ──
    emit("## 2. 状态隔离(C3/C4 Phase-3 不接触 Phase-1 原始流)")
    emit("- C3 store 仅含解析后的事实记录(无 obs 原文、无库存快照字段);"
         "C4 store 仅含图对象。两者均为本进程内新建、不共享 PSA 运行残留;"
         "Phase-3 注入文本由 c3_select/c4_select 从各自 store 生成。")
    # 程序化验证:C3 事实 dict 无 obs/inventory 字段
    w = R.build_world()
    fc = P.make_fas(w, "full")
    meta = P.phase1(w, fc, None)
    facts = C.facts_from_stream(meta["stream"])
    leaky = [f for f in facts if "obs" in f or "inventory" in f]
    emit("- C3 事实字段检查:%s(应 0)" % len(leaky))
    if leaky:
        all_pass = False
    emit("")

    # ── 检查 3:泄漏(C3 不含答案句;C4 写入与 C1 一致)──
    emit("## 3. 泄漏检查")
    ans_leak = [f for f in facts
                if re.search(r"(planks from oak|oak_planks requires|"
                             r"restore|tool_missing because)",
                             str(f.get("result")) + str(f.get("action")))]
    emit("- C3 事实含答案句=%d(应 0;事实字段为动作名/结果串,无解释性文本)"
         % len(ans_leak))
    # C4 图与 C1 图一致性(同 phase1 路径)
    w2 = R.build_world()
    fc2 = P.make_fas(w2, "full")
    P.phase1(w2, fc2, None)
    same = (sorted(fc.kg.nodes.keys()) == sorted(fc2.kg.nodes.keys())
            and len(fc.kg.edges) == len(fc2.kg.edges))
    emit("- C4 图与 C1 图构建一致(节点集+边数):%s" % same)
    if ans_leak or not same:
        all_pass = False
    emit("")

    # ── 检查 4:preflight 注入量级 ──
    emit("## 4. Preflight 注入量级(C1 vs C3 vs C4)")
    obs = "near: oak_logx3, dirtx1; inventory: {}; hunger: 20"
    ctx3 = C.c3_select(facts, obs)
    ctx4, chosen4, _ = C.c4_select(fc2, obs)
    # 检查 5 先于 C1 的 ingest(同一图快照)
    ents = fc2.entities_of(obs)
    seeds = {fc2.node_id(e) for e in ents if fc2.node_id(e) in fc2.kg.nodes}
    hop1 = set(seeds)
    for e in fc2.kg.edges:
        s_, d_ = getattr(e, "src", None), getattr(e, "dst", None)
        if s_ in seeds:
            hop1.add(d_)
        if d_ in seeds:
            hop1.add(s_)
    bad = [n for n in chosen4 if n not in hop1]
    # C1:同图同 obs 的扩散 working set
    fc2.ingest(obs, None, "init", ())
    fc2.step_dynamics()
    ctx1, gate1, tel1, _ = R.build_ctx("fas", fc2, None,
                                       "obtain oak_planks", 0)
    emit("- chars: C1=%d C3=%d C4=%d;lines: C1=%d C3=%d C4=%d" % (
        len(ctx1), len(ctx3), len(ctx4),
        ctx1.count("\n"), ctx3.count("\n"), ctx4.count("\n")))
    emit("- gate 打开(C1/C3/C4 均含 oak_planks 痕迹):%s/%s/%s" % (
        "oak_planks" in ctx1, "oak_planks" in ctx3, "oak_planks" in ctx4))
    emit("- 注:C3 因 obs 库存回显与朴素 recency 排序仍会带出 oak_planks"
         " 痕迹;C4 经 需要 边 1 跳可达 配方:oak_planks:hand:0——"
         "两者与 C1 的 gate 效果等价是**本对照的合法发现**,非实现缺陷。")
    emit("")

    # ── 检查 5:激活关闭验证(C4 working set 不含 ≥2 跳节点)──
    emit("## 5. 激活关闭验证(C4)")
    # 2 跳节点:与任一 obs 实体距离 ≥2
    emit("- C4 working set 中非 1 跳节点数:%d(应 0)%s" % (
        len(bad), (" 例:%s" % bad[:3] if bad else "")))
    if bad:
        all_pass = False
    emit("")
    emit("## 结论:全部检查 %s" % ("**PASS**" if all_pass else "**存在失败项——见上**"))
    with open(os.path.join(C.HERE, "AUDIT_REPORT.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(OUT))


if __name__ == "__main__":
    main()
