import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import PasskeyRegistration from "../passkey-register";

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

const fakeCredential = () => ({
  id: "cred-1",
  rawId: new Uint8Array([1, 2, 3]).buffer,
  type: "public-key",
  response: {
    attestationObject: new Uint8Array([9]).buffer,
    clientDataJSON: new Uint8Array([4, 5]).buffer,
    transports: ["internal"],
  },
  clientExtensionResults: {},
  authenticatorAttachment: "platform",
});

/** Stand in for navigator.credentials (missing in jsdom, not writable). */
function stubCredentialsCreate(credential: unknown) {
  const create = vi.fn().mockResolvedValue(credential);
  Object.defineProperty(window.navigator, "credentials", {
    value: { create },
    configurable: true,
  });
  return create;
}

/** Pin navigator.userAgent so the default passkey name is deterministic. */
function stubUserAgent(ua: string) {
  Object.defineProperty(window.navigator, "userAgent", {
    value: ua,
    configurable: true,
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window.navigator as { credentials?: unknown }).credentials;
  delete (window.navigator as { userAgent?: string }).userAgent;
});

/* ------------------------------------------------------------------ *
 * PasskeyRegistration
 * ------------------------------------------------------------------ */

describe("PasskeyRegistration", () => {
  it("reports unsupported browsers instead of the add button", () => {
    render(<PasskeyRegistration onSuccess={vi.fn()} />);

    expect(screen.getByText("Passkeys are not supported in this browser.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add passkey" })).not.toBeInTheDocument();
  });

  it("reveals the named form from the add button and keeps submit disabled while unnamed", async () => {
    stubWebAuthnSupport();
    stubUserAgent("opaque-client/1.0");
    const user = userEvent.setup();
    render(<PasskeyRegistration onSuccess={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Add passkey" }));

    expect(screen.getByLabelText("Passkey name")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("e.g., MacBook Pro, iPhone")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Register passkey" })).toBeDisabled();

    await user.type(screen.getByLabelText("Passkey name"), "Yubikey 5");
    expect(screen.getByRole("button", { name: "Register passkey" })).toBeEnabled();
  });

  it("derives a default passkey name from the browser user agent", async () => {
    stubWebAuthnSupport();
    stubUserAgent(
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    );
    const user = userEvent.setup();
    render(<PasskeyRegistration onSuccess={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Add passkey" }));

    expect(screen.getByLabelText("Passkey name")).toHaveValue("Chrome on Mac");
  });

  it("runs the ceremony: options fetch, credential creation, bridge POST, onSuccess", async () => {
    stubWebAuthnSupport();
    stubUserAgent("opaque-client/1.0");
    const create = stubCredentialsCreate(fakeCredential());
    const onSuccess = vi.fn();
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/user/passkeys/options") {
        return jsonResponse({
          // base64url bytes [1,2,3,4] — JSON cannot carry the buffers
          // navigator.credentials.create needs, the component must decode.
          challenge: "AQIDBA",
          rp: { name: "Fastplace" },
          user: { id: "AQIDBA", name: "u", displayName: "U" },
          excludeCredentials: [{ id: "AQIDBA", type: "public-key" }],
        });
      }
      return jsonResponse({ ok: true });
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<PasskeyRegistration onSuccess={onSuccess} />);
    await user.click(screen.getByRole("button", { name: "Add passkey" }));
    await user.type(screen.getByLabelText("Passkey name"), "Yubikey 5");
    await user.click(screen.getByRole("button", { name: "Register passkey" }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));

    const options = fetchCall(fetchMock, 0);
    expect(options.url).toBe("/user/passkeys/options");
    expect(options.headers.Accept).toBe("application/json");

    const decoded = new Uint8Array([1, 2, 3, 4]);
    expect(create).toHaveBeenCalledWith({
      publicKey: {
        challenge: decoded,
        rp: { name: "Fastplace" },
        user: { id: decoded, name: "u", displayName: "U" },
        excludeCredentials: [{ id: decoded, type: "public-key" }],
      },
    });

    const store = fetchCall(fetchMock, 1);
    expect(store.url).toBe("/user/passkeys");
    expect(store.method).toBe("POST");
    expect(store.headers["X-Fastplace-Request"]).toBe("true");
    expect(store.body).toEqual({
      name: "Yubikey 5",
      credential: {
        id: "cred-1",
        rawId: "AQID",
        type: "public-key",
        response: {
          attestationObject: "CQ",
          clientDataJSON: "BAU",
          transports: ["internal"],
        },
        clientExtensionResults: {},
        authenticatorAttachment: "platform",
      },
    });

    // Resting state again — form hidden behind the add button.
    expect(screen.getByRole("button", { name: "Add passkey" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Passkey name")).not.toBeInTheDocument();
  });

  it("surfaces a backend field error when the store endpoint rejects with 422", async () => {
    stubWebAuthnSupport();
    stubUserAgent("opaque-client/1.0");
    stubCredentialsCreate(fakeCredential());
    const onSuccess = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/user/passkeys/options") return jsonResponse({ challenge: "AQIDBA" });
        return jsonResponse(
          {
            message: "The given data was invalid.",
            errors: { name: ["The name has already been taken."] },
          },
          false,
          422,
        );
      }),
    );

    const user = userEvent.setup();
    render(<PasskeyRegistration onSuccess={onSuccess} />);
    await user.click(screen.getByRole("button", { name: "Add passkey" }));
    await user.type(screen.getByLabelText("Passkey name"), "Yubikey 5");
    await user.click(screen.getByRole("button", { name: "Register passkey" }));

    expect(await screen.findByText("The name has already been taken.")).toBeInTheDocument();
    expect(onSuccess).not.toHaveBeenCalled();
    // The form stays open so the user can retry.
    expect(screen.getByLabelText("Passkey name")).toBeInTheDocument();
  });

  it("shows a friendly error and recovers when the ceremony is aborted", async () => {
    stubWebAuthnSupport();
    stubUserAgent("opaque-client/1.0");
    const create = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error("not allowed"), { name: "NotAllowedError" }));
    Object.defineProperty(window.navigator, "credentials", {
      value: { create },
      configurable: true,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ challenge: "c" })),
    );

    const user = userEvent.setup();
    render(<PasskeyRegistration onSuccess={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Add passkey" }));
    await user.type(screen.getByLabelText("Passkey name"), "Yubikey 5");
    await user.click(screen.getByRole("button", { name: "Register passkey" }));

    expect(
      await screen.findByText("Unable to register this passkey. Please try again."),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Register passkey" })).toBeEnabled(),
    );
  });

  it("cancel hides the form again", async () => {
    stubWebAuthnSupport();
    const user = userEvent.setup();
    render(<PasskeyRegistration onSuccess={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Add passkey" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "Add passkey" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Passkey name")).not.toBeInTheDocument();
  });
});
