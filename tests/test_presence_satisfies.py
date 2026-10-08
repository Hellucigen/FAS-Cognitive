# -*- coding: utf-8 -*-
"""陪伴即满足（2026-09-24）：同伴在场必须**压低** SocialDrive，而不是喂饱它。

旧语义 social_presence +0.25 是"越在一起越想跟"——跟屁虫循环的最后一条腿
（前两条：贴身跳过、追上即释，都已修）。真实玩家同服是各玩各的：在场=满足，
只有人走远后的缺席时钟才该把她拉回你身边。

数据级修改（config drive_field.social.affinities 整体覆盖，_merged 一层深）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as _C
from graph_model import KnowledgeGraph
from drive_engine import DriveEvaluator

FAILURES = []


def check(name, cond, detail=""):
    print(("PASS: " if cond else "FAIL: ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILURES.append(name)


def social_level(players, idle=0.0, need=0.4):
    """players_present 提供者 = players，归属需求固定 0.4 作非零基线，
    结算一轮取 SocialDrive（全零基线会把两组都钳在 0，看不出抑制）。"""
    kg = KnowledgeGraph()
    ev = DriveEvaluator(kg, dict(_C.DEFAULT_CONFIG))
    ev.set_signal_provider("players_present", lambda: players)
    ev.set_signal_provider("social_idle_minutes", lambda: idle)
    ev.set_signal_provider("social_need", lambda: need)
    r = ev.evaluate(force=True)
    for d in r.get("drives", []):
        if d.get("drive") == "social":
            return float(d.get("activation") or 0.0)
    return -1.0


# 1. 数据行：config 里 social_presence 是负亲和（翻转生效且三键全带）
aff = (((_C.DEFAULT_CONFIG.get("drive_field") or {}).get("drives") or {})
       .get("social") or {}).get("affinities") or {}
check("config social_presence 翻为负", aff.get("social_presence", 0) < 0,
      str(aff))
check("缺席/归属两键未丢（整体覆盖语义）",
      aff.get("social_absence") == 0.45 and aff.get("belonging_need") == 0.30)

# 2. 同基线对照：在场压低、独处抬升
together = social_level(1.0)
alone = social_level(0.0)
check("同伴在场 → 社交驱动被压低", together < alone,
      f"together={together:.3f} alone={alone:.3f}")

# 3. 独处 + 长缺席 → 驱动真的会涨（找你回来的那半边还在）
kg3 = KnowledgeGraph()
ev3 = DriveEvaluator(kg3, dict(_C.DEFAULT_CONFIG))
ev3.set_signal_provider("players_present", lambda: 0.0)
ev3.set_signal_provider("social_idle_minutes", lambda: 60.0)   # 一小时没互动
ev3.set_signal_provider("social_need", lambda: 0.0)
r3 = ev3.evaluate(force=True)
lonely = next((float(d.get("activation") or 0.0) for d in r3.get("drives", [])
               if d.get("drive") == "social"), -1.0)
check("独处+缺席 → 驱动升高（人走远她会想找你）", lonely > alone,
      f"lonely={lonely:.3f} alone={alone:.3f}")

# 4. 与认知场装配一致：CognitiveField 读到的是同一份翻转
from cognitive_field import CognitiveField
cf = CognitiveField(dict(_C.DEFAULT_CONFIG))
sp = ((cf.drives._specs.get("social") or {}).get("affinities") or {}
      ).get("social_presence")
check("CognitiveField 装配读到同一份负亲和", sp is not None and sp < 0, str(sp))

print()
if FAILURES:
    print(f"FAILURES: {len(FAILURES)}"); sys.exit(1)
print("ALL PASS"); sys.exit(0)
