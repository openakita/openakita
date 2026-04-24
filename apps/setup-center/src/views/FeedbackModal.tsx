import React, { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { downloadFile, showInFolder } from "../platform";
import { IconX, IconInfo } from "../icons";
import { safeFetch } from "../providers";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "../components/ui/dialog";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import { Label } from "../components/ui/label";
import { Checkbox } from "../components/ui/checkbox";

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

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [imagePreviews, setImagePreviews] = useState<string[]>([]);

  const [steps, setSteps] = useState("");
  const [uploadLogs, setUploadLogs] = useState(true);
  const [uploadDebug, setUploadDebug] = useState(true);
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [sysInfoExpanded, setSysInfoExpanded] = useState(false);

  const [contactEmail, setContactEmail] = useState("");
  const [contactWechat, setContactWechat] = useState("");

  const [captchaCfg, setCaptchaCfg] = useState<{ scene_id: string; prefix: string } | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitResult, setSubmitResult] = useState<{ ok: boolean; msg: string; downloadUrl?: string } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const captchaTokenRef = useRef("");
  const captchaContainerRef = useRef<HTMLDivElement>(null);
  const captchaInstanceRef = useRef<any>(null);
  const handleSubmitRef = useRef<() => void>(() => {});
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setMode(initialMode);
      setSubmitResult(null);
      setDownloading(false);
    }
  }, [open, initialMode]);

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

  useEffect(() => {
    if (!open || !captchaCfg) return;
    let destroyed = false;

    // Radix Dialog (modal) sets `pointer-events: none` on <body>, which kills
    // all interaction on elements outside the dialog portal — including the
    // Aliyun CAPTCHA popup that renders as a direct child of <body>.
    // Use a MutationObserver to detect captcha-related elements added to <body>
    // and force pointer-events / z-index so the slider is draggable.
    const liftCaptchaNode = (el: HTMLElement) => {
      el.style.setProperty("z-index", "2147483647", "important");
      el.style.setProperty("pointer-events", "auto", "important");
    };

    const isCaptchaNode = (el: HTMLElement): boolean => {
      const hay = `${el.id} ${el.className}`;
      return /captcha|slidetounlock|nc[-_]|sm[-_]/i.test(hay);
    };

    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        for (const node of m.addedNodes) {
          if (node instanceof HTMLElement && node.parentElement === document.body && isCaptchaNode(node)) {
            liftCaptchaNode(node);
          }
        }
      }
    });
    observer.observe(document.body, { childList: true });

    // Also fix any captcha elements already in the DOM
    document.querySelectorAll<HTMLElement>(
      "body > div[id*='captcha' i], body > div[class*='captcha' i]",
    ).forEach(liftCaptchaNode);

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
      observer.disconnect();
      if (captchaInstanceRef.current?.destroy) {
        try { captchaInstanceRef.current.destroy(); } catch {}
      }
      captchaInstanceRef.current = null;
      captchaTokenRef.current = "";
    };
  }, [open, captchaCfg]);

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

  const handleSubmit = useCallback(async () => {
    if (!title.trim() || !description.trim()) return;
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
      }
      form.append("contact_email", contactEmail.trim());
      form.append("contact_wechat", contactWechat.trim());

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

  handleSubmitRef.current = handleSubmit;

  const handleClose = useCallback(() => { setSubmitResult(null); onClose(); }, [onClose]);

  const isBug = mode === "bug";

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) handleClose(); }}>
      <DialogContent
        className="sm:max-w-[520px] p-0 gap-0 overflow-hidden"
        showCloseButton={true}
        onPointerDownOutside={(e) => {
          const t = e.target as HTMLElement | null;
          if (t?.closest?.("[class*='aliyunCaptcha'], [id*='aliyunCaptcha'], [id*='aliyun-captcha']")) {
            e.preventDefault();
          }
        }}
        onInteractOutside={(e) => {
          const t = e.target as HTMLElement | null;
          if (t?.closest?.("[class*='aliyunCaptcha'], [id*='aliyunCaptcha'], [id*='aliyun-captcha']")) {
            e.preventDefault();
          }
        }}
      >
        <DialogHeader className="sr-only">
          <DialogTitle>{isBug ? t("bugReport.title") : t("featureRequest.title")}</DialogTitle>
          <DialogDescription>{isBug ? t("bugReport.title") : t("featureRequest.title")}</DialogDescription>
        </DialogHeader>

        {/* Tab navigation as header */}
        <div className="flex items-end gap-0 border-b border-border px-5 pt-4 shrink-0">
          {(["bug", "feature"] as FeedbackMode[]).map((m) => (
            <span
              key={m}
              onClick={() => { setMode(m); setSubmitResult(null); }}
              className={`relative mr-6 pb-2.5 text-[15px] cursor-pointer transition-colors select-none ${
                mode === m
                  ? "font-semibold text-primary"
                  : "font-normal text-muted-foreground hover:text-foreground"
              }`}
            >
              {m === "bug" ? t("bugReport.tabBug") : t("featureRequest.tabFeature")}
              {mode === m && (
                <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-primary rounded-full" />
              )}
            </span>
          ))}
        </div>

        {/* Scrollable body */}
        <div className="overflow-y-auto overflow-x-hidden px-5 py-4 space-y-3.5" style={{ maxHeight: "calc(85vh - 180px)" }}>
          {/* Title */}
          <div className="space-y-1">
            <Label className="text-[13px]">
              {isBug ? t("bugReport.titleLabel") : t("featureRequest.nameLabel")} <span className="text-destructive">*</span>
            </Label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={isBug ? t("bugReport.titlePlaceholder") : t("featureRequest.namePlaceholder")}
              maxLength={200}
            />
          </div>

          {/* Description */}
          <div className="space-y-1">
            <Label className="text-[13px]">
              {isBug ? t("bugReport.descLabel") : t("featureRequest.descLabel")} <span className="text-destructive">*</span>
            </Label>
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={isBug ? t("bugReport.descPlaceholder") : t("featureRequest.descPlaceholder")}
              rows={3}
              className="resize-y"
            />
          </div>

          {/* Bug: Repro steps */}
          {isBug && (
            <div className="space-y-1">
              <Label className="text-[13px]">{t("bugReport.stepsLabel")}</Label>
              <Textarea
                value={steps}
                onChange={(e) => setSteps(e.target.value)}
                placeholder={t("bugReport.stepsPlaceholder")}
                rows={2}
                className="resize-y"
              />
            </div>
          )}

          {/* Contact info */}
          <div className="space-y-1">
            <Label className="text-[13px]">
              {isBug ? t("featureRequest.contactLabel") : t("featureRequest.contactLabel")}
            </Label>
            {isBug && (
              <p className="text-[11px] text-muted-foreground/70">{t("bugReport.contactHint")}</p>
            )}
            <div className="flex gap-2">
              <Input
                value={contactEmail}
                onChange={(e) => setContactEmail(e.target.value)}
                placeholder={t("featureRequest.emailPlaceholder")}
                type="email"
                className="flex-1"
              />
              <Input
                value={contactWechat}
                onChange={(e) => setContactWechat(e.target.value)}
                placeholder={t("featureRequest.wechatPlaceholder")}
                className="flex-1"
              />
            </div>
          </div>

          {/* Image upload */}
          <div className="space-y-1">
            <Label className="text-[13px]">{isBug ? t("bugReport.images") : t("featureRequest.attachments")}</Label>
            <div
              onDrop={handleDrop}
              onDragOver={(e) => e.preventDefault()}
              onClick={() => fileInputRef.current?.click()}
              className="border-2 border-dashed border-border rounded-md py-3 text-center cursor-pointer text-[13px] text-muted-foreground transition-colors hover:border-primary/40"
              onDragEnter={(e) => { e.currentTarget.classList.add("border-primary"); }}
              onDragLeave={(e) => { e.currentTarget.classList.remove("border-primary"); }}
            >
              {t("bugReport.imageDropHint")}
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={(e) => { if (e.target.files) addImages(e.target.files); e.target.value = ""; }}
            />
            {imagePreviews.length > 0 && (
              <div className="flex gap-1.5 flex-wrap mt-1.5">
                {imagePreviews.map((src, i) => (
                  <div key={i} className="relative w-14 h-14 rounded-md overflow-hidden border border-border">
                    <img src={src} alt="" className="w-full h-full object-cover" />
                    <button
                      onClick={(e) => { e.stopPropagation(); removeImage(i); }}
                      className="absolute top-0.5 right-0.5 w-4 h-4 rounded-full border-0 bg-black/60 text-white text-[9px] flex items-center justify-center cursor-pointer p-0"
                    >
                      <IconX size={8} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Bug: Checkboxes */}
          {isBug && (
            <div className="space-y-2 pt-0.5">
              <label className="flex items-center gap-2 cursor-pointer" htmlFor="upload-logs">
                <Checkbox
                  id="upload-logs"
                  checked={uploadLogs}
                  onCheckedChange={(v) => setUploadLogs(v === true)}
                />
                <span className="text-[13px]">{t("bugReport.uploadLogs")}</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer" htmlFor="upload-debug">
                <Checkbox
                  id="upload-debug"
                  checked={uploadDebug}
                  onCheckedChange={(v) => setUploadDebug(v === true)}
                />
                <span className="text-[13px]">{t("bugReport.uploadDebug")}</span>
              </label>
              <p className="text-[11px] text-muted-foreground/70 leading-relaxed pl-6">
                <IconInfo size={10} className="inline align-[-1px] mr-0.5" />
                {t("bugReport.debugWarning")}
              </p>
            </div>
          )}

          {/* Bug: System info */}
          {isBug && systemInfo && (
            <div>
              <button
                type="button"
                onClick={() => setSysInfoExpanded(!sysInfoExpanded)}
                className="text-[12px] cursor-pointer text-muted-foreground bg-transparent border-0 p-0 select-none hover:text-foreground transition-colors"
              >
                {sysInfoExpanded ? "▾" : "▸"} {t("bugReport.systemInfo")}
              </button>
              {sysInfoExpanded && (
                <pre className="text-[11px] bg-muted rounded-md p-2 mt-1 overflow-x-auto max-h-32 whitespace-pre-wrap break-all leading-relaxed">
                  {JSON.stringify(systemInfo, null, 2)}
                </pre>
              )}
            </div>
          )}

          <div ref={captchaContainerRef} id="aliyun-captcha-element" />

          {/* Result */}
          {submitResult && (
            <div className={`rounded-md p-2.5 text-[13px] leading-relaxed ${
              submitResult.ok
                ? "bg-green-500/10 text-green-600 dark:text-green-400"
                : "bg-destructive/10 text-destructive"
            }`}>
              <div className="whitespace-pre-wrap">{submitResult.msg}</div>
              {submitResult.downloadUrl && (
                <Button
                  size="sm"
                  disabled={downloading}
                  className="mt-1.5 h-7 text-xs"
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
                >
                  {downloading ? t("feedback.downloading") : t("feedback.saveLocal")}
                </Button>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-border shrink-0">
          <Button variant="outline" size="sm" onClick={handleClose}>
            {t("common.cancel")}
          </Button>
          <Button
            id="feedback-submit-btn"
            size="sm"
            disabled={submitting || !title.trim() || !description.trim()}
            onClick={handleSubmit}
            className="min-w-[100px]"
          >
            {submitting
              ? t("bugReport.submitting")
              : isBug ? t("bugReport.submit") : t("featureRequest.submit")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

