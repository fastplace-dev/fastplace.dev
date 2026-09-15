import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router, type Page } from "@fastplace/react";

import { AppSidebar } from "../app-sidebar";
import { SidebarProvider } from "@/components/ui/sidebar";
import type { SharedData, User } from "@/types";

const user: User = {
  id: 1,
  name: "Firoz Anam",
  email: "firoz@example.com",
  avatar: undefined,
  email_verified_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

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
  router.reset();
});

// AppSidebar reads the app name and signed-in user through the bridge page.
function renderSidebar(authUser: User | null = user, sidebarOpen = true) {
  const initialPage: Page = {
    component: "Dashboard/Index",
    props: {
      name: "Fastplace",
      sidebarOpen: true,
      auth: { user: authUser },
    } as unknown as SharedData,
    url: "/dashboard",
  };
  return render(
    <FastplaceProvider initialPage={initialPage}>
      <SidebarProvider defaultOpen={sidebarOpen}>
        <AppSidebar />
      </SidebarProvider>
    </FastplaceProvider>,
  );
}

describe("AppSidebar", () => {
  it("renders the sidebar in icon-collapsible inset mode", () => {
    // Collapsed exposes the icon collapsible mode on the container (the ui
    // primitive reports an empty mode while expanded).
    renderSidebar(user, false);

    const sidebar = document.querySelector("[data-slot='sidebar']");
    expect(sidebar).not.toBeNull();
    expect(sidebar).toHaveAttribute("data-collapsible", "icon");
    expect(sidebar).toHaveAttribute("data-variant", "inset");
    expect(sidebar).toHaveAttribute("data-state", "collapsed");
  });

  it("separates from the content with a right divider matching the header border", () => {
    // The inset variant ships no edge of its own; the app chrome draws the
    // same hairline the inset header uses underneath itself.
    renderSidebar();

    const fixed = document.querySelector("[data-slot='sidebar'] > div:last-child");
    expect(fixed).toHaveClass("border-r");
    expect(fixed).toHaveClass("border-sidebar-border/50");
  });

  it("links the brand header to the dashboard", () => {
    renderSidebar();

    const logo = screen.getByRole("link", { name: /fastplace/i });
    expect(logo).toHaveAttribute("href", "/dashboard");
    expect(logo).toHaveAttribute("data-fastplace-link");
    // The brand link is a large sidebar menu button inside the header slot.
    expect(logo.closest("[data-slot='sidebar-menu-button']")).not.toBeNull();
    expect(logo.closest("[data-slot='sidebar-header']")).not.toBeNull();
  });

  it("renders the main navigation with the Dashboard item", () => {
    renderSidebar();

    expect(screen.getByText("Platform")).toBeInTheDocument();
    const dashboard = screen.getByRole("link", { name: /dashboard/i });
    expect(dashboard).toHaveAttribute("href", "/dashboard");
    expect(dashboard.closest("[data-slot='sidebar-content']")).not.toBeNull();
  });

  it("renders footer links to the repository and docs in a new tab", () => {
    renderSidebar();

    const repository = screen.getByRole("link", { name: /repository/i });
    expect(repository).toHaveAttribute("href", "https://github.com/firozanam/fastplace.dev");
    expect(repository).toHaveAttribute("target", "_blank");
    expect(repository).toHaveAttribute("rel", "noopener noreferrer");

    const documentation = screen.getByRole("link", { name: /documentation/i });
    expect(documentation).toHaveAttribute("href", "https://fastplace.dev/docs");
    expect(documentation).toHaveAttribute("target", "_blank");
    expect(documentation).toHaveAttribute("rel", "noopener noreferrer");

    // Footer links live under the sidebar footer slot, above the user control.
    const footer = document.querySelector("[data-slot='sidebar-footer']");
    expect(footer).toContainElement(repository);
    expect(footer).toContainElement(documentation);
  });

  it("renders the signed-in user control in the footer", () => {
    renderSidebar();

    const footer = document.querySelector("[data-slot='sidebar-footer']");
    const userButton = within(footer as HTMLElement).getByRole("button", {
      name: /firoz anam/i,
    });
    expect(userButton).toBeInTheDocument();
  });

  it("falls back to a guest user control in the footer when nobody is signed in", () => {
    renderSidebar(null);

    const footer = document.querySelector("[data-slot='sidebar-footer']");
    expect(
      within(footer as HTMLElement).getByRole("button", { name: /guest/i }),
    ).toBeInTheDocument();
    // The footer still carries the external links for guests.
    expect(screen.getByRole("link", { name: /repository/i })).toBeInTheDocument();
  });
});
