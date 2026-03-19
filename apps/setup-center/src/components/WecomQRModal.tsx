import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { invoke, IS_TAURI } from "../platform";
import { safeFetch } from "../providers";
import { QRCodeSVG } from "qrcode.react";
import { ModalOverlay } from "./ModalOverlay";

type OnboardState = "idle" | "loading" | "scanning" | "polling" | "success" | "error";

interface WecomQRModalProps {
  venvDir: string;
  apiBaseUrl?: string;
  onClose: () => void;
  onSuccess: (botId: string, secret: string) => void;
}

async function onboardStart(venvDir: string, apiBaseUrl?: string): Promise<Record<string, any>> {
  if (IS_TAURI) {
    const raw = await invoke<string>("openakita_wecom_onboard_start", { venvDir });
    return JSON.parse(raw);
  }
  const base = apiBaseUrl || "";
  const res = await safeFetch(`${base}/api/wecom/onboard/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  return res.json();
}

async function onboardPoll(venvDir: string, scode: string, apiBaseUrl?: string): Promise<Record<string, any>> {
  if (IS_TAURI) {
    const raw = await invoke<string>("openakita_wecom_onboard_poll", { venvDir, scode });
    return JSON.parse(raw);
  }
  const base = apiBaseUrl || "";
  const res = await safeFetch(`${base}/api/wecom/onboard/poll`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scode }),
  });
  return res.json();
}

export function WecomQRModal({ venvDir, apiBaseUrl, onClose, onSuccess }: WecomQRModalProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<OnboardState>("idle");
  const [qrUrl, setQrUrl] = useState("");
  const [scode, setScode] = useState("");
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
      if (data.auth_url && data.scode) {
        setScode(data.scode);
        setQrUrl(data.auth_url);
        setState("scanning");
        startPolling(data.scode);
      } else {
        setError(data.error || t("wecom.qrInitFailed"));
        setState("error");
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      setError(String(e));
      setState("error");
    }
  }, [venvDir, apiBaseUrl, t]);

  const startPolling = useCallback((sc: string) => {
    let attempts = 0;
    const maxAttempts = 100;

    pollRef.current = setInterval(async () => {
      attempts++;
      if (attempts > maxAttempts || !mountedRef.current) {
        if (pollRef.current) clearInterval(pollRef.current);
        if (mountedRef.current) {
          setError(t("wecom.qrTimeout"));
          setState("error");
        }
        return;
      }
      try {
        const data = await onboardPoll(venvDir, sc, apiBaseUrl);
        if (!mountedRef.current) return;

        if (data.bot_id && data.secret) {
          if (pollRef.current) clearInterval(pollRef.current);
          setState("success");
          onSuccess(data.bot_id, data.secret);
          return;
        }

        const status = data.status || "";
        if (status === "expired" || status === "error") {
          if (pollRef.current) clearInterval(pollRef.current);
          setError(status === "expired" ? t("wecom.qrExpired") : (data.error || t("wecom.qrFailed")));
          setState("error");
        }
      } catch {
        // polling error is non-fatal, keep trying
      }
    }, 3000);
  }, [venvDir, apiBaseUrl, onSuccess, t]);

  useEffect(() => {
    startOnboard();
  }, [startOnboard]);

  return (
    <ModalOverlay onClose={onClose}>
      <div className="card" style={{ width: 380, maxWidth: "90vw", padding: 24, position: "relative" }}>
        <button
          onClick={onClose}
          style={{
            position: "absolute", top: 8, right: 12,
            background: "none", border: "none", fontSize: 18,
            cursor: "pointer", color: "var(--text3)",
          }}
        >&times;</button>

        <div style={{ textAlign: "center", marginBottom: 16 }}>
          <div className="cardTitle" style={{ marginBottom: 4 }}>{t("wecom.qrTitle")}</div>
          <div style={{ fontSize: 12, color: "var(--text3)" }}>{t("wecom.qrSubtitle")}</div>
        </div>

        {state === "loading" && (
          <div style={{ textAlign: "center", padding: 40 }}>
            <div className="spinner" style={{ width: 32, height: 32, margin: "0 auto" }} />
            <div style={{ marginTop: 12, fontSize: 13, color: "var(--text3)" }}>{t("wecom.qrLoading")}</div>
          </div>
        )}

        {(state === "scanning" || state === "polling") && qrUrl && (
          <div style={{ textAlign: "center" }}>
            <div style={{
              background: "white", padding: 16, borderRadius: 8,
              display: "inline-block", marginBottom: 12,
            }}>
              <QRCodeSVG value={qrUrl} size={200} />
            </div>
            <div style={{ fontSize: 12, color: "var(--text3)", marginBottom: 8 }}>
              {t("wecom.qrScanHint")}
            </div>
            <div style={{
              fontSize: 11, color: "var(--muted)", wordBreak: "break-all",
              padding: "4px 8px", background: "var(--bg2)", borderRadius: 4,
            }}>
              {qrUrl}
            </div>
          </div>
        )}

        {state === "success" && (
          <div style={{ textAlign: "center", padding: 24, color: "var(--success)" }}>
            <div style={{ fontSize: 32, marginBottom: 8 }}>&#10003;</div>
            <div style={{ fontSize: 14 }}>{t("wecom.qrSuccess")}</div>
          </div>
        )}

        {state === "error" && (
          <div style={{ textAlign: "center", padding: 16 }}>
            <div style={{ color: "var(--danger)", fontSize: 13, marginBottom: 12 }}>{error}</div>
            <button className="btnSmall" onClick={startOnboard}>{t("wecom.qrRetry")}</button>
          </div>
        )}
      </div>
    </ModalOverlay>
  );
}
