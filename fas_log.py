# fas_log.py — FAS 统一运行日志（Observability 层）
# ============================================================================
# 目标：不看代码、不改行为，只凭 logs/ 就能回答
#   "FAS 这一刻在做什么 / 为什么这么做 / 链路是什么"。
#
# 铁律（任务书 §26/约束2/约束6）：
#   - 本模块只做记录，永不参与注意力/决策/驱动/行动/记忆；
#   - 认知模块 import 它可以，但它不 import 任何认知模块（叶子依赖）；
#   - setup() 之前所有 emit 走 NullHandler，静默且零副作用；
#   - 热路径成本 = 一次 put_nowait（QueueHandler 异步落盘）。
#
# 关联字段：runtime_session_id（进程会话）/ cycle_id（复用 internal_state
# 的 "c_%06d"，单一真源，见 set_cycle 接线）/ trace_id（contextvar，
# 输入→认知→决策→LLM→动作→响应 串链）。
# ============================================================================

import atexit
import contextlib
import hashlib
import itertools
import json
import logging
import os
import queue
import socket
import sys
import threading
import time
from datetime import datetime

try:
    from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
except ImportError:  # pragma: no cover
    QueueHandler = QueueListener = RotatingFileHandler = None

# ── 子系统枚举（任务书 §4–§16；值即文件名/jsonl 的 subsystem 字段）──────
SYSTEM = "system"
COGNITION = "cognition"
INPUT = "input"
PERCEPTION = "perception"
GRAPH = "graph"
ACTIVATION = "activation"
DRIVE = "drive"
CURIOSITY = "curiosity"
DECISION = "decision"
LLM = "llm"
ACTION = "action"
MINECRAFT = "minecraft"
MEMORY = "memory"
SELF = "self"
COMMUNICATION = "communication"   # B5/§5：主动言语的投递与回执（不混进 action）
ERROR = "error"

SUBSYSTEMS = (SYSTEM, COGNITION, INPUT, PERCEPTION, GRAPH, ACTIVATION, DRIVE,
              CURIOSITY, DECISION, LLM, ACTION, MINECRAFT, MEMORY, SELF,
              COMMUNICATION, ERROR)

# 拆分 jsonl 的子系统（任务书 §19 点名清单，其余走 fas_all.jsonl）
FANOUT = ("cognition", "graph", "llm", "action", "perception",
          "minecraft", "memory", "error")

_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
           "WARNING": logging.WARNING, "ERROR": logging.ERROR,
           "CRITICAL": logging.CRITICAL}

_fas = logging.getLogger("FAS")          # 结构化事件专用 logger
_fas.propagate = False                   # 不进 root，避免与 basicConfig 双写
_fas.addHandler(logging.NullHandler())   # setup 前静默
_root_qh = None                          # root 捕获挂点（legacy 文本进总览）

# ── 会话 / 关联上下文 ──────────────────────────────────────────────
SESSION_ID = ""
_TRACE_SEQ = itertools.count(1)
_CYCLE_VAR = None      # contextvar（setup 时创建，避免 import 期污染）
_TRACE_VAR = None
_PURPOSE_VAR = None
_TCOUNT_VAR = None
_TLS = threading.local()


def _now_iso() -> str:
    dt = datetime.now()
    return f"{dt.strftime('%Y-%m-%d %H:%M:%S')}.{dt.microsecond // 1000:03d}"


class _State:
    """运行期单例：队列、计数器、去重表、聚合器、监听线程。"""

    def __init__(self):
        self.started = False
        self.log_dir = "logs"
        self.q = None
        self.listener = None
        self.console_events = {"startup", "shutdown", "session_summary", "runtime_summary"}
        self.level = logging.INFO
        self.sub_level = {}            # subsystem -> 最低级别数值
        self.text_policy = "truncate"  # full | truncate | hash
        self.text_max = 200
        self.lock = threading.Lock()
        self.counters = {}             # name -> int
        self.by_sub = {}               # subsystem -> 结构化事件数
        self.dropped = 0
        self.dedup = {}                # key -> [count, first_ts, last_ts, 摘要data]
        self.dedup_window = 60.0
        self.aggs = {}                 # key -> Aggregator
        self.summary_providers = []
        self.summary_thread = None
        self.summary_stop = threading.Event()
        self.start_ts = 0.0
        self.max_bytes = 10 * 1024 * 1024
        self.backup = 5


_ST = _State()


# ── 初始化 / 关闭 ─────────────────────────────────────────────────

def setup(log_dir: str = "logs", level: str = "INFO", config: dict = None,
          capture_root: bool = True, summary_interval_s: float = 300.0,
          console: bool = True) -> bool:
    """装配统一日志。幂等：重复调用只刷新一次，返回是否已就绪。

    只加 handler，不改 basicConfig 的既有控制台输出（约束6）。
    """
    global SESSION_ID, _CYCLE_VAR, _TRACE_VAR, _PURPOSE_VAR, _TCOUNT_VAR, _root_qh
    import contextvars
    if _CYCLE_VAR is None:
        _CYCLE_VAR = contextvars.ContextVar("fas_cycle", default=None)
        _TRACE_VAR = contextvars.ContextVar("fas_trace", default=None)
        _PURPOSE_VAR = contextvars.ContextVar("fas_purpose", default=None)
        _TCOUNT_VAR = contextvars.ContextVar("fas_tcount", default=None)
    if _ST.started or QueueHandler is None:
        return _ST.started
    cfg = config or {}
    _ST.log_dir = log_dir or "logs"
    try:
        os.makedirs(_ST.log_dir, exist_ok=True)
    except OSError:
        return False
    _ST.level = _LEVELS.get(str(cfg.get("log_level") or level).upper(), logging.INFO)
    _ST.text_policy = str(cfg.get("log_text_policy") or "truncate")
    _ST.text_max = int(cfg.get("log_text_truncate") or 200)
    _ST.max_bytes = int(float(cfg.get("log_rotated_mb") or 10) * 1024 * 1024)
    _ST.backup = int(cfg.get("log_keep_files") or 5)
    _ST.dedup_window = float(cfg.get("log_exc_dedup_window_s") or 60)
    _ST.started = True
    SESSION_ID = (time.strftime("ses_%Y%m%d_%H%M%S_") +
                  os.urandom(2).hex())
    _ST.start_ts = time.time()

    q = queue.Queue(maxsize=int(cfg.get("log_queue_size") or 20000))
    _ST.q = q
    handlers = []
    human_fmt = _HumanFormatter()
    json_fmt = _JsonFormatter()

    def _fh(name, formatter, level=logging.NOTSET, filt=None):
        try:
            h = RotatingFileHandler(os.path.join(_ST.log_dir, name),
                                    maxBytes=_ST.max_bytes,
                                    backupCount=_ST.backup,
                                    encoding="utf-8", delay=True)
        except OSError:
            return None
        h.setLevel(level)
        h.setFormatter(formatter)
        if filt:
            h.addFilter(filt)
        return h

    # 总览：结构化 + legacy 文本都进 runtime.log
    handlers.append(_fh("runtime.log", human_fmt))
    # 机读：全量结构化
    handlers.append(_fh("fas_all.jsonl", json_fmt, filt=lambda r: hasattr(r, "fas")))
    # 拆分文件（§19 点名清单）。error.jsonl 特殊：收所有子系统的 ERROR+，
    # 不只 subsystem=="error"——§16 要求异常在单一文件可查。
    for sub in FANOUT:
        if sub == "error":
            filt = lambda r: getattr(r, "fas", {}).get("level") in ("ERROR", "CRITICAL")
        else:
            filt = (lambda s: (lambda r: getattr(r, "fas", {}).get("subsystem") == s))(sub)
        handlers.append(_fh(f"{sub}.jsonl", json_fmt, filt=filt))
    # error.jsonl 同时收 legacy 的 ERROR+
    handlers.append(_fh("error_legacy.log", human_fmt, level=logging.ERROR,
                        filt=lambda r: not hasattr(r, "fas")))
    if console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(human_fmt)
        ch.addFilter(_ConsoleFilter(_ST))
        handlers.append(ch)
    handlers = [h for h in handlers if h]

    _ST.listener = QueueListener(q, *handlers, respect_handler_level=True)
    _ST.listener.start()
    _fas.handlers = [QueueHandler(q)]
    if capture_root:
        # legacy getLogger(__name__) 的文本也进总览文件（不重复上控制台）
        _root_qh = QueueHandler(q)
        _root_qh.addFilter(lambda r: not hasattr(r, "fas"))
        logging.getLogger().addHandler(_root_qh)

    if summary_interval_s and summary_interval_s > 0:
        _start_summary_thread(summary_interval_s)
    atexit.register(shutdown)
    return True


def shutdown(reason: str = "process_exit"):
    """退出收口：会话摘要 + 去重表冲刷 + 停监听线程。幂等。"""
    if not _ST.started or _ST.q is None:
        return
    st = _ST.q
    if getattr(_TLS, "in_shutdown", False):
        return
    _TLS.in_shutdown = True
    try:
        flush_dedups()
        uptime = round(time.time() - _ST.start_ts, 1)
        emit(SYSTEM, "INFO", "session_summary",
             f"会话结束 {SESSION_ID}（uptime {uptime}s）",
             uptime_s=uptime, events=dict(_ST.by_sub),
             counters=_snapshot_counters(), dropped=_ST.dropped,
             reason=reason,
             gauges=_gauges())
    except Exception:
        pass
    try:
        for a in list(_ST.aggs.values()):
            a.flush()
    except Exception:
        pass
    _ST.summary_stop.set()
    try:
        if _root_qh is not None:
            logging.getLogger().removeHandler(_root_qh)
        _ST.listener.stop()
    except Exception:
        pass
    _ST.q = None
    _ST.started = False
    _TLS.in_shutdown = False


# ── 关联 ID ───────────────────────────────────────────────────────

def new_trace(source: str = "app") -> str:
    tid = f"tr{next(_TRACE_SEQ) % 1000000:06d}"
    if _TRACE_VAR is not None:
        _TRACE_VAR.set(tid)
    if _TCOUNT_VAR is not None:
        _TCOUNT_VAR.set({})
    emit(SYSTEM, "DEBUG", "trace_start", f"trace 开启（source={source}）",
         trace_id=tid, source=source)
    return tid


def ensure_trace(source: str = "auto") -> str:
    if _TRACE_VAR is not None and _TRACE_VAR.get():
        return _TRACE_VAR.get()
    return new_trace(source)


def get_trace():
    return _TRACE_VAR.get() if _TRACE_VAR is not None else None


def set_cycle(cycle_id):
    """周期关联的唯一接线点（internal_state.begin_cycle 内调用）。"""
    if _CYCLE_VAR is not None:
        _CYCLE_VAR.set(cycle_id)


_EPHEMERAL_SEQ = itertools.count(1)


def ephemeral_cycle() -> str:
    """internal_state 之外的短周期（如 CC 脉冲）用的临时 cycle 关联 id。
    前缀 x_ 与 c_ 区分：它不进 InternalState 轮次账本，只作日志关联。"""
    return f"x_{next(_EPHEMERAL_SEQ) % 1000000:06d}"


def get_cycle():
    return _CYCLE_VAR.get() if _CYCLE_VAR is not None else None


@contextlib.contextmanager
def llm_purpose(purpose: str):
    """调用点标注 LLM 用途：with fas_log.llm_purpose("dialogue_decomposition")。
    只提供日志字段，不改变任何行为。"""
    tok = _PURPOSE_VAR.set(purpose) if _PURPOSE_VAR is not None else None
    try:
        yield
    finally:
        if tok is not None:
            _PURPOSE_VAR.reset(tok)


def current_purpose():
    return _PURPOSE_VAR.get() if _PURPOSE_VAR is not None else None


# trace 作用域计数（如本轮 LLM 调用次数）：new_trace 时清零，随线程/请求隔离
def bump_trace(name: str, n: int = 1):
    if _TCOUNT_VAR is None:
        return
    d = _TCOUNT_VAR.get()
    if d is None:
        d = {}
        _TCOUNT_VAR.set(d)
    d[name] = d.get(name, 0) + n


def trace_count(name: str) -> int:
    d = _TCOUNT_VAR.get() if _TCOUNT_VAR is not None else None
    return int((d or {}).get(name, 0))


# ── 核心发射 ──────────────────────────────────────────────────────

def emit(subsystem: str, level: str, event: str, msg: str = "", _exc=None,
         _throttle_s: float = 0.0, **data):
    """发一条结构化事件。_ST 未就绪时零成本返回。

    观测层纪律：本函数**永不向外抛异常**（内部任何失败静默丢该条并计入
    dropped）——调用点不需要包 try/except，日志不可能改变认知行为。

    _throttle_s>0 时：同 (subsystem,event) 在窗口内折叠计数，只保留首条全文，
    窗口结束后的下一条携带 suppressed=N（用于可容忍丢失的观测点；
    主干 cycle/decision 事件不要用）。
    """
    if not _ST.started:
        return
    try:
        _emit_inner(subsystem, level, event, msg, _exc, _throttle_s, data)
    except Exception:
        try:
            with _ST.lock:
                _ST.dropped += 1
        except Exception:
            pass


def _emit_inner(subsystem, level, event, msg, _exc, _throttle_s, data):
    lv = _LEVELS.get(str(level).upper(), logging.INFO)
    # 子系统级设置优先于全局（允许全局 INFO、graph=DEBUG 的局部放开）
    if lv < _ST.sub_level.get(subsystem, _ST.level):
        return
    now = time.time()
    if _throttle_s > 0:
        key = (subsystem, event)
        last = _ST.aggs.get(("__thr",) + key)
        if last and now - last < _throttle_s:
            with _ST.lock:
                _ST.counters[f"throttled:{event}"] = _ST.counters.get(f"throttled:{event}", 0) + 1
            _ST.aggs[("__thr",) + key] = now
            return
        _ST.aggs[("__thr",) + key] = now
    rec = {
        "ts": _now_iso(),
        "level": str(level).upper(),
        "subsystem": subsystem,
        "event": event,
        "msg": msg,
        "session_id": SESSION_ID,
        "cycle_id": get_cycle(),
        "trace_id": get_trace(),
    }
    data = dict(data or {})
    if _exc is not None:
        rec["exc"] = _trim_exc(_exc)
    if data.pop("_with_exc", False) and sys.exc_info()[0] is not None:
        rec["exc"] = _trim_exc(_format_exc())
    if data:
        rec["data"] = data
    with _ST.lock:
        _ST.by_sub[subsystem] = _ST.by_sub.get(subsystem, 0) + 1
    try:
        _ST.q.put_nowait(_make_record(subsystem, lv, rec))
    except queue.Full:
        with _ST.lock:
            _ST.dropped += 1


def _make_record(subsystem, lv, rec):
    r = logging.makeLogRecord({"name": "FAS", "levelno": lv,
                               "levelname": rec.get("level", "INFO"),
                               "msg": rec.get("msg", ""), "exc_info": None})
    r.fas = rec
    return r


class _HumanFormatter(logging.Formatter):
    """结构化：ts [LEVEL] SUBSYS event cycle trace — msg {k=v…}；
    legacy：ts [LEVEL] name: msg（与 basicConfig 同款式，多行 traceback 保留）。"""

    LEGACY_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    def __init__(self):
        super().__init__(fmt=self.LEGACY_FMT)

    def format(self, record):
        rec = getattr(record, "fas", None)
        if rec is None:
            try:
                base = super().format(record)
            except Exception:
                return repr(record.getMessage())
            if record.exc_info:
                base += "\n" + self.formatException(record.exc_info)
            return base
        parts = [rec["ts"], f"[{rec['level']}]", rec["subsystem"].upper(),
                 rec["event"]]
        if rec.get("cycle_id"):
            parts.append(rec["cycle_id"])
        if rec.get("trace_id"):
            parts.append(rec["trace_id"])
        line = " ".join(parts)
        if rec.get("msg"):
            line += " — " + str(rec["msg"]).replace("\n", " ⏎ ")
        d = rec.get("data")
        if d:
            try:
                line += " " + json.dumps(d, ensure_ascii=False, default=str,
                                         separators=(",", ":"))[:600]
            except Exception:
                pass
        if rec.get("exc"):
            line += " EXC: " + rec["exc"].replace("\n", " ⏎ ")[:800]
        return line


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        rec = getattr(record, "fas", None)
        if rec is None:
            return ""  # fanout 文件都有 filt，理论上到不了这里
        try:
            return json.dumps(rec, ensure_ascii=False, default=str)
        except Exception:
            return json.dumps({"subsystem": "system", "event": "json_fail",
                               "level": rec.get("level", "INFO")})


class _ConsoleFilter:
    """控制台只放行：ERROR/CRITICAL + 会话级摘要事件，其余只进文件——防刷屏
    （约束5）。DEBUG 永不进控制台。"""

    def __init__(self, st):
        self.st = st

    def filter(self, record):
        rec = getattr(record, "fas", None)
        if rec is None:
            return False  # legacy 已有 basicConfig 的控制台通道，不重复
        lv = rec["level"]
        if lv in ("ERROR", "CRITICAL", "WARNING"):
            return True
        return (lv == "INFO" and rec["event"] in self.st.console_events
                and rec["subsystem"] == SYSTEM)


# ── 便捷封装：模块侧一个 get_logger 就够 ──────────────────────────

class _Sub:
    __slots__ = ("sub",)

    def __init__(self, sub):
        self.sub = sub

    def debug(self, event, msg="", **d):
        emit(self.sub, "DEBUG", event, msg, **d)

    def info(self, event, msg="", **d):
        emit(self.sub, "INFO", event, msg, **d)

    def warning(self, event, msg="", **d):
        emit(self.sub, "WARNING", event, msg, **d)

    def error(self, event, msg="", **d):
        d["_with_exc"] = True
        emit(self.sub, "ERROR", event, msg, **d)

    def exception(self, event, msg="", exc=None, **d):
        """记录一次异常（带 traceback + 去重限频，§16）。
        窗口内重复只累计计数不落盘；窗口过后的首条携带 repeated_exception。"""
        e = exc if exc is not None else _format_exc()
        key = (self.sub, event, _exc_type_of(e))
        action = _bump_dedup(key, e, d)
        if action == _EMIT:
            emit(self.sub, "ERROR", event, msg, _exc=e, **d)
        elif action == _SKIP:
            return
        else:  # (count, first_ts)
            cnt, first = action
            emit(self.sub, "ERROR", event,
                 f"{msg}（同类异常 {cnt} 次，首次 {first}）",
                 _exc=e, repeated_exception=cnt, first_ts=first, **d)


_loggers = {}


def get_logger(subsystem: str) -> _Sub:
    """fas_log.get_logger(fas_log.COGNITION).info("cycle_start", "...", k=v)"""
    if subsystem not in SUBSYSTEMS:
        subsystem = SYSTEM
    if subsystem not in _loggers:
        _loggers[subsystem] = _Sub(subsystem)
    return _loggers[subsystem]


# ── 异常去重（§16） ───────────────────────────────────────────────

def _format_exc():
    try:
        return "".join(__import__("traceback").format_exc(limit=12))
    except Exception:
        return "<unprintable>"


def _trim_exc(e) -> str:
    s = str(e or "")
    return s if len(s) <= 4000 else s[:2000] + "\n...[truncated]...\n" + s[-1000:]


def _exc_type_of(e) -> str:
    for ln in reversed(str(e).strip().splitlines() or [""]):
        ln = ln.strip()
        if ln and not ln.startswith("File "):
            return ln[:120]
    return "?"


_EMIT, _SKIP = "emit", "skip"


def _bump_dedup(key, e, d):
    """返回 _EMIT（首见，发全文）/ _SKIP（窗口内重复，吞掉只计数）/
    (count, first_ts)（窗口已过且此前累计>1，发折叠条）。"""
    now = time.time()
    with _ST.lock:
        ent = _ST.dedup.get(key)
        if ent is None:
            _ST.dedup[key] = [1, now, now]
            return _EMIT
        if now - ent[1] >= _ST.dedup_window:
            cnt = ent[0]
            _ST.dedup[key] = [1, now, now]
            if cnt > 1:
                # suppressed 计数在 _SKIP 分支已逐次累加，这里不再重复
                return cnt, datetime.fromtimestamp(ent[1]).strftime("%H:%M:%S")
            return _EMIT
        ent[0] += 1
        ent[2] = now
        _ST.counters[f"dedup_suppressed:{key[1]}"] = _ST.counters.get(
            f"dedup_suppressed:{key[1]}", 0) + 1
        return _SKIP


def flush_dedups():
    with _ST.lock:
        _ST.dedup.clear()


# ── 高频源聚合器（§6/§17：感知、激活这类先聚合再落）────────────────

class Aggregator:
    def __init__(self, sub, event, flush_every_s=60.0, flush_every_n=200,
                 msg=""):
        self.sub, self.event, self.msg = sub, event, msg
        self.every_s, self.every_n = flush_every_s, flush_every_n
        self.n = 0
        self.fields = {}
        self.last = time.time()
        self.lock = threading.Lock()

    def add(self, n=1, **last_fields):
        if not _ST.started:
            return
        with self.lock:
            self.n += n
            self.fields.update(last_fields)
            if self.n >= self.every_n or time.time() - self.last >= self.every_s:
                self._flush_locked()

    def flush(self):
        with self.lock:
            if self.n:
                self._flush_locked()

    def _flush_locked(self):
        fields, self.fields = self.fields, {}
        n, self.n = self.n, 0
        self.last = time.time()
        emit(self.sub, "INFO", self.event, self.msg, count=n, **fields)


def aggregator(key: str, sub, event, **kw) -> Aggregator:
    """进程内共享的命名聚合器（按 key 幂等）。"""
    with _ST.lock:
        a = _ST.aggs.get(key)
        if a is None:
            a = _ST.aggs[key] = Aggregator(sub, event, **kw)
        return a


def flush_aggregators():
    for a in list(_ST.aggs.values()):
        if isinstance(a, Aggregator):
            try:
                a.flush()
            except Exception:
                pass


# ── 计数器 / 摘要 ─────────────────────────────────────────────────

def bump(name: str, n: int = 1):
    if not _ST.started:
        return
    with _ST.lock:
        _ST.counters[name] = _ST.counters.get(name, 0) + n


def _snapshot_counters():
    with _ST.lock:
        return dict(_ST.counters)


def register_summary_provider(fn):
    """fn() -> dict，被周期性摘要/会话摘要合并（如图谱规模、通道状态）。"""
    _ST.summary_providers.append(fn)


def _gauges() -> dict:
    out = {}
    for fn in _ST.summary_providers:
        try:
            out.update(fn() or {})
        except Exception:
            pass
    return out


def _start_summary_thread(interval_s: float):
    def _loop():
        while not _ST.summary_stop.wait(interval_s):
            try:
                flush_dedups()
                emit(SYSTEM, "INFO", "runtime_summary",
                     f"[RUNTIME] {socket.gethostname()} up "
                     f"{round(time.time() - _ST.start_ts)}s",
                     events=dict(_ST.by_sub), counters=_snapshot_counters(),
                     dropped=_ST.dropped,
                     gauges=_gauges())
            except Exception:
                pass
    t = threading.Thread(target=_loop, daemon=True, name="fas-log-summary")
    _ST.summary_thread = t
    t.start()


# ── 文本策略（§5：full | truncate | hash；默认截断+哈希防敏感全文）──

def text(s, max_len: int = None) -> str:
    s = str(s if s is not None else "")
    pol = _ST.text_policy
    if pol == "full":
        return s
    h = "sha1:" + hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()[:10]
    if pol == "hash":
        return h
    m = max_len or _ST.text_max
    if len(s) <= m:
        return s
    return f"{s[:m]}…[{h}]"


# ── 动态级别（§18） ───────────────────────────────────────────────

def set_level(level: str, subsystem: str = None):
    """全局或按子系统调级别。subsystem=None → 全局。"""
    lv = _LEVELS.get(str(level).upper())
    if lv is None:
        return False
    if subsystem is None:
        _ST.level = lv
    else:
        _ST.sub_level[subsystem] = lv
    return True


def get_level(subsystem: str = None):
    if subsystem is None:
        for k, v in _LEVELS.items():
            if v == _ST.level:
                return k
        return str(_ST.level)
    lv = max(_ST.level, _ST.sub_level.get(subsystem, 0))
    for k, v in _LEVELS.items():
        if v == lv:
            return k
    return str(lv)


def is_enabled(subsystem: str, level: str = "INFO") -> bool:
    if not _ST.started:
        return False
    return _LEVELS.get(str(level).upper(), logging.INFO) >= max(
        _ST.level, _ST.sub_level.get(subsystem, 0))


def enabled_for_cognition() -> bool:
    """给观测接线点做便宜的短路判断（不参与任何认知决策）。"""
    return _ST.started


# ── 顶层异常兜底（§3：未捕获异常必须可见）─────────────────────────

def install_excepthooks():
    """sys/threading 两个钩子，转成 ERROR 事件。重复注册无害。"""
    def _sys_hook(tp, val, tb):
        try:
            emit(SYSTEM, "CRITICAL", "uncaught_exception",
                 f"{tp.__name__}: {val}",
                 _exc="".join(__import__("traceback").format_exception(tp, val, tb)))
        except Exception:
            pass
        __import__("sys").__excepthook__(tp, val, tb)

    def _thr_hook(args):
        try:
            emit(SYSTEM, "CRITICAL", "uncaught_thread_exception",
                 f"{args.exc_type.__name__}: {args.exc_value}",
                 thread=args.thread.name if args.thread else "?",
                 _exc="".join(__import__("traceback").format_exception(
                     args.exc_type, args.exc_value, args.exc_traceback)))
        except Exception:
            pass

    try:
        if getattr(sys, "_fas_excepthook", False) is False:
            sys.excepthook = _sys_hook
            sys._fas_excepthook = True
        threading.excepthook = _thr_hook
    except Exception:
        pass
