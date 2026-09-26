import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { BookOpen } from "lucide-react";

import { NavFooter } from "../nav-footer";
import { SidebarProvider } from "@/components/ui/sidebar";
import type { NavItem } from "@/types";

type MediaQueryListener = (event: MediaQueryListEvent) => void;

// jsdom ships no matchMedia — the sidebar provider's viewport probe needs a
// stand-in (same pattern as the sidebar ui tests).
const mediaQuery = {
  matches: false,
  media: "(max-width: 767px)",
  onchange: null,
  addEventListener: (_: string, listener: MediaQueryListener) => void listener,
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => true,
} as MediaQueryList;

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => mediaQuery),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  // Clear the persisted sidebar state cookie between tests.
  document.cookie = "sidebar_state=; path=/; max-age=0";
});

const items: NavItem[] = [
  { title: "Documentation", href: "https://example.com/docs", icon: BookOpen },
  { title: "Changelog", href: "https://example.com/changelog" },
];

function renderNavFooter(props: Parameters<typeof NavFooter>[0]) {
  return render(
    <SidebarProvider>
      <NavFooter {...props} />
    </SidebarProvider>,
  );
}

describe("NavFooter", () => {
  it("renders one external anchor per item with the item's href", () => {
    renderNavFooter({ items });

    const docs = screen.getByRole("link", { name: /documentation/i });
    const changelog = screen.getByRole("link", { name: /changelog/i });
    expect(docs).toHaveAttribute("href", "https://example.com/docs");
    expect(changelog).toHaveAttribute("href", "https://example.com/changelog");
  });

  it("opens links in a new tab with safe rel attributes", () => {
    renderNavFooter({ items });

    for (const item of items) {
      const link = screen.getByRole("link", { name: new RegExp(item.title, "i") });
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }
  });

  it("renders the item icon when provided and omits it when not", () => {
    renderNavFooter({ items });

    const docs = screen.getByRole("link", { name: /documentation/i });
    const changelog = screen.getByRole("link", { name: /changelog/i });
    expect(docs.querySelector("svg")).not.toBeNull();
    expect(changelog.querySelector("svg")).toBeNull();
  });

  it("composes the footer group, content, and menu slots", () => {
    renderNavFooter({ items });

    expect(document.querySelector("[data-slot='sidebar-group']")).not.toBeNull();
    expect(document.querySelector("[data-slot='sidebar-group-content']")).not.toBeNull();
    expect(document.querySelectorAll("[data-slot='sidebar-menu-item']")).toHaveLength(2);
    // Every footer button carries the semantic muted-ink token pair.
    const buttons = document.querySelectorAll("[data-slot='sidebar-menu-button']");
    expect(buttons).toHaveLength(2);
    for (const button of buttons) {
      expect(button.className).toContain("text-ink-muted");
      expect(button.className).toContain("hover:text-ink");
    }
  });

  it("keeps the icon-collapsible padding override and merges a caller className", () => {
    renderNavFooter({ items, className: "mb-4" });

    const group = document.querySelector("[data-slot='sidebar-group']");
    expect(group).not.toBeNull();
    expect(group!.className).toContain("group-data-[collapsible=icon]:p-0");
    expect(group!.className).toContain("mb-4");
  });
});
