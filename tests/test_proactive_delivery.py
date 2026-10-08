# test_proactive_delivery.py — B5：主动表达的嘴与回执（§5/§21）
# ============================================================================
# 钉死六件事：
#   1 出生拍豁免：CI 形成与衰减同拍（先 L575 形成 → L577 衰减），沟通类
#     出生拍不再被 ×decay 咬掉（0.79 出生即濒死的根）；下一拍照常衰减。
#   2 express→行动：_express 产文后 propose communicate（source=cognition、
#     motivation=self_expression、reason 带 ci_id），不是旁路直说。
#   3 真回执写回：_settle → cc.note_delivery 只在 delivery.action_id 匹配
#     且 pending 时认领；送达/失败只认回执，绝不自判"说了"。
#   4 失约保护：propose 被拒 → pending_retry；到点（改字段模拟 ≥60s）再试
#     一次；再拒 → abandoned（一次性机会，不刷屏也不假送达）。
#   5 通道出口：send_text 复用画像清洗+单点节流（同文去重/最小间隔），
#     不可用/停用/节流各如实返回；embodiment._communicate 有 hub 走 hub、
#     节流不兜底重发、仅通道不存在/停用才兜底 bridge.say。
#   6 自环防线：她自己的 chat 行 classify=KIND_SELF（机械过滤已在，钉回归）。
# 离线：假桥/假 LLM（RunnableLambda）；不连服务器、不写用户经历。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_proactive_delivery.py
# ============================================================================

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.WARNING)

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


from graph_model import KnowledgeGraph, Node
from continuous_cognition import (ContinuousCognition, ST_READY,
                                  ST_EXPRESSED)

# ═══ 共用桩（与 test_continuous_cognition 同型）═══
class FakeEngine:
    def __init__(self):
        import threading
        self._lock = threading.RLock()
        self._running = False
        self.topk = []

    def register_activation_source(self, ids, source_type="external_input"):
        pass

    def get_topk(self, k=15):
        return self.topk[:k], []

    def decay_step(self):
        pass

    def diffuse_step(self):
        pass

    def mark_active(self, ids):
        pass

    def mark_edges_active(self, edges):
        pass

    def clear_anchors(self):
        pass


class _Resp:
    content = "我看到一只牛，想跟你说说它。"


from langchain_core.runnables import RunnableLambda


class FakeNLP:
    def __init__(self):
        self.calls = 0
        self.chat_llm = RunnableLambda(self._gen)

    def _gen(self, *a, **k):
        self.calls += 1
        return _Resp()

    def ask(self, *a, **k):
        self.calls += 1
        return "{}"


class FakeBuffer:
    def __init__(self):
        self.exprs = []

    def add_expression(self, e):
        self.exprs.append(e)


CFG = {"continuous_cognition": {
    "tick_seconds": 0.01, "pulse_every_ticks": 1,
    "form_threshold": 0.50, "express_threshold": 1.01,
    "inhibition_cooldown_s": 0.1}}


def make_cc():
    kg = KnowledgeGraph()
    kg.add_node(Node(id="用户", weight=1.0, graph_space="semantic"))
    kg.add_node(Node(id="Self", weight=1.0, graph_space="self"))
    cc = ContinuousCognition(kg, FakeEngine(), FakeNLP(), FakeBuffer(), dict(CFG))
    return kg, cc


def add_ci(kg, cc, ci_id="CI_test", act=0.8, born=None, typ="communication_intention"):
    kg.add_node(Node(
        id=ci_id, weight=0.5, label="intention", graph_space="self",
        extra_attrs={"type": typ, "kind": "communication", "status": ST_READY,
                     "activation": act, "basis": ["用户"],
                     "born_pulse": (cc._pulse_seq if born is None else born),
                     "created": "now"}))
    return kg.nodes[ci_id]


# ═══ 1. 出生拍豁免 ═══
print("\n── 1 出生拍豁免 ──")
kg, cc = make_cc()
below = cc.cfg["discard_below"]
ci = add_ci(kg, cc, "CI_born", act=round(below * 1.05, 4))   # 出生即贴淘汰线
act0 = ci.extra_attrs["activation"]
cc._decay_intentions()
check("沟通类出生拍不被 ×decay（豁免前 act 原样）",
      ci.extra_attrs["activation"] == act0
      and ci.extra_attrs["status"] == ST_READY,
      str(ci.extra_attrs.get("activation")))
cc._pulse_seq += 1                  # 下一拍：豁免只护出生拍
cc._decay_intentions()
check("下一拍照常衰减（晋升/淘汰照常评估）", round(ci.extra_attrs["activation"], 6)
      < act0, str(ci.extra_attrs.get("activation")))
gen = add_ci(kg, cc, "GEN_born", typ="intention")
g0 = gen.extra_attrs["activation"]
cc._decay_intentions()   # 同拍（_tick 未动）
check("豁免只给沟通类（通用 intention 照旧衰减）",
      gen.extra_attrs["activation"] < g0, str(gen.extra_attrs))

# ═══ 2+3. express → propose → 真 _settle → note_delivery（全真实链路）═══
print("\n── 2/3 产文→动作→回执 ──")
from action_system import ActionManager


class FakeEmb:
    """同步 communicate 具身桩：只证明「投递经通道/回执链」，不含 MC 语义。"""

    def __init__(self, ok=True):
        self.ok = ok
        self.seen = []

    def execute(self, action):
        self.seen.append(dict(action))
        if self.ok:
            return {"success": True, "action": "communicate",
                    "result": "said:牛", "via": "channel_hub"}
        return {"success": False, "action": "communicate",
                "reason": "say_failed"}

    def raw_state(self):
        return {}

    def cancel(self):
        return {"ok": True}


kg, cc = make_cc()
ci = add_ci(kg, cc, "CI_ok", act=0.9)
ci.extra_attrs["reply_goal"] = "share_related_knowledge"
emb_stub = FakeEmb(ok=True)
am = ActionManager(embodiment=emb_stub, kg=kg, config={})
am.cc = cc                    # 生产接线形状（app.py L1165 同款属性注入）
cc.action_manager = am
done = cc._express(ci)
dlv = (ci.extra_attrs.get("delivery") or {})
check("_express 成功产文", done is True)
check("动作被真实执行（embodiment 收到 communicate+文本+ci_id）",
      len(emb_stub.seen) == 1
      and emb_stub.seen[0]["action_type"] == "communicate"
      and ci.id in (emb_stub.seen[0].get("reason") or [])
      and emb_stub.seen[0].get("motivation") == "self_expression",
      str(emb_stub.seen))
check("同步结算回执写回：delivered（经 _settle→cc.note_delivery）",
      dlv.get("status") == "delivered" and dlv.get("success") is True,
      str(dlv))
check("送达话文进 expressed_text（网页出口保留，非双写）",
      bool(ci.extra_attrs.get("expressed_text")) and len(cc._queue) == 1)

# 说话失败 → say_failed（不自判送达）
ci2 = add_ci(kg, cc, "CI_bad", act=0.9)
ci2.extra_attrs["reply_goal"] = "share_related_knowledge"
am.embodiment = FakeEmb(ok=False)
cc._express(ci2)
d2 = ci2.extra_attrs.get("delivery") or {}
check("具身失败 → delivery=say_failed+reason（诚实失败留痕）",
      d2.get("status") == "say_failed" and d2.get("reason") == "say_failed",
      str(d2))
check("note_delivery 不认领无关 action_id",
      cc.note_delivery("act_不存在", {}, True) is False
      and cc.note_delivery(None, {}, True) is False)

# ═══ 4. busy → pending_retry → 一次性再试 → abandoned ═══
print("\n── 4 失约保护 ──")


class StubAM:
    """假动作调度：accept_at 指定第几次 propose 被接受（默认永不）。"""

    def __init__(self, accept_at=()):
        self.calls = []
        self.accept_at = set(accept_at)

    def propose(self, spec, source="autonomy", now=None):
        self.calls.append((spec, source))
        if len(self.calls) in self.accept_at:
            return {"started": True, "queued": False,
                    "action_id": spec.get("action_id")}
        return {"started": False, "queued": False,
                "action_id": spec.get("action_id"),
                "reason": "当前动作 mining 承诺期内"}


kg, cc = make_cc()
ci = add_ci(kg, cc, "CI_retry", act=0.9)
stub = StubAM()                      # 永不接受 → 全程走保护路径
cc.action_manager = stub
accepted = cc._propose_communicate(ci, "第一句")
d = ci.extra_attrs["delivery"]
check("被拒 → pending_retry + retry_after≈+60s + attempts=1",
      accepted is False and d["status"] == "pending_retry"
      and abs(d["retry_after"] - time.time() - 60) < 5 and d["attempts"] == 1,
      str(d))
# 生产顺序里 _express 已先写 expressed_text；再试从那里取话（不重造文本）
ci.extra_attrs["expressed_text"] = "第一句"
cc._retry_pending_delivery()
check("未到点不再试（retry_after 未到期 → 零额外 propose）",
      len(stub.calls) == 1, str(len(stub.calls)))
with kg._lock:
    ci.extra_attrs["delivery"]["retry_after"] = time.time() - 1  # 模拟 ≥60s
cc._retry_pending_delivery()
check("到点再试一次（第二次 propose 带 retry_once 注记）",
      len(stub.calls) == 2 and "retry_once" in stub.calls[1][0]["reason"],
      str(stub.calls[-1][0]["reason"]))
d = ci.extra_attrs["delivery"]
check("再试仍被拒 → abandoned（一次性机会，不无限重试）",
      d["status"] == "abandoned", str(d))

# 到点且接受 → pending（等回执）
kg, cc = make_cc()
ci = add_ci(kg, cc, "CI_reok", act=0.9)
stub = StubAM(accept_at={2})         # 首次拒、再试接受
cc.action_manager = stub
cc._propose_communicate(ci, "x")
ci.extra_attrs["expressed_text"] = "x"
with kg._lock:
    ci.extra_attrs["delivery"]["retry_after"] = time.time() - 1
    ci.extra_attrs["delivery"]["attempts"] = 1
cc._retry_pending_delivery()
check("到点再试被接受 → 回到 pending（action_id 已换新，等回执）",
      ci.extra_attrs["delivery"]["status"] == "pending"
      and ci.extra_attrs["delivery"]["action_id"]
      == stub.calls[1][0]["action_id"], str(ci.extra_attrs["delivery"]))
with kg._lock:
    aid = ci.extra_attrs["delivery"]["action_id"]
check("pending 期间回执到达 → delivered",
      cc.note_delivery(aid, {"result": "said:x"}, True) is True
      and ci.extra_attrs["delivery"]["status"] == "delivered")

# ═══ 5. ChannelHub.send_text：清洗+节流+诚实 ═══
print("\n── 5 通道出口 ──")
from conversation_channel import ChannelHub


class FakeCh:
    def __init__(self, cid="fake_chat", ok=True):
        self.id = cid
        self.kind = "test"
        self.profile = {"max_reply_chars": 20, "strip_markdown": True}
        self.sent = []
        self.ok = ok

    def available(self):
        return True

    def fetch(self):
        return []

    def send(self, text, meta=None):
        self.sent.append(text)
        return {"ok": self.ok, "detail": "已发" if self.ok else "桥没接"}


hub = ChannelHub(config={})
ch = FakeCh()
hub.register(ch)
r1 = hub.send_text("fake_chat", "**加粗**和超长的" + "话" * 60)
_t = ch.sent[0] if ch.sent else ""
check("清洗复用画像（strip_markdown+按 profile 截断）后发送",
      r1.get("ok") and "**" not in _t and _t.startswith("加粗和超长的")
      and len(_t) <= 20 and _t.endswith("…"), repr(_t))
r2 = hub.send_text("fake_chat", ch.sent[0])
check("同文去重窗口内 → throttled（不假送达）",
      r2.get("ok") is False and r2.get("reason") == "throttled"
      and len(ch.sent) == 1, str(r2))
r3 = hub.send_text("fake_chat", "换个说法")
check("最小间隔未到也节流（默认 6s）",
      r3.get("ok") is False and r3.get("reason") == "throttled", str(r3))
hub_fast = ChannelHub(config={"channel_send_min_interval_s": 0.0,
                               "channel_send_dedup_window_s": 0.0})
ch2 = FakeCh(cid="fake_chat")
hub_fast.register(ch2)
hub_fast.send_text("fake_chat", "第一")
r4 = hub_fast.send_text("fake_chat", "第一")
check("节流参数可配（0/0 时同文可重发——证明确实读 config）",
      r4.get("ok") and len(ch2.sent) == 2, str(r4))
ch3 = FakeCh(ok=False)
hub_fast2 = ChannelHub(config={"channel_send_min_interval_s": 0.0,
                                "channel_send_dedup_window_s": 0.0})
hub_fast2.register(ch3)
r5 = hub_fast2.send_text("fake_chat", "发不出的")
check("通道 send 如实失败 → ok=False（不假成功）",
      r5.get("ok") is False and "桥没接" in r5.get("detail", ""), str(r5))
r6 = hub_fast2.send_text("不存在的通道", "喂")
check("通道不存在 → reason=channel_unavailable",
      r6.get("ok") is False and r6.get("reason") == "channel_unavailable", str(r6))
hub_fast2.set_enabled("fake_chat", False)
r7 = hub_fast2.send_text("fake_chat", "喂")
check("通道停用 → reason=channel_disabled",
      r7.get("ok") is False and r7.get("reason") == "channel_disabled", str(r7))
check("send_text 不抛异常（空文本如实拒）",
      hub_fast2.send_text("fake_chat", "   ").get("ok") is False)

# embodiment._communicate 接 hub
print("\n── 5b 具身发言口 ──")
import config as C
import minecraft.bridge as bridge
import minecraft.embodiment as me_mod
from minecraft.embodiment import MinecraftEmbodiment

say_calls = []
bridge.get_state = lambda: {}
bridge.health = lambda: False
bridge.say = lambda t: (say_calls.append(t) or True)

kg2 = KnowledgeGraph()
eng2 = FakeEngine()
emb = MinecraftEmbodiment(kg=kg2, engine=eng2, config=dict(C.DEFAULT_CONFIG))
hub_ok = ChannelHub(config={})
ch_ok = FakeCh(cid="minecraft_chat")
hub_ok.register(ch_ok)
emb.channel_hub = hub_ok
res = emb._communicate({"action_type": "communicate",
                        "params": {"text": "你好呀"}}, {"text": "你好呀"})
check("_communicate 经通道出口（bridge.say 未被旁路调用）",
      res.get("success") and res.get("via") == "channel_hub"
      and ch_ok.sent == ["你好呀"] and not say_calls, str(res))
r_thr = hub_ok.send_text("minecraft_chat", "你好呀")  # 先把同文打进节流窗
res2 = emb._communicate({"action_type": "communicate",
                         "params": {"text": "你好呀"}}, {"text": "你好呀"})
check("节流拒发时不兜底重发（bridge.say 仍零调用，reason=say_failed 如实）",
      res2.get("success") is False and res2.get("reason") == "say_failed"
      and not say_calls and r_thr.get("reason") == "throttled", str(res2))
hub_missing = ChannelHub(config={})            # 没注册 minecraft_chat
emb.channel_hub = hub_missing
res3 = emb._communicate({"action_type": "communicate",
                         "params": {"text": "兜底一句"}}, {"text": "兜底一句"})
check("通道不存在 → 兜底 bridge.say（如实，另一条诚实路）",
      res3.get("success") and res3.get("via") == "bridge_direct"
      and say_calls == ["兜底一句"], str(res3))

# ═══ 6. 自环防线（KIND_SELF 机械过滤，回归钉）═══
print("\n── 6 自环 ──")
import minecraft.channel as mc_ch_mod
import types
mc_ch_mod.bridge = types.SimpleNamespace(get_state=lambda: {
    "connected": True, "username": "Haru", "chat": []})
mch = mc_ch_mod.MinecraftChatChannel()
mch.available()
check("她自己发的话 classify=KIND_SELF（回她自己的话→自环不可能）",
      mch.classify({"sender": "Haru", "text": "我看到一只牛"}) ==
      mc_ch_mod.KIND_SELF, "")
check("用户的话 classify=KIND_USER（对照）",
      mch.classify({"sender": "NotHer", "text": "在哪呢"}) in
      (mc_ch_mod.KIND_OTHER, mc_ch_mod.KIND_USER), "")

print("\n" + "=" * 60)
if FAILURES:
    print(f"FAIL: {len(FAILURES)} 项未通过: {FAILURES}")
    sys.exit(1)
print("PASS: 主动投递全链验收通过（豁免/产文→动作/回执/再试/节流/自环）")
