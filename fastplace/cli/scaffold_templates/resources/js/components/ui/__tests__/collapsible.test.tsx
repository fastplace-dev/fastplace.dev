import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../collapsible";

afterEach(cleanup);

function DemoCollapsible() {
  return (
    <Collapsible>
      <CollapsibleTrigger>Toggle panel</CollapsibleTrigger>
      <CollapsibleContent>Panel body</CollapsibleContent>
    </Collapsible>
  );
}

describe("Collapsible", () => {
  it("keeps the content hidden until the trigger is clicked", async () => {
    const user = userEvent.setup();
    render(<DemoCollapsible />);

    expect(screen.queryByText("Panel body")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /toggle panel/i }));
    expect(await screen.findByText("Panel body")).toBeInTheDocument();
  });

  it("reflects open state on the trigger and closes again on a second click", async () => {
    const user = userEvent.setup();
    render(<DemoCollapsible />);
    const trigger = screen.getByRole("button", { name: /toggle panel/i });

    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(trigger).toHaveAttribute("data-state", "open");
    expect(screen.getByText("Panel body")).toBeInTheDocument();

    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(screen.queryByText("Panel body")).not.toBeInTheDocument());
  });

  it("marks each part with its data-slot attribute", () => {
    render(<DemoCollapsible />);

    expect(document.querySelector("[data-slot='collapsible']")).not.toBeNull();
    expect(screen.getByRole("button", { name: /toggle panel/i })).toHaveAttribute(
      "data-slot",
      "collapsible-trigger",
    );
  });
});
