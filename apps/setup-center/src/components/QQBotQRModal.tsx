import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { invoke, IS_TAURI } from "../platform";
import { safeFetch } from "../providers";
import { QRCodeSVG } from "qrcode.react";
import { ModalOverlay } from "./ModalOverlay";

type OnboardState =
  | "idle"
  | "loading"
  | "scanning"
  | "login_ok"
  | "creating"
  | "success"
  | "partial"
  | "error";

interface QQBotQRModalProps {
  venvDir: string;
  apiBaseUrl?: string;
  onClose: () => void;
  onSuccess: (appId: string, appSecret: string) => void;
}

async function onboardStart(
  venvDir: string,
  apiBaseUrl?: string,
): Promise<Record<string, any>> {
  if (IS_TAURI) {
    const raw = await invoke<string>("openakita_qqbot_onboard_start", {
      venvDir,
    });
    return JSON.parse(raw);
  }
  const base = apiBaseUrl || "";
  const res = await safeFetch(`${base}/api/qqbot/onboard/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  return res.json();
}

async function onboardPoll(
  venvDir: string,
  sessionId: string,
  apiBaseUrl?: string,
): Promise<Record<string, any>> {
  if (IS_TAURI) {
    const raw = await invoke<string>("openakita_qqbot_onboard_poll", {
      venvDir,
      sessionId,
    });
    return JSON.parse(raw);
  }
  const base = apiBaseUrl || "";
  const res = await safeFetch(`${base}/api/qqbot/onboard/poll`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  return res.json();
}

async function onboardPollAndCreate(
  venvDir: string,
  sessionId: string,
  apiBaseUrl?: string,
): Promise<Record<string, any>> {
  if (IS_TAURI) {
    const raw = await invoke<string>(
      "openakita_qqbot_onboard_poll_and_create",
      { venvDir, sessionId },
    );
    return JSON.parse(raw);
  }
  const base = apiBaseUrl || "";
  const res = await safeFetch(`${base}/api/qqbot/onboard/poll-and-create`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  return res.json();
}

export function QQBotQRModal({
  venvDir,
  apiBaseUrl,
  onClose,
  onSuccess,
}: QQBotQRModalProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<OnboardState>("idle");
  const [qrUrl, setQrUrl] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [error, setError] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const startOnboard = useCallback(async () => {
    setState("loading");
    setError("");
    try {
      const data = await onboardStart(venvDir, apiBaseUrl);
      if (!mountedRef.current) return;
      if (data.session_id && data.qr_url) {
        setSessionId(data.session_id);
        setQrUrl(data.qr_url);
        setState("scanning");
        startPolling(data.session_id);
      } else {
        setError(data.error || t("qqbot.qrInitFailed"));
        setState("error");
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      setError(String(e));
      setState("error");
    }
  }, [venvDir, apiBaseUrl, t]);

  const startPolling = useCallback(
    (sid: string) => {
      let attempts = 0;
      const maxAttempts = 150;

      pollRef.current = setInterval(async () => {
        attempts++;
        if (attempts > maxAttempts || !mountedRef.current) {
          if (pollRef.current) clearInterval(pollRef.current);
          if (mountedRef.current) {
            setError(t("qqbot.qrTimeout"));
            setState("error");
          }
          return;
        }
        try {
          const data = await onboardPoll(venvDir, sid, apiBaseUrl);
          if (!mountedRef.current) return;

          if (data.status === "ok" && data.developer_id) {
            if (pollRef.current) clearInterval(pollRef.current);
            setState("creating");

            // login succeeded — use atomic poll+create to preserve cookies
            try {
              const bot = await onboardPollAndCreate(
                venvDir,
                sid,
                apiBaseUrl,
              );
              if (!mountedRef.current) return;

              if (bot.app_id && bot.app_secret) {
                setState("success");
                onSuccess(bot.app_id, bot.app_secret);
              } else if (bot.app_id && bot.needs_secret) {
                onSuccess(bot.app_id, "");
                setState("partial");
              } else {
                setError(bot.error || t("qqbot.qrCreateFailed"));
                setState("error");
              }
            } catch (createErr: unknown) {
              if (!mountedRef.current) return;
              const msg = String(createErr);
              if (msg.includes("cookie") || msg.includes("凭证")) {
                setError(t("qqbot.qrNeedBrowser"));
              } else {
                setError(msg);
              }
              setState("error");
            }
            return;
          }

          if (data.status === "error") {
            if (pollRef.current) clearInterval(pollRef.current);
            setError(data.message || t("qqbot.qrInitFailed"));
            setState("error");
          }
        } catch {
          // polling error is non-fatal, keep trying
        }
      }, 2000);
    },
    [venvDir, apiBaseUrl, onSuccess, t],
  );

  useEffect(() => {
    startOnboard();
  }, [startOnboard]);

  return (
    <ModalOverlay onClose={onClose}>
      <div
        className="card"
        style={{
          width: 380,
          maxWidth: "90vw",
          padding: 24,
          position: "relative",
        }}
      >
        <button
          onClick={onClose}
          style={{
            position: "absolute",
            top: 8,
            right: 12,
            background: "none",
            border: "none",
            fontSize: 18,
            cursor: "pointer",
            color: "var(--text3)",
          }}
        >
          ×
        </button>

        <div style={{ textAlign: "center", marginBottom: 16 }}>
          <div className="cardTitle" style={{ marginBottom: 4 }}>
            {t("qqbot.qrTitle")}
          </div>
          <div style={{ fontSize: 12, color: "var(--text3)" }}>
            {t("qqbot.qrSubtitle")}
          </div>
        </div>

        {state === "loading" && (
          <div style={{ textAlign: "center", padding: 40 }}>
            <div
              className="spinner"
              style={{ width: 32, height: 32, margin: "0 auto" }}
            />
            <div
              style={{
                marginTop: 12,
                fontSize: 13,
                color: "var(--text3)",
              }}
            >
              {t("qqbot.qrLoading")}
            </div>
          </div>
        )}

        {state === "scanning" && qrUrl && (
          <div style={{ textAlign: "center" }}>
            <div
              style={{
                background: "white",
                padding: 16,
                borderRadius: 8,
                display: "inline-block",
                marginBottom: 12,
              }}
            >
              <QRCodeSVG value={qrUrl} size={200} />
            </div>
            <div
              style={{
                fontSize: 12,
                color: "var(--text3)",
                marginBottom: 6,
              }}
            >
              {t("qqbot.qrScanHint")}
            </div>
            <div
              style={{
                fontSize: 11,
                color: "var(--warning, #d97706)",
                lineHeight: 1.5,
                padding: "8px 12px",
                background: "var(--warning-bg, rgba(217,119,6,0.08))",
                borderRadius: 6,
                marginBottom: 8,
                textAlign: "left",
              }}
            >
              {t("qqbot.qrScanNote")}
            </div>
            <div
              style={{
                fontSize: 11,
                color: "var(--muted)",
                wordBreak: "break-all",
                padding: "4px 8px",
                background: "var(--bg2)",
                borderRadius: 4,
              }}
            >
              {qrUrl}
            </div>
          </div>
        )}

        {(state === "login_ok" || state === "creating") && (
          <div style={{ textAlign: "center", padding: 24 }}>
            <div
              className="spinner"
              style={{ width: 32, height: 32, margin: "0 auto" }}
            />
            <div
              style={{
                marginTop: 12,
                fontSize: 13,
                color: "var(--text3)",
              }}
            >
              {t("qqbot.qrLoginOk")}
            </div>
          </div>
        )}

        {state === "success" && (
          <div
            style={{
              textAlign: "center",
              padding: 24,
              color: "var(--success)",
            }}
          >
            <div style={{ fontSize: 32, marginBottom: 8 }}>✓</div>
            <div style={{ fontSize: 14 }}>{t("qqbot.qrSuccess")}</div>
          </div>
        )}

        {state === "partial" && (
          <div style={{ textAlign: "center", padding: 20 }}>
            <div style={{ fontSize: 28, marginBottom: 8, color: "var(--warning, #d97706)" }}>!</div>
            <div style={{ fontSize: 13, color: "var(--text2)", marginBottom: 12, lineHeight: 1.5 }}>
              {t("qqbot.qrPartialSuccess")}
            </div>
            <button className="btnSmall" onClick={onClose}>OK</button>
          </div>
        )}

        {state === "error" && (
          <div style={{ textAlign: "center", padding: 16 }}>
            <div
              style={{
                color: "var(--danger)",
                fontSize: 13,
                marginBottom: 12,
              }}
            >
              {error}
            </div>
            <button className="btnSmall" onClick={startOnboard}>
              {t("qqbot.qrRetry")}
            </button>
          </div>
        )}
      </div>
    </ModalOverlay>
  );
}
