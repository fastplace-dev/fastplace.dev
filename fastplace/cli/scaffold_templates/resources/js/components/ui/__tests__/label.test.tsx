import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { Label } from "../label";

afterEach(cleanup);

describe("Label", () => {
  it("renders a label element with the label slot", () => {
    render(<Label>Email</Label>);
    const label = screen.getByText("Email");
    expect(label.tagName).toBe("LABEL");
    expect(label).toHaveAttribute("data-slot", "label");
  });

  it("associates with an input through htmlFor", () => {
    render(
      <div>
        <Label htmlFor="email">Email</Label>
        <input id="email" />
      </div>,
    );
    expect(screen.getByLabelText("Email")).toHaveAttribute("id", "email");
  });

  it("applies the base typography classes", () => {
    render(<Label>Name</Label>);
    expect(screen.getByText("Name").className).toContain("text-sm");
    expect(screen.getByText("Name").className).toContain("font-medium");
  });

  it("merges a caller className into the base classes", () => {
    render(<Label className="text-destructive">Delete</Label>);
    const label = screen.getByText("Delete");
    expect(label.className).toContain("text-destructive");
    expect(label.className).toContain("font-medium");
  });
});
