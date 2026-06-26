import { useCallback, useEffect, useState } from "react";
import { getAppVersion, checkForUpdate, relaunchApp, type UpdateInfo } from "../platform";

const GITHUB_REPO = "openakita/openakita";

export function compareSemver(a: string, b: string): number {
  const parse = (v: string) => v.replace(/^v/, "").split(".").map((s) => parseInt(s, 10) || 0);
  const pa = parse(a);
  const pb = parse(b);
  for (let i = 0; i < 3; i++) {
    if ((pa[i] ?? 0) > (pb[i] ?? 0)) return 1;
    if ((pa[i] ?? 0) < (pb[i] ?? 0)) return -1;
  }
  return 0;
}

export function useVersionCheck() {
  const [desktopVersion, setDesktopVersion] = useState("0.0.0");
  const [backendVersion, setBackendVersion] = useState<string | null>(null);
  const [versionMismatch, setVersionMismatch] = useState<{ backend: string; desktop: string } | null>(null);
  const [newRelease, setNewRelease] = useState<{ latest: string; current: string; url: string } | null>(null);
  const [updateAvailable, setUpdateAvailable] = useState<UpdateInfo | null>(null);
  const [updateProgress, setUpdateProgress] = useState<{
    status: "idle" | "downloading" | "installing" | "done" | "error";
    percent?: number;
    error?: string;
  }>({ status: "idle" });

  useEffect(() => {
    getAppVersion().then((v) => setDesktopVersion(v)).catch(() => setDesktopVersion("1.10.5"));
  }, []);

  const checkVersionMismatch = useCallback((bv: string) => {
    if (!bv || bv === "0.0.0-dev") return;
    if (!desktopVersion || desktopVersion === "0.0.0") return;
    const normB = bv.replace(/^v/, "");
    const normD = desktopVersion.replace(/^v/, "");
    setVersionMismatch(normB !== normD ? { backend: normB, desktop: normD } : null);
  }, [desktopVersion]);

  const checkForAppUpdate = useCallback(async () => {
    const dismissKey = "openakita_release_dismissed";
    try {
      const update = await checkForUpdate({ apiBaseUrl: "http://127.0.0.1:18900" });
      if (update) {
        const dismissed = localStorage.getItem(dismissKey);
        if (dismissed !== update.version) {
          setUpdateAvailable(update);
          setNewRelease({
            latest: update.version,
            current: desktopVersion,
            url: `https://github.com/${GITHUB_REPO}/releases/tag/v${update.version}`,
          });
        }
      }
    } catch {
      try {
        const res = await fetch(`https://api.github.com/repos/${GITHUB_REPO}/releases/latest`, {
          signal: AbortSignal.timeout(4000),
          headers: { Accept: "application/vnd.github.v3+json" },
        });
        if (!res.ok) return;
        const data = await res.json();
        const tagName = (data.tag_name || "").replace(/^v/, "");
        if (tagName && compareSemver(tagName, desktopVersion) > 0) {
          const dismissed = localStorage.getItem(dismissKey);
          if (dismissed !== tagName) {
            setNewRelease({
              latest: tagName,
              current: desktopVersion,
              url: data.html_url || `https://github.com/${GITHUB_REPO}/releases`,
            });
          }
        }
      } catch { /* both methods failed */ }
    }
  }, [desktopVersion]);

  const doDownloadAndInstall = useCallback(async () => {
    if (!updateAvailable) return;
    setUpdateProgress({ status: "downloading", percent: 0 });
    try {
      let totalBytes = 0;
      let downloadedBytes = 0;
      await updateAvailable.downloadAndInstall((event) => {
        if (event.event === "Started" && event.data.contentLength) {
          totalBytes = event.data.contentLength;
        } else if (event.event === "Progress") {
          downloadedBytes += event.data.chunkLength;
          const percent = totalBytes > 0 ? Math.round((downloadedBytes / totalBytes) * 100) : 0;
          setUpdateProgress({ status: "downloading", percent });
        } else if (event.event === "Finished") {
          setUpdateProgress({ status: "installing" });
        }
      });
      setUpdateProgress({ status: "done" });
    } catch (err) {
      setUpdateProgress({ status: "error", error: String(err) });
    }
  }, [updateAvailable]);

  const doRelaunchAfterUpdate = useCallback(async () => {
    try {
      await relaunchApp();
    } catch {
      setUpdateProgress({ status: "error", error: "请手动重启应用以完成更新" });
    }
  }, []);

  return {
    desktopVersion,
    backendVersion, setBackendVersion,
    versionMismatch, setVersionMismatch,
    newRelease, setNewRelease,
    updateAvailable, setUpdateAvailable,
    updateProgress, setUpdateProgress,
    checkVersionMismatch,
    checkForAppUpdate,
    doDownloadAndInstall,
    doRelaunchAfterUpdate,
  };
}
