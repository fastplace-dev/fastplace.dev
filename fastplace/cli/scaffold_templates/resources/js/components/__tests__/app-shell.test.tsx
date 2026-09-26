import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router } from "@fastplace/react";

import { AppShell } from "../app-shell";
import { useSidebar } from "@/components/ui/sidebar";
import type { SharedData } from "@/types";

// jsdom ships no matchMedia — a desktop (non-mobile) stand-in, same pattern
// as the sidebar tests, so SidebarProvider's useIsMobile hook resolves.
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

// Surfaces the live useSidebar payload so assertions can read provider state.
function SidebarProbe() {
  const { state, open, isMobile } = useSidebar();
  return <div data-testid="sidebar-probe">{JSON.stringify({ state, open, isMobile })}</div>;
}

const readProbe = () =>
  JSON.parse(screen.getByTestId("sidebar-probe").textContent ?? "{}") as {
    state: string;
    open: boolean;
    isMobile: boolean;
  };

function renderShell(sidebarOpen: boolean) {
  return render(
    <FastplaceProvider
      initialPage={{
        component: "Dashboard/Index",
        props: { sidebarOpen } as SharedData,
        url: "/dashboard",
      }}
    >
      <AppShell>
        <SidebarProbe />
        <p>shell content</p>
      </AppShell>
    </FastplaceProvider>,
  );
}

describe("AppShell", () => {
  it("wraps children in a SidebarProvider for the sidebar variant (default)", () => {
    renderShell(true);

    expect(document.querySelector("[data-slot='sidebar-wrapper']")).not.toBeNull();
    expect(screen.getByText("shell content")).toBeInTheDocument();
  });

  it("seeds the sidebar open state from the shared sidebarOpen prop", () => {
    renderShell(true);
    expect(readProbe()).toEqual({ state: "expanded", open: true, isMobile: false });

    cleanup();
    router.reset();

    renderShell(false);
    expect(readProbe()).toEqual({ state: "collapsed", open: false, isMobile: false });
  });

  it("renders a plain full-height column for the header variant", () => {
    render(
      <FastplaceProvider
        initialPage={{ component: "Dashboard/Index", props: {}, url: "/dashboard" }}
      >
        <AppShell variant="header">
          <p>header content</p>
        </AppShell>
      </FastplaceProvider>,
    );

    expect(document.querySelector("[data-slot='sidebar-wrapper']")).toBeNull();
    expect(screen.queryByTestId("sidebar-probe")).toBeNull();

    const column = screen.getByText("header content").parentElement;
    expect(column?.tagName).toBe("DIV");
    expect(column?.className).toContain("min-h-screen");
    expect(column?.className).toContain("flex-col");
  });
});
