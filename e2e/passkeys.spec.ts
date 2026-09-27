import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

/**
 * Passkey journey through a REAL Chromium WebAuthn stack: Playwright's
 * virtual authenticator (context.credentials, 1.61+) answers
 * navigator.credentials.create/get, so the framework's register → login →
 * confirm → delete ceremonies run end to end against the live ASGI app
 * (spec §6, E2E v1 gate). Chromium-only — the WebAuthn override is the gate.
 *
 * Everything shares ONE browser context: the virtual authenticator and its
 * resident credential must survive the logout → passkey-login hop, and each
 * Playwright test gets a fresh context — hence the whole journey in one test.
 *
 * The password-confirmation leg drives the two-factor enable POST (the
 * sample app's password.confirm-guarded route): pre-confirm it 302s onto
 * /user/confirm-password; after the passkey confirm the same guarded POST
 * succeeds — that is the pass-through.
 */

// The journey is long (register → verify → 3 WebAuthn ceremonies) — the
// project default (30s) is tuned for single-page smokes.
test.setTimeout(120_000);

test("passkey journey: register, passwordless login, confirm, delete", async ({ page }) => {
  // 0. Arm Playwright's virtual WebAuthn authenticator (1.61+) before any
  // ceremony runs. Its credentials are discoverable by design and user
  // verification is auto-granted — the confirm ceremony always demands UV
  // (spec §4). Raw CDP addVirtualAuthenticator can't stand in here: it
  // stores residentKey "preferred" registrations as non-discoverable, so
  // the usernameless login finds nothing and dies NotAllowedError.
  await page.context().credentials.install();

  // 1. Register a fresh user — the scratch DB resets every run, mail.log
  // does not, so the inbox scan below takes the LAST line for the address.
  const email = "passkey-e2e@example.test";
  await page.goto("/register");
  await page.getByLabel("Name").fill("Passkey E2E");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill("secret123");
  await page.getByLabel("Confirm password").fill("secret123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Email verification" })).toBeVisible();

  // 2. Verify through the mailed link (the passkey routes sit behind
  // auth + verified; the settings pages themselves are auth-only).
  const log = readFileSync("storage/logs/mail.log", "utf8");
  const line = log
    .trim()
    .split("\n")
    .filter((entry) => entry.includes(email))
    .at(-1);
  const verifyLink = JSON.parse(line!).text.match(/https?:\/\/\S+/)![0];
  await page.goto(verifyLink);
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("region", { name: "Stats" })).toBeVisible();

  // 3. Register a passkey on the Security page — the virtual authenticator
  // answers navigator.credentials.create; a residentKey:preferred credential
  // is created, which the discoverable login in step 5 relies on.
  await page.goto("/settings/security");
  await expect(page.getByRole("heading", { name: "Security settings" })).toBeVisible();
  await expect(page.getByText("No passkeys yet")).toBeVisible();

  await page.getByRole("button", { name: "Add passkey" }).click();
  await page.getByLabel("Passkey name").fill("E2E Passkey");
  await page.getByRole("button", { name: "Register passkey" }).click();

  // Success refetches the page — the card replaces the empty state.
  await expect(page.getByText("E2E Passkey")).toBeVisible();

  // 4. Log out through the UI.
  await page.goto("/settings/security");
  // Logout lives in the sidebar user dropdown; the app uses data-test, not
  // data-testid, so locate by attribute.
  await page.locator('[data-test="sidebar-menu-button"]').click();
  await page.locator('[data-test="logout-button"]').click();
  await expect(page).toHaveURL(/\/login$/);

  // 5. Passwordless login — from a FRESH page. The logout handoff is racy:
  // the bridge follows the 303 as an XHR while the browser also navigates,
  // and two overlapping session-less loads of /login can leave the rendered
  // CSRF meta and the stored session cookie sourced from different
  // responses (a 419 on the assertion POST under full-suite load). A new
  // page loads /login exactly once, so meta and cookie cannot disagree.
  const login = await page.context().newPage();
  await page.close();
  await login.goto("/login");
  await expect(login.getByRole("heading", { name: "Log in to your account" })).toBeVisible();

  // Empty allowList: the authenticator discovers the resident credential
  // from step 3 and signs the assertion.
  await login.getByRole("button", { name: "Sign in with a passkey" }).click();
  await expect(login).toHaveURL(/\/dashboard$/);
  await expect(login.getByRole("region", { name: "Stats" })).toBeVisible();

  // 6. The password.confirm gate: enabling 2FA is a guarded route — the
  // unconfirmed session 302s onto the confirm page (intended parked).
  await login.goto("/settings/security");
  await login.getByRole("button", { name: "Enable 2FA" }).click();
  await expect(login).toHaveURL(/\/user\/confirm-password$/);
  await expect(
    login.getByText("This is a secure area of the application", { exact: false }),
  ).toBeVisible();

  // 7. Confirm with the passkey instead of the password — UV is mandatory
  // on this ceremony regardless of config, and the virtual authenticator's
  // auto-granted user verification covers it.
  await login.getByRole("button", { name: "Confirm with passkey" }).click();
  // The confirm payload redirects to the parked intended URL — a POST-only
  // route, so the bridge's full-navigation fallback lands on its 405 page.
  await expect(login).toHaveURL(/\/user\/two-factor-authentication$/);

  // 8. Pass-through: the SAME guarded POST now succeeds — the setup modal
  // opens only on success, proving the passkey confirmation stamped the
  // session (password_confirmed_at) without a password ever being typed.
  await login.goto("/settings/security");
  await login.getByRole("button", { name: "Enable 2FA" }).click();
  await expect(login.getByRole("dialog")).toBeVisible();

  // 9. Delete the passkey (confirm dialog), then the empty state returns.
  await login.goto("/settings/security");
  await login.getByRole("button", { name: "Remove", exact: true }).click();
  await login.getByRole("button", { name: "Remove passkey" }).click();
  // The dialog stays open after a successful delete — close, then refetch.
  await login.getByRole("button", { name: "Cancel" }).click();
  await login.goto("/settings/security");
  await expect(login.getByText("No passkeys yet")).toBeVisible();
});
