/**
 * Generic Plugin Onboard Modal
 *
 * Supports QR code scanning, OAuth redirect, and credential-only flows.
 * Works with any plugin that declares an "onboard" section in plugin.json.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { QRCodeSVG } from "qrcode.react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Loader2, RefreshCw } from "lucide-react";
import { safeFetch } from "../providers";

interface OnboardConfig {
  type: "qr" | "oauth" | "credentials";
  start_endpoint: string;
  poll_endpoint: string;
  description?: string;
}

interface PluginOnboardModalProps {
  pluginId: string;
  apiBaseUrl: string;
  onboard: OnboardConfig;
  onClose: () => void;
  onSuccess: (credentials: Record<string, string>) => void;
}

export function PluginOnboardModal({
  pluginId,
  apiBaseUrl,
  onboard,
  onClose,
  onSuccess,
}: PluginOnboardModalProps) {
  const { t } = useTranslation();
  const [qrData, setQrData] = useState<string>("");
  const [status, setStatus] = useState<string>("initializing");
  const [error, setError] = useState<string>("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const startOnboard = useCallback(async () => {
    setStatus("initializing");
    setError("");
    try {
      const resp = await safeFetch(
        `${apiBaseUrl}/api/plugins/${pluginId}${onboard.start_endpoint}`,
        { method: "POST" }
      );
      if (resp.ok) {
        const data = await resp.json();
        if (data.qr_data) {
          setQrData(data.qr_data);
          setStatus("scanning");
        } else if (data.auth_url) {
          window.open(data.auth_url, "_blank");
          setStatus("waiting");
        } else {
          setStatus(data.status || "waiting");
        }
      } else {
        setError("Failed to start onboarding");
        setStatus("error");
      }
    } catch (e: any) {
      setError(e.message || "Connection error");
      setStatus("error");
    }
  }, [apiBaseUrl, pluginId, onboard.start_endpoint]);

  const pollStatus = useCallback(async () => {
    try {
      const resp = await safeFetch(
        `${apiBaseUrl}/api/plugins/${pluginId}${onboard.poll_endpoint}`,
        { method: "POST" }
      );
      if (resp.ok) {
        const data = await resp.json();
        if (data.status === "success") {
          setStatus("success");
          if (pollRef.current) clearInterval(pollRef.current);
          onSuccess(data.credentials || {});
          return;
        }
        if (data.status === "expired") {
          if (data.qr_data) {
            setQrData(data.qr_data);
            setStatus("scanning");
          } else {
            setStatus("expired");
          }
        } else {
          if (data.qr_data) setQrData(data.qr_data);
          setStatus(data.status || "waiting");
        }
      }
    } catch {
      // Silently retry
    }
  }, [apiBaseUrl, pluginId, onboard.poll_endpoint, onSuccess]);

  useEffect(() => {
    startOnboard();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [startOnboard]);

  useEffect(() => {
    if (status === "scanning" || status === "waiting" || status === "scanned") {
      pollRef.current = setInterval(pollStatus, 3000);
      return () => {
        if (pollRef.current) clearInterval(pollRef.current);
      };
    }
  }, [status, pollStatus]);

  return (
    <Dialog open onOpenChange={() => onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>
            {onboard.type === "qr"
              ? t("im.pluginQrTitle", { defaultValue: "Scan QR Code" })
              : t("im.pluginOnboardTitle", { defaultValue: "Connect Account" })}
          </DialogTitle>
        </DialogHeader>

        <div className="flex flex-col items-center gap-4 py-4">
          {onboard.description && (
            <p className="text-sm text-muted-foreground text-center">
              {onboard.description}
            </p>
          )}

          {status === "initializing" && (
            <Loader2 className="animate-spin" size={32} />
          )}

          {(status === "scanning" || status === "scanned") && qrData && (
            <div className="bg-white p-4 rounded-lg">
              <QRCodeSVG value={qrData} size={220} />
            </div>
          )}

          {status === "scanning" && (
            <p className="text-sm text-muted-foreground animate-pulse">
              {t("im.pluginQrWaiting", { defaultValue: "Waiting for scan..." })}
            </p>
          )}

          {status === "scanned" && (
            <p className="text-sm text-green-600">
              {t("im.pluginQrScanned", { defaultValue: "Scanned! Confirming..." })}
            </p>
          )}

          {status === "success" && (
            <p className="text-sm text-green-600 font-medium">
              {t("im.pluginOnboardSuccess", { defaultValue: "Connected successfully!" })}
            </p>
          )}

          {status === "expired" && (
            <div className="flex flex-col items-center gap-2">
              <p className="text-sm text-amber-600">
                {t("im.pluginQrExpired", { defaultValue: "QR code expired" })}
              </p>
              <Button variant="outline" size="sm" onClick={startOnboard}>
                <RefreshCw size={14} className="mr-1" />
                {t("im.pluginQrRefresh", { defaultValue: "Refresh" })}
              </Button>
            </div>
          )}

          {status === "error" && (
            <div className="flex flex-col items-center gap-2">
              <p className="text-sm text-destructive">{error}</p>
              <Button variant="outline" size="sm" onClick={startOnboard}>
                {t("im.pluginOnboardRetry", { defaultValue: "Retry" })}
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

