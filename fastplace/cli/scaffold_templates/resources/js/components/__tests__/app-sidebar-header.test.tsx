import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";

import { AppSidebarHeader } from "../app-sidebar-header";
import { SidebarProvider } from "@/components/ui/sidebar";

// jsdom ships no matchMedia — a desktop stand-in, same pattern as the sidebar
// ui tests, so SidebarProvider's useIsMobile hook resolves.
beforeEach(() => {
  const mediaQuery = {
    matches: false,
    media: "(max-width: 767px)",
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => true,
  } as MediaQueryList;
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => mediaQuery),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderHeader(breadcrumbs?: { title: string; href: string }[]) {
  return render(
    <SidebarProvider>
      <AppSidebarHeader breadcrumbs={breadcrumbs} />
    </SidebarProvider>,
  );
}

describe("AppSidebarHeader", () => {
  it("renders the sidebar toggle and breadcrumb trail inside a header bar", () => {
    renderHeader([
      { title: "Dashboard", href: "/dashboard" },
      { title: "Profile", href: "/settings/profile" },
    ]);

    const toggle = screen.getByRole("button", { name: "Toggle sidebar" });
    expect(toggle).toHaveAttribute("data-slot", "sidebar-trigger");

    const header = toggle.closest("header");
    expect(header).not.toBeNull();
    expect(header).toHaveClass("h-16", "border-b");
    expect(header).toContainElement(screen.getByRole("navigation", { name: "breadcrumb" }));
  });

  it("links non-final crumbs and marks the final crumb as the current page", () => {
    renderHeader([
      { title: "Dashboard", href: "/dashboard" },
      { title: "Profile", href: "/settings/profile" },
    ]);

    const nav = screen.getByRole("navigation", { name: "breadcrumb" });
    const dashboard = within(nav).getByRole("link", { name: "Dashboard" });
    expect(dashboard).toHaveAttribute("href", "/dashboard");

    const current = within(nav).getByText("Profile");
    expect(current).toHaveAttribute("aria-current", "page");
  });

  it("defaults to no breadcrumbs and renders only the toggle", () => {
    renderHeader();

    expect(screen.getByRole("button", { name: "Toggle sidebar" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });
});
