import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

// E2E runs against APP_ENV=production, whose shell resolves hashed assets
// from the build manifest. public/build is gitignored, so a fresh clone has
// none — build it once here to keep `npx playwright test` self-contained.
export default function globalSetup() {
  const vite6 = path.resolve("public/build/.vite/manifest.json");
  const legacy = path.resolve("public/build/manifest.json");
  if (!existsSync(vite6) && !existsSync(legacy)) {
    console.log("[e2e] no build manifest found — running npm run build…");
    execSync("npm run build", { stdio: "inherit" });
  }
}
