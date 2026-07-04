import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for the OpenAkita setup-center frontend.
 * Targets the running Vite dev server at http://127.0.0.1:5173 and
 * the backend at http://127.0.0.1:18900 (already started outside).
 *
 * smoke-5bug: this config lands purely to support the
 * tmp_p10/_5bug_screens regression sweep.  It is intentionally
 * minimal -- single chromium project, no retries, no parallelism.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:5173",
    headless: true,
    viewport: { width: 1280, height: 800 },
    actionTimeout: 8_000,
    navigationTimeout: 20_000,
    screenshot: "only-on-failure",
    trace: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});