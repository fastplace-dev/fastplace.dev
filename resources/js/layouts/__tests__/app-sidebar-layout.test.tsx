import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router } from "@fastplace/react";

import AppSidebarLayout from "../app/app-sidebar-layout";
import type { BreadcrumbItem, SharedData } from "@/types";

// The real sidebar leaves are heavy Radix components ported separately —
// stub them so this test pins the layout's composition contract.
vi.mock("@/components/app-sidebar", () => ({
  AppSidebar: () => <div data-testid="app-sidebar" />,
}));
vi.mock("@/components/app-sidebar-header", () => ({
  AppSidebarHeader: ({ breadcrumbs }: { breadcrumbs?: BreadcrumbItem[] }) => (
    <div data-testid="app-sidebar-header" data-breadcrumbs={JSON.stringify(breadcrumbs ?? null)} />
  ),
}));

// jsdom ships no matchMedia — desktop stand-in so SidebarProvider's
// useIsMobile hook resolves (same pattern as the sidebar tests).
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
  router.reset();
  vi.unstubAllGlobals();
});

const breadcrumbs: BreadcrumbItem[] = [{ title: "Profile", href: "/settings/profile" }];

function renderLayout(props: { breadcrumbs?: BreadcrumbItem[] } = {}) {
  return render(
    <FastplaceProvider
      initialPage={{
        component: "Dashboard/Index",
        props: { sidebarOpen: true } as SharedData,
        url: "/dashboard",
      }}
    >
      <AppSidebarLayout {...props}>
        <p>page body</p>
      </AppSidebarLayout>
    </FastplaceProvider>,
  );
}

describe("AppSidebarLayout", () => {
  it("renders the sidebar beside the content inside SidebarProvider", () => {
    renderLayout({ breadcrumbs });

    // Sidebar variant of AppShell wraps everything in SidebarProvider.
    expect(document.querySelector("[data-slot='sidebar-wrapper']")).not.toBeNull();
    expect(screen.getByTestId("app-sidebar")).toBeInTheDocument();
  });

  it("renders the sidebar header above the children inside the inset main", () => {
    renderLayout({ breadcrumbs });

    const main = document.querySelector("main[data-slot='sidebar-inset']");
    expect(main).not.toBeNull();

    const header = screen.getByTestId("app-sidebar-header");
    expect(main).toContainElement(header);
    expect(main).toContainElement(screen.getByText("page body"));
    expect(
      header.compareDocumentPosition(screen.getByText("page body")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("keeps the content column clipped with min-w-0 overflow-x-clip", () => {
    renderLayout({ breadcrumbs });

    const main = document.querySelector("main[data-slot='sidebar-inset']");
    expect(main?.className).toContain("min-w-0");
    expect(main?.className).toContain("overflow-x-clip");
  });

  it("exposes the inset main as the skip-link target (a11y1-G2)", () => {
    renderLayout({ breadcrumbs });

    const main = document.querySelector("main[data-slot='sidebar-inset']");
    expect(main).toHaveAttribute("id", "main-content");
  });

  it("renders the skip link as the first focusable element in the layout", () => {
    renderLayout({ breadcrumbs });

    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip).toHaveAttribute("href", "#main-content");

    const focusables = document.querySelectorAll(
      "a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex='-1'])",
    );
    expect(focusables.length).toBeGreaterThan(0);
    expect(focusables[0]).toBe(skip);
  });

  it("forwards breadcrumbs to the sidebar header", () => {
    renderLayout({ breadcrumbs });

    expect(screen.getByTestId("app-sidebar-header")).toHaveAttribute(
      "data-breadcrumbs",
      JSON.stringify(breadcrumbs),
    );
  });

  it("defaults breadcrumbs to an empty list when none are supplied", () => {
    renderLayout();

    expect(screen.getByTestId("app-sidebar-header")).toHaveAttribute("data-breadcrumbs", "[]");
  });
});
