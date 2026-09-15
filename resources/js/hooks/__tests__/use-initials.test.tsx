import "@testing-library/jest-dom/vitest";
import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useInitials } from "../use-initials";

afterEach(() => {
  cleanup();
});

describe("useInitials", () => {
  it("derives first and last initials from a full name", () => {
    const { result } = renderHook(() => useInitials());

    expect(result.current("Ada Lovelace")).toBe("AL");
  });

  it("falls back to the single initial for one name", () => {
    const { result } = renderHook(() => useInitials());

    expect(result.current("grace")).toBe("G");
  });

  it("returns an empty string for an empty name", () => {
    const { result } = renderHook(() => useInitials());

    expect(result.current("")).toBe("");
  });

  it("collapses extra whitespace between names", () => {
    const { result } = renderHook(() => useInitials());

    expect(result.current("  Grace   Hopper ")).toBe("GH");
  });

  it("uppercases lowercase initials", () => {
    const { result } = renderHook(() => useInitials());

    expect(result.current("alan turing")).toBe("AT");
  });
});
