import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import { Toggle } from "../toggle";

afterEach(cleanup);

describe("Toggle", () => {
  it("renders a toggle button with the on-state surface classes", () => {
    render(<Toggle aria-label="Bold">B</Toggle>);
    const toggle = screen.getByRole("button", { name: "Bold" });

    expect(toggle).toHaveAttribute("data-slot", "toggle");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(toggle.className).toContain("data-[state=on]:bg-accent-surface");
    expect(toggle.className).toContain("data-[state=on]:text-accent-surface-foreground");
  });

  it("flips to the on state and calls onPressedChange on click", async () => {
    const user = userEvent.setup();
    const onPressedChange = vi.fn();
    render(
      <Toggle aria-label="Bold" onPressedChange={onPressedChange}>
        B
      </Toggle>,
    );
    const toggle = screen.getByRole("button", { name: "Bold" });

    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(toggle).toHaveAttribute("data-state", "on");
    expect(onPressedChange).toHaveBeenCalledTimes(1);
    expect(onPressedChange).toHaveBeenCalledWith(true);
  });

  it("maps the outline variant onto its border and hover surface classes", () => {
    render(
      <Toggle variant="outline" aria-label="Italic">
        I
      </Toggle>,
    );
    const toggle = screen.getByRole("button", { name: "Italic" });

    expect(toggle.className).toContain("border-input");
    expect(toggle.className).toContain("hover:bg-accent-surface");
    expect(toggle.className).toContain("hover:text-accent-surface-foreground");
  });
});
