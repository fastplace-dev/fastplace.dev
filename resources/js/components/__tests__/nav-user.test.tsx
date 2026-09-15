import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router, type Page } from "@fastplace/react";

import { NavUser } from "../nav-user";
import { SidebarProvider } from "@/components/ui/sidebar";
import type { SharedData, User } from "@/types";

type MediaQueryListener = (event: MediaQueryListEvent) => void;

// jsdom ships no matchMedia — a controllable stand-in drives the viewport for
// the isMobile branches (same pattern as the sidebar ui tests). The stub
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

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// jsdom ships no ResizeObserver (the popper positioning needs one).
globalThis.ResizeObserver = globalThis.ResizeObserver ?? ResizeObserverStub;

// jsdom ships no PointerEvent, so synthesized pointerdown events would lose
// their button state and the trigger's left-click guard would never pass.
class PointerEventStub extends MouseEvent {
  pointerId: number;
  pointerType: string;
  isPrimary: boolean;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
    this.pointerType = init.pointerType ?? "";
    this.isPrimary = init.isPrimary ?? false;
  }
}
window.PointerEvent = window.PointerEvent ?? PointerEventStub;

// jsdom's selector engine re-enters Element.matches with identical arguments
// while a positioned overlay is open, which stalls the event loop. Memoizing
// per (element, selector) pair keeps matching results identical but fast.
const matchesMemo = new WeakMap<Element, Map<string, boolean>>();
const originalMatches = Element.prototype.matches;
Element.prototype.matches = function (this: Element, selector: string) {
  let perElement = matchesMemo.get(this);
  if (!perElement) {
    perElement = new Map<string, boolean>();
    matchesMemo.set(this, perElement);
  }
  if (perElement.has(selector)) return perElement.get(selector) as boolean;
  const result = originalMatches.call(this, selector);
  perElement.set(selector, result);
  return result;
};

const user: User = {
  id: 1,
  name: "Firoz Anam",
  email: "firoz@example.com",
  avatar: undefined,
  email_verified_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

// NavUser reads the signed-in user from the bridge page props, so each render
// adopts its own initialPage (mirrors the nav-main tests).
function renderNavUser({
  authUser = user,
  includeAuth = true,
  mobile = false,
  sidebarOpen = true,
}: {
  authUser?: User | null;
  /** False simulates the bridge before an auth backend shares any auth prop. */
  includeAuth?: boolean;
  mobile?: boolean;
  sidebarOpen?: boolean;
} = {}) {
  mobileViewport = mobile;
  const initialPage: Page = {
    component: "Dashboard/Index",
    props: (includeAuth ? { auth: { user: authUser } } : {}) as unknown as SharedData,
    url: "/dashboard",
  };
  return render(
    <FastplaceProvider initialPage={initialPage}>
      <SidebarProvider defaultOpen={sidebarOpen}>
        <NavUser />
      </SidebarProvider>
    </FastplaceProvider>,
  );
}

async function openMenu() {
  const userEvent_ = userEvent.setup();
  const trigger = screen.getByRole("button", { name: /firoz anam/i });
  await userEvent_.click(trigger);
  return screen.findByRole("menu");
}

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
  router.reset();
  document.body.style.removeProperty("pointer-events");
});

describe("NavUser", () => {
  it("renders the signed-in user inside the sidebar menu trigger", () => {
    renderNavUser();

    const trigger = screen.getByRole("button", { name: /firoz anam/i });
    // Radix's asChild trigger composes onto the sidebar menu button, so the
    // button keeps the menu-button sizing while acting as the dropdown trigger.
    expect(trigger).toHaveAttribute("data-size", "lg");
    expect(trigger).toHaveClass("text-sidebar-accent-foreground");
    // The avatar fallback carries the user's initials.
    expect(screen.getByText("FA")).toBeInTheDocument();
    expect(trigger.querySelector("svg")).not.toBeNull();
  });

  it("falls back to a guest control when the bridge shares no auth prop", () => {
    renderNavUser({ includeAuth: false });

    // Pre-auth backend every viewer is a guest, but the chrome keeps the
    // profile control the reference app shows, under a placeholder account.
    expect(screen.getByRole("button", { name: /guest/i })).toBeInTheDocument();
    expect(screen.getByText("G")).toBeInTheDocument();
  });

  it("falls back to a guest control when no user is authenticated", () => {
    renderNavUser({ authUser: null });

    // The provider wrapper stays mounted and still carries the user control.
    const wrapper = document.querySelector("[data-slot='sidebar-wrapper']");
    expect(wrapper).not.toBeNull();
    expect(wrapper).not.toBeEmptyDOMElement();
    expect(screen.getByRole("button", { name: /guest/i })).toBeInTheDocument();
  });

  it("opens the user menu with the profile label and account actions on click", async () => {
    renderNavUser();

    const menu = await openMenu();

    // The menu labels the signed-in user with name and email.
    expect(menu).toHaveTextContent("Firoz Anam");
    expect(menu).toHaveTextContent("firoz@example.com");
    expect(screen.getByRole("menuitem", { name: /settings/i })).toHaveAttribute(
      "href",
      "/settings/profile",
    );
    expect(screen.getByRole("menuitem", { name: /log out/i })).toHaveAttribute("href", "/logout");
  });

  it("opens the guest menu with the same account actions", async () => {
    renderNavUser({ includeAuth: false });

    const userEvent_ = userEvent.setup();
    await userEvent_.click(screen.getByRole("button", { name: /guest/i }));
    const menu = await screen.findByRole("menu");

    // The placeholder account gets the identical menu structure as a
    // signed-in user — only the identity label differs.
    expect(menu).toHaveTextContent("Guest");
    expect(screen.getByRole("menuitem", { name: /settings/i })).toHaveAttribute(
      "href",
      "/settings/profile",
    );
    expect(screen.getByRole("menuitem", { name: /log out/i })).toHaveAttribute("href", "/logout");
  });

  it("places the menu below the trigger on an expanded desktop sidebar", async () => {
    renderNavUser({ mobile: false, sidebarOpen: true });

    const menu = await openMenu();
    await waitFor(() => expect(menu).toHaveAttribute("data-side", "bottom"));
  });

  it("places the menu to the left when the desktop sidebar is collapsed", async () => {
    renderNavUser({ mobile: false, sidebarOpen: false });

    const menu = await openMenu();
    await waitFor(() => expect(menu).toHaveAttribute("data-side", "left"));
  });

  it("places the menu below the trigger on mobile", async () => {
    renderNavUser({ mobile: true });

    const menu = await openMenu();
    await waitFor(() => expect(menu).toHaveAttribute("data-side", "bottom"));
  });
});
