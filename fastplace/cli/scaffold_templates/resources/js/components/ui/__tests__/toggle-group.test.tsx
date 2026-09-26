import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import { ToggleGroup, ToggleGroupItem } from "../toggle-group";

afterEach(cleanup);

function SingleDemo({ onValueChange }: { onValueChange?: (value: string) => void }) {
  const [value, setValue] = React.useState("left");
  return (
    <ToggleGroup
      type="single"
      value={value}
      onValueChange={(next) => {
        setValue(next);
        onValueChange?.(next);
      }}
    >
      <ToggleGroupItem value="left">Left</ToggleGroupItem>
      <ToggleGroupItem value="center">Center</ToggleGroupItem>
      <ToggleGroupItem value="right">Right</ToggleGroupItem>
    </ToggleGroup>
  );
}

function MultipleDemo() {
  const [value, setValue] = React.useState<string[]>(["bold", "italic"]);
  return (
    <ToggleGroup type="multiple" value={value} onValueChange={setValue}>
      <ToggleGroupItem value="bold">Bold</ToggleGroupItem>
      <ToggleGroupItem value="italic">Italic</ToggleGroupItem>
    </ToggleGroup>
  );
}

describe("ToggleGroup", () => {
  it("renders the group and exposes the current single selection", () => {
    render(<SingleDemo />);

    expect(document.querySelector("[data-slot='toggle-group']")).not.toBeNull();
    const left = screen.getByRole("radio", { name: "Left" });
    expect(left).toHaveAttribute("data-slot", "toggle-group-item");
    expect(left.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("radio", { name: "Center" }).getAttribute("aria-checked")).toBe(
      "false",
    );
  });

  it("moves the single selection to the clicked item", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    render(<SingleDemo onValueChange={onValueChange} />);

    await user.click(screen.getByRole("radio", { name: "Center" }));

    expect(screen.getByRole("radio", { name: "Center" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("radio", { name: "Left" }).getAttribute("aria-checked")).toBe("false");
    expect(onValueChange).toHaveBeenCalledWith("center");
  });

  it("supports multiple selected items and toggles one off", async () => {
    const user = userEvent.setup();
    render(<MultipleDemo />);

    expect(screen.getByRole("button", { name: "Bold" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Italic" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "Italic" }));

    expect(screen.getByRole("button", { name: "Italic" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Bold" })).toHaveAttribute("aria-pressed", "true");
  });

  it("propagates size and variant from the group to its items", () => {
    render(
      <ToggleGroup type="single" size="sm" variant="outline">
        <ToggleGroupItem value="left">Left</ToggleGroupItem>
      </ToggleGroup>,
    );
    const item = screen.getByRole("radio", { name: "Left" });

    expect(item).toHaveAttribute("data-size", "sm");
    expect(item).toHaveAttribute("data-variant", "outline");
    expect(item.className).toContain("h-8");
  });
});
