# experience_replay.py — 离线经验重放（Experience Replay）：现有机制的调度/入口层
# ============================================================================
# 定位（任务 §三）：这不是第二套学习机制。本模块只做三件事：
#   1. 候选选择——从**已有**数据（experience timeline 的 recent/failed/repeat、
#      CausalLearner 未晋升假设、P3 开放缺口）挑出最有价值的少量经验；
#   2. 再激活注入——完全复用 continuous_cognition._reactivate_field 的直写
#      语义（activation+=boost、cap、hot 跳过、touch、mark_active、
#      register_activation_source("memory_recall")），扩散本身交回
#      DiffusionEngine.diffuse_from（其防饱和/Hebbian 白名单/软遗忘原样生效）；
#   3. 收益核算——新因果边只经**已存在**且证据门控的 promote_to_kg（幂等），
#      其余收益 = Hebbian 实际增量 / 可达性改善；连续无收益 → 退避早停。
#
# 硬约束（任务 §一/§七/§十二/§十三）：
#   - 禁止全时间线扫描：每次最多读 scan_max 条（timeline.recent 走尾部）；
#   - 每轮 batch_max 条经验、seeds_max 个种子、per-experience min_interval；
#   - 只在空闲跑：由 CC 非 busy 分支调用（对话回合天然抢占 tick），且动作
#     执行中（am.busy）不跑；批内每处理一条都查墙钟预算，超了立即停；
#   - 零 LLM：本模块不 import 不调用任何 llm 接口；
#   - 调度状态（上次处理时间/连亏计数）有界落盘；学习收益全部走图谱与
#     causal 的既有持久化，重启自然存活。
# 日志（任务 §十八）：fas_log MEMORY 通道，replay_batch / replay_stopped 摘要；
#   逐条明细仅 debug=True 时发。emit 永不抛（fas_log 合同）。
# ============================================================================

import json
import os
import random
import re
import time

import fas_log
from fas_log import MEMORY
import prior_knowledge as pk

DEFAULTS = {
    "enabled": True,
    "gate_min_interval_s": 20.0,  # 空闲检测：距上轮选择至少开一轮
    "batch_max": 3,               # 每轮最多重放的候选数
    "seeds_max": 12,              # 每轮种子总预算（发射能量上限的粗界）
    "steps": 2,                   # 扩散步数（增量小，避免全图扫）
    "boost": 1.0,                 # 注入量（reactivation 用 1.2，离线略低）
    "boost_cap": 3.0,             # 与再点火直写同帽
    "hot_skip": 0.3,              # 已热节点跳过（reactivation 同款）
    "batch_time_budget_s": 1.0,   # 墙钟预算：到点即停，可被任何实时事务甩开
    "min_interval_s": 3600.0,     # 同一经验一小时内不重复处理
    "recent_window_s": 21600.0,   # "最近"桶 = 近 6 小时
    "scan_max": 200,              # 单次选择最多读的历史条数（禁全量）
    "hypothesis_max": 3,
    "gap_max": 3,
    "stop_after_zero": 3,         # 连续 N 轮零收益 → 退避
    "backoff_s": 1800.0,
    "reach_depth": 4,             # 可达性度量 BFS 深度（有界）
    "reach_cap": 600,             # 可达性度量访问上限
    "state_max_entries": 500,     # 调度状态有界（§十五）
    "state_ttl_s": 7 * 86400.0,
    "debug_log": False,           # 逐条明细日志开关（平时只摘要）
}

_SIG_TARGET = re.compile(r"\(([^()]*)\)$")
_SIG_OUTCOME = re.compile(r"^[^:]+:[^:]+:([^:]+):")  # kind:actor:subj:change


def _g(cfg, key):
    v = (cfg or {}).get(key)
    return DEFAULTS[key] if v is None else v


class ExperienceReplay:
    """空闲时把少量高价值经验重新接进扩散——不新增任何边写路径。"""

    def __init__(self, kg, engine, cfg: dict = None, state_path: str = None,
                 rng: random.Random = None):
        self.kg = kg
        self.engine = engine
        self.cfg = dict(cfg or {})
        self.state_path = (state_path if state_path is not None else
                           os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "data", "experience_replay_state.json"))
        self._rng = rng or random.Random()
        self._last_gate_ts = 0.0
        self._stats = {"batches": 0, "replays": 0, "promoted": 0,
                       "seeds": 0, "lit": 0, "hebb_up": 0, "reach_gain": 0,
                       "new_edges": 0, "aborted": 0}
        # 有界调度状态：{ep_id: ts}（LRU 修剪）+ 连亏计数 + 退避截止
        self._processed = {}       # ep_id → 最近处理时间
        self.zero_streak = 0
        self.next_allowed_ts = 0.0
        self._load_state()

    # ── 门控（任务 §十二/§十三）──────────────────────────

    def gate(self, timeline=None, causal=None, now=None) -> bool:
        """CC 空闲分支每拍调用。返回是否真跑了一轮（便于测试/观测）。

        抢占语义：本函数在 CC 的 `if not self._busy` 内被调——对话一来整个
        拍主体让位，正在跑的重放最多再花 batch_time_budget_s 内的一小段；
        动作执行中（调用方保证，见 CC 注释）不进这里。
        """
        if not _g(self.cfg, "enabled") or timeline is None:
            return False
        now = time.time() if now is None else float(now)
        if now - self._last_gate_ts < _g(self.cfg, "gate_min_interval_s"):
            return False
        if now < self.next_allowed_ts:
            return False
        self._last_gate_ts = now
        return self.run_batch(timeline, causal, now)

    # ── 候选选择（任务 §四/§八）：分桶 + 显著性，不建评分大厦 ──

    def select_candidates(self, timeline, causal=None, now=None) -> list:
        now = time.time() if now is None else float(now)
        window = _g(self.cfg, "recent_window_s")
        min_iv = _g(self.cfg, "min_interval_s")
        cands = []
        seen_ids = set()

        def _fresh(eid):
            if eid in seen_ids:
                return False
            seen_ids.add(eid)
            last = self._processed.get(eid)
            return not (last is not None and now - float(last) < min_iv)

        def _add(eid, reason, salience, refs):
            seeds = self._resolve_refs(refs)
            if not seeds:
                return                     # 引用解析不到图上节点 → 无重放意义
            cands.append({"id": eid, "reason": reason,
                          "salience": round(float(salience), 4), "seeds": seeds})

        # 桶 1：最近窗口内的事件。显著性 = 新鲜度 + 失败/重复加成（全是既有字段）
        try:
            events = timeline.recent(int(_g(self.cfg, "scan_max")),
                                     since=now - window) or []
        except TypeError:                   # 桩 timeline 无 since 参数
            events = timeline.recent(int(_g(self.cfg, "scan_max"))) or []
        for ev in events:
            eid = f"tl:{ev.get('event_type')}:{ev.get('ts')}"
            if not _fresh(eid):
                continue
            age = max(0.0, now - float(ev.get("ts") or now))
            rec = 0.35 * max(0.0, 1.0 - age / max(1.0, window))
            content = ev.get("content") or {}
            meta = (ev.get("meta") or {}) if isinstance(ev.get("meta"), dict) else {}
            reason = "recent"
            bonus = 0.0
            res = content.get("result")
            failed = res is False or (isinstance(res, str)
                                      and res.strip().lower().startswith("fail"))
            if failed:
                bonus = max(bonus, 0.40)    # 桶：失败/意外
                reason = "failed"
            if int(meta.get("repeat") or 1) >= 2:
                bonus = max(bonus, 0.30)    # 桶：高频重复
                reason = reason if bonus >= 0.40 else "repeat"
            refs = [ev.get("subject"), content.get("target"),
                    content.get("block"), content.get("item")]
            _add(eid, reason, rec + bonus + self._rng.random() * 0.05, refs)

        # 桶 2：未晋升的因果假设（"未解决的 causal hypothesis"）——重放让
        # 相关节点共激活；是否晋升**只由**既有证据门（support/confidence）决定
        hyps = getattr(causal, "_hypotheses", None) or {}
        promoted = getattr(causal, "_promoted", set())
        rows = sorted(((k, h) for k, h in hyps.items()
                       if h.get("status") == "hypothesis" and k not in promoted),
                      key=lambda kh: -float(kh[1].get("support") or 0))
        for key, h in rows[:int(_g(self.cfg, "hypothesis_max"))]:
            eid = f"ch:{key}"
            if not _fresh(eid):
                continue
            a_sig = str(h.get("action") or key.split("|", 1)[0])
            o_sig = str(h.get("outcome") or "")
            m = _SIG_TARGET.search(a_sig)
            refs = [m.group(1) if m else None, a_sig, o_sig,
                    (_SIG_OUTCOME.match(o_sig).group(1)
                     if _SIG_OUTCOME.match(o_sig) else None)]
            # 已晋升形态的泛化节点若存在也作种子（操作:x / 变化:y）
            sup = min(1.0, float(h.get("support") or 0) / 5.0)
            _add(eid, "causal_pending", 0.25 + 0.35 * sup, refs)

        # 桶 3：P3 开放缺口（与 exploration gap / prior 相关的经验）
        try:
            gaps = pk.open_gaps(self.kg) or []
        except Exception:
            gaps = []
        for gm in gaps[:int(_g(self.cfg, "gap_max"))]:
            gid = str(gm.get("gap") or "")
            obj = str(gm.get("object") or gm.get("obj") or gid)
            eid = f"gap:{gid}"
            if not gid or not _fresh(eid):
                continue
            refs = [obj, pk.gap_node_id(obj), gid]
            _add(eid, "gap", 0.50 + self._rng.random() * 0.05, refs)

        cands.sort(key=lambda c: -c["salience"])
        # 少量随机探索（§八）：末位 15% 概率换成随机候选
        bm = int(_g(self.cfg, "batch_max"))
        if len(cands) > bm and self._rng.random() < 0.15:
            pool = cands[bm:]
            cands = cands[:bm - 1] + [self._rng.choice(pool)]
        return cands[:bm]

    def _resolve_refs(self, refs) -> list:
        """引用 → **已存在**节点 id。宁缺勿假：绝不为缺失对象造节点。"""
        out, seen = [], set()
        for r in refs:
            s = str(r or "").strip()
            if not s:
                continue
            with self.kg._lock:
                for cand in (s, s.lower(), f"物品:{s.lower()}", f"物品:{s}"):
                    if cand and cand in self.kg.nodes and cand not in seen:
                        seen.add(cand)
                        out.append(cand)
                        break
        return out

    # ── 一轮重放（预算 + 收益核算 + 早停）──────────────────

    def run_batch(self, timeline=None, causal=None, now=None,
                  selected=None) -> bool:
        now = time.time() if now is None else float(now)
        budget_s = float(_g(self.cfg, "batch_time_budget_s"))
        cands = selected if selected is not None else \
            self.select_candidates(timeline, causal, now)
        self._stats["batches"] += 1
        if not cands:
            return False
        n_seeds = 0
        per = []
        promoted_total = []
        stopped_reason = None
        yield_reach = yield_hebb = yield_edges = 0
        seeds_all = []
        for c in cands:
            if time.time() - now > budget_s:
                stopped_reason = "time_budget"       # §十二：可抢占
                break
            if n_seeds >= int(_g(self.cfg, "seeds_max")):
                stopped_reason = "seed_budget"       # §七：显式预算
                break
            seeds = c["seeds"][:int(_g(self.cfg, "seeds_max")) - n_seeds]
            n_seeds += len(seeds)
            res = self._replay_one(c, seeds, budget_s - (time.time() - now))
            per.append({"id": res["id"], "reason": c["reason"],
                        "salience": c["salience"], "lit": res["lit"],
                        "hebb_up": res["hebb_up"], "reach": res["reach"]})
            yield_reach += max(0, res["reach_delta"])
            yield_hebb += res["hebb_up"]
            yield_edges += res["new_edges"]
            seeds_all.extend(seeds)
            self._processed[c["id"]] = now
            if res["lit"] == 0 and res["hebb_up"] == 0:
                # 全种子已热/冷却 → 零收益。早停：同轮剩余候选直接放弃。
                stopped_reason = stopped_reason or "all_hot_zero_yield"
                break
        # 因果收益：调既有晋升口（幂等、证据门在 learner 内）——不判 co-occurrence
        edges_before = len(self.kg.edges)
        promoted = []
        if causal is not None:
            try:
                promoted = causal.promote_to_kg(self.kg, self.engine) or []
            except Exception as e:               # 晋升失败不影响本层
                promoted = []
                fas_log.emit(MEMORY, "WARN", "replay_promote_failed",
                             str(e)[:160], error=str(type(e).__name__))
        self._stats["promoted"] += len(promoted)
        promoted_total.extend(promoted)
        new_edges = max(0, len(self.kg.edges) - edges_before)
        yield_edges += new_edges
        # 收益定义（§五）：**持久性**收益——Hebbian 实际强化 / 可达性提升 /
        # 新边晋升。共激活点亮本身不算收益（那是瞬态场状态）。
        yielded = (yield_hebb > 0) or (yield_reach > 0) \
            or (len(promoted_total) > 0) or (yield_edges > 0)
        self._stats["replays"] += len(per)
        if stopped_reason in ("time_budget", "seed_budget"):
            self._stats["aborted"] += 1
        self._stats["seeds"] += n_seeds
        self._stats["lit"] += sum(p["lit"] for p in per)
        self._stats["hebb_up"] += yield_hebb
        self._stats["reach_gain"] += yield_reach
        self._stats["new_edges"] += yield_edges
        if yielded:
            self.zero_streak = 0
        else:
            self.zero_streak += 1
            if self.zero_streak >= int(_g(self.cfg, "stop_after_zero")):
                self.next_allowed_ts = now + float(_g(self.cfg, "backoff_s"))
                self.zero_streak = 0
                fas_log.emit(MEMORY, "INFO", "replay_stopped",
                             "连续零收益，退避", reason="zero_yield_streak",
                             until=round(now - time.time() + float(
                                 _g(self.cfg, "backoff_s")), 1))
        fas_log.emit(MEMORY, "INFO", "replay_batch",
                     f"重放 {len(per)} 条经验",
                     candidates=len(cands), replayed=len(per),
                     seeds=n_seeds, lit=self._stats["lit"],
                     hebb_up=yield_hebb, reach_delta=yield_reach,
                     new_edges=new_edges, promoted=len(promoted_total),
                     yielded=bool(yielded),
                     stopped_reason=stopped_reason or "complete",
                     cost_ms=round((time.time() - now) * 1000, 1))
        if _g(self.cfg, "debug_log"):
            for p in per:
                fas_log.emit(MEMORY, "DEBUG", "replay_item", "", **p)
        self._save_state()
        return True

    def _replay_one(self, c, seeds, time_budget_s):
        """注入 → 现有扩散。返回核算量。不新增边写路径。"""
        now = time.time()
        lit = hebb_up = new_edges = reach_delta = 0
        reach_before = self._reach(seeds)
        edges_before = len(self.kg.edges)
        # 测 Hebbian 增量：种子一邻边权重快照（有界；白名单/帽由引擎执行）
        watched = self._watch_edges(seeds)
        cold, _hot = self._inject(seeds, c["salience"], time_budget_s)
        if cold and self.engine is not None:
            try:
                self.engine.diffuse_from(cold, steps=int(_g(self.cfg, "steps")))
            except Exception as e:
                fas_log.emit(MEMORY, "WARN", "replay_diffuse_failed",
                             str(e)[:160], ep=c["id"])
        # 点亮：邻域内激活升高 >1e-6 的非种子节点（只测邻域，不全图）
        for nid in watched["nodes"]:
            n = self.kg.nodes.get(nid)
            if n is not None and nid not in seeds:
                if float(getattr(n, "activation", 0.0) or 0.0) > 1e-6:
                    lit += 1
        for e in watched["edges"]:
            edge = self.kg.get_edge(e[0], e[1], e[2]) if hasattr(
                self.kg, "get_edge") else None
            if edge is not None and float(edge.weight) > e[3] + 1e-9:
                hebb_up += 1
        new_edges = max(0, len(self.kg.edges) - edges_before)
        reach_after = self._reach(seeds)
        reach_delta = reach_after - reach_before
        return {"id": c["id"], "lit": lit, "hebb_up": hebb_up,
                "new_edges": new_edges, "reach": reach_after,
                "reach_delta": reach_delta, "cost_ms": round(
                    (time.time() - now) * 1000, 2)}

    def _inject(self, seeds, salience, time_budget_s):
        """reactivation 同款直写（活跃前沿不变量：touch+mark_active+源锚点）。"""
        cap = float(_g(self.cfg, "boost_cap"))
        hot_skip = float(_g(self.cfg, "hot_skip"))
        amt = float(_g(self.cfg, "boost")) * (0.5 + 0.5 * min(1.0, salience))
        hot = set()
        started = time.time()
        with self.kg._lock:
            for nid in seeds:
                if time.time() - started > max(0.0, time_budget_s):
                    break
                n = self.kg.nodes.get(nid)
                if n is None:
                    continue
                a = float(getattr(n, "activation", 0.0) or 0.0)
                if a >= hot_skip:
                    hot.add(nid)
                    continue
                n.activation = min(cap, a + amt)
                n.touch()
        cold = [s for s in seeds if s not in hot]
        if cold and self.engine is not None:
            # 活跃前沿不变量：直写后 mark_active + 源锚点（reactivation 同款）
            self.engine.mark_active(cold)
            self.engine.register_activation_source(cold, "memory_recall")
        return cold, hot

    def _watch_edges(self, seeds):
        """种子一跳邻域：节点集 + (src,dst,rel,weight) 快照。规模有界。"""
        nodes, edges = set(), []
        seen = set()
        with self.kg._lock:
            for e in self.kg.edges:
                if e.src in seeds or e.dst in seeds:
                    nodes.add(e.src)
                    nodes.add(e.dst)
                    key = (e.src, e.dst, e.relation)
                    if key not in seen:
                        seen.add(key)
                        edges.append((e.src, e.dst, e.relation,
                                      float(e.weight)))
                if len(edges) > 200:
                    break
        nodes.update(seeds)
        return {"nodes": list(nodes), "edges": edges}

    def _reach(self, seeds):
        """有界 BFS（关系双向、忽略类型）：种子可达节点数。"""
        depth = int(_g(self.cfg, "reach_depth"))
        cap = int(_g(self.cfg, "reach_cap"))
        adj = {}
        with self.kg._lock:
            for e in self.kg.edges:
                adj.setdefault(e.src, []).append(e.dst)
                adj.setdefault(e.dst, []).append(e.src)
            reach = set()
            frontier = [s for s in seeds if s in self.kg.nodes]
            for _ in range(depth):
                nxt = []
                for nid in frontier:
                    for nb in adj.get(nid, ()):
                        if nb not in reach and nb not in seeds:
                            reach.add(nb)
                            nxt.append(nb)
                frontier = nxt
                if len(reach) > cap:
                    return len(reach)   # 触顶即真值下界，够比较
        return len(reach)

    # ── 有界调度状态（§十五：瞬时状态不无限落盘）────────────

    def _load_state(self):
        if not self.state_path or not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self._processed = d.get("processed") or {}
            self.zero_streak = int(d.get("zero_streak") or 0)
            self.next_allowed_ts = float(d.get("next_allowed_ts") or 0.0)
            self._stats.update(d.get("stats") or {})
        except Exception:
            pass   # 状态可丢：只影响节流，不影响学习收益（那些在图里）

    def _save_state(self):
        if not self.state_path:
            return
        now = time.time()
        ttl = float(_g(self.cfg, "state_ttl_s"))
        items = sorted(self._processed.items(), key=lambda kv: -float(kv[1]))
        items = [(k, v) for k, v in items if now - float(v) <= ttl]
        cap = int(_g(self.cfg, "state_max_entries"))
        self._processed = dict(items[:cap])
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.state_path)),
                        exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"processed": self._processed,
                           "zero_streak": self.zero_streak,
                           "next_allowed_ts": self.next_allowed_ts,
                           "stats": self._stats}, f, ensure_ascii=False)
            os.replace(tmp, self.state_path)
        except Exception:
            pass

    # ── 观测（§十七）──────────────────────────────────────

    def stats(self) -> dict:
        return dict(self._stats, zero_streak=self.zero_streak,
                    next_allowed_ts=self.next_allowed_ts)
