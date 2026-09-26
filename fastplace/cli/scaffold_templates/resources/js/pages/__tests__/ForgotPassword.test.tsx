import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import ForgotPassword from "../Auth/ForgotPassword";

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

/**
 * Read fetch-mock call `index` as a typed view: url, method, headers as a
 * plain record, and body parsed from JSON when it is a string.
 */
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

function renderPage(extraProps: Record<string, unknown> = {}) {
  return render(
    <FastplaceProvider
      initialPage={{
        component: "Auth/ForgotPassword",
        url: "/forgot-password",
        props: { name: "fastplace", sidebarOpen: false, ...extraProps },
      }}
    >
      <ForgotPassword />
    </FastplaceProvider>,
  );
}

afterEach(() => {
  cleanup();
  router.reset();
  document.title = "";
  vi.unstubAllGlobals();
});

/* ------------------------------------------------------------------ *
 * Tests
 * ------------------------------------------------------------------ */

describe("ForgotPassword", () => {
  it("renders the email form, sets the document title, and links back to login", () => {
    renderPage();

    expect(screen.getByLabelText("Email address")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("email@example.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /email password reset link/i })).toBeInTheDocument();

    const loginLink = screen.getByRole("link", { name: /log in/i });
    expect(loginLink).toHaveAttribute("href", "/login");
    expect(screen.getByText("Or, return to")).toBeInTheDocument();

    expect(document.title).toBe("Forgot password");
  });

  it("declares a static layout wrapper for the auth chrome", () => {
    expect(typeof ForgotPassword.layout).toBe("function");
  });

  it("shows the flash status message from page props", () => {
    renderPage({ status: "We have emailed your password reset link." });

    expect(screen.getByText("We have emailed your password reset link.")).toBeInTheDocument();
  });

  it("does not show a status message when the prop is absent", () => {
    renderPage();

    expect(screen.queryByText(/emailed/i)).not.toBeInTheDocument();
  });

  it("submits the email to /forgot-password over the bridge", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ status: "We have emailed your password reset link." }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await user.type(screen.getByLabelText("Email address"), "user@example.com");
    await user.click(screen.getByRole("button", { name: /email password reset link/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    const call = fetchCall(fetchMock, 0);
    expect(call.url).toBe("/forgot-password");
    expect(call.method).toBe("POST");
    expect(call.headers["X-Fastplace-Request"]).toBe("true");
    expect(call.body).toEqual({ email: "user@example.com" });
  });

  it("shows field errors from a 422 response", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse(
        { errors: { email: ["We can't find a user with that email address."] } },
        false,
        422,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await user.type(screen.getByLabelText("Email address"), "unknown@example.com");
    await user.click(screen.getByRole("button", { name: /email password reset link/i }));

    expect(
      await screen.findByText("We can't find a user with that email address."),
    ).toBeInTheDocument();
  });
});
