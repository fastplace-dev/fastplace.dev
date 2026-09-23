import { readFile } from "node:fs/promises";

import { expect, test } from "@playwright/test";

/**
 * Auth Phase 3 journeys — reset + verification through the real browser
 * bridge (spec §4.10/§4.11). mail.log (JSON lines, newest LAST) is the
 * inbox; logout goes through the UI TextLink (a raw request.post lacks
 * the CSRF token and dies 419).
 */
const SENT = "We have emailed your password reset link.";
const RESET = "Your password has been reset.";

// The spec itself runs in Node, so read the inbox here — page.evaluate
// would run inside Chromium, where node:fs/promises cannot be imported.
// mail.log appends across tests, so scope the scan to one recipient.
async function lastLinkMatching(fragment: string, to: string) {
  const lines = (await readFile("storage/logs/mail.log", "utf8")).trim().split("\n");
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const message = JSON.parse(lines[i]);
    if (message.to !== to) continue;
    const found = String(message.text)
      .split(/\s+/)
      .find((word) => word.includes(fragment));
    if (found) return found;
  }
  throw new Error(`no link containing ${fragment} mailed to ${to}`);
}

test.describe.serial("password reset and email verification", () => {
  test("anonymous dashboard visit bounces to login", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "Log in to your account" })).toBeVisible();
  });

  test("unknown forgot-password email answers identically", async ({ page }) => {
    await page.goto("/forgot-password");
    await page.getByLabel("Email address").fill("ghost@example.test");
    await page.getByRole("button", { name: "Email password reset link" }).click();
    await expect(page.getByText(SENT)).toBeVisible();
  });

  test("register, reset, verify — the full journey", async ({ page }) => {
    // Register: auto-login, then the verified gate bounces to the notice page.
    await page.goto("/register");
    await page.getByLabel("Name").fill("E2E Firoz");
    await page.getByLabel("Email address").fill("e2e-firoz@example.test");
    await page.getByLabel("Password", { exact: true }).fill("secret123");
    await page.getByLabel("Confirm password").fill("secret123");
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByRole("heading", { name: "Email verification" })).toBeVisible();
    await expect(page.getByText("Please verify your email address by clicking on the link we just emailed to you.")).toBeVisible();

    // Log out through the UI link (raw posts lack CSRF and die 419). The
    // bridge swap above leaves the document's csrf meta stale (login rotated
    // the token) — reload the notice page first so the link posts the fresh
    // token instead of dying 419 and falling back to a raw GET /logout.
    await page.goto("/email/verify");
    await page.getByRole("link", { name: "Log out" }).click();
    await expect(page.getByRole("heading", { name: "Log in to your account" })).toBeVisible();

    // Forgot password: flash + the reset link lands in mail.log.
    await page.goto("/forgot-password");
    await page.getByLabel("Email address").fill("e2e-firoz@example.test");
    await page.getByRole("button", { name: "Email password reset link" }).click();
    await expect(page.getByText(SENT)).toBeVisible();
    const resetUrl = await lastLinkMatching("/reset-password/", "e2e-firoz@example.test");
    const resetPath = resetUrl.replace(/^https?:\/\/[^/]+/, "");
    await page.goto(resetPath);
    await expect(page.getByRole("heading", { name: "Reset password" })).toBeVisible();

    // Redeem: new password, flashed confirmation on /login.
    await page.getByLabel("Password", { exact: true }).fill("new-secret-123");
    await page.getByLabel("Confirm password").fill("new-secret-123");
    await page.getByRole("button", { name: "Reset password" }).click();
    await expect(page.getByText(RESET)).toBeVisible();

    // Login with the new password — still unverified, bounced again.
    await page.getByLabel("Email address").fill("e2e-firoz@example.test");
    await page.getByLabel("Password", { exact: true }).fill("new-secret-123");
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page.getByRole("heading", { name: "Email verification" })).toBeVisible();

    // The REGISTRATION verification link (first mail for this address)
    // clears the gate; only a full page.goto may assert URLs.
    const verifyUrl = await lastLinkMatching("/email/verify/", "e2e-firoz@example.test");
    const verifyPath = verifyUrl.replace(/^https?:\/\/[^/]+/, "");
    await page.goto(verifyPath);
    await expect(page).toHaveURL(/dashboard/);
    // The dashboard's only h1 is the app name — assert its Stats region
    // instead, which no other page renders.
    await expect(page.getByRole("region", { name: "Stats" })).toBeVisible();
  });
});
