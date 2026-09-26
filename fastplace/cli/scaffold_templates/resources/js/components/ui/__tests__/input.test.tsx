import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import { Input } from "../input";

afterEach(cleanup);

describe("Input", () => {
  it("renders a text input with the input slot attribute", () => {
    render(<Input />);
    const input = screen.getByRole("textbox");
    expect(input).toHaveAttribute("data-slot", "input");
  });

  it("passes the type prop through to the native input", () => {
    render(<Input type="email" />);
    expect(screen.getByRole("textbox")).toHaveAttribute("type", "email");
  });

  it("exposes its placeholder to accessibility queries", () => {
    render(<Input placeholder="you@example.com" />);
    expect(screen.getByPlaceholderText("you@example.com")).toBeInTheDocument();
  });

  it("lets a caller className override the default height", () => {
    render(<Input className="h-10" />);
    const input = screen.getByRole("textbox");
    expect(input.className).toContain("h-10");
    expect(input.className).not.toContain("h-9");
  });

  it("honours the disabled attribute", () => {
    render(<Input disabled />);
    expect(screen.getByRole("textbox")).toBeDisabled();
  });
});
