# cognitive_regulation.py — 认知调节协调器
# ============================================================================
# 把三个基础机制接成一条链（§6.1），并提供 app.py 只需持有的单一入口：
#
#   状态提交 / 轮询 → StateMonitor（状态 + 变化事件）
#                     → TriggerEngine（结构化条件评估、冷却、生命周期）
#                     → CognitiveEvent（认知事件）
#                     → 动作派发（激活图谱节点 / 记录 / 通知）→ 认知循环
#
# 设计约束：
#   - 全程确定性：没有任何一步依赖 LLM。llm_mode 只标记该认知事件"是否允许
#     进入更高层认知（含 LLM）"，由认知层按需取用（见 recent_cognitive_events）。
#   - 不预设应用：本模块不含任何具体对象/游戏/传感器的字段或分支。
#   - 不新建线程：轮询入口 poll_due() 由既有认知循环（CC tick）按节拍调用。
#   - 不绕过锁：动作里的节点激活走 engine.activate_from_inputs，因此自动
#     受认知锁约束（锁定节点不会被触发器强拉进激活）。
# ============================================================================

import logging
import threading
from collections import deque

from cognitive_locks import LockRegistry
from state_monitors import MonitorRegistry
from cognitive_triggers import TriggerEngine, nl_to_trigger

logger = logging.getLogger(__name__)

RECENT_EVENT_LIMIT = 50


class CognitiveRegulation:
    """锁 / 状态检测器 / 触发器的统一入口与事件派发。"""

    def __init__(self, kg=None, engine=None, config=None, data_dir: str = "data",
                 save_graph_fn=None):
        self.kg = kg
        self.engine = engine
        self.config = config or {}
        self._save_graph_fn = save_graph_fn

        self.locks = LockRegistry(path=f"{data_dir}/cognitive_locks.json", kg=kg)
        self.monitors = MonitorRegistry(path=f"{data_dir}/state_monitors.json", kg=kg)
        self.triggers = TriggerEngine(path=f"{data_dir}/cognitive_triggers.json", kg=kg)

        self._handlers: list = []
        self._recent_events = deque(maxlen=RECENT_EVENT_LIMIT)
        self._delivered_epoch: float = 0.0   # 已被认知层消费的事件水位
        self._lock = threading.RLock()

        # 状态变化 → 触发器评估 → 派发（确定性链路，同线程完成）
        self.monitors.subscribe(self._on_state_event)

    # ── 订阅 ──────────────────────────────────────────────

    def register_handler(self, fn):
        """注册认知事件处理器 fn(cognitive_event, action_result)。

        处理器用于"认知事件进入认知循环"的具体接线（例如写日志、注入回答
        上下文）。处理器异常只记日志，不影响触发链本身。
        """
        with self._lock:
            if fn not in self._handlers:
                self._handlers.append(fn)

    # ── 链路 ──────────────────────────────────────────────

    def submit_state(self, monitor_id: str, value, source: str = "event",
                     confidence=None) -> dict:
        """提交状态（唯一入口）。状态变化时自动走触发器评估。"""
        return self.monitors.observe(monitor_id, value, source=source,
                                     confidence=confidence)

    def _on_state_event(self, event: dict):
        monitor = self.monitors.get(event.get("monitor_id")) or {}
        fired = self.triggers.evaluate(event, monitor=monitor)
        for cognitive_event in fired:
            if cognitive_event:
                self._dispatch(cognitive_event)

    def _dispatch(self, cognitive_event: dict) -> dict:
        """执行触发动作。返回动作结果（同时进入最近事件环，供认知层/前端读取）。"""
        action = cognitive_event.get("action") or {}
        action_type = action.get("type", "record")
        result = {"ok": True, "type": action_type, "activated": []}

        if action_type == "activate_nodes":
            node_ids = [str(n) for n in (action.get("nodes") or []) if str(n).strip()]
            if node_ids and self.engine is not None:
                try:
                    # 走引擎既有入口 → 自动受锁约束、自动同步活跃前沿
                    self.engine.activate_from_inputs(node_ids, [])
                    result["activated"] = node_ids
                    if self._save_graph_fn:
                        self._save_graph_fn()
                    logger.info(f"[Regulation] 触发动作激活节点: {node_ids}")
                except Exception as e:
                    result = {"ok": False, "type": action_type,
                              "error": f"激活失败: {e}", "activated": []}
                    logger.warning(f"[Regulation] 激活失败: {e}")
            elif node_ids:
                result = {"ok": False, "type": action_type,
                          "error": "引擎未绑定，无法激活节点", "activated": []}

        elif action_type == "notify":
            result["message"] = action.get("message") or cognitive_event.get("name", "")

        cognitive_event = dict(cognitive_event)
        cognitive_event["action_result"] = result
        with self._lock:
            self._recent_events.append(cognitive_event)
            handlers = list(self._handlers)

        for fn in handlers:
            try:
                fn(cognitive_event, result)
            except Exception as e:
                logger.warning(f"[Regulation] 处理器异常: {e}")
        return result

    def poll_due(self, now: float = None) -> list:
        """调度入口（由既有认知循环按节拍调用；无数据源时零成本）。

        本阶段不内置任何数据源——没有 register_poller 的检测器不会被采样，
        接口在此以便未来接入感知/工具数据。
        """
        return self.monitors.poll_due(now)

    def recent_cognitive_events(self, n: int = 5, llm_mode_min: int = 1) -> list:
        """最近触发的认知事件（llm_mode >= llm_mode_min 才返回）。

        认知层（回答生成 / 反思）用它决定"有哪些触发值得进更高层处理"，
        默认只要 llm_mode>=1 的事件——llm_mode=0 的事件不参与任何 LLM 通路。
        """
        with self._lock:
            events = list(self._recent_events)
        return [e for e in events if int(e.get("llm_mode", 0)) >= llm_mode_min][-n:]

    def drain_cognitive_events(self, llm_mode_min: int = 1, limit: int = 3) -> list:
        """取出尚未被认知层消费的触发事件（消费即推进水位，避免重复入 prompt）。

        回答生成每轮取一次：只有"本轮新发生的、且 llm_mode 达标的"事件会进
        认知上下文——旧事件不会在后续每轮反复出现。
        """
        with self._lock:
            events = [e for e in self._recent_events
                      if int(e.get("llm_mode", 0)) >= llm_mode_min
                      and float(e.get("ts_epoch", 0)) > self._delivered_epoch]
            if events:
                self._delivered_epoch = max(float(e.get("ts_epoch", 0)) for e in events)
            return events[-limit:]

    # ── Mode 1：自然语言 → 结构化触发器定义 ──────────────

    def parse_trigger_text(self, text: str, llm_fn) -> dict:
        """把自然语言转成候选触发器定义（不落盘，由调用方确认后再 create）。"""
        return nl_to_trigger(text, llm_fn)

    # ── 锁 → 运行时即时生效（协调器持有引擎，注册表不反向依赖引擎）──

    def _sync_lock_effect(self, target_type: str, target_id: str):
        """让锁立刻作用到运行时状态上。

        blocking 锁意味着"此刻不参与"：若不清掉目标锁前残留的激活值，
        它凭旧值仍会出现在 Top-K/快照里，看上去像没锁住。激活是瞬时认知
        状态（不是节点原始数据——weight/label/文本一律不动），清它不违反
        "锁不改动对象"的约定。
        """
        if self.engine is None or target_type != "node":
            return
        try:
            if self.locks.blocks_node(target_id, "diffusion"):
                node = self.kg.nodes.get(target_id) if self.kg is not None else None
                if node is not None and node.activation > 0:
                    node.activation = 0.0
                with self.engine._lock:
                    self.engine._active_nodes.pop(target_id, None)
            self.engine.note_action_dirty(target_id)
            self.engine._refresh_action_queue()
        except Exception as e:
            logger.warning(f"[Regulation] 锁生效同步失败 {target_id}: {e}")

    def create_lock(self, **kwargs) -> dict:
        result = self.locks.create(**kwargs)
        if result.get("ok"):
            lock = result["lock"]
            self._sync_lock_effect(lock.get("target_type"), lock.get("target_id"))
        return result

    def update_lock(self, lock_id: str, **fields) -> dict:
        result = self.locks.update(lock_id, **fields)
        if result.get("ok"):
            lock = result["lock"]
            self._sync_lock_effect(lock.get("target_type"), lock.get("target_id"))
        return result

    def delete_lock(self, lock_id: str) -> dict:
        result = self.locks.delete(lock_id)
        if result.get("ok") and self.engine is not None:
            # 解锁后队列资格要重估（被排除的节点可能重新入队）
            try:
                self.engine._refresh_action_queue()
            except Exception as e:
                logger.warning(f"[Regulation] 解锁后队列刷新失败: {e}")
        return result

    # ── 状态汇总（API 用） ────────────────────────────────

    def state(self) -> dict:
        return {
            "locks": self.locks.state(),
            "monitors": self.monitors.state(),
            "triggers": self.triggers.state(),
            "recent_cognitive_events": self.recent_cognitive_events(n=10, llm_mode_min=0),
        }
