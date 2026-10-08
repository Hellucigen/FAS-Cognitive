# fix_campaign_print.py — 修复 campaign() 统计行的 print 引用错误
import io
import ast

p = r"scripts\run_exp_reldir.py"
t = io.open(p, encoding="utf-8").read()

old = '''            out.append({"input_cond": ic, "metric": met, "n": n,
                        "mean_F": round(st.mean(xa), 4),
                        "mean_B": round(st.mean(xb), 4),
                        "p": round(p, 6) if p is not None else None,
                        "effect_r": round(r_eff, 3)})
            print(f"[paired] {ic} {met}: F={row.get('mean_F')} "
                  f"B={row.get('mean_B')} p={row.get('p')} "
                  f"r={row.get('effect_r')} keys={sorted(row)}")'''
new = '''            stat_row = {"input_cond": ic, "metric": met, "n": n,
                        "mean_F": round(st.mean(xa), 4),
                        "mean_B": round(st.mean(xb), 4),
                        "p": round(p, 6) if p is not None else None,
                        "effect_r": round(r_eff, 3)}
            out.append(stat_row)
            print(f"[paired] {ic} {met}: F={stat_row['mean_F']} "
                  f"B={stat_row['mean_B']} p={stat_row['p']} "
                  f"r={stat_row['effect_r']}")'''
assert old in t, "anchor"
t = t.replace(old, new, 1)
io.open(p, "w", encoding="utf-8").write(t)
ast.parse(t)
print("fixed; syntax OK")
