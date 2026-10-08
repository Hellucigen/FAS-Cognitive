# test_modulation_targets.py — R2 P8 闸门：14 个认知场目标都是"真旋钮"
# ============================================================================
# §8 的目标表列了 14 个认知场参数。R2 之前的状态是：4 个从没接过的参数、
# 一条谁都没读的 `retrieval.*`、以及"过载支走不到参数"（曲线字段没有牙，审计 D5）。
# 本文件锁住 R2 P8 的四件事，全部走**真实装配**（CognitiveField 合并 + 投影器写行），
# 不自己手搓一份 effects 表（那是 §26 禁的"测试期逻辑"）：
#
#   A 每个目标都有生产读者：源码里存在 `.get("<param>"` / `_mod("<param>"` 调用点
#     ——注册了就没人读 = 假旋钮（D-3 的形状）
#   B 每个目标在其**主导致信号**上单调且落在 [min,max] 内：给 0/± 时参数真的动，
#     动到头也不会越界（EMA 收敛后才读，避免读到追赶过程）
#   C 倒 U 能走到参数：把调制器浓度从基线扫到顶格，参数先跟上去、过峰后**退回来**
#     ——过饱和必须削弱效力（§禁令 8/§9，D5 的兑现）
#   D 图是权威：删掉某个靶点的调制边 → 该参数不再受该激素影响，加回来又恢复
#     （P6 证的是"系数行"，这里证的是"参数值"，两层都要）
#
# 运行: E:/Miniforge.envs/Fascinator/python.exe -X utf8 tests/test_modulation_targets.py
# ============================================================================
import copy
import logging
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.WARNING)

import config as C                                        # noqa: E402
from graph_model import KnowledgeGraph, Node              # noqa: E402
from internal_state import InternalState                  # noqa: E402
from cognitive_field import CognitiveField                # noqa: E402
import modulator_subgraph as MS                           # noqa: E402

FAILURES = []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_KEEP = []
# 靶点的调制关系词（只有这三个产生系数；`交互` 是调制器之间的耦合）
COEF_RELS = ("增强", "抑制", "调制")


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def build():
    """真实装配：config → CognitiveField（解析 baseline_from）→ 图 → 投影。"""
    cfg = copy.deepcopy(C.DEFAULT_CONFIG)
    kg = KnowledgeGraph()
    kg.add_node(Node(id="Self", label="declarative-semantic", graph_space="self"))
    d = tempfile.mkdtemp(prefix="fas_p8_")
    TMP_KEEP.append(d)
    st = InternalState(kg=kg, config=cfg, data_dir=d)
    st.sync_graph()
    MS.ensure_modulator_subgraph(kg, st, cfg)
    field = CognitiveField(config=cfg, state_file=os.path.join(d, "cf_state.json"))
    proj = MS.project_coefficients(kg, st, field.modulation, cfg)
    return kg, st, cfg, field, proj


def settled(layer, param, signals, tol=1e-12, cap=500):
    """把信号喂够拍数让 EMA 收敛后再读——否则读到的是"正在追"的中间值。"""
    prev = None
    for _ in range(cap):
        layer.compute(signals)
        v = float(layer.get(param))
        if prev is not None and abs(v - prev) < tol:
            return v
        prev = v
    return prev


def spec_of(layer, param) -> dict:
    """装配后的真实规格（min/max/baseline）。测试读私有表是故意的：
    要断言的正是"层拿到的那份规格"，而不是 config 里可能被覆盖的原始行。"""
    return layer._specs.get(param) or {}


# ═══════ A 真实读者：不留假旋钮（D-3） ═══════
SKIP_DIRS = {".git", ".zcode", "__pycache__", "data", "docs", "minecraft_bot",
             "prototype", "reports", "scripts", "tests", "web", "node_modules"}
READER = (r'(?:\.get|\.get_int|\.get_float|_mod|_mod_th)\s*\(\s*["\']{p}["\']')


def readers(param):
    """源码里把该参数当值读出来的位置（file, line）。
    只认调用位（`x.get("param"` / `_mod("param"`），表定义位（`"param": {`）不算，
    否则 config 的出厂表会伪装成消费者。"""
    pat = re.compile(READER.format(p=re.escape(param)))
    hits = []
    for dirpath, dirnames, names in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                       and not d.startswith(".")]
        for fn in names:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                with open(fp, encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            for m in pat.finditer(text):
                hits.append((os.path.relpath(fp, ROOT).replace("\\", "/"),
                             text[:m.start()].count("\n") + 1))
    return hits


def test_A_readers(targets):
    print("\n── A 14 个目标都有生产读者（注册≠接线）──")
    dead = []
    for short, param in sorted(targets.items()):
        hs = readers(param)
        if not hs:
            dead.append(short)
        check(f"A {short} → {param} 有读者", bool(hs), "无任何 .get( 调用点")
        if hs:
            print(f"      {len(hs)} 处：{', '.join(f'{f}:{l}' for f, l in hs[:3])}")
    check("A 汇总：零假旋钮", not dead, str(dead))


# ═══════ B 单调 + 有界：信号动，参数真的动 ═══════
def test_B_monotone(layer, cfg, targets):
    print("\n── B 主导致信号上单调、有界、且确实改变参数 ──")
    no_teeth, out_of_range, flat_rows = [], [], []
    for short, param in sorted(targets.items()):
        eff = {k: v for k, v in layer.effects(param).items() if k != "constant"}
        if not eff:
            no_teeth.append((short, "无 effects 行"))
            continue
        sig, coef = max(eff.items(), key=lambda kv: abs(kv[1]))
        sp = spec_of(layer, param)
        lo, hi = float(sp.get("min", -1e9)), float(sp.get("max", 1e9))
        # 扫描量程按信号族取物理范围：激素是基线偏移（±），其余是 [0,1] 强度
        amp = 0.45 if sig.startswith("hormone.") else 1.0
        xs = ([0.0, 0.25, 0.5, 0.75, 1.0] if not sig.startswith("hormone.")
              else [-amp, -amp / 2, 0.0, amp / 2, amp])
        vs = [settled(layer, param, {sig: x}) for x in xs]
        bad_bound = [v for v in vs if v < lo - 1e-9 or v > hi + 1e-9]
        if bad_bound:
            out_of_range.append((short, param, bad_bound[0], lo, hi))
        seq = vs if coef > 0 else vs[::-1]
        monotone = all(seq[i + 1] >= seq[i] - 1e-9 for i in range(len(seq) - 1))
        moved = abs(vs[-1] - vs[0]) > 1e-6 if not sig.startswith("hormone.") \
            else abs(vs[2] - vs[0]) > 1e-6 and abs(vs[2] - vs[-1]) > 1e-6
        if not moved:
            flat_rows.append((short, param, sig, coef))
        check(f"B {short}：{sig}（系数 {coef:+.2f}）单调{'' if coef > 0 else '（负向）'}"
              f"且有界", monotone and not bad_bound,
              f"{[round(v, 4) for v in vs]} xs={xs}")
    check("B 汇总：无越界参数", not out_of_range, str(out_of_range[:3]))
    check("B 汇总：每个靶点的主导行都有牙（参数真的动）", not flat_rows,
          str(flat_rows))
    check("B 汇总：无空 effects 靶点", not no_teeth, str(no_teeth))


# ═══════ C 倒 U 走到参数：过饱和削弱效力（D5） ═══════
def test_C_overload_reversal(kg, st, cfg, layer, targets):
    print("\n── C 浓度扫到顶格：参数先升后降（过载不是加数）──")
    specs = cfg["modulator_system"]["specs"]
    rows_by_mod = {}
    for param, eff in ((p, layer.effects(p)) for p in targets.values()):
        for k, coef in eff.items():
            if k.startswith("hormone."):
                rows_by_mod.setdefault(k.split(".", 1)[1], []).append((param, coef))
    inert_at_param, no_reversal = [], []
    for name in sorted(st.modulator_names()):
        s = specs[name]
        rows = sorted(rows_by_mod.get(name) or [], key=lambda t: abs(t[1]))
        if not rows:
            inert_at_param.append((name, "图上一条系数边都没有"))
            continue
        base = float(s["baseline"])
        peak_c = None
        peak_dev = 0.0
        # 先在真曲线上找响应峰值浓度（用 set_value/manual：夹速是真实机制，
        # 这里要的是"曲线形状"，不是"多少分钟能爬上去"）
        for c in [base + (float(s["max"]) - base) * i / 40.0 for i in range(41)]:
            st.set_value("modulator", name, c, source="manual")
            dv = float(st.modulator_dev(name))
            if dv > peak_dev:
                peak_dev, peak_c = dv, c
        if peak_c is None:
            inert_at_param.append((name, "曲线上找不到正响应峰值（基线以上无增益）"))
            continue
        chosen = None
        for param, coef in rows:                    # 从弱到强：强系数容易钉在边界
            v_base = _at_dev(layer, param, name, 0.0)
            v_peak = _at_dev(layer, param, name, peak_dev)
            if abs(v_peak - v_base) <= 1e-9:
                continue                            # 该参数被别的行为/边界吃掉，换一条
            st.set_value("modulator", name, float(s["max"]), source="manual")
            top_dev = float(st.modulator_dev(name))
            v_top = _at_dev(layer, param, name, top_dev)
            chosen = (param, coef, peak_c, peak_dev, top_dev, v_base, v_peak, v_top)
            break
        if chosen is None:
            inert_at_param.append((name, "所有系数行在响应峰值处都不改变参数值"))
            continue
        param, coef, peak_c, pdev, tdev, vb, vp, vt = chosen
        ok = abs(vt - vb) < abs(vp - vb)
        if not ok:
            no_reversal.append((name, param))
        check(f"C {name}：峰 {pdev:+.3f}@{peak_c:.2f} → 顶格 {tdev:+.3f} 时"
              f" {param} 回落", ok,
              f"base={vb:.4f} peak={vp:.4f} top={vt:.4f} coef={coef:+.3f}")
    for name in sorted(st.modulator_names()):      # 复位到基线（本文件不写盘，纯粹卫生）
        st.set_value("modulator", name, float(specs[name]["baseline"]),
                     source="manual")
    check("C 汇总：每个调制器都在参数面上可观测", not inert_at_param, str(inert_at_param))
    check("C 汇总：全部 12 个调制器的过载支都传到参数", not no_reversal, str(no_reversal))


def _at_dev(layer, param, name, dev):
    return settled(layer, param, {f"hormone.{name}": float(dev)})


# ═══════ D 删边即失效（图上权威，参数值层面） ═══════
def test_D_graph_authority(kg, st, cfg, layer, targets, proj):
    print("\n── D 删靶点的调制边 → 该参数对激素彻底无感；加回来又恢复 ──")
    mirror = st.mirror_modulator
    n_by_target = {}
    for param in targets.values():
        n_by_target[param] = {k: v for k, v in layer.effects(param).items()
                              if k.startswith("hormone.")}
    covered = [p for p, rows in n_by_target.items() if rows]
    for short, param in sorted(targets.items()):
        rows = n_by_target[param]
        tgt = f"调制目标:{short}"
        before = dict(rows)
        dropped = 0
        for name, mid in mirror.items():
            if f"hormone.{name}" not in before:
                continue
            for rel in COEF_RELS:
                if kg.get_edge(mid, tgt, rel) is not None:
                    kg.remove_edge(mid, tgt, rel)
                    dropped += 1
        if not before:
            check(f"D {short}：无激素行（该靶点只受网络/张力驱动）", dropped == 0,
                  f"意外删了 {dropped} 条边")
            MS.ensure_modulator_subgraph(kg, st, cfg)
            MS.project_coefficients(kg, st, layer, cfg)
            continue
        sig = {k: 0.4 for k in before}          # 键本身就是 hormone.<名> 信号
        v_with = settled(layer, param, sig)
        MS.project_coefficients(kg, st, layer, cfg)
        rows_after = {k: v for k, v in layer.effects(param).items()
                      if k.startswith("hormone.")}
        v_without = settled(layer, param, sig)
        v_zero = settled(layer, param, {})
        MS.ensure_modulator_subgraph(kg, st, cfg)     # 出厂表重新种入（图被删=可复原）
        MS.project_coefficients(kg, st, layer, cfg)
        v_back = settled(layer, param, sig)
        check(f"D {short}：删 {dropped} 条边 → 激素行清空且参数无感（"
              f"{len(before)} 行 → {len(rows_after)}）",
              not rows_after and abs(v_without - v_zero) < 1e-9 and dropped > 0,
              f"{before} → {rows_after}; v {v_with:.4f}→{v_without:.4f}"
              f" (无信号 {v_zero:.4f})")
        check(f"D {short}：边加回来 → 效力恢复（往返可逆）",
              abs(v_back - v_with) < 1e-9 and
              {k: round(v, 6) for k, v in layer.effects(param).items()
               if k.startswith("hormone.")} ==
              {k: round(v, 6) for k, v in before.items()},
              f"{v_with:.4f} vs {v_back:.4f}")
    check(f"D 汇总：{len(covered)}/14 靶点确有激素系数边（拓扑覆盖度）",
          len(covered) >= 10, str(sorted(set(targets.values()) - set(covered))))


# ═══════ E 三处一致：targets ↔ 靶点节点 ↔ 层内参数 ═══════
def test_E_wiring(kg, cfg, layer, targets, proj):
    print("\n── E 目标表/靶点节点/调制层三处一致 ──")
    check("E 14 个目标参数互不相同（两个短名挤同一参数=有一个是假的）",
          len(set(targets.values())) == len(targets), str(targets))
    missing = [p for p in targets.values() if not layer.has(p)]
    check("E 每个目标参数都在调制层里（投影不硬造参数）", not missing, str(missing))
    bad_node = []
    for short, param in targets.items():
        n = kg.get_node(f"调制目标:{short}")
        if n is None or (n.extra_attrs or {}).get("param") != param:
            bad_node.append(short)
    check("E 每个靶点节点存在且 extra_attrs.param 与表一致", not bad_node, str(bad_node))
    orphans = [short for short in targets
               if kg.get_node(f"调制目标:{short}") is not None
               and not any(f"hormone.{n}" in layer.effects(targets[short])
                           for n in cfg["modulator_system"]["specs"])
               and not [e for e in kg.get_in_edges(f"调制目标:{short}")
                        if e.relation in COEF_RELS]]
    check("E 没有悬空靶点（无入边且无系数行的目标 = 装饰节点）", not orphans,
          str(orphans))
    check("E 投影读到的边数 == 层内激素行数（无静默丢边/凭空造行）",
          proj["projection"]["edges_read"]
          == sum(len([k for k in layer.effects(p) if k.startswith("hormone.")])
                 for p in set(targets.values())),
          f'{proj["projection"]["edges_read"]} vs '
          f'{sum(len([k for k in layer.effects(p) if k.startswith("hormone.")]) for p in set(targets.values()))}')


if __name__ == "__main__":
    kg, st, cfg, field, proj = build()
    layer = field.modulation
    targets = cfg["modulator_system"]["targets"]
    print(f"装配：{proj['projection']['edges_read']} 条调制边 → "
          f"{proj['applied']} 行系数（回收 {proj['removed']} 行）；"
          f"层内 {len(layer._specs)} 个参数")
    test_A_readers(targets)
    test_B_monotone(layer, cfg, targets)
    test_C_overload_reversal(kg, st, cfg, layer, targets)
    test_D_graph_authority(kg, st, cfg, layer, targets, proj)
    test_E_wiring(kg, cfg, layer, targets, proj)
    for d in TMP_KEEP:
        shutil.rmtree(d, ignore_errors=True)
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项:")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("P8 闸门全部通过：14 个认知场目标都是真旋钮（有读者、单调有界、")
    print("过载能削弱、图是权威）")
