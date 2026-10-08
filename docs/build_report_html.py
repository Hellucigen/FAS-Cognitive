#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""build_report_html.py — 把技术报告 Markdown 打包成单文件 HTML（图表渲染版）。

用途
----
`docs/技术报告_Fascinator.md` 是报告的**唯一真源**。本脚本把它嵌进一个
自包含的 HTML 页面：Markdown 用 marked 渲染，架构图/流程图用 mermaid 渲染，
阅读体验比纯文本好，且可以直接双击用浏览器打开。

用法
----
    python docs/build_report_html.py

输出 `docs/技术报告_Fascinator.html`。

关于离线
--------
marked / mermaid 走 CDN（jsDelivr）。无网络时页面仍会显示报告全文，只是
mermaid 代码块以源码形式呈现（可读，只是没有图形）——不会白屏。
Markdown 原文件在 VS Code / JetBrains IDE / GitHub 上都能原生渲染 mermaid，
所以离线场景直接看 .md 即可。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MD_PATH = os.path.join(HERE, "技术报告_Fascinator.md")
OUT_PATH = os.path.join(HERE, "技术报告_Fascinator.html")

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fascinator 技术报告</title>
<style>
  :root {
    --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --border: #d1d9e0;
    --code-bg: #f6f8fa; --accent: #0969da; --th-bg: #f6f8fa;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --border: #3d444d;
      --code-bg: #161b22; --accent: #4493f8; --th-bg: #161b22;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 0; background: var(--bg); color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                 "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    line-height: 1.75; font-size: 16px;
  }
  .wrap { max-width: 1000px; margin: 0 auto; padding: 48px 28px 120px; }
  h1, h2, h3, h4 { line-height: 1.3; margin-top: 1.8em; margin-bottom: .6em; font-weight: 600; }
  h1 { font-size: 2.1em; border-bottom: 2px solid var(--border); padding-bottom: .3em; margin-top: 0; }
  h2 { font-size: 1.55em; border-bottom: 1px solid var(--border); padding-bottom: .25em; }
  h3 { font-size: 1.22em; }
  h4 { font-size: 1.05em; color: var(--muted); }
  p { margin: .8em 0; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  code {
    background: var(--code-bg); padding: .18em .4em; border-radius: 5px;
    font-family: ui-monospace, SFMono-Regular, "Cascadia Code", Consolas, monospace;
    font-size: .88em;
  }
  pre {
    background: var(--code-bg); padding: 14px 16px; border-radius: 8px;
    overflow-x: auto; border: 1px solid var(--border);
  }
  pre code { background: none; padding: 0; font-size: .86em; line-height: 1.55; }
  blockquote {
    margin: 1em 0; padding: .4em 1em; border-left: 4px solid var(--border);
    color: var(--muted); background: var(--code-bg); border-radius: 0 6px 6px 0;
  }
  table { border-collapse: collapse; width: 100%; margin: 1.2em 0; font-size: .92em; display: block; overflow-x: auto; }
  th, td { border: 1px solid var(--border); padding: 8px 12px; text-align: left; vertical-align: top; }
  th { background: var(--th-bg); font-weight: 600; white-space: nowrap; }
  tr:nth-child(even) td { background: color-mix(in srgb, var(--code-bg) 45%, transparent); }
  hr { border: none; border-top: 1px solid var(--border); margin: 2.5em 0; }
  .mermaid {
    background: var(--code-bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 20px; margin: 1.4em 0; text-align: center; overflow-x: auto;
  }
  .mermaid svg { max-width: 100%; height: auto; }
  /* 顶部目录悬浮按钮 */
  #tocbtn {
    position: fixed; right: 22px; bottom: 22px; z-index: 99;
    background: var(--accent); color: #fff; border: none; border-radius: 999px;
    padding: 11px 20px; font-size: 14px; cursor: pointer; opacity: .92;
    box-shadow: 0 3px 14px rgba(0,0,0,.22);
  }
  #tocbtn:hover { opacity: 1; }
  #toast {
    position: fixed; left: 50%; transform: translateX(-50%); top: 18px; z-index: 100;
    background: #b42318; color: #fff; padding: 10px 18px; border-radius: 8px;
    font-size: 14px; display: none; max-width: 88vw;
  }
</style>
</head>
<body>
<div id="toast"></div>
<div class="wrap" id="content"><p style="color:#888">正在渲染报告…</p></div>
<button id="tocbtn" onclick="document.getElementById('content').scrollIntoView({behavior:'smooth'})">↑ 回到顶部</button>

<script type="text/plain" id="md-source">__MD__</script>

<script type="module">
const toast = (m) => {
  const t = document.getElementById('toast');
  t.textContent = m; t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 6000);
};

const mdText = document.getElementById('md-source').textContent;

let markedOk = false, mermaidOk = false;

// ── 1. 渲染 Markdown ──
try {
  const { marked } = await import('https://cdn.jsdelivr.net/npm/marked@12/lib/marked.esm.js');
  marked.setOptions({ gfm: true, breaks: false });
  document.getElementById('content').innerHTML = marked.parse(mdText);
  markedOk = true;
} catch (e) {
  // CDN 不可达：退化为纯文本展示（保留全文，只是不排版）
  const pre = document.createElement('pre');
  pre.style.whiteSpace = 'pre-wrap';
  pre.textContent = mdText;
  document.getElementById('content').innerHTML = '';
  document.getElementById('content').appendChild(pre);
  toast('无法加载 Markdown 渲染库（CDN 不可达），已退化为纯文本。联网后刷新即可。');
}

// ── 2. 渲染 Mermaid 图 ──
if (markedOk) {
  try {
    const mermaid = (await import('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs')).default;
    const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    mermaid.initialize({ startOnLoad: false, theme: dark ? 'dark' : 'default',
                         securityLevel: 'loose', flowchart: { htmlLabels: true } });
    const blocks = document.querySelectorAll('pre > code.language-mermaid');
    for (let i = 0; i < blocks.length; i++) {
      const src = blocks[i].textContent;
      const holder = document.createElement('div');
      holder.className = 'mermaid';
      try {
        const { svg } = await mermaid.render('mmd-' + i, src);
        holder.innerHTML = svg;
      } catch (err) {
        holder.style.textAlign = 'left';
        holder.innerHTML = '<pre style="margin:0"><code></code></pre>';
        holder.querySelector('code').textContent = src;
      }
      blocks[i].closest('pre').replaceWith(holder);
    }
    mermaidOk = true;
  } catch (e) {
    toast('无法加载 Mermaid（CDN 不可达），架构图以源码形式展示。');
  }
}
</script>
</body>
</html>
"""


def main():
    if not os.path.exists(MD_PATH):
        print(f"[错误] 找不到报告源文件: {MD_PATH}", file=sys.stderr)
        return 1

    with open(MD_PATH, "r", encoding="utf-8") as f:
        md = f.read()

    # 嵌在 <script type="text/plain"> 里：只需排除 </script 序列
    if "</script" in md.lower():
        print("[错误] 报告含 </script> 字面量，无法安全嵌入 HTML", file=sys.stderr)
        return 1

    html = TEMPLATE.replace("__MD__", md)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = os.path.getsize(OUT_PATH) / 1024
    print(f"[完成] {OUT_PATH}  ({size_kb:.0f} KB, 源 {len(md)} 字符)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
