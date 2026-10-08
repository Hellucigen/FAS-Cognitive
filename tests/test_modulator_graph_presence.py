# test_modulator_graph_presence.py — R2 P4：12 个调制器真的都是图谱公民
#
# 离线：把真实 runtime_graph.json 复制进临时目录后**剪掉调制接线**（见 graph_copy
# 的说明：运行时图是活的，一次真实启动就会把镜像与 P6 的边落盘），全程不写回
# data/runtime_graph.json 与 data/internal_state.json。
# 测的是"存在且形态正确"，不是"接口有这个方法"。
#
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_modulator_graph_presence.py

import copy
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)
for _n in ("faiss", "transformers", "torch", "jieba"):
    logging.getLogger(_n).setLevel(logging.ERROR)

import config as C
from graph_model import KnowledgeGraph, Node
from internal_state import InternalState

FAILURES = []
MS = "modulator_system"
REAL_GRAPH = os.path.join("data", "runtime_graph.json")
# 旧 4 个镜像 id：历史图谱/快照/前端都在引用，改一个就是回归
LEGACY_IDS = {"多巴胺样", "皮质醇样", "血清素样", "催产素样"}
MOD_LABEL = "declarative-semantic"


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def _mirror_ids():
    """12 个镜像节点 id（从规格表算，与 InternalState.mirror_modulator 同一条规则）。"""
    specs = ((C.DEFAULT_CONFIG.get(MS) or {}).get("specs") or {})
    out = set(LEGACY_IDS)
    for name, spec in specs.items():
        out.add((spec or {}).get("mirror") or name + "样")
    return out


def graph_copy():
    """把真实图复制进临时目录（原文件只读不动），返回路径。

    ⚠️ 复制后**先把 P4/P6/P7 的产物剪掉**再交出去：本文件测的是"sync_graph 从无到有
    把 12 个镜像建齐、且不改拓扑"，而运行时图是活的——一次真实启动就会把镜像、
    `调制目标:*`、`事件类型:*` 与调制/影响边**落盘**（2026-09-21 验证装配时就这样发生过）。
    不剪的话，这些断言会在"已经接线"的图上永久为假，而那是 P6/P7 测试
    （test_modulator_subgraph / test_modulation_events）的断言方向，不是这里的。
    """
    d = tempfile.mkdtemp(prefix="fas_p4_graph_")
    if os.path.exists(REAL_GRAPH):
        p = os.path.join(d, "runtime_graph.json")
        shutil.copy(REAL_GRAPH, p)
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        mirrors = _mirror_ids()
        # 旧 4 个镜像**留点**（D 组要测的就是"存量只有 4 个 → sync 长出 8 个"），
        # 新 8 个与整个 调制目标:* / 事件类型:* 族剪掉（后者是 P7 的事件表）
        drop = (mirrors - LEGACY_IDS) | {n["id"] for n in data.get("nodes") or []
                                         if str(n.get("id", "")).startswith(
                                             ("调制目标:", "事件类型:"))}
        data["nodes"] = [n for n in data.get("nodes") or [] if n["id"] not in drop]
        # 边：**凡 touching 镜像节点的边一律剪**（不限关系词）。旧版只剪
        # mod_rels 六个关系词，2026-09-23 被活图打穿：CI 节点对镜像种了
        # "基于"边（非调制词表）漏进测试图，"入边==0"当场为假。本文件
        # 测的是 sync_graph 不改拓扑，交给它的图本就应是未接线态——
        # 白名单式剪枝永远追不上新子系统，改黑名单为全剪。
        data["edges"] = [e for e in data.get("edges") or []
                         if e["src"] not in drop and e["dst"] not in drop
                         and e["src"] not in mirrors and e["dst"] not in mirrors]
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return p, d
    # 干净环境：造一张只有 Self + 旧 4 镜像的骨架图，保证测试可独立跑
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", label="declarative-semantic", graph_space="self"))
    for nid in sorted(LEGACY_IDS):
        kg.add_node(Node(id=nid, weight=0.4, label=MOD_LABEL, graph_space="self",
                         extra_attrs={"type": "modulator", "canon": "internal_state"}))
    p = os.path.join(d, "runtime_graph.json")
    kg.save(p)
    return p, d


def build(kg_path):
    kg = KnowledgeGraph.load(kg_path)
    base = os.path.join(tempfile.gettempdir(),
                        f"fas_p4_state_{os.path.basename(os.path.dirname(kg_path))}")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    st = InternalState(kg=kg, config=copy.deepcopy(C.DEFAULT_CONFIG), data_dir=base)
    return kg, st


def modulator_nodes(kg):
    return {n.id: n for n in kg.nodes.values()
            if (n.extra_attrs or {}).get("type") == "modulator"}


# ═══════ A 全部 12 个都落到图上，形态与既有约定一致 ═══════
def test_A_presence():
    print("\n── A 12 个镜像节点的存在性与形态 ──")
    path, _ = graph_copy()
    kg, st = build(path)
    before = len(kg.nodes)
    r = st.sync_graph()
    mods = modulator_nodes(kg)
    want = set(st.mirror_modulator.values())
    check("sync_graph 报告同步了 12 个调制器",
          r.get("synced", 0) >= 12, str(r))
    check("图上调制器节点 == 12 个（旧 4 + 新 8 齐全）", len(mods) == 12,
          f"{len(mods)}: {sorted(mods)}")
    check("调制器节点 id == 规格 mirror 名单", set(mods) == want,
          str(set(mods) ^ want))
    check("旧 4 个 id 一字未改（历史图谱/前端仍可引用）",
          LEGACY_IDS <= set(mods), str(LEGACY_IDS - set(mods)))
    check("新建的 8 个确实是 8 个（不是重复覆盖）",
          len(set(mods) - LEGACY_IDS) == 8, str(sorted(set(mods) - LEGACY_IDS)))
    check("节点数增量正好 8（没顺手造别的节点）", len(kg.nodes) - before == 8,
          f"{before} → {len(kg.nodes)}")
    bad_label = [i for i, n in mods.items() if n.label != MOD_LABEL]
    bad_space = [i for i, n in mods.items() if n.graph_space != "self"]
    check("全部 label=declarative-semantic（不需改 VALID_LABELS）", not bad_label,
          str(bad_label))
    check("全部 graph_space=self（与既有 4 个一致）", not bad_space, str(bad_space))
    no_cat = [i for i, n in mods.items() if not (n.extra_attrs or {}).get("category")]
    check("每个节点带 category（§5：该字段有真实归属，不是占位）", not no_cat, str(no_cat))
    cats = {n.extra_attrs.get("category") for n in mods.values()}
    check("category 覆盖 6 类且无未知类",
          {"monoamine", "cholinergic", "hormone", "neuropeptide",
           "inhibitory", "excitatory", "circadian"} >= cats and len(cats) >= 6,
          str(sorted(cats)))
    no_desc = [i for i, n in mods.items() if not (n.extra_attrs or {}).get("description")]
    check("每个节点带功能描述（可解释性）", not no_desc, str(no_desc))
    return kg, st


# ═══════ B 属性是活的：level 写的是派生视图，且与读接口一致 ═══════
def test_B_live_attrs():
    print("\n── B 镜像属性跟着状态走 ──")
    path, _ = graph_copy()
    kg, st = build(path)
    st.sync_graph()
    n = kg.nodes["多巴胺样"]
    check("初始镜像 level == modulator_level()",
          abs(float(n.extra_attrs["level"]) - st.modulator_level("dopamine")) < 1e-6,
          f"{n.extra_attrs['level']} vs {st.modulator_level('dopamine')}")
    check("镜像同时带 tonic 与 phasic（可诊断：哪条通道动了）",
          "tonic" in n.extra_attrs and "phasic" in n.extra_attrs,
          str(sorted(n.extra_attrs)))
    st.pulse("dopamine", 0.3, reason="假设：RPE 正向意外", source="reward")
    st.sync_graph()
    check("脉冲后镜像 level 上升（phasic 已折进视图）",
          float(kg.nodes["多巴胺样"].extra_attrs["level"]) > 0.5,
          str(kg.nodes["多巴胺样"].extra_attrs["level"]))
    check("镜像 tonic 不因脉冲改变（phasic 不污染真值）",
          abs(float(kg.nodes["多巴胺样"].extra_attrs["tonic"]) - 0.50) < 1e-6,
          str(kg.nodes["多巴胺样"].extra_attrs["tonic"]))
    st.apply_delta("modulator", "cortisol", 0.2, reason="假设：持续压力", source="stress")
    st.sync_graph()
    check("慢写后皮质醇镜像 level 抬高",
          float(kg.nodes["皮质醇样"].extra_attrs["level"]) > 0.30,
          str(kg.nodes["皮质醇样"].extra_attrs["level"]))
    check("每个调制器在 state() 里能查到自己的图节点 id",
          all(v.get("graph_id") == st.mirror_modulator[k]
              for k, v in st.state()["modulators"].items()),
          json.dumps({k: v.get("graph_id") for k, v in st.state()["modulators"].items()},
                     ensure_ascii=False))
    check("state() 的 level 与节点属性一致（前端读哪边都同一个数）",
          all(abs(float(st.state()["modulators"][k]["level"])
                  - float(kg.nodes[st.mirror_modulator[k]].extra_attrs["level"])) < 5e-4
              for k in st.modulator_names()),
          "")


# ═══════ C 幂等 + 结构零副作用（P4 只建点，不建边）═══════
def test_C_idempotent():
    print("\n── C 反复 sync 幂等，且不悄悄加边 ──")
    path, _ = graph_copy()
    kg, st = build(path)
    st.sync_graph()
    n_nodes, n_edges = len(kg.nodes), len(kg.edges)
    ids0 = {i: id(kg.nodes[i]) for i in modulator_nodes(kg)}
    for _ in range(5):
        st.sync_graph()
    check("重复 sync 不新增节点", len(kg.nodes) == n_nodes,
          f"{n_nodes} → {len(kg.nodes)}")
    check("重复 sync 不新增边（sync_graph 自己不接线；边由 P6 的 bootstrap 建）",
          len(kg.edges) == n_edges, f"{n_edges} → {len(kg.edges)}")
    check("仍是同一批节点对象（in-place 更新，不删了重建）",
          all(kg.nodes[i] is not None and id(kg.nodes[i]) == oid
              for i, oid in ids0.items()), "")
    check("未跑 P6 bootstrap 时调制器节点没有出边（隔离只在未接线阶段成立）",
          all(len(kg.get_out_edges(i)) == 0 for i in modulator_nodes(kg)),
          str({i: len(kg.get_out_edges(i)) for i in modulator_nodes(kg)
               if len(kg.get_out_edges(i))}))
    check("图里没有 调制目标:* 节点（那是 P6 bootstrap 的产物）",
          not [i for i in kg.nodes if i.startswith("调制目标:")], "")
    check("Self 的出边条数不因 P4 改变（不摊薄既有传播强度）",
          len(kg.get_out_edges("Self")) == len(
              KnowledgeGraph.load(path).get_out_edges("Self")), "")
    check("调制器节点也没有入边（Self-[处于] 锚定边是 P6 建的）",
          all(len(kg.get_in_edges(i)) == 0 for i in modulator_nodes(kg)), "")
    # P4↔P6 交互：接好线之后，sync_graph 仍然只是"擦镜子"，不动拓扑
    import modulator_subgraph as MS
    MS.ensure_modulator_subgraph(kg, st, st.config)
    n_edges_wired = len(kg.edges)
    for _ in range(3):
        st.sync_graph()
    check("接线后反复 sync 仍不改拓扑（sync_graph 不删边也不复制边）",
          len(kg.edges) == n_edges_wired, f"{n_edges_wired} → {len(kg.edges)}")
    check("接线后调制器节点确有出边（D-15 由 P6 关闭）",
          all(len(kg.get_out_edges(i)) > 0 for i in modulator_nodes(kg)), "")


# ═══════ D 旧图谱载入路径：不需要迁移、不产生非法 label ═══════
def test_D_legacy_graph():
    print("\n── D 只有 4 个镜像的存量图 ──")
    path, _ = graph_copy()
    kg = KnowledgeGraph.load(path)
    check("存量图（或骨架图）可载入", len(kg.nodes) > 0, str(len(kg.nodes)))
    st = InternalState(kg=kg, config=copy.deepcopy(C.DEFAULT_CONFIG),
                       data_dir=tempfile.mkdtemp(prefix="fas_p4_d_"))
    st.sync_graph()
    from graph_model import VALID_LABELS
    bad = [n.id for n in kg.nodes.values() if n.label not in VALID_LABELS]
    check("sync 后全图无非法 label（VALID_LABELS 未改）", not bad, str(bad[:5]))
    check("VALID_LABELS 里确有 declarative-semantic",
          MOD_LABEL in VALID_LABELS, str(sorted(VALID_LABELS)))
    # 规格缺段的老配置（回退路径）也必须能镜像，用兜底 4 个
    kg2 = KnowledgeGraph()
    kg2.add_node(Node(id="Self", label=MOD_LABEL, graph_space="self"))
    st2 = InternalState(kg=kg2, config={}, data_dir=tempfile.mkdtemp(prefix="fas_p4_d2_"))
    st2.sync_graph()
    mods2 = modulator_nodes(kg2)
    check("无 modulator_system 段时仍镜像出兜底 4 个（老环境不破）", len(mods2) == 4,
          str(sorted(mods2)))
    check("兜底 4 个的 id 与旧版逐字一致", LEGACY_IDS == set(mods2), str(set(mods2)))


# ═══════ E 前端契约：12 个调制器都可在 /state 里渲染 ═══════
def test_E_frontend():
    print("\n── E 前端可读性（滑条数据源）──")
    path, _ = graph_copy()
    kg, st = build(path)
    st.sync_graph()
    mods = st.state()["modulators"]
    check("/state 暴露 12 个调制器（前端滑条自动长到 12）", len(mods) == 12, str(len(mods)))
    missing = [k for k, v in mods.items()
               if any(f not in v for f in ("level", "baseline", "min", "max"))]
    check("每个都有画一根条所需的最小字段", not missing, str(missing))
    noconc = [k for k, v in mods.items() if "conc" not in v]
    check("每个都另给 conc（浓度/响应两条路的调试基础）", not noconc, str(noconc))
    # JSON 可序列化（web 层直接 jsonify；曾因此炸过）
    try:
        json.dumps(st.state(), ensure_ascii=False)
        ok = True
    except Exception as e:      # noqa: BLE001
        ok = False
        print("  dump err:", e)
    check("整个 state() 可 JSON 序列化", ok, "")
    dumped = st.dump_modulator_state(top=99)      # 默认 top=8：12 个必须显式取全
    rows = dumped.get("modulators") or []
    check("dump 的 count 报告 12 个（默认 top=8 只是截断展示）",
          dumped.get("count") == 12 and len(rows) == 12,
          f"count={dumped.get('count')} rows={len(rows)}")
    check("dump 每行都带 mirror 且该节点确实在图上",
          all(r.get("mirror") in kg.nodes for r in rows),
          str([r.get("mirror") for r in rows if r.get("mirror") not in kg.nodes]))


# ═══════ F 中文镜像名的稳定性（命名即引用）═══════
def test_F_naming():
    print("\n── F 命名约定 ──")
    specs = C.DEFAULT_CONFIG[MS]["specs"]
    check("12 个规格都显式写了 mirror 名（不靠 f\"{name}样\" 兜底）",
          all(specs[k].get("mirror") for k in specs),
          str([k for k in specs if not specs[k].get("mirror")]))
    mirrors = [specs[k]["mirror"] for k in specs]
    check("镜像名互不相同（同名会互相覆盖节点）", len(set(mirrors)) == 12, str(mirrors))
    check("英文调制器名用 GABA/谷氨酸这类可读中文（GABA样 保留缩写）",
          "GABA样" in mirrors and "谷氨酸样" in mirrors and "去甲肾上腺素样" in mirrors,
          str(mirrors))
    _, st = build(graph_copy()[0])
    check("运行时名单 == 配置名单（无第二份表）",
          set(st.modulator_names()) == set(specs), "")


if __name__ == "__main__":
    print("R2 P4 闸门：调制器是图谱公民（只读真实图，不落盘）")
    test_A_presence()
    test_B_live_attrs()
    test_C_idempotent()
    test_D_legacy_graph()
    test_E_frontend()
    test_F_naming()
    # 临时目录以 fas_p4_ 前缀命名，跑完即清（不留垃圾，也不碰 data/）
    tmpdirs = [x for x in os.listdir(tempfile.gettempdir())
               if x.startswith(("fas_p4_", "fas_p4_d"))]
    for x in tmpdirs:
        shutil.rmtree(os.path.join(tempfile.gettempdir(), x), ignore_errors=True)
    print("\n" + "=" * 56)
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项:")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print(f"全部通过（临时目录已清 {len(tmpdirs)} 个）")
