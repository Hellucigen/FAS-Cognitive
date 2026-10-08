# conversation_channel.py — 对话通道层（输入/输出的通用收发）
# ============================================================================
# 问题：FAS 的对话入口原本只有 Web 前端一条（POST /api/nlp）。具身环境里的
# 文字聊天（例如某个游戏的聊天框）既进不来也出不去——世界里的对话被丢在地上。
#
# 通用性做法：把"在哪说话"抽象成**通道**（channel），而不是给某个游戏写死一条路。
# 任何环境只要能"收消息 / 发消息"就能注册成通道（游戏内聊天、外部 IM 桥、
# 未来的语音转写……），走的是同一条认知管线：
#
#   通道.fetch() → 归一化消息（含身份事实）→ 去重 → 统一认知入口
#        → 认知决定是否回应 → 回复文本 → 通道.send()（回到消息来的那个通道）
#
# 边界（2026-09-21 重构）：**Channel 描述"外部世界如何与 FAS 连接"，
# 统一认知系统决定"FAS 在这个世界里想做什么"**。
#   · Channel 提供：环境事实（谁说的、哪个通道、何时、是否被点名、发送成没成）
#     + 环境/工程约束（长度上限、Markdown 支持、轮询、过期、队列容量）。
#   · Channel 不决定：回应与否。旧的 `reply_to_others`/`answer_when_mentioned`
#     硬规则已移除——"other → 忽略"和"mention → 回复"都曾是通道层的认知越权。
#     现在所有非 self 消息一律进入统一入口，是否回应由管线内的
#     dialogue_decide 行为竞争决定（沉默是合法结果）。mention 只是**注意力
#     线索**：影响排队位置，并把事实交给认知，永不直接触发回复。
#
# 三条硬约束（重构后仍然成立）：
#   1. 不让每个通道各自调 LLM 或各自维护对话状态：通道只做收发，认知在管线里。
#   2. 身份判定只回答"这个事件是谁产生的"，不回答"FAS 必须怎么做"；
#      唯一例外是 self 过滤——那不是社交政策，是防"自己回自己"的机械回环。
#   3. 不假装配送：send 失败/通道不可用要如实记录，回复不会"看起来发了"。
# ============================================================================

import hashlib
import logging
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)

# 消息身份 —— 只描述"这个事件是谁产生的"，不预设行为（行为归认知）
KIND_USER = "user"      # 主要用户说的话
KIND_OTHER = "other"    # 世界里的其他人：身份事实，交给认知定夺
KIND_SELF = "self"      # 自己说的话：机械过滤（防自己回自己的回环，非社交政策）
KIND_SYSTEM = "system"  # 系统/环境提示

DEFAULT_PROFILE = {
    # ── 环境/工程约束（Channel 有权决定；纯收发侧）──
    "max_reply_chars": 200,       # 场合的回复长度上限（发送后处理）
    "strip_markdown": True,       # 该平台聊天框渲染不了 Markdown
    "poll_interval_s": 2.0,
    "max_age_s": 90,              # 太旧的消息不再处理（避免积压后补答）
    "max_queue": 20,              # 队列上限，超出丢最低优先级的尾部
    # ── 兼容占位（2026-09-21 起**不再是行为规则**）──
    # "是否回应他人 / 被点名是否回应"已交还给统一认知管线（dialogue_decide）。
    # 保留键位只为旧 profile 不报 KeyError；读到它们会打 deprecation 日志。
    "reply_to_others": False,
    "answer_when_mentioned": True,
}

RECENT_LIMIT = 50


class ChannelHub:
    """通道中枢：轮询各通道的收件箱，把消息**连同身份事实**喂给认知管线，
    并把认知真正产生的回复送回原通道。回应与否在这里不做决定。"""

    def __init__(self, config: dict = None, poll_interval_s: float = 2.0,
                 max_queue: int = 20, max_age_s: float = 120):
        self.config = config or {}
        self._channels: dict[str, object] = {}
        self._lock = threading.RLock()
        self._thread = None
        self._stop = threading.Event()

        self._seen: deque = deque(maxlen=400)      # 已处理消息键（去重）
        self._queue: deque = deque()               # 待处理（含到达时间）
        self._recent: deque = deque(maxlen=RECENT_LIMIT)
        self._others: deque = deque(maxlen=RECENT_LIMIT)

        self.poll_interval_s = float(poll_interval_s)
        self.max_queue = int(max_queue)
        self.max_age_s = float(max_age_s)

        self.stats = {"polled": 0, "accepted": 0, "answered": 0, "dropped_old": 0,
                      "dropped_full": 0, "ignored_self": 0, "observed_other": 0,
                      "routed_other": 0, "silence": 0,
                      "send_failed": 0, "turn_failed": 0,
                      "sent": 0, "throttled": 0}   # B5：主动投递出口

        self._turn_runner = None    # fn(text, meta) -> {"answer": str, ...}
        self._observer = None       # fn(msg) -> None（他人消息的通知钩子，非旁路）
        self._busy_check = None     # fn() -> bool（认知管线正忙时让位，不并发跑回合）
        # B5/§5 防刷屏：主动外发是**单点节流**（通道→{last_ts,last_text}）。
        # 只覆盖 send_text 出口；回合回复走 _deliver 另一条语义（回应不受罚）。
        self._out_state: dict[str, dict] = {}

    # ── 注册与生命周期 ────────────────────────────────────

    def register(self, channel):
        """注册通道适配器。适配器需要提供：
        id / kind / profile(dict) / available() / fetch() / send(text, meta)
        可选：is_mentioned(text) 提供"被点名"这一注意力线索（只影响
        排队位置与交给认知的上下文，不构成回复触发器）。
        """
        cid = getattr(channel, "id", None) or f"channel_{len(self._channels)}"
        raw_prof = getattr(channel, "profile", {}) or {}
        if "reply_to_others" in raw_prof or "answer_when_mentioned" in raw_prof:
            logger.info(f"[Channel] {cid} 的 profile 仍带 reply_to_others/"
                        f"answer_when_mentioned——这两个键 2026-09-21 起只是兼容"
                        f"占位，**不再决定回应**（是否回应由认知管线决定）")
        prof = dict(DEFAULT_PROFILE)
        prof.update(raw_prof)
        channel.profile = prof
        with self._lock:
            self._channels[cid] = channel
        logger.info(f"[Channel] 注册通道 {cid} ({getattr(channel, 'kind', '?')}) "
                    f"profile: 回复上限={prof['max_reply_chars']} "
                    f"Markdown清洗={prof['strip_markdown']}")
        return channel

    def set_turn_runner(self, fn):
        """注入认知管线入口：fn(text, meta) -> dict（至少含 answer 字段）。"""
        self._turn_runner = fn

    def set_observer(self, fn):
        """注入观察者通知钩子：他人消息入队时同步知会（记日志/前端）。
        2026-09-21 起它**不再是吞掉消息的旁路**——消息照常进统一入口。"""
        self._observer = fn

    def set_busy_check(self, fn):
        """注入"管线是否正忙"判断（如 Web 回合进行中）：忙时把消息留回队列。"""
        self._busy_check = fn

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="channel-hub", daemon=True)
        self._thread.start()
        logger.info(f"[Channel] 通道中枢已启动（{len(self._channels)} 个通道，"
                    f"轮询 {self.poll_interval_s}s）")

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as e:
                logger.warning(f"[Channel] 轮询异常: {e}")
            self._stop.wait(self.poll_interval_s)

    # ── 轮询（测试可直接调用，不依赖线程） ────────────────

    def poll_once(self, now: float = None) -> dict:
        now = time.time() if now is None else now
        fetched = accepted = answered = 0
        with self._lock:
            channels = list(self._channels.items())

        for cid, ch in channels:
            if not self._channel_enabled(ch):
                continue
            try:
                if not ch.available():
                    continue
            except Exception as e:
                logger.debug(f"[Channel] {cid} 可用性检查失败: {e}")
                continue
            try:
                messages = ch.fetch() or []
            except Exception as e:
                logger.warning(f"[Channel] {cid} 收取失败: {e}")
                continue
            self.stats["polled"] += len(messages)
            fetched += len(messages)

            for raw in messages:
                msg = self._normalize(cid, ch, raw, now)
                if msg is None:
                    continue
                key = msg["key"]
                if key in self._seen:
                    import fas_log
                    fas_log.get_logger(fas_log.INPUT).debug(
                        "input_filtered", "重复消息被过滤", source="channel",
                        channel=cid, sender=msg.get("sender"), reason="duplicate")
                    continue
                self._seen.append(key)

                if msg["kind"] == KIND_SELF:
                    # 唯一的无条件过滤：不是社交政策，是机械防回环
                    # （自己的话再喂回自己 → 自己回自己 → 无限循环）
                    self.stats["ignored_self"] += 1
                    import fas_log
                    fas_log.get_logger(fas_log.INPUT).debug(
                        "input_filtered", "自己的话防回环过滤", source="channel",
                        channel=cid, reason="self_echo",
                        text=fas_log.text(msg.get("text") or ""))
                    continue
                if msg["kind"] == KIND_OTHER:
                    # 他人消息：先记入观察簿（前端/日志可见），再照常入队。
                    # "只观察"从今往后是**认知的决定**（沉默候选），
                    # 不是通道把消息吞掉的决定。
                    self.stats["routed_other"] += 1
                    with self._lock:
                        self._others.append(msg)
                    if self._observer:
                        try:
                            self._observer(msg)
                        except Exception as e:
                            logger.debug(f"[Channel] 观察者异常: {e}")

                with self._lock:
                    self._enqueue(msg, now)
                accepted += 1
                self.stats["accepted"] += 1

        # 处理队列（认知回合在这里执行）
        while True:
            with self._lock:
                if not self._queue:
                    break
                msg, at = self._queue.popleft()
            age = now - at
            if age > self.max_age_s:
                self.stats["dropped_old"] += 1
                self._record("dropped", msg, f"过期 {age:.0f}s 未处理（积压）")
                import fas_log
                fas_log.get_logger(fas_log.INPUT).warning(
                    "input_dropped", f"队列积压过期 {age:.0f}s 未进认知",
                    source="channel", channel=msg.get("channel"),
                    sender=msg.get("sender"), reason="queue_age")
                continue
            if self._run_turn(msg):
                answered += 1
            # 一轮只处理一条：认知回合本身要几秒到几十秒，不在这里连续阻塞
            break

        return {"fetched": fetched, "accepted": accepted, "answered": answered,
                "queue": len(self._queue), "stats": dict(self.stats)}

    # ── 内部：归一化 / 策略 / 回合 / 回复 ────────────────

    def _channel_enabled(self, ch) -> bool:
        return bool(getattr(ch, "enabled", True))

    def _normalize(self, cid, ch, raw, now):
        """把通道给的原始消息归一化：{channel, sender, kind, text, ts, key}。"""
        text = str((raw or {}).get("text", "")).strip()
        if not text:
            return None
        sender = str((raw or {}).get("sender", "") or "").strip()
        try:
            kind = ch.classify(raw) if hasattr(ch, "classify") else KIND_USER
        except Exception:
            kind = KIND_USER
        if kind not in (KIND_USER, KIND_OTHER, KIND_SELF, KIND_SYSTEM):
            kind = KIND_USER
        key = (raw or {}).get("key")
        if not key:
            # 没有序号时用 (通道, 发送者, 文本, 时间) 兜底去重
            key = f"{cid}|{sender}|{text}|{(raw or {}).get('time', '')}"
        if len(key) > 200:
            key = hashlib.sha1(key.encode("utf-8")).hexdigest()
        mention = False
        if kind == KIND_OTHER and hasattr(ch, "is_mentioned"):
            # 点名检测 = 环境事实（"有人喊了她的名字"），不是回复触发器
            try:
                mention = bool(ch.is_mentioned(text))
            except Exception:
                mention = False
        return {"channel": cid, "sender": sender, "kind": kind, "text": text,
                "ts": (raw or {}).get("time"), "key": f"{cid}:{key}",
                "mention": mention}

    @staticmethod
    def _priority(msg) -> int:
        """排队优先级（**调度**层的工程约束，不是认知裁决）：
        user > 被点名 > 其他。mention 只是注意力线索——它决定谁先被
        认知看到，绝不决定谁会收到回复。"""
        if msg.get("kind") == KIND_USER:
            return 0
        if msg.get("mention"):
            return 1
        return 2

    def _enqueue(self, msg, now):
        prio = self._priority(msg)
        if len(self._queue) >= self.max_queue:
            # 满了丢**最低优先级段**的末位（队列按优先级有序，尾部即最低段）；
            # 不再像纯 FIFO 那样丢队头——队头现在可能是用户消息。
            self._queue.pop()
            self.stats["dropped_full"] += 1
        idx = len(self._queue)
        for i, (m, _at) in enumerate(self._queue):
            if self._priority(m) > prio:
                idx = i
                break
        self._queue.insert(idx, (msg, now))

    def _run_turn(self, msg) -> bool:
        started = time.time()
        if self._busy_check is not None:
            try:
                if self._busy_check():
                    # 管线正忙（例如 Web 端正在处理一轮）→ 放回队列，下一轮再试；
                    # 太旧的会在 poll 里按 max_age 丢掉，不会无限等待
                    with self._lock:
                        self._queue.appendleft((msg, time.time()))
                    return False
            except Exception:
                pass
        if self._turn_runner is None:
            self._record("no_runner", msg, "未注入认知管线入口")
            return False
        try:
            result = self._turn_runner(msg["text"], {
                "channel": msg["channel"], "sender": msg["sender"],
                "kind": msg["kind"], "mention": msg.get("mention", False)}) or {}
        except Exception as e:
            self.stats["turn_failed"] += 1
            self._record("turn_failed", msg, f"认知管线异常: {e}")
            logger.warning(f"[Channel] 回合失败（{msg['channel']}）: {e}")
            return False

        answer = str(result.get("answer") or "").strip()
        meta = {"sender": msg["sender"], "channel": msg["channel"],
                "duration_s": round(time.time() - started, 2),
                "cycle": result.get("cycle_id")}
        # 观测：通道回合结果（cycle 回指认知管线，trace 在同线程已串联）
        import fas_log
        fas_log.get_logger(fas_log.INPUT).info(
            "channel_turn_finished",
            f"通道回合完成 {msg['channel']}←{msg.get('sender')}",
            source="channel", channel=msg["channel"], sender=msg.get("sender"),
            kind=msg.get("kind"), cycle_id=result.get("cycle_id"),
            behavior=str(result.get("behavior") or ""),
            has_answer=bool(answer), duration_s=meta["duration_s"])
        if not answer:
            if str(result.get("behavior") or "") == "silence":
                # 认知**选择了**沉默（dialogue_decide 的合法候选胜出）——
                # 如实记录为"决定不回应"，与"管线没给回答"区分开。
                self.stats["silence"] += 1
                self._record("silence", msg, "认知决定不回应（未发送）")
            else:
                self._record("no_answer", msg, "管线返回空回答（未发送）")
            return False

        ch = self._channels.get(msg["channel"])
        ok, detail = False, "通道不存在"
        if ch is not None:
            try:
                ok, detail = self._deliver(ch, answer, meta)
            except Exception as e:
                ok, detail = False, f"发送异常: {e}"
        if not ok:
            self.stats["send_failed"] += 1
        else:
            self.stats["answered"] += 1
        self._record("replied" if ok else "send_failed", msg,
                     f"{detail} | 回复: {answer[:60]}")
        return bool(ok)

    @staticmethod
    def _deliver(ch, answer, meta):
        """按通道画像加工回复再发送（聊天框不适合 Markdown / 长段落）。"""
        prof = getattr(ch, "profile", {}) or {}
        text = answer
        if prof.get("strip_markdown", True):
            text = _strip_markdown(text)
        limit = int(prof.get("max_reply_chars", 200) or 200)
        if len(text) > limit:
            text = text[:limit - 1].rstrip() + "…"
        res = ch.send(text, meta) or {}
        ok = bool(res.get("ok"))
        return ok, str(res.get("detail") or ("已送达" if ok else "发送失败"))

    def _record(self, kind, msg, detail):
        entry = {"ts": time.strftime("%Y/%m/%d %H:%M:%S"), "kind": kind,
                 "channel": msg.get("channel"), "sender": msg.get("sender"),
                 "text": (msg.get("text") or "")[:120], "detail": str(detail)[:160]}
        with self._lock:
            self._recent.append(entry)
        logger.info(f"[Channel] {kind} {msg.get('channel')} ← {msg.get('sender')}: {detail}")

    # ── 观测 ──────────────────────────────────────────────

    def recent(self, n: int = 20) -> list:
        with self._lock:
            return list(self._recent)[-n:]

    def other_messages(self, n: int = 20) -> list:
        """世界里别人说过的话（观察簿；它们同时也进了认知管线）。"""
        with self._lock:
            return list(self._others)[-n:]

    def state(self) -> dict:
        with self._lock:
            channels = []
            for cid, ch in self._channels.items():
                try:
                    avail = bool(ch.available())
                except Exception:
                    avail = False
                channels.append({
                    "id": cid, "kind": getattr(ch, "kind", "?"),
                    "enabled": self._channel_enabled(ch), "available": avail,
                    "profile": dict(getattr(ch, "profile", {}) or {}),
                })
            return {"channels": channels, "stats": dict(self.stats),
                    "queue": len(self._queue), "poll_interval_s": self.poll_interval_s,
                    "recent": list(self._recent)[-20:],
                    "other_messages": list(self._others)[-10:]}

    def set_enabled(self, channel_id: str, enabled: bool) -> dict:
        ch = self._channels.get(channel_id)
        if ch is None:
            return {"ok": False, "error": f"通道不存在: {channel_id}"}
        ch.enabled = bool(enabled)
        logger.info(f"[Channel] {channel_id} → {'启用' if enabled else '停用'}")
        return {"ok": True, "id": channel_id, "enabled": bool(enabled)}

    # ── 主动投递（B5/§5：CI 的话经由这里出口，不绕过通道层）──────

    def send_text(self, channel_id: str, text: str) -> dict:
        """把一句话主动发到指定通道（发送者是她自己，不是对入站的回复）。

        与 _run_turn 的区别只在语义；清洗（strip_markdown/截断）复用
        _deliver 同一份画像逻辑。**出口单点节流**：同通道同文在去重窗口内、
        或距上次外发不足最小间隔 → 拒发（reason="throttled"），让上游如实
        失败，绝不"没送也报送"。通道不存在/停用 → reason 区分，由调用方
        决定兜底（embodiment 只在通道不可用时退回 bridge.say）。
        返回 {ok, reason?, detail}；本函数永不抛异常。
        """
        ch = self._channels.get(channel_id)
        if ch is None:
            return {"ok": False, "reason": "channel_unavailable",
                    "detail": f"通道不存在: {channel_id}"}
        if not self._channel_enabled(ch):
            return {"ok": False, "reason": "channel_disabled",
                    "detail": "通道已停用（UI 开关）"}
        raw = str(text or "").strip()
        if not raw:
            return {"ok": False, "reason": "empty", "detail": "空文本不发送"}
        prof = getattr(ch, "profile", {}) or {}
        clean = _strip_markdown(raw) if prof.get("strip_markdown", True) else raw
        limit = int(prof.get("max_reply_chars", 200) or 200)
        if len(clean) > limit:
            clean = clean[:limit - 1].rstrip() + "…"
        now = time.time()
        min_gap = float(self.config.get("channel_send_min_interval_s", 6.0))
        dedup_win = float(self.config.get("channel_send_dedup_window_s", 180.0))
        with self._lock:
            st = self._out_state.setdefault(channel_id,
                                            {"last_ts": 0.0, "last_text": ""})
            if (st["last_text"] == clean and now - st["last_ts"] < dedup_win) \
                    or now - st["last_ts"] < min_gap:
                self.stats["throttled"] += 1
                throttled = True
            else:
                st["last_ts"] = now
                st["last_text"] = clean
                throttled = False
        if throttled:
            self._record("throttled", {"channel": channel_id, "sender": "self",
                                       "text": clean[:120]},
                         "同文去重或最小间隔未到（防刷屏）")
            return {"ok": False, "reason": "throttled",
                    "detail": "同文去重或最小间隔未到（防刷屏）"}
        try:
            ok, detail = self._deliver(ch, clean,
                                       {"source": "self_expression",
                                        "channel": channel_id})
        except Exception as e:
            ok, detail = False, f"发送异常: {e}"
        self.stats["sent" if ok else "send_failed"] += 1
        self._record("sent" if ok else "send_failed",
                     {"channel": channel_id, "sender": "self",
                      "text": clean[:120]}, detail)
        return {"ok": bool(ok), "detail": detail}


def _strip_markdown(text: str) -> str:
    """聊天框里 Markdown 只会变成噪声：去掉常见标记、压平换行。"""
    out = []
    for line in str(text).splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(("#", ">", "- ", "* ", "• ")):
            s = s.lstrip("#>*-• ").strip()
        out.append(s)
    s = " ".join(out)
    for token in ("**", "__", "`", "~~"):
        s = s.replace(token, "")
    return s.strip()
