import "@testing-library/jest-dom/vitest";
import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { useFlashToast } from "../use-flash-toast";

// The hook's only bridge dependency is usePage(): props — mock it so the
// contract (flash in props -> sonner toast) stays independent of the store.
const usePageMock = vi.hoisted(() => vi.fn());
vi.mock("@fastplace/react", () => ({
  usePage: () => usePageMock(),
}));

// Toast side effects go through sonner's per-severity dispatchers.
vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
  },
}));

function pageWith(props: Record<string, unknown>) {
  usePageMock.mockReturnValue({ component: "Dashboard/Index", props, url: "/" });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useFlashToast", () => {
  it("fires the matching sonner dispatcher for a flash toast", () => {
    pageWith({ flash: { toast: { type: "success", message: "Project created." } } });

    renderHook(() => useFlashToast());

    expect(toast.success).toHaveBeenCalledWith("Project created.");
    expect(toast.error).not.toHaveBeenCalled();
  });

  it.each([
    ["info", "FYI."],
    ["warning", "Careful."],
    ["error", "It broke."],
  ] as const)("maps type %s to its dispatcher", (type, message) => {
    pageWith({ flash: { toast: { type, message } } });

    renderHook(() => useFlashToast());

    expect(toast[type]).toHaveBeenCalledWith(message);
  });

  it("is a no-op when the props carry no flash bag", () => {
    pageWith({ user: "Firoz" });

    const { result } = renderHook(() => useFlashToast());

    expect(result.current).toBeUndefined();
    for (const dispatcher of [toast.success, toast.info, toast.warning, toast.error]) {
      expect(dispatcher).not.toHaveBeenCalled();
    }
  });

  it("tolerates a flash bag without a toast entry", () => {
    pageWith({ flash: { message: "a different flash shape" } });

    expect(() => renderHook(() => useFlashToast())).not.toThrow();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it.each([
    { type: "celebration", message: "unknown severity" },
    { type: "success" },
    { message: "missing type" },
    null,
  ])("ignores a malformed toast payload (%j)", (payload) => {
    pageWith({ flash: { toast: payload } });

    expect(() => renderHook(() => useFlashToast())).not.toThrow();
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.info).not.toHaveBeenCalled();
    expect(toast.warning).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("re-fires only when a navigation swaps in a new flash payload", () => {
    const first = { type: "success" as const, message: "first" };
    pageWith({ flash: { toast: first } });

    const { rerender } = renderHook(() => useFlashToast());
    expect(toast.success).toHaveBeenCalledTimes(1);

    // Same payload object re-rendered (identity-stable) — no duplicate toast.
    rerender();
    expect(toast.success).toHaveBeenCalledTimes(1);

    // A fresh navigation sends a new flash object — toast again.
    pageWith({ flash: { toast: { type: "success", message: "second" } } });
    rerender();
    expect(toast.success).toHaveBeenCalledTimes(2);
    expect(toast.success).toHaveBeenLastCalledWith("second");
  });
});
