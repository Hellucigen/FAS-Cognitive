# verify_action_eventframe.py — C21 行动侧事件框架生产落图只读审计（2026-09-28）
# 扫生产图谱快照（data/runtime_graph.json），断言：
#   a. 行动经验:/行动结果: 节点形状（label/space/type/extra_attrs 计数）
#   b. 涉及 槽位边极性（失败 -0.8 / 成功 +0.5 之一）与关系类别
#   c. 事件节点 total_w>0（抑制发行前提，diffusion_engine.py:1023 口径）
#   d. 结果 边恒正且关系已注册（非兜底 关联——C21 词表同步）
#   e. 签名降级无异常（裸 行动经验:{atype} 只为无目标动作，不混入带参签名）
# 只读：不写任何文件。真机跑一段后运行：
#   E:/Miniforge.envs/Fascinator/python.exe -X utf8 scripts/verify_action_eventframe.py

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def main():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "runtime_graph.json")
    if not os.path.exists(path):
        print(f"[SKIP] {path} 不存在（尚未真机跑出事件框架数据，属正常前置状态）")
        return 0
    with open(path, encoding="utf-8") as f:
        g = json.load(f)
    nodes = {n["id"]: n for n in g["nodes"]}
    edges = g["edges"]

    evf = {nid: n for nid, n in nodes.items() if nid.startswith("行动经验:")}
    res = {nid: n for nid, n in nodes.items() if nid.startswith("行动结果:")}
    if not evf and not res:
        print(f"[SKIP] 图谱尚无事件框架节点（行动经验:×0 / 行动结果:×0）——"
              f"enabled=True 前属设计状态（C21 enabled 默认 False）；"
              f"真机跑过失败/成功结算后再审计")
        return 0
    check("事件框架节点存在", bool(evf), f"行动经验:×{len(evf)} / 行动结果:×{len(res)}")
    if not evf:
        print("（图谱尚无事件框架数据：C21 enabled=True 后真机跑过才有样本）")
        return 0 if not res else 1

    bad_shape = []
    for nid, n in evf.items():
        ea = n.get("extra_attrs") or {}
        if n.get("label") != "declarative-episodic" \
                or n.get("graph_space") != "episodic" \
                or ea.get("type") != "action_experience" \
                or ea.get("success_count") is None:
            bad_shape.append(nid)
    check("事件节点形状合规（label/space/type/计数）", not bad_shape,
          f"异常: {bad_shape[:3]}")

    bad_pol = []
    leaf_bad = []
    total_w_bad = []
    for nid in evf:
        out = [e for e in edges if e["src"] == nid]
        pos = sum(max(float(e["weight"]), 0.0) for e in out)
        if pos <= 0:
            total_w_bad.append(nid)
        for e in out:
            if e["relation"] == "涉及":
                w = float(e["weight"])
                if abs(w + 0.8) > 1e-6 and abs(w - 0.5) > 1e-6 \
                        or e["relation_category"] != "cognitive_relation":
                    bad_pol.append((nid, e["dst"], e["relation"], w))
            if e["relation"] == "结果":
                if float(e["weight"]) <= 0 \
                        or e["relation_category"] != "cognitive_relation":
                    leaf_bad.append((nid, e["dst"], float(e["weight"])))
    check("涉及 槽位边极性/类别（-0.8 或 +0.5）", not bad_pol, f"异常: {bad_pol[:3]}")
    check("结果 边恒正且已注册（非兜底 关联）", not leaf_bad,
          f"异常: {leaf_bad[:3]}; 若显示 关联 请检查 relation_ontology 是否登记 结果")
    check("事件节点 total_w>0（抑制可发行）", not total_w_bad,
          f"异常: {total_w_bad[:3]}")

    # 结果叶子的宿主边完整性：每个 行动结果: 节点应恰有一条来自宿主事件节点的 结果 边
    hostless = []
    for rid in res:
        inc = [e for e in edges if e["dst"] == rid and e["relation"] == "结果"]
        if not inc or inc[0]["src"] not in evf:
            hostless.append(rid)
    check("结果叶子宿主完整", not hostless, f"孤儿: {hostless[:3]}")

    # 签名降级：裸 行动经验:{atype}（无括号）只应出现在无目标动作上（如 explore_area）
    naked = [nid for nid in evf if "(" not in nid]
    suspect = [nid for nid in naked
               if has_target_actions(nodes, nid)] if naked else []
    check("无参降级签名无异常", not suspect, f"疑似有目标却降级: {suspect[:3]}")

    print(f"\n{'='*60}\n事件框架落图审计: {len(evf)} 事件节点 / {len(res)} 结果叶子"
          f" | FAILURES={FAILURES}")
    return 1 if FAILURES else 0


def has_target_actions(nodes, nid):
    """裸签名是否曾与带目标签名并存（说明降级决策不一致）。"""
    bare = nid[len("行动经验:"):].strip()
    prefixed = f"行动经验:{bare}("
    return any(k.startswith(prefixed) for k in nodes)


if __name__ == "__main__":
    sys.exit(main())