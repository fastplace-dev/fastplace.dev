import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { PlaceholderPattern } from "../placeholder-pattern";

afterEach(cleanup);

describe("PlaceholderPattern", () => {
  it("renders an svg whose rect is filled by its own pattern", () => {
    const { container } = render(<PlaceholderPattern />);

    const svg = container.querySelector("svg");
    const pattern = container.querySelector("pattern");
    const rect = container.querySelector("rect");

    expect(svg).not.toBeNull();
    expect(svg?.getAttribute("data-slot")).toBe("placeholder-pattern");
    expect(pattern?.id).toBeTruthy();
    expect(pattern?.getAttribute("patternUnits")).toBe("userSpaceOnUse");
    expect(rect?.getAttribute("fill")).toBe(`url(#${pattern?.id})`);
  });

  it("generates a unique pattern id per instance", () => {
    const { container } = render(
      <React.Fragment>
        <PlaceholderPattern />
        <PlaceholderPattern />
      </React.Fragment>,
    );

    const ids = Array.from(container.querySelectorAll("pattern")).map((pattern) => pattern.id);
    expect(ids).toHaveLength(2);
    expect(ids[0]).not.toBe(ids[1]);
  });

  it("forwards the className onto the svg", () => {
    const { container } = render(<PlaceholderPattern className="h-full w-full" />);

    expect(container.querySelector("svg")?.getAttribute("class")).toContain("h-full");
    expect(container.querySelector("svg")?.getAttribute("class")).toContain("w-full");
  });
});
