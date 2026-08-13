export const ACCOUNT_STATUS_CHANGED_EVENT = "openakita:account-status-changed";

export type AccountStatusSummary = {
  status: string;
  status_reason?: string | null;
  fetched_at?: string;
  entitlements?: unknown;
  profile?: {
    email?: string;
    name?: string;
    preferred_username?: string;
  };
};

export function dispatchAccountStatusChanged(snapshot: AccountStatusSummary): void {
  window.dispatchEvent(new CustomEvent<AccountStatusSummary>(ACCOUNT_STATUS_CHANGED_EVENT, {
    detail: snapshot,
  }));
}
