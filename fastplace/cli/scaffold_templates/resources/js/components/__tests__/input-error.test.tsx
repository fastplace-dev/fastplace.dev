import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import InputError from "../input-error";

afterEach(cleanup);

describe("InputError", () => {
  it("renders the message in a paragraph when provided", () => {
    render(<InputError message="The email field is required." />);

    const error = screen.getByText("The email field is required.");
    expect(error.tagName).toBe("P");
  });

  it("renders nothing when the message is missing or empty", () => {
    const { container, rerender } = render(<InputError />);

    expect(container).toBeEmptyDOMElement();

    rerender(<InputError message="" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("colors the message with the scheme-aware danger token", () => {
    render(<InputError message="Invalid credentials" />);

    expect(screen.getByText("Invalid credentials").className).toContain("text-danger");
  });

  it("keeps the size class and merges a caller className after it", () => {
    render(<InputError message="Too short" className="mt-2 text-center" />);

    const { className } = screen.getByText("Too short");
    expect(className).toContain("text-sm");
    expect(className).toContain("mt-2");
    expect(className).toContain("text-center");
  });

  it("spreads the remaining paragraph attributes onto the element", () => {
    render(<InputError message="Wrong password" id="password-error" data-testid="field-error" />);

    const error = screen.getByTestId("field-error");
    expect(error).toHaveAttribute("id", "password-error");
  });
});
