# cognitive_triggers.py — 触发器（Trigger）
# ============================================================================
# 数据流（§6.1）：
#   State Monitor → State/Event → Trigger Evaluation → Cognitive Event → 认知过程
#
# 触发器只负责**条件评估 + 生成认知事件**，不承担业务逻辑：动作由 action
# 声明（激活图谱节点 / 记录 / 通知），把"触发"和"做什么"分开。
#
# 条件是可验证的结构化表达式，不把自然语言留给 LLM 在运行时判断（§6.2）：
#   {"op": "gt",  "field": "current", "value": 37}
#   {"op": "changed"}
#   {"op": "eq",  "field": "current", "value": "low"}
#   {"op": "in",  "field": "current", "values": ["low", "empty"]}
#   {"op": "all", "conditions": [ ... ]}
#   {"op": "any", "conditions": [ ... ]}
#   {"op": "not", "condition": {...}}
#   字段（field）限定：current / previous / delta / observation_count / confidence
#   —— 只允许读事件与检测器状态里确实存在的量，不接受任意表达式求值。
#
# 生命周期（§6.3）：创建 → 启用/禁用 → 更新 → 删除 → 条件评估 → 触发 →
# 冷却 → 重复触发控制。要点：
#   fire_mode="edge"  条件从"不满足→满足"才触发（默认；记录上一轮条件结果，
#                     持久化，重启不会因状态残留而误触发）
#   fire_mode="level" 条件为真即可触发，但受 cooldown_s 约束
#   once=true         只触发一次（触发后自动停用，保留记录便于审计）
#   cooldown_s        冷却期内的触发请求被拒绝并记入日志（可见，不静默）
#
# LLM 介入分级（§10）落在 trigger.llm_mode：
#   0 = 纯确定性（默认）：触发只做动作，不进任何 LLM 通路
#   1 = 结构化辅助：允许在**创建/编辑时**把自然语言转成结构化条件
#       （nl_to_trigger，一次性调用，不参与运行时判断）
#   2 = 认知推理：触发事件带推理标记，可由认知层按需调用 LLM
#   运行时的条件评估永远是确定性的——llm_mode 不改这一点。
# ============================================================================

import json
import logging
import threading
import time

from graph_model import now_str
from json_store import load_json, atomic_write_json

logger = logging.getLogger(__name__)

FIRE_MODES = ("edge", "level")
LLM_MODES = (0, 1, 2)
OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "changed", "in", "all", "any", "not")
FIELDS = ("current", "previous", "delta", "observation_count", "confidence")
ACTION_TYPES = ("activate_nodes", "record", "notify")
LOG_LIMIT = 200


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def evaluate_condition(condition: dict, event: dict, monitor: dict = None) -> tuple:
    """评估结构化条件 → (是否满足, 说明文本)。异常一律视为不满足并带说明。"""
    if not isinstance(condition, dict):
        return False, "条件不是对象"
    op = str(condition.get("op", "")).strip()
    if op not in OPS:
        return False, f"未知操作符: {op!r}"

    if op in ("all", "any"):
        subs = condition.get("conditions") or []
        if not isinstance(subs, list) or not subs:
            return False, f"{op} 缺少子条件"
        results = [evaluate_condition(c, event, monitor) for c in subs]
        if op == "all":
            ok = all(r[0] for r in results)
        else:
            ok = any(r[0] for r in results)
        detail = "; ".join(f"{r[1]}={r[0]}" for r in results)
        return ok, f"{op}({detail})"

    if op == "not":
        ok, why = evaluate_condition(condition.get("condition") or {}, event, monitor)
        return (not ok), f"not({why})"

    field = str(condition.get("field", "current"))
    if field not in FIELDS:
        return False, f"未知字段: {field!r}"

    if op == "changed":
        # current != previous（类型不同也算变化）
        cur = event.get("current", (monitor or {}).get("current_value"))
        prev = event.get("previous", (monitor or {}).get("previous_value"))
        return (cur != prev), f"changed({prev}→{cur})"

    value = event.get(field, (monitor or {}).get(field))
    if field == "observation_count" and value is None:
        value = (monitor or {}).get("observation_count")

    if op == "in":
        allowed = condition.get("values") or []
        return (value in allowed), f"{field}={value!r} in {allowed}"

    target = condition.get("value")
    if op in ("eq", "ne"):
        ok = (value == target)
        if op == "ne":
            ok = not ok
        return ok, f"{field}={value!r} {op} {target!r}"

    left, right = _num(value), _num(target)
    if left is None or right is None:
        return False, f"{field}={value!r} 与 {target!r} 无法数值比较"
    if op == "gt":
        return left > right, f"{field}={left} > {right}"
    if op == "gte":
        return left >= right, f"{field}={left} >= {right}"
    if op == "lt":
        return left < right, f"{field}={left} < {right}"
    if op == "lte":
        return left <= right, f"{field}={left} <= {right}"
    return False, f"未实现的操作符: {op}"


class TriggerEngine:
    """触发器引擎：条件评估 + 生命周期 + 触发记录。"""

    def __init__(self, path: str = "data/cognitive_triggers.json", kg=None):
        self.path = path
        self.kg = kg
        self._lock = threading.RLock()
        self._triggers: dict[str, dict] = {}
        self._seq = 0
        self._log: list[dict] = []
        self._load()

    # ── 持久化 ────────────────────────────────────────────

    def _load(self):
        data = load_json(self.path, default=None) or {}
        with self._lock:
            self._seq = int(data.get("seq", 0) or 0)
            self._triggers = {}
            for item in data.get("triggers", []) or []:
                if isinstance(item, dict) and item.get("id"):
                    self._triggers[item["id"]] = self._normalize(item)
            self._log = list(data.get("log", []) or [])[-LOG_LIMIT:]
        if self._triggers:
            logger.info(f"[Trigger] 加载 {len(self._triggers)} 条触发器")

    def _save(self):
        with self._lock:
            payload = {
                "version": 1,
                "seq": self._seq,
                "triggers": list(self._triggers.values()),
                "log": self._log[-LOG_LIMIT:],
            }
        atomic_write_json(self.path, payload)

    @staticmethod
    def _normalize(item: dict) -> dict:
        fire_mode = item.get("fire_mode", "edge")
        return {
            "id": str(item.get("id")),
            "name": item.get("name", ""),
            "description": item.get("description", ""),
            "monitor_id": item.get("monitor_id", "*"),
            "event_types": list(item.get("event_types", ["state_changed"])),
            "condition": item.get("condition") or {},
            "fire_mode": fire_mode if fire_mode in FIRE_MODES else "edge",
            "enabled": bool(item.get("enabled", True)),
            "priority": int(item.get("priority", 0) or 0),
            "cooldown_s": float(item.get("cooldown_s", 0) or 0),
            "once": bool(item.get("once", False)),
            "action": item.get("action") or {"type": "record"},
            "llm_mode": int(item.get("llm_mode", 0) or 0),
            "created_at": item.get("created_at") or now_str(),
            "updated_at": item.get("updated_at") or now_str(),
            "fire_count": int(item.get("fire_count", 0) or 0),
            "last_fired_at": item.get("last_fired_at"),
            "last_fired_epoch": item.get("last_fired_epoch"),
            "last_condition": item.get("last_condition"),      # 上一轮条件结果
            "last_error": item.get("last_error"),
            "error_count": int(item.get("error_count", 0) or 0),
            "metadata": item.get("metadata") or {},
        }

    # ── 评估 ──────────────────────────────────────────────

    def evaluate(self, event: dict, monitor: dict = None,
                 now: float = None) -> list:
        """对一条状态事件评估全部触发器，返回本次触发的认知事件列表。

        调用方（协调器）负责把 monitor 快照一并传入，用于字段回退读取。
        """
        now = time.time() if now is None else now
        fired: list[dict] = []
        skipped: list[dict] = []

        with self._lock:
            candidates = [dict(t) for t in self._triggers.values()]

        for trig in candidates:
            if not trig.get("enabled"):
                continue
            monitor_id = trig.get("monitor_id", "*")
            if monitor_id not in ("*", event.get("monitor_id")):
                continue
            event_types = trig.get("event_types") or []
            if event_types and event.get("type") not in event_types:
                continue

            try:
                ok, explain = evaluate_condition(trig.get("condition") or {},
                                                 event, monitor)
            except Exception as e:
                self._record_error(trig["id"], f"条件评估异常: {e}")
                continue

            prev_ok = trig.get("last_condition")
            if trig.get("fire_mode") == "edge":
                # 只在"不满足 → 满足"的跃变处触发（§6.3）
                should_fire = bool(ok) and not bool(prev_ok)
            else:
                should_fire = bool(ok)

            self._set_last_condition(trig["id"], bool(ok), explain)

            if not should_fire:
                continue

            # 冷却（§6.3）：冷却期内的触发请求被明确拒绝并留痕
            cd = float(trig.get("cooldown_s") or 0)
            last_epoch = trig.get("last_fired_epoch")
            if cd > 0 and last_epoch and (now - float(last_epoch)) < cd:
                skipped.append({"trigger_id": trig["id"], "reason": "cooldown",
                                "remaining_s": round(cd - (now - float(last_epoch)), 1)})
                continue

            if trig.get("once") and int(trig.get("fire_count", 0)) >= 1:
                skipped.append({"trigger_id": trig["id"], "reason": "once"})
                continue

            cognitive_event = self._fire(trig, event, explain, now)
            fired.append(cognitive_event)

        for item in skipped:
            self._log_skip(item)
        if fired or skipped:
            self._save()
        # 优先级：先触发的先派发（同级按创建时间）；派发放协调器
        fired.sort(key=lambda e: (-int(e.get("priority", 0) or 0),
                                  e.get("trigger_created_at", "")))
        return fired

    def _fire(self, trig: dict, event: dict, explain: str, now: float) -> dict:
        action = trig.get("action") or {"type": "record"}
        with self._lock:
            t = self._triggers.get(trig["id"])
            if t is None:                       # 评估期间被删除：不触发（§6.3）
                return {}
            t["fire_count"] = int(t.get("fire_count", 0)) + 1
            t["last_fired_at"] = now_str()
            t["last_fired_epoch"] = now
            if t.get("once"):
                t["enabled"] = False            # 只触发一次：自动停用
            flags = {
                "id": t["id"], "priority": t.get("priority", 0),
                "llm_mode": t.get("llm_mode", 0), "name": t.get("name", ""),
            }
        cognitive_event = {
            "type": "trigger_fired",
            "trigger_id": flags["id"],
            "trigger_name": flags["name"],
            "priority": flags["priority"],
            "llm_mode": flags["llm_mode"],
            "monitor_id": event.get("monitor_id"),
            "monitor_name": event.get("monitor_name"),
            "target_type": event.get("target_type"),
            "target_id": event.get("target_id"),
            "cause_event_id": event.get("event_id"),
            "condition_explain": explain,
            "previous": event.get("previous"),
            "current": event.get("current"),
            "action": action,
            "ts": now_str(),
            "ts_epoch": now,
            "trigger_created_at": (self.get(trig["id"]) or {}).get("created_at", ""),
        }
        with self._lock:
            self._log.append({
                "ts": cognitive_event["ts"],
                "kind": "fired",
                "trigger_id": flags["id"],
                "monitor_id": event.get("monitor_id"),
                "cause_event_id": event.get("event_id"),
                "explain": explain,
                "action_type": action.get("type"),
            })
            if len(self._log) > LOG_LIMIT:
                self._log = self._log[-LOG_LIMIT:]
        logger.info(f"[Trigger] 触发 {flags['id']} ({flags['name']}) ← "
                    f"{event.get('monitor_id')} {explain}")
        return cognitive_event

    def _log_skip(self, item: dict):
        with self._lock:
            self._log.append({
                "ts": now_str(), "kind": "skipped",
                "trigger_id": item["trigger_id"],
                "reason": item.get("reason"),
                "remaining_s": item.get("remaining_s"),
            })
            if len(self._log) > LOG_LIMIT:
                self._log = self._log[-LOG_LIMIT:]

    def _set_last_condition(self, trigger_id: str, result: bool, explain: str):
        with self._lock:
            t = self._triggers.get(trigger_id)
            if t is not None:
                t["last_condition"] = result
                t["last_condition_explain"] = explain

    def _record_error(self, trigger_id: str, message: str):
        with self._lock:
            t = self._triggers.get(trigger_id)
            if t is not None:
                t["last_error"] = message
                t["error_count"] = int(t.get("error_count", 0)) + 1
        self._save()
        logger.warning(f"[Trigger] {trigger_id} 评估错误: {message}")

    # ── CRUD ──────────────────────────────────────────────

    def create(self, trigger_id: str = None, name: str = "", monitor_id: str = "*",
               condition: dict = None, event_types: list = None,
               action: dict = None, fire_mode: str = "edge", cooldown_s: float = 0,
               priority: int = 0, once: bool = False, llm_mode: int = 0,
               enabled: bool = True, description: str = "",
               metadata: dict = None) -> dict:
        condition = condition or {}
        ok, explain = evaluate_condition(condition, {}, None)
        if condition and str(condition.get("op", "")) not in OPS:
            return {"ok": False, "error": f"条件不合法: {explain}"}
        if fire_mode not in FIRE_MODES:
            return {"ok": False, "error": f"fire_mode 必须是 {FIRE_MODES}"}
        if int(llm_mode) not in LLM_MODES:
            return {"ok": False, "error": f"llm_mode 必须是 {LLM_MODES}"}
        action = action or {"type": "record"}
        if action.get("type") not in ACTION_TYPES:
            return {"ok": False, "error": f"action.type 必须是 {ACTION_TYPES}"}

        with self._lock:
            if not trigger_id:
                self._seq += 1
                trigger_id = f"trig_{self._seq}"
            if trigger_id in self._triggers:
                return {"ok": False, "error": f"触发器已存在: {trigger_id}"}
            trig = self._normalize({
                "id": trigger_id,
                "name": name or trigger_id,
                "description": description,
                "monitor_id": monitor_id,
                "event_types": event_types or ["state_changed"],
                "condition": condition,
                "fire_mode": fire_mode,
                "cooldown_s": cooldown_s,
                "priority": priority,
                "once": once,
                "action": action,
                "llm_mode": int(llm_mode),
                "enabled": enabled,
                "metadata": metadata or {},
            })
            self._triggers[trigger_id] = trig
        self._save()
        logger.info(f"[Trigger] + {trigger_id} monitor={monitor_id} "
                    f"mode={fire_mode} llm_mode={llm_mode}")
        return {"ok": True, "trigger": trig}

    def update(self, trigger_id: str, **fields) -> dict:
        with self._lock:
            trig = self._triggers.get(trigger_id)
            if trig is None:
                return {"ok": False, "error": f"触发器不存在: {trigger_id}"}
            for key in ("name", "description", "monitor_id", "event_types",
                        "condition", "fire_mode", "cooldown_s", "priority",
                        "once", "action", "llm_mode", "enabled", "metadata"):
                if key in fields and fields[key] is not None:
                    trig[key] = fields[key]
            if trig.get("fire_mode") not in FIRE_MODES:
                return {"ok": False, "error": f"fire_mode 必须是 {FIRE_MODES}"}
            if int(trig.get("llm_mode", 0)) not in LLM_MODES:
                return {"ok": False, "error": f"llm_mode 必须是 {LLM_MODES}"}
            if (trig.get("action") or {}).get("type") not in ACTION_TYPES:
                return {"ok": False, "error": f"action.type 必须是 {ACTION_TYPES}"}
            trig["enabled"] = bool(trig.get("enabled", True))
            trig["updated_at"] = now_str()
            # 条件改了 → 上一轮条件结果作废，避免用旧状态判跃变
            if "condition" in fields:
                trig["last_condition"] = None
        self._save()
        return {"ok": True, "trigger": trig}

    def set_enabled(self, trigger_id: str, enabled: bool) -> dict:
        return self.update(trigger_id, enabled=bool(enabled))

    def delete(self, trigger_id: str) -> dict:
        with self._lock:
            trig = self._triggers.pop(trigger_id, None)
            if trig is None:
                return {"ok": False, "error": f"触发器不存在: {trigger_id}"}
        self._save()
        logger.info(f"[Trigger] - {trigger_id}（评估循环无残留任务：触发器不持有线程）")
        return {"ok": True, "deleted": trigger_id}

    # ── 查询 ──────────────────────────────────────────────

    def get(self, trigger_id: str) -> dict:
        with self._lock:
            trig = self._triggers.get(trigger_id)
            return dict(trig) if trig else None

    def list(self) -> list:
        with self._lock:
            out = [dict(t) for t in self._triggers.values()]
        out.sort(key=lambda t: (-int(t.get("priority", 0) or 0),
                                t.get("created_at", "")))
        return out

    def recent_log(self, n: int = 20) -> list:
        with self._lock:
            return list(self._log[-n:])

    def state(self) -> dict:
        with self._lock:
            triggers = self.list()
            log = list(self._log[-20:])
        return {
            "count": len(triggers),
            "enabled_count": sum(1 for t in triggers if t.get("enabled")),
            "error_count": sum(1 for t in triggers if t.get("last_error")),
            "triggers": triggers,
            "recent_log": log,
        }


# ── Mode 1：自然语言 → 结构化定义（仅在创建/编辑时调用一次） ──────────
# 运行时条件评估永远走 evaluate_condition（确定性）；LLM 只在这里出现，
# 且必须返回严格 JSON，失败就如实报错，不猜测。

NL_TRIGGER_PROMPT = """把下面这句话转换成一个结构化触发器定义，只输出 JSON，不要解释。

可用结构（字段名与取值必须严格照写）：
{
  "monitor_id": "检测器 id（不知道就用 *）",
  "condition": {"op": "gt|gte|lt|lte|eq|ne|changed|in|all|any|not",
                 "field": "current|previous|delta|observation_count|confidence",
                 "value": 数字或字符串,
                 "conditions": [子条件...]（op 为 all/any 时用）,
                 "values": [枚举值...]（op 为 in 时用）},
  "fire_mode": "edge|level",
  "cooldown_s": 数字（秒）,
  "once": true/false,
  "llm_mode": 0|1|2,
  "action": {"type": "activate_nodes|record|notify", "nodes": ["要激活的图谱节点 id"]},
  "name": "简短名字",
  "description": "一句话说明"
}

注意：条件必须是上述结构，不允许输出自然语言条件。用户的这句话是：
"""


def nl_to_trigger(text: str, llm_fn) -> dict:
    """把自然语言描述转成结构化触发器定义（Mode 1，调用方注入 llm_fn）。

    llm_fn(prompt) -> str(JSON)。解析失败如实返回错误，不猜测、不静默降级。
    """
    if llm_fn is None:
        return {"ok": False, "error": "未提供 LLM 调用函数（Mode 1 需要）"}
    try:
        raw = llm_fn(NL_TRIGGER_PROMPT + str(text))
    except Exception as e:
        return {"ok": False, "error": f"LLM 调用失败: {e}"}
    if not raw:
        return {"ok": False, "error": "LLM 未返回内容"}
    raw = str(raw).strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:-1])
    try:
        data = json.loads(raw)
    except Exception as e:
        return {"ok": False, "error": f"返回不是合法 JSON: {e}", "raw": raw[:300]}
    if not isinstance(data, dict):
        return {"ok": False, "error": "返回不是 JSON 对象", "raw": raw[:300]}

    cond = data.get("condition") or {}
    if not isinstance(cond, dict) or str(cond.get("op", "")) not in OPS:
        return {"ok": False, "error": "条件结构不合法（op 必须在允许集合内）",
                "candidate": data}
    return {"ok": True, "candidate": data}
