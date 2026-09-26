import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { Badge } from "../badge";

afterEach(cleanup);

describe("Badge", () => {
  it("renders a span with the default variant classes", () => {
    render(<Badge>New</Badge>);
    const badge = screen.getByText("New");

    expect(badge.tagName).toBe("SPAN");
    expect(badge).toHaveAttribute("data-slot", "badge");
    expect(badge.className).toContain("bg-primary");
    expect(badge.className).toContain("text-primary-foreground");
  });

  it("maps each variant onto its distinct classes", () => {
    const { rerender } = render(<Badge variant="secondary">S</Badge>);
    expect(screen.getByText("S").className).toContain("bg-secondary");

    rerender(<Badge variant="destructive">D</Badge>);
    const destructive = screen.getByText("D");
    expect(destructive.className).toContain("bg-destructive");
    expect(destructive.className).toContain("text-destructive-foreground");
    expect(destructive.className).not.toContain("text-white");

    rerender(<Badge variant="outline">O</Badge>);
    const outline = screen.getByText("O");
    expect(outline.className).toContain("text-foreground");
    expect(outline.className).toContain("[a&]:hover:bg-accent-surface");
    expect(outline.className).toContain("[a&]:hover:text-accent-surface-foreground");
  });

  it("lets a caller className override the base padding", () => {
    render(<Badge className="px-4">P</Badge>);
    const badge = screen.getByText("P");
    expect(badge.className).toContain("px-4");
    expect(badge.className).not.toContain("px-2");
  });
});
