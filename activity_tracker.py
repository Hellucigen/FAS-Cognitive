# activity_tracker.py — CurrentActivity（当前活动）认知结构
# ============================================================================
# 架构对齐（2026-09-19）新增。回答一个此前图上无法表达的问题：
#
#   "我此刻正在做什么？"  （不是"我刚刚执行了哪个 action"）
#
# 论文/设计中的三层区分：
#   A. Haru        —— 我是谁（具身主体）
#   B. 当前状态     —— 我现在处于什么状态（minecraft.perception 的状态槽位）
#   C. 当前活动     —— 我此刻正在做什么（本模块）
#
# 图结构：
#   Haru -[当前活动 0.9]-> 活动_{kind}_{ts} -[类型]-> 活动类型:{kind}
#                                         -[状态]-> 活动状态:{status}
#                                         -[目标]-> 目标实体（可选）
#
# 关键语义（区别于 action）：
#   action 是执行层事件（ActionManager 的一次技能调用，秒级）；
#   activity 是认知层状态（分钟级，多个同族 action 归入同一活动）。
#   同 kind 连续动作复用同一活动节点；kind 切换才结算旧活动、开新活动。
#
# 生命周期：执行中 → 已完成 / 失败 / 取消 / 暂停（闭集，见 graph_schema）。
# 历史留存：已结束活动保留最近 ACTIVITY_HISTORY_CAP 个，超出删最旧——
# 活动是"当前认知"不是流水账，流水账由 行动_*/经验时间轴 承担。
# ============================================================================

import logging
import time

from graph_model import Node, Edge, now_str
import graph_schema as schema

logger = logging.getLogger(__name__)

HUB_ID = "Haru"
CURRENT_REL = "当前活动"
TYPE_REL = "类型"
STATUS_REL = "状态"
TARGET_REL = "目标"

_KIND_PREFIX = "活动类型:"
_STATUS_PREFIX = "活动状态:"

_ENDED_STATUSES = ("已完成", "失败", "取消")


class ActivityTracker:
    """当前活动的唯一写入口。挂接点：
      - action_system.ActionManager._start   → start(kind, target, ...)
      - action_system.ActionManager._settle  → settle(success)
      - autonomy 空闲分支                    → idle()
    所有方法自带防御：图/engine 异常永不影响行动主流程。
    """

    def __init__(self, kg, engine=None):
        self.kg = kg
        self.engine = engine
        self.current_id = None
        self._last_success = True

    # ── 词表节点（闭集，惰性创建）─────────────────────────────

    def _vocab_node(self, nid: str, desc: str):
        n = self.kg.nodes.get(nid)
        if n is None:
            self.kg.add_node(Node(
                id=nid, weight=0.4, label="declarative-semantic",
                graph_space="cognitive",
                extra_attrs={"type": "activity_vocab", "desc": desc}))
            n = self.kg.nodes[nid]
        return n

    def _set_status_edge(self, act_id: str, status: str):
        """in-place 换状态边：活动节点同时最多只有一条 状态 边。"""
        self._vocab_node(f"{_STATUS_PREFIX}{status}", f"活动生命周期状态：{status}")
        try:
            self.kg.remove_edges(src=act_id, relation=STATUS_REL)
        except Exception as e:
            logger.warning(f"[Activity] 状态边清除失败（可能双状态边）: {e!r}")
        self.kg.add_edge(Edge(src=act_id, dst=f"{_STATUS_PREFIX}{status}",
                              relation=STATUS_REL, weight=0.6,
                              relation_category="cognitive_relation"))

    # ── 查询 ─────────────────────────────────────────────────

    def current(self):
        """当前活动节点（经 Haru-[当前活动]-> 指针边读取；无则 None）。"""
        try:
            with self.kg._lock:
                for e in self.kg.get_out_edges(HUB_ID):
                    if e.relation == CURRENT_REL:
                        return self.kg.nodes.get(e.dst)
        except Exception as e:
            # §14：记忆读取失败≠"没有当前活动"，静默是撒谎，要留痕
            logger.warning(f"[Activity] 当前活动指针边读取失败: {e!r}")
        return None

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self, kind: str, target: str = None, reason: str = "",
              source: str = ""):
        """开始/延续一个活动。同 kind 的活动会被延续而不是新建。"""
        try:
            kind = schema.activity_kind_for(kind)
            if kind not in schema.ACTIVITY_KINDS:
                kind = "行动"

            cur = self.kg.nodes.get(self.current_id) if self.current_id else None
            if (cur is not None
                    and (cur.extra_attrs or {}).get("status") == "执行中"
                    and (cur.extra_attrs or {}).get("kind") == kind):
                # 同族动作延续同一活动（activity ≠ action）
                if target and not cur.extra_attrs.get("target"):
                    cur.extra_attrs["target"] = str(target)[:80]
                cur.extra_attrs["action_count"] = cur.extra_attrs.get("action_count", 0) + 1
                cur.touch()
                self._mark_active([cur.id])
                return cur.id

            # kind 切换：结算旧活动（有结算结果的用结果，被切换的按取消记）
            if cur is not None and (cur.extra_attrs or {}).get("status") == "执行中":
                self._end(cur, "已完成" if self._last_success else "取消")

            return self._create(kind, target, reason, source)
        except Exception as e:
            logger.debug(f"[Activity] start 失败（不影响行动主流程）: {e}")
            return None

    def settle(self, success: bool):
        """当前活动的一次动作结算。活动本身通常继续执行中（多动作活动）。"""
        self._last_success = bool(success)
        try:
            cur = self.kg.nodes.get(self.current_id) if self.current_id else None
            if cur is None or (cur.extra_attrs or {}).get("status") != "执行中":
                return
            # 失败立即反映到状态边；成功保持 执行中（活动可能还有后续动作）
            if not success:
                self._end(cur, "失败")
            else:
                cur.extra_attrs["success_count"] = cur.extra_attrs.get("success_count", 0) + 1
                cur.touch()
        except Exception as e:
            logger.debug(f"[Activity] settle 失败: {e}")

    def idle(self, reason: str = "自主空闲，保持观察"):
        """自主层空闲 → 活动_观察环境。已处于观察活动则只续期。"""
        return self.start("观察", target=None, reason=reason, source="autonomy_idle")

    # ── 内部 ─────────────────────────────────────────────────

    def _reuse_ended_like(self, kind: str):
        """同 kind 的已结束活动节点（含进程重启后残留的执行中孤儿按取消处理）。
        2026-09-28 清理批次：此前每次 start 都新建 活动_{kind}_{ts}，进程重启后
        旧节点永不结算/复用 → 图上残留了 158 个裸瞬态活动节点（本次已一次清空）。
        复用 = 同类型活动在图上有上界，靠 _prune_history 修出界。"""
        for n in self.kg.nodes.values():
            ea = n.extra_attrs or {}
            if ea.get("type") == "activity" and ea.get("kind") == kind:
                st = ea.get("status")
                if st in _ENDED_STATUSES or st == "执行中":
                    return n
        return None

    def _create(self, kind: str, target, reason: str, source: str):
        import json as _json
        reused = self._reuse_ended_like(kind)
        if reused is not None:
            # 复用旧节点：状态翻回执行中，字段刷新；指针/状态边由下方共有逻辑重挂
            act_id = reused.id
            ea = reused.extra_attrs
            ea["status"] = "执行中"
            ea["started"] = now_str()
            ea["ended"] = None
            ea["action_count"] = (ea.get("action_count") or 0) + 1
            if target and str(target) in self.kg.nodes:
                ea["target"] = str(target)[:80]
            reused.touch()
        else:
            act_id = f"活动_{kind}_{int(time.time() * 1000) % 10 ** 9}"
            self.kg.add_node(Node(
                id=act_id, weight=0.5, label="declarative-episodic",
                graph_space="self",
                extra_attrs={
                    "type": "activity", "kind": kind, "status": "执行中",
                    "target": str(target)[:80] if target else None,
                    "reason": str(reason)[:120] if reason else "",
                    "source": source or "action_manager",
                    "created": now_str(), "started": now_str(),
                    "ended": None, "action_count": 1,
                }))
        self._vocab_node(f"{_KIND_PREFIX}{kind}", f"当前活动类型：{kind}")

        # Haru 的当前活动指针：换边，恒定单条（Haru 出度不随历史增长）
        try:
            self.kg.remove_edges(src=HUB_ID, relation=CURRENT_REL)
        except Exception:
            pass
        if HUB_ID in self.kg.nodes:
            self.kg.add_edge(Edge(src=HUB_ID, dst=act_id, relation=CURRENT_REL,
                                  weight=0.9, relation_category="cognitive_relation"))
        self.kg.add_edge(Edge(src=act_id, dst=f"{_KIND_PREFIX}{kind}",
                              relation=TYPE_REL, weight=0.7,
                              relation_category="cognitive_relation"))
        self._set_status_edge(act_id, "执行中")
        # 目标实体边：目标已在图中才有意义（避免为坐标串造节点）
        if target and str(target) in self.kg.nodes:
            self.kg.add_edge(Edge(src=act_id, dst=str(target), relation=TARGET_REL,
                                  weight=0.6, relation_category="cognitive_relation"))

        self.current_id = act_id
        self._mark_active([act_id])
        self._prune_history()
        logger.info(f"[Activity] 当前活动 → {act_id}"
                    + (f"（目标: {target}）" if target else ""))
        return act_id

    def _end(self, node, status: str):
        node.extra_attrs["status"] = status
        node.extra_attrs["ended"] = now_str()
        node.touch()
        self._set_status_edge(node.id, status)

    def _mark_active(self, ids):
        if self.engine is not None:
            try:
                self.engine.mark_active(ids)
            except Exception:
                pass

    def _prune_history(self):
        """已结束活动超过上限时删最旧（连边级联删除）。"""
        try:
            ended = []
            for n in self.kg.nodes.values():
                ea = n.extra_attrs or {}
                if ea.get("type") == "activity" and ea.get("status") in _ENDED_STATUSES:
                    ended.append((ea.get("ended") or ea.get("created") or "", n.id))
            if len(ended) <= schema.ACTIVITY_HISTORY_CAP:
                return
            ended.sort()
            for _, nid in ended[:len(ended) - schema.ACTIVITY_HISTORY_CAP]:
                if nid == self.current_id:
                    continue
                self.kg.remove_node(nid)
        except Exception as e:
            logger.debug(f"[Activity] 历史修剪失败: {e}")
