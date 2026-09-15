import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import {
  NavigationMenu,
  NavigationMenuContent,
  NavigationMenuItem,
  NavigationMenuLink,
  NavigationMenuList,
  NavigationMenuTrigger,
} from "../navigation-menu";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// jsdom ships no ResizeObserver (the menu viewport measures open content with one).
globalThis.ResizeObserver = globalThis.ResizeObserver ?? ResizeObserverStub;

// jsdom ships no PointerEvent, so synthesized pointerdown events would lose
// their button state and the trigger's left-click guard would never pass.
class PointerEventStub extends MouseEvent {
  pointerId: number;
  pointerType: string;
  isPrimary: boolean;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
    this.pointerType = init.pointerType ?? "";
    this.isPrimary = init.isPrimary ?? false;
  }
}
// jsdom ships no PointerEvent.
window.PointerEvent = window.PointerEvent ?? PointerEventStub;

function DemoNavigationMenu() {
  return (
    <NavigationMenu>
      <NavigationMenuList>
        <NavigationMenuItem>
          <NavigationMenuTrigger>Products</NavigationMenuTrigger>
          <NavigationMenuContent>
            <ul className="grid gap-1">
              <li>
                <NavigationMenuLink href="/docs">Documentation</NavigationMenuLink>
              </li>
            </ul>
          </NavigationMenuContent>
        </NavigationMenuItem>
        <NavigationMenuItem>
          <NavigationMenuLink href="/pricing">Pricing</NavigationMenuLink>
        </NavigationMenuItem>
      </NavigationMenuList>
    </NavigationMenu>
  );
}

afterEach(cleanup);

describe("NavigationMenu", () => {
  it("renders the navigation landmark with its direct links", () => {
    render(<DemoNavigationMenu />);

    const nav = screen.getByRole("navigation");
    expect(nav).toHaveAttribute("data-slot", "navigation-menu");
    expect(screen.getByRole("list")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Pricing" })).toHaveAttribute("href", "/pricing");
    // Submenu content stays unmounted until its trigger opens.
    expect(screen.queryByRole("link", { name: "Documentation" })).not.toBeInTheDocument();
  });

  it("reveals the submenu content inside a list item when the trigger is clicked", async () => {
    const user = userEvent.setup();
    render(<DemoNavigationMenu />);
    const trigger = screen.getByRole("button", { name: /products/i });

    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await user.click(trigger);

    const docs = await screen.findByRole("link", { name: "Documentation" });
    expect(docs).toBeInTheDocument();
    expect(docs.closest("li")).not.toBeNull();
    expect(trigger).toHaveAttribute("aria-expanded", "true");
  });

  it("opens the submenu content when the trigger is hovered", async () => {
    const user = userEvent.setup();
    render(<DemoNavigationMenu />);

    await user.hover(screen.getByRole("button", { name: /products/i }));

    const docs = await screen.findByRole("link", { name: "Documentation" });
    expect(docs).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /products/i })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });
});
