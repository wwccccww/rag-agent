import { defineConfig, devices } from "@playwright/test";

const PORT = 3000;
const baseURL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev",
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: "chromium",
      // 本地环境可能无法下载 Playwright 内置浏览器：优先使用已安装的 Chrome。
      // CI 里会显式安装 playwright 浏览器，因此不设置 channel。
      use: { ...devices["Desktop Chrome"], ...(process.env.CI ? {} : { channel: "chrome" as const }) },
    },
  ],
});

