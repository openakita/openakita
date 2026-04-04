import { useState, useMemo, useCallback } from "react";
import { useTranslation } from "react-i18next";
import type { ChatAskUser, ChatAskQuestion } from "../utils/chatTypes";
import { formatAskUserAnswer } from "../utils/chatHelpers";

function AskQuestionItem({
  question,
  selected,
  onSelect,
  otherText,
  onOtherText,
  showOther,
  onToggleOther,
  letterOffset,
  onSubmit,
}: {
  question: ChatAskQuestion;
  selected: Set<string>;
  onSelect: (optId: string) => void;
  otherText: string;
  onOtherText: (v: string) => void;
  showOther: boolean;
  onToggleOther: () => void;
  letterOffset?: number;
  onSubmit?: () => void;
}) {
  const { t } = useTranslation();
  const optionLetters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const hasOptions = question.options && question.options.length > 0;
  const isMulti = question.allow_multiple === true;

  return (
    <div style={{ marginBottom: 4 }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6, color: "var(--fg, #333)" }}>
        {question.prompt}
        {isMulti && <span style={{ fontWeight: 400, fontSize: 12, opacity: 0.55, marginLeft: 6 }}>({t("chat.multiSelect", "可多选")})</span>}
      </div>
      {hasOptions ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {question.options!.map((opt, idx) => {
            const isSelected = selected.has(opt.id);
            return (
              <button
                key={opt.id}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "7px 14px", borderRadius: 8,
                  border: isSelected ? "1.5px solid rgba(124,58,237,0.55)" : "1px solid rgba(124,58,237,0.18)",
                  background: isSelected ? "rgba(124,58,237,0.10)" : "var(--panel)",
                  cursor: "pointer", fontSize: 13, textAlign: "left",
                  transition: "all 0.15s",
                }}
                onMouseEnter={(e) => { if (!isSelected) { e.currentTarget.style.background = "rgba(124,58,237,0.06)"; e.currentTarget.style.borderColor = "rgba(124,58,237,0.35)"; } }}
                onMouseLeave={(e) => { if (!isSelected) { e.currentTarget.style.background = "var(--panel)"; e.currentTarget.style.borderColor = "rgba(124,58,237,0.18)"; } }}
                onClick={() => onSelect(opt.id)}
              >
                <span style={{
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  width: 22, height: 22, borderRadius: isMulti ? 4 : 11, flexShrink: 0,
                  background: isSelected ? "rgba(124,58,237,0.85)" : "rgba(124,58,237,0.10)",
                  color: isSelected ? "#fff" : "rgba(124,58,237,0.8)",
                  fontSize: 11, fontWeight: 700, transition: "all 0.15s", lineHeight: 1,
                }}>
                  {optionLetters[(letterOffset || 0) + idx] || String(idx + 1)}
                </span>
                <span>{opt.label}</span>
              </button>
            );
          })}
          {!showOther ? (
            <button
              style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "7px 14px", borderRadius: 8,
                border: "1px dashed rgba(124,58,237,0.18)",
                background: "transparent",
                cursor: "pointer", fontSize: 13, textAlign: "left",
                transition: "all 0.15s", opacity: 0.55,
              }}
              onMouseEnter={(e) => { e.currentTarget.style.opacity = "1"; e.currentTarget.style.borderColor = "rgba(124,58,237,0.4)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.opacity = "0.55"; e.currentTarget.style.borderColor = "rgba(124,58,237,0.18)"; }}
              onClick={onToggleOther}
            >
              <span style={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                width: 22, height: 22, borderRadius: isMulti ? 4 : 11, flexShrink: 0,
                background: "rgba(0,0,0,0.04)", color: "rgba(0,0,0,0.35)",
                fontSize: 11, fontWeight: 700, lineHeight: 1,
              }}>…</span>
              <span>{t("chat.otherOption", "其他（手动输入）")}</span>
            </button>
          ) : (
            <input
              autoFocus
              value={otherText}
              onChange={(e) => onOtherText(e.target.value)}
              placeholder={t("chat.askPlaceholder")}
              style={{ fontSize: 13, padding: "7px 12px", borderRadius: 8, border: "1px solid rgba(124,58,237,0.25)", outline: "none" }}
              onKeyDown={(e) => { if (e.nativeEvent.isComposing || e.keyCode === 229) return; if (e.key === "Escape") onToggleOther(); if (e.key === "Enter" && otherText.trim()) onSubmit?.(); }}
            />
          )}
        </div>
      ) : (
        <input
          autoFocus
          value={otherText}
          onChange={(e) => onOtherText(e.target.value)}
          placeholder={t("chat.askPlaceholder")}
          style={{ width: "100%", fontSize: 13, padding: "7px 12px", borderRadius: 8, border: "1px solid rgba(124,58,237,0.25)", outline: "none", boxSizing: "border-box" }}
          onKeyDown={(e) => { if (e.nativeEvent.isComposing || e.keyCode === 229) return; if (e.key === "Enter" && otherText.trim()) onSubmit?.(); }}
        />
      )}
    </div>
  );
}

export function AskUserBlock({ ask, onAnswer }: { ask: ChatAskUser; onAnswer: (answer: string) => void }) {
  const { t } = useTranslation();

  const normalizedQuestions: ChatAskQuestion[] = useMemo(() => {
    if (ask.questions && ask.questions.length > 0) return ask.questions;
    return [{
      id: "__single__",
      prompt: ask.question,
      options: ask.options,
      allow_multiple: false,
    }];
  }, [ask]);

  const isSingle = normalizedQuestions.length === 1;

  const [selections, setSelections] = useState<Record<string, Set<string>>>(() => {
    const init: Record<string, Set<string>> = {};
    normalizedQuestions.forEach((q) => { init[q.id] = new Set(); });
    return init;
  });
  const [otherTexts, setOtherTexts] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    normalizedQuestions.forEach((q) => { init[q.id] = ""; });
    return init;
  });
  const [showOthers, setShowOthers] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {};
    normalizedQuestions.forEach((q) => { init[q.id] = !(q.options && q.options.length > 0); });
    return init;
  });

  const handleSelect = useCallback((qId: string, optId: string, isMulti: boolean) => {
    setSelections((prev) => {
      const s = new Set(prev[qId]);
      if (isMulti) {
        if (s.has(optId)) s.delete(optId); else s.add(optId);
      } else {
        if (s.has(optId)) {
          s.clear();
        } else {
          s.clear();
          s.add(optId);
        }
        if (isSingle && s.size > 0) {
          onAnswer(optId);
          return prev;
        }
      }
      return { ...prev, [qId]: s };
    });
  }, [isSingle, onAnswer]);

  const handleSubmit = useCallback(() => {
    if (isSingle) {
      const q = normalizedQuestions[0];
      const sel = selections[q.id];
      const other = otherTexts[q.id]?.trim();
      if (sel && sel.size > 0) {
        const arr = Array.from(sel);
        if (other) arr.push(`OTHER:${other}`);
        onAnswer(q.allow_multiple ? arr.join(",") : arr[0]);
      } else if (other) {
        onAnswer(other);
      }
      return;
    }
    const result: Record<string, string | string[]> = {};
    normalizedQuestions.forEach((q) => {
      const sel = selections[q.id];
      const other = otherTexts[q.id]?.trim();
      const arr = sel ? Array.from(sel) : [];
      if (other) arr.push(`OTHER:${other}`);
      if (arr.length === 0 && !other) return;
      result[q.id] = q.allow_multiple ? arr : (arr[0] || other || "");
    });
    if (Object.keys(result).length === 0) return;
    onAnswer(JSON.stringify(result));
  }, [isSingle, normalizedQuestions, selections, otherTexts, onAnswer]);

  if (ask.answered) {
    const displayAnswer = formatAskUserAnswer(ask.answer || "", ask);
    return (
      <div style={{ margin: "8px 0", padding: "10px 14px", borderRadius: 10, background: "rgba(37,99,235,0.06)", border: "1px solid rgba(37,99,235,0.15)" }}>
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>{ask.question}</div>
        <div style={{ fontSize: 13, opacity: 0.7 }}>{t("chat.answered")}{displayAnswer}</div>
      </div>
    );
  }

  const canSubmit = normalizedQuestions.some((q) => {
    const sel = selections[q.id];
    const other = otherTexts[q.id]?.trim();
    return (sel && sel.size > 0) || !!other;
  });

  return (
    <div style={{ margin: "8px 0", padding: "12px 14px", borderRadius: 12, background: "rgba(124,58,237,0.04)", border: "1px solid rgba(124,58,237,0.16)" }}>
      {!isSingle && (
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10, color: "var(--fg, #333)" }}>{ask.question}</div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {normalizedQuestions.map((q) => (
          <AskQuestionItem
            key={q.id}
            question={q}
            selected={selections[q.id] || new Set()}
            onSelect={(optId) => handleSelect(q.id, optId, q.allow_multiple === true)}
            otherText={otherTexts[q.id] || ""}
            onOtherText={(v) => setOtherTexts((prev) => ({ ...prev, [q.id]: v }))}
            showOther={showOthers[q.id] || false}
            onToggleOther={() => setShowOthers((prev) => ({ ...prev, [q.id]: !prev[q.id] }))}
            onSubmit={isSingle ? handleSubmit : undefined}
          />
        ))}
      </div>
      {(!isSingle || normalizedQuestions.some((q) => q.allow_multiple || !q.options?.length)) && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
          <button
            className="btnPrimary"
            disabled={!canSubmit}
            onClick={handleSubmit}
            style={{ fontSize: 13, padding: "7px 22px", opacity: canSubmit ? 1 : 0.4, cursor: canSubmit ? "pointer" : "not-allowed" }}
          >
            {t("chat.submitAnswer", "提交")}
          </button>
        </div>
      )}
    </div>
  );
}
