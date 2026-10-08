# test_stimulation_hunger.py — "闲得发慌"必须是真张力（2026-09-24 用户愤怒案）
# ============================================================================
# 用户："她每次都在我旁边不动——这叫什么玩游戏。" 机制取证：张力场里社交有
# social_absence（N 分钟没互动会自己涨），好奇侧却**没有任何随时间累积的
# 剥夺源**：世界安静时 exploration/novelty 全是即时读数，发呆一小时和刚醒
# 一模一样。结果：没有陌生刺激 = 永远躺着。这不是调参能治的，是缺一条与
# social_absence 同构的通用时钟——stimulation_hunger。
# 钉四件事：
#   1 张力表存在（config + cognitive_field 默认），源=stimulation_idle_minutes，
#     30 分钟封顶（scale 0.033），且喂进 curiosity 亲和度为正；
#   2 场动力学：idle 越久 level 越高、单调、封顶 1.0；刺激恢复后快退（fall）；
#   3 结算时钟：curiosity 动机成功 → _last_novel_ts 更新；social 成功/失败
#     都不更新（剥夺钟只认真的新体验）；
#   4 app 接线存在（provider 注册，缺位=假旋钮，D-11 教训）。
# 离线：零 LLM、零网络、临时目录。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_stimulation_hunger.py
# ============================================================================

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

import config as C
from cognitive_field import DEFAULT_TENSIONS, DEFAULT_DRIVES
from tension_field import TensionField

fail = []


def check(n, c, d=""):
    print(f"[{'PASS' if c else 'FAIL'}] {n}" + (f" | {d}" if d and not c else ""))
    if not c:
        fail.append(n)


# ── 1 注册表 ──
spec = DEFAULT_TENSIONS.get("stimulation_hunger") or {}
comp = (spec.get("components") or [{}])[0]
check("1 张力默认表含 stimulation_hunger（源=剥夺分钟数）",
      comp.get("source") == "stimulation_idle_minutes"
      and abs(float(comp.get("scale", 0)) - 0.033) < 1e-9, str(spec))
check("2 config 覆盖表同在（rise<fall 快消退：喂饱就该静下来）",
      "stimulation_hunger" in C.DEFAULT_CONFIG.get("tension_sources", {})
      and float(spec.get("rise", 9)) < float(spec.get("fall", 0)))
aff = DEFAULT_DRIVES["curiosity"]["affinities"]
check("3 curiosity 亲和度为正加数（剥夺→好奇，不是禁令/后门）",
      0 < float(aff.get("stimulation_hunger", 0)) <= 0.25, str(aff))

# ── 2 场动力学 ──
idle = {"m": 0.0}
tf = TensionField(specs={"stimulation_hunger": DEFAULT_TENSIONS["stimulation_hunger"]},
                  samplers={"stimulation_idle_minutes": lambda: (idle["m"], {})})
idle["m"] = 15.0
lv15 = tf.step(settle=True)["stimulation_hunger"]      # 15 分钟≈半饱和
idle["m"] = 90.0
for _ in range(30):
    lv = tf.step()["stimulation_hunger"]               # 常规拍趋近
idle["m"] = 45.0
lv45 = lv
idle["m"] = 90.0
tf.step(settle=True)
lv_hi = tf.step(settle=True)["stimulation_hunger"]
idle["m"] = 0.0                                        # 真拿到新体验，钟归零
lvs = [tf.step()["stimulation_hunger"] for _ in range(6)]
check("4 level 随剥夺时长单调升且封顶（15min < 90min≈1.0）",
      lv15 < lv_hi <= 1.0 and lv_hi > 0.97,
      f"{lv15:.2f} {lv_hi:.2f}")
check("5 刺激恢复后快退（fall>rise：喂饱的张力几分钟内让位）",
      lvs[-1] < 0.35 and lvs == sorted(lvs, reverse=True), f"{lvs[0]:.2f}→{lvs[-1]:.2f}")

# ── 3 结算时钟 ──
import tempfile
BASE = tempfile.mkdtemp(prefix="fas_stim_")
from autonomy import AutonomousLoop
loop = AutonomousLoop(kg=None, config=dict(C.DEFAULT_CONFIG), data_dir=BASE)
t0 = time.time() - 500
loop._last_novel_ts = t0
loop._on_action_settled({"action_type": "follow_entity", "target": "P",
                         "motivation": "social"},
                        {"success": True, "cancelled": False}, True, now=t0 + 1)
check("6 社交成功不归零剥夺钟（那不是新体验）",
      loop._last_novel_ts == t0, str(loop._last_novel_ts))
loop._on_action_settled({"action_type": "inspect_entity", "target": "cow",
                         "motivation": "curiosity"},
                        {"success": True, "cancelled": False,
                         # 真看见了才算：P6 之后守卫要求直接观察证据
                         "detail": {"entity": "cow", "dist": 3.0}}, True, now=t0 + 2)
check("7 好奇动机成功结算 → 归零（真看见了新东西才算）",
      loop._last_novel_ts == t0 + 2, str(loop._last_novel_ts))
loop._last_novel_ts = t0
loop._on_action_settled({"action_type": "explore_direction", "target": "north",
                         "motivation": "curiosity"},
                        {"success": False, "cancelled": False,
                         "reason": "path_stall:legs_not_moving"}, False, now=t0 + 3)
check("8 失败（哪怕假成功平反后的真失败）不归零",
      loop._last_novel_ts == t0, str(loop._last_novel_ts))

# ── 4 生产接线（D-11"假旋钮"防线）──
src = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app.py"), encoding="utf-8").read()
check("9 app 注册了 stimulation_idle_minutes provider（有读者）",
      '"stimulation_idle_minutes"' in src and "_last_novel_ts" in src)

shutil.rmtree(BASE, ignore_errors=True)
print("\n" + "=" * 60)
if fail:
    print(f"FAIL: {len(fail)} 项未通过: {fail}")
    sys.exit(1)
print("PASS: 无聊自己会长成想动的理由（剥夺张力+时钟+接线齐）")
