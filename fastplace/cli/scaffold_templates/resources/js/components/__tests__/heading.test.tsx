import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import Heading from "../heading";

afterEach(() => {
  cleanup();
});

describe("Heading", () => {
  it("renders the title as a level-two heading", () => {
    render(<Heading title="Profile settings" />);

    const heading = screen.getByRole("heading", { level: 2, name: "Profile settings" });
    expect(heading.tagName).toBe("H2");
  });

  it("renders the description as muted body text", () => {
    render(<Heading title="Profile settings" description="Update your personal details." />);

    const description = screen.getByText("Update your personal details.");
    expect(description.tagName).toBe("P");
    expect(description).toHaveClass("text-ink-muted", "text-sm");
  });

  it("omits the description paragraph when none is given", () => {
    const { container } = render(<Heading title="Profile settings" />);

    expect(container.querySelector("header p")).toBeNull();
  });

  it("styles the default variant with spacious spacing", () => {
    render(<Heading title="Profile settings" description="Update your personal details." />);

    const header = document.querySelector("header");
    expect(header).toHaveClass("mb-8", "space-y-0.5");

    const heading = screen.getByRole("heading", { level: 2 });
    expect(heading).toHaveClass("text-xl", "font-semibold", "tracking-tight");
  });

  it("styles the small variant as a compact subsection label", () => {
    render(
      <Heading
        title="API tokens"
        description="Manage the tokens used to authenticate requests."
        variant="small"
      />,
    );

    const header = document.querySelector("header");
    expect(header).not.toHaveClass("mb-8");

    const heading = screen.getByRole("heading", { level: 2 });
    expect(heading).toHaveClass("text-base", "font-medium");
    expect(heading).not.toHaveClass("text-xl");
  });
});
