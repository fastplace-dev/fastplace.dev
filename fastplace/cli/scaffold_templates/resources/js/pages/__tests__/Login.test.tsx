import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import Login from "../Auth/Login";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// jsdom ships no ResizeObserver (the separator's size tracking needs one).
globalThis.ResizeObserver = globalThis.ResizeObserver ?? ResizeObserverStub;

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

function renderLogin(props: { status?: string; canResetPassword?: boolean } = {}) {
  // Production wiring: props ride through the page payload, never the
  // component — mirror exactly what the bridge bootstrap does.
  return render(
    <FastplaceProvider
      initialPage={{ component: "Auth/Login", url: "/login", props: { ...props } }}
    >
      <Login />
    </FastplaceProvider>,
  );
}

afterEach(() => {
  cleanup();
  router.reset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window.navigator as { credentials?: unknown }).credentials;
});

/* ------------------------------------------------------------------ *
 * Login page
 * ------------------------------------------------------------------ */

describe("Login page", () => {
  it("declares a layout wrapper", () => {
    expect(typeof Login.layout).toBe("function");
  });

  it("renders the email, password and remember-me fields with a submit button", () => {
    renderLogin();

    expect(screen.getByLabelText("Email address")).toBeInTheDocument();
    // Password inputs expose no textbox role — select through the label.
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByLabelText("Remember me")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log in" })).toBeInTheDocument();
    expect(screen.getByText("Don't have an account?")).toBeInTheDocument();
  });

  it("links to register and, when allowed, to forgot-password", () => {
    renderLogin({ canResetPassword: true });

    expect(screen.getByRole("link", { name: "Forgot your password?" })).toHaveAttribute(
      "href",
      "/forgot-password",
    );
    expect(screen.getByRole("link", { name: "Sign up" })).toHaveAttribute("href", "/register");
  });

  it("hides the forgot-password link when resetting is disabled", () => {
    renderLogin({ canResetPassword: false });

    expect(screen.queryByText("Forgot your password?")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign up" })).toBeInTheDocument();
  });

  it("renders the passkey block above the form when WebAuthn is supported", () => {
    vi.stubGlobal("PublicKeyCredential", class PublicKeyCredential {});
    renderLogin();

    expect(screen.getByRole("button", { name: /sign in with a passkey/i })).toBeInTheDocument();
    expect(screen.getByText("Or continue with email")).toBeInTheDocument();
    // The form's submit button still follows the passkey section.
    expect(screen.getByRole("button", { name: "Log in" })).toBeInTheDocument();
  });

  it("submits the filled form to /login through the bridge", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByLabelText("Email address"), "jane@example.com");
    await user.type(screen.getByLabelText("Password"), "secret123");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const call = fetchCall(fetchMock, 0);
    expect(call.url).toBe("/login");
    expect(call.method).toBe("POST");
    expect(call.headers["X-Fastplace-Request"]).toBe("true");
    expect(call.body).toEqual({ email: "jane@example.com", password: "secret123" });
  });

  it("includes remember: on when the remember-me checkbox is checked", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByLabelText("Email address"), "jane@example.com");
    await user.type(screen.getByLabelText("Password"), "secret123");
    await user.click(screen.getByLabelText("Remember me"));
    await user.click(screen.getByRole("button", { name: "Log in" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchCall(fetchMock, 0).body).toEqual({
      email: "jane@example.com",
      password: "secret123",
      remember: "on",
    });
  });

  it("maps 422 field errors onto the matching fields", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(
        {
          message: "The given data was invalid.",
          errors: { email: ["These credentials do not match our records."] },
        },
        false,
        422,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByLabelText("Email address"), "jane@example.com");
    await user.type(screen.getByLabelText("Password"), "secret123");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    expect(
      await screen.findByText("These credentials do not match our records."),
    ).toBeInTheDocument();
    // The user's input survives a failed submit.
    expect(screen.getByLabelText("Email address")).toHaveValue("jane@example.com");
  });

  it("resets only the password field after a successful submit", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByLabelText("Email address"), "jane@example.com");
    await user.type(screen.getByLabelText("Password"), "secret123");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByLabelText("Password")).toHaveValue(""));
    expect(screen.getByLabelText("Email address")).toHaveValue("jane@example.com");
  });

  it("shows the status flash message when present", () => {
    renderLogin({ status: "Your password has been reset." });
    // Theme contract: success flashes use the semantic success token so the
    // dark palette can adjust the shade (raw palette greens cannot).
    expect(screen.getByText("Your password has been reset.")).toHaveClass("text-success");

    expect(screen.getByText("Your password has been reset.")).toBeInTheDocument();
  });
});
