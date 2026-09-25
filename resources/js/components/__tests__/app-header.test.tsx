import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router } from "@fastplace/react";

import { AppHeader } from "../app-header";
import type { BreadcrumbItem, SharedData, User } from "@/types";

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

const testUser: User = {
  id: 1,
  name: "Firoz Anam",
  email: "firoz@example.com",
  avatar: undefined,
  email_verified_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function renderHeader({
  url = "/",
  breadcrumbs = [],
  includeAuth = true,
}: {
  url?: string;
  breadcrumbs?: BreadcrumbItem[];
  /** False simulates the bridge before an auth backend shares any auth prop. */
  includeAuth?: boolean;
} = {}) {
  return render(
    <FastplaceProvider
      initialPage={{
        component: "Dashboard/Index",
        props: {
          name: "Fastplace",
          ...(includeAuth ? { auth: { user: testUser } } : {}),
          sidebarOpen: true,
        } as SharedData,
        url,
      }}
    >
      <AppHeader breadcrumbs={breadcrumbs} />
    </FastplaceProvider>,
  );
}

afterEach(() => {
  cleanup();
  router.reset();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  document.body.style.removeProperty("pointer-events");
});

describe("AppHeader", () => {
  it("links the logo to the dashboard", () => {
    renderHeader();

    const logo = screen.getByRole("link", { name: /fastplace/i });
    expect(logo).toHaveAttribute("href", "/dashboard");
  });

  it("renders the main navigation link to the dashboard", () => {
    renderHeader();

    const dashboard = screen.getByRole("link", { name: "Dashboard" });
    expect(dashboard).toHaveAttribute("href", "/dashboard");
  });

  it("renders the header chrome without an auth prop (pre-auth backend)", () => {
    renderHeader({ includeAuth: false });

    // Chrome (mobile menu, logo, nav) renders; no user avatar or menu.
    expect(screen.getByRole("link", { name: /fastplace/i })).toHaveAttribute("href", "/dashboard");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("marks the dashboard item active only on the dashboard URL", () => {
    const { unmount } = renderHeader({ url: "/dashboard" });

    const active = screen.getByRole("link", { name: "Dashboard" });
    // Exact whole-class checks — the base trigger style carries prefixed
    // variants like data-[active=true]:bg-accent-surface/50 that would
    // fool a substring match.
    expect(active.classList.contains("bg-accent-surface/50")).toBe(true);
    expect(active.classList.contains("text-accent-surface-foreground")).toBe(true);
    // Active underline indicator is present while on the dashboard.
    expect(active.parentElement?.querySelector(".bg-ink")).not.toBeNull();

    unmount();
    // The bridge router initializes once per provider — reset it so the
    // re-render's initialPage (a different URL) actually takes effect.
    router.reset();
    renderHeader({ url: "/projects" });

    const inactive = screen.getByRole("link", { name: "Dashboard" });
    expect(inactive.classList.contains("bg-accent-surface/50")).toBe(false);
    expect(inactive.classList.contains("text-accent-surface-foreground")).toBe(false);
    expect(inactive.parentElement?.querySelector(".bg-ink")).toBeNull();
  });

  it("opens external reference links in a new tab", () => {
    renderHeader();

    const repository = screen.getByRole("link", { name: "Repository" });
    expect(repository).toHaveAttribute("href", "https://github.com/fastplace-dev/fastplace.dev");
    expect(repository).toHaveAttribute("target", "_blank");
    expect(repository).toHaveAttribute("rel", "noopener noreferrer");

    const documentation = screen.getByRole("link", { name: "Documentation" });
    expect(documentation).toHaveAttribute("href", "https://fastplace.dev/docs");
    expect(documentation).toHaveAttribute("target", "_blank");
    expect(documentation).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("opens the mobile navigation sheet from the menu button", async () => {
    const { container } = renderHeader({ url: "/projects" });
    const userEvent_ = userEvent.setup();

    const menuButton = container.querySelector<HTMLButtonElement>("div.lg\\:hidden button");
    expect(menuButton).not.toBeNull();
    await userEvent_.click(menuButton as HTMLButtonElement);

    const dialog = await screen.findByRole("dialog", { name: /navigation menu/i });
    const dashboard = within(dialog).getByRole("link", { name: "Dashboard" });
    expect(dashboard).toHaveAttribute("href", "/dashboard");

    const repository = within(dialog).getByRole("link", { name: "Repository" });
    expect(repository).toHaveAttribute("target", "_blank");
    expect(within(dialog).getByRole("link", { name: "Documentation" })).toHaveAttribute(
      "href",
      "https://fastplace.dev/docs",
    );
  });

  it("shows the user's initials as the avatar fallback", () => {
    renderHeader();

    expect(screen.getByRole("button", { name: "FA" })).toBeInTheDocument();
  });

  it("opens the user menu from the avatar with account actions", async () => {
    renderHeader();
    const userEvent_ = userEvent.setup();

    await userEvent_.click(screen.getByRole("button", { name: "FA" }));

    const menu = await screen.findByRole("menu");
    expect(menu).toHaveTextContent("Firoz Anam");
    expect(menu).toHaveTextContent("firoz@example.com");

    const settings = within(menu).getByRole("menuitem", { name: /settings/i });
    expect(settings).toHaveAttribute("href", "/settings/profile");

    const logout = within(menu).getByRole("menuitem", { name: /log out/i });
    expect(logout).toHaveAttribute("href", "/logout");
  });

  it("renders breadcrumbs only when more than one crumb is supplied", () => {
    const { unmount } = renderHeader({
      breadcrumbs: [
        { title: "Dashboard", href: "/dashboard" },
        { title: "Projects", href: "/projects" },
      ],
    });

    const navigation = screen.getByRole("navigation", { name: /breadcrumb/i });
    expect(navigation).toHaveTextContent("Dashboard");
    expect(navigation).toHaveTextContent("Projects");

    unmount();
    router.reset();
    renderHeader({ breadcrumbs: [{ title: "Dashboard", href: "/dashboard" }] });
    expect(screen.queryByRole("navigation", { name: /breadcrumb/i })).toBeNull();

    unmount();
    router.reset();
    renderHeader();
    expect(screen.queryByRole("navigation", { name: /breadcrumb/i })).toBeNull();
  });
});
