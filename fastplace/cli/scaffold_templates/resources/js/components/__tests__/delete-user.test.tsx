import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";

import DeleteUser from "../delete-user";

beforeEach(() => {
  // The bridge posts through window.fetch — stand in a controllable spy so
  // submissions never leave jsdom and calls can be asserted.
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockJsonResponse({ ok: true })));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function mockJsonResponse(payload: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    redirected: false,
    json: () => Promise.resolve(payload),
  } as unknown as Response;
}

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Delete account" }));
  return screen.findByRole("dialog");
}

describe("DeleteUser", () => {
  it("renders the section heading and the warning box", () => {
    render(<DeleteUser />);

    expect(screen.getByRole("heading", { level: 2, name: "Delete account" })).toBeInTheDocument();
    expect(screen.getByText("Delete your account and all of its resources")).toBeInTheDocument();
    expect(screen.getByText("Warning")).toBeInTheDocument();
    expect(
      screen.getByText("Please proceed with caution, this cannot be undone."),
    ).toBeInTheDocument();
  });

  it("keeps the confirmation dialog closed until the trigger is clicked", async () => {
    const user = userEvent.setup();
    render(<DeleteUser />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    const dialog = await openDialog(user);
    expect(
      within(dialog).getByText("Are you sure you want to delete your account?"),
    ).toBeInTheDocument();

    const passwordField = within(dialog).getByLabelText("Password");
    expect(passwordField).toHaveAttribute("name", "password");
    expect(passwordField).toHaveAttribute("autocomplete", "current-password");
  });

  it("submits the password as a DELETE request to the profile endpoint", async () => {
    const user = userEvent.setup();
    render(<DeleteUser />);

    await openDialog(user);
    await user.type(screen.getByLabelText("Password"), "hunter2");

    // The confirm control is flagged with data-test (not data-testid).
    const confirm = document.querySelector('[data-test="confirm-delete-user-button"]');
    expect(confirm).not.toBeNull();
    expect(confirm).toHaveAttribute("type", "submit");
    await user.click(confirm!);

    const fetchSpy = vi.mocked(globalThis.fetch);
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/settings/profile");
    expect(init.method).toBe("DELETE");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Fastplace-Request"]).toBe("true");
    expect(JSON.parse(String(init.body))).toEqual({ password: "hunter2" });
  });

  it("shows the validation message and focuses the password field on error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        mockJsonResponse(
          {
            message: "The password is incorrect.",
            errors: { password: ["The password is incorrect."] },
          },
          false,
          422,
        ),
      ),
    );
    const user = userEvent.setup();
    render(<DeleteUser />);

    await openDialog(user);
    await user.type(screen.getByLabelText("Password"), "wrong-pass");
    await user.click(document.querySelector('[data-test="confirm-delete-user-button"]')!);

    expect(await screen.findByText("The password is incorrect.")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveFocus();
  });

  it("closes the dialog from Cancel without submitting", async () => {
    const user = userEvent.setup();
    render(<DeleteUser />);

    await openDialog(user);
    await user.type(screen.getByLabelText("Password"), "hunter2");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(vi.mocked(globalThis.fetch)).not.toHaveBeenCalled();
  });
});
