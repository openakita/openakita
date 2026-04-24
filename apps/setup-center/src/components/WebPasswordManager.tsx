import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { safeFetch } from "../providers";
import { copyToClipboard } from "../utils/clipboard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function WebPasswordManager({ apiBase }: { apiBase: string }) {
  const { t } = useTranslation();
  const [hint, setHint] = useState<string | null>(null);
  const [newPw, setNewPw] = useState("");
  const [isBusy, setIsBusy] = useState(false);

  const loadHint = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBase}/api/auth/password-hint`);
      const data = await res.json();
      setHint(data.hint || "—");
    } catch {
      setHint(null);
    }
  }, [apiBase]);

  useEffect(() => { loadHint(); }, [loadHint]);

  const doChangePassword = async (password: string) => {
    const loadingId = toast.loading(t("common.loading"));
    setIsBusy(true);
    try {
      await safeFetch(`${apiBase}/api/auth/change-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_password: password }),
      });
      toast.success(t("adv.webPasswordChanged"));
      setNewPw("");
      await loadHint();
    } catch (e) {
      toast.error(String(e));
    } finally {
      toast.dismiss(loadingId);
      setIsBusy(false);
    }
  };

  const [generatedPw, setGeneratedPw] = useState<string | null>(null);

  const doRandomize = async () => {
    const chars = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789";
    let pw = "";
    for (let i = 0; i < 16; i++) pw += chars[Math.floor(Math.random() * chars.length)];
    await doChangePassword(pw);
    setGeneratedPw(pw);
    await copyToClipboard(pw);
    toast.success(t("adv.webPasswordReset", { password: pw }));
  };

  const copyGenerated = async () => {
    if (!generatedPw) return;
    const ok = await copyToClipboard(generatedPw);
    if (ok) toast.success(t("adv.webPasswordCopied", { defaultValue: "密码已复制到剪贴板" }));
  };

  return (
    <div className="flex flex-col gap-2.5">
      {hint !== null && (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground min-w-[80px]">{t("adv.webPasswordCurrent")}:</span>
          <code className="px-2 py-0.5 bg-muted/40 rounded text-sm tracking-wide">{hint}</code>
        </div>
      )}
      {generatedPw && (
        <div className="flex items-center gap-2 text-sm px-2.5 py-1.5 rounded-md border border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950/30">
          <span className="text-green-600 dark:text-green-400 font-medium whitespace-nowrap">{t("adv.webPasswordGenerated", { defaultValue: "新密码" })}:</span>
          <code className="flex-1 px-1.5 py-0.5 bg-muted/40 rounded text-sm tracking-wide select-all break-all">{generatedPw}</code>
          <Button variant="outline" size="xs" onClick={copyGenerated}>
            {t("common.copy", { defaultValue: "复制" })}
          </Button>
        </div>
      )}
      <div className="flex items-center gap-1.5 flex-wrap">
        <Input
          type="password"
          value={newPw}
          onChange={(e) => setNewPw(e.target.value)}
          placeholder={t("adv.webPasswordNewPlaceholder")}
          className="flex-1 min-w-[160px]"
        />
        <Button
          size="sm"
          onClick={() => { if (newPw.trim()) doChangePassword(newPw.trim()); }}
          disabled={!newPw.trim() || isBusy}
        >
          {t("adv.webPasswordSet")}
        </Button>
        <Button variant="outline" size="sm" onClick={doRandomize} disabled={isBusy}>
          {t("adv.webPasswordRandomize")}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground/70">{t("adv.webPasswordSetHint")}</p>
    </div>
  );
}

