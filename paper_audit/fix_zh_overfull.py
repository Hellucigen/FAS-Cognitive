# fix_zh_overfull.py — 消除中文稿 overfull（CJK+长 monospace 混排）
import io

P = r"E:\Project\Fascinator\FAS_Paper_I\main_zh.tex"
t = io.open(P, encoding="utf-8").read()

# 1. 宽容断行设置（CJK 混排标准做法）
if "\\emergencystretch" not in t:
    t = t.replace("\\usepackage{caption}",
                  "\\usepackage{caption}\n\\sloppy\n\\setlength{\\emergencystretch}{3em}", 1)

# 2. taxonomy 表长串拆行（与英文稿同款）
t = t.replace(
    r"\texttt{gather\_resource(oak\_log)} $\rightarrow$ \texttt{change:inventory:oak\_log:count\_increased} & 实质 & 挖掘产出物品 \\",
    r"\texttt{gather\_resource(oak\_log)} $\rightarrow$ \texttt{change:inventory:\newline oak\_log:count\_increased} & 实质 & 挖掘产出物品 \\")
t = t.replace(
    r"\texttt{gather\_resource(oak\_log)} $\rightarrow$ \texttt{change:block:oak\_log:disappeared} & 实质 & 挖掘移除方块 \\",
    r"\texttt{gather\_resource(oak\_log)} $\rightarrow$ \texttt{change:block:\newline oak\_log:disappeared} & 实质 & 挖掘移除方块 \\")
t = t.replace(
    r"\texttt{craft\_item(oak\_planks)} $\rightarrow$ \texttt{change:inventory:oak\_log:count\_decreased} & 实质 & 合成消耗原木 \\",
    r"\texttt{craft\_item(oak\_planks)} $\rightarrow$ \texttt{change:inventory:\newline oak\_log:count\_decreased} & 实质 & 合成消耗原木 \\")
t = t.replace(
    r"\texttt{craft\_item(oak\_planks)} $\rightarrow$ \texttt{change:inventory:stick:count\_decreased} & 实质 & 合成消耗木棍 \\",
    r"\texttt{craft\_item(oak\_planks)} $\rightarrow$ \texttt{change:inventory:\newline stick:count\_decreased} & 实质 & 合成消耗木棍 \\")
t = t.replace(
    r"\texttt{gather\_resource(oak\_log)} $\rightarrow$ \texttt{change:inventory:oak\_planks:count\_increased} & 实质、窗污染 & 下游合成效果归入上游采集 \\",
    r"\texttt{gather\_resource(oak\_log)} $\rightarrow$ \texttt{change:inventory:\newline oak\_planks:count\_increased} & 实质、窗污染 & 下游合成效果归入上游采集 \\")
t = t.replace(
    r"\texttt{gather\_resource(oak\_log)} $\rightarrow$ \texttt{change:inventory:stick:count\_increased} & 实质、窗污染 & 同上 \\",
    r"\texttt{gather\_resource(oak\_log)} $\rightarrow$ \texttt{change:inventory:\newline stick:count\_increased} & 实质、窗污染 & 同上 \\")

# 3. 表格宽度：与英文稿一致收窄
t = t.replace(r"\begin{tabular}{p{5.4cm}p{3.1cm}p{6.6cm}}",
              r"\begin{tabular}{p{4.7cm}p{2.7cm}p{7.2cm}}")
t = t.replace(r"\begin{tabular}{p{5.6cm}p{1.2cm}p{8.4cm}}",
              r"\begin{tabular}{p{5.0cm}p{0.9cm}p{8.6cm}}")

io.open(P, "w", encoding="utf-8").write(t)
print("zh overfull fixes applied")
