import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../tooltip";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// jsdom ships no ResizeObserver.
globalThis.ResizeObserver = globalThis.ResizeObserver ?? ResizeObserverStub;

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

afterEach(cleanup);

function DemoTooltip() {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button>Hover me</button>
        </TooltipTrigger>
        <TooltipContent>Hint text</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// The trigger opens on a non-touch pointermove; user-event's hover does not
// dispatch pointermove, so drive the open path with the real pointer event.
async function hoverOpen(trigger: HTMLElement) {
  fireEvent.pointerMove(trigger, { pointerType: "mouse" });
  return screen.findByRole("tooltip");
}

// jsdom ships no PointerEvent, so testing-library drops pointer coordinates;
// dispatch coordinate-carrying MouseEvents instead — the grace-area listeners
// that close the tooltip only read clientX/clientY. Leaving the trigger arms
// the tracker and the tooltip closes once the pointer moves on elsewhere.
async function dispatchPointer(el: EventTarget, type: string, x: number, y: number) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y }));
  });
}

async function hoverClose(trigger: HTMLElement) {
  await dispatchPointer(trigger, "pointerleave", 1, 1);
  await dispatchPointer(document, "pointermove", 500, 500);
}

describe("Tooltip", () => {
  it("keeps the tooltip hidden until the trigger is hovered", async () => {
    render(<DemoTooltip />);

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    const tooltip = await hoverOpen(screen.getByRole("button", { name: "Hover me" }));
    expect(tooltip).toBeInTheDocument();
  });

  it("renders the open tooltip content with a data-slot attribute", async () => {
    render(<DemoTooltip />);

    const tooltip = await hoverOpen(screen.getByRole("button", { name: "Hover me" }));
    expect(tooltip).toHaveAttribute("data-slot", "tooltip-content");
    expect(tooltip).toHaveTextContent("Hint text");
  });

  it("closes the tooltip when the trigger is unhovered", async () => {
    render(<DemoTooltip />);

    const trigger = screen.getByRole("button", { name: "Hover me" });
    await hoverOpen(trigger);
    await hoverClose(trigger);
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
  });

  it("merges a caller className onto the content", async () => {
    render(
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <button>Hover me</button>
          </TooltipTrigger>
          <TooltipContent className="tracking-wide">Hint text</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );

    const tooltip = await hoverOpen(screen.getByRole("button", { name: "Hover me" }));
    expect(tooltip).toHaveClass("tracking-wide");
    expect(tooltip.className).toContain("max-w-sm");
  });
});
