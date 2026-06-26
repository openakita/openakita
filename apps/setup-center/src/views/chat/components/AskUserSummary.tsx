import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { ChatAskUser, ChatAskQuestion } from "../utils/chatTypes";

/**
 * Read-only summary of an answered ask_user prompt.
 *
 * Survives reload / window switch (the answered state is now persisted in
 * backend history), and renders the resolved "question -> chosen answer(s)"
 * layout from the user's screenshot instead of re-offering clickable options.
 */

function labelForValue(value: string, options?: { id: string; label: string }[]): string {
  if (value.startsWith("OTHER:")) return value.slice(6);
  return options?.find((o) => o.id === value)?.label ?? value;
}

type AnsweredRow = { prompt: string; labels: string[] };

function buildRows(ask: ChatAskUser, answer: string): AnsweredRow[] {
  const questions: ChatAskQuestion[] = ask.questions?.length
    ? ask.questions
    : [{ id: "__single__", prompt: ask.question, options: ask.options }];

  // Structured multi-question answers arrive as a JSON object keyed by question id.
  try {
    const parsed = JSON.parse(answer);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const rows = questions
        .map((q) => {
          const val = (parsed as Record<string, unknown>)[q.id];
          if (val == null || val === "") return null;
          const vals = Array.isArray(val) ? (val as string[]) : [String(val)];
          return { prompt: q.prompt, labels: vals.map((v) => labelForValue(String(v), q.options)) };
        })
        .filter(Boolean) as AnsweredRow[];
      if (rows.length > 0) return rows;
    }
  } catch {
    /* not JSON — fall through to single-answer handling */
  }

  // Single-question answer: may be a comma-joined id list or a free-text string.
  const q = questions[0];
  const opts = ask.options || q?.options;
  let labels: string[];
  if (answer.includes(",") && opts) {
    const ids = answer.split(",");
    const allKnown = ids.every((id) => id.startsWith("OTHER:") || opts.some((o) => o.id === id));
    labels = allKnown ? ids.map((id) => labelForValue(id, opts)) : [answer];
  } else {
    labels = [labelForValue(answer, opts)];
  }
  return [{ prompt: q?.prompt || ask.question, labels }];
}

export function AskUserSummary({ ask }: { ask: ChatAskUser }) {
  const { t } = useTranslation();
  const answer = ask.answer || "";
  const rows = useMemo(() => buildRows(ask, answer), [ask, answer]);
  const isMulti = (ask.questions?.length ?? 0) > 1;

  return (
    <div
      style={{
        margin: "8px 0",
        padding: "10px 14px",
        borderRadius: 10,
        background: "rgba(37,99,235,0.06)",
        border: "1px solid rgba(37,99,235,0.15)",
      }}
    >
      <div style={{ fontSize: 11, fontWeight: 700, opacity: 0.55, marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.4 }}>
        {t("chat.answered", "已回答")}
      </div>
      {isMulti && ask.question && (
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>{ask.question}</div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {rows.map((row, i) => (
          <div key={i} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontSize: 12, opacity: 0.65 }}>{row.prompt}</span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {row.labels.map((label, j) => (
                <span
                  key={j}
                  style={{
                    fontSize: 12.5,
                    fontWeight: 600,
                    padding: "2px 10px",
                    borderRadius: 999,
                    background: "rgba(37,99,235,0.12)",
                    color: "var(--brand, #2563eb)",
                  }}
                >
                  {label}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
