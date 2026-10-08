# test_no_llm_on_fact_path.py — B7/§26：事实路径零 LLM 的 grep 断言
# ============================================================================
# 任务书钉死："看到一只鸡"不得触发任何模型调用。事实/增量/结果/简单因果
# 全部必须是确定性代码。本测试不跑行为，**钉结构**：这些模块里不允许出现
# LLM 调用原语；ActionManager._settle 源码单独检查（结算路径含归因入口）。
# 反面教材：curiosity_engine 故意**不在清单里**——它的提问生成是对话侧
# 合法 LLM 消费者，清单只圈"事实路径"。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_no_llm_on_fact_path.py
# ============================================================================

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LLM_MARKERS = ("ChatPromptTemplate", "build_prompt", ".invoke(",
               "chat_llm", "nlp.ask(", "ask_llm(", "complete(")

FACT_FILES = [
    "experience.py",            # 时间轴 + pending-window 归因 + 因果统计
    "minecraft/perception.py",  # 未知建档/物种沉淀（事实翻译层）
    "drive_engine.py",          # 张力→Drive（确定性读数）
    "capability_graph.py",      # 能力索引/经验回流（图查询）
    # P3/§2-8 先验层与配方通道：先验种图/探索缺口/配方回写/桥读数
    # 全部是确定性代码——缺口"值不值得探索"进不了 LLM 也进不了攻略表
    "prior_knowledge.py",
    "minecraft/bridge.py",
]
FACT_FILES = [f for f in FACT_FILES if f != "curiosity_engine.py"]

for rel in FACT_FILES:
    src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
    hits = [m for m in LLM_MARKERS if m in src]
    check(f"{rel} 零 LLM 原语", not hits, str(hits))

# skills/ 全目录（具身执行与回执）
skill_dir = os.path.join(ROOT, "skills")
bad = []
for fn in sorted(os.listdir(skill_dir)):
    if not fn.endswith(".py"):
        continue
    src = open(os.path.join(skill_dir, fn), encoding="utf-8").read()
    hits = [m for m in LLM_MARKERS if m in src]
    if hits:
        bad.append((fn, hits))
check("skills/ 全目录零 LLM 原语（执行/轮询/回执确定性）", not bad, str(bad))

# embodiment 的感知与结算钩子（事实路径函数级检查）
from minecraft.embodiment import MinecraftEmbodiment
from action_system import ActionManager
for cls, meths in ((MinecraftEmbodiment, ("perceive", "on_settled",
                                          "_emit_timeline_events")),
                   (ActionManager, ("_settle", "_emit_result_event",
                                    "_write_action_memory"))):
    for m in meths:
        fn = getattr(cls, m, None)
        if fn is None:
            check(f"{cls.__name__}.{m} 存在（事实路径挂点）", False, "缺方法")
            continue
        src = inspect.getsource(fn)
        hits = [k for k in LLM_MARKERS if k in src]
        check(f"{cls.__name__}.{m} 源码零 LLM", not hits, str(hits))

# "看到一只鸡" 行为的运行时零调用证明：走一遍 perceive+物种沉淀+结算，
# 用计数桩 LLM 断言调用数 0（结构 grep 之外的行为级双保险）。
import config as C
from graph_model import KnowledgeGraph, Node
from diffusion_engine import DiffusionEngine


class BoomNLP:
    calls = 0

    def __getattr__(self, k):
        def _boom(*a, **w):
            raise AssertionError(f"事实路径调用了 LLM 成员 {k}")
        return _boom


import minecraft.bridge as bridge
STATE = {"connected": True, "username": "Haru",
         "position": {"x": 0.0, "y": 64.0, "z": 0.0},
         "health": 20, "food": 20, "heldItem": None,
         "playersNearby": [],
         "nearbyEntities": [{"name": "Chicken", "dist": 5.0,
                             "rel": {"dx": 5.0, "dy": 0.0, "dz": 0.0}}],
         "nearbyBlocks": [], "chat": [], "connectedAt": 1,
         "timeOfDay": 6000, "isRunning": False}
bridge.get_state = lambda: dict(STATE)
bridge.health = lambda: 20
bridge.inventory = lambda: {"ok": True, "items": []}
bridge.recipes = lambda: {"ok": True, "craftable": []}   # P3/§8 配方通道
bridge.call = lambda p, payload=None, timeout=3: {"ok": True}
bridge.stop_goto = lambda: {"ok": True}
bridge.sprint = lambda on=True: {"ok": True}
bridge.move = lambda d, s=1.0: True
bridge.say = lambda t: True
bridge.get_action_result = lambda: {}

ECFG = {"lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 4,
        "theta_threshold": 0.01, "activation_max": 5.0,
        "min_spread_threshold": 0.01, "activation_epsilon": 1e-4,
        "input_similarity_floor": 0.5, "input_default_bonus": 0.5,
        "theta_action": 0.5}
kg = KnowledgeGraph()
kg.add_node(Node(id="Haru"))
eng = DiffusionEngine(kg, dict(ECFG))
eng.name_to_node = dict(kg.nodes)
emb = MinecraftEmbodiment(kg=kg, engine=eng, config=dict(C.DEFAULT_CONFIG))
emb.nlp = BoomNLP()   # 任何属性触碰都炸（行为级证明）
try:
    rep = emb.perceive()
    from minecraft.perception import promote_species
    promote_species(kg, eng, "Chicken", {"dist": 5.0, "rel": {}},
                    config=emb.config)
    emb.on_settled({"action_type": "inspect_entity", "target": "Chicken"},
                   {"success": True, "result": {"entity": "Chicken",
                                                "dist": 5.0}}, True)
    llm_free = True
    detail = ""
except AssertionError as e:
    llm_free, detail = False, str(e)
except Exception as e:
    llm_free, detail = False, f"非 LLM 异常（另行排查）: {e}"
check("「看到一只鸡」全链（感知→建档→沉淀→结算）零 LLM 触碰",
      llm_free, detail)
check("对照：鸡已沉淀为经验物种（零 LLM 也在产出知识）",
      "chicken" in kg.nodes and (kg.nodes["chicken"].extra_attrs or {})
      .get("source") == "experience",
      str(list(kg.nodes)[:0]))
# P3/§5 行为级证据：认识了≠懂了——鸡的用途未知 → 探索缺口自动开启，
# 全程零 LLM（这是先验层接在事实路径上的直接产物）。
import prior_knowledge as pk
check("P3/§5：经验识别即产生探索缺口（零 LLM，缺口在图上）",
      pk.GAP_HUB in kg.nodes and pk.gap_node_id("chicken") in kg.nodes
      and any(str(x.get("gap")) == pk.gap_node_id("chicken")
              for x in pk.open_gaps(kg)),
      str([g.get("gap") for g in pk.open_gaps(kg)]))

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
    sys.exit(1)
print("PASS: 事实路径零 LLM（结构 grep + 函数源码 + 行为级桩三重钉）")
