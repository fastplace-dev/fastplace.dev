import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";
import { FastplaceProvider, router, type Page } from "@fastplace/react";

import SettingsLayout from "../settings/layout";

// The layout reads the current url through the bridge page, so each render
// adopts its own initialPage (same pattern as the nav-main tests).
function renderSettingsAt(url: string) {
  const initialPage: Page = { component: "Settings/Profile/Edit", props: {}, url };
  return render(
    <FastplaceProvider initialPage={initialPage}>
      <SettingsLayout>
        <p>settings-body</p>
      </SettingsLayout>
    </FastplaceProvider>,
  );
}

const navItems = [
  { title: "Profile", href: "/settings/profile" },
  { title: "Security", href: "/settings/security" },
  { title: "Appearance", href: "/settings/appearance" },
];

afterEach(() => {
  cleanup();
  // Reset the module-global bridge page store between tests.
  router.reset();
});

describe("SettingsLayout", () => {
  it("renders the settings heading with its description", () => {
    renderSettingsAt("/settings/profile");

    expect(screen.getByRole("heading", { level: 2, name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("Manage your profile and account settings")).toBeInTheDocument();
  });

  it("renders the Settings nav with one link per section in order", () => {
    renderSettingsAt("/settings/profile");

    const nav = screen.getByRole("navigation", { name: "Settings" });
    const links = [...nav.querySelectorAll("a")];
    expect(links.map((link) => link.getAttribute("href"))).toEqual(
      navItems.map((item) => item.href),
    );
    for (const item of navItems) {
      expect(screen.getByRole("link", { name: new RegExp(item.title, "i") })).toHaveAttribute(
        "href",
        item.href,
      );
    }
  });

  it("marks only the section matching the current url as active", () => {
    renderSettingsAt("/settings/security");

    const profile = screen.getByRole("link", { name: /profile/i });
    const security = screen.getByRole("link", { name: /security/i });
    const appearance = screen.getByRole("link", { name: /appearance/i });

    expect(security.className).toContain("bg-muted");
    expect(profile.className).not.toContain("bg-muted");
    expect(appearance.className).not.toContain("bg-muted");
  });

  it("marks a section active when the current url is nested below it", () => {
    renderSettingsAt("/settings/security/sessions");

    expect(screen.getByRole("link", { name: /security/i }).className).toContain("bg-muted");
  });

  it("renders children as the settings content", () => {
    renderSettingsAt("/settings/profile");

    expect(screen.getByText("settings-body")).toBeInTheDocument();
  });

  it("separates the nav from the content area", () => {
    renderSettingsAt("/settings/profile");

    expect(document.querySelector("[data-slot='separator-root']")).not.toBeNull();
  });
});
