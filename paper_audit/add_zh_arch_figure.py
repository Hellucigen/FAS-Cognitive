# add_zh_arch_figure.py — 把英文稿的 TikZ 架构图移植到中文稿
import io
import re

en = io.open(r"E:\Project\Fascinator\FAS_Paper_I\main.tex", encoding="utf-8").read()
m = re.search(r"\\begin\{figure\}\[t\]\s*\n\\centering\s*\n\\begin\{tikzpicture\}.*?\\label\{fig:arch\}\s*\n\\end\{figure\}",
              en, re.S)
assert m, "tikz block not found in en"
block = m.group(0)
block = block.replace(
    "\\caption{FAS data flow. Solid blocks are mechanisms measured in this paper; orange blocks are the three experience stores whose functional roles are tested. The scalar emotion-modulation pathway (arousal/stress $\\rightarrow$ emission gain) feeds the spreading block and is measured in Section~\\ref{sec:dyn}. All flows correspond to executed code paths.}",
    "\\caption{FAS 数据流。实心块为本文测量的机制；橙色块为功能角色受检验的三个经验存储。标量情绪调制通路（唤醒/压力 $\\rightarrow$ 发射增益）馈入传播块，于第~\\ref{sec:dyn} 节测量。全部数据流对应可执行代码路径。}")

zh_path = r"E:\Project\Fascinator\FAS_Paper_I\main_zh.tex"
zh = io.open(zh_path, encoding="utf-8").read()
anchor = "\\begin{table}[t]\n\\centering\\small\n\\caption{本文涉及的系统组件"
assert anchor in zh, "zh table anchor missing"
zh = zh.replace(anchor, block + "\n\n" + anchor, 1)
io.open(zh_path, "w", encoding="utf-8").write(zh)
print("zh arch figure inserted, block chars:", len(block))
