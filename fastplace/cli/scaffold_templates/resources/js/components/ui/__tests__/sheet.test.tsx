import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "../sheet";

afterEach(cleanup);

function DemoSheet({ ...rootProps }) {
  return (
    <Sheet {...rootProps}>
      <SheetTrigger>Open sheet</SheetTrigger>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>Edit profile</SheetTitle>
          <SheetDescription>Make changes to your public profile.</SheetDescription>
        </SheetHeader>
        <SheetFooter>
          <SheetClose>Cancel</SheetClose>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

describe("Sheet", () => {
  it("keeps the sheet hidden until the trigger is clicked, then wires title and description", async () => {
    const user = userEvent.setup();
    render(<DemoSheet />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open sheet" }));

    const sheet = await screen.findByRole("dialog");
    expect(sheet).toBeInTheDocument();
    const title = screen.getByRole("heading", { name: "Edit profile" });
    expect(sheet).toHaveAttribute("aria-labelledby", title.id);
    expect(screen.getByText("Make changes to your public profile.")).toBeInTheDocument();
    // Portal content lives outside the render container, on document.body.
    expect(sheet.closest("body")).toBe(document.body);
  });

  it("places the content on the right by default", () => {
    render(
      <Sheet open>
        <SheetTrigger>Open sheet</SheetTrigger>
        <SheetContent>
          <SheetTitle>Right</SheetTitle>
          <SheetDescription>Default side.</SheetDescription>
        </SheetContent>
      </Sheet>,
    );

    const sheet = screen.getByRole("dialog");
    expect(sheet.className).toContain("slide-in-from-right");
    expect(sheet).toHaveClass("right-0");
  });

  it("places the content on the left when side is set to left", () => {
    render(
      <Sheet open>
        <SheetTrigger>Open sheet</SheetTrigger>
        <SheetContent side="left">
          <SheetTitle>Left</SheetTitle>
          <SheetDescription>Alternate side.</SheetDescription>
        </SheetContent>
      </Sheet>,
    );

    const sheet = screen.getByRole("dialog");
    expect(sheet.className).toContain("slide-in-from-left");
    expect(sheet).toHaveClass("left-0");
  });

  it("renders an overlay while open", () => {
    render(<DemoSheet open />);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(document.querySelector("[data-slot='sheet-overlay']")).not.toBeNull();
  });

  it("closes on the escape key", async () => {
    const user = userEvent.setup();
    render(<DemoSheet />);

    await user.click(screen.getByRole("button", { name: "Open sheet" }));
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("merges a caller className onto the content panel", () => {
    render(
      <Sheet open>
        <SheetTrigger>Open sheet</SheetTrigger>
        <SheetContent className="w-full">
          <SheetTitle>Wide</SheetTitle>
          <SheetDescription>Something</SheetDescription>
        </SheetContent>
      </Sheet>,
    );

    const sheet = screen.getByRole("dialog");
    expect(sheet).toHaveClass("w-full");
    // The caller width replaces the default side width via tailwind-merge.
    expect(sheet.className).not.toContain("w-3/4");
  });
});
