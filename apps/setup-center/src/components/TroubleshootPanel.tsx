import { useState } from "react";
import { copyToClipboard } from "../utils/clipboard";

export function TroubleshootPanel({ t }: { t: (k: string) => string }) {
  const [copied, setCopied] = useState<string | null>(null);
  const isWin = navigator.platform?.toLowerCase().includes("win");
  const listCmd = isWin ? 'tasklist | findstr python' : 'ps aux | grep openakita';
  const killCmd = isWin ? 'taskkill /F /PID <PID>' : 'kill -9 <PID>';

  const copyText = async (text: string, id: string) => {
    const ok = await copyToClipboard(text);
    if (ok) {
      setCopied(id);
      setTimeout(() => setCopied(null), 1500);
    }
  };

  return (
    <div style={{ padding: "10px 16px", fontSize: 12, color: "var(--text)", borderTop: "1px solid var(--line)", borderBottom: "1px solid var(--line)" }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>{t("status.troubleshootTitle")}</div>
      <div style={{ marginBottom: 6, color: "var(--muted)" }}>{t("status.troubleshootTip")}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ color: "var(--muted)", minWidth: 60, flexShrink: 0 }}>{t("status.troubleshootListProcess")}:</span>
          <code style={{ background: "var(--nav-hover)", padding: "2px 8px", borderRadius: 4, fontSize: 11, flex: 1, color: "var(--text)" }}>{listCmd}</code>
          <button className="btnSmall" style={{ fontSize: 10, padding: "1px 6px" }} onClick={() => copyText(listCmd, "list")}>
            {copied === "list" ? t("status.troubleshootCopied") : t("status.troubleshootCopy")}
          </button>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ color: "var(--muted)", minWidth: 60, flexShrink: 0 }}>{t("status.troubleshootKillProcess")}:</span>
          <code style={{ background: "var(--nav-hover)", padding: "2px 8px", borderRadius: 4, fontSize: 11, flex: 1, color: "var(--text)" }}>{killCmd}</code>
          <button className="btnSmall" style={{ fontSize: 10, padding: "1px 6px" }} onClick={() => copyText(killCmd, "kill")}>
            {copied === "kill" ? t("status.troubleshootCopied") : t("status.troubleshootCopy")}
          </button>
        </div>
      </div>
      <div style={{ marginTop: 6, color: "var(--muted)", fontSize: 11 }}>{t("status.troubleshootRestart")}</div>
    </div>
  );
}

