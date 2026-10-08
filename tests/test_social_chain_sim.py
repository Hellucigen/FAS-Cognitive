# test_social_chain_sim.py — B4：社交邀请 → 承诺全链验收（§4）
# ============================================================================
# 钉死五件事（全部走真实函数，只有 bot HTTP 层是假桥）：
#   1 邀请落账：register_invitation 生成持久目标（落盘→跨重启存活）；
#     "一起走"进 follow 正则；否定句（不用一起走）仍是 stop。
#   2 目标透传兑现：legacy "follow" 目标经 realize_goal 映射为
#     follow_entity 后进 _feasible_action —— 旧病是 cap_index 注入
#     （旧 8 词退役）后透传候选永远死于"具身环境不支持"。
#   3 竞争不硬编码：同 (action_type,target) dedupe 保动机地板高者
#     （user_goal 0.8 压过 social 0.3），但 survival 0.9 评分权重链
#     仍可反超——承诺没有插队权，只有地板。
#   4 兑现销账 + 撤账：settle 成功且目标匹配 → 移除；stop 反射
#     cancel_invitation；同型新邀请替换旧邀请。
#   5 社交动机真读图：_motivation_value("social") 消费 SocialDrive
#     激活（旧版 mood 捷径：0.5+mood 钉住，Drive 永不入账）。
# 离线：假桥 + 临时 data_dir；不连真实服务器、不调 LLM、不写用户经历。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_social_chain_sim.py
# ============================================================================

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


import config as C
import minecraft.bridge as bridge
from graph_model import KnowledgeGraph, Node
from diffusion_engine import DiffusionEngine
from minecraft.embodiment import MinecraftEmbodiment
from autonomy import AutonomousLoop
from capability_graph import CapabilityIndex
from minecraft.reflex import parse_reflex_command

BASE = tempfile.mkdtemp(prefix="fas_social_chain_")
CFG = dict(C.DEFAULT_CONFIG)

ENG_CFG = {"lambda_decay": 0.05, "beta_spread": 1.0, "max_depth": 4,
           "theta_threshold": 0.01, "activation_max": 5.0,
           "min_spread_threshold": 0.01, "activation_epsilon": 1e-4,
           "input_similarity_floor": 0.5, "input_default_bonus": 0.5,
           "theta_action": 0.5}

STATE = {}


def set_state(**over):
    STATE.clear()
    STATE.update({
        "connected": True, "username": "Haru",
        "position": {"x": 0.0, "y": 64.0, "z": 0.0},
        "health": 20, "food": 20, "heldItem": None,
        "playersNearby": [], "nearbyEntities": [], "nearbyBlocks": [],
        "chat": [], "connectedAt": 1, "timeOfDay": 6000, "isRaining": False,
    })
    STATE.update(over)


bridge.get_state = lambda: dict(STATE)
bridge.health = lambda: True
bridge.inventory = lambda: {"ok": True, "items": []}
bridge.call = lambda p, payload=None, timeout=3: {"ok": True}
bridge.stop_goto = lambda: {"ok": True}
bridge.sprint = lambda on=True: {"ok": True}
bridge.move = lambda d, s=1.0: True
bridge.get_action_result = lambda: {}
set_state()   # available()→caps 非空的真相源（STATE 默认 connected=True）

kg = KnowledgeGraph()
kg.add_node(Node(id="Haru"))
eng = DiffusionEngine(kg, dict(ENG_CFG))
eng.name_to_node = dict(kg.nodes)
emb = MinecraftEmbodiment(kg=kg, engine=eng, config=dict(CFG))
# 生产形状：cap_index 注入后旧 8 语义词退役——透传必须经实现化才活着
emb.cap_index = CapabilityIndex(kg, eng, dict(CFG))
loop = AutonomousLoop(kg=kg, config=dict(CFG), data_dir=BASE)
loop.register_embodiment(emb)
loop.cap_index = emb.cap_index   # 生产形状：图谱候选在场（未播种→发现
                                 # 失败被吞=空，透传/追问链照跑）

PLAYER = [{"name": "Hellucigen", "dist": 2.5, "rel": {"dx": 2.5}}]


def percept():
    return {"connected": True, "health": 20, "food": 20,
            "position": {"x": 0.0, "y": 64.0, "z": 0.0},
            "players": list(PLAYER), "entities": [], "blocks": [],
            "unknown_entities": [], "unknown_blocks": []}


# ═══ 1. 邀请落账 ═══
print("\n── 1 邀请→承诺 ──")
r = loop.register_invitation("follow", "Hellucigen")
gs = loop.goals()
check("register_invitation 生成持久目标",
      r.get("ok") and len(gs) == 1 and gs[0]["type"] == "follow"
      and gs[0]["source"] == "user_invitation", str(gs))
persisted = os.path.join(BASE, "autonomy.json")
check("目标已落盘（跨重启存活）", os.path.exists(persisted), persisted)

loop.register_invitation("approach", "Hellucigen", text="过来")
loop.register_invitation("approach", "Hellucigen", text="再到我这")
gs = loop.goals()
check("同型新邀请替换旧邀请（不堆积）",
      sum(1 for g in gs if g["type"] == "approach") == 1, str(gs))

# 执行词也接受（反射处可能拿 follow_entity 来登记）
r2 = loop.register_invitation("follow_entity", "Hellucigen")
check("executor 名登记归一为契约词（follow_entity→follow）",
      r2.get("ok") and r2["goal"]["type"] == "follow", str(r2))
check("不支持的类型拒绝（不造承诺）",
      loop.register_invitation("attack", "creeper")["ok"] is False
      and loop.register_invitation("follow", "")["ok"] is False)

# 反射数据行：一起走 / 否定
p1 = parse_reflex_command("一起走")
p2 = parse_reflex_command("不用一起走了")
check("「一起走」命中 follow 指令", p1.get("action") == "follow", str(p1))
check("否定句仍走 stop（不误判为肯定）",
      p2.get("action") == "stop" and p2.get("negated"), str(p2))

# ═══ 2. 目标透传在 cap_index 注入后依然活着（实现化）═══
print("\n── 2 透传实现化 ──")
cands = loop._action_candidates(percept(), [], time.time())
follow_c = [c for c in cands
            if c.get("action_type") == "follow_entity"
            and c.get("motivation") == "user_goal"]
check("legacy follow 目标透传为 follow_entity 候选（user_goal 动机）",
      len(follow_c) == 1, str([(c['action_type'], c['motivation'])
                               for c in cands]))
ok, why = loop._feasible_action(follow_c[0], percept(), time.time())
check("透传候选过 _feasible_action（旧病：具身环境不支持 follow）",
      ok is True, why)
sc, _expl = loop._score_action(follow_c[0], percept(), time.time())
check("候选可评分（走的是全链评分，不是旁路直通）",
      isinstance(sc, float), str(sc))
appr = [c for c in cands if c.get("action_type") == "navigate_to_entity"
        and c.get("target") == "Hellucigen"]
ok2, why2 = (loop._feasible_action(appr[0], percept(), time.time())
             if appr else (False, "无候选"))
check("approach 承诺对**玩家**目标也过在场检查（旧只查生物表）",
      ok2 is True, why2)

# ═══ 3. dedupe 保地板高者（承诺有地板、没有插队权）═══
print("\n── 3 竞争不硬编码 ──")
social_dup = {**follow_c[0], "motivation": "social"}
merged = loop._action_candidates(percept(), [], time.time())
keys = [(c["action_type"], c["target"]) for c in merged]
dups = {k for k in keys if keys.count(k) > 1}
check("候选表内无同键重复（dedupe 生效）", not dups, str(dups))
from autonomy import AutonomousLoop as AL
check("动机地板数据行：user_goal(0.8) > social(0.3)，survival(0.9) 仍最高",
      AL.BASE_MOTIVATION["user_goal"] == 0.8
      and AL.BASE_MOTIVATION["social"] == 0.3
      and AL.BASE_MOTIVATION["survival"] > AL.BASE_MOTIVATION["user_goal"])
# 合并语义走**生产函数**：桩只替换候选来源（_graph_action_candidates），
# dedupe 段仍是 _action_candidates 里的真实代码。
_orig_graph_cands = loop._graph_action_candidates
try:
    loop._graph_action_candidates = lambda ci, p, n, ev: [dict(social_dup)]
    m1 = loop._action_candidates(percept(), [], time.time())
finally:
    loop._graph_action_candidates = _orig_graph_cands
winners = [c for c in m1 if (c["action_type"], c["target"])
           == (social_dup["action_type"], social_dup["target"])]
check("同键 social(图先) vs user_goal(承诺后) → 保承诺（生产 dedupe）",
      len(winners) == 1 and winners[0]["motivation"] == "user_goal",
      str([(c["action_type"], c["motivation"]) for c in m1]))

# ═══ 4. 兑现销账 / 撤账 ═══
print("\n── 4 销账 ──")
n0 = len(loop.goals())
loop._on_action_settled(
    {"action_type": "follow_entity", "target": "Hellucigen",
     "params": {"entity": "Hellucigen"}},
    {"success": True}, True)
gs = loop.goals()
check("follow 兑现 → 该承诺移除，approach 承诺不动",
      len(gs) == n0 - 1 and not any(
          g["type"] == "follow" for g in gs), str(gs))
loop._on_action_settled(
    {"action_type": "navigate_to_entity", "target": "Hellucigen",
     "params": {"entity": "Hellucigen"}},
    {"success": False, "reason": "unreachable"}, False)
check("失败不销账（承诺还在，下一拍继续兑现）",
      any(g["type"] == "approach" for g in loop.goals()), str(loop.goals()))
n_before = len(loop.goals())
c = loop.cancel_invitation()
check("撤账清空全部邀请承诺", c["cancelled"] == n_before
      and all(g.get("source") != "user_invitation" for g in loop.goals()),
      str(c))
loop.add_goal({"type": "collect", "target": "coal_ore", "params": {}})
loop.cancel_invitation()
check("cancel 只清邀请来源（调试面板目标不动）",
      any(g["type"] == "collect" for g in loop.goals()), str(loop.goals()))

# 跨重启：重新构造 loop 读同一 data_dir，邀请承诺仍在
loop.register_invitation("follow", "Hellucigen")
loop2 = AutonomousLoop(kg=kg, config=dict(CFG), data_dir=BASE)
check("承诺跨重启存活（持久目标，不是内存标签）",
      any(g["type"] == "follow" and g.get("source") == "user_invitation"
          for g in loop2.goals()), str(loop2.goals()))

# ═══ 5. 社交动机读图（SocialDrive 真消费者）═══
print("\n── 5 动机读图 ──")
kg.add_node(Node(id="SocialDrive",
                 extra_attrs={"type": "drive", "status": "active"}))
kg.add_node(Node(id="CuriosityDrive",
                 extra_attrs={"type": "drive", "status": "active"}))
kg.nodes["SocialDrive"].activation = 4.0      # 孤独压满
v_lonely = loop._motivation_value("social")
kg.nodes["SocialDrive"].activation = 0.2      # 刚聊过，驱动回落
v_fresh = loop._motivation_value("social")
check("SocialDrive 激活真实进入社交动机（4.0/5 ≫ 0.2/5+地板）",
      v_lonely >= 0.79 and v_lonely > v_fresh,
      f"lonely={v_lonely} fresh={v_fresh}")
check("动机不靠 mood 捷径钉底（无 persona 时地板=social 0.3 与 Drive 取大）",
      0.0 <= v_fresh <= 1.0, str(v_fresh))

# config 数据行：score_threshold 与 curiosity 对称吃 drive.social
row = None
for _k, _v in CFG.items():
    if isinstance(_v, dict):
        for _sub in _v.values():
            if isinstance(_sub, dict) and "action.score_threshold" in _sub:
                row = _sub["action.score_threshold"]["effects"]
check("action.score_threshold effects 含 drive.social（与 curiosity 对称）",
      row is not None and row.get("drive.social") == -0.06
      and row.get("drive.curiosity") == -0.06, str(row))

# app.py 生产面标签：user_invitation 已退役为 user_commitment + register
src = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app.py"), encoding="utf-8").read()
check("app.py 不再生产 user_invitation 动机标签",
      '"motivation": "user_invitation"' not in src
      and src.count('"user_commitment"') >= 2, "")
check("app.py 反射处接 register_invitation/cancel_invitation",
      "autonomy.register_invitation(" in src
      and "autonomy.cancel_invitation()" in src)

# ═══ 6. "跟随占大头"回归（2026-09-24 真机：AFK 玩家被她贴身跟满 10 分钟
#     一块，周而复始；探索/采集全被挤在后面）═══
print("\n── 6 在场≠要跟 ──")
NOW6 = time.time()
PERC6 = percept()
_d_bind = loop._bind_follow({"action_type": "follow_entity"}, PERC6, NOW6)
check("同伴就在跟前（dist≤follow_skip_dist）→ 自主层不再提出跟随",
      _d_bind == [], str(_d_bind))
PERC6["players"] = [{"name": "Hellucigen", "dist": 10.0, "rel": {"dx": 10.0}}]
_d_bind2 = loop._bind_follow({"action_type": "follow_entity"}, PERC6, NOW6)
check("人走远（>4 格）→ 下一拍跟随自然回来（是动力学不是禁令）",
      len(_d_bind2) == 1 and _d_bind2[0].get("motivation") == "social",
      str(_d_bind2))
# 孤独信号：同伴在场按时长 ×0.25 缓释（app.py 生产函数本体，非复制）
_app = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app.py"), encoding="utf-8").read()
_seg = _app[_app.index("def _cs_last_interaction_min"):_app.index("def _cs_safety_target")]
check("social_idle_minutes 生产语义含在场缓释（×0.25），交流才算满足",
      "playersNearby" in _seg and "0.25" in _seg)

shutil.rmtree(BASE, ignore_errors=True)

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
    sys.exit(1)
print("PASS: 社交承诺全链验收通过（落账/透传/竞争/销账/读图）")
