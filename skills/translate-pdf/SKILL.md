---
name: openakita/skills@translate-pdf
description: Translate PDF documents while preserving original layout, styling, tables, images, and formatting. Supports Simplified Chinese, Traditional Chinese, English, Japanese, Korean, and more. Page-by-page translation with structure preservation.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# Translate PDF — PDF Document Translation

## When to Use

- User needs to translate an English PDF to Chinese (or another language)
- Need to preserve the PDF's original layout, tables, images, and formatting
- Need to translate academic papers, technical documents, or business reports
- Need bilingual PDF output
- Need to batch translate multiple PDF files

---

## Prerequisites

### Required Dependencies

| Dependency | Purpose | Installation |
|------------|---------|-------------|
| Python ≥ 3.10 | Run translation scripts | Pre-installed |
| `PyMuPDF` (fitz) | PDF parsing and reconstruction | `pip install PyMuPDF` |
| `httpx` | HTTP API calls | `pip install httpx` |

### Optional Dependencies

| Dependency | Purpose | Installation |
|------------|---------|-------------|
| `pdf2image` | PDF to image (OCR scenarios) | `pip install pdf2image` |
| `pytesseract` | OCR text recognition | `pip install pytesseract` |
| `pdfplumber` | Table extraction | `pip install pdfplumber` |
| `reportlab` | PDF generation | `pip install reportlab` |
| `deep-translator` | Multi-engine translation | `pip install deep-translator` |
| `openai` | GPT translation | `pip install openai` |

### System-Level Dependencies

| Tool | Purpose | Description |
|------|---------|-------------|
| Poppler | pdf2image backend | Windows: Download poppler-utils; macOS: `brew install poppler` |
| Tesseract | OCR engine | Windows: Download installer; macOS: `brew install tesseract` |
| Chinese fonts | Chinese text rendering in PDF | System needs Chinese fonts installed (Microsoft YaHei, Noto Sans CJK, etc.) |

### Verify Installation

```bash
python -c "import fitz; print('PyMuPDF', fitz.version)"
python -c "import pdfplumber; print('pdfplumber OK')"
```

### LLM API Configuration

The translation engine prefers LLM (GPT-4 / Claude). Configure in `.env`:

```
OPENAI_API_KEY=sk-xxxxx
```

If no LLM API is configured, it will fall back to `deep-translator` (Google Translate / DeepL).

---

## Instructions

### Supported Languages

| Language Code | Language | Translation Quality |
|--------------|----------|--------------------|
| `zh-CN` | Simplified Chinese | ★★★★★ |
| `zh-TW` | Traditional Chinese | ★★★★★ |
| `en` | English | ★★★★★ |
| `ja` | Japanese | ★★★★ |
| `ko` | Korean | ★★★★ |
| `fr` | French | ★★★★ |
| `de` | German | ★★★★ |
| `es` | Spanish | ★★★★ |
| `ru` | Russian | ★★★ |
| `ar` | Arabic | ★★★ |

### Translation Engine Priority

| Priority | Engine | Characteristics |
|----------|--------|----------------|
| 1 | LLM (GPT-4/Claude) | Highest quality, understands context, consistent terminology |
| 2 | DeepL API | High-quality machine translation |
| 3 | Google Translate | Free, wide language coverage |

The agent automatically selects the available translation engine by priority. Users can specify a particular engine.

### PDF Element Processing Strategy

| Element | Processing Method |
|---------|-------------------|
| Body text | Translate while preserving font size, color, bold/italic |
| Headings | Translate while preserving hierarchy and style |
| Tables | Translate cell content, preserve table structure |
| Images | Keep images unchanged |
| Text within images | Optional OCR recognition then translation |
| Headers/footers | Translate and maintain position |
| Page numbers | Keep unchanged |
| Footnotes/endnotes | Translate content, preserve numbering |
| Table of contents | Translate entries, keep page numbers |
| Bookmarks | Translate titles |
| Links/URLs | Keep unchanged |
| Math formulas | Keep unchanged |
| Code blocks | Keep unchanged (only translate comments) |
| Watermarks | Keep as-is |

---

## Workflows

### Workflow 1: Standard PDF Translation

**Step 1 — Parse PDF Structure**

```python
import fitz

doc = fitz.open("input.pdf")
print(f"Total pages: {doc.page_count}")
print(f"Metadata: {doc.metadata}")

for page_num in range(min(3, doc.page_count)):
    page = doc[page_num]
    text = page.get_text("dict")
    print(f"Page {page_num + 1}: {len(text['blocks'])} text blocks")
```

**Step 2 — Extract Text Blocks Page by Page**

```python
def extract_text_blocks(page):
    """Extract all text blocks from the page along with their positions and styles"""
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

**Step 3 — Batch Translation**

Group extracted text into paragraphs and send batches to the translation engine:

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

**LLM Translation Prompt**

```
You are a professional document translator. Please translate the following text from {source_lang} to {target_lang}.

Requirements:
1. Maintain accuracy and consistency of technical terminology
2. Preserve paragraph structure
3. For technical terms, include the original term on first occurrence, e.g., "Convolutional Neural Network (CNN)"
4. Do not translate code, formulas, URLs, or personal names (unless a common Chinese translation exists)
5. Preserve the tone and style of the original

Text to translate:
---
{text}
---
```

**Step 4 — Rebuild PDF**

```python
def rebuild_pdf(original_doc, translated_blocks, output_path):
    """Replace original text with translated text, preserving layout"""
    new_doc = fitz.open()

    for page_num in range(original_doc.page_count):
        orig_page = original_doc[page_num]
        new_page = new_doc.new_page(
            width=orig_page.rect.width,
            height=orig_page.rect.height
        )

        # Copy images and non-text elements
        new_page.show_pdf_page(new_page.rect, original_doc, page_num)

        # Cover original text areas and write translations
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

**Step 5 — Quality Check**

After translation completes, automatically check:
- Page count matches the original
- No blank pages
- Translation coverage (translated text / total text ≥ 95%)
- Font rendering is correct

---

### Workflow 2: Bilingual PDF

Generate side-by-side or top/bottom bilingual PDF:

**Layout Options**

| Layout | Description | Use Case |
|--------|-------------|----------|
| Side-by-side | Left page original, right page translation | Academic papers, comparison review |
| Top/bottom | Paragraph-level alternating display | Learning materials |
| Annotation mode | Translation as side annotations | Preserve original as primary |

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
            # Original on the left
            new_page.show_pdf_page(
                fitz.Rect(0, 0, orig_page.rect.width, orig_page.rect.height),
                original_doc, page_num
            )
            # Translation on the right
            insert_translated_page(
                new_page,
                translated_blocks[page_num],
                offset_x=orig_page.rect.width
            )

    new_doc.save(output_path)
```

---

### Workflow 3: Scanned PDF Translation (OCR)

Process scanned or image-based PDFs:

**Step 1 — Detect PDF Type**

```python
def is_scanned_pdf(doc):
    """Detect whether the PDF is a scan"""
    for page_num in range(min(3, doc.page_count)):
        page = doc[page_num]
        text = page.get_text().strip()
        images = page.get_images()
        if not text and images:
            return True
    return False
```

**Step 2 — OCR Recognition**

```python
from pdf2image import convert_from_path
import pytesseract

images = convert_from_path("scanned.pdf", dpi=300)
for i, img in enumerate(images):
    text = pytesseract.image_to_string(img, lang='eng')
    # Use image_to_data to get text position information
    data = pytesseract.image_to_data(img, lang='eng', output_type=pytesseract.Output.DICT)
```

**Step 3** — Apply Workflow 1 translation and rebuild process to OCR results

---

### Workflow 4: Batch PDF Translation

```python
import glob
import asyncio

async def batch_translate_pdfs(input_dir, output_dir, target_lang="zh-CN"):
    pdf_files = glob.glob(f"{input_dir}/*.pdf")
    print(f"Found {len(pdf_files)} PDF files")

    for pdf_path in pdf_files:
        output_path = os.path.join(
            output_dir,
            os.path.basename(pdf_path).replace('.pdf', f'_{target_lang}.pdf')
        )
        print(f"Translating: {pdf_path} -> {output_path}")
        await translate_single_pdf(pdf_path, output_path, target_lang)
```

---

## Output Format

### File Naming

```
{original_filename}_{target_language}.pdf
```

Examples:
- `research_paper_zh-CN.pdf` (translated version)
- `research_paper_bilingual.pdf` (bilingual version)

### Output Report

```
📄 PDF Translation Complete
- Original file: research_paper.pdf (25 pages)
- Translated file: research_paper_zh-CN.pdf (25 pages)
- Source language: English → Target language: Simplified Chinese
- Translation engine: GPT-4
- Translation coverage: 98.5%
- Tables: 12 (translated)
- Images: 8 (preserved)
- Time taken: 3 min 42 sec
- Estimated cost: $0.85
```

---

## Common Pitfalls

### 1. Garbled Text Due to Missing Chinese Fonts

**Symptom**: Chinese text displays as boxes or garbled characters in the translated PDF
**Solution**: Ensure Chinese fonts are installed on the system and registered in PyMuPDF:

```python
import fitz

# Chinese fonts supported by PyMuPDF
# "china-ss" = Source Han Sans (Simplified)
# "china-ts" = Source Han Sans (Traditional)
# Or use custom fonts
page.insert_font(fontname="custom-zh", fontfile="/path/to/NotoSansCJK-Regular.ttf")
```

### 2. Misaligned Tables After Translation

**Symptom**: Table content overflows cells
**Cause**: Chinese translations are typically shorter than English, but can sometimes be longer
**Solution**:
- Dynamically adjust font size to fit cells
- Allow automatic text wrapping
- For complex tables, use pdfplumber to extract and translate separately

### 3. Math Formulas Incorrectly Translated

**Symptom**: Formulas are treated as regular text and translated
**Solution**: Identify and mark math formula regions before translation, skip them:

```python
import re

def should_skip_translation(text):
    """Determine if text should be skipped during translation"""
    # Math formula pattern
    if re.match(r'^[\s\d\+\-\*\/\=\(\)\[\]\{\}\^\_\\\$]+$', text):
        return True
    # LaTeX formulas
    if text.strip().startswith('\\') and not text.strip().startswith('\\text'):
        return True
    # Code blocks
    if re.match(r'^(def |class |import |from |const |let |var |function )', text.strip()):
        return True
    return False
```

### 4. Memory Overflow with Large Files

**Symptom**: Out of memory when processing large PDFs with 100+ pages
**Solution**: Process page by page instead of loading everything at once:

```python
for page_num in range(doc.page_count):
    page = doc[page_num]
    # Process current page
    process_page(page)
    # Free memory
    page = None
```

### 5. Low OCR Accuracy

**Symptom**: High error rate in text recognition from scanned PDFs
**Solution**:
- Increase scan DPI (≥ 300)
- Preprocess images (binarization, noise removal, skew correction)
- Use language packs: `pytesseract.image_to_string(img, lang='eng+chi_sim')`

### 6. Inconsistent Terminology in Translation

**Symptom**: Same term translated differently across pages
**Solution**:
- Build a glossary during the first pass scan
- Pass the glossary as context to the LLM
- Check terminology consistency with a script after translation

```python
glossary = {
    "machine learning": "machine learning",
    "neural network": "neural network",
    "gradient descent": "gradient descent",
    "backpropagation": "backpropagation",
}
```

### 7. Repeated Translation of Headers and Footers

**Symptom**: Header and footer translations vary slightly on each page
**Solution**: Identify header/footer patterns first, translate once uniformly, then apply to all pages

---

## Advanced Configuration

### Translation Quality Levels

| Level | Method | Speed | Quality | Cost |
|-------|--------|-------|---------|------|
| Quick | Google Translate | ★★★★★ | ★★★ | Free |
| Standard | DeepL | ★★★★ | ★★★★ | $$ |
| Professional | GPT-4 | ★★★ | ★★★★★ | $$$ |
| Human-in-the-Loop | GPT-4 + human review | ★★ | ★★★★★+ | $$$$ |

### Custom Glossary

Users can provide a glossary file (CSV/JSON) to ensure consistent translation of specific terms:

```json
{
  "source_lang": "en",
  "target_lang": "zh-CN",
  "terms": {
    "OpenAkita": "OpenAkita",
    "Agent": "Agent",
    "fine-tuning": "fine-tuning",
    "prompt engineering": "prompt engineering"
  }
}
```

---

## EXTEND.md Extension

Users can create `EXTEND.md` in the same directory as the skill to add:
- Industry-specific glossaries
- Preferred translation engine and quality level
- Custom font paths
- PDF template and style override rules
- Preprocessing rules for specific document types
