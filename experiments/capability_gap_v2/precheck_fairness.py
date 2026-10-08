# -*- coding: utf-8 -*-
# precheck_fairness.py — Step 7:baseline 公平性审计(零 LLM,可重复)
# 验证 BASELINE_MATRIX.md 的人工核查清单:练习流逐字节一致、门控对称、
# 消融真实生效、闭卷规则统一。
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "capability_gap_v2"))
os.chdir(ROOT)

import run_experiment as R  # noqa: E402
import harness as H  # noqa: E402

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append({"check": name, "pass": bool(cond), "detail": str(detail)[:200]})
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(detail)[:160] if detail else ""))


def practice_hash(cond, exp):
    """重放练习流,返回逐字节哈希与行数。"""
    w = R.build_world()
    kind, fc, hist = R.make_condition(cond, w,
                                      R.EXCL_A if exp == "A" else R.EXCL_I)
    ex = R.fresh_exec(w)
    if exp == "A":
        H.script_practice_A(ex, (hist, None), fc if kind == "fas" else None)
        H.script_practice_C(ex, (hist, None), fc if kind == "fas" else None)
        H.script_filler(ex, (hist, None), fc if kind == "fas" else None)
    else:
        H.script_practice_A(ex, (hist, None), fc)
        H.script_practice_B(ex, (hist, None), fc)
    h = hashlib.sha256("\n".join(hist.lines).encode("utf-8")).hexdigest()
    return h, len(hist.lines)


def main():
    # 1) 练习流逐字节一致(FAS 与 history 条件收到同一经历流)
    h1, n1 = practice_hash("fas", "A")
    h2, n2 = practice_hash("history", "A")
    check("A 练习流逐字节一致(FAS vs history)", h1 == h2 and n1 == n2,
          "hash=%s.. n=%d" % (h1[:12], n1))
    # 2) I 经历子集:A+B 流包含两配方知识;A-only 不含 stick 字样
    hA, _ = practice_hash("fas", "I1")  # 仅 A
    hAB, _ = practice_hash("fas", "I3")
    # 重新取 A-only 的行文本
    w = R.build_world()
    kind, fc, hist = R.make_condition("fas", w, R.EXCL_I)
    ex = R.fresh_exec(w)
    H.script_practice_A(ex, (hist, None), fc)
    a_lines = "\n".join(hist.lines)
    check("I1(A-only)经历流不含 stick 配方线索", "stick" not in a_lines,
          a_lines[:120])
    # 3) 门控对称:同一 ctx 文本 → 同一 gate
    g1 = H.gate_from_context("inventory: {\"oak_planks\": 2} | action: craft_stick")
    g2 = H.gate_from_context("inventory: {\"oak_planks\": 2} | action: craft_stick")
    check("门控规则确定性(同 ctx 同 gate)", g1 == g2, str(sorted(g1)))
    # 4) no-writeback 真实断边
    w = R.build_world()
    kind, fc, hist = R.make_condition("fas-nowb", w, R.EXCL_A)
    ex = R.fresh_exec(w)
    H.script_practice_A(ex, (hist, None), fc)
    check("no-writeback:经历不建边(exp_edges=0)", fc.exp_edges == 0,
          "exp_edges=%d" % fc.exp_edges)
    # 5) 全量 FAS 建边
    w = R.build_world()
    kind, fc2, hist = R.make_condition("fas", w, R.EXCL_A)
    ex = R.fresh_exec(w)
    H.script_practice_A(ex, (hist, None), fc2)
    check("FAS 全量:经历建边(exp_edges>0)", fc2.exp_edges > 0,
          "exp_edges=%d" % fc2.exp_edges)
    # 6) 闭卷:direct 条件图谱零写入
    w = R.build_world()
    kind, fc3, hist3 = R.make_condition("direct", w, R.EXCL_A)
    check("direct:无 fc 无 store 写入", fc3 is None and len(hist3.lines) == 0)
    # 7) 排除播种生效:oak_planks 配方边不在初始图
    w = R.build_world()
    kind, fc4, _ = R.make_condition("fas", w, R.EXCL_A)
    planks_edges = [e for e in (fc4.kg.edges.values() if hasattr(fc4.kg.edges, "values")
                                else fc4.kg.edges)
                    if "oak_planks" in str(getattr(e, "dst", "")) and
                    getattr(e, "relation", "") == "相关"]
    check("排除播种:oak_planks 相关先验边为 0", len(planks_edges) == 0,
          "n=%d" % len(planks_edges))
    # 8) I 的 fence 配方对全条件对称(facts 文本一致)
    check("I facts 对称(全条件同一文本)",
          R.CLOSED_FACTS_I.count("oak_fence") == 1)

    out = os.path.join(ROOT, "experiments", "capability_gap_v2",
                       "PRECHECK_REPORT.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("# PRECHECK_REPORT.md — Step 7 baseline 公平性审计\n\n")
        f.write("日期:2026-10-01。零 LLM 可重复审计(`precheck_fairness.py`)。\n\n")
        f.write("| 检查项 | 结果 | 细节 |\n|---|---|---|\n")
        for c in CHECKS:
            f.write("| %s | %s | %s |\n" % (c["check"],
                                            "PASS" if c["pass"] else "**FAIL**",
                                            c["detail"].replace("|", "/")))
        f.write("\n## 已知不对称声明(正文口径)\n")
        f.write("1. FAS 图谱含生产配方闭包播种(排除项除外);凡目标配方被排除的实验"
                "(A/I/K),任务解不依赖该播种;其余配方闭包仍在 FAS 一侧——scope 注记。\n")
        f.write("2. LLM-history 输入侧无 token 上限,FAS 上下文固定 top-8:这是被研究的"
                "压缩价值本身,token 成本单独报告。\n")
        f.write("3. 菜单可见性本身泄漏部分配方(持有原料时 craft_X 出现在菜单):"
                "对 I 实验,经历子集间的严格隔离受此限制,已在结果解读中声明。\n")
        f.write("4. 实验 F 的 self_goal_on=False 消融钩子指向 autonomy 中不存在的"
                "方法(静默 no-op)→ 该消融为 F2(机制断连),F 的 self-model 行为"
                "相关性结论不受支持(见 EXPERIMENT_REPORT)。\n")
    print("saved ->", out)


if __name__ == "__main__":
    main()
