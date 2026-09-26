import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import PasskeyItem from "../passkey-item";
import type { Passkey } from "@/types/auth";

const basePasskey: Passkey = {
  id: 7,
  name: "My Yubikey",
  authenticator: "Chrome",
  created_at_diff: "2 weeks ago",
  last_used_at_diff: "yesterday",
};

afterEach(cleanup);

describe("PasskeyItem", () => {
  it("renders the passkey name, authenticator badge and usage line", () => {
    render(<PasskeyItem passkey={basePasskey} onDelete={vi.fn()} />);

    expect(screen.getByText("My Yubikey")).toBeInTheDocument();
    expect(screen.getByText("Chrome")).toBeInTheDocument();
    expect(screen.getByText(/Added 2 weeks ago/)).toBeInTheDocument();
    expect(screen.getByText(/Last used yesterday/)).toBeInTheDocument();
  });

  it("omits the badge and last-used hint when the passkey lacks them", () => {
    render(
      <PasskeyItem
        passkey={{ ...basePasskey, authenticator: null, last_used_at_diff: null }}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.queryByText("Chrome")).not.toBeInTheDocument();
    expect(screen.queryByText(/Last used/)).not.toBeInTheDocument();
    expect(screen.getByText("Added 2 weeks ago")).toBeInTheDocument();
  });

  it("opens a removal confirmation from the remove control and cancels cleanly", async () => {
    const user = userEvent.setup();
    render(<PasskeyItem passkey={basePasskey} onDelete={vi.fn()} />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove" }));

    const dialog = await screen.findByRole("dialog");
    expect(screen.getByRole("heading", { name: "Remove passkey" })).toBeInTheDocument();
    expect(dialog).toHaveTextContent(/"My Yubikey"/);

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("confirms deletion with the passkey id and settles the busy state via the callback", async () => {
    const onDelete = vi.fn();
    const user = userEvent.setup();
    render(<PasskeyItem passkey={basePasskey} onDelete={onDelete} />);

    await user.click(screen.getByRole("button", { name: "Remove" }));
    await user.click(await screen.findByRole("button", { name: "Remove passkey" }));

    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(onDelete).toHaveBeenCalledWith(7, expect.any(Function));

    const busy = screen.getByRole("button", { name: "Removing..." });
    expect(busy).toBeDisabled();

    act(() => {
      onDelete.mock.calls[0][1]();
    });
    expect(screen.getByRole("button", { name: "Remove passkey" })).toBeEnabled();
  });
});
