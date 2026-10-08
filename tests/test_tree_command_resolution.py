# test_tree_command_resolution.py — "砍个树吧" 命令落地回归（2026-09-24 真机事故）
# ============================================================================
# 真机时间轴（端口 56135）：用户说"砍个树吧"→ NLP 给出 target="tree" →
# 技能白名单没有这个名 → requires_user_permission 拒绝 → 她回话"你没让我
# 动手我就先不动它"（明明刚被命令）。"我让你砍树 求你了"→ target="wood" →
# 白名单有（旧方块名）但 find_blocks unknown_block → 16 格没找到。
# 修复三层各钉一条：名称组展开（tree/wood→八族原木）、描述性说法归一
# （一些泥土→dirt）、用户命令授权语义（kernel source=user 即授权）。
# 离线：假桥，零 LLM。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_tree_command_resolution.py
# ============================================================================

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

import config as C
from graph_model import KnowledgeGraph, Node
import mc_knowledge as mck
from safety_kernel import SafetyKernel
from skills.observation import resolve_variants, normalize_resource
from skills.gathering import GatherResource

fail = []


def check(n, c, d=""):
    print(f"[{'PASS' if c else 'FAIL'}] {n}" + (f" | {d}" if d and not c else ""))
    if not c:
        fail.append(n)


# ── 1. 名称组展开：tree/wood 不是某个方块，是原木族的口语名 ──
label, variants = resolve_variants("tree")
check("1 『tree』展开为原木族（含本世界的 acacia_log）",
      "acacia_log" in variants and "oak_log" in variants, str(variants))
label2, v2 = resolve_variants("wood")
check("1b 『wood』同样展开（真机第二句死在这）", "acacia_log" in v2)
label3, v3 = resolve_variants("泥土")
check("1c 具体名不展开（单候选，行为不变）", v3 == ("dirt",), str(v3))

# ── 2. 描述性说法词表归一（昨天死过一条"一些泥土"）──
check("2 『一些泥土』归一到 dirt（最长命中表键）",
      normalize_resource("一些泥土") == "dirt")
check("2b 精确别名不受影响（橡木仍特指 oak_log）",
      normalize_resource("橡木") == "oak_log"
      and normalize_resource("铁矿") == "iron_ore")

# ── 3. kernel：用户命令即授权（K2 语义），非用户来源不变 ──
kg = KnowledgeGraph()
kg.add_node(Node(id="Haru", weight=0.5, label="self", graph_space="self"))
cfg = dict(C.DEFAULT_CONFIG)
mck.init_protection_node(cfg)
mck.ensure_mc_world(kg, cfg)
k = SafetyKernel(kg=kg, engine=None, config=cfg)
act = {"action_type": "gather_resource", "target": "tree",
       "params": {"resource": "tree"}, "priority": 0.5}
ok, why, granted = k.guard(dict(act), source="user", now=8000.0)
check("3 用户命令 gather(tree) → 放行且 granted=True", ok and granted, why)
ok2, why2, g2 = k.guard({"action_type": "gather_resource", "target": "acacia_leaves",
                         "params": {"resource": "acacia_leaves"},
                         "priority": 0.5}, source="autonomy", now=8002.0)
check("3b 非用户来源不因此获得旁路（granted=False，白名单自己判断）",
      ok2 and not g2)

# ── 4. 技能端到端（假桥：世界只有 acacia，tree 应选中它并走向/开挖）──
class _Bridge:
    def call(self, path, payload=None, timeout=None):
        if path == "/find_blocks":
            b = (payload or {}).get("block")
            if b == "acacia_log":
                return {"ok": True, "positions": [{"x": 8, "y": 64, "z": 0}]}
            return {"ok": False, "reason": "unknown_block"}
        return {"ok": True}

    def goto_coords(self, x, y, z):
        return {"ok": True}

    def stop_goto(self):
        return {"ok": True}

    def equip(self, item):
        return {"ok": True}


class _Ctx:
    def __init__(self):
        self.bridge = _Bridge()
        self.session = {}

    def connected(self):
        return True

    def position(self):
        return {"x": 0.0, "y": 64.0, "z": 0.0}

    def inventory(self):
        return []


sk = GatherResource()
ctx = _Ctx()
r = sk.start(ctx, {"resource": "tree"})
s = ctx.session
check("4 gather(tree) 不再被白名单拒（用户命令/组名成员安全都放行）",
      r.get("status") in ("pending", "running") or r.get("ok") is not False,
      str(r))
check("4b 具体化到本世界真名 acacia_log 并锁定目标位置",
      s.get("resource") == "acacia_log" and (s.get("target_pos") or {}).get("x") == 8,
      str({k: s.get(k) for k in ("resource", "target_pos", "phase")}))
check("4c 距离 8 格 → 走最近者（phase=approach），不空转",
      s.get("phase") == "approach", str(s.get("phase")))

# 描述性口语目标同样落地："去砍点一些原木" 之类兜底
ctx2 = _Ctx()
r2 = GatherResource().start(ctx2, {"target": "一些原木"})
check("4d 『一些原木』→ 组展开（描述性说法不但不拒，还能选到树）",
      ctx2.session.get("resource") == "acacia_log", str(r2))

print("\n" + "=" * 60)
if fail:
    print(f"FAIL: {len(fail)} 项未通过: {fail}")
    sys.exit(1)
print("PASS: 砍树命令解析与授权语义回归全过")
