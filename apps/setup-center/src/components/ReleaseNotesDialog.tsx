import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { proxyFetch } from "../platform";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "./ui/dialog";
import { useMdModules } from "../views/chat/hooks/useMdModules";

const RELEASE_NOTES_BASE_URL = "https://dl-openakita.fzstack.com/api/releases";

type ReleaseNotesJson = {
  version: string;
  pub_date: string;
  notes: string;
  notes_zh?: string;
  notes_en?: string;
  channel?: string;
};

export function normalizeReleaseVersion(version: string): string {
  return String(version || "").trim().replace(/^v/i, "");
}

function formatReleaseTime(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
  ].join("-") + ` ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function pickReleaseNotes(data: ReleaseNotesJson, lang: string): string {
  const base = lang.split("-")[0];
  if (base === "zh" && data.notes_zh?.trim()) return data.notes_zh;
  if (base === "en" && data.notes_en?.trim()) return data.notes_en;
  return data.notes || data.notes_zh || data.notes_en || "";
}

function coerceReleaseNotesJson(data: unknown): ReleaseNotesJson {
  const obj = data && typeof data === "object" ? data as Record<string, unknown> : {};
  return {
    version: typeof obj.version === "string" ? obj.version : "",
    pub_date: typeof obj.pub_date === "string" ? obj.pub_date : "",
    notes: typeof obj.notes === "string" ? obj.notes : "",
    notes_zh: typeof obj.notes_zh === "string" ? obj.notes_zh : undefined,
    notes_en: typeof obj.notes_en === "string" ? obj.notes_en : undefined,
    channel: typeof obj.channel === "string" ? obj.channel : undefined,
  };
}

async function fetchReleaseNotesJson(url: string, defaultVersion = ""): Promise<ReleaseNotesJson> {
  const response = await proxyFetch(url, {
    timeoutSecs: 12,
    headers: { Accept: "application/json" },
  });
  if (response.status < 200 || response.status >= 300) {
    throw new Error(`HTTP ${response.status}`);
  }
  const data = coerceReleaseNotesJson(JSON.parse(response.body));
  if (!data.version && defaultVersion) data.version = defaultVersion;
  return data;
}

export function ReleaseNotesDialog({
  version,
  onClose,
  title,
  description,
  footer,
}: {
  version: string;
  onClose: () => void;
  title?: string;
  description?: string;
  footer?: ReactNode;
}) {
  const { t, i18n } = useTranslation();
  const mdModules = useMdModules();
  const [release, setRelease] = useState<ReleaseNotesJson | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const normalizedVersion = normalizeReleaseVersion(version);

  const loadReleaseNotes = useCallback(async () => {
    if (!normalizedVersion) {
      setRelease(null);
      setError("Missing version");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const url = `${RELEASE_NOTES_BASE_URL}/v${encodeURIComponent(normalizedVersion)}.json`;
      const data = await fetchReleaseNotesJson(url, normalizedVersion);
      setRelease(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRelease(null);
    } finally {
      setLoading(false);
    }
  }, [normalizedVersion]);

  useEffect(() => {
    void loadReleaseNotes();
  }, [loadReleaseNotes]);

  const notes = release ? pickReleaseNotes(release, i18n.language) : "";
  const releaseVersion = release?.version || normalizedVersion;
  const releaseTime = formatReleaseTime(release?.pub_date);
  const versionLabel = `v${normalizeReleaseVersion(releaseVersion)}`;
  const releaseSubtitle = description || (releaseTime
    ? `${versionLabel} · ${t("version.releasePubDate", { date: releaseTime })}`
    : versionLabel);
  const dialogTitle = title || t("version.releaseNotesTitle");

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="releaseNotesDialogContent" onOpenAutoFocus={(e) => e.preventDefault()}>
        <DialogHeader className="sr-only">
          <DialogTitle>{dialogTitle}</DialogTitle>
          <DialogDescription>{releaseSubtitle}</DialogDescription>
        </DialogHeader>
        <div className="releaseNotesView">
          <div className="inboxHeader releaseNotesHeader">
            <div className="min-w-0">
              <h1 className="inboxTitle">{dialogTitle}</h1>
              <p className="inboxSubtitle">{releaseSubtitle}</p>
            </div>
          </div>
          <div className="releaseNotesBody">
            {loading && (
              <div className="releaseNotesState">
                <div className="spinner" style={{ width: 18, height: 18 }} />
                <span>{t("common.loading")}</span>
              </div>
            )}
            {!loading && error && (
              <div className="releaseNotesState releaseNotesError">
                <span>{t("version.releaseNotesLoadFailed", { error })}</span>
                <button type="button" className="btnSmall" onClick={loadReleaseNotes}>{t("common.retry")}</button>
              </div>
            )}
            {!loading && !error && release && (
              mdModules ? (
                <div className="feedbackMdContent inboxMarkdown releaseNotesMarkdown">
                  <mdModules.ReactMarkdown
                    remarkPlugins={mdModules.remarkPlugins}
                    rehypePlugins={mdModules.rehypePlugins}
                  >
                    {notes || t("version.releaseNotesEmpty")}
                  </mdModules.ReactMarkdown>
                </div>
              ) : (
                <div className="releaseNotesPlain">{notes || t("version.releaseNotesEmpty")}</div>
              )
            )}
          </div>
          {footer && <div className="releaseNotesFooter">{footer}</div>}
        </div>
      </DialogContent>
    </Dialog>
  );
}
