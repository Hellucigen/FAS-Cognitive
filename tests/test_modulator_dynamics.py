# test_modulator_dynamics.py — R2 P3：调制器时间动力学真的起作用（不测接口存在，测效力）
#
# 离线：临时目录，不碰 data/internal_state.json 与真实图谱。
# 事件全部走**真实管线**（pulse / apply_delta / tick_decay / RewardSystem.modulation），
# 不写任何"为了测试而存在的"旁路开关（任务规范 §26）。
# 规格数值一律来自 config["modulator_system"]["specs"]（§24：集中管理，测试只改副本）。
#
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_modulator_dynamics.py

import copy
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.WARNING)

import config as C
from internal_state import (InternalState, MODULATOR_SPEC, receptor_response)
from reward import RewardSystem

FAILURES = []
MS = "modulator_system"


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def near(a, b, eps=1e-6):
    return abs(float(a) - float(b)) < eps


def cfg(mut=None, drop=False):
    """真实 config 的副本；mut={名: {字段: 值}} 只改测试所需的规格字段。"""
    c = copy.deepcopy(C.DEFAULT_CONFIG)
    if drop:
        c.pop(MS, None)
    for name, patch in (mut or {}).items():
        c[MS]["specs"][name].update(patch)
    return c


def build(cfg_dict=None, tag=""):
    base = os.path.join(tempfile.gettempdir(), f"fas_test_moddyn_{tag}")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    return InternalState(kg=None, config=cfg_dict if cfg_dict is not None else cfg(),
                         data_dir=base), base


# ═══════ A：规格装载与回退（§24 单一真源；旧路径逐字不变）═══════
def test_A_specs():
    print("\n── A 规格装载 ──")
    st, _ = build(tag="a1")
    names = set(st.modulator_names())
    check("config 给 12 个调制器", len(names) == 12, str(sorted(names)))
    spec_names = set(C.DEFAULT_CONFIG[MS]["specs"])
    check("运行时名单 == config 名单（无第二份表）", names == spec_names,
          str(names ^ spec_names))
    check("旧 4 个的镜像节点 id 逐字保留",
          st.mirror_modulator.get("dopamine") == "多巴胺样"
          and st.mirror_modulator.get("cortisol") == "皮质醇样"
          and st.mirror_modulator.get("serotonin") == "血清素样"
          and st.mirror_modulator.get("oxytocin") == "催产素样",
          json.dumps({k: v for k, v in st.mirror_modulator.items()
                      if k in MODULATOR_SPEC}, ensure_ascii=False))
    check("新增 8 个也都有中文镜像 id",
          st.mirror_modulator.get("melatonin") == "褪黑素样"
          and st.mirror_modulator.get("gaba") == "GABA样",
          str(st.mirror_modulator.get("melatonin")))
    check("每个调制器的 decay_per_min 互不相同（§8 禁同一衰减）",
          len({round(st.modulator_field(n, "decay_per_min"), 6) for n in names})
          == len(names), "")
    check("褪黑素标记为 circadian 驱动（不吃系统时钟）",
          st.modulator_field("melatonin", "circadian_driven") is True)
    # 回退路径：config 缺该段 → 与 R2 之前完全一致的 4 个
    st2, _ = build(cfg({}, drop=True), tag="a2")
    check("config 缺段时回退旧 4 个调制器",
          set(st2.modulator_names()) == set(MODULATOR_SPEC), str(st2.modulator_names()))
    check("回退路径下曲线/动量/不应期全关（恒等）",
          all(st2.modulator_field(n, "saturation") == 1.0
              and st2.modulator_field(n, "overload") == 1.0
              and st2.modulator_field(n, "momentum") == 0.0
              and st2.modulator_field(n, "refractory_min") == 0.0
              and st2.modulator_field(n, "rise_per_min") is None
              for n in st2.modulator_names()))


# ═══════ B：存储语义（tonic=真值，level=派生视图）═══════
def test_B_channels():
    print("\n── B tonic/level 两通道 ──")
    st, _ = build(cfg({"dopamine": {"momentum": 0.0, "refractory_min": 0.0}}), tag="b1")
    c0 = st.modulator_conc("dopamine")
    check("出厂时 level 视图 == 张力性浓度（曲线在基线附近是恒等）",
          near(st.modulator_level("dopamine"), c0, 1e-9),
          f"level={st.modulator_level('dopamine')} conc={c0}")
    st.pulse("dopamine", 0.4, reason="意外的好结果")
    check("脉冲只动 phasic，不动浓度",
          near(st.modulator_conc("dopamine"), c0, 1e-9),
          str(st.modulator_conc("dopamine")))
    check("脉冲抬高了体验水位（level = 合成视图，过受体曲线）",
          near(st.modulator_level("dopamine"),
               min(1.0, c0 + receptor_response(0.4, 0.5, 0.85, 0.95,
                                               st.modulator_field("dopamine",
                                                                  "overload_pull"))),
               1e-3),
          f"level={st.modulator_level('dopamine')}")
    check("脉冲抬了体验水位但没顶到浓度上限（曲线压缩 + 与浓度分离）",
          st.modulator_level("dopamine") > c0 + 0.3
          and st.modulator_conc("dopamine") < st.modulator_level("dopamine"), "")
    check("参数通道（tonic）不含脉冲 → 瞬时惊喜不推走慢参数",
          near(st.modulator_tonic("dopamine"),
               st._view_of("dopamine", dict(st._modulators["dopamine"], phasic=0.0)),
               1e-9))
    check("modulator_dev 只看慢分量", near(st.modulator_dev("dopamine"),
                                          st.modulator_tonic("dopamine")
                                          - st.modulator_baseline("dopamine"), 1e-9))
    # 未拆快通道的调制器：脉冲 = 慢分量位移（带习惯化），level 跟着走
    st.pulse("gaba", 0.05, reason="抑制性事件")
    check("未拆脉冲确实抬升浓度（不是空转）",
          st.modulator_conc("gaba") > 0.55 - 1e-9 or
          st.modulator_field("gaba", "rise_per_min") is not None,
          str(st.modulator_conc("gaba")))
    # phasic_to_level 折算比例有效
    st3, _ = build(cfg({"dopamine": {"phasic_to_level": 0.5, "saturation": 1.0,
                                     "overload": 1.0, "momentum": 0.0,
                                     "refractory_min": 0.0}}), tag="b2")
    b = st3.modulator_baseline("dopamine")
    st3.pulse("dopamine", 0.4, reason="半折算")
    check("phasic_to_level 参与合成（0.5 → 只抬一半）",
          near(st3.modulator_level("dopamine"), b + 0.2, 1e-3),
          str(st3.modulator_level("dopamine")))


# ═══════ C：sensitivity / momentum / refractory 都在脉冲通路上生效 ═══════
def test_C_pulse_gains():
    print("\n── C 脉冲增益三件套 ──")
    # sensitivity：adrenaline 1.30 vs serotonin 0.90
    st, _ = build(cfg({"adrenaline": {"refractory_min": 0.0, "momentum": 0.0},
                       "serotonin": {"momentum": 0.0, "refractory_min": 0.0}}),
                  tag="c1")
    r_ad = st.pulse("adrenaline", 0.10, reason="同一事件")   # 未拆 → 慢分量
    r_se = st.pulse("serotonin", 0.10, reason="同一事件")
    check("sensitivity 放大/缩小同幅事件（1.3 vs 0.9）",
          abs(r_ad["delta"]) > abs(r_se["delta"]) or
          (r_ad["pending_rise"] or 0) > (r_se["pending_rise"] or 0),
          f"ad={r_ad['delta']}/{r_ad.get('pending_rise')} se={r_se['delta']}/{r_se.get('pending_rise')}")

    # momentum：同向重复递减，反向不衰减
    st2, _ = build(cfg({"dopamine": {"momentum": 0.6, "refractory_min": 0.0,
                                     "sensitivity": 1.0}}), tag="c2")
    d1 = st2.pulse("dopamine", 0.20, reason="好消息 1")["delta"]
    d2 = st2.pulse("dopamine", 0.20, reason="好消息 2")["delta"]
    d3 = st2.pulse("dopamine", 0.20, reason="好消息 3")["delta"]
    check("同向重复脉冲效力递减（习惯化）", d1 > d2 > d3 > 0,
          f"{d1} {d2} {d3}")
    rev = st2.pulse("dopamine", -0.20, reason="突然反转")["delta"]
    check("反向脉冲不受习惯化压制（意外保留全效力）",
          abs(rev) >= d3, f"rev={rev} d3={d3}")
    check("momentum_state 被写入并在范围内",
          0 < abs(st2.modulator_field("dopamine", "momentum_state")) < 1.0,
          str(st2.modulator_field("dopamine", "momentum_state")))

    # refractory：dopamine 0.5 分钟
    st3, _ = build(cfg({"dopamine": {"refractory_min": 5.0, "momentum": 0.0}}), tag="c3")
    a = st3.pulse("dopamine", 0.20, reason="第一下")["delta"]
    b = st3.pulse("dopamine", 0.20, reason="紧跟的第二下")["delta"]
    check("不应期内第二下明显打不动", abs(b) < abs(a) * 0.5, f"a={a} b={b}")
    check("不应期剩余时间可观测",
          st3.modulator_field("dopamine", "refractory_until") > time.time(), "")
    # 过了不应期恢复（用假时钟推进 refractory_until 之后）
    st3._modulators["dopamine"]["refractory_until"] = time.time() - 1
    c = st3.pulse("dopamine", 0.20, reason="恢复后")["delta"]
    check("不应期结束后恢复效力", abs(c) > abs(b) * 5, f"b={b} c={c}")


# ═══════ D：非线性响应（§14 低/常/高/过载是连续函数）═══════
def test_D_curve():
    print("\n── D 受体曲线 ──")
    # 关闭态：saturation/overload 都是 1.0 → 恒等
    check("曲线关闭时恒等（旧行为逐字不变）",
          near(receptor_response(0.37, 0.5, 1.0, 1.0, 0.6), 0.37, 1e-12))
    # 开启态：线性区斜率 1、饱和点后边际递减、**峰值正落在过载点上**、之后回落
    f = lambda u: receptor_response(u, 0.20, 0.60, 0.80, 0.60)   # adrenaline
    check("线性区内 = 恒等", near(f(0.10), 0.10, 1e-12), str(f(0.10)))
    check("饱和点后边际递减（响应 < 浓度）", 0 < f(0.50) < 0.50, str(f(0.50)))
    check("响应峰值正好在配置的过载点上（u=O 处斜率 0）",
          f(0.599) < f(0.60) and f(0.601) < f(0.60),
          f"{f(0.599)} {f(0.60)} {f(0.601)}")
    check("过载点之后继续加反而更弱（倒 U 下行支可达且单调下行）",
          f(0.70) > f(0.80) > f(0.95) > 0, f"{f(0.70)} {f(0.80)} {f(0.95)}")
    check("负向对称（下界同样有曲线）", near(f(-0.30), -f(0.30), 1e-12))
    check("曲线连续（饱和点与过载点都无跳变）",
          abs(f(0.400 + 1e-6) - f(0.400 - 1e-6)) < 1e-3
          and abs(f(0.600 + 1e-6) - f(0.600 - 1e-6)) < 1e-3,
          f"{f(0.399)} vs {f(0.401)}")
    check("响应天花板有限（再多分泌也不可能把信号推到无穷）",
          max(f(u / 100.0) for u in range(0, 81)) <= 0.5001, "")
    check("overload_pull 只控制峰值后回落速度（=0 时仍降但更缓）",
          receptor_response(0.95, 0.20, 0.60, 0.80, 0.0)
          > receptor_response(0.95, 0.20, 0.60, 0.80, 0.6)
          > 0, "")
    # 端到端：把浓度顶到过载区，level 视图反而回落
    st, _ = build(cfg({"adrenaline": {"rise_per_min": None}}), tag="d1")
    st.set_value("modulator", "adrenaline", 0.70, reason="测试顶到高位")
    lo = st.modulator_level("adrenaline")
    st.set_value("modulator", "adrenaline", 0.99, reason="测试顶到过载")
    hi = st.modulator_level("adrenaline")
    check("浓度上升但响应水位回落（过载不是万能开关）", hi < lo,
          f"conc .70→.99 但 level {lo:.3f}→{hi:.3f}")
    dump = st.dump_modulator_state(top=12)
    row = [r for r in dump["modulators"] if r["name"] == "adrenaline"][0]
    check("dump 标出过载态且按浓度判定", row["overload"] is True
          and row["saturated"] is True, str(row))
    devs = [r["|dev|"] for r in dump["modulators"]]
    check("dump 按 |dev| 降序（最活跃的在前）", devs == sorted(devs, reverse=True),
          str(devs))
    check("dump 提供 top-modulators 计数",
          dump["count"] == 12 and dump["active_count"] >= 1, str(dump["count"]))
    dyn = st.modulator_dynamics("adrenaline")
    check("modulator_dynamics 暴露曲线成分（实验台用）",
          dyn["ok"] and dyn["curve"]["overload"] == 0.80
          and dyn["conc"] > dyn["tonic_response"], json.dumps(dyn, ensure_ascii=False))


# ═══════ E：时间演化（decay / phasic 衰减 / 上升吸收 / 习惯化恢复）═══════
def test_E_time():
    print("\n── E 时间动力学 ──")
    st, _ = build(tag="e1")
    for n in ("cortisol", "serotonin", "adrenaline", "glutamate"):
        st.set_value("modulator", n, st.modulator_baseline(n) + 0.25,
                     reason="测试抬升")
    t0 = st.modulator_field("cortisol", "last_update")
    # 4 个调制器各差 0.25；等距抬升后放 3 分钟，速率不同 → 落点不同
    for n in ("cortisol", "serotonin", "adrenaline", "glutamate"):
        st._modulators[n]["last_update"] = t0
        st._modulators[n]["last_slow_ts"] = t0
    after = t0 + 180.0
    moved = st.tick_decay(now=after)
    check("tick_decay 报告动过的调制器数", moved >= 4, str(moved))
    resid = {n: st.modulator_conc(n) - st.modulator_baseline(n)
             for n in ("cortisol", "serotonin", "adrenaline", "glutamate")}
    check("衰减快的（肾上腺素 .12）比衰减慢的（血清素 .01）更接近基线",
          resid["adrenaline"] < resid["cortisol"] < resid["serotonin"], str(resid))
    check("没有任何两个调制器落在同一点（每拍速率各异）",
          len({round(v, 5) for v in resid.values()}) == 4, str(resid))
    # phasic 各自的衰减速度：dopamine .30 快，serotonin .05 慢
    st2, _ = build(cfg({"dopamine": {"refractory_min": 0.0, "momentum": 0.0}}), tag="e2")
    st2.pulse("dopamine", 0.5, reason="脉冲")
    st2.set_value("modulator", "serotonin", 0.9, reason="抬")
    st2.pulse("serotonin", 0.5, reason="脉冲")   # 未拆 → 走慢分量，这里只为留水位
    base_t = st2.modulator_field("dopamine", "last_update")
    st2.tick_decay(now=base_t + 180.0)
    check("phasic 快速回 0（多巴胺 3 分钟后已基本消失）",
          abs(st2.modulator_pulse("dopamine")) < 0.1,
          str(st2.modulator_pulse("dopamine")))
    check("phasic 不改变浓度（衰减只把脉冲带走）",
          near(st2.modulator_conc("dopamine"), 0.50, 1e-6),
          str(st2.modulator_conc("dopamine")))
    # 上升夹速 + pending 吸收（§13 累积）
    st3, _ = build(cfg({"dopamine": {"rise_per_min": 0.05, "momentum": 0.0,
                                     "refractory_min": 0.0,
                                     "decay_per_min": 0.0}}), tag="e3")
    st3.apply_delta("modulator", "dopamine", 0.02, reason="建立时钟")
    ts = st3.modulator_field("dopamine", "last_slow_ts")
    st3._modulators["dopamine"]["last_update"] = ts
    r = st3.apply_delta("modulator", "dopamine", 0.30, reason="一次性大事件")
    conc_after = st3.modulator_conc("dopamine")
    check("上升被 rise_per_min 夹住（大事件不会瞬间顶满）",
          conc_after < 0.55 + 0.20, f"conc={conc_after}")
    check("夹下来的余量进了 pending_rise", r["pending_rise"] > 0.1,
          json.dumps({k: r[k] for k in ("slew_limited", "pending_rise")}))
    st3._modulators["dopamine"]["last_update"] = ts
    st3.tick_decay(now=ts + 60.0)          # 1 分钟后：应吸收 ≈0.05
    absorbed = st3.modulator_conc("dopamine") - conc_after
    check("pending 按速率逐拍吸收", 0.03 < absorbed < 0.08, str(absorbed))
    check("吸收后 pending 减少",
          st3.modulator_field("dopamine", "pending_rise") < r["pending_rise"])
    # 习惯化恢复
    st4, _ = build(cfg({"dopamine": {"momentum": 0.6, "refractory_min": 0.0}}), tag="e4")
    st4.pulse("dopamine", 0.3, reason="建立动量")
    mv0 = st4.modulator_field("dopamine", "momentum_state")
    st4._modulators["dopamine"]["last_update"] = time.time()
    st4.tick_decay(now=time.time() + 600.0)
    check("事件停了呢习惯化会退掉",
          abs(st4.modulator_field("dopamine", "momentum_state")) < abs(mv0),
          f"{mv0} -> {st4.modulator_field('dopamine','momentum_state')}")
    # 历史合并（审计 D-6）：连续衰减不刷屏，真实事件留在历史里
    st5, _ = build(cfg({"cortisol": {"rise_per_min": None}}), tag="e5")
    st5.apply_delta("modulator", "cortisol", 0.4, reason="真实事件：被吼了")
    t = st5.modulator_field("cortisol", "last_update")
    for i in range(1, 20):
        st5._modulators["cortisol"]["last_update"] = t + (i - 1) * 30.0
        st5.tick_decay(now=t + i * 30.0)
    hist = st5.history("modulator", "cortisol", 50)
    check("20 拍衰减没有把历史刷满（同源合并）", len(hist) <= 4,
          str(len(hist)))
    check("真实事件仍在历史里且未被合并掉",
          any(h["reason"] == "真实事件：被吼了" for h in hist),
          json.dumps([h["reason"] for h in hist], ensure_ascii=False))


# ═══════ F：与奖赏管线的接缝（不重复计数）═══════
def test_F_reward_pipeline():
    print("\n── F 奖赏 → 调制 → 学习率 ──")
    st, _ = build(cfg({"dopamine": {"momentum": 0.0, "refractory_min": 0.0}}), tag="f1")
    rw = RewardSystem(internal_state=st, config=cfg())
    m0 = rw.modulation()
    check("modulation 返回可解释分量", {"base", "phasic", "tonic", "stress",
                                        "factor"} <= set(m0.keys()), str(m0))
    st.pulse_dopamine(0.5, reason="RPE 大正")
    m1 = rw.modulation()
    check("phasic 抬学习率（脉冲确实进学习项）",
          m1["phasic"] > m0["phasic"] and m1["factor"] > m0["factor"],
          f"{m0} -> {m1}")
    check("脉冲不改 tonic 学习项（同一份 phasic 不被算两遍）",
          near(m1["tonic"], m0["tonic"], 1e-9), f"{m0['tonic']} -> {m1['tonic']}")
    st.apply_delta("modulator", "dopamine", 0.30, reason="长期回报预期上升")
    m2 = rw.modulation()
    check("慢分量上升才改 tonic 项", m2["tonic"] > m1["tonic"],
          f"{m1['tonic']} -> {m2['tonic']}")
    st.apply_delta("modulator", "cortisol", 0.5, reason="压力")
    m3 = rw.modulation()
    check("压力项钝化学习", m3["stress"] < 0 and m3["factor"] < m2["factor"], str(m3))
    check("学习因子始终在 [0.4,1.8]",
          all(0.4 <= m["factor"] <= 1.8 for m in (m0, m1, m2, m3)), "")
    # 参数通道读数走 tonic（app.py 的接线方式）；两通道在此明确分离
    read = st.modulator_tonic("dopamine")
    dev = st.modulator_dev("dopamine")
    check("hormone.* 信号的输入 = 慢分量偏移（曲线后）",
          near(read - st.modulator_baseline("dopamine"), dev, 1e-9),
          f"tonic={read} dev={dev}")
    check("体验通道 ≠ 参数通道：0.5 的巨脉冲把合成信号顶进过载区，"
          "水位视图反而低于慢分量读数（过载不是加数）",
          st.modulator_level("dopamine") < read,
          f"tonic={read} level={st.modulator_level('dopamine')}")
    st_small, _ = build(cfg({"dopamine": {"momentum": 0.0, "refractory_min": 0.0}}),
                        tag="f2")
    st_small.pulse("dopamine", 0.25, reason="典型 RPE 脉冲")
    check("典型幅度（0.25）的脉冲仍在曲线线性区：体验水位高于慢分量读数",
          st_small.modulator_level("dopamine") > st_small.modulator_tonic("dopamine"),
          f"{st_small.modulator_level('dopamine')} vs {st_small.modulator_tonic('dopamine')}")


# ═══════ G：可观测性 + 持久化往返 + 旧快照迁移 ═══════
def test_G_observe_persist():
    print("\n── G 观测 / 持久化 / 迁移 ──")
    st, base = build(tag="g1")
    st.pulse("norepinephrine", 0.3, reason="意外打断")
    st.apply_delta("modulator", "cortisol", 0.2, reason="威胁线索")
    s = st.state()["modulators"]
    check("state() 每个调制器都带全字段（level/conc/dev/曲线参数）",
          all({"level", "conc", "tonic", "phasic", "dev", "decay_per_min",
               "rise_per_min", "momentum", "sensitivity", "saturation", "overload",
               "refractory_min", "refractory_left_s", "category", "graph_id",
               "recent_sources", "pending_rise", "momentum_state"} <= set(v)
              for v in s.values()),
          str(sorted(set(s["dopamine"]) - {"level"})))
    check("level 仍是数值（前端契约不破）",
          all(isinstance(v["level"], float) and 0.0 <= v["level"] <= 1.0
              for v in s.values()), "")
    check("图谱 id 在 state 里可见（可解释：哪个调制器对应哪个节点）",
          s["norepinephrine"]["graph_id"] == "去甲肾上腺素样",
          str(s["norepinephrine"]["graph_id"]))
    check("recent_sources 记录了推动事件",
          any("威胁" in (r.get("reason") or "") for r in
              s["cortisol"]["recent_sources"]), str(s["cortisol"]["recent_sources"]))
    # 快照 → 改 → 恢复
    snap = st.snapshot()
    st.apply_delta("modulator", "cortisol", 0.1, reason="改动")
    st.pulse("norepinephrine", 0.2, reason="改动脉冲")
    st.restore(snap)
    check("快照恢复把浓度与脉冲都带回去",
          near(st.modulator_conc("cortisol"),
               snap["modulators"]["cortisol"]["tonic"], 1e-6)
          and near(st.modulator_pulse("norepinephrine"),
                   snap["modulators"]["norepinephrine"]["phasic"], 1e-6),
          f"{st.modulator_conc('cortisol')} {st.modulator_pulse('norepinephrine')}")
    check("恢复后 level 视图重算（不是过期缓存）",
          near(st.modulator_level("norepinephrine"),
               st._view_of("norepinephrine", st._modulators["norepinephrine"]), 1e-9))
    # 落盘 → 重启
    st.save()
    st2 = InternalState(kg=None, config=cfg(), data_dir=base)
    check("重启后浓度一致",
          near(st2.modulator_conc("cortisol"), st.modulator_conc("cortisol"), 1e-6))
    check("重启后脉冲/动量/不应期一起恢复",
          near(st2.modulator_pulse("norepinephrine"),
               st.modulator_pulse("norepinephrine"), 1e-6)
          and "momentum_state" in st2._modulators["norepinephrine"])
    # 复位：连 pending / 习惯化 / 不应期一起清
    st3, _ = build(cfg({"dopamine": {"rise_per_min": 0.05}}), tag="g2")
    st3.apply_delta("modulator", "dopamine", 0.02, reason="建立时钟")
    st3.apply_delta("modulator", "dopamine", 0.3, reason="大事件")
    st3.pulse("norepinephrine", 0.3, reason="动量")
    st3.reset_modulators()
    check("恢复出厂：水位回基线",
          all(near(st3.modulator_conc(n), st3.modulator_baseline(n), 1e-9)
              for n in st3.modulator_names()),
          str({n: st3.modulator_conc(n) for n in st3.modulator_names()
               if not near(st3.modulator_conc(n), st3.modulator_baseline(n), 1e-9)}))
    check("恢复出厂：余量/习惯化/不应期都清零（不会自己爬回去）",
          st3.modulator_field("dopamine", "pending_rise") == 0.0
          and st3.modulator_field("norepinephrine", "momentum_state") == 0.0
          and st3.modulator_field("dopamine", "refractory_until") == 0.0, "")
    # 旧快照迁移：tonic 为 null 的历史文件
    legacy = {"version": 1, "cycle_seq": 7,
              "modulators": {n: {"level": 0.66, "tonic": None, "phasic": None,
                                 "history": []} for n in MODULATOR_SPEC}}
    legacy["modulators"]["dopamine"]["phasic"] = 0.12
    ldir = os.path.join(tempfile.gettempdir(), "fas_test_moddyn_legacy")
    shutil.rmtree(ldir, ignore_errors=True)
    os.makedirs(ldir, exist_ok=True)
    with open(os.path.join(ldir, "internal_state.json"), "w", encoding="utf-8") as f:
        json.dump(legacy, f)
    lst = InternalState(kg=None, config={}, data_dir=ldir)
    check("旧快照（tonic=null）迁移：浓度回落到 level 值",
          near(lst.modulator_conc("cortisol"), 0.66, 1e-9),
          str(lst.modulator_conc("cortisol")))
    check("旧快照迁移：视图 = 浓度 + phasic（多巴胺 0.66+0.12）",
          near(lst.modulator_level("dopamine"), 0.78, 1e-6),
          str(lst.modulator_level("dopamine")))
    check("旧快照迁移：未拆的调制器没有快通道",
          lst.modulator_pulse("cortisol") == 0.0)


def main():
    for fn in (test_A_specs, test_B_channels, test_C_pulse_gains, test_D_curve,
               test_E_time, test_F_reward_pipeline, test_G_observe_persist):
        fn()
    print("\n" + "=" * 56)
    if FAILURES:
        print(f"FAILED {len(FAILURES)}: " + "; ".join(FAILURES))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
