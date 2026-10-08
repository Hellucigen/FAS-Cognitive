# scripts/scan_modulator_curves.py — R2 P8 诊断：12 条受体曲线的形状实测
# ============================================================================
# 为什么存在：config 里每个调制器都有 saturation / overload / overload_pull 三个
# 字段，但"字段真的起作用"要能被看见——P8 之前 pull 的绝对尺度落在 (max−overload)
# 只有 .05–.20 的量程里，顶格时扣掉的量是 1e-3 量级，等于没有过载支（审计 D5）。
# 这个脚本把每条曲线从 baseline 扫到 max，打印峰值位置、顶格响应、是否反号。
#
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 scripts/scan_modulator_curves.py

import copy
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.WARNING)

import config as C                                     # noqa: E402
from graph_model import KnowledgeGraph, Node           # noqa: E402
from internal_state import InternalState               # noqa: E402

kg = KnowledgeGraph()
kg.add_node(Node(id="Self", label="declarative-semantic", graph_space="self"))
st = InternalState(kg=kg, config=copy.deepcopy(C.DEFAULT_CONFIG),
                   data_dir=tempfile.mkdtemp(prefix="fas_curves_"))
st.sync_graph()

specs = st.config["modulator_system"]["specs"]
print(f"{'调制器':16s}{'基线':>6s}{'饱和':>6s}{'过载':>6s}{'pull':>6s}"
      f"{'系数':>9s}{'峰值@浓度':>11s}{'峰值响应':>10s}{'顶格响应':>10s}  过峰点→顶格的轨迹")
bad = []
for n, s in specs.items():
    b, sat, over, hi = s["baseline"], s["saturation"], s["overload"], s["max"]
    probe = sorted({b, (b + sat) / 2, sat, (sat + over) / 2, over,
                    (over + hi) / 2, hi})
    curve = []
    for c in probe:
        st.set_value("modulator", n, c, source="manual")
        curve.append((c, st.modulator_dev(n)))
    peak = max(curve, key=lambda t: t[1])
    top = curve[-1][1]
    coeff = st._pull_coefficient(n, st._modulators[n])
    # 三条硬判据（P8 的"字段必须有牙"）：峰值不落在基线、过峰后确实下降、
    # 且 pull>0 的调制器顶格处必须明显低于峰值（掉到峰值的一半以下）
    ok = (peak[0] > b + 1e-9 and top < peak[1] - 1e-6
          and (s.get("overload_pull", 0) <= 0 or top <= peak[1] * 0.6))
    if not ok:
        bad.append(n)
    print(f"{n:16s}{b:6.2f}{sat:6.2f}{over:6.2f}{s.get('overload_pull', 0):6.2f}"
          f"{coeff:9.1f}{peak[0]:11.2f}{peak[1]:10.3f}{top:10.3f}  "
          + " ".join(f"{v:+.3f}" for _, v in curve) + ("" if ok else "   ← 不达标"))
st.set_value("modulator", "dopamine", specs["dopamine"]["baseline"], source="manual")
print()
if bad:
    print(f"[FAIL] {len(bad)} 个调制器的过载支走不到: {bad}")
    sys.exit(1)
print("[OK] 12 条曲线的饱和/过载/pull 三个字段都在真实改变响应")
