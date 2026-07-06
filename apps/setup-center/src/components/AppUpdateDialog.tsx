import { useTranslation } from "react-i18next";
import { IconCheckCircle, IconPartyPopper, IconXCircle } from "../icons";
import { openExternalUrl, type UpdateInfo } from "../platform";
import type { NewReleaseInfo, UpdateProgressState } from "../hooks/useVersionCheck";
import { ReleaseNotesDialog } from "./ReleaseNotesDialog";

export function AppUpdateDialog({
  release,
  updateAvailable,
  onUpdate,
  onSkipVersion,
  onRemindLater,
}: {
  release: NewReleaseInfo;
  updateAvailable: UpdateInfo | null;
  onUpdate: () => void;
  onSkipVersion: () => void;
  onRemindLater: () => void;
}) {
  const { t } = useTranslation();

  const openReleasePage = async () => {
    await openExternalUrl(release.url);
  };

  return (
    <ReleaseNotesDialog
      version={release.latest}
      title={t("version.newRelease")}
      description={t("version.newReleaseDetail", {
        latest: release.latest,
        current: release.current,
      })}
      onClose={onRemindLater}
      footer={(
        <div className="appUpdateDialogActions">
          <button type="button" className="btnSmall" onClick={onSkipVersion}>
            {t("version.skipVersion")}
          </button>
          <div className="appUpdateDialogActionsRight">
            <button type="button" className="btnSmall" onClick={onRemindLater}>
              {t("version.remindNextTime")}
            </button>
            <button
              type="button"
              className="btnPrimary btnSmall"
              onClick={() => {
                if (updateAvailable) {
                  onUpdate();
                } else {
                  void openReleasePage();
                }
              }}
            >
              {updateAvailable ? t("version.updateAction") : t("version.viewRelease")}
            </button>
          </div>
        </div>
      )}
    />
  );
}

export function UpdateProgressToast({
  release,
  updateProgress,
  onRelaunch,
  onRetry,
}: {
  release: NewReleaseInfo | null;
  updateProgress: UpdateProgressState;
  onRelaunch: () => void;
  onRetry: () => void;
}) {
  const { t } = useTranslation();

  if (!release || updateProgress.status === "idle") return null;

  const statusIcon = updateProgress.status === "done"
    ? <IconCheckCircle size={16} />
    : updateProgress.status === "error"
      ? <IconXCircle size={16} />
      : <IconPartyPopper size={16} />;
  const statusLabel = updateProgress.status === "done"
    ? t("version.updateReady")
    : updateProgress.status === "error"
      ? t("version.updateFailed")
      : t("version.updating");

  return (
    <div className="appUpdateProgressToast">
      <div className="appUpdateProgressHeader">
        <span className="appUpdateProgressIcon">{statusIcon}</span>
        <span className="appUpdateProgressTitle">{statusLabel}</span>
      </div>

      <div className="appUpdateProgressDetail">
        {t("version.newReleaseDetail", { latest: release.latest, current: release.current })}
      </div>

      {updateProgress.status === "downloading" && (
        <>
          <div className="appUpdateProgressTrack">
            <div className="appUpdateProgressFill" style={{ width: `${updateProgress.percent || 0}%` }} />
          </div>
          <div className="appUpdateProgressText">
            {t("version.downloading")} {updateProgress.percent || 0}%
          </div>
        </>
      )}
      {updateProgress.status === "installing" && (
        <div className="appUpdateProgressText">{t("version.installing")}</div>
      )}
      {updateProgress.status === "error" && (
        <div className="appUpdateProgressError">{updateProgress.error}</div>
      )}

      <div className="appUpdateProgressActions">
        {updateProgress.status === "done" && (
          <button type="button" className="btnSmall btnSmallPrimary" onClick={onRelaunch}>
            {t("version.restartNow")}
          </button>
        )}
        {updateProgress.status === "error" && (
          <button type="button" className="btnSmall" onClick={onRetry}>
            {t("version.retry")}
          </button>
        )}
      </div>
    </div>
  );
}
