import { describe, expect, it } from "vitest";

import type { ChatMessage } from "../chatTypes";
import { chooseHydratedMessages, messageHistoryRichness } from "../chatHelpers";

const user: ChatMessage = {
  id: "user-1",
  role: "user",
  content: "你好",
  timestamp: 1,
};

describe("chat error hydration", () => {
  it("prefers a finalized error card over a stale streaming placeholder", () => {
    const streaming: ChatMessage = {
      id: "assistant-1",
      role: "assistant",
      content: "",
      timestamp: 2,
      streaming: true,
    };
    const error: ChatMessage = {
      ...streaming,
      streaming: false,
      errorInfo: {
        message: "无法下发组织指令。当前状态：休眠",
        category: "unknown",
      },
    };

    expect(messageHistoryRichness([user, error]))
      .toBeGreaterThan(messageHistoryRichness([user, streaming]));
  });

  it("hydrates a persisted backend error onto the local placeholder", () => {
    const local: ChatMessage[] = [
      user,
      {
        id: "assistant-local",
        role: "assistant",
        content: "",
        timestamp: 2,
        streaming: true,
      },
    ];
    const backend: ChatMessage[] = [
      user,
      {
        id: "assistant-backend",
        role: "assistant",
        content: "",
        timestamp: 3,
        errorInfo: {
          message: "无法下发组织指令。当前状态：休眠",
          category: "unknown",
        },
      },
    ];

    const hydrated = chooseHydratedMessages(local, backend);

    expect(hydrated[1].errorInfo?.message).toBe("无法下发组织指令。当前状态：休眠");
  });
});
