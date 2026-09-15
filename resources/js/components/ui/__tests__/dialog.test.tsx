import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import React from "react";

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../dialog";

afterEach(cleanup);

function DemoDialog({ ...rootProps }) {
  return (
    <Dialog {...rootProps}>
      <DialogTrigger>Open dialog</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete project</DialogTitle>
          <DialogDescription>This permanently removes the project and its tasks.</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose>Cancel</DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

describe("Dialog", () => {
  it("keeps the dialog hidden until the trigger is clicked", async () => {
    const user = userEvent.setup();
    render(<DemoDialog />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open dialog" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("renders the open dialog with wired title and description", () => {
    render(<DemoDialog open />);

    const dialog = screen.getByRole("dialog");
    const title = screen.getByRole("heading", { name: "Delete project" });
    expect(dialog).toHaveAttribute("aria-labelledby", title.id);
    expect(
      screen.getByText("This permanently removes the project and its tasks."),
    ).toBeInTheDocument();
    // Portal content lives outside the render container, on document.body.
    expect(dialog.closest("body")).toBe(document.body);
  });

  it("renders an overlay and a labelled close button inside the dialog", () => {
    render(<DemoDialog open />);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(document.querySelector("[data-slot='dialog-overlay']")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });

  it("closes on the escape key", async () => {
    const user = userEvent.setup();
    render(<DemoDialog />);

    await user.click(screen.getByRole("button", { name: "Open dialog" }));
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("closes via the footer close control", async () => {
    const user = userEvent.setup();
    render(<DemoDialog />);

    await user.click(screen.getByRole("button", { name: "Open dialog" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("merges a caller className onto the content panel", () => {
    render(
      <Dialog open>
        <DialogContent className="max-w-3xl">
          <DialogTitle>Wide</DialogTitle>
          <DialogDescription>Something</DialogDescription>
        </DialogContent>
      </Dialog>,
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveClass("max-w-3xl");
    expect(dialog.className).toContain("sm:max-w-lg");
  });
});
