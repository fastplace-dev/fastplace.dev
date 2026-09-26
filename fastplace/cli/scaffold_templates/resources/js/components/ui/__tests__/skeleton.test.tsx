import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { Skeleton } from "../skeleton";

afterEach(cleanup);

describe("Skeleton", () => {
  it("renders a pulsing muted block that merges custom classes", () => {
    const { container } = render(<Skeleton className="h-10 w-full" />);

    const skeleton = container.querySelector("[data-slot='skeleton']");
    expect(skeleton).not.toBeNull();
    expect(skeleton?.className).toContain("animate-pulse");
    expect(skeleton?.className).toContain("bg-muted");
    expect(skeleton?.className).toContain("rounded-md");
    expect(skeleton?.className).toContain("h-10");
  });
});
