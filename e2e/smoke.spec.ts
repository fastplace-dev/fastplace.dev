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

test("initial load serves the HTML shell and hydrates the dashboard", async ({ page }) => {
  await page.goto("/dashboard");

  // Server-rendered container the React app mounts into.
  await expect(page.locator("#fastplace")).toHaveCount(1);

  // React mounted and rendered the controller-supplied props.
  await expect(page.getByRole("heading", { name: "Fastplace" })).toBeVisible();
  await expect(page.getByText("No projects yet.")).toBeVisible();
});

test("bridge request answers with a JSON page payload", async ({ request }) => {
  const res = await request.get("/dashboard", {
    headers: { "X-Fastplace-Request": "true", Accept: "application/json" },
  });
  expect(res.ok()).toBeTruthy();
  expect(res.headers()["content-type"]).toContain("application/json");

  const payload = await res.json();
  expect(payload.component).toBe("Dashboard/Index");
  expect(payload.props.appName).toBe("Fastplace");
  expect(payload.url).toBe("/dashboard");
  expect(typeof payload.version).toBe("string");
});

test("Link click performs a bridge navigation without a full page load", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "Fastplace" })).toBeVisible();

  // The SPA router should own this navigation — no document reload.
  const shell = page.locator("#fastplace");
  await page.getByRole("link", { name: "About" }).click();

  await expect(page).toHaveURL("/about");
  await expect(page.getByRole("heading", { name: "About/Index" })).toBeVisible();
  // Same container element the initial shell mounted — no full reload.
  await expect(shell).toHaveCount(1);

  // Back-button (popstate) restores the previous page payload.
  await page.goBack();
  await expect(page).toHaveURL("/dashboard");
  await expect(page.getByRole("heading", { name: "Fastplace" })).toBeVisible();
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
  await page.goto("/settings/appearance");

  // The settings section nav links the ported pages — both must resolve.
  await page.getByRole("link", { name: "Profile" }).click();
  await expect(page).toHaveURL("/settings/profile");
  await expect(page.getByRole("heading", { name: "Profile settings" })).toBeVisible();

  await page.getByRole("link", { name: "Security" }).click();
  await expect(page).toHaveURL("/settings/security");
  await expect(page.getByRole("heading", { name: "Security settings" })).toBeVisible();
});

test("unified API health endpoint answers ok", async ({ request }) => {
  const res = await request.get("/api/v1/health");
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  expect(body.status).toBe("ok");
  expect(body.framework).toBe("fastplace");
});
