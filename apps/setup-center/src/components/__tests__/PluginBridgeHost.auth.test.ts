import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";

const mocks = vi.hoisted(() => ({
  authFetch: vi.fn(),
  downloadFile: vi.fn(),
}));

vi.mock("../../platform/auth", () => ({
  authFetch: mocks.authFetch,
  isTauriRemoteMode: () => false,
}));

vi.mock("../../platform", () => ({
  IS_TAURI: false,
  copyFileToDownloads: vi.fn(),
  downloadFile: mocks.downloadFile,
}));

import { PluginBridgeHost } from "../../lib/plugin-bridge-host";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("PluginBridgeHost authenticated proxy", () => {
  let iframe: HTMLIFrameElement;
  let bridge: PluginBridgeHost;
  let postMessage: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    mocks.authFetch.mockReset();
    mocks.downloadFile.mockReset();
    iframe = document.createElement("iframe");
    document.body.appendChild(iframe);
    postMessage = vi.spyOn(iframe.contentWindow!, "postMessage");
    bridge = new PluginBridgeHost({
      pluginId: "happyhorse-video",
      iframe,
      apiBase: "https://akita.example",
      theme: "light",
      locale: "zh-CN",
    });
  });

  afterEach(() => {
    bridge.dispose();
    iframe.remove();
  });

  function send(type: string, payload: Record<string, unknown>, requestId: string) {
    window.dispatchEvent(new MessageEvent("message", {
      source: iframe.contentWindow,
      data: { __akita_bridge: true, version: 1, type, payload, requestId },
    }));
  }

  it("uses the auth-aware transport for plugin API requests", async () => {
    mocks.authFetch.mockResolvedValueOnce(response({ tasks: [] }));

    send("bridge:api-request", {
      method: "GET",
      path: "/api/plugins/happyhorse-video/tasks",
    }, "api-1");

    await waitFor(() => expect(mocks.authFetch).toHaveBeenCalledTimes(1));
    expect(mocks.authFetch.mock.calls[0][0]).toBe(
      "https://akita.example/api/plugins/happyhorse-video/tasks",
    );
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      type: "bridge:api-response",
      requestId: "api-1",
      payload: expect.objectContaining({ ok: true, status: 200, body: { tasks: [] } }),
    }), "*"));
  });

  it("rebuilds multipart form data and uploads through the auth-aware transport", async () => {
    mocks.authFetch.mockResolvedValueOnce(response({ ok: true, preview_url: "/preview/1" }));
    const file = new File(["image"], "frame.png", { type: "image/png" });

    send("bridge:upload", {
      path: "/api/plugins/happyhorse-video/upload",
      entries: [{ name: "file", value: file, filename: file.name }],
    }, "upload-1");

    await waitFor(() => expect(mocks.authFetch).toHaveBeenCalledTimes(1));
    const init = mocks.authFetch.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBeInstanceOf(Blob);
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      type: "bridge:upload-ack",
      requestId: "upload-1",
      payload: expect.objectContaining({ ok: true, status: 200 }),
    }), "*"));
  });

  it("rejects requests outside the current plugin API", async () => {
    send("bridge:api-request", {
      method: "GET",
      path: "/api/config/workspace-info",
    }, "api-denied");

    await waitFor(() => expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      type: "bridge:api-response",
      requestId: "api-denied",
      payload: expect.objectContaining({ ok: false, status: 0 }),
    }), "*"));
    expect(mocks.authFetch).not.toHaveBeenCalled();
  });
});
