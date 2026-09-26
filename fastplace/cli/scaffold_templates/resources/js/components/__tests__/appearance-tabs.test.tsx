import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";

import AppearanceTabs from "../appearance-tabs";
import { initializeTheme } from "@/hooks/use-appearance";

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

// jsdom has no matchMedia — a controllable stand-in drives the system
// preference in these tests.
function stubMatchMedia(prefersDark: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  let matches = prefersDark;
  const query: MediaQueryList = {
    get matches() {
      return matches;
    },
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
  return {
    setPreference(dark: boolean) {
      matches = dark;
      for (const listener of listeners) listener({ matches: dark } as MediaQueryListEvent);
    },
  };
}

afterEach(() => {
  cleanup();
  localStorage.clear(); // while the stub is still installed
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.classList.remove("dark");
  document.documentElement.style.colorScheme = "";
});

describe("AppearanceTabs", () => {
  it("renders with safe defaults when initializeTheme has not run", async () => {
    // Fresh module graph so the store sits at its pristine "system" default —
    // the state a first paint sees before any boot code runs.
    vi.resetModules();
    const { default: FreshTabs } = await import("../appearance-tabs");
    render(<FreshTabs />);

    const group = screen.getByRole("radiogroup", { name: "Appearance" });
    expect(within(group).getByRole("radio", { name: "System" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("renders Light, Dark and System options on semantic token surfaces", () => {
    stubMatchMedia(false);
    initializeTheme();
    render(<AppearanceTabs />);

    const group = screen.getByRole("radiogroup", { name: "Appearance" });
    for (const label of ["Light", "Dark", "System"]) {
      expect(within(group).getByRole("radio", { name: label })).toBeInTheDocument();
    }

    expect(group.className).toContain("bg-surface-raised");
    expect(group.className).toContain("border-line");

    const checked = within(group).getByRole("radio", { name: "System" });
    expect(checked.className).toContain("bg-surface");
    expect(checked.className).toContain("text-ink");
  });

  it("marks the persisted appearance as the checked option", () => {
    localStorage.setItem("fastplace-appearance", "dark");
    stubMatchMedia(false);
    initializeTheme();
    render(<AppearanceTabs />);

    expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "Light" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("radio", { name: "System" })).toHaveAttribute("aria-checked", "false");
  });

  it("clicking Dark drives the real theme store", async () => {
    stubMatchMedia(true);
    initializeTheme();
    const user = userEvent.setup();
    render(<AppearanceTabs />);

    await user.click(screen.getByRole("radio", { name: "Dark" }));

    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(document.documentElement).toHaveClass("dark");
    expect(localStorage.getItem("fastplace-appearance")).toBe("dark");
    expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
  });

  it("clicking Light clears .dark and sets data-theme=light", async () => {
    localStorage.setItem("fastplace-appearance", "dark");
    stubMatchMedia(false);
    initializeTheme();
    const user = userEvent.setup();
    render(<AppearanceTabs />);

    await user.click(screen.getByRole("radio", { name: "Light" }));

    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(document.documentElement).not.toHaveClass("dark");
    expect(localStorage.getItem("fastplace-appearance")).toBe("light");
  });
});
