import { useCallback } from "react";

export type CleanupFn = () => void;

/**
 * Mobile overlays (sheets, dialogs) lock scrolling by disabling pointer
 * events on the body. This hook hands back the cleanup that releases the
 * lock — call it when the navigation UI closes.
 */
export function useMobileNavigation(): CleanupFn {
  return useCallback(() => {
    document.body.style.removeProperty("pointer-events");
  }, []);
}
