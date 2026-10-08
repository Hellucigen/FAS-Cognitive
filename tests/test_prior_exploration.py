# test_prior_exploration.py — P3/§2-5/§8 §16：先验种图 + 扩散可达 + 探索缺口生命周期
# ============================================================================
# 离线确定性：临时 KG + 真实 DiffusionEngine（只验"桥能走"——扩散代码本身
# 零改动）+ 真实 DriveEvaluator（只验 exploration_gap 张力真从图上缺口升起）。
# 不连 Minecraft、不碰 LLM。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_prior_exploration.py
# ============================================================================

import logging
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.WARNING)

from graph_model import KnowledgeGraph, Node, Edge
from diffusion_engine import DiffusionEngine
import config as C
import mc_knowledge as mck
import prior_knowledge as pk

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class Eng:
    """前沿证人：记录激活直写伴随物（不变量：直写必须 mark_active）。"""

    def __init__(self):
        self.marked = []
        self.sources = []

    def mark_active(self, ids):
        self.marked.append([str(i) for i in (ids or [])])

    def register_activation_source(self, ids, kind="external_input"):
        self.sources.append(([str(i) for i in (ids or [])], kind))


ECFG = {"lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 4,
        "theta_threshold": 0.01, "activation_max": 5.0,
        "min_spread_threshold": 0.01, "activation_epsilon": 1e-4,
        "input_similarity_floor": 0.5, "input_default_bonus": 0.5,
        "theta_action": 0.5}


def fresh():
    kg = KnowledgeGraph()
    cfg = dict(C.DEFAULT_CONFIG)
    mck.init_protection_node(cfg)
    mck.ensure_mc_world(kg, cfg)
    pk.ensure_prior(kg, cfg)
    return kg, cfg


def act(kg, nid):
    n = kg.nodes.get(nid)
    return float(getattr(n, "activation", 0.0) or 0.0) if n else 0.0


# ═══ 1. 先验层种图（§2：节点+关系+可传播激活，非 prompt 清单）═══
kg, cfg = fresh()
need = ["物体", "资源", "工具", "制作", "缺口", "探索", "未知", "尝试",
        "结果", "成功", "失败", "经验", "学习", "可能性", "危险", "生存"]
check("先验通用概念节点入图（16 项抽查）",
      all(n in kg.nodes for n in need),
      str([n for n in need if n not in kg.nodes]))
check("概念带 prior_seed 出处（先天/习得可区分）",
      all((kg.nodes[n].extra_attrs or {}).get("source") == "prior_seed"
          for n in need))
check("认知环骨架边存在（未知→指向→缺口→驱动→探索；尝试→产生→结果）",
      kg.get_edge("未知", "缺口", "指向") is not None
      and kg.get_edge("缺口", "探索", "驱动") is not None
      and kg.get_edge("尝试", "结果", "产生") is not None)
check("聚合 hub 入图（探索缺口/可制作物品）",
      pk.GAP_HUB in kg.nodes and pk.CRAFT_HUB in kg.nodes)
check("mc 分类→先验桥（可采资源-属于->资源）：专门知识接上通用知识",
      kg.get_edge("可采资源", "资源", "属于") is not None)
n0, m0 = len(kg.nodes), len(kg.edges)
pk.ensure_prior(kg, cfg)
pk.ensure_prior(kg, cfg)
check("重复播种幂等（零重复节点/边）",
      len(kg.nodes) == n0 and len(kg.edges) == m0,
      f"{n0}→{len(kg.nodes)}, {m0}→{len(kg.edges)}")

# ═══ 2. 扩散可达性：桥能走（§4——不是砍树脚本，是通路存在）═══
eng = DiffusionEngine(kg, dict(ECFG))
eng.name_to_node = dict(kg.nodes)
with kg._lock:
    kg.nodes["chest"].activation = 2.0
eng.mark_active(["chest"])
eng.diffuse_from(["chest"], steps=3)
check("扩散沿 物种→分类→先验 走通（chest→容器 链路点亮）",
      act(kg, "容器") > 0.01, f"容器={act(kg, '容器'):.3f}")
with kg._lock:
    kg.nodes["未知"].activation = 2.5
eng.mark_active(["未知"])
eng.diffuse_from(["未知"], steps=2)
check("缺口环走通（未知→缺口→探索 被扩散点亮）",
      act(kg, "缺口") > 0.01 and act(kg, "探索") > 0.01,
      f"缺口={act(kg, '缺口'):.3f} 探索={act(kg, '探索'):.3f}")

# ═══ 3. 探索缺口：开/亮/有界/关（§5）═══
eng2 = Eng()
kg2, cfg2 = fresh()
kg2.add_node(Node(id="glowstone_dust", weight=0.4, graph_space="semantic",
                  extra_attrs={"type": "mc_species", "source": "experience"}))
opened = pk.consider_recognition(kg2, eng2, "Glowstone_Dust", cfg2)
check("经验识别已知对象∧用途未知 → 开缺口（大小写归一）",
      opened and any(str(x.get("gap")) == pk.gap_node_id("glowstone_dust")
                     for x in pk.open_gaps(kg2)))
gid = pk.gap_node_id("glowstone_dust")
check("缺口节点带激活并指向对象",
      gid in kg2.nodes and act(kg2, gid) > 0
      and kg2.get_edge(gid, "glowstone_dust", "指向") is not None)
check("激活直写伴随 mark_active（活跃前沿不变量）",
      any(pk.GAP_HUB in m for m in eng2.marked), str(eng2.marked))
again = pk.consider_recognition(kg2, eng2, "glowstone_dust", cfg2)
check("重复识别不叠账（无无限重复开）",
      not again and len(pk.open_gaps(kg2)) == 1)
hub_a0 = act(kg2, pk.GAP_HUB)
pk.close_gap(kg2, eng2, "glowstone_dust", "used:craft_item", cfg2)
check("真实用途 → 关闭 + hub 激活回落 + 留退役痕",
      len(pk.open_gaps(kg2)) == 0 and act(kg2, pk.GAP_HUB) < hub_a0
      and (kg2.nodes[gid].extra_attrs or {}).get("closed") is not None
      and (kg2.nodes[gid].extra_attrs or {}).get("retired") == "used:craft_item")
kg2.add_node(Node(id="物品:coal", weight=0.4, graph_space="episodic"))
kg2.add_node(Node(id="配方:torch", weight=0.4, graph_space="semantic"))
kg2.add_edge(Edge(src="配方:torch", dst="物品:coal", relation="需要",
                  weight=0.6))
check("配方引用=用途知识（has_known_use 认 配方: 入边）",
      pk.has_known_use(kg2, "coal")
      and not pk.consider_recognition(kg2, eng2, "coal", cfg2))
for i in range(40):
    pk.open_gap(kg2, eng2, f"mystery_{i}", "unknown_use", cfg2)
check("缺口有界（超 max_gaps 最旧退役，不无限堆积）",
      len(pk.open_gaps(kg2)) <= cfg2["prior"]["max_gaps"],
      str(len(pk.open_gaps(kg2))))
check("hub 激活封顶（缺口不自举成饱和源）",
      act(kg2, pk.GAP_HUB) <= 2.5 + 1e-9, str(act(kg2, pk.GAP_HUB)))

# ═══ 4. 配方线索上图（§8）：环境事实→图→缺口联动 ═══
eng3 = Eng()
kg3, cfg3 = fresh()
pk.open_gap(kg3, eng3, "stick", "unknown_use", cfg3)
pk.open_gap(kg3, eng3, "oak_planks", "unknown_use", cfg3)
rec = [{"result": "stick", "ingredients": {"oak_planks": 2},
        "needs_table": False, "yield": 4},
       {"result": "torch", "ingredients": {"coal": 1, "stick": 1},
        "needs_table": False, "yield": 4},
       "not-a-dict", {"nope": 1}]
res = pk.note_craftable(kg3, eng3, rec, cfg3)
hub = kg3.nodes[pk.CRAFT_HUB]
check("配方 hints 上 hub（脏数据静默过滤；_bind_craft 的来源）",
      res.get("hints") == 2
      and len((hub.extra_attrs or {}).get("hints") or []) == 2
      and act(kg3, pk.CRAFT_HUB) > 0.05, str(res))
check("配方: 节点与 需要/产生 边上图",
      kg3.get_edge("配方:stick", "物品:oak_planks", "需要") is not None
      and kg3.get_edge("配方:stick", "物品:stick", "产生") is not None
      and "配方:stick" in kg3.nodes)
gaps_now = {str(x.get("gap")) for x in pk.open_gaps(kg3)}
check("配方提及（产物与材料）自动关缺口：线索=用途知识",
      pk.gap_node_id("stick") not in gaps_now
      and pk.gap_node_id("oak_planks") not in gaps_now, str(gaps_now))
n_e = len(kg3.edges)
pk.note_craftable(kg3, eng3, rec, cfg3)
check("配方回写幂等", len(kg3.edges) == n_e, f"{n_e}→{len(kg3.edges)}")
pk.note_craftable(kg3, eng3, [], cfg3)
check("材料耗尽→hub 激活归零（CRAFT 自然熄灭，无 if 撤权）",
      act(kg3, pk.CRAFT_HUB) == 0.0
      and (kg3.nodes[pk.CRAFT_HUB].extra_attrs or {}).get("hints") == [])
check("engine=None 安全（离线装配形态）",
      pk.open_gap(kg3, None, "amethyst_shard", "unknown_use", cfg3) is True
      and pk.close_gap(kg3, None, "amethyst_shard", "used:x", cfg3) is True)

# ═══ 5. 缺口→张力→驱动：真实反馈前它持续，反馈后它消退（§5）═══
from drive_engine import DriveEvaluator
kg4, cfg4 = fresh()
de = DriveEvaluator(kg4, cfg4)
r0 = de.evaluate(force=True)
t0 = float((r0.get("tensions") or {}).get("exploration_gap", 0.0) or 0.0)
kg4.add_node(Node(id="deepslate_gold", weight=0.4, graph_space="semantic",
                  extra_attrs={"type": "mc_species", "source": "experience"}))
pk.open_gap(kg4, None, "deepslate_gold", "unknown_use", cfg4)
r1 = de.evaluate(force=True)
t1 = float((r1.get("tensions") or {}).get("exploration_gap", 0.0) or 0.0)
check("图上缺口点亮 exploration_gap 张力（0 → >0）",
      t0 == 0.0 and t1 > 0.0, f"{t0} → {t1}")
cur1 = next((d for d in r1.get("drives", []) if d.get("drive") == "curiosity"), {})
check("缺口对 CuriosityDrive 有正贡献（参与评分动力学的驱动端）",
      float((cur1.get("components") or {}).get("exploration_gap", 0.0) or 0.0) > 0.0,
      str(cur1.get("components")))
pk.close_gap(kg4, None, "deepslate_gold", "use_known", cfg4)
r2 = de.evaluate(force=True)
t2 = float((r2.get("tensions") or {}).get("exploration_gap", 0.0) or 0.0)
check("用途已知 → 张力回落（t1 > t2：知道了就不再痒）",
      t2 < t1, f"{t1} → {t2}")

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
    sys.exit(1)
print("PASS: 先验层/扩散可达/探索缺口/配方线索（确定性全链）")
