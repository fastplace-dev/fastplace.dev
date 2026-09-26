import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import VerifyEmail from "../Auth/VerifyEmail";

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

function renderPage(status?: string, element: React.ReactNode = <VerifyEmail />) {
  return render(
    <FastplaceProvider
      initialPage={{
        component: "Auth/VerifyEmail",
        url: "/email/verify",
        props: status === undefined ? {} : { status },
      }}
    >
      {element}
    </FastplaceProvider>,
  );
}

/** Render the page through its declared `.layout` static, as the bridge does. */
function renderWithLayout(status?: string) {
  const Layout = VerifyEmail.layout as (props: {
    children?: React.ReactNode;
  }) => React.ReactElement;
  return renderPage(
    status,
    <Layout>
      <VerifyEmail />
    </Layout>,
  );
}

afterEach(() => {
  cleanup();
  router.reset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.title = "";
});

/* ------------------------------------------------------------------ *
 * VerifyEmail
 * ------------------------------------------------------------------ */
describe("VerifyEmail", () => {
  it("declares a layout static wrapping the page in the auth layout", () => {
    expect(typeof VerifyEmail.layout).toBe("function");
  });

  it("renders the heading, description, resend button and log out link", () => {
    renderWithLayout();

    expect(
      screen.getByRole("heading", { level: 1, name: "Email verification" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Please verify your email address by clicking on the link we just emailed to you.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resend verification email/i })).toBeInTheDocument();

    const logout = screen.getByRole("link", { name: "Log out" });
    expect(logout).toHaveAttribute("href", "/logout");
  });

  it("sets the document title", () => {
    renderPage();

    expect(document.title).toBe("Email verification");
  });

  it("shows the resend confirmation only when the status flash is present", () => {
    renderWithLayout("verification-link-sent");

    const flash = screen.getByText(/A new verification link has been sent/);
    expect(flash).toBeInTheDocument();
    // Theme contract: success flashes use the semantic success token so the
    // dark palette can adjust the shade (raw palette greens cannot).
    expect(flash).toHaveClass("text-success");
  });

  it("hides the resend confirmation without a status flash", () => {
    renderWithLayout();

    expect(screen.queryByText(/a new verification link has been sent/i)).not.toBeInTheDocument();
  });

  it("resends through a bridge POST to the verification notification endpoint", async () => {
    let resolveFetch: (response: Response) => void = () => {};
    const fetchMock = vi.fn(
      (_url: string, _init?: RequestInit) =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderPage();
    await user.click(screen.getByRole("button", { name: /resend verification email/i }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const call = fetchCall(fetchMock, 0);
    expect(call.url).toBe("/email/verification-notification");
    expect(call.method).toBe("POST");
    expect(call.headers["X-Fastplace-Request"]).toBe("true");

    // While the request is in flight the button is disabled...
    const button = screen.getByRole("button", {
      name: /resend verification email/i,
    }) as HTMLButtonElement;
    expect(button).toBeDisabled();

    resolveFetch(jsonResponse({ ok: true }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /resend verification email/i })).toBeEnabled();
    });
  });

  it("logs out through a bridge POST to /logout", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ component: "Auth/Login", url: "/login", props: {} }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderPage();
    await user.click(screen.getByRole("link", { name: "Log out" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const call = fetchCall(fetchMock, 0);
    expect(call.url).toBe("/logout");
    expect(call.method).toBe("POST");
    expect(call.headers["X-Fastplace-Request"]).toBe("true");
  });
});
