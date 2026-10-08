# skills/exploration.py — L 探索技能
# ============================================================================
# 探索 ≠ 随机走。每次探索都有目标（找村庄/找矿/看新地方）、搜索方向偏好、
# 停止条件（找到目标 / 危险 / 距离上限 / 时间上限）。
# 状态机：选路点（方向偏好 + 足迹最少的网格）→ 走过去 → 观察快照 →
# 检查目标是否达成 → 重复。足迹记在 LocationMemory（32 格网格桶）。
# ============================================================================

import math
import time

from skills.base import Skill, register, res_ok, res_fail, res_pending
from skills.movement import DIRECTIONS
from skills.observation import (_find_blocks, time_of_day_label,
                                HOSTILE_ENTITIES)

# 探索目标 → 达成判定（要找的方块/实体名）
EXPLORE_GOALS = {
    "village": {"blocks": ["bell", "villager"], "entity": "villager",
                "label": "村庄"},
    "iron": {"blocks": ["iron_ore"], "label": "铁矿"},
    "coal": {"blocks": ["coal_ore"], "label": "煤矿"},
    "cave": {"blocks": ["cave_air"], "label": "洞穴"},
    "water": {"blocks": ["water"], "label": "水域"},
    "tree": {"blocks": ["oak_log", "birch_log", "spruce_log"], "label": "树"},
    "villager": {"blocks": [], "entity": "villager", "label": "村民"},
}

GOAL_ALIASES = {"村庄": "village", "铁": "iron", "铁矿": "iron", "煤": "coal",
                "煤矿": "coal", "洞穴": "cave", "水": "water", "树": "tree",
                "村民": "villager"}


class _ExplorationBase(Skill):
    category = "exploration"
    sustained = True

    def _init_session(self, ctx, params, goal: str = ""):
        pos = ctx.position() or {}
        ctx.session.update({
            "goal": GOAL_ALIASES.get(goal, goal),
            "direction": str(params.get("direction") or ""),
            "origin": dict(pos),
            # 图上并列等价来源（autonomy goal_blocks）：目标词的物种不锁死
            # （2026-09-26 场景实测：cherry_log 目标看不见旁边的 oak_log）
            "goal_blocks": tuple(str(b) for b in (params.get("goal_blocks")
                                                  or []) if str(b)),
            "max_distance": float(params.get("max_distance",
                                             ctx.config.get("explore_max_distance", 64))),
            "deadline": time.time() + float(params.get("max_time",
                                                       ctx.config.get("explore_max_time_s", 300))),
            "step": float(params.get("step", ctx.config.get("explore_step", 16))),
            # 昼夜拦截开关（写了一年没人接线的既有字段）：认知层可以按"这次
            # 是带着目标去找东西"降级夜拦；危险生物临近（danger）不受此影响。
            "night_stop": bool(params.get("night_stop", True)),
            "phase": "pick", "visited_now": [],
            "findings": {}, "moved": False,
        })

    def _check_goal(self, ctx) -> dict:
        """目标达成检查：找到目标方块/实体 → 返回发现。
        词汇表（EXPLORE_GOALS）只是命名目标的别名；**未知目标名按方块名
        直接找**（§17 泛化：探索目标集合不由攻略表限定——图上的下一步、
        检测到的任何资源都可以是 goal）。"""
        goal = GOAL_ALIASES.get(ctx.session.get("goal", ""), ctx.session.get("goal", ""))
        spec = EXPLORE_GOALS.get(goal)
        if not spec:
            raw = str(ctx.session.get("goal", "") or "").strip().lower()
            if raw and raw != "any":
                from skills.observation import resolve_variants
                _, cands = resolve_variants(raw)
                # 图上下一步带来的并列等价来源（autonomy 的 goal_blocks）：
                # 2026-09-26 场景实测——目标词 cherry_log 锁死单物种，出生点
                # 旁真立着 oak_log 也"看不见"。带上并列物种逐个扫。
                gb = ctx.session.get("goal_blocks")
                if gb:
                    cands = tuple(str(b) for b in gb if str(b)) or cands
                for blk in cands:
                    hits = _find_blocks(ctx, blk, 24, 1)
                    if hits:
                        # 记入位置记忆（seen:<方块>）：gather 找不到时先回
                        # 这里，不再朝随机方向漂（2026-09-26 场景实测：橡木
                        # 在出生点，探索漂出 260 格）。
                        try:
                            ctx.locations.remember(f"seen:{blk}", hits[0],
                                                   kind="seen")
                        except Exception:
                            pass
                        return {"found": blk, "block": blk, "pos": hits[0]}
            return {}
        for blk in spec.get("blocks", []):
            hits = _find_blocks(ctx, blk, 24, 1)
            if hits:
                return {"found": spec["label"], "block": blk, "pos": hits[0]}
        ent_name = spec.get("entity")
        if ent_name:
            for e in ctx.state().get("nearbyEntities") or []:
                if e.get("name") == ent_name:
                    return {"found": spec["label"], "entity": ent_name,
                            "dist": e.get("dist")}
        return {}

    def _pick_waypoint(self, ctx) -> dict:
        """选下一个路点：方向偏好 × 足迹最少。不是随机——确定性评分。"""
        s = ctx.session
        pos = ctx.position() or {}
        dirs = list(DIRECTIONS.items())[:4]        # 只要四个基本方向
        # 上一条腿刚在这个方向如实失败（2026-09-25 真机：同方向反复重发
        # goto，每条腿 30-60s，一路硬撞到 max_time）。足迹罚分（×2/次）会
        # 压过任何加权惩罚，所以失败方向直接**剔除**——去别处绕，不去撞
        # 同一个门框；只剩它一个候选时才允许再试（世界会变）。
        dirs = [d for d in dirs if d[0] != s.get("failed_dir")] or dirs
        best, best_score = None, -1e9
        for name, (dx, dz) in dirs:
            cand = {"x": float(pos.get("x", 0)) + dx * s["step"],
                    "y": float(pos.get("y", 64)),
                    "z": float(pos.get("z", 0)) + dz * s["step"]}
            visits = ctx.locations.visit_count(cand)
            score = -visits * 2.0
            if s.get("direction") and name == s["direction"]:
                score += 3.0      # 用户/认知层给的搜索方向偏好
            origin = s.get("origin") or pos
            if math.hypot(cand["x"] - origin.get("x", 0),
                          cand["z"] - origin.get("z", 0)) > s["max_distance"]:
                score -= 10.0     # 超出距离上限的方向基本排除
            if score > best_score:
                best, best_score = cand, score
        return best or {"x": pos.get("x", 0), "y": pos.get("y", 64),
                        "z": pos.get("z", 0) - s["step"]}

    @staticmethod
    def _cardinal_dir(waypoint: dict, pos: dict) -> str:
        """航点相对当前位置的主方向名（四基本方向取分量最大者）。"""
        dx = float(waypoint.get("x", 0)) - float(pos.get("x", 0))
        dz = float(waypoint.get("z", 0)) - float(pos.get("z", 0))
        if abs(dx) <= 1e-6 and abs(dz) <= 1e-6:
            return ""
        return ("east" if dx > 0 else "west") if abs(dx) >= abs(dz) \
            else ("south" if dz > 0 else "north")

    def _stop_conditions(self, ctx) -> dict:
        s = ctx.session
        pos = ctx.position() or {}
        origin = s.get("origin") or pos
        if time.time() > s.get("deadline", 0):
            return "time_limit"
        if math.hypot(pos.get("x", 0) - origin.get("x", 0),
                      pos.get("z", 0) - origin.get("z", 0)) >= s["max_distance"]:
            return "distance_limit"
        # 生存风险：夜晚 + 敌对生物在 8 格内 → 暂停探索
        for e in ctx.state().get("nearbyEntities") or []:
            if str(e.get("name") or "") in HOSTILE_ENTITIES \
                    and float(e.get("dist") or 99) <= 8:
                return "danger"
        if time_of_day_label(ctx.state().get("timeOfDay")) == "night" \
                and s.get("night_stop", True):
            return "night"
        return ""

    def _advance(self, ctx) -> dict:
        s = ctx.session
        stop = self._stop_conditions(ctx)
        if stop:
            self.cancel(ctx)
            finding = self._check_goal(ctx)
            if not finding and not s.get("moved"):
                # 诚实（2026-09-24 真机）：出发那一刻就被拦（night/danger），
                # 还没迈出一步也没看到目标——不能 0 秒报"成功"。假成功会把
                # "什么都没做"写进能力记忆的成功率并触发 600s habituation，
                # 酿成探索黑屏（11:59:23 夜间 explore 0.0s"成功"→ 全家冷却
                # 到 12:09）。"世界此刻没准备好"是暂态失败，与 no_path 同类。
                return res_fail(f"preempted:{stop}",
                                describe=f"{stop}，这一步没迈出去，等条件允许再试")
            if (not finding and s.get("moved") and stop == "time_limit"
                    and not s.get("findings")):
                # 结算门（2026-09-24 真机 18:38：整段会话回执 ok 但全程零位移，
                # 225s 后"探索结束"报成功）：time_limit 收尾、什么也没发现时，
                # 用起点↔当下的真实位移补一道终审——一步没走就不算探索过。
                _p = ctx.position() or {}
                _o = s.get("origin") or {}
                if math.hypot(_p.get("x", 0) - _o.get("x", 0),
                              _p.get("z", 0) - _o.get("z", 0)) < 1.0:
                    return res_fail("path_stall:zero_displacement",
                                    describe="这一路其实没挪开过，不算探索成功")
            if finding:
                return res_ok(describe=f"探索目标达成：发现了{finding.get('found')}",
                              detail={**finding, "stop": stop,
                                      "observed": s.get("findings")})
            return res_ok(status="done",
                          describe=f"探索结束（{stop}），这次看到了 "
                                   f"{', '.join(sorted(s.get('findings', {}))) or '没什么特别的'}",
                          detail={"stop": stop, "observed": s.get("findings")})
        finding = self._check_goal(ctx)
        if finding:
            self.cancel(ctx)
            ctx.locations.mark_visited(ctx.position() or {})
            return res_ok(describe=f"探索目标达成：发现了{finding.get('found')}",
                          detail={**finding, "observed": s.get("findings")})
        phase = s.get("phase")
        if phase == "pick":
            wp = self._pick_waypoint(ctx)
            s["waypoint"] = wp
            ctx.locations.mark_visited(ctx.position() or {})
            ctx.bridge.stop_goto()
            r = ctx.bridge.goto_coords(wp["x"], wp["y"], wp["z"])
            if not r.get("ok"):
                self.cancel(ctx)
                return res_fail("no_path")
            s["phase"] = "walk"
            s["moved"] = True
            # 腿基线（2026-09-24 真机：pathfinder 回执 ok 后内部死，225s 零位移
            # 还"成功"结算）：moved 只证明指令发出去了，真没真走看位移。
            s["leg_t0"] = time.time()
            s["leg_pos0"] = dict(ctx.position() or {})
            _lbl = s.get("goal") if s.get("goal") not in ("", "any") \
                else (s.get("direction") or "未知区域")
            return res_pending(describe=f"朝{_lbl}的方向前进")
        if phase == "walk":
            raw = ctx.bridge.get_action_result() or {}
            st = str(raw.get("status", "")).lower()
            pos = ctx.position() or {}
            wp = s.get("waypoint") or {}
            if math.hypot(pos.get("x", 0) - wp.get("x", 0),
                          pos.get("z", 0) - wp.get("z", 0)) <= 2.5 or st in ("done", "partial"):
                s["phase"] = "observe"
            elif st == "failed":
                # 这条腿如实失败（noPath / 原始步态认输）。2026-09-25 真机：
                # 原地反复重发 goto，每条腿 30-60s，一路耗到 max_time 才
                # zero_displacement 收场——4 分钟站着不动。现在记住失败方向
                # （下一腿换向），连续几条腿全失败且全程没挪出 2 格就快败，
                # 把时间还给上层：换目标/换物种都比原地硬撞有用。
                s["stall_legs"] = int(s.get("stall_legs", 0)) + 1
                s["failed_dir"] = self._cardinal_dir(s.get("waypoint") or {},
                                                     pos)
                _o = s.get("origin") or {}
                _disp = math.hypot(pos.get("x", 0) - _o.get("x", 0),
                                   pos.get("z", 0) - _o.get("z", 0))
                if (s["stall_legs"] >= int(
                        ctx.config.get("explore_stall_legs_max", 3))
                        and _disp < 2.0):
                    self.cancel(ctx)
                    return res_fail(
                        "path_stall:repeated_leg_failure",
                        describe="连着几条腿都没迈出去，这一带暂时过不去，"
                                 "先停下别再原地撞")
                s["phase"] = "pick"    # 换个方向继续（诚实：此路不通）
            elif (s.get("leg_t0") and (s.get("leg_pos0") or {})
                    and time.time() - float(s["leg_t0"]) > float(
                        ctx.config.get("explore_stall_grace_s", 45))
                    and math.hypot(pos.get("x", 0)
                                   - s["leg_pos0"].get("x", 0),
                                   pos.get("z", 0)
                                   - s["leg_pos0"].get("z", 0)) < 1.0):
                # 宽限期内连 1 格都没挪=腿没动（pathfinder 假活 / MC 26.1 下
                # A* 静默无路）。先试**原始步态**（look+forward 朝航点转向走，
                # 纯执行层工程兜底），连续 6 步还挪不动才如实失败。
                _dx = float(wp.get("x", 0)) - float(pos.get("x", 0))
                _dz = float(wp.get("z", 0)) - float(pos.get("z", 0))
                # mineflayer look() 收弧度；视线向量=(-sin,-cos)（ray_trace.js
                # :29），朝向目标的 yaw = atan2(-dx, -dz)。旧版 degrees+正 dz
                # 双重错，这一步兜底从来没朝对过方向。
                ctx.bridge.look(math.atan2(-_dx, -_dz), 0.0)
                ctx.bridge.move("forward", 2.0)
                ctx.bridge.move("jump", 0.5)
                s["leg_pos0"] = dict(pos)          # 重置基线：看原始步态走不走
                s["leg_t0"] = time.time()
                s["raw_steps"] = int(s.get("raw_steps", 0)) + 1
                if s["raw_steps"] > 6:
                    self.cancel(ctx)
                    return res_fail("path_stall:legs_not_moving",
                                    describe="寻路和原始步态都迈不动，"
                                             "这一步先停下")
                return res_pending(describe="原始步态朝航点挪动中")
            return res_pending(describe="探索移动中")
        if phase == "observe":
            # 到达路点：观察快照记 findings（零 LLM）
            snap = ctx.state(refresh=True)
            for b in (snap.get("nearbyBlocks") or [])[:6]:
                s.setdefault("findings", {})[b.get("name")] = \
                    int(s.get("findings", {}).get(b.get("name"), 0)) + int(b.get("count", 1))
            for e in (snap.get("nearbyEntities") or []):
                s.setdefault("findings", {})[e.get("name")] = \
                    int(s.get("findings", {}).get(e.get("name"), 0)) + 1
            s["phase"] = "pick"
            # 到过一个真路点：腿是好的，失败计数清零（2026-09-25 快败门）
            s["stall_legs"] = 0
            s.pop("failed_dir", None)
            return res_pending(describe="观察了一个新地方")

    def poll(self, ctx):
        return self._advance(ctx)

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
        except Exception:
            pass


@register
class ExploreArea(_ExplorationBase):
    name = "explore_area"
    description = "带目标的区域探索（goal: village/iron/cave…或 any）"

    def check(self, ctx, params):
        return ctx.connected(), "not_connected"

    def start(self, ctx, params):
        goal = str(params.get("goal") or params.get("target") or "any")
        self._init_session(ctx, params, goal)
        # 立即迈出第一步（选路点 + 出发），不等下一次 poll
        return self._advance(ctx)


@register
class ExploreDirection(ExploreArea):
    name = "explore_direction"
    description = "朝指定方向探索（探索有方向偏好，仍带目标与停止条件）"

    def start(self, ctx, params):
        direction = str(params.get("direction") or params.get("target") or "north")
        params = dict(params)
        params["direction"] = direction if direction in DIRECTIONS else "north"
        goal = str(params.get("goal") or "any")
        self._init_session(ctx, params, goal)
        # 立即迈出第一步（选路点 + 出发）
        return self._advance(ctx)


@register
class ExploreUnknownRegion(ExploreArea):
    name = "explore_unknown_region"
    description = "探索没去过的区域（足迹最少的方向优先）"

    def start(self, ctx, params):
        self._init_session(ctx, params, str(params.get("goal") or "any"))
        ctx.session["direction"] = ""   # 不带方向偏好：足迹说了算
        return self._advance(ctx)


@register
class InvestigateLocation(Skill):
    name = "investigate_location"
    description = "去某个坐标调查（到达后环顾）"
    category = "exploration"

    def start(self, ctx, params):
        p = params.get("position")
        if not p and params.get("x") is not None:
            p = {"x": params.get("x"), "y": params.get("y", 64), "z": params.get("z")}
        if not p:
            return res_fail("missing_position")
        ctx.bridge.stop_goto()
        r = ctx.bridge.goto_coords(p["x"], p.get("y", 64), p["z"])
        if not r.get("ok"):
            return res_fail("no_path")
        ctx.session.update({"phase": "walk", "goal": params.get("goal") or "",
                            "deadline": time.time() + 90})
        return res_pending(describe="去调查目标地点")

    def poll(self, ctx):
        raw = ctx.bridge.get_action_result() or {}
        st = str(raw.get("status", "")).lower()
        if st in ("done", "partial"):
            # 到场核验：带着 goal 来的，先确认目标还在不在附近——不在
            # 就是记忆里的坐标已过期（2026-09-27 真机：熔炉从世界消失后
            # investigate 照样报成功，autonomy 反复回访废墟×N）。如实
            # 失败 goal_not_found，结算侧借机作废这格 seen 记忆。
            goal = str(ctx.session.get("goal") or "")
            if goal:
                try:
                    from skills.observation import _find_blocks, normalize_resource
                    g = normalize_resource(goal)
                    if g and not _find_blocks(ctx, g, 16, 1):
                        return res_fail("goal_not_found",
                                        describe=f"到场查看：{goal} 不在附近",
                                        detail={"goal": goal})
                except Exception:
                    pass  # 桥瞬断不计核验成败，走原路径
            from skills.observation import InspectArea
            return InspectArea().start(ctx, {})
        if st == "failed":
            return res_fail("no_path")
        if time.time() > float(ctx.session.get("deadline", 0)):
            return res_fail("timeout")
        return res_pending(describe="调查移动中")

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
        except Exception:
            pass


@register
class RevisitLocation(Skill):
    name = "revisit_location"
    description = "回到之前去过/标记的位置看看"

    def start(self, ctx, params):
        name = str(params.get("name") or params.get("target") or "")
        found_name, entry = ctx.locations.find(name) if name else (None, None)
        if not entry:
            return res_fail("location_unknown")
        return InvestigateLocation().start(ctx, {"position": entry})

    def poll(self, ctx):
        return InvestigateLocation().poll(ctx)

    def cancel(self, ctx):
        InvestigateLocation().cancel(ctx)


@register
class ReturnToKnownLocation(Skill):
    name = "return_to_known_location"
    description = "回到已知安全位置（家/上一个安全点）"
    category = "exploration"

    def start(self, ctx, params):
        name = str(params.get("name") or "")
        entry = None
        if name:
            _, entry = ctx.locations.find(name)
        if not entry:
            _, entry = ctx.locations.find("home", "家", "base", "last_safe", "出生")
        if not entry:
            return res_fail("location_unknown")
        ctx.bridge.stop_goto()
        r = ctx.bridge.goto_coords(entry["x"], entry.get("y", 64), entry["z"])
        if not r.get("ok"):
            return res_fail("no_path")
        ctx.session.update({"deadline": time.time() + 120, "dest": entry})
        return res_pending(describe="回去的路上")

    def poll(self, ctx):
        raw = ctx.bridge.get_action_result() or {}
        st = str(raw.get("status", "")).lower()
        if st in ("done", "partial"):
            return res_ok(describe="回来了")
        if st == "failed":
            return res_fail("no_path")
        if time.time() > float(ctx.session.get("deadline", 0)):
            return res_fail("timeout")
        return res_pending(describe="返程中")

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
        except Exception:
            pass


@register
class MarkLocation(Skill):
    name = "mark_location"
    description = "标记当前位置（带名字）"
    category = "exploration"

    def start(self, ctx, params):
        from skills.observation import RememberLocation
        return RememberLocation().start(ctx, params)


@register
class NavigateHome(ReturnToKnownLocation):
    name = "navigate_home"
    description = "回家"

    def start(self, ctx, params):
        params = dict(params)
        params.setdefault("name", "home")
        return super().start(ctx, params)
