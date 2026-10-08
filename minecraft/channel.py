# minecraft.channel.py — 游戏内聊天作为一条对话通道
# ============================================================================
# 把"世界里的文字聊天"接进通用通道层（conversation_channel.ChannelHub）：
#   收：读 bot /state 的 chat 记录（带 seq 序号去重），识别谁在说话
#   发：把回复写回游戏聊天框（清洗 Markdown、限长、单行）
#
# 身份判定（只回答"这是谁说的"，不规定 FAS 必须怎么做）：
#   sender == 自己的 bot 名 → self（机械过滤：否则自己回自己，无限回环）
#   sender == 用户（配置里的 MC 名，默认 Hellucigen）→ user（照常进认知）
#   sender == 其他人 → other（**身份事实**随消息一起交给认知；是否回应由
#            统一管线里的 dialogue_decide 决定，"被点名"只是注意力线索）
#
# 换环境只需换 profile（纯环境约束：长度、Markdown、轮询、过期）。
# 本文件不含任何认知逻辑——它只负责收发与报告世界事实。
# ============================================================================

import logging

import minecraft.bridge as bridge
from conversation_channel import KIND_USER, KIND_OTHER, KIND_SELF, DEFAULT_PROFILE

logger = logging.getLogger(__name__)

DEFAULT_USER_NAMES = ("Hellucigen",)
DEFAULT_ALIASES = ("Haru", "哈鲁", "Fascinator", "fas")


class MinecraftChatChannel:
    """游戏内聊天通道适配器。"""

    id = "minecraft_chat"
    kind = "embodiment_chat"

    profile = {
        # 环境约束（这个场合的聊天框长什么样）：短、单行、不要 Markdown。
        # 2026-09-21 起 profile 里**没有**社交政策键——是否回应他人/回应点名
        # 由统一认知管线（dialogue_decide）决定，通道只报告事实。
        "max_reply_chars": 160,
        "strip_markdown": True,
        "poll_interval_s": 2.0,
        "max_age_s": 90,
    }

    def __init__(self, user_names=None, aliases=None, enabled: bool = True,
                 self_name: str = "Haru"):
        self.user_names = tuple(user_names or DEFAULT_USER_NAMES)
        # 别名是"她叫什么"，传入的与默认**合并**而不是覆盖：漏掉一个名字的
        # 后果是别人喊她她装没听见，比多认一个名字严重
        merged = list(DEFAULT_ALIASES) + [a for a in (aliases or []) if a]
        self.aliases = tuple(dict.fromkeys(merged))
        self.enabled = bool(enabled)
        self._last_seq = 0
        # 自己的名字一开始就要有：身份判定不能依赖"先 fetch 过一次 state"
        self.self_name = str(self_name or "Haru")
        self._self_name = self.self_name

    # ── 通道能力 ──────────────────────────────────────────

    def available(self) -> bool:
        state = bridge.get_state()
        if not state or not state.get("connected"):
            return False
        self._self_name = state.get("username") or self._self_name or self.self_name
        return True

    # 服务器系统消息（出现在玩家聊天流里，但不是任何人"说的话"）。
    # 不滤掉的话她会回复 "Teleported ... to ..." 这类系统回执（实测 bug）。
    import re as _re
    _SYSTEM_MSG_RE = _re.compile(
        r"^Teleported | joined the game| left the game| achieved |"
        r"^Unable to |^\[Server\]|^Server:|^You |^\\[\[[A-Za-z]")

    def fetch(self) -> list:
        """取未处理过的聊天消息（按 seq 去重，且不重复消费；滤系统消息）。"""
        state = bridge.get_state() or {}
        if not state.get("connected"):
            return []
        self._self_name = state.get("username") or self._self_name
        out = []
        for item in state.get("chat") or []:
            seq = item.get("seq")
            try:
                seq = int(seq)
            except (TypeError, ValueError):
                seq = None
            if seq is not None:
                if seq <= self._last_seq:
                    continue
            text = str(item.get("text") or "")
            if self._SYSTEM_MSG_RE.search(text):
                continue   # 系统回执：不入认知管线（seq 照常推进水位）
            out.append({"sender": item.get("sender"), "text": item.get("text"),
                        "time": item.get("time"), "seq": seq})
        # 记录水位（同一批里可能有乱序，取最大）
        seqs = [m["seq"] for m in out if m.get("seq") is not None]
        if seqs:
            self._last_seq = max(self._last_seq, max(seqs))
        return out

    # ── 身份与策略 ────────────────────────────────────────

    def classify(self, raw: dict) -> str:
        sender = str((raw or {}).get("sender") or "").strip()
        if sender and sender in (self._self_name, self.self_name):
            return KIND_SELF
        if sender in self.user_names:
            return KIND_USER
        if not sender:
            return KIND_OTHER
        return KIND_OTHER

    def is_mentioned(self, text: str) -> bool:
        """"有人喊 Haru"这一环境事实的检测器。它是交给认知的注意力线索
        （排队位置 + 事件字段），**不是**回复触发器——mention ≠ reply。"""
        t = str(text or "")
        names = tuple(self.aliases) + (self.self_name, self._self_name)
        return any(n and n in t for n in names)

    def message_key(self, raw: dict) -> str:
        seq = (raw or {}).get("seq")
        if seq is not None:
            return f"seq:{seq}"
        return f"{raw.get('time')}|{raw.get('sender')}|{raw.get('text')}"

    # ── 发送 ──────────────────────────────────────────────

    def send(self, text: str, meta: dict = None) -> dict:
        said = str(text or "").strip()
        if not said:
            return {"ok": False, "detail": "空回复不发送"}
        if not self.available():
            return {"ok": False, "detail": "bot 未连接，游戏内回复未送达"}
        if bridge.say(said):
            return {"ok": True, "detail": f"已发到游戏聊天: {said[:40]}"}
        return {"ok": False, "detail": "bridge.say 失败（游戏内回复未送达）"}

    # ── 观测 ──────────────────────────────────────────────

    def state(self) -> dict:
        return {"last_seq": self._last_seq, "self_name": self._self_name,
                "user_names": list(self.user_names),
                "aliases": list(self.aliases)}
