---
name: openakita/skills@ppt-creator
description: Create professional presentations using the Pyramid Principle methodology. Supports PPTX generation, Marp/Reveal.js Markdown slides, chart creation, speaker notes, and self-evaluation rubrics. Minimal intake form to rapid output workflow.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# PPT Creator — 专业演示文稿制作

## When to Use

- When the user needs制作商业演示文稿（汇报、路演、培训、产品介绍）
- 需要将非结构化的想法整理为逻辑清晰的幻灯片
- 需要GenerationIncludes数据图表的演示文稿
- 需要AutomaticGeneration演讲稿/备注
- 需要将 Markdown 转换为演示文稿格式（Marp / Reveal.js）
- 需要评估已有 PPT 的质量

---

## Prerequisites

### 必需工具

| 工具 | 用途 | install方式 |
|------|------|---------|
| Python ≥ 3.10 | RunGeneration脚本 | 系统预装 |
| `python-pptx` | Generation PPTX 文件 | `pip install python-pptx` |

### 可选工具

| 工具 | 用途 | install方式 |
|------|------|---------|
| `marp-cli` | Markdown → PPT/PDF | `npm install -g @marp-team/marp-cli` |
| `matplotlib` | 数据图表Generation | `pip install matplotlib` |
| `Pillow` | 图片handle | `pip install Pillow` |
| `plotly` | 交互式图表 | `pip install plotly kaleido` |

### 验证install

```bash
python -c "from pptx import Presentation; print('python-pptx OK')"
marp --version  # 可选
```

---

## Instructions

### 核心方法论：金字塔原理（Pyramid Principle）

本技能采用 Barbara Minto 的金字塔原理组织幻灯片结构：

```
           ┌──────────────┐
           │  核心结论     │  ← 1 句话概括
           └──────┬───────┘
        ┌─────────┼─────────┐
   ┌────┴────┐ ┌──┴───┐ ┌──┴────┐
   │ 论点 A  │ │论点 B│ │论点 C │  ← 3 个支撑论点
   └────┬────┘ └──┬───┘ └──┬────┘
     证据1-3    证据1-3   证据1-3   ← 每个论点 2-3 条证据
```

**原则：**
1. **结论先行** — 每页幻灯片的标题就Yes该页的结论
2. **以上统下** — 上层观点Yes下层内容的概括
3. **归类分组** — 同层内容属于同一逻辑范畴
4. **逻辑递进** — 同层内容有明确的排列顺序（时间、结构、Importance）

### 演示文稿结构模板

| 幻灯片 | 内容 | 时间 |
|--------|------|------|
| 封面 | 标题、副标题、演讲者、日期 | — |
| 目录/议程 | 演讲结构概览 | 30s |
| 背景/问题 | 现状描述、痛点阐述 | 45-60s |
| 核心论点 1 | 第一个支撑论点 + 数据/案例 | 45-60s |
| 核心论点 2 | 第二个支撑论点 + 数据/案例 | 45-60s |
| 核心论点 3 | 第三个支撑论点 + 数据/案例 | 45-60s |
| 方案/建议 | 具体行动方案 | 45-60s |
| Timeline/路线图 | 实施计划 | 45-60s |
| 总结 | 回扣核心结论、关键要点 | 30s |
| Q&A / 致谢 | 联系方式、讨论时间 | — |

---

## Workflows

### Workflow 1: Quickcreate（Minimal Intake Form）

**步骤 1 — 收集关键信息**

向用户询问以下 5 个要素（尽量精简）：

| # | 问题 | 示例回答 |
|---|------|---------|
| 1 | 演讲主题Yes什么？ | "Q4 产品路线图汇报" |
| 2 | 目标受众Yes谁？ | "公司manage层" |
| 3 | 核心结论/诉求？ | "需要追加 30% 研发预算" |
| 4 | 有哪些关键数据或论据？ | "用户增长 200%、竞品Analyze、技术债" |
| 5 | 幻灯片数量偏好？ | "10-15 页" |

如果用户只给了主题，根据常识和金字塔原理自行推导其余要素，并在Generation前确认。

**步骤 2 — 构建金字塔结构**

Based on收集的信息，输出结构大纲：

```
核心结论：追加 30% 研发预算以支撑用户增长
├── 论点A：用户量爆发增长（200%）带来系统压力
│   ├── 证据：月活跃用户趋势图
│   ├── 证据：服务器负载数据
│   └── 证据：用户投诉工单增长
├── 论点B：竞品正在加速投入
│   ├── 证据：竞品融资/招聘动态
│   ├── 证据：功能对比矩阵
│   └── 证据：市场份额变化
└── 论点C：技术债已影响迭代速度
    ├── 证据：发布周期对比
    ├── 证据：Bug 趋势
    └── 证据：开发者满意度调查
```

**步骤 3 — Generation幻灯片内容**

逐页编写：
- **标题**：一句Full的结论性陈述（非主题词）
- **正文**：3-5 个要点，每点 ≤ 15 字
- **图表/数据**：识别需要数据可视化的页面
- **备注**：45-60 秒的演讲脚本

**步骤 4 — 输出文件**

根据When the user needs选择输出格式（见 Output Format Partial）。

---

### Workflow 2: 数据图表Generation

**Supports的图表类型**

| 图表 | 适用场景 | 库 |
|------|---------|-----|
| 柱状图 | 对比数据 | matplotlib / plotly |
| 折线图 | 趋势变化 | matplotlib / plotly |
| 饼图 | 占比分布 | matplotlib / plotly |
| 散点图 | 相关性Analyze | matplotlib / plotly |
| 瀑布图 | 增减分解 | plotly |
| 热力图 | 矩阵数据 | matplotlib / seaborn |
| 雷达图 | 多维对比 | matplotlib |

**图表设计原则**

1. **标题即结论** — 图表标题描述洞察而非数据（"销售额同比增长 40%" vs "Q4 销售数据"）
2. **极简配色** — Use 2-3 种颜色，重点数据用强调色
3. **去除噪音** — delete网格线、多余边框、3D 效果
4. **标注关键值** — 在图表上直接标注最重要的数据点
5. **适当留白** — 图表不超过幻灯片面积的 60%

**Generation图表的 Python 示例**

```python
import matplotlib.pyplot as plt
import matplotlib

matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
matplotlib.rcParams['axes.unicode_minus'] = False

fig, ax = plt.subplots(figsize=(10, 6))
categories = ['Q1', 'Q2', 'Q3', 'Q4']
values = [120, 180, 240, 350]
colors = ['#e0e0e0', '#e0e0e0', '#e0e0e0', '#4A90D9']

ax.bar(categories, values, color=colors, width=0.6)
ax.set_title('Q4 营收突破 350 万，环比增长 46%', fontsize=16, fontweight='bold', pad=20)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)

for i, v in enumerate(values):
    ax.text(i, v + 5, f'{v}万', ha='center', fontsize=12)

plt.tight_layout()
plt.savefig('chart_revenue.png', dpi=200, bbox_inches='tight')
```

---

### Workflow 3: 演讲备注Generation

为每张幻灯片Generation 45-60 秒的演讲稿：

**规则：**
1. 开头：过渡语连接上一页（"接下来看...""那么..."）
2. 中间：用口语化表达阐述幻灯片要点
3. 结尾：用一句话总结本页核心信息
4. 字数：中文约 150-200 字 ≈ 45-60 秒
5. 标注停顿点：`[停顿]`、`[看向观众]`
6. 标注交互点：`[提问]`、`[举手调查]`

**示例：**

```
那么我们来看第一个关键发现。[停顿]

过去一个季度，我们的月活跃用户从 50 万增长到了 150 万，
同比增长了 200%。[看向观众] 这Yes一个非常可喜的数字，
但同时也带来了挑战。

大家可以看到右边的图表，随着用户量的激增，
我们的服务器平均响应时间从 200 毫秒上升到了 800 毫秒，
高峰期甚至出现过 5 秒的延迟。[停顿]

这意味着什么？如果我们不尽快扩容，
用户体验将会显著下降，这直接影响留存率。
```

---

### Workflow 4: 自评打分

Generation完成后，用以下 rubric 评估质量：

| 维度 | 满分 | 评分标准 |
|------|------|---------|
| 结构逻辑 | 25 | 金字塔结构Full、论点 MECE |
| 视觉设计 | 20 | 一致的配色、排版、留白 |
| 数据可视化 | 20 | 图表清晰、标题即结论 |
| 演讲备注 | 15 | 流畅、口语化、时间合适 |
| 受众适配 | 10 | 语言风格匹配目标受众 |
| 行动导向 | 10 | 明确的 CTA 和下一步 |

目标分数：≥ 80/100。低于 80 分时Automatic修订弱项。

---

## Output Format

### 格式 1: PPTX 文件（python-pptx）

Use `python-pptx` Generation标准 PowerPoint 文件：

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)

slide_layout = prs.slide_layouts[6]  # blank layout
slide = prs.slides.add_slide(slide_layout)

title_shape = slide.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.7), Inches(1.2))
tf = title_shape.text_frame
tf.text = "Q4 营收同比增长 46%，超额完成目标"
tf.paragraphs[0].font.size = Pt(28)
tf.paragraphs[0].font.bold = True

notes_slide = slide.notes_slide
notes_slide.notes_text_frame.text = "演讲备注内容..."

prs.save('presentation.pptx')
```

### 格式 2: Marp Markdown

```markdown
---
marp: true
theme: default
paginate: true
header: "公司名 | Q4 汇报"
footer: "机密"
---

# Q4 产品路线图汇报
### 需要追加 30% 研发预算以支撑用户增长

**演讲者** | 日期

---

## 用户量爆发增长带来系统压力

- 月活从 50 万 → 150 万（+200%）
- 服务器响应时间 200ms → 800ms
- 用户投诉工单增长 3 倍

![bg right:40%](chart_growth.png)

---
```

转换命令：

```bash
marp slides.md --pptx -o presentation.pptx
marp slides.md --pdf -o presentation.pdf
marp slides.md --html -o presentation.html
```

### 格式 3: Reveal.js HTML

```html
<!DOCTYPE html>
<html>
<head>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@4/dist/reveal.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@4/dist/theme/white.css">
</head>
<body>
<div class="reveal">
    <div class="slides">
        <section>
            <h1>Q4 产品路线图汇报</h1>
            <p>需要追加 30% 研发预算以支撑用户增长</p>
            <aside class="notes">演讲备注...</aside>
        </section>
        <section>
            <h2>用户量爆发增长带来系统压力</h2>
            <ul>
                <li>月活从 50 万 → 150 万（+200%）</li>
                <li>服务器响应时间 200ms → 800ms</li>
            </ul>
            <aside class="notes">演讲备注...</aside>
        </section>
    </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/reveal.js@4/dist/reveal.js"></script>
<script>Reveal.initialize({ hash: true });</script>
</body>
</html>
```

### 格式 4: PNG 图表

每张数据图表单独输出为 PNG，分辨率 ≥ 200 DPI。

---

## Common Pitfalls

### 1. 标题写成了"主题词"而非"结论"

**错误**：幻灯片标题 "Q4 销售数据"
**正确**：幻灯片标题 "Q4 销售额同比增长 40%，超额完成年度目标"

### 2. 每页内容过多

每张幻灯片不超过 5 个要点，每个要点不超过 15 个汉字。当内容过多时拆分为多页。

### 3. 配色不一致

整套 PPT Use同一配色方案。Recommendations的安全配色：
- 主色 1 种 + 强调色 1 种 + 灰色系
- 避免Use超过 4 种颜色

### 4. 图表与结论脱节

每张图表必须支撑其所在页的标题结论。如果图表无法直接证明标题，要么修改图表要么修改标题。

### 5. 忽略受众水平

- 给manage层：聚焦商业影响、省略技术细节
- 给技术团队：可以深入架构、代码层面
- 给客户：突出价值和收益

### 6. 中文字体兼容

Use python-pptx 时确保指定中文字体：

```python
from pptx.util import Pt
paragraph.font.name = 'Microsoft YaHei'
```

### 7. 演讲时间估算不准

中文演讲速度约 200-250 字/分钟。10 页幻灯片 × 1 分钟/页 = 10 分钟演讲。如果用户要求 30 分钟演讲，需要约 25-30 页内容。

---

## EXTEND.md 扩展

用户可在技能同目录下create `EXTEND.md` 添加：
- 公司 PPT 模板路径
- 品牌配色和字体
- 常用 PPT 结构模板
- 特定行业/场景的幻灯片范例
