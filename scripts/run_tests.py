"""R2 顺手补的回归入口：一条命令跑全套件（此前"回归命令集"只能手敲逐个文件）。

用法：
    E:/Miniforge.envs/Fascinator/python.exe -X utf8 scripts/run_tests.py            # 只跑 tests/
    E:/Miniforge.envs/Fascinator/python.exe -X utf8 scripts/run_tests.py --gates    # 再加四个闸门
    E:/Miniforge.envs/Fascinator/python.exe -X utf8 scripts/run_tests.py -k modulat # 只跑名字含 modulat 的

闸门说明（诚实版）：`bench_diffusion --compare` 与 `graph_integrity_audit.py` 在 R2 之前就是红的
（基线 9/3 未重批；审计里 2 条 label×space + 42 条"产生"类别错配 + 22 个论文事件命名被判复合，
都需要用户裁定）。它们默认**不计入退出码**——`--gates` 只是把输出摆在一起看，
红字标 STALE。真要卡住回归，用 `--strict-gates`。

退出码：0 = 全绿；1 = 有测试失败；2 = 有测试没跑起来（导入炸等）。
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATES = [
    ("verify_p0_all", [os.path.join("scripts", "verify_p0_all.py")], False),
    ("bench_diffusion", [os.path.join("scripts", "bench_diffusion.py"),
                         "--compare", os.path.join("scripts", "bench_baseline.json")], True),
    ("graph_integrity_audit", ["graph_integrity_audit.py"], True),
]


def run(cmd: list[str]) -> tuple[int, float, str]:
    t0 = time.time()
    p = subprocess.run([PY, "-X", "utf8"] + cmd, cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, time.time() - t0, p.stdout + p.stderr


def main() -> int:
    ap = argparse.ArgumentParser(description="FAS 回归入口")
    ap.add_argument("-k", dest="pattern", default=None, help="只跑匹配此子串/通配的测试文件")
    ap.add_argument("--gates", action="store_true", help="跑完套件后再跑四个闸门（不影响退出码）")
    ap.add_argument("--strict-gates", action="store_true", help="闸门红也算失败（当前必然红，见文件头）")
    ap.add_argument("-q", "--quiet", action="store_true", help="只打失败项")
    args = ap.parse_args()

    pat = args.pattern or ""
    if "*" not in pat and "?" not in pat:
        pat = f"*{pat}*" if pat else "*"
    files = sorted(f for f in os.listdir(os.path.join(ROOT, "tests"))
                   if f.startswith("test_") and f.endswith(".py")
                   and fnmatch.fnmatch(f, pat))
    if not files:
        print(f"没有测试匹配 {args.pattern!r}")
        return 2

    print(f"运行 {len(files)} 个测试文件（{os.path.basename(PY)}）")
    failed: list[str] = []
    broken: list[str] = []
    total_t = 0.0
    for f in files:
        code, dt, out = run([os.path.join("tests", f)])
        total_t += dt
        n_fail = out.count("[FAIL]")
        if code == 0 and n_fail == 0:
            if not args.quiet:
                tail = [ln for ln in out.splitlines() if "通过" in ln or "PASS]" in ln][-1:]
                print(f"  ok   {f:52s} {dt:5.1f}s  {tail[0].strip() if tail else ''}")
        elif "Traceback" in out or "ModuleNotFoundError" in out or code >= 2:
            broken.append(f)
            print(f"  BROKEN {f} exit={code}")
            print("     " + "\n     ".join(out.strip().splitlines()[-6:]))
        else:
            failed.append(f)
            print(f"  FAIL {f} exit={code} ([FAIL]×{n_fail})")
            bad = [ln for ln in out.splitlines() if "[FAIL]" in ln]
            print("     " + "\n     ".join(bad[:8]))
    print(f"\n套件：{len(files) - len(failed) - len(broken)}/{len(files)} 通过，"
          f"失败 {len(failed)}，起不来 {len(broken)}，用时 {total_t:.0f}s")

    gate_bad: list[str] = []
    if args.gates or args.strict_gates:
        print("\n── 闸门 ──")
        for name, cmd, stale_ok in GATES:
            code, dt, out = run(cmd)
            mark = "ok " if code == 0 else "RED"
            note = "（R2 之前就红，成因见 docs/neuromodulation_R2_audit.md）" \
                if code and stale_ok else ""
            print(f"  {mark} {name:24s} exit={code} {dt:5.1f}s {note}")
            if code and not stale_ok:
                gate_bad.append(name)
            if code and args.strict_gates:
                gate_bad.append(name)
            if code and not args.quiet:
                print("     " + "\n     ".join(out.strip().splitlines()[-4:]))

    if broken:
        return 2
    if failed or (args.strict_gates and gate_bad):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
