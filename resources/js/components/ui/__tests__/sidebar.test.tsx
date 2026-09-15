import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "../sidebar";

type MediaQueryListener = (event: MediaQueryListEvent) => void;

// jsdom ships no matchMedia — a controllable stand-in drives the viewport
// for the isMobile branches (same pattern as use-appearance.test). The stub
// always hands back this one instance so the use-mobile hook's memoized
// query keeps pointing at it while tests flip `mobileViewport`.
const mediaListeners = new Set<MediaQueryListener>();
let mobileViewport = false;
const mediaQuery = {
  get matches() {
    return mobileViewport;
  },
  media: "(max-width: 767px)",
  onchange: null,
  addEventListener: (_: string, listener: MediaQueryListener) => mediaListeners.add(listener),
  removeEventListener: (_: string, listener: MediaQueryListener) => mediaListeners.delete(listener),
  addListener: (listener: MediaQueryListener) => mediaListeners.add(listener),
  removeListener: (listener: MediaQueryListener) => mediaListeners.delete(listener),
  dispatchEvent: () => true,
} as MediaQueryList;

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => mediaQuery),
  );
  mobileViewport = false;
  mediaListeners.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  // Clear the persisted sidebar state cookie between tests.
  document.cookie = "sidebar_state=; path=/; max-age=0";
});

// Surfaces the live useSidebar payload so assertions can read provider state.
function SidebarProbe() {
  const { state, open, openMobile, isMobile } = useSidebar();
  return (
    <div data-testid="sidebar-probe">{JSON.stringify({ state, open, openMobile, isMobile })}</div>
  );
}

const readProbe = () =>
  JSON.parse(screen.getByTestId("sidebar-probe").textContent ?? "{}") as {
    state: string;
    open: boolean;
    openMobile: boolean;
    isMobile: boolean;
  };

describe("SidebarProvider", () => {
  it("renders children inside the wrapper and exposes expanded state", () => {
    render(
      <SidebarProvider>
        <SidebarProbe />
        <SidebarTrigger />
      </SidebarProvider>,
    );

    expect(document.querySelector("[data-slot='sidebar-wrapper']")).not.toBeNull();
    expect(readProbe()).toEqual({
      state: "expanded",
      open: true,
      openMobile: false,
      isMobile: false,
    });
  });

  it("gives SidebarTrigger an accessible name, toggles open, and persists the cookie", async () => {
    const user = userEvent.setup();
    render(
      <SidebarProvider>
        <SidebarProbe />
        <SidebarTrigger />
      </SidebarProvider>,
    );

    const trigger = screen.getByRole("button", { name: "Toggle sidebar" });
    await user.click(trigger);
    expect(readProbe().open).toBe(false);
    expect(readProbe().state).toBe("collapsed");
    expect(document.cookie).toContain("sidebar_state=false");

    await user.click(trigger);
    expect(readProbe().open).toBe(true);
    expect(document.cookie).toContain("sidebar_state=true");
  });

  it("toggles the sidebar on ctrl/cmd+b", () => {
    render(
      <SidebarProvider>
        <SidebarProbe />
      </SidebarProvider>,
    );

    fireEvent.keyDown(window, { key: "b", ctrlKey: true });
    expect(readProbe().open).toBe(false);

    fireEvent.keyDown(window, { key: "b", metaKey: true });
    expect(readProbe().open).toBe(true);
  });
});

describe("Sidebar", () => {
  it("renders the desktop container with state, side, and variant attributes", () => {
    render(
      <SidebarProvider>
        <Sidebar>
          <SidebarHeader>Brand</SidebarHeader>
        </Sidebar>
      </SidebarProvider>,
    );

    const sidebar = document.querySelector("[data-slot='sidebar']");
    expect(sidebar).not.toBeNull();
    expect(sidebar).toHaveAttribute("data-state", "expanded");
    expect(sidebar).toHaveAttribute("data-side", "left");
    expect(sidebar).toHaveAttribute("data-variant", "sidebar");
    // Expanded sidebars report an empty collapsible mode.
    expect(sidebar).toHaveAttribute("data-collapsible", "");
    expect(document.querySelector("[data-sidebar='sidebar']")).not.toBeNull();
    expect(document.querySelector("[data-slot='sidebar-header']")).not.toBeNull();
  });

  it("marks collapsed icon mode, side, and variant on the container", () => {
    render(
      <SidebarProvider defaultOpen={false}>
        <Sidebar side="right" variant="floating" collapsible="icon" />
      </SidebarProvider>,
    );

    const sidebar = document.querySelector("[data-slot='sidebar']");
    expect(sidebar).toHaveAttribute("data-state", "collapsed");
    expect(sidebar).toHaveAttribute("data-collapsible", "icon");
    expect(sidebar).toHaveAttribute("data-side", "right");
    expect(sidebar).toHaveAttribute("data-variant", "floating");
  });

  it("renders a static sidebar for collapsible='none'", () => {
    render(
      <SidebarProvider>
        <Sidebar collapsible="none">
          <SidebarHeader>Static</SidebarHeader>
        </Sidebar>
      </SidebarProvider>,
    );

    const sidebar = document.querySelector("[data-slot='sidebar']");
    expect(sidebar).not.toBeNull();
    expect(sidebar!).not.toHaveAttribute("data-state");
    expect(sidebar!.className).toContain("bg-sidebar");
    expect(sidebar!.textContent).toBe("Static");
  });

  it("renders the mobile sidebar through the sheet dialog", async () => {
    const user = userEvent.setup();
    mobileViewport = true;
    render(
      <SidebarProvider>
        <Sidebar>
          <SidebarHeader>Mobile brand</SidebarHeader>
        </Sidebar>
        <SidebarTrigger />
      </SidebarProvider>,
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Toggle sidebar" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("data-mobile", "true");
    expect(dialog).toHaveAttribute("data-slot", "sidebar");
    expect(dialog).toHaveAttribute("data-sidebar", "sidebar");
    expect(screen.getByText("Mobile brand")).toBeInTheDocument();
  });
});

describe("SidebarMenuButton and slots", () => {
  it("renders a button by default and a link via asChild", () => {
    render(
      <SidebarProvider>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton isActive size="lg">
              Projects
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton asChild>
              <a href="/about">About</a>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarProvider>,
    );

    const button = screen.getByRole("button", { name: "Projects" });
    expect(button).toHaveAttribute("data-slot", "sidebar-menu-button");
    expect(button).toHaveAttribute("data-active", "true");
    expect(button).toHaveAttribute("data-size", "lg");

    const link = screen.getByRole("link", { name: "About" });
    expect(link).toHaveAttribute("data-slot", "sidebar-menu-button");
  });

  it("composes group, menu, and sub-menu slots", () => {
    render(
      <SidebarProvider>
        <Sidebar collapsible="none">
          <SidebarContent>
            <SidebarGroup>
              <SidebarGroupLabel>Platform</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  <SidebarMenuItem>
                    <SidebarMenuButton>Home</SidebarMenuButton>
                    <SidebarMenuSub>
                      <SidebarMenuSubItem>
                        <SidebarMenuSubButton href="/settings">Settings</SidebarMenuSubButton>
                      </SidebarMenuSubItem>
                    </SidebarMenuSub>
                  </SidebarMenuItem>
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          </SidebarContent>
          <SidebarFooter>Footer</SidebarFooter>
        </Sidebar>
      </SidebarProvider>,
    );

    const slots = [
      "sidebar-content",
      "sidebar-group",
      "sidebar-group-label",
      "sidebar-group-content",
      "sidebar-menu",
      "sidebar-menu-item",
      "sidebar-menu-sub",
      "sidebar-menu-sub-item",
      "sidebar-menu-sub-button",
      "sidebar-footer",
    ];
    for (const slot of slots) {
      expect(document.querySelector(`[data-slot='${slot}']`), slot).not.toBeNull();
    }
  });
});
