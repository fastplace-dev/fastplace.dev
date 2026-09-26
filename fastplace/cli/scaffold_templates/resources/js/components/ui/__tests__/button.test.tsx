import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { Button } from "../button";

afterEach(cleanup);

describe("Button", () => {
  it("renders a button with the default variant and size classes", () => {
    render(<Button>Save</Button>);
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toHaveAttribute("data-slot", "button");
    expect(button.className).toContain("bg-primary");
    expect(button.className).toContain("h-9");
  });

  it("maps each variant onto its surface classes", () => {
    const { rerender } = render(<Button variant="destructive">Delete</Button>);
    expect(screen.getByRole("button").className).toContain("bg-destructive");

    rerender(<Button variant="outline">Cancel</Button>);
    expect(screen.getByRole("button").className).toContain("border-input");

    rerender(<Button variant="secondary">Close</Button>);
    expect(screen.getByRole("button").className).toContain("bg-secondary");

    rerender(<Button variant="link">Details</Button>);
    expect(screen.getByRole("button").className).toContain("underline-offset-4");
  });

  it("maps the icon size onto a square box", () => {
    render(
      <Button size="icon" aria-label="Add">
        +
      </Button>,
    );
    expect(screen.getByRole("button", { name: "Add" }).className).toContain("size-9");
  });

  it("lets a caller className override the variant padding", () => {
    render(<Button className="px-6">Wide</Button>);
    expect(screen.getByRole("button").className).toContain("px-6");
    expect(screen.getByRole("button").className).not.toContain("px-4");
  });

  it("passes through native button attributes", () => {
    render(
      <Button type="submit" disabled>
        Go
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Go" });
    expect(button).toHaveAttribute("type", "submit");
    expect(button).toBeDisabled();
  });

  it("renders its child element instead of a button when asChild", () => {
    render(
      <Button asChild>
        <a href="/projects">Projects</a>
      </Button>,
    );
    const link = screen.getByRole("link", { name: "Projects" });
    expect(link).toHaveAttribute("data-slot", "button");
    expect(link.className).toContain("bg-primary");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
