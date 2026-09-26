import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "../select";

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

// Radix Select scrolls the highlighted option into view each time the listbox
// opens and routes item selection through pointer capture — jsdom ships
// neither API, so stand in no-op versions.
window.HTMLElement.prototype.scrollIntoView =
  window.HTMLElement.prototype.scrollIntoView ?? vi.fn();
window.HTMLElement.prototype.hasPointerCapture =
  window.HTMLElement.prototype.hasPointerCapture ?? vi.fn(() => false);
window.HTMLElement.prototype.releasePointerCapture =
  window.HTMLElement.prototype.releasePointerCapture ?? vi.fn();
window.HTMLElement.prototype.setPointerCapture =
  window.HTMLElement.prototype.setPointerCapture ?? vi.fn();

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

function DemoSelect({
  defaultValue,
  onValueChange,
}: {
  defaultValue?: string;
  onValueChange?: (value: string) => void;
}) {
  return (
    <Select defaultValue={defaultValue} onValueChange={onValueChange}>
      <SelectTrigger aria-label="Fruit">
        <SelectValue placeholder="Pick a fruit" />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectLabel>Fruits</SelectLabel>
          <SelectItem value="apple">Apple</SelectItem>
          <SelectItem value="banana">Banana</SelectItem>
        </SelectGroup>
        <SelectSeparator />
        <SelectItem value="carrot">Carrot</SelectItem>
      </SelectContent>
    </Select>
  );
}

afterEach(cleanup);

describe("Select", () => {
  it("shows the placeholder and keeps the listbox closed until opened", () => {
    render(<DemoSelect />);

    const trigger = screen.getByRole("combobox", { name: "Fruit" });
    expect(trigger).toHaveAttribute("data-slot", "select-trigger");
    expect(trigger).toHaveTextContent("Pick a fruit");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("opens the listbox in a portal on the document body", async () => {
    const user = userEvent.setup();
    render(<DemoSelect />);

    await user.click(screen.getByRole("combobox", { name: "Fruit" }));
    const listbox = await screen.findByRole("listbox");

    // Portal content lives outside the render container, on document.body.
    expect(listbox.closest("body")).toBe(document.body);
    expect(screen.getAllByRole("option")).toHaveLength(3);
    expect(screen.getByRole("option", { name: "Apple" })).toBeInTheDocument();
  });

  it("marks the selected item with aria-selected and a check indicator", async () => {
    const user = userEvent.setup();
    render(<DemoSelect defaultValue="banana" />);

    await user.click(screen.getByRole("combobox"));
    const banana = await screen.findByRole("option", { name: "Banana" });

    expect(banana).toHaveAttribute("aria-selected", "true");
    expect(banana.querySelector("[data-slot='select-item-indicator'] svg")).not.toBeNull();
    expect(screen.getByRole("option", { name: "Apple" }).getAttribute("aria-selected")).toBe(
      "false",
    );
  });

  it("updates the trigger label and closes after choosing an option", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    render(<DemoSelect onValueChange={onValueChange} />);

    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Banana" }));

    expect(onValueChange).toHaveBeenCalledWith("banana");
    await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
    const trigger = screen.getByRole("combobox");
    expect(trigger).toHaveTextContent("Banana");
    expect(trigger).not.toHaveTextContent("Pick a fruit");
  });

  it("opens with the space key and selects the highlighted option via Enter", async () => {
    const user = userEvent.setup();
    render(<DemoSelect />);

    screen.getByRole("combobox").focus();
    await user.keyboard(" ");
    await screen.findByRole("listbox");

    // Opening with the keyboard auto-highlights the first option
    // (Radix flags it with a bare data-highlighted attribute).
    const apple = screen.getByRole("option", { name: "Apple" });
    expect(apple).toHaveAttribute("data-highlighted");
    expect(screen.getByRole("option", { name: "Banana" }).hasAttribute("data-highlighted")).toBe(
      false,
    );

    await user.keyboard("{Enter}");

    await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
    expect(screen.getByRole("combobox")).toHaveTextContent("Apple");
  });

  it("opens with ArrowDown and closes on the escape key", async () => {
    const user = userEvent.setup();
    render(<DemoSelect />);

    screen.getByRole("combobox").focus();
    await user.keyboard("{ArrowDown}");
    await screen.findByRole("listbox");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
  });

  it("renders the label, group, and separator inside the content", async () => {
    const user = userEvent.setup();
    render(<DemoSelect />);

    await user.click(screen.getByRole("combobox"));
    const listbox = await screen.findByRole("listbox");
    const content = listbox.closest("[data-slot='select-content']");

    expect(content).not.toBeNull();
    expect(screen.getByText("Fruits")).toHaveAttribute("data-slot", "select-label");
    expect(content?.querySelector("[data-slot='select-group']")).not.toBeNull();
    expect(content?.querySelector("[data-slot='select-separator']")).not.toBeNull();
    // Scroll buttons only mount once the viewport can actually overflow,
    // which jsdom's zero-size layout never triggers — so they stay absent.
    expect(content?.querySelector("[data-slot='select-scroll-up-button']")).toBeNull();
  });
});
