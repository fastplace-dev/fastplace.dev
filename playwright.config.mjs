import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";

// E2E smoke against the real ASGI app — the webServer boots uvicorn on a
// scratch port so `npx playwright test` is self-contained (blueprint §12).
// globalSetup builds the frontend first when the (gitignored) manifest is
// absent, e.g. on a fresh clone — the scratch database is prepared by the
// webServer command below, not here.
const PORT = Number(process.env.E2E_PORT || 8907);

// Local runs use the project venv; CI (and any fresh clone without one)
// falls back to the interpreter the package is installed in.
const PY = existsSync(".venv/bin/python") ? ".venv/bin/python" : "python";
const FASTPLACE = existsSync(".venv/bin/fastplace") ? ".venv/bin/fastplace" : "fastplace";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: false,
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.mjs",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // APP_ENV=production → the shell resolves hashed assets from the build
    // manifest instead of the (absent here) Vite dev server. APP_KEY is a
    // throwaway fixture: production apps refuse to boot without one. The
    // DATABASE_URL points at a scratch DB — never a developer's seeded
    // local database.
    //
    // Playwright boots the webServer BEFORE globalSetup, so the scratch DB
    // is reset + migrated here, ahead of uvicorn: globalSetup cannot do it
    // without deleting the file out from under the live server's connection
    // pool (every later write then dies on the stale inode). Framework-
    // owned tables (sessions, password_reset_tokens) are created lazily by
    // the app against this final file. storage/ is gitignored runtime
    // output — sqlite refuses to open a file whose parent does not exist,
    // so mkdir it for fresh clones/worktrees.
    command:
      `APP_ENV=production APP_KEY=e2e-test-secret-key-0123456789abcdef ` +
      `APP_URL=http://localhost:${PORT} ` +
      `CACHE_ALLOW_MEMORY_IN_PRODUCTION=1 ` + // single uvicorn worker — safe here
      `DATABASE_URL=sqlite+aiosqlite:///storage/e2e.sqlite3 ` +
      `sh -c 'mkdir -p storage && ` +
      `rm -f storage/e2e.sqlite3 storage/e2e.sqlite3-wal storage/e2e.sqlite3-shm && ` +
      `${FASTPLACE} migrate && exec ${PY} -m uvicorn asgi:app --host 127.0.0.1 --port ${PORT}'`,
    url: `http://localhost:${PORT}/api/v1/health`,
    // Always boot a fresh server: the command above resets + re-migrates the
    // scratch DB before exec'ing uvicorn, and a reused server would skip
    // that reset — the tests expect the freshly migrated file, not the
    // previous run's data.
    reuseExistingServer: false,
    // Surface boot failures in the test log instead of opaque timeouts.
    stdout: "pipe",
    stderr: "pipe",
    timeout: 60_000,
  },
});
