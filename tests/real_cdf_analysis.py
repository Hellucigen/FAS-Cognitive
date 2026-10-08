# real_cdf_analysis.py — 从 G 样本 + fas_log 离线取证 C(主动话)/D(好奇饱食)/F(因果)/G(自主)
# 纯本地读取（logs/*.jsonl + tests/_g_samples.jsonl），零 HTTP 刺激。
import sys, os, json, re, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "tests", "_g_samples.jsonl")


def load_jsonl(p):
    rows = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def read_log(event, subsystem_glob="*.jsonl"):
    out = []
    for fn in os.listdir(os.path.join(ROOT, "logs")):
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(ROOT, "logs", fn), encoding="utf-8") as f:
            for line in f:
                if f'"{event}"' in line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    return out


print("=" * 64)
print("C 主动言语投递（双证：channel sent + CI delivery=delivered）")
print("=" * 64)
ds = read_log("delivery_settled")
for e in ds:
    print("  ", e["ts"], "→", e["msg"][:70])
ok_c = any(e["msg"].startswith("delivered") for e in ds)
print(f"[{'PASS' if ok_c else 'GAP '}] C: ≥1 条主动话真实投递（delivery_settled delivered）")

print("=" * 64)
print("D 好奇饱食（curiosity_satiated 前后 level + 物种二遇计数）")
print("=" * 64)
cs = read_log("curiosity_satiated")
seen = {}
for e in cs:
    d = e.get("data") or {}
    ent = d.get("entity")
    seen.setdefault(ent, []).append((e["ts"], d.get("level_before"),
                                     d.get("level_after")))
for ent, tr in seen.items():
    print(f"  {ent}: {len(tr)} 次 satiation; trace={tr[:3]}")
lv_ok = [ (t[1], t[2]) for tr in seen.values() for t in tr
          if t[1] is not None and t[2] is not None ]
ok_d = bool(lv_ok) and all(b is None or a is None or a <= b + 1e-6 for b, a in lv_ok)
print(f"[{'PASS' if ok_d else 'GAP '}] D: satiation 记录在场且 level 单调不升（×0.4 通道） n={len(lv_ok)}")
sp = collections.Counter()
for e in read_log("species_observed"):
    sp[(e.get("data") or {}).get("entity")] += 1
if sp:
    print("  二遇计数(species_observed):", dict(sp))
else:
    print("  二遇: 无 species_observed 事件（GAP：需窗口内再遇同名物种）")

print("=" * 64)
print("F 因果可查询（attributed→promoted；hypotheses 计数）")
print("=" * 64)
ca = read_log("causal_attributed")
cp = read_log("causal_promoted")
print(f"  causal_attributed={len(ca)}  causal_promoted={len(cp)}")
for e in cp[:6]:
    print("   PROMOTE:", e["ts"], e["msg"][:90])
samples = load_jsonl(SAMPLES)
last_causal = None
for s in reversed(samples):
    if isinstance(s.get("causal"), dict) and "err" not in s.get("causal", {}):
        last_causal = s["causal"]
        break
if last_causal:
    print("  /api/debug/causal 末帧:", json.dumps(
        {k: last_causal[k] for k in list(last_causal)[:8]}, ensure_ascii=False)[:600])
ok_f = len(ca) > 0
print(f"[{'PASS' if ok_f else 'FAIL'}] F: 真实动作→结果归因在场 attributed={len(ca)}"
      f"（promoted={len(cp)}：门槛未到=诚实缺口）")

print("=" * 64)
print("G 自主连跑（决策多样性/活动统计/LLM 开销）")
print("=" * 64)
if not samples:
    print("[FAIL] 无样本文件——real_g_watch 未跑")
    sys.exit(1)
span_s = len(samples) and None
acts = collections.Counter()
for s in samples:
    for r in (s.get("action") or {}).get("recent", []):
        acts[(r.get("action"), bool(r.get("success")))] += 1
dur = len(samples) * float(os.environ.get("G_EVERY", "20"))
print(f"  样本 {len(samples)} 帧 ≈ {dur/60:.1f}min；动作×成败 分布:")
for k, n in acts.most_common(12):
    print(f"    {k[0]:22s} ok={k[1]!s:5s} ×{n}")
first, last = samples[0], samples[-1]
acts_count = len(acts)
def ch(s):
    c = s.get("channels") or {}
    st = c.get("stats") or {}
    return {k: st.get(k) for k in ("sent", "throttled", "send_failed", "answered", "polled")}
print("  channels 首帧:", ch(first), "末帧:", ch(last))
llm_f = (first.get("llm") or {})
llm_l = (last.get("llm") or {})
print("  llm 首帧:", json.dumps(llm_f, ensure_ascii=False)[:180])
print("  llm 末帧:", json.dumps(llm_l, ensure_ascii=False)[:180])
ok_g = len(samples) >= 30 and len(acts) >= 3
print(f"[{'PASS' if ok_g else 'GAP '}] G: 长窗口自主活动多样（{len(acts)} 种动作×结果，{len(samples)} 帧）")
