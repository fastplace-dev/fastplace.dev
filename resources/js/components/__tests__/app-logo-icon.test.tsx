import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import AppLogoIcon from "../app-logo-icon";

afterEach(() => {
  cleanup();
});

describe("AppLogoIcon", () => {
  it("renders the Fastplace mark as a tall svg with two unfilled paths", () => {
    const { container } = render(<AppLogoIcon />);

    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg).toHaveAttribute("viewBox", "0 0 417.27 535.81");
    expect(svg).toHaveAttribute("xmlns", "http://www.w3.org/2000/svg");

    const paths = container.querySelectorAll("svg path");
    expect(paths).toHaveLength(2);
    // No fill of its own — color must come from the spread className
    // (fill-current) so every context inherits currentColor.
    expect(paths[0]).not.toHaveAttribute("fill");
    expect(paths[1]).not.toHaveAttribute("fill");
    expect(paths[0].getAttribute("d")).toContain("M0,405.17");
    expect(paths[1].getAttribute("d")).toContain("M392.13,292.2");
  });

  it("spreads svg attributes onto the root element", () => {
    const { container } = render(
      <AppLogoIcon className="size-5 fill-current" aria-hidden="true" />,
    );

    const svg = container.querySelector("svg");
    expect(svg).toHaveClass("size-5", "fill-current");
    expect(svg).toHaveAttribute("aria-hidden", "true");
  });
});
