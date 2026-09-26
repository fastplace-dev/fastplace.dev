import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, applyLayouts, router } from "@fastplace/react";

import ResetPassword from "@/pages/Auth/ResetPassword";

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

const baseProps = {
  token: "tok-123",
  email: "user@example.com",
  passwordRules: "minlength:8;",
};

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

function renderPage() {
  const layout = (ResetPassword as unknown as { layout?: unknown }).layout;
  return render(
    <FastplaceProvider
      initialPage={{
        component: "Auth/ResetPassword",
        props: { ...baseProps },
        url: `/reset-password/${baseProps.token}`,
      }}
    >
      {applyLayouts(<ResetPassword />, layout)}
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
 * ResetPassword
 * ------------------------------------------------------------------ */

describe("ResetPassword", () => {
  it("declares a static layout function", () => {
    expect(typeof (ResetPassword as unknown as { layout?: unknown }).layout).toBe("function");
  });

  it("renders the auth layout chrome with prefilled read-only email and password fields", async () => {
    renderPage();

    // Layout chrome (title + description from the AuthLayout wrapper).
    expect(screen.getByRole("heading", { name: "Reset password" })).toBeInTheDocument();
    expect(screen.getByText("Please enter your new password below")).toBeInTheDocument();

    // Email arrives from the page props, read-only.
    const email = screen.getByLabelText("Email");
    expect(email).toHaveValue(baseProps.email);
    expect(email).toHaveAttribute("readonly");

    // Password fields render (no textbox role — select via label/placeholder).
    expect(screen.getByPlaceholderText("Password")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Confirm password")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm password")).toHaveValue("");

    const submit = screen.getByRole("button", { name: "Reset password" });
    expect(submit).toBeEnabled();
    expect(submit).toHaveAttribute("type", "submit");

    await waitFor(() => expect(document.title).toBe("Reset password"));
  });

  it("posts the token, email and new passwords to the mapped endpoint", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      mockJsonResponse({ ok: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await user.type(screen.getByLabelText("Password"), "new-secret");
    await user.type(screen.getByLabelText("Confirm password"), "new-secret");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const call = fetchCall(fetchMock, 0);
    expect(call.url).toBe("/reset-password");
    expect(call.method).toBe("POST");
    expect(call.headers["X-Fastplace-Request"]).toBe("true");
    expect(call.headers["Content-Type"]).toBe("application/json");
    expect(call.body).toEqual({
      token: baseProps.token,
      email: baseProps.email,
      password: "new-secret",
      password_confirmation: "new-secret",
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
              email: ["We could not find a user with that email address."],
              password: ["The password must be at least eight characters."],
            },
          },
          false,
          422,
        ),
      ),
    );

    renderPage();
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(
      await screen.findByText("We could not find a user with that email address."),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("The password must be at least eight characters."),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Reset password" })).toBeEnabled(),
    );
  });

  it("clears the password fields but keeps the email after a successful reset", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, _init?: RequestInit) => mockJsonResponse({ ok: true })),
    );

    renderPage();
    await user.type(screen.getByLabelText("Password"), "new-secret");
    await user.type(screen.getByLabelText("Confirm password"), "new-secret");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    await waitFor(() => {
      expect(screen.getByLabelText("Password")).toHaveValue("");
      expect(screen.getByLabelText("Confirm password")).toHaveValue("");
    });
    expect(screen.getByLabelText("Email")).toHaveValue(baseProps.email);
  });
});
