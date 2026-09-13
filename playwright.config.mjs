import { defineConfig, devices } from "@playwright/test";

// E2E smoke against the real ASGI app — the webServer boots uvicorn on a
// scratch port so `npx playwright test` is self-contained (blueprint §12).
// globalSetup builds the frontend first when the (gitignored) manifest is
// absent, e.g. on a fresh clone.
const PORT = Number(process.env.E2E_PORT || 8907);

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: false,
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.mjs",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // APP_ENV=production → the shell resolves hashed assets from the build
    // manifest instead of the (absent here) Vite dev server. APP_KEY is a
    // throwaway fixture: production apps refuse to boot without one.
    command:
      `APP_ENV=production APP_KEY=e2e-test-secret-key-0123456789abcdef ` +
      `.venv/bin/python -m uvicorn asgi:app --host 127.0.0.1 --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/v1/health`,
    reuseExistingServer: !process.env.CI,
    // Surface boot failures in the test log instead of opaque timeouts.
    stdout: "pipe",
    stderr: "pipe",
    timeout: 60_000,
  },
});
