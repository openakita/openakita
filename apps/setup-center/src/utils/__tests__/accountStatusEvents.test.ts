import { describe, expect, it, vi } from "vitest";

import {
  ACCOUNT_STATUS_CHANGED_EVENT,
  dispatchAccountStatusChanged,
} from "../accountStatusEvents";

describe("account status events", () => {
  it("publishes the latest account snapshot", () => {
    const listener = vi.fn();
    window.addEventListener(ACCOUNT_STATUS_CHANGED_EVENT, listener);

    dispatchAccountStatusChanged({ status: "signed_out" });

    expect(listener).toHaveBeenCalledOnce();
    expect((listener.mock.calls[0][0] as CustomEvent).detail).toEqual({ status: "signed_out" });
    window.removeEventListener(ACCOUNT_STATUS_CHANGED_EVENT, listener);
  });
});
