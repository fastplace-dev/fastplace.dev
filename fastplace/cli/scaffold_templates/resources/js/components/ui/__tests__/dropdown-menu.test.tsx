import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuPortal,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "../dropdown-menu";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// jsdom ships no ResizeObserver (the popper positioning needs one).
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

// jsdom's selector engine re-enters Element.matches with identical arguments
// when resolving pseudo-class state, which blows up exponentially and stalls
// the event loop for seconds while a positioned overlay is open. Memoizing
// per (element, selector) pair keeps matching results identical but fast.
const matchesMemo = new WeakMap<Element, Map<string, boolean>>();
const originalMatches = Element.prototype.matches;
Element.prototype.matches = function (this: Element, selector: string) {
  let perElement = matchesMemo.get(this);
  if (!perElement) {
    perElement = new Map<string, boolean>();
    matchesMemo.set(this, perElement);
  }
  if (perElement.has(selector)) return perElement.get(selector) as boolean;
  const result = originalMatches.call(this, selector);
  perElement.set(selector, result);
  return result;
};

function DemoDropdown({
  itemOnSelect,
  onCheckedChange,
}: {
  itemOnSelect?: () => void;
  onCheckedChange?: (checked: boolean) => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger>Actions</DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuLabel>My Account</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem onSelect={itemOnSelect}>
            Profile
            <DropdownMenuShortcut>Ctrl+P</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive">Delete</DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuCheckboxItem checked onCheckedChange={onCheckedChange}>
          Show status bar
        </DropdownMenuCheckboxItem>
        <DropdownMenuRadioGroup value="light">
          <DropdownMenuRadioItem value="light">Light</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">Dark</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>More</DropdownMenuSubTrigger>
          <DropdownMenuPortal>
            <DropdownMenuSubContent>
              <DropdownMenuLabel inset>Sub options</DropdownMenuLabel>
              <DropdownMenuItem inset>Sub item</DropdownMenuItem>
            </DropdownMenuSubContent>
          </DropdownMenuPortal>
        </DropdownMenuSub>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

afterEach(cleanup);

describe("DropdownMenu", () => {
  it("keeps the menu hidden until the trigger is clicked", async () => {
    const user = userEvent.setup();
    render(<DemoDropdown />);

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Actions" }));
    const menu = await screen.findByRole("menu");
    expect(menu).toBeInTheDocument();
    // Portal content lives outside the render container, on document.body.
    expect(menu.closest("body")).toBe(document.body);
    expect(screen.getAllByRole("menuitem").length).toBeGreaterThan(0);
  });

  it("calls an item onSelect handler and closes the menu on click", async () => {
    const user = userEvent.setup();
    const itemOnSelect = vi.fn();
    render(<DemoDropdown itemOnSelect={itemOnSelect} />);

    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: /profile/i }));

    expect(itemOnSelect).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });

  it("shows the check indicator on a checked checkbox item and fires onCheckedChange", async () => {
    const user = userEvent.setup();
    const onCheckedChange = vi.fn();
    render(<DemoDropdown onCheckedChange={onCheckedChange} />);

    await user.click(screen.getByRole("button", { name: "Actions" }));
    const checkboxItem = await screen.findByRole("menuitemcheckbox", {
      name: /show status bar/i,
    });

    expect(checkboxItem.getAttribute("aria-checked")).toBe("true");
    expect(checkboxItem.querySelector("svg")).not.toBeNull();

    await user.click(checkboxItem);
    expect(onCheckedChange).toHaveBeenCalledTimes(1);
  });

  it("renders a label and a separator inside the menu", async () => {
    const user = userEvent.setup();
    render(<DemoDropdown />);

    await user.click(screen.getByRole("button", { name: "Actions" }));
    await screen.findByRole("menu");

    expect(screen.getByText("My Account")).toBeInTheDocument();
    const separator = document.querySelector("[data-slot='dropdown-menu-separator']");
    expect(separator).not.toBeNull();
  });

  it("marks a destructive item with data-variant", async () => {
    const user = userEvent.setup();
    render(<DemoDropdown />);

    await user.click(screen.getByRole("button", { name: "Actions" }));
    const deleteItem = await screen.findByRole("menuitem", { name: /delete/i });

    expect(deleteItem).toHaveAttribute("data-variant", "destructive");
  });

  it("closes the open menu on the escape key", async () => {
    const user = userEvent.setup();
    render(<DemoDropdown />);

    await user.click(screen.getByRole("button", { name: "Actions" }));
    await screen.findByRole("menu");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });
});
