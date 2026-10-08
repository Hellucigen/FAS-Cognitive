# test_action_sandbox.py — 动作代码沙箱与 execution 写入闸门（回归护栏）
# 起因：execute_action() 回退路径用 exec() 执行节点 execution 字符串，而内建白名单
# 里直接放的是裸 __import__ —— 等于给沙箱留了一扇门：
#     exec("__import__('os').getcwd()", {"__builtins__": {"__import__": __import__}})
# 实测能成功返回工作目录，即沙箱形同虚设。而 execution 又能经无鉴权的
# POST /api/nodes 写入，构成"任何能发请求的人都能注入并执行代码"。
#
# 修复分三层（本测试逐层锁住）：
#   1. 沙箱：__import__ 换成受控 _action_safe_import，只放行 ALLOWED_ACTION_IMPORTS
#   2. 写入面：HTTP 三处写入口过 _execution_write_denied（默认关，且只收本机请求）
#   3. 暴露面：app.py 默认只监听 127.0.0.1（FAS_HOST 可显式放开）
#
# 不锁死"白名单永不扩大"——新增动作需要新模块时按需加，并在此补一条断言。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_action_sandbox.py

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

from graph_model import KnowledgeGraph, Node
from diffusion_engine import (DiffusionEngine, ALLOWED_ACTION_IMPORTS,
                              _action_safe_import)
import config as cfgmod

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_repo_file(rel_path: str) -> str:
    with open(os.path.join(REPO_ROOT, rel_path), encoding="utf-8") as f:
        return f.read()


def make_engine():
    cfg = copy.deepcopy(cfgmod.DEFAULT_CONFIG)
    kg = KnowledgeGraph()
    kg.add_node(Node(id="沙箱探针_合法", label="procedural",
                     execution="import time\nresult['t'] = time.time()\nprint('ok')"))
    kg.add_node(Node(id="沙箱探针_逃逸", label="procedural",
                     execution="result['cwd'] = __import__('os').getcwd()"))
    kg.add_node(Node(id="沙箱探针_网络", label="procedural",
                     execution="result['s'] = __import__('socket').gethostname()"))
    kg.add_node(Node(id="沙箱探针_子进程", label="procedural",
                     execution="import subprocess\nresult['p'] = 1"))
    kg.add_node(Node(id="沙箱探针_空代码", label="procedural", execution=""))
    return kg, DiffusionEngine(kg, cfg)


# ══════════════════════════════════════════════════════════
# S1 白名单本身：该有的在、危险的没有
# ══════════════════════════════════════════════════════════

check("S1a 现有动作节点所需模块在白名单内（ctypes/time）",
      {"ctypes", "time"}.issubset(ALLOWED_ACTION_IMPORTS),
      str(sorted({"ctypes", "time"} - set(ALLOWED_ACTION_IMPORTS))))

_DANGEROUS = ["os", "subprocess", "socket", "shutil", "importlib", "pathlib",
              "sys", "builtins", "pickle"]
_leaked = [m for m in _DANGEROUS if m in ALLOWED_ACTION_IMPORTS]
check("S1b 危险模块不在白名单（逃逸面收窄）", not _leaked, str(_leaked))

check("S1c 白名单是 frozenset（运行时不可被改写）",
      isinstance(ALLOWED_ACTION_IMPORTS, frozenset))

# ══════════════════════════════════════════════════════════
# S2 受控 import 的行为
# ══════════════════════════════════════════════════════════

for _m in ("ctypes", "time", "json", "math"):
    try:
        _action_safe_import(_m)
        _ok, _err = True, ""
    except ImportError as e:
        _ok, _err = False, str(e)
    check(f"S2a 允许导入 {_m}", _ok, _err)

for _m in ("os", "subprocess", "socket", "shutil"):
    try:
        _action_safe_import(_m)
        _blocked, _err = False, ""
    except ImportError as e:
        _blocked, _err = True, str(e)
    check(f"S2b 拒绝导入 {_m}", _blocked, _err or "居然放行了")

try:
    _action_safe_import("os.path")
    _sub_blocked = False
except ImportError:
    _sub_blocked = True
check("S2c 子模块按顶层判定（os.path 被拒）", _sub_blocked)

# ══════════════════════════════════════════════════════════
# S3 端到端：走真实 execute_action 路径
# ══════════════════════════════════════════════════════════

kg, engine = make_engine()

_r_ok = engine.execute_action("沙箱探针_合法")
check("S3a 合法动作代码仍能执行（能力未被误伤）",
      _r_ok.get("success") is True, str(_r_ok))
check("S3b 合法动作拿到输出",
      "ok" in str(_r_ok.get("output", "")), str(_r_ok.get("output"))[:120])

for _nid in ("沙箱探针_逃逸", "沙箱探针_网络", "沙箱探针_子进程"):
    _r = engine.execute_action(_nid)
    check(f"S3c {_nid} 被沙箱拦下（success=False）",
          _r.get("success") is False, str(_r))
    check(f"S3d {_nid} 错误信息指向白名单",
          "白名单" in str(_r.get("error", "")), str(_r.get("error"))[:160])

_r_empty = engine.execute_action("沙箱探针_空代码")
check("S3e 空 execution 如实报错（不静默成功）",
      _r_empty.get("success") is False, str(_r_empty))

_r_missing = engine.execute_action("不存在的节点")
check("S3f 未知节点如实报错", _r_missing.get("success") is False, str(_r_missing))

_logged = [x.get("node_id") for x in engine.execution_log]
check("S3g 失败动作也进 execution_log（可追溯）",
      "沙箱探针_逃逸" in _logged, str(_logged))

# ══════════════════════════════════════════════════════════
# S4 源码接线（app.py 导入即启动整套认知系统，太重——
#    沿用项目既有做法：对源码做存在性断言，见 test_energy_conservation E3d）
# ══════════════════════════════════════════════════════════

_app_src = read_repo_file("app.py")

check("S4a 闸门默认关闭（allow_api_execution_write=False）",
      cfgmod.DEFAULT_CONFIG.get("allow_api_execution_write") is False,
      str(cfgmod.DEFAULT_CONFIG.get("allow_api_execution_write")))

check("S4b 三处 execution 写入口都过闸门",
      _app_src.count("_execution_write_denied(data)") >= 3,
      f"实际接线 {_app_src.count('_execution_write_denied(data)')} 处")

check("S4c 闸门拒绝时返回 403（不是静默忽略）",
      'return jsonify({"error": _deny}), 403' in _app_src)

check("S4d 默认监听回环地址（FAS_HOST 可放开）",
      'os.environ.get("FAS_HOST", "127.0.0.1")' in _app_src)

check("S4e app.run 用的是 _host 变量（没被改回 0.0.0.0）",
      "host=_host," in _app_src and 'host="0.0.0.0",' not in _app_src)

check("S4f 非回环监听时有安全告警日志",
      "[SECURITY] 正在监听非回环地址" in _app_src)

check("S4g 闸门对 null/空串放行（前端建点带 execution:null 不能误伤）",
      "if not (isinstance(_exe, str) and _exe.strip())" in _app_src)

# ══════════════════════════════════════════════════════════
# S5 沙箱的 import 入口只有受控那一个
# ══════════════════════════════════════════════════════════

_eng_src = read_repo_file("diffusion_engine.py")
check("S5a 沙箱内 __import__ 已换成受控实现",
      '"__import__": _action_safe_import' in _eng_src)
check("S5b 源码里不再有裸 __import__ 进沙箱白名单",
      '"__import__": __import__' not in _eng_src)

print()
if FAILURES:
    print(f"✗ {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("✓ 动作沙箱与 execution 写入闸门测试全过（逃逸被拦；合法动作未受害）")
