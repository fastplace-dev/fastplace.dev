import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router } from "@fastplace/react";

import AppLayout from "../app-layout";
import type { BreadcrumbItem, SharedData } from "@/types";

// The real sidebar leaves are heavy Radix components ported separately —
// stub them so this test exercises the real AppLayout -> AppSidebarLayout
// -> AppShell/AppContent chain without their internals.
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

function renderLayout(breadcrumbs?: BreadcrumbItem[]) {
  return render(
    <FastplaceProvider
      initialPage={{
        component: "Dashboard/Index",
        props: { sidebarOpen: true } as SharedData,
        url: "/dashboard",
      }}
    >
      <AppLayout breadcrumbs={breadcrumbs}>
        <p>page body</p>
      </AppLayout>
    </FastplaceProvider>,
  );
}

describe("AppLayout", () => {
  it("defaults to the sidebar variant shell with children in the inset", () => {
    renderLayout();

    expect(document.querySelector("[data-slot='sidebar-wrapper']")).not.toBeNull();
    expect(screen.getByTestId("app-sidebar")).toBeInTheDocument();

    const main = document.querySelector("main[data-slot='sidebar-inset']");
    expect(main).toContainElement(screen.getByText("page body"));
  });

  it("passes breadcrumbs through to the sidebar header", () => {
    const breadcrumbs: BreadcrumbItem[] = [{ title: "Dashboard", href: "/dashboard" }];
    renderLayout(breadcrumbs);

    expect(screen.getByTestId("app-sidebar-header")).toHaveAttribute(
      "data-breadcrumbs",
      JSON.stringify(breadcrumbs),
    );
  });

  it("defaults breadcrumbs to an empty list", () => {
    renderLayout();

    expect(screen.getByTestId("app-sidebar-header")).toHaveAttribute("data-breadcrumbs", "[]");
  });
});
