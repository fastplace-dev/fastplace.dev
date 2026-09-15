import "@testing-library/jest-dom/vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type MediaQueryListener = (event: MediaQueryListEvent) => void;

// jsdom ships no matchMedia — a controllable stand-in drives the viewport
// width in these tests (same pattern as use-appearance.test). Every
// matchMedia() call returns this one instance so the hook's memoized query
// and the stub stay in lockstep.
const listeners = new Set<MediaQueryListener>();
let matches = false;
const mediaQuery = {
  get matches() {
    return matches;
  },
  media: "(max-width: 767px)",
  onchange: null,
  addEventListener: (_: string, listener: MediaQueryListener) => listeners.add(listener),
  removeEventListener: (_: string, listener: MediaQueryListener) => listeners.delete(listener),
  addListener: (listener: MediaQueryListener) => listeners.add(listener),
  removeListener: (listener: MediaQueryListener) => listeners.delete(listener),
  dispatchEvent: () => true,
} as MediaQueryList;

// The hook memoizes its MediaQueryList on first use, so each test resolves a
// fresh module copy against whatever environment (stubbed or bare) is set up.
async function loadUseIsMobile() {
  vi.resetModules();
  const { useIsMobile } = await import("../use-mobile");
  return useIsMobile;
}

beforeEach(() => {
  matches = false;
  listeners.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("useIsMobile", () => {
  it("reports desktop while the viewport is wider than the breakpoint", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => mediaQuery),
    );
    const useIsMobile = await loadUseIsMobile();

    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("reports mobile at or below the breakpoint and follows live changes", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => mediaQuery),
    );
    matches = true;
    const useIsMobile = await loadUseIsMobile();

    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);

    act(() => {
      matches = false;
      for (const listener of listeners) listener({ matches: false } as MediaQueryListEvent);
    });
    expect(result.current).toBe(false);
  });

  it("falls back to desktop when matchMedia is unavailable", async () => {
    // No matchMedia stub installed — the module must not throw at import or
    // during render, and the snapshot settles on "desktop".
    const useIsMobile = await loadUseIsMobile();

    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });
});
