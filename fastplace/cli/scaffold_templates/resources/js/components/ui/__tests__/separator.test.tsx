import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { Separator } from "../separator";

afterEach(cleanup);

function root(): HTMLElement {
  return document.querySelector("[data-slot='separator-root']") as HTMLElement;
}

describe("Separator", () => {
  it("renders a horizontal full-width rule by default", () => {
    render(<Separator />);

    const separator = root();
    expect(separator).not.toBeNull();
    expect(separator).toHaveAttribute("data-orientation", "horizontal");
    expect(separator.className).toContain("w-full");
    expect(separator.className).toContain("h-px");
  });

  it("renders a vertical full-height rule", () => {
    render(<Separator orientation="vertical" />);

    const separator = root();
    expect(separator).toHaveAttribute("data-orientation", "vertical");
    expect(separator.className).toContain("h-full");
    expect(separator.className).toContain("w-px");
  });

  it("stays decorative by default and exposes the separator role only when asked", () => {
    const { unmount } = render(<Separator />);
    // Decorative separators are hidden from the accessibility tree.
    expect(root()).toHaveAttribute("role", "none");
    unmount();

    render(<Separator decorative={false} />);
    expect(root()).toHaveAttribute("role", "separator");
  });
});
