import type { TFunction } from "i18next";
import { describe, expect, it } from "vitest";

import { localizeOrgCommandStateError, localizeOrgStatus } from "../orgStatus";

const labels: Record<string, string> = {
  "org.orgStatus.dormant": "休眠",
  "org.orgStatus.stopped": "已停止",
  "org.orgStatus.unknown": "未知",
};

const t = ((key: string, options?: Record<string, unknown>) => {
  if (key === "org.chat.commandUnavailableStatus") {
    return `无法下发组织指令。当前状态：${String(options?.status || "")}`;
  }
  return labels[key] || key;
}) as TFunction;

describe("organization status localization", () => {
  it("localizes all known state values through stable keys", () => {
    expect(localizeOrgStatus(t, "dormant")).toBe("休眠");
    expect(localizeOrgStatus(t, "STOPPED")).toBe("已停止");
  });

  it("localizes structured SSE and REST state errors", () => {
    expect(localizeOrgCommandStateError(t, {
      error_code: "org_not_runnable",
      org_status: "dormant",
    })).toBe("无法下发组织指令。当前状态：休眠");

    expect(localizeOrgCommandStateError(t, {
      detail: { code: "org_not_runnable", org_status: "stopped" },
    })).toBe("无法下发组织指令。当前状态：已停止");
  });

  it("leaves unrelated command errors to their existing renderer", () => {
    expect(localizeOrgCommandStateError(t, {
      code: "org_command_conflict",
      org_status: "active",
    })).toBeNull();
  });
});
