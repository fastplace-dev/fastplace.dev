import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { Spinner } from "../spinner";

afterEach(cleanup);

describe("Spinner", () => {
  it("renders a status indicator with an accessible loading label", () => {
    render(<Spinner />);
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
  });

  it("applies the spin animation and the default icon size", () => {
    render(<Spinner />);
    const spinner = screen.getByRole("status");
    expect(spinner.getAttribute("class")).toContain("animate-spin");
    expect(spinner.getAttribute("class")).toContain("size-4");
  });

  it("lets a caller className override the default icon size", () => {
    render(<Spinner className="size-6" />);
    const spinner = screen.getByRole("status");
    expect(spinner.getAttribute("class")).toContain("size-6");
    expect(spinner.getAttribute("class")).not.toContain("size-4");
  });
});
