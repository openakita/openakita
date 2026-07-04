import { defineConfig, devices } from "@playwright/test";

/**
 * 财务插件 M1 W3 端到端冒烟测试专用配置。
 *
 * 区别于 playwright.config.ts：
 *   - baseURL 直接指向后端 18900（OpenAkita 后端会同时托管前端 bundle），
 *     不依赖独立的 Vite dev server，CI / 桌面环境都能跑。
 *   - testMatch 限定到 finance-auto-ui.spec.ts，避免拖到旧版 v2-orgs 用例。
 *   - 截图落到 ../../tmp_p10/_finance_w3_screens/，归档到完成报告里。
 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: "finance-auto-ui.spec.ts",
  timeout: 180_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:18900",
    headless: true,
    viewport: { width: 1440, height: 900 },
    actionTimeout: 12_000,
    navigationTimeout: 30_000,
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
