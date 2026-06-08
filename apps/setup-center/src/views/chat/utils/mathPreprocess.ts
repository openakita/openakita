// LaTeX / math preprocessing for the markdown pipeline.
//
// 背景（issue #580 "不支持latex"）：大模型输出数学公式时几乎都用 LaTeX 的
// 标准定界符 `\( ... \)`（行内）和 `\[ ... \]`（行间），偶尔用 `$ ... $` /
// `$$ ... $$`。但 react-markdown 的 remark-math 只认 `$` 形式，且 markdown
// 本身会把 `\[`、`\]` 里的反斜杠当成"转义标点"吃掉，于是公式在聊天里被渲染
// 成裸文本（截图里那一坨 `[ \frac{1}{n}\sum_i ... ]`）。
//
// 这里在送进 react-markdown 之前做一次轻量改写：把 `\(...\)` / `\[...\]`
// 统一改写成 remark-math 认识的 `$...$` / `$$...$$`，并且只在"散文"片段里
// 做——代码块（``` 围栏 / 行内 `code`）原样保留，避免把正在讨论 LaTeX 源码
// 或 shell 里的 `$VAR`、`\(` 误伤。
//
// 设计参考了若干成熟的 LLM 桌面端实现，思路一致：preprocess 阶段做定界符
// 归一 + 货币 `$` escape，再交给 remark-math + rehype-katex 渲染。

// —— 栅栏 / 反引号噪声清理（移植自成熟桌面端实现的保守子集）——
//
// 大模型常吐出"散落的反引号"：成对的 ``` 之外多出一截 ```、或孤立的 ``，
// 这些会被 markdown 误当成代码围栏/行内代码的起点，把后面一大段渲染坏。
// 这里在不破坏"真实围栏"的前提下，把这些噪声反引号清掉。
//
// 关键安全措施：先标出两类"受保护区间"——(1) 已闭合的成对围栏；(2) 流式
// 尚未闭合、但带合法语言标签的尾部开栏（正在流入的代码块）。只在保护区间
// **之外** 才清理反引号噪声，绝不动正在显示/流入的代码块。
const VALID_LANGUAGE_RE = /^[a-z0-9][a-z0-9+#-]*$/i;
function sanitizeLanguageTag(tag: string): string {
  const first = tag.trim().split(/\s/, 1)[0] || "";
  return VALID_LANGUAGE_RE.test(first) && first.length <= 16 ? first.toLowerCase() : "";
}

// 用纯字符串比较（不构造正则）判断 body 里是否存在一行恰好等于 marker 的
// 闭合栅栏行，避免把输入数据混进正则源。
function hasCloseFenceLine(body: string, marker: string): boolean {
  const lines = body.split("\n");
  // 原逻辑要求闭合栅栏前必须有换行，故 body 第一行不计入。
  for (let i = 1; i < lines.length; i += 1) {
    if (lines[i].trim() === marker) return true;
  }
  return false;
}

function scrubBacktickNoise(text: string): string {
  const protectedRanges: { end: number; start: number }[] = [];
  let match: RegExpExecArray | null;

  // (1) 已闭合的成对围栏整段保护。
  const balancedFenceRe = /(^|\n)([ \t]*)(`{3,}|~{3,})([^\n]*)\n([\s\S]*?)\n[ \t]*\3[ \t]*(?=\n|$)/g;
  while ((match = balancedFenceRe.exec(text)) !== null) {
    protectedRanges.push({ end: balancedFenceRe.lastIndex, start: match.index + match[1].length });
  }

  // (2) 流式尾部：未闭合但带合法语言标签的开栏（正在流入的代码块）。
  // 比上游更宽——只要语言标签合法且尚未闭合就保护，宁可少清也绝不误删开栏。
  const danglingFenceRe = /(^|\n)[ \t]*(`{3,}|~{3,})([a-z0-9][a-z0-9+#-]{0,15})[ \t]*\n([\s\S]*)$/gi;
  while ((match = danglingFenceRe.exec(text)) !== null) {
    const marker = match[2] || "```";
    const info = match[3] || "";
    const body = match[4] || "";
    if (!hasCloseFenceLine(body, marker) && sanitizeLanguageTag(info)) {
      protectedRanges.push({ end: text.length, start: match.index + match[1].length });
      break;
    }
  }

  protectedRanges.sort((a, b) => a.start - b.start);

  const fenceNoiseRe = /`{3,}/g;
  let out = "";
  let cursor = 0;
  for (const range of protectedRanges) {
    out += text.slice(cursor, range.start).replace(fenceNoiseRe, "");
    out += text.slice(range.start, range.end);
    cursor = range.end;
  }
  out += text.slice(cursor).replace(fenceNoiseRe, "");

  // 清掉成对/孤立的双反引号噪声（两遍以处理相邻情形）。
  for (let pass = 0; pass < 2; pass += 1) {
    out = out.replace(/(?<!`)``(?!`)\s*(?<!`)``(?!`)/g, "");
    out = out.replace(/(^|[^`])``(?=\s|[.,;:!?)\]'"\u2014\u2013-]|$)/g, "$1");
  }
  return out;
}

// 剔除空围栏块（``` 紧跟 ``` 之间无内容），它们只会渲染成一个空代码卡片。
const EMPTY_FENCE_BLOCK_RE =
  /(^|\n)[ \t]*(?:`{3,}|~{3,})[^\n]*\n[ \t]*(?:`{3,}|~{3,})[ \t]*(?=\n|$)/g;
function stripEmptyFenceBlocks(text: string): string {
  return text.replace(EMPTY_FENCE_BLOCK_RE, "$1");
}

// 把文本按"代码围栏"切片：```...``` 或 ~~~...~~~ 整段保留。
const CODE_FENCE_SPLIT_RE = /((?:```|~~~)[\s\S]*?(?:```|~~~))/g;
// 行内代码 `like this` 整段保留。
const INLINE_CODE_SPLIT_RE = /(`[^`\n]+`)/g;

// `\( ... \)` 行内数学 —— 不跨段落（限制在单行内，避免贪婪误吞）。
const LATEX_INLINE_RE = /\\\(([^\n]+?)\\\)/g;
// `\[ ... \]` 行间数学 —— 允许跨行。
const LATEX_DISPLAY_RE = /\\\[([\s\S]+?)\\\]/g;

// `$` 紧跟数字时视为货币金额（$5、$19.99、$1,299），escape 成 `\$`，
// 避免 remark-math 在 singleDollarTextMath 下把两个货币符号之间的文字
// 当成行内公式。数学表达式几乎总是以字母或 `\命令` 开头，所以这个启发
// 式的误伤率极低。
const CURRENCY_DOLLAR_RE = /(^|[^\\$])\$(?=\d)/g;

function rewriteLatexBracketDelimiters(text: string): string {
  return text
    .replace(LATEX_INLINE_RE, (_m, body: string) => `$${body}$`)
    .replace(LATEX_DISPLAY_RE, (_m, body: string) => `$$${body}$$`);
}

function escapeCurrencyDollars(text: string): string {
  return text.replace(CURRENCY_DOLLAR_RE, "$1\\$");
}

function transformProse(text: string): string {
  // 行内代码片段同样原样保留，其余散文做货币 escape + 定界符归一。
  // 顺序很关键：先在"原始文本"上 escape 货币 `$`，再把 `\(..\)` 改写成
  // `$..$`。否则像 `\(5x\)` 这种以数字开头的行内公式被改写成 `$5x$` 后，
  // 会被货币正则误当成金额、把开头的 `$` escape 掉，导致公式破损。
  return text
    .split(INLINE_CODE_SPLIT_RE)
    .map((part) =>
      part.startsWith("`") ? part : rewriteLatexBracketDelimiters(escapeCurrencyDollars(part)),
    )
    .join("");
}

/**
 * 送进 react-markdown 之前的统一预处理：
 *   1. 清理散落反引号 / 空围栏噪声（保护真实围栏与流式开栏）；
 *   2. 把 LaTeX 定界符归一成 remark-math 认识的 `$` 形式（仅散文）。
 * 返回值可直接喂给 react-markdown。
 */
export function preprocessMath(content: string): string {
  if (!content) return content;
  // 快速短路：既无反引号、也无任何数学定界符时直接返回，省掉正则与 split 开销。
  if (
    !content.includes("`") &&
    !content.includes("\\(") &&
    !content.includes("\\[") &&
    !content.includes("$")
  ) {
    return content;
  }

  const cleaned = stripEmptyFenceBlocks(scrubBacktickNoise(content));

  return cleaned
    .split(CODE_FENCE_SPLIT_RE)
    .map((part) => (/^(?:```|~~~)/.test(part) ? part : transformProse(part)))
    .join("");
}
