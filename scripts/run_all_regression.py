#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# run_all_regression.py — 全仓回归执行器（A6，2026-09-30）
# ============================================================================
# 本项目真实回归通道 = 脚本式回归（tests/ 下 93 个文件"模块顶层直接执行
# 断言 + sys.exit 状态码"），pytest 只承载 12 个真 pytest 文件（conftest.py
# AST 分类器见 tests/conftest.py）。本执行器统一两条通道：
#
#   通道 1（真 pytest 文件）: python -m pytest tests/<f> -q   → exit 0 = PASS
#   通道 2（脚本式/装配式）: 通道 1 收集到 0 项（pytest exit 5）后，
#                            python tests/<f>                → exit 0 = PASS
#
# real_* 系列需运行中的 app（127.0.0.1:5000），默认排除，--with-real 强制。
#
# 用法:
#   python scripts/run_all_regression.py                # 全量（串行）
#   python scripts/run_all_regression.py --parallel 4   # 并行 4 路
#   python scripts/run_all_regression.py --only test_eye_salience.py test_fas_log.py
#   python scripts/run_all_regression.py --timeout 900  # 单文件超时秒（默认 600）
#   python scripts/run_all_regression.py --with-real
# 退出码: 0 = 全 PASS（或全 SKIP）；非 0 = 有 FAIL（或不可用）。
# ============================================================================

import argparse
import concurrent.futures
import os
import pathlib
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
PY = sys.executable or "python"

# 复用 conftest.py 的 AST 分类器(_is_script_style)作为单一真源:
# pytest 对命令行显式文件不套用 collect_ignore(实测),所以"哪些文件是
# 脚本式"必须由 runner 自行判定,不能赌 pytest 的 returncode 5。
sys.path.insert(0, TESTS)
from conftest import _is_script_style  # noqa: E402


def list_test_files():
    return sorted(
        f for f in os.listdir(TESTS)
        if f.startswith("test_") and f.endswith(".py")
    )


def run_channel2(fname, fpath, timeout):
    """"通道 2":python 直跑脚本式回归,退出码=结果。返回 (status, channel, detail)。"""
    p2 = subprocess.run(
        [PY, "-X", "utf8", fpath],
        cwd=ROOT, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    if p2.returncode == 0:
        return "PASS", "script", ""
    tail = "\n".join((p2.stdout or "").splitlines()[-6:] +
                     ["--stderr--"] +
                     (p2.stderr or "").splitlines()[-6:])
    return "FAIL", "script", tail.strip()


def _mk_result(fname, status, channel, detail, elapsed):
    return {"name": fname, "channel": channel, "status": status,
            "detail": detail, "elapsed": elapsed}


def run_one(fname, timeout, with_real):
    t0 = time.time()
    fpath = os.path.join(TESTS, fname)

    if fname.startswith("real_") and not with_real:
        return _mk_result(fname, "SKIP", "real",
                          "需运行中的 app(127.0.0.1:5000)，默认排除，--with-real 强制",
                          0.0)

    # 判定路径 1:conftest AST 分类器 → 脚本式/装配式直接通道 2
    if _is_script_style(pathlib.Path(fpath)):
        st, ch, det = run_channel2(fname, fpath, timeout)
        return _mk_result(fname, st, ch, det, time.time() - t0)

    # 判定路径 2:pytest 单文件(真 pytest 风格,exit 0 = PASS)。
    p1 = subprocess.run(
        [PY, "-m", "pytest", fpath, "-q", "--no-header"],
        cwd=ROOT, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    if p1.returncode == 0:
        return _mk_result(fname, "PASS", "pytest", "", time.time() - t0)
    if p1.returncode == 5:
        # pytest "no tests collected" → AST 分类盲区(main-guard 脚本式,
        # 例如 test_promote_trigger.py 全逻辑在 main() + __main__ 守卫)——
        # 回退通道 2 直跑,与 conftest 互补。
        st, ch, det = run_channel2(fname, fpath, timeout)
        return _mk_result(fname, st, ch, det, time.time() - t0)
    if p1.returncode in (1, 2, 3):
        tail = "\n".join((p1.stderr or "").splitlines()[-6:])
        return _mk_result(fname, "FAIL", "pytest",
                          f"pytest 退出码 {p1.returncode}: {tail}",
                          time.time() - t0)

    return _mk_result(fname, "FAIL", "pytest",
                      f"非预期退出码 {p1.returncode}", time.time() - t0)


def main():
    ap = argparse.ArgumentParser(description="Fascinator 全仓回归执行器")
    ap.add_argument("--parallel", type=int, default=1, help="并行路数(默认 1=串行)")
    ap.add_argument("--only", nargs="*", default=None, help="只跑指定文件(可多个)")
    ap.add_argument("--timeout", type=int, default=600, help="单文件超时秒")
    ap.add_argument("--with-real", action="store_true", help="包含 real_* 实机系列")
    args = ap.parse_args()

    files = (args.only if args.only else list_test_files())
    missing = [f for f in files if not os.path.exists(os.path.join(TESTS, f))]
    if missing:
        print(f"未知文件: {missing}")
        return 1
    print(f"运行 {len(files)} 个文件, 并行 {args.parallel} 路")

    t_start = time.time()
    results = []
    if args.parallel <= 1:
        for i, f in enumerate(files, 1):
            r = run_one(f, args.timeout, args.with_real)
            results.append(r)
            print(f"[{i}/{len(files)}] {r['status']:<4} {r['channel']:<6} {r['name']} "
                  f"({r['elapsed']:.1f}s)")
            if r["status"] == "FAIL" and r["detail"]:
                print("   " + r["detail"].replace("\n", "\n   "))
            sys.stdout.flush()
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as ex:
            futs = [ex.submit(run_one, f, args.timeout, args.with_real) for f in files]
            done = 0
            for fut in concurrent.futures.as_completed(futs):
                r = fut.result()
                results.append(r)
                done += 1
                print(f"[{done}/{len(files)}] {r['status']:<4} {r['channel']:<6} "
                      f"{r['name']} ({r['elapsed']:.1f}s)")
                if r["status"] == "FAIL" and r["detail"]:
                    print("   " + r["detail"].replace("\n", "\n   "))
                sys.stdout.flush()
    results.sort(key=lambda r: r["name"])

    ok = [r for r in results if r["status"] == "PASS"]
    fail = [r for r in results if r["status"] == "FAIL"]
    skip = [r for r in results if r["status"] == "SKIP"]
    wall = time.time() - t_start

    print("\n" + "=" * 70)
    print(f"PASS {len(ok)} / FAIL {len(fail)} / SKIP {len(skip)} "
          f"(墙钟 {wall:.1f}s)")
    if fail:
        print("失败清单:")
        for r in fail:
            print(f"  ✗ {r['name']} [{r['channel']}]")
        return 1
    if not ok and skip:
        print("全 SKIP（无可运行文件）")
        return 0
    print("全 PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())