// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "../chatTypes";

const messages: ChatMessage[] = [
  { id: "message-1", role: "user", content: "Hello", timestamp: 1 },
];

async function loadExporter(options: { tauri: boolean; savePath?: string | null }) {
  const saveFileDialog = vi.fn().mockResolvedValue(options.savePath ?? null);
  const writeTextFile = vi.fn().mockResolvedValue(undefined);
  const logger = {
    info: vi.fn(),
    error: vi.fn(),
  };

  vi.doMock("../../../../platform", () => ({
    IS_TAURI: options.tauri,
    logger,
    saveFileDialog,
    writeTextFile,
  }));
  vi.doMock("../../../../platform/auth", () => ({ getAccessToken: vi.fn() }));

  const { exportConversation } = await import("../chatHelpers");
  return { exportConversation, logger, saveFileDialog, writeTextFile };
}

describe("exportConversation", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("uses the native save dialog and filesystem in Tauri", async () => {
    const { exportConversation, saveFileDialog, writeTextFile } = await loadExporter({
      tauri: true,
      savePath: "C:\\Users\\tester\\Desktop\\Chat.md",
    });

    await expect(exportConversation(messages, "Chat", "md")).resolves.toBe(true);
    expect(saveFileDialog).toHaveBeenCalledWith({
      title: "导出会话",
      defaultPath: "Chat.md",
      filters: [{ name: "Markdown", extensions: ["md"] }],
    });
    expect(writeTextFile).toHaveBeenCalledWith(
      "C:\\Users\\tester\\Desktop\\Chat.md",
      expect.stringContaining("Hello"),
    );
  });

  it("does not write when the native save dialog is cancelled", async () => {
    const { exportConversation, writeTextFile } = await loadExporter({
      tauri: true,
      savePath: null,
    });

    await expect(exportConversation(messages, "Chat", "json")).resolves.toBe(false);
    expect(writeTextFile).not.toHaveBeenCalled();
  });

  it("keeps browser downloads for the web build", async () => {
    const { exportConversation, saveFileDialog, writeTextFile } = await loadExporter({ tauri: false });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const createObjectURL = vi.fn().mockReturnValue("blob:conversation");
    const revokeObjectURL = vi.fn();
    Object.defineProperties(URL, {
      createObjectURL: { configurable: true, value: createObjectURL },
      revokeObjectURL: { configurable: true, value: revokeObjectURL },
    });

    await expect(exportConversation(messages, "Chat", "json")).resolves.toBe(true);
    expect(click).toHaveBeenCalledOnce();
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(saveFileDialog).not.toHaveBeenCalled();
    expect(writeTextFile).not.toHaveBeenCalled();
  });
});
