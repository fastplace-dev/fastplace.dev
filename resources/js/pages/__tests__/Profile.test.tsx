import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router, type Page } from "@fastplace/react";

import ProfilePage from "../Settings/Profile";
import SettingsLayout from "@/layouts/settings/layout";
import type { User } from "@/types";

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

function jsonResponse(payload: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    redirected: false,
    json: () => Promise.resolve(payload),
  } as unknown as Response;
}

/** Read fetch-mock call `index` as a typed view: url, method, headers, body. */
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

function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: 1,
    name: "Jane Doe",
    email: "jane@example.com",
    email_verified_at: "2026-09-01T10:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderProfile(
  props: { mustVerifyEmail?: boolean } = {},
  pageProps: Record<string, unknown> = {},
) {
  const initialPage: Page = {
    component: "Settings/Profile",
    url: "/settings/profile",
    props: pageProps,
  };
  return render(
    <FastplaceProvider initialPage={initialPage}>
      <ProfilePage mustVerifyEmail={props.mustVerifyEmail ?? false} />
    </FastplaceProvider>,
  );
}

afterEach(() => {
  cleanup();
  router.reset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/* ------------------------------------------------------------------ *
 * Settings/Profile page
 * ------------------------------------------------------------------ */

describe("Settings/Profile page", () => {
  it("declares the settings layout pair as its static layout", () => {
    const layout = (ProfilePage as unknown as { layout?: unknown }).layout;

    expect(Array.isArray(layout)).toBe(true);
    const layouts = layout as unknown[];
    expect(layouts).toHaveLength(2);
    // Outer: the app sidebar shell wrapper; inner: the settings nav layout.
    expect(typeof layouts[0]).toBe("function");
    expect(layouts[1]).toBe(SettingsLayout);
  });

  it("sets the document title and renders the sr-only page heading", () => {
    renderProfile();

    expect(document.title).toBe("Profile settings");
    expect(screen.getByRole("heading", { level: 1, name: "Profile settings" })).toBeInTheDocument();
  });

  it("renders the profile heading block with its description", () => {
    renderProfile();

    expect(screen.getByRole("heading", { level: 2, name: "Profile" })).toBeInTheDocument();
    expect(screen.getByText("Update your name and email address")).toBeInTheDocument();
  });

  it("prefills name and email from the auth user", () => {
    renderProfile({}, { auth: { user: makeUser() } });

    expect(screen.getByLabelText("Name")).toHaveValue("Jane Doe");
    expect(screen.getByLabelText("Email address")).toHaveValue("jane@example.com");
  });

  it("renders empty fields when no auth user is present yet", () => {
    renderProfile();

    expect(screen.getByLabelText("Name")).toHaveValue("");
    expect(screen.getByLabelText("Email address")).toHaveValue("");
  });

  it("submits the edited profile to /settings/profile via PATCH through the bridge", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderProfile({}, { auth: { user: makeUser() } });
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "Jane Smith");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const call = fetchCall(fetchMock, 0);
    expect(call.url).toBe("/settings/profile");
    expect(call.method).toBe("PATCH");
    expect(call.headers["X-Fastplace-Request"]).toBe("true");
    expect(call.body).toEqual({ name: "Jane Smith", email: "jane@example.com" });
  });

  it("marks the save button with the update-profile test hook", () => {
    renderProfile();

    expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute(
      "data-test",
      "update-profile-button",
    );
  });

  it("maps 422 field errors onto the matching fields", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(
        {
          message: "The given data was invalid.",
          errors: { email: ["The email has already been taken."] },
        },
        false,
        422,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderProfile({}, { auth: { user: makeUser() } });
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("The email has already been taken.")).toBeInTheDocument();
    // The user's input survives a failed submit.
    expect(screen.getByLabelText("Name")).toHaveValue("Jane Doe");
  });

  it("hides the verification notice when email verification is not required", () => {
    renderProfile(
      { mustVerifyEmail: false },
      { auth: { user: makeUser({ email_verified_at: null }) } },
    );

    expect(screen.queryByText(/unverified/i)).not.toBeInTheDocument();
  });

  it("shows the unverified notice and resend link when verification is pending", () => {
    renderProfile(
      { mustVerifyEmail: true },
      { auth: { user: makeUser({ email_verified_at: null }) } },
    );

    expect(screen.getByText(/Your email address is unverified/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /re-send the verification email/i })).toHaveAttribute(
      "href",
      "/email/verification-notification",
    );
    // No status flash until the backend reports a sent link.
    expect(screen.queryByText(/A new verification link has been sent/i)).not.toBeInTheDocument();
  });

  it("hides the verification notice for a user without auth data", () => {
    renderProfile({ mustVerifyEmail: true });

    expect(screen.queryByText(/unverified/i)).not.toBeInTheDocument();
  });

  it("shows the status flash after a verification link was sent", () => {
    renderProfile(
      { mustVerifyEmail: true },
      { auth: { user: makeUser({ email_verified_at: null }) }, status: "verification-link-sent" },
    );

    expect(
      screen.getByText("A new verification link has been sent to your email address."),
    ).toBeInTheDocument();
  });

  it("renders the delete account section from the shared component", () => {
    renderProfile();

    expect(screen.getByRole("heading", { level: 2, name: "Delete account" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete account" })).toBeInTheDocument();
  });
});
