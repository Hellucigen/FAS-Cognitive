# state_monitors.py — 状态检测器（State Monitor）
# ============================================================================
# 检测器维护"某个认知对象当前是什么状态"，并在状态**发生变化**时产生结构化
# 状态事件。它不执行动作、不调用 LLM、不替代认知循环——只做四件事：
#   获取/接收状态 → 维护当前状态 → 识别变化 → 生成事件交给触发器
#
# 状态与事件是两件事（§5.3）：
#   状态：对象现在是什么（monitor.current_value，可持久化、可查询）
#   事件：状态发生了什么变化（state_changed，带 previous/current/delta）
#   相同状态重复提交 **不产生事件**（这正是去重要求），但会更新 updated_at。
#
# 状态不强行塞进知识图谱（§2.1）：检测器状态是随时间变化的时序量，
# 图谱只承载"认知内容"。需要进图谱的内容由触发器动作显式声明（见
# cognitive_triggers.py 的 action.type=activate_nodes）。
#
# 数据来源（source）与运行方式（mode）：
#   mode=event    外部事件到来时由 API/模块提交（本阶段主用）
#   mode=manual   前端或其它模块显式提交
#   mode=poll     由调度器定期查询（接口已预留：register_poller + due()，
#                 本阶段不内置任何轮询线程/数据源，避免绑定具体应用）
# ============================================================================

import logging
import threading
import time

from graph_model import now_str
from json_store import load_json, atomic_write_json

logger = logging.getLogger(__name__)

STATE_TYPES = ("number", "bool", "string", "enum", "object")
MODES = ("event", "manual", "poll")
SOURCES = ("event", "manual", "poll", "perception", "tool", "external")
DEFAULT_EPSILON = 1e-9
CHANGE_HISTORY_LIMIT = 50      # 每个检测器保留的最近变化条数
EVENT_RING_LIMIT = 200         # 全局事件环大小（供触发器与前端查看）


def _coerce(value, state_type: str):
    """按声明的状态类型转换输入；失败抛 ValueError（由调用方记为错误）。"""
    if state_type == "number":
        if isinstance(value, bool):
            raise ValueError("bool 不是 number")
        return float(value)
    if state_type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "1", "yes", "y", "on", "是", "真"):
                return True
            if low in ("false", "0", "no", "n", "off", "否", "假"):
                return False
        raise ValueError(f"无法把 {value!r} 解释为 bool")
    if state_type in ("string", "enum"):
        return str(value)
    return value                      # object：原样存（json 可序列化由前端负责）


def _validate_schema(value, monitor: dict):
    """按 monitor.schema 校验（min/max/enum）；返回错误字符串或 None。"""
    schema = monitor.get("schema") or {}
    state_type = monitor.get("state_type", "number")
    if state_type == "number":
        lo, hi = schema.get("min"), schema.get("max")
        if lo is not None and value < float(lo):
            return f"{value} < schema.min({lo})"
        if hi is not None and value > float(hi):
            return f"{value} > schema.max({hi})"
    if state_type == "enum":
        allowed = schema.get("enum")
        if allowed and value not in allowed:
            return f"{value!r} 不在 schema.enum({allowed}) 内"
    return None


class MonitorRegistry:
    """检测器注册表：状态维护 + 变化识别 + 事件分发。"""

    def __init__(self, path: str = "data/state_monitors.json", kg=None):
        self.path = path
        self.kg = kg
        self._lock = threading.RLock()
        self._monitors: dict[str, dict] = {}
        self._seq = 0
        self._events: list[dict] = []      # 事件环（最近 EVENT_RING_LIMIT 条）
        self._event_seq = 0
        self._subscribers: list = []       # fn(event) —— 协调器订阅
        self._pollers: dict = {}           # monitor_id → fn() -> value（未来数据源）
        self._load()

    # ── 持久化 ────────────────────────────────────────────

    def _load(self):
        data = load_json(self.path, default=None) or {}
        with self._lock:
            self._seq = int(data.get("seq", 0) or 0)
            self._event_seq = int(data.get("event_seq", 0) or 0)
            self._monitors = {}
            for item in data.get("monitors", []) or []:
                if isinstance(item, dict) and item.get("id"):
                    self._monitors[item["id"]] = self._normalize(item)
            self._events = list(data.get("events", []) or [])[-EVENT_RING_LIMIT:]
        if self._monitors:
            logger.info(f"[StateMonitor] 加载 {len(self._monitors)} 个检测器"
                        f"（状态随文件恢复，变化历史一并恢复）")

    def _save(self):
        with self._lock:
            payload = {
                "version": 1,
                "seq": self._seq,
                "event_seq": self._event_seq,
                "monitors": list(self._monitors.values()),
                "events": self._events[-EVENT_RING_LIMIT:],
            }
        atomic_write_json(self.path, payload)

    @staticmethod
    def _normalize(item: dict) -> dict:
        return {
            "id": str(item.get("id")),
            "name": item.get("name", ""),
            "description": item.get("description", ""),
            "target_type": item.get("target_type", "external"),
            "target_id": str(item.get("target_id", "")),
            "state_type": item.get("state_type", "number"),
            "unit": item.get("unit", ""),
            "schema": item.get("schema") or {},
            "epsilon": item.get("epsilon"),
            "current_value": item.get("current_value"),
            "previous_value": item.get("previous_value"),
            "updated_at": item.get("updated_at"),
            "last_changed_at": item.get("last_changed_at"),
            "source": item.get("source", "manual"),
            "mode": item.get("mode", "event"),
            "poll_interval_s": float(item.get("poll_interval_s", 0) or 0),
            "enabled": bool(item.get("enabled", True)),
            "confidence": item.get("confidence"),
            "observation_count": int(item.get("observation_count", 0) or 0),
            "last_error": item.get("last_error"),
            "changes": list(item.get("changes", []) or [])[-CHANGE_HISTORY_LIMIT:],
            "created_at": item.get("created_at") or now_str(),
            "metadata": item.get("metadata") or {},
        }

    def _audit_change(self, monitor: dict, event: dict):
        monitor["changes"].append({
            "ts": event["ts"],
            "previous": event.get("previous"),
            "current": event.get("current"),
            "delta": event.get("delta"),
            "event_id": event["event_id"],
        })
        if len(monitor["changes"]) > CHANGE_HISTORY_LIMIT:
            monitor["changes"] = monitor["changes"][-CHANGE_HISTORY_LIMIT:]

    # ── 订阅与数据源 ──────────────────────────────────────

    def subscribe(self, fn):
        """注册事件订阅者 fn(event)。订阅者异常不影响检测器本体。"""
        with self._lock:
            if fn not in self._subscribers:
                self._subscribers.append(fn)

    def register_poller(self, monitor_id: str, fn):
        """为 mode=poll 的检测器登记数据源（未来扩展点，本阶段可不用）。"""
        with self._lock:
            self._pollers[monitor_id] = fn

    def due(self, now: float = None) -> list:
        """到期待轮询的检测器 id 列表（供调度器调用；无数据源则不会被调用）。"""
        now = time.time() if now is None else now
        out = []
        with self._lock:
            for mid, mon in self._monitors.items():
                if not mon.get("enabled") or mon.get("mode") != "poll":
                    continue
                interval = float(mon.get("poll_interval_s") or 0)
                if interval <= 0 or mid not in self._pollers:
                    continue
                last = mon.get("updated_at_epoch") or 0
                if now - float(last) >= interval:
                    out.append(mid)
        return out

    def poll_due(self, now: float = None) -> list:
        """执行到期轮询：调用已登记的数据源并提交状态。

        本阶段没有内置数据源（不绑定具体应用）；没有 register_poller 的
        poll 型检测器会被静默跳过——接口在此，接线留给未来。
        """
        results = []
        for mid in self.due(now):
            poller = self._pollers.get(mid)
            if poller is None:
                continue
            try:
                value = poller()
            except Exception as e:
                results.append({"monitor_id": mid, "ok": False, "error": str(e)})
                self.record_error(mid, f"poller 异常: {e}")
                continue
            if value is None:
                continue
            results.append(self.observe(mid, value, source="poll"))
        return results

    # ── 状态更新（唯一入口） ──────────────────────────────

    def observe(self, monitor_id: str, value, source: str = "event",
                confidence=None, ts: float = None) -> dict:
        """提交一次观测。返回 {ok, accepted, changed, event?}。

        - 检测器不存在 → ok=False
        - 检测器已禁用 → accepted=False（禁用后不再产生任何新的检测结果）
        - 输入不合法 → ok=False + 记录 last_error（不污染 current_value）
        - 值未变化（含浮点容差）→ changed=False，**不产生事件**（去重）
        - 值变化 → 更新状态 + 生成 state_changed 事件 + 通知订阅者
        """
        now = time.time() if ts is None else ts
        with self._lock:
            mon = self._monitors.get(monitor_id)
            if mon is None:
                return {"ok": False, "error": f"检测器不存在: {monitor_id}"}
            if not mon.get("enabled"):
                return {"ok": True, "accepted": False, "changed": False,
                        "reason": "检测器已禁用"}

            try:
                new_value = _coerce(value, mon.get("state_type", "number"))
            except (TypeError, ValueError) as e:
                mon["last_error"] = f"输入非法: {e}"
                mon["updated_at"] = now_str()
                self._save()
                return {"ok": False, "accepted": False, "changed": False,
                        "error": str(e)}

            schema_err = _validate_schema(new_value, mon)
            if schema_err:
                mon["last_error"] = f"越界: {schema_err}"
                mon["updated_at"] = now_str()
                self._save()
                return {"ok": False, "accepted": False, "changed": False,
                        "error": f"违反 schema: {schema_err}"}

            old_value = mon.get("current_value")
            changed = self._values_differ(old_value, new_value, mon)

            mon["observation_count"] = int(mon.get("observation_count", 0)) + 1
            mon["updated_at"] = now_str()
            mon["updated_at_epoch"] = now
            mon["source"] = source if source in SOURCES else "event"
            mon["last_error"] = None
            if confidence is not None:
                mon["confidence"] = confidence

            if not changed:
                # 状态没变：只刷新观测时间，不产生事件（去重语义）
                self._save()
                return {"ok": True, "accepted": True, "changed": False,
                        "current": new_value}

            event = self._make_event(mon, old_value, new_value, now)
            mon["previous_value"] = old_value
            mon["current_value"] = new_value
            mon["last_changed_at"] = event["ts"]
            self._audit_change(mon, event)
            self._events.append(event)
            if len(self._events) > EVENT_RING_LIMIT:
                self._events = self._events[-EVENT_RING_LIMIT:]
            subscribers = list(self._subscribers)

        self._save()
        logger.info(f"[StateMonitor] {monitor_id} 状态变化: "
                    f"{event.get('previous')} → {event.get('current')}")

        # 订阅者在锁外调用（触发器可能反向读检测器，避免自锁）
        for fn in subscribers:
            try:
                fn(event)
            except Exception as e:
                logger.warning(f"[StateMonitor] 订阅者异常: {e}")
        return {"ok": True, "accepted": True, "changed": True, "event": event,
                "current": new_value}

    @staticmethod
    def _values_differ(old, new, monitor: dict) -> bool:
        """变化判定：浮点用容差，其余用相等。"""
        if old is None:
            return True
        if isinstance(old, (int, float)) and isinstance(new, (int, float)) \
                and not isinstance(old, bool) and not isinstance(new, bool):
            eps = monitor.get("epsilon")
            if eps is None:
                eps = (monitor.get("schema") or {}).get("precision", DEFAULT_EPSILON)
            return abs(float(new) - float(old)) > float(eps)
        return old != new

    def _make_event(self, monitor: dict, old, new, now: float) -> dict:
        with self._lock:
            self._event_seq += 1
            event_id = f"evt_{self._event_seq}"
        delta = None
        if isinstance(old, (int, float)) and isinstance(new, (int, float)) \
                and not isinstance(old, bool) and not isinstance(new, bool):
            delta = float(new) - float(old)
        return {
            "event_id": event_id,
            "type": "state_changed",
            "monitor_id": monitor["id"],
            "monitor_name": monitor.get("name", ""),
            "target_type": monitor.get("target_type"),
            "target_id": monitor.get("target_id"),
            "state_type": monitor.get("state_type"),
            "unit": monitor.get("unit", ""),
            "previous": old,
            "current": new,
            "delta": delta,
            "source": monitor.get("source"),
            "ts": now_str(),
            "ts_epoch": now,
        }

    def record_error(self, monitor_id: str, message: str):
        """记录检测器级错误（数据源异常等）。"""
        with self._lock:
            mon = self._monitors.get(monitor_id)
            if mon is None:
                return
            mon["last_error"] = str(message)
            mon["updated_at"] = now_str()
        self._save()

    # ── CRUD ──────────────────────────────────────────────

    def create(self, monitor_id: str = None, name: str = "", target_type: str = "external",
               target_id: str = "", state_type: str = "number", unit: str = "",
               mode: str = "event", poll_interval_s: float = 0, enabled: bool = True,
               schema: dict = None, epsilon=None, description: str = "",
               metadata: dict = None) -> dict:
        if state_type not in STATE_TYPES:
            return {"ok": False, "error": f"state_type 必须是 {STATE_TYPES}"}
        if mode not in MODES:
            return {"ok": False, "error": f"mode 必须是 {MODES}"}
        with self._lock:
            if not monitor_id:
                self._seq += 1
                monitor_id = f"mon_{self._seq}"
            if monitor_id in self._monitors:
                return {"ok": False, "error": f"检测器已存在: {monitor_id}"}
            mon = self._normalize({
                "id": monitor_id,
                "name": name or monitor_id,
                "description": description,
                "target_type": target_type,
                "target_id": target_id,
                "state_type": state_type,
                "unit": unit,
                "schema": schema or {},
                "epsilon": epsilon,
                "mode": mode,
                "poll_interval_s": poll_interval_s,
                "enabled": enabled,
                "metadata": metadata or {},
            })
            self._monitors[monitor_id] = mon
        self._save()
        logger.info(f"[StateMonitor] + {monitor_id} "
                    f"({state_type}, mode={mode}, target={target_type}:{target_id})")
        return {"ok": True, "monitor": mon}

    def update(self, monitor_id: str, **fields) -> dict:
        with self._lock:
            mon = self._monitors.get(monitor_id)
            if mon is None:
                return {"ok": False, "error": f"检测器不存在: {monitor_id}"}
            for key in ("name", "description", "target_type", "target_id", "unit",
                        "schema", "epsilon", "mode", "poll_interval_s", "enabled",
                        "metadata"):
                if key in fields and fields[key] is not None:
                    mon[key] = fields[key]
            if "state_type" in fields and fields["state_type"]:
                if fields["state_type"] not in STATE_TYPES:
                    return {"ok": False, "error": f"state_type 必须是 {STATE_TYPES}"}
                mon["state_type"] = fields["state_type"]
            if mon.get("mode") not in MODES:
                return {"ok": False, "error": f"mode 必须是 {MODES}"}
            mon["enabled"] = bool(mon.get("enabled", True))
        self._save()
        return {"ok": True, "monitor": mon}

    def set_enabled(self, monitor_id: str, enabled: bool) -> dict:
        return self.update(monitor_id, enabled=bool(enabled))

    def delete(self, monitor_id: str) -> dict:
        with self._lock:
            mon = self._monitors.pop(monitor_id, None)
            self._pollers.pop(monitor_id, None)
            if mon is None:
                return {"ok": False, "error": f"检测器不存在: {monitor_id}"}
        self._save()
        logger.info(f"[StateMonitor] - {monitor_id}")
        return {"ok": True, "deleted": monitor_id}

    # ── 查询 ──────────────────────────────────────────────

    def get(self, monitor_id: str) -> dict:
        with self._lock:
            mon = self._monitors.get(monitor_id)
            return dict(mon) if mon else None

    def list(self, target_id: str = None, target_type: str = None) -> list:
        """全量或按 target_id / target_type 过滤（与锁注册表 list 同名同义）。"""
        with self._lock:
            mons = [dict(m) for m in self._monitors.values()]
        if target_id is not None:
            mons = [m for m in mons if m.get("target_id") == target_id]
        if target_type is not None:
            mons = [m for m in mons if m.get("target_type") == target_type]
        return mons

    def recent_events(self, n: int = 20) -> list:
        with self._lock:
            return list(self._events[-n:])

    def state(self) -> dict:
        with self._lock:
            monitors = [dict(m) for m in self._monitors.values()]
            events = list(self._events[-20:])
        return {
            "count": len(monitors),
            "enabled_count": sum(1 for m in monitors if m.get("enabled")),
            "error_count": sum(1 for m in monitors if m.get("last_error")),
            "monitors": monitors,
            "recent_events": events,
        }
