# test_modulator_relations.py — R2 P5：调制关系词已注册且是闭合小词表
#
# 闸门（计划表 P5）：审计器无非规范关系；normalize_relation 往返测试。
# 这里不建边（建边是 P6），只把"词"这一层钉住：词表闭合、类别正确、
# 方向符合语义（交互对称、调制/增强/抑制有向），并且**注册没有改动任何存量归一**。
#
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_modulator_relations.py

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

import config as C
import graph_schema as gs

FAILURES = []

# 调制子系统允许的全部关系词（禁令 2：闭合的只有词表，拓扑与强度在图上）
MOD_RELATIONS = ["调制", "增强", "抑制", "交互"]
SURFACES = {                       # 表面形式 → 期望规范词
    "调控": "调制", "调节": "调制", "modulates": "调制", "调制作用": "调制",
    "易化": "增强", "上调": "增强", "potentiates": "增强", "enhances": "增强",
    "inhibits": "抑制", "抑制作用": "抑制",
    "相互作用": "交互", "协同": "交互", "interacts_with": "交互",
}
# 注册前后都必须保持原样的存量归一（防止"为新功能扰动旧语义"）
LEGACY_NORMALIZATION = {
    "处于": "处于", "位于": "位于", "在": "位于", "强化": "导致", "关注": "注意",
    "关于": "关于", "影响": "影响", "激活": "激活", "产生": "产生", "关联": "关联",
    "随手写的自由关系": "关联",            # 兜底路径
}


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ═══════ A 注册与类别 ═══════
def test_A_registration():
    print("\n── A 词已进本体 ──")
    onto = C.DEFAULT_CONFIG["relation_ontology"]
    missing = [r for r in MOD_RELATIONS + ["影响"] if r not in onto]
    check("调制/增强/抑制/交互/影响 全部已注册", not missing, str(missing))
    wrong = {r: onto.get(r) for r in MOD_RELATIONS if onto.get(r) != "causal_relation"}
    check("四个调制词都归 causal_relation（有传播语义，不是纯关联）", not wrong, str(wrong))
    canon = {r for r in MOD_RELATIONS if gs.is_canonical_relation(r)}
    check("graph_schema 认得这四个词（is_canonical_relation）",
          canon == set(MOD_RELATIONS), str(canon))
    check("本体里没有第二个'激素/神经'专属新造词（词表闭合）",
          not [k for k in onto if any(x in k for x in
                 ("激素", "神经调", "多巴胺", "皮质醇"))],
          str([k for k in onto if "激素" in k or "多巴胺" in k]))


# ═══════ B 归一往返（幂等 + 表面形式收敛）═══════
def test_B_roundtrip():
    print("\n── B normalize_relation 往返 ──")
    for r in MOD_RELATIONS:
        check(f"规范词原样返回且幂等：{r}",
              gs.normalize_relation(r) == r
              and gs.normalize_relation(gs.normalize_relation(r)) == r,
              gs.normalize_relation(r))
    bad = {s: gs.normalize_relation(s) for s, want in SURFACES.items()
           if gs.normalize_relation(s) != want}
    check(f"{len(SURFACES)} 个表面形式都收敛到期望规范词", not bad, str(bad))
    notid = {s: gs.normalize_relation(gs.normalize_relation(s))
             for s in SURFACES if gs.normalize_relation(
                 gs.normalize_relation(s)) != gs.normalize_relation(s)}
    check("两次归一 == 一次归一（表面形式不会二次漂移）", not notid, str(notid))
    # 大小写/空白容错（LLM 输出常态）
    check("空白与大小写被吃掉",
          gs.normalize_relation("  Modulates ") == "调制"
          and gs.normalize_relation("ENHANCES") == "增强",
          f"{gs.normalize_relation('  Modulates ')} / {gs.normalize_relation('ENHANCES')}")
    check("未收录的自由词仍兜底为 关联（没被误注册）",
          gs.normalize_relation("激素水平影响表现") == "关联",
          gs.normalize_relation("激素水平影响表现"))


# ═══════ C 方向语义 ═══════
def test_C_direction():
    print("\n── C 方向 ──")
    check("交互 = 双向（调制器间耦合是对称事实）",
          gs.direction_for("交互") == "bidirectional", str(gs.direction_for("交互")))
    forwards = {r: gs.direction_for(r) for r in ("调制", "增强", "抑制")
                if gs.direction_for(r) != "forward"}
    check("调制/增强/抑制 = 单向（反向不构成调制）", not forwards, str(forwards))
    check("闭合词表里没有'未知方向'的关系（direction_for 全覆盖）",
          all(gs.direction_for(r) in ("forward", "bidirectional", "backward")
              for r in MOD_RELATIONS), "")


# ═══════ D 存量语义未被扰动（注册是加法，不是改写）═══════
def test_D_no_disturbance():
    print("\n── D 存量归一不变 ──")
    drift = {k: (v, gs.normalize_relation(k)) for k, v in LEGACY_NORMALIZATION.items()
             if gs.normalize_relation(k) != v}
    check(f"{len(LEGACY_NORMALIZATION)} 条存量归一逐字不变", not drift, str(drift))
    # 同义表里所有值都必须是规范词，否则构建时被丢弃（graph_schema 的防御）
    onto = set(C.DEFAULT_CONFIG["relation_ontology"])
    syn = C.DEFAULT_CONFIG["relation_synonyms"]
    dead = {k: v for k, v in syn.items() if v not in onto}
    check("relation_synonyms 没有指向未注册词的死映射", not dead, str(list(dead.items())[:5]))
    shadowed = [k for k in syn if k in onto]
    check("新注册的词没有被同义表反向遮蔽（如 调制←调控 方向正确）",
          not [k for k in ("调制", "增强", "交互") if k in syn],
          str([k for k in ("调制", "增强", "交互") if k in syn]))
    print("   （既有事实，供参考）被本体遮蔽的历史同义键数:", len(shadowed))
    # P6 的锚定边要用 处于；同义表里有一条历史映射 处于→位于。
    # 现在 处于 是规范词，所以遮蔽成立、不会被静默改写成空间关系；哪天有人把它
    # 从本体里删掉，Self-[处于]->调制器 会一夜之间变成 Self-[位于]->调制器。钉住它。
    check("处于 仍是规范词（否则 P6 锚定边会被同义表改写成 位于）",
          gs.normalize_relation("处于") == "处于" and "处于" in onto,
          gs.normalize_relation("处于"))


# ═══════ E 写边路径：调制关系能真的落到图上并通过审计口径 ═══════
def test_E_edges_are_legal():
    print("\n── E 边合法性（不建持久图）──")
    from graph_model import KnowledgeGraph, Node, Edge
    kg = KnowledgeGraph()
    for nid in ("多巴胺样", "调制目标:探索速率", "GABA样", "皮质醇样"):
        kg.add_node(Node(id=nid, label="declarative-semantic", graph_space="self",
                         extra_attrs={"type": "modulator"}))
    triples = [("多巴胺样", "调制", "调制目标:探索速率", 0.80),
               ("多巴胺样", "增强", "调制目标:探索速率", 0.20),
               ("GABA样", "抑制", "调制目标:扩散增益", -0.30),
               ("皮质醇样", "交互", "多巴胺样", -0.15)]
    kg.add_edge(Edge(src="GABA样", dst="调制目标:扩散增益", relation="抑制", weight=-0.30))
    ok = 0
    for s, r, d, w in triples:
        if not d.startswith("调制目标:") or kg.nodes.get(d) is None:
            if kg.nodes.get(d) is None and d not in kg.nodes:
                kg.add_node(Node(id=d, label="infrastructure", graph_space="cognitive"))
        if kg.add_edge(Edge(src=s, dst=d, relation=r, weight=w)):
            ok += 1
    check("四类调制边都能写入", ok == len(triples), f"{ok}/{len(triples)}")
    rels = {e.relation for e in kg.edges}
    check("落盘关系词全在本体内（审计'非规范关系'必为 0）",
          all(gs.is_canonical_relation(r) for r in rels), str(rels))
    check("归一后关系词不变（bootstrap 写 调制，读出来还是 调制）",
          all(gs.normalize_relation(r) == r for r in rels), str(rels))
    # 符号约定：增强/抑制的符号与权重一致，调制可带符号（P6 投影器的判据）
    sign_bad = [(e.relation, e.weight) for e in kg.edges
                if (e.relation == "增强" and e.weight <= 0)
                or (e.relation == "抑制" and e.weight >= 0)]
    check("增强为正、抑制为负（符号写在关系上的约定自洽）", not sign_bad, str(sign_bad))


if __name__ == "__main__":
    print("R2 P5 闸门：调制关系词的注册与闭合")
    test_A_registration()
    test_B_roundtrip()
    test_C_direction()
    test_D_no_disturbance()
    test_E_edges_are_legal()
    print("\n" + "=" * 56)
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项:")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("全部通过")
