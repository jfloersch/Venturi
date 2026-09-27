import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 180_000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:1420",
    viewport: { width: 1440, height: 1100 },
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "uv run venturi serve --storage ../../artifacts/e2e",
      url: "http://127.0.0.1:8765/v1/health",
      env: { VENTURI_API_TOKEN: "venturi-e2e-session-only" },
      timeout: 30_000,
    },
    { command: "npm run dev", url: "http://127.0.0.1:1420", timeout: 30_000 },
  ],
});
