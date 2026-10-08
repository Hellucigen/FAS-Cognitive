# experiment_mode.py — 统一实验模式入口（FAS_EXPERIMENT_MODE）
# ============================================================================
# 单一真相源：config["experiment"]（config.py:1877-1913 默认段）。本模块是
# 一切实验相关的门：模式判定、调制屏蔽、启动横幅、结构化实验日志、seed。
#
# ══ 模式枚举（统一入口，全部经本模块判定，禁止他处散读环境变量） ══
#   normal             正常模式（实验全关；兼容旧值 "off"）
#   learning_closed_loop  学习闭环实验（2026-09-25 §16/§20 定版语义：调制
#                       全屏蔽 + 注入结构化目标 + [TAG] 实验日志）
#   sandbox             受控环境实验（调制屏蔽 + seed + 实验日志；不注入任务
#                       目标——沙箱任务由实验部署脚本通过环境/场景给出）
# 启动开关：环境变量 FAS_EXPERIMENT_MODE=<mode>（新旧兼容：EXPERIMENT_MODE
# 亦认可，新名优先）。取非规范值 = normal（不报错，横幅里如实显示原文）。
#
# ── 本模块 API ──────────────────────────────────────────────────────────
#   configure(config)        装配期注入 + 环境变量覆写 + seed 播种
#   mode() -> str            规范模式名（normal|learning_closed_loop|sandbox）
#   enabled() -> bool        实验模式是否激活（learning_closed_loop|sandbox）
#   shield(name) -> bool     某调制通道是否应被屏蔽（未激活恒 False）
#   goal_obtain() -> str     实验目标（learning_closed_loop 定向目标）
#   attention_floor() -> float  目标节点注意地板
#   prior_extra() -> list    人工先验补充行
#   banner()                 启动横幅（FAS RESEARCH EXPERIMENT，只打一次）
#   xlog(tag, msg, **fields) 结构化实验日志（12 标签，绝不抛）
#   seed_value() -> int|None 当前实验 seed
#   git_hash() -> str        仓库 HEAD hash（lazy+缓存，失败返回 ""）
#   experiment_id() -> str   实验标识（config 或自动生成）
# ============================================================================
# 屏蔽清单与消费者（2026-09-25 §20；谁在何处尊重本开关）：
#   hormone_to_params     app.py 装配：跳过 hormone tonic 读数注册
#   graph_projection      app.py 装配：modulator_system.subgraph=False
#   graph_pulse           config：modulator_system.graph_pulse=False
#   drift                 config：need/circadian 漂移速率=0
#   modulation_events     modulation_events.ModulatorEngine.apply_event 入口
#   internal_state_heartbeat  continuous_cognition.tick_once：decay/drift/
#                         needs 三个写入节拍跳过（状态钉在装配时刻）
#   world_events_to_needs autonomy._apply_event_effects 的需求写跳过
#   mood                  app.py 装配：不调 set_mood_source + 落点旁路
#   social_drive          config：drive_field.social.enabled=False + 候选过滤
#   personality_tendency  config：autonomy.weights.tendency=0 +
#                         learning_modulation 系数中性
#   cognition_llm         cognition_modes.BudgetManager.can_call → False
#   idle_companion        cc 表达节拍（无人时不说）
#   night_preempt         autonomy 目标导向探索候选 night_stop=False
# 事实/因果通道本来就零 LLM（timeline/CausalLearner/prior/配方全为确定性
# 代码，test_no_llm_on_fact_path 钉死），这里关的是**调制与表达**。
# ============================================================================

import logging
import os
import subprocess
import threading

logger = logging.getLogger("experiment")

_LOCK = threading.RLock()
_CFG = {}              # config["experiment"] 的引用（configure 注入）
_STARTED = False       # banner 只打一次
_GIT_HASH = None
_EXPERIMENT_ID = None
_SANDBOX_ONLY_SEED = None   # 记录实际播种值（banner 展示用）

# 环境变量：FAS_EXPERIMENT_MODE 优先（新名），EXPERIMENT_MODE 兼容（旧名）
_ENV_VARS = ("FAS_EXPERIMENT_MODE", "EXPERIMENT_MODE")

# 规范模式名
MODE_NORMAL = "normal"
MODE_LEARNING = "learning_closed_loop"
MODE_SANDBOX = "sandbox"
_MODES = {MODE_NORMAL, MODE_LEARNING, MODE_SANDBOX}
# 兼容别名 → 规范名（不认的取值一律落 normal，横幅如实显示原文）
_ALIASES = {"off": MODE_NORMAL, "": MODE_NORMAL,
            "lcl": MODE_LEARNING, "closed_loop": MODE_LEARNING,
            "sb": MODE_SANDBOX}


def _normalize(raw) -> str:
    v = str(raw or "").strip().lower()
    if v in _MODES:
        return v
    return _ALIASES.get(v, MODE_NORMAL)


def mode() -> str:
    """当前规范模式名。"""
    with _LOCK:
        return _normalize(_CFG.get("mode"))


def seed_value() -> int | None:
    with _LOCK:
        try:
            s = _CFG.get("seed")
            return int(s) if s not in (None, "", 0) else None
        except (TypeError, ValueError):
            return None


def experiment_id() -> str:
    """实验标识：config["experiment"]["experiment_id"] 或自动生成。"""
    global _EXPERIMENT_ID
    if _EXPERIMENT_ID:
        return _EXPERIMENT_ID
    with _LOCK:
        cfg_id = str(_CFG.get("experiment_id") or "").strip()
        import time as _t
        import random as _r
        if cfg_id:
            _EXPERIMENT_ID = cfg_id
        else:
            _EXPERIMENT_ID = (f"fas_exp_{_t.strftime('%Y%m%d_%H%M%S')}_"
                              f"{_r.randrange(10**4):04d}")
        return _EXPERIMENT_ID


def configure(config) -> None:
    """装配期注入（app 启动即调；测试可注入自定义 dict）。
    环境变量 FAS_EXPERIMENT_MODE（兼容 EXPERIMENT_MODE）非空时覆写 mode；
    若配置了 seed，立即统一播种（random/numpy/torch 已加载者）。"""
    global _CFG, _SANDBOX_ONLY_SEED
    with _LOCK:
        _CFG = dict((config or {}).get("experiment") or {})
    for var in _ENV_VARS:
        _env = (os.environ.get(var) or "").strip()
        if _env:
            with _LOCK:
                _CFG["mode"] = _env
            break
    try:
        (config or {})["experiment"] = _CFG
    except Exception:
        pass
    _apply_seed()


def _apply_seed() -> None:
    """实验 seed 播种（random/numpy/torch 全部统一；torch 未加载则跳过，
    不得因播种触发大型库加载）。"""
    global _SANDBOX_ONLY_SEED
    s = seed_value()
    if s is None:
        return
    import random as _r
    _r.seed(s)
    try:
        import numpy as _np
        _np.random.seed(s)
    except Exception:
        pass
    try:
        import torch as _torch
        _torch.manual_seed(s)          # GPU 态也统一
    except Exception:
        pass
    _SANDBOX_ONLY_SEED = s
    logger.info("[Experiment] 随机种子已统一播种 seed=%s", s)


def enabled() -> bool:
    """实验模式是否激活（learning_closed_loop 或 sandbox）。"""
    with _LOCK:
        return _normalize(_CFG.get("mode")) in (MODE_LEARNING, MODE_SANDBOX)


def shield(name: str) -> bool:
    """某条调制通道是否应被屏蔽。实验未开→恒 False（永不干扰正常行为）。"""
    if not enabled():
        return False
    with _LOCK:
        table = _CFG.get("shield") or {}
    return bool(table.get(name, True))      # 实验开启时默认全shield


def goal_obtain() -> str:
    """learning_closed_loop 实验目标（获得某物品）。返回规范物品名（英文），
    空=不设目标。sandbox 模式不注入任务目标（任务由部署侧给出）。"""
    if mode() != MODE_LEARNING:
        return ""
    with _LOCK:
        return str(_CFG.get("goal_obtain") or "").strip().lower()


def attention_floor() -> float:
    with _LOCK:
        try:
            return float(_CFG.get("attention_floor", 0.9))
        except (TypeError, ValueError):
            return 0.9


def prior_extra() -> list:
    """人工先验补充行（运行时数据缺口的最小闭环，provenance 必须自带
    version_verified 标记）。"""
    with _LOCK:
        return list(_CFG.get("prior_extra") or [])


def git_hash() -> str:
    """仓库 HEAD hash（lazy+缓存；非 git 仓库/失败返回 ""，绝不抛）。"""
    global _GIT_HASH
    if _GIT_HASH is not None:
        return _GIT_HASH
    try:
        p = subprocess.run(["git", "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=5,
                           cwd=os.path.dirname(os.path.abspath(__file__)))
        _GIT_HASH = p.stdout.strip() if p.returncode == 0 else ""
    except Exception:
        _GIT_HASH = ""
    return _GIT_HASH


def mc_endpoint() -> str:
    """Minecraft 端点（bot 真源 minecraft/bot/config.json；读不到返回
    "not_configured"）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "minecraft", "bot", "config.json"),
                  encoding="utf-8") as f:
            import json as _json
            cfg = _json.load(f)
        return f"{cfg.get('host', '127.0.0.1')}:{cfg.get('port', '?')}" \
               f" (bridge {cfg.get('bridge_port', 5010)})"
    except Exception:
        return "not_configured"


def llm_summary() -> str:
    """LLM 配置摘要（provider/model 只读，绝不含任何密钥）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "data", "llm_config.json"),
                  encoding="utf-8") as f:
            import json as _json
            cfg = _json.load(f)
        return f"provider={cfg.get('provider', '?')} model={cfg.get('model', '?')}"
    except Exception:
        return "provider=? model=? (no llm_config)"


def config_snapshot() -> dict:
    """实验配置段完整快照（recorder/审计用，只读引用语义）。"""
    with _LOCK:
        return dict(_CFG)


def shield_summary() -> list:
    """已屏蔽通道清单（enabled 时有效）。"""
    if not enabled():
        return []
    with _LOCK:
        table = _CFG.get("shield") or {}
    return [k for k, v in table.items() if v]


def banner() -> None:
    global _STARTED
    if _STARTED or not enabled():
        return
    _STARTED = True
    import time as _t
    m = mode()
    lines = [
        "",
        "═" * 72,
        "FAS RESEARCH EXPERIMENT",
        "═" * 72,
        f"experiment_id : {experiment_id()}",
        f"mode          : {m}",
        f"timestamp     : {_t.strftime('%Y-%m-%d %H:%M:%S')}",
        f"git commit    : {git_hash() or '(not a git repo)'}",
        f"random seed   : {seed_value() or 'unset'}",
        "configuration : " + str(_CFG)[:300],
        f"mc endpoint   : {mc_endpoint()}",
        f"llm           : {llm_summary()}",
        "shielded      : " + ("|".join(shield_summary()) or "(none)"),
    ]
    lines += [
        "─" * 72,
        f"Goal: obtain {goal_obtain()}  (structured graph goal, no LLM parsing)",
        "Exploration: directed (goal→means-end→target)",
        "Prior: version-verified via runtime minecraft-data + explicit provenance",
        "Causal learning: ENABLED (no-LLM action→delta→result loop)",
        "LLM (facts/causal): DISABLED" if m == MODE_LEARNING
        else "LLM policy: per experiment config",
        "═" * 72,
        "",
    ]
    for line in lines:
        logger.warning(line)
        print(line, flush=True)


# 允许的结构化标签（消费点见文件头注释与各模块 xlog 调用）
TAGS = ("GOAL", "TARGET", "PATH", "ACTION", "OBSERVATION", "DELTA",
        "LEARNING", "CONFIDENCE", "GRAPH", "GAP", "SEARCH", "RESULT")


def xlog(tag: str, msg: str, **fields) -> None:
    """结构化实验日志：控制台 [TAG] 行 + fas_log（如有）。绝不抛。"""
    try:
        suffix = ""
        if fields:
            suffix = "  " + " ".join(f"{k}={v}" for k, v in fields.items())
        logger.info(f"[{tag}] {msg}{suffix}")
        if enabled():
            print(f"[{tag}] {msg}{suffix}", flush=True)
        try:
            import fas_log
            fas_log.get_logger(fas_log.COGNITION).info(
                f"exp.{str(tag).lower()}", str(msg)[:300], **fields)
        except Exception:
            pass
    except Exception:
        pass