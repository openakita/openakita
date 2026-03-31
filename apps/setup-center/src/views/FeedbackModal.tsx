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

export type FeedbackMode = "bug" | "feature";

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

export type FeedbackPrefill = {
  mode?: FeedbackMode;
  title?: string;
  description?: string;
};

type FeedbackModalProps = {
  open: boolean;
  onClose: () => void;
  apiBase: string;
  initialMode?: FeedbackMode;
  prefill?: FeedbackPrefill | null;
  onNavigateToMyFeedback?: () => void;
};

export function FeedbackModal({ open, onClose, apiBase, initialMode = "bug", prefill, onNavigateToMyFeedback }: FeedbackModalProps) {
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

  const [contactEmail, setContactEmail] = useState("");

  const [captchaCfg, setCaptchaCfg] = useState<{ scene_id: string; prefix: string } | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitResult, setSubmitResult] = useState<{ ok: boolean; msg: string; downloadUrl?: string } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{
    percent: number;
    phase: string;
    detail: string;
  } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const captchaTokenRef = useRef("");
  const captchaContainerRef = useRef<HTMLDivElement>(null);
  const captchaInstanceRef = useRef<any>(null);
  const handleSubmitRef = useRef<() => void>(() => {});
  const fileInputRef = useRef<HTMLInputElement>(null);

  type Phase = "form" | "uploading" | "success";
  const [phase, setPhase] = useState<Phase>("form");

  useEffect(() => {
    if (open) {
      setSubmitResult(null);
      setDownloading(false);
      setUploadProgress(null);
      setPhase("form");
      if (prefill) {
        setMode(prefill.mode ?? initialMode);
        setTitle(prefill.title ?? "");
        setDescription(prefill.description ?? "");
      }
    }
  }, [open, initialMode, prefill]);

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

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const imgs: File[] = [];
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        const file = item.getAsFile();
        if (file) imgs.push(file);
      }
    }
    if (imgs.length > 0) {
      addImages(imgs);
    }
  }, [addImages]);

  const resetForm = useCallback(() => {
    setMode(initialMode);
    setTitle("");
    setDescription("");
    setSteps("");
    setContactEmail("");
    setImageFiles([]);
    setImagePreviews((old) => { old.forEach(URL.revokeObjectURL); return []; });
    setUploadLogs(true);
    setUploadDebug(true);
    setSubmitResult(null);
  }, [initialMode]);

  const handleSubmit = useCallback(async () => {
    if (!title.trim() || !description.trim()) return;
    if (captchaCfg && !captchaTokenRef.current) return;

    setSubmitting(true);
    setSubmitResult(null);
    setPhase("uploading");
    setUploadProgress({ percent: 0, phase: "starting", detail: t("feedback.progressPacking") });

    const controller = new AbortController();
    abortRef.current = controller;

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
      form.append("contact_wechat", "");

      const res = await fetch(url, {
        method: "POST",
        body: form,
        signal: controller.signal,
      });

      const contentType = res.headers.get("content-type") || "";

      if (contentType.includes("text/event-stream") && res.body) {
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const blocks = buffer.split("\n\n");
          buffer = blocks.pop() || "";

          for (const block of blocks) {
            const eventMatch = block.match(/^event:\s*(.+)$/m);
            const dataMatch = block.match(/^data:\s*(.+)$/m);
            if (!eventMatch || !dataMatch) continue;
            const eventType = eventMatch[1].trim();
            let data: any;
            try { data = JSON.parse(dataMatch[1]); } catch { continue; }

            if (eventType === "progress") {
              setUploadProgress({
                percent: data.percent ?? 0,
                phase: data.phase ?? "",
                detail: data.detail ?? "",
              });
            } else if (eventType === "complete") {
              setUploadProgress(null);
              if (data.status === "upload_failed") {
                const dlUrl = data.download_url ? `${apiBase}${data.download_url}` : undefined;
                setSubmitResult({
                  ok: false,
                  msg: t("feedback.uploadFailedSaved", { error: data.error || "unknown" }),
                  downloadUrl: dlUrl,
                });
                setPhase("form");
              } else {
                resetForm();
                setPhase("success");
              }
            } else if (eventType === "error") {
              setUploadProgress(null);
              setSubmitResult({ ok: false, msg: data.detail || t("feedback.uploadFailedNetwork") });
              setPhase("form");
            }
          }
        }
      } else {
        setUploadProgress(null);
        const data = await res.json();
        if (data.status === "upload_failed") {
          const dlUrl = data.download_url ? `${apiBase}${data.download_url}` : undefined;
          setSubmitResult({
            ok: false,
            msg: t("feedback.uploadFailedSaved", { error: data.error || "unknown" }),
            downloadUrl: dlUrl,
          });
          setPhase("form");
        } else {
          resetForm();
          setPhase("success");
        }
      }
    } catch (err: any) {
      setUploadProgress(null);
      if (err?.name === "AbortError") {
        setSubmitResult({ ok: false, msg: t("feedback.uploadCancelled") });
      } else {
        setSubmitResult({ ok: false, msg: err?.message || t("feedback.uploadFailedNetwork") });
      }
      setPhase("form");
    } finally {
      captchaTokenRef.current = "";
      abortRef.current = null;
      setSubmitting(false);
    }
  }, [captchaCfg, mode, title, description, steps, uploadLogs, uploadDebug, contactEmail, imageFiles, apiBase, t, resetForm]);

  handleSubmitRef.current = handleSubmit;

  const handleClose = useCallback(() => {
    abortRef.current?.abort();
    setSubmitResult(null);
    setUploadProgress(null);
    onClose();
  }, [onClose]);

  const isBug = mode === "bug";

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) {
          if (phase === "uploading") return;
          if (phase === "success") { onNavigateToMyFeedback?.(); }
          handleClose();
        }
      }}
    >
      <DialogContent
        className={`p-0 gap-0 overflow-hidden ${phase === "form" ? "sm:max-w-[520px]" : "sm:max-w-[380px]"}`}
        showCloseButton={phase !== "uploading"}
        onPaste={phase === "form" ? handlePaste : undefined}
        onPointerDownOutside={(e) => {
          if (phase === "uploading") { e.preventDefault(); return; }
          const el = e.target as HTMLElement | null;
          if (el?.closest?.("[class*='aliyunCaptcha'], [id*='aliyunCaptcha'], [id*='aliyun-captcha']")) {
            e.preventDefault();
          }
        }}
        onInteractOutside={(e) => {
          if (phase === "uploading") { e.preventDefault(); return; }
          const el = e.target as HTMLElement | null;
          if (el?.closest?.("[class*='aliyunCaptcha'], [id*='aliyunCaptcha'], [id*='aliyun-captcha']")) {
            e.preventDefault();
          }
        }}
        onEscapeKeyDown={(e) => { if (phase === "uploading") e.preventDefault(); }}
      >
        {phase === "form" && (
          <>
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
            <fieldset disabled={submitting} className="contents">
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
                  {t("bugReport.contactEmailLabel")}
                </Label>
                <Input
                  value={contactEmail}
                  onChange={(e) => setContactEmail(e.target.value)}
                  placeholder={t("featureRequest.emailPlaceholder")}
                  type="email"
                />
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
                      <div key={i} className="relative w-14 h-14 rounded-md overflow-hidden border border-border group">
                        <img src={src} alt="" className="w-full h-full object-cover" />
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); removeImage(i); }}
                          className="absolute top-0 right-0 w-5 h-5 rounded-bl-md bg-black/60 hover:bg-black/80 text-white flex items-center justify-center cursor-pointer transition-colors"
                          style={{ border: "none", padding: 0, lineHeight: 1 }}
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

              <div ref={captchaContainerRef} id="aliyun-captcha-element" />

              {/* Error result */}
              {submitResult && !submitResult.ok && (
                <div className="rounded-md p-2.5 text-[13px] leading-relaxed bg-destructive/10 text-destructive">
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
            </fieldset>

            {/* Footer */}
            <div className="flex items-center px-5 py-3 border-t border-border shrink-0">
              <Button variant="ghost" size="sm" onClick={resetForm} className="text-muted-foreground hover:text-foreground">
                {t("feedback.clearForm")}
              </Button>
              <div className="flex items-center gap-2 ml-auto">
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
            </div>
          </>
        )}

        {phase === "uploading" && (
          <>
            <DialogHeader className="px-5 pt-5 pb-0">
              <DialogTitle className="text-[15px]">{t("feedback.uploadingTitle")}</DialogTitle>
              <DialogDescription className="sr-only">{t("feedback.uploadingTitle")}</DialogDescription>
            </DialogHeader>
            <div className="px-5 py-6 space-y-4">
              {uploadProgress && (
                <>
                  <div className="flex items-center justify-between text-[13px] text-muted-foreground">
                    <span>{uploadProgress.detail}</span>
                    <span>{uploadProgress.percent}%</span>
                  </div>
                  <div className="h-2 bg-muted rounded-full overflow-hidden">
                    <div
                      className="h-full bg-primary rounded-full transition-all duration-300 ease-out"
                      style={{ width: `${uploadProgress.percent}%` }}
                    />
                  </div>
                </>
              )}
            </div>
            <div className="flex justify-end px-5 py-3 border-t border-border">
              <Button
                variant="destructive"
                size="sm"
                onClick={() => { abortRef.current?.abort(); }}
              >
                {t("feedback.cancelUpload")}
              </Button>
            </div>
          </>
        )}

        {phase === "success" && (
          <>
            <DialogHeader className="px-5 pt-5 pb-0">
              <DialogTitle className="text-[15px]">{t("feedback.submitSuccessTitle")}</DialogTitle>
              <DialogDescription className="sr-only">{t("feedback.submitSuccessTitle")}</DialogDescription>
            </DialogHeader>
            <div className="px-5 py-4">
              <p className="text-[14px] text-muted-foreground leading-relaxed">
                {t("feedback.submitSuccessMessage")}
              </p>
            </div>
            <div className="flex justify-end gap-2 px-5 py-3 border-t border-border">
              <Button variant="outline" size="sm" onClick={() => { onNavigateToMyFeedback?.(); handleClose(); }}>
                {t("feedback.close")}
              </Button>
              {onNavigateToMyFeedback && (
                <Button size="sm" onClick={() => { onNavigateToMyFeedback(); handleClose(); }}>
                  {t("myFeedback.viewFeedback")}
                </Button>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
