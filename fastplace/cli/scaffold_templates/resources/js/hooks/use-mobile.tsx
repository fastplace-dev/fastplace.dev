import { useSyncExternalStore } from "react";

const MOBILE_BREAKPOINT = 768;

// The MediaQueryList is resolved lazily (and memoized) rather than at import
// time so environments without matchMedia — SSR, embedded views — degrade to
// "desktop" instead of throwing while the module loads.
let mediaQuery: MediaQueryList | null | undefined;

function resolveMediaQuery(): MediaQueryList | null {
  if (mediaQuery === undefined) {
    mediaQuery =
      typeof window !== "undefined" && typeof window.matchMedia === "function"
        ? window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`)
        : null;
  }
  return mediaQuery;
}

function subscribe(callback: () => void): () => void {
  const query = resolveMediaQuery();
  if (!query) {
    return () => {};
  }

  query.addEventListener("change", callback);
  return () => {
    query.removeEventListener("change", callback);
  };
}

/** True while the viewport is at or below the mobile breakpoint. */
export function useIsMobile(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => resolveMediaQuery()?.matches ?? false,
    () => false,
  );
}
