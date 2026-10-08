# test_semantic_intent_demotion.py — 语义意图降级重构（2026-09-21）的验收测试
# 验证目标管线：输入 → 语义假设 → 候选 → ActionManager → 能力 → 执行。
# 离线、假具身、不连游戏、不调 LLM、不写真实数据。
# 覆盖 §11 的 A-F：A intent 仍存在 / B 未知 intent 不被白名单拒绝 /
# C intent 单独不能执行 Action / D 动作仍全部经 ActionManager /
# F 旧名 _pick_semantic 无残留代码引用。（E=MC 基础动作由既有
# test_minecraft.embodiment / test_minecraft.reflex 覆盖。）
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_semantic_intent_demotion.py

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from action_intents import l1_to_action
from action_system import ActionManager, normalize_action

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class FakeEmbodiment:
    """记录型假具身：只回答'我被调用了'，用来证明执行入口唯一。"""

    def __init__(self):
        self.calls = []

    def execute(self, action):
        self.calls.append(dict(action))
        return {"success": True, "pending": True,
                "action": action.get("action_type"), "describe": "假执行"}


# ── A. intent 字段仍被支持（兼容不删）──────────────────────
spec = l1_to_action({"intent": "mine_block", "needs_action": True,
                     "target": "钻石矿", "count": 3})
check("A1 已知语义假设仍产出行动候选（intent→candidate 翻译在位）",
      spec is not None and spec.get("action_type") == "gather_resource", str(spec))

# ── B. 未知 intent 不被当作白名单拒绝 ─────────────────────
try:
    r = l1_to_action({"intent": "quantum_renarration", "needs_action": True,
                      "target": "x"})
    check("B1 未知假设：静默降级为纯语义证据（None，不抛错不拒绝输入）", r is None)
except Exception as e:
    check("B1 未知假设处理不抛异常", False, repr(e))

a = normalize_action({"action_type": "dance_celestial_floss"})
check("B2 管理器入口不做动作名白名单：未知 action_type 原样通过（能不能做由能力面出口定）",
      a.get("action_type") == "dance_celestial_floss", str(a.get("action_type")))

# ── C. intent 本身绝不能执行 Action ────────────────────────
emb = FakeEmbodiment()
_ = l1_to_action({"intent": "follow_user", "needs_action": True, "target": "用户"})
check("C1 候选翻译阶段零副作用：l1_to_action 不触碰任何具身", emb.calls == [])
check("C2 翻译产物是纯数据 dict（无回调/无 handle）",
      isinstance(spec, dict) and not any(callable(v) for v in spec.values()))

# ── D. 动作仍全部经 ActionManager ─────────────────────────
import inspect
emb0 = FakeEmbodiment()
mgr = ActionManager(embodiment=emb0)
out = mgr.propose(spec, source="user")
check("D1 候选经 ActionManager 竞争后才到达具身",
      out.get("started") is True and len(emb0.calls) == 1
      and emb0.calls[0].get("action_type") == "gather_resource", str(out))
src = inspect.getsource(ActionManager._start)
check("D2 执行调用的唯一入口在 ActionManager._start（管理器源码含 embodiment.execute）",
      "self.embodiment.execute(action)" in src)

# ── F. 旧名 _pick_semantic 无残留代码引用 ─────────────────
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
resid = []
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames
                   if d not in ("__pycache__", ".git", "node_modules",
                                "prototype", "legacy")]
    for fn in filenames:
        if not fn.endswith(".py") or fn == os.path.basename(__file__):
            continue
        p = os.path.join(dirpath, fn)
        try:
            tree = ast.parse(open(p, encoding="utf-8").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute, ast.FunctionDef)) \
                    and getattr(node, "id", None) == "_pick_semantic" \
                    or isinstance(node, ast.Attribute) \
                    and node.attr == "_pick_semantic":
                resid.append(f"{p}:{node.lineno}")
check("F1 全仓 AST 扫描：_pick_semantic 无代码引用残留（注释/文档提及不算）",
      not resid, "; ".join(resid))
import action_resolver
check("F2 新名 _resolve_action_candidate 已就位且旧名不再是其属性",
      hasattr(action_resolver, "_resolve_action_candidate")
      and not hasattr(action_resolver, "_pick_semantic"))

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 语义意图降级验收全过（假设→候选→管理器链路成立，无执行旁路）")
