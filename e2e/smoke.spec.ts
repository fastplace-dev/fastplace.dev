import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

// Full-stack smoke: HTML shell + bridge hydration + unified API health.

test("home page serves the HTML shell and hydrates the Home component", async ({ page }) => {
  await page.goto("/");

  // Server-rendered container the React app mounts into.
  await expect(page.locator("#fastplace")).toHaveCount(1);

  // Guest view: landing content plus the auth entry points.
  await expect(page.getByRole("heading", { name: "Let's get started" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Log in" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Register" })).toBeVisible();
});

test("anonymous dashboard visit bounces to the login page", async ({ page }) => {
  await page.goto("/dashboard");

  // The auth gate redirects anonymous users to /login (intended URL parked).
  await expect(page.getByRole("heading", { name: "Log in to your account" })).toBeVisible();
});

test("bridge request answers with a JSON page payload", async ({ request }) => {
  // /about is anonymous; /dashboard now sits behind the auth gate.
  const res = await request.get("/about", {
    headers: { "X-Fastplace-Request": "true", Accept: "application/json" },
  });
  expect(res.ok()).toBeTruthy();
  expect(res.headers()["content-type"]).toContain("application/json");

  const payload = await res.json();
  expect(payload.component).toBe("About/Index");
  expect(payload.url).toBe("/about");
  expect(typeof payload.version).toBe("string");
});

test("Link click performs a bridge navigation without a full page load", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Let's get started" })).toBeVisible();

  // The SPA router should own this navigation — no document reload.
  const shell = page.locator("#fastplace");
  await page.getByRole("link", { name: "Log in" }).click();

  await expect(page).toHaveURL("/login");
  await expect(page.getByRole("heading", { name: "Log in to your account" })).toBeVisible();
  // Same container element the initial shell mounted — no full reload.
  await expect(shell).toHaveCount(1);

  // Back-button (popstate) restores the previous page payload.
  await page.goBack();
  await expect(page).toHaveURL("/");
  await expect(page.getByRole("heading", { name: "Let's get started" })).toBeVisible();
});

test("guest auth entry points navigate to the Login page", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Let's get started" })).toBeVisible();

  await page.getByRole("link", { name: "Log in" }).click();

  await expect(page).toHaveURL("/login");
  await expect(page.getByRole("heading", { name: "Log in to your account" })).toBeVisible();
});

test("guest auth entry points navigate to the Register page", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Let's get started" })).toBeVisible();

  await page.getByRole("link", { name: "Register" }).click();

  await expect(page).toHaveURL("/register");
  await expect(page.getByRole("button", { name: "Create account" })).toBeVisible();
});

test("settings section nav reaches the Profile and Security pages", async ({ page }) => {
  // Register through the UI first — settings pages sit behind ["auth"]
  // (no ["verified"]), so the freshly registered (unverified) user gets in.
  await page.goto("/register");
  await page.getByLabel("Name").fill("Smoke");
  await page.getByLabel("Email address").fill("smoke@example.test");
  await page.getByLabel("Password", { exact: true }).fill("secret123");
  await page.getByLabel("Confirm password").fill("secret123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Email verification" })).toBeVisible();

  await page.goto("/settings/appearance");

  // The settings section nav links the ported pages — both must resolve.
  await page.getByRole("link", { name: "Profile" }).click();
  await expect(page).toHaveURL("/settings/profile");
  await expect(page.getByRole("heading", { name: "Profile settings" })).toBeVisible();

  await page.getByRole("link", { name: "Security" }).click();
  await expect(page).toHaveURL("/settings/security");
  await expect(page.getByRole("heading", { name: "Security settings" })).toBeVisible();
});

test("registration verifies through the mailed link and reaches the dashboard", async ({
  page,
}) => {
  // The first-user journey as the starter ships it: register → the
  // verification notice → the mailed link (log transport) → the blank,
  // zero-stats dashboard. Admin-ness of the first account is pinned by the
  // backend suite; this test pins the UX flow end to end.
  await page.goto("/register");
  await page.getByLabel("Name").fill("Verify");
  await page.getByLabel("Email address").fill("verify@example.test");
  await page.getByLabel("Password", { exact: true }).fill("secret123");
  await page.getByLabel("Confirm password").fill("secret123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Email verification" })).toBeVisible();

  // The webServer runs with APP_URL pointing at itself, so the log
  // transport's link is followable from this browser context. The mail log
  // accumulates across runs — take the latest line for this address.
  const log = readFileSync("storage/logs/mail.log", "utf8");
  const line = log
    .trim()
    .split("\n")
    .filter((entry) => entry.includes("verify@example.test"))
    .at(-1);
  const link = JSON.parse(line!).text.match(/https?:\/\/\S+/)![0];

  await page.goto(link);
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByText("0 projects")).toBeVisible();
});

test("unified API health endpoint answers ok", async ({ request }) => {
  const res = await request.get("/api/v1/health");
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  expect(body.status).toBe("ok");
  expect(body.framework).toBe("fastplace");
});
