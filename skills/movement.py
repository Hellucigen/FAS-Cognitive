# skills/movement.py — A 移动 + M 导航 技能
# ============================================================================
# 所有"位置移动"类技能：寻路（含卡住检测/恢复/超时）、跟随、视角、
# 姿态控制、保持距离、回家。长动作是 pending 状态机：每次 poll 推进一步，
# 从不阻塞（tick 驱动）。危险规避：寻路目标尽量用已知安全 y；卡住先跳，
# 再侧移，仍卡住才如实失败（no_path/stuck）。
# ============================================================================

import math
import time

from skills.base import (Skill, register, res_ok, res_fail, res_pending)


def _dist(a: dict, b: dict) -> float:
    try:
        return math.sqrt((float(a.get("x", 0)) - float(b.get("x", 0))) ** 2
                         + (float(a.get("y", 0)) - float(b.get("y", 0))) ** 2
                         + (float(a.get("z", 0)) - float(b.get("z", 0))) ** 2)
    except (TypeError, ValueError):
        return 1e9


def _xz_dist(a: dict, b: dict) -> float:
    try:
        return math.hypot(float(a.get("x", 0)) - float(b.get("x", 0)),
                          float(a.get("z", 0)) - float(b.get("z", 0)))
    except (TypeError, ValueError):
        return 1e9


def _goto_y(target: dict, pos: dict) -> float:
    """寻路 y：显式给定优先；缺省用**当前高度**——绝不默认 0。
    （旧版 params.get("y",0) 让没有 y 的目标变成床岩寻路：必定 stuck，
    且失败还被归因成"这条路走不通"的假世界知识。）"""
    y = (target or {}).get("y")
    if y is None:
        y = (pos or {}).get("y", 64)
    try:
        y = float(y)
    except (TypeError, ValueError):
        y = float((pos or {}).get("y", 64))
    if not math.isfinite(y):
        y = float((pos or {}).get("y", 64))
    return y


_RAW_WALK_UNTIL = 0.0   # 原始步态走通过 → 10 分钟内后续导航直接用它（模块级）

# 方向词 → 单位向量（探索/移动指令用）
DIRECTIONS = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0),
              "北": (0, -1), "南": (0, 1), "东": (1, 0), "西": (-1, 0),
              "前": (0, -1), "后": (0, 1), "左": (-1, 0), "右": (1, 0),
              # B8 真机（00:14 unknown_direction:forward）：反射层输出的是英文
              # 相对词（minecraft.reflex.py:110），词表只认中文单字——补数据行。
              "forward": (0, -1), "back": (0, 1), "left": (-1, 0), "right": (1, 0)}


class _NavigateBase(Skill):
    """寻路基类：卡住检测 + 跳跃恢复 + 侧移恢复 + 超时。"""
    category = "movement"
    arrival_radius = 1.5

    def _start_goto(self, ctx, target: dict, timeout: float) -> dict:
        pos = ctx.position()
        if not pos:
            return res_fail("no_position")
        target = dict(target)
        target["y"] = _goto_y(target, pos)
        # 寻路腿最近 10 分钟内被判死过 → 直接原始步态，不再付阶梯等待
        if time.time() < _RAW_WALK_UNTIL:
            ctx.session.update({"target": target,
                                "timeout_at": time.time() + timeout,
                                "raw_walk": True, "raw_started": time.time(),
                                "last_pos": dict(pos),
                                "last_progress": time.time(), "recovered": 9})
            return res_pending(describe="原始步态接近（寻路不可用）",
                               detail={"target": target})
        r = ctx.bridge.goto_coords(target["x"], target["y"], target["z"])
        if not r.get("ok"):
            return res_fail(r.get("reason") or "goto_failed")
        ctx.session.update({
            "target": target, "timeout_at": time.time() + timeout,
            "last_pos": dict(pos), "last_progress": time.time(),
            "recovered": 0,
        })
        return res_pending(describe=f"寻路到 ({target['x']:.0f},{target['z']:.0f})",
                           detail={"target": target})

    def poll(self, ctx):
        s = ctx.state(refresh=True)
        if not s.get("connected"):
            return res_fail("not_connected")
        pos = s.get("position") or {}
        target = ctx.session.get("target") or {}
        now = time.time()
        started = float(ctx.session.get("started_at", now))
        # 原始步态模式：每拍朝目标转向走一步，到达判定在此
        if ctx.session.get("raw_walk") and target:
            if _dist(pos, target) <= self.arrival_radius + 0.8:
                ctx.bridge.move("forward", 0.1)
                return res_ok(describe=f"到达 ({target.get('x', 0):.0f},{target.get('z', 0):.0f})",
                              detail={"target": target, "position": pos,
                                      "raw_walk": True})
            return self._raw_step(ctx, pos, target, now)
        # ── 回执优先：bot 侧寻路的真实状态（goal_reached / noPath）──
        raw = ctx.bridge.get_action_result() or {}
        rn = str(raw.get("name") or "").lower()   # 单槽回执：只采信本动作的
        st = str(raw.get("status", "")).lower()
        if rn in ("", "goto"):
            if st in ("done", "partial"):
                ctx.bridge.sprint(False)
                return res_ok(describe=f"到达 ({target.get('x', 0):.0f},{target.get('z', 0):.0f})",
                              detail={"target": target, "position": pos})
            if st == "failed":
                reason = str((raw.get("detail") or {}).get("reason") or "no_path")
                if reason.lower() in ("nopath", "no path"):
                    reason = "no_path"    # 归一：暂态集合/causal 豁免都按 no_path 认
                return res_fail(reason, detail={"target": target})
        # ── 位置到达（回执丢失时的兜底；**3D 真距离**——启动后 2 秒内不判）──
        if now - started > 2.0 and pos and target:
            if _dist(pos, target) <= self.arrival_radius:
                ctx.bridge.sprint(False)
                ctx.bridge.stop_goto()
                return res_ok(describe=f"到达 ({target.get('x', 0):.0f},{target.get('z', 0):.0f})",
                              detail={"target": target, "position": pos,
                                      "dist3d": round(_dist(pos, target), 2)})
        if now > float(ctx.session.get("timeout_at", now + 60)):
            ctx.bridge.stop_goto()
            return res_fail("timeout", detail={"target": target})
        # 卡住检测：3 秒无位移 → 恢复动作（跳 → 侧移 → 重新寻路 → 失败）
        last = ctx.session.get("last_pos") or pos
        moved = _dist(pos, last)
        if moved > 0.6:
            ctx.session["last_pos"] = dict(pos)
            ctx.session["last_progress"] = now
            ctx.session["recovered"] = 0
            return res_pending(describe="移动中")
        if now - float(ctx.session.get("last_progress", now)) >= 3.0 and target:
            rec = int(ctx.session.get("recovered", 0))
            if rec == 0:
                ctx.bridge.move("jump", 0.6)
            elif rec == 1:
                # 侧移一步绕障
                side = "left" if ctx.session.get("_side", "left") == "left" else "right"
                ctx.session["_side"] = "right" if side == "left" else "left"
                ctx.bridge.move(side, 0.8)
            elif rec == 2:
                # 重新寻路（旧 goal 可能已失效）
                ctx.bridge.stop_goto()
                ctx.bridge.goto_coords(target["x"], _goto_y(target, pos),
                                       target["z"])
            elif rec == 3:
                # 原始步态兜底（2026-09-25 根因：MC 26.1 下方块物性数据
                # 不被寻路栈认识 → A* 静默无路，寻路腿从出生就是死的）。
                # look+forward 原始转向走，不依赖寻路。一旦走通，10 分钟内
                # 的后续导航直接用原始步态（模块级记忆，省掉阶梯等待）。
                ctx.bridge.stop_goto()
                ctx.session["raw_walk"] = True
                ctx.session["raw_started"] = now
                return self._raw_step(ctx, pos, target, now)
            else:
                ctx.bridge.stop_goto()
                return res_fail("stuck", detail={"target": target,
                                                 "position": pos})
            ctx.session["recovered"] = rec + 1
            ctx.session["last_progress"] = now
        return res_pending(describe="移动中")

    # ── 原始步态（pathfinder 不可用时的工程兜底；纯执行层，认知无感）──
    def _raw_step(self, ctx, pos, target, now):
        dx = float(target.get("x", 0)) - float(pos.get("x", 0))
        dz = float(target.get("z", 0)) - float(pos.get("z", 0))
        # mineflayer look() 收弧度；视线向量=(-sin,-cos)（ray_trace.js:29），
        # 朝向目标的 yaw = atan2(-dx, -dz)。旧版 degrees+正 dz 双重错，
        # 原始步态从来没朝对过方向（对照 skills/exploration.py 的修正）。
        ctx.bridge.look(math.atan2(-dx, -dz), 0.0)
        ctx.bridge.move("forward", 2.0)
        last = ctx.session.get("last_pos") or pos
        if _dist(pos, last) < 0.3:
            ctx.bridge.move("jump", 0.6)   # 前方有台阶/方块：跳
        else:
            global _RAW_WALK_UNTIL
            _RAW_WALK_UNTIL = time.time() + 600.0
        ctx.session["last_pos"] = dict(pos)
        if now - float(ctx.session.get("raw_started", now)) > 40.0:
            ctx.bridge.stop_goto()
            return res_fail("stuck", detail={"target": target,
                                             "position": pos,
                                             "raw_walk": True})
        return res_pending(describe="原始步态接近中")

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
            ctx.bridge.sprint(False)
        except Exception:
            pass


@register
class WalkTo(_NavigateBase):
    name = "walk_to"
    description = "走到指定世界坐标（寻路 + 卡住恢复 + 超时）"

    def check(self, ctx, params):
        if not (isinstance(params.get("x"), (int, float))
                and isinstance(params.get("z"), (int, float))):
            return False, "missing_coords"
        if not ctx.connected():
            return False, "not_connected"
        return True, ""

    def start(self, ctx, params):
        target = {"x": float(params["x"]), "y": params.get("y"),
                  "z": float(params["z"])}   # y 缺省 → _start_goto 用当前高度
        timeout = float(params.get("timeout",
                                   ctx.config.get("navigate_timeout_s", 90)))
        return self._start_goto(ctx, target, timeout)


@register
class RunTo(WalkTo):
    name = "run_to"
    description = "疾跑到指定坐标"

    def start(self, ctx, params):
        ctx.bridge.sprint(True)
        return super().start(ctx, params)


@register
class GoDirection(_NavigateBase):
    """朝方向词走一段距离（不是探索——只是移动）。"""
    name = "go_direction"
    description = "朝某方向走 N 格（north/south/east/west/北/南/东/西）"
    arrival_radius = 2.0

    def check(self, ctx, params):
        d = str(params.get("direction") or "")
        if d not in DIRECTIONS:
            return False, f"unknown_direction:{d}"
        return ctx.connected(), "not_connected"

    def start(self, ctx, params):
        pos = ctx.position()
        if not pos:
            return res_fail("no_position")
        dx, dz = DIRECTIONS[str(params.get("direction"))]
        step = float(params.get("distance", 8))
        target = {"x": float(pos.get("x", 0)) + dx * step,
                  "y": float(pos.get("y", 64)),
                  "z": float(pos.get("z", 0)) + dz * step}
        return self._start_goto(ctx, target,
                                float(params.get("timeout", 60)))


@register
class NavigateToEntity(_NavigateBase):
    name = "navigate_to_entity"
    description = "走到可见实体附近（保持指定距离）"
    arrival_radius = 2.5

    def check(self, ctx, params):
        return bool(params.get("entity")), "missing_entity"

    def _find_entity(self, ctx, name):
        s = ctx.state()
        for e in s.get("nearbyEntities", []) or []:
            if name in (e.get("name"), e.get("displayName")):
                return e
        for p in s.get("playersNearby", []) or []:
            if name == p.get("name"):
                # B8 真机实锤（22:33 "过来"）：玩家只在 playersNearby，bot 侧
                # /goto 按实体找不到人 → goto_entity 恒 entity_not_visible。
                # 标记来源，_begin 改用 bot.follow（GoalFollow 认玩家）。
                return {"name": p.get("name"), "rel": p.get("rel"),
                        "dist": p.get("dist"), "player": True}
        return None

    def _begin(self, ctx, params, ent):
        """发起**动态**追击：bot 侧 GoalFollow（goto_entity）会随目标移动
        持续重算路径。旧版按快照 rel 缩放出一个冻结坐标一次性寻路——
        目标一走就追不上（实测 cow 62s timeout、chicken stuck），到达与否
        说的都不是真话。快照坐标只留作汇报字段。"""
        name = str(params.get("entity") or ctx.session.get("entity") or "")
        if not name:
            return res_fail("missing_entity")
        pos = ctx.position()
        if not pos:
            return res_fail("no_position")
        keep = float(params.get("keep_distance", self.arrival_radius))
        if ent.get("player"):
            # 目标是玩家：bot.follow（GoalFollow）是唯一认玩家的原语；到达
            # 判定照旧走 3D 实测（_poll_dynamic 的 d3<=keep），不改变语义。
            if not ctx.bridge.follow(name):
                return res_fail("goto_failed", detail={"entity": name,
                                                       "player": True})
            ctx.session["nav_player"] = True
        else:
            r = ctx.bridge.goto_entity(name, range_=keep)
            if not r.get("ok"):
                reason = str(r.get("reason") or "goto_failed")
                return res_fail("entity_not_visible"
                                if reason == "entity_not_visible" else reason)
        rel = ent.get("rel") or {}
        ctx.session.update({
            "entity": name, "keep": keep, "nav_dynamic": True,
            "target": {"x": float(pos.get("x", 0)) + float(rel.get("dx", 0)),
                       "y": float(pos.get("y", 0)) + float(rel.get("dy", 0)),
                       "z": float(pos.get("z", 0)) + float(rel.get("dz", 0))},
            "timeout_at": time.time() + float(params.get("timeout", 60)),
            "last_pos": dict(pos), "last_progress": time.time(),
            "recovered": 0, "gone_since": None,
        })
        d0 = float(ent.get("dist") or 0)
        return res_pending(describe=f"追击 {name}（动态追踪，起距 {d0:.0f} 格）",
                           detail={"entity": name, "keep": keep, "dynamic": True})

    def _measure(self, ctx, ent):
        """实测 3D 距离（rel 三分量优先；缺 rel 退回桥报 dist）。"""
        rel = (ent or {}).get("rel") or {}
        try:
            return math.sqrt(float(rel.get("dx", 0)) ** 2
                             + float(rel.get("dy", 0)) ** 2
                             + float(rel.get("dz", 0)) ** 2)
        except (TypeError, ValueError):
            return float((ent or {}).get("dist") or 1e9)

    def _halt(self, ctx):
        """终止追击的原语清理：goto 与玩家 follow 都停（多余一次无害）。"""
        ctx.bridge.stop_goto()
        if ctx.session.get("nav_player"):
            try:
                ctx.bridge.stopfollow()
            except Exception:
                pass

    def _poll_dynamic(self, ctx):
        s = ctx.state(refresh=True)
        now = time.time()
        name = str(ctx.session.get("entity") or "")
        keep = float(ctx.session.get("keep", self.arrival_radius))
        if not s.get("connected"):
            self._halt(ctx)
            return res_fail("not_connected")
        pos = s.get("position") or {}
        started = float(ctx.session.get("started_at", now))
        ent = self._find_entity(ctx, name)
        d3 = self._measure(ctx, ent) if ent else None
        if ent:
            ctx.session["gone_since"] = None
            ctx.session["target"] = {   # 快照只供失败/汇报引用，不用于判定
                "x": float(pos.get("x", 0)) + float((ent.get("rel") or {}).get("dx", 0)),
                "y": float(pos.get("y", 0)) + float((ent.get("rel") or {}).get("dy", 0)),
                "z": float(pos.get("z", 0)) + float((ent.get("rel") or {}).get("dz", 0))}
        # ── 回执：name 匹配 + 新鲜才采信（GoalFollow 的 goal_reached=真到位；
        #    noPath=绕不过去 → 如实 unreachable，绝不"再 bump 一下"假重试）──
        raw = ctx.bridge.get_action_result() or {}
        if str(raw.get("name") or "") == "goto_entity":
            fresh = float(raw.get("startedAt") or 0) / 1000.0 >= started - 2.0
            st = str(raw.get("status") or "").lower()
            if fresh and st == "done":
                self._halt(ctx)
                return res_ok(describe=f"到 {name} 附近了"
                                       + (f"（{d3:.1f} 格）" if d3 is not None else ""),
                              detail={"entity": name, "receipt": "goal_reached",
                                      "dist3d": round(d3, 2) if d3 is not None else None,
                                      "position": pos})
            if fresh and st == "failed" and str(
                    (raw.get("detail") or {}).get("reason") or "").lower() in ("nopath", "no_path"):
                self._halt(ctx)
                return res_fail("unreachable",
                                detail={"entity": name, "keep": keep,
                                        "target_last": ctx.session.get("target"),
                                        "position": pos})
        # ── 实测到达（3D：XZ 贴近但头顶/脚下差几格 = 没到）──
        if d3 is not None and d3 <= keep:
            self._halt(ctx)
            dy = float((ent.get("rel") or {}).get("dy", 0))
            return res_ok(describe=f"到 {name} 附近了（{d3:.1f} 格，高度差 {dy:.0f}）",
                          detail={"entity": name, "dist3d": round(d3, 2),
                                  "dy": round(dy, 2), "position": pos})
        # ── 目标失联：20s 宽限（FollowEntity 先例）→ 如实 target_lost ──
        if d3 is None:
            gone = ctx.session.get("gone_since")
            if gone is None:
                ctx.session["gone_since"] = now
            elif now - gone > 20.0:
                self._halt(ctx)
                return res_fail("target_lost", detail={
                    "entity": name,
                    "last_target": ctx.session.get("target"),
                    "gone_s": round(now - float(gone), 1)})
        # ── 超时（如实：带当前实测距离）──
        if now > float(ctx.session.get("timeout_at", now + 60)):
            self._halt(ctx)
            return res_fail("timeout", detail={
                "entity": name,
                "dist3d": round(d3, 2) if d3 is not None else None,
                "target_last": ctx.session.get("target")})
        # ── 卡住阶梯（自己不动才算卡；重追发 goto_entity 而不是快照点）──
        last = ctx.session.get("last_pos") or pos
        if pos and _dist(pos, last) > 0.6:
            ctx.session["last_pos"] = dict(pos)
            ctx.session["last_progress"] = now
            ctx.session["recovered"] = 0
        elif pos and now - float(ctx.session.get("last_progress", now)) >= 3.0:
            rec = int(ctx.session.get("recovered", 0))
            if rec == 0:
                ctx.bridge.move("jump", 0.6)
            elif rec == 1:
                side = "left" if ctx.session.get("_side", "left") == "left" else "right"
                ctx.session["_side"] = "right" if side == "left" else "left"
                ctx.bridge.move(side, 0.8)
            elif rec == 2:
                self._halt(ctx)
                if ctx.session.get("nav_player"):
                    ctx.bridge.follow(name)
                else:
                    ctx.bridge.goto_entity(name, range_=keep)
            else:
                self._halt(ctx)
                return res_fail("stuck", detail={
                    "entity": name, "position": pos,
                    "dist3d": round(d3, 2) if d3 is not None else None})
            ctx.session["recovered"] = rec + 1
            ctx.session["last_progress"] = now
        return res_pending(describe=f"追击 {name} 中"
                                   + (f"（还差 {d3:.0f} 格）" if d3 is not None
                                      else "（目标暂不可见）"))

    def start(self, ctx, params):
        name = str(params["entity"])
        ent = self._find_entity(ctx, name)
        if ent:
            return self._begin(ctx, params, ent)
        # 刚连接/渲染距离边界：桥还没把这个实体同步过来 ≠ 走不过去。
        # 给一段可见宽限（pending 状态机轮询刷新，绝不阻塞），宽限内
        # 出现就照常出发；到期仍不见才如实 entity_not_visible。
        grace = float(ctx.config.get("navigate_visible_grace_s", 20) or 0)
        if grace > 0:
            now = time.time()
            ctx.session.update({"entity": name, "nav_params": dict(params),
                                "search_from": now, "search_until": now + grace})
            return res_pending(describe=f"先找找 {name} 在哪",
                               detail={"entity": name, "grace_s": grace})
        return res_fail("entity_not_visible")

    def poll(self, ctx):
        if ctx.session.get("nav_dynamic"):
            return self._poll_dynamic(ctx)
        # ── 还在可见宽限期里：轮询等实体同步 ──
        s = ctx.state(refresh=True)
        if not s.get("connected"):
            return res_fail("not_connected")
        name = str(ctx.session.get("entity") or "")
        ent = self._find_entity(ctx, name)
        if ent:
            return self._begin(
                ctx, ctx.session.get("nav_params") or {"entity": name}, ent)
        now = time.time()
        if now > float(ctx.session.get("search_until", 0)):
            return res_fail("entity_not_visible",
                            detail={"entity": name,
                                    "searched_s": round(
                                        now - float(ctx.session.get(
                                            "search_from", now)), 1)})
        return res_pending(describe=f"还在找 {name}")


@register
class FollowEntity(Skill):
    """跟随玩家/实体：bot 侧低层控制器维持，持续型（直到取消/超时/目标消失）。"""
    name = "follow_entity"
    description = "跟随指定玩家（持续行为；目标走就跟着，停就停下）"
    category = "movement"
    sustained = True

    def check(self, ctx, params):
        return bool(params.get("entity") or params.get("player")), "missing_target"

    def start(self, ctx, params):
        player = str(params.get("entity") or params.get("player"))
        r = ctx.bridge.call("/follow", {"player": player})
        if not r.get("ok"):
            return res_fail(r.get("reason") or "follow_failed")
        timeout = float(params.get("timeout", ctx.config.get("follow_timeout_s", 600)))
        ctx.session["target"] = player
        ctx.session["timeout_at"] = time.time() + timeout
        ctx.session["gone_since"] = None
        # 自主跟随 = "追上就完"；显式"一起走"承诺不带此标志，陪到超时为止
        ctx.session["release_when_close"] = bool(params.get("release_when_close"))
        ctx.session["close_since"] = None
        return res_pending(describe=f"开始跟随 {player}", detail={"target": player})

    def poll(self, ctx):
        s = ctx.state(refresh=True)
        target = ctx.session.get("target")
        if time.time() > float(ctx.session.get("timeout_at", 0)):
            self.cancel(ctx)
            return res_ok(describe=f"跟随 {target} 时间到，停下了",
                          detail={"target": target, "timed_out": True})
        names = [p.get("name") for p in s.get("playersNearby", []) or []]
        ents = [e.get("name") for e in s.get("nearbyEntities", []) or []]
        if target not in names and target not in ents:
            gone = ctx.session.get("gone_since")
            if gone is None:
                ctx.session["gone_since"] = time.time()
            elif time.time() - gone > 20.0:
                self.cancel(ctx)
                return res_fail("target_lost", detail={"target": target})
        else:
            ctx.session["gone_since"] = None
            # 跟随的原始步态兜底（2026-09-25）：bot.follow 内部走 pathfinder
            # GoalFollow——26.1 上同样死。目标在 6 格外且自身 3 秒没挪 →
            # look+forward 原始逼近一步（纯执行层，认知无感；10 分钟记忆内
            # 才启用，寻路正常时不插手）。
            if time.time() < _RAW_WALK_UNTIL + 600.0:
                me = s.get("position") or {}
                tinfo = next((q for q in s.get("playersNearby", [])
                              if q.get("name") == target), None)
                tdist = None
                if tinfo is not None:
                    try:
                        tdist = float(tinfo.get("dist"))
                    except (TypeError, ValueError):
                        tdist = None
                lastp = ctx.session.get("_f_last_pos") or me
                moved = math.hypot(
                    float(me.get("x", 0)) - float(lastp.get("x", 0)),
                    float(me.get("z", 0)) - float(lastp.get("z", 0)))
                ctx.session["_f_last_pos"] = dict(me)
                if (tdist is not None and tdist > 6.0 and moved < 0.4):
                    rel = (tinfo or {}).get("rel") or {}
                    # 弧度 + (-dz)：与 _raw_step/exploration 同一约定
                    # （bot.look 收弧度；degrees+正 dz 是双重错）。
                    ctx.bridge.look(math.atan2(
                        -float(rel.get("dx") or 0),
                        -float(rel.get("dz") or 0)), 0.0)
                    ctx.bridge.move("forward", 1.5)
                    ctx.bridge.move("jump", 0.5)
        # 2026-09-24 "还是老是跟着我"修正：bot.follow 皮套原本一挂满
        # follow_timeout_s（600s），追上也不松——玩家身边她寸步不离，
        # 其余行为全被挤成边角料。自主发起的跟随（release_when_close）
        # 语义是"回到同伴身边"：贴住 ≤follow_release_dist 连续
        # follow_release_grace_s 秒即提前了结（真到了才了结，如实成功）；
        # 之后人再走远，>follow_skip_dist 下一拍自会重新提出。承诺陪走
        # （不带该标志）不受影响——陪到超时是兑现，不是占大头。
        if ctx.session.get("release_when_close"):
            tdist = None
            for p in s.get("playersNearby", []) or []:
                if p.get("name") == target:
                    try:
                        tdist = float(p.get("dist"))
                    except (TypeError, ValueError):
                        tdist = None
                    break
            if tdist is not None and tdist <= float(
                    ctx.config.get("follow_release_dist", 3.5)):
                cs = ctx.session.get("close_since")
                if cs is None:
                    ctx.session["close_since"] = time.time()
                elif time.time() - cs >= float(
                        ctx.config.get("follow_release_grace_s", 20)):
                    self.cancel(ctx)
                    return res_ok(describe=f"追上 {target} 了，先在他附近自己玩",
                                  detail={"target": target, "released": True,
                                          "dist": tdist})
            else:
                ctx.session["close_since"] = None
        return res_pending(describe=f"跟随 {target} 中")

    def cancel(self, ctx):
        try:
            ctx.bridge.stopfollow()
        except Exception:
            pass


@register
class Stop(Skill):
    name = "stop"
    description = "停下：取消寻路、清控制状态、解除跟随"
    category = "movement"

    def start(self, ctx, params):
        ctx.bridge.stop()
        ctx.bridge.stop_goto()
        ctx.bridge.stopfollow()
        return res_ok(describe="停下了")

    def poll(self, ctx):
        return res_ok(describe="停下了")


@register
class LookAt(Skill):
    name = "look_at"
    description = "把视线转向坐标或可见实体"

    def start(self, ctx, params):
        s = ctx.state()
        pos = s.get("position") or {}
        x, y, z = params.get("x"), params.get("y"), params.get("z")
        if x is None and params.get("entity"):
            name = str(params["entity"])
            for e in s.get("nearbyEntities", []) or []:
                if name in (e.get("name"), e.get("displayName")):
                    rel = e.get("rel") or {}
                    x = float(pos.get("x", 0)) + float(rel.get("dx", 0))
                    y = float(pos.get("y", 0)) + float(rel.get("dy", 0)) + 1.0
                    z = float(pos.get("z", 0)) + float(rel.get("dz", 0))
                    break
        if x is None:
            return res_fail("no_target")
        r = ctx.bridge.look_at(float(x), float(y if y is not None else 0) or 64.0,
                               float(z))
        if not r.get("ok"):
            return res_fail(r.get("reason") or "look_failed")
        return res_ok(describe="看过去了",
                      detail={"x": round(float(x), 1), "y": round(float(y or 0), 1),
                              "z": round(float(z), 1)})


@register
class TurnTo(Skill):
    name = "turn_to"
    description = "转身到指定 yaw 角度"

    def start(self, ctx, params):
        # 参数合同与 building/blocks 同族：yaw 用“角度”；bridge.look 收弧度
        yaw = math.radians(float(params.get("yaw", 0)))
        r = ctx.bridge.look(yaw, math.radians(float(params.get("pitch", 0))))
        if not r.get("ok"):
            return res_fail("look_failed")
        return res_ok(describe=f"转向 yaw={yaw:.1f}")


@register
class Jump(Skill):
    name = "jump"
    description = "跳一下"

    def start(self, ctx, params):
        ok = ctx.bridge.move("jump", 0.5)
        return res_ok(describe="跳了一下") if ok else res_fail("move_failed")


@register
class Sneak(Skill):
    name = "sneak"
    description = "潜行开/关（params.on，默认开）"

    def start(self, ctx, params):
        on = bool(params.get("on", True))
        r = ctx.bridge.sneak(on)
        if not r.get("ok"):
            return res_fail("sneak_failed")
        return res_ok(describe="开始潜行" if on else "站直了")


@register
class Descend(_NavigateBase):
    name = "descend"
    description = "向下走 N 格（下潜/下楼梯）"
    arrival_radius = 2.0

    def start(self, ctx, params):
        pos = ctx.position()
        if not pos:
            return res_fail("no_position")
        down = float(params.get("depth", 3))
        target = {"x": float(pos.get("x", 0)), "y": float(pos.get("y", 64)) - down,
                  "z": float(pos.get("z", 0))}
        return self._start_goto(ctx, target, float(params.get("timeout", 45)))


@register
class Swim(Skill):
    name = "swim"
    description = "在水中朝目标方向游（朝向 + 前进 + 跳跃换气）"
    sustained = True
    category = "movement"

    def start(self, ctx, params):
        if not bool(ctx.state().get("inWater")):
            return res_fail("not_in_water")
        ctx.session["timeout_at"] = time.time() + float(params.get("timeout", 20))
        d = str(params.get("direction") or "forward")
        ctx.session["direction"] = d
        return res_pending(describe="游泳中")

    def poll(self, ctx):
        s = ctx.state(refresh=True)
        if not s.get("inWater"):
            ctx.bridge.stop()
            return res_ok(describe="上岸了")
        if time.time() > float(ctx.session.get("timeout_at", 0)):
            self.cancel(ctx)
            return res_fail("timeout")
        ctx.bridge.move("forward", 0.5)
        ctx.bridge.move("jump", 0.3)
        return res_pending(describe="游泳中")

    def cancel(self, ctx):
        try:
            ctx.bridge.stop()
        except Exception:
            pass


@register
class MaintainDistance(Skill):
    """与实体保持距离（社交跟随的近身版 / 对敌对生物的风筝）。"""
    name = "maintain_distance"
    description = "与目标实体保持 N 格距离（近了退，远了跟）"
    sustained = True
    category = "movement"

    def check(self, ctx, params):
        return bool(params.get("entity")), "missing_entity"

    def start(self, ctx, params):
        ctx.session["entity"] = str(params["entity"])
        ctx.session["distance"] = float(params.get("distance", 4))
        ctx.session["timeout_at"] = time.time() + float(params.get("timeout", 120))
        return res_pending(describe=f"与 {params['entity']} 保持 {params.get('distance', 4)} 格")

    def poll(self, ctx):
        s = ctx.state(refresh=True)
        if time.time() > float(ctx.session.get("timeout_at", 0)):
            self.cancel(ctx)
            return res_ok(describe="保持距离时间到，结束")
        pos = s.get("position") or {}
        ent = None
        for e in s.get("nearbyEntities", []) or []:
            if e.get("name") == ctx.session.get("entity"):
                ent = e
                break
        for p in s.get("playersNearby", []) or []:
            if p.get("name") == ctx.session.get("entity"):
                ent = p
                break
        if not ent or not pos:
            return res_fail("target_lost")
        dist = float(ent.get("dist") or 0)
        want = float(ctx.session.get("distance", 4))
        rel = ent.get("rel") or {}
        if abs(dist - want) <= 0.8:
            ctx.bridge.stop()
            return res_pending(describe="距离合适，跟着")
        if dist < want - 0.8:
            # 后退
            d = max(1.0, dist)
            tx = float(pos.get("x", 0)) - float(rel.get("dx", 0)) / d * 2.0
            tz = float(pos.get("z", 0)) - float(rel.get("dz", 0)) / d * 2.0
            ctx.bridge.stop_goto()
            ctx.bridge.goto_coords(tx, float(pos.get("y", 64)), tz)
        else:
            ctx.bridge.goto_entity(ent.get("name") or "")
        return res_pending(describe=f"调整距离（当前 {dist:.1f}）")

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
        except Exception:
            pass


@register
class RecoverStuck(Skill):
    name = "recover_stuck"
    description = "脱困：跳 + 侧移一步 + 重新寻路到上一个目标"

    def start(self, ctx, params):
        ctx.bridge.move("jump", 0.6)
        ctx.bridge.move("left", 0.6)
        ctx.session["timeout_at"] = time.time() + 6
        return res_pending(describe="尝试脱困")

    def poll(self, ctx):
        if time.time() > float(ctx.session.get("timeout_at", 0)):
            return res_ok(describe="脱困动作完成")
        return res_pending(describe="脱困中")


@register
class ReturnToLocation(_NavigateBase):
    """回到记住的位置（家/上一个安全点）。target=地名关键词。"""
    name = "return_to_location"
    description = "回到记住的位置（home/家/base…按名字或关键词找）"
    arrival_radius = 2.5

    def check(self, ctx, params):
        name, entry = ctx.locations.find(
            params.get("name") or "home", "home", "家", "base", "出生")
        if entry is None:
            return False, "location_unknown"
        return ctx.connected(), "not_connected"

    def start(self, ctx, params):
        name, entry = ctx.locations.find(
            params.get("name") or "home", "home", "家", "base", "出生")
        if not entry:
            return res_fail("location_unknown")
        return self._start_goto(
            ctx, {"x": entry["x"], "y": entry.get("y", 64), "z": entry["z"]},
            float(params.get("timeout", 120)))

    def poll(self, ctx):
        out = super().poll(ctx)
        if out.get("status") == "done":
            ctx.locations.remember("last_visit", ctx.position(), kind="visit")
        return out


@register
class Amble(WalkTo):
    """踱步张望（2026-09-25，用户："真人会因为没到阈值就站游戏里不动吗"）：
    阈值下的空闲给一个身体——短距离无承诺地走走。认识论上是诚实的
    （换位置=换一批感知样本=可能撞见新东西），不是装活的随机抖动；
    距离/时长有界，结算如实，经 ActionManager 提交（kernel 照常裁决）。"""
    name = "amble"
    description = "附近随便走走、看看四周（空闲时的低强度活动）"

    def check(self, ctx, params):
        return ctx.connected(), "not_connected"

    def start(self, ctx, params):
        import random as _rd
        pos = ctx.position()
        if not pos:
            return res_fail("no_position")
        # 纯原始步态：踱步是随机短逛，寻路（尤其 26.1 下的死 A*）纯属浪费
        heading = _rd.uniform(0.0, 6.2832)
        ctx.session.update({
            "heading": heading, "amble_deadline": time.time() + float(
                ctx.config.get("amble_timeout_s", 25) or 25),
            "last_pos": dict(pos),
        })
        ctx.bridge.look(heading, 0.0)   # heading 本就是弧度（0~2π）
        return res_pending(describe="出去走走，看看四周")

    def poll(self, ctx):
        import random as _rd
        pos = ctx.position() or {}
        if time.time() > float(ctx.session.get("amble_deadline", 0)):
            return res_ok(describe="四处走了走，看了看四周",
                          detail={"amble": True, "position": pos})
        # 被挡住（2 拍没挪）→ 随机换个方向再走
        last = ctx.session.get("last_pos") or pos
        if math.hypot(float(pos.get("x", 0)) - float(last.get("x", 0)),
                      float(pos.get("z", 0)) - float(last.get("z", 0))) < 0.4:
            ctx.session["heading"] = _rd.uniform(0.0, 6.2832)
            ctx.bridge.move("jump", 0.5)
        ctx.session["last_pos"] = dict(pos)
        ctx.bridge.look(float(ctx.session.get("heading", 0)), 0.0)
        ctx.bridge.move("forward", 2.0)
        return res_pending(describe="走着")
