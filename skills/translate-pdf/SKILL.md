---
name: openakita/skills@translate-pdf
description: Translate PDF documents while preserving original layout, styling, tables, images, and formatting. Supports Simplified Chinese, Traditional Chinese, English, Japanese, Korean, and more. Page-by-page translation with structure preservation.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# Translate PDF — PDF 文档翻译

## When to Use

- 用户需要将英文 PDF 翻译为中文（或其他语言）
- 需要保留 PDF 的原始排版、表格、图片和格式
- 需要翻译学术论文、技术文档、商业报告
- 需要双语对照的 PDF 输出
- 需要批量翻译多个 PDF 文件

---

## Prerequisites

### 必需依赖

| 依赖 | 用途 | 安装方式 |
|------|------|---------|
| Python ≥ 3.10 | 运行翻译脚本 | 系统预装 |
| `PyMuPDF` (fitz) | PDF 解析与重建 | `pip install PyMuPDF` |
| `httpx` | HTTP API 调用 | `pip install httpx` |

### 可选依赖

| 依赖 | 用途 | 安装方式 |
|------|------|---------|
| `pdf2image` | PDF 转图片（OCR 场景） | `pip install pdf2image` |
| `pytesseract` | OCR 文字识别 | `pip install pytesseract` |
| `pdfplumber` | 表格提取 | `pip install pdfplumber` |
| `reportlab` | PDF 生成 | `pip install reportlab` |
| `deep-translator` | 多引擎翻译 | `pip install deep-translator` |
| `openai` | GPT 翻译 | `pip install openai` |

### 系统级依赖

| 工具 | 用途 | 说明 |
|------|------|------|
| Poppler | pdf2image 的后端 | Windows: 下载 poppler-utils; macOS: `brew install poppler` |
| Tesseract | OCR 引擎 | Windows: 下载安装包; macOS: `brew install tesseract` |
| 中文字体 | PDF 中文渲染 | 系统需安装中文字体（微软雅黑、思源黑体等） |

### 验证安装

```bash
python -c "import fitz; print('PyMuPDF', fitz.version)"
python -c "import pdfplumber; print('pdfplumber OK')"
```

### LLM API 配置

翻译引擎首选 LLM（GPT-4 / Claude），在 `.env` 中配置：

```
OPENAI_API_KEY=sk-xxxxx
```

如果未配置 LLM API，将回退到 `deep-translator`（Google Translate / DeepL）。

---

## Instructions

### 支持的语言

| 语言代码 | 语言 | 翻译质量 |
|---------|------|---------|
| `zh-CN` | 简体中文 | ★★★★★ |
| `zh-TW` | 繁体中文 | ★★★★★ |
| `en` | 英文 | ★★★★★ |
| `ja` | 日文 | ★★★★ |
| `ko` | 韩文 | ★★★★ |
| `fr` | 法文 | ★★★★ |
| `de` | 德文 | ★★★★ |
| `es` | 西班牙文 | ★★★★ |
| `ru` | 俄文 | ★★★ |
| `ar` | 阿拉伯文 | ★★★ |

### 翻译引擎优先级

| 优先级 | 引擎 | 特点 |
|--------|------|------|
| 1 | LLM (GPT-4/Claude) | 最高质量，理解上下文，术语一致 |
| 2 | DeepL API | 高质量机器翻译 |
| 3 | Google Translate | 免费，覆盖语种广 |

Agent 按照优先级自动选择可用的翻译引擎。用户可以指定使用特定引擎。

### PDF 元素处理策略

| 元素 | 处理方式 |
|------|---------|
| 正文文本 | 翻译并保留字体大小、颜色、粗体/斜体 |
| 标题 | 翻译并保留层级和样式 |
| 表格 | 翻译单元格内容，保留表格结构 |
| 图片 | 保留原图不动 |
| 图片中的文字 | 可选 OCR 识别后翻译 |
| 页眉/页脚 | 翻译并保持位置 |
| 页码 | 保持不变 |
| 脚注/尾注 | 翻译内容，保留编号 |
| 目录 | 翻译条目，页码不变 |
| 书签 | 翻译标题 |
| 链接/URL | 保持不变 |
| 数学公式 | 保持不变 |
| 代码块 | 保持不变（仅翻译注释） |
| 水印 | 保留原样 |

---

## Workflows

### Workflow 1: 标准 PDF 翻译

**步骤 1 — 解析 PDF 结构**

```python
import fitz

doc = fitz.open("input.pdf")
print(f"总页数: {doc.page_count}")
print(f"元数据: {doc.metadata}")

for page_num in range(min(3, doc.page_count)):
    page = doc[page_num]
    text = page.get_text("dict")
    print(f"第 {page_num + 1} 页: {len(text['blocks'])} 个文本块")
```

**步骤 2 — 逐页提取文本块**

```python
def extract_text_blocks(page):
    """提取页面中所有文本块及其位置和样式"""
    blocks = []
    text_dict = page.get_text("dict")

    for block in text_dict["blocks"]:
        if block["type"] == 0:  # text block
            for line in block["lines"]:
                for span in line["spans"]:
                    blocks.append({
                        "text": span["text"],
                        "bbox": span["bbox"],
                        "font": span["font"],
                        "size": span["size"],
                        "color": span["color"],
                        "flags": span["flags"],
                    })
    return blocks
```

**步骤 3 — 批量翻译**

将提取的文本按段落分组，批量发送给翻译引擎：

```python
async def translate_blocks(blocks, target_lang="zh-CN"):
    paragraphs = merge_spans_to_paragraphs(blocks)

    translated = []
    for batch in chunk_list(paragraphs, batch_size=20):
        texts = [p["text"] for p in batch]
        results = await batch_translate(texts, target_lang)
        translated.extend(results)

    return translated
```

**LLM 翻译 Prompt**

```
你是一位专业的文档翻译师。请将以下文本从{source_lang}翻译为{target_lang}。

要求：
1. 保持专业术语的准确性和一致性
2. 保持段落结构不变
3. 对于技术术语，首次出现时附上原文：如"卷积神经网络（CNN）"
4. 不翻译代码、公式、URL、人名（除非有通用中文译名）
5. 保持原文的语气和风格

待翻译文本：
---
{text}
---
```

**步骤 4 — 重建 PDF**

```python
def rebuild_pdf(original_doc, translated_blocks, output_path):
    """用翻译后的文本替换原文，保留排版"""
    new_doc = fitz.open()

    for page_num in range(original_doc.page_count):
        orig_page = original_doc[page_num]
        new_page = new_doc.new_page(
            width=orig_page.rect.width,
            height=orig_page.rect.height
        )

        # 复制图片和非文本元素
        new_page.show_pdf_page(new_page.rect, original_doc, page_num)

        # 覆盖原文区域并写入译文
        for block in translated_blocks[page_num]:
            rect = fitz.Rect(block["bbox"])
            new_page.draw_rect(rect, color=None, fill=(1, 1, 1))
            new_page.insert_textbox(
                rect,
                block["translated_text"],
                fontsize=block["size"] * 0.85,
                fontname="china-ss",
                align=fitz.TEXT_ALIGN_LEFT
            )

    new_doc.save(output_path)
```

**步骤 5 — 质量检查**

翻译完成后执行自动检查：
- 页数与原文一致
- 无空白页面
- 翻译覆盖率（已翻译文本 / 总文本 ≥ 95%）
- 字体渲染正常

---

### Workflow 2: 双语对照 PDF

生成左右/上下对照的双语 PDF：

**布局选项**

| 布局 | 说明 | 适用场景 |
|------|------|---------|
| 左右对照 | 左页原文、右页译文 | 学术论文、对比审阅 |
| 上下对照 | 段落级交替显示 | 学习材料 |
| 注释模式 | 译文作为侧边注释 | 保留原文为主 |

```python
def create_bilingual_pdf(original_doc, translated_blocks, output_path, layout="side-by-side"):
    new_doc = fitz.open()

    for page_num in range(original_doc.page_count):
        orig_page = original_doc[page_num]

        if layout == "side-by-side":
            new_width = orig_page.rect.width * 2
            new_page = new_doc.new_page(
                width=new_width,
                height=orig_page.rect.height
            )
            # 左侧放原文
            new_page.show_pdf_page(
                fitz.Rect(0, 0, orig_page.rect.width, orig_page.rect.height),
                original_doc, page_num
            )
            # 右侧放译文
            insert_translated_page(
                new_page,
                translated_blocks[page_num],
                offset_x=orig_page.rect.width
            )

    new_doc.save(output_path)
```

---

### Workflow 3: 扫描版 PDF 翻译（OCR）

处理扫描件或图片型 PDF：

**步骤 1 — 检测 PDF 类型**

```python
def is_scanned_pdf(doc):
    """检测 PDF 是否为扫描件"""
    for page_num in range(min(3, doc.page_count)):
        page = doc[page_num]
        text = page.get_text().strip()
        images = page.get_images()
        if not text and images:
            return True
    return False
```

**步骤 2 — OCR 识别**

```python
from pdf2image import convert_from_path
import pytesseract

images = convert_from_path("scanned.pdf", dpi=300)
for i, img in enumerate(images):
    text = pytesseract.image_to_string(img, lang='eng')
    # 使用 image_to_data 获取文字位置信息
    data = pytesseract.image_to_data(img, lang='eng', output_type=pytesseract.Output.DICT)
```

**步骤 3** — 对 OCR 结果执行 Workflow 1 的翻译和重建流程

---

### Workflow 4: 批量 PDF 翻译

```python
import glob
import asyncio

async def batch_translate_pdfs(input_dir, output_dir, target_lang="zh-CN"):
    pdf_files = glob.glob(f"{input_dir}/*.pdf")
    print(f"发现 {len(pdf_files)} 个 PDF 文件")

    for pdf_path in pdf_files:
        output_path = os.path.join(
            output_dir,
            os.path.basename(pdf_path).replace('.pdf', f'_{target_lang}.pdf')
        )
        print(f"翻译: {pdf_path} -> {output_path}")
        await translate_single_pdf(pdf_path, output_path, target_lang)
```

---

## Output Format

### 文件命名

```
{原文件名}_{目标语言}.pdf
```

示例：
- `research_paper_zh-CN.pdf`（翻译版）
- `research_paper_bilingual.pdf`（双语版）

### 输出报告

```
📄 PDF 翻译完成
- 原文件：research_paper.pdf (25 页)
- 译文件：research_paper_zh-CN.pdf (25 页)
- 源语言：English → 目标语言：简体中文
- 翻译引擎：GPT-4
- 翻译覆盖率：98.5%
- 表格数量：12 个（已翻译）
- 图片数量：8 张（已保留）
- 耗时：3 分 42 秒
- 费用估算：$0.85
```

---

## Common Pitfalls

### 1. 中文字体缺失导致乱码

**症状**：翻译后的 PDF 中文显示为方框或乱码
**解决**：确保系统安装了中文字体，并在 PyMuPDF 中注册：

```python
import fitz

# PyMuPDF 支持的中文字体
# "china-ss" = 思源宋体 (简体)
# "china-ts" = 思源宋体 (繁体)
# 或使用自定义字体
page.insert_font(fontname="custom-zh", fontfile="/path/to/NotoSansCJK-Regular.ttf")
```

### 2. 表格翻译后错位

**症状**：表格内容溢出单元格
**原因**：中文译文通常比英文短，但某些情况下可能更长
**解决**：
- 动态调整字体大小以适应单元格
- 允许文本自动换行
- 对于复杂表格，使用 pdfplumber 提取后单独翻译

### 3. 数学公式被错误翻译

**症状**：公式被当作文本翻译
**解决**：在翻译前识别并标记数学公式区域，跳过翻译：

```python
import re

def should_skip_translation(text):
    """判断文本是否应跳过翻译"""
    # 数学公式模式
    if re.match(r'^[\s\d\+\-\*\/\=\(\)\[\]\{\}\^\_\\\$]+$', text):
        return True
    # LaTeX 公式
    if text.strip().startswith('\\') and not text.strip().startswith('\\text'):
        return True
    # 代码块
    if re.match(r'^(def |class |import |from |const |let |var |function )', text.strip()):
        return True
    return False
```

### 4. 大文件内存溢出

**症状**：处理超过 100 页的大型 PDF 时内存不足
**解决**：逐页处理而非一次性加载：

```python
for page_num in range(doc.page_count):
    page = doc[page_num]
    # 处理当前页
    process_page(page)
    # 释放内存
    page = None
```

### 5. OCR 识别率低

**症状**：扫描版 PDF 的文字识别错误多
**解决**：
- 提高扫描 DPI（≥ 300）
- 预处理图片（二值化、去噪、倾斜校正）
- 使用语言包：`pytesseract.image_to_string(img, lang='eng+chi_sim')`

### 6. 翻译术语不一致

**症状**：同一术语在不同页面有不同翻译
**解决**：
- 第一遍扫描时建立术语表
- 将术语表作为上下文传递给 LLM
- 翻译后用脚本检查术语一致性

```python
glossary = {
    "machine learning": "机器学习",
    "neural network": "神经网络",
    "gradient descent": "梯度下降",
    "backpropagation": "反向传播",
}
```

### 7. 页眉页脚重复翻译

**症状**：每页的页眉页脚翻译结果微有差异
**解决**：先识别页眉页脚模式，统一翻译一次后应用到所有页面

---

## 高级配置

### 翻译质量等级

| 等级 | 方式 | 速度 | 质量 | 费用 |
|------|------|------|------|------|
| 快速 | Google Translate | ★★★★★ | ★★★ | 免费 |
| 标准 | DeepL | ★★★★ | ★★★★ | $$ |
| 专业 | GPT-4 | ★★★ | ★★★★★ | $$$ |
| 人机协作 | GPT-4 + 人工审校 | ★★ | ★★★★★+ | $$$$ |

### 自定义术语表

用户可提供术语表文件（CSV/JSON）确保特定术语的翻译一致：

```json
{
  "source_lang": "en",
  "target_lang": "zh-CN",
  "terms": {
    "OpenAkita": "OpenAkita",
    "Agent": "智能体",
    "fine-tuning": "微调",
    "prompt engineering": "提示词工程"
  }
}
```

---

## EXTEND.md 扩展

用户可在技能同目录下创建 `EXTEND.md` 添加：
- 行业专用术语表
- 首选翻译引擎和质量等级
- 自定义字体路径
- PDF 模板和样式覆盖规则
- 特定文档类型的预处理规则

