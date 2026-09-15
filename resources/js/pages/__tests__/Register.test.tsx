import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";

import { FastplaceProvider, applyLayouts, router } from "@fastplace/react";

import Register from "../Auth/Register";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// jsdom ships no ResizeObserver (some shared chrome needs one).
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

/** Render the page through its declared `.layout` static, as the bridge does. */
function renderRegister(props: Record<string, unknown> = {}) {
  return render(
    <FastplaceProvider initialPage={{ component: "Auth/Register", url: "/register", props }}>
      {applyLayouts(<Register />, Register.layout)}
    </FastplaceProvider>,
  );
}

beforeEach(() => {
  document.title = "";
});

afterEach(() => {
  cleanup();
  router.reset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/* ------------------------------------------------------------------ *
 * Register page
 * ------------------------------------------------------------------ */

describe("Register page", () => {
  it("declares a layout wrapper", () => {
    expect(typeof Register.layout).toBe("function");
  });

  it("renders the registration form inside the auth layout", () => {
    renderRegister({ passwordRules: "minlength: 12; require: lowercases;" });

    expect(
      screen.getByRole("heading", { level: 1, name: "Create an account" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Enter your details below to create your account")).toBeInTheDocument();

    expect(screen.getByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByLabelText("Email address")).toBeInTheDocument();
    // Password inputs expose no textbox role — select through the label.
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm password")).toBeInTheDocument();

    const submit = screen.getByRole("button", { name: "Create account" });
    expect(submit).toHaveAttribute("data-test", "register-user-button");

    expect(screen.getByText("Already have an account?")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Log in" })).toHaveAttribute("href", "/login");

    expect(document.title).toBe("Register");
  });

  it("masks the password fields and forwards the password rules", () => {
    renderRegister({ passwordRules: "minlength: 12; require: lowercases;" });

    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Confirm password")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Password")).toHaveAttribute(
      "passwordrules",
      "minlength: 12; require: lowercases;",
    );
    expect(screen.getByLabelText("Confirm password")).toHaveAttribute(
      "passwordrules",
      "minlength: 12; require: lowercases;",
    );
  });

  it("submits the filled form to /register through the bridge", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ ok: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderRegister();
    await user.type(screen.getByLabelText("Name"), "Jane Doe");
    await user.type(screen.getByLabelText("Email address"), "jane@example.com");
    await user.type(screen.getByLabelText("Password"), "secret123");
    await user.type(screen.getByLabelText("Confirm password"), "secret123");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const call = fetchCall(fetchMock, 0);
    expect(call.url).toBe("/register");
    expect(call.method).toBe("POST");
    expect(call.headers["X-Fastplace-Request"]).toBe("true");
    expect(call.body).toEqual({
      name: "Jane Doe",
      email: "jane@example.com",
      password: "secret123",
      password_confirmation: "secret123",
    });
  });

  it("resets only the password fields after a successful submit", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ ok: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderRegister();
    await user.type(screen.getByLabelText("Name"), "Jane Doe");
    await user.type(screen.getByLabelText("Email address"), "jane@example.com");
    await user.type(screen.getByLabelText("Password"), "secret123");
    await user.type(screen.getByLabelText("Confirm password"), "secret123");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByLabelText("Password")).toHaveValue(""));
    expect(screen.getByLabelText("Confirm password")).toHaveValue("");
    expect(screen.getByLabelText("Name")).toHaveValue("Jane Doe");
    expect(screen.getByLabelText("Email address")).toHaveValue("jane@example.com");
  });

  it("maps 422 field errors onto the matching fields", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse(
        {
          message: "The given data was invalid.",
          errors: {
            email: ["The email has already been taken."],
            password: ["The password must contain at least one symbol."],
          },
        },
        false,
        422,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderRegister();
    await user.type(screen.getByLabelText("Name"), "Jane Doe");
    await user.type(screen.getByLabelText("Email address"), "jane@example.com");
    await user.type(screen.getByLabelText("Password"), "secret123");
    await user.type(screen.getByLabelText("Confirm password"), "secret123");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("The email has already been taken.")).toBeInTheDocument();
    expect(screen.getByText("The password must contain at least one symbol.")).toBeInTheDocument();
    // The user's input survives a failed submit.
    expect(screen.getByLabelText("Email address")).toHaveValue("jane@example.com");
  });

  it("disables the submit button while the submission is in flight", async () => {
    let resolveFetch!: (response: Response) => void;
    const fetchMock = vi.fn(
      (_url: string, _init?: RequestInit) =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderRegister();
    await user.type(screen.getByLabelText("Name"), "Jane Doe");
    await user.type(screen.getByLabelText("Email address"), "jane@example.com");
    await user.type(screen.getByLabelText("Password"), "secret123");
    await user.type(screen.getByLabelText("Confirm password"), "secret123");
    const submit = screen.getByRole("button", { name: "Create account" });
    await user.click(submit);

    await waitFor(() => expect(submit).toBeDisabled());
    expect(screen.getByRole("status")).toBeInTheDocument();

    resolveFetch(jsonResponse({ ok: true }));
    await waitFor(() => expect(submit).toBeEnabled());
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
