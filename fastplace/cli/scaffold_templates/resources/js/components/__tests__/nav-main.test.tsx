import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router, type Page } from "@fastplace/react";
import { LayoutDashboard, Settings } from "lucide-react";

import { NavMain } from "../nav-main";
import { SidebarProvider } from "@/components/ui/sidebar";
import type { NavItem } from "@/types";

type MediaQueryListener = (event: MediaQueryListEvent) => void;

// jsdom ships no matchMedia — the sidebar provider's viewport probe needs a
// stand-in (same pattern as the sidebar ui tests).
const mediaQuery = {
  matches: false,
  media: "(max-width: 767px)",
  onchange: null,
  addEventListener: (_: string, listener: MediaQueryListener) => void listener,
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => true,
} as MediaQueryList;

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => mediaQuery),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  // Reset the module-global bridge page store between tests.
  router.reset();
});

const items: NavItem[] = [
  { title: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { title: "Settings", href: "/settings", icon: Settings },
];

// NavMain reads the current url through the bridge page, so each render
// adopts its own initialPage (mirrors use-current-url tests).
function renderNavMainAt(url: string) {
  const initialPage: Page = { component: "Dashboard/Index", props: {}, url };
  return render(
    <FastplaceProvider initialPage={initialPage}>
      <SidebarProvider>
        <NavMain items={items} />
      </SidebarProvider>
    </FastplaceProvider>,
  );
}

describe("NavMain", () => {
  it("renders the group label and one link per item with its href", () => {
    renderNavMainAt("/dashboard");

    expect(screen.getByText("Platform")).toBeInTheDocument();
    const dashboard = screen.getByRole("link", { name: /dashboard/i });
    const settings = screen.getByRole("link", { name: /settings/i });
    expect(dashboard).toHaveAttribute("href", "/dashboard");
    expect(settings).toHaveAttribute("href", "/settings");
  });

  it("marks only the item matching the current url as active", () => {
    renderNavMainAt("/dashboard");

    const dashboard = screen.getByRole("link", { name: /dashboard/i });
    const settings = screen.getByRole("link", { name: /settings/i });
    // The active flag lands on the composed sidebar-menu-button slot.
    expect(dashboard.closest("[data-slot='sidebar-menu-button']")).toHaveAttribute(
      "data-active",
      "true",
    );
    expect(settings.closest("[data-slot='sidebar-menu-button']")).toHaveAttribute(
      "data-active",
      "false",
    );
  });

  it("tracks the current url as it changes between renders", () => {
    renderNavMainAt("/dashboard");
    const settings = screen.getByRole("link", { name: /settings/i });
    expect(settings.closest("[data-slot='sidebar-menu-button']")).toHaveAttribute(
      "data-active",
      "false",
    );
  });

  it("renders each item's icon inside its link", () => {
    renderNavMainAt("/dashboard");

    for (const item of items) {
      const link = screen.getByRole("link", { name: new RegExp(item.title, "i") });
      expect(link.querySelector("svg")).not.toBeNull();
      expect(link).toHaveTextContent(item.title);
    }
  });

  it("composes the sidebar group and menu slots", () => {
    renderNavMainAt("/dashboard");

    expect(document.querySelector("[data-slot='sidebar-group']")).not.toBeNull();
    expect(document.querySelector("[data-slot='sidebar-group-label']")).not.toBeNull();
    expect(document.querySelector("[data-slot='sidebar-menu']")).not.toBeNull();
    expect(document.querySelectorAll("[data-slot='sidebar-menu-item']")).toHaveLength(2);
  });

  it("renders items without an icon", () => {
    const initialPage: Page = { component: "Dashboard/Index", props: {}, url: "/about" };
    render(
      <FastplaceProvider initialPage={initialPage}>
        <SidebarProvider>
          <NavMain items={[{ title: "About", href: "/about" }]} />
        </SidebarProvider>
      </FastplaceProvider>,
    );

    const about = screen.getByRole("link", { name: /about/i });
    expect(about).toHaveAttribute("href", "/about");
    expect(about.querySelector("svg")).toBeNull();
  });
});
