import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { router } from "@fastplace/react";

import { UserMenuContent } from "../user-menu-content";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { User } from "@/types";

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

function bridgePage(url: string) {
  return {
    ok: true,
    headers: new Headers({ "content-type": "application/json" }),
    redirected: false,
    json: () => Promise.resolve({ component: "Dashboard/Index", props: {}, url }),
  } as unknown as Response;
}

const user: User = {
  id: 1,
  name: "Firoz Anam",
  email: "firoz@example.com",
  avatar: undefined,
  email_verified_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function renderOpenMenu() {
  return render(
    <DropdownMenu open onOpenChange={() => {}}>
      <DropdownMenuTrigger>User menu</DropdownMenuTrigger>
      <DropdownMenuContent>
        <UserMenuContent user={user} />
      </DropdownMenuContent>
    </DropdownMenu>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  router.reset();
  window.history.replaceState(null, "", "/");
  document.body.style.removeProperty("pointer-events");
});

describe("UserMenuContent", () => {
  it("labels the menu with the user's name and email", async () => {
    renderOpenMenu();

    const menu = await screen.findByRole("menu");
    expect(menu).toHaveTextContent("Firoz Anam");
    expect(menu).toHaveTextContent("firoz@example.com");
    expect(screen.getByText("FA")).toBeInTheDocument();
  });

  it("links the Settings item to the profile settings page", async () => {
    renderOpenMenu();

    const settings = await screen.findByRole("menuitem", { name: /settings/i });
    expect(settings.tagName).toBe("A");
    expect(settings).toHaveAttribute("href", "/settings/profile");
  });

  it("exposes the logout item as a link to /logout", async () => {
    renderOpenMenu();

    const logout = await screen.findByRole("menuitem", { name: /log out/i });
    expect(logout.tagName).toBe("A");
    expect(logout).toHaveAttribute("href", "/logout");
    expect(logout).toHaveAttribute("data-test", "logout-button");
  });

  it("releases the body pointer-events lock when Settings is clicked", async () => {
    const fetchMock = vi.fn().mockResolvedValue(bridgePage("/settings/profile"));
    vi.stubGlobal("fetch", fetchMock);
    const userEvent_ = userEvent.setup();
    renderOpenMenu();

    // Simulate the mobile sheet scroll lock the cleanup releases.
    document.body.style.pointerEvents = "none";
    await userEvent_.click(await screen.findByRole("menuitem", { name: /settings/i }));

    expect(document.body.style.pointerEvents).toBe("");
  });

  it("logs out through a single POST bridge visit", async () => {
    const fetchMock = vi.fn().mockResolvedValue(bridgePage("/dashboard"));
    vi.stubGlobal("fetch", fetchMock);
    const userEvent_ = userEvent.setup();
    renderOpenMenu();

    document.body.style.pointerEvents = "none";
    await userEvent_.click(await screen.findByRole("menuitem", { name: /log out/i }));

    // The click both releases the mobile navigation lock and issues the
    // logout POST — no duplicate GET navigation alongside it.
    expect(document.body.style.pointerEvents).toBe("");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/logout");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Fastplace-Request"]).toBe("true");
  });
});
