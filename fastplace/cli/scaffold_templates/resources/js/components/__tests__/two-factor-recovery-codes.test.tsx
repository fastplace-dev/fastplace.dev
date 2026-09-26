import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";

import TwoFactorRecoveryCodes from "../two-factor-recovery-codes";

// jsdom ships no scrollIntoView — revealing the codes schedules a smooth
// scroll onto the section (same stub pattern as select.test).
beforeEach(() => {
  window.HTMLElement.prototype.scrollIntoView =
    window.HTMLElement.prototype.scrollIntoView ?? vi.fn();
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

const CODES = ["AB12-CD34", "EF56-GH78"];

function renderCodes({
  recoveryCodesList = CODES,
  fetchRecoveryCodes = vi.fn().mockResolvedValue(undefined),
  errors = [] as string[],
} = {}) {
  return render(
    <TwoFactorRecoveryCodes
      recoveryCodesList={recoveryCodesList}
      fetchRecoveryCodes={fetchRecoveryCodes}
      errors={errors}
    />,
  );
}

describe("TwoFactorRecoveryCodes", () => {
  it("renders the card with the codes section collapsed", () => {
    renderCodes();

    expect(screen.getByText("2FA recovery codes")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Recovery codes let you regain access if you lose your 2FA device. Store them in a secure password manager.",
      ),
    ).toBeInTheDocument();

    const toggle = screen.getByRole("button", { name: /View recovery codes/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", "recovery-codes-section");

    const section = document.getElementById("recovery-codes-section");
    expect(section).not.toBeNull();
    expect(section).toHaveAttribute("aria-hidden", "true");

    expect(screen.queryByRole("button", { name: /Regenerate codes/ })).not.toBeInTheDocument();
  });

  it("reveals the codes list on toggle and switches the button to Hide", async () => {
    const user = userEvent.setup();
    renderCodes();

    await user.click(screen.getByRole("button", { name: /View recovery codes/ }));

    const toggle = screen.getByRole("button", { name: /Hide recovery codes/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    const list = screen.getByRole("list", { name: "Recovery codes" });
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(list).toHaveTextContent(CODES[0]);
    expect(list).toHaveTextContent(CODES[1]);

    // With codes loaded and visible, the regenerate action appears.
    expect(screen.getByRole("button", { name: /Regenerate codes/ })).toHaveAttribute(
      "aria-describedby",
      "regenerate-warning",
    );
  });

  it("fetches codes on mount when the list is empty and shows skeletons", async () => {
    const fetchRecoveryCodes = vi.fn().mockResolvedValue(undefined);
    const { container } = renderCodes({
      recoveryCodesList: [],
      fetchRecoveryCodes,
    });

    await waitFor(() => expect(fetchRecoveryCodes).toHaveBeenCalledTimes(1));

    expect(screen.getByLabelText("Loading recovery codes")).toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse")).toHaveLength(8);

    // Codes never loaded — regenerate stays hidden even after revealing.
    await userEvent.click(screen.getByRole("button", { name: /View recovery codes/ }));
    expect(screen.queryByRole("button", { name: /Regenerate codes/ })).not.toBeInTheDocument();
  });

  it("posts to the recovery-codes endpoint on regenerate and refetches", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(mockJsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchSpy);
    const fetchRecoveryCodes = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderCodes({ fetchRecoveryCodes });

    await user.click(screen.getByRole("button", { name: /View recovery codes/ }));
    await user.click(screen.getByRole("button", { name: /Regenerate codes/ }));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/user/two-factor-recovery-codes");
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Fastplace-Request"]).toBe("true");
    expect(JSON.parse(String(init.body))).toEqual({});

    // The success callback refetches the fresh code list.
    await waitFor(() => expect(fetchRecoveryCodes).toHaveBeenCalledTimes(1));
  });

  it("renders the error alert instead of the codes when errors exist", async () => {
    const user = userEvent.setup();
    renderCodes({ errors: ["Two-factor authentication is not enabled."] });

    await user.click(screen.getByRole("button", { name: /View recovery codes/ }));

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong.");
    expect(alert).toHaveTextContent("Two-factor authentication is not enabled.");
  });
});
