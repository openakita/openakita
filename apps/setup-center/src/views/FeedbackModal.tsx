import React, { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { downloadFile, showInFolder } from "../platform";
import { IconX, IconInfo } from "../icons";
import { safeFetch } from "../providers";
import { ModalOverlay } from "../components/ModalOverlay";

type FeedbackMode = "bug" | "feature";

type SystemInfo = {
  os?: string;
  python?: string;
  openakita_version?: string;
  packages?: Record<string, string>;
  memory_total_gb?: number;
  disk_free_gb?: number;
  im_channels?: string[];
  [key: string]: unknown;
};

type FeedbackModalProps = {
  open: boolean;
  onClose: () => void;
  apiBase: string;
  initialMode?: FeedbackMode;
};

export function FeedbackModal({ open, onClose, apiBase, initialMode = "bug" }: FeedbackModalProps) {
  const { t } = useTranslation();

  const [mode, setMode] = useState<FeedbackMode>(initialMode);

  // Shared fields
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [imagePreviews, setImagePreviews] = useState<string[]>([]);

  // Bug report fields
  const [steps, setSteps] = useState("");
  const [uploadLogs, setUploadLogs] = useState(true);
  const [uploadDebug, setUploadDebug] = useState(true);
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [sysInfoExpanded, setSysInfoExpanded] = useState(false);

  // Feature request fields
  const [contactEmail, setContactEmail] = useState("");
  const [contactWechat, setContactWechat] = useState("");

  // CAPTCHA config fetched from backend (no hardcoded keys in source)
  const [captchaCfg, setCaptchaCfg] = useState<{ scene_id: string; prefix: string } | null>(null);

  // State
  const [submitting, setSubmitting] = useState(false);
  const [submitResult, setSubmitResult] = useState<{ ok: boolean; msg: string; downloadUrl?: string } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const captchaTokenRef = useRef("");
  const captchaContainerRef = useRef<HTMLDivElement>(null);
  const captchaInstanceRef = useRef<any>(null);
  const handleSubmitRef = useRef<() => void>(() => {});
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Reset mode when re-opened
  useEffect(() => {
    if (open) {
      setMode(initialMode);
      setSubmitResult(null);
      setDownloading(false);
    }
  }, [open, initialMode]);

  // Fetch system info + CAPTCHA config on open
  useEffect(() => {
    if (!open) return;
    safeFetch(`${apiBase}/api/system-info`, { signal: AbortSignal.timeout(5000) })
      .then((r) => r.json())
      .then(setSystemInfo)
      .catch(() => setSystemInfo(null));

    safeFetch(`${apiBase}/api/feedback-config`, { signal: AbortSignal.timeout(5000) })
      .then((r) => r.json())
      .then((cfg: any) => {
        if (cfg.captcha_scene_id && cfg.captcha_prefix) {
          setCaptchaCfg({ scene_id: cfg.captcha_scene_id, prefix: cfg.captcha_prefix });
        }
      })
      .catch(() => {});
  }, [open, apiBase]);

  // Load Alibaba Cloud CAPTCHA 2.0 once config is available
  useEffect(() => {
    if (!open || !captchaCfg) return;
    let destroyed = false;

    const initCaptcha = async () => {
      const initFn = (window as any).initAliyunCaptcha;
      if (typeof initFn === "function") {
        if (destroyed || !captchaContainerRef.current) return;
        try {
          captchaContainerRef.current.innerHTML = "";
          captchaInstanceRef.current = await initFn({
            SceneId: captchaCfg.scene_id,
            prefix: captchaCfg.prefix,
            mode: "popup",
            element: captchaContainerRef.current,
            button: "#feedback-submit-btn",
            captchaVerifyCallback: async (captchaVerifyParam: string) => {
              captchaTokenRef.current = captchaVerifyParam;
              return { captchaResult: true, bizResult: true };
            },
            onBizResultCallback: () => {
              handleSubmitRef.current();
            },
            getInstance: (inst: any) => { captchaInstanceRef.current = inst; },
            language: document.documentElement.lang?.startsWith("zh") ? "cn" : "en",
          });
        } catch { /* init failed, allow submission without captcha */ }
        return;
      }
      if (!document.querySelector('script[src*="AliyunCaptcha"]')) {
        const s = document.createElement("script");
        s.src = "https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js";
        s.async = true;
        s.onload = () => setTimeout(initCaptcha, 200);
        document.head.appendChild(s);
      } else {
        setTimeout(initCaptcha, 300);
      }
    };

    const timer = setTimeout(initCaptcha, 150);
    return () => {
      destroyed = true;
      clearTimeout(timer);
      if (captchaInstanceRef.current?.destroy) {
        try { captchaInstanceRef.current.destroy(); } catch {}
      }
      captchaInstanceRef.current = null;
      captchaTokenRef.current = "";
    };
  }, [open, captchaCfg]);

  // ─── Image handling ───
  const addImages = useCallback((files: FileList | File[]) => {
    const newFiles = Array.from(files).filter(
      (f) => f.type.startsWith("image/") && f.size < 10 * 1024 * 1024,
    );
    setImageFiles((prev) => {
      const combined = [...prev, ...newFiles].slice(0, 10);
      setImagePreviews((old) => { old.forEach(URL.revokeObjectURL); return combined.map((f) => URL.createObjectURL(f)); });
      return combined;
    });
  }, []);

  const removeImage = useCallback((idx: number) => {
    setImageFiles((prev) => {
      const next = prev.filter((_, i) => i !== idx);
      setImagePreviews((old) => { old.forEach(URL.revokeObjectURL); return next.map((f) => URL.createObjectURL(f)); });
      return next;
    });
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files.length) addImages(e.dataTransfer.files);
  }, [addImages]);

  // ─── Submit ───
  const handleSubmit = useCallback(async () => {
    if (!title.trim() || !description.trim()) return;

    // When CAPTCHA is configured, the first click is intercepted by the SDK.
    // handleSubmit is called again from onBizResultCallback after verification.
    if (captchaCfg && !captchaTokenRef.current) return;

    setSubmitting(true);
    setSubmitResult(null);

    try {
      const form = new FormData();
      form.append("title", title.trim());
      form.append("description", description.trim());
      form.append("captcha_verify_param", captchaTokenRef.current || "none");
      for (const img of imageFiles) {
        form.append("images", img);
      }

      let url: string;
      if (mode === "bug") {
        url = `${apiBase}/api/bug-report`;
        form.append("steps", steps.trim());
        form.append("upload_logs", String(uploadLogs));
        form.append("upload_debug", String(uploadDebug));
      } else {
        url = `${apiBase}/api/feature-request`;
        form.append("contact_email", contactEmail.trim());
        form.append("contact_wechat", contactWechat.trim());
      }

      const res = await safeFetch(url, {
        method: "POST",
        body: form,
        signal: AbortSignal.timeout(60_000),
      });

      const data = await res.json();

      if (data.status === "upload_failed") {
        const dlUrl = data.download_url ? `${apiBase}${data.download_url}` : undefined;
        setSubmitResult({
          ok: false,
          msg: t("feedback.uploadFailedSaved", { error: data.error || "unknown" }),
          downloadUrl: dlUrl,
        });
        return;
      }

      const successKey = mode === "bug" ? "bugReport.submitSuccess" : "featureRequest.submitSuccess";
      setSubmitResult({ ok: true, msg: t(successKey, { id: data.report_id }) });

      setTitle("");
      setDescription("");
      setSteps("");
      setContactEmail("");
      setContactWechat("");
      setImageFiles([]);
      setImagePreviews((old) => { old.forEach(URL.revokeObjectURL); return []; });
    } catch (err: any) {
      setSubmitResult({ ok: false, msg: err?.message || t("feedback.uploadFailedNetwork") });
    } finally {
      captchaTokenRef.current = "";
      setSubmitting(false);
    }
  }, [captchaCfg, mode, title, description, steps, uploadLogs, uploadDebug, contactEmail, contactWechat, imageFiles, apiBase, t]);

  // Keep ref in sync so the CAPTCHA callback always calls the latest version
  handleSubmitRef.current = handleSubmit;

  const handleClose = useCallback(() => { setSubmitResult(null); onClose(); }, [onClose]);

  if (!open) return null;

  const isBug = mode === "bug";

  return (
    <ModalOverlay onClose={handleClose}>
      <div
        className="modalContent"
        style={{ width: 560, maxHeight: "85vh" }}
      >
        {/* Header */}
        <div className="dialogHeader">
          <div className="cardTitle" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {isBug ? t("bugReport.title") : t("featureRequest.title")}
            <span style={{
              fontSize: 10, fontWeight: 700, letterSpacing: 0.5,
              padding: "1px 6px", borderRadius: 4,
              background: "linear-gradient(135deg, #f59e0b, #f97316)",
              color: "#fff", lineHeight: "16px", textTransform: "uppercase",
            }}>
              Beta
            </span>
          </div>
          <button className="dialogCloseBtn" onClick={handleClose}>
            <IconX size={14} />
          </button>
        </div>

        {/* Tab switcher */}
        <div style={{
          display: "flex",
          gap: 0,
          padding: "0 24px",
          borderBottom: "1px solid var(--line)",
          flexShrink: 0,
        }}>
          {(["bug", "feature"] as FeedbackMode[]).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setSubmitResult(null); }}
              style={{
                flex: 1,
                padding: "10px 0",
                fontSize: 13,
                fontWeight: mode === m ? 600 : 400,
                color: mode === m ? "var(--brand)" : "var(--text-dim)",
                background: "transparent",
                border: "none",
                borderBottom: mode === m ? "2px solid var(--brand)" : "2px solid transparent",
                cursor: "pointer",
                transition: "all 0.15s",
              }}
            >
              {m === "bug" ? t("bugReport.tabBug") : t("featureRequest.tabFeature")}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="dialogBody" style={{ padding: "16px 24px", overflowY: "auto", overflowX: "hidden" }}>
          {/* Title */}
          <label className="dialogLabel" style={{ display: "block", marginBottom: 12 }}>
            <span style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, display: "block" }}>
              {isBug ? t("bugReport.titleLabel") : t("featureRequest.nameLabel")} <span style={{ color: "#ef4444" }}>*</span>
            </span>
            <input
              className="dialogInput"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={isBug ? t("bugReport.titlePlaceholder") : t("featureRequest.namePlaceholder")}
              maxLength={200}
              style={{ width: "100%" }}
            />
          </label>

          {/* Description */}
          <label className="dialogLabel" style={{ display: "block", marginBottom: 12 }}>
            <span style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, display: "block" }}>
              {isBug ? t("bugReport.descLabel") : t("featureRequest.descLabel")} <span style={{ color: "#ef4444" }}>*</span>
            </span>
            <textarea
              className="dialogInput"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={isBug ? t("bugReport.descPlaceholder") : t("featureRequest.descPlaceholder")}
              rows={4}
              style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
            />
          </label>

          {/* Bug: Repro steps */}
          {isBug && (
            <label className="dialogLabel" style={{ display: "block", marginBottom: 12 }}>
              <span style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, display: "block" }}>
                {t("bugReport.stepsLabel")}
              </span>
              <textarea
                className="dialogInput"
                value={steps}
                onChange={(e) => setSteps(e.target.value)}
                placeholder={t("bugReport.stepsPlaceholder")}
                rows={3}
                style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
              />
            </label>
          )}

          {/* Feature: Contact info */}
          {!isBug && (
            <div style={{ marginBottom: 12 }}>
              <span style={{ fontSize: 13, fontWeight: 500, marginBottom: 6, display: "block" }}>
                {t("featureRequest.contactLabel")}
              </span>
              <div style={{ display: "flex", gap: 10 }}>
                <input
                  className="dialogInput"
                  value={contactEmail}
                  onChange={(e) => setContactEmail(e.target.value)}
                  placeholder={t("featureRequest.emailPlaceholder")}
                  type="email"
                  style={{ flex: 1 }}
                />
                <input
                  className="dialogInput"
                  value={contactWechat}
                  onChange={(e) => setContactWechat(e.target.value)}
                  placeholder={t("featureRequest.wechatPlaceholder")}
                  style={{ flex: 1 }}
                />
              </div>
            </div>
          )}

          {/* Image upload (shared) */}
          <div style={{ marginBottom: 12 }}>
            <span style={{ fontSize: 13, fontWeight: 500, marginBottom: 6, display: "block" }}>
              {isBug ? t("bugReport.images") : t("featureRequest.attachments")}
            </span>
            <div
              onDrop={handleDrop}
              onDragOver={(e) => e.preventDefault()}
              onClick={() => fileInputRef.current?.click()}
              style={{
                border: "2px dashed var(--line)",
                borderRadius: 10,
                padding: "14px 16px",
                textAlign: "center",
                cursor: "pointer",
                fontSize: 13,
                color: "var(--text-dim)",
                transition: "border-color 0.15s",
              }}
              onDragEnter={(e) => { (e.currentTarget as HTMLElement).style.borderColor = "var(--brand)"; }}
              onDragLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = "var(--line)"; }}
            >
              {t("bugReport.imageDropHint")}
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              style={{ display: "none" }}
              onChange={(e) => { if (e.target.files) addImages(e.target.files); e.target.value = ""; }}
            />
            {imagePreviews.length > 0 && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                {imagePreviews.map((src, i) => (
                  <div key={i} style={{ position: "relative", width: 64, height: 64, borderRadius: 8, overflow: "hidden", border: "1px solid var(--line)" }}>
                    <img src={src} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                    <button
                      onClick={(e) => { e.stopPropagation(); removeImage(i); }}
                      style={{
                        position: "absolute", top: 2, right: 2, width: 18, height: 18,
                        borderRadius: "50%", border: "none", background: "rgba(0,0,0,0.6)",
                        color: "#fff", fontSize: 10, display: "flex", alignItems: "center",
                        justifyContent: "center", cursor: "pointer", padding: 0,
                      }}
                    >
                      <IconX size={10} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Bug: Checkboxes */}
          {isBug && (
            <div className="feedbackOptions">
              <label className="feedbackOptionRow">
                <span className="feedbackOptionText">{t("bugReport.uploadLogs")}</span>
                <input
                  className="feedbackOptionCheckbox"
                  type="checkbox"
                  checked={uploadLogs}
                  onChange={(e) => setUploadLogs(e.target.checked)}
                  style={{ width: 16, height: 16 }}
                />
              </label>
              <label className="feedbackOptionRow">
                <span className="feedbackOptionText">{t("bugReport.uploadDebug")}</span>
                <input
                  className="feedbackOptionCheckbox"
                  type="checkbox"
                  checked={uploadDebug}
                  onChange={(e) => setUploadDebug(e.target.checked)}
                  style={{ width: 16, height: 16 }}
                />
              </label>
              <div className="feedbackOptionHint">
                <IconInfo size={11} style={{ verticalAlign: "-1px", marginRight: 3 }} />
                {t("bugReport.debugWarning")}
              </div>
            </div>
          )}

          {/* Bug: System info */}
          {isBug && systemInfo && (
            <div style={{ marginBottom: 8 }}>
              <div
                onClick={() => setSysInfoExpanded(!sysInfoExpanded)}
                style={{ fontSize: 13, fontWeight: 500, cursor: "pointer", color: "var(--text-dim)", userSelect: "none" }}
              >
                {sysInfoExpanded ? "▾" : "▸"} {t("bugReport.systemInfo")}
              </div>
              {sysInfoExpanded && (
                <pre style={{
                  fontSize: 11, background: "var(--bg1)", borderRadius: 8,
                  padding: "10px 12px", marginTop: 6, overflowX: "auto",
                  maxHeight: 160, whiteSpace: "pre-wrap", wordBreak: "break-all", lineHeight: 1.5,
                }}>
                  {JSON.stringify(systemInfo, null, 2)}
                </pre>
              )}
            </div>
          )}

          {/* Alibaba Cloud CAPTCHA 2.0 */}
          <div ref={captchaContainerRef} id="aliyun-captcha-element" style={{ marginBottom: 4 }} />

          {/* Result */}
          {submitResult && (
            <div style={{
              padding: "10px 14px", borderRadius: 8, fontSize: 13, marginTop: 4,
              background: submitResult.ok ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
              color: submitResult.ok ? "#16a34a" : "#dc2626", lineHeight: 1.5,
            }}>
              <div style={{ whiteSpace: "pre-wrap" }}>{submitResult.msg}</div>
              {submitResult.downloadUrl && (
                <button
                  type="button"
                  disabled={downloading}
                  onClick={async () => {
                    if (!submitResult.downloadUrl) return;
                    setDownloading(true);
                    const url = submitResult.downloadUrl;
                    const ts = Math.floor(Date.now() / 1000);
                    const filename = `openakita-feedback-${ts}.zip`;
                    try {
                      const dest = await downloadFile(url, filename);
                      await showInFolder(dest);
                    } catch (err: unknown) {
                      const msg = t("feedback.downloadFailed", {
                        error: err instanceof Error ? err.message : String(err),
                      });
                      setSubmitResult((prev) => (prev ? { ...prev, msg: prev.msg + "\n" + msg } : prev));
                    } finally {
                      setDownloading(false);
                    }
                  }}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 4,
                    marginTop: 8, padding: "6px 14px", borderRadius: 6,
                    background: downloading ? "var(--muted, #9ca3af)" : "var(--brand, #2563eb)",
                    color: "#fff", border: "none", cursor: downloading ? "wait" : "pointer",
                    fontSize: 13, fontWeight: 500,
                  }}
                >
                  {downloading ? t("feedback.downloading") : t("feedback.saveLocal")}
                </button>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="dialogFooter">
          <button className="btnSmall" onClick={handleClose}>
            {t("common.cancel")}
          </button>
          <button
            id="feedback-submit-btn"
            className="btnSmall"
            disabled={submitting || !title.trim() || !description.trim()}
            onClick={handleSubmit}
            style={{
              background: submitting ? undefined : "var(--brand)",
              color: submitting ? undefined : "#fff",
              opacity: submitting || !title.trim() || !description.trim() ? 0.5 : 1,
              minWidth: 120,
            }}
          >
            {submitting
              ? t("bugReport.submitting")
              : isBug ? t("bugReport.submit") : t("featureRequest.submit")}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
