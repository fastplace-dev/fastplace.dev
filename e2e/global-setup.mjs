import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

// E2E runs against APP_ENV=production, whose shell resolves hashed assets
// from the build manifest. public/build is gitignored, so a fresh clone has
// none — build it once here to keep `npx playwright test` self-contained.
//
// The scratch database is deliberately NOT prepared here: Playwright boots
// the webServer before globalSetup, so anything this file does to the
// database happens under the live server. Deleting a SQLite file out from
// under a server's connection pool strands every pooled connection on a
// stale inode — all later writes die with "attempt to write a readonly
// database". The reset + migrate therefore live in the webServer command
// itself (see playwright.config.mjs), which runs before uvicorn opens the
// file; framework-owned tables (sessions, password_reset_tokens) are then
// created lazily by the running app against that final file.
export default function globalSetup() {
  const vite6 = path.resolve("public/build/.vite/manifest.json");
  const legacy = path.resolve("public/build/manifest.json");
  if (!existsSync(vite6) && !existsSync(legacy)) {
    console.log("[e2e] no build manifest found — running npm run build…");
    execSync("npm run build", { stdio: "inherit" });
  }
}
