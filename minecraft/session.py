# minecraft.session.py — Minecraft 世界接入生命周期（Phase C Step 3）
# ============================================================================
# 会话状态机（规范 4.4）：单图节点 Minecraft会话（self 空间，in-place 更新，
# 不逐轮建节点）。端口必须来自用户明确输入——不猜测、不扫描。
#
# 2026-09-21 重构（用户设计准则："输入→正则→行为"枚举禁令）：
#   原 handle_reply（PORT_RE/CANCEL_RE/REUSE_RE 字符串表）已删除。
#   "等待端口时这轮输入算什么"的裁决搬进了动作概念层：
#   action_resolver.resolve_minecraft_intent 读图谱节点上的槽位事实
#   （state=awaiting_port），据此把数字解读为端口填槽、把显式否定
#   （NEGATION_RE 单一真源）解读为取消、把"刚才那个"解读为对节点
#   last_port 的回指；其余输入就是继续等待。本模块只剩两件事：
#   状态机（进/退状态）+ 连接工程（config 写入、bot 重启、桥等待）。
#
# 状态：disconnected → requesting_port → awaiting_port → connecting
#       → connected / failed / cancelled
# ============================================================================

import json
import logging
import os
import subprocess
import time

from graph_model import Node, Edge, now_str

logger = logging.getLogger(__name__)

BOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot")
CONFIG_PATH = os.path.join(BOT_DIR, "config.json")
BRIDGE = "http://127.0.0.1:5010"

STATES = ("disconnected", "requesting_port", "awaiting_port", "connecting",
          "connected", "failed", "cancelled")


def _bridge_up() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(BRIDGE + "/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


class MinecraftSession:
    """Minecraft 世界接入生命周期。单图节点 in-place 状态机。"""

    def __init__(self, kg, bridge_get_state=None, ensure_bot_process=None,
                 restart_bot_process=None):
        self.kg = kg
        self._get_state = bridge_get_state or (lambda: None)
        self._ensure_bot = ensure_bot_process or (lambda: True)
        # 换世界专用：bot.js 只在启动时读 config，连接到**新端口**必须重启
        # bot 进程（旧进程会永远重试旧端口）。缺省退化为 _ensure_bot。
        self._restart_bot = restart_bot_process or self._ensure_bot
        self.last_port = None
        self.on_connected = None       # 后台连接成功回调（app 注入）
        self.on_failed = None          # 失败回调
        self._watching = False
        self._ensure_node()

    # ── 图节点 ────────────────────────────────────────────

    def _ensure_node(self):
        with self.kg._lock:
            n = self.kg.nodes.get("Minecraft会话")
            if n is None:
                self.kg.add_node(Node(
                    id="Minecraft会话", weight=0.6, label="declarative-semantic",
                    graph_space="self",
                    extra_attrs={"type": "mc_session", "canon": "cognitive_state",
                                 "state": "disconnected", "bot_name": "Haru"}))
                self.kg.add_edge(Edge(src="Self", dst="Minecraft会话",
                                      relation="意图", weight=0.5,
                                      relation_category="cognitive_relation"))

    def _set(self, state, **extra):
        self._ensure_node()
        _prev = self.get_state()
        with self.kg._lock:
            n = self.kg.nodes["Minecraft会话"]
            n.extra_attrs["state"] = state
            n.extra_attrs["updated"] = now_str()
            for k, v in extra.items():
                n.extra_attrs[k] = v
            n.touch()
        # 观测：只在状态真变化时发一条（会话生命周期，§14）
        if state != _prev:
            import fas_log
            fas_log.get_logger(fas_log.MINECRAFT).info(
                "session_state", f"Minecraft 会话 {_prev} → {state}",
                old=_prev, new=state, **{k: v for k, v in extra.items()
                                         if isinstance(v, (int, float, str, bool, type(None)))})

    def get_state(self) -> str:
        self._ensure_node()
        return self.kg.nodes["Minecraft会话"].extra_attrs.get("state", "disconnected")

    def on_bridge_lost(self, reason: str = "bridge_unreachable") -> bool:
        """桥活性检测器报告失联（§18.2 #7）：connected → disconnected 回写。

        只翻转"宣称连着"的状态；awaiting/connecting 等期由各自回调与
        用户指令处理，桥健康抖动不越过对话裁决直接改它们。返回是否
        真发生翻转（订阅方据此决定是否记事件，重复 False 不刷屏）。
        """
        if self.get_state() != "connected":
            return False
        self._set("disconnected", lost_reason=reason)
        logger.info(f"[MCSession] 桥失联回写：connected → disconnected（{reason}）")
        return True

    def summary(self) -> dict:
        self._ensure_node()
        ea = self.kg.nodes["Minecraft会话"].extra_attrs
        return {"state": ea.get("state"), "port": ea.get("port"),
                "last_port": self.last_port, "error": ea.get("error")}

    # ── 动作：请求端口 ────────────────────────────────────

    def request_port(self) -> dict:
        # 槽位待填 + 可复用值都写成节点上的**事实**：下一轮输入的解释
        # （数字=端口 / "刚才那个"=last_port 回指）由 action_resolver
        # 读图裁决，这里只负责把记忆放进图谱。
        self._set("awaiting_port", last_port=self.last_port)
        return {"success": True, "action": "request_minecraft_port",
                "request_message": "我想进入你的 Minecraft 世界。请把\"对局域网开放\"后显示的端口号告诉我。",
                "state": "awaiting_port"}

    def cancel_awaiting(self, reason: str = "") -> dict:
        """显式取消进入（否定语义由 resolver 的 NEGATION_RE 单一真源裁决，
        这里只是执行状态迁移——不再是"等一下/还没开"字符串表的分支）。"""
        self._set("cancelled", cancel_reason=reason or "user_negation")
        return {"success": True, "action": "cancel_minecraft_entry",
                "cancelled": True, "message": "好的，先不进了。"}

    # ── 连接动作 ──────────────────────────────────────────

    def connect(self, port: int) -> dict:
        self._set("connecting", port=port)
        if not isinstance(port, int) or not (1024 <= port <= 65535):
            self._set("failed", error="invalid_port", port=port)
            return {"success": False, "reason": "invalid_port", "port": port}
        try:
            cfg = {"host": "127.0.0.1", "port": port, "username": "Haru",
                   "auth": "offline", "bridge_port": 5010}
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._set("failed", error=str(e))
            return {"success": False, "reason": "config_write_failed", "port": port}
        self.last_port = port
        # 连接 = 加入（可能换了）世界：重启 bot 进程读取最新 config。
        # 旧进程会永远重试旧端口（实测 54766 关闭后 ECONNREFUSED 循环），
        # 不重启就无法换服。重启由 app 的 _restart_mc_bot 完成（/quit + 重拉）。
        self._restart_bot()
        # 轮询连接结果（≤25s）
        import urllib.request
        deadline = time.time() + 25
        while time.time() < deadline:
            time.sleep(2)
            try:
                with urllib.request.urlopen(BRIDGE + "/state", timeout=2) as r:
                    st = json.loads(r.read().decode("utf-8"))
                if st.get("connected"):
                    self._set("connected", port=port,
                              position=st.get("position"))
                    return {"success": True, "action": "connect_minecraft",
                            "session_id": f"mc_{port}_{int(time.time())}",
                            "host": "127.0.0.1", "port": port, "bot_name": "Haru"}
                err = st.get("error") or ""
            except Exception:
                err = "bridge_down"
            if "Unsupported" in err or "No data" in err:
                break   # 协议不支持：重试无意义
        self._set("failed", error=err or "timeout", port=port)
        return {"success": False, "reason": err or "timeout", "port": port}

    # ── 后台连接（异步化改造 2026-09-20）──────────────────
    # 旧 connect() 在 /api/nlp 请求线程里轮询 25 秒（实测 bot 重启+握手
    # 要 20~26 秒）——一轮"来玩我的世界"被整个阻塞在接入上。connect_async
    # 只做"写配置"这件必须同步的事（配置是竞态敏感点），重启 bot 与等待
    # 桥就绪放后台线程；连上/失败经 on_connected/on_failed 回调进认知
    # （走向用户、会话节点更新、游戏内播报）。回合立刻拿到 pending 结果，
    # 语言层据证据状态说"正在连"，不说"进来了"。

    def connect_async(self, port: int, timeout_s: float = 45.0) -> dict:
        import threading
        self._set("connecting", port=port)
        if not isinstance(port, int) or not (1024 <= port <= 65535):
            self._set("failed", error="invalid_port", port=port)
            return {"success": False, "reason": "invalid_port", "port": port}
        try:
            cfg = {"host": "127.0.0.1", "port": port, "username": "Haru",
                   "auth": "offline", "bridge_port": 5010}
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._set("failed", error=str(e))
            return {"success": False, "reason": "config_write_failed", "port": port}
        self.last_port = port
        if getattr(self, "_watching", False):
            return {"success": True, "pending": True,
                    "action": "connect_minecraft", "port": port,
                    "note": "already_connecting"}

        def _watch():
            self._watching = True
            deadline = time.time() + timeout_s
            err = ""
            try:
                self._restart_bot()      # 后台：/quit 旧进程 + 重拉 + 等桥
                while time.time() < deadline:
                    try:
                        # 复用注入的桥状态读取（与 _get_state 同一扇门，可测）
                        st = self._get_state() or {}
                        if st.get("connected"):
                            self._set("connected", port=port,
                                      position=st.get("position"))
                            cb = getattr(self, "on_connected", None)
                            if cb:
                                try:
                                    cb({"success": True, "action": "connect_minecraft",
                                         "port": port, "session_id":
                                         f"mc_{port}_{int(time.time())}"})
                                except Exception:
                                    pass
                            return
                        err = st.get("error") or ""
                    except Exception:
                        err = "bridge_down"
                    if "Unsupported" in err or "No data" in err:
                        break
                    time.sleep(1.5)
                self._set("failed", error=err or "timeout", port=port)
                cbf = getattr(self, "on_failed", None)
                if cbf:
                    try:
                        cbf({"success": False, "reason": err or "timeout",
                             "port": port})
                    except Exception:
                        pass
            finally:
                self._watching = False

        threading.Thread(target=_watch, name="mc-session-connect",
                         daemon=True).start()
        return {"success": True, "pending": True,
                "action": "connect_minecraft", "port": port,
                "describe": f"正在连接 Minecraft 世界（端口 {port}）"}
