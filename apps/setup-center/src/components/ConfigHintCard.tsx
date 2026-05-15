// ConfigHintCard — actionable card rendered inside ToolCallDetail when the
// backend emits a `config_hint` SSE event (see chatTypes.StreamEvent and
// src/openakita/core/reasoning_engine.py:_build_tool_end_events).
//
// Why a dedicated component (instead of inline rendering in ThinkingChain)?
//   - The hint payload is structured (error_code-driven severity, action
//     list with URLs / navigation targets) — keeping the rendering logic
//     in one place avoids special-casing every tool result viewer.
//   - The card needs to coordinate with the settings-side <details> via
//     ``dispatchExpandPanel(anchor)`` and hash-based view navigation; that
//     bridging is awkward to read inline.
//
// Severity mapping by error_code:
//   missing_credential  → info     (user just needs to plug in a key)
//   auth_failed         → warning  (credential present but wrong)
//   rate_limited        → warning  (transient — try again later or switch)
//   network_unreachable → warning  (probably proxy / VPN issue)
//   content_filter      → info     (rephrase query)
//   unknown             → error    (genuine failure, escalate)

import { useTranslation } from "react-i18next";

import { dispatchExpandPanel } from "../hooks/useExpandPanel";
import type { ConfigHintPayload } from "../types";

// Re-export the canonical type so existing call sites that imported
// ``ConfigHintPayload`` from this module keep working — the source of truth
// lives in ``types.ts`` (shared with ChainEntry and ChatToolCall) so all
// three render paths can never drift apart.
export type { ConfigHintPayload };

// Single-action shape derived from the payload — convenience alias only,
// not a parallel definition (kept here for local readability).
export type ConfigHintAction = NonNullable<ConfigHintPayload["actions"]>[number];

interface ConfigHintCardProps {
  hint: ConfigHintPayload;
}

// Backend section names (free-form per handler) → hash step ids understood
// by App.tsx::_parseHashRoute. Add entries as new tools start emitting hints.
const SECTION_TO_STEP: Record<string, string> = {
  "tools-and-skills": "tools",
  "tools": "tools",
  "llm": "llm",
  "im": "im",
  "agent": "agent",
  "advanced": "advanced",
};

const SEVERITY_BY_CODE: Record<ConfigHintPayload["error_code"], "info" | "warning" | "error"> = {
  missing_credential: "info",
  auth_failed: "warning",
  rate_limited: "warning",
  network_unreachable: "warning",
  content_filter: "info",
  unknown: "error",
};

function navigateAndExpand(action: ConfigHintAction): void {
  // Step 1: switch view via hash (App.tsx listens for ``hashchange``).
  // Only do this for actions explicitly targeting the config view; others
  // (no view, only anchor) just toggle the panel in-place.
  if (action.view === "config") {
    const step = action.section ? SECTION_TO_STEP[action.section] || "tools" : "tools";
    const targetHash = `#/config/${step}`;
    if (window.location.hash !== targetHash) {
      window.location.hash = targetHash;
    }
  }

  // Step 2: dispatch the expand event after a short delay so the wizard view
  // has a chance to mount its <details> elements before we look for them.
  // 80ms is empirical: long enough for React to commit, short enough that
  // the user perceives the action as "instant".
  if (action.anchor) {
    window.setTimeout(() => {
      dispatchExpandPanel(action.anchor!);
    }, 80);
  }
}

function isExternalLink(action: ConfigHintAction): boolean {
  return !!action.url && /^https?:\/\//i.test(action.url);
}

export function ConfigHintCard({ hint }: ConfigHintCardProps) {
  const { t } = useTranslation();
  const severity = SEVERITY_BY_CODE[hint.error_code] ?? "info";
  const actions = hint.actions || [];

  // Translate the title/message via i18n keys when present, falling back to
  // the backend-provided strings (which are already user-friendly Chinese).
  // Format: ``chat.configHint.<error_code>.title`` / ``.message``.
  const i18nTitle = t(`chat.configHint.${hint.error_code}.title`, {
    defaultValue: hint.title,
  });
  const i18nMessage = t(`chat.configHint.${hint.error_code}.message`, {
    defaultValue: hint.message || "",
  });

  return (
    <div
      className="configHintCard"
      data-code={hint.error_code}
      data-severity={severity}
      data-scope={hint.scope}
      role="status"
    >
      <div className="configHintCardTitle">
        <span aria-hidden="true">
          {severity === "error" ? "!" : severity === "warning" ? "!" : "i"}
        </span>
        <strong>{i18nTitle}</strong>
      </div>

      {i18nMessage && (
        <div className="configHintCardMessage">{i18nMessage}</div>
      )}

      {actions.length > 0 && (
        <div className="configHintCardActions">
          {actions.map((action, idx) => {
            const label = action.label || t("chat.configHint.actionDefault", "查看详情");
            // First navigation action gets the primary style — usually
            // "open settings" which is what we want users to click first.
            const isPrimary = idx === 0 && !isExternalLink(action);
            if (isExternalLink(action)) {
              return (
                <a
                  key={action.id || idx}
                  className="configHintAction"
                  href={action.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {label}
                </a>
              );
            }
            return (
              <button
                key={action.id || idx}
                type="button"
                className="configHintAction"
                data-primary={isPrimary ? "true" : undefined}
                onClick={() => navigateAndExpand(action)}
              >
                {label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default ConfigHintCard;
