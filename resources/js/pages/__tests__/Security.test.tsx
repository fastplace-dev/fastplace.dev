import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router, type Page } from "@fastplace/react";

import SecurityPage from "../Settings/Security";
import SettingsLayout from "@/layouts/settings/layout";
import type { Passkey } from "@/types/auth";

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

const baseProps = {
  passwordRules: "minlength:8;",
  canManageTwoFactor: false,
  canManagePasskeys: false,
};

function makePasskey(overrides: Partial<Passkey> = {}): Passkey {
  return {
    id: 1,
    name: "MacBook Pro",
    authenticator: null,
    created_at_diff: "1 day ago",
    last_used_at_diff: null,
    ...overrides,
  };
}

function mockJsonResponse(payload: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    redirected: false,
    json: () => Promise.resolve(payload),
  } as unknown as Response;
}

/** Typed reader for the stubbed fetch mock's recorded calls. */
function fetchCall(mock: ReturnType<typeof vi.fn>, index: number) {
  const [url, init] = mock.mock.calls[index] as [string, RequestInit];
  const headers = (init?.headers ?? {}) as Record<string, string>;
  return {
    url,
    method: init?.method,
    headers,
    body: typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : init?.body,
  };
}

function renderPage(props: Record<string, unknown> = {}) {
  const initialPage: Page = {
    component: "Settings/Security",
    props: { ...baseProps, ...props },
    url: "/settings/security",
  };
  return render(
    <FastplaceProvider initialPage={initialPage}>
      {/* Props arrive via the provider's initialPage, not component arguments. */}
      <SecurityPage />
    </FastplaceProvider>,
  );
}

beforeEach(() => {
  cleanup();
  document.title = "";
});

afterEach(() => {
  cleanup();
  router.reset();
  vi.unstubAllGlobals();
});

/* ------------------------------------------------------------------ *
 * Settings/Security
 * ------------------------------------------------------------------ */

describe("Settings/Security page", () => {
  it("sets the document title and renders the sr-only page heading", () => {
    renderPage();

    expect(document.title).toBe("Security settings");
    expect(
      screen.getByRole("heading", { level: 1, name: "Security settings" }),
    ).toBeInTheDocument();
  });

  it("renders the update password section with its three fields and save button", () => {
    renderPage();

    expect(screen.getByRole("heading", { level: 2, name: "Update password" })).toBeInTheDocument();
    expect(
      screen.getByText("Ensure your account is using a long, random password to stay secure"),
    ).toBeInTheDocument();

    // Password inputs expose no textbox role — select by label text.
    expect(screen.getByLabelText("Current password")).toHaveValue("");
    expect(screen.getByLabelText("New password")).toHaveValue("");
    expect(screen.getByLabelText("Confirm password")).toHaveValue("");

    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeEnabled();
  });

  it("submits the typed credentials as a PUT to /settings/password", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      mockJsonResponse({ ok: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await user.type(screen.getByLabelText("Current password"), "old-secret");
    await user.type(screen.getByLabelText("New password"), "new-secret");
    await user.type(screen.getByLabelText("Confirm password"), "new-secret");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const call = fetchCall(fetchMock, 0);
    expect(call.url).toBe("/settings/password");
    expect(call.method).toBe("PUT");
    expect(call.headers["X-Fastplace-Request"]).toBe("true");
    expect(call.headers["Content-Type"]).toBe("application/json");
    expect(call.body).toEqual({
      current_password: "old-secret",
      password: "new-secret",
      password_confirmation: "new-secret",
    });
  });

  it("resets the password fields after a successful save", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, _init?: RequestInit) => mockJsonResponse({ ok: true })),
    );

    renderPage();
    await user.type(screen.getByLabelText("Current password"), "old-secret");
    await user.type(screen.getByLabelText("New password"), "new-secret");
    await user.type(screen.getByLabelText("Confirm password"), "new-secret");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(screen.getByLabelText("Current password")).toHaveValue("");
      expect(screen.getByLabelText("New password")).toHaveValue("");
      expect(screen.getByLabelText("Confirm password")).toHaveValue("");
    });
  });

  it("maps 422 validation errors onto the matching fields", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, _init?: RequestInit) =>
        mockJsonResponse(
          {
            message: "The given data was invalid.",
            errors: {
              current_password: ["The current password is incorrect."],
              password: ["The password must be at least eight characters."],
            },
          },
          false,
          422,
        ),
      ),
    );

    renderPage();
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("The current password is incorrect.")).toBeInTheDocument();
    expect(
      await screen.findByText("The password must be at least eight characters."),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeEnabled());
  });

  it("focuses the new password input when it fails validation", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, _init?: RequestInit) =>
        mockJsonResponse(
          {
            message: "The given data was invalid.",
            errors: { password: ["The password must be at least eight characters."] },
          },
          false,
          422,
        ),
      ),
    );

    renderPage();
    await user.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("The password must be at least eight characters.");
    expect(document.activeElement).toBe(screen.getByLabelText("New password"));
  });

  it("hides the two-factor section when it cannot be managed", () => {
    renderPage({ canManageTwoFactor: false });

    expect(screen.queryByRole("heading", { name: "Two-factor authentication" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Enable 2FA" })).toBeNull();
  });

  it("offers enabling two-factor authentication when manageable but disabled", () => {
    renderPage({ canManageTwoFactor: true, twoFactorEnabled: false });

    expect(
      screen.getByRole("heading", { level: 2, name: "Two-factor authentication" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enable 2FA" })).toBeEnabled();
  });

  it("offers disabling two-factor authentication when it is enabled", () => {
    renderPage({ canManageTwoFactor: true, twoFactorEnabled: true });

    expect(screen.getByRole("button", { name: "Disable 2FA" })).toBeEnabled();
    expect(
      screen.getByText(/you will be prompted for a secure, random pin during login/i),
    ).toBeInTheDocument();
  });

  it("lists registered passkeys when passkey management is enabled", () => {
    renderPage({
      canManagePasskeys: true,
      passkeys: [
        makePasskey(),
        makePasskey({ id: 2, name: "iPhone", created_at_diff: "2 weeks ago" }),
      ],
    });

    expect(screen.getByRole("heading", { level: 2, name: "Passkeys" })).toBeInTheDocument();
    expect(screen.getByText("MacBook Pro")).toBeInTheDocument();
    expect(screen.getByText("iPhone")).toBeInTheDocument();
    expect(screen.getByText("Added 1 day ago")).toBeInTheDocument();
    expect(screen.queryByText("No passkeys yet")).toBeNull();
  });

  it("shows the empty passkey state when none are registered", () => {
    renderPage({ canManagePasskeys: true, passkeys: [] });

    expect(screen.getByText("No passkeys yet")).toBeInTheDocument();
    expect(screen.getByText("Add a passkey to sign in without a password")).toBeInTheDocument();
  });

  it("hides the passkeys section when passkey management is disabled", () => {
    renderPage({ canManagePasskeys: false, passkeys: [makePasskey()] });

    expect(screen.queryByRole("heading", { name: "Passkeys" })).toBeNull();
    expect(screen.queryByText("MacBook Pro")).toBeNull();
  });

  it("declares the settings layout pair as its static layout", () => {
    const layout = (SecurityPage as unknown as { layout?: unknown }).layout;

    expect(Array.isArray(layout)).toBe(true);
    const layouts = layout as unknown[];
    expect(layouts).toHaveLength(2);
    // Outer: the app sidebar shell wrapper; inner: the settings nav layout.
    expect(typeof layouts[0]).toBe("function");
    expect(layouts[1]).toBe(SettingsLayout);
  });
});
