# experiment_recorder.py — 统一 Experiment Recorder（§4，2026-09-28 论文实验套件）
# ============================================================================
# 单一 write 通道：fas_log（观测层真源，只记录不参与决策）。本模块是实验
# run 的"打包器"：为每个 run 生成自包含实验包目录
#   FAS_Research_Experiments/<family>/run_<ts>_<id>/
#     metadata.json     run 元数据（git hash/模式/seed/配置快照/端点/LLM 摘要）
#     events.jsonl      实验事件流（tag 语义同 experiment_mode.xlog 12 标签）
#     snapshots/        graph/internal_state 关键点快照（初/终/控制点）
#     metrics.json      量化指标（计数器、LLM 调用汇总）
#     summary.json      finalize 时的健康状态与备注
# 事件行同时落 fas_log（COGNITION 子系统 exp.* 事件）——日志可全局检索。
# 协议：本模块绝不修改认知系统行为；失败一律静默降级（记录仍可继续）。
# ============================================================================

import json
import logging
import os
import threading
import time

logger = logging.getLogger("experiment.recorder")

_LOCK = threading.RLock()
_RUN_SEQ = [0]
_ROOTS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "FAS_Research_Experiments"),
]


def root_dir() -> str:
    """实验数据根目录（FAS_Research_Experiments/）。"""
    return _ROOTS[0]


class RunRecorder:
    """单个实验 run 的记录器。用法：
        rec = RunRecorder(family="minecraft", mode="learning_closed_loop",
                          seed=3, label="M1 知识发现")
        rec.start()                 # 建目录 + metadata.json
        rec.log("OBSERVATION", "发现 x=...", **fields)
        rec.snapshot("graph_start", graph_dict=kg.to_dict())
        ...
        rec.finalize()              # metrics + summary + manifest 登记
    """

    def __init__(self, family="misc", mode="normal", seed=None,
                 label="", experiment_id=None, run_id=None):
        self.family = str(family or "misc").strip() or "misc"
        self.mode = str(mode or "normal")
        self.seed = seed
        self.label = label
        self.experiment_id = experiment_id or "auto"
        # C32：run_id 加毫秒+序号——秒级分辨率在同秒启动的 run 间碰撞
        # （同族目录互相覆盖，reuse_a2 实测丢 5/27 个 run 目录）。
        _RUN_SEQ[0] += 1
        self.run_id = run_id or time.strftime("run_%Y%m%d_%H%M%S") + \
            f"_{int(time.time() * 1000) % 1000:03d}{_RUN_SEQ[0]:02d}"
        self.dir = os.path.join(root_dir(), self.family, self.run_id)
        self._started = False
        self._finalized = False
        self._step = 0
        self._t0 = time.time()
        self.counters = {}          # 计数器（metrics）
        self._events = []           # 内存事件兜底（落盘失败时长程可用）
        self._snapshots = []
        self._flushed_n = 0         # 已落盘事件数（增量 flush，防缺口/重复）

    # ── 生命周期 ──────────────────────────────────────────────────────
    def start(self, extra=None):
        if self._started:
            return self
        self._started = True
        try:
            os.makedirs(os.path.join(self.dir, "snapshots"), exist_ok=True)
            meta = self._build_metadata(extra)
            self._write_json("metadata.json", meta)
            self.log("RUN_START", f"run 开始 mode={self.mode} label={self.label}",
                     experiment_id=self.experiment_id, run_id=self.run_id)
        except Exception as e:
            logger.warning(f"[Recorder] start 降级: {e}")
        return self

    def finalize(self, extra=None, ok=None):
        if self._finalized:
            return
        self._finalized = True
        try:
            metrics = {
                "duration_s": round(time.time() - self._t0, 2),
                "event_count": self._step,
                "counters": dict(self.counters),
            }
            metrics.update(extra or {})
            if ok is not None:
                metrics["ok"] = bool(ok)
            self._write_json("metrics.json", metrics)
            self.log("RUN_END", f"run 结束 {"ok" if ok is None or ok else "failed"}",
                     **metrics)
            self._flush_events()        # 每 100 条节拍之外的全部残余（含 RUN_END）补盘
            self._append_manifest(metrics)
        except Exception as e:
            logger.warning(f"[Recorder] finalize 降级: {e}")
        return self

    # ── 事件/计数 ─────────────────────────────────────────────────────
    def log(self, tag: str, event: str, **fields) -> dict:
        """记录一条实验事件（事件行 → events.jsonl + fas_log）。"""
        row = {"ts": self._now_iso(), "t_rel_s": round(time.time() - self._t0, 3),
               "step": self._step, "tag": tag, "event": str(event)[:400],
               "fields": {k: (str(v)[:200] if not isinstance(v, (int, float, bool))
                              else v) for k, v in fields.items()}}
        self._step += 1
        self._events.append(row)
        if len(self._events) % 100 == 0:
            self._flush_events()
        try:
            import fas_log
            fas_log.emit(fas_log.COGNITION, "INFO", f"exp.{tag.lower()}",
                         row["event"], run_id=self.run_id, **row["fields"])
        except Exception:
            pass
        return row

    def counter(self, name: str, n: int = 1):
        with _LOCK:
            self.counters[name] = self.counters.get(name, 0) + int(n)

    # ── 快照 ──────────────────────────────────────────────────────────
    def snapshot(self, key: str, graph_dict=None, state_dict=None, note=""):
        """关键点快照：graph（kg.to_dict()）与/或 internal_state 字典。
        graph 可传 dict 或可调用（惰性求值，失败降级）。"""
        if not self._started:
            self.start()
        rec = {"key": key, "ts": self._now_iso(), "note": note}
        try:
            if graph_dict is not None:
                g = graph_dict() if callable(graph_dict) else graph_dict
                fn = os.path.join(self.dir, "snapshots", f"graph_{key}.json")
                self._write_json(fn, g)
                rec["graph"] = os.path.basename(fn)
                rec["graph_nodes"] = len(g.get("nodes", {})) if isinstance(
                    g, dict) else None
            if state_dict is not None:
                s = state_dict() if callable(state_dict) else state_dict
                fn = os.path.join(self.dir, "snapshots", f"state_{key}.json")
                self._write_json(fn, s)
                rec["state"] = os.path.basename(fn)
        except Exception as e:
            rec["error"] = str(e)[:200]
        self._snapshots.append(rec)
        self.log("GRAPH", f"快照 {key}" + (f" {note}" if note else ""),
                 snapshot=key, **{k: v for k, v in rec.items()
                                  if k not in ("ts",)})
        return rec

    def graph_delta(self, before: dict, after: dict, note="") -> dict:
        """两个图快照之间的数量差异（节点/边层级）。
        nodes/edges 兼容两种形状：{id: node} dict 或 [node...] list
        （runtime_graph_*.json 与 /api/graph?full=true 均为 list of dict）。"""
        bn, be = self._graph_ids(before.get("nodes", {})), \
            self._graph_ids(before.get("edges", {}), edge=True)
        an, ae = self._graph_ids(after.get("nodes", {})), \
            self._graph_ids(after.get("edges", {}), edge=True)
        d = {"note": note, "nodes_added": len(an - bn),
             "nodes_removed": len(bn - an), "edges_added": len(ae - be),
             "edges_removed": len(be - ae)}
        self.log("GRAPH", f"差异 {note or 'graph_delta'}", **d)
        return d

    @staticmethod
    def _graph_ids(seq, edge=False):
        """记录/图形式的节点（目）或边 id 集合。空/异常返回空集。"""
        try:
            if isinstance(seq, dict):
                return set(seq) if not edge else {k for k in seq}
            if not seq:
                return set()
            if isinstance(seq[0], dict):
                if edge:
                    return {(e.get("src"), e.get("dst"), e.get("relation"))
                            for e in seq}
                return {n.get("id") for n in seq if n.get("id")}
            return set(seq)
        except Exception:
            return set()

    # ── 内部 ──────────────────────────────────────────────────────────
    @staticmethod
    def _now_iso() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S") + f".{int(time.time()*1000) % 1000:03d}"

    def _write_json(self, name, obj):
        fn = os.path.join(self.dir, name)
        tmp = fn + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, fn)
        return fn

    def _flush_events(self):
        fn = os.path.join(self.dir, "events.jsonl")
        try:
            with _LOCK:
                start = self._flushed_n
                self._flushed_n = len(self._events)
                rows = self._events[start:]
            if not rows:
                return
            mode = "a" if os.path.exists(fn) else "w"
            with open(fn, "a", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False,
                                       default=str) + "\n")
        except Exception:
            pass

    def _build_metadata(self, extra) -> dict:
        import experiment_mode as xm
        meta = {
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "family": self.family,
            "label": self.label,
            "mode": self.mode,
            "seed": self.seed,
            "timestamp": self._now_iso(),
            "git_commit": xm.git_hash(),
            "mc_endpoint": xm.mc_endpoint(),
            "llm": xm.llm_summary(),
            "shields": xm.shield_summary() if xm.enabled() else [],
            "cfg": xm.config_snapshot(),
        }
        if extra:
            meta["extra"] = extra
        return meta

    def _append_manifest(self, metrics):
        fn = os.path.join(root_dir(), "manifest.jsonl")
        try:
            os.makedirs(root_dir(), exist_ok=True)
            row = {"run_id": self.run_id, "family": self.family,
                   "mode": self.mode, "seed": self.seed,
                   "label": self.label, "ts": self._now_iso(),
                   "metrics": metrics}
            with open(fn, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass