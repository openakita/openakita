import { useTranslation } from "react-i18next";

// P0-2 后续：把 LLM 输出里的 `[来源:工具]` / `[来源:历史]` / `[来源:常识]` /
// `[来源:不确定]` 反幻觉来源标签从"裸文本"渲染成可视化 badge。
//
// 这是 OpenAkita 防幻觉机制的一部分——后端 prompt 要求 LLM 在涉及外部事实的
// 句段后显式标注来源（详见 src/openakita/prompt/builder.py），过去前端原样
// 显示成 `[来源:工具]`，看起来像"模型 leak 出了系统提示词"，本组件统一渲染
// 为带 tooltip 的彩色徽章。
//
// 提供三种用法：
//   - <SourceBadge type="工具" />     单独渲染一个 badge（少见）
//   - <TextWithSourceBadges text=.. /> 用于纯文本上下文（thinking chain / thinking block）
//   - useSourceTagFormatter()        对 markdown 源字符串做 HTML span 替换，
//                                    依赖 useMdModules 的 rehype-raw + rehype-sanitize 把
//                                    span (className + title) 渲染出来。

export type SourceType = "工具" | "历史" | "常识" | "不确定";

export const SOURCE_TYPE_KIND: Record<SourceType, "tool" | "history" | "knowledge" | "uncertain"> = {
  "工具": "tool",
  "历史": "history",
  "常识": "knowledge",
  "不确定": "uncertain",
};

// Stateful regex must be re-created per scan (see splitTextWithSourceTags).
const SOURCE_TAG_PATTERN = "\\[来源[:：]\\s*(工具|历史|常识|不确定)\\s*\\]";

export function SourceBadge({ type }: { type: SourceType }) {
  const { t } = useTranslation();
  const kind = SOURCE_TYPE_KIND[type];
  const label = t(`chat.sourceTag.${kind}`, type);
  const tip = t(`chat.sourceTagTip.${kind}`, "");
  return (
    <span className={`srcBadge srcBadge-${kind}`} title={tip || undefined}>
      {label}
    </span>
  );
}

export type TextSegment =
  | { kind: "text"; value: string }
  | { kind: "badge"; type: SourceType };

export function splitTextWithSourceTags(text: string): TextSegment[] {
  if (!text) return [];
  const segs: TextSegment[] = [];
  const re = new RegExp(SOURCE_TAG_PATTERN, "g");
  let lastIdx = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > lastIdx) {
      segs.push({ kind: "text", value: text.slice(lastIdx, m.index) });
    }
    segs.push({ kind: "badge", type: m[1] as SourceType });
    lastIdx = m.index + m[0].length;
  }
  if (lastIdx === 0) return [{ kind: "text", value: text }];
  if (lastIdx < text.length) {
    segs.push({ kind: "text", value: text.slice(lastIdx) });
  }
  return segs;
}

/**
 * Render plain text with inline source badges. Suitable for non-markdown contexts
 * (ThinkingChain entries, ThinkingBlock content). Preserves the original surface
 * whitespace by emitting each text segment as-is into a single span.
 */
export function TextWithSourceBadges({ text }: { text: string }) {
  const segs = splitTextWithSourceTags(text);
  if (segs.length === 0) return null;
  return (
    <>
      {segs.map((s, i) =>
        s.kind === "text" ? (
          <span key={i}>{s.value}</span>
        ) : (
          <SourceBadge key={i} type={s.type} />
        ),
      )}
    </>
  );
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Pre-process a markdown source string by replacing `[来源:X]` tokens with raw
 * `<span class="srcBadge srcBadge-xxx" title="...">label</span>` HTML. Relies on
 * useMdModules.ts's rehype-raw + rehype-sanitize pipeline, which already allows
 * `span` with `className` (and the GitHub default schema allows `title`).
 *
 * Labels/tips are baked in at transform time from the current i18n locale, so
 * the returned string is self-contained and locale-correct.
 *
 * Fenced code blocks (```...```) are preserved verbatim — if a tutorial / agent
 * example literally shows `[来源:工具]` inside a code fence we must not turn it
 * into a styled span (that would change the meaning of the demonstrated text).
 */
export function transformSourceTagsForMarkdown(
  text: string,
  getLabel: (type: SourceType) => string,
  getTip: (type: SourceType) => string,
): string {
  if (!text) return text;
  // Split keeps the delimiter (fenced code block) at odd indices. Unclosed
  // fences (common during streaming) land in the trailing odd part and are
  // therefore conservatively preserved — which is the safe default.
  const parts = text.split(/(```[\s\S]*?```)/g);
  return parts
    .map((part, idx) => {
      if (idx % 2 === 1) return part;
      return part.replace(/\[来源[:：]\s*(工具|历史|常识|不确定)\s*\]/g, (_match, type: string) => {
        const t = type as SourceType;
        const kind = SOURCE_TYPE_KIND[t];
        const label = escapeHtml(getLabel(t));
        const tip = escapeHtml(getTip(t));
        return `<span class="srcBadge srcBadge-${kind}" title="${tip}">${label}</span>`;
      });
    })
    .join("");
}

/**
 * Hook returning a stable transformer that bakes the current locale's
 * labels/tips into markdown source strings before they hit ReactMarkdown.
 */
export function useSourceTagFormatter(): (text: string) => string {
  const { t } = useTranslation();
  const getLabel = (type: SourceType) =>
    t(`chat.sourceTag.${SOURCE_TYPE_KIND[type]}`, type);
  const getTip = (type: SourceType) =>
    t(`chat.sourceTagTip.${SOURCE_TYPE_KIND[type]}`, "");
  return (text: string) => transformSourceTagsForMarkdown(text, getLabel, getTip);
}
