import "@testing-library/jest-dom/vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useClipboard } from "../use-clipboard";

// jsdom ships no navigator.clipboard (browsers always do) — each test
// installs the stand-in it needs, and the suite strips it afterwards so
// the "unsupported" case starts from a clean navigator.
function stubClipboard(writeText: (text: string) => Promise<void>): void {
  Object.assign(navigator, { clipboard: { writeText } });
}

afterEach(() => {
  cleanup();
  delete (navigator as Partial<Navigator> & { clipboard?: unknown }).clipboard;
  vi.clearAllMocks();
});

describe("useClipboard", () => {
  it("resolves true, writes the text, and exposes it as copied", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(writeText);

    const { result } = renderHook(() => useClipboard());
    expect(result.current[0]).toBeNull();

    let outcome: boolean | undefined;
    await act(async () => {
      outcome = await result.current[1]("sk-setup-key");
    });

    expect(outcome).toBe(true);
    expect(writeText).toHaveBeenCalledWith("sk-setup-key");
    expect(result.current[0]).toBe("sk-setup-key");
  });

  it("returns false and warns when the clipboard is unavailable", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    const { result } = renderHook(() => useClipboard());

    let outcome: boolean | undefined;
    await act(async () => {
      outcome = await result.current[1]("anything");
    });

    expect(outcome).toBe(false);
    expect(result.current[0]).toBeNull();
    expect(warn).toHaveBeenCalledWith("Clipboard not supported");
  });

  it("returns false, warns, and clears the copied value when writeText rejects", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    stubClipboard(vi.fn().mockRejectedValue(new Error("denied")));

    const { result } = renderHook(() => useClipboard());

    let outcome: boolean | undefined;
    await act(async () => {
      outcome = await result.current[1]("forbidden");
    });

    expect(outcome).toBe(false);
    expect(result.current[0]).toBeNull();
    expect(warn).toHaveBeenCalledWith("Copy failed", expect.any(Error));
  });
});
