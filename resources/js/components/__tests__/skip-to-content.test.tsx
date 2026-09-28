import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { SkipToContent } from "../skip-to-content";

afterEach(cleanup);

describe("SkipToContent", () => {
  it("renders a skip link targeting the main content landmark (a11y1-G2)", () => {
    render(<SkipToContent />);

    const link = screen.getByRole("link", { name: "Skip to content" });
    expect(link).toHaveAttribute("href", "#main-content");
  });

  it("stays visually hidden until focused", () => {
    render(<SkipToContent />);

    const { className } = screen.getByRole("link", { name: "Skip to content" });
    expect(className).toContain("sr-only");
    expect(className).toContain("focus:not-sr-only");
  });

  it("styles its focused state as a solid brand chip on top of the page", () => {
    render(<SkipToContent />);

    const { className } = screen.getByRole("link", { name: "Skip to content" });
    expect(className).toContain("focus:bg-primary");
    expect(className).toContain("focus:text-primary-foreground");
    expect(className).toContain("focus:z-50");
  });

  it("targets a custom landmark id when provided", () => {
    render(<SkipToContent targetId="custom-main" />);

    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute(
      "href",
      "#custom-main",
    );
  });
});
