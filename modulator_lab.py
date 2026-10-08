# modulator_lab.py — R2 实验台（P12，§26：实验/调试模式驱动的必须是**真实管线**）
# ============================================================================
# 这里只有一个原则：**本模块不实现任何调制逻辑**，它只是把生产里已经存在的
# 入口按生产的顺序串起来，并且把每一步的读数如实报出来。
#
#   · 注入事件   → modulation_events.ModulatorEngine.emit()（与 reward/CC 同一个
#                  入口；扇出仍由 `事件类型:* -[影响 w]-> 调制器` 的边决定）
#   · 结果结算   → RewardSystem.evaluate() + release()（与 action_system 同一条路）
#   · 手动拧旋钮 → InternalState.set_value(source="manual")（与前端滑杆同一条路）
#   · 时间快进   → 与 continuous_cognition 的"状态拍"**同一族函数**逐块重放
#                  （tick_decay → 昼夜漂移 → 需求漂移 → update_needs_from_signals），
#                  而不是另写一份衰减公式——§26 的原文就是"测试接口应调用真实
#                  Modulator event pipeline"。
#
# 唯一的"新状态"是一个**时基偏移**（虚拟时钟）。它为什么合法：统一时基是 P10
# 就建好的正式机制（`InternalState.set_clock`），persona 通过 P11 的 `now()` 与
# 它同源，所以快进 6 小时之后，"事件余韵的衰减、调制器回基线、不应期退场"
# 走的都是同一条 Δt 路径——这恰恰是实验台存在的意义：让小时级的机制能在
# 秒级里被**真实地**观察，而不是被模拟。close() 原样交还时钟。
#
# 本模块不持有调制器数值、不写图谱拓扑、不建第二份状态表（禁令 11）。
# ============================================================================

import logging
import threading
import time

logger = logging.getLogger(__name__)

# 快进时一块多少分钟：与 `modulator_subgraph._apply_state_drift` 的 dt 钳位
# （min(5, dt_min)）取同一个数 ⇒ 每一块都是"生产的一拍"，不会一拍补跳一小时。
DEFAULT_CHUNK_MIN = 5.0


class ModulatorLab:
    """实验/调试驱动器。构造它**什么都不改**；每个动作都走生产入口。"""

    def __init__(self, kg, st, config, engine=None, reward=None,
                 persona=None, field=None, wall_clock=time.time):
        self.kg = kg
        self.st = st
        self.config = config or {}
        self._engine = engine          # ModulatorEngine（不传则从 reward 借同一个）
        self._reward = reward
        self.persona = persona
        self.field = field             # CognitiveField（观测可选）
        self._wall = wall_clock
        self._lock = threading.RLock()
        # 虚拟时基会话（P10 的统一时基，这里只是它的一个用户）
        self._prev_clock = None
        self._offset_s = 0.0
        self._open = False
        self._trace = []               # 本会话动作摘要（有界；观测用，非状态源）

    # ── 时钟（虚拟时间会话）──────────────────────────────────

    def open(self) -> dict:
        """接管时基（幂等）。原时钟存下来，close() 时**原样交还**——
        不假设原来是墙钟，否则实验台会把别的回放会话踩掉。"""
        with self._lock:
            if self._open:
                return {"ok": True, "already_open": True,
                        "offset_min": round(self._offset_s / 60.0, 2)}
            self._prev_clock = self.st.get_clock()
            self.st.set_clock(self._virtual_now)
            self._open = True
            self._log("open", {"prev": "injected" if self._prev_clock else "wall"})
            return {"ok": True, "already_open": False, "now": round(self.st.now(), 3)}

    def close(self) -> dict:
        with self._lock:
            if not self._open:
                return {"ok": True, "already_closed": True}
            self.st.set_clock(self._prev_clock)
            self._prev_clock = None
            self._open = False
            self._log("close", {"total_offset_min": round(self._offset_s / 60.0, 2)})
            return {"ok": True, "advanced_min_total": round(self._offset_s / 60.0, 2)}

    def _virtual_now(self) -> float:
        return float(self._wall()) + self._offset_s

    @property
    def is_open(self) -> bool:
        return self._open

    def advance(self, minutes: float, chunk_min: float = DEFAULT_CHUNK_MIN) -> dict:
        """把虚拟时钟拨快 `minutes`，并且**逐块重放生产的状态拍**
        （continuous_cognition.py 里那段：tick_decay → 昼夜漂移 → 需求漂移 →
        需求靶值收敛）。没有第二套衰减实现可以"快进"——快进的就是那些函数。

        要求会话已 open：Δt 的所有来源（写入时间戳 / 不应期 / 衰减）必须同
        一个时基，否则会出现"合成 now 落在墙钟之前 ⇒ dt≤0 ⇒ 衰减静默失效"
        （internal_state.set_clock 的 docstring 记着这个坑）。"""
        if not self._open:
            return {"ok": False, "error": "先 open()：快进必须走在统一时基上"}
        import modulator_subgraph as MS
        minutes = max(0.0, float(minutes))
        chunk = max(0.5, min(float(chunk_min), 5.0))   # 与漂移钳位同界，不放大单拍
        before = self.numeric_view()
        blocks = 0
        written_need = written_circ = decayed = 0
        left = minutes
        while left > 1e-9:
            dt = min(chunk, left)
            left -= dt
            blocks += 1
            # 先把时基拨这一块，再走这一块的拍——`tick_decay` 的 Δt 来自
            # `now - last_update`（都读同一注入时基），所以"时钟动了"这件事
            # 只在这里发生一次；漂移函数吃显式 dt_min，与生产的 CC 拍同形。
            self._offset_s += dt * 60.0
            decayed += self.st.tick_decay() or 0
            try:
                rc = MS.apply_circadian_drift(self.kg, self.st, self.config,
                                              dt_min=dt)
                rn = MS.apply_need_drift(self.kg, self.st, self.config, dt_min=dt)
                bias = (MS.graph_biases(self.kg, self.st, self.config)
                        or {}).get("need") or {}
                self.st.update_needs_from_signals(bias=bias)
                written_circ += int((rc or {}).get("written") or 0)
                written_need += int((rn or {}).get("written") or 0)
            except Exception as e:                      # noqa: BLE001
                self._log("advance-error", {"why": str(e)})
                return {"ok": False, "error": str(e), "blocks": blocks}
        after = self.numeric_view()
        out = {"ok": True, "minutes": round(minutes, 2), "blocks": blocks,
               "chunk_min": chunk, "decayed": decayed,
               "need_drift_writes": written_need,
               "circadian_drift_writes": written_circ,
               "diff": self._diff(before, after)}
        self._log("advance", {"minutes": out["minutes"], "blocks": blocks})
        return out

    def settle_field(self, steps: int = 60) -> dict:
        """让认知场（张力/驱动/网络/调制）连续推进若干拍。

        偏置是**稳态**通道（P9）：一步看不出位移，要让它朝着"基线+偏置"收敛。
        这不是实验台的发明——`field.step()` 本来就是生产的周期拍。"""
        if self.field is None:
            return {"ok": False, "error": "未接 CognitiveField"}
        n = max(1, min(500, int(steps)))
        for _ in range(n):
            self.field.step()
        self.field.refresh_modulation()
        return {"ok": True, "steps": n,
                "drives": {k: round(float(v), 4)
                           for k, v in (self.field.drives.levels() or {}).items()},
                "networks": {k: round(float(v), 4)
                             for k, v in (self.field.networks.levels() or {}).items()}}

    # ── 事件与结果（真实管线，§26 的核心要求）────────────────

    def engine(self):
        if self._engine is None and self._reward is not None:
            self._engine = self._reward.mod_engine()
        return self._engine

    def inject(self, event_type: str, source: str = "experiment", **fields) -> dict:
        """注入一次**结构化调制事件**——和生产走同一个 `ModulatorEngine.emit`：
        扇出来自 `事件类型:* -[影响 w]-> 调制器` 的边，门控/夹幅/交互增益/
        circadian_driven 一视同仁。返回 before/after/diff，全部是可复算的数。"""
        eng = self.engine()
        if eng is None:
            return {"ok": False, "error": "没有 ModulatorEngine（也没给 reward）"}
        before = self.numeric_view()
        res = eng.emit(event_type, source=source, **fields)
        after = self.numeric_view()
        out = {"ok": bool(res.get("ok", True)), "event": res.get("event"),
               "source_of_rules": res.get("source"),
               "applied": res.get("applied"), "gated": res.get("gated"),
               "interaction": res.get("interaction"),
               "before": before, "after": after, "diff": self._diff(before, after)}
        self._log("inject", {"event_type": event_type,
                             "applied": [a["mod"] for a in res.get("applied") or []]})
        return out

    def outcome(self, social_outcome: str = None, self_outcome: str = None,
                behavior: str = "experiment", context: str = "lab",
                ref: str = "") -> dict:
        """走**奖赏评估层**的完整一圈：evaluate → release（RPE、预期更新、
        图上扇出、台账）→ modulation()（这次经历值多少学习）。与
        action_system 的结算入口是同两个函数。"""
        rs = self._reward
        if rs is None:
            return {"ok": False, "error": "未接 RewardSystem"}
        before = self.numeric_view()
        events = rs.evaluate(behavior=behavior, context=context,
                             social_outcome=social_outcome,
                             self_outcome=self_outcome, ref=ref)
        rel = rs.release(events)
        learn = rs.modulation(key=f"{behavior}@{context}")
        after = self.numeric_view()
        out = {"ok": rel.get("ok"), "events": [e["event_id"] for e in events],
               "releases": rel.get("releases"), "surprises": rel.get("surprises"),
               "fanout": rel.get("fanout"), "learning_modulation": learn,
               "before": before, "after": after, "diff": self._diff(before, after)}
        self._log("outcome", {"social": social_outcome, "self": self_outcome,
                              "factor": learn.get("factor")})
        return out

    def mood_event(self, kind: str) -> dict:
        """交互事件 → 心情余韵（persona 的公开入口；仍不会动任何调制器——
        单向性由 tests/test_mood_one_way.py 钉住）。"""
        if self.persona is None:
            return {"ok": False, "error": "未接 persona"}
        before = self.numeric_view()
        self.persona.mood_event(kind)
        after = self.numeric_view()
        return {"ok": True, "kind": kind, "mood": self.persona.current_mood(),
                "diff": self._diff(before, after)}

    def dial(self, name: str, value: float, kind: str = "modulator",
             reason: str = "实验台调节") -> dict:
        """手动拧浓度真值——与前端滑杆同一个 `set_value(source="manual")`。
        夹速/量程/历史/镜像同步全都照常（实验不豁免物理，只豁免墙钟）。"""
        r = self.st.set_value(kind, name, value, source="manual", reason=reason)
        if r.get("ok"):
            self.st.sync_graph()
        return r

    # ── 观测面（§22）────────────────────────────────────────

    def numeric_view(self) -> dict:
        """所有通道的浓度/响应/脉冲 + 需求水位 + 心情两项——diff 的原料。
        纯读；不产生历史条目。"""
        mods = {}
        for m in self.st.modulator_names():
            mods[m] = {"conc": round(self.st.modulator_conc(m), 4),
                       "tonic": round(self.st.modulator_tonic(m), 4),
                       "phasic": round(self.st.modulator_pulse(m), 4),
                       "dev": round(self.st.modulator_dev(m), 4)}
        needs = {n: round(self.st.need_level(n), 4)
                 for n in self.st.need_names()}
        view = {"t": round(self.st.now(), 1), "mods": mods, "needs": needs}
        if self.persona is not None:
            try:
                cm = self.persona.current_mood()
                view["mood"] = {k: cm.get(k) for k in
                                ("valence", "afterglow", "modulator_term")}
            except Exception:                           # noqa: BLE001
                pass
        return view

    @staticmethod
    def _diff(before: dict, after: dict) -> dict:
        d = {"mods": {}, "needs": {}, "mood": {}}
        for m, row in after["mods"].items():
            b = before["mods"].get(m, {})
            chg = {k: round(row[k] - float(b.get(k, 0.0) or 0.0), 4)
                   for k in ("conc", "tonic", "phasic", "dev")
                   if abs(row[k] - float(b.get(k, 0.0) or 0.0)) > 1e-9}
            if chg:
                d["mods"][m] = chg
        for n, v in after["needs"].items():
            if abs(v - float(before["needs"].get(n, v) or 0.0)) > 1e-9:
                d["needs"][n] = round(v - float(before["needs"].get(n, 0.0)), 4)
        bm, am = before.get("mood") or {}, after.get("mood") or {}
        for k in ("valence", "afterglow", "modulator_term"):
            if k in am and k in bm and abs(float(am[k] or 0) - float(bm[k] or 0)) > 1e-9:
                d["mood"][k] = round(float(am[k]) - float(bm[k]), 4)
        return d

    def params(self, names: list = None) -> dict:
        """认知场参数读数（14 靶点的当前调制值）。没给 field 就只回调制层
        自己那张表（ModulationLayer.get 是同一个读者用的函数）。"""
        lay = getattr(self.field, "modulation", None)
        if lay is None:
            return {}
        if names is None:
            names = list(((self.config.get("modulator_system") or {})
                          .get("targets") or {}).values())
        return {p: lay.get(p) for p in names}

    def chain(self, event_type: str = None) -> dict:
        """一条链的全貌：事件 →(影响边) 调制器 →(调制边) 参数/偏置/心情。
        `explain_subgraph` / `engine.explain` / `mood_facts` 的**拼装**，
        不新增任何判据——人读与机器读同一份数据。"""
        import modulator_subgraph as MS
        eng = self.engine()
        out = {"event_types": (eng.explain() if eng is not None else {}),
               "subgraph": MS.explain_subgraph(self.kg, self.st, self.config),
               "mood": MS.mood_facts(self.kg, self.st, self.config),
               "modulators": self.st.dump_modulator_state(),
               "clock": {"virtual": self._open,
                         "offset_min": round(self._offset_s / 60.0, 2)}}
        if event_type and eng is not None:
            out["focus"] = eng.rules_for(event_type)
        return out

    def recent(self, n: int = 20) -> list:
        return list(self._trace[-int(n):])

    def observe(self) -> dict:
        """一次拿全观测面（调试台一个 GET 就够画整页）。"""
        return {"clock": {"virtual": self._open,
                          "offset_min": round(self._offset_s / 60.0, 2),
                          "now": round(self.st.now(), 1)},
                "view": self.numeric_view(),
                "params": self.params(),
                "trace": self.recent()}

    def _log(self, action: str, detail: dict):
        self._trace.append({"t": round(self.st.now(), 1), "action": action,
                            **detail})
        del self._trace[:-200]

    # 上下文管理：with 语句里忘了 close 也一定交还时钟
    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False
