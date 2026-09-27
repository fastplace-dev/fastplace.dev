import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// W5 accessibility verification against the real hydrated app:
//  - axe-core WCAG 2.1 AA scans on the shell pages, in BOTH themes
//  - the skip-to-content keyboard journey
//  - page landmarks / headings that the audit flagged (a11y1-G4)

type Theme = "light" | "dark";

/** Pin a theme before any app code runs, the way the Appearance page does. */
async function withTheme(page: Page, theme: Theme) {
  await page.addInitScript(
    (mode) => localStorage.setItem("fastplace-appearance", mode),
    theme,
  );
}

/** Scan the current page and fail with the violation list if any exist. */
async function expectAxeClean(page: Page, context: string) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa"])
    .analyze();
  const summary = results.violations.map((v) => ({
    id: v.id,
    impact: v.impact,
    nodes: v.nodes.slice(0, 3).map((n) => n.target.join(" ")),
  }));
  expect(summary, `${context}: axe violations`).toEqual([]);
}

for (const theme of ["light", "dark"] as const) {
  test(`axe: /login is WCAG 2.1 AA clean (${theme})`, async ({ page }) => {
    await withTheme(page, theme);
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Log in to your account" })).toBeVisible();
    await expectAxeClean(page, `/login ${theme}`);
  });

  test(`axe: /register is WCAG 2.1 AA clean (${theme})`, async ({ page }) => {
    await withTheme(page, theme);
    await page.goto("/register");
    await expect(page.getByRole("heading", { name: "Create an account" })).toBeVisible();
    await expectAxeClean(page, `/register ${theme}`);
  });
}

test("axe: /dashboard is WCAG 2.1 AA clean (light, logged in)", async ({ page }) => {
  await withTheme(page, "light");
  await page.goto("/register");
  await page.getByLabel("Name").fill("E2E A11y");
  await page.getByLabel("Email address").fill("e2e-a11y@example.test");
  await page.getByLabel("Password", { exact: true }).fill("secret123");
  await page.getByLabel("Confirm password").fill("secret123");
  await page.getByRole("button", { name: "Create account" }).click();

  // First signup lands on the dashboard, whose h1 carries the app name.
  const h1 = page.locator("main#main-content h1");
  await expect(h1).toBeVisible();
  await expectAxeClean(page, "/dashboard light");
});

test("skip-to-content: keyboard journey moves focus into the main landmark", async ({
  page,
}) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Log in to your account" })).toBeVisible();

  // Autofocus puts focus on the email input at load and Chromium anchors
  // sequential navigation there, so there is no clean "Tab from the top" in
  // a driven browser. Walk the real user's path instead: tab forward off the
  // LAST focusable element (the Sign up link) — focus wraps to the start of
  // the page, and the element it lands on must be the skip link. Positive
  // tabIndex on the form fields would have hoisted them ahead of it.
  const signUp = page.getByRole("link", { name: "Sign up" });
  await signUp.focus();
  const skip = page.getByRole("link", { name: "Skip to content" });
  // First Tab steps off the page (focus lands on the body); the second
  // re-enters at the FIRST focusable element.
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();

  // Activating it moves focus (not just scroll) into #main-content, which
  // carries tabindex="-1" for exactly this.
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
});

test("login validation errors are announced and associated with the field", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByLabel("Email address").fill("a11y@example.test");
  // exact: the password visibility toggle ("Show password") also matches a
  // substring query and would make this locator ambiguous.
  await page.getByLabel("Password", { exact: true }).fill("secret123");
  await page.getByRole("button", { name: "Log in" }).click();

  const alert = page.getByRole("alert");
  await expect(alert).toHaveAttribute("id", "email-error");
  const email = page.getByLabel("Email address");
  await expect(email).toHaveAttribute("aria-describedby", "email-error");
  await expect(email).toHaveAttribute("aria-invalid", "true");
});
