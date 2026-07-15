import { describe, expect, it } from "vitest";

import tauriConfig from "../../../src-tauri/tauri.conf.json";

describe("desktop updater configuration", () => {
  it("uses the platform API with the Tauri-compatible OSS manifest as fallback", () => {
    expect(tauriConfig.plugins.updater.endpoints).toEqual([
      "https://openakita-admin-api.fzstack.com/updater/release.json?version={{current_version}}&platform={{target}}",
      "https://dl-openakita.fzstack.com/api/release.json",
    ]);
    expect(tauriConfig.plugins.updater.pubkey).toBe(
      "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDQ1RTQ1NjM2RkMxQ0Y4MDMKUldRRCtCejhObGJrUmR2VWdtbDMwSmhqdlE2RURSYTJKUTIxV25wRE1mcFA0Sy82Vi9zbUo3YWQK",
    );
  });
});
