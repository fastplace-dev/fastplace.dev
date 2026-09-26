import "@testing-library/jest-dom/vitest";
import { cleanup, renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { initializeTheme, useAppearance } from "../use-appearance";

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

describe("initializeTheme", () => {
  it("applies explicit light from stored preference", () => {
    localStorage.setItem("fastplace-appearance", "light");
    stubMatchMedia(true);

    initializeTheme();

    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(document.documentElement).not.toHaveClass("dark");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("applies explicit dark via both mechanisms", () => {
    localStorage.setItem("fastplace-appearance", "dark");
    stubMatchMedia(false);

    initializeTheme();

    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(document.documentElement).toHaveClass("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("defaults to system and follows the OS preference", () => {
    stubMatchMedia(true);

    initializeTheme();

    expect(localStorage.getItem("fastplace-appearance")).toBe("system");
    expect(document.documentElement).not.toHaveAttribute("data-theme");
    expect(document.documentElement).toHaveClass("dark");

    const media = stubMatchMedia(false);
    initializeTheme();
    expect(document.documentElement).not.toHaveClass("dark");
    expect(media).toBeTruthy();
  });
});

describe("useAppearance", () => {
  it("reacts to OS theme changes while in system mode", () => {
    const media = stubMatchMedia(false);
    initializeTheme();

    const { result } = renderHook(() => useAppearance());
    expect(result.current.resolvedAppearance).toBe("light");

    act(() => media.setPreference(true));
    expect(result.current.resolvedAppearance).toBe("dark");
    expect(document.documentElement).toHaveClass("dark");
    expect(document.documentElement).not.toHaveAttribute("data-theme");
  });

  it("persists and applies an explicit choice", () => {
    stubMatchMedia(true);
    initializeTheme();

    const { result } = renderHook(() => useAppearance());
    act(() => result.current.updateAppearance("light"));

    expect(localStorage.getItem("fastplace-appearance")).toBe("light");
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(document.documentElement).not.toHaveClass("dark");
    expect(result.current.appearance).toBe("light");
    expect(result.current.resolvedAppearance).toBe("light");
  });
});
