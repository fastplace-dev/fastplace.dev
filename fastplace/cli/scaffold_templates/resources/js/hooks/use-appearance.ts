import { useSyncExternalStore } from "react";

export type ResolvedAppearance = "light" | "dark";
export type Appearance = ResolvedAppearance | "system";

const STORAGE_KEY = "fastplace-appearance";

type AppearanceState = {
  readonly appearance: Appearance;
  readonly resolved: ResolvedAppearance;
};

// The stylesheet reacts to two mechanisms in lockstep — the .dark class
// (Tailwind's dark: variant) and the data-theme attribute (the token
// palette). "system" clears the attribute and mirrors the OS preference
// onto .dark so both mechanisms stay consistent with the palette's
// prefers-color-scheme block.
const SERVER_STATE: AppearanceState = { appearance: "system", resolved: "light" };

const listeners = new Set<() => void>();
let state: AppearanceState = SERVER_STATE;

const prefersDark = (): boolean =>
  typeof window === "undefined" ? false : window.matchMedia("(prefers-color-scheme: dark)").matches;

const isDark = (appearance: Appearance): boolean =>
  appearance === "dark" || (appearance === "system" && prefersDark());

const storedAppearance = (): Appearance => {
  if (typeof window === "undefined") return "system";
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" || stored === "dark" ? stored : "system";
};

const applyAppearance = (appearance: Appearance): void => {
  const dark = isDark(appearance);
  state = { appearance, resolved: dark ? "dark" : "light" };

  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (appearance === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", appearance);
  root.classList.toggle("dark", dark);
  root.style.colorScheme = dark ? "dark" : "light";
};

const subscribe = (callback: () => void) => {
  listeners.add(callback);
  return () => listeners.delete(callback);
};

const notify = (): void => listeners.forEach((listener) => listener());

const handleSystemChange = (): void => {
  applyAppearance(state.appearance);
  notify();
};

/** Seed the store and the DOM from the persisted preference. Call once on boot. */
export function initializeTheme(): void {
  if (typeof window === "undefined") return;
  if (!localStorage.getItem(STORAGE_KEY)) localStorage.setItem(STORAGE_KEY, "system");

  applyAppearance(storedAppearance());
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", handleSystemChange);
}

export function useAppearance(): {
  readonly appearance: Appearance;
  readonly resolvedAppearance: ResolvedAppearance;
  readonly updateAppearance: (mode: Appearance) => void;
} {
  const snapshot = useSyncExternalStore(
    subscribe,
    () => state,
    () => SERVER_STATE,
  );

  const updateAppearance = (mode: Appearance): void => {
    if (typeof window !== "undefined") localStorage.setItem(STORAGE_KEY, mode);
    applyAppearance(mode);
    notify();
  };

  return {
    appearance: snapshot.appearance,
    resolvedAppearance: snapshot.resolved,
    updateAppearance,
  };
}
