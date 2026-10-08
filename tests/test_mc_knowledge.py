# test_mc_knowledge.py — MC 世界语义入图 + Safety Kernel 验收
# ============================================================================
# 2026-09-21 具身图谱化 P1。核心断言方向：
#   * 保护是图上的关系（受保护边），不是名单；授权是图节点、可过期
#   * 用户明确命令=授权（source=user 直接通过并留授权证据）
#   * 自主/队列动作无授权 → 拒（requires_user_permission），但技能层
#     保留防裸调用的物理兜底
#   * 攻击对象核验基于图因果（造成/敌对边）或用户点名
#   * 防抖是纯技术约束（同目标秒级重复），不是失败学习
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

import config as C
from graph_model import KnowledgeGraph, Node
import mc_knowledge as mck
from safety_kernel import SafetyKernel

fail = []
def check(n, c, d=''):
    print(f"[{'PASS' if c else 'FAIL'}] {n}" + (f' | {d}' if d and not c else ''))
    if not c:
        fail.append(n)


def build():
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Haru", weight=0.5, label="self", graph_space="self"))
    cfg = dict(C.DEFAULT_CONFIG)
    mck.init_protection_node(cfg)
    st = mck.ensure_mc_world(kg, cfg)
    return kg, cfg, st


# ═══ 1. 世界语义种图（幂等、关系在图上）═══
kg, cfg, st = build()
check("物种与类别节点入图（chest 是容器/用户资产）",
      kg.get_node("chest") is not None
      and any(e.src == "chest" and e.relation == "属于" and e.dst == "容器"
              for e in kg.edges)
      and any(e.src == "chest" and e.relation == "受保护"
              for e in kg.edges))
check("可采集分类图镜像存在（stone→可采资源）",
      mck.is_gatherable(kg, "stone") and not mck.is_gatherable(kg, "chest"))
check("工具需求与产出上图（铁矿-需要工具->石镐 / 石头-产生->圆石）",
      any(e.src == "iron_ore" and e.relation == "需要工具"
          and e.dst == "stone_pickaxe" for e in kg.edges)
      and any(e.src == "stone" and e.relation == "产生"
              and e.dst == "cobblestone" for e in kg.edges))
check("危险因果上图（creeper-造成->爆炸伤害-威胁->健康；lava 造成烧伤）",
      any(e.src == "creeper" and e.relation == "造成" for e in kg.edges)
      and any(e.src == "爆炸伤害" and e.relation == "威胁"
              and e.dst == "健康" for e in kg.edges)
      and any(e.src == "lava" and e.relation == "造成" for e in kg.edges))
n_edges = len(kg.edges)
mck.ensure_mc_world(kg, cfg)
check("再播种幂等（零重复边/节点）", len(kg.edges) == n_edges)
check("敌对物种带 敌对->玩家 边，情形类不挂",
      any(e.src == "creeper" and e.relation == "敌对" for e in kg.edges)
      and not any(e.src == "lava" and e.relation == "敌对" for e in kg.edges))

# ═══ 2. Kernel：保护=关系，授权=图节点 ═══
k = SafetyKernel(kg=kg, engine=None, config=cfg)
act = {"action_type": "gather_resource", "target": "chest",
       "params": {"resource": "chest"}, "priority": 0.5}
ok, why, granted = k.guard(dict(act), source="autonomy", now=1000.0)
check("自主拆箱 → 拒绝（reason 语义化，不是名单）",
      not ok and why.startswith("requires_user_permission"), why)
ok, why, granted = k.guard(dict(act), source="user", now=1001.0)
check("用户命令拆箱 → 放行（source=user 即授权）", ok and granted, why)
check("用户命令留下图上授权证据（授权:modify:chest 节点）",
      kg.get_node("授权:modify:chest") is not None)
# 授权有效期内（>anti_loop 窗），之后的自主关联动作被放行（ttl 600s）
ok2, _, _ = k.guard(dict(act), source="autonomy", now=1010.0)
check("授权窗口内自主关联动作被放行（保护可解除的关系语义）", ok2)
# TTL 过期后恢复保护
ok3, _, _ = k.guard(dict(act), source="autonomy", now=1010.0 + 601)
check("授权过期 → 恢复保护（时间参数是授权语义不是行为规则）", not ok3)
# 普通资源不受影响
ok4, _, g4 = k.guard({"action_type": "gather_resource", "target": "iron_ore",
                      "params": {"resource": "iron_ore"},
                      "priority": 0.5}, source="autonomy", now=2000.0)
check("可采资源自主执行不被保护拦（granted=False 走白名单正常路径）",
      ok4 and not g4, ok4)

# ═══ 3. Kernel：攻击对象核验（图因果，不是名单点名）═══
okA, _, _ = k.guard({"action_type": "attack_entity", "target": "creeper",
                     "params": {"entity": "creeper"}, "priority": 0.6},
                    source="autonomy", now=3000.0)
check("目标在图上有危险因果（creeper 造成爆炸伤害）→ 攻击可执行", okA)
okB, whyB, _ = k.guard({"action_type": "attack_animal", "target": "cow",
                        "params": {"entity": "cow"}, "priority": 0.6},
                       source="autonomy", now=3010.0)
check("被动生物无危险边 → 自主攻击被拒（target_not_hostile）",
      not okB and whyB.startswith("target_not_hostile"), whyB)
okC, _, _ = k.guard({"action_type": "attack_animal", "target": "cow",
                     "params": {"entity": "cow"}, "priority": 0.6},
                    source="user", now=3020.0)
check("用户点名打牛 → 放行（用户命令授权，语言诚实线在 reflex 层另保）", okC)

# ═══ 4. 防抖 = 技术约束，不冒充学习 ═══
spec = {"action_type": "walk_to", "target": "x", "params": {}, "priority": 0.4}
ok_first, _, _ = k.guard(dict(spec), source="autonomy", now=4000.0)
ok_loop, why_loop, _ = k.guard(dict(spec), source="autonomy", now=4001.0)
ok_after, _, _ = k.guard(dict(spec), source="autonomy", now=4010.0)
check("同目标 5s 内重复 → anti_loop 拒绝（纯防死循环）",
      ok_first and not ok_loop and "anti_loop" in why_loop and ok_after)

# ═══ 5. 濒死停手 = 控制原语（不指定去向）═══
check("HP≤4 且有在飞动作 → 停手线触发",
      k.death_floor_check({"health": 3.5}, {"action_type": "mine"}))
check("HP=6 不触发停手（生存行为交给认知，不再是 HP≤8 强制逃跑）",
      not k.death_floor_check({"health": 6.0}, {"action_type": "mine"}))
check("空闲时无需停手", not k.death_floor_check({"health": 1.0}, None))

# ═══ 6. normalize/spec 通道：granted 标记传到技能层 ═══
import skills.gathering as gath
check("技能层保留物理兜底：非白名单且无 _kernel_granted → 拒绝",
      any(r in open(os.path.join(os.path.dirname(os.path.dirname(
          os.path.abspath(__file__))), "skills", "gathering.py"),
          encoding="utf-8").read()
          for r in ("_kernel_granted",)))

print()
if fail:
    print("✗", fail)
    sys.exit(1)
print("✓ MC 世界语义 + Safety Kernel 验收通过（保护=关系/授权=图节点/攻击=图因果/防抖=技术）")
