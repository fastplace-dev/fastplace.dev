import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

import { router } from "@fastplace/react";

import PasskeyVerify from "../passkey-verify";

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

const fakeAssertion = () => ({
  id: "cred-1",
  rawId: new Uint8Array([1, 2, 3]).buffer,
  type: "public-key",
  response: {
    authenticatorData: new Uint8Array([7]).buffer,
    clientDataJSON: new Uint8Array([4, 5]).buffer,
    signature: new Uint8Array([9]).buffer,
    userHandle: null,
  },
});

/** Stand in for navigator.credentials (missing in jsdom, not writable). */
function stubCredentialsGet(assertion: unknown) {
  const get = vi.fn().mockResolvedValue(assertion);
  Object.defineProperty(window.navigator, "credentials", {
    value: { get },
    configurable: true,
  });
  return get;
}

afterEach(() => {
  cleanup();
  router.reset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window.navigator as { credentials?: unknown }).credentials;
});

/* ------------------------------------------------------------------ *
 * PasskeyVerify
 * ------------------------------------------------------------------ */

describe("PasskeyVerify", () => {
  it("renders the passkey button and separator with default copy", () => {
    stubWebAuthnSupport();
    render(<PasskeyVerify />);

    expect(screen.getByRole("button", { name: /sign in with a passkey/i })).toBeInTheDocument();
    expect(screen.getByText("Or continue with email")).toBeInTheDocument();
    expect(document.querySelector("[data-slot='separator-root']")).not.toBeNull();
  });

  it("renders nothing without WebAuthn support", () => {
    const { container } = render(<PasskeyVerify />);
    expect(container).toBeEmptyDOMElement();
  });

  it("honors custom label, loading and separator copy", () => {
    stubWebAuthnSupport();
    render(
      <PasskeyVerify
        label="Use a security key"
        loadingLabel="Checking..."
        separator="Or use a password"
      />,
    );

    expect(screen.getByRole("button", { name: /use a security key/i })).toBeInTheDocument();
    expect(screen.getByText("Or use a password")).toBeInTheDocument();
  });

  it("verifies via options, credential get and a bridge POST, then follows the redirect", async () => {
    stubWebAuthnSupport();
    const get = stubCredentialsGet(fakeAssertion());
    const visit = vi.spyOn(router, "visit").mockResolvedValue();
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/passkeys/login/options")
        return jsonResponse({
          // base64url bytes [1,2,3,4] — the component must decode these into
          // buffers before handing them to navigator.credentials.get.
          challenge: "AQIDBA",
          allowCredentials: [{ id: "AQIDBA", type: "public-key", transports: ["internal"] }],
        });
      return jsonResponse({ redirect: "/tantrum" });
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<PasskeyVerify />);
    await user.click(screen.getByRole("button", { name: /sign in with a passkey/i }));

    await waitFor(() => expect(visit).toHaveBeenCalledWith("/tantrum"));

    const options = fetchCall(fetchMock, 0);
    expect(options.url).toBe("/passkeys/login/options");
    expect(options.headers.Accept).toBe("application/json");
    const decoded = new Uint8Array([1, 2, 3, 4]);
    expect(get).toHaveBeenCalledWith({
      publicKey: {
        challenge: decoded,
        allowCredentials: [{ id: decoded, type: "public-key", transports: ["internal"] }],
      },
    });

    const submit = fetchCall(fetchMock, 1);
    expect(submit.url).toBe("/passkeys/login");
    expect(submit.method).toBe("POST");
    expect(submit.headers["X-Fastplace-Request"]).toBe("true");
    expect(submit.body).toEqual({
      credential: {
        id: "cred-1",
        rawId: "AQID",
        type: "public-key",
        response: {
          authenticatorData: "Bw",
          clientDataJSON: "BAU",
          signature: "CQ",
          userHandle: null,
        },
      },
    });
  });

  it("falls back to the dashboard when the payload carries no redirect", async () => {
    stubWebAuthnSupport();
    stubCredentialsGet(fakeAssertion());
    const visit = vi.spyOn(router, "visit").mockResolvedValue();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/passkeys/login/options") return jsonResponse({ challenge: "AQIDBA" });
        return jsonResponse({ ok: true });
      }),
    );

    const user = userEvent.setup();
    render(<PasskeyVerify />);
    await user.click(screen.getByRole("button", { name: /sign in with a passkey/i }));

    // No redirect in the payload — land on the app dashboard.
    await waitFor(() => expect(visit).toHaveBeenCalledWith("/dashboard"));
  });

  it("accepts confirm-flow routes as url objects or plain strings", async () => {
    stubWebAuthnSupport();
    stubCredentialsGet(fakeAssertion());
    const visit = vi.spyOn(router, "visit").mockResolvedValue();
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/passkeys/confirm/options") return jsonResponse({ challenge: "AQIDBA" });
      return jsonResponse({ redirect: "/settings/security" });
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(
      <PasskeyVerify
        routes={{ options: { url: "/passkeys/confirm/options" }, submit: "/passkeys/confirm" }}
      />,
    );
    await user.click(screen.getByRole("button", { name: /sign in with a passkey/i }));

    await waitFor(() => expect(visit).toHaveBeenCalledWith("/settings/security"));
    expect(fetchMock.mock.calls[0][0]).toBe("/passkeys/confirm/options");
    expect(fetchMock.mock.calls[1][0]).toBe("/passkeys/confirm");
  });

  it("shows a friendly error and re-enables when the ceremony fails", async () => {
    stubWebAuthnSupport();
    const get = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error("not allowed"), { name: "NotAllowedError" }));
    Object.defineProperty(window.navigator, "credentials", {
      value: { get },
      configurable: true,
    });
    const visit = vi.spyOn(router, "visit").mockResolvedValue();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ challenge: "AQIDBA" })),
    );

    const user = userEvent.setup();
    render(<PasskeyVerify />);
    const button = screen.getByRole("button", { name: /sign in with a passkey/i });
    await user.click(button);

    expect(
      await screen.findByText("Unable to verify this passkey. Please try again."),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /sign in with a passkey/i })).toBeEnabled(),
    );
    expect(visit).not.toHaveBeenCalled();
  });

  it("shows the loading label and disables the button while options are pending", async () => {
    stubWebAuthnSupport();
    stubCredentialsGet(fakeAssertion());
    const visit = vi.spyOn(router, "visit").mockResolvedValue();

    let resolveOptions!: (value: Response) => void;
    const optionsPending = new Promise<Response>((resolve) => {
      resolveOptions = resolve;
    });
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/passkeys/login/options") return optionsPending;
      return jsonResponse({ redirect: "/after" });
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<PasskeyVerify />);
    await user.click(screen.getByRole("button", { name: /sign in with a passkey/i }));

    expect(await screen.findByText("Authenticating...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /authenticating\.\.\./i })).toBeDisabled();

    resolveOptions(jsonResponse({ challenge: "late" }));
    await waitFor(() => expect(visit).toHaveBeenCalledWith("/after"));
  });
});
