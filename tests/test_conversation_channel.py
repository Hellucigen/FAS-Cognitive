# test_conversation_channel.py — 对话通道层测试
# 离线、假通道适配器、假认知管线（不连游戏、不调 LLM、不写真实数据）。
# 覆盖：去重 / 身份判定（自己·用户·他人）/ 边界重构：other 与 mention 只是
#       事实不是决定 / 沉默如实记录 / 队列与过期 / 优先级排队 /
#       回复加工（去 Markdown、限长）/ 送达失败如实记录 / 管线忙时让位 /
#       启停 / 观测。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_conversation_channel.py

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from conversation_channel import (ChannelHub, KIND_USER, KIND_OTHER, KIND_SELF,
                                  KIND_SYSTEM)
from minecraft.channel import MinecraftChatChannel

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class FakeChannel:
    """假通道：可编排收件箱与发送结果。"""

    id = "fake"
    kind = "test_chat"
    profile = {}

    def __init__(self, user_names=("用户甲",), self_name="Bot", aliases=("Bot",)):
        self.user_names = tuple(user_names)
        self.self_name = self_name
        self.aliases = tuple(aliases)
        self.enabled = True
        self.available_flag = True
        self.inbox = []
        self.sent = []
        self.send_ok = True

    def available(self):
        return self.available_flag

    def fetch(self):
        return list(self.inbox)

    def classify(self, raw):
        s = str((raw or {}).get("sender") or "")
        if s == self.self_name:
            return KIND_SELF
        if s in self.user_names:
            return KIND_USER
        return KIND_OTHER

    def is_mentioned(self, text):
        return any(a in str(text) for a in self.aliases)

    def send(self, text, meta=None):
        if not self.send_ok:
            return {"ok": False, "detail": "假失败"}
        self.sent.append((text, dict(meta or {})))
        return {"ok": True, "detail": "sent"}


def build(profile=None, runner=None, **hub_kw):
    hub = ChannelHub(config={}, poll_interval_s=0.01, **hub_kw)
    ch = FakeChannel()
    ch.profile = dict(profile or {})
    hub.register(ch)
    turns = []

    def _runner(text, meta):
        turns.append((text, dict(meta)))
        return (runner or (lambda t, m: {"answer": "好的，我听到了", "cycle_id": "c_000001"}))(text, meta)

    hub.set_turn_runner(_runner)
    return hub, ch, turns


# ── 1. 用户消息 → 走管线 → 回复回到同一通道 ───────────────
hub, ch, turns = build()
ch.inbox = [{"sender": "用户甲", "text": "你在干嘛", "time": "t1", "seq": 1}]
r = hub.poll_once(now=1000.0)
check("用户消息被接受并处理", len(turns) == 1 and turns[0][0] == "你在干嘛", str(r))
check("回复送回了原通道", len(ch.sent) == 1 and ch.sent[0][0] == "好的，我听到了", str(ch.sent))
check("回复带通道与会话元信息",
      ch.sent[0][1].get("channel") == "fake" and ch.sent[0][1].get("sender") == "用户甲",
      str(ch.sent[0][1]))
check("统计可观测（收到/回复）",
      r["stats"]["accepted"] == 1 and r["stats"]["answered"] == 1, str(r["stats"]))

# ── 2. 去重：同一条消息不会处理两次 ───────────────────────
r2 = hub.poll_once(now=1001.0)
check("重复收到的同一条消息被去重", len(turns) == 1 and r2["accepted"] == 0, str(r2))
ch.inbox = [{"sender": "用户甲", "text": "你在干嘛", "time": "t1", "seq": 1},
            {"sender": "用户甲", "text": "在看什么", "time": "t2", "seq": 2}]
r3 = hub.poll_once(now=1002.0)
check("新增消息照常处理（只去重重复项）",
      len(turns) == 2 and turns[1][0] == "在看什么", str(turns))

# ── 3. 自己的话必须过滤（否则自己回自己） ─────────────────
hub, ch, turns = build()
ch.inbox = [{"sender": "Bot", "text": "我先去看看那边", "time": "t1", "seq": 1}]
r = hub.poll_once(now=1000.0)
check("自己说的消息被过滤", len(turns) == 0 and r["stats"]["ignored_self"] == 1, str(r["stats"]))

# ── 4. 他人消息：进统一认知（通道不再吞），同时记观察簿 ────
hub, ch, turns = build()
observed = []
hub.set_observer(lambda m: observed.append(m))
ch.inbox = [{"sender": "路人乙", "text": "这房子真好看", "time": "t1", "seq": 1}]
r = hub.poll_once(now=1000.0)
check("他人消息也进统一认知入口（other ≠ ignore）",
      len(turns) == 1 and turns[0][0] == "这房子真好看", str(r))
check("meta 携带身份事实（kind=other + sender + mention）",
      turns[0][1].get("kind") == KIND_OTHER
      and turns[0][1].get("sender") == "路人乙"
      and turns[0][1].get("mention") is False, str(turns[0][1]))
check("他人消息同时进观察簿与观察者（通知，不是旁路）",
      len(observed) == 1 and len(hub.other_messages(5)) == 1, str(observed))
check("旧政策键 reply_to_others=False 不再是判据（兼容占位）",
      r["stats"]["routed_other"] == 1 and r["stats"]["observed_other"] == 0,
      str(r["stats"]))

# ── 4b. system 身份仍然被正确分类（入认知，但不算任何人的话） ──
hub, ch, turns = build()
ch.classify = lambda raw: KIND_SYSTEM if raw.get("sender") == "«server»" \
    else FakeChannel.classify(ch, raw)
ch.inbox = [{"sender": "«server»", "text": "天气转为下雨", "time": "t1", "seq": 1}]
r = hub.poll_once(now=1000.0)
check("system 消息按身份分类进统一入口（kind=system，不冒充 user）",
      len(turns) == 1 and turns[0][1].get("kind") == "system", str(turns))

# ── 5. mention = 注意力线索：改排队位置，**不**触发回复 ────
hub, ch, turns = build()
ch.inbox = [{"sender": "路人乙", "text": "这房子真好看", "time": "t1", "seq": 1},
            {"sender": "路人乙", "text": "Bot 你觉得呢", "time": "t2", "seq": 2}]
r = hub.poll_once(now=1000.0)
check("同批里被点名的先被认知看到（调度优先级）",
      len(turns) == 1 and turns[0][0] == "Bot 你觉得呢", str(turns))
check("点名消息的 mention 事实随 meta 交给认知",
      turns[0][1].get("mention") is True, str(turns[0][1]))
hub.poll_once(now=1001.0)
check("没被点名的他人消息**同样**进认知（只是排队靠后）",
      len(turns) == 2 and turns[1][0] == "这房子真好看", str(turns))
hubX, chX, turnsX = build(profile={"reply_to_others": True})
chX.inbox = [{"sender": "路人乙", "text": "随便说点啥", "time": "t1", "seq": 1}]
hubX.poll_once(now=1000.0)
check("reply_to_others=True 档与他人默认档**同路**（True 不产生第二套行为）",
      len(turnsX) == 1 and turnsX[0][1].get("kind") == KIND_OTHER, str(turnsX))

hub3, ch3, turns3 = build(
    profile={"reply_to_others": True},
    runner=lambda t, m: {"answer": "", "behavior": "silence"})
ch3.inbox = [{"sender": "路人乙", "text": "随便说点啥", "time": "t1", "seq": 1}]
r3 = hub3.poll_once(now=1000.0)
check("认知可选择沉默：不发送、记 silence（≠ 发送失败/空回答）",
      not ch3.sent and r3["stats"]["silence"] == 1
      and any(e["kind"] == "silence" for e in hub3.recent(5)), str(r3["stats"]))

# ── 5a. 用户优先级保持（本次重构不降低用户交互优先级） ────
hub, ch, turns = build()
ch.inbox = [{"sender": "路人乙", "text": "闲谈一", "time": "t1", "seq": 1},
            {"sender": "路人乙", "text": "闲谈二", "time": "t2", "seq": 2},
            {"sender": "用户甲", "text": "你先说", "time": "t3", "seq": 3}]
r = hub.poll_once(now=1000.0)
check("user 消息插在同批他人闲聊之前",
      len(turns) == 1 and turns[0][0] == "你先说", str(turns))
hub.poll_once(now=1001.0)
hub.poll_once(now=1002.0)
check("他人消息随后也逐一进认知（不被饿死也不被吞）",
      [t[0] for t in turns] == ["你先说", "闲谈一", "闲谈二"], str(turns))

# ── 6. 回复加工：去 Markdown、压平换行、限长 ──────────────
hub, ch, turns = build(profile={"strip_markdown": True, "max_reply_chars": 40},
                       runner=lambda t, m: {"answer": "**要点**：\n- 第一行\n- 第二行"})
ch.inbox = [{"sender": "用户甲", "text": "说说看", "time": "t1", "seq": 1}]
hub.poll_once(now=1000.0)
check("Markdown 被去掉、换行压平",
      ch.sent[0][0] == "要点： 第一行 第二行", repr(ch.sent[0][0]))
long_hub, long_ch, _ = build(profile={"max_reply_chars": 10},
                             runner=lambda t, m: {"answer": "一" * 50})
long_ch.inbox = [{"sender": "用户甲", "text": "长回答", "time": "t1", "seq": 1}]
long_hub.poll_once(now=1000.0)
check("超长回复被截断并加省略号",
      len(long_ch.sent[0][0]) == 10 and long_ch.sent[0][0].endswith("…"),
      repr(long_ch.sent[0][0]))

# ── 7. 送达失败 / 空回答：如实记录，不假装发过 ─────────────
hub, ch, turns = build()
ch.send_ok = False
ch.inbox = [{"sender": "用户甲", "text": "在吗", "time": "t1", "seq": 1}]
r = hub.poll_once(now=1000.0)
check("发送失败被计入且留痕",
      r["stats"]["send_failed"] == 1 and any(e["kind"] == "send_failed"
                                           for e in hub.recent(5)), str(r["stats"]))
hub2, ch2, _ = build(runner=lambda t, m: {"answer": ""})
ch2.inbox = [{"sender": "用户甲", "text": "在吗", "time": "t1", "seq": 1}]
r2 = hub2.poll_once(now=1000.0)
check("空回答不发送（记 no_answer）",
      not ch2.sent and any(e["kind"] == "no_answer" for e in hub2.recent(5)), str(r2["stats"]))

# ── 8. 管线正忙时让位（消息留队列，不并发跑回合） ─────────
busy = {"v": True}
hub, ch, turns = build()
hub.set_busy_check(lambda: busy["v"])
ch.inbox = [{"sender": "用户甲", "text": "忙的时候", "time": "t1", "seq": 1}]
r = hub.poll_once(now=1000.0)
check("忙碌时不执行回合（消息留在队列）",
      len(turns) == 0 and r["queue"] == 1, str(r))
busy["v"] = False
r = hub.poll_once(now=1001.0)
check("空闲后立刻处理积压消息", len(turns) == 1, str(r))

# ── 9. 队列上限与过期丢弃（游戏中刷屏不会拖垮管线） ──────
hub, ch, turns = build(max_queue=2, max_age_s=10)
ch.inbox = [{"sender": "用户甲", "text": f"第{i}条", "time": f"t{i}", "seq": i}
            for i in range(1, 5)]
r = hub.poll_once(now=1000.0)
check("超出队列上限时丢最旧的并计数",
      r["stats"]["dropped_full"] == 2, str(r["stats"]))
check("队列非空时一轮只处理一条（不连续阻塞）", len(turns) == 1, str(turns))
# 让剩下的消息过期
r2 = hub.poll_once(now=1000.0 + 60)
check("过期消息被丢弃并留痕",
      r2["stats"]["dropped_old"] >= 1 and any(e["kind"] == "dropped" for e in hub.recent(5)),
      str(r2["stats"]))

# ── 10. 通道不可用 / 停用：不收取也不发送 ────────────────
hub, ch, turns = build()
ch.available_flag = False
ch.inbox = [{"sender": "用户甲", "text": "不可用时", "time": "t1", "seq": 1}]
r = hub.poll_once(now=1000.0)
check("通道不可用时完全不收取", len(turns) == 0 and r["fetched"] == 0, str(r))
ch.available_flag = True
hub.set_enabled("fake", False)
r = hub.poll_once(now=1001.0)
check("停用后不再收取", len(turns) == 0, str(r))
hub.set_enabled("fake", True)
r = hub.poll_once(now=1002.0)
check("重新启用后恢复收取", len(turns) == 1, str(r))

# ── 11. 管线异常不拖垮通道（记录后继续） ─────────────────
def boom(t, m):
    raise RuntimeError("管线炸了")


hub, ch, turns = build(runner=boom)
ch.inbox = [{"sender": "用户甲", "text": "会失败", "time": "t1", "seq": 1}]
r = hub.poll_once(now=1000.0)
check("管线异常被计数且通道继续运行",
      r["stats"]["turn_failed"] == 1 and not ch.sent, str(r["stats"]))
ch.inbox = [{"sender": "用户甲", "text": "再试一次", "time": "t2", "seq": 2}]
hub.set_turn_runner(lambda t, m: {"answer": "这次好了"})
r = hub.poll_once(now=1001.0)
check("后续消息照常处理", len(ch.sent) == 1 and ch.sent[0][0] == "这次好了", str(ch.sent))

# ── 12. 游戏聊天通道适配器（身份/点名/序号去重/发送诚实） ──
import minecraft.bridge as bridge

mc_ch = MinecraftChatChannel(user_names=["Hellucigen"], aliases=["Haru"])
mc_ch.profile.update({"max_reply_chars": 160})
check("MC 通道：玩家名归属用户", mc_ch.classify({"sender": "Hellucigen"}) == KIND_USER)
check("MC 通道：自己（bot 名）被识别", mc_ch.classify({"sender": "Haru"}) == KIND_SELF)
check("MC 通道：其他玩家归为他人", mc_ch.classify({"sender": "朋友甲"}) == KIND_OTHER)
check("MC 通道：点名识别（Haru）", mc_ch.is_mentioned("Haru 过来一下"))
check("MC 通道：点名识别（别名）", mc_ch.is_mentioned("fas你在吗"))
check("MC 通道：非点名不算", not mc_ch.is_mentioned("我们去挖矿吧"))
check("MC profile 是环境描述，不含社交政策键（2026-09-21 边界重构）",
      "reply_to_others" not in MinecraftChatChannel.profile
      and "answer_when_mentioned" not in MinecraftChatChannel.profile,
      str(MinecraftChatChannel.profile))

state = {"connected": True, "username": "Haru",
         "chat": [{"seq": 1, "sender": "Hellucigen", "text": "在吗", "time": "t1"},
                  {"seq": 2, "sender": "朋友甲", "text": "哈喽", "time": "t2"}]}
bridge.get_state = lambda: dict(state)
bridge.say = lambda text: True
got = mc_ch.fetch()
check("MC 通道：读到两条聊天", len(got) == 2, str(got))
check("MC 通道：序号水位推进后不重复读",
      mc_ch.fetch() == [], str(mc_ch.fetch()))
state["chat"].append({"seq": 3, "sender": "Hellucigen", "text": "第二句", "time": "t3"})
got2 = mc_ch.fetch()
check("MC 通道：只读新增的那条", len(got2) == 1 and got2[0]["text"] == "第二句", str(got2))
check("MC 通道：消息键用序号（去重稳定）", mc_ch.message_key({"seq": 7}) == "seq:7")
sent_ok = mc_ch.send("你好呀")
check("MC 通道：连接时能发到游戏聊天", sent_ok.get("ok") is True, str(sent_ok))
state["connected"] = False
fail = mc_ch.send("再说一句")
check("MC 通道：未连接时如实失败（不假装送达）",
      fail.get("ok") is False and "未连接" in fail.get("detail", ""), str(fail))
check("MC 通道：未连接时不可用", mc_ch.available() is False)

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 对话通道层测试全过（假通道/假管线，无真实连接与 LLM）")
