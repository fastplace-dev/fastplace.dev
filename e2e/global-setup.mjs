import { execSync } from "node:child_process";
import { existsSync, mkdirSync, rmSync } from "node:fs";
import path from "node:path";

// E2E runs against APP_ENV=production, whose shell resolves hashed assets
// from the build manifest. public/build is gitignored, so a fresh clone has
// none — build it once here to keep `npx playwright test` self-contained.
//
// The suite also gets its own scratch database: a developer's seeded
// database.sqlite3 must not leak into (or break) the "empty app" smoke
// assertions. Fresh file + `fastplace migrate` every run. storage/ itself
// is gitignored runtime output — a fresh clone has no such directory, and
// sqlite refuses to open a file whose parent does not exist.
export const E2E_DATABASE = "storage/e2e.sqlite3";

export default function globalSetup() {
  const vite6 = path.resolve("public/build/.vite/manifest.json");
  const legacy = path.resolve("public/build/manifest.json");
  if (!existsSync(vite6) && !existsSync(legacy)) {
    console.log("[e2e] no build manifest found — running npm run build…");
    execSync("npm run build", { stdio: "inherit" });
  }

  mkdirSync(path.dirname(E2E_DATABASE), { recursive: true });
  rmSync(E2E_DATABASE, { force: true });
  rmSync(`${E2E_DATABASE}-wal`, { force: true });
  rmSync(`${E2E_DATABASE}-shm`, { force: true });
  execSync(`.venv/bin/fastplace migrate`, {
    stdio: "inherit",
    env: { ...process.env, DATABASE_URL: `sqlite+aiosqlite:///${E2E_DATABASE}` },
  });
}
