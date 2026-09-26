import "@testing-library/jest-dom/vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useMobileNavigation } from "../use-mobile-navigation";

afterEach(() => {
  cleanup();
  document.body.style.removeProperty("pointer-events");
});

describe("useMobileNavigation", () => {
  it("returns a cleanup that releases the body pointer-events lock", () => {
    document.body.style.pointerEvents = "none";

    const { result } = renderHook(() => useMobileNavigation());
    act(() => result.current());

    expect(document.body.style.pointerEvents).toBe("");
  });

  it("is safe to call when no lock was applied", () => {
    const { result } = renderHook(() => useMobileNavigation());

    expect(() => act(() => result.current())).not.toThrow();
    expect(document.body.style.pointerEvents).toBe("");
  });
});
