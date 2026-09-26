import { useEffect } from "react";
import { usePage, type PageProps } from "@fastplace/react";
import { toast } from "sonner";

/** A flash message the backend may attach to a page payload. */
export type FlashToast = {
  type: "success" | "info" | "warning" | "error";
  message: string;
};

const TOAST_TYPES = new Set<string>(["success", "info", "warning", "error"]);

/** The flash bag lives at props.flash; its toast entry carries the payload. */
function readFlashToast(props: PageProps): unknown {
  const flash = props.flash;
  if (flash == null || typeof flash !== "object") return undefined;
  return (flash as { toast?: unknown }).toast;
}

/** Runtime guard — a malformed flash payload must never reach sonner. */
function isFlashToast(value: unknown): value is FlashToast {
  if (value == null || typeof value !== "object") return false;
  const { type, message } = value as { type?: unknown; message?: unknown };
  return typeof type === "string" && TOAST_TYPES.has(type) && typeof message === "string";
}

/**
 * Fire sonner toasts for flash messages delivered through page props.
 *
 * Tolerant by design: when a payload carries no flash bag (or one with an
 * unrecognized shape) the hook is a no-op. The effect keys on the toast
 * payload's identity, so a re-render of the same page never duplicates a
 * toast, while every bridge navigation with fresh flash fires exactly once.
 */
export function useFlashToast(): void {
  const { props } = usePage();
  const payload = readFlashToast(props);

  useEffect(() => {
    if (!isFlashToast(payload)) return;
    toast[payload.type](payload.message);
  }, [payload]);
}
