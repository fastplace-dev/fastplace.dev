import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import { InputOTP, InputOTPGroup, InputOTPSeparator, InputOTPSlot } from "../input-otp";

// jsdom ships no document.elementFromPoint — input-otp's password-manager
// badge probe calls it from a timer while the input is focused.
document.elementFromPoint = document.elementFromPoint ?? (() => null);

afterEach(cleanup);

function DemoInputOTP({
  value = "12",
  onChange,
}: {
  value?: string;
  onChange?: (value: string) => void;
}) {
  return (
    <InputOTP maxLength={4} value={value} onChange={onChange} aria-label="One-time code">
      <InputOTPGroup>
        <InputOTPSlot index={0} />
        <InputOTPSlot index={1} />
        <InputOTPSlot index={2} />
        <InputOTPSlot index={3} />
      </InputOTPGroup>
    </InputOTP>
  );
}

describe("InputOTP", () => {
  it("mounts one slot per digit and mirrors the controlled value into them", () => {
    render(<DemoInputOTP value="12" />);

    const slots = document.querySelectorAll("[data-slot='input-otp-slot']");
    expect(slots).toHaveLength(4);
    expect(slots[0].textContent).toBe("1");
    expect(slots[1].textContent).toBe("2");
    expect(slots[2].textContent).toBe("");

    const container = document.querySelector("[data-input-otp-container]");
    expect(container?.className).toContain("flex");
    expect(container?.className).toContain("gap-2");

    const input = screen.getByRole("textbox", { name: /one-time code/i });
    expect(input).toHaveAttribute("maxlength", "4");
  });

  it("reports typed characters through onChange in a controlled flow", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    function ControlledDemo() {
      const [value, setValue] = React.useState("");
      return (
        <DemoInputOTP
          value={value}
          onChange={(next) => {
            setValue(next);
            onChange(next);
          }}
        />
      );
    }
    render(<ControlledDemo />);

    await user.type(screen.getByRole("textbox", { name: /one-time code/i }), "21");

    expect(onChange).toHaveBeenLastCalledWith("21");
    const slots = document.querySelectorAll("[data-slot='input-otp-slot']");
    expect(slots[0].textContent).toBe("2");
    expect(slots[1].textContent).toBe("1");
  });

  it("renders a separator part wrapping an icon", () => {
    render(<InputOTPSeparator />);

    const separator = screen.getByRole("separator");
    expect(separator).toHaveAttribute("data-slot", "input-otp-separator");
    expect(separator.querySelector("svg")).not.toBeNull();
  });
});
