import { defineConfig } from "@playwright/test";

const uiPort = 1421;
const apiPort = 8766;

export default defineConfig({
  testDir: "./tests",
  timeout: 180_000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${uiPort}`,
    viewport: { width: 1440, height: 1100 },
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `uv run venturi serve --storage ../../artifacts/e2e --port ${apiPort}`,
      url: `http://127.0.0.1:${apiPort}/v1/health`,
      env: {
        VENTURI_API_TOKEN: "venturi-e2e-session-only",
        VENTURI_UI_ORIGIN: `http://127.0.0.1:${uiPort}`,
      },
      timeout: 30_000,
    },
    {
      command: `npm run dev -- --port ${uiPort} --strictPort`,
      url: `http://127.0.0.1:${uiPort}`,
      env: { VITE_VENTURI_API: `http://127.0.0.1:${apiPort}` },
      timeout: 30_000,
    },
  ],
});
