import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router } from "@fastplace/react";
import { toast } from "sonner";
import { initializeTheme } from "@/hooks/use-appearance";
import { Toaster } from "../sonner";

// The vitest jsdom window exposes no localStorage (browsers always do) —
// stand in a minimal, spec-shaped one.
beforeEach(() => {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size;
    },
  });
});

// jsdom has no matchMedia — initializeTheme() registers a listener on it.
function stubMatchMedia(prefersDark: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  const query: MediaQueryList = {
    matches: prefersDark,
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    addEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) =>
      listeners.add(listener),
    removeEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) =>
      listeners.delete(listener),
    addListener: (listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
    removeListener: (listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
    dispatchEvent: () => true,
  } as MediaQueryList;
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => query),
  );
  return query;
}

function renderToaster(initialProps: Record<string, unknown> = {}) {
  return render(
    <FastplaceProvider
      initialPage={{ component: "Dashboard/Index", props: initialProps, url: "/" }}
    >
      <Toaster />
    </FastplaceProvider>,
  );
}

afterEach(() => {
  cleanup();
  // The bridge page store and sonner's toast log are module-level singletons
  // — without these resets each test replays the previous test's state.
  router.reset();
  toast.dismiss();
  localStorage.clear(); // while the stub is still installed
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.classList.remove("dark");
  document.documentElement.style.colorScheme = "";
});

describe("Toaster", () => {
  it("renders the live toast region", () => {
    renderToaster();

    expect(screen.getByRole("region", { name: /notifications/i })).toBeInTheDocument();
  });

  it("themes toasts with the resolved dark appearance and pins them bottom-right", async () => {
    localStorage.setItem("fastplace-appearance", "dark");
    stubMatchMedia(false);
    initializeTheme();
    renderToaster();

    toast.success("Saved to dark corner.");
    const item = await screen.findByText("Saved to dark corner.");
    const list = item.closest("ol");
    expect(list).not.toBeNull();
    expect(list).toHaveAttribute("data-sonner-theme", "dark");
    expect(list).toHaveAttribute("data-y-position", "bottom");
    expect(list).toHaveAttribute("data-x-position", "right");
    // Toast chrome rides the semantic popover tokens, not hardcoded colors.
    expect(list?.style.getPropertyValue("--normal-bg")).toBe("var(--popover)");
    expect(list?.style.getPropertyValue("--normal-text")).toBe("var(--popover-foreground)");
    expect(list?.style.getPropertyValue("--normal-border")).toBe("var(--border)");
  });

  it("follows the resolved light appearance", async () => {
    localStorage.setItem("fastplace-appearance", "light");
    stubMatchMedia(true);
    initializeTheme();
    renderToaster();

    toast.info("Bright and airy.");
    const list = (await screen.findByText("Bright and airy.")).closest("ol");
    expect(list).not.toBeNull();
    expect(list).toHaveAttribute("data-sonner-theme", "light");
  });

  it("shows flash toasts delivered through page props", async () => {
    renderToaster({ flash: { toast: { type: "success", message: "Project published." } } });

    const item = await screen.findByText("Project published.");
    expect(item.closest("li")).toHaveAttribute("data-type", "success");
  });

  it("stays quiet when the page props carry no flash", () => {
    renderToaster({ user: "Firoz" });

    const region = screen.getByRole("region", { name: /notifications/i });
    expect(region.querySelector("[data-sonner-toaster]")).toBeNull();
  });
});
