# FASCINATOR 前端风格规范

> 提取自 `index.html` 的"工业分析仪表盘"风格,用于统一其他前端。
> 关键词:**几何结构 · 数据模块 · 浅色基底 · 橙 + 青绿点缀 · 等宽科技字体**

---

## 1. 设计理念

- **工业控制台 / 实验室仪表盘**气质:一切内容装进带边框的"模块"卡片,像机架设备。
- **浅色基底 + 细边框分割**,几乎不用阴影和圆角(全局圆角仅 `2px`,唯一阴影在模态框)。
- **双强调色系统**:橙色 = 主要动作 / 用户侧 / 高激活;青绿 = 次要 / 系统侧 / 焦点态。
- **等宽字体做"数据语言"**(数值、标签、按钮),半压缩无衬线做"界面语言"(标题、正文)。
- 大量 **uppercase + letter-spacing** 制造仪器铭牌感;装饰性技术标签(`SYS::READY`、`GRID::ACTIVE`)点缀四角。
- 背景有**网格纹理**(页面 32px 线网格 / 画布区 24px 点阵),强化坐标仪感。

---

## 2. 色彩系统(CSS 变量,直接复制)

```css
:root {
  /* 基底 */
  --bg: #f4f4f4;          /* 页面背景 */
  --bg2: #e8e8e8;
  --panel: #fafafa;       /* 模块卡片背景 */
  --panel2: #f0f0f0;
  --border: #d0d0d0;      /* 常规边框 */
  --border2: #b0b0b0;
  /* 文字 */
  --text: #1a1a1a;        /* 主文字 */
  --text2: #3a3a3a;       /* 次文字 */
  --text-dim: #888888;    /* 弱化/标签 */
  /* 强调色 */
  --orange: #ff6b00;      /* 主强调:动作/用户/高激活 */
  --orange-dim: #ff8c3a;
  --teal: #00d2d3;        /* 次强调:焦点/系统/成功侧 */
  --teal-dim: #009a9b;    /* teal 的可读版本(浅底上做文字) */
  /* 语义色 */
  --red: #e03030;
  --green: #10a860;
  /* 字体 */
  --mono: 'Share Tech Mono', 'JetBrains Mono', 'Consolas', monospace;
  --sans: 'Barlow Semi Condensed', 'Noto Sans SC', sans-serif;
  --radius: 2px;          /* 全局唯一圆角 */
}
```

字体引入(Google Fonts):

```html
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Barlow+Semi+Condensed:wght@400;500;600;700&display=swap" rel="stylesheet">
```

### 强调色的浅色衍生底(用于 pill / 标签 / hover)

| 用途 | 背景 | 文字/边框 |
|---|---|---|
| 橙色 pill | `#fff0e5` | 文字 `--orange`,边框 `rgba(255,107,0,0.25)` |
| 青绿 pill | `#e5fafa` | 文字 `--teal-dim`,边框 `rgba(0,210,211,0.25)` |
| 橙 hover 底 | `#fff5ee` | — |
| 青 hover 底 | `#f0fefe` | — |

---

## 3. 字体排版规范

| 层级 | 字体 | 字号 | 附加 |
|---|---|---|---|
| Logo / 大标题 | sans | 16px / 700 | letter-spacing 6px, uppercase |
| 模块标题 | sans | 10px / 600 | letter-spacing 2px, uppercase, 灰底头部 |
| 区块小标题(▸ 开头) | sans | 9px / 600 | letter-spacing 1~2px, uppercase, teal-dim 或 orange |
| 按钮文字 | mono | 8–9px | uppercase, letter-spacing 0.5–1px |
| 表单标签 | mono | 8px | uppercase, `--text-dim` |
| 输入框文字 | mono | 10–11px | — |
| 数值/状态读数 | mono | 8–10px | `--text-dim` 或强调色 |
| 正文(聊天/回答) | sans | 11–13px | line-height 1.5–1.7 |
| 装饰性技术标签 | mono | 8px | letter-spacing 1–2px, `#c0c0c0` |

排版口诀:**界面文字全部大写 + 加字距;数字和代码一律等宽;中文正文是唯一的"正常文字"。**

---

## 4. 布局骨架

```
┌──────────────────────────────────────────────┐
│ TOPBAR 40px:系统标签│LOGO│状态灯│操作按钮组│统计 │
├───────────┬──────────────────────┬────────────┤
│ 左栏       │ 中央画布区            │ 右栏        │
│ 264px     │ 1fr                  │ 296px      │
│ (模块卡片) │ 网格底纹+浮动信息层    │ (输入+Tabs) │
├───────────┴──────────────────────┴────────────┤
│ CONSOLE BAR 32px(深色,可展开到 170px)          │
└──────────────────────────────────────────────┘
```

```css
body { height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
#main {
  display: grid;
  grid-template-columns: 264px 1fr 296px;
  grid-template-areas: "left graph right";
  flex: 1; overflow: hidden; min-height: 0;
}
```

- 顶栏 `#fafafa` + 底边框,元素之间用 **1px 竖线分隔符**(`.sep`)或 `border-right` 切割。
- 页面背景网格纹理:

```css
body {
  background-image:
    linear-gradient(#e0e0e0 1px, transparent 1px),
    linear-gradient(90deg, #e0e0e0 1px, transparent 1px);
  background-size: 32px 32px;
  background-position: -1px -1px;
}
```

- 画布区点阵纹理:

```css
background-image: radial-gradient(circle, #ddd 1px, transparent 1px);
background-size: 24px 24px; opacity: 0.35;
```

---

## 5. 组件规范

### 5.1 模块卡片(核心容器)

```css
.module {
  background: #fafafa;
  border: 1px solid #d5d5d5;
  display: flex; flex-direction: column; overflow: hidden;
}
.module-header {           /* 头部:灰底 + 小圆点 + 大写标题 */
  font-family: var(--sans); font-size: 10px; font-weight: 600;
  letter-spacing: 2px; text-transform: uppercase;
  padding: 8px 12px; border-bottom: 1px solid #e0e0e0;
  background: #f5f5f5; display: flex; align-items: center; gap: 8px;
}
.module-header .dot { width: 6px; height: 6px; }  /* orange 或 teal */
.module-body { padding: 10px 12px; overflow-y: auto; flex: 1; }
```

### 5.2 按钮(扁平 + 1px 边框,无圆角)

```css
.btn {
  font-family: var(--mono); font-size: 9px; padding: 5px 12px;
  border: 1px solid #aaa; background: #fafafa; color: var(--text);
  cursor: pointer; text-transform: uppercase; letter-spacing: 0.5px;
  transition: all 0.15s;
}
.btn:hover { border-color: var(--text); background: #eee; }
.btn.primary { border-color: var(--orange); color: var(--orange); }
.btn.primary:hover { background: #fff5ee; }
.btn.teal { border-color: var(--teal); color: var(--teal-dim); }
.btn.teal:hover { background: #f0fefe; }
.btn.danger:hover { border-color: var(--red); color: var(--red); }
.btn-sm { font-size: 8px; padding: 3px 8px; border-color: #ccc; color: #555; }
```

实心按钮只有一种(主提交按钮,橙色填充):

```css
#nlp-submit {
  background: var(--orange); border: 1px solid var(--orange); color: #fff;
  font-family: var(--sans); font-weight: 600; letter-spacing: 2px;
  text-transform: uppercase; padding: 7px; transition: all 0.15s;
}
#nlp-submit:hover { background: #e55d00; }        /* 加深 10% */
#nlp-submit:disabled { background: #ccc; border-color: #ccc; color: #999; }
```

### 5.3 输入框 / 表单

```css
input, select, textarea {
  background: #fff; border: 1px solid #ccc; color: var(--text);
  font-family: var(--mono); font-size: 10px; padding: 4px 6px;
  outline: none; transition: border 0.15s; border-radius: 0;
}
input:focus, select:focus, textarea:focus { border-color: var(--teal); }  /* 焦点=青绿边框 */
```

- 表单行:`display:flex; gap:6px; margin-bottom:5px;`,左侧 8px 等宽大写标签。
- 滑杆 `accent-color: var(--orange);`,右侧橙色等宽数值显示(`0.50`)。

### 5.4 Pill / 标签徽章

```css
.pill {
  font-family: var(--mono); font-size: 8px; padding: 2px 8px;
  border-radius: 10px; text-transform: uppercase; letter-spacing: 0.5px;
}
.pill-orange { background: #fff0e5; color: var(--orange); border: 1px solid rgba(255,107,0,0.25); }
.pill-teal   { background: #e5fafa; color: var(--teal-dim); border: 1px solid rgba(0,210,211,0.25); }
.pill-gray   { background: #eee; color: #666; border: 1px solid #ddd; }
```

分类徽章(知识类型):语义 `#e0f7fa/#00838f`、情景 `#e8f5e9/#2e7d32`、程序 `#ede7f6/#4527a0`。

### 5.5 Tab 页签

```css
.tab-btn {
  font-family: var(--mono); font-size: 8px; padding: 7px 10px;
  background: transparent; border: none; border-bottom: 2px solid transparent;
  color: #999; text-transform: uppercase; letter-spacing: 1px; white-space: nowrap;
}
.tab-btn.active { color: var(--orange); border-bottom-color: var(--orange); }
```

### 5.6 开关(Toggle)

```css
.toggle-switch { position: relative; width: 36px; height: 18px; }
.toggle-slider { background: #ccc; border-radius: 18px; transition: 0.3s; }
.toggle-slider:before { /* 14px 白色圆点 */ }
.toggle-switch input:checked + .toggle-slider { background: var(--teal); }
```

### 5.7 滚动条(极细)

```css
::-webkit-scrollbar { width: 3px; }
::-webkit-scrollbar-thumb { background: #ccc; }  /* 深色区用 #555 */
```

### 5.8 Toast(深色底部浮层)

```css
#toast {
  position: fixed; bottom: 44px; left: 50%;
  background: #2a2a2a; border: 1px solid #555; color: #eee;
  padding: 8px 20px; font-family: var(--mono); font-size: 10px;
  opacity: 0; transition: opacity 0.3s, transform 0.3s;
}
#toast.success { border-left: 3px solid var(--green); }
#toast.error   { border-left: 3px solid var(--red); }
```

### 5.9 模态框(唯一允许阴影的地方)

```css
.modal {
  background: #fafafa; border: 1px solid #ccc;
  padding: 20px 24px; min-width: 360px; max-width: 440px;
  box-shadow: 0 8px 40px rgba(0,0,0,0.15);
}
/* 遮罩 rgba(0,0,0,0.5);标题橙色大写;主按钮橙色实心 */
```

### 5.10 深色控制台条(底部)

- 收起 32px / 展开 170px,`background:#2a2a2a`,顶边框 `#555`。
- 提示符橙色 `FASCINATOR>`,日志青绿输入/浅灰输出,时间戳 `#666`。
- 右侧装饰文字 `SYS::READY`(灰 `#555`,字距 2px)。

### 5.11 列表行(通用模式)

```css
.list-item {
  padding: 5px 0; border-bottom: 1px solid #eee;  /* 用细分隔线,不用卡片 */
}
.list-item:hover { background: rgba(255,107,0,0.03~0.04); }  /* 极淡橙 hover */
```

左侧引用条模式(高亮信息块):

```css
.note-orange { background: #fff8f2; border-left: 2px solid var(--orange); }
.note-teal   { background: #f0fefe; border-left: 2px solid var(--teal); }
```

### 5.12 状态指示灯

```css
.blip { width: 8px; height: 8px; }
.blip.online { background: var(--green); box-shadow: 0 0 6px rgba(16,168,96,0.4); }
.blip.active { background: var(--orange); animation: blip-pulse 1.2s infinite; }
```

---

## 6. 图谱 / 数据可视化配色

**节点激活热度(heat map,由低到高):**

| 状态 | 填充色 | 区间 |
|---|---|---|
| DORMANT | `#e0e0e0` | < 0.05 |
| LOW | `#bbdefb`(浅蓝) | 0.05–0.3 |
| MID | `#80deea`(浅青) | 0.3–1.0 |
| HIGH | `#ffab91`(浅橙) | 1.0–2.0 |
| PEAK | `#ef9a9a`(浅红) | > 2.0 |

**节点类型描边色:** 语义 = `#ff6b00`(橙)、情景 = `#00c853`(绿)、程序 = `#7c4dff`(紫)。

**激活光晕:** 激活 > 0.3 时外圈 `rgba(255,107,0,0.15)` 或 `rgba(0,210,211,0.15)`。

**边:** 低激活灰绿 `rgba(0,150,160,α)`,高激活青绿;hover 变 `#00828c`。边标签 8px 等宽灰。

**Top-K 激活数值色阶:** `>2 红 #e03030 / >1 橙 #ff6b00 / >0.3 青 #009a9b / 其余蓝 #42a5f5`(激活进度条同色,高度 3px)。

---

## 7. 交互与动效规范

- 全局过渡 `transition: all 0.15s`(按钮/输入);Toast/开关 0.3s。
- Hover 一律**只变边框色 + 极淡底色**,不变形、不放字重。
- 焦点 = 青绿边框(`--teal`),不用系统默认 outline。
- 唯一动画:状态灯脉冲 `blip-pulse`(box-shadow 呼吸,1.2s 循环)。
- 图标全部用** Unicode 几何/箭头字符**:▸ ◂ ◈ ● ○ ▶ ■ ⟳ ⚙ ◆,不引图标库。

---

## 8. 文案语气

- 界面标签全部**英文大写**(CREATE / UPDATE / DELETE / QUEUE EMPTY)。
- 提示、占位符用中文("输入节点名…"、"等待输入…")。
- 装饰字符串用 `SYS::` / `GRID::` 前缀 + 大写。
- 按钮动作用动词原形;状态用 `ONLINE / STANDBY / DIFFUSING / OFF`。

---

## 9. 快速核对清单(给新页面统一风格时逐项检查)

- [ ] 引入 Share Tech Mono + Barlow Semi Condensed
- [ ] 挂上 `:root` 变量,基底 `#f4f4f4`,卡片 `#fafafa` + 1px `#d5d5d5` 边框
- [ ] body 加 32px 线网格背景;大面积内容区加 24px 点阵
- [ ] 圆角:容器 0(或 2px),pill 10px,开关 18px —— 此外不再有圆角
- [ ] 阴影:只有模态框有;其余全靠边框
- [ ] 所有按钮/标签大写 + 等宽字体 + 字距
- [ ] 输入焦点 = 青绿边框;主按钮 = 橙色;hover = 边框加深 + 淡色底
- [ ] 分隔用 1px 细线(`#eee`–`#e0e0e0`),不用卡片套卡片
- [ ] 滚动条 3px 细条
- [ ] 状态灯 / toast / 深色控制台条保持同款
