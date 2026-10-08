# test_episodic_action_recall.py — 记忆重构 §5/§6：对象→情景史→行动链（真扩散）
# ============================================================================
# 离线确定性：桩具身 + 真实 ActionManager 留痕（生产写图路径）+
# 真实 DiffusionEngine（默认 config，零改动）。
# 验证"过去发生的事能改变当前认知"的最小实验：
#   Episode A = 我观察过 cow（行动_* 留痕，事件-[涉及]->cow，双向）
#   Episode B = 再次感知 cow → 扩散从 cow 沿入边回溯点亮"我对它做过什么"，
#               再沿 时间顺序 链前进到下一个动作（前后序列可走）。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_episodic_action_recall.py
# ============================================================================

import logging
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.WARNING)

from graph_model import KnowledgeGraph, Node
from diffusion_engine import DiffusionEngine
from action_system import ActionManager, SRC_AUTONOMY
import config as _C

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class StubEmbodiment:
    name = "stub"

    def __init__(self):
        self.capabilities_set = {"walk_to", "gather_resource", "inspect_area",
                                 "stop"}
        self.next_result = {"success": True, "pending": True, "describe": "…"}
        self.poll_status = {"status": "done", "detail": {}}

    def available(self):
        return True

    def capabilities(self):
        return set(self.capabilities_set)

    def perceive(self):
        return {"connected": True, "health": 20, "food": 20, "players": [],
                "entities": [], "blocks": [], "unknown_entities": [],
                "unknown_blocks": []}

    def execute(self, action):
        return dict(self.next_result)

    def poll_action(self):
        return dict(self.poll_status)

    def cancel(self):
        return {"ok": True}


base = os.path.join(tempfile.gettempdir(), "fas_ep_recall")
shutil.rmtree(base, ignore_errors=True)
os.makedirs(base, exist_ok=True)

kg = KnowledgeGraph()
kg.add_node(Node(id="Self", graph_space="self"))
kg.add_node(Node(id="用户"))
kg.add_node(Node(id="cow", weight=0.5, graph_space="semantic"))
kg.add_node(Node(id="无关孤岛", weight=0.5, graph_space="semantic"))
emb = StubEmbodiment()
am = ActionManager(embodiment=emb, kg=kg, config={}, data_dir=base)
from safety_kernel import SafetyKernel
am.kernel = SafetyKernel(kg=kg, engine=None, config=dict(_C.DEFAULT_CONFIG))

# ── Episode A：两个连续动作都经生产路径留痕 ──
am.propose({"action_type": "inspect_area", "target": "Cow"},
           source=SRC_AUTONOMY, now=100.0)
am.tick(now=101.0)
am.propose({"action_type": "walk_to", "target": "dark_cave"},
           source=SRC_AUTONOMY, now=102.0)
am.tick(now=103.0)
acts = [nid for nid in kg.nodes if nid.startswith("行动_")]
check("两次具身留痕入图", len(acts) == 2, str(acts))

# ── Episode B：再遇 cow → 真扩散把它带进当前认知 ──
eng = DiffusionEngine(kg, dict(_C.DEFAULT_CONFIG))
eng.name_to_node = dict(kg.nodes)
with kg._lock:
    kg.nodes["cow"].activation = 2.0
eng.mark_active(["cow"])
eng.diffuse_from(["cow"], steps=4)


def a(nid):
    n = kg.nodes.get(nid)
    return float(getattr(n, "activation", 0.0) or 0.0) if n else 0.0


check("对象→情景史可达：cow 的再感知点亮『我对它做过什么』（入边回溯）",
      a(acts[0]) > 0.01, f"{acts[0]}={a(acts[0]):.4f}")
check("时间顺序链前进：上一动作把信号传给下一动作（序列可走）",
      a(acts[1]) > 0.005, f"{acts[1]}={a(acts[1]):.4f}")
check("扩散不污染无关节点（只走有意义的连接，§八）",
      a("无关孤岛") == 0.0, f"={a('无关孤岛')}")

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
    sys.exit(1)
print("PASS: 情景经历经图谱激活参与当前认知（真实扩散验收）")
