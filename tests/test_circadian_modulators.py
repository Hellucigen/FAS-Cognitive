# tests/test_circadian_modulators.py — R2 P10 闸门：褪黑素样/组胺样的日内水位来自**图上时段事实**
# ============================================================================
# 计划表给这一期的验收原文：**时间重放测试（同一 bucket 序列 ⇒ 同一曲线）；
# 确认代码里无 `now().hour` 判据**。围绕它要钉住的形状有六条：
#
#   A 事实层    小时→"夜间度"只在 temporal_awareness 翻译一次；知识(circadian_load)
#               与"此刻在势"(circadian_strength)分开；缺事实就说缺事实（ready=False）
#   B 显著性    读法只认 strength：activation 拉满不算夜（语言激活不伪装昼夜）；
#               键不在位 = 0（不拿默认值冒充）；类型集合与读法表一一对应
#   C 重放      同一 bucket 序列 ⇒ 逐点**完全相等**的曲线；方向正确（夜抬褪黑、
#               昼抬清醒）；跑 7 个昼夜不钉量程；速率/子图两个开关都能关掉驱动；
#               没有时段节点就什么都不动（不静默建默认边）
#   D 快通道    跨段事件的幅度 = 图上夜间度之差（带符号 ⇒ 一条边管两个方向）；
#               差值为 0 的迁移自门控；`circadian_driven` 门卫只吃 circadian 语境
#   E 源码扫描  调制侧没有 `.hour` / 小时比较 / 第二张昼夜表（唯一翻译点在传感器）
#   F 时基      注入时基后衰减真的发生（附九 6② 那个"衰减静默失效"的形状），
#               且写入时间戳与注入源同域 —— 这是重放能确定的前提
#
# 全程自建小图 + 临时目录 + 合成时基，不碰真实图谱、不读系统时钟。
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_circadian_modulators.py
# ============================================================================
import copy
import glob
import logging
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

logging.disable(logging.WARNING)

import config as C                                          # noqa: E402
from graph_model import KnowledgeGraph, Node, Edge          # noqa: E402
from internal_state import InternalState                    # noqa: E402
from modulation_events import ModulatorEngine               # noqa: E402
import modulator_subgraph as MS                             # noqa: E402
import temporal_awareness as TA                             # noqa: E402

FAILURES = []
PASSED = [0]
TMP_KEEP = []
MEL, HIS = "melatonin", "histamine"
# CC 每拍调用这些函数的文件（调制侧）；temporal_awareness 是**传感器**，不在其列
SCAN_MOD = ("modulator_subgraph.py", "modulation_events.py", "internal_state.py",
            "cognitive_field.py", "cognitive_networks.py", "drive_field.py",
            "modulation.py", "continuous_cognition.py", "need_drivers.py")
BASE = datetime(2026, 9, 20, 9, 0)       # 上午起点：序列里含"零差值迁移"（上午→中午）


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
    else:
        FAILURES.append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" | {detail}" if detail and not cond else ""))


def build(seed_time=True, config=None):
    """真实装配：图 + InternalState + 调制子图（64 条边表原样落地）+ 事件引擎。

    `seed_time=False` 用来验"缺事实"这一支——那时昼夜边的端点不存在，
    bootstrap 必须**如实跳过**，而不是把边造出来或拿默认值驱动。
    """
    cfg = copy.deepcopy(config if config is not None else C.DEFAULT_CONFIG)
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", label="declarative-semantic", graph_space="self"))
    for nid in ("CENetwork", "DMNetwork", "CuriosityDrive", "SocialDrive",
                "LearningDrive", "ConsistencyDrive",
                "行为:探索", "行为:分享", "行为:沉默", "行为:共情", "行为:延续"):
        kg.add_node(Node(id=nid, label="declarative-semantic",
                         graph_space="cognitive", extra_attrs={"type": "node"}))
    d = tempfile.mkdtemp(prefix="fas_p10_")
    TMP_KEEP.append(d)
    st = InternalState(kg=kg, config=cfg, data_dir=d)
    st.sync_graph()                       # 调制器/需求镜像点入图
    if seed_time:
        TA.ensure_bucket_nodes(kg)
    boot = MS.ensure_modulator_subgraph(kg, st, cfg)
    eng = ModulatorEngine(kg=kg, st=st, config=cfg)
    return kg, st, cfg, eng, boot


class Replay:
    """合成时基 + **CC 每拍调用的那三个真函数**（顺序也照 `tick_once`：
    衰减 → 漂移 → 时钟写事实 → 跨段事件）。

    曲线只由 (bucket 序列, 每拍分钟数) 决定：没有墙钟、没有 LLM、没有对话。
    这就是"实验/回放模式驱动真实管线"（§26）——调的是真状态、真图、真写入口。
    """

    def __init__(self, kg, st, cfg, eng, start=BASE, minutes=1.0):
        self.kg, self.st, self.cfg, self.eng = kg, st, cfg, eng
        self.minutes = float(minutes)
        self.epoch = start.timestamp()
        st.set_clock(lambda: self.epoch)
        self.curve = []                    # [(bucket, melatonin, histamine)]
        self.transitions = []              # 所有跨段（含差值 0 的）
        self.emitted = []                  # 真的发了事件的跨段（差值>0）
        self.drifts = []                   # [apply_circadian_drift 的返回]

    def step(self):
        self.epoch += self.minutes * 60.0
        self.st.tick_decay()               # 不传 now：时基没接上这里就会静默不衰减（F1）
        drift = MS.apply_circadian_drift(self.kg, self.st, self.cfg,
                                         dt_min=self.minutes)
        clk = TA.update_clock_state(self.kg, None,
                                    now=datetime.fromtimestamp(self.epoch))
        tr = (clk or {}).get("transition") or {}
        ev = None
        if tr.get("from") and float(tr.get("strength_delta") or 0.0) > 0:
            ev = self.eng.emit(
                "time_bucket_changed", source="circadian",
                valence=float(tr["load_to"]) - float(tr["load_from"]),
                intensity=float(tr["strength_delta"]),
                novelty=float(tr["strength_delta"]),
                ref=f"{tr['from']}→{tr['to']}")
            self.emitted.append((tr["from"], tr["to"], ev["applied"]))
        if tr:
            self.transitions.append((tr["from"], tr["to"], tr["strength_delta"]))
        self.drifts.append(drift)
        self.curve.append((clk["bucket"],
                           self.st.modulator_conc(MEL),
                           self.st.modulator_conc(HIS)))
        return {"drift": drift, "clock": clk, "event": ev}

    def run(self, steps):
        for _ in range(int(steps)):
            self.step()
        return self

    def col(self, i):
        return [row[i] for row in self.curve]


# ── A 事实层：小时只在传感器里翻译成事实 ────────────────────

print("── A 事实层 ──")
kg, st, cfg, eng, boot = build()
check("A1 每个时段桶都有夜间度（缺一个桶就有一段时间没有昼夜事实）",
      set(TA.BUCKET_PHASE) <= set(TA.BUCKET_CIRCADIAN),
      str(sorted(set(TA.BUCKET_PHASE) - set(TA.BUCKET_CIRCADIAN))))
check("A2 夜间度都在 0~1",
      all(0.0 <= v <= 1.0 for v in TA.BUCKET_CIRCADIAN.values()))
f0 = TA.circadian_facts(kg)
check("A3 时钟没跑过 ⇒ ready=False（不拿默认值冒充昼夜）",
      f0["ready"] is False and f0["load"] is None, str(f0))
clk = TA.update_clock_state(kg, None, now=datetime(2026, 9, 21, 1, 30))
f1 = TA.circadian_facts(kg)
check("A4 时钟跑过 ⇒ 事实可读且与表一致",
      f1["ready"] is True and f1["bucket"] == "凌晨"
      and abs(float(f1["load"]) - TA.BUCKET_CIRCADIAN["凌晨"]) < 1e-9, str(f1))
check("A5 当前桶 strength=夜间度；其余桶=0（'此刻在势'只属于现在）",
      abs(f1["bucket_strength"].get("凌晨", -1) - 1.00) < 1e-9
      and all(abs(v) < 1e-9 for k, v in f1["bucket_strength"].items()
              if k != "凌晨"), str(f1["bucket_strength"]))
check("A6 昼夜相位互补：夜间=load、白天=1−load",
      abs(f1["phase_strength"].get("夜间", -1) - 1.00) < 1e-6
      and abs(f1["phase_strength"].get("白天", -1) - 0.0) < 1e-6,
      str(f1["phase_strength"]))
# 播种不得覆盖时钟写的"此刻"（幂等里最容易踩的一条）
before = kg.nodes["深夜"].extra_attrs[TA.CIRCADIAN_STRENGTH_KEY]
kg.nodes["深夜"].extra_attrs[TA.CIRCADIAN_STRENGTH_KEY] = 0.95
TA.ensure_bucket_nodes(kg)
check("A7 重复播种不覆盖时钟写的 strength（setdefault 那条纪律）",
      abs(kg.nodes["深夜"].extra_attrs[TA.CIRCADIAN_STRENGTH_KEY] - 0.95) < 1e-9,
      str(kg.nodes["深夜"].extra_attrs.get(TA.CIRCADIAN_STRENGTH_KEY)))
kg.nodes["深夜"].extra_attrs[TA.CIRCADIAN_STRENGTH_KEY] = before

# ── B 显著性读法 ───────────────────────────────────────────

print("── B 显著性读法 ──")
kg.add_node(Node(id="测试时段", label="declarative-semantic", graph_space="semantic",
                 extra_attrs={"type": "time_of_day"}))
check("B1 strength 键不在位 ⇒ 0（没有事实就没有驱动）",
      MS._circadian_salience(kg, st, "测试时段") == 0.0)
kg.nodes["测试时段"].extra_attrs["circadian_strength"] = 3.0
check("B2 越界值钳在 0~1", MS._circadian_salience(kg, st, "测试时段") == 1.0)
kg.nodes["测试时段"].extra_attrs["circadian_strength"] = 0.0
kg.nodes["测试时段"].activation = 5.0
check("B3 activation 拉满也不算夜（语言激活不伪装昼夜；也不借扩散的货币）",
      MS._circadian_salience(kg, st, "测试时段") == 0.0)
kg.nodes["测试时段"].extra_attrs["circadian_strength"] = 0.4
check("B4 与 activation 无关：只有 strength 说话",
      abs(MS._circadian_salience(kg, st, "测试时段") - 0.4) < 1e-9)
check("B5 状态源类型都有注册读法（类型在集合里没读法 = 配置错）",
      set(MS.CIRCADIAN_SOURCE_TYPES) <= set(MS.SALIENCE_READERS),
      str(sorted(set(MS.CIRCADIAN_SOURCE_TYPES) - set(MS.SALIENCE_READERS))))
_saved = dict(MS.SALIENCE_READERS)
try:
    del MS.SALIENCE_READERS["day_phase"]
    bad = MS.apply_circadian_drift(kg, st, cfg, dt_min=1.0)
finally:
    MS.SALIENCE_READERS.clear()
    MS.SALIENCE_READERS.update(_saved)
check("B6 真缺读法时如实报 skip（不是静默少写）",
      any("无显著性读法" in str(s.get("why")) for s in bad["skipped"]),
      str(bad["skipped"][:3]))

# ── C 时间重放：本期主闸门 ─────────────────────────────────

print("── C 时间重放 ──")
kg1, st1, cfg1, eng1, _ = build()
kg2, st2, cfg2, eng2, _ = build()
r1 = Replay(kg1, st1, cfg1, eng1).run(12 * 60)       # 09:00 → 21:00，一整段天黑过程
kg3, st3, cfg3, eng3, _ = build()
r2 = Replay(kg3, st3, cfg3, eng3).run(12 * 60)
c1m, c1h = r1.col(1), r1.col(2)
c2m, c2h = r2.col(1), r2.col(2)
check("C1 同一 bucket 序列 ⇒ 褪黑素样曲线逐点**完全相等**（不是近似）",
      c1m == c2m, f"{c1m[100]:.6f} vs {c2m[100]:.6f}")
check("C2 同一 bucket 序列 ⇒ 组胺样曲线逐点完全相等", c1h == c2h)
check("C3 迁移序列也确定（同一 bucket 序列 ⇒ 同一跨段事件流）",
      r1.transitions == r2.transitions, str(r1.transitions[:3]))
check("C4 零差值迁移不发事件（上午→中午 夜间度没变 ⇒ 不'跨段抖一下'）",
      ("上午", "中午") in [p[:2] for p in r1.transitions]
      and ("上午", "中午") not in [p[:2] for p in r1.emitted]
      and len(r1.emitted) == sum(1 for t in r1.transitions if t[2] > 0),
      f"transitions={r1.transitions} emitted={[(f,t) for f,t,_ in r1.emitted]}")

# 7 个昼夜（168 小时的小时级快拍）：方向、量程、不钉死
kgd, std, cfgd, engd, _ = build()
rd = Replay(kgd, std, cfgd, engd, start=datetime(2026, 9, 20, 20, 0),
            minutes=5.0).run(7 * 24 * 12)
spec_mel = std._specs_mod.get(MEL) or {}
spec_his = std._specs_mod.get(HIS) or {}
mmax, hmax = float(spec_mel.get("max", 1.0)), float(spec_his.get("max", 1.0))
mel_c, his_c = rd.col(1), rd.col(2)
night_mel = max(mel_c[len(mel_c) // 2:])
day_his = max(his_c[len(his_c) // 2:])
night_his = min(his_c[len(his_c) // 2:])
day_mel = min(mel_c[len(mel_c) // 2:])
print(f"     实测（7 个昼夜稳态段）褪黑素样 夜 {night_mel:.4f} / 昼 {day_mel:.4f}"
      f"（saturation {spec_mel.get('saturation')}, overload {spec_mel.get('overload')}）")
print(f"     实测（7 个昼夜稳态段）组胺样   昼 {day_his:.4f} / 夜 {night_his:.4f}"
      f"（saturation {spec_his.get('saturation')}, overload {spec_his.get('overload')}）")
check("C5 夜里褪黑素样高于白天（方向：夜间度↑→睡意↑）",
      night_mel - day_mel >= 0.30, f"{night_mel:.4f} vs {day_mel:.4f}")
check("C6 白天组胺样高于夜里（方向：亮→清醒↑；与褪黑素同向的两端）",
      day_his - night_his >= 0.30, f"{day_his:.4f} vs {night_his:.4f}")
check("C7 长跑不钉量程：两端都留在 (min, max) 内（钉满=没有信息，09-13 事故形状）",
      0.0 < day_mel and night_mel < mmax - 1e-9 and 0.0 < night_his and day_his < hmax - 1e-9,
      f"mel {day_mel:.4f}~{night_mel:.4f}/{mmax} his {night_his:.4f}~{day_his:.4f}/{hmax}")
check("C8 不长期停在过载反转区（越过 overload 就该往回拉，不是继续推高）",
      night_mel < float(spec_mel.get("overload", 1.0)) + 1e-9,
      f"{night_mel:.4f} vs overload {spec_mel.get('overload')}")

# 回滚开关：速率归零 / 子图关闭 ⇒ 日内水位不动（§24：只改 config 就能撤）
def cfg_with(**blk_over):
    base = copy.deepcopy(C.DEFAULT_CONFIG)
    base["modulator_system"].update(blk_over)
    return base


kgx, stx, cfgx, engx, _ = build(config=cfg_with(circadian_drift_rate_per_min=0))
kgw, stw, cfgw, engw, _ = build()
_START, _MIN = datetime(2026, 9, 21, 18, 0), 5.0
rx = Replay(kgx, stx, cfgx, engx, start=_START, minutes=_MIN).run(24 * 12)
rw = Replay(kgw, stw, cfgw, engw, start=_START, minutes=_MIN).run(24 * 12)
check("C9 circadian_drift_rate_per_min=0 ⇒ 慢通道一步不写（回滚只改 config）",
      all(d["written"] == 0 and d["rate_per_min"] == 0.0 for d in rx.drifts)
      and any(d["written"] > 0 for d in rw.drifts),
      f"关={rx.drifts[0]} 开={rw.drifts[0]}")
check("C9b 同一 bucket 序列下关掉慢通道会改变曲线（这条通道本来真的在驱动水位）",
      rx.col(1) != rw.col(1), f"关 {rx.col(1)[-1]:.4f} vs 开 {rw.col(1)[-1]:.4f}")
kgn, stn, cfgn, engn, _ = build(config=cfg_with(subgraph=False))
TA.update_clock_state(kgn, None, now=datetime(2026, 9, 21, 2, 0))
rn = MS.apply_circadian_drift(kgn, stn, cfgn, dt_min=5.0)
check("C10 子图开关 subgraph=false ⇒ 漂移整体停用（回滚不留平行通道）",
      rn["written"] == 0 and abs(stn.modulator_conc(MEL)
                                 - float(stn._specs_mod[MEL]["baseline"])) < 1e-9,
      str(rn))
kgz, stz, cfgz, engz, bootz = build(seed_time=False)
rz = MS.apply_circadian_drift(kgz, stz, cfgz, dt_min=5.0)
check("C11 没有时段节点 ⇒ 边表如实跳过 + 漂移一步不写（不静默建默认边）",
      bootz["edges_skipped"] >= 6 and rz["written"] == 0,
      f"skipped={bootz['edges_skipped']} written={rz['written']}")

# ── D 快通道：跨时段事件 ───────────────────────────────────

print("── D 快通道与 circadian_driven 门卫 ──")
kgd2, std2, cfgd2, engd2, _ = build()
TA.update_clock_state(kgd2, None, now=datetime(2026, 9, 20, 18, 0))    # 傍晚 0.30
m0, h0 = std2.modulator_conc(MEL), std2.modulator_conc(HIS)
clk = TA.update_clock_state(kgd2, None, now=datetime(2026, 9, 20, 19, 30))  # →晚上 0.60
tr = clk["transition"]
check("D1 迁移幅度 = 图上夜间度之差（不是拍脑袋常数）",
      abs(tr["load_to"] - tr["load_from"] - 0.30) < 1e-9
      and abs(tr["strength_delta"] - 0.30) < 1e-9, str(tr))
ev = engd2.emit("time_bucket_changed", source="circadian",
                valence=tr["load_to"] - tr["load_from"],
                intensity=tr["strength_delta"], novelty=tr["strength_delta"],
                ref="傍晚→晚上")
mods_applied = {a["mod"] for a in ev["applied"]}
check("D2 天黑（正 valence）：褪黑素被推、组胺被拉（同一条边管两个方向）",
      {MEL, HIS} <= mods_applied, str(ev["applied"]))
d_mel = {a["mod"]: a for a in ev["applied"]}[MEL]
d_his = {a["mod"]: a for a in ev["applied"]}[HIS]
check("D3 写入方向：褪黑素↑、组胺↓（w=−0.35 × 正 valence）",
      float(d_mel["requested"]) > 0 > float(d_his["requested"]),
      f"{d_mel.get('requested')} / {d_his.get('requested')}")
check("D3b 真正落到数值：褪黑素 tonic 抬、组胺 tonic 落（不是只记了一笔请求）",
      std2.modulator_conc(MEL) > m0 and std2.modulator_conc(HIS) < h0,
      f"mel {m0:.4f}→{std2.modulator_conc(MEL):.4f} "
      f"his {h0:.4f}→{std2.modulator_conc(HIS):.4f}")
kgd3, std3, cfgd3, engd3, _ = build()
TA.update_clock_state(kgd3, None, now=datetime(2026, 9, 20, 9, 0))     # 上午 0.00
clk3 = TA.update_clock_state(kgd3, None, now=datetime(2026, 9, 20, 12, 0))  # →中午 0.00
tr3 = clk3["transition"]
ev3 = engd3.emit("time_bucket_changed", source="circadian",
                 valence=tr3["load_to"] - tr3["load_from"],
                 intensity=tr3["strength_delta"], novelty=tr3["strength_delta"],
                 ref="上午→中午")
gated_mods = {g["mod"] for g in ev3["gated"]}
check("D4 零差值迁移自门控（上午→中午 不算'跨段抖一下'）",
      tr3["strength_delta"] == 0.0 and ev3["applied"] == []
      and {MEL, HIS} <= gated_mods, str(ev3["gated"]))

# circadian_driven 门卫：图是规则的真源，所以直接加一条事件边来造"否则会命中"的情形
kgg, stg, cfgg, _, bootg = build()
kgg.add_node(Node(id="事件类型:reward", label="declarative-semantic",
                  graph_space="cognitive",
                  extra_attrs={"type": "modulation_event", "signal": "valence"}))
for dst in ("褪黑素样", "组胺样"):
    kgg.add_edge(Edge(src="事件类型:reward", dst=dst, relation="影响", weight=0.5,
                      relation_category="causal_relation"))
engg = ModulatorEngine(kg=kgg, st=stg, config=cfgg)
m_before = stg.modulator_conc(MEL)
h_before = stg.modulator_conc(HIS)
rg = engg.emit("reward", source="reward", valence=0.8, intensity=0.8)
check("D5 circadian_driven 有消费者：奖赏事件推不动褪黑素样（吃了甜点≠褪黑素上升）",
      MEL not in {a["mod"] for a in rg["applied"]}
      and abs(stg.modulator_conc(MEL) - m_before) < 1e-9, str(rg["applied"]))
check("D6 跳过要记一笔、看得见（不是静默丢）",
      any(g["mod"] == MEL and "circadian_driven" in str(g.get("gate"))
          for g in rg["gated"]), str(rg["gated"]))
check("D7 门卫不是关掉事件系统：未打标记的组胺样照常被同一事件推动",
      HIS in {a["mod"] for a in rg["applied"]}
      and abs(stg.modulator_conc(HIS) - h_before) > 1e-9, str(rg["applied"]))
check("D8 出厂只有褪黑素样打这个标记",
      [n for n, s in stg._specs_mod.items()
       if s.get("circadian_driven")] == [MEL],
      str([n for n, s in stg._specs_mod.items() if s.get("circadian_driven")]))
# CC 接线（不是只在测试里手工发射）
cc_src = open(os.path.join(ROOT, "continuous_cognition.py"), encoding="utf-8").read()
_cc_flat = re.sub(r"\s+", "", cc_src)      # 换行/缩进不算差异：查的是**语义**接线
check("D9 CC 每拍确实接了慢通道（drift 与需求漂移同处）",
      "apply_circadian_drift(" in cc_src and "apply_need_drift(" in cc_src)
check("D10 CC 的跨段事件带符号且以 strength_delta>0 为闸",
      'emit("time_bucket_changed"' in cc_src
      and 'float(_tr.get("strength_delta")or0.0)>0' in _cc_flat
      and 'valence=float(_tr["load_to"])-float(_tr["load_from"])' in _cc_flat,
      "接线字符串不符（改了 CC 的发射方式就得同步这条闸门）")

# ── E 源码扫描：调制侧没有小时判据 ──────────────────────────

print("── E 源码扫描 ──")
HOUR_PAT = re.compile(r"\.hour\b|hour\s*[<>]=?|[<>]=?\s*hour\b|BUCKET_CIRCADIAN|hour_bucket")
hits = []
for fn in SCAN_MOD:
    p = os.path.join(ROOT, fn)
    if not os.path.exists(p):
        continue
    for ln, line in enumerate(open(p, encoding="utf-8").read().splitlines(), 1):
        code = line.split("#", 1)[0]        # 注释里提"小时"是在解释，不是判据
        if HOUR_PAT.search(code):
            hits.append(f"{fn}:{ln}:{code.strip()[:60]}")
check("E1 调制侧无 now().hour / 小时比较 / 第二张昼夜表（唯一翻译点在 temporal_awareness）",
      not hits, "; ".join(hits[:4]))


def code_only(path):
    """去掉整行注释后的源码（注释里提字段名是在解释，不是在使用）。"""
    return "\n".join(l.split("#", 1)[0]
                     for l in open(path, encoding="utf-8").read().splitlines())


WRITE_PAT = re.compile(r'\[\s*(?:CIRCADIAN_STRENGTH_KEY|["\']circadian_strength["\'])'
                       r'\s*\]\s*=')
writers = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "*.py"))
                 if WRITE_PAT.search(code_only(p)))
check("E2 circadian_strength 只有一个作者=时钟（语言激活/调制层都不写它）",
      writers == ["temporal_awareness.py"], str(writers))

# ── F 时基：重放能确定的前提（附九 6② 的坑）────────────────

print("── F 时基 ──")
kgf, stf, cfgf, engf, _ = build()
t_syn = 1_700_000_000.0          # **远在墙钟之前**——旧写法在这里会 dt<=0 静默跳过
stf.set_clock(lambda: t_syn)     # 先注入时基，再写值 ⇒ 时间戳与 Δt 同域
stf.set_value("modulator", MEL, 0.9, source="manual", reason="闸门：造一个高水位")
before = stf.modulator_conc(MEL)
t_syn += 60 * 60.0               # 合成时基前进一小时（真实墙钟一秒没动）
moved = stf.tick_decay()         # 不传 now：只能靠注入源算 Δt
after = stf.modulator_conc(MEL)
check("F1 注入时基后衰减真的发生（合成 now 落在墙钟之前也不能失效；附九 6②）",
      moved > 0 and after < before - 0.02, f"{before} → {after} moved={moved}")
item = stf._modulators[MEL]
check("F2 写入时间戳与注入时基同域（否则 last_update 被墙钟盖掉 ⇒ Δt 错乱）",
      abs(float(item.get("last_update") or 0.0) - t_syn) < 1e-6,
      str(item.get("last_update")))
stf.set_clock(None)
check("F3 不注入时 _now() 就是墙钟（生产逐字不变）",
      abs(stf._now() - __import__("time").time()) < 5.0)
_t_back = t_syn - 3600.0            # 时基倒挂（换回更早的合成时刻）
stf.set_clock(lambda: _t_back)
moved_b = stf.tick_decay()
check("F1b 时基倒挂时宁可不动，也不误衰减（换时钟不会把水位砸回基线）",
      moved_b == 0 and abs(stf.modulator_conc(MEL) - after) < 1e-9,
      f"moved={moved_b} {after} → {stf.modulator_conc(MEL)}")
stf.set_clock(None)

# ── G 观测面：事实是能被问出来的（P12 调试台复用）───────────

print("── G 观测面 ──")
TA.update_clock_state(kg1, None, now=datetime(2026, 9, 21, 2, 0))
fv = TA.circadian_facts(kg1)
check("G1 circadian_facts 给出调制器这一拍会看到什么（全部从节点属性读）",
      fv["ready"] and fv["bucket"] == "凌晨"
      and "夜间" in fv["phase_strength"] and abs(fv["phase_strength"]["夜间"] - 1.0) < 1e-9,
      str(fv))
check("G2 边表行数含 6 条昼夜边（数据在 config，不在代码里）",
      sum(1 for row in cfg["modulator_system"]["edges"]
          if str(row[0]) in ("夜间", "白天", "深夜", "凌晨")) == 6)

print()
print(f"通过 {PASSED[0]} 条" + (f"，失败 {len(FAILURES)} 条：{FAILURES}" if FAILURES
                                else "，全部通过"))
for d in TMP_KEEP:
    shutil.rmtree(d, ignore_errors=True)
sys.exit(1 if FAILURES else 0)
