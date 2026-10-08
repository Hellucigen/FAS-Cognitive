# skills/gathering.py — D 基础资源采集技能
# ============================================================================
# gather_resource 是核心状态机：
#   选工具（缺工具 → tool_missing 如实失败，认知层学习）→ 找方块（找不到 →
#   not_found_in_radius，认知层可决定先探索）→ 走过去 → 挖 → 收掉落物 →
#   重复直到数量满足/附近挖空。
# 不硬编码任何资源坐标；资源位置每次用 findBlocks 现查。
# ============================================================================

import math
import time

from skills.base import (Skill, register, res_ok, res_fail, res_pending,
                         receipt_reason)
from skills.observation import (_find_blocks, normalize_resource,
                                resolve_variants, PICKAXE_REQUIREMENT)
from skills.inventory import choose_best_tool, equip_best


def _dist2d(a: dict, b: dict) -> float:
    try:
        return math.hypot(float(a.get("x", 0)) - float(b.get("x", 0)),
                          float(a.get("z", 0)) - float(b.get("z", 0)))
    except (TypeError, ValueError):
        return 1e9


def _inv_counts(ctx, refresh: bool = False) -> dict:
    """{物品名: 总数}。查询失败按空表处理——_finish 里空表对空表=零增益，
    会把"查不到背包"如实当成"没拿到东西"，宁可漏报成功也不假报成功。"""
    agg = {}
    try:
        for it in ctx.inventory(refresh=refresh) or []:
            n = str((it or {}).get("name") or "")
            if n:
                agg[n] = agg.get(n, 0) + int((it or {}).get("count", 1) or 1)
    except Exception:
        pass
    return agg


# 可采集材料白名单（安全不变量，继承旧 minecraft.embodiment.SAFE_DIG_BLOCKS）：
# 箱子/工作台/熔炉/建筑方块绝不在列——采集技能拒绝挖掘清单外的任何东西，
# 绝不自动破坏用户资产。
SAFE_GATHER_BLOCKS = {
    # 原木与木制品
    "oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log",
    "dark_oak_log", "mangrove_log", "cherry_log", "log", "wood",
    # 土石
    "stone", "cobblestone", "dirt", "grass_block", "sand", "gravel",
    "netherrack", "deepslate", "cobbled_deepslate",
    # 矿石
    "coal_ore", "iron_ore", "copper_ore", "gold_ore", "diamond_ore",
    "redstone_ore", "lapis_ore", "emerald_ore",
    "deepslate_coal_ore", "deepslate_iron_ore", "deepslate_copper_ore",
    "deepslate_gold_ore", "deepslate_diamond_ore", "deepslate_redstone_ore",
    "deepslate_lapis_ore", "deepslate_emerald_ore",
    # 植物/农作物
    "wheat", "carrots", "potatoes", "beetroot", "sugar_cane",
    "sweet_berry_bush", "pumpkin", "melon", "cactus",
    "oak_leaves", "spruce_leaves", "birch_leaves", "jungle_leaves",
}


def _safe_resource(name: str) -> bool:
    n = str(name or "")
    return n in SAFE_GATHER_BLOCKS


@register
class GatherResource(Skill):
    name = "gather_resource"
    description = "采集资源：自动找方块、选工具、挖够数量、收掉落物"
    category = "gathering"

    def check(self, ctx, params):
        if not ctx.connected():
            return False, "not_connected"
        if not (params.get("resource") or params.get("target") or params.get("block")):
            return False, "missing_resource"
        return True, ""

    def start(self, ctx, params):
        resource, variants = resolve_variants(
            params.get("resource") or params.get("target")
            or params.get("block"))
        # 认知层给的并列等价来源（world_prior._leaf_siblings，图上的配方族）：
        # 白名单过滤后并入候选，find 阶段逐成员现查取最近者 —— 哪种真在场由
        # 世界回答。组名展开保持原样（那是执行层自己的命名学表）。
        _extra = params.get("variants") or []
        if _extra:
            merged = [normalize_resource(str(v)) for v in _extra
                      if str(v).strip() and str(v) in SAFE_GATHER_BLOCKS]
            seen_v = [str(v) for v in variants]
            variants = tuple(list(variants) + [v for v in merged
                                               if v not in seen_v])
        # 资产保护已上移 Safety Kernel（图上"受保护"关系 + 授权证据），
        # 技能层只保留"非可采集方块"的物理兜底：无 kernel 授权标记的
        # 清单外目标一律拒绝（防绕过入口的裸调用）；带标记则放行
        # （用户明确授权的拆除/修改走这里执行）。组名（tree/wood）的
        # 成员全在白名单内，等同安全——按"任一成员可采集"判。
        safe = any(v in SAFE_GATHER_BLOCKS for v in variants)
        if not safe and not params.get("_kernel_granted"):
            return res_fail(f"requires_user_permission:{resource}",
                            describe=f"{resource} 不是可采集资源，"
                                     f"未经用户授权不会去动")
        qty = max(1, int(params.get("quantity", params.get("count", 1))))
        radius = int(params.get("search_radius", params.get("radius", 16)))
        ctx.session.update({
            "resource": resource, "variants": list(variants),
            "qty": qty, "radius": radius,
            "collected": 0, "phase": "tool",
            "timeout_at": time.time() + float(params.get("timeout", 180)),
        })
        # §四D 背包基线：采集成不成，最后要看背包真的多出东西，不能只信
        # "方块消失"这个回执（2026-09-25 实机假阳性：徒手挖 deepslate 挖掉
        # 两块、服务器一件不掉，旧 _finish 却按挖掉计数报成功）。
        ctx.session["_inv_base"] = _inv_counts(ctx, refresh=True)
        return self._advance(ctx)

    # ── 状态机 ────────────────────────────────────────────

    def _advance(self, ctx) -> dict:
        s = ctx.session
        if time.time() > float(s.get("timeout_at", 0)):
            self.cancel(ctx)
            return res_fail("timeout", detail=s.get("detail") or {})
        phase = s.get("phase")
        if phase == "tool":
            return self._phase_tool(ctx)
        if phase == "find":
            return self._phase_find(ctx)
        if phase == "approach":
            return self._phase_approach(ctx)
        if phase == "dig":
            return self._phase_dig(ctx)
        if phase == "dig_wait":
            return self._phase_dig_wait(ctx)
        if phase == "collect":
            return self._phase_collect(ctx)
        return res_fail("bad_phase")

    def _phase_tool(self, ctx):
        s = ctx.session
        resource = s["resource"]
        # 原木/泥土/沙子徒手可挖；矿石按需求表检查镐
        tool, why = choose_best_tool(ctx, resource)
        if tool:
            equip_best(ctx, tool)
        elif why:
            self.cancel(ctx)
            return res_fail(why, describe=f"没有合适的工具，挖不了 {resource}",
                            detail={"resource": resource})
        s["tool_for"] = resource
        s["phase"] = "find"
        return self._phase_find(ctx)

    def _phase_find(self, ctx):
        s = ctx.session
        pos = ctx.position() or {}
        # 组名（tree/wood…）与认知层等价物种逐成员现查，取全局最近者并把资源
        # **具体化**为真名（工具选择/掉落核对/话术都跟真名走）；单名目标行为不变。
        # 诚实（2026-09-25）：查询接口自己失败（unknown_block/chunks_unloaded）
        # 不是"此地没有"——全部成员都查失败时如实上报 find_query_failed，
        # 别把"我没查成"喂给认知层当缺席证据（absent 降级/因果账都会被喂假）。
        best, best_name, best_d = None, "", 1e18
        probe_reasons, probe_ok = [], False
        for v in (s.get("variants") or [s["resource"]]):
            r = ctx.bridge.call("/find_blocks", {"block": v,
                                                 "radius": s["radius"],
                                                 "count": 8}) or {}
            if not r.get("ok"):
                probe_reasons.append(f"{v}:{r.get('reason') or 'query_failed'}")
                continue
            probe_ok = True
            for p in (r.get("positions") or []):
                d = _dist2d(pos, p)
                if d < best_d:
                    best, best_name, best_d = p, v, d
        if best is not None:
            # 记入位置记忆（seen:<真名>）：这一片挖空后，下一次 gather 能
            # "回上次见过的地方"，而不是朝随机方向漂（2026-09-26 场景实测：
            # 橡木在出生点，探索漂出 260 格外还找不到）。
            try:
                ctx.locations.remember(f"seen:{best_name}", best, kind="seen")
            except Exception:
                pass
        if best is None:
            # 附近挖空/找不到：若已挖到一部分算 partial 成功，否则如实失败
            if s.get("collected", 0) > 0:
                return self._finish(ctx, partial=True)
            # 位置记忆里有它 → 回上次见过的地方再找（2026-09-26 场景实测：
            # 探索漂出 260 格后原地判"没有"，而橡木好好地在出生点）。只在
            # 记忆点离得够远时值得走；到了那儿还找不到才如实判缺。
            spot = None
            try:
                for v in (s.get("variants") or [s["resource"]]):
                    e = ctx.locations.get(f"seen:{v}") or {}
                    if e.get("x") is not None:
                        spot = {"x": e["x"], "y": e.get("y", 64),
                                "z": e["z"]}
                        break
            except Exception:
                spot = None
            if spot is not None and s.get("seek_done") != (
                    round(spot["x"]), round(spot["z"])) and \
                    _dist2d(pos, spot) > 20:
                s["seek_done"] = (round(spot["x"]), round(spot["z"]))
                s["target_pos"] = spot
                s["seeking"] = True
                ctx.bridge.stop_goto()
                r = ctx.bridge.goto_coords(spot["x"], spot["y"], spot["z"])
                if r.get("ok"):
                    s["phase"] = "approach"
                    return res_pending(describe=f"回上次见到 "
                                       f"{s['resource']} 的地方")
            if spot is not None and s.get("seeking") is None and \
                    _dist2d(pos, spot) <= 20:
                # 记忆点近在咫尺却探不到：方块已被挖掉，记忆过期 → 清除，
                # 否则下次还往这儿跑。
                try:
                    for v in (s.get("variants") or [s["resource"]]):
                        ctx.locations.forget(f"seen:{v}")
                except Exception:
                    pass
            self.cancel(ctx)
            if not probe_ok and probe_reasons:
                return res_fail("find_query_failed",
                                describe=f"探测接口失败（{probe_reasons[0]}），"
                                         f"先不判\"此地没有\"",
                                detail={"resource": s["resource"],
                                        "radius": s["radius"],
                                        "probes": probe_reasons[:8]})
            return res_fail("not_found_in_radius",
                            describe=f"{s['resource']} 在 {s['radius']} 格内没找到",
                            detail={"resource": s["resource"], "radius": s["radius"]})
        if best_name:
            s["resource"] = best_name
            if s.get("tool_for") != best_name:
                # 真名是刚探出来的：工具档位必须跟着真名复核。组名（"diamond"）
                # 在需求表里没有条目 → 开局判"徒手可挖"，具体化成 diamond_ore
                # 后若不复核，石镐就能"成功"挖钻矿（2026-09-26 离线验收 E4
                # 诱饵暴露；上面注释说的"工具选择跟真名走"此前并没有兑现）。
                s["tool_for"] = best_name
                tool, why = choose_best_tool(ctx, best_name)
                if tool:
                    equip_best(ctx, tool)
                elif why:
                    self.cancel(ctx)
                    return res_fail(why, describe=f"没有合适的工具，挖不了 {best_name}",
                                    detail={"resource": best_name})
        s["target_pos"] = best
        if best_d <= 4.5:
            s["phase"] = "dig"
            return self._phase_dig(ctx)   # 已在旁边：立即开挖（不等下一轮）
        s["phase"] = "approach"
        ctx.bridge.stop_goto()
        r = ctx.bridge.goto_coords(best.get("x"), best.get("y", pos.get("y", 64)),
                                   best.get("z"))
        if not r.get("ok"):
            self.cancel(ctx)
            return res_fail(r.get("reason") or "no_path",
                            detail={"resource": s["resource"]})
        return res_pending(describe=f"采集 {s['resource']}（{s.get('collected', 0)}/{s['qty']}）")

    def _phase_approach(self, ctx):
        s = ctx.session
        raw = ctx.bridge.get_action_result() or {}
        st = str(raw.get("status", "")).lower()
        pos = ctx.position() or {}
        target = s.get("target_pos") or {}
        if _dist2d(pos, target) <= 4.5:
            ctx.bridge.stop_goto()
            if s.pop("seeking", None):
                # 到了记忆点：重新探测（记忆坐标不盲挖——方块可能已被挖掉）
                s["phase"] = "find"
                return self._phase_find(ctx)
            s["phase"] = "dig"
            return res_pending(describe="到目标方块旁了")
        if st in ("failed", "done", "partial"):
            # 先分诊失败原因（2026-09-26 离线验收 T6c）：
            _why = receipt_reason(raw, "")
            if st == "failed" and _why in ("block_not_found", "unknown_block"):
                # 目标方块不在了（被挖/区块没加载）：继续朝坐标补腿是
                # 空转到承诺期超时——回 find 重新定位（宁重新找，不傻走）。
                s["phase"] = "find"
                return self._phase_find(ctx)
            if st == "failed" and _why in ("noPath", "path_failed"):
                # 寻路明确无路（bot 侧已先试过原始步态腿才记的 noPath）：
                # 同一目的地重发 goto 只会原地打转，如实失败交上层改道。
                self.cancel(ctx)
                return res_fail(_why, detail={"resource": s["resource"]})
            # failed/done/partial 都可能"没到 4.5 格内"（GoalNear 容差 1.5、
            # 外部 goto 打断、原始步态 60s 一腿）——只要没到就续腿单调逼近。
            # 2026-09-26 真机：done-但差 8 格 → approach 永远 pending，
            # 整个动作队列挂到 600s 超时，bot 假死。
            if _dist2d(pos, target) > 4.5 and \
                    time.time() < float(s.get("timeout_at", 0)) and \
                    int(s.get("reissued", 0)) < 12:
                s["reissued"] = int(s.get("reissued", 0)) + 1
                ctx.bridge.stop_goto()
                r = ctx.bridge.goto_coords(target.get("x"),
                                           target.get("y", 64),
                                           target.get("z"))
                if r.get("ok"):
                    return res_pending(describe=f"继续走向 "
                                       f"{s['resource']}（续腿 "
                                       f"{s['reissued']}）")
            self.cancel(ctx)
            return res_fail(receipt_reason(raw, "no_path"),
                            detail={"resource": s["resource"]})
        if time.time() > float(s.get("timeout_at", 0)):
            self.cancel(ctx)
            return res_fail("timeout")
        return res_pending(describe=f"走向 {s['resource']}")

    def _phase_dig(self, ctx):
        s = ctx.session
        p = s.get("target_pos") or {}
        r = ctx.bridge.call("/dig_pos", {"x": p.get("x", 0), "y": p.get("y", 0),
                                         "z": p.get("z", 0)})
        if not r.get("ok"):
            # 方块可能已经被挖掉（世界变化）：回 find 重新定位
            reason = str(r.get("reason") or "")
            if reason in ("block_not_found", "unknown_block"):
                s["phase"] = "find"
                return res_pending(describe="目标变了，重新找")
            self.cancel(ctx)
            return res_fail(reason or "dig_failed", detail={"resource": s["resource"]})
        s["phase"] = "dig_wait"
        s["dig_deadline"] = time.time() + 20
        return res_pending(describe=f"正在挖 {s['resource']}")

    def _phase_dig_wait(self, ctx):
        s = ctx.session
        raw = ctx.bridge.get_action_result() or {}
        st = str(raw.get("status", "")).lower()
        if st in ("done", "partial"):
            s["collected"] = int(s.get("collected", 0)) + 1
            # 真实挖掉的坐标（世界事件层 B2：动作回执 → 含坐标的环境事实）
            tp = s.get("target_pos") or {}
            if tp:
                s.setdefault("dug", []).append(dict(tp))
            # 每次挖完都等捡拾落地（/collect_item 走 GoalFollow 需要几秒；
            # 旧的"凑够数量才 collect"让中途捡拾被下一发 goto 冲掉——
            # 2026-09-26 真机：方块挖了、掉落物原地消失，dug_no_item）。
            s["phase"] = "collect"
            ctx.bridge.call("/collect_item", {"radius": 6})
            return self._advance(ctx)
        if st == "failed":
            reason = receipt_reason(raw, "dig_failed")
            if reason in ("block_not_found", "unknown_block"):
                s["phase"] = "find"
                return res_pending(describe="重新找目标")
            self.cancel(ctx)
            return res_fail(reason, detail={"resource": s["resource"]})
        if time.time() > float(s.get("dig_deadline", 0)):
            s["phase"] = "find"
            return res_pending(describe="挖掘超时，重新找目标")
        return res_pending(describe=f"挖掘中（{s.get('collected', 0)}/{s['qty']}）")

    def _phase_collect(self, ctx):
        s = ctx.session
        raw = ctx.bridge.get_action_result() or {}
        st = str(raw.get("status", "")).lower()
        if st in ("running", "pending"):
            if time.time() > float(s.get("timeout_at", 0)):
                return self._finish(ctx, partial=True)
            return res_pending(describe="收掉落物中")
        # 捡拾结算（done=进了背包 / failed=没捡到）：数量没凑够就回 find
        # 找下一块，凑够了才结算整次采集。
        if int(s.get("collected", 0)) < int(s.get("qty", 1)):
            s["phase"] = "find"
            return self._phase_find(ctx)
        return self._finish(ctx, partial=False)

    def _finish(self, ctx, partial: bool) -> dict:
        s = ctx.session
        dug = int(s.get("collected", 0))
        self.cancel(ctx)
        # §四D 成功判据 = 背包真的多出东西，不是"方块消失"回执。
        # 差值按**全物品**统计：矿石掉的是 raw_iron 这类异名物品，按资源名
        # 数会漏——"背包净增"才是"获得环境物品"这个动作的公共事实核。
        base = s.get("_inv_base") or {}
        now = _inv_counts(ctx, refresh=True)
        gained = {k: v - int(base.get(k, 0)) for k, v in now.items()
                  if v - int(base.get(k, 0)) > 0}
        inv_total = sum(gained.values())
        if inv_total <= 0:
            # 挖开了却没拿到：游戏物理的诚实回答（徒手挖 deepslate 不掉东西
            # /掉落物没捡回）。对认知层这是真实负例，不是执行故障。
            return res_fail(
                "dug_no_item" if dug > 0 else "nothing_collected",
                detail={"resource": s["resource"], "blocks_broken": dug,
                        "dug_positions": (s.get("dug") or [])[:16]},
                describe=(f"挖开了 {dug} 块 {s['resource']}，"
                          f"但背包没多出东西" if dug
                          else f"没采集到 {s['resource']}"))
        got = min(inv_total, int(s.get("qty", 1))) if partial else inv_total
        status = "partial" if partial else "done"
        return res_ok(status=status,
                      describe=f"采集了 {s['resource']} x{got}" +
                               ("（附近挖空了，先到这里）" if partial else ""),
                      detail={"resource": s["resource"], "collected": got,
                              "inv_gained": gained,
                              "blocks_broken": dug,
                              "requested": s["qty"],
                              "dug_positions": (s.get("dug") or [])[:16]})

    def poll(self, ctx):
        return self._advance(ctx)

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
        except Exception:
            pass


# ── 常用采集别名（Action 层直接可用的具体资源技能）─────────

@register
class ChopTree(GatherResource):
    name = "chop_tree"
    description = "砍树收集原木（自动找最近的树干）"

    def start(self, ctx, params):
        params = dict(params)
        params.setdefault("resource", "oak_log")
        params.setdefault("quantity", params.get("quantity", 3))
        return super().start(ctx, params)


@register
class GatherWood(ChopTree):
    name = "gather_wood"
    description = "收集木头"


@register
class GatherStone(GatherResource):
    name = "gather_stone"
    description = "挖石头（自动检查镐）"

    def start(self, ctx, params):
        params = dict(params)
        params.setdefault("resource", "stone")
        return super().start(ctx, params)


@register
class CollectFood(Skill):
    """找食物（自包含状态机，不嵌套子技能会话）：
    背包有食物 → 直接报；附近有作物 → 采集；附近有动物 → 猎捕；否则如实失败。"""
    name = "collect_food"
    description = "找食物（背包 → 采作物 → 猎动物）"
    category = "gathering"

    def start(self, ctx, params):
        ctx.session.update({"phase": "scan", "collected": 0,
                            "timeout_at": time.time() + float(params.get("timeout", 180))})
        return self._advance(ctx)

    def _advance(self, ctx):
        s = ctx.session
        if time.time() > float(s.get("timeout_at", 0)):
            self.cancel(ctx)
            return res_fail("timeout")
        phase = s.get("phase")
        if phase == "scan":
            from skills.inventory import choose_food
            food = choose_food(ctx.inventory(refresh=True))
            if food:
                return res_ok(describe=f"背包里就有 {food}，不用找",
                              detail={"source": "inventory", "food": food})
            s["phase"] = "scan_world"
            return res_pending(describe="在附近找吃的")
        if phase == "scan_world":
            st = ctx.state(refresh=True)
            crops = []
            for plant in ("wheat", "carrots", "potatoes", "beetroot",
                          "sweet_berry_bush", "sugar_cane"):
                crops = _find_blocks(ctx, plant, 16, 4)
                if crops:
                    s["plant"] = plant
                    break
            animals = [e for e in (st.get("nearbyEntities") or [])
                       if str(e.get("name") or "") in
                       ("cow", "pig", "sheep", "chicken", "rabbit")]
            if crops:
                s["target_pos"] = crops[0]
                s["phase"] = "approach_crop"
                ctx.bridge.stop_goto()
                r = ctx.bridge.goto_coords(crops[0]["x"], crops[0].get("y", 64),
                                           crops[0]["z"])
                if not r.get("ok"):
                    self.cancel(ctx)
                    return res_fail("no_path")
                return res_pending(describe=f"走向{s.get('plant')}")
            if animals:
                s["animal"] = animals[0].get("name")
                s["phase"] = "hunt"
                s["attack_cd"] = 0.0
                return res_pending(describe=f"去猎 {s['animal']}")
            self.cancel(ctx)
            return res_fail("no_food_source_nearby",
                            describe="附近没有动物也没有作物")
        if phase == "approach_crop":
            raw = ctx.bridge.get_action_result() or {}
            pos = ctx.position() or {}
            target = s.get("target_pos") or {}
            if _dist2d(pos, target) <= 4.0:
                s["phase"] = "dig_crop"
                return res_pending(describe="到作物旁了")
            st = str(raw.get("status", "")).lower()
            if st == "failed":
                self.cancel(ctx)
                return res_fail("no_path")
            return res_pending(describe="走向作物")
        if phase == "dig_crop":
            p = s.get("target_pos") or {}
            r = ctx.bridge.call("/dig_pos", {"x": p.get("x", 0), "y": p.get("y", 0),
                                             "z": p.get("z", 0)})
            if not r.get("ok"):
                s["phase"] = "scan_world"
                return res_pending(describe="作物没了，再找")
            s["dig_deadline"] = time.time() + 15
            s["phase"] = "dig_wait"
            return res_pending(describe="采集作物中")
        if phase == "dig_wait":
            raw = ctx.bridge.get_action_result() or {}
            st = str(raw.get("status", "")).lower()
            if st in ("done", "partial"):
                s["collected"] = int(s.get("collected", 0)) + 1
                ctx.bridge.call("/collect_item", {"radius": 4})
                if s["collected"] >= 2:
                    return res_ok(describe="采集到食物了",
                                  detail={"source": "crop", "count": s["collected"]})
                s["phase"] = "scan_world"
                return res_pending(describe="再采一点")
            if st == "failed" or time.time() > float(s.get("dig_deadline", 0)):
                s["phase"] = "scan_world"
            return res_pending(describe="采集作物中")
        if phase == "hunt":
            st = ctx.state(refresh=True)
            ents = [e for e in (st.get("nearbyEntities") or [])
                    if e.get("name") == s.get("animal")]
            if not ents:
                if s.get("collected", 0) > 0 or s.get("attacked"):
                    ctx.bridge.call("/collect_item", {"radius": 6})
                    return res_ok(describe=f"{s.get('animal')} 倒下了，收好掉落物",
                                  detail={"source": "hunt", "animal": s.get("animal")})
                self.cancel(ctx)
                return res_fail("target_lost")
            e = ents[0]
            if float(e.get("dist") or 99) > 3.2:
                pos = ctx.position() or {}
                rel = e.get("rel") or {}
                ctx.bridge.stop_goto()
                ctx.bridge.goto_coords(
                    float(pos.get("x", 0)) + float(rel.get("dx", 0)),
                    float(pos.get("y", 64)),
                    float(pos.get("z", 0)) + float(rel.get("dz", 0)))
                return res_pending(describe="接近猎物")
            now = time.time()
            if now >= float(s.get("attack_cd", 0)):
                ctx.bridge.attack(e.get("name") or "")
                s["attack_cd"] = now + 0.8
                s["attacked"] = True
            return res_pending(describe="狩猎中")

    def poll(self, ctx):
        return self._advance(ctx)

    def cancel(self, ctx):
        try:
            ctx.bridge.stop_goto()
        except Exception:
            pass


@register
class CollectPlant(Skill):
    name = "collect_plant"
    description = "采集植物（小麦/胡萝卜/马铃薯/甘蔗…）"

    def start(self, ctx, params):
        plant = normalize_resource(params.get("plant") or params.get("target") or "wheat")
        sub = GatherResource()
        out = sub.start(ctx, {"resource": plant,
                              "quantity": int(params.get("quantity", 2))})
        if out.get("status") == "pending":
            ctx.session["skill"] = "gather_resource"
        return out

    def poll(self, ctx):
        from skills.base import get as get_skill
        sub = get_skill("gather_resource")
        out = sub.poll(ctx)
        if out.get("status") in ("done", "failed"):
            ctx.session["skill"] = "collect_plant"
        return out
