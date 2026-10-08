# test_minecraft.reflex.py — 游戏内指令解析测试（纯函数）
# 全部用例来自真实游玩中说过的话 + 由它们暴露出的三类 bug：
#   否定句被当肯定执行（"不用跟着我了"→ 她开始跟）、分组错位（挖/攻击取到空目标）、
#   秒数解析 ValueError（"向前走3秒"整轮回答崩）。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_minecraft.reflex.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from minecraft.reflex import parse_reflex_command, describe

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def act(text):
    return parse_reflex_command(text)


# ── 1. 否定优先（真实 bug：她说"好，不跟了"，但身体开始跟） ──
p = act("不用跟着我了")
check("否定+跟着我 → 执行停止（不是跟随）", p["action"] == "stop" and p["negated"] is True,
      str(p))
check("否定原因可读（写清为什么反着执行）", "否定" in p.get("reason", ""), p.get("reason"))
for t in ("别跟着我了", "不要跟着我", "别再过来了", "不用过来了", "不要跟我走", "别向我走了"):
    pp = act(t)
    check(f"否定式「{t}」→ stop", pp["action"] == "stop", str(pp))

# 否定+挖/攻击（2026-09-19 安全修复）：没有"反着挖/反着打"的语义——
# 旧实现会照挖照打（"不要攻击他"→ attack("他")），现在显式拒绝并留 refused 痕。
for t, refused in (("别挖这个", "dig"), ("不要挖石头", "dig"), ("别挖了", "dig"),
                   ("不要攻击他", "attack"), ("别打那个僵尸", "attack")):
    pp = act(t)
    check(f"否定式「{t}」→ 拒绝执行 refused={refused}",
          pp["action"] == "none" and pp.get("negated") is True
          and pp.get("refused") == refused and "否定" in pp.get("reason", ""),
          str(pp))

# ── 2. 肯定式照常执行 ────────────────────────────────────
check("「跟着我」→ follow", act("跟着我")["action"] == "follow")
check("「过来」→ approach", act("过来")["action"] == "approach")
check("「到我这边」→ approach", act("到我这边")["action"] == "approach")
check("「停下」→ stop", act("停下")["action"] == "stop")
check("「别动」→ stop（本身就是停止命令）", act("别动")["action"] == "stop")
check("「跳一下」→ jump", act("跳一下")["action"] == "jump")

# ── 3. 移动方向与秒数（旧代码 float("前") 抛 ValueError） ──
p = act("向前走3秒")
check("向前走3秒 → forward 3.0s",
      p["action"] == "move" and p["params"]["direction"] == "forward"
      and p["params"]["seconds"] == 3.0, str(p))
p = act("向后走2.5秒")
check("向后走2.5秒 → back 2.5s",
      p["params"]["direction"] == "back" and p["params"]["seconds"] == 2.5, str(p))
p = act("左移")
check("左移（无秒数）→ left 1.0s 默认",
      p["params"]["direction"] == "left" and p["params"]["seconds"] == 1.0, str(p))
p = act("前进5秒")
check("前进5秒 → forward 5.0s（秒数不再取错组）",
      p["action"] == "move" and p["params"]["direction"] == "forward"
      and p["params"]["seconds"] == 5.0, str(p))
check("纯方向词不会抛异常（旧实现会崩整轮回答）",
      act("向前走")["action"] == "move")

# ── 4. 挖掘：目标必须解析出来（旧的 group 错位取到空） ────
p = act("挖一个木头")
check("挖一个木头 → 目标原木/oak_log",
      p["action"] == "dig" and p["params"]["target"] == "木头"
      and p["params"]["block"] == "oak_log", str(p))
p = act("挖点石头")
check("挖点石头 → stone", p["params"]["block"] == "stone", str(p))
p = act("挖钻石矿")
check("挖钻石矿 → diamond_ore", p["params"]["block"] == "diamond_ore", str(p))

# ── 5. 攻击：必须点名，绝不"就近挑一个"（旧实现在空目标时攻击最近实体） ──
p = act("攻击僵尸")
check("攻击僵尸 → zombie 且标为已知敌对",
      p["action"] == "attack" and p["params"]["entity"] == "zombie"
      and p["params"]["known_hostile"] is True, str(p))
p = act("攻击")
check("只说「攻击」→ 拒绝执行（不替用户挑目标）",
      p["action"] == "none" and "不会替你" in p.get("reason", ""), str(p))
p = act("打")
check("只说「打」→ 拒绝执行", p["action"] == "none", str(p))
p = act("攻击苦力怕")
check("攻击苦力怕 → creeper", p["params"]["entity"] == "creeper", str(p))
p = act("攻击鸡")
# 2026-09-19：新增中文→英文实体名翻译——bot /attack 按英文名匹配，
# 不翻译永远 no_target（实测 bug）。"鸡"→"chicken"。
check("用户明确点名非敌对目标也执行（翻译为英文名并说明不在敌对表里）",
      p["action"] == "attack" and p["params"]["entity"] == "chicken"
      and "不在已知敌对生物表里" in p.get("reason", ""), str(p))

# ── 6. 非指令不乱动 ──────────────────────────────────────
for t in ("你现在在哪里", "你怎么在水下", "今天天气不错", "这房子真好看"):
    check(f"非指令「{t}」→ none", act(t)["action"] == "none", str(act(t)))
check("空输入 → none", act("")["action"] == "none")
check("None 输入不崩", act(None)["action"] == "none")

# ── 7. 真实游玩原话回归（用户实际说过的那 8 句） ──────────
real = {
    "过来": "approach",
    "跳一下": "jump",
    "不用跟着我了": "stop",
    "放下你手里的方块": "none",      # 没有对应动作面 → 交给对话层，不误触发
    "你现在在哪里": "none",
    "你怎么在水下": "none",
}
for text, want in real.items():
    got = act(text)["action"]
    check(f"真实原话「{text}」→ {want}", got == want, f"got={got}")

# ── 8. describe 只描述解析结果（不编造） ──────────────────
check("describe 移动", "forward" in describe(act("向前走2秒")))
check("describe 挖掘", "木头" in describe(act("挖木头")))
check("describe 攻击", "僵尸" in describe(act("攻击僵尸")))
check("describe 停止", describe(act("停下")) == "stop")

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 游戏内指令解析测试全过（含否定优先与三类真实 bug 回归）")
