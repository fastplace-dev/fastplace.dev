import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router, type Page } from "@fastplace/react";

import AppearancePage from "../Settings/Appearance";
import { initializeTheme } from "@/hooks/use-appearance";
import SettingsLayout from "@/layouts/settings/layout";

// The page renders AppearanceTabs, whose appearance store touches both
// localStorage and matchMedia — neither exists under jsdom. Same stand-in
// pattern as the use-appearance hook tests.
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

  const mediaQuery = {
    matches: false,
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => true,
  } as MediaQueryList;
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => mediaQuery),
  );
});

afterEach(() => {
  cleanup();
  router.reset();
  document.title = "";
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.classList.remove("dark");
  document.documentElement.style.colorScheme = "";
  vi.unstubAllGlobals();
});

function renderPage() {
  const initialPage: Page = {
    component: "Settings/Appearance",
    props: {},
    url: "/settings/appearance",
  };
  // Re-seed the module-global theme store so each test starts at system.
  initializeTheme();
  return render(
    <FastplaceProvider initialPage={initialPage}>
      <AppearancePage />
    </FastplaceProvider>,
  );
}

describe("Settings/Appearance page", () => {
  it("sets the document title and renders the sr-only page heading", () => {
    renderPage();

    expect(document.title).toBe("Appearance settings");
    expect(
      screen.getByRole("heading", { level: 1, name: "Appearance settings" }),
    ).toBeInTheDocument();
  });

  it("renders the settings heading block with its description", () => {
    renderPage();

    expect(
      screen.getByRole("heading", { level: 2, name: "Appearance settings" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Update the appearance settings for your account")).toBeInTheDocument();
  });

  it("renders the appearance radio group defaulting to the system theme", () => {
    renderPage();

    expect(screen.getByRole("radiogroup", { name: "Appearance" })).toBeInTheDocument();

    expect(screen.getByRole("radio", { name: /system/i })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: /light/i })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("radio", { name: /dark/i })).toHaveAttribute("aria-checked", "false");
  });

  it("applies and persists a theme selection", () => {
    renderPage();

    fireEvent.click(screen.getByRole("radio", { name: /dark/i }));

    expect(screen.getByRole("radio", { name: /dark/i })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: /system/i })).toHaveAttribute("aria-checked", "false");
    expect(localStorage.getItem("fastplace-appearance")).toBe("dark");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(document.documentElement).toHaveClass("dark");
  });

  it("declares the settings layout pair as its static layout", () => {
    const layout = (AppearancePage as unknown as { layout?: unknown }).layout;

    expect(Array.isArray(layout)).toBe(true);
    const layouts = layout as unknown[];
    expect(layouts).toHaveLength(2);
    // Outer: the app sidebar shell wrapper; inner: the settings nav layout.
    expect(typeof layouts[0]).toBe("function");
    expect(layouts[1]).toBe(SettingsLayout);
  });
});
