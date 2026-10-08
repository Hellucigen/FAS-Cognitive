# observer.py — FAS Dynamic Environment Revalidation Experiment 观测器
# ============================================================================
# 只读观测：tail fas_all.jsonl / minecraft.jsonl / llm.jsonl，轮询 bridge /state
# 与 data/autonomy.json（goals 差异），把过滤后的系统事件 + 实验干预事件
# 追加到 experiment 目录。绝不修改生产文件，绝不注入任何行为。
# ============================================================================
import json, os, sys, time, urllib.request, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXP = os.path.join(ROOT, "experiment_dynamic_revalidation")
LOG = os.path.join(ROOT, "logs")
BRIDGE = "http://127.0.0.1:5010"

sys_events_path = os.path.join(EXP, "sys_events.jsonl")
marker_path = os.path.join(EXP, "events.jsonl")

# 只要这些子系统事件（高频噪声滤掉 cycle_*/diffusion 等）
IMPORTANT = {
    "action_proposed", "action_started", "action_settled", "causal_attributed",
    "causal_promoted", "replay_batch", "mc_perception_updated",
    "bridge_command", "session_state", "llm_call_finished", "dominant_shift",
    "response_sent", "input_received", "resources_routed", "screen_observed",
    "action_execution", "revalidation", "goal", "server_listening",
    "runtime_summary", "startup",
}
# 完整保留的子系统拆分文件
SPLIT_FILES = ("minecraft.jsonl", "llm.jsonl", "cognition.jsonl", "memory.jsonl")

def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

class Tailer:
    def __init__(self, path):
        self.path = path
        self.pos = os.path.getsize(path) if os.path.exists(path) else 0
    def lines(self):
        if not os.path.exists(self.path):
            return []
        size = os.path.getsize(self.path)
        if size < self.pos:      # 轮转/重写
            self.pos = 0
        out = []
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self.pos)
            for ln in f:
                ln = ln.strip()
                if ln:
                    out.append(ln)
            self.pos = f.tell()
        return out

def bridge_state():
    try:
        with urllib.request.urlopen(BRIDGE + "/state", timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"connected": False, "err": str(e)[:80]}

def main(stop_after_s=60 * 60):
    t0 = time.time()
    tails = {name: Tailer(os.path.join(LOG, name)) for name in IMPORTANT and ()}
    # 只 tail 需要的：minecraft.jsonl(桥命令+会话)、llm.jsonl(LLM调用)、memory.jsonl、cognition.jsonl
    tail_mc = Tailer(os.path.join(LOG, "minecraft.jsonl"))
    tail_llm = Tailer(os.path.join(LOG, "llm.jsonl"))
    tail_alg = Tailer(os.path.join(LOG, "fas_all.jsonl"))
    last_goals = None
    last_pos = None
    state_sink = open(os.path.join(EXP, "states.ndjson"), "a", encoding="utf-8")
    se_sink = open(sys_events_path, "a", encoding="utf-8")
    ev_sink = open(marker_path, "a", encoding="utf-8")
    try:
        while time.time() - t0 < stop_after_s:
            # ── fas_all + 拆分：过滤重要事件 ──
            for src, tl in (("fas_all", tail_alg), ("minecraft", tail_mc),
                            ("llm", tail_llm)):
                for ln in tl.lines():
                    try:
                        ev = json.loads(ln)
                    except Exception:
                        continue
                    name = str(ev.get("event") or "")
                    if src == "fas_all" and name not in IMPORTANT:
                        # autonomy/goal 记录走 runtime 输出但会进 fas_all 的
                        # cognition 拆分？保守：缓存所有 event 名，只滤已知高频
                        if name in ("cycle_start", "cycle_end", "edge_added",
                                    "edge_updated", "edge_removed", "node_added",
                                    "node_removed", "diffusion_ticks", "diffusion_summary",
                                    "demand_evaluated", "decision_started", "decision_finished",
                                    "context_built", "recipe_clues"):
                            continue
                    if src == "minecraft":
                        # 桥命令太吵——只保留失败/爆破关键路径
                        name = ev.get("event")
                        if name == "bridge_command" and ev.get("data", {}).get("ok") is True and ev.get("data", {}).get("latency_ms", 0) < 500:
                            continue
                    se_sink.write(json.dumps({"obs_ts": now_iso(), "src": src, **ev}, ensure_ascii=False) + "\n")
                    se_sink.flush()
            # ── autonomy goals 差异 ──
            try:
                a = json.load(open(os.path.join(ROOT, "data", "autonomy.json"), encoding="utf-8"))
                goals = a.get("goals") or []
                sig = [(g.get("type"), g.get("target"), g.get("status")) for g in goals]
                if sig != last_goals:
                    ev = {"obs_ts": now_iso(), "event": "autonomy_goals_snapshot", "goals": goals}
                    se_sink.write(json.dumps(ev, ensure_ascii=False) + "\n")
                    se_sink.flush()
                    last_goals = sig
            except Exception:
                pass
            # ── bridge state 快照（4s）──
            st = bridge_state()
            row = {"ts": now_iso(), "t": round(time.time() - t0, 1)}
            if st.get("connected"):
                row.update({"pos": st.get("position"), "health": st.get("health"),
                            "food": st.get("food"), "gm": st.get("gameMode"),
                            "tod": st.get("timeOfDay"), "day": st.get("day"),
                            "held": st.get("heldItem"), "near": [str(x) for x in (st.get("nearbyBlocks") or [])][:6],
                            "ents": [(e.get("name"), e.get("dist")) for e in (st.get("nearbyEntities") or [])][:5]})
            else:
                row.update({"err": st.get("err")})
            state_sink.write(json.dumps(row, ensure_ascii=False) + "\n")
            state_sink.flush()
            time.sleep(4)
    finally:
        state_sink.close(); se_sink.close(); ev_sink.close()

if __name__ == "__main__":
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 3600
    main(stop_after_s=dur)