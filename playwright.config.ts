import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  timeout: 12 * 60 * 1000,
  expect: { timeout: 120_000 },
  use: {
    baseURL: "http://127.0.0.1:4173/build123d-flow/",
    headless: true,
    serviceWorkers: "block",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: {
      args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"]
    }
  },
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173/build123d-flow/",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
    env: { GITHUB_PAGES: "true" }
  }
});
