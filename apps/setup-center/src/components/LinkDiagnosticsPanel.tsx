import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link2, RefreshCw, Eraser } from "lucide-react";
import { Button } from "@/components/ui/button";
import { safeFetch } from "../providers";
import { notifyError, notifySuccess } from "../utils/notify";

export type LinkDiagnostic = {
  requested_url?: string;
  final_url?: string;
  redirect_chain?: string[];
  status_code?: number;
  content_type?: string;
  status?: "ok" | "error" | string;
  error_code?: string;
  hostname?: string;
};

type ClearResponse = { ok: boolean; cleared?: Record<string, boolean> };

export interface LinkDiagnosticsPanelProps {
  httpApiBase: () => string;
  initialDiagnostic?: LinkDiagnostic | null;
}

function shortUrl(url: string | undefined, max = 80): string {
  if (!url) return "";
  if (url.length <= max) return url;
  return url.slice(0, max - 1) + "…";
}

export function LinkDiagnosticsPanel({ httpApiBase, initialDiagnostic }: LinkDiagnosticsPanelProps) {
  const { t } = useTranslation();
  const [diag, setDiag] = useState<LinkDiagnostic | null>(initialDiagnostic ?? null);
  const [loading, setLoading] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [statusText, setStatusText] = useState("");

  const applyDiagnostic = useCallback((
    body: LinkDiagnostic | Record<string, never> | null | undefined,
    showResult: boolean,
  ) => {
    if (body && Object.keys(body).length > 0) {
      setDiag(body as LinkDiagnostic);
      if (showResult) {
        setStatusText(t("status.linkDiag.refreshed", { defaultValue: "已刷新，已读取到最近一次链接记录。" }));
      }
    } else {
      setDiag(null);
      if (showResult) {
        setStatusText(t("status.linkDiag.refreshedEmpty", { defaultValue: "已刷新，本次会话还没有链接读取记录。" }));
      }
    }
  }, [t]);

  const refresh = async (showResult = true) => {
    setLoading(true);
    try {
      const resp = await safeFetch(`${httpApiBase()}/api/health`, {
        signal: AbortSignal.timeout(5_000),
      });
      const body = await resp.json();
      applyDiagnostic(body?.last_link_diagnostic || null, showResult);
    } catch (e) {
      if (showResult) {
        setStatusText(t("status.linkDiag.refreshFailedDetail", {
          defaultValue: "刷新失败：{{error}}",
          error: String(e),
        }));
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    applyDiagnostic(initialDiagnostic || null, false);
  }, [applyDiagnostic, initialDiagnostic]);

  const onClear = async () => {
    setClearing(true);
    try {
      const resp = await safeFetch(
        `${httpApiBase()}/api/diagnostics/clear-session-caches`,
        { method: "POST" },
      );
      if (resp.ok) {
        const body = (await resp.json()) as ClearResponse;
        const items = Object.entries(body.cleared || {})
          .filter(([, v]) => v)
          .map(([k]) => k);
        notifySuccess(
          t("status.linkDiag.cleared", {
            defaultValue: "已清理：{{items}}",
            items: items.length > 0 ? items.join("、") : "—",
          }),
        );
        setDiag(null);
        setStatusText(t("status.linkDiag.clearedHint", {
          defaultValue: "已清理本会话缓存。不会删除聊天记录，只会让下次读取重新获取。",
        }));
      } else {
        notifyError(`HTTP ${resp.status}`);
        setStatusText(t("status.linkDiag.clearFailed", { defaultValue: "清理失败，请稍后再试。" }));
      }
    } catch (e) {
      notifyError(String(e));
      setStatusText(t("status.linkDiag.clearFailedDetail", {
        defaultValue: "清理失败：{{error}}",
        error: String(e),
      }));
    } finally {
      setClearing(false);
    }
  };

  const requested = diag?.requested_url || "";
  const finalUrl = diag?.final_url || requested;
  const redirected = !!(requested && finalUrl && requested !== finalUrl);
  const isError = (diag?.status || "").toLowerCase() === "error";

  const errorReason = (() => {
    const code = (diag?.error_code || "").toString();
    switch (code) {
      case "binary_content":
        return t("status.linkDiag.reason.binary", { defaultValue: "页面是文件不是网页" });
      case "domain_blocked":
        return t("status.linkDiag.reason.blocked", { defaultValue: "该域名被你屏蔽了" });
      case "too_many_redirects":
        return t("status.linkDiag.reason.tooManyRedirects", { defaultValue: "跳转次数太多" });
      case "network_error":
        return t("status.linkDiag.reason.network", { defaultValue: "网络问题" });
      case "empty_content":
        return t("status.linkDiag.reason.empty", { defaultValue: "页面没有可读正文" });
      case "redirect_missing_location":
        return t("status.linkDiag.reason.redirectInvalid", { defaultValue: "跳转响应不规范" });
      default:
        if (typeof diag?.status_code === "number" && diag.status_code >= 400) {
          return t("status.linkDiag.reason.httpError", {
            defaultValue: "服务器返回 {{code}}",
            code: diag.status_code,
          });
        }
        return code || "";
    }
  })();

  return (
    <div className="statusPanelRow">
      <div className="statusPanelIcon">
        <Link2 size={18} />
      </div>
      <div className="statusPanelInfo">
        <div className="statusPanelTitle">
          {t("status.linkDiag.title", { defaultValue: "链接读取诊断" })}
        </div>
        <div className="statusPanelDesc">
          {diag ? (
            isError ? (
              <span style={{ color: "var(--muted)" }}>
                {t("status.linkDiag.notRead", {
                  defaultValue: "上次链接没读取成功：{{url}}",
                  url: shortUrl(finalUrl || requested),
                })}
                {errorReason ? `（${errorReason}）` : ""}
              </span>
            ) : redirected ? (
              <span>
                {t("status.linkDiag.redirected", {
                  defaultValue: "上次读取了 {{final}}（由 {{requested}} 跳转）",
                  final: shortUrl(finalUrl),
                  requested: shortUrl(requested),
                })}
              </span>
            ) : (
              <span>
                {t("status.linkDiag.ok", {
                  defaultValue: "上次读取了 {{final}}",
                  final: shortUrl(finalUrl),
                })}
              </span>
            )
          ) : (
            <span style={{ opacity: 0.7 }}>
              {t("status.linkDiag.empty", {
                defaultValue: "本次会话还未读取过链接",
              })}
            </span>
          )}
          {statusText && (
            <div style={{ marginTop: 4, fontSize: 12, opacity: 0.75 }}>
              {statusText}
            </div>
          )}
        </div>
      </div>
      <div className="statusPanelActions" style={{ display: "flex", gap: 6 }}>
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs px-2.5"
          onClick={() => refresh(true)}
          disabled={loading || clearing}
          title={t("status.linkDiag.refresh", { defaultValue: "刷新" }) as string}
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          {loading ? t("status.checking", { defaultValue: "刷新中" }) : t("status.linkDiag.refresh", { defaultValue: "刷新" })}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs px-2.5"
          onClick={onClear}
          disabled={clearing || loading}
          title={
            t("status.linkDiag.clearHint", {
              defaultValue:
                "清理 WebFetch 缓存、工具结果缓存、浏览器导航记忆和上下文摘要（不会删除对话）",
            }) as string
          }
        >
          {clearing ? <RefreshCw size={12} className="animate-spin" /> : <Eraser size={12} />}
          {clearing ? t("status.linkDiag.clearing", { defaultValue: "清理中" }) : t("status.linkDiag.clear", { defaultValue: "清理本会话缓存" })}
        </Button>
      </div>
    </div>
  );
}
