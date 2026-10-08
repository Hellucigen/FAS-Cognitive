# run_mc_observe.py — Minecraft 正式 run 观测器（§7-13；Recorder 绑定）
# 用法:
#   E:/Miniforge.envs/Fascinator/python.exe -X utf8 scripts/run_mc_observe.py \
#       --family minecraft --label "M1 知识发现" --minutes 25 --tag M1
# 观测面：mc/autonomy/goals/internal 采样（15s）+ 归档 cognition.jsonl 内本 run
# 时间窗的 exp.* 事件 + 图快照（起/终 diff）+ LLM 遥测汇总。
# 零认知干预：只读 API + 只读日志；不改任何行为。
import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.join(ROOT, "scripts"))
if os.getcwd() != ROOT:
    os.chdir(ROOT)

from experiment_recorder import RunRecorder  # noqa: E402

BASE = "http://127.0.0.1:5000"


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=6) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"_err": str(e)[:120]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="minecraft")
    ap.add_argument("--label", default="MC run")
    ap.add_argument("--minutes", type=float, default=25.0)
    ap.add_argument("--tag", default="RUN")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    rec = RunRecorder(family=args.family, mode="learning_closed_loop",
                      seed=args.seed, label=args.label,
                      experiment_id=args.tag)
    rec.start({"mc_endpoint": "http://127.0.0.1:5010 (bot bridge)",
               "base": BASE})
    graph_before = rec.snapshot("graph_start",
                                graph_dict=lambda: get("/api/graph?full=true")
                                or {})
    graph_start_data = graph_before.get("graph")
    t0 = time.time()
    dur = args.minutes * 60
    last_cog_cut = 0.0
    try:
        while time.time() - t0 < dur:
            ts = time.time() - t0
            au = get("/api/autonomy/state") or {}
            cands = au.get("candidates", [])
            for c in cands:
                rec.log("TARGET" if c.get("motivation") == "user_goal"
                        else "CANDIDATE",
                        f"{c.get('type')} target={c.get('target')} "
                        f"score={round(c.get('score') or 0, 3)}",
                        motivation=c.get("motivation"),
                        score=round(c.get("score") or 0, 3))
            cur = au.get("current_action")
            if isinstance(cur, dict):
                rec.log("ACTION", f"{cur.get('action_type')} "
                                  f"state={cur.get('state')}",
                        **{k: v for k, v in cur.items()
                           if k in ("state", "started_at")})
            ints = get("/api/internal/state") or {}
            mods = {k: str(v.get("level") if isinstance(v, dict) else v)[:40]
                    for k, v in list((ints.get("modulators") or {}).items())[:6]}
            rec.log("STATE", "认知状态采样", modulators=mods,
                    cycle=ints.get("cycle_seq"))
            mc = get("/api/mc/status") or {}
            st = mc.get("state") or {}
            rec.log("OBSERVATION", "mc 状态采样",
                    pos=str(st.get("position")), health=st.get("health"),
                    food=st.get("food"), held=str(st.get("heldItem")))
            # 每 60s 归档一次 cognition.jsonl 里的 exp.* 事件（增量复制语义）
            if ts - last_cog_cut >= 60:
                last_cog_cut = ts
                _copy_exp_events(rec, t0)
                g = get("/api/autonomy/goals") or {}
                rec.log("GOAL", "目标状态", goals=str(g.get("goals"))[:300])
                _sample_causal(rec)
            rec.counter("samples", 1)
            time.sleep(15)
    finally:
        _copy_exp_events(rec, t0)
        gfin = rec.snapshot("graph_end",
                            graph_dict=lambda: get("/api/graph?full=true")
                            or {})
        _gs = get("/api/graph?full=true") or {}
        try:
            rec.graph_delta(graph_start_data, _gs,
                            note="run 前后图谱规模变化")
        except Exception:
            pass
        rec.finalize(ok=True,
                     extra={"git": rec._build_metadata({}).get("git_commit")})
        print(f"[{args.tag}] run 完成 → {rec.dir}")


def _sample_causal(rec):
    """每 60s 采样一次 live 因果账本规模（aggregations/hypotheses/promoted）
    ——episodic→动作先验的实机累积曲线（M3 观测面）。只读 data 文件。"""
    try:
        import json as _json
        p = os.path.join("data", "experience_timeline.json")
        if not os.path.exists(p):
            return
        with open(p, encoding="utf-8") as f:
            doc = _json.load(f)
        c = doc.get("causal") or {}
        agg = c.get("aggregations") or {}
        hyp = c.get("hypotheses") or {}
        rec.log("CAUSAL", "因果账本采样",
                aggregations=len(agg), hypotheses=len(hyp),
                promoted=len(c.get("promoted") or {}),
                top_hyp=sorted(hyp.items(),
                               key=lambda kv: -(kv[1].get("support") or 0))[:6])
    except Exception:
        pass


def _copy_exp_events(rec, t0):
    """把本 run 时间窗内的 exp.* 事件从 logs/cognition.jsonl 抽到 events.jsonl
    （payload 语义同 recorder.log，避免超长循环日志进 run）。"""
    try:
        t_lo = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0))
        t_hi = time.strftime("%Y-%m-%d %H:%M:%S")
        p = os.path.join("logs", "cognition.jsonl")
        if not os.path.exists(p):
            return
        fn = os.path.join(rec.dir, "exp_events.jsonl")
        mode = "a" if os.path.exists(fn) else "w"
        n = 0
        with open(p, encoding="utf-8") as f, open(fn, mode, encoding="utf-8") as o:
            for line in f:
                if "exp." not in line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts = str(row.get("ts") or "")
                if not (t_lo <= ts[:19] <= t_hi) and mode == "a":
                    continue
                if "exp." in str(row.get("event") or ""):
                    o.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                    n += 1
        rec.counter("exp_events_copied", n)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())