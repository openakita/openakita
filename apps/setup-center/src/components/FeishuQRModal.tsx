import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { invoke, IS_TAURI } from "../platform";
import { safeFetch } from "../providers";
import { QRCodeSVG } from "qrcode.react";
import { ModalOverlay } from "./ModalOverlay";

type OnboardState = "idle" | "loading" | "scanning" | "polling" | "success" | "error";

interface FeishuQRModalProps {
  venvDir: string;
  apiBaseUrl?: string;
  domain?: string;
  onClose: () => void;
  onSuccess: (appId: string, appSecret: string) => void;
}

async function onboardStart(venvDir: string, domain: string, apiBaseUrl?: string): Promise<Record<string, any>> {
  if (apiBaseUrl) {
    const res = await safeFetch(`${apiBaseUrl}/api/feishu/onboard/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain }),
    });
    return res.json();
  }
  if (IS_TAURI) {
    const raw = await invoke<string>("openakita_feishu_onboard_start", { venvDir, domain });
    return JSON.parse(raw);
  }
  const res = await safeFetch(`/api/feishu/onboard/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ domain }),
  });
  return res.json();
}

async function onboardPoll(venvDir: string, domain: string, deviceCode: string, apiBaseUrl?: string): Promise<Record<string, any>> {
  if (apiBaseUrl) {
    const res = await safeFetch(`${apiBaseUrl}/api/feishu/onboard/poll`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, device_code: deviceCode }),
    });
    return res.json();
  }
  if (IS_TAURI) {
    const raw = await invoke<string>("openakita_feishu_onboard_poll", { venvDir, domain, deviceCode });
    return JSON.parse(raw);
  }
  const res = await safeFetch(`/api/feishu/onboard/poll`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ domain, device_code: deviceCode }),
  });
  return res.json();
}

export function FeishuQRModal({ venvDir, apiBaseUrl, domain = "feishu", onClose, onSuccess }: FeishuQRModalProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<OnboardState>("idle");
  const [verificationUri, setVerificationUri] = useState("");
  const [deviceCode, setDeviceCode] = useState("");
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
      const data = await onboardStart(venvDir, domain, apiBaseUrl);
      if (!mountedRef.current) return;
      if (data.device_code && data.verification_uri) {
        setDeviceCode(data.device_code);
        setVerificationUri(data.verification_uri);
        setState("scanning");
        startPolling(data.device_code);
      } else {
        setError(data.error || t("feishu.qrInitFailed"));
        setState("error");
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      setError(String(e));
      setState("error");
    }
  }, [venvDir, domain, apiBaseUrl, t]);

  const startPolling = useCallback((code: string) => {
    let attempts = 0;
    const maxAttempts = 60;

    pollRef.current = setInterval(async () => {
      attempts++;
      if (attempts > maxAttempts || !mountedRef.current) {
        if (pollRef.current) clearInterval(pollRef.current);
        if (mountedRef.current) {
          setError(t("feishu.qrTimeout"));
          setState("error");
        }
        return;
      }
      try {
        const data = await onboardPoll(venvDir, domain, code, apiBaseUrl);
        if (!mountedRef.current) return;

        if (data.app_id && data.app_secret) {
          if (pollRef.current) clearInterval(pollRef.current);
          setState("success");
          onSuccess(data.app_id, data.app_secret);
          return;
        }

        const status = data.status || data.error || "";
        if (status === "expired_token" || status === "access_denied") {
          if (pollRef.current) clearInterval(pollRef.current);
          setError(status === "access_denied" ? t("feishu.qrDenied") : t("feishu.qrExpired"));
          setState("error");
        }
      } catch {
        // polling error is non-fatal, keep trying
      }
    }, 3000);
  }, [venvDir, domain, apiBaseUrl, onSuccess, t]);

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
        >×</button>

        <div style={{ textAlign: "center", marginBottom: 16 }}>
          <div className="cardTitle" style={{ marginBottom: 4 }}>{t("feishu.qrTitle")}</div>
          <div style={{ fontSize: 12, color: "var(--text3)" }}>{t("feishu.qrSubtitle")}</div>
        </div>

        {state === "loading" && (
          <div style={{ textAlign: "center", padding: 40 }}>
            <div className="spinner" style={{ width: 32, height: 32, margin: "0 auto" }} />
            <div style={{ marginTop: 12, fontSize: 13, color: "var(--text3)" }}>{t("feishu.qrLoading")}</div>
          </div>
        )}

        {(state === "scanning" || state === "polling") && verificationUri && (
          <div style={{ textAlign: "center" }}>
            <div style={{
              background: "white", padding: 16, borderRadius: 8,
              display: "inline-block", marginBottom: 12,
            }}>
              <QRCodeSVG value={verificationUri} size={200} />
            </div>
            <div style={{ fontSize: 12, color: "var(--text3)", marginBottom: 8 }}>
              {t("feishu.qrScanHint")}
            </div>
            <div style={{
              fontSize: 11, color: "var(--muted)", wordBreak: "break-all",
              padding: "4px 8px", background: "var(--bg2)", borderRadius: 4,
            }}>
              {verificationUri}
            </div>
          </div>
        )}

        {state === "success" && (
          <div style={{ textAlign: "center", padding: 24, color: "var(--success)" }}>
            <div style={{ fontSize: 32, marginBottom: 8 }}>✓</div>
            <div style={{ fontSize: 14 }}>{t("feishu.qrSuccess")}</div>
          </div>
        )}

        {state === "error" && (
          <div style={{ textAlign: "center", padding: 16 }}>
            <div style={{ color: "var(--danger)", fontSize: 13, marginBottom: 12 }}>{error}</div>
            <button className="btnSmall" onClick={startOnboard}>{t("feishu.qrRetry")}</button>
          </div>
        )}
      </div>
    </ModalOverlay>
  );
}
