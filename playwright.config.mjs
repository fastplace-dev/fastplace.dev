import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";

// E2E smoke against the real ASGI app — the webServer boots uvicorn on a
// scratch port so `npx playwright test` is self-contained (blueprint §12).
// globalSetup builds the frontend first when the (gitignored) manifest is
// absent, e.g. on a fresh clone, and prepares a scratch database.
const PORT = Number(process.env.E2E_PORT || 8907);

// Local runs use the project venv; CI (and any fresh clone without one)
// falls back to the interpreter the package is installed in.
const PY = existsSync(".venv/bin/python") ? ".venv/bin/python" : "python";

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
    // throwaway fixture: production apps refuse to boot without one. The
    // DATABASE_URL points at the scratch DB globalSetup migrated — never a
    // developer's seeded local database.
    command:
      `APP_ENV=production APP_KEY=e2e-test-secret-key-0123456789abcdef ` +
      `DATABASE_URL=sqlite+aiosqlite:///storage/e2e.sqlite3 ` +
      `${PY} -m uvicorn asgi:app --host 127.0.0.1 --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/v1/health`,
    // Always boot a fresh server: globalSetup deletes + re-migrates the
    // scratch DB, and a reused server would keep pooled connections to the
    // deleted file (stale inode) while the tests expect the fresh one.
    reuseExistingServer: false,
    // Surface boot failures in the test log instead of opaque timeouts.
    stdout: "pipe",
    stderr: "pipe",
    timeout: 60_000,
  },
});
