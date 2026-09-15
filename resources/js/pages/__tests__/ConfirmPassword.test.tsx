import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import ConfirmPassword from "../Auth/ConfirmPassword";

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

function renderPage() {
  return render(
    <FastplaceProvider
      initialPage={{ component: "Auth/ConfirmPassword", url: "/user/confirm-password", props: {} }}
    >
      <ConfirmPassword />
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
 * Auth/ConfirmPassword
 * ------------------------------------------------------------------ */

describe("Auth/ConfirmPassword", () => {
  it("renders the passkey confirm section and sets the document title", () => {
    // jsdom ships no WebAuthn — install the feature-detect marker.
    vi.stubGlobal("PublicKeyCredential", class PublicKeyCredential {});
    renderPage();

    expect(screen.getByRole("button", { name: /confirm with passkey/i })).toBeInTheDocument();
    expect(screen.getByText("Or confirm with password")).toBeInTheDocument();
    expect(document.title).toBe("Confirm password");
  });

  it("omits the passkey section when WebAuthn is unavailable", () => {
    renderPage();

    expect(screen.queryByRole("button", { name: /confirm with passkey/i })).toBeNull();
    // The password path still renders.
    expect(screen.getByPlaceholderText("Password")).toBeInTheDocument();
  });

  it("runs the passkey CONFIRM ceremony against the confirm endpoints", async () => {
    vi.stubGlobal("PublicKeyCredential", class PublicKeyCredential {});
    Object.defineProperty(window.navigator, "credentials", {
      value: {
        get: vi.fn().mockResolvedValue({
          id: "cred-1",
          rawId: new Uint8Array([1]).buffer,
          type: "public-key",
          response: { clientDataJSON: new Uint8Array([2]).buffer },
        }),
      },
      configurable: true,
    });
    const visit = vi.spyOn(router, "visit").mockResolvedValue();
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/passkeys/confirm/options") return jsonResponse({ challenge: "AQIDBA" });
      return jsonResponse({ redirect: "/settings/security" });
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /confirm with passkey/i }));

    await waitFor(() => expect(visit).toHaveBeenCalled());
    // The confirm page must drive the CONFIRM ceremony, never the login one.
    expect(fetchCall(fetchMock, 0).url).toBe("/passkeys/confirm/options");
    expect(fetchCall(fetchMock, 1).url).toBe("/passkeys/confirm");
  });

  it("renders the password field as a masked input", () => {
    renderPage();

    // Password inputs expose no textbox role — reach it by placeholder.
    const input = screen.getByPlaceholderText("Password");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "current-password");
    expect(input).toHaveAttribute("name", "password");
    expect(screen.getByText("Password", { selector: "label" })).toBeInTheDocument();
  });

  it("posts the password to the confirm-password endpoint over the bridge", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByPlaceholderText("Password"), "secret-password");
    await user.click(screen.getByRole("button", { name: /confirm password/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const call = fetchCall(fetchMock, 0);
    expect(call.url).toBe("/user/confirm-password");
    expect(call.method).toBe("POST");
    expect(call.headers["X-Fastplace-Request"]).toBe("true");
    expect(call.body).toEqual({ password: "secret-password" });
  });

  it("shows the mapped field error when validation fails", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ errors: { password: ["The password field is required."] } }, false, 422),
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /confirm password/i }));

    expect(await screen.findByText("The password field is required.")).toBeInTheDocument();
  });

  it("clears the password field after a successful confirmation", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderPage();
    const input = screen.getByPlaceholderText("Password");
    await user.type(input, "secret-password");
    await user.click(screen.getByRole("button", { name: /confirm password/i }));

    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("exposes a static layout wrapping children in the auth layout", () => {
    expect(typeof ConfirmPassword.layout).toBe("function");
    render(
      <FastplaceProvider
        initialPage={{
          component: "Auth/ConfirmPassword",
          url: "/user/confirm-password",
          props: {},
        }}
      >
        <ConfirmPassword.layout>
          <p>Secure area content</p>
        </ConfirmPassword.layout>
      </FastplaceProvider>,
    );

    expect(screen.getByRole("heading", { level: 1, name: "Confirm password" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "This is a secure area of the application. Please confirm your password before continuing.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Secure area content")).toBeInTheDocument();
  });
});
