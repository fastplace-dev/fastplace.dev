import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import PasswordInput from "../password-input";

afterEach(cleanup);

describe("PasswordInput", () => {
  it("renders a masked password input by default", () => {
    render(<PasswordInput name="password" placeholder="Enter your password" />);

    const input = screen.getByPlaceholderText("Enter your password");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("name", "password");
    expect(input).toHaveAttribute("data-slot", "input");
  });

  it("starts with a Show password toggle that is keyboard-excluded", () => {
    render(<PasswordInput />);

    const toggle = screen.getByRole("button", { name: "Show password" });
    expect(toggle).toHaveAttribute("type", "button");
    expect(toggle).toHaveAttribute("tabindex", "-1");
  });

  it("switches the input type and label when toggled, and back again", async () => {
    const user = userEvent.setup();
    render(<PasswordInput placeholder="Password" />);

    const input = screen.getByPlaceholderText("Password");

    await user.click(screen.getByRole("button", { name: "Show password" }));
    expect(input).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Hide password" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Hide password" }));
    expect(input).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Show password" })).toBeInTheDocument();
  });

  it("keeps the typed value when the password is revealed", async () => {
    const user = userEvent.setup();
    render(<PasswordInput placeholder="Password" />);

    const input = screen.getByPlaceholderText("Password");
    await user.type(input, "s3cret-value");
    await user.click(screen.getByRole("button", { name: "Show password" }));

    expect(input).toHaveValue("s3cret-value");
    expect(input).toHaveAttribute("type", "text");
  });

  it("merges the caller className after the reserved toggle padding", () => {
    render(<PasswordInput className="h-10" placeholder="Password" />);

    const { className } = screen.getByPlaceholderText("Password");
    expect(className).toContain("pr-10");
    expect(className).toContain("h-10");
  });

  it("forwards a ref onto the underlying input", () => {
    const ref = React.createRef<HTMLInputElement>();
    render(<PasswordInput ref={ref} placeholder="Password" />);

    expect(ref.current).toBe(screen.getByPlaceholderText("Password"));
  });

  it("renders the reveal icon inside the toggle button", () => {
    render(<PasswordInput />);

    const toggle = screen.getByRole("button", { name: "Show password" });
    expect(toggle.querySelector("svg")).toBeInTheDocument();
  });
});
