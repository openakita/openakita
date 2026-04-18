---
name: openakita/skills@wechat-article
description: Create and format WeChat Official Account (公众号) articles with proper Markdown-to-WeChat HTML conversion, rich formatting, cover image guidance, and both API and manual publishing workflows.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# WeChat Official Account文章发布助手

专为微信公众号内容创作和发布打造的技能，提供从写作到排版到发布的全流程支持，包括 Markdown 转换、富文本样式、封面设计和摘要生成。

## When to Use

- 撰写公众号原创文章（深度长文、观点评论、行业分析）
- Markdown 文章转微信公众号格式
- 文章排版美化和样式优化
- 封面图选择和摘要生成
- 多平台内容适配（将博客/知乎内容转公众号格式）
- 通过 API 批量发布文章
- 手动复制粘贴发布的格式优化

## Markdown 转微信 HTML 规范

### 转换核心原理

微信公众号编辑器不支持标准 Markdown，需要将 Markdown 转为内联样式的 HTML。关键限制：

- **不支持** `<style>` 标签和外部 CSS
- **不支持** `class` 属性
- **所有样式必须内联**（`style="..."` 属性）
- 图片必须上传到微信素材库（外链图片无法显示）
- 不支持 JavaScript

### 基础元素转换表

| Markdown | 微信 HTML |
|----------|----------|
| `# 标题` | `<h1 style="font-size:22px;font-weight:bold;color:#333;border-bottom:2px solid #f0b849;padding-bottom:8px;">标题</h1>` |
| `## 标题` | `<h2 style="font-size:18px;font-weight:bold;color:#333;margin-top:24px;">标题</h2>` |
| `**粗体**` | `<strong style="color:#f0b849;">粗体</strong>` |
| `> 引用` | 使用金色引用框（见下方） |
| `` `代码` `` | `<code style="background:#f5f5f5;padding:2px 6px;border-radius:3px;font-size:14px;color:#c7254e;">代码</code>` |
| `---` | `<hr style="border:none;border-top:1px dashed #ddd;margin:24px 0;">` |
| `[链接](url)` | `<a style="color:#576b95;text-decoration:none;">链接文字</a>`（微信不支持外链，仅用于阅读原文） |

### 金色引用框（标志性样式）

```html
<blockquote style="
  border-left: 4px solid #f0b849;
  background: linear-gradient(to right, #fdf8e8, #ffffff);
  padding: 16px 20px;
  margin: 16px 0;
  border-radius: 0 8px 8px 0;
  font-size: 15px;
  color: #666;
  line-height: 1.8;
">
  引用内容
</blockquote>
```

### 段落分隔线

```html
<!-- 简约分隔线 -->
<p style="text-align:center;color:#f0b849;font-size:16px;margin:20px 0;">
  ✦ ✦ ✦
</p>

<!-- 虚线分隔 -->
<hr style="border:none;border-top:1px dashed #e0e0e0;margin:24px 0;">

<!-- 图形分隔线 -->
<p style="text-align:center;color:#ccc;font-size:12px;letter-spacing:8px;margin:24px 0;">
  ◆◇◆◇◆
</p>
```

## 公众号排版规范

### 一、正文排版

**字体和间距：**
```html
<section style="
  font-size: 15px;
  color: #3f3f3f;
  line-height: 1.8;
  letter-spacing: 0.5px;
  word-spacing: 2px;
  text-align: justify;
  padding: 0 8px;
">
  正文内容
</section>
```

**排版核心参数：**

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| 正文字号 | 15px | 移动端最佳阅读大小 |
| 行间距 | 1.8 | 阅读舒适度最佳 |
| 字间距 | 0.5px | 增加空气感 |
| 段间距 | 16px | 段落间留白 |
| 两侧留白 | 8px | 正文区域内缩 |
| 正文颜色 | #3f3f3f | 深灰，柔和不刺眼 |
| 强调色 | #f0b849 | 金色，品牌感 |

### 二、标题层级样式

**一级标题（文章主标题）：**
```html
<h1 style="
  font-size: 22px;
  font-weight: bold;
  color: #1a1a1a;
  text-align: center;
  margin: 32px 0 16px;
  line-height: 1.4;
">文章标题</h1>
```

**二级标题（章节）：**
```html
<h2 style="
  font-size: 18px;
  font-weight: bold;
  color: #333;
  border-left: 4px solid #f0b849;
  padding-left: 12px;
  margin: 28px 0 12px;
  line-height: 1.5;
">章节标题</h2>
```

**三级标题（小节）：**
```html
<h3 style="
  font-size: 16px;
  font-weight: bold;
  color: #555;
  margin: 20px 0 8px;
  line-height: 1.5;
">小节标题</h3>
```

### 三、特殊排版组件

**高亮信息框：**
```html
<section style="
  background: #fffbeb;
  border: 1px solid #f0b849;
  border-radius: 8px;
  padding: 16px;
  margin: 16px 0;
">
  <p style="font-weight:bold;color:#f0a000;margin-bottom:8px;">💡 提示</p>
  <p style="font-size:14px;color:#666;line-height:1.6;">提示内容</p>
</section>
```

**编号列表（带样式）：**
```html
<section style="margin:12px 0;display:flex;align-items:flex-start;">
  <span style="
    display:inline-block;
    width:24px;height:24px;
    background:#f0b849;
    color:#fff;
    border-radius:50%;
    text-align:center;
    line-height:24px;
    font-size:13px;
    font-weight:bold;
    margin-right:12px;
    flex-shrink:0;
  ">1</span>
  <span style="font-size:15px;color:#3f3f3f;line-height:1.8;">列表项内容</span>
</section>
```

**图片标注：**
```html
<figure style="text-align:center;margin:20px 0;">
  <img src="[图片链接]" style="max-width:100%;border-radius:8px;">
  <figcaption style="
    font-size:12px;
    color:#999;
    margin-top:8px;
    line-height:1.5;
  ">图片说明文字</figcaption>
</figure>
```

## 封面图设计

### 尺寸规范

| 类型 | 尺寸 | 比例 | 用途 |
|------|------|------|------|
| 头条封面 | 900×383 px | 2.35:1 | 第一条推文 |
| 次条封面 | 500×500 px | 1:1 | 第二条及之后 |
| 文章头图 | 1080×608 px | 16:9 | 文章顶部大图 |

### 封面设计原则

1. **文字清晰**：标题字号至少 40px，确保缩略图可读
2. **色彩醒目**：高对比度，避免纯白背景
3. **主题鲜明**：一眼看出文章主题
4. **风格统一**：建立账号视觉识别体系
5. **留白适当**：避免信息过载

### 封面文字排版公式

```
主标题（大字）+ 副标题/关键词（小字）+ 品牌标识（角落）
```

## 摘要生成规范

公众号摘要显示在推送消息和分享卡片中，最多 **120 字**。

**摘要写作原则：**
- 第一句概括文章核心价值
- 包含 1-2 个关键词（SEO）
- 制造悬念或给出利益点
- 避免重复标题内容

**模板：**
```
[核心观点/发现]。本文从[角度1]、[角度2]等方面深入分析，
并提供[具体价值]。适合[目标读者]阅读。
```

## 发布工作流

### 方式一：API 自动发布（推荐）

适用于有微信公众号开发者权限的用户。

```
Step 1: Markdown 文章 → 微信 HTML 格式转换
  ↓
Step 2: 图片资源上传至微信素材库
  - 调用素材管理接口上传图片
  - 获取 media_id 替换图片链接
  ↓
Step 3: 生成封面图 + 摘要
  ↓
Step 4: 创建图文素材（draft）
  - POST /cgi-bin/draft/add
  - 参数：title, content, digest, thumb_media_id
  ↓
Step 5: 预览验证
  - POST /cgi-bin/message/mass/preview
  - 发送到指定微信号预览
  ↓
Step 6: 群发或定时发布
  - POST /cgi-bin/freepublish/submit
```

**关键 API 端点：**

| 接口 | 用途 |
|------|------|
| `POST /cgi-bin/material/add_material` | 上传永久素材 |
| `POST /cgi-bin/media/uploadimg` | 上传文章内图片 |
| `POST /cgi-bin/draft/add` | 新建草稿 |
| `POST /cgi-bin/draft/update` | 更新草稿 |
| `POST /cgi-bin/freepublish/submit` | 发布 |
| `POST /cgi-bin/message/mass/preview` | 预览 |

### 方式二：手动发布

适用于无 API 权限或偶尔发布的用户。

```
Step 1: 生成微信 HTML 格式内容
  ↓
Step 2: 复制 HTML 到微信公众号后台编辑器
  - 打开 mp.weixin.qq.com → 内容管理 → 新建图文
  - 切换到「代码编辑」模式（</>按钮）
  - 粘贴 HTML 代码
  ↓
Step 3: 切回「可视化编辑」模式检查排版
  - 确认标题样式正确
  - 确认图片正常显示
  - 确认引用框和分隔线样式
  ↓
Step 4: 上传封面图、填写摘要
  ↓
Step 5: 预览 → 手机端检查 → 发布
```

**手动发布注意事项：**
- HTML 中的图片需提前上传到微信素材库
- 外链图片不会显示（微信安全策略）
- 粘贴后检查是否有样式丢失
- 建议先在手机端预览再发布

## 常用排版模板

### 模板一：深度长文

```
[文章头图]
[标题]
[作者信息/引言]
[金色分隔线]
[目录/导读]
[分隔线]
[正文章节 1]
[章节间分隔]
[正文章节 2]
...
[金色引用框总结]
[CTA/作者介绍]
[往期推荐]
```

### 模板二：清单推荐

```
[封面]
[标题]
[一句话导语]
[分隔线]
[推荐项 1：编号 + 图片 + 描述]
[推荐项 2]
...
[总结对比表格]
[CTA]
```

### 模板三：教程指南

```
[封面]
[标题]
[前置说明/工具准备]
[分隔线]
[Step 1 + 配图]
[Step 2 + 配图]
...
[常见问题 FAQ]
[CTA]
```

## 完整示例

### Examples：技术博客转公众号

**输入 Markdown：**
```markdown
# 2026 年前端性能优化指南

> 性能不是锦上添花，而是用户体验的基石。

## 为什么性能很重要

根据 Google 的研究，页面加载时间超过 **3秒**，
53% 的移动用户会离开。
```

**转换后微信 HTML：**
```html
<h1 style="font-size:22px;font-weight:bold;color:#1a1a1a;
  text-align:center;margin:32px 0 16px;line-height:1.4;">
  2026 年前端性能优化指南
</h1>

<blockquote style="border-left:4px solid #f0b849;
  background:linear-gradient(to right,#fdf8e8,#ffffff);
  padding:16px 20px;margin:16px 0;border-radius:0 8px 8px 0;
  font-size:15px;color:#666;line-height:1.8;">
  性能不是锦上添花，而是用户体验的基石。
</blockquote>

<p style="text-align:center;color:#f0b849;font-size:16px;
  margin:20px 0;">✦ ✦ ✦</p>

<h2 style="font-size:18px;font-weight:bold;color:#333;
  border-left:4px solid #f0b849;padding-left:12px;
  margin:28px 0 12px;line-height:1.5;">
  为什么性能很重要
</h2>

<section style="font-size:15px;color:#3f3f3f;line-height:1.8;
  letter-spacing:0.5px;">
  <p>根据 Google 的研究，页面加载时间超过
  <strong style="color:#f0b849;">3秒</strong>，
  53% 的移动用户会离开。</p>
</section>
```

## FAQ

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 图片不显示 | 使用了外链图片 | 上传到微信素材库，使用微信图片链接 |
| 样式丢失 | 使用了 class 或外部 CSS | 所有样式改为内联 style 属性 |
| 排版错乱 | HTML 标签嵌套错误 | 检查标签闭合，简化嵌套层级 |
| 链接无法点击 | 微信限制外链 | 使用「阅读原文」放置唯一外链 |
| 代码块不美观 | 微信不支持语法高亮 | 用背景色块+内联样式模拟代码块 |
| 表格显示不全 | 宽表格溢出 | 控制列数，或用列表替代表格 |

## 写作建议

### 标题优化

- 字数控制在 **15-30 字**
- 包含核心关键词
- 使用数字增加具体感（`7个方法`、`3分钟学会`）
- 适当使用竖线分隔（`前端优化 | 你必须知道的 7 个技巧`）

### 开头黄金三行

读者在 3 秒内决定是否继续阅读，开头必须有冲击力：

1. **数据开头**：`据统计，90% 的用户...`
2. **痛点开头**：`你是否也遇到过...`
3. **故事开头**：`上周，一个同事问我...`
4. **观点开头**：`我一直认为...直到...`

### 结尾引导

- 总结全文核心观点
- 引导点赞/在看/分享
- 预告下期内容
- 引导关注/加群

## 多平台适配

将公众号内容适配到其他平台的要点：

| 平台 | 调整方向 |
|------|---------|
| 知乎 | 增加专业深度，添加引用来源 |
| 掘金/SegmentFault | 增加代码示例，技术细节 |
| 小红书 | 缩短到 500 字，增加 emoji 和视觉 |
| 头条号 | 增加标题党元素，分段更密 |

## Output Format

每次生成公众号内容时，按以下结构输出：

```markdown
## 📰 公众号文章

### 基础信息
- 标题：[标题]
- 摘要：[120字以内]
- 封面建议：[尺寸+风格]

### 文章内容（微信 HTML）
[完整 HTML 代码]

### 文章内容（Markdown 备份）
[Markdown 版本，便于其他平台使用]

### 发布检查清单
- [ ] 图片已上传素材库
- [ ] 手机端预览无排版问题
- [ ] 外链已移至「阅读原文」
- [ ] 摘要和封面已设置
```
