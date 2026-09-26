import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { FastplaceProvider, router } from "@fastplace/react";

import TwoFactorChallenge from "../Auth/TwoFactorChallenge";

// jsdom ships no document.elementFromPoint — input-otp's password-manager
// badge probe calls it from a timer while the input is focused.
document.elementFromPoint = document.elementFromPoint ?? (() => null);

/** Bridge-shaped JSON answer, mirroring the form.test.tsx helper. */
function mockJsonResponse(payload: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    redirected: false,
    json: () => Promise.resolve(payload),
  } as unknown as Response;
}

beforeEach(() => {
  document.title = "";
});

afterEach(() => {
  cleanup();
  router.reset();
  vi.unstubAllGlobals();
});

/** Fully-typed fetch stand-in returning canned bridge responses. */
function stubFetch(responder: () => Response) {
  const mock = vi.fn(async (_url: string, _init?: RequestInit) => responder());
  vi.stubGlobal("fetch", mock);
  return mock;
}

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
  render(
    <FastplaceProvider
      initialPage={{
        component: "Auth/TwoFactorChallenge",
        url: "/two-factor-challenge",
        props: {},
      }}
    >
      <TwoFactorChallenge />
    </FastplaceProvider>,
  );
}

describe("TwoFactorChallenge", () => {
  it("renders code mode by default with six OTP slots and no static layout", () => {
    renderPage();

    expect(screen.getByRole("heading", { name: "Authentication code" })).toBeInTheDocument();
    expect(
      screen.getByText("Enter the authentication code provided by your authenticator application."),
    ).toBeInTheDocument();
    expect(document.querySelectorAll("[data-slot='input-otp-slot']")).toHaveLength(6);
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "login using a recovery code" })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Enter recovery code")).not.toBeInTheDocument();

    // Head sets the document title; the layout is inline, not a static wrapper.
    expect(document.title).toBe("Two-factor authentication");
    expect((TwoFactorChallenge as unknown as { layout?: unknown }).layout).toBeUndefined();
  });

  it("toggles to recovery-code mode with switched copy and a recovery input", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("button", { name: "login using a recovery code" }));

    expect(screen.getByRole("heading", { name: "Recovery code" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "Please confirm access to your account by entering one of your emergency recovery codes.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Enter recovery code")).toBeInTheDocument();
    expect(document.querySelectorAll("[data-slot='input-otp-slot']")).toHaveLength(0);
    expect(
      screen.getByRole("button", { name: "login using an authentication code" }),
    ).toBeInTheDocument();
  });

  it("toggles back to code mode from recovery mode", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("button", { name: "login using a recovery code" }));
    await user.click(screen.getByRole("button", { name: "login using an authentication code" }));

    expect(screen.getByRole("heading", { name: "Authentication code" })).toBeInTheDocument();
    expect(document.querySelectorAll("[data-slot='input-otp-slot']")).toHaveLength(6);
    expect(screen.queryByPlaceholderText("Enter recovery code")).not.toBeInTheDocument();
  });

  it("posts the authentication code through the bridge on submit", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch(() => mockJsonResponse({ ok: true }));
    renderPage();

    await user.type(screen.getByRole("textbox"), "123456");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const call = fetchCall(fetchMock, 0);
    expect(call.url).toBe("/two-factor-challenge");
    expect(call.method).toBe("POST");
    expect(call.headers["X-Fastplace-Request"]).toBe("true");
    expect(call.body).toEqual({ code: "123456" });
  });

  it("posts the recovery code through the bridge in recovery mode", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch(() => mockJsonResponse({ ok: true }));
    renderPage();

    await user.click(screen.getByRole("button", { name: "login using a recovery code" }));
    await user.type(screen.getByRole("textbox"), "ABCD-EFGH");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const call = fetchCall(fetchMock, 0);
    expect(call.url).toBe("/two-factor-challenge");
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({ recovery_code: "ABCD-EFGH" });
  });

  it("surfaces a rejected code as a field error and clears it on mode toggle", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch(() =>
      mockJsonResponse(
        {
          message: "Invalid.",
          errors: { code: ["The provided two factor authentication code is invalid."] },
        },
        false,
        422,
      ),
    );
    renderPage();

    await user.type(screen.getByRole("textbox"), "000000");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(
      await screen.findByText("The provided two factor authentication code is invalid."),
    ).toBeInTheDocument();

    // Switching modes clears the stale error along with the entered code.
    await user.click(screen.getByRole("button", { name: "login using a recovery code" }));
    expect(
      screen.queryByText("The provided two factor authentication code is invalid."),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recovery code" })).toBeInTheDocument();
  });
});
