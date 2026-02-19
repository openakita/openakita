import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
import { PhysicalPosition, PhysicalSize, currentMonitor, getCurrentWindow } from "@tauri-apps/api/window";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { IconChevronDown, IconChevronUp, IconMinus, IconPin, IconSend } from "../icons";

function TypewriterPlaceholder({ text }: { text: string }) {
  const [displayed, setDisplayed] = useState("");
  useEffect(() => {
    let idx = 0, erasing = false, tid: ReturnType<typeof setTimeout>;
    const tick = () => {
      if (!erasing) {
        idx++;
        setDisplayed(text.slice(0, idx));
        tid = setTimeout(tick, idx >= text.length ? 3000 : 80);
        if (idx >= text.length) erasing = true;
      } else {
        idx--;
        setDisplayed(text.slice(0, idx));
        tid = setTimeout(tick, idx <= 0 ? 500 : 25);
        if (idx <= 0) erasing = false;
      }
    };
    tid = setTimeout(tick, 40);
    return () => clearTimeout(tid);
  }, [text]);
  return (
    <span className="typewriterPlaceholder">
      {displayed}
    </span>
  );
}

type FloatingUiPrefs = {
  alwaysOnTop: boolean;
  opacity: number;
  selectedEndpoint?: string | null;
};

type StreamEvent =
  | { type: "text_delta"; content: string }
  | { type: "error"; message: string }
  | { type: "done" }
  | { type: string; content?: string; message?: string };

export function FloatingChatView({
  serviceRunning,
  apiBaseUrl = "http://127.0.0.1:18900",
  onStartService,
  compact = false,
  modeControl = null,
}: {
  serviceRunning: boolean;
  apiBaseUrl?: string;
  onStartService: () => void | Promise<void>;
  /**
   * `compact=true` 对应“极简模式”:
   * - 目标是“一行悬浮输入 + 可展开结果”
   * - 该模式用于 App 的 uiMode=minimal（三形态中的极简）
   */
  compact?: boolean;
  // App 注入的“传统/正常/极简”切换按钮，在极简悬浮窗中保留。
  modeControl?: ReactNode;
}) {
  // 宽度统一缩放：当前对话条基准宽度按 0.7 缩放。
  // 注意：该值需与 Rust `set_minimal_floating_mode` 的 `target_width` 保持同步。
  const COMPACT_WIDTH_BASE = 1290;
  const COMPACT_WIDTH_SCALE = 0.7;
  const COMPACT_DEFAULT_WIDTH = Math.round(COMPACT_WIDTH_BASE * COMPACT_WIDTH_SCALE); // 903
  const { t } = useTranslation();
  const [input, setInput] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [opacityOpen, setOpacityOpen] = useState(false);
  const [prefs, setPrefs] = useState<FloatingUiPrefs>({
    alwaysOnTop: false,
    opacity: 0.92,
    selectedEndpoint: null,
  });
  const dragCleanupRef = useRef<(() => void) | null>(null);
  const compactWidthInitializedRef = useRef(false);

  const selectedEndpoint = prefs.selectedEndpoint || "auto";
  const currentWindow = useMemo(() => getCurrentWindow(), []);

  const persistPrefs = useCallback(async (next: FloatingUiPrefs, applyWindow = false) => {
    setPrefs(next);
    if (applyWindow) {
      await invoke("set_window_always_on_top", { alwaysOnTop: next.alwaysOnTop }).catch(() => {});
      await invoke("set_window_opacity", { opacity: next.opacity }).catch(() => {});
    }
    await invoke("set_floating_ui_prefs", {
      prefs: {
        alwaysOnTop: next.alwaysOnTop,
        opacity: next.opacity,
        selectedEndpoint: next.selectedEndpoint || null,
      },
    }).catch(() => {});
  }, []);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const remote = await invoke<FloatingUiPrefs>("get_floating_ui_prefs");
        if (!mounted) return;
        const normalized: FloatingUiPrefs = {
          alwaysOnTop: !!remote?.alwaysOnTop,
          opacity: Math.min(1, Math.max(0.35, Number(remote?.opacity ?? 0.92))),
          selectedEndpoint: remote?.selectedEndpoint || null,
        };
        setPrefs(normalized);
        await invoke("set_window_always_on_top", { alwaysOnTop: normalized.alwaysOnTop }).catch(() => {});
        await invoke("set_window_opacity", { opacity: normalized.opacity }).catch(() => {});
      } catch {
        // keep defaults
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  // 透明度作用到整个窗口层（含背景层与白色面板）。
  useEffect(() => {
    if (!compact) {
      document.body.style.opacity = "";
      return;
    }
    const alpha = String(Math.min(1, Math.max(0.35, prefs.opacity)));
    document.body.style.opacity = alpha;
    return () => {
      document.body.style.opacity = "";
    };
  }, [compact, prefs.opacity]);

  // 极简模式：锁定高度，仅允许左右拉伸
  useEffect(() => {
    if (!compact) {
      // 退出极简时清除 maxSize 约束
      void currentWindow.setMaxSize(null as unknown as PhysicalSize).catch(() => {});
      void currentWindow.setMinSize(null as unknown as PhysicalSize).catch(() => {});
      void currentWindow.setResizable(true).catch(() => {});
      compactWidthInitializedRef.current = false;
      setOpacityOpen(false);
      return;
    }
    void currentWindow.setResizable(false).catch(() => {});
    const hasResult = Boolean(question.trim()) || Boolean(answer.trim()) || streaming;
    let h = expanded || streaming ? 548 : hasResult ? 116 : 60;
    if (opacityOpen) h += 34;
    void (async () => {
      try {
        // 兜底：进入极简时强制一次默认宽度，避免只改外框未改白窗体感。
        if (!compactWidthInitializedRef.current) {
          const size = await currentWindow.outerSize();
          const monitor = await currentMonitor();
          const monitorWidth = monitor?.size?.width ?? 0;
          const maxSafeWidth = monitorWidth > 120 ? monitorWidth - 80 : COMPACT_DEFAULT_WIDTH;
          const desiredWidth = Math.max(size.width, Math.min(COMPACT_DEFAULT_WIDTH, maxSafeWidth));
          if (Math.abs(size.width - desiredWidth) > 3) {
            await currentWindow.setSize(new PhysicalSize(desiredWidth, size.height));
          }
          compactWidthInitializedRef.current = true;
        }

        await currentWindow.setMinSize(new PhysicalSize(420, h));
        await currentWindow.setMaxSize(new PhysicalSize(9999, h));
        const size = await currentWindow.outerSize();
        if (Math.abs(size.height - h) > 3) {
          await currentWindow.setSize(new PhysicalSize(size.width, h));
        }
      } catch { /* ignore */ }
    })();
  }, [compact, currentWindow, expanded, streaming, question, answer, opacityOpen, COMPACT_DEFAULT_WIDTH]);

  const startWindowDragFallback = useCallback((startScreenX: number, startScreenY: number) => {
    void (async () => {
      const startPos = await currentWindow.outerPosition().catch(() => null);
      if (!startPos) return;

      const onMove = (ev: MouseEvent) => {
        const dx = ev.screenX - startScreenX;
        const dy = ev.screenY - startScreenY;
        void currentWindow.setPosition(
          new PhysicalPosition(startPos.x + dx, startPos.y + dy),
        ).catch(() => {});
      };
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        dragCleanupRef.current = null;
      };
      dragCleanupRef.current = onUp;
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp, { once: true });
    })();
  }, [currentWindow]);

  const startWindowDrag = useCallback((e: React.MouseEvent<HTMLElement>) => {
    if (!compact || e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    // 历史坑点：`startDragging()` 在部分环境会出现“光标变拖拽但窗口不移动”。
    // 这里固定走手动位移链路，保证拖拽稳定。
    startWindowDragFallback(e.screenX, e.screenY);
  }, [compact, startWindowDragFallback]);

  useEffect(() => {
    return () => {
      if (dragCleanupRef.current) dragCleanupRef.current();
    };
  }, []);

  const minimizeWindow = useCallback(() => {
    void currentWindow.minimize().catch(() => {});
  }, [currentWindow]);

  const toggleAlwaysOnTop = useCallback(() => {
    const next = { ...prefs, alwaysOnTop: !prefs.alwaysOnTop };
    void persistPrefs(next, true);
  }, [persistPrefs, prefs]);

  const updateOpacity = useCallback((nextOpacity: number) => {
    const next = {
      ...prefs,
      opacity: Math.min(1, Math.max(0.35, nextOpacity)),
    };
    void persistPrefs(next, true);
  }, [persistPrefs, prefs]);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || streaming) return;

    if (!serviceRunning) {
      setError(t("floating.serviceRequired"));
      await onStartService();
      return;
    }

    setError(null);
    setExpanded(true);
    setQuestion(text);
    setAnswer("");
    setInput("");
    setStreaming(true);

    try {
      const response = await fetch(`${apiBaseUrl}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: "floating_default",
          message: text,
          endpoint: selectedEndpoint === "auto" ? null : selectedEndpoint,
          plan_mode: false,
          thinking_mode: "off",
          thinking_depth: "medium",
          attachments: [],
        }),
      });
      if (!response.ok || !response.body) {
        const details = await response.text().catch(() => "");
        throw new Error(`${response.status} ${details}`.trim());
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let current = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const data = line.slice(6).trim();
          if (!data || data === "[DONE]") continue;
          try {
            const event = JSON.parse(data) as StreamEvent;
            if (event.type === "text_delta") {
              current += event.content || "";
              setAnswer(current);
              continue;
            }
            if (event.type === "error") {
              current += `\n\n错误: ${event.message || "unknown"}`;
              setAnswer(current);
              continue;
            }
            if (event.type === "done") {
              setAnswer(current || t("floating.emptyAnswer"));
              break;
            }
          } catch {
            // ignore malformed event
          }
        }
      }
      if (!current.trim()) {
        setAnswer((prev) => prev.trim() ? prev : t("floating.emptyAnswer"));
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      setAnswer(`连接失败: ${msg}`);
    } finally {
      setStreaming(false);
    }
  }, [apiBaseUrl, input, onStartService, selectedEndpoint, serviceRunning, streaming, t]);

  return (
    <div
      className={compact ? "floatingRoot floatingRootCompact" : "floatingRoot"}
      style={{
        // 历史坑点：极简壳层是 flex 布局，若不显式拉满宽度会触发 shrink-to-fit。
        // 一旦父容器被收缩，子层 `width:100%` 会失效为“收缩后的 100%”，导致白窗看起来没变宽。
        width: compact ? "100%" : undefined,
        height: "100%",
        display: "flex",
        justifyContent: "center",
        alignItems: compact ? "center" : "flex-start",
        padding: compact ? 0 : "18px 16px 12px",
        overflow: "hidden",
        cursor: compact ? "grab" : undefined,
      }}
    >
      <div
        className={compact ? "floatingFrame floatingFrameCompact" : "floatingFrame"}
        style={{
          position: "relative",
          width: compact ? "100%" : "min(920px, 100%)",
          maxWidth: "100%",
          display: "flex",
          flexDirection: "column",
          gap: compact ? 6 : 10,
          ...(compact ? { flex: 1, justifyContent: "center" } : {}),
        }}
      >
        <div
          className="card"
          style={{ width: "100%", padding: compact ? "4px 6px" : 10, borderRadius: compact ? 999 : 12 }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: compact ? 4 : 8 }}>
            {compact && (
              <div
                className="floatingDragHandle"
                data-tauri-drag-region
                onMouseDown={startWindowDrag}
                title={t("floating.dragHandle")}
                aria-label={t("floating.dragHandle")}
              >
                <span>⋮⋮</span>
              </div>
            )}
            <div style={{ position: "relative", flex: 1, minWidth: 0 }}>
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onMouseDown={(e) => e.stopPropagation()}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void sendMessage();
                  }
                }}
                placeholder=""
                style={{ height: compact ? 28 : 34, width: "100%", boxSizing: "border-box" }}
              />
              {!input && (
                <div style={{ position: "absolute", left: 10, top: 0, bottom: 0, display: "flex", alignItems: "center", pointerEvents: "none", opacity: 0.45, fontSize: compact ? 12 : 13 }}>
                  <TypewriterPlaceholder text={t("floating.inputPlaceholder")} />
                </div>
              )}
            </div>
            <button
              className="btnPrimary"
              style={{ height: compact ? 28 : 34, minWidth: compact ? 32 : 58, padding: compact ? "0 6px" : "0 12px" }}
              disabled={streaming || (!input.trim() && serviceRunning)}
              onMouseDown={(e) => e.stopPropagation()}
              onClick={() => void sendMessage()}
              title={t("chat.send")}
            >
              <IconSend size={compact ? 12 : 14} />
            </button>
            {compact && (
              <>
                {modeControl}
                <button
                  className="chatTopBarBtn floatingOpacityBtn"
                  onClick={() => setOpacityOpen((v) => !v)}
                  onMouseDown={(e) => e.stopPropagation()}
                  title={t("floating.opacity")}
                >
                  {Math.round(prefs.opacity * 100)}%
                </button>
                <button
                  className="chatTopBarBtn"
                  onClick={toggleAlwaysOnTop}
                  onMouseDown={(e) => e.stopPropagation()}
                  title={prefs.alwaysOnTop ? t("floating.unpin") : t("floating.pin")}
                  style={{ color: prefs.alwaysOnTop ? "var(--brand)" : undefined }}
                >
                  <IconPin size={13} />
                </button>
                <button className="chatTopBarBtn" onMouseDown={(e) => e.stopPropagation()} onClick={minimizeWindow} title={t("floating.minimize")}>
                  <IconMinus size={13} />
                </button>
              </>
            )}
          </div>
          {compact && opacityOpen && (
            <div className="floatingOpacityPanel" onMouseDown={(e) => e.stopPropagation()}>
              <span>{t("floating.opacity")}</span>
              <input
                type="range"
                min={35}
                max={100}
                step={1}
                value={Math.round(prefs.opacity * 100)}
                onChange={(e) => updateOpacity(Number(e.target.value) / 100)}
              />
              <span className="floatingOpacityValue">{Math.round(prefs.opacity * 100)}%</span>
            </div>
          )}

          {!serviceRunning && (
            <div style={{ marginTop: 10, fontSize: 12, color: "var(--muted)" }}>
              {t("floating.serviceHint")}
            </div>
          )}
          {error && (
            <div style={{ marginTop: 10, fontSize: 12, color: "var(--danger)" }}>{error}</div>
          )}
        </div>

        {(expanded || streaming || answer) && (
          <div className="card" style={{ width: "100%", padding: 0, borderRadius: compact ? 10 : 12, overflow: "hidden" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 12px", borderBottom: "1px solid var(--line)" }}>
              <div style={{ fontSize: 12, color: "var(--muted)" }}>{t("floating.resultTitle")}</div>
              <button className="btnSmall" onClick={() => setExpanded((v) => !v)} title={expanded ? t("chat.collapse") : t("chat.expand")}>
                {expanded ? <IconChevronUp size={13} /> : <IconChevronDown size={13} />}
              </button>
            </div>
            {expanded && (
              <div className="floatingResultScroll" style={{ padding: 12, maxHeight: "56vh", overflow: "auto" }}>
                {question && (
                  <div style={{ marginBottom: 10, padding: "8px 10px", borderRadius: 8, background: "var(--bg-subtle)", fontSize: 13 }}>
                    <strong>Q:</strong> {question}
                  </div>
                )}
                <div className="chatMdContent">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {answer || (streaming ? t("chat.streaming") : t("floating.emptyAnswer"))}
                  </ReactMarkdown>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
