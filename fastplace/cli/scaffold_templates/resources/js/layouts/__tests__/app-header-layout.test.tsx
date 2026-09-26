import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router } from "@fastplace/react";

import AppHeaderLayout from "../app/app-header-layout";
import type { BreadcrumbItem } from "@/types";

// The real AppHeader is a heavy leaf ported separately — stub it so this test
// pins the layout's composition contract, not the header's internals.
vi.mock("@/components/app-header", () => ({
  AppHeader: ({ breadcrumbs }: { breadcrumbs?: BreadcrumbItem[] }) => (
    <div data-testid="app-header" data-breadcrumbs={JSON.stringify(breadcrumbs ?? null)} />
  ),
}));

afterEach(() => {
  cleanup();
  router.reset();
});

const breadcrumbs: BreadcrumbItem[] = [
  { title: "Dashboard", href: "/dashboard" },
  { title: "Profile", href: "/settings/profile" },
];

function renderLayout(props: { breadcrumbs?: BreadcrumbItem[] } = {}) {
  return render(
    <FastplaceProvider initialPage={{ component: "Dashboard/Index", props: {}, url: "/dashboard" }}>
      <AppHeaderLayout {...props}>
        <p>page body</p>
      </AppHeaderLayout>
    </FastplaceProvider>,
  );
}

describe("AppHeaderLayout", () => {
  it("renders AppHeader above a centered AppContent inside the header shell", () => {
    renderLayout({ breadcrumbs });

    const header = screen.getByTestId("app-header");
    const main = screen.getByRole("main");

    // Header variant of AppShell: a single full-height column...
    const column = header.parentElement;
    expect(column?.tagName).toBe("DIV");
    expect(column?.className).toContain("min-h-screen");
    expect(column?.className).toContain("flex-col");

    // ...with the header first and the content main directly after it.
    expect(header.nextElementSibling).toBe(main);
    expect(main.className).toContain("max-w-7xl");
    expect(main).toContainElement(screen.getByText("page body"));
  });

  it("forwards breadcrumbs to the header", () => {
    renderLayout({ breadcrumbs });

    expect(screen.getByTestId("app-header")).toHaveAttribute(
      "data-breadcrumbs",
      JSON.stringify(breadcrumbs),
    );
  });

  it("leaves breadcrumbs undefined when none are supplied", () => {
    renderLayout();

    expect(screen.getByTestId("app-header")).toHaveAttribute("data-breadcrumbs", "null");
  });
});
