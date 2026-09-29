import { expect, test } from "@playwright/test";

/**
 * Broadcasting journey against the live ASGI app: a registered user's
 * /broadcast demo page holds a real /ws/broadcast socket, receives a
 * server-pushed message through the memory driver, and mirrors a presence
 * roster as a second browser context joins. The e2e webServer is a single
 * uvicorn process, so the in-process driver is the honest topology — no
 * redis in CI (guides/broadcasting: multi-worker deployments need redis).
 *
 * One test, two contexts: the first page's roster assertion depends on the
 * second context joining, and the demo page's socket must stay connected
 * across both legs.
 */
test("broadcast demo: server push arrives live and presence rosters track joins", async ({
  page,
  browser,
}) => {
  test.setTimeout(120_000);

  // 1. Register the first user — the sample app logs the session in at
  // once (unverified), and /ws/broadcast only needs the session cookie.
  await page.goto("/register");
  await page.getByLabel("Name").fill("Broadcast A");
  await page.getByLabel("Email address").fill("broadcast-a-e2e@example.test");
  await page.getByLabel("Password", { exact: true }).fill("secret123");
  await page.getByLabel("Confirm password").fill("secret123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Email verification" })).toBeVisible();

  // 2. Open the demo page — the socket connects and both subscriptions
  // (public demo channel + presence roster) land.
  await page.goto("/broadcast");
  await expect(page.getByRole("heading", { name: "Broadcasting" })).toBeVisible();
  await expect(page.locator('[data-test="ws-status"]')).toHaveText("socket: open", {
    timeout: 15_000,
  });
  await expect(page.locator('[data-test="member-count"]')).toHaveText("1", { timeout: 15_000 });

  // 3. Publish through the page's form — a bridge POST (no reload), so the
  // open socket receives the frame this very connection is subscribed to.
  await page.getByRole("button", { name: "Publish demo message" }).click();
  await expect(page.locator('[data-test="last-message"]')).toContainText("Server push works", {
    timeout: 15_000,
  });

  // 4. A second user joins the presence channel from a fresh context: the
  // server republishes the roster and the FIRST page must list both.
  const second = await browser.newContext();
  const pageB = await second.newPage();
  await pageB.goto("/register");
  await pageB.getByLabel("Name").fill("Broadcast B");
  await pageB.getByLabel("Email address").fill("broadcast-b-e2e@example.test");
  await pageB.getByLabel("Password", { exact: true }).fill("secret123");
  await pageB.getByLabel("Confirm password").fill("secret123");
  await pageB.getByRole("button", { name: "Create account" }).click();
  await expect(pageB.getByRole("heading", { name: "Email verification" })).toBeVisible();

  await pageB.goto("/broadcast");
  await expect(pageB.locator('[data-test="ws-status"]')).toHaveText("socket: open", {
    timeout: 15_000,
  });
  await expect(pageB.locator('[data-test="member-count"]')).toHaveText("2", { timeout: 15_000 });

  // The join propagated to the already-open first page.
  await expect(page.locator('[data-test="member-count"]')).toHaveText("2", { timeout: 15_000 });

  await second.close();
});
