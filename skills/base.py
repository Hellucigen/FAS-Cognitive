# skills/base.py — 技能基类 / 注册表 / 上下文（Skill Library 基础设施）
# ============================================================================
# 技能接口（pending 状态机约定）：
#   check(ctx, params) -> (ok, reason)     前置条件（用已有状态判断，不执行）
#   start(ctx, params) -> SkillResult      启动；长动作返回 status="pending"
#   poll(ctx) -> SkillResult               pending 时每次推进一小步
#   cancel(ctx)                            被中断时清理（停寻路/清控制状态）
#
# SkillContext 是技能与世界的唯一接口：bridge（HTTP 桥）+ 状态缓存 +
# 位置记忆 + 当前会话（一个时刻只有一个技能在执行——由 ActionManager 保证）。
# ============================================================================

import json
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# ── SkillResult（统一结构化结果）──────────────────────────

def res_ok(status="done", describe="", detail=None, **kw) -> dict:
    return {"ok": True, "status": status, "describe": describe,
            "detail": detail or {}, **kw}


def res_fail(reason: str, describe="", detail=None, **kw) -> dict:
    return {"ok": False, "status": "failed", "reason": str(reason)[:120],
            "describe": describe or f"失败: {reason}", "detail": detail or {}, **kw}


def res_pending(describe="", detail=None, **kw) -> dict:
    return {"ok": True, "status": "pending", "describe": describe,
            "detail": detail or {}, **kw}


def receipt_reason(raw: dict, default: str = "action_failed") -> str:
    """从 bot 动作回执里取真实失败原因（reason 或 error，不吞细节）。"""
    detail = (raw or {}).get("detail") or {}
    return str(detail.get("reason") or detail.get("error") or default)


# ── 位置记忆（remember_location / navigate_home 的存储）──

class LocationMemory:
    """具身位置记忆：她记住自己去过/标记过的地方（万物皆图之外的轻量
    空间索引；图谱里同时有 Location 事件节点，这里只存坐标）。

    2026-09-26 修（离线验收 P1）：进程里存在两个实例（autonomy 与
    SkillContext 各一，同一路径不同内存 dict），旧版"整文件覆写"会让
    后保存者抹掉前者刚写的条目。现在：每次操作先与磁盘合并（ts 新者
    为准，__visited__ 桶计数取 max），读侧按 mtime 惰性同步。"""

    def __init__(self, path: str = "data/mc_locations.json"):
        self.path = path
        self._lock = threading.Lock()
        self._places = {}
        self._mtime = None
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self._places = json.load(f) or {}
                self._mtime = os.path.getmtime(self.path)
        except Exception as e:
            logger.warning(f"[Locations] 载入失败: {e}")

    def _read_disk(self):
        """返回磁盘 dict；读不到/坏了返回 None（不参与合并）。"""
        try:
            if not os.path.exists(self.path):
                return None
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _merge_locked(self, incoming) -> dict:
        """磁盘视图与内存视图合并：同键以 ts 新者为准；__visited__
        桶计数取 max（单调足迹，无时间戳可依据）。调用方须已持锁。
        墓碑 __forgotten__ 取并集 max：另一个实例 forget 掉的键，本
        实例缓存里的旧条目不得复活（2026-09-27 真机：熔炉从世界消失
        后，技能侧旧缓存 mark_visited 时把 seen:furnace 又并回磁盘）。"""
        if not isinstance(incoming, dict):
            return dict(self._places)
        merged = dict(incoming)
        mine = self._places
        for k, v in mine.items():
            if k == "__visited__" or k == "__forgotten__":
                continue
            cur = merged.get(k)
            if (isinstance(v, dict) and isinstance(cur, dict)
                    and float(v.get("ts") or 0) < float(cur.get("ts") or 0)):
                continue          # 磁盘上的更新（另一实例刚写），保留磁盘
            merged[k] = v         # 我的更新，或磁盘条目无 ts 可比
        dead = dict(merged.get("__forgotten__") or {})
        if isinstance(mine.get("__forgotten__"), dict):
            for k, d in mine["__forgotten__"].items():
                dead[k] = max(float(dead.get(k) or 0), float(d or 0))
        for k, dts in list(dead.items()):
            cur = merged.get(k)
            if not isinstance(cur, dict):
                continue
            if float(cur.get("ts") or 0) < float(dts or 0):
                merged.pop(k, None)      # 墓碑新于条目 → 判死，不得复活
            elif k in dead:
                del dead[k]              # 重记的更新版（ts 新于墓碑）销碑
        merged["__forgotten__"] = dead
        vm = merged.get("__visited__")
        vs = mine.get("__visited__")
        if isinstance(vm, dict) and isinstance(vs, dict):
            for b, c in vs.items():
                try:
                    if int(vm.get(b) or 0) < int(c or 0):
                        vm[b] = int(c)
                except (TypeError, ValueError):
                    pass
            merged["__visited__"] = vm
        elif isinstance(vs, dict) and not isinstance(vm, dict):
            merged["__visited__"] = dict(vs)
        return merged

    def _sync_locked(self):
        """读侧惰性同步：文件被别的实例改过就并进内存。"""
        try:
            m = os.path.getmtime(self.path) if os.path.exists(self.path) else None
        except OSError:
            m = None
        if m is not None and m != self._mtime:
            merged = self._merge_locked(self._read_disk())
            self._places = merged
            self._mtime = m

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._places, f, ensure_ascii=False, indent=1)
            self._mtime = os.path.getmtime(self.path)
        except Exception as e:
            logger.warning(f"[Locations] 保存失败: {e}")

    def remember(self, name: str, pos: dict, kind: str = "place") -> dict:
        name = str(name or "place").strip()[:40]
        entry = {"x": round(float(pos.get("x", 0)), 1),
                 "y": round(float(pos.get("y", 0)), 1),
                 "z": round(float(pos.get("z", 0)), 1),
                 "kind": kind, "ts": time.time()}
        with self._lock:
            self._places = self._merge_locked(self._read_disk())
            self._places[name] = entry
            # 重记 = 墓碑的效力被新事实覆盖，销碑
            dead = self._places.get("__forgotten__")
            if isinstance(dead, dict) and name in dead:
                dead.pop(name, None)
            self._places.setdefault("__visited__", {})
            self._save()
        return entry

    def forget(self, name: str, ts: float = None):
        """删除一条记忆并记墓碑（seen: 点被证伪时调用——方块可能已被
        挖掉/随世界消失，留着它会让 gather 反复回一个空位，另一实例的
        旧缓存也会把它并回磁盘）。"""
        ts = time.time() if ts is None else float(ts)
        with self._lock:
            self._places = self._merge_locked(self._read_disk())
            if name in self._places:
                self._places.pop(name, None)
            dead = self._places.setdefault("__forgotten__", {})
            dead[name] = max(float(dead.get(name) or 0), ts)
            self._save()

    def get(self, name: str):
        with self._lock:
            self._sync_locked()
            return dict(self._places.get(name) or {})

    def find(self, *keywords: str):
        """按关键词模糊查找（"家"匹配 home/家/base…）。"""
        with self._lock:
            self._sync_locked()
            items = list(self._places.items())
        for key, entry in items:
            if key.startswith("__"):
                continue
            if any(k.lower() in key.lower() for k in keywords if k):
                return key, dict(entry)
        return None, None

    def mark_visited(self, pos: dict):
        """探索足迹：32 格网格桶（探索技能用来去没去过的地方）。"""
        try:
            gx, gz = int(float(pos.get("x", 0)) // 32), int(float(pos.get("z", 0)) // 32)
        except (TypeError, ValueError):
            return
        with self._lock:
            self._places = self._merge_locked(self._read_disk())
            visited = self._places.setdefault("__visited__", {})
            bucket = f"{gx},{gz}"
            visited[bucket] = visited.get(bucket, 0) + 1
            if len(visited) > 2000:
                visited.pop(next(iter(visited)))
            self._save()

    def visit_count(self, pos: dict) -> int:
        try:
            gx, gz = int(float(pos.get("x", 0)) // 32), int(float(pos.get("z", 0)) // 32)
        except (TypeError, ValueError):
            return 0
        with self._lock:
            self._sync_locked()
            return int(self._places.get("__visited__", {}).get(f"{gx},{gz}", 0))


# ── SkillContext（技能的世界接口）────────────────────────

class SkillContext:
    def __init__(self, bridge=None, config=None, locations: LocationMemory = None):
        import minecraft.bridge as _bridge
        self.bridge = bridge or _bridge
        self.config = config or {}
        self.locations = locations or LocationMemory()
        self.session = {}            # 当前技能的运行态（ActionManager 独占写）
        self._state_cache = None
        self._state_ts = 0.0
        self._inv_cache = None
        self._inv_ts = 0.0

    # ── 状态（短 TTL 缓存：技能状态机一次 poll 内多次读不刷桥）──

    def state(self, refresh: bool = False) -> dict:
        now = time.time()
        if refresh or self._state_cache is None or now - self._state_ts > 0.8:
            self._state_cache = self.bridge.get_state() or {}
            self._state_ts = now
        return self._state_cache

    def inventory(self, refresh: bool = False) -> list:
        now = time.time()
        if refresh or self._inv_cache is None or now - self._inv_ts > 1.5:
            inv = self.bridge.inventory()
            self._inv_cache = inv.get("items", []) if isinstance(inv, dict) else []
            self._inv_ts = now
        return self._inv_cache

    def connected(self) -> bool:
        return bool(self.state().get("connected"))

    def position(self) -> dict:
        return self.state().get("position") or {}

    def invalidate(self):
        self._state_cache = None
        self._inv_cache = None


# ── Skill 基类与注册表 ────────────────────────────────────

class Skill:
    """技能基类：无实例状态（运行态全在 ctx.session），可安全做单例。"""
    name = ""
    category = ""
    description = ""
    sustained = False            # 持续型技能（跟随/警戒）：只有超时/失败/被取消才结束

    def check(self, ctx: SkillContext, params: dict):
        return True, ""

    def start(self, ctx: SkillContext, params: dict) -> dict:
        raise NotImplementedError

    def poll(self, ctx: SkillContext) -> dict:
        return res_fail("not_pending", describe="该技能没有进行中的会话")

    def cancel(self, ctx: SkillContext):
        pass


REGISTRY = {}


def register(skill_cls):
    s = skill_cls()
    if not s.name:
        raise ValueError(f"{skill_cls.__name__} 缺少 name")
    REGISTRY[s.name] = s
    return skill_cls


def get(name: str):
    return REGISTRY.get(str(name or "").strip())


def has(name: str) -> bool:
    return str(name or "").strip() in REGISTRY


def all_skills() -> list:
    return [{"name": s.name, "category": s.category,
             "description": s.description, "sustained": s.sustained}
            for s in REGISTRY.values()]


def run(name: str, ctx: SkillContext, params: dict = None,
        first_poll_delay: float = 0.8) -> dict:
    """执行技能的统一入口（ActionManager / 具身层都走这里）。

    会话约定：ctx.session["skill"] 记录当前 pending 技能名；同名调用 → poll()；
    新技能调用 → 先 cancel 旧的（由 ActionManager 决定，这里直接拒绝并提示）。
    """
    skill = get(name)
    if skill is None:
        return res_fail(f"unknown_skill:{name}")
    params = params or {}
    if ctx.session.get("skill"):
        if ctx.session.get("skill") == name:
            return skill.poll(ctx)
        return res_fail(f"skill_busy:{ctx.session['skill']}",
                        describe=f"另一个技能({ctx.session['skill']})正在进行")
    try:
        ok, reason = skill.check(ctx, params)
        if not ok:
            return res_fail(reason)
        out = skill.start(ctx, params)
    except Exception as e:
        logger.warning(f"[Skill] {name} 执行异常: {e}")
        return res_fail(f"skill_error:{e}")
    if out.get("status") == "pending":
        ctx.session["skill"] = name
        ctx.session["started_at"] = time.time()
        ctx.session["poll_after"] = time.time() + first_poll_delay
        ctx.session.setdefault("steps", 0)
    return out


def poll(ctx: SkillContext) -> dict:
    """推进当前 pending 技能（ActionManager tick 调用）。"""
    name = ctx.session.get("skill")
    skill = get(name) if name else None
    if skill is None:
        ctx.session.clear()
        return res_ok(status="done", describe="没有进行中的技能")
    if time.time() < float(ctx.session.get("poll_after", 0)):
        return res_pending(describe=f"{name} 进行中")
    try:
        out = skill.poll(ctx)
    except Exception as e:
        logger.warning(f"[Skill] {name} poll 异常: {e}")
        out = res_fail(f"skill_error:{e}")
    if out.get("status") in ("done", "failed", "partial"):
        # partial 也是终态：ActionManager._tick_inflight 按 done/partial 结算
        # 销账（action_system.py:394），若不清 session，残留的 skill 键会让
        # 下一个同名 run() 走 poll 分支"续跑"死会话——新目标被旧会话劫持，
        # 秒回 partial→结算 ok dur=0（2026-09-26 离线验收 §16 抓到）。
        skill.cancel(ctx)
        ctx.session.clear()
        ctx.invalidate()
    return out


def cancel(ctx: SkillContext) -> dict:
    """取消当前技能（优先级中断时由 ActionManager 调用）。"""
    name = ctx.session.get("skill")
    skill = get(name) if name else None
    try:
        if skill is not None:
            skill.cancel(ctx)
    except Exception as e:
        logger.warning(f"[Skill] {name} cancel 异常: {e}")
    ctx.session.clear()
    return {"ok": True, "cancelled": name}
