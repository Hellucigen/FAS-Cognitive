# -*- coding: utf-8 -*-
"""最终审计用只读日志统计（一次性分析脚本，不修改任何实验代码）。

对 logs/ 下各模块 JSONL 做客观统计：
  - 每个模块的行总数、时间覆盖窗口、事件类型分布（按天）
  - llm: purpose/caller/模型分布 + 延迟百分位
  - action: 每类动作的 proposed/结果分布（按天）
输出为 stdout 文本，供审计报告直接引用。
"""
import glob
import json
import math
import os
import sys
from collections import Counter, defaultdict

LOGS = r"E:\Project\Fascinator\logs"
MODULES = ["action", "cognition", "llm", "memory", "minecraft",
           "perception", "graph", "fas_all", "error"]

FMT = "%Y-%m-%d %H:%M:%S.%f"


def parse_ts(s):
    if not s:
        return None
    s = str(s).strip()
    for f in (FMT, "%Y-%m-%d %H:%M:%S"):
        try:
            return __import__("datetime").datetime.strptime(s[:23], f)
        except ValueError:
            continue
    return None


def load(path):
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def day_of(row):
    dt = parse_ts(row.get("ts"))
    return dt.strftime("%m-%d") if dt else "?" + (row.get("ts") or "?")


def pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    idx = int(math.ceil(p / 100.0 * len(s))) - 1
    return s[max(0, min(len(s) - 1, idx))]


def main():
    print("# 最终审计 · 日志模块统计")
    for mod in MODULES:
        path = os.path.join(LOGS, f"{mod}.jsonl")
        if not os.path.exists(path):
            print(f"\n## {mod}: 无文件")
            continue
        rows = load(path)
        if not rows:
            print(f"\n## {mod}: 0 可解析行")
            continue
        dts = [parse_ts(r.get("ts")) for r in rows]
        dts = [d for d in dts if d]
        span = (dts[0], dts[-1]) if dts else (None, None)
        print(f"\n## {mod}: {len(rows)} 行 "
              f"({span[0].strftime('%m-%d %H:%M') if span[0] else '?'}"
              f" → {span[1].strftime('%m-%d %H:%M') if span[1] else '?'})")
        evc = Counter(r.get("event") or "-" for r in rows)
        print(f"事件类型: {len(evc)} 种 → {evc.most_common()[:18]}")

        # 按天的事件类型分布（top 事件各天计数）
        by_day = defaultdict(Counter)
        for r in rows:
            by_day[day_of(r)][r.get("event") or "-"] += 1
        print("按天总数:", {d: sum(c.values()) for d, c in sorted(by_day.items())})

        # 专项
        if mod == "llm":
            print("## llm 专项 ###")
            PC = Counter(r.get("data", {}).get("purpose") for r in rows)
            CM = Counter(r.get("data", {}).get("caller") for r in rows)
            MD = Counter(r.get("data", {}).get("model") for r in rows)
            lat = [r["data"]["latency_ms"] for r in rows
                   if isinstance(r.get("data", {}).get("latency_ms"), (int, float))]
            toks = [r["data"]["total_tokens"] for r in rows
                    if isinstance(r.get("data", {}).get("total_tokens"), (int, float))]
            print("purpose:", PC.most_common())
            print("caller:", CM.most_common())
            print("model:", MD.most_common())
            if lat:
                print(f"latency_ms n={len(lat)} p50={pct(lat, 50)} "
                      f"p90={pct(lat, 90)} p99={pct(lat, 99)} min={min(lat)} max={max(lat)}")
            if toks:
                print(f"total_tokens n={len(toks)} p50={pct(toks, 50)} "
                      f"p90={pct(toks, 90)} sum={sum(toks)}")
            # 按天 purpose
            dlp = defaultdict(Counter)
            for r in rows:
                dlp[day_of(r)][r.get("data", {}).get("purpose") or "-"] += 1
            print("按天 purpose:")
            for d, c in sorted(dlp.items()):
                print("  ", d, dict(c.most_common()))
        if mod == "action":
            print("## action 专项 ###")
            # 每种动作类型的 proposed 与结局分布
            per_type = defaultdict(Counter)
            for r in rows:
                ev = r.get("event") or "-"
                data = r.get("data") or {}
                key = data.get("action_type") or data.get("skill") or data.get("type") or ev
                per_type[key][ev] += 1
            for t, c in sorted(per_type.items()):
                print(f"  {t}: {dict(c)}")
        if mod == "minecraft":
            paths = Counter((r.get("data") or {}).get("path") for r in rows)
            ok = Counter((r.get("data") or {}).get("ok") for r in rows)
            print("命令 ok 分布:", dict(ok))
            print("命令路径 top30:", paths.most_common(30))
            # 按天
            dmp = defaultdict(Counter)
            for r in rows:
                dmp[day_of(r)][(r.get("data") or {}).get("path") or "-"] += 1
            print("按天 myocket命令:")
            for d, c in sorted(dmp.items()):
                print("  ", d, dict(c.most_common(12)))
        if mod == "perception":
            evset = Counter(r.get("event") or "-" for r in rows)
            print("事件:", evset.most_common())
            # 附近的生物/方块计数变化出现次数
            has_biome = sum(1 for r in rows if r.get("event") == "mc_perception_updated")
            print("mc_perception_updated 行数:", has_biome)
        if mod == "memory":
            evm = Counter(r.get("event") or "-" for r in rows)
            print("事件:", evm.most_common())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()