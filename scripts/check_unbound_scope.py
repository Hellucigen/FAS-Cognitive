# -*- coding: utf-8 -*-
# 收尾 2026-09-22 v2：全仓 UnboundLocalError 模式扫描（完整绑定形态）
# 常驻检查器。来历：/api/nlp 生产 500——process_nlp 内两处局部 `import fas_log`
# 把 fas_log 变成整个函数的局部名，行首 fas_log.new_trace() 必然
# UnboundLocalError。py_compile 抓不到作用域地雷、测试从不 import app.py，
# 所以这类坑只有静态扫可行。**验收标准：import 型必须为 0**；
# assign 型大量是 comprehension/for 变量的假阳性（py3 独立作用域），
# 2026-09-22 基线：import 0 / assign 62（已逐一核查 app.py 热路径命中，
# 均为假阳性），新增 assign 命中需人工看执行序。
# 规则：函数自身作用域内某名字先被“读”、后才第一次“绑定”，且该名字
#   不是参数、不在任何更早的可执行路径上绑定。行号只是启发：绑定行 < 使用行
#   仅说明**按文本顺序**先绑后读；跨分支执行序需人工看。
# import 型绑定（局部 import）在更早行被读 → 该函数只要走到读点必炸（fas_log 案）。
import ast
import io
import os

ROOT = r"E:\Project\Fascinator"
SKIP_DIRS = {".git", ".zcode", "prototype", "node_modules", "__pycache__",
             "bot"}

def _target_names(t):
    out = []
    if isinstance(t, ast.Name):
        out.append(t.id)
    elif isinstance(t, (ast.Tuple, ast.List)):
        for e in t.elts:
            out += _target_names(e)
    elif isinstance(t, ast.Starred):
        out += _target_names(t.value)
    return out

def collect_scope(fn):
    """{name: {"binds": [(line,kind)], "uses": [line]}}，fn 自身作用域。"""
    info = {}
    def bind(lineno, kind, *names):
        for nm in names:
            d = info.setdefault(nm, {"binds": [], "uses": []})
            d["binds"].append((lineno, kind))
    def use(lineno, nm):
        d = info.setdefault(nm, {"binds": [], "uses": []})
        d["uses"].append(lineno)

    params = fn.args
    for a in (list(params.posonlyargs) + list(params.args) +
              list(params.kwonlyargs)):
        bind(fn.lineno, "param", a.arg)
    if params.vararg:
        bind(fn.lineno, "param", params.vararg.arg)
    if params.kwarg:
        bind(fn.lineno, "param", params.kwarg.arg)

    def walk(n):
        for child in ast.iter_child_nodes(n):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ClassDef)):
                # 嵌套作用域整体跳过（其内绑定/使用都不算本层）；
                # 但 def 名字本身绑定于本层，装饰器表达式在本层求值——简化：
                # 记 def 名为绑定，忽略其体与装饰器。
                if not isinstance(child, ast.Lambda):
                    bind(child.lineno, "defbind", child.name)
                continue
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for a in child.names:
                    bind(child.lineno, "import",
                         (a.asname or a.name).split(".")[0])
            elif isinstance(child, ast.Assign):
                for t in child.targets:
                    bind(child.lineno, "assign", *_target_names(t))
            elif isinstance(child, ast.AugAssign):
                for nm in _target_names(child.target):
                    use(child.lineno, nm)   # 先读
                    bind(child.lineno, "assign", nm)
            elif isinstance(child, ast.AnnAssign):
                if child.value is not None:
                    bind(child.lineno, "assign",
                         *_target_names(child.target))
            elif isinstance(child, ast.For):
                # iter 在本层读、target 在本层绑
                bind(child.lineno, "assign", *_target_names(child.target))
            elif isinstance(child, (ast.With, ast.AsyncWith)):
                for item in child.items:
                    if item.optional_vars is not None:
                        bind(item.optional_vars.lineno, "assign",
                             *_target_names(item.optional_vars))
            elif isinstance(child, ast.ExceptHandler):
                if child.name:
                    bind(child.lineno, "exceptbind", child.name)
            elif isinstance(child, ast.NamedExpr):   # walrus :=
                bind(child.lineno, "assign",
                     *_target_names(child.target))
            elif isinstance(child, (ast.ImportFrom,)):
                pass
            if isinstance(child, ast.Name):
                if isinstance(child.ctx, ast.Load):
                    use(child.lineno, child.id)
            walk(child)
    walk(fn)
    for st in ast.walk(fn):
        if isinstance(st, (ast.Global, ast.Nonlocal)):
            for nm in st.names:
                info.pop(nm, None)
    return info

def scan_file(path):
    try:
        tree = ast.parse(io.open(path, encoding="utf-8").read())
    except (SyntaxError, UnicodeDecodeError):
        return []
    fnd = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for nm, d in collect_scope(fn).items():
            if not d["binds"] or not d["uses"]:
                continue
            kinds = {b[1] for b in d["binds"]}
            if kinds <= {"param", "defbind"}:
                continue
            first_bind = min(b[0] for b in d["binds"])
            imp_binds = [b[0] for b in d["binds"] if b[1] == "import"]
            earlier = [u for u in d["uses"] if u < first_bind]
            if not earlier:
                continue
            # 若在使用行之后、赋值行之前不存在任何 import 绑定，
            # import 型仍要报（fas_log 案：绑定只有晚到的 import）
            fnd.append((os.path.relpath(path, ROOT), fn.name, fn.lineno, nm,
                        min(earlier), first_bind,
                        "import" if imp_binds and first_bind in imp_binds
                        else ("param+" if "param" in kinds else "assign")))
    return fnd

allf = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames
                   if d not in SKIP_DIRS and not d.endswith(".egg-info")]
    for f in filenames:
        if f.endswith(".py"):
            allf += scan_file(os.path.join(dirpath, f))

imp = sorted(x for x in allf if x[6] == "import")
oth = sorted(x for x in allf if x[6] != "import")
print(f"import 型（走到读点必炸）：{len(imp)}")
for x in imp:
    print(f"  {x[0]}::{x[1]} (def@{x[2]}) '{x[3]}' 用@{x[4]} < import@{x[5]}")
print(f"其他绑定（需按执行序人工判断）：{len(oth)}")
for x in oth:
    print(f"  {x[6]} {x[0]}::{x[1]} (def@{x[2]}) '{x[3]}' 用@{x[4]} < 绑@{x[5]}")
# 作为验收闸门时：import 型非 0 → 退出码 1
raise SystemExit(1 if imp else 0)
