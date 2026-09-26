import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";
import { Sparkles } from "lucide-react";

import { Icon } from "../icon";

afterEach(cleanup);

describe("Icon", () => {
  it("renders the provided lucide icon node as an svg", () => {
    const { container } = render(<Icon iconNode={Sparkles} />);

    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute("data-slot")).toBe("icon");
  });

  it("forwards the className to the rendered icon", () => {
    const { container } = render(<Icon iconNode={Sparkles} className="size-5 text-ink-muted" />);

    expect(container.querySelector("svg")?.getAttribute("class")).toContain("size-5");
    expect(container.querySelector("svg")?.getAttribute("class")).toContain("text-ink-muted");
  });

  it("renders nothing when no icon node is given", () => {
    const { container } = render(<Icon />);

    expect(container.querySelector("svg")).toBeNull();
  });
});
