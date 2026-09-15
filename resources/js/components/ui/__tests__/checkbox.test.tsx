import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { Checkbox } from "../checkbox";

afterEach(cleanup);

describe("Checkbox", () => {
  it("renders unchecked by default with the checkbox slot attribute", () => {
    render(<Checkbox aria-label="Subscribe" />);
    const checkbox = screen.getByRole("checkbox", { name: "Subscribe" });
    expect(checkbox).toHaveAttribute("data-slot", "checkbox");
    expect(checkbox).toHaveAttribute("aria-checked", "false");
  });

  it("does not render the check indicator while unchecked", () => {
    render(<Checkbox aria-label="Subscribe" />);
    const checkbox = screen.getByRole("checkbox", { name: "Subscribe" });
    expect(checkbox.querySelector('[data-slot="checkbox-indicator"]')).not.toBeInTheDocument();
  });

  it("toggles to checked on click and shows the check svg indicator", async () => {
    const user = userEvent.setup();
    render(<Checkbox aria-label="Subscribe" />);
    const checkbox = screen.getByRole("checkbox", { name: "Subscribe" });

    await user.click(checkbox);

    expect(checkbox).toHaveAttribute("aria-checked", "true");
    const indicator = checkbox.querySelector('[data-slot="checkbox-indicator"]');
    expect(indicator).toBeInTheDocument();
    expect(indicator?.querySelector("svg")).toBeInTheDocument();
  });

  it("does not toggle when disabled", async () => {
    const user = userEvent.setup();
    render(<Checkbox aria-label="Subscribe" disabled />);
    const checkbox = screen.getByRole("checkbox", { name: "Subscribe" });

    await user.click(checkbox);

    expect(checkbox).toBeDisabled();
    expect(checkbox).toHaveAttribute("aria-checked", "false");
    expect(checkbox.querySelector('[data-slot="checkbox-indicator"]')).not.toBeInTheDocument();
  });

  it("merges a caller className onto the base surface classes", () => {
    render(<Checkbox aria-label="Subscribe" className="size-5" />);
    const checkbox = screen.getByRole("checkbox", { name: "Subscribe" });
    expect(checkbox.className).toContain("size-5");
    expect(checkbox.className).not.toContain("size-4");
    expect(checkbox.className).toContain("border-input");
    expect(checkbox.className).toContain("data-[state=checked]:bg-primary");
  });
});
