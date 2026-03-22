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
  | "scaned"
  | "success"
  | "expired"
  | "error";

interface WechatQRModalProps {
  venvDir?: string;
  apiBaseUrl?: string;
  onClose: () => void;
  onSuccess: (token: string) => void;
}

async function onboardStart(
  venvDir?: string,
  apiBaseUrl?: string,
): Promise<Record<string, any>> {
  if (apiBaseUrl) {
    const res = await safeFetch(`${apiBaseUrl}/api/wechat/onboard/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    return res.json();
  }
  if (IS_TAURI && venvDir) {
    const raw = await invoke<string>("openakita_wechat_onboard_start", {
      venvDir,
    });
    return JSON.parse(raw);
  }
  const res = await safeFetch(`/api/wechat/onboard/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  return res.json();
}

async function onboardPoll(
  qrcode: string,
  venvDir?: string,
  apiBaseUrl?: string,
): Promise<Record<string, any>> {
  if (apiBaseUrl) {
    const res = await safeFetch(`${apiBaseUrl}/api/wechat/onboard/poll`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ qrcode }),
    });
    return res.json();
  }
  if (IS_TAURI && venvDir) {
    const raw = await invoke<string>("openakita_wechat_onboard_poll", {
      venvDir,
      qrcode,
    });
    return JSON.parse(raw);
  }
  const res = await safeFetch(`/api/wechat/onboard/poll`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ qrcode }),
  });
  return res.json();
}

export function WechatQRModal({
  venvDir,
  apiBaseUrl,
  onClose,
  onSuccess,
}: WechatQRModalProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<OnboardState>("idle");
  const [qrUrl, setQrUrl] = useState("");
  const [qrCode, setQrCode] = useState("");
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
      if (data.qrcode && data.qrcode_url) {
        setQrCode(data.qrcode);
        setQrUrl(data.qrcode_url);
        setState("scanning");
        startPolling(data.qrcode);
      } else {
        setError(data.error || t("wechat.qrInitFailed"));
        setState("error");
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      setError(String(e));
      setState("error");
    }
  }, [venvDir, apiBaseUrl, t]);

  const startPolling = useCallback(
    (qrcode: string) => {
      let attempts = 0;
      const maxAttempts = 150;

      pollRef.current = setInterval(async () => {
        attempts++;
        if (attempts > maxAttempts || !mountedRef.current) {
          if (pollRef.current) clearInterval(pollRef.current);
          if (mountedRef.current) {
            setError(t("wechat.qrTimeout"));
            setState("error");
          }
          return;
        }
        try {
          const data = await onboardPoll(qrcode, venvDir, apiBaseUrl);
          if (!mountedRef.current) return;

          if (data.status === "confirmed" && data.token) {
            if (pollRef.current) clearInterval(pollRef.current);
            setState("success");
            onSuccess(data.token);
            return;
          }

          if (data.status === "scaned") {
            setState("scaned");
            return;
          }

          if (data.status === "expired") {
            if (pollRef.current) clearInterval(pollRef.current);
            setState("expired");
            return;
          }

          if (data.status === "error") {
            if (pollRef.current) clearInterval(pollRef.current);
            setError(data.message || t("wechat.qrInitFailed"));
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
            {t("wechat.qrTitle")}
          </div>
          <div style={{ fontSize: 12, color: "var(--text3)" }}>
            {t("wechat.qrSubtitle")}
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
              {t("wechat.qrLoading")}
            </div>
          </div>
        )}

        {(state === "scanning" || state === "scaned") && qrUrl && (
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
                color: state === "scaned" ? "var(--success, #16a34a)" : "var(--text3)",
                marginBottom: 6,
                fontWeight: state === "scaned" ? 500 : 400,
              }}
            >
              {state === "scaned" ? t("wechat.qrScaned") : t("wechat.qrScanHint")}
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
              {t("wechat.qrScanNote")}
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
            <div style={{ fontSize: 14 }}>{t("wechat.qrSuccess")}</div>
          </div>
        )}

        {state === "expired" && (
          <div style={{ textAlign: "center", padding: 20 }}>
            <div
              style={{
                fontSize: 13,
                color: "var(--text3)",
                marginBottom: 12,
              }}
            >
              {t("wechat.qrExpired")}
            </div>
            <button className="btnSmall" onClick={startOnboard}>
              {t("wechat.qrRefresh")}
            </button>
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
              {t("wechat.qrRetry")}
            </button>
          </div>
        )}
      </div>
    </ModalOverlay>
  );
}
