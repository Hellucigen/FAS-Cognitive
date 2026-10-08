# cognitive_locks.py — 认知锁（Lock Mechanism）
# ============================================================================
# 锁控制"某个认知对象是否参与某个认知过程"，它不是删除、不是隐藏、也不是
# 负权边：
#   - 删除是数据操作，锁是控制机制；对象数据一个字节都不改（§4.2-6/7）
#   - 负权边是图谱关系（参与计算并产生抑制），锁不改变对象语义
#   - 隐藏是展示层白名单（config.hide_temp_nodes / is_cognitive_visible），
#     锁是认知过程级的、可解释、可审计、可解除的控制
#
# 两种锁语义（论文与用户规范的核心区别）：
#   blocking     阻塞锁：对象不参与指定过程。扩散语境下 = 不接收、不发射、
#                不被种子激活（激活前就拦住，激活值保持 0）。
#   non_blocking 非阻塞锁：允许参与内部计算（照常传播激活值），但不进入
#                指定过程的输出（Top-K / 活跃快照 / 回答区）。
#   即"允许内部处理，但限制认知输出"——这是必须保留的区别。
#
# 作用范围 scope（认知过程名）：
#   diffusion  注意力扩散（活跃度计算与传播）
#   topk       回答区/输出结果（Top-K、扩散结果输出）
#   action     动作队列入选资格
#   all        以上全部
#   未知 scope 会被存下但当"惰性"处理（不做任何拦截）——不假装支持没实现的钩子。
#
# 优先级与组合规则（§4.2-4，必须明确）：
#   同类型锁之间是**并集**：任一 enabled 且未过期的 blocking 锁命中，对象即
#   被阻塞；non_blocking 同理。priority 不改变并集语义（阻塞是保守行为，
#   高优先级锁不能"解锁"低优先级锁），只决定对外展示的原因取哪一条
#   （priority 高者优先，同优先级取创建早者）。
#
# 线程与锁序（与 disposition_store 一致）：self._lock → kg._lock。
# ============================================================================

import logging
import threading
import time

from graph_model import now_str
from json_store import load_json, atomic_write_json

logger = logging.getLogger(__name__)

LOCK_TYPES = ("blocking", "non_blocking")
SCOPES = ("diffusion", "topk", "action", "all")
SOURCES = ("manual", "automatic", "system")
# system：进程级认知状态槽（不在图上的对象，如"话题终止抑制期"）。
# 锁记录只作为**带 expires_at/审计/解除 API 的状态载体**存在——
# _rebuild_gate_locked 对非 node/edge 目标天然跳过，绝不进任何拦截
# 集合，扩散语义零影响（2026-09-22，§18.2 #10）。
TARGET_TYPES = ("node", "edge", "system")

# scope=all 对所有过程生效；每个过程只关心自己的名字
_SCOPE_ALL = "all"
HISTORY_LIMIT = 200


def edge_key(src: str, relation: str, dst: str) -> str:
    """边的规范标识：src|relation|dst。"""
    return f"{src}|{relation}|{dst}"


def parse_edge_key(target_id: str) -> tuple:
    """解析边标识 → (src, relation, dst)；不合法返回 (None, None, None)。"""
    parts = str(target_id or "").split("|")
    if len(parts) != 3:
        return None, None, None
    return parts[0], parts[1], parts[2]


class LockRegistry:
    """认知锁注册表：CRUD + 持久化 + 拦截判定。

    engine 只读本对象的三组 set（blocked/hidden），不反向依赖本模块，
    避免 diffusion_engine ↔ cognitive_locks 的循环依赖。
    """

    def __init__(self, path: str = "data/cognitive_locks.json", kg=None):
        self.path = path
        self.kg = kg
        self._lock = threading.RLock()
        self._locks: dict[str, dict] = {}
        self._seq = 0
        self._history: list[dict] = []

        # ── 拦截判定用的派生集合（锁变更时重建，扩散热路径只做 set 查找）──
        # 按 scope 分离：scope=action 的锁不该顺手把节点从扩散里拦掉。
        #   diffusion：blocking 拦扩散过程（激活/发射/接收），
        #              non_blocking 拦扩散输出（Top-K / 活跃快照 / 回答区）
        #   topk / action 是"输出型"过程：两种锁型都表现为不进入该过程的输出
        #              （对输出型过程，blocking 与 non_blocking 效果收敛，
        #               见模块头注释）
        self._blocked_nodes_diffusion: set = set()
        self._hidden_nodes_diffusion: set = set()
        self._blocked_edges_diffusion: set = set()
        self._hidden_edges_diffusion: set = set()
        self._hidden_nodes_topk: set = set()
        self._hidden_edges_topk: set = set()
        self._hidden_nodes_action: set = set()
        self._hidden_edges_action: set = set()
        self._next_expiry: float = 0.0    # 最近一个到期时刻；到点需重建
        self._dirty = True

        # 扁平视图（供 /api/regulation 状态与测试直读；语义见注释）
        self.blocked_node_ids = self._blocked_nodes_diffusion
        self.hidden_node_ids = self._hidden_nodes_diffusion
        self.blocked_edge_keys: set = set()
        self.hidden_edge_keys: set = set()

        self._load()

    # ── 持久化 ────────────────────────────────────────────

    def _load(self):
        data = load_json(self.path, default=None) or {}
        with self._lock:
            self._seq = int(data.get("seq", 0) or 0)
            self._locks = {}
            for item in data.get("locks", []) or []:
                if isinstance(item, dict) and item.get("id"):
                    self._locks[item["id"]] = self._normalize(item)
            self._history = list(data.get("history", []) or [])[-HISTORY_LIMIT:]
            self._dirty = True
            self._rebuild_gate_locked()
        if self._locks:
            logger.info(f"[CognitiveLock] 加载 {len(self._locks)} 条锁")

    def _save(self):
        with self._lock:
            payload = {
                "version": 1,
                "seq": self._seq,
                "locks": list(self._locks.values()),
                "history": self._history[-HISTORY_LIMIT:],
            }
        atomic_write_json(self.path, payload)

    @staticmethod
    def _normalize(item: dict) -> dict:
        """补齐缺省字段（兼容旧文件 / 外部直接写盘）。"""
        out = {
            "id": str(item.get("id")),
            "target_type": item.get("target_type", "node"),
            "target_id": str(item.get("target_id", "")),
            "lock_type": item.get("lock_type", "blocking"),
            "scope": item.get("scope", "diffusion"),
            "reason": item.get("reason", ""),
            "source": item.get("source", "manual"),
            "priority": int(item.get("priority", 0) or 0),
            "enabled": bool(item.get("enabled", True)),
            "created_at": item.get("created_at") or now_str(),
            "updated_at": item.get("updated_at") or now_str(),
            "expires_at": item.get("expires_at"),
            "metadata": item.get("metadata") or {},
        }
        if out["target_type"] == "edge":
            src, rel, dst = parse_edge_key(out["target_id"])
            out["target_meta"] = {"src": src, "relation": rel, "dst": dst}
        return out

    # ── 派生集合重建 ──────────────────────────────────────

    def _rebuild_gate_locked(self):
        """按当前锁集合重建各 scope 的拦截集合。调用方须持 self._lock。"""
        bd_n, hd_n = set(), set()          # diffusion：blocking / hidden
        bd_e, hd_e = set(), set()
        ht_n, ht_e = set(), set()           # topk 输出
        ha_n, ha_e = set(), set()           # action 输出
        next_expiry = 0.0
        now = time.time()

        for lk in self._locks.values():
            if not lk.get("enabled"):
                continue
            exp = lk.get("expires_at")
            if exp:
                try:
                    exp_f = float(exp)
                except (TypeError, ValueError):
                    exp_f = 0.0
                if exp_f and exp_f <= now:
                    continue                      # 已过期：不参与拦截
                if exp_f and (next_expiry == 0.0 or exp_f < next_expiry):
                    next_expiry = exp_f

            scope = lk.get("scope", "diffusion")
            is_blocking = lk.get("lock_type") == "blocking"
            is_node = lk.get("target_type") == "node"
            tid = lk.get("target_id", "")
            if not is_node:
                src, rel, dst = parse_edge_key(tid)
                if src is None:
                    continue                  # 标识不合法：不参与拦截
                key = (src, rel, dst)
            else:
                key = tid

            scopes = ("diffusion", "topk", "action") if scope == _SCOPE_ALL else (scope,)
            for sc in scopes:
                if is_node:
                    if sc == "diffusion":
                        if is_blocking:
                            bd_n.add(key)
                        else:
                            hd_n.add(key)     # 参与扩散但不进扩散输出
                    elif sc == "topk":
                        ht_n.add(key)
                    else:
                        ha_n.add(key)
                else:
                    if sc == "diffusion":
                        if is_blocking:
                            bd_e.add(key)
                        else:
                            hd_e.add(key)
                    elif sc == "topk":
                        ht_e.add(key)
                    else:
                        ha_e.add(key)

        self._blocked_nodes_diffusion = bd_n
        self._hidden_nodes_diffusion = hd_n
        self._blocked_edges_diffusion = bd_e
        self._hidden_edges_diffusion = hd_e
        self._hidden_nodes_topk = ht_n
        self._hidden_edges_topk = ht_e
        self._hidden_nodes_action = ha_n
        self._hidden_edges_action = ha_e
        # 扁平视图：被锁的**对象**（不论 scope），供状态查询与调试
        self.blocked_node_ids = bd_n
        self.hidden_node_ids = hd_n
        self.blocked_edge_keys = bd_e
        self.hidden_edge_keys = hd_e
        self._next_expiry = next_expiry
        self._dirty = False

    def _ensure_current(self):
        """到期时刻过了就重建（过期无需显式操作即可生效）。"""
        with self._lock:
            if self._dirty:
                self._rebuild_gate_locked()
                return
            nxt = self._next_expiry
            if nxt and time.time() >= nxt:
                self._rebuild_gate_locked()

    def refresh(self):
        """外部改图后（节点/边删除）调用：清除悬空锁的拦截效果。"""
        with self._lock:
            self._rebuild_gate_locked()

    # ── 查询接口（热路径） ────────────────────────────────

    def blocks_node(self, node_id: str, scope: str = "diffusion") -> bool:
        """该对象是否被阻止**参与**指定过程（blocking 锁）。"""
        self._ensure_current()
        if scope == "diffusion":
            return node_id in self._blocked_nodes_diffusion
        if scope in ("topk", "action"):
            return False      # 输出型过程：不存在"参与但被拦"的区分
        return False

    def hides_node(self, node_id: str, scope: str = "topk") -> bool:
        """该对象是否被限制**进入输出**（non_blocking 锁，或输出型过程的锁）。"""
        self._ensure_current()
        if scope == "topk":
            return (node_id in self._hidden_nodes_topk
                    or node_id in self._hidden_nodes_diffusion)
        if scope == "action":
            return node_id in self._hidden_nodes_action
        if scope == "diffusion":
            return node_id in self._hidden_nodes_diffusion
        return False

    def blocks_edge(self, src: str, relation: str, dst: str,
                    scope: str = "diffusion") -> bool:
        self._ensure_current()
        if scope == "diffusion":
            return (src, relation, dst) in self._blocked_edges_diffusion
        return False

    def hides_edge(self, src: str, relation: str, dst: str,
                   scope: str = "topk") -> bool:
        self._ensure_current()
        key = (src, relation, dst)
        if scope == "topk":
            return key in self._hidden_edges_topk or key in self._hidden_edges_diffusion
        if scope == "action":
            return key in self._hidden_edges_action
        if scope == "diffusion":
            return key in self._hidden_edges_diffusion
        return False

    # 兼容直读的简写（等价于 scope=diffusion 的阻塞/隐藏判定）
    def is_node_blocked(self, node_id: str) -> bool:
        return self.blocks_node(node_id, "diffusion")

    def is_node_hidden(self, node_id: str) -> bool:
        return self.hides_node(node_id, "topk")

    def is_edge_blocked(self, src: str, relation: str, dst: str) -> bool:
        return self.blocks_edge(src, relation, dst, "diffusion")

    def is_edge_hidden(self, src: str, relation: str, dst: str) -> bool:
        return self.hides_edge(src, relation, dst, "topk")

    # ── 目标校验 ──────────────────────────────────────────

    def _target_exists(self, target_type: str, target_id: str) -> bool:
        """目标是否可被准确定位（§4.2-1）。无 kg 引用时跳过校验。"""
        if target_type == "system":
            return bool(str(target_id or "").strip())
        if self.kg is None:
            return True
        if target_type == "node":
            return target_id in self.kg.nodes
        src, rel, dst = parse_edge_key(target_id)
        if src is None:
            return False
        return any(e.src == src and e.dst == dst and e.relation == rel
                   for e in self.kg.edges)

    def target_status(self, lk: dict) -> dict:
        """锁目标当前是否还在图里（悬空锁要能看出来，不静默）。"""
        return {"target_missing": not self._target_exists(
            lk.get("target_type", "node"), lk.get("target_id", ""))}

    def _audit(self, action: str, lock: dict, detail: str = ""):
        self._history.append({
            "ts": now_str(),
            "action": action,
            "lock_id": lock.get("id"),
            "target_type": lock.get("target_type"),
            "target_id": lock.get("target_id"),
            "lock_type": lock.get("lock_type"),
            "scope": lock.get("scope"),
            "detail": detail,
        })
        if len(self._history) > HISTORY_LIMIT:
            self._history = self._history[-HISTORY_LIMIT:]

    # ── CRUD ──────────────────────────────────────────────

    def create(self, target_type: str, target_id: str, lock_type: str = "blocking",
               scope: str = "diffusion", reason: str = "", source: str = "manual",
               priority: int = 0, expires_at=None, metadata: dict = None) -> dict:
        target_type = str(target_type or "").strip()
        target_id = str(target_id or "").strip()
        if target_type not in TARGET_TYPES:
            return {"ok": False, "error": f"target_type 必须是 {TARGET_TYPES}"}
        if not target_id:
            return {"ok": False, "error": "target_id 不能为空"}
        if lock_type not in LOCK_TYPES:
            return {"ok": False, "error": f"lock_type 必须是 {LOCK_TYPES}"}
        if scope not in SCOPES:
            return {"ok": False, "error": f"scope 必须是 {SCOPES}"}
        if not self._target_exists(target_type, target_id):
            return {"ok": False,
                    "error": f"目标不存在，无法准确定位: {target_type} {target_id}"}

        with self._lock:
            self._seq += 1
            lock = self._normalize({
                "id": f"lock_{self._seq}",
                "target_type": target_type,
                "target_id": target_id,
                "lock_type": lock_type,
                "scope": scope,
                "reason": reason,
                "source": source if source in SOURCES else "manual",
                "priority": priority,
                "enabled": True,
                "created_at": now_str(),
                "updated_at": now_str(),
                "expires_at": expires_at,
                "metadata": metadata or {},
            })
            self._locks[lock["id"]] = lock
            self._audit("created", lock, reason)
            self._rebuild_gate_locked()
        self._save()
        logger.info(f"[CognitiveLock] + {lock['id']} {lock_type} "
                    f"{target_type}:{target_id} scope={scope}")
        return {"ok": True, "lock": lock}

    def update(self, lock_id: str, **fields) -> dict:
        with self._lock:
            lock = self._locks.get(lock_id)
            if lock is None:
                return {"ok": False, "error": f"锁不存在: {lock_id}"}

            for key in ("lock_type", "scope", "reason", "source", "priority",
                        "expires_at", "metadata", "enabled"):
                if key in fields and fields[key] is not None:
                    lock[key] = fields[key]
            if lock["lock_type"] not in LOCK_TYPES:
                return {"ok": False, "error": f"lock_type 必须是 {LOCK_TYPES}"}
            if lock["scope"] not in SCOPES:
                return {"ok": False, "error": f"scope 必须是 {SCOPES}"}
            lock["enabled"] = bool(lock["enabled"])
            lock["updated_at"] = now_str()
            self._audit("updated", lock, str(fields.get("reason", "")))
            self._rebuild_gate_locked()
        self._save()
        return {"ok": True, "lock": lock}

    def set_enabled(self, lock_id: str, enabled: bool) -> dict:
        return self.update(lock_id, enabled=bool(enabled))

    def delete(self, lock_id: str) -> dict:
        """删除锁本身。不触碰被锁对象（§4.2-6，测试项）。"""
        with self._lock:
            lock = self._locks.pop(lock_id, None)
            if lock is None:
                return {"ok": False, "error": f"锁不存在: {lock_id}"}
            self._audit("deleted", lock)
            self._rebuild_gate_locked()
        self._save()
        logger.info(f"[CognitiveLock] - {lock_id}（目标对象未改动）")
        return {"ok": True, "deleted": lock_id}

    def purge_expired(self, now: float = None) -> list:
        """把已过期的锁置为 enabled=False（保留记录，便于审计）。"""
        now = time.time() if now is None else now
        expired = []
        with self._lock:
            for lock in self._locks.values():
                exp = lock.get("expires_at")
                if not lock.get("enabled") or not exp:
                    continue
                try:
                    if float(exp) <= now:
                        lock["enabled"] = False
                        lock["updated_at"] = now_str()
                        self._audit("expired", lock)
                        expired.append(lock["id"])
                except (TypeError, ValueError):
                    continue
            if expired:
                self._rebuild_gate_locked()
        if expired:
            self._save()
            logger.info(f"[CognitiveLock] 过期停用 {len(expired)} 条: {expired}")
        return expired

    # ── 列表与状态 ────────────────────────────────────────

    def get(self, lock_id: str) -> dict:
        with self._lock:
            lock = self._locks.get(lock_id)
            return dict(lock) if lock else None

    def list(self, target_id: str = None, target_type: str = None,
             enabled_only: bool = False) -> list:
        with self._lock:
            out = []
            for lock in self._locks.values():
                if target_id and lock.get("target_id") != target_id:
                    continue
                if target_type and lock.get("target_type") != target_type:
                    continue
                if enabled_only and not lock.get("enabled"):
                    continue
                item = dict(lock)
                item.update(self.target_status(lock))
                out.append(item)
        # 稳定排序：priority 降序 → 创建时间升序
        out.sort(key=lambda x: (-int(x.get("priority", 0) or 0), x.get("created_at", "")))
        return out

    def effective_for(self, target_type: str, target_id: str) -> dict:
        """某对象的锁效果（§9.2 锁状态查询）：判定 + 生效锁 + 原因。"""
        hits = [lk for lk in self.list(target_id=target_id,
                                       target_type=target_type, enabled_only=True)]
        blocking = [lk for lk in hits if lk.get("lock_type") == "blocking"]
        non_blocking = [lk for lk in hits if lk.get("lock_type") == "non_blocking"]
        self._ensure_current()
        if target_type == "node":
            blocked = target_id in self.blocked_node_ids
            hidden = target_id in self.hidden_node_ids
        else:
            src, rel, dst = parse_edge_key(target_id)
            blocked = src is not None and (src, rel, dst) in self.blocked_edge_keys
            hidden = src is not None and (src, rel, dst) in self.hidden_edge_keys
        return {
            "target_type": target_type,
            "target_id": target_id,
            "blocked": bool(blocked),
            "hidden": bool(hidden),
            "locks": hits,
            "reason": (blocking or non_blocking)[0].get("reason", "") if hits else "",
        }

    def state(self) -> dict:
        with self._lock:
            locks = self.list()
            history = list(self._history[-20:])
        return {
            "count": len(locks),
            "enabled_count": sum(1 for lk in locks if lk.get("enabled")),
            "blocking_count": sum(1 for lk in locks
                                  if lk.get("lock_type") == "blocking"
                                  and lk.get("enabled")),
            "non_blocking_count": sum(1 for lk in locks
                                      if lk.get("lock_type") == "non_blocking"
                                      and lk.get("enabled")),
            "blocked_nodes": sorted(self.blocked_node_ids),
            "hidden_nodes": sorted(self.hidden_node_ids),
            "blocked_edges": [list(k) for k in sorted(self.blocked_edge_keys)],
            "hidden_edges": [list(k) for k in sorted(self.hidden_edge_keys)],
            "locks": locks,
            "history": history,
        }
