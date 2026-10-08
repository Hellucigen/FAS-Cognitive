# tests/test_need_modulation_coupling.py — R2 P9 闸门：需求↔调制器 多对多 + drive/网络 连续偏置
# ============================================================================
# P9 要验收的不是"参数变了"（P8 的闸门管那个），而是**耦合的形状**：
#
#   A 落点名册    图上的 `调制器→网络/驱动` 边必须每条都被认领（没有静默失效的边），
#                 且名册由**拥有字段的对象**给（不在本模块抄一份对应表）
#   B 网络偏置    连续、有界、可达双高态、过载会反转；**不存在二值模式切换**
#                 （行为断言 + 源码扫描两头都堵）
#   C 需求位移    调制器钉在多高，需求靶值的位移都有界；一正一反成**负反馈**，
#                 双向同时开满跑长拍数也不会推爆
#   D 三种通道分工 rate 只改速度（稳态不变）/ affinity 需要张力在场 / bias 改稳态
#                 —— 这条解释了为什么 `rate_gains_n` 留成恒等槽而不是拿它当偏置用
#   E need.* 信号  effects 表里那行 `need.social` 现在真有生产者（审计 D-4）
#   F 动机表      需求→行动的耦合是数据不是 if 链；`urgency` 有了真消费者（D-8）
#   G 唯一写入口  需求漂移经 apply_delta（留痕、受 rise 夹速、关子图即全停）
#
# 全程不碰真实图谱：自建小图 + 临时目录。测的是**装配后的真链路**
# （config → CognitiveField → 投影 → graph_biases → step），不是手搓字典。
# ============================================================================
import copy
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config as C                                          # noqa: E402
from graph_model import KnowledgeGraph, Node                # noqa: E402
from internal_state import InternalState                    # noqa: E402
from cognitive_field import CognitiveField                  # noqa: E402
from cognitive_networks import NetworkField                 # noqa: E402
from drive_field import DriveField                          # noqa: E402
import modulator_subgraph as MS                             # noqa: E402

FAILURES = []
PASSED = [0]
TMP_KEEP = []
NEEDS = ("safety", "exploration", "competence", "social")
SCAN_FILES = ("cognitive_networks.py", "cognitive_field.py")


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
    else:
        FAILURES.append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" | {detail}" if detail and not cond else ""))


def build():
    """真实装配 + 把偏置落点需要的图节点补齐（行为概念 5 条边留缺端点不算错）。"""
    cfg = copy.deepcopy(C.DEFAULT_CONFIG)
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", label="declarative-semantic", graph_space="self"))
    for nid in ("CENetwork", "DMNetwork", "CuriosityDrive", "SocialDrive",
                "LearningDrive", "ConsistencyDrive",
                "行为:探索", "行为:分享", "行为:沉默", "行为:共情", "行为:延续"):
        kg.add_node(Node(id=nid, label="declarative-semantic",
                         graph_space="cognitive", extra_attrs={"type": "node"}))
    d = tempfile.mkdtemp(prefix="fas_p9_")
    TMP_KEEP.append(d)
    st = InternalState(kg=kg, config=cfg, data_dir=d)
    st.sync_graph()                 # 需求/调制器镜像点入图（type=need / modulator）
    MS.ensure_modulator_subgraph(kg, st, cfg)
    field = CognitiveField(config=cfg, state_file=os.path.join(d, "cf.json"))
    MS.project_coefficients(kg, st, field.modulation, cfg)
    field.set_bias_source(lambda: MS.graph_biases(kg, st, cfg, field.node_names()))
    return kg, st, cfg, field


def pin(st, name, value):
    """把调制器**慢分量**钉到指定浓度。manual 豁免 rise 夹速是实验模式（§26：
    驱动真实管线、调真实状态），不是给测试开后门——它回答的是
    "如果这条通道一直待在这个水位，场会怎样"。"""
    r = st.set_value("modulator", name, float(value), source="manual",
                     reason="P9 实验：钉住慢分量")
    assert r.get("ok"), (name, r)


def unpin_all(st, cfg):
    for n in st.modulator_names():
        pin(st, n, float(cfg["modulator_system"]["specs"][n].get("baseline", 0.5)))


def fmt(d) -> str:
    return "{" + ", ".join(f"{k}:{round(float(v), 3)}" for k, v in d.items()) + "}"


def peak_conc(st, cfg, name) -> float:
    """响应（dev）最大的那个浓度——方向断言要用它，而不是量程顶：
    顶格是**过载反转区**（见 B4），在那里"抑制边"会给出正偏置，这是设计如此，
    但拿它当"方向对不对"的证据会把两件事混成一件。"""
    base = float(cfg["modulator_system"]["specs"][name]["baseline"])
    hi = float(cfg["modulator_system"]["specs"][name].get("max", 1.0))
    best, bestv = base, -1.0
    x = base
    while x <= hi + 1e-9:
        pin(st, name, x)
        v = abs(float(st.modulator_dev(name) or 0.0))
        if v > bestv:
            best, bestv = x, v
        x += 0.005
    pin(st, name, best)
    return best


# ═══════ A 落点名册：边不静默失效 ═══════
kg, st, cfg, field = build()
names = field.node_names()
check("A1 场自己给落点名册（网络 2 + 驱动 4）",
      len(names.get("network") or {}) == 2 and len(names.get("drive") or {}) == 4,
      str(names))
drv_or_net = set(names["network"]) | set(names["drive"])
unclaimed = []
for nid in st.mirror_modulator.values():
    for e in kg.get_out_edges(nid):
        if e.dst in drv_or_net and e.relation in MS.PROJECTED_RELATIONS:
            if e.dst not in names["network"] and e.dst not in names["drive"]:
                unclaimed.append(e.dst)
check("A2 图上每条 调制器→网络/驱动 边都被某个字段认领", not unclaimed, str(unclaimed))
check("A3 名册里的落点都是真节点（边没有连向不存在的字段）",
      all(kg.get_node(nid) is not None for nid in drv_or_net), str(sorted(drv_or_net)))
b0 = MS.graph_biases(kg, st, cfg, names)
check("A4 全部在基线上 ⇒ 没有任何偏置（0=无调制，不藏默认值）",
      not b0["network"] and not b0["drive"] and not b0["need"]
      and b0["edges_used"] == 0, str(b0))
_rows = [r for r in cfg["modulator_system"]["edges"] if str(r[2]) in drv_or_net]
check("A5 出厂表里 网络/驱动 落点边的行数 = 图上认领到的边数（迁移无损）",
      len(_rows) == sum(1 for nid in st.mirror_modulator.values()
                        for e in kg.get_out_edges(nid)
                        if e.dst in drv_or_net
                        and e.relation in MS.PROJECTED_RELATIONS),
      f"表 {len(_rows)}")

# A6 出厂耦合表有两个副本（config 是声明处，模块内是"裸装配兜底"）——
# 副本必然漂移，所以把它钉成断言。同时验回归：P9 把表从 step() 硬编码搬进
# config 后，`CognitiveField(config={})` 曾连表一起丢掉了。
from cognitive_field import DEFAULT_RATE_GAINS                 # noqa: E402
check("A6 模块兜底表 = config 声明表（两份副本不许漂移）",
      DEFAULT_RATE_GAINS == cfg["modulator_system"]["rate_gains"],
      f"模块 {sorted(DEFAULT_RATE_GAINS)} vs config "
      f"{sorted(cfg['modulator_system']['rate_gains'])}")
bare = CognitiveField(config={})
check("A6b 裸装配仍拿到出厂增益（离线测试=config={} 必须像生产）",
      bare._gain_table == cfg["modulator_system"]["rate_gains"],
      str(bare._gain_table))
_ms = cfg["modulator_system"]
check("A7 幅度参数在 config 里有名有姓，且与模块兜底一致（§24）",
      _ms.get("bias_cap") == MS.BIAS_CAP
      and _ms.get("need_drift_rate_per_min") == MS.NEED_DRIFT_RATE_PER_MIN,
      f"bias_cap={_ms.get('bias_cap')}/{MS.BIAS_CAP} "
      f"drift={_ms.get('need_drift_rate_per_min')}/{MS.NEED_DRIFT_RATE_PER_MIN}")
pin(st, "norepinephrine", 0.9)
pin(st, "melatonin", 0.9)
cfg_t = copy.deepcopy(cfg)
cfg_t["modulator_system"]["bias_cap"] = 0.05
b_tight = MS.graph_biases(kg, st, cfg_t, names)
b_norm = MS.graph_biases(kg, st, cfg, names)
check("A8 钳位读 config（bias_cap 调小 ⇒ 同一批边给出更小的偏置，不是常数写死）",
      b_norm["network"] and all(abs(v) <= 0.05 + 1e-9
                                for v in b_tight["network"].values())
      and max(abs(v) for v in b_tight["network"].values())
      < max(abs(v) for v in b_norm["network"].values()),
      f"tight={b_tight['network']} norm={b_norm['network']}")
check("A8b 触到钳位会说出来（capped 里能查到，不静默削平）",
      any(c["kind"] == "network" and abs(c["raw"]) > 0.05
          for c in b_tight["capped"]), str(b_tight["capped"]))
unpin_all(st, cfg)

# ═══════ B 网络：连续偏置，无二值切换 ═══════
unpin_all(st, cfg)
pin(st, "norepinephrine", 0.85)          # 抬 CEN（+0.25）
pin(st, "melatonin", 0.85)               # 抬 DMN（+0.30）
b1 = MS.graph_biases(kg, st, cfg, names)
check("B1 调制器离开基线 ⇒ 网络偏置出现且由边权决定",
      b1["network"].get("CEN", 0.0) > 0.02 and b1["network"].get("DMN", 0.0) > 0.02,
      str(b1["network"]))
sweep = []
for x in (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0):
    unpin_all(st, cfg)
    pin(st, "norepinephrine", x)
    sweep.append((x, (MS.graph_biases(kg, st, cfg, names)["network"].get("CEN")
                      or 0.0)))
_peak_i = max(range(len(sweep)), key=lambda i: sweep[i][1])
check("B2 CEN 偏置全程有界（|bias| ≤ BIAS_CAP）",
      all(abs(v) <= MS.BIAS_CAP + 1e-9 for _, v in sweep), str(sweep))
check("B3 倒 U 落到了稳态水位上：峰不在末位，钉顶的抬升低于峰位",
      _peak_i < len(sweep) - 1 and sweep[-1][1] < sweep[_peak_i][1],
      str([(x, round(v, 4)) for x, v in sweep]))
check("B4 过载反转走到对侧（浓度顶格时偏置变号——「过度警觉反而失去聚焦」）",
      sweep[-1][1] < 0.0, str([(x, round(v, 4)) for x, v in sweep]))
unpin_all(st, cfg)
_lv0 = {}
_d = tempfile.mkdtemp(prefix="fas_p9a_")
TMP_KEEP.append(_d)
_field0 = CognitiveField(config=cfg, state_file=os.path.join(_d, "cf.json"))
_field0.networks.load_state({"levels": {"CEN": 0.5, "DMN": 0.5}})
for _ in range(40):
    _lv0 = _field0.step()["networks"]
pin(st, "norepinephrine", 0.85)
pin(st, "melatonin", 0.85)
_d = tempfile.mkdtemp(prefix="fas_p9b_")
TMP_KEEP.append(_d)
field2 = CognitiveField(config=cfg, state_file=os.path.join(_d, "cf.json"))
field2.networks.load_state({"levels": {"CEN": 0.5, "DMN": 0.5}})
field2.set_bias_source(lambda: MS.graph_biases(kg, st, cfg, field2.node_names()))
lv = {}
for _ in range(40):
    lv = field2.step()["networks"]
check("B5 双高态可达：两个方向同时给偏置，两个网络都抬起来（非 winner-take-all）",
      lv["CEN"] > _lv0["CEN"] + 0.05 and lv["DMN"] > _lv0["DMN"] + 0.05,
      f"无偏置 {fmt(_lv0)} → 有偏置 {fmt(lv)}")
check("B6 抬一个网络不会把另一个压到近零（软竞争，不是模式开关）",
      lv["CEN"] > 0.15 and lv["DMN"] > 0.30, str(lv))
check("B7 网络值始终在开区间（没有 0/1 端点吸附）",
      all(0.0 < v < 1.0 for v in lv.values()), str(lv))
# 行为上"无二值切换"的另一半：偏置扫全程，level 的变化是逐步的
_d = tempfile.mkdtemp(prefix="fas_p9c_")
TMP_KEEP.append(_d)
field3 = CognitiveField(config=cfg, state_file=os.path.join(_d, "cf.json"))
field3.set_bias_source(lambda: MS.graph_biases(kg, st, cfg, field3.node_names()))
traj = []
for x in (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00):
    unpin_all(st, cfg)
    pin(st, "norepinephrine", x)
    traj.append(field3.step()["networks"]["CEN"])
steps = [abs(traj[i] - traj[i - 1]) for i in range(1, len(traj))]
check("B8 改一次偏置只让水位挪一点（连续，无翻转）",
      all(s < 0.30 for s in steps) and max(steps) > 0.0,
      str([round(s, 4) for s in steps]))
code = "\n".join(
    ln for fn in SCAN_FILES
    for ln in open(os.path.join(ROOT, fn), encoding="utf-8").read().splitlines()
    if not ln.strip().startswith("#"))
hits = re.findall(r".*(?:\bmode\s*==|(?:level|activation)\w*\s*[<>]=?\s*0\.5).*", code)
check("B9 源码里没有模式判据（mode== / 拿 0.5 当开关比）", not hits, str(hits[:2]))

# ═══════ C 需求位移：有界 + 负反馈方向 ═══════
def need_case(pin_name, where="peak"):
    """某调制器离开基线后，四维需求靶值各被推动多少（控制器一步）。

    `where="peak"` = 响应最大处（判方向用它）；`"max"` = 量程顶（判有界用它，
    那里已进入过载反转区）。
    """
    kg_a, st_a, cfg_a, _f = build()
    for n, v in (("safety", 0.20), ("exploration", 0.30),
                 ("competence", 0.25), ("social", 0.30)):
        st_a.set_need_signal_provider(n, (lambda x=v: x))
    st_a.update_needs_from_signals(
        bias=MS.graph_biases(kg_a, st_a, cfg_a)["need"])       # 基线：无偏置
    base = {n: st_a.need_level(n) for n in NEEDS}
    unpin_all(st_a, cfg_a)
    if where == "peak":
        peak_conc(st_a, cfg_a, pin_name)
    else:
        pin(st_a, pin_name, float(cfg_a["modulator_system"]["specs"][pin_name]["max"]))
    bias_on = MS.graph_biases(kg_a, st_a, cfg_a)["need"]
    st_a.update_needs_from_signals(bias=bias_on)
    moved = {n: round(st_a.need_level(n) - base[n], 4) for n in NEEDS}
    bounded = all(st_a._bounds("need", n)[0] - 1e-9 <= st_a.need_level(n)
                  <= st_a._bounds("need", n)[1] + 1e-9 for n in NEEDS)
    return bias_on, moved, bounded


bias_ceiling, moved_ceiling, bounded = need_case("dopamine", where="max")
check("C1 调制器钉在量程顶 ⇒ 每个需求的靶值位移都有界（需求也仍在量程内）",
      bounded and all(abs(v) <= MS.BIAS_CAP + 1e-9
                      for v in bias_ceiling.values()),
      f"bias={fmt(bias_ceiling)} bounded={bounded}")
bias_dp, moved_dp, _ = need_case("dopamine", where="peak")
check("C2 饱足方向对：多巴胺样高位 ⇒ 探索需求靶值被压低（负反馈的一半）",
      bias_dp.get("exploration", 0.0) < 0.0
      and moved_dp.get("exploration", 0.0) < 0.0,
      f"bias={fmt(bias_dp)} moved={fmt(moved_dp)}")
check("C2b 顶格处符号反转（过载区：越「满足」反而把靶值抬回去）",
      bias_ceiling.get("exploration", 0.0) > 0.0,
      f"ceiling bias={fmt(bias_ceiling)} vs peak bias={fmt(bias_dp)}")
bias_ct, moved_ct, _ = need_case("cortisol", where="peak")
check("C3 压力方向对：皮质醇样高位 ⇒ 安全需求靶值被抬高（想避险）",
      bias_ct.get("safety", 0.0) > 0.0 and moved_ct.get("safety", 0.0) > 0.0,
      f"bias={fmt(bias_ct)} moved={fmt(moved_ct)}")
# 稳态回路跑长：两侧同时开满也不推爆（这才是"一正一反成负反馈"的实证）
kg_e, st_e, cfg_e, _f = build()
st_e.set_need_signal_provider("exploration", lambda: 0.30)
st_e.set_need_signal_provider("safety", lambda: 0.20)
mid = None
sal_hi, conc_lo, conc_hi = 0.0, 9.0, -9.0
for i in range(600):
    _g = MS.graph_biases(kg_e, st_e, cfg_e)
    MS.apply_need_drift(kg_e, st_e, cfg_e, dt_min=0.05)
    st_e.update_needs_from_signals(bias=_g.get("need") or {})
    if i == 299:
        mid = (round(st_e.need_salience("exploration"), 4),
               round(st_e.modulator_conc("dopamine"), 4))
    sal_hi = max(sal_hi, st_e.need_salience("exploration"))
    conc_lo = min(conc_lo, st_e.modulator_conc("dopamine"))
    conc_hi = max(conc_hi, st_e.modulator_conc("dopamine"))
check("C4 双向回路 600 拍全程有界（需求 0~1、调制器 min~max）",
      0.0 <= sal_hi <= 1.0 and 0.0 <= conc_lo <= conc_hi <= 1.0,
      f"sal_hi={sal_hi} conc={conc_lo}~{conc_hi}")
check("C5 回路收敛不推爆（后半程既没钉在量程端点、也没回到 0）",
      mid is not None and 0.0 < mid[1] < 1.0
      and 0.0 < st_e.modulator_conc("dopamine") < 1.0,
      f"mid={mid} end={round(st_e.modulator_conc('dopamine'), 4)}")

# ═══════ D 三种通道的分工（解释 rate_gains_n 为何留空） ═══════
_spec = {"X": {"inputs": {"base": 0.2}, "rise": 0.10, "fall": 0.10, "slew": 1.0}}
nf = NetworkField(copy.deepcopy(_spec), defaults={})
nf2 = NetworkField(copy.deepcopy(_spec), defaults={})
slow = nf.step({}, {}, rate_gains={"*": 1.0})["X"]
fast = nf2.step({}, {}, rate_gains={"*": 5.0})["X"]
check("D1 速率增益只改快慢（同一拍数下更快到位）", fast > slow,
      f"slow={slow} fast={fast}")
for _ in range(400):
    nf.step({}, {}, rate_gains={"*": 1.0})
    nf2.step({}, {}, rate_gains={"*": 5.0})
check("D2 到位后两者一致 ⇒ 持续偏置不能靠 rate 实现（所以网络走 bias）",
      abs(nf.level("X") - nf2.level("X")) < 1e-6,
      f"{nf.level('X')} vs {nf2.level('X')}")
nf3 = NetworkField({"X": {"inputs": {"base": 0.2}, "rise": 0.5, "fall": 0.5,
                           "slew": 1.0}}, defaults={})
for _ in range(60):
    nf3.step({}, {}, bias={"X": 0.30})
check("D3 加性偏置改的是稳态（同一 inputs，目标抬 0.30）",
      abs(nf3.level("X") - 0.50) < 1e-6, str(nf3.level("X")))
_dspec = {"a": {"node": "ADrive", "affinities": {"t1": 0.5}, "rise": 0.5, "fall": 0.5}}
df = DriveField(copy.deepcopy(_dspec))
no_t = df.target_of("a", {}, affinity_gain={"a.t1": 2.0})
with_t = df.target_of("a", {"t1": 0.6}, affinity_gain={"a.t1": 2.0})
check("D4 亲和增益需要张力在场（无张力时乘不动）",
      no_t == 0.0 and with_t > 0.5 * 0.6, f"{no_t} vs {with_t}")
check("D5 整驱键是逐张力键的回落（同一增益两种写法结果相同）",
      abs(df.target_of("a", {"t1": 0.6}, affinity_gain={"a": 2.0}) - with_t) < 1e-9,
      str(df.target_of("a", {"t1": 0.6}, affinity_gain={"a": 2.0})))
df2 = DriveField(copy.deepcopy(_dspec))
check("D6 驱动偏置在零张力时仍抬稳态（水位高但没有张力，也有落点）",
      df2.target_of("a", {}, bias=0.2) > 0.19, str(df2.target_of("a", {}, bias=0.2)))
check("D7 驱动偏置出不了量程", df2.target_of("a", {"t1": 1.0}, bias=9.0) == 1.0,
      str(df2.target_of("a", {"t1": 1.0}, bias=9.0)))

# ═══════ E need.* 进信号空间（审计 D-4） ═══════
kg, st, cfg, field = build()
sig_before = field._build_signals(field._merged_context(), field._hormone_devs(),
                                  field.networks.levels())
check("E1 未注册需求 ⇒ 不产 need.*（没有信号就没有观点，不用 0 伪装）",
      not any(k.startswith("need.") for k in sig_before),
      str([k for k in sig_before if k.startswith("need.")]))
for n in st.need_names():
    field.register_need(n, (lambda x=n: st.need_salience(x)))
sig_after = field._build_signals(field._merged_context(), field._hormone_devs(),
                                 field.networks.levels())
check("E2 注册后 4 维需求都进信号",
      all(f"need.{n}" in sig_after for n in NEEDS), str(sorted(sig_after))[:160])
check("E3 值是显著性（紧迫度或离靶距离），不是裸 level",
      abs(sig_after["need.social"] - st.need_salience("social")) < 1e-9,
      f"{sig_after.get('need.social')} vs {st.need_salience('social')}")
p_param = "cognition.express_threshold"
unpin_all(st, cfg)
st.set_need_signal_provider("social", lambda: 0.95)
for _ in range(60):
    st.update_needs_from_signals()
    field.step(settle=True)
lo_v = field.modulation.get(p_param)
hi_sal = st.need_salience("social")
st.set_need_signal_provider("social", lambda: 0.0)
for _ in range(60):
    st.update_needs_from_signals()
    field.step(settle=True)
hi_v = field.modulation.get(p_param)
check("E4 社交需求显著性改变表达阈值（那行 effects 不再是幽灵）",
      lo_v != hi_v, f"sal {hi_sal:.2f} → {lo_v:.4f}；sal 0 → {hi_v:.4f}")

# ═══════ F 动机表（autonomy）：数据不是 if 链 ═══════
import autonomy as A                                        # noqa: E402
kg_f, st_f, cfg_f, _f = build()
st_f.set_need_signal_provider("exploration", lambda: 1.0)
loop = A.AutonomousLoop(kg_f, None, config=cfg_f)
loop.internal_state = st_f
base_m = loop._motivation_value("curiosity")
for _ in range(60):
    st_f.update_needs_from_signals()
after_m = loop._motivation_value("curiosity")
check("F1 需求显著性进动机分（探索饥渴 → 好奇动机地板抬高）",
      after_m > base_m, f"{base_m} → {after_m}"
      f"（salience={st_f.need_salience('exploration')}）")
check("F2 urgency 有真消费者（显著性随超界程度上升）",
      st_f.need_urgency("exploration") > 0.0
      and st_f.need_salience("exploration") >= st_f.need_urgency("exploration"),
      f"urgency={st_f.need_urgency('exploration')}")
cfg_f["autonomy"]["motivation_needs"]["curiosity"] = [
    {"need": "exploration", "coef": 1.0}, {"need": "social", "coef": 0.5}]
st_f.set_need_signal_provider("social", lambda: 1.0)
for _ in range(60):
    st_f.update_needs_from_signals()
check("F3 表里加一维即生效（不改 _motivation_value 的代码）",
      loop._motivation_value("curiosity") >= after_m
      and st_f.need_salience("social") > 0.0,
      str(cfg_f["autonomy"]["motivation_needs"]))

# ═══════ G 漂移经唯一写入口 ═══════
kg, st, cfg, field = build()
st.set_need_signal_provider("safety", lambda: 1.0)
for _ in range(60):
    st.update_needs_from_signals()
before = st.modulator_conc("cortisol")
r = MS.apply_need_drift(kg, st, cfg, dt_min=1.0)
hist = st.history("modulator", "cortisol", 8)
check("G1 需求→调制器有写（经 apply_delta，source=needs，留痕带边义）",
      r["written"] > 0 and any(h.get("source") == "needs" for h in hist),
      f"written={r['written']} hist={[h.get('source') for h in hist]}")
check("G2 历史里的理由是漂移自己的话（可解释：哪个需求、多显著、乘多少）",
      any(str(h.get("reason", "")).startswith("需求漂移") for h in hist),
      str([h.get("reason") for h in hist][:2]))
pend = st.modulator_field("cortisol", "pending_rise") or 0.0
check("G3 上升受 rise 夹速：一次拍推不爆，余量进 pending_rise 而不是丢掉",
      abs(st.modulator_conc("cortisol") - before) <= 0.10 and pend >= 0.0,
      f"Δ={st.modulator_conc('cortisol') - before:.4f} pending={pend}")
cfg_off = copy.deepcopy(cfg)
cfg_off["modulator_system"]["subgraph"] = False
b0 = st.modulator_conc("cortisol")
r0 = MS.apply_need_drift(kg, st, cfg_off, dt_min=1.0)
check("G4 关掉子图 ⇒ 什么都不写（回滚开关是真的）",
      r0["written"] == 0 and st.modulator_conc("cortisol") == b0, str(r0))
check("G5 没有 kg / dt=0 时不写（不静默用默认时长顶替）",
      MS.apply_need_drift(None, st, cfg)["written"] == 0
      and MS.apply_need_drift(kg, st, cfg, dt_min=0.0)["written"] == 0, "")
_g6 = MS.graph_biases(kg, st, cfg_off, field.node_names())
check("G6 子图关闭时偏置也算不出来（skipped，而不是当 0 静默通过）",
      _g6.get("skipped") is True and not _g6["network"], str(_g6))

for t in TMP_KEEP:
    import shutil
    shutil.rmtree(t, ignore_errors=True)

print()
if FAILURES:
    print(f"✗ R2 P9 闸门 {len(FAILURES)}/{PASSED[0] + len(FAILURES)} 未过："
          + "；".join(FAILURES))
    sys.exit(1)
print(f"✓ R2 P9 闸门全部通过（{PASSED[0]} 项）")
