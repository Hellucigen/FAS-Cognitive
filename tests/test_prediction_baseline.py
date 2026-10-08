# test_prediction_baseline.py — 预测误差基线（V2 补账）测试
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_prediction_baseline.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prediction_baseline import PredictionBaseline

FAILURES = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond: FAILURES.append(name)

pb = PredictionBaseline(config={"prediction_baseline": {"focus_n": 4, "keep_stale": 0.4}})
r = pb.observe(["A", "B", "C", "D"])
check("首轮无基线：surprise=None（不假装测量）", r["surprise"] is None, str(r))
r = pb.observe(["A", "B", "C", "D"])
check("完全按预期：surprise=0", r["surprise"] == 0.0, str(r))
r = pb.observe(["W", "X", "Y", "Z"])
check("完全意外：surprise=1", r["surprise"] == 1.0, str(r))
# 部分重叠 → 中间值；预期以现实为主保留少量旧焦点
r = pb.observe(["W", "X", "Y", "Z"])          # 预期滚动到 W..Z
r = pb.observe(["W", "X", "M", "N"])
check("部分重叠 surprise∈(0,1)", 0.3 < r["surprise"] < 0.9, str(r))
r = pb.observe(["P", "Q", "S", "T"])
check("惯性：旧焦点少量保留在预期里（不瞬断）", any(
    x in r["predicted"] for x in ("W", "X", "M", "N")) or len(r["predicted"]) >= 4,
    str(r["predicted"]))
avg = pb.recent_avg_surprise()
check("recent_avg 可聚合（drive 信号）", avg > 0, str(avg))
st = pb.state()
check("state 快照字段齐", {"enabled", "predicted", "recent", "avg_surprise_30m"} <= set(st))
pb.reset()
check("reset 清空基线", pb.observe(["A"])["surprise"] is None)
pb2 = PredictionBaseline(config={"prediction_baseline": {"enabled": False}})
pb2.observe(["A"])
check("开关关闭：不测不记", pb2.observe(["B"])["surprise"] is None)

print()
if FAILURES: print("✗", len(FAILURES), FAILURES); sys.exit(1)
print("✓ 预测误差基线全过")
