import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import { FastplaceProvider, router } from "@fastplace/react";

import ManagePasskeys from "../manage-passkeys";
import type { Passkey } from "@/types/auth";

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

/** jsdom ships no WebAuthn — install the feature-detect marker. */
function stubWebAuthnSupport() {
  vi.stubGlobal("PublicKeyCredential", class PublicKeyCredential {});
}

const passkeys: Passkey[] = [
  {
    id: 7,
    name: "My Yubikey",
    authenticator: "Chrome",
    created_at_diff: "2 weeks ago",
    last_used_at_diff: "yesterday",
  },
  {
    id: 12,
    name: "iPhone",
    authenticator: null,
    created_at_diff: "3 days ago",
    last_used_at_diff: null,
  },
];

function renderWithProvider(ui: React.ReactNode) {
  return render(
    <FastplaceProvider
      initialPage={{ component: "Settings/Security", props: {}, url: "/settings/security" }}
    >
      {ui}
    </FastplaceProvider>,
  );
}

afterEach(() => {
  cleanup();
  router.reset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window.navigator as { credentials?: unknown }).credentials;
  delete (window.navigator as { userAgent?: string }).userAgent;
});

/* ------------------------------------------------------------------ *
 * ManagePasskeys
 * ------------------------------------------------------------------ */

describe("ManagePasskeys", () => {
  it("renders nothing when passkey management is not permitted", () => {
    const disabled = render(<ManagePasskeys canManagePasskeys={false} passkeys={passkeys} />);
    expect(disabled.container).toBeEmptyDOMElement();

    const omitted = render(<ManagePasskeys passkeys={passkeys} />);
    expect(omitted.container).toBeEmptyDOMElement();
  });

  it("renders the heading and the empty state when no passkeys exist", () => {
    renderWithProvider(<ManagePasskeys canManagePasskeys />);

    expect(screen.getByRole("heading", { name: "Passkeys" })).toBeInTheDocument();
    expect(screen.getByText("Manage your passkeys for passwordless sign-in")).toBeInTheDocument();
    expect(screen.getByText("No passkeys yet")).toBeInTheDocument();
    expect(screen.getByText("Add a passkey to sign in without a password")).toBeInTheDocument();
  });

  it("lists one item per passkey", () => {
    renderWithProvider(<ManagePasskeys canManagePasskeys passkeys={passkeys} />);

    expect(screen.getByText("My Yubikey")).toBeInTheDocument();
    expect(screen.getByText("iPhone")).toBeInTheDocument();
    expect(screen.queryByText("No passkeys yet")).not.toBeInTheDocument();
  });

  it("deletes through the bridge against the interpolated item URL", async () => {
    stubWebAuthnSupport();
    Object.defineProperty(window.navigator, "userAgent", {
      value: "opaque-client/1.0",
      configurable: true,
    });
    const fetchMock = vi.fn(async (_url: string) => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderWithProvider(<ManagePasskeys canManagePasskeys passkeys={passkeys} />);

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    await user.click(await screen.findByRole("button", { name: "Remove passkey" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const call = fetchCall(fetchMock, 0);
    expect(call.url).toBe("/user/passkeys/7");
    expect(call.method).toBe("DELETE");
    expect(call.headers["X-Fastplace-Request"]).toBe("true");
  });

  it("settles the item busy state when the delete is rejected", async () => {
    stubWebAuthnSupport();
    Object.defineProperty(window.navigator, "userAgent", {
      value: "opaque-client/1.0",
      configurable: true,
    });
    // Deferred response keeps the busy window observable.
    let respond!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      respond = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => pending),
    );

    const user = userEvent.setup();
    renderWithProvider(<ManagePasskeys canManagePasskeys passkeys={passkeys} />);

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    const confirm = await screen.findByRole("button", { name: "Remove passkey" });
    await user.click(confirm);
    expect(confirm).toBeDisabled(); // busy while the request is in flight
    expect(confirm).toHaveTextContent("Removing...");

    // The 422 triggers the onError callback, which un-busies the item.
    respond(
      jsonResponse(
        { message: "The given data was invalid.", errors: { id: ["Failed."] } },
        false,
        422,
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Remove passkey" })).toBeEnabled(),
    );
  });

  it("reloads the current page after a successful registration", async () => {
    stubWebAuthnSupport();
    Object.defineProperty(window.navigator, "userAgent", {
      value: "opaque-client/1.0",
      configurable: true,
    });
    Object.defineProperty(window.navigator, "credentials", {
      value: {
        create: vi.fn().mockResolvedValue({
          id: "cred-1",
          rawId: new Uint8Array([1]).buffer,
          type: "public-key",
          response: { clientDataJSON: new Uint8Array([2]).buffer },
        }),
      },
      configurable: true,
    });
    const visit = vi.spyOn(router, "visit").mockResolvedValue();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/user/passkeys/options") return jsonResponse({ challenge: "AQIDBA" });
        return jsonResponse({ ok: true });
      }),
    );

    const user = userEvent.setup();
    renderWithProvider(<ManagePasskeys canManagePasskeys passkeys={passkeys} />);

    await user.click(screen.getByRole("button", { name: "Add passkey" }));
    await user.type(screen.getByLabelText("Passkey name"), "MacBook Pro");
    await user.click(screen.getByRole("button", { name: "Register passkey" }));

    await waitFor(() => expect(visit).toHaveBeenCalledWith("/settings/security"));
  });
});
